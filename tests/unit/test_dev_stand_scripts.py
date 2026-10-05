"""T7.59(в): deploy/dev-stand scripts are verified with SUBSTITUTED commands only (item 9–14).

A real bootstrap may never run in CI or on the dev box (.87) — these tests run the real scripts in
--dry-run with stub docker/ss/uv/sudo on PATH. They pin the behaviours that failed on the stand VM:
the port is checked and picked before secrets are written, a rerun reuses the container's port,
--force does not rotate secrets, and the uv fallback runs the installer as root into /usr/local/bin.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "deploy" / "dev-stand" / "bootstrap.sh"
STATUS = REPO_ROOT / "deploy" / "dev-stand" / "status.sh"

# bootstrap asks systemctl only whether the tick timer is enabled (T7.61(б)); status.sh additionally lists
# units and shows timer schedules. STUB_TICK_TIMER_STATE pins the answer for the tick timer specifically.
_SYSTEMCTL_STUB = """#!/usr/bin/env bash
if [[ $1 == is-enabled && $2 == noezema-dev-tick.timer && -n "${STUB_TICK_TIMER_STATE:-}" ]]; then
  printf '%s\n' "$STUB_TICK_TIMER_STATE"; exit 0
fi
if [[ $1 == is-enabled ]]; then
  printf 'disabled\n'; exit 0
fi
exit 0
"""

FAKE_DB_PASSWORD = "faux-db-password-must-never-be-printed"
FAKE_ADMIN_TOKEN = "faux-admin-token-must-never-be-printed"

_DOCKER_STUB = """#!/usr/bin/env bash
args="$*"
if [[ $args == ps* ]]; then
  if [[ -n "${STUB_CONTAINER_NAME:-}" ]]; then printf '%s\\n' "$STUB_CONTAINER_NAME"; fi
  exit 0
fi
if [[ $args == inspect*HostPort* ]]; then
  if [[ -n "${STUB_PUBLISHED_PORT:-}" ]]; then printf '%s\\n' "$STUB_PUBLISHED_PORT"; exit 0; fi
  exit 1
fi
if [[ $args == *'.State.Running'* ]]; then printf 'true\\n'; exit 0; fi
if [[ $args == *health* ]]; then printf 'healthy\\n'; exit 0; fi
if [[ $args == volume* ]]; then exit "${STUB_VOLUME_EXIT:-1}"; fi
if [[ $args == *'image inspect'* ]]; then exit "${STUB_IMAGE_EXIT:-0}"; fi
exit 0
"""

_SS_STUB = """#!/usr/bin/env bash
printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\\n'
for port in ${STUB_LISTENING:-}; do
  printf 'LISTEN 0 4096 127.0.0.1:%s 0.0.0.0:*\\n' "$port"
done
exit 0
"""

_ENV_FILE_TEMPLATE = f"""NOEZEMA_DATABASE_URL=postgresql+asyncpg://noezema:{FAKE_DB_PASSWORD}@127.0.0.1:5432/noezema-dev
NOEZEMA_DB_PASSWORD={FAKE_DB_PASSWORD}
NOEZEMA_ADMIN_TOKEN={FAKE_ADMIN_TOKEN}
NOEZEMA_DEV_DB_PORT=5432
"""


def _write_stub(dir_path: Path, name: str, body: str) -> None:
    script = dir_path / name
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)


@pytest.fixture()
def stubs(tmp_path: Path) -> dict[str, Path]:
    """PATH containing only the substituted commands plus the real coreutils/python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "docker", _DOCKER_STUB)
    _write_stub(bin_dir, "ss", _SS_STUB)
    _write_stub(bin_dir, "sudo", '#!/usr/bin/env bash\nexec "$@"\n')
    _write_stub(bin_dir, "apt-get", '#!/usr/bin/env bash\nexit 0\n')
    _write_stub(bin_dir, "systemctl", _SYSTEMCTL_STUB)
    _write_stub(bin_dir, "ufw", '#!/usr/bin/env bash\nprintf "Status: active\\n"\n')
    _write_stub(bin_dir, "usermod", '#!/usr/bin/env bash\nexit 0\n')
    return {
        "bin": bin_dir,
        "env_file": tmp_path / "dev.env",
        "data_root": tmp_path / "var-lib-noezema-dev",
    }


def _run_bootstrap(
    stubs: dict[str, Path],
    *flags: str,
    listening: tuple[int, ...] = (),
    container_name: str = "",
    published_port: str = "",
    with_uv: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real bootstrap.sh in --dry-run against substituted commands."""
    env = {
        "PATH": f"{stubs['bin']}:/usr/bin:/bin",
        "HOME": str(stubs["bin"].parent),
        "NOEZEMA_DEV_ENV_FILE": str(stubs["env_file"]),
        "NOEZEMA_DEV_DATA_ROOT": str(stubs["data_root"]),
        "NOEZEMA_DEV_DB_CONTAINER": "noezema-dev-db-test",
        "NOEZEMA_DEV_TOOL_EXECUTOR": "stub",
        "STUB_LISTENING": " ".join(str(port) for port in listening),
        "STUB_CONTAINER_NAME": container_name,
        "STUB_PUBLISHED_PORT": published_port,
    }
    if with_uv:
        _write_stub(stubs["bin"], "uv", '#!/usr/bin/env bash\nprintf "uv 0.9.18\\n"\n')
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(BOOTSTRAP), "--dry-run", *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _run_status(
    stubs: dict[str, Path],
    *flags: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real status.sh (read-only by construction) against substituted commands.

    The DB container name is deliberately absent so that the script reports instead of reaching into a real
    Postgres; LLM and web probes are skipped by flags.
    """
    env = {
        "PATH": f"{stubs['bin']}:/usr/bin:/bin",
        "HOME": str(stubs["bin"].parent),
        "NOEZEMA_DEV_ENV_FILE": str(stubs["env_file"]),
        "NOEZEMA_DEV_DATA_ROOT": str(stubs["data_root"]),
        "NOEZEMA_DEV_DB_CONTAINER": "noezema-dev-db-not-present",
        "STUB_TICK_TIMER_STATE": "",
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(STATUS), "--no-llm", "--no-web", *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


pytestmark = pytest.mark.unit

_REQUIRED_RULES: ClassVar[tuple[str, ...]] = (
    "sudo ufw allow out on docker0 to 172.17.0.0/16",
    "sudo ufw allow in from 192.168.1.0/24 to any port $WEB_PORT proto tcp",
)


class TestDevStandBootstrapDryRun:
    def test_changes_nothing_and_masks_secrets(self, stubs: dict[str, Path]) -> None:
        stubs["env_file"].write_text(_ENV_FILE_TEMPLATE, encoding="utf-8")

        result = _run_bootstrap(stubs)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (result.stdout + result.stderr).count(FAKE_DB_PASSWORD) == 0
        assert (result.stdout + result.stderr).count(FAKE_ADMIN_TOKEN) == 0
        # dry-run never touches the env file or the data root.
        assert stubs["env_file"].read_text(encoding="utf-8") == _ENV_FILE_TEMPLATE
        assert not stubs["data_root"].exists()
        assert "[dry-run]" in result.stdout

    def test_port_is_picked_before_secrets_when_5432_is_taken(self, stubs: dict[str, Path]) -> None:
        # The stand VM runs a native postgresql.service on 127.0.0.1:5432 (observed on 192.168.1.92).
        result = _run_bootstrap(stubs, listening=(5432,))

        assert result.returncode == 0, result.stdout + result.stderr
        assert "port БД" in result.stdout or "порт БД" in result.stdout
        assert "5433" in result.stdout
        assert "127.0.0.1:5433/noezema-dev" in result.stdout  # the URL written to the env file

    def test_explicit_taken_port_fails_with_a_hint(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(
            stubs,
            listening=(5432,),
            extra_env={"NOEZEMA_DEV_DB_PORT": "5432"},
        )

        assert result.returncode != 0
        assert "уже занят" in result.stderr
        assert "NOEZEMA_DEV_DB_PORT=5433" in result.stderr

    def test_rerun_reuses_the_existing_container_port(self, stubs: dict[str, Path]) -> None:
        # Container already publishes 5439; a rerun must not pick anything else and must not die on 5432.
        stubs["env_file"].write_text(_ENV_FILE_TEMPLATE, encoding="utf-8")

        result = _run_bootstrap(
            stubs,
            listening=(5432, 5439),
            container_name="noezema-dev-db-test",
            published_port="5439",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "переиспользуем" in result.stdout
        assert "127.0.0.1:5439/noezema-dev" in result.stdout
        assert "NOEZEMA_DEV_DB_PORT=5433" not in result.stderr

    def test_existing_cluster_without_a_known_password_refuses(self, stubs: dict[str, Path]) -> None:
        # Inventing a password next to an existing volume would write an env file that cannot connect.
        result = _run_bootstrap(
            stubs,
            container_name="noezema-dev-db-test",
            published_port="5439",
        )

        assert result.returncode != 0
        assert "не найден" in result.stderr
        assert "docker volume rm noezema-dev-pgdata" in result.stderr

    def test_force_keeps_secrets_of_an_existing_cluster(self, stubs: dict[str, Path]) -> None:
        stubs["env_file"].write_text(_ENV_FILE_TEMPLATE, encoding="utf-8")

        result = _run_bootstrap(
            stubs,
            "--force",
            container_name="noezema-dev-db-test",
            published_port="5432",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "kept (--force его НЕ ротирует" in result.stdout
        assert FAKE_ADMIN_TOKEN not in result.stdout

    def test_rotation_is_an_explicit_flag_only(self, stubs: dict[str, Path]) -> None:
        stubs["env_file"].write_text(_ENV_FILE_TEMPLATE, encoding="utf-8")

        plain = _run_bootstrap(stubs, container_name="noezema-dev-db-test", published_port="5432")
        rotating = _run_bootstrap(
            stubs,
            "--rotate-secrets",
            container_name="noezema-dev-db-test",
            published_port="5432",
        )

        assert plain.returncode == 0 and "kept (--force его НЕ ротирует" in plain.stdout
        assert rotating.returncode == 0, rotating.stdout + rotating.stderr
        assert "rotated the admin token" in rotating.stdout
        # The DB password lives in the volume: rotation there is refused, not silently done.
        assert "пароль БД НЕ ротирован" in rotating.stdout
        assert FAKE_DB_PASSWORD not in rotating.stdout

    def test_uv_fallback_installs_as_root_into_usr_local_bin(self, stubs: dict[str, Path]) -> None:
        # Ubuntu 24.04 has no apt package uv; the old fallback wrote /usr/local/bin without rights.
        result = _run_bootstrap(stubs, with_uv=False)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "sudo env UV_INSTALL_DIR=/usr/local/bin" in result.stdout
        assert "verify uv --version" in result.stdout


class TestDevStandScriptContent:
    """Cheap pins for the two host-facing behaviours that broke on the VM."""

    def test_postgres_healthcheck_names_a_database_that_exists(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")

        # Without -d, pg_isready asks for database "noezema" and the container spams unhealthy every 5 s.
        assert '--health-cmd "pg_isready -U $DB_USER -d postgres"' in text

    def test_readiness_is_probed_on_the_endpoint_the_application_uses(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")

        assert "db_endpoint_ok" in text
        assert "SELECT 1" in text
        assert '127.0.0.1:$DB_PORT/postgres' in text

    def test_firewall_is_only_diagnosed_never_changed(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")

        assert "firewall_diagnostics" in text
        for rule in _REQUIRED_RULES:
            assert rule in text
        # The script prints rules; it must never install them itself.
        assert "ufw allow" not in _strip_notes(text)

    def test_wake_now_is_described_as_a_button_not_systemctl(self) -> None:
        for path in (BOOTSTRAP, REPO_ROOT / "deploy" / "dev-stand" / "status.sh"):
            text = path.read_text(encoding="utf-8")
            assert "interval_not_elapsed" in text
            assert "НЕ запускает" in text

    def test_tick_timer_is_reported_by_status_and_bootstrap(self) -> None:
        # T7.61(б): both scripts must state the tick-timer decision in words an operator can act on.
        status_text = (REPO_ROOT / "deploy" / "dev-stand" / "status.sh").read_text(encoding="utf-8")
        bootstrap_text = BOOTSTRAP.read_text(encoding="utf-8")
        assert "тик-таймер: выключен (сессии — только wake now)" in status_text
        assert "systemctl disable --now noezema-dev-tick.timer" in status_text
        assert "--with-tick-timer) WITH_TICK_TIMER=true" in bootstrap_text
        # An already enabled timer is only reported. The scripts never run systemctl disable themselves.
        assert "systemctl disable" not in _strip_notes(bootstrap_text)


class TestDevStandTickTimerOptIn:
    """T7.61(б): плановые сессии — только по явному флагу; уже включённый таймер не трогается."""

    def test_tick_timer_is_not_enabled_by_default(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs)

        assert result.returncode == 0, result.stdout + result.stderr
        enabled_line = next(
            (line for line in result.stdout.splitlines() if "systemctl enable" in line), ""
        )
        assert "noezema-dev-tick.timer" not in enabled_line
        # everything else is still enabled: without unit-state the Command API answers 423, without maint
        # nothing reconciles.
        for unit in ("noezema-dev-unit-state.timer", "noezema-dev-maint.timer", "noezema-dev-web.service"):
            assert unit in enabled_line
        assert "дефолт" in result.stdout and "wake now" in result.stdout

    def test_flag_enables_the_tick_timer(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, "--with-tick-timer")

        assert result.returncode == 0, result.stdout + result.stderr
        enabled_line = next(
            (line for line in result.stdout.splitlines() if "systemctl enable" in line), ""
        )
        assert "noezema-dev-tick.timer" in enabled_line
        assert "--with-tick-timer" in result.stdout

    def test_already_enabled_timer_is_reported_not_disabled(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, extra_env={"STUB_TICK_TIMER_STATE": "enabled"})

        assert result.returncode == 0, result.stdout + result.stderr
        assert "УЖЕ включён" in result.stdout
        # The hint is printed for the operator; the script never runs it.
        assert "sudo systemctl disable --now noezema-dev-tick.timer" in result.stdout
        # The hint is printed for the operator; the plan itself contains no disable step.
        assert not [line for line in result.stdout.splitlines() if "[dry-run]" in line and "disable" in line]
        enabled_line = next(
            (line for line in result.stdout.splitlines() if "systemctl enable" in line), ""
        )
        assert "noezema-dev-tick.timer" not in enabled_line

    def test_flag_is_documented_in_help(self) -> None:
        result = subprocess.run(
            ["bash", str(BOOTSTRAP), "--help"], capture_output=True, text=True, timeout=60, check=False
        )
        assert result.returncode == 0
        assert "--with-tick-timer" in result.stdout

    def test_status_shows_the_tick_timer_state(self, stubs: dict[str, Path]) -> None:
        off = _run_status(stubs)
        assert off.returncode == 0, off.stdout + off.stderr
        assert "тик-таймер: выключен (сессии — только wake now)" in off.stdout

        on = _run_status(stubs, extra_env={"STUB_TICK_TIMER_STATE": "enabled"})
        assert on.returncode == 0, on.stdout + on.stderr
        assert "тик-таймер: включён" in on.stdout
        assert "тик-таймер: выключен" not in on.stdout

    def test_readme_documents_how_sessions_start(self) -> None:
        text = (REPO_ROOT / "deploy" / "dev-stand" / "README.md").read_text(encoding="utf-8")
        assert "## Как запускать сессии" in text
        assert "--with-tick-timer" in text
        assert "systemctl disable --now noezema-dev-tick.timer" in text


def _strip_notes(text: str) -> str:
    """Drop the printed guidance lines, keep only what the script could execute."""

    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("note ", 'note "', "printf"))
    )
