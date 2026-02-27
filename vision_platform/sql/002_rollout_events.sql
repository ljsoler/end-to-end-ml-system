CREATE TABLE IF NOT EXISTS rollout_events (
  id BIGSERIAL PRIMARY KEY,
  model_name TEXT NOT NULL,
  action TEXT NOT NULL, -- ramp | promote | rollback | freeze | skip
  previous_canary_percent INT NULL,
  new_canary_percent INT NULL,
  stable_version TEXT NULL,
  canary_version TEXT NULL,
  error_stable DOUBLE PRECISION NULL,
  error_canary DOUBLE PRECISION NULL,
  drift_score DOUBLE PRECISION NULL,
  reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rollout_events_model_time
  ON rollout_events(model_name, created_at);