CREATE TABLE IF NOT EXISTS rollout_events (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  model_name TEXT NOT NULL,
  action TEXT NOT NULL, -- ramp | promote | rollback_error | rollback_shadow | freeze_* | skip

  previous_canary_percent INTEGER NULL,
  new_canary_percent INTEGER NULL,

  stable_version TEXT NULL,
  canary_version TEXT NULL,

  error_stable DOUBLE PRECISION NULL,
  error_canary DOUBLE PRECISION NULL,

  drift_score DOUBLE PRECISION NULL,

  shadow_agreement DOUBLE PRECISION NULL,
  shadow_count DOUBLE PRECISION NULL,

  reason TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_rollout_events_model_time
  ON rollout_events(model_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rollout_events_action_time
  ON rollout_events(action, created_at DESC);