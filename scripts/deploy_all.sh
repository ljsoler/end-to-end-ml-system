#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[0] Create namespaces"
kubectl create namespace data --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace serving --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

echo "[1] Helm repos"
helm repo add nats https://nats-io.github.io/k8s/helm/charts/ >/dev/null 2>&1 || true
helm repo add minio https://charts.min.io/ >/dev/null 2>&1 || true
helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "[2] Deploy NATS JetStream"
helm upgrade --install nats nats/nats -n data \
  --set jetstream.enabled=true \
  --wait

echo "[3] Deploy MinIO"
helm upgrade --install minio minio/minio -n data \
  --set mode=standalone \
  --set rootUser=minio \
  --set rootPassword=minio123456 \
  --set persistence.enabled=false \
  --set resources.requests.memory=256Mi \
  --set resources.limits.memory=512Mi \
  --wait

echo "[3.1] Create MinIO bucket (triton-models)"

kubectl -n data port-forward svc/minio 9000:9000 >/dev/null 2>&1 &
PF_PID=$!

echo "Waiting for MinIO to be ready..."

for i in {1..30}; do
  if curl -s http://127.0.0.1:9000/minio/health/live >/dev/null; then
    echo "MinIO is ready"
    break
  fi
  sleep 2
done

docker run --rm --entrypoint /bin/sh minio/mc -c "
  mc alias set local http://host.docker.internal:9000 minio minio123456 &&
  mc mb -p local/triton-models || true
"

kill $PF_PID || true

echo "[4] Deploy Postgres"

helm upgrade --install postgres bitnami/postgresql -n data \
  --set auth.postgresPassword=postgres \
  --set auth.database=visiondb \
  --set primary.resources.requests.memory=256Mi \
  --set primary.resources.limits.memory=512Mi \
  --wait

echo "[5] Build Gateway Image"

docker build -t vision-gateway:dev "${ROOT_DIR}/vision_platform"

echo "[6] Import Gateway Image into k3d"

k3d image import vision-gateway:dev -c vision

echo "[7] Apply Gateway Secret"

kubectl apply -f "${ROOT_DIR}/k8s/serving/gateway-secret.yaml"

echo "[8] Deploy Monitoring Stack (Prometheus + Grafana)"

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --wait

kubectl apply -f k8s/monitoring/gateway-servicemonitor.yaml

echo "[8.1] Deploy Prometheus Pushgateway"

helm upgrade --install pushgateway prometheus-community/prometheus-pushgateway \
  -n monitoring --wait

echo "[9] Build Canary Controller"

docker build -f vision_platform/Dockerfile.controller \
  -t vision-canary-controller:dev vision_platform

k3d image import vision-canary-controller:dev -c vision

kubectl apply -f k8s/serving/canary-controller.yaml

echo "[10] Create Triton S3 secret"

kubectl -n serving create secret generic triton-s3 \
  --from-literal=access_key=minio \
  --from-literal=secret_key=minio123456 \
  --dry-run=client -o yaml | kubectl apply -f -

echo "[10.1] Deploy Triton + Gateway"

kubectl apply -f "${ROOT_DIR}/k8s/serving/triton.yaml"
kubectl apply -f "${ROOT_DIR}/k8s/serving/gateway.yaml"

echo "Waiting for serving pods..."

kubectl -n serving rollout status deploy/triton
kubectl -n serving rollout status deploy/gateway

echo ""
echo "System ready."
echo ""

echo "Useful commands:"
echo ""
echo "Triton:"
echo "kubectl -n serving port-forward svc/triton 8000:8000"
echo ""
echo "Gateway:"
echo "kubectl -n serving port-forward svc/gateway 8082:8000"
echo ""
echo "MinIO:"
echo "kubectl -n data port-forward svc/minio-console 9001:9001"
echo ""
echo "Grafana:"
echo "kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80"