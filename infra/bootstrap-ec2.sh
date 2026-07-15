#!/usr/bin/env bash

set -Eeuo pipefail

dnf install --assumeyes docker
systemctl enable --now docker

install -d -m 700 /opt/aiserver1
if [[ ! -f /opt/aiserver1/.env ]]; then
  printf 'APP_ENV=production\nLOG_LEVEL=INFO\n' >/opt/aiserver1/.env
fi
chmod 600 /opt/aiserver1/.env

docker --version
systemctl is-active docker
