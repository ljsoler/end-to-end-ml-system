-- =========================
-- model_registry (final v2)
-- =========================
CREATE TABLE IF NOT EXISTS model_registry (
  id BIGSERIAL PRIMARY KEY,

  model_name TEXT NOT NULL UNIQUE,
  task_type TEXT NOT NULL,

  active BOOLEAN NOT NULL DEFAULT TRUE,

  -- Versioning
  stable_version TEXT NOT NULL DEFAULT '1',
  canary_version TEXT NULL,
  canary_percent INTEGER NOT NULL DEFAULT 0
    CHECK (canary_percent >= 0 AND canary_percent <= 100),

  -- Rollout
  rollout_strategy TEXT NOT NULL DEFAULT 'manual', -- manual | progressive | shadow

  ramp_step INTEGER NOT NULL DEFAULT 10,
  ramp_steps INTEGER[] NULL,  -- NEW: explicit milestones (e.g., {10,25,50,100})

  ramp_interval_seconds INTEGER NOT NULL DEFAULT 120,
  last_ramp_at TIMESTAMPTZ NULL,

  -- Health gates
  error_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.03,
  ratio_threshold DOUBLE PRECISION NOT NULL DEFAULT 2.0,
  min_requests INTEGER NOT NULL DEFAULT 50,

  -- Drift gates
  drift_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.30,
  drift_rollback_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.50,
  drift_freeze BOOLEAN NOT NULL DEFAULT TRUE,
  drift_required BOOLEAN NOT NULL DEFAULT FALSE,

  -- Runtime configs
  preprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  postprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,

  description TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_registry_active ON model_registry(active);
CREATE INDEX IF NOT EXISTS idx_model_registry_task ON model_registry(task_type);
CREATE INDEX IF NOT EXISTS idx_model_registry_rollout ON model_registry(rollout_strategy);
CREATE INDEX IF NOT EXISTS idx_model_registry_model_name ON model_registry(model_name);