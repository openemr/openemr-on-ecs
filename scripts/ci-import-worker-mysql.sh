#!/usr/bin/env bash
# Bring up TLS OpenEMR/MariaDB, build import fixtures, and run the worker harness.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${ROOT}/compose"
COMPOSE_FILE="docker-compose.test-ssl.yml"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-openemrimportci}"

WORKDIR="${IMPORT_CI_WORKDIR:-${ROOT}/.import-worker-mysql-ci}"
RAW_SITES="${WORKDIR}/raw-sites"
FIXTURES="${WORKDIR}/fixtures"
SITES_MOUNT="${WORKDIR}/sites-mount"
LOG_DIR="${WORKDIR}/logs"
IMAGE="${IMPORT_WORKER_IMAGE:-openemr-import-worker:ci}"
IMAGE_PLATFORM="${IMPORT_WORKER_PLATFORM:-linux/arm64}"
OPENEMR_CONTAINER="${OPENEMR_CONTAINER:-openemr-container-test-ssl}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-mysql-test-ssl}"
WAIT_SECONDS="${IMPORT_CI_WAIT_SECONDS:-1200}"

mkdir -p "${WORKDIR}" "${LOG_DIR}"
chmod 700 "${WORKDIR}"

cleanup() {
  local status=$?
  set +e
  echo "Cleaning up compose stack (${COMPOSE_PROJECT_NAME})..."
  (
    cd "${COMPOSE_DIR}"
    docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans
  ) >"${LOG_DIR}/compose-down.log" 2>&1 || true
  exit "${status}"
}
trap cleanup EXIT

log() {
  printf '%s\n' "$*"
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

# Make bind-mount paths writable by the importer user (uid 1000) without host sudo.
own_importer() {
  local path
  for path in "$@"; do
    docker run --rm --platform "${IMAGE_PLATFORM}" --user 0:0 --entrypoint chown \
      -v "${path}:${path}" \
      "${IMAGE}" \
      -R 1000:1000 "${path}"
  done
}

log "==> Building import worker image (${IMAGE})"
docker buildx build \
  --platform "${IMAGE_PLATFORM}" \
  --target ci \
  --load \
  --tag "${IMAGE}" \
  "${ROOT}/tools/openemr-import-worker"

log "==> Starting SSL compose stack"
cd "${COMPOSE_DIR}"
compose up -d

log "==> Waiting for MySQL TLS service"
for ((i = 1; i <= 60; i++)); do
  if compose logs mysql-test-ssl 2>/dev/null | grep -qiE 'ready for connections|mysqld.*ready'; then
    log "MySQL is ready"
    break
  fi
  if ((i == 60)); then
    compose logs mysql-test-ssl | tee "${LOG_DIR}/mysql.log"
    log "ERROR: MySQL failed to become ready"
    exit 1
  fi
  sleep 2
done

log "==> Waiting for OpenEMR bootstrap completion (up to ${WAIT_SECONDS}s)"
deadline=$((SECONDS + WAIT_SECONDS))
ready=0
while ((SECONDS < deadline)); do
  if docker exec "${OPENEMR_CONTAINER}" test -f /var/www/localhost/htdocs/openemr/sites/docker-completed 2>/dev/null; then
    log "Found sites/docker-completed"
    ready=1
    break
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "${OPENEMR_CONTAINER}" 2>/dev/null || echo false)" != "true" ]]; then
    if docker cp "${OPENEMR_CONTAINER}:/var/www/localhost/htdocs/openemr/sites/docker-completed" \
      "${LOG_DIR}/docker-completed" 2>/dev/null; then
      log "Found sites/docker-completed in stopped container after setup"
      ready=1
      break
    fi
    compose logs --tail=200 openemr-test-ssl | tee "${LOG_DIR}/openemr.log"
    log "ERROR: OpenEMR stopped before bootstrap completed"
    exit 1
  fi
  sleep 5
done
if ((ready != 1)); then
  compose logs --tail=200 openemr-test-ssl | tee "${LOG_DIR}/openemr.log"
  compose logs --tail=100 mysql-test-ssl | tee "${LOG_DIR}/mysql.log"
  log "ERROR: OpenEMR failed to initialize within ${WAIT_SECONDS}s"
  exit 1
fi

# Give openemr.sh a brief moment to finish key/material writes after the marker.
sleep 5

log "==> Exporting OpenEMR sites tree"
rm -rf "${RAW_SITES}" "${FIXTURES}" "${SITES_MOUNT}"
mkdir -p "${RAW_SITES}" "${FIXTURES}" "${SITES_MOUNT}"
# OpenEMR ships some site dirs as mode 0500; a filesystem docker cp preserves
# that and then fails mid-copy. Stream a tar instead. Prefer docker exec when
# the container is still up; fall back to docker cp's tar stdout for a
# container that exited after writing docker-completed (QEMU Apache mutex).
if [[ "$(docker inspect -f '{{.State.Running}}' "${OPENEMR_CONTAINER}" 2>/dev/null || echo false)" == "true" ]]; then
  docker exec "${OPENEMR_CONTAINER}" \
    tar -C /var/www/localhost/htdocs/openemr/sites -cf - . \
    | tar -C "${RAW_SITES}" -xf -
else
  docker cp "${OPENEMR_CONTAINER}:/var/www/localhost/htdocs/openemr/sites" - \
    | tar -C "${RAW_SITES}" --strip-components=1 -xf -
fi
chmod -R u+rwX "${RAW_SITES}"
docker cp "${MYSQL_CONTAINER}:/mysql-ssl-certs/ca-cert.pem" "${FIXTURES}/ca-cert.pem"
chmod 644 "${FIXTURES}/ca-cert.pem"

NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "${MYSQL_CONTAINER}")"
if [[ -z "${NETWORK}" ]]; then
  NETWORK="${COMPOSE_PROJECT_NAME}_openemr-test-network"
fi
log "Using docker network: ${NETWORK}"

# Ownership for bind mounts only — keep logs/ writable by the CI runner user.
own_importer "${RAW_SITES}" "${FIXTURES}" "${SITES_MOUNT}"

log "==> Preparing synthetic native-backup fixtures"
docker run --rm \
  --platform "${IMAGE_PLATFORM}" \
  --user 1000:1000 \
  --network "${NETWORK}" \
  --entrypoint python \
  -e MYSQL_HOST=mysql-test-ssl \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USERNAME=root \
  -e MYSQL_PASSWORD=testpass \
  -e MYSQL_DATABASE=openemr \
  -e MYSQL_SSL_CA=/certs/ca-cert.pem \
  -v "${FIXTURES}/ca-cert.pem:/certs/ca-cert.pem:ro" \
  -v "${RAW_SITES}:/fixtures-raw:ro" \
  -v "${FIXTURES}:/fixtures" \
  -v "${SITES_MOUNT}:/mnt/openemr-sites" \
  "${IMAGE}" \
  /app/ci_prepare_mysql_fixtures.py \
  --raw-sites /fixtures-raw \
  --fixtures-dir /fixtures \
  --sites-mount /mnt/openemr-sites \
  | tee "${LOG_DIR}/prepare.stdout"

if ! tail -n 1 "${LOG_DIR}/prepare.stdout" | grep -q '"status": "passed"'; then
  log "ERROR: fixture preparation failed"
  exit 1
fi

own_importer "${FIXTURES}" "${SITES_MOUNT}"

log "==> Running happy-path + rollback + recovery import harness"
docker run --rm \
  --platform "${IMAGE_PLATFORM}" \
  --user 1000:1000 \
  --network "${NETWORK}" \
  --entrypoint python \
  -e MYSQL_HOST=mysql-test-ssl \
  -e MYSQL_PORT=3306 \
  -e MYSQL_USERNAME=root \
  -e MYSQL_PASSWORD=testpass \
  -e MYSQL_DATABASE=openemr \
  -e MYSQL_SSL_CA=/certs/ca-cert.pem \
  -e TARGET_OPENEMR_VERSION=8.4.1 \
  -e TARGET_DATABASE_VERSION=543 \
  -e OPENEMR_SITES_MOUNT_ROOT=/mnt/openemr-sites \
  -e IMPORT_SOURCE_TAR=/fixtures/source.tar \
  -e IMPORT_FIXTURES_DIR=/fixtures \
  -e IMPORT_MIGRATION_ID=import-0123456789abcdef \
  -e IMPORT_SCENARIO=all \
  -v "${FIXTURES}/ca-cert.pem:/certs/ca-cert.pem:ro" \
  -v "${FIXTURES}:/fixtures" \
  -v "${SITES_MOUNT}:/mnt/openemr-sites" \
  "${IMAGE}" \
  /app/ci_live_mysql_import.py --scenario all \
  | tee "${LOG_DIR}/harness.stdout"

if ! tail -n 1 "${LOG_DIR}/harness.stdout" | grep -q '"status": "passed"'; then
  log "ERROR: import harness failed"
  exit 1
fi

log "==> Import worker MySQL integration passed"
# Successful runs leave workdir for local inspection unless CI clears the runner.
