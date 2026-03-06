#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# release_model.sh
# ============================================================
# 1) Stage Triton repo layout locally:
#      <model>/config.pbtxt
#      <model>/<version>/model.onnx
# 2) Upload to MinIO bucket using dockerized mc + kubectl port-forward
#    (no kubectl cp; works on macOS with host.docker.internal)
# 3) Validate Triton sees the model version and it is READY
# 4) UPSERT model_registry in Postgres (idempotent)
# ============================================================

MODEL=""
TASK=""
STABLE="1"

CANARY=""
CANARY_PERCENT="0"
ROLLOUT="manual"

SHADOW=""
SHADOW_PERCENT="0"

PREPROCESS='{}'
POSTPROCESS='{}'
DESCRIPTION=""

ONNX_PATH=""
CONFIG_PBTXT=""

TRITON_NAMESPACE="serving"
TRITON_SERVICE="triton"
WAIT_TRITON_SECS="120"

DATA_NAMESPACE="data"
PG_DB="visiondb"
PG_USER="postgres"
PG_SECRET_NAME="postgres-postgresql"

# MinIO (cluster)
MINIO_NAMESPACE="data"
MINIO_SERVICE="minio"
MINIO_ACCESS="minio"
MINIO_SECRET="minio123456"
MINIO_BUCKET="triton-models"

# model_registry defaults aligned with DB schema
RAMP_STEP="10"
RAMP_STEPS="{10,25,50,100}"
RAMP_INTERVAL="120"

ERROR_THRESHOLD="0.03"
RATIO_THRESHOLD="2.0"
MIN_REQUESTS="50"

DRIFT_THRESHOLD="0.30"
DRIFT_RB_THRESHOLD="0.50"
DRIFT_FREEZE="true"
DRIFT_REQUIRED="false"

SHADOW_REQUIRED="false"
SHADOW_AGR_TH="0.95"
SHADOW_MIN_REQ="50"
SHADOW_WIN_SEC="300"

# ============================================================
die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing dependency: $1"; }

json_escape_sql() {
  local s="${1//\'/\'\'}"
  printf "%s" "$s"
}

wait_http_200() {
  local url="$1"
  local timeout="$2"
  local start now
  start="$(date +%s)"
  while true; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 2
  done
}

pick_free_port() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
  else
    for p in $(seq 9000 9100); do
      if ! lsof -iTCP -sTCP:LISTEN -P 2>/dev/null | grep -q ":$p"; then
        echo "$p"
        return 0
      fi
    done
    return 1
  fi
}

cleanup() {
  [[ -n "${PF_MINIO_PID:-}" ]] && kill "${PF_MINIO_PID}" >/dev/null 2>&1 || true
  [[ -n "${TMP_DIR:-}" ]] && rm -rf "${TMP_DIR}" >/dev/null 2>&1 || true
  [[ -n "${MC_CONFIG_DIR:-}" ]] && rm -rf "${MC_CONFIG_DIR}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ============================================================
# Parse args
# ============================================================
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --task) TASK="$2"; shift 2 ;;
    --stable) STABLE="$2"; shift 2 ;;

    --canary) CANARY="$2"; shift 2 ;;
    --canary-percent) CANARY_PERCENT="$2"; shift 2 ;;
    --rollout) ROLLOUT="$2"; shift 2 ;;

    --shadow) SHADOW="$2"; shift 2 ;;
    --shadow-percent) SHADOW_PERCENT="$2"; shift 2 ;;

    --ramp-step) RAMP_STEP="$2"; shift 2 ;;
    --ramp-steps) RAMP_STEPS="$2"; shift 2 ;;
    --ramp-interval) RAMP_INTERVAL="$2"; shift 2 ;;

    --error-threshold) ERROR_THRESHOLD="$2"; shift 2 ;;
    --ratio-threshold) RATIO_THRESHOLD="$2"; shift 2 ;;
    --min-requests) MIN_REQUESTS="$2"; shift 2 ;;

    --drift-threshold) DRIFT_THRESHOLD="$2"; shift 2 ;;
    --drift-rollback-threshold) DRIFT_RB_THRESHOLD="$2"; shift 2 ;;
    --drift-freeze) DRIFT_FREEZE="$2"; shift 2 ;;
    --drift-required) DRIFT_REQUIRED="$2"; shift 2 ;;

    --shadow-required) SHADOW_REQUIRED="$2"; shift 2 ;;
    --shadow-agreement-threshold) SHADOW_AGR_TH="$2"; shift 2 ;;
    --shadow-min-requests) SHADOW_MIN_REQ="$2"; shift 2 ;;
    --shadow-window-seconds) SHADOW_WIN_SEC="$2"; shift 2 ;;

    --preprocess) PREPROCESS="$2"; shift 2 ;;
    --postprocess) POSTPROCESS="$2"; shift 2 ;;
    --description) DESCRIPTION="$2"; shift 2 ;;

    --onnx) ONNX_PATH="$2"; shift 2 ;;
    --config) CONFIG_PBTXT="$2"; shift 2 ;;

    --triton-namespace) TRITON_NAMESPACE="$2"; shift 2 ;;
    --triton-service) TRITON_SERVICE="$2"; shift 2 ;;
    --wait-triton-secs) WAIT_TRITON_SECS="$2"; shift 2 ;;

    --minio-namespace) MINIO_NAMESPACE="$2"; shift 2 ;;
    --minio-service) MINIO_SERVICE="$2"; shift 2 ;;
    --minio-bucket) MINIO_BUCKET="$2"; shift 2 ;;
    --minio-access) MINIO_ACCESS="$2"; shift 2 ;;
    --minio-secret) MINIO_SECRET="$2"; shift 2 ;;

    --pg-namespace) DATA_NAMESPACE="$2"; shift 2 ;;
    --pg-db) PG_DB="$2"; shift 2 ;;
    --pg-user) PG_USER="$2"; shift 2 ;;
    --pg-secret) PG_SECRET_NAME="$2"; shift 2 ;;

    *) die "Unknown arg: $1" ;;
  esac
done

[[ -n "$MODEL" ]] || die "--model required"
[[ -n "$TASK"  ]] || die "--task required"
[[ -n "$ONNX_PATH" ]] || die "--onnx required"
[[ -n "$CANARY" ]] || die "--canary required"
[[ -f "$ONNX_PATH" ]] || die "ONNX not found: $ONNX_PATH"
[[ -z "$CONFIG_PBTXT" || -f "$CONFIG_PBTXT" ]] || die "config.pbtxt not found: $CONFIG_PBTXT"

need kubectl
need curl
need docker
[[ -x "scripts/validate_triton_model.sh" ]] || die "scripts/validate_triton_model.sh not found or not executable"

# lightweight sanity checks
[[ "$CANARY_PERCENT" =~ ^[0-9]+$ ]] || die "--canary-percent must be an integer"
[[ "$SHADOW_PERCENT" =~ ^[0-9]+$ ]] || die "--shadow-percent must be an integer"
(( CANARY_PERCENT >= 0 && CANARY_PERCENT <= 100 )) || die "--canary-percent must be in [0,100]"
(( SHADOW_PERCENT >= 0 && SHADOW_PERCENT <= 100 )) || die "--shadow-percent must be in [0,100]"

# ============================================================
# 1) Stage Triton repo locally
# ============================================================
echo "[1] Stage Triton repo locally"
TMP_DIR="$(mktemp -d)"
mkdir -p "${TMP_DIR}/${MODEL}/${CANARY}"
cp "$ONNX_PATH" "${TMP_DIR}/${MODEL}/${CANARY}/model.onnx"

if [[ -n "$CONFIG_PBTXT" ]]; then
  cp "$CONFIG_PBTXT" "${TMP_DIR}/${MODEL}/config.pbtxt"
else
  echo "  ⚠️ No --config provided. Recommended: always ship config.pbtxt"
fi

echo "  -> staged: ${TMP_DIR}/${MODEL}/${CANARY}/model.onnx"

# ============================================================
# 2) Upload to MinIO via port-forward + dockerized mc
# ============================================================
echo "[2] Upload to MinIO via port-forward (s3://${MINIO_BUCKET}/${MODEL}/...)"

MINIO_PORT_LOCAL="$(pick_free_port)" || die "Could not find a free local port for MinIO port-forward"
MINIO_ENDPOINT_HOST="http://127.0.0.1:${MINIO_PORT_LOCAL}"
MINIO_ENDPOINT_DOCKER="http://host.docker.internal:${MINIO_PORT_LOCAL}"

echo "  -> port-forward svc/${MINIO_SERVICE} (ns=${MINIO_NAMESPACE}) to localhost:${MINIO_PORT_LOCAL}"
kubectl -n "${MINIO_NAMESPACE}" port-forward "svc/${MINIO_SERVICE}" "${MINIO_PORT_LOCAL}:9000" >/dev/null 2>&1 &
PF_MINIO_PID=$!

echo "  -> waiting MinIO ready on ${MINIO_ENDPOINT_HOST} ..."
if ! wait_http_200 "${MINIO_ENDPOINT_HOST}/minio/health/ready" 60; then
  kubectl -n "${MINIO_NAMESPACE}" get svc "${MINIO_SERVICE}" -o wide || true
  die "MinIO not reachable on ${MINIO_ENDPOINT_HOST} (port-forward failed?)"
fi
echo "  ✅ MinIO is ready"

MC_CONFIG_DIR="$(mktemp -d)"

mc_run() {
  docker run --rm \
    -v "${MC_CONFIG_DIR}:/mc" \
    -v "${TMP_DIR}:/staging:ro" \
    minio/mc:latest \
    --config-dir /mc \
    "$@"
}

echo "  -> mc alias set local ${MINIO_ENDPOINT_DOCKER}"
mc_run alias set local "${MINIO_ENDPOINT_DOCKER}" "${MINIO_ACCESS}" "${MINIO_SECRET}" >/dev/null

echo "  -> ensure bucket exists: ${MINIO_BUCKET}"
mc_run mb -p "local/${MINIO_BUCKET}" >/dev/null 2>&1 || true

# remove previous uploaded content for this model/version to avoid stale files
echo "  -> clean existing path for model/version"
mc_run rm --recursive --force "local/${MINIO_BUCKET}/${MODEL}/${CANARY}" >/dev/null 2>&1 || true
if [[ -n "$CONFIG_PBTXT" ]]; then
  mc_run rm --force "local/${MINIO_BUCKET}/${MODEL}/config.pbtxt" >/dev/null 2>&1 || true
fi

echo "  -> upload ${MODEL} folder to bucket"
# trailing slashes are important: contents -> bucket/MODEL/
mc_run cp --recursive "/staging/${MODEL}/" "local/${MINIO_BUCKET}/${MODEL}/" >/dev/null

# quick post-upload checks
mc_run ls "local/${MINIO_BUCKET}/${MODEL}/" >/dev/null
mc_run ls "local/${MINIO_BUCKET}/${MODEL}/${CANARY}/" >/dev/null

echo "  ✅ uploaded to s3://${MINIO_BUCKET}/${MODEL}/"

# ============================================================
# 3) Validate Triton sees the version
# ============================================================
echo "[3] Validate Triton sees model and version is READY"
scripts/validate_triton_model.sh \
  --namespace "${TRITON_NAMESPACE}" \
  --service "${TRITON_SERVICE}" \
  --model "${MODEL}" \
  --version "${CANARY}" \
  --timeout-secs "${WAIT_TRITON_SECS}"

# ============================================================
# 4) UPSERT model_registry (idempotent)
# ============================================================
echo "[4] UPSERT model_registry (idempotent)"
PG_POD="$(kubectl -n "${DATA_NAMESPACE}" get pod -l app.kubernetes.io/instance=postgres -o jsonpath='{.items[0].metadata.name}')"
[[ -n "$PG_POD" ]] || die "Could not find Postgres pod in namespace ${DATA_NAMESPACE}"

PG_PASS="$(kubectl -n "${DATA_NAMESPACE}" get secret "${PG_SECRET_NAME}" -o jsonpath='{.data.postgres-password}' | base64 -d)"
[[ -n "$PG_PASS" ]] || die "Could not read postgres password from secret ${PG_SECRET_NAME}"

DESC_SQL="NULL"
if [[ -n "$DESCRIPTION" ]]; then
  DESC_SQL="'$(json_escape_sql "$DESCRIPTION")'"
fi

CANARY_SQL="'$(json_escape_sql "$CANARY")'"
SHADOW_SQL="NULL"
[[ -n "$SHADOW" ]] && SHADOW_SQL="'$(json_escape_sql "$SHADOW")'"

SQL=$(cat <<SQL_EOF
INSERT INTO model_registry (
  model_name,
  task_type,
  active,

  stable_version,
  canary_version,
  canary_percent,

  shadow_version,
  shadow_percent,
  shadow_required,
  shadow_agreement_threshold,
  shadow_min_requests,
  shadow_window_seconds,

  rollout_strategy,
  ramp_step,
  ramp_steps,
  ramp_interval_seconds,
  last_ramp_at,

  error_threshold,
  ratio_threshold,
  min_requests,

  drift_threshold,
  drift_rollback_threshold,
  drift_freeze,
  drift_required,

  preprocess_config,
  postprocess_config,
  description,
  updated_at
)
VALUES (
  '$(json_escape_sql "$MODEL")',
  '$(json_escape_sql "$TASK")',
  true,

  '$(json_escape_sql "$STABLE")',
  ${CANARY_SQL},
  ${CANARY_PERCENT},

  ${SHADOW_SQL},
  ${SHADOW_PERCENT},
  ${SHADOW_REQUIRED},
  ${SHADOW_AGR_TH},
  ${SHADOW_MIN_REQ},
  ${SHADOW_WIN_SEC},

  '$(json_escape_sql "$ROLLOUT")',
  ${RAMP_STEP},
  '${RAMP_STEPS}'::int[],
  ${RAMP_INTERVAL},
  NULL,

  ${ERROR_THRESHOLD},
  ${RATIO_THRESHOLD},
  ${MIN_REQUESTS},

  ${DRIFT_THRESHOLD},
  ${DRIFT_RB_THRESHOLD},
  ${DRIFT_FREEZE},
  ${DRIFT_REQUIRED},

  '$(json_escape_sql "$PREPROCESS")'::jsonb,
  '$(json_escape_sql "$POSTPROCESS")'::jsonb,
  ${DESC_SQL},
  now()
)
ON CONFLICT (model_name) DO UPDATE SET
  task_type = EXCLUDED.task_type,
  active = EXCLUDED.active,

  stable_version = EXCLUDED.stable_version,
  canary_version = EXCLUDED.canary_version,
  canary_percent = EXCLUDED.canary_percent,

  shadow_version = EXCLUDED.shadow_version,
  shadow_percent = EXCLUDED.shadow_percent,
  shadow_required = EXCLUDED.shadow_required,
  shadow_agreement_threshold = EXCLUDED.shadow_agreement_threshold,
  shadow_min_requests = EXCLUDED.shadow_min_requests,
  shadow_window_seconds = EXCLUDED.shadow_window_seconds,

  rollout_strategy = EXCLUDED.rollout_strategy,
  ramp_step = EXCLUDED.ramp_step,
  ramp_steps = EXCLUDED.ramp_steps,
  ramp_interval_seconds = EXCLUDED.ramp_interval_seconds,
  last_ramp_at = EXCLUDED.last_ramp_at,

  error_threshold = EXCLUDED.error_threshold,
  ratio_threshold = EXCLUDED.ratio_threshold,
  min_requests = EXCLUDED.min_requests,

  drift_threshold = EXCLUDED.drift_threshold,
  drift_rollback_threshold = EXCLUDED.drift_rollback_threshold,
  drift_freeze = EXCLUDED.drift_freeze,
  drift_required = EXCLUDED.drift_required,

  preprocess_config = EXCLUDED.preprocess_config,
  postprocess_config = EXCLUDED.postprocess_config,
  description = EXCLUDED.description,
  updated_at = now();
SQL_EOF
)

kubectl -n "${DATA_NAMESPACE}" exec -i "${PG_POD}" -- bash -lc \
  "PGPASSWORD='${PG_PASS}' psql -U ${PG_USER} -d ${PG_DB} -v ON_ERROR_STOP=1" <<EOF
${SQL}
EOF

echo ""
echo "✅ Release complete."
echo "Check:"
echo "  kubectl -n ${DATA_NAMESPACE} exec -it ${PG_POD} -- bash -lc \"PGPASSWORD=*** psql -U ${PG_USER} -d ${PG_DB} -c \\\"select model_name, stable_version, canary_version, canary_percent, shadow_version, shadow_percent, rollout_strategy from model_registry where model_name='${MODEL}';\\\"\""