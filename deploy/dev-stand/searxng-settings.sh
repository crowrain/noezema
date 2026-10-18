#!/usr/bin/env bash
# NOEZEMA dev stand (T7.70): render the SearXNG settings file from the repo template.
#
# The secret_key is generated here, is NEVER printed and NEVER committed (AGENTS §5). The rendered
# file is what the container mounts read-only; the template in this repository keeps only the
# placeholder. A rerun keeps the existing key: rotating it would silently invalidate whatever session
# cookies the operator has in the SearXNG html UI, so rotation is an explicit act (--rotate-secret).
#
#   ./searxng-settings.sh --out /etc/noezema/searxng/settings.yml         create if missing (idempotent)
#   ./searxng-settings.sh --out ... --rotate-secret                       deliberately new key
#   ./searxng-settings.sh --out ... --check                               read-only: ready? (exit 0/1)
#
# Env knob: SEARXNG_SETTINGS_TEMPLATE (default deploy/dev-stand/searxng/settings.yml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SEARXNG_SETTINGS_TEMPLATE:-$SCRIPT_DIR/searxng/settings.yml}"
PLACEHOLDER='@SECRET@'

OUT=""
ROTATE=false
CHECK_ONLY=false

say()  { printf '   %s\n' "$*"; }
die()  { printf 'searxng-settings: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="${2:?--out needs a path}"; shift ;;
    --template) TEMPLATE="${2:?--template needs a path}"; shift ;;
    --rotate-secret) ROTATE=true ;;
    --check) CHECK_ONLY=true ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

[[ -n "$OUT" ]] || die "--out is required"

existing_key() {  # read-only: does the rendered file already carry a generated key?
  [[ -s "$OUT" ]] || return 1
  grep -q '^[[:space:]]*secret_key:' "$OUT" || return 1
  ! grep -q "$PLACEHOLDER" "$OUT"
}

if $CHECK_ONLY; then
  if existing_key; then say "settings готовы: $OUT (ключ записан, не печатается)"; exit 0; fi
  say "settings НЕ готовы: $OUT (файла нет или ключ не сгенерирован)"
  exit 1
fi

[[ -f "$TEMPLATE" ]] || die "template not found: $TEMPLATE"
grep -q "$PLACEHOLDER" "$TEMPLATE" || die "template has no $PLACEHOLDER placeholder: $TEMPLATE"

if existing_key && ! $ROTATE; then
  say "секрет уже записан в $OUT: оставляем (повторный запуск его НЕ ротирует; для ротации — --rotate-secret)"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || die "openssl нужен, чтобы сгенерировать secret_key"
secret="$(openssl rand -hex 32)"

mkdir -p "$(dirname "$OUT")"
tmp="$(mktemp)"
# The substitution is done by awk with the value taken from the environment: a secret must never end up
# in a command line (it would be visible in ps and in shell history).
SEARXNG_SECRET="$secret" awk '{ gsub(/@SECRET@/, ENVIRON["SEARXNG_SECRET"]); print }' "$TEMPLATE" > "$tmp"
if grep -q "$PLACEHOLDER" "$tmp"; then
  rm -f "$tmp"
  die "placeholder не подставился: проверьте $TEMPLATE"
fi
chmod 0644 "$tmp"          # читаемость нужна процессу внутри контейнера: его uid не владелец файла на хосте
mv "$tmp" "$OUT"
say "settings записаны: $OUT (secret_key сгенерирован и НЕ печатается; режим $(stat -c %a "$OUT"))"
say "файл опубликован контейнеру только как /etc/searxng/settings.yml (read-only), сам контейнер — на 127.0.0.1"
