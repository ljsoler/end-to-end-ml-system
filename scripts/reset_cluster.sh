#!/bin/bash

set -e

echo "🧹 Deleting k3d cluster..."
k3d cluster delete vision || true

echo "🧹 Removing Triton images..."
docker rmi nvcr.io/nvidia/tritonserver:24.01-py3 2>/dev/null || true
docker rmi nvcr.io/nvidia/tritonserver:23.08-py3 2>/dev/null || true

echo "🧹 Pruning unused Docker images..."
docker image prune -f

# echo "🧹 Cleaning local Triton model repo..."
# rm -rf triton_models
# mkdir -p triton_models

echo "✅ Reset complete."