import asyncpg
import json

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS inference_results (
  id SERIAL PRIMARY KEY,
  trace_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  model_name TEXT NOT NULL,
  latency_ms DOUBLE PRECISION NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB,
  prediction JSONB,
  raw_image_key TEXT
);
"""

async def init_db(dsn: str):
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=10
    )

    # ensure table exists once at startup
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SQL)

    return pool


async def insert_result(
    pool,
    *,
    trace_id: str,
    task_type: str,
    model_name: str,
    latency_ms: float,
    metadata: dict,
    prediction: dict,
    raw_image_key: str | None,
):
    async with pool.acquire() as conn:
        await conn.execute(
        """
        INSERT INTO inference_results
        (trace_id, task_type, model_name, latency_ms, metadata, prediction, raw_image_key)
        VALUES($1, $2, $3, $4, $5, $6, $7)
        """,
        trace_id,
        task_type,
        model_name,
        latency_ms,
        json.dumps(metadata),
        json.dumps(prediction),
        raw_image_key
    )