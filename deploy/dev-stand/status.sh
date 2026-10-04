#!/usr/bin/env bash
# NOEZEMA dev stand status (T7.59(b)): units, DB, question queue, last session, LLM availability,
# code version, executor mode. Read-only: it changes nothing and never prints secrets (AGENTS §5).
#
#   ./status.sh              everything
#   ./status.sh --no-llm     skip the LLM probe (use this off the stand VM)
#   ./status.sh --no-web     skip the HTTP probes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${NOEZEMA_DEV_ENV_FILE:-/etc/noezema/dev.env}"
DB_CONTAINER="${NOEZEMA_DEV_DB_CONTAINER:-noezema-dev-db}"
DB_NAME="${NOEZEMA_DEV_DB_NAME:-noezema-dev}"
DB_USER=noezema
UNIT_PREFIX=noezema-dev

SKIP_LLM=false
SKIP_WEB=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-llm) SKIP_LLM=true ;;
    --no-web) SKIP_WEB=true ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

ENV_TEXT=""
if [[ -r "$ENV_FILE" ]]; then
  ENV_TEXT="$(cat "$ENV_FILE")"
elif have sudo; then
  ENV_TEXT="$(sudo cat "$ENV_FILE" 2>/dev/null || true)"
fi
env_get() { printf '%s\n' "$ENV_TEXT" | sed -n "s/^$1=//p" | tail -1; }

if [[ -z "$ENV_TEXT" ]]; then
  echo "env file $ENV_FILE is not readable (run as the stand user or with sudo) — partial report follows" >&2
fi
WEB_HOST="$(env_get NOEZEMA_WEB_HOST)"; WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="$(env_get NOEZEMA_WEB_PORT)"; WEB_PORT="${WEB_PORT:-8321}"
LLM_BASE_URL="$(env_get NOEZEMA_LLM_BASE_URL)"
EXECUTOR="$(env_get NOEZEMA_TOOL_EXECUTOR)"; EXECUTOR="${EXECUTOR:-stub (default)}"
SANDBOX_IMAGE="$(env_get NOEZEMA_SANDBOX_IMAGE)"
DATA_ROOT="$(env_get NOEZEMA_DATA_ROOT)"

PY="$REPO_ROOT/.venv/bin/python"; have "$PY" || PY=python3

say "код"
if have git; then
  note "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD) @ $(git -C "$REPO_ROOT" rev-parse --short HEAD)$(git -C "$REPO_ROOT" status --porcelain | grep -q . && echo ' (working tree dirty)')"
else
  note "git недоступен"
fi

say "юниты стенда"
if have systemctl; then
  for unit in $(systemctl list-units --all --plain --no-legend "$UNIT_PREFIX*" 2>/dev/null | awk '{print $1}' | sort); do
    printf '   %-34s active=%-8s enabled=%s\n' "$unit" \
      "$(systemctl is-active "$unit" 2>/dev/null || true)" \
      "$(systemctl is-enabled "$unit" 2>/dev/null || echo -)"
  done
  for timer in noezema-dev-tick.timer noezema-dev-maint.timer noezema-dev-unit-state.timer; do
    next="$(systemctl show -p NextElapseUSecRealtime --value "$timer" 2>/dev/null || true)"
    printf '   %-34s next=%s\n' "$timer" "${next:-n/a}"
  done
  note "последний код: $(for u in noezema-dev-tick.service noezema-dev-maint.service; do printf '%s=%s ' "$u" "$(systemctl show -p ExecMainStatus --value "$u" 2>/dev/null || true)"; done)"
else
  note "systemctl недоступен (стенд живёт без systemd?)"
fi

say "docker"
if have docker; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
    printf '   %-34s state=%s health=%s ports=%s\n' "$DB_CONTAINER" \
      "$(docker inspect -f '{{.State.Status}}' "$DB_CONTAINER")" \
      "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$DB_CONTAINER")" \
      "$(docker inspect -f '{{range $p, $conf := .NetworkSettings.Ports}}{{(index $conf 0).HostIp}}:{{(index $conf 0).HostPort}} {{end}}' "$DB_CONTAINER")"
  else
    note "контейнер $DB_CONTAINER не создан (запусти bootstrap.sh)"
  fi
  if [[ -n "$SANDBOX_IMAGE" ]]; then
    if docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then note "образ $SANDBOX_IMAGE: есть"; else note "образ $SANDBOX_IMAGE: ОТСУТСТВУЕТ — sandbox-сессии не запустятся"; fi
  fi
  note "сессионных контейнеров noezema-sb-* сейчас: $(docker ps -a --format '{{.Names}}' | grep -c '^noezema-sb-' || true)"
else
  note "docker недоступен"
fi

say "БД: очередь вопросов и последняя сессия"
if have docker && docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  q() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc "$1" 2>/dev/null || true; }
  note "миграции: $(q 'SELECT version FROM alembic_version') | БД: $DB_NAME | data_root: ${DATA_ROOT:-?}"
  note "конфиг: active=$(q "SELECT s.activation_mode||'/'||s.activation_state FROM runtime_config_heads h JOIN config_snapshots s ON s.id=h.active_config_snapshot_id WHERE h.scope='global'")"
  note "вопросы по состояниям:"
  q "SELECT '   - '||state||': '||count(*) FROM questions GROUP BY state ORDER BY state" | sed 's/^/ /'
  note "(из них origin='message' — принятых оператором: $(q "SELECT count(*) FROM questions WHERE origin='message'"))"
  note "очередь (candidate, FIFO-порядок):"
  q "SELECT '   #'||row_number() OVER (ORDER BY priority DESC, created_at ASC, id)||'  '||left(id::text,8)||' pr='||priority||'  '||origin||'  '||left(text,70) FROM questions WHERE state='candidate' ORDER BY priority DESC, created_at ASC, id LIMIT 10" | sed 's/^/ /'
  note "последняя сессия:"
  q "SELECT '   '||left(s.id::text,8)||'  '||s.state||'  reason='||coalesce(s.termination_reason,'-')||'  вопрос='||coalesce(left(q.text,50),'-') FROM sessions s LEFT JOIN questions q ON q.id=s.question_id ORDER BY s.created_at DESC NULLS LAST LIMIT 1" | sed 's/^/ /'
  note "нерешённые commit_attempts (блокируют wake): $(q "SELECT count(*) FROM commit_attempts WHERE state NOT IN ('committed','rolled_back')")"
  note "wake-состояние: $(q "SELECT coalesce(paused_reason,'not paused')||', consecutive_failures='||consecutive_failures FROM wake_scheduler_state LIMIT 1")"
else
  note "Postgres недоступен — пропущено"
fi

if ! $SKIP_WEB; then
  say "web API (GET, без токена)"
  "$PY" - "$WEB_HOST" "$WEB_PORT" <<'PY'
import json, sys, urllib.request
host, port = sys.argv[1], sys.argv[2]
def get(path):
    with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=8) as r:
        return json.loads(r.read())
try:
    s = get("/api/v1/status")
    h = s.get("host") or {}
    print(f"   host: healthy={h.get('healthy')} recovery={h.get('recovery_state')} "
          f"unit_state={h.get('unit_state')} db_reachable={h.get('db_reachable')}")
    if h.get("warnings"): print("   warnings:", ", ".join(h["warnings"]))
    print(f"   узел: {s.get('node_state')} · сессия: "
          f"{(s.get('session') or {}).get('state', 'нет активной')} · counts: {s.get('counts')}")
    w = s.get("wake") or {}
    if w: print("   wake:", {k: w[k] for k in ("consecutive_failures", "paused_reason", "next_wake_at") if k in w})
except Exception as exc:
    print(f"   /api/v1/status недоступен: {exc}")
try:
    q = get("/api/v1/questions?limit=5")
    print(f"   очередь вопросов: {q.get('count')} строк в выдаче")
    for row in (q.get("questions") or [])[:5]:
        print(f"   #{row.get('position')} {str(row.get('id'))[:8]} {row.get('state')} pr={row.get('priority')} "
              f"{row.get('origin')} session={(row.get('session') or {}).get('state', '-')} "
              f"{str(row.get('text'))[:60]}")
except Exception as exc:
    print(f"   /api/v1/questions недоступен: {exc}")
PY
else
  note "web-проверки пропущены (--no-web)"
fi

say "LLM (доступность модели)"
if $SKIP_LLM; then
  note "пропуск (--no-llm): на .87 к 192.168.1.42 не обращаемся"
elif [[ -z "$LLM_BASE_URL" ]]; then
  note "NOEZEMA_LLM_BASE_URL не задан"
else
  "$PY" - "${LLM_BASE_URL%/}/models" <<'PY'
import json, sys, urllib.request
url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read())
    ids = [m.get("id") for m in data.get("data", [])]
    print(f"   {url}: доступен, модели: {', '.join(map(str, ids[:5])) or '—'}")
except Exception as exc:
    print(f"   {url}: НЕДОСТУПЕН ({type(exc).__name__}: {exc}) — сессии будут падать на LLM")
PY
fi

say "исполнитель инструментов"
note "NOEZEMA_TOOL_EXECUTOR=$EXECUTOR"
note "образ: ${SANDBOX_IMAGE:-не задан} · work_root: $(env_get NOEZEMA_SANDBOX_WORK_ROOT)"
if [[ "$EXECUTOR" == "sandbox" ]]; then
  note "в этом режиме сессии стартуют только если образ и docker доступны (иначе отказ exit 78, откат на stub молча не делается)"
fi
