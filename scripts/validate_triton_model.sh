#!/usr/bin/env bash
set -euo pipefail

NS="serving"
SVC="triton"
MODEL=""
VERSION=""
TIMEOUT_SECS="180"
ENABLE_SMOKE="false"

HTTP_PORT_LOCAL="8000"

die() { echo "ERROR: $*" >&2; exit 1; }

wait_http_code() {
  local method="$1"
  local url="$2"
  local timeout="$3"
  local expect_code="$4"
  local data="${5:-}"
  local start now code

  start="$(date +%s)"
  while true; do
    if [[ -n "$data" ]]; then
      code="$(curl -sS -o /dev/null -w "%{http_code}" \
        -X "$method" \
        -H "Content-Type: application/json" \
        -d "$data" \
        "$url" || true)"
    else
      code="$(curl -sS -o /dev/null -w "%{http_code}" \
        -X "$method" \
        "$url" || true)"
    fi

    if [[ "$code" == "$expect_code" ]]; then
      return 0
    fi

    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 2
  done
}

wait_port() {
  local port="$1"
  for _ in {1..30}; do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NS="$2"; shift 2 ;;
    --service) SVC="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --timeout-secs) TIMEOUT_SECS="$2"; shift 2 ;;
    --smoke) ENABLE_SMOKE="true"; shift ;;
    *) die "Unknown arg: $1" ;;
  esac
done

[[ -n "$MODEL" ]] || die "--model required"
[[ -n "$VERSION" ]] || die "--version required"

echo "[validate] Port-forward Triton svc/${SVC}"
kubectl -n "${NS}" port-forward "svc/${SVC}" "${HTTP_PORT_LOCAL}:8000" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill "${PF_PID}" >/dev/null 2>&1 || true' EXIT

wait_port "${HTTP_PORT_LOCAL}" || die "Port-forward failed"

BASE="http://127.0.0.1:${HTTP_PORT_LOCAL}"

echo "[validate] Triton health"
wait_http_code GET "${BASE}/v2/health/ready" 30 200 || die "Triton not ready"

echo "[validate] Repository index (POST, Triton API)"
start="$(date +%s)"
while true; do
  INDEX="$(curl -sS \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"ready": true}' \
    "${BASE}/v2/repository/index" || true)"

  if echo "$INDEX" | grep -q "\"name\"[[:space:]]*:[[:space:]]*\"${MODEL}\""; then
    echo "  ✅ Model present in repository index"
    break
  fi

  now="$(date +%s)"
  if (( now - start >= TIMEOUT_SECS )); then
    echo "❌ Model ${MODEL} never appeared in repository index"
    echo "Last repository index response:"
    echo "$INDEX"
    echo ""
    echo "Check Triton logs:"
    echo "kubectl -n ${NS} logs deploy/triton --tail=300"
    exit 1
  fi
  sleep 2
done

echo "[validate] Model config"
curl -fsS "${BASE}/v2/models/${MODEL}/config" >/dev/null \
  || die "Model config not readable"

echo "[validate] Model metadata"
curl -fsS "${BASE}/v2/models/${MODEL}/versions/${VERSION}" >/dev/null \
  || die "Model metadata not readable"

echo "[validate] Wait READY"
READY_URL="${BASE}/v2/models/${MODEL}/versions/${VERSION}/ready"
wait_http_code GET "${READY_URL}" "${TIMEOUT_SECS}" 200 || {
  echo "❌ Model version not ready"
  echo "Check Triton logs:"
  echo "kubectl -n ${NS} logs deploy/triton --tail=200"
  exit 1
}

echo "  ✅ Model READY"

if [[ "${ENABLE_SMOKE}" == "true" ]]; then
  [[ -f "scripts/smoke_triton_client.py" ]] || die "scripts/smoke_triton_client.py not found"

  echo "[validate] Smoke inference"
  docker run --rm \
    -e TRITON_URL="host.docker.internal:${HTTP_PORT_LOCAL}" \
    -e MODEL="${MODEL}" \
    -e VERSION="${VERSION}" \
    -v "$(pwd)/scripts/smoke_triton_client.py:/app/smoke.py:ro" \
    python:3.11-slim bash -lc "
      pip install --no-cache-dir tritonclient[http] numpy >/dev/null &&
      python /app/smoke.py
    " || die "Smoke inference failed"

  echo "  ✅ Smoke test passed"
else
  echo "[validate] Smoke test skipped"
fi

echo ""
echo "✅ Triton validation successful"
echo "Model: ${MODEL}"
echo "Version: ${VERSION}"