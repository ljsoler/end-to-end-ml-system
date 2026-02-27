CREATE TABLE IF NOT EXISTS shadow_results (
  id BIGSERIAL PRIMARY KEY,
  trace_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  stable_version TEXT NOT NULL,
  shadow_version TEXT NOT NULL,
  task_type TEXT NOT NULL,
  camera_id TEXT NULL,
  machine_id TEXT NULL,

  stable_latency_ms DOUBLE PRECISION NULL,
  shadow_latency_ms DOUBLE PRECISION NULL,

  comparison JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shadow_model_time
  ON shadow_results(model_name, created_at);