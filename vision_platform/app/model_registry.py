import json

import json

async def get_model_entry(pool, model_name: str):

    query = """
        SELECT
            model_name,
            task_type,
            preprocess_config,
            postprocess_config,
            active,
            stable_version,
            canary_version,
            canary_percent
        FROM model_registry
        WHERE model_name = $1
        LIMIT 1
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, model_name)

    if not row:
        return None

    result = dict(row)

    # Ensure JSON fields are dicts
    for field in ["preprocess_config", "postprocess_config"]:
        if isinstance(result[field], str):
            result[field] = json.loads(result[field])

    return result
