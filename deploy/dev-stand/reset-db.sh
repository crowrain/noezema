#!/usr/bin/env bash
# NOEZEMA dev stand database reset (T7.59(b)): recreate the DEV database from scratch, migrate it,
# re-activate the stand configuration (config-v20, T7.83(б); rollback: NOEZEMA_DEV_CONFIG_PAYLOAD points to
# docs/eval/config-v19-payload.json). Nothing else is touched: the Postgres container and its volume stay,
# no production path and no eval/smoke database is ever a target (checked below, not by convention).
#
#   ./reset-db.sh            interactive: prints what will be lost and asks for the DB name
#   ./reset-db.sh --yes      non-interactive (for scripts); the checks still apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${NOEZEMA_DEV_ENV_FILE:-/etc/noezema/dev.env}"
DB_CONTAINER="${NOEZEMA_DEV_DB_CONTAINER:-noezema-dev-db}"
DB_VOLUME="${NOEZEMA_DEV_DB_VOLUME:-noezema-dev-pgdata}"
DB_NAME="${NOEZEMA_DEV_DB_NAME:-noezema-dev}"
DB_USER=noezema
DB_PORT="${NOEZEMA_DEV_DB_PORT:-}"   # empty -> taken from the env file / the container (bootstrap may have auto-picked 5433+)
CONFIG_PAYLOAD="${NOEZEMA_DEV_CONFIG_PAYLOAD:-$REPO_ROOT/docs/eval/config-v20-payload.json}"
ASSUME_YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=true ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { printf '\033[31mreset-db: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[[ "$DB_NAME" == noezema-dev* ]] || die "refusing: DB '$DB_NAME' is not a dev stand database"
case "$DB_NAME" in *eval*|*smoke*) die "refusing: eval/smoke databases are off-limits (SELECT only)" ;; esac
have docker || die "docker is not available"
docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER" || die "container $DB_CONTAINER is not running"

psql_as() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$1" -Atc "$2"; }

ENV_TEXT=""
if [[ -r "$ENV_FILE" ]]; then ENV_TEXT="$(cat "$ENV_FILE")"; elif have sudo; then ENV_TEXT="$(sudo cat "$ENV_FILE" 2>/dev/null || true)"; fi
db_password="$(printf '%s\n' "$ENV_TEXT" | sed -n 's/^NOEZEMA_DB_PASSWORD=//p' | tail -1)"
[[ -n "$db_password" ]] || die "NOEZEMA_DB_PASSWORD not readable from $ENV_FILE"

# Порт БД: явный env > записанный bootstrap'ом в env-файле > опубликованный портом контейнера.
if [[ -z "$DB_PORT" ]]; then
  DB_PORT="$(printf '%s\n' "$ENV_TEXT" | sed -n 's/^NOEZEMA_DEV_DB_PORT=//p' | tail -1)"
fi
if [[ -z "$DB_PORT" ]]; then
  DB_PORT="$(docker inspect -f '{{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{(index $conf 0).HostPort}}{{end}}{{end}}' "$DB_CONTAINER" 2>/dev/null | head -1)"
fi
[[ -n "$DB_PORT" ]] || die "не понять, на каком порту опубликован $DB_CONTAINER: задайте NOEZEMA_DEV_DB_PORT=<порт>"

counts="$(psql_as "$DB_NAME" "SELECT 'questions='||count(*) FROM questions" 2>/dev/null || true)"
sessions="$(psql_as "$DB_NAME" "SELECT 'sessions='||count(*) FROM sessions" 2>/dev/null || true)"
nonterminal="$(psql_as "$DB_NAME" "SELECT count(*) FROM sessions WHERE state NOT IN ('succeeded','succeeded_partial','failed','cancelled')" 2>/dev/null || echo 0)"

echo "Это уничтожит в БД '$DB_NAME' (контейнер $DB_CONTAINER, том $DB_VOLUME не трогается):"
echo "  ${counts:-questions=0}, ${sessions:-sessions=0}, знания, сообщения, аудит-события, снапшоты конфигурации."
if [[ "$nonterminal" != "0" ]]; then
  die "в БД есть незавершённые сессии ($nonterminal) — сначала дождись их терминации или останови tick: sudo systemctl stop noezema-dev-tick.timer noezema-dev-maint.timer"
fi

if ! $ASSUME_YES; then
  printf 'Для продолжения впиши имя базы данных: '
  read -r answer
  [[ "$answer" == "$DB_NAME" ]] || die "имя не совпало — ничего не изменено"
fi

echo "остановка таймеров, чтобы reset не столкнулся с новым wake…"
if have systemctl; then
  sudo systemctl stop noezema-dev-tick.timer noezema-dev-maint.timer noezema-dev-unit-state.timer || true
fi

echo "пересоздание базы…"
psql_as postgres "DROP DATABASE IF EXISTS \"$DB_NAME\" WITH (FORCE)"
psql_as postgres "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\""

url="postgresql+asyncpg://$DB_USER:$db_password@127.0.0.1:$DB_PORT/$DB_NAME"
(cd "$REPO_ROOT" && env NOEZEMA_DATABASE_URL="$url" "$REPO_ROOT/.venv/bin/python" -m alembic upgrade head)

echo "активация конфигурации (config-v20)…"
(cd "$REPO_ROOT" && env NOEZEMA_DATABASE_URL="$url" "$REPO_ROOT/.venv/bin/python" -m hostctl.cli activate-online \
  --payload "$CONFIG_PAYLOAD" --reason "T7.83 dev-stand: reset-db re-activation (config-v20)" --drain-wait-seconds 120)

if have systemctl; then
  sudo systemctl start noezema-dev-unit-state.timer noezema-dev-tick.timer noezema-dev-maint.timer || true
fi

echo "готово: очередь пуста, конфиг активен. Следующий шаг — задать вопрос ('hostctl ask' или форма на странице)."
