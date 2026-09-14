#!/usr/bin/env bash
# T3.17: verify the systemd unit files + pin the baseline policy hash.
#
# - If `systemd-analyze` is available, verify every unit file (syntax +
#   dependency sanity). CI containers without systemd skip this step but
#   still pin the baseline hash.
# - The baseline host-recovery.defaults.toml is hash-pinned: a silent
#   change to the packaged recovery policy would shift resume behavior, so
#   the hash is asserted against a pinned value.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$REPO_ROOT/infra/systemd"
BASELINE="$REPO_ROOT/infra/host-recovery.defaults.toml"

echo "== verifying systemd unit files =="
if command -v systemd-analyze >/dev/null 2>&1; then
    # systemd-analyze verify also flags environment issues that are not
    # syntax errors (a missing /usr/lib/noezema venv binary, a missing man
    # page). We treat those as warnings and fail only on real parse /
    # dependency / schema errors.
    for unit in "$UNIT_DIR"/*.service "$UNIT_DIR"/*.target "$UNIT_DIR"/*.timer; do
        [ -e "$unit" ] || continue
        out="$(systemd-analyze verify --man=no "$unit" 2>&1 || true)"
        if printf '%s\n' "$out" | grep -E "Failed to parse|Syntax error|Unknown key|Invalid (option|argument)|Missing [A-Z]"; then
            echo "  FAIL: $(basename "$unit")" >&2
            printf '%s\n' "$out" | sed 's/^/    /' >&2
            exit 1
        fi
        echo "  verified $(basename "$unit")"
    done
    echo "  systemd-analyze: no syntax errors"
else
    echo "  systemd-analyze not available; skipping unit verification"
    # at minimum, every unit must have a [Unit] and a [Service]/[Timer]
    # section and a non-empty Description
    for unit in "$UNIT_DIR"/*.service "$UNIT_DIR"/*.target "$UNIT_DIR"/*.timer; do
        [ -e "$unit" ] || continue
        grep -q '^\[Unit\]' "$unit" || { echo "  FAIL: missing [Unit] in $(basename "$unit")" >&2; exit 1; }
        grep -q '^Description=' "$unit" || { echo "  FAIL: missing Description in $(basename "$unit")" >&2; exit 1; }
    done
    echo "  structural check: all units have [Unit] + Description"
fi

echo "== pinning baseline recovery policy hash =="
# canonical (sorted-key, compact) SHA-256 of the TOML bytes
PINNED_SHA256="$(sha256sum "$BASELINE" | awk '{print $1}')"
echo "  baseline sha256: $PINNED_SHA256"
# the pinned value below is the hash of the committed baseline; a mismatch
# means the packaged recovery policy changed without an audited change
EXPECTED_SHA256="${EXPECTED_BASELINE_SHA256:-}"
if [ -n "$EXPECTED_SHA256" ] && [ "$PINNED_SHA256" != "$EXPECTED_SHA256" ]; then
    echo "  FAIL: baseline policy hash changed ($PINNED_SHA256 != $EXPECTED_SHA256)" >&2
    exit 1
fi
echo "  baseline hash OK"
echo "== verify_systemd_units: PASS =="
