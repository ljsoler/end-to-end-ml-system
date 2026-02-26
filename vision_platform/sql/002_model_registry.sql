CREATE TABLE IF NOT EXISTS model_registry (
  id BIGSERIAL PRIMARY KEY,

  -- Logical model identifier used by Gateway
  model_name TEXT NOT NULL UNIQUE,  

  -- Task abstraction (identity, detection, segmentation, ensemble, etc.)
  task_type TEXT NOT NULL,

  -- Whether model is routable
  active BOOLEAN NOT NULL DEFAULT TRUE,

  -- 🔵 Version control
  stable_version TEXT NOT NULL DEFAULT '1',
  canary_version TEXT NULL,
  canary_percent INTEGER NOT NULL DEFAULT 0 CHECK (canary_percent >= 0 AND canary_percent <= 100),

  -- Optional: future ramp strategy (manual, auto, shadow, etc.)
  rollout_strategy TEXT NOT NULL DEFAULT 'manual',

  -- Runtime configs
  preprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  postprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Metadata
  description TEXT NULL,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_model_registry_active 
  ON model_registry(active);

CREATE INDEX IF NOT EXISTS idx_model_registry_task 
  ON model_registry(task_type);

CREATE INDEX IF NOT EXISTS idx_model_registry_rollout 
  ON model_registry(rollout_strategy);