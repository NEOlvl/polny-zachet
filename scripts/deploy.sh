#!/usr/bin/env bash
# Обновление бота на сервере: забрать код, пересобрать и перезапустить контейнеры.
# Контент (content/*.yaml, кроме тестового) и база (data/) в git не лежат и не затрагиваются.
set -euo pipefail

cd "$(dirname "$0")/.."

git pull --ff-only
docker compose up -d --build
docker compose logs --tail 30 bot
