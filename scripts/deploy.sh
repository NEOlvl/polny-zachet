#!/usr/bin/env bash
# Обновление бота на сервере: забрать код, пересобрать и перезапустить контейнеры.
# Контент (content/*.yaml, кроме тестового) и база (data/) в git не лежат и не затрагиваются.
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only
# профиль miniapp (сервис caddy) — только когда задан реальный домен в MINIAPP_URL
if grep -qE '^MINIAPP_URL=https?://[^/[:space:]]+' .env; then
  docker compose --profile miniapp up -d --build
else
  docker compose up -d --build
fi
docker compose logs --tail 30 bot
