# app/shadow_db.py
from __future__ import annotations
from typing import Optional
import json

async def insert_shadow_result(
    pool,
    *,
    trace_id: str,
    model_name: str,
    task_type: str,
    stable_version: str,
    shadow_version: str,
    machine_id: Optional[str],
    camera_id: Optional[str],
    stable_latency_ms: Optional[float],
    shadow_latency_ms: Optional[float],
    comparison: dict,
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO shadow_results(
              trace_id, model_name, task_type,
              stable_version, shadow_version,
              machine_id, camera_id,
              stable_latency_ms, shadow_latency_ms,
              comparison
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            """,
            trace_id,
            model_name,
            task_type,
            stable_version,
            shadow_version,
            machine_id,
            camera_id,
            stable_latency_ms,
            shadow_latency_ms,
            json.dumps(comparison),  # <-- dict is fine, cast handles it
        )