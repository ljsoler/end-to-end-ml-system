CREATE TABLE IF NOT EXISTS model_registry (
  id BIGSERIAL PRIMARY KEY,
  model_name TEXT NOT NULL UNIQUE,          -- e.g., "identity_onnx" o "ensemble_detection_v3"
  task_type TEXT NOT NULL,                  -- e.g., "classification", "detection", "segmentation"
  active BOOLEAN NOT NULL DEFAULT TRUE,

  -- runtime config para la task (resize, mean/std, thresholds, etc.)
  preprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  postprocess_config JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- opcional: etiquetas útiles
  description TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_registry_active ON model_registry(active);
CREATE INDEX IF NOT EXISTS idx_model_registry_task ON model_registry(task_type);