#!/usr/bin/env bash

set -Eeuo pipefail

: "${IMAGE:?IMAGE environment variable is required}"
: "${CONTAINER_NAME:?CONTAINER_NAME environment variable is required}"
: "${APP_PORT:?APP_PORT environment variable is required}"
: "${AWS_REGION:?AWS_REGION environment variable is required}"
: "${GHCR_USERNAME:?GHCR_USERNAME environment variable is required}"
: "${GHCR_TOKEN_PARAMETER:?GHCR_TOKEN_PARAMETER environment variable is required}"

ENV_FILE="${ENV_FILE:-/opt/${CONTAINER_NAME}/.env}"

command -v aws >/dev/null || { echo "AWS CLI is required on the EC2 instance" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker is required on the EC2 instance" >&2; exit 1; }

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file does not exist: ${ENV_FILE}" >&2
  exit 1
fi

ghcr_token="$(
  aws ssm get-parameter \
    --name "${GHCR_TOKEN_PARAMETER}" \
    --with-decryption \
    --query Parameter.Value \
    --output text \
    --region "${AWS_REGION}"
)"

# Called indirectly by the EXIT trap.
# shellcheck disable=SC2329
cleanup_credentials() {
  docker logout ghcr.io >/dev/null 2>&1 || true
  unset ghcr_token
}
trap cleanup_credentials EXIT

printf '%s' "${ghcr_token}" | docker login ghcr.io \
  --username "${GHCR_USERNAME}" \
  --password-stdin
image_to_deploy="${IMAGE}"
docker pull "${image_to_deploy}"

previous_image=""
if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  previous_image="$(docker container inspect --format '{{.Config.Image}}' "${CONTAINER_NAME}")"
fi

run_container() {
  local image="$1"

  docker run \
    --detach \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    --env-file "${ENV_FILE}" \
    --publish "${APP_PORT}:8000" \
    "${image}"
}

rollback() {
  docker logs --tail 100 "${CONTAINER_NAME}" >&2 2>/dev/null || true
  docker rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  if [[ -n "${previous_image}" ]]; then
    echo "Deployment failed; restoring ${previous_image}" >&2
    run_container "${previous_image}" >/dev/null
  else
    echo "Deployment failed and there is no previous image to restore" >&2
  fi
}

docker rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true

if ! run_container "${image_to_deploy}" >/dev/null; then
  rollback
  exit 1
fi

for _ in $(seq 1 18); do
  health_status="$(
    docker container inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' \
      "${CONTAINER_NAME}"
  )"

  case "${health_status}" in
    healthy)
      echo "Deployment completed: ${image_to_deploy}"
      docker image prune --force --filter 'until=168h' >/dev/null
      exit 0
      ;;
    unhealthy)
      break
      ;;
  esac

  sleep 5
done

echo "Container did not become healthy" >&2
rollback
exit 1
