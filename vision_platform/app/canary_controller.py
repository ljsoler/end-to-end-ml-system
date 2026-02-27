import os
import asyncio
import time
import asyncpg # type: ignore
import requests
from datetime import datetime, timezone

PROM_URL = os.getenv(
    "PROM_URL",
    "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
)
PG_DSN = os.getenv("PG_DSN")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
ERROR_WINDOW = os.getenv("ERROR_WINDOW", "5m")     # for fast canary decisions
DRIFT_QUERY_MODE = os.getenv("DRIFT_QUERY_MODE", "max")  # max | p95 (future)

def prom_query_scalar(query: str) -> tuple[float, bool]:
    """returns (value, present). present=False if Prometheus returns empty result."""
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=5)
    r.raise_for_status()
    data = r.json()
    result = data.get("data", {}).get("result", [])
    if not result:
        return 0.0, False
    return float(result[0]["value"][1]), True

def clamp_div(num: float, den: float) -> float:
    return num / max(den, 1e-12)

def error_rate(model: str, version: str, window: str) -> float:
    q = f'''
    sum(rate(gateway_requests_total{{model_name="{model}",model_version="{version}",status="error"}}[{window}]))
    /
    clamp_min(sum(rate(gateway_requests_total{{model_name="{model}",model_version="{version}"}}[{window}])), 1e-9)
    '''
    v, _ = prom_query_scalar(q)
    return v

def req_rate(model: str, version: str, window: str) -> float:
    q = f'''
    sum(rate(gateway_requests_total{{model_name="{model}",model_version="{version}"}}[{window}]))
    '''
    v, _ = prom_query_scalar(q)
    return v

def req_count(model: str, version: str, window: str) -> float:
    q = f'''
    sum(increase(gateway_requests_total{{model_name="{model}",model_version="{version}"}}[{window}]))
    '''
    v, _ = prom_query_scalar(q)
    return v

def model_drift(model: str) -> tuple[float, bool]:
    # conservative: max drift across cameras for this model
    q = f'max(gateway_drift_score{{model_name="{model}"}})'
    return prom_query_scalar(q)

def seconds_since(ts) -> float:
    if ts is None:
        return 1e18
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()

def next_percent(current: int, step: int) -> int:
    return min(100, current + step)

def next_percent_from_steps(current: int, steps: list[int] | None, fallback_step: int) -> int:
    if steps:
        # pick next step strictly greater than current
        for s in steps:
            if s > current:
                return min(100, int(s))
        return 100
    return min(100, current + fallback_step)

async def fetch_progressive_models(pool):
    sql = """
    SELECT model_name, active, task_type,
           stable_version, canary_version, canary_percent,
           rollout_strategy, ramp_step, ramp_steps, ramp_interval_seconds, last_ramp_at,
           error_threshold, ratio_threshold, min_requests,
           drift_threshold, drift_rollback_threshold, drift_freeze, drift_required
    FROM model_registry
    WHERE rollout_strategy='progressive'
      AND active=true
      AND canary_version IS NOT NULL
      AND canary_percent > 0
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]

async def rollback(pool, model: str, reason: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET canary_percent=0,
              canary_version=NULL,
              rollout_strategy='manual',
              updated_at=now()
          WHERE model_name=$1
        """, model)
    print(f"🚨 ROLLBACK {model}: {reason}", flush=True)

async def ramp(pool, model: str, new_percent: int, reason: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET canary_percent=$2,
              last_ramp_at=now(),
              updated_at=now()
          WHERE model_name=$1
        """, model, new_percent)
    print(f"⬆️ RAMP {model}: {new_percent}% ({reason})", flush=True)

async def promote(pool, model: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET stable_version = (SELECT canary_version FROM model_registry WHERE model_name=$1),
              canary_version = NULL,
              canary_percent = 0,
              rollout_strategy='manual',
              last_ramp_at=now(),
              updated_at=now()
          WHERE model_name=$1
        """, model)
    print(f"✅ PROMOTED {model}", flush=True)

async def monitor():
    if not PG_DSN:
        raise RuntimeError("PG_DSN missing")

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    print("drift-aware progressive controller started", flush=True)

    while True:
        try:
            models = await fetch_progressive_models(pool)

            for m in models:
                model = m["model_name"]
                stable_v = str(m["stable_version"])
                canary_v = str(m["canary_version"])
                pct = int(m["canary_percent"])

                ramp_step = int(m["ramp_step"])
                ramp_interval = int(m["ramp_interval_seconds"])
                min_requests = int(m["min_requests"])

                err_th = float(m["error_threshold"])
                ratio_th = float(m["ratio_threshold"])

                drift_th = float(m["drift_threshold"])
                drift_rb_th = float(m["drift_rollback_threshold"])
                drift_freeze = bool(m["drift_freeze"])
                drift_required = bool(m["drift_required"])

                # ---- traffic volume gate ----
                nreq = req_count(model, canary_v, ERROR_WINDOW)
                if nreq < min_requests:
                    print(f"[{model}] skip: not enough canary requests {nreq:.0f} < {min_requests}", flush=True)
                    continue

                # ---- compute error ----
                stable_e = error_rate(model, stable_v, ERROR_WINDOW)
                canary_e = error_rate(model, canary_v, ERROR_WINDOW)

                # ---- compute drift ----
                drift_val, drift_present = model_drift(model)

                if drift_freeze:
                    if (not drift_present) and drift_required:
                        # safest behavior
                        print(f"[{model}] FREEZE: drift missing but required", flush=True)
                        continue

                    if drift_present and drift_val > drift_th:
                        # freeze ramping, but still allow rollback if catastrophic
                        print(f"[{model}] FREEZE: drift={drift_val:.3f} > {drift_th}", flush=True)

                        # drift + error worsening => rollback
                        if drift_val >= drift_rb_th and (canary_e > err_th) and (canary_e > stable_e * ratio_th):
                            await rollback(pool, model, f"drift={drift_val:.3f}>=rb_th and canary_err={canary_e:.4f} bad")
                        continue

                # ---- classic rollback gate (error-based) ----
                if (canary_e > err_th) and (canary_e > stable_e * ratio_th):
                    await rollback(pool, model, f"canary_err={canary_e:.4f} stable_err={stable_e:.4f} th={err_th}")
                    continue

                # ---- cooldown gate ----
                if seconds_since(m["last_ramp_at"]) < ramp_interval:
                    continue

                # ---- ramp / promote ----
                if pct >= 100:
                    # promote only if drift is healthy (or not present and not required)
                    if drift_freeze and drift_present and drift_val > drift_th:
                        print(f"[{model}] HOLD PROMOTION: drift={drift_val:.3f} > {drift_th}", flush=True)
                        continue
                    await promote(pool, model)
                else:
                    steps = m.get("ramp_steps")
                    # asyncpg returns list already; if it returns None it stays None
                    new_pct = next_percent_from_steps(pct, steps, ramp_step)
                    await ramp(pool, model, new_pct, "healthy (err ok, drift ok)")

        except Exception as e:
            print("controller error:", repr(e), flush=True)

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(monitor())