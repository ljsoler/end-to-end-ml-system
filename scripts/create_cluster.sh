#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="vision"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "${ROOT_DIR}/triton_models"

echo "[0/2] Deleting old cluster (if exists)"
k3d cluster delete "${CLUSTER_NAME}" || true

echo "[1/2] Creating k3d cluster '${CLUSTER_NAME}' with sufficient memory"

k3d cluster create "${CLUSTER_NAME}" \
  --servers 1 \
  --agents 2 \
  --servers-memory 4g \
  --agents-memory 4g \
  --port 8080:80@loadbalancer \
  --volume "${ROOT_DIR}/triton_models:/models@all"

echo "[2/2] Applying namespaces"
kubectl apply -f "${ROOT_DIR}/k8s/base/namespaces.yaml"

echo "Cluster ready."