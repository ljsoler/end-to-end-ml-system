CREATE TABLE IF NOT EXISTS model_registry (
  id BIGSERIAL PRIMARY KEY,

  -- Logical model identifier used by Gateway
  model_name TEXT NOT NULL UNIQUE,

  -- Task abstraction
  task_type TEXT NOT NULL,

  -- Whether model is routable
  active BOOLEAN NOT NULL DEFAULT TRUE,

  -- 🔵 Version control
  stable_version TEXT NOT NULL DEFAULT '1',
  canary_version TEXT NULL,
  canary_percent INTEGER NOT NULL DEFAULT 0
      CHECK (canary_percent >= 0 AND canary_percent <= 100),

  -- 🔵 Rollout control
  rollout_strategy TEXT NOT NULL DEFAULT 'manual', -- manual | progressive | shadow

  ramp_step INTEGER NOT NULL DEFAULT 10, -- % increment per promotion
  ramp_interval_seconds INTEGER NOT NULL DEFAULT 120, -- wait time between ramps

  last_ramp_at TIMESTAMPTZ NULL, -- last time traffic was increased

  -- 🔵 Promotion thresholds
  error_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.03,
  ratio_threshold DOUBLE PRECISION NOT NULL DEFAULT 2.0,

  min_requests INTEGER NOT NULL DEFAULT 50, -- minimum traffic before evaluation

  -- Runtime configs
  preprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  postprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Metadata
  description TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_drift_metrics (
  id BIGSERIAL PRIMARY KEY,

  model_name TEXT NOT NULL,
  task_type TEXT NOT NULL,
  camera_id TEXT NOT NULL,

  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  reference_start TIMESTAMPTZ NOT NULL,
  reference_end TIMESTAMPTZ NOT NULL,

  drift_score DOUBLE PRECISION NOT NULL,
  share_drifted_features DOUBLE PRECISION NOT NULL,
  n_ref INTEGER NOT NULL,
  n_cur INTEGER NOT NULL,

  report_key TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_drift_model_camera_time
  ON model_drift_metrics(model_name, camera_id, created_at);