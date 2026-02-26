#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[0] Helm repos"
helm repo add nats https://nats-io.github.io/k8s/helm/charts/ >/dev/null 2>&1 || true
helm repo add minio https://charts.min.io/ >/dev/null 2>&1 || true
helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "[1] Deploy NATS JetStream"
helm upgrade --install nats nats/nats -n data \
  --set jetstream.enabled=true \
  --wait

echo "[2] Deploy MinIO"
helm upgrade --install minio minio/minio -n data \
  --set mode=standalone \
  --set rootUser=minio \
  --set rootPassword=minio123456 \
  --set persistence.enabled=false \
  --set resources.requests.memory=256Mi \
  --set resources.limits.memory=512Mi \
  --wait

echo "[3] Deploy Postgres"
helm upgrade --install postgres bitnami/postgresql -n data \
  --set auth.postgresPassword=postgres \
  --set auth.database=visiondb \
  --set primary.resources.requests.memory=256Mi \
  --set primary.resources.limits.memory=512Mi \
  --wait

echo "[4] Build Gateway Image"
docker build -t vision-gateway:dev "${ROOT_DIR}/vision_platform"

echo "[5] Import Gateway Image into k3d"
k3d image import vision-gateway:dev -c vision

echo "[6] Apply Gateway Secret"
kubectl apply -f "${ROOT_DIR}/k8s/serving/gateway-secret.yaml"

echo "[7] Build Monitoring Stack (Prometheus + Grafana)"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace

kubectl apply -f k8s/monitoring/gateway-servicemonitor.yaml

echo "[8] Build Canary Controller Image"
docker build -f vision_platform/Dockerfile.controller -t vision-canary-controller:dev vision_platform
k3d image import vision-canary-controller:dev -c vision

kubectl apply -f k8s/serving/canary-controller.yaml

echo "[9] Deploy Triton + Gateway (YAML manifests)"
kubectl apply -f "${ROOT_DIR}/k8s/serving/triton.yaml"
kubectl apply -f "${ROOT_DIR}/k8s/serving/gateway.yaml"

echo "Waiting for serving pods..."
kubectl -n serving rollout status deploy/triton
kubectl -n serving rollout status deploy/gateway

echo ""
echo "Done."
echo ""
echo "Next:"
echo "  - Port-forward Triton:  kubectl -n serving port-forward svc/triton 8000:8000"
echo "  - Port-forward Gateway: kubectl -n serving port-forward svc/gateway 8082:8000"
echo "  - Port-forward MinIO:   kubectl -n data port-forward svc/minio 9001:9001 9000:9000"