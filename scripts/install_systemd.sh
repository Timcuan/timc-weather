#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install_systemd.sh <linux-user>"
  exit 1
fi

APP_USER="${1:-}"
if [[ -z "$APP_USER" ]]; then
  echo "Usage: sudo bash scripts/install_systemd.sh <linux-user>"
  exit 1
fi

SERVICE_SRC="ops/systemd/weatherbot.service"
SERVICE_DST="/etc/systemd/system/weatherbot@.service"

if [[ ! -f "$SERVICE_SRC" ]]; then
  echo "Missing $SERVICE_SRC"
  exit 1
fi

install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
systemctl daemon-reload
systemctl enable "weatherbot@${APP_USER}"
systemctl restart "weatherbot@${APP_USER}"
systemctl status "weatherbot@${APP_USER}" --no-pager
