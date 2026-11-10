#!/usr/bin/env bash
# Uso: APP_ENV=demo ./run.sh   (padrão: demo, porta 8000)
set -euo pipefail
cd "$(dirname "$0")/server"
export APP_ENV="${APP_ENV:-demo}"
exec python3 -m uvicorn "app.main:create_app" --factory --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --timeout-graceful-shutdown 3
