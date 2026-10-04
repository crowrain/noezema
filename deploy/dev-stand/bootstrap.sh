#!/usr/bin/env bash
# NOEZEMA dev stand bootstrap (T7.59(b)) — target: single-user Ubuntu 24.04 VM, no GPU.
#
# Idempotent by construction: every step first looks at the current state and skips what is
# already in place; existing secrets/config are never overwritten without --force.
#   ./bootstrap.sh --dry-run             print the plan, change nothing
#   ./bootstrap.sh                       do it (sudo is used where the OS requires it)
#   ./bootstrap.sh --no-docker-install   skip apt installs (docker already present)
#   ./bootstrap.sh --no-units            skip installing/activating systemd units
#   ./bootstrap.sh --stub-executor       sessions use the dev stub instead of containers
#   ./bootstrap.sh --force               regenerate env file / venv even if they exist
#
# The stand NEVER touches the production contour paths (/var/lib/noezema, /run/noezema) and
# never works on a database that is not noezema-dev* — enforced below, not by convention.
# Secrets (DB password, admin token) are generated into $ENV_FILE with mode 0600, are never
# printed, and are masked in every --dry-run line (AGENTS §5).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── knobs: env override first, flags last ─────────────────────────────────────────────
STAND_USER="${NOEZEMA_DEV_USER:-${SUDO_USER:-$(id -un)}}"
APP_DIR="${NOEZEMA_DEV_APP_DIR:-$REPO_ROOT}"
DATA_ROOT="${NOEZEMA_DEV_DATA_ROOT:-/var/lib/noezema-dev}"
HOST_LIB="$DATA_ROOT/host"
UNIT_STATE="$HOST_LIB/unit-state.json"
ENV_FILE="${NOEZEMA_DEV_ENV_FILE:-/etc/noezema/dev.env}"

DB_CONTAINER="${NOEZEMA_DEV_DB_CONTAINER:-noezema-dev-db}"
DB_VOLUME="${NOEZEMA_DEV_DB_VOLUME:-noezema-dev-pgdata}"
DB_IMAGE="postgres:15"
DB_NAME="${NOEZEMA_DEV_DB_NAME:-noezema-dev}"
DB_USER=noezema
DB_PORT="${NOEZEMA_DEV_DB_PORT:-5432}"            # published on 127.0.0.1 ONLY

SANDBOX_IMAGE="${NOEZEMA_DEV_SANDBOX_IMAGE:-noezema-sandbox:dev-stand}"
TOOL_EXECUTOR="${NOEZEMA_DEV_TOOL_EXECUTOR:-sandbox}"

WEB_HOST="${NOEZEMA_DEV_WEB_HOST:-127.0.0.1}"
WEB_PORT="${NOEZEMA_DEV_WEB_PORT:-8321}"

LLM_BASE_URL="${NOEZEMA_DEV_LLM_BASE_URL:-http://192.168.1.42:8080/v1}"
LLM_MODEL="${NOEZEMA_DEV_LLM_MODEL:-qwen38-exl3-3bpw-128k}"
LLM_SCHEMA_PROFILE="${NOEZEMA_DEV_LLM_SCHEMA_PROFILE:-none}"
LLM_MAX_OUTPUT_TOKENS="${NOEZEMA_DEV_LLM_MAX_OUTPUT_TOKENS:-8192}"
LLM_TIMEOUT_SECONDS="${NOEZEMA_DEV_LLM_TIMEOUT_SECONDS:-600}"

CONFIG_PAYLOAD="${NOEZEMA_DEV_CONFIG_PAYLOAD:-$REPO_ROOT/docs/eval/config-v12-payload.json}"
CONFIG_REASON="${NOEZEMA_DEV_CONFIG_REASON:-T7.59 dev-stand: activate config-v12}"
NODE_OWNER="${NOEZEMA_DEV_NODE_OWNER:-dev-stand}"

DRY_RUN=false
DOCKER_INSTALL=true
FORCE=false
WITH_UNITS=true

# ── helpers ───────────────────────────────────────────────────────────────────────────
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die()  { printf '\033[31mdev-stand: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

mask_creds() { sed -E -e 's#://[^:/@]*:[^@]*@#://<creds>@#' -e 's#((POSTGRES_)?PASSWORD|ADMIN_TOKEN)=[^ ]*#\2PASSWORD=<masked>#g'; }
run() { if $DRY_RUN; then printf '   [dry-run] %s\n' "$*"; else "$@"; fi }
# for commands whose arguments carry credentials: print the masked form, execute the real one
run_secret() {
  local line
  line="$(printf '%s ' "$@" | mask_creds)"
  if $DRY_RUN; then printf '   [dry-run] %s\n' "$line"; else "$@"; fi
}

SUDO=()
if [[ $EUID -ne 0 ]]; then
  if have sudo; then SUDO=(sudo); else die "not root and sudo unavailable: run as root or install sudo"; fi
fi

assert_dev_db_name() {
  [[ "$DB_NAME" == noezema-dev* ]] || die "refusing to use DB '$DB_NAME': the dev stand only uses noezema-dev*"
  case "$DB_NAME" in
    *eval*|*smoke*) die "refusing to use DB '$DB_NAME': eval/smoke databases are off-limits (SELECT only)" ;;
  esac
}
assert_dev_path() {
  case "$1" in
    /var/lib/noezema|/run/noezema|/var/lib/noezema/*|/run/noezema/*)
      die "refusing to use '$1': production contour path (the stand uses $DATA_ROOT)" ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --no-docker-install) DOCKER_INSTALL=false ;;
    --force) FORCE=true ;;
    --no-units) WITH_UNITS=false ;;
    --stub-executor) TOOL_EXECUTOR=stub ;;
    --web-host) WEB_HOST="${2:?--web-host needs a value}"; shift ;;
    --web-port) WEB_PORT="${2:?--web-port needs a value}"; shift ;;
    --user) STAND_USER="${2:?--user needs a value}"; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

assert_dev_db_name
assert_dev_path "$DATA_ROOT"
assert_dev_path "$HOST_LIB"
[[ -f "$APP_DIR/pyproject.toml" ]] || die "$APP_DIR does not look like the noezema repo"
[[ -f "$CONFIG_PAYLOAD" ]] || die "config payload not found: $CONFIG_PAYLOAD"
STAND_GROUP="$(id -gn "$STAND_USER")"
VENV="$APP_DIR/.venv"

# Secrets live in one place: generated once, reused by every step and every rerun.
DB_PASSWORD=""
ADMIN_TOKEN=""
read_env_value() {
  if [[ -f "$ENV_FILE" ]]; then sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; fi
}

say "NOEZEMA dev stand bootstrap (T7.59(b))"
note "repo=$REPO_ROOT app=$APP_DIR user=$STAND_USER:$STAND_GROUP data_root=$DATA_ROOT"
note "db=$DB_CONTAINER/$DB_NAME on 127.0.0.1:$DB_PORT · sandbox_image=$SANDBOX_IMAGE executor=$TOOL_EXECUTOR"
note "web=$WEB_HOST:$WEB_PORT · llm=$LLM_BASE_URL model=$LLM_MODEL schema_profile=$LLM_SCHEMA_PROFILE max_out=$LLM_MAX_OUTPUT_TOKENS timeout=$LLM_TIMEOUT_SECONDS"
note "env_file=$ENV_FILE (0600, owner $STAND_USER) dry_run=$DRY_RUN force=$FORCE units=$WITH_UNITS"

# ── 1. packages: docker + python >= 3.11 + uv ─────────────────────────────────────────
step_packages() {
  say "1. packages"
  local py="" candidate
  for candidate in python3.12 python3.11 python3; do
    if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
      py="$candidate"; break
    fi
  done
  if [[ -z "$py" ]]; then
    note "python >= 3.11 missing -> apt install"
    run "${SUDO[@]}" apt-get update -y
    run "${SUDO[@]}" apt-get install -y python3 python3-venv
    have python3 || die "python3 still missing after apt install"
    py=python3
  fi
  note "python: $py ($("$py" --version 2>&1 | head -1))"

  if ! have docker; then
    if $DOCKER_INSTALL; then
      note "docker missing -> apt install docker.io + enable"
      run "${SUDO[@]}" apt-get update -y
      run "${SUDO[@]}" apt-get install -y docker.io
      run "${SUDO[@]}" systemctl enable --now docker
    else
      die "docker is missing and --no-docker-install was given"
    fi
  fi

  if ! have uv; then
    note "uv missing -> apt install uv; if the distro has no uv, fall back to the official installer"
    if $DRY_RUN; then
      printf '   [dry-run] %s apt-get install -y uv   (fallback: astral installer -> /usr/local/bin)\n' "${SUDO[*]}"
    elif ! "${SUDO[@]}" apt-get install -y uv; then
      have curl || run "${SUDO[@]}" apt-get install -y curl
      run sh -c 'curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin CARGO_HOME=/tmp/uv-cargo sh'
      have uv || die "uv is still not on PATH after both attempts"
    fi
  fi
  note "uv: $(command -v uv || echo 'not installed')"

  # Units run as $STAND_USER and must reach /var/run/docker.sock (root:docker) in sandbox mode.
  if [[ "$TOOL_EXECUTOR" == "sandbox" && "$STAND_USER" != "root" ]] && getent group docker >/dev/null 2>&1; then
    run "${SUDO[@]}" usermod -aG docker "$STAND_USER"
    note "group docker = доступ к /var/run/docker.sock для пользователя юнитов (интерактивной сессии нужен перелогин)"
  fi
}

# ── 2. venv + dependencies: only via uv, prod deps only (AGENTS §6) ───────────────────
step_venv() {
  say "2. venv + dependencies"
  local pybin
  pybin="$(command -v python3.12 || command -v python3.11 || command -v python3)"
  if [[ -x "$VENV/bin/python" ]] && ! $FORCE; then
    note "venv exists: $VENV (kept; --force recreates it)"
  else
    run uv venv "$VENV" --python "$pybin"
  fi
  run sh -c "cd '$APP_DIR' && env UV_CACHE_DIR='$APP_DIR/.uv-cache' uv pip install --python '$VENV/bin/python' -e ."
  note "prod dependencies only (no [dev] extras): ruff/mypy/pytest belong to the dev environment"
}

# ── 3. secrets + env file, before anything needs them ────────────────────────────────
step_env_file() {
  say "3. $ENV_FILE"
  DB_PASSWORD="$(read_env_value NOEZEMA_DB_PASSWORD)"
  ADMIN_TOKEN="$(read_env_value NOEZEMA_ADMIN_TOKEN)"
  if [[ -z "$DB_PASSWORD" ]]; then
    DB_PASSWORD="$(openssl rand -hex 16)"; note "generated the DB password (never printed)"
  else
    note "NOEZEMA_DB_PASSWORD already present: kept"
  fi
  if [[ -n "$ADMIN_TOKEN" ]] && ! $FORCE; then
    note "NOEZEMA_ADMIN_TOKEN already present: kept (--force regenerates it)"
  else
    ADMIN_TOKEN="$(openssl rand -hex 32)"
    note "generated an admin token (never printed): it is the X-Admin-Token of the UI and of commands"
  fi
  if [[ -f "$ENV_FILE" ]] && ! $FORCE; then
    note "env file exists: rewritten with the SAME secrets (--force would rotate the token)"
  fi

  local url="postgresql+asyncpg://$DB_USER:$DB_PASSWORD@127.0.0.1:$DB_PORT/$DB_NAME"
  if $DRY_RUN; then
    printf '   [dry-run] write %s (mode 0600, owner %s:%s) with NOEZEMA_DATABASE_URL=%s\n' \
      "$ENV_FILE" "$STAND_USER" "$STAND_GROUP" "$(printf '%s' "$url" | mask_creds)"
    note "            and NODE_OWNER, ADMIN_TOKEN, LLM_BASE_URL/MODEL/SCHEMA_PROFILE/MAX_OUTPUT_TOKENS/TIMEOUT,"
    note "            TOOL_EXECUTOR, SANDBOX_IMAGE/ENGINE/WORK_ROOT, HOST_LIB, UNIT_STATE, DATA_ROOT, WEB_HOST/PORT"
    return
  fi

  local tmp
  tmp="$(mktemp)"
  {
    echo "# NOEZEMA dev stand (T7.59) — generated by deploy/dev-stand/bootstrap.sh"
    echo "# mode 0600 owner $STAND_USER:$STAND_GROUP; contains secrets (AGENTS §5): never paste into chat or commits."
    echo "NOEZEMA_DATABASE_URL=$url"
    echo "NOEZEMA_DB_PASSWORD=$DB_PASSWORD"
    echo "NOEZEMA_DATA_ROOT=$DATA_ROOT"
    echo "NOEZEMA_HOST_LIB=$HOST_LIB"
    echo "NOEZEMA_UNIT_STATE=$UNIT_STATE"
    echo "NOEZEMA_NODE_OWNER=$NODE_OWNER"
    echo "NOEZEMA_ADMIN_TOKEN=$ADMIN_TOKEN"
    echo "NOEZEMA_LLM_BASE_URL=$LLM_BASE_URL"
    echo "NOEZEMA_LLM_MODEL=$LLM_MODEL"
    echo "NOEZEMA_LLM_SCHEMA_PROFILE=$LLM_SCHEMA_PROFILE"
    echo "NOEZEMA_LLM_MAX_OUTPUT_TOKENS=$LLM_MAX_OUTPUT_TOKENS"
    echo "NOEZEMA_LLM_TIMEOUT_SECONDS=$LLM_TIMEOUT_SECONDS"
    echo "NOEZEMA_TOOL_EXECUTOR=$TOOL_EXECUTOR"
    echo "NOEZEMA_SANDBOX_IMAGE=$SANDBOX_IMAGE"
    echo "NOEZEMA_SANDBOX_ENGINE=docker"
    echo "NOEZEMA_SANDBOX_WORK_ROOT=$DATA_ROOT/sandbox"
    echo "NOEZEMA_WEB_HOST=$WEB_HOST"
    echo "NOEZEMA_WEB_PORT=$WEB_PORT"
  } > "$tmp"
  run "${SUDO[@]}" install -d -m 0750 -o "$STAND_USER" -g "$STAND_GROUP" "$(dirname "$ENV_FILE")"
  run "${SUDO[@]}" install -m 0600 -o "$STAND_USER" -g "$STAND_GROUP" "$tmp" "$ENV_FILE"
  rm -f "$tmp"
  note "written: $(grep -c '^[A-Z]' "$ENV_FILE") variables, mode $(stat -c %a "$ENV_FILE") owner $(stat -c %U "$ENV_FILE")"
}

database_url() { printf 'postgresql+asyncpg://%s:%s@127.0.0.1:%s/%s' "$DB_USER" "$DB_PASSWORD" "$DB_PORT" "$DB_NAME"; }

# ── 4. data directories + pinned sandbox image ───────────────────────────────────────
step_dirs_and_image() {
  say "4. data directories + sandbox image"
  run "${SUDO[@]}" install -d -m 0750 -o "$STAND_USER" -g "$STAND_GROUP" \
    "$DATA_ROOT" "$DATA_ROOT/sandbox" "$HOST_LIB"
  note "$DATA_ROOT/sandbox = NOEZEMA_SANDBOX_WORK_ROOT (owner $STAND_USER: sessions mkdir one overlay each)"
  if docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
    note "image $SANDBOX_IMAGE already present: kept"
  else
    run "${SUDO[@]}" docker build -f "$REPO_ROOT/sandbox/Containerfile" -t "$SANDBOX_IMAGE" "$REPO_ROOT/sandbox/"
  fi
  note "pinned for the stand as NOEZEMA_SANDBOX_IMAGE=$SANDBOX_IMAGE (dev/test tags stay untouched)"
}

# ── 5. Postgres 15 in docker, published on 127.0.0.1 only ────────────────────────────
step_postgres() {
  say "5. Postgres 15 (docker, 127.0.0.1 only)"
  local exists running
  exists="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -c "^${DB_CONTAINER}$" || true)"
  if [[ "${exists:-0}" -gt 0 ]]; then
    note "container $DB_CONTAINER exists: kept (data volume $DB_VOLUME is reused)"
    running="$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null || echo false)"
    [[ "$running" == "true" ]] || run "${SUDO[@]}" docker start "$DB_CONTAINER"
  else
    run_secret "${SUDO[@]}" docker run -d --name "$DB_CONTAINER" --restart unless-stopped \
      -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB=postgres \
      -v "$DB_VOLUME:/var/lib/postgresql/data" \
      -p "127.0.0.1:$DB_PORT:5432" \
      --health-cmd "pg_isready -U $DB_USER" --health-interval 5s --health-timeout 5s --health-retries 12 \
      "$DB_IMAGE"
  fi
  if $DRY_RUN; then note "(dry-run: healthcheck wait skipped)"; return; fi
  local status="unknown"
  for _ in $(seq 1 90); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$DB_CONTAINER" 2>/dev/null || echo missing)"
    [[ "$status" == "healthy" ]] && break
    sleep 1
  done
  [[ "$status" == "healthy" ]] || die "container $DB_CONTAINER is '$status', not healthy: journalctl/docker logs explain why"
  note "postgres healthy on 127.0.0.1:$DB_PORT (never published outside the VM)"
}

# ── 6. database, migrations, effective config ────────────────────────────────────────
active_config_mode() {
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "SELECT s.activation_mode FROM runtime_config_heads h JOIN config_snapshots s ON s.id = h.active_config_snapshot_id WHERE h.scope = 'global'" 2>/dev/null || true
}

step_database() {
  say "6. database, migrations, effective config"
  local exists url mode
  exists="$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -Atc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" 2>/dev/null || true)"
  if [[ "$exists" == "1" ]]; then
    note "database $DB_NAME exists: kept (reset-db.sh recreates it deliberately)"
  else
    run docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$DB_NAME"
  fi

  url="$(database_url)"
  if $DRY_RUN; then
    printf '   [dry-run] cd %s && NOEZEMA_DATABASE_URL=%s %s -m alembic upgrade head\n' \
      "$APP_DIR" "$(printf '%s' "$url" | mask_creds)" "$VENV/bin/python"
  else
    (cd "$APP_DIR" && env NOEZEMA_DATABASE_URL="$url" "$VENV/bin/python" -m alembic upgrade head)
  fi

  mode="unknown"
  if ! $DRY_RUN; then mode="$(active_config_mode)"; mode="${mode:-none}"; fi
  if [[ "$mode" == "bootstrap" || "$mode" == "none" || "$mode" == "unknown" ]]; then
    note "effective config is still the bootstrap snapshot -> activating $CONFIG_PAYLOAD (config-v12)"
    run_secret env NOEZEMA_DATABASE_URL="$url" "$VENV/bin/python" -m hostctl.cli activate-online \
      --payload "$CONFIG_PAYLOAD" --reason "$CONFIG_REASON" --drain-wait-seconds 120
  else
    note "a config snapshot (mode=$mode) is already active: activation skipped (use activate-online to change it)"
  fi
}

# ── 7. dev units: rendered templates, deliberately NOT copies of infra/systemd ────────
step_units() {
  say "7. systemd units"
  if ! $WITH_UNITS; then note "skipped (--no-units)"; return; fi
  local src out tmp
  for src in "$SCRIPT_DIR"/systemd/*; do
    out="/etc/systemd/system/$(basename "$src")"
    if $DRY_RUN; then
      printf '   [dry-run] render %s -> %s (REPO=%s USER=%s ENV_FILE=%s UNIT_STATE=%s)\n' \
        "${src##*/}" "$out" "$APP_DIR" "$STAND_USER" "$ENV_FILE" "$UNIT_STATE"
      continue
    fi
    tmp="$(mktemp)"
    sed -e "s#@REPO@#$APP_DIR#g" -e "s#@USER@#$STAND_USER#g" -e "s#@GROUP@#$STAND_GROUP#g" \
        -e "s#@ENVFILE@#$ENV_FILE#g" -e "s#@DATA@#$DATA_ROOT#g" -e "s#@UNITSTATE@#$UNIT_STATE#g" \
        "$src" > "$tmp"
    run "${SUDO[@]}" install -m 0644 "$tmp" "$out"
    rm -f "$tmp"
  done
  note "unit files: $(find "$SCRIPT_DIR/systemd" -maxdepth 1 -type f | wc -l), rendered into /etc/systemd/system"
  run "${SUDO[@]}" systemctl daemon-reload
  run "${SUDO[@]}" systemctl enable noezema-dev-unit-state.timer noezema-dev-tick.timer \
    noezema-dev-maint.timer noezema-dev-web.service
  note "started by you: systemctl start noezema-dev.target (the stand is not pulled in at boot)"
}

step_summary() {
  say "готово"
  note "старт/стоп:   systemctl start|stop noezema-dev.target"
  note "UI:           http://$WEB_HOST:$WEB_PORT (token = NOEZEMA_ADMIN_TOKEN из $ENV_FILE)"
  note "вопрос:       форма «Задать вопрос», или $VENV/bin/python -m hostctl.cli ask \"...\" --priority 9"
  note "wake now:      кнопка «wake now» на странице, или systemctl start noezema-dev-tick.service"
  note "лента событий: ссылка из таблицы очереди (/session/<id>) или curl http://127.0.0.1:$WEB_PORT/api/v1/timeline"
  note "состояние:    ./status.sh · сброс: ./reset-db.sh"
  if $DRY_RUN; then note "(dry-run: ничего не изменено)"; fi
}

step_packages
step_venv
step_env_file
step_dirs_and_image
step_postgres
step_database
step_units
step_summary
