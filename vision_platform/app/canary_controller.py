# vision_platform/app/canary_controller.py
#
# Drift + Error + Shadow-Agreement aware progressive rollout controller
# - Reads model_registry for models with rollout_strategy='progressive'
# - Uses Prometheus for error-rate + request-volume + shadow-agreement (histogram)
# - Uses Evidently drift metric already exported as gateway_drift_score{model_name=...}
# - Writes audit trail into rollout_events table
#
# Env:
#   PG_DSN
#   PROM_URL (default kube-prometheus service)
#   CHECK_INTERVAL (default 30)
#   ERROR_WINDOW (default 5m)
#   DRIFT_QUERY_MODE (unused placeholder)
#
import os
import asyncio
import time
import asyncpg  # type: ignore
import requests
from datetime import datetime, timezone

PROM_URL = os.getenv(
    "PROM_URL",
    "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
)
PG_DSN = os.getenv("PG_DSN")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
ERROR_WINDOW = os.getenv("ERROR_WINDOW", "5m")  # fast canary decisions


# ---------------------------
# Prom helpers
# ---------------------------
def prom_query_scalar(query: str) -> tuple[float, bool]:
    r = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=5)
    r.raise_for_status()
    data = r.json()
    result = data.get("data", {}).get("result", [])
    if not result:
        return 0.0, False
    return float(result[0]["value"][1]), True


def error_rate(model: str, version: str, window: str) -> float:
    q = f'''
    sum(rate(gateway_requests_total{{model_name="{model}",model_version="{version}",status="error"}}[{window}]))
    /
    clamp_min(sum(rate(gateway_requests_total{{model_name="{model}",model_version="{version}"}}[{window}])), 1e-9)
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


def shadow_agreement_avg(model: str, window: str) -> tuple[float, bool]:
    # average agreement from histogram _sum/_count
    q = f'''
    sum(rate(gateway_shadow_agreement_sum{{model_name="{model}"}}[{window}]))
    /
    clamp_min(sum(rate(gateway_shadow_agreement_count{{model_name="{model}"}}[{window}])), 1e-9)
    '''
    return prom_query_scalar(q)


def shadow_agreement_count(model: str, window: str) -> tuple[float, bool]:
    q = f'''
    sum(increase(gateway_shadow_agreement_count{{model_name="{model}"}}[{window}]))
    '''
    return prom_query_scalar(q)


def seconds_since(ts) -> float:
    if ts is None:
        return 1e18
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def next_percent_from_steps(current: int, steps: list[int] | None, fallback_step: int) -> int:
    if steps:
        for s in steps:
            if s > current:
                return min(100, int(s))
        return 100
    return min(100, current + fallback_step)


# ---------------------------
# DB helpers
# ---------------------------
async def fetch_progressive_models(pool):
    sql = """
    SELECT model_name, active, task_type,
           stable_version, canary_version, canary_percent,
           rollout_strategy, ramp_step, ramp_steps, ramp_interval_seconds, last_ramp_at,
           error_threshold, ratio_threshold, min_requests,
           drift_threshold, drift_rollback_threshold, drift_freeze, drift_required,
           shadow_required, shadow_agreement_threshold, shadow_min_requests, shadow_window_seconds
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
        await conn.execute(
            """
            UPDATE model_registry
            SET canary_percent=0,
                canary_version=NULL,
                rollout_strategy='manual',
                updated_at=now()
            WHERE model_name=$1
            """,
            model,
        )
    print(f"🚨 ROLLBACK {model}: {reason}", flush=True)


async def ramp(pool, model: str, new_percent: int, reason: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE model_registry
            SET canary_percent=$2,
                last_ramp_at=now(),
                updated_at=now()
            WHERE model_name=$1
            """,
            model,
            new_percent,
        )
    print(f"⬆️ RAMP {model}: {new_percent}% ({reason})", flush=True)


async def promote(pool, model: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE model_registry
            SET stable_version = (SELECT canary_version FROM model_registry WHERE model_name=$1),
                canary_version = NULL,
                canary_percent = 0,
                rollout_strategy='manual',
                last_ramp_at=now(),
                updated_at=now()
            WHERE model_name=$1
            """,
            model,
        )
    print(f"✅ PROMOTED {model}", flush=True)


async def insert_rollout_event(
    pool,
    *,
    model_name: str,
    action: str,
    previous_canary_percent: int | None = None,
    new_canary_percent: int | None = None,
    stable_version: str | None = None,
    canary_version: str | None = None,
    error_stable: float | None = None,
    error_canary: float | None = None,
    drift_score: float | None = None,
    shadow_agreement: float | None = None,
    shadow_count: float | None = None,
    reason: str | None = None,
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rollout_events(
              model_name, action,
              previous_canary_percent, new_canary_percent,
              stable_version, canary_version,
              error_stable, error_canary,
              drift_score, shadow_agreement, shadow_count,
              reason
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
            model_name,
            action,
            previous_canary_percent,
            new_canary_percent,
            stable_version,
            canary_version,
            error_stable,
            error_canary,
            drift_score,
            shadow_agreement,
            shadow_count,
            reason,
        )


# ---------------------------
# Monitor loop
# ---------------------------
async def monitor():
    if not PG_DSN:
        raise RuntimeError("PG_DSN missing")

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)
    print("drift+error+shadow progressive controller started", flush=True)

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

                # ---- shadow gating config ----
                shadow_required = bool(m.get("shadow_required", False))
                shadow_agree_th = float(m.get("shadow_agreement_threshold", 0.95))
                shadow_min_req = int(m.get("shadow_min_requests", 50))
                shadow_window_s = int(m.get("shadow_window_seconds", 300))
                shadow_window = f"{max(60, shadow_window_s)}s"

                # ---- traffic volume gate (canary) ----
                nreq = req_count(model, canary_v, ERROR_WINDOW)
                if nreq < min_requests:
                    msg = f"not enough canary requests {nreq:.0f} < {min_requests}"
                    print(f"[{model}] skip: {msg}", flush=True)
                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="skip",
                        previous_canary_percent=pct,
                        new_canary_percent=pct,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        reason=msg,
                    )
                    continue

                # ---- compute error ----
                stable_e = error_rate(model, stable_v, ERROR_WINDOW)
                canary_e = error_rate(model, canary_v, ERROR_WINDOW)

                # ---- compute drift ----
                drift_val, drift_present = model_drift(model)

                # ---- compute shadow agreement ----
                sh_cnt, sh_cnt_present = shadow_agreement_count(model, shadow_window)
                sh_avg, sh_avg_present = shadow_agreement_avg(model, shadow_window)

                # If shadow is required but metrics missing => freeze
                if shadow_required:
                    if (not sh_cnt_present) or (not sh_avg_present):
                        msg = "shadow required but metrics missing"
                        print(f"[{model}] FREEZE: {msg}", flush=True)
                        await insert_rollout_event(
                            pool,
                            model_name=model,
                            action="freeze_shadow_missing",
                            previous_canary_percent=pct,
                            new_canary_percent=pct,
                            stable_version=stable_v,
                            canary_version=canary_v,
                            error_stable=stable_e,
                            error_canary=canary_e,
                            drift_score=(drift_val if drift_present else None),
                            shadow_agreement=(sh_avg if sh_avg_present else None),
                            shadow_count=(sh_cnt if sh_cnt_present else None),
                            reason=msg,
                        )
                        continue

                # If we have shadow metrics but not enough samples => skip/freeze
                if sh_cnt_present and sh_cnt < shadow_min_req:
                    msg = f"not enough shadow samples {sh_cnt:.0f} < {shadow_min_req}"
                    print(f"[{model}] FREEZE: {msg}", flush=True)
                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="freeze_shadow_insufficient",
                        previous_canary_percent=pct,
                        new_canary_percent=pct,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        error_stable=stable_e,
                        error_canary=canary_e,
                        drift_score=(drift_val if drift_present else None),
                        shadow_agreement=(sh_avg if sh_avg_present else None),
                        shadow_count=(sh_cnt if sh_cnt_present else None),
                        reason=msg,
                    )
                    continue

                # If shadow agreement present and below threshold => rollback
                if sh_avg_present and sh_avg < shadow_agree_th:
                    msg = f"shadow agreement {sh_avg:.3f} < {shadow_agree_th}"
                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="rollback_shadow",
                        previous_canary_percent=pct,
                        new_canary_percent=0,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        error_stable=stable_e,
                        error_canary=canary_e,
                        drift_score=(drift_val if drift_present else None),
                        shadow_agreement=sh_avg,
                        shadow_count=(sh_cnt if sh_cnt_present else None),
                        reason=msg,
                    )
                    await rollback(pool, model, msg)
                    continue

                # ---- drift freeze behavior ----
                if drift_freeze:
                    if (not drift_present) and drift_required:
                        msg = "drift required but missing"
                        print(f"[{model}] FREEZE: {msg}", flush=True)
                        await insert_rollout_event(
                            pool,
                            model_name=model,
                            action="freeze_drift_missing",
                            previous_canary_percent=pct,
                            new_canary_percent=pct,
                            stable_version=stable_v,
                            canary_version=canary_v,
                            error_stable=stable_e,
                            error_canary=canary_e,
                            shadow_agreement=(sh_avg if sh_avg_present else None),
                            shadow_count=(sh_cnt if sh_cnt_present else None),
                            reason=msg,
                        )
                        continue

                    if drift_present and drift_val > drift_th:
                        msg = f"drift {drift_val:.3f} > {drift_th}"
                        print(f"[{model}] FREEZE: {msg}", flush=True)
                        await insert_rollout_event(
                            pool,
                            model_name=model,
                            action="freeze_drift",
                            previous_canary_percent=pct,
                            new_canary_percent=pct,
                            stable_version=stable_v,
                            canary_version=canary_v,
                            error_stable=stable_e,
                            error_canary=canary_e,
                            drift_score=drift_val,
                            shadow_agreement=(sh_avg if sh_avg_present else None),
                            shadow_count=(sh_cnt if sh_cnt_present else None),
                            reason=msg,
                        )

                        # catastrophic drift + bad errors -> rollback
                        if drift_val >= drift_rb_th and (canary_e > err_th) and (canary_e > stable_e * ratio_th):
                            msg2 = f"drift {drift_val:.3f} >= {drift_rb_th} AND bad canary errors"
                            await insert_rollout_event(
                                pool,
                                model_name=model,
                                action="rollback_drift_error",
                                previous_canary_percent=pct,
                                new_canary_percent=0,
                                stable_version=stable_v,
                                canary_version=canary_v,
                                error_stable=stable_e,
                                error_canary=canary_e,
                                drift_score=drift_val,
                                shadow_agreement=(sh_avg if sh_avg_present else None),
                                shadow_count=(sh_cnt if sh_cnt_present else None),
                                reason=msg2,
                            )
                            await rollback(pool, model, msg2)

                        continue

                # ---- classic rollback gate (error-based) ----
                if (canary_e > err_th) and (canary_e > stable_e * ratio_th):
                    msg = f"canary_err={canary_e:.4f} stable_err={stable_e:.4f} th={err_th} ratio={ratio_th}"
                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="rollback_error",
                        previous_canary_percent=pct,
                        new_canary_percent=0,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        error_stable=stable_e,
                        error_canary=canary_e,
                        drift_score=(drift_val if drift_present else None),
                        shadow_agreement=(sh_avg if sh_avg_present else None),
                        shadow_count=(sh_cnt if sh_cnt_present else None),
                        reason=msg,
                    )
                    await rollback(pool, model, msg)
                    continue

                # ---- cooldown gate ----
                if seconds_since(m["last_ramp_at"]) < ramp_interval:
                    continue

                # ---- ramp / promote ----
                if pct >= 100:
                    # promote only if drift is healthy (or not present and not required)
                    if drift_freeze and drift_present and drift_val > drift_th:
                        msg = f"hold promotion: drift {drift_val:.3f} > {drift_th}"
                        print(f"[{model}] {msg}", flush=True)
                        await insert_rollout_event(
                            pool,
                            model_name=model,
                            action="hold_promotion_drift",
                            previous_canary_percent=pct,
                            new_canary_percent=pct,
                            stable_version=stable_v,
                            canary_version=canary_v,
                            error_stable=stable_e,
                            error_canary=canary_e,
                            drift_score=drift_val,
                            shadow_agreement=(sh_avg if sh_avg_present else None),
                            shadow_count=(sh_cnt if sh_cnt_present else None),
                            reason=msg,
                        )
                        continue

                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="promote",
                        previous_canary_percent=pct,
                        new_canary_percent=0,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        error_stable=stable_e,
                        error_canary=canary_e,
                        drift_score=(drift_val if drift_present else None),
                        shadow_agreement=(sh_avg if sh_avg_present else None),
                        shadow_count=(sh_cnt if sh_cnt_present else None),
                        reason="healthy (err ok, drift ok, shadow ok)",
                    )
                    await promote(pool, model)

                else:
                    steps = m.get("ramp_steps")
                    new_pct = next_percent_from_steps(pct, steps, ramp_step)

                    await insert_rollout_event(
                        pool,
                        model_name=model,
                        action="ramp",
                        previous_canary_percent=pct,
                        new_canary_percent=new_pct,
                        stable_version=stable_v,
                        canary_version=canary_v,
                        error_stable=stable_e,
                        error_canary=canary_e,
                        drift_score=(drift_val if drift_present else None),
                        shadow_agreement=(sh_avg if sh_avg_present else None),
                        shadow_count=(sh_cnt if sh_cnt_present else None),
                        reason="healthy (err ok, drift ok, shadow ok)",
                    )
                    await ramp(pool, model, new_pct, "healthy (err ok, drift ok, shadow ok)")

        except Exception as e:
            print("controller error:", repr(e), flush=True)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(monitor())