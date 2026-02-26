import os
import asyncio
import asyncpg  # type: ignore
import requests
from datetime import datetime, timezone

PROM_URL = os.getenv("PROM_URL", "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090")
PG_DSN = os.getenv("PG_DSN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

def prom_scalar(query: str) -> float:
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=5)
    r.raise_for_status()
    data = r.json()
    if not data.get("data", {}).get("result"):
        return 0.0
    return float(data["data"]["result"][0]["value"][1])

def req_rate(model: str, version: str, window: str) -> float:
    q = f'''
    sum(rate(gateway_requests_total{{model_name="{model}", model_version="{version}"}}[{window}]))
    '''
    return prom_scalar(q)

def err_rate(model: str, version: str, window: str) -> float:
    q = f'''
    sum(rate(gateway_requests_total{{model_name="{model}", model_version="{version}", status="error"}}[{window}]))
    /
    clamp_min(sum(rate(gateway_requests_total{{model_name="{model}", model_version="{version}"}}[{window}])), 1e-9)
    '''
    return prom_scalar(q)

async def fetch_progressive_models(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
          SELECT model_name, active, stable_version, canary_version, canary_percent,
                 rollout_strategy, ramp_steps, ramp_interval_seconds, last_ramp_at,
                 error_threshold, ratio_threshold, min_requests_5m, min_requests_30m,
                 rollout_status
          FROM model_registry
          WHERE rollout_strategy='progressive'
            AND active=true
            AND canary_version IS NOT NULL
            AND canary_percent > 0
        """)
    return [dict(r) for r in rows]

async def rollback(pool, model_name: str, reason: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET canary_percent=0,
              canary_version=NULL,
              rollout_status='rolled_back',
              rollout_reason=$2,
              last_ramp_at=now(),
              updated_at=now()
          WHERE model_name=$1
        """, model_name, reason)
    print(f"🚨 rollback {model_name}: {reason}", flush=True)

async def ramp_to(pool, model_name: str, new_percent: int, reason: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET canary_percent=$2,
              rollout_status='ramping',
              rollout_reason=$3,
              last_ramp_at=now(),
              updated_at=now()
          WHERE model_name=$1
        """, model_name, new_percent, reason)
    print(f"⬆️ ramp {model_name} -> {new_percent}% ({reason})", flush=True)

async def promote(pool, model_name: str):
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET stable_version = stable_version, -- placeholder
              rollout_status='promoted',
              rollout_reason='promoted to stable',
              updated_at=now()
          WHERE model_name=$1
        """, model_name)

    # Promote must set stable_version = old canary_version, clear canary fields atomically:
    async with pool.acquire() as conn:
        await conn.execute("""
          UPDATE model_registry
          SET stable_version = (SELECT canary_version FROM model_registry WHERE model_name=$1),
              canary_version = NULL,
              canary_percent = 0,
              rollout_status='promoted',
              rollout_reason='promoted to stable',
              last_ramp_at=now(),
              updated_at=now()
          WHERE model_name=$1
        """, model_name)

    print(f"✅ promoted {model_name}", flush=True)

def next_step(steps, current):
    # steps is a list like [10,25,50,100]
    for s in steps:
        if s > current:
            return s
    return None

def seconds_since(ts) -> float:
    if ts is None:
        return 1e18
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()

async def monitor():
    if not PG_DSN:
        raise RuntimeError("PG_DSN is missing")

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    print("progressive-ramp controller started", flush=True)

    while True:
        try:
            models = await fetch_progressive_models(pool)

            for m in models:
                name = m["model_name"]
                stable_v = m["stable_version"]
                canary_v = m["canary_version"]
                pct = int(m["canary_percent"])
                steps = list(m["ramp_steps"] or [10,25,50,100])
                interval = int(m["ramp_interval_seconds"])
                err_th = float(m["error_threshold"])
                ratio_th = float(m["ratio_threshold"])
                min5 = int(m["min_requests_5m"])
                min30 = int(m["min_requests_30m"])

                # volume checks
                canary_rps_5m = req_rate(name, canary_v, "5m")
                canary_rps_30m = req_rate(name, canary_v, "30m")
                canary_req_5m = canary_rps_5m * 300.0
                canary_req_30m = canary_rps_30m * 1800.0

                stable_e5 = err_rate(name, stable_v, "5m")
                canary_e5 = err_rate(name, canary_v, "5m")
                stable_e30 = err_rate(name, stable_v, "30m")
                canary_e30 = err_rate(name, canary_v, "30m")

                print(
                    f"[{name}] pct={pct} stable={stable_v} canary={canary_v} "
                    f"req5={canary_req_5m:.0f} req30={canary_req_30m:.0f} "
                    f"e5 s={stable_e5:.4f} c={canary_e5:.4f} | e30 s={stable_e30:.4f} c={canary_e30:.4f}",
                    flush=True
                )

                # if not enough traffic, do nothing
                if canary_req_5m < min5 or canary_req_30m < min30:
                    continue

                # rollback condition: both windows bad + relative worse than stable
                if (canary_e5 > err_th and canary_e30 > err_th) and (canary_e5 > stable_e5 * ratio_th):
                    await rollback(pool, name, f"canary_err={canary_e5:.4f}/{canary_e30:.4f} stable_err={stable_e5:.4f} th={err_th}")
                    continue

                # cooldown before ramping
                if seconds_since(m["last_ramp_at"]) < interval:
                    continue

                nxt = next_step(steps, pct)
                if nxt is None:
                    # already at max (likely 100) -> promote
                    if pct >= 100:
                        await promote(pool, name)
                    continue

                await ramp_to(pool, name, nxt, "healthy; advancing ramp")

        except Exception as e:
            print("controller error:", repr(e), flush=True)

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(monitor())