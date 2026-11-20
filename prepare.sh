#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export COMPOSE_FILE=compose.prod.yml
docker compose build --push