# Vision System (Mac + k3d)

Production-style end-to-end industrial computer vision platform running locally on Mac + k3d Kubernetes.

The system is task-agnostic and supports multiple ML tasks such as identity recognition, detection, segmentation, and ensembles.

## Core Pipeline

1. Edge client sends image + metadata
2. FastAPI Gateway validates request and routes the task
3. Triton Inference Server executes model inference
4. MinIO (S3) stores raw images and artifacts
5. Postgres stores metadata, predictions, rollout state, and drift metrics
6. Prometheus + Grafana provide monitoring and observability
7. Controllers manage safe model rollout and monitoring

## System Architecture

Edge Client
     │
     ▼
FastAPI Gateway
     │
     ├── Prometheus Metrics
     │
     ▼
Triton Inference Server
     │
     ▼
Predictions
     │
     ├── Postgres (metadata, predictions)
     └── MinIO (raw images)

Monitoring Layer
     ├── Prometheus
     ├── Grafana
     └── Pushgateway

ML Safety Layer
     ├── Progressive Canary Controller
     ├── Drift Detection Worker (Evidently)
     └── Shadow Model Evaluation

## Key Features

### Task-Agnostic Gateway

The FastAPI gateway supports multiple ML tasks via a task registry abstraction.

```
TASK_REGISTRY = {
  "identity": IdentityTask(),
  "detection": DetectionTask(),
  "segmentation": SegmentationTask()
}
```

The gateway:
- validates requests
- fetches runtime configuration from model_registry
- routes inference to Triton
- exports metrics to Prometheus
- stores results in Postgres

### Model Versioning

Models are versioned in Triton and controlled through the database.

Example:

```
stable_version = 1
canary_version = 2
canary_percent = 10
```

Traffic routing:

```
90% → stable
10% → canary
````

Prometheus tracks metrics per model version.

### Progressive Canary Rollout

A Kubernetes controller automatically increases canary traffic when the model is healthy.

Example ramp:

```
10% → 25% → 50% → 100% → promote
````

Promotion occurs when:
- error rate is acceptable
- drift is acceptable
- shadow agreement is acceptable

Rollback occurs when:

```
canary_error > threshold
AND
canary_error > stable_error * ratio
````

All decisions are stored in:
```
rollout_events
````

## Shadow Model Evaluation

Shadow inference runs asynchronously without affecting the primary prediction.

```
Stable/Canary inference → returned to client
Shadow inference → evaluated in background
````

Results stored in:

```
shadow_results
````

Metrics exported:
```
gateway_shadow_agreement
gateway_shadow_latency_ms
````
This allows validation of new models on real production traffic.

## Drift Detection (Evidently)

A background worker computes data drift using Evidently AI.

Pipeline:

```
predictions → Postgres
         ↓
Evidently Drift Worker
         ↓
drift metrics → Postgres
drift score → Prometheus
HTML reports → MinIO
````

Metrics exported:
```
gateway_drift_score
````

Drift is used as a safety gate in rollout decisions.

## Observability Stack (LGTM)

Monitoring is deployed via Helm.

Component	    Role
Prometheus	    metrics storage
Grafana	        dashboards
Pushgateway	    batch metrics (drift worker)

Tracked metrics include:

```
gateway_requests_total
gateway_inference_latency_ms
gateway_shadow_agreement
gateway_drift_score
````

## Databases

### model_registry

Central configuration for model routing and rollout.

Controls:
- stable/canary versions
- rollout strategy
- drift thresholds
- shadow validation rules
- rollout_events

Audit trail of controller decisions.

Examples:
```
ramp
freeze_shadow
freeze_drift
rollback_error
rollback_shadow
promote
```

### shadow_results

Stores comparisons between stable and shadow outputs.

Used to validate new models before promotion.

### model_drift_metrics

Stores Evidently drift results.

Includes:
```
drift_score
share_drifted_features
reference_window
current_window
report_key
```

## Deployment Stack

Running inside a k3d Kubernetes cluster.
```
data namespace
  ├── Postgres
  ├── MinIO
  └── NATS JetStream

serving namespace
  ├── Triton
  ├── Vision Gateway
  └── Canary Controller

monitoring namespace
  ├── Prometheus
  ├── Grafana
  └── Pushgateway
```

## Quick Start (Local)

### 1 Create cluster
```
k3d cluster create vision
```

###2 Deploy platform 
```
scripts/deploy.sh
````

This deploys:
- NATS
- MinIO
- Postgres
- Triton
- Gateway
- Canary controller
- Prometheus + Grafana

### 3 Port-forward services

Gateway

```
kubectl -n serving port-forward svc/gateway 8082:8000
```

Triton

```
kubectl -n serving port-forward svc/triton 8000:8000
```

Grafana
```
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

### 4 Send test request
```
curl http://localhost:8082/infer \
  -H "Content-Type: application/json" \
  -d @test.json
```

## Current Capabilities

✔ Task-agnostic ML gateway
✔ Triton model serving
✔ Model versioning
✔ Progressive canary rollout
✔ Automatic rollback
✔ Shadow model validation
✔ Data drift detection (Evidently)
✔ Prometheus monitoring
✔ Grafana dashboards
✔ MinIO artifact storage

## Next Steps

Planned improvements:
- Model release CLI
- Automated training pipeline
- Dataset versioning
- Alertmanager alerts
- Loki log aggregation
- Tempo distributed tracing
- Automated retraining triggers
- Model lineage tracking