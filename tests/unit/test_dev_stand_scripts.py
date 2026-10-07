"""T7.59(в): deploy/dev-stand scripts are verified with SUBSTITUTED commands only (item 9–14).

A real bootstrap may never run in CI or on the dev box (.87) — these tests run the real scripts in
--dry-run with stub docker/ss/uv/sudo on PATH. They pin the behaviours that failed on the stand VM:
the port is checked and picked before secrets are written, a rerun reuses the container's port,
--force does not rotate secrets, and the uv fallback runs the installer as root into /usr/local/bin.
"""

from __future__ import annotations

import re
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


# ─── T7.70: пакет поиска (SearXNG) ─────────────────────────────────────────────────────

SEARXNG_SCRIPT = REPO_ROOT / "deploy" / "dev-stand" / "searxng-settings.sh"
SEARXNG_TEMPLATE = REPO_ROOT / "deploy" / "dev-stand" / "searxng" / "settings.yml"


def _run_settings_script(out_path: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """The settings renderer is pure local work: openssl + template. No network, no docker."""
    return subprocess.run(
        ["bash", str(SEARXNG_SCRIPT), "--out", str(out_path), *flags],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _secret_of(path: Path) -> str:
    line = next(ln for ln in path.read_text(encoding="utf-8").splitlines() if "secret_key:" in ln)
    return line.split("secret_key:", 1)[1].strip().strip('"')


class TestDevStandSearchPackage:
    """Поиск ставится только по явному флагу и никогда не пересоздаётся молча (T7.70)."""

    def test_search_is_not_installed_by_default(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "docker run" not in result.stdout or "noezema-searxng" not in result.stdout
        assert "поиск не ставим (дефолт)" in result.stdout
        assert "--with-searxng" in result.stdout

    def test_with_searxng_prints_the_exact_container_command(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, "--with-searxng")

        assert result.returncode == 0, result.stdout + result.stderr
        run_lines = [ln for ln in result.stdout.splitlines() if "docker run" in ln and "noezema-searxng" in ln]
        assert len(run_lines) == 1
        command = run_lines[0]
        # Published on 127.0.0.1 only, container listens 8080 internally (the spec's own port).
        assert "-p 127.0.0.1:8888:8080" in command
        assert "--restart unless-stopped" in command
        # Settings come from /etc/noezema/searxng and are mounted read-only.
        assert "-v /etc/noezema/searxng/settings.yml:/etc/searxng/settings.yml:ro" in command
        assert "searxng/searxng:latest" in command
        # The settings file is rendered from the repo template, never copied with a committed secret.
        assert "searxng-settings.sh --out /etc/noezema/searxng/settings.yml" in result.stdout
        assert "secret_key генерируется при установке и не печатается" in result.stdout

    def test_egress_is_described_never_opened(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, "--with-searxng")

        assert "исходящие 80/443 tcp" in result.stdout
        assert "DNS" in result.stdout
        assert "правила применяете ВЫ, не скрипт" in result.stdout
        text = BOOTSTRAP.read_text(encoding="utf-8")
        # Firewall words may only appear as prose/notes. What must never exist is a firewall command in
        # an executable position (optionally under $SUDO) — that is what would actually change the host.
        assert "ufw allow" not in _strip_notes(text)
        firewall_command = re.compile(
            r"(?m)^\s*(?:\"?\$\{?SUDO\}?\"?|sudo)?\s*(-n\s+)?(?:iptables|ip6tables|ufw)\s+(allow|deny|delete|insert|append|-)"
        )
        assert firewall_command.search(text) is None
        assert "DOCKER-USER" not in _strip_notes(text)

    def test_existing_search_container_survives_a_rerun(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(
            stubs,
            "--with-searxng",
            container_name="noezema-searxng",
            published_port="8888",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "уже есть" in result.stdout
        assert "НЕ пересоздаём" in result.stdout
        assert "docker rm -f noezema-searxng" not in result.stdout
        assert "docker run -d --name noezema-searxng" not in result.stdout

    def test_without_the_flag_an_existing_container_is_only_reported(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, container_name="noezema-searxng", published_port="8888")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "без --with-searxng мы его НЕ меняем" in result.stdout
        assert "docker rm -f noezema-searxng" not in result.stdout

    def test_rebuilding_the_container_is_an_explicit_flag(self, stubs: dict[str, Path]) -> None:
        plain = _run_bootstrap(stubs, "--with-searxng", container_name="noezema-searxng", published_port="8888")
        rebuilt = _run_bootstrap(
            stubs,
            "--with-searxng",
            "--recreate-searxng",
            container_name="noezema-searxng",
            published_port="8888",
        )

        assert plain.returncode == 0 and "docker rm -f noezema-searxng" not in plain.stdout
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        assert "--recreate-searxng" in rebuilt.stdout
        assert "docker rm -f noezema-searxng" in rebuilt.stdout
        assert any("docker run" in ln and "noezema-searxng" in ln for ln in rebuilt.stdout.splitlines())

    def test_taken_search_port_refuses_with_a_hint(self, stubs: dict[str, Path]) -> None:
        result = _run_bootstrap(stubs, "--with-searxng", listening=(8888,))

        assert result.returncode != 0
        assert "127.0.0.1:8888 уже занят" in result.stderr
        assert "NOEZEMA_DEV_SEARXNG_PORT=" in result.stderr

    def test_flags_are_documented_in_help(self) -> None:
        result = subprocess.run(
            ["bash", str(BOOTSTRAP), "--help"], capture_output=True, text=True, timeout=60, check=False
        )

        assert result.returncode == 0
        assert "--with-searxng" in result.stdout
        assert "--recreate-searxng" in result.stdout


class TestDevStandSearchSettings:
    """Настройки поиска: секрет живёт только на ВМ (AGENTS §5), шаблон — в репо."""

    def test_template_carries_no_secret(self) -> None:
        text = SEARXNG_TEMPLATE.read_text(encoding="utf-8")

        assert 'secret_key: "@SECRET@"' in text
        assert "limiter: false" in text
        assert "- html" in text and "- json" in text
        assert "@SECRET@" in text
        # A committed 32-byte hex key would mean a real secret leaked into the repository.
        assert re.search(r"secret_key:\s*\"[0-9a-f]{16,}\"", text) is None

    def test_bootstrap_renders_settings_instead_of_committing_them(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")

        assert "searxng-settings.sh" in text
        assert "/etc/noezema/searxng/settings.yml" in text

    def test_renderer_generates_a_secret_and_never_prints_it(self, tmp_path: Path) -> None:
        out = tmp_path / "settings.yml"

        first = _run_settings_script(out)

        assert first.returncode == 0, first.stdout + first.stderr
        secret = _secret_of(out)
        assert re.fullmatch(r"[0-9a-f]{32,64}", secret), "secret_key must be a generated hex token"
        assert secret not in first.stdout and secret not in first.stderr
        assert "@SECRET@" not in out.read_text(encoding="utf-8")
        assert out.stat().st_mode & 0o777 == 0o644  # readable by the (non-owner) uid inside the container

    def test_rerun_keeps_the_secret_rotation_is_explicit(self, tmp_path: Path) -> None:
        out = tmp_path / "settings.yml"

        _run_settings_script(out)
        first = _secret_of(out)
        again = _run_settings_script(out)
        assert again.returncode == 0, again.stdout + again.stderr
        assert "оставляем" in again.stdout
        assert _secret_of(out) == first

        rotated = _run_settings_script(out, "--rotate-secret")
        assert rotated.returncode == 0, rotated.stdout + rotated.stderr
        assert _secret_of(out) != first
        assert first not in rotated.stdout

    def test_check_mode_is_read_only(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "settings.yml"

        missing = subprocess.run(
            ["bash", str(SEARXNG_SCRIPT), "--out", str(out), "--check"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert missing.returncode == 1 and not out.exists()

        _run_settings_script(out)
        ready = subprocess.run(
            ["bash", str(SEARXNG_SCRIPT), "--out", str(out), "--check"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert ready.returncode == 0 and "готовы" in ready.stdout

    def test_readme_documents_the_search_package(self) -> None:
        text = (REPO_ROOT / "deploy" / "dev-stand" / "README.md").read_text(encoding="utf-8")

        assert "## Поиск (SearXNG, T7.70)" in text
        assert "./bootstrap.sh --with-searxng" in text
        assert "sudo docker rm -v -f noezema-searxng" in text
        assert "/etc/noezema/searxng/settings.yml" in text
        assert "127.0.0.1:8888" in text
        # The rollback and the firewall guidance are operator actions, not script actions.
        assert "не меняет UFW и iptables" in text


class TestDevStandStatusSearch:
    """status.sh обязан сказать про поиск три вещи: есть/нет, отвечает/нет, режим снапшота."""

    def test_status_reports_the_missing_search_container(self, stubs: dict[str, Path]) -> None:
        result = _run_status(stubs)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "SearXNG: контейнера нет" in result.stdout
        assert "режим research_proxy активного снапшота" in result.stdout

    def test_status_reports_an_existing_container_without_probing_it(self, stubs: dict[str, Path]) -> None:
        result = _run_status(
            stubs,
            "--no-search",
            extra_env={"STUB_CONTAINER_NAME": "noezema-searxng", "STUB_PUBLISHED_PORT": "8888"},
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "SearXNG: контейнер есть" in result.stdout
        assert "не проверяли (--no-search)" in result.stdout

    def test_search_probe_is_opt_out_not_opt_in(self) -> None:
        text = STATUS.read_text(encoding="utf-8")

        assert "--no-search" in text
        assert "research_proxy->>'mode'" in text
        assert "web.search доступен модели в этом снапшоте" in text


class TestDevStandConfigVersion:
    """Стенд по умолчанию поднимает актуальную конфигурацию, а предыдущая остаётся откатом (T7.76).

    Это не косметика: менеджер, повторяющий `bootstrap.sh` на .92, активирует ровно тот payload,
    который закреплён дефолтом скрипта. Если дефолт и откат перепутаны местами, стенд молча вернётся
    на прежний промпт исследователя (односторонний поиск) — и следующий замер это измерит не там.
    """

    V16 = "config-v16-payload.json"
    V15 = "config-v15-payload.json"
    #: как эти файлы названы в скриптах (они указывают на payload через $REPO_ROOT)
    V16_REF = "docs/eval/config-v16-payload.json"
    V15_REF = "docs/eval/config-v15-payload.json"
    #: canonical-хеші payload'ов: именно они попадают в `config_snapshots.payload_sha256` (AGENTS §8),
    #  в скриптах и README они названы короткими префиксами — оператор сверяет head по ним
    V16_CANONICAL = "740ae9a1"
    V15_CANONICAL = "b3801812"

    def test_bootstrap_defaults_to_config_v16_and_names_v15_as_the_rollback(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")

        assert f'CONFIG_PAYLOAD="${{NOEZEMA_DEV_CONFIG_PAYLOAD:-$REPO_ROOT/{self.V16_REF}}}"' in text
        # откат обязан быть назван явно: прежнему payload'у принадлежит canonical-хеш, не хеш файла
        rollback_line = next(line for line in text.splitlines() if self.V15_REF in line)
        assert "NOEZEMA_DEV_CONFIG_PAYLOAD" in rollback_line
        assert self.V15_CANONICAL in text
        # причина активации говорит, что именно изменилось (промпт + лимит шагов), а не «обновили конфиг»
        reason = next(line for line in text.splitlines() if line.startswith("CONFIG_REASON="))
        assert "config-v16" in reason and "explorer-v8" in reason and "16 explorer steps" in reason

    def test_reset_db_reactivates_the_same_config_as_bootstrap(self) -> None:
        script = (REPO_ROOT / "deploy" / "dev-stand" / "reset-db.sh").read_text(encoding="utf-8")

        assert f'CONFIG_PAYLOAD="${{NOEZEMA_DEV_CONFIG_PAYLOAD:-$REPO_ROOT/{self.V16_REF}}}"' in script
        assert self.V15_REF in script  # откат не потерян и после сброса базы
        assert "config-v16" in script

    def test_readme_documents_the_activation_and_the_rollback_with_canonical_hashes(self) -> None:
        text = (REPO_ROOT / "deploy" / "dev-stand" / "README.md").read_text(encoding="utf-8")

        assert self.V16_CANONICAL in text
        assert self.V15_CANONICAL in text
        assert "config-v16-payload.json" in text
        assert "docs/eval/config-v15-payload.json" in text
        # активация — команда менеджера с drain, а не тихая правка снапшота
        assert "activate-online" in text and "--drain-wait-seconds 120" in text

    def test_config_v16_payload_file_exists_next_to_the_older_ones(self) -> None:
        """Новый номер конфигурации = новый файл, прежние не переписываются (AGENTS §8)."""
        for name in ("config-v13-payload.json", "config-v14-payload.json", "config-v15-payload.json", self.V16):
            path = REPO_ROOT / "docs" / "eval" / name
            assert path.is_file(), name

