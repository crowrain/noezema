#!/usr/bin/env bash
# NOEZEMA dev stand bootstrap (T7.59(b)) — target: single-user Ubuntu 24.04 VM, no GPU.
#
# Idempotent by construction: every step first looks at the current state and skips what is already
# in place; secrets are never rotated implicitly (see --rotate-secrets below).
#   ./bootstrap.sh --dry-run             print the plan, change nothing
#   ./bootstrap.sh                       do it (sudo is used where the OS requires it)
#   ./bootstrap.sh --no-docker-install   skip apt installs (docker already present)
#   ./bootstrap.sh --no-units            skip installing/activating systemd units
#   ./bootstrap.sh --stub-executor       sessions use the dev stub instead of containers
#   ./bootstrap.sh --force               rebuild venv (--clear) / units / env file; secrets are KEPT
#                                        as long as the Postgres container or its volume exists
#   ./bootstrap.sh --rotate-secrets      deliberately rotate the admin token (and the DB password,
#                                        only when no cluster exists — see step 3)
#   ./bootstrap.sh --with-tick-timer     enable noezema-dev-tick.timer: sessions then start BY THEMSELVES
#                                        every wake_schedule.interval from the snapshot. Without the flag the
#                                        tick timer is NOT enabled (T7.61(б)) — a stand starts a session only
#                                        when the operator presses «wake now». An already-enabled timer is
#                                        never silently disabled: the script reports its state instead.
#   ./bootstrap.sh --with-searxng        install the search package (T7.70, §5.12): settings are rendered
#                                        into /etc/noezema/searxng/settings.yml (secret generated there,
#                                        never printed and never committed) and the container is published on
#                                        127.0.0.1:8888 only — that is the address research_proxy uses as
#                                        searxng_url in the config snapshot. Without the flag NOTHING search
#                                        related is created, changed or removed: an existing container is
#                                        only reported. Upstream engines are reachable only if the operator
#                                        opened egress 80/443 + DNS (printed as guidance; the script never
#                                        changes UFW or iptables — AGENTS §5 host contour).
#   ./bootstrap.sh --recreate-searxng    explicit rebuild of the search container (docker rm -f + run).
#                                        Without this flag a running OR existing container is never recreated:
#                                        settings are rendered, a stopped container is started, done.
#
#
# Env knobs (all optional): NOEZEMA_DEV_USER / _APP_DIR / _DATA_ROOT / _ENV_FILE,
#   NOEZEMA_DEV_DB_CONTAINER / _DB_VOLUME / _DB_NAME / _DB_PORT (порт публикации на 127.0.0.1; если не
#   задан — выбирается сам: 5432, а занят → первый свободный из 5433..5440, и он записывается в env-файл),
#   NOEZEMA_DEV_DB_READY_TIMEOUT (секунд ожидания SELECT 1 на этом порту, по умолчанию 120),
#   NOEZEMA_DEV_SANDBOX_IMAGE / _TOOL_EXECUTOR / _WEB_HOST / _WEB_PORT / _LLM_* / _CONFIG_PAYLOAD /
#   _CONFIG_REASON / _NODE_OWNER.
# The firewall is never touched: if ufw is active the script only prints the rules you need (README).
#
# The stand NEVER touches the production contour paths (/var/lib/noezema, /run/noezema) and
# never works on a database that is not noezema-dev* — enforced below, not by convention.
# Secrets (DB password, admin token) are generated into $ENV_FILE with mode 0600, are never
# printed, and are masked in every --dry-run line (AGENTS §5). Decision recorded in T7.59(в): a
# rerun with --force does NOT rotate them while the Postgres container/volume exist — the password
# already lives in that volume, and rotating it would desync the cluster from $ENV_FILE. Rotation
# is an explicit act (--rotate-secrets) and only for a stand without an existing cluster.

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
# Published on 127.0.0.1 ONLY. Empty = resolve it below (T7.59(в): a VM may already hold 5432 —
# e.g. a native postgresql.service — and `docker run -p 127.0.0.1:5432:5432` then dies with
# "port is already allocated", which looks like a broken stand rather than a busy port).
DB_PORT="${NOEZEMA_DEV_DB_PORT:-}"
DB_PORT_EXPLICIT=false
[[ -n "$DB_PORT" ]] && DB_PORT_EXPLICIT=true
DB_PORT_DEFAULT=5432
DB_PORT_ALTERNATIVES="5433 5434 5435 5436 5437 5438 5439 5440"
DB_READY_TIMEOUT="${NOEZEMA_DEV_DB_READY_TIMEOUT:-120}"   # endpoint wait, seconds (never > 120)

SANDBOX_IMAGE="${NOEZEMA_DEV_SANDBOX_IMAGE:-noezema-sandbox:dev-stand}"
TOOL_EXECUTOR="${NOEZEMA_DEV_TOOL_EXECUTOR:-sandbox}"

WEB_HOST="${NOEZEMA_DEV_WEB_HOST:-127.0.0.1}"
WEB_PORT="${NOEZEMA_DEV_WEB_PORT:-8321}"

# Поиск (T7.70, §5.12): SearXNG ставится ТОЛЬКО по --with-searxng. Контейнер публикуется на 127.0.0.1:8888
# (внутри он слушает 8080 как обычно) — это ровно тот адрес, который снапшот конфигурации записывает как
# research_proxy.searxng_url (config-v16 унаследовал http://127.0.0.1:8888 в private_allowlist от
# config-v15, а тот — от config-v14).
SEARXNG_CONTAINER="${NOEZEMA_DEV_SEARXNG_CONTAINER:-noezema-searxng}"
SEARXNG_IMAGE="${NOEZEMA_DEV_SEARXNG_IMAGE:-searxng/searxng:latest}"
SEARXNG_PORT="${NOEZEMA_DEV_SEARXNG_PORT:-8888}"
SEARXNG_INTERNAL_PORT=8080
SEARXNG_SETTINGS_DIR="${NOEZEMA_DEV_SEARXNG_SETTINGS_DIR:-/etc/noezema/searxng}"
SEARXNG_READY_TIMEOUT="${NOEZEMA_DEV_SEARXNG_READY_TIMEOUT:-60}"   # ожидание JSON /search, секунд

LLM_BASE_URL="${NOEZEMA_DEV_LLM_BASE_URL:-http://192.168.1.42:8080/v1}"
LLM_MODEL="${NOEZEMA_DEV_LLM_MODEL:-qwen38-exl3-3bpw-128k}"
LLM_SCHEMA_PROFILE="${NOEZEMA_DEV_LLM_SCHEMA_PROFILE:-none}"
# T7.80 (ADR-0030): how THIS engine can be asked to stop reasoning. "none" (the default) means
# the deployment has not been probed: no reasoning parameter is added to any request, so every
# stand run stays byte-for-byte comparable with earlier config versions. "halogen" adds
# reasoning_effort=none, "chat-template" (llama.cpp Qwen3.x) adds chat_template_kwargs
# {"enable_thinking": false} — both verified on 192.168.1.141 (reasoning_tokens=0). An unknown
# name is a startup failure, not a silent default: the stand would otherwise send a parameter the
# engine may reject.
LLM_REASONING_PROFILE="${NOEZEMA_DEV_LLM_REASONING_PROFILE:-none}"
LLM_MAX_OUTPUT_TOKENS="${NOEZEMA_DEV_LLM_MAX_OUTPUT_TOKENS:-8192}"
LLM_TIMEOUT_SECONDS="${NOEZEMA_DEV_LLM_TIMEOUT_SECONDS:-600}"

# Activated config (T7.59(в), T7.71, T7.73, T7.76; дефолт с T7.77 — config-v17, см. ниже):
# config-v16 = config-v15 with exactly two changes —
# the explorer prompt pin (explorer-v8: правило 10 — спорное число проверяется двумя сторонами поиска,
# официальным первоисточником И независимым исследованием, расхождение называется открыто либо честно
# говорится «независимых оценок не найдено»; правило 11 — беречь шаги) and one session limit
# (session_limits.max_explorer_steps 10 → 16: на подставке перепроверка израсходовала 8 из 10 шагов на
# официальную сторону, включая повторное чтение уже прочитанного адреса и два захода на недоступный URL,
# и независимая сторона не состоялась; при ~57 с на шаг это 16×57 ≈ 912 с + куратор ≤180 с — ниже
# session_timeout/phase_deadline 1800 с, а ≤15 upstream-запросов поиска укладываются в rate_limit_max 20).
# Thresholds, claim_type_rules, token budgets (Σ=26624), curator-v8 and explorer-v7 pins, model windows
# and the tool grant are byte-identical to config-v15, which was config-v14 with two prompt pins
# (curator-v8: reverify keeps the anchor's as_of/date_anchor/scope and must link every source it actually
# used; explorer-v7: primary source vs derivative publication, divergence shown instead of silently
# picked) — and config-v14 was config-v13 with policy.capabilities.tools (search granted, `artifact.create`
# removed: it is not in the tool registry and every step that tried it failed with "unknown tool:
# artifact.create") and explorer-v6 (v5 told the model "Sealed: сети нет" while the stand runs curated);
# v13 itself was config-v12 with model.context_window / model.backend_context_limit lowered to 131072 —
# the physical window of the EXL3 engine the stand talks to (qwen38-exl3-3bpw-128k). v12 advertised 262144
# (input_budget 251904 > the engine window, observed in SMOKE-V14B); schedule and thresholds are untouched,
# so stand sessions stay comparable with the smoke series. config-v16 (T7.76) is config-v15 with the
# explorer pin replaced by explorer-v8 (двусторонний поиск: первоисточник + отдельный запрос про
# независимую оценку, расхождение называется открыто) and session_limits.max_explorer_steps 10 → 16
# (16 шагов ≈ 912 с исследования при замеренных ~57 с на шаг, укладывается в phase_deadline 1800 с;
# ≤15 upstream-поисков < rate_limit_max 20). Override with NOEZEMA_DEV_CONFIG_PAYLOAD — rollback is
# `NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v15-payload.json` (canonical hash
# b3801812…, pinned in tests; config-v16 itself is 740ae9a1…). v15, v14, v13 and v12 stay in the repo
# and are never rewritten.
# config-v17 (T7.77) = config-v16 with exactly ONE change: the explorer prompt pin — explorer-v9
# (правило 12: прогноз до события — не независимая оценка реализованного значения; сопоставим только с
# ожиданиями, а измерением состоявшегося называются опросы домохозяйств, альтернативные индексы цен и
# академические исследования; пересказ официальной цифры со ссылкой на первоисточник вторым наблюдением
# не считается). Лимит шагов (16), пороги, claim_type_rules, token-бюджеты, окна модели, research_proxy
# и список инструментов — байт в байт v16: правка живёт в инструкциях модели, движок независимости не
# тронут. Откатом v17 был config-v16 (canonical 740ae9a1…, сам v17 — 5c402f4d…).
# config-v18 (T7.80, ADR-0030) = config-v17 ровно с одной правкой: в раздел `model` добавлен
# `reasoning_by_phase` — политика «рассуждать/не рассуждать» для каждой фазы ВЫЗОВА
# (consolidation/extraction/verification off, exploration/planning on). Пороги, claim_type_rules,
# token-бюджеты, лимит шагов (16), окна модели, research_proxy, список инструментов и все пины
# промптов — байт в байт v17. В том числе model.max_output_tokens остаётся 8192: halogen держит
# фиксированную комнату ответа (~1000 токенов), поэтому raising потолок не убирает обрезку длинного
# JSON — он переносит место среза (замер T7.80). Сама возможность выключить рассуждение задаётся НЕ
# payload'ом, а окружением стенда (`NOEZEMA_DEV_LLM_REASONING_PROFILE` выше): payload описывает
# намерение, движок — способность. Откат теперь config-v17 (canonical 5c402f4d…, сам v18 —
# b5605e4e…) и задаётся строкой `NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v17-payload.json`;
# v17, v16 и v15 остаются в репо и не переписываются.
CONFIG_PAYLOAD="${NOEZEMA_DEV_CONFIG_PAYLOAD:-$REPO_ROOT/docs/eval/config-v18-payload.json}"
CONFIG_REASON="${NOEZEMA_DEV_CONFIG_REASON:-T7.80 dev-stand: activate config-v18 (model.reasoning_by_phase: the curator, extractor and verifier answers are asked without engine reasoning because those answers are long structured JSON under a fixed output limit; explorer and planner keep it; step limit, curator-v8, explorer-v9, thresholds and token budgets unchanged from config-v17)}"
NODE_OWNER="${NOEZEMA_DEV_NODE_OWNER:-dev-stand}"

DRY_RUN=false
DOCKER_INSTALL=true
FORCE=false
ROTATE_SECRETS=false
WITH_UNITS=true
# T7.61(б): плановые сессии — НЕ по умолчанию. Без флага тик-таймер не включается ни при первом,
# ни при повторном запуске; уже включённый таймер скрипт сам не отключает (см. step_units).
WITH_TICK_TIMER=false
# T7.70: пакет поиска — тоже НЕ по умолчанию (он означает исходящий трафик к внешним движкам).
# Существующий контейнер поиска без --recreate-searxng не пересоздаётся никогда.
WITH_SEARXNG=false
RECREATE_SEARXNG=false

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

# Is anything already listening on this TCP port (host side)? Checked with ss BEFORE we ask docker
# to publish it: a VM may run a native postgresql.service on 127.0.0.1:5432, and the old behaviour
# was a `docker run` that died with "port is already allocated" after secrets had been written.
listening_ports() {
  have ss || return 1
  ss -ltn 2>/dev/null | awk 'NR > 1 {print $4}' | sed -E 's/.*:([0-9]+)$/\1/' | sort -u
}

port_is_taken() {
  local port="$1"
  have ss || return 1            # cannot check -> treat as free, the docker error will be explicit
  listening_ports | grep -qx "$port"
}

container_published_port() {
  docker inspect -f '{{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{(index $conf 0).HostPort}} {{end}}{{end}}' \
    "$DB_CONTAINER" 2>/dev/null | awk '{print $1}' | head -1
}

resolve_db_port() {
  local existing taken candidate
  if have docker && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$DB_CONTAINER"; then
    existing="$(container_published_port)"
    if [[ -n "$existing" ]]; then
      DB_PORT="$existing"
      note "порт БД: переиспользуем опубликованный порт существующего контейнера $DB_CONTAINER = 127.0.0.1:$DB_PORT"
      [[ "$DB_PORT_EXPLICIT" != true ]] || note "  (NOEZEMA_DEV_DB_PORT игнорирован: контейнер уже занят другим портом — так повторный запуск не разъезжается с реальностью)"
      return 0
    fi
  fi
  taken="$(read_env_value NOEZEMA_DEV_DB_PORT)"
  if $DB_PORT_EXPLICIT; then
    port_is_taken "$DB_PORT" || { note "порт БД: $DB_PORT (задан явно, свободен)"; return 0; }
    die "NOEZEMA_DEV_DB_PORT=$DB_PORT уже занят на этом хосте (ss -ltn). Освободите его или укажите другой: NOEZEMA_DEV_DB_PORT=5433 ./bootstrap.sh"
  fi
  if [[ -n "$taken" ]] && ! port_is_taken "$taken"; then
    DB_PORT="$taken"
    note "порт БД: $DB_PORT (как записано в $ENV_FILE при прошлом запуске)"
    return 0
  fi
  if ! port_is_taken "$DB_PORT_DEFAULT"; then
    DB_PORT="$DB_PORT_DEFAULT"
    note "порт БД: $DB_PORT_DEFAULT (свободен)"
    return 0
  fi
  for candidate in $DB_PORT_ALTERNATIVES; do
    port_is_taken "$candidate" || {
      DB_PORT="$candidate"
      note "порт БД: $DB_PORT_DEFAULT занят (нативный postgresql.service?), выбрали свободный $DB_PORT — он записан в $ENV_FILE как NOEZEMA_DEV_DB_PORT"
      return 0
    }
  done
  die "127.0.0.1:$DB_PORT_DEFAULT и $DB_PORT_ALTERNATIVES заняты: освободите порт или задайте явно NOEZEMA_DEV_DB_PORT=<порт>"
}

# ufw diagnostics ONLY — the script never changes the firewall (AGENTS §5 discipline about the host
# contour; a deploy script must not silently open the VM). Printed before any docker action.
ufw_required_rules() {
  note "  sudo ufw allow out on docker0 to 172.17.0.0/16                # пулл образа и трафик контейнера"
  note "  sudo ufw allow out to any port $(printf '%s' "$LLM_BASE_URL" | sed -E 's#.*:##; s#/.*##') proto tcp   # $LLM_BASE_URL"
  note "  sudo ufw allow in from 192.168.1.0/24 to any port $WEB_PORT proto tcp   # если UI открывают из LAN"
  note "  обычно нужны и исходящие: DNS (53), NTP (123), 80/443 (apt и пулл образов)"
  note "полные команды для 192.168.1.92 — README, раздел «ВМ с deny-by-default UFW»"
}

firewall_diagnostics() {
  if ! have ufw; then note "ufw не установлен: проверку deny-by-default сделать нельзя"; return 0; fi
  if $DRY_RUN; then
    say "диагностика фаервола (dry-run: состояние ufw не читаем, меня его менять никто не будет)"
    note "если на ВМ включён deny-by-default ufw, без этих правил стенд не поднимется:"
    ufw_required_rules
    return 0
  fi
  # -n (non-interactive): diagnostics must never hang on a password prompt in the middle of bootstrap.
  local reader=(ufw) status=""
  [[ $EUID -eq 0 ]] || reader=(sudo -n ufw)
  status="$("${reader[@]}" status 2>/dev/null | head -1 || true)"
  if [[ "$status" == *"active"* ]]; then
    say "диагностика фаервола: ufw активен — скрипт НИЧЕГО не меняет, правила применяете вы сами"
    note "без этих правил стенд не поднимется (пулл образа, доступ контейнера к LLM, доступ к вебу из LAN):"
    ufw_required_rules
  else
    note "ufw: ${status:-состояние недоступно без пароля} — если он активен, правила см. в README («ВМ с deny-by-default UFW»)"
  fi
}

assert_dev_path() {
  case "$1" in
    /var/lib/noezema|/run/noezema|/var/lib/noezema/*|/run/noezema/*)
      die "refusing to use '$1': production contour path (the stand uses $DATA_ROOT)" ;;
  esac
}

# Read-only: как сейчас стоит тик-таймер (T7.61(б)). Ничего не меняет и никогда не спросят пароль:
# systemctl is-enabled — обычная операция чтения, root для неё не нужен.
tick_timer_state() {
  have systemctl || { printf 'systemctl недоступен'; return 0; }
  local state
  state="$(systemctl is-enabled noezema-dev-tick.timer 2>/dev/null || true)"
  printf '%s' "${state:-не установлен (unit не найден)}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --no-docker-install) DOCKER_INSTALL=false ;;
    --force) FORCE=true ;;
    --rotate-secrets) ROTATE_SECRETS=true ;;
    --no-units) WITH_UNITS=false ;;
    --stub-executor) TOOL_EXECUTOR=stub ;;
    --with-tick-timer) WITH_TICK_TIMER=true ;;
    --with-searxng) WITH_SEARXNG=true ;;
    --recreate-searxng) RECREATE_SEARXNG=true ;;
    --web-host) WEB_HOST="${2:?--web-host needs a value}"; shift ;;
    --web-port) WEB_PORT="${2:?--web-port needs a value}"; shift ;;
    --user) STAND_USER="${2:?--user needs a value}"; shift ;;
    -h|--help) sed -n '2,/^[^#]/p' "$0" | sed '$d'; exit 0 ;;
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

# The port is decided BEFORE the env file is written: $NOEZEMA_DATABASE_URL carries it, and a rerun
# must land on the same port the existing container publishes instead of failing on 5432.
resolve_db_port

say "NOEZEMA dev stand bootstrap (T7.59(b))"
note "repo=$REPO_ROOT app=$APP_DIR user=$STAND_USER:$STAND_GROUP data_root=$DATA_ROOT"
note "db=$DB_CONTAINER/$DB_NAME on 127.0.0.1:$DB_PORT · sandbox_image=$SANDBOX_IMAGE executor=$TOOL_EXECUTOR"
note "web=$WEB_HOST:$WEB_PORT · llm=$LLM_BASE_URL model=$LLM_MODEL schema_profile=$LLM_SCHEMA_PROFILE reasoning_profile=$LLM_REASONING_PROFILE max_out=$LLM_MAX_OUTPUT_TOKENS timeout=$LLM_TIMEOUT_SECONDS"
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
    note "uv missing"
    if $DRY_RUN; then
      printf '   [dry-run] %s apt-get install -y uv   (Ubuntu 24.04 has no uv package -> expect failure)\n' "${SUDO[*]}"
      printf '   [dry-run] download https://astral.sh/uv/install.sh, then: sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh <installer>\n'
      printf '   [dry-run] verify uv --version\n'
    else
      # Ubuntu 24.04 ships NO apt package named uv (on .92: "Unable to locate package uv"). The
      # official installer writes into /usr/local/bin, which the stand user cannot write to, so the
      # fallback runs it as root with the install dir pinned — then the binary is VERIFIED, not
      # assumed (a half-installed uv on PATH used to fail later, in step 2, with a cryptic error).
      if ! "${SUDO[@]}" apt-get install -y uv >/dev/null 2>&1; then
        note "apt has no uv package (Ubuntu 24.04) -> official installer as root into /usr/local/bin"
        have curl || run "${SUDO[@]}" apt-get install -y curl
        local installer="/tmp/noezema-uv-installer.sh"
        run curl -fsSL https://astral.sh/uv/install.sh -o "$installer"
        if ! "${SUDO[@]}" env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh "$installer"; then
          note "root install failed -> installing into ~/.local/bin and adding it to PATH for this run"
          run env HOME="$HOME" sh "$installer"
          export PATH="$HOME/.local/bin:$PATH"
        fi
        rm -f "$installer"
      fi
      hash -r 2>/dev/null || true
      have uv || die "uv is still not on PATH after both attempts: install it manually and re-run"
    fi
  fi
  if $DRY_RUN; then
    note "uv: $(command -v uv || echo 'not installed yet')"
  else
    note "uv: $(uv --version 2>&1)"
  fi

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
  elif [[ -d "$VENV" ]]; then
    # uv refuses to overwrite an existing venv without --clear: `--force` used to die right here.
    run uv venv "$VENV" --python "$pybin" --clear
    note "venv recreated (--clear): uv otherwise refuses an existing directory"
  else
    run uv venv "$VENV" --python "$pybin"
  fi
  run sh -c "cd '$APP_DIR' && env UV_CACHE_DIR='$APP_DIR/.uv-cache' uv pip install --python '$VENV/bin/python' -e ."
  note "prod dependencies only (no [dev] extras): ruff/mypy/pytest belong to the dev environment"
}

# ── 3. secrets + env file, before anything needs them ────────────────────────────────
# Does the Postgres this stand would connect to already exist (container or its data volume)?
cluster_exists() {
  have docker || return 1
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$DB_CONTAINER" && return 0
  docker volume inspect "$DB_VOLUME" >/dev/null 2>&1
}

step_env_file() {
  say "3. $ENV_FILE"
  DB_PASSWORD="$(read_env_value NOEZEMA_DB_PASSWORD)"
  ADMIN_TOKEN="$(read_env_value NOEZEMA_ADMIN_TOKEN)"
  local cluster=false
  cluster_exists && cluster=true

  if [[ -z "$DB_PASSWORD" ]]; then
    if $cluster; then
      # The password already lives inside the existing volume; inventing a new one here would write an
      # env file that cannot connect. That recovery is a human decision, not a side effect of a rerun.
      die "Postgres-кластер ($DB_CONTAINER/$DB_VOLUME) существует, а NOEZEMA_DB_PASSWORD в $ENV_FILE не найден: восстановите env-файл либо удалите том осознанно (docker volume rm $DB_VOLUME)"
    fi
    DB_PASSWORD="$(openssl rand -hex 16)"; note "generated the DB password (never printed)"
  else
    note "NOEZEMA_DB_PASSWORD already present: kept"
  fi

  # Decision recorded in T7.59(в): --force rebuilds venv/units/env file but does NOT rotate secrets
  # while a cluster exists — rotating the DB password would desync it from the volume, and rotating the
  # admin token would silently invalidate every already-open UI session. Rotation is an explicit act:
  # --rotate-secrets (for the DB password only when there is no cluster at all).
  if [[ -n "$ADMIN_TOKEN" ]] && ! $ROTATE_SECRETS; then
    note "NOEZEMA_ADMIN_TOKEN already present: kept (--force его НЕ ротирует; для ротации — --rotate-secrets)"
  elif [[ -n "$ADMIN_TOKEN" ]]; then
    ADMIN_TOKEN="$(openssl rand -hex 32)"
    note "rotated the admin token (--rotate-secrets, never printed): сессии UI придётся открыть заново"
  else
    ADMIN_TOKEN="$(openssl rand -hex 32)"
    note "generated an admin token (never printed): it is the X-Admin-Token of the UI and of commands"
  fi
  if $ROTATE_SECRETS && $cluster; then
    note "пароль БД НЕ ротирован: он закреплён в томе $DB_VOLUME — ротация возможна только после его удаления"
  fi
  if [[ -f "$ENV_FILE" ]] && ! $ROTATE_SECRETS; then
    note "env file exists: rewritten with the SAME secrets"
  fi

  local url="postgresql+asyncpg://$DB_USER:$DB_PASSWORD@127.0.0.1:$DB_PORT/$DB_NAME"
  if $DRY_RUN; then
    printf '   [dry-run] write %s (mode 0600, owner %s:%s) with NOEZEMA_DATABASE_URL=%s\n' \
      "$ENV_FILE" "$STAND_USER" "$STAND_GROUP" "$(printf '%s' "$url" | mask_creds)"
    note "            and NODE_OWNER, ADMIN_TOKEN, LLM_BASE_URL/MODEL/SCHEMA_PROFILE/MAX_OUTPUT_TOKENS/TIMEOUT,"
    note "            TOOL_EXECUTOR, SANDBOX_IMAGE/ENGINE/WORK_ROOT, HOST_LIB, UNIT_STATE, DATA_ROOT, WEB_HOST/PORT,"
    note "            NOEZEMA_DEV_DB_PORT=$DB_PORT (выбранный порт: его же читают status.sh и reset-db.sh)"
    return
  fi

  local tmp
  tmp="$(mktemp)"
  {
    echo "# NOEZEMA dev stand (T7.59) — generated by deploy/dev-stand/bootstrap.sh"
    echo "# mode 0600 owner $STAND_USER:$STAND_GROUP; contains secrets (AGENTS §5): never paste into chat or commits."
    echo "NOEZEMA_DATABASE_URL=$url"
    echo "NOEZEMA_DB_PASSWORD=$DB_PASSWORD"
    # The published host port of the dev container: chosen here (5432 or an auto-picked fallback) and
    # reused by status.sh / reset-db.sh so a rerun never "loses" the stand's database.
    echo "NOEZEMA_DEV_DB_PORT=$DB_PORT"
    echo "NOEZEMA_DATA_ROOT=$DATA_ROOT"
    echo "NOEZEMA_HOST_LIB=$HOST_LIB"
    echo "NOEZEMA_UNIT_STATE=$UNIT_STATE"
    echo "NOEZEMA_NODE_OWNER=$NODE_OWNER"
    echo "NOEZEMA_ADMIN_TOKEN=$ADMIN_TOKEN"
    echo "NOEZEMA_LLM_BASE_URL=$LLM_BASE_URL"
    echo "NOEZEMA_LLM_MODEL=$LLM_MODEL"
    echo "NOEZEMA_LLM_SCHEMA_PROFILE=$LLM_SCHEMA_PROFILE"
    # T7.80: without this line the stand keeps profile "none" — no reasoning parameter is ever
    # sent, and a truncated curator answer stays an immediate honest failure instead of one
    # reasoning-off retry. Set NOEZEMA_DEV_LLM_REASONING_PROFILE=halogen on the halogen node.
    echo "NOEZEMA_LLM_REASONING_PROFILE=$LLM_REASONING_PROFILE"
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

db_endpoint_ok() {  # TCP handshake + SELECT 1 on the published endpoint — what the APPLICATION does
  env NOEZEMA_DATABASE_URL="postgresql://$DB_USER:$DB_PASSWORD@127.0.0.1:$DB_PORT/postgres" \
    "$VENV/bin/python" - >/dev/null 2>&1 <<'PY'
import asyncio
import os

import asyncpg


async def main() -> None:
    conn = await asyncpg.connect(os.environ["NOEZEMA_DATABASE_URL"], timeout=5)
    try:
        assert (await conn.fetchval("SELECT 1")) == 1
    finally:
        await conn.close()


asyncio.run(main())
PY
}

# ── 5. Postgres 15 in docker, published on 127.0.0.1 only ────────────────────────────
step_postgres() {
  say "5. Postgres 15 (docker, 127.0.0.1 only)"
  firewall_diagnostics                     # diagnostics only: the script never touches the firewall
  local exists running status waited probe_ok=false
  exists="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -c "^${DB_CONTAINER}$" || true)"
  if [[ "${exists:-0}" -gt 0 ]]; then
    note "container $DB_CONTAINER exists: kept (data volume $DB_VOLUME is reused)"
    running="$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null || echo false)"
    [[ "$running" == "true" ]] || run "${SUDO[@]}" docker start "$DB_CONTAINER"
  else
    note "публикуем 127.0.0.1:$DB_PORT (порт выбран на шаге 0; в контейнере Postgres слушает 5432 как обычно)"
    run_secret "${SUDO[@]}" docker run -d --name "$DB_CONTAINER" --restart unless-stopped \
      -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB=postgres \
      -v "$DB_VOLUME:/var/lib/postgresql/data" \
      -p "127.0.0.1:$DB_PORT:5432" \
      --health-cmd "pg_isready -U $DB_USER -d postgres" \
      --health-interval 5s --health-timeout 5s --health-retries 12 "$DB_IMAGE"
  fi
  if $DRY_RUN; then note "(dry-run: ожидание эндпоинта пропущено)"; return; fi

  # Readiness is measured on the endpoint the application actually uses (127.0.0.1:$DB_PORT, SELECT 1),
  # not only on the container's internal healthcheck: an unpublished/blocked port otherwise shows up
  # much later as a mysterious session failure. -d postgres matters: without it pg_isready asks for a
  # database "noezema" that does not exist and the container reports unhealthy spam every 5 s.
  waited=0
  while (( waited < DB_READY_TIMEOUT )); do
    db_endpoint_ok && { probe_ok=true; break; }
    sleep 3
    waited=$((waited + 3))
  done
  if ! $probe_ok; then
    status="$(docker inspect -f 'state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$DB_CONTAINER" 2>/dev/null || echo missing)"
    note "Postgres не ответил на 127.0.0.1:$DB_PORT за ${DB_READY_TIMEOUT} с. Диагностика:"
    note "  контейнер: $status"
    if have ss; then
      note "  слушатели на :$DB_PORT -> $(ss -ltn 2>/dev/null | awk -v port=":$DB_PORT" '$4 ~ port "$"' | head -3 | tr '\n' ' ')"
    fi
    "${SUDO[@]}" docker logs --tail 20 "$DB_CONTAINER" 2>&1 | mask_creds | sed 's/^/   log: /' || true
    note "  если контейнер healthy, а порт не отвечает: deny-by-default фаервол/ufw (правило out на docker0,"
    note "   README «ВМ с deny-by-default UFW») или чужой процесс занял 127.0.0.1:$DB_PORT"
    die "postgres недоступен на 127.0.0.1:$DB_PORT: см. диагностику выше"
  fi
  note "postgres отвечает на 127.0.0.1:$DB_PORT (TCP + SELECT 1) за ${waited} с; наружу порт не опубликован"
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
    note "effective config is still the bootstrap snapshot -> activating $CONFIG_PAYLOAD"
    note "  (online change, §8.7.2: drain window up to 120 s; to change the config later use"
    note "   .venv/bin/python -m hostctl.cli activate-online --payload <file> --reason '…')"
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

  # Тик-таймер — единственный юнит стенда, который сам запускает сессию. По умолчанию он НЕ включается
  # (T7.61(б)): узел будит только оператор («wake now»), а не таймер каждые 60 с. Уже включённый таймер
  # мы не отключаем — это решение оператора, скрипт только сообщает текущее состояние.
  local units=(noezema-dev-unit-state.timer noezema-dev-maint.timer noezema-dev-web.service)
  if $WITH_TICK_TIMER; then
    units+=(noezema-dev-tick.timer)
    note "тик-таймер включается по --with-tick-timer: сессии стартуют сами, каждые wake_schedule.interval из снапшота"
  else
    note "тик-таймер НЕ включаем (дефолт): сессию запускает только «wake now» (кнопка или POST /api/v1/commands)"
    local tick_state
    tick_state="$(tick_timer_state)"
    if [[ "$tick_state" == enabled* ]]; then
      note "он УЖЕ включён (state=$tick_state) — сами мы его не отключаем: решение за оператором"
      note "  выключить плановые сессии: sudo systemctl disable --now noezema-dev-tick.timer"
    else
      note "состояние тик-таймера сейчас: $tick_state; включить плановые сессии: ./bootstrap.sh --with-tick-timer"
    fi
  fi
  run "${SUDO[@]}" systemctl enable "${units[@]}"
  note "started by you: systemctl start noezema-dev.target (the stand is not pulled in at boot)"
}

# ── 8. поиск (SearXNG): только по --with-searxng (T7.70, §5.12/§5.12.1) ────────────────
# Что это вообще такое: research_proxy (единственный egress узла) в режиме curated отправляет
# поисковый запрос на локальный SearXNG и получает навигационные данные (заголовок/url/фрагменты).
# Скорость, расход и раскрытие темы ограничивает узел (rate limit + журнал upstream из снапшота),
# а не контейнер. Контейнер публикуется только на 127.0.0.1: наружу он ничего не слушает.
searxng_container_state() {  # '' = контейнера нет; иначе одна строка состояния
  have docker || return 0
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$SEARXNG_CONTAINER" || return 0
  docker inspect -f 'state={{.State.Status}} restart={{.HostConfig.RestartPolicy.Name}} ports={{range $p, $conf := .NetworkSettings.Ports}}{{if $conf}}{{(index $conf 0).HostIp}}:{{(index $conf 0).HostPort}}<-{{$p}}{{end}}{{end}}' \
    "$SEARXNG_CONTAINER" 2>/dev/null || printf 'state=unknown'
}

# Guidance only: ни UFW, ни iptables, ни цепочку DOCKER-FORWARD скрипт не трогает (AGENTS §5).
searxng_egress_notes() {
  note "что нужно хосту, чтобы upstream-поиск работал (правила применяете ВЫ, не скрипт):"
  note "  исходящие 80/443 tcp — запросы SearXNG к поисковым движкам идут из контейнера (§5.12.1)"
  note "  исходящие DNS udp/tcp 53 — без резолвинга движков поиск не состоится"
  note "  правило out на docker0 to 172.17.0.0/16 — оно же нужно для пулл образов"
  note "  публикация только 127.0.0.1:$SEARXNG_PORT: контейнер не должен слушать ничего наружу"
  note "полные команды — README, разделы «ВМ с deny-by-default UFW» и «Поиск (SearXNG)»"
}

searxng_json_ready() {  # ровно тот эндпоинт, который использует research proxy: /search?format=json
  env NOEZEMA_SEARXNG_PROBE_URL="http://127.0.0.1:$SEARXNG_PORT" "$VENV/bin/python" - >/dev/null 2>&1 <<'PY'
import json
import os
import urllib.parse
import urllib.request

base = os.environ["NOEZEMA_SEARXNG_PROBE_URL"]
query = urllib.parse.urlencode({"q": "test", "format": "json"})
with urllib.request.urlopen(f"{base}/search?{query}", timeout=10) as response:
    if response.status != 200:
        raise SystemExit(1)
    payload = json.loads(response.read().decode("utf-8"))
if not isinstance(payload, dict) or "results" not in payload:
    raise SystemExit(1)
PY
}

step_searxng() {
  say "8. поиск (SearXNG)"
  local state settings running waited probe_ok
  state="$(searxng_container_state)"
  settings="$SEARXNG_SETTINGS_DIR/settings.yml"

  if ! $WITH_SEARXNG; then
    if [[ -n "$state" ]]; then
      note "контейнер $SEARXNG_CONTAINER уже есть: $state — без --with-searxng мы его НЕ меняем (не пересоздаём, не останавливаем, не удаляем)"
      note "проверить, что поиск отвечает: ./status.sh"
    else
      note "поиск не ставим (дефолт): пакет поднимается явно — ./bootstrap.sh --with-searxng"
    fi
    return 0
  fi

  searxng_egress_notes

  # 1) settings: секрет генерируется на ВМ, в репозитории лежит только шаблон с плейсхолдером (AGENTS §5).
  if $DRY_RUN; then
    printf '   [dry-run] %s --out %s   (secret_key генерируется при установке и не печатается)\n' \
      "$SCRIPT_DIR/searxng-settings.sh" "$settings"
  else
    run "${SUDO[@]}" bash "$SCRIPT_DIR/searxng-settings.sh" --out "$settings"
  fi

  # 2) container: существующий НЕ пересоздаётся, остановленный — поднимается.
  if [[ -n "$state" ]] && $RECREATE_SEARXNG; then
    note "пересоздаём по явному флагу --recreate-searxng: $state"
    run "${SUDO[@]}" docker rm -f "$SEARXNG_CONTAINER"
    state=""
  elif [[ -n "$state" ]]; then
    note "контейнер $SEARXNG_CONTAINER уже есть: $state — НЕ пересоздаём (для пересборки нужен --recreate-searxng)"
    running="$(docker inspect -f '{{.State.Running}}' "$SEARXNG_CONTAINER" 2>/dev/null || echo false)"
    [[ "$running" == "true" ]] || run "${SUDO[@]}" docker start "$SEARXNG_CONTAINER"
  fi

  if [[ -z "$state" ]]; then
    if port_is_taken "$SEARXNG_PORT"; then
      die "127.0.0.1:$SEARXNG_PORT уже занят (не нашим контейнером поиска): освободите его или задайте другой порт NOEZEMA_DEV_SEARXNG_PORT=<порт> и поменяйте research_proxy.searxng_url в снапшоте"
    fi
    note "публикуем 127.0.0.1:$SEARXNG_PORT (внутри контейнера SearXNG слушает $SEARXNG_INTERNAL_PORT)"
    run "${SUDO[@]}" docker run -d --name "$SEARXNG_CONTAINER" --restart unless-stopped \
      -v "$settings:/etc/searxng/settings.yml:ro" \
      -p "127.0.0.1:$SEARXNG_PORT:$SEARXNG_INTERNAL_PORT" \
      "$SEARXNG_IMAGE"
  fi
  if $DRY_RUN; then note "(dry-run: ожидание JSON-ответа пропущено)"; return 0; fi

  # 3) readiness is measured on the endpoint the node actually calls (HTTP 200 + JSON with results).
  # The probe itself goes to upstream engines: it is a real search for "test", and that is the point —
  # a container that answers HTML but cannot reach engines is not a working search.
  waited=0; probe_ok=false
  while (( waited < SEARXNG_READY_TIMEOUT )); do
    searxng_json_ready && { probe_ok=true; break; }
    sleep 3
    waited=$((waited + 3))
  done
  if ! $probe_ok; then
    note "SearXNG не ответил JSON на 127.0.0.1:$SEARXNG_PORT/search за ${SEARXNG_READY_TIMEOUT} с. Диагностика:"
    note "  контейнер: $(searxng_container_state)"
    note "  настройки: $settings (secret_key внутри файла не печатается)"
    "${SUDO[@]}" docker logs --tail 20 "$SEARXNG_CONTAINER" 2>&1 | mask_creds | sed 's/^/   log: /' || true
    note "  частые причины: нет исходящих 80/443 или DNS (см. правила выше), чужой процесс занял порт,"
    note "  контейнер не смог прочитать settings.yml (права), в настройках не включён формат json"
    die "поиск не готов: сессии узла будут работать, upstream-поиск — нет (вернитесь к правилам выше)"
  fi
  note "SearXNG отвечает JSON на 127.0.0.1:$SEARXNG_PORT (за ${waited} с); настройки: $settings (секрет не печатается)"
  note "снапшот указывает research_proxy.searxng_url=http://127.0.0.1:8888 — тот же адрес; состояние: ./status.sh"
}

step_summary() {
  say "готово"
  note "старт/стоп:   systemctl start|stop noezema-dev.target"
  note "UI:           http://$WEB_HOST:$WEB_PORT (token = NOEZEMA_ADMIN_TOKEN из $ENV_FILE)"
  note "вопрос:       форма «Задать вопрос», или $VENV/bin/python -m hostctl.cli ask \"...\" --priority 9"
  note "wake now:      ТОЛЬКО кнопка «wake now» на странице или POST /api/v1/commands (wake_now): они обходят интервал,"
  note "                но не admission. systemctl start noezema-dev-tick.service сессию НЕ запускает — тик"
  note "                посмотрит на интервал из снапшота и выведет wait (interval_not_elapsed)"
  note "сессии:      по умолчанию — только «wake now»; плановый тик включается явно (./bootstrap.sh --with-tick-timer)"
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
step_searxng
step_summary
