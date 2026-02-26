import os
import asyncio
import asyncpg # type: ignore
import requests

PROM_URL = os.getenv(
    "PROM_URL",
    "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
)
PG_DSN = os.getenv("PG_DSN")
MODEL_NAME = os.getenv("MODEL_NAME", "identity_onnx")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # seconds
ERROR_THRESHOLD = float(os.getenv("ERROR_THRESHOLD", "0.03"))  # 3%
RATIO_THRESHOLD = float(os.getenv("RATIO_THRESHOLD", "2.0"))  # canary > stable * 2


def prom_query(query: str) -> float:
    r = requests.get(
        f"{PROM_URL}/api/v1/query",
        params={"query": query},
        timeout=5,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("data", {}).get("result"):
        return 0.0
    return float(data["data"]["result"][0]["value"][1])


def error_rate(model_name: str, version: str, window: str = "1m") -> float:
    # Protect division by zero using clamp_min
    query = f"""
    sum(rate(gateway_requests_total{{model_name="{model_name}",model_version="{version}",status="error"}}[{window}]))
    /
    clamp_min(sum(rate(gateway_requests_total{{model_name="{model_name}",model_version="{version}"}}[{window}])), 1e-9)
    """
    return prom_query(query)


async def get_registry(pool, model_name: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT stable_version, canary_version, canary_percent, active
            FROM model_registry
            WHERE model_name = $1
            """,
            model_name,
        )
    return dict(row) if row else None


async def rollback(pool, model_name: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE model_registry
            SET canary_percent = 0,
                canary_version = NULL,
                updated_at = now()
            WHERE model_name = $1
            """,
            model_name,
        )
    print(f"🚨 Canary rolled back for {model_name}", flush=True)


async def monitor():
    if not PG_DSN:
        raise RuntimeError("PG_DSN env var is missing")

    pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=5)

    print("canary-controller started", flush=True)

    while True:
        try:
            entry = await get_registry(pool, MODEL_NAME)
            if not entry:
                print(f"[warn] model '{MODEL_NAME}' not found in registry", flush=True)
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            if not entry["active"]:
                print(f"[info] model '{MODEL_NAME}' is inactive; skipping", flush=True)
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            stable_v = entry["stable_version"]
            canary_v = entry["canary_version"]
            canary_pct = entry["canary_percent"]

            print(
                f"tick model={MODEL_NAME} stable={stable_v} canary={canary_v} pct={canary_pct}",
                flush=True,
            )

            # No canary configured
            if not canary_v or canary_pct <= 0:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            stable_err = error_rate(MODEL_NAME, stable_v, window="1m")
            canary_err = error_rate(MODEL_NAME, canary_v, window="1m")

            print(
                f"errors stable(v={stable_v})={stable_err:.4f} canary(v={canary_v})={canary_err:.4f}",
                flush=True,
            )

            if canary_err > ERROR_THRESHOLD and canary_err > stable_err * RATIO_THRESHOLD:
                await rollback(pool, MODEL_NAME)

        except Exception as e:
            print("Controller error:", repr(e), flush=True)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(monitor())