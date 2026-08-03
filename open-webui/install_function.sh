#!/usr/bin/env bash
# Ставит функции из ./functions в работающий Open WebUI через его REST API.
# Идемпотентно: повторный запуск обновляет содержимое, а не плодит копии.
# Функции Open WebUI хранит строками в SQLite — файл в контейнер не подложить,
# ставить можно только так.
#
#   ./install_function.sh                 # поставить всё из ./functions
#   ./install_function.sh qwen_auto       # поставить одну
#   DRY_RUN=1 ./install_function.sh       # показать, что будет сделано
#
# Учётка берётся из .env (OWUI_EMAIL / OWUI_PASSWORD). На пустом инстансе первый
# signup автоматически становится админом.

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

BASE="${OWUI_URL:-http://localhost:3111}"
EMAIL="${OWUI_EMAIL:?задать OWUI_EMAIL в .env}"
PASS="${OWUI_PASSWORD:?задать OWUI_PASSWORD в .env}"
DRY_RUN="${DRY_RUN:-0}"

api() { # api <method> <path> [json]
  curl -sS -X "$1" "$BASE$2" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    ${3+--data-binary @-} <<<"${3-}"
}

echo "==> жду готовности $BASE"
for i in $(seq 1 60); do
  curl -sf -m 3 "$BASE/health" >/dev/null 2>&1 && break
  [ "$i" = 60 ] && { echo "Open WebUI не поднялся"; exit 1; }
  sleep 2
done

echo "==> вход"
TOKEN=$(curl -sS -X POST "$BASE/api/v1/auths/signin" -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg e "$EMAIL" --arg p "$PASS" '{email:$e,password:$p}')" \
  | jq -r '.token // empty')

if [ -z "$TOKEN" ]; then
  echo "   signin не прошёл — регистрирую первого пользователя (станет админом)"
  TOKEN=$(curl -sS -X POST "$BASE/api/v1/auths/signup" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg e "$EMAIL" --arg p "$PASS" '{name:"admin",email:$e,password:$p}')" \
    | jq -r '.token // empty')
fi
[ -n "$TOKEN" ] || { echo "не удалось получить токен"; exit 1; }

TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && mapfile -t TARGETS < <(cd functions && ls *.py | sed 's/\.py$//')

EXISTING=$(api GET /api/v1/functions/ | jq -r '.[].id')

for id in "${TARGETS[@]}"; do
  src="functions/$id.py"
  [ -f "$src" ] || { echo "!! нет $src"; continue; }

  name=$(sed -n 's/^title:[[:space:]]*//p' "$src" | head -1); name="${name:-$id}"
  desc=$(sed -n 's/^description:[[:space:]]*//p' "$src" | head -1)

  payload=$(jq -nc --arg id "$id" --arg name "$name" --arg desc "$desc" \
    --rawfile content "$src" \
    '{id:$id, name:$name, content:$content, meta:{description:$desc, manifest:{}}}')

  if [ "$DRY_RUN" = 1 ]; then
    echo "-- [dry-run] $id ($name), $(wc -c <"$src") байт, $(grep -q "^$id\$" <<<"$EXISTING" && echo обновление || echo создание)"
    continue
  fi

  if grep -q "^$id\$" <<<"$EXISTING"; then
    echo "==> обновляю $id"
    api POST "/api/v1/functions/id/$id/update" "$payload" | jq -r '.id // .detail'
  else
    echo "==> создаю $id"
    api POST /api/v1/functions/create "$payload" | jq -r '.id // .detail'
  fi

  active=$(api GET "/api/v1/functions/id/$id" | jq -r '.is_active')
  if [ "$active" != "true" ]; then
    api POST "/api/v1/functions/id/$id/toggle" >/dev/null
    echo "    включена"
  fi
done

echo
echo "==> модели, видимые интерфейсу:"
api GET /api/models | jq -r '.data[] | "    \(.id)\t\(.name)"'
