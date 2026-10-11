"""One-shot container sandbox runtime (T2.3, ADR-0002, §11).

A disposable single-use container per session:
  - network: none, read-only rootfs, cap-drop ALL, no-new-privileges;
  - /workspace is a per-session HOST directory bind-mounted rw, initialized
    from the read-only /base at container start (disposable: the host dir is
    destroyed with the session; the frozen manifest is the only survivor —
    PR #14). A host bind is used instead of a container tmpfs because
    docker-cp from tmpfs is broken on Docker 29 + the containerd store;
  - a scratch /tmp tmpfs makes the read-only rootfs usable by the tools;
  - hard CPU / memory / PID / timeout limits;
  - destroyed after the session (and on any failure path).

The engine is the `docker` CLI in dev and `podman` in prod — the command
verbs used here are identical for both.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

WORKSPACE_PATH = "/workspace"
BASE_PATH = "/base"
# the sandbox user in sandbox/Containerfile (uid/gid 10001)
SANDBOX_UID = 10001
SANDBOX_GID = 10001
INIT_COMMAND = f"cp -a {BASE_PATH}/. {WORKSPACE_PATH}/ 2>/dev/null; exec sleep infinity"

_KNOWN_PROFILE_KEYS = frozenset(
    {
        "version",
        "name",
        "network",
        "read_only_rootfs",
        "capabilities",
        "no_new_privileges",
        "seccomp",
        "resource_limits",
        "tools",
        "paths",
        "secrets",
        "metadata_endpoints",
    }
)


class SandboxError(RuntimeError):
    """Base for sandbox failures."""


class SandboxHealthError(SandboxError):
    """The engine or image is not usable."""


class SandboxImageMissingError(SandboxHealthError):
    pass


class SandboxProfile(BaseModel):
    """Capability profile for the sandbox (T2.1/T2.4 input).

    Closed schema: unknown keys are rejected so a typo in a YAML cannot
    silently widen (or narrow) the profile.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    name: str
    network: str = "none"
    read_only_rootfs: bool = True
    capabilities: list[str] = []
    no_new_privileges: bool = True
    seccomp: str = "default"
    resource_limits: dict[str, Any] = {
        "cpu": "1.0",
        "memory_mb": 512,
        "max_processes": 32,
        "timeout_seconds": 60,
        "max_file_size_mb": 50,
    }
    tools: dict[str, bool] = {}
    paths: dict[str, list[str]] = {"writable": [WORKSPACE_PATH], "readable": [BASE_PATH, WORKSPACE_PATH]}
    secrets: str = "deny"
    metadata_endpoints: str = "deny"

    @classmethod
    def from_yaml(cls, path: Path) -> SandboxProfile:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SandboxError(f"profile {path} is not a mapping")
        unknown = set(raw) - _KNOWN_PROFILE_KEYS
        if unknown:
            raise SandboxError(f"profile {path} has unknown keys: {sorted(unknown)}")
        profile = cls.model_validate(raw)
        if profile.secrets != "deny":
            raise SandboxError(f"profile {path}: secrets must be 'deny' (§11.2)")
        if profile.metadata_endpoints != "deny":
            raise SandboxError(f"profile {path}: metadata endpoints must be 'deny'")
        return profile

    def tool_allowed(self, tool: str) -> bool:
        return self.tools.get(tool, False)

    @property
    def cpu(self) -> str:
        return str(self.resource_limits.get("cpu", "1.0"))

    @property
    def memory_mb(self) -> int:
        return int(self.resource_limits.get("memory_mb", 512))

    @property
    def max_processes(self) -> int:
        return int(self.resource_limits.get("max_processes", 32))

    @property
    def timeout_seconds(self) -> int:
        return int(self.resource_limits.get("timeout_seconds", 60))

    @property
    def max_file_size_mb(self) -> int:
        return int(self.resource_limits.get("max_file_size_mb", 50))

    @property
    def tmpfs_size_mb(self) -> int:
        """Workspace tmpfs cap: headroom over the single-file limit."""
        return max(64, self.max_file_size_mb * 4)


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOEZEMA_SANDBOX_")

    image: str = "noezema-sandbox:dev"
    engine: str = "docker"  # or "podman" in prod
    work_root: Path = Path("/var/lib/noezema/sandbox")


logger = logging.getLogger(__name__)

# ── T7.95: потолки уборки, которая идёт ПОСЛЕ внешней отмены ─────────────────────────
# `asyncio.wait_for` возвращается на ПЕРВОЙ отмене (замер: 0,000 с), поэтому работа ниже начинается
# ровно в момент отмены сессии — внутри дренажа остановки веба (`apps/web/api.py`,
# `_SHUTDOWN_DRAIN_CEILING_SECONDS` = 20,0 с, затем повторная отмена). Значения обычных движковых
# вызовов (15 / 30 / 60 / +15 с) здесь НЕ меняются: это потолки ТОЛЬКО уборочных вызовов, и они
# обязаны быть меньше дренажа — иначе сам дренаж обрывает уборку (именно так и терялся контейнер:
# потолок `destroy` 30 с > 20 с дренажа).
#   reap    2,0 с — пожатие уже убитого (SIGKILL) CLI-клиента: зависший child-watcher не должен
#                   подвешивать остановку узла.
#   cleanup 8,0 с — `rm -f` осиротевшего контейнера; здоровый dockerd делает это за доли секунды
#                   (окно замера T7.91), 8 с — запас на нагруженный демон.
# Худший случай одной отменённой уборки: 2,0 + 8,0 = 10,0 с < 20,0 с дренажа (≥10 с остаётся учёту
# исхода сессии); весь остановочный путь ≤ 10 + 20 = 30 с < TimeoutStopSec 90 с юнита.
# Разбор чисел и альтернативы: docs/STATUS.md T7.95.
_REAP_CEILING_SECONDS = 2.0
_CLEANUP_CEILING_SECONDS = 8.0

# Потолки двух движковых глаголов запуска сессии. ЗНАЧЕНИЯ НЕ ИЗМЕНЯНЫ (60 с — они такие с T2.3 и
# так же разобраны в T7.93); они только названы, чтобы тест мог опустить их как fixture knob и
# проверить уборку после истечения потолка за секунды, а не за минуту (так же, как тесты T7.92/T7.93
# опускают окна брокера). Продуктовые вызовы используют эти константы как есть.
_CREATE_CEILING_SECONDS = 60.0
_START_VERB_CEILING_SECONDS = 60.0

# ── T7.95a: уборка считается завершённой только когда движок ПОДТВЕРДИЛ отсутствие контейнера ──
# Замеры на docker 29 (.87, docs/STATUS.md T7.95a): `rm -f` с rc=0 НИЧЕГО не доказывает — он бывает и
# по контейнеру, которого никогда не было, и по ещё незавершённому `docker create` (убитый клиент не
# отменяет работу в dockerd), после чего контейнер «появляется» — по замерам до ~0,13 с. Отказ rc=1
# «removal … already in progress» означает, что dockerd удаляет САМ (завершает за 22–86 мс): ждать и
# перепроверять, а не поднимать тревогу. Значит после каждого `rm -f` идёт серия `inspect`, и отсутствие
# ПОДТВЕРЖДЁННО, если имя непрерывно отвечает «no such» всё окно; появившийся контейнер удаётся повторно
# ровно один раз. Все глаголы подтверждения живут ВНУТРИ прежнего `_CLEANUP_CEILING_SECONDS` (8 с),
# арифметика дренажа не меняется: reap 2 + cleanup 8 = 10 < 20 с.
_CONFIRM_POLL_INTERVAL_SECONDS = 0.1   # быстрее обоих измеренных окон (появление ≤0,13 с, in-progress ≤86 мс)
_CONFIRM_ABSENT_WINDOW_SECONDS = 1.5   # ≈10 худших измеренных задержек появления после «успешного» rm -f
_CONFIRM_MIN_CALL_SECONDS = 0.25       # не начинать подвызов дешевле: сам docker CLI замеряется ≤60 мс
_MAX_REMOVE_ATTEMPTS = 2               # одна уборка + одна повторная по контейнеру, появившемуся после неё


def _consume_detached_task(task: asyncio.Task[Any]) -> None:
    """A reap/cleanup task may deliberately outlive its cancelled caller (it is shielded): consume
    its result so asyncio does not report an unretrieved exception when the loop closes."""
    if not task.cancelled():
        task.exception()


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class SandboxHandle:
    session_id: str
    container_name: str
    base_dir: Path
    workspace_dir: Path  # host destination for collect_workspace()
    profile: SandboxProfile


class ContainerSandboxRuntime:
    """Wraps the container engine CLI; one container per session."""

    MAX_OUTPUT = 1_000_000

    def __init__(
        self,
        image: str,
        engine: str = "docker",
        work_root: Path | None = None,
    ) -> None:
        self.image = image
        self.engine = engine
        self.work_root = work_root if work_root is not None else SandboxSettings().work_root
        self.work_root.mkdir(parents=True, exist_ok=True)

    # ── pure command builders (unit-tested) ───────────────────────────────

    @staticmethod
    def create_command(
        name: str,
        image: str,
        profile: SandboxProfile,
        base_path: Path,
        workspace_path: Path,
    ) -> list[str]:
        cmd = [
            "create",
            "--name",
            name,
            "--network",
            profile.network,
            "--cap-drop",
            "ALL",
            "--memory",
            f"{profile.memory_mb}m",
            "--cpus",
            profile.cpu,
            "--pids-limit",
            str(profile.max_processes),
            # scratch tmpfs so the read-only rootfs is usable by the tools
            "--tmpfs",
            "/tmp:rw,size=64m,mode=1777",
            # per-session writable workspace (disposable host dir; rw is the
            # default, so no mode suffix — --mount only accepts key=value)
            "--mount",
            f"type=bind,src={workspace_path},dst={WORKSPACE_PATH}",
            # read-only base, copied into the workspace at init
            "--mount",
            f"type=bind,src={base_path},dst={BASE_PATH},ro",
        ]
        if profile.read_only_rootfs:
            cmd += ["--read-only"]
        if profile.no_new_privileges:
            cmd += ["--security-opt", "no-new-privileges"]
        cmd += [image, "bash", "-c", INIT_COMMAND]
        return cmd

    @staticmethod
    def exec_command(name: str, command: list[str], timeout_seconds: float) -> list[str]:
        # timeout(1) inside the container: a host-side kill would not stop
        # the in-container process.
        return ["exec", name, "timeout", "-k", "5", str(timeout_seconds), *command]

    @staticmethod
    def destroy_command(name: str) -> list[str]:
        return ["rm", "-f", name]

    # ── engine ────────────────────────────────────────────────────────────

    async def _run(self, args: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
        # T7.93: the ceiling covers the WHOLE engine call — spawning the CLI client and
        # waiting for it to finish. Before this, wait_for guarded only create_subprocess_exec
        # and communicate() was unbounded: a wedged daemon/network left the session hanging
        # forever (neither unknown nor transient). On expiry the CLI process is killed and
        # reaped (no orphan, no zombie) and callers get the SAME SandboxError
        # "engine command timed out: ..." as before — the broker classification built in
        # T7.91/T7.92 applies to it unchanged. Per-call timeout values are not touched here;
        # their per-call analysis is in docs/STATUS.md (T7.93).
        proc: asyncio.subprocess.Process | None = None

        async def _spawn_and_complete() -> tuple[int, bytes, bytes]:
            nonlocal proc
            proc = await asyncio.create_subprocess_exec(
                self.engine,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return (
                proc.returncode if proc.returncode is not None else -1,
                stdout,
                stderr,
            )

        try:
            rc, out, err = await asyncio.wait_for(_spawn_and_complete(), timeout=timeout)
        except TimeoutError as exc:
            await self._kill_and_reap(proc)
            raise SandboxError(f"engine command timed out: {' '.join(args[:2])}") from exc
        except asyncio.CancelledError:
            # T7.95(а): внешняя отмена НЕ убивает запущенный CLI и НЕ идёт веткой TimeoutError.
            # `wait_for` отменяет внутреннюю корутину (её `proc.communicate()` прерывается), возврат
            # происходит сразу, а docker/podman-клиент продолжает жить: замер на 6ee80c5 — PID клиента
            # через 1 с после отмены в состоянии S (спит), то есть сирота в таблице процессов.
            # Клиента убиваем и пожираем тем же `_kill_and_reap` (ограниченно), после чего отмена
            # идёт ДАЛЬШЕ без изменений: отмена — не отказ движка, превращать её в SandboxError
            # нельзя (классификация брокера T7.91/T7.92 к ней неприменима).
            await self._kill_and_reap(proc)
            raise
        except FileNotFoundError as exc:
            raise SandboxHealthError(f"container engine not found: {self.engine}") from exc
        return (
            rc,
            out.decode("utf-8", "replace")[: self.MAX_OUTPUT],
            err.decode("utf-8", "replace")[: self.MAX_OUTPUT],
        )

    @staticmethod
    async def _kill_and_reap(proc: asyncio.subprocess.Process | None) -> None:
        """A ceiling-expired or cancelled engine CLI must not survive the call.

        kill() stops a client that hangs in the daemon; awaiting wait() reaps it, so no
        zombie is left in our process table either. A client that exited at the very same
        moment raises ProcessLookupError — then only the reap matters.

        T7.95: the reap runs as its OWN task under `_REAP_CEILING_SECONDS` and is shielded from
        our caller's cancellation. Cancellation is exactly the case where somebody is already
        waiting for us to finish (the shutdown drain of `apps/web/api.py`), so a wedged child
        watcher must not turn a bounded drain into an unbounded wait. SIGKILL is delivered
        synchronously, BEFORE any await: giving up on the reap costs only the reap — asyncio's
        child watcher still reaps the killed child — and a repeated cancellation of our caller
        cannot abort the kill either. A *fresh* cancellation is not swallowed here: it travels on.
        """
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            # the client exited at the very same moment — only the reap matters
            proc.kill()
        reap = asyncio.create_task(proc.wait())
        reap.add_done_callback(_consume_detached_task)
        try:
            await asyncio.wait_for(asyncio.shield(reap), timeout=_REAP_CEILING_SECONDS)
        except TimeoutError:
            logger.warning(
                "engine CLI pid %s was killed but not reaped within %.1f s",
                proc.pid,
                _REAP_CEILING_SECONDS,
            )

    async def remove_container_best_effort(self, name: str, where: str) -> bool:
        """Remove the container whose name is already with the engine and CONFIRM its absence (T7.95a).

        Measured on docker 29 (docs/STATUS.md T7.95a): an `rm -f` that answers rc=0 proves NOTHING — it
        happens for a container that never existed and for one whose `docker create` dockerd has not
        finished, after which the container appears. So cleanup counts as done only when the series of
        `inspect` that follows every `rm -f` continuously sees "no such" through the whole confirmation
        window; an "already in progress" refusal means dockerd is removing on its own (wait and recheck),
        a container that appeared after a "successful" `rm -f` is removed exactly once more. Every engine
        verb of this sequence lives inside the same `_CLEANUP_CEILING_SECONDS` — the ceiling is what keeps
        the shutdown drain safe: T7.63 deliberately lets a shutdown abandon a wedged engine rather than
        hang on it, and a repeated cancellation only cuts OUR await (the sequence runs as its own shielded
        task and reaches its own ceiling regardless).

        Returns True only when the engine confirmed the absence. Otherwise exactly ONE warning names the
        problem — `LEAKED container …`, and it says which case it is: at the end of the ceiling the engine
        reports the CONTAINER PRESENT, or the absence remained UNCONFIRMED (that replaced T7.95's naming
        on an rc that dockerd never really answered; the manager's probes showed the old line was a false
        alarm on every mid-removal cancellation). The ONLY exception this method lets out stays a fresh
        asyncio.CancelledError — a cleanup must never swallow a cancellation, and it never replaces what
        the caller raises.
        """
        attempt = asyncio.create_task(self._remove_and_confirm(name, where))
        attempt.add_done_callback(_consume_detached_task)
        try:
            return await asyncio.shield(attempt)
        except asyncio.CancelledError:
            # повторная отмена срывает только НАШ await: detached-последовательность доживает до своего
            # потолка и напишет про контейнер ровно там, где истечёт потолок (или подтвердит отсутствие)
            logger.debug(
                "sandbox cleanup of %s (%s): the caller was cancelled again; the engine sequence runs to its ceiling",
                name,
                where,
            )
            raise

    async def _remove_and_confirm(self, name: str, where: str) -> bool:
        """The bounded remove → confirm → (re-remove if the container appeared) → verdict sequence.

        Runs as its own task under `_CLEANUP_CEILING_SECONDS` so it survives a repeated cancellation of
        our caller (see `remove_container_best_effort`). Blindly removing a name the engine has never
        confirmed as ours stays out of reach: this method is only called for names already handed over.
        """
        deadline = time.monotonic() + _CLEANUP_CEILING_SECONDS
        verdict, detail = "unknown", "the ceiling expired before the engine was asked"
        for attempt_no in range(1, _MAX_REMOVE_ATTEMPTS + 1):
            if deadline - time.monotonic() < _CONFIRM_MIN_CALL_SECONDS:
                break  # нет бюджета даже на один глагол — квалифицируем уже известное
            answered = False  # `rm -f` вернул rc (любой) — в отличие от вызова, до rc не дожившего
            removal_underway = False  # «already in progress»: dockerd удаляет сам, повторять ему нечем
            try:
                rc, _out, err = await self._run(
                    self.destroy_command(name),
                    timeout=max(
                        deadline - time.monotonic() - _CONFIRM_ABSENT_WINDOW_SECONDS,
                        _CONFIRM_MIN_CALL_SECONDS,
                    ),
                )
                answered = True
                if rc != 0 and "already in progress" in err.lower():
                    removal_underway = True
                elif rc != 0:
                    detail = err.strip()[:200] or "no reason given"
            except SandboxError as exc:  # наш собственный потолок (T7.93: CLI убит и пожат) или отказ движка
                answered = False
                detail = str(exc)

            may_remove_what_appeared = answered and not removal_underway and attempt_no < _MAX_REMOVE_ATTEMPTS
            verdict, confirm_detail = await self._confirm_absent(
                name, deadline, stop_on_present=may_remove_what_appeared
            )
            if verdict == "absent":
                return True  # движок подтвердил отсутствие полным окном — LEAKED нет (rc=0 одного rm -f мало)
            detail = confirm_detail or detail
            if verdict == "present" and may_remove_what_appeared:
                logger.debug(
                    "sandbox cleanup of %s: the container is present after `rm -f` answered — removing it again", name
                )
                continue
            break  # unknown, или presence без права повторного rm -f: дожидать потолок и назвать итог
        if verdict == "present":
            logger.warning(
                "LEAKED container %s: cleanup during %s ran out its %.1f s ceiling while the engine "
                "keeps reporting the container present",
                name,
                where,
                _CLEANUP_CEILING_SECONDS,
            )
        else:
            logger.warning(
                "LEAKED container %s: cleanup during %s ended with the absence UNCONFIRMED by the engine "
                "within its %.1f s ceiling: %s",
                name,
                where,
                _CLEANUP_CEILING_SECONDS,
                detail,
            )
        return False

    async def _confirm_absent(self, name: str, deadline: float, stop_on_present: bool) -> tuple[str, str]:
        """A short bounded `inspect` series after an `rm -f` (T7.95a), inside the same cleanup ceiling.

        Absence is CONFIRMED when consecutive answers keep saying "no such" for at least
        `_CONFIRM_ABSENT_WINDOW_SECONDS` without a gap: measured docker answers exactly that (`rc != 0`,
        stderr «error: no such object …»), presence answers `rc == 0`. An answer unrelated to the
        container being missing (daemon unreachable) restarts the window — it does not assert presence.
        "present" returns at once only when a re-removal decision must be made now; otherwise the series
        keeps observing until the ceiling, so that LEAKED describes the state AT THE CEILING'S EXPIRY:
        last conclusive answer present → "present"; no conclusive answer ever → "unknown".
        """
        absent_since: float | None = None
        last_error = ""
        last_state = "unknown"
        while True:
            remaining = deadline - time.monotonic()
            if remaining < _CONFIRM_MIN_CALL_SECONDS:
                gap = "" if last_state != "unknown" else "engine gave no conclusive answer within the ceiling"
                return (last_state, last_error or gap)
            try:
                rc, _out, err = await self._run(["inspect", name], timeout=remaining)
            except SandboxError as exc:
                last_error, absent_since = str(exc), None
                await asyncio.sleep(_CONFIRM_POLL_INTERVAL_SECONDS)
                continue
            if rc == 0:
                last_state = "present"
                absent_since = None
                if stop_on_present:
                    return ("present", "")
            elif "no such" in err.lower():
                now = time.monotonic()
                if absent_since is None:
                    absent_since = now
                elif now - absent_since >= _CONFIRM_ABSENT_WINDOW_SECONDS:
                    return ("absent", "")
            else:
                last_error, absent_since = err.strip()[:200] or "engine refused to answer", None
            await asyncio.sleep(_CONFIRM_POLL_INTERVAL_SECONDS)

    async def health_check(self) -> None:
        """Engine reachable, image present, a one-shot container runs."""
        rc, _out, err = await self._run(["image", "inspect", self.image])
        if rc != 0:
            raise SandboxImageMissingError(f"image {self.image} not found: {err[:200]}")
        name = f"noezema-sb-hc-{uuid.uuid4().hex[:8]}"
        cmd = [
            "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            self.image, "bash", "-c", "echo ok > /tmp/.health",
        ]
        rc, _out, err = await self._run(cmd, timeout=60)
        if rc != 0:
            raise SandboxHealthError(f"sandbox health check failed: {err[:300]}")

    async def start(
        self,
        session_id: str,
        base_workspace: Path | None,
        profile: SandboxProfile,
    ) -> SandboxHandle:
        sb_dir = self.work_root / session_id
        base_dir = sb_dir / "base"
        workspace_dir = sb_dir / "work"
        name = f"noezema-sb-{session_id.replace('-', '')[:12]}"

        # T7.95a(3): `<work_root>/<session_id>` создаётся до первого движкового глагола и обязан быть
        # удалён ЛЮБЫМ выходом из start без выданного хэндла — отменой, истечением потолка, отказом
        # движка (rc != 0) и хозяйской ошибкой. С T7.95 контейнер после create убирается, но каталог
        # оставался: исполнитель вызывает close_session только при живом хэндле, а start его не создавал;
        # проба менеджера после ~30 отменённых start насчитала 30 leftover-каталогов (docs/STATUS.md).
        # Исключение/отмена пробрасываются без изменений — уборка каталога их не заменяет.
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            if base_workspace is not None and base_workspace.exists():
                shutil.copytree(base_workspace, base_dir, dirs_exist_ok=True)

            # the in-container user (uid 10001) must be able to write the
            # workspace bind; chown when possible, else make it world-writable
            # (the dir is disposable and destroyed with the session)
            try:
                os.chown(workspace_dir, SANDBOX_UID, SANDBOX_GID)
            except PermissionError:
                os.chmod(workspace_dir, 0o777)

            cmd = self.create_command(name, self.image, profile, base_dir, workspace_dir)
            # T7.95(б/г): с этой строки имя контейнера УЖЕ выдано движку, а `SandboxHandle` ещё не выдан.
            # Именно хэндл делает контейнер доступным для `destroy`, а `close_session()` исполнителя при
            # `handle is None` просто ничего не убирает — значит любой выход отсюда до успешного `start`
            # (внешняя отмена сессии, истечение потолка вызова по T7.93) обязан закончиться ограниченной
            # по времени попыткой убрать контейнер с подтверждением отсутствия (T7.95a); исходное
            # исключение и отмена при этом пробрасываются без изменений. Оговорка прежняя: отказ самого
            # движка на `create` (`rc != 0`) уборки НЕ вызывает — там движок ОТВЕТИЛ, что контейнера нет
            # (замер: rc=1 «No such container» бывает и при незавершённом create, но именно этот путь —
            # единственный, где мы не знаем подтверждённого имени), а слепо удалять не подтверждённое нам
            # имя было бы единственным путём, способным зацепить чужой контейнер с тем же префиксом.
            # Разбор: docs/STATUS.md T7.95/T7.95a.
            try:
                rc, _out, err = await self._run(cmd, timeout=_CREATE_CEILING_SECONDS)
            except SandboxHealthError:
                raise  # движок не был запущен вовсе (нет бинаря): к движку не попало ничего
            except asyncio.CancelledError:
                await self.remove_container_best_effort(name, "the create call was cancelled")
                raise
            except SandboxError as exc:  # потолок вызова (T7.93): CLI убит, что успел dockerd — неизвестно
                await self.remove_container_best_effort(name, f"the create call ended early: {exc}")
                raise
            if rc != 0:
                raise SandboxError(f"failed to create sandbox container: {err[:300]}")
            try:
                rc, _out, err = await self._run(["start", name], timeout=_START_VERB_CEILING_SECONDS)
            except SandboxHealthError:
                raise  # движок уже ответил на `create`; ветка — та же симметрия, что и выше
            except asyncio.CancelledError:
                await self.remove_container_best_effort(name, "the start call was cancelled")
                raise
            except SandboxError as exc:
                await self.remove_container_best_effort(name, f"the start call ended early: {exc}")
                raise
            if rc != 0:
                # поведение прежнее (T2.3): контейнер, который движок отказывается запустить, убирается
                # здесь. С T7.95 — тем же ограниченным хэлпером: прежде этот `rm -f` имел собственный
                # вызов на 30 с, и его собственный SandboxError заменял собой «failed to start sandbox
                # container: …»; теперь уборка ограничена `_CLEANUP_CEILING_SECONDS` и исходную ошибку
                # заменить уже не может. С T7.95a она ещё и подтверждается движком.
                await self.remove_container_best_effort(name, "the engine refused to start the container")
                raise SandboxError(f"failed to start sandbox container: {err[:300]}")

            handle = SandboxHandle(
                session_id=session_id,
                container_name=name,
                base_dir=base_dir,
                workspace_dir=workspace_dir,
                profile=profile,
            )
        except BaseException:  # отмена, потолок, отказ движка, хозяйская ошибка — любой выход без хэндла
            try:
                shutil.rmtree(sb_dir)
            except OSError as exc:
                logger.warning("sandbox session dir %s not removed after a failed start: %s", sb_dir, exc)
            raise
        return handle

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        timeout = timeout if timeout is not None else float(handle.profile.timeout_seconds)
        rc, out, err = await self._run(
            self.exec_command(handle.container_name, command, timeout), timeout=timeout + 15.0
        )
        return ExecResult(
            exit_code=rc,
            stdout=out,
            stderr=err,
            timed_out=rc == 124,
        )

    async def is_running(self, handle: SandboxHandle) -> bool:
        """Whether the sandbox container is still alive."""
        rc, out, _err = await self._run(
            ["inspect", "-f", "{{.State.Running}}", handle.container_name], timeout=15.0
        )
        return rc == 0 and out.strip() == "true"

    async def collect_workspace(self, handle: SandboxHandle) -> Path:
        # the workspace is a host bind mount: it is already on the host
        return handle.workspace_dir

    async def destroy(self, handle: SandboxHandle) -> None:
        rc, _out, err = await self._run(self.destroy_command(handle.container_name), timeout=30.0)
        if rc != 0:
            raise SandboxError(f"failed to destroy sandbox: {err[:300]}")


def sandbox_available(engine: str = "docker") -> bool:
    """Best-effort check used by tests to skip when no engine is present."""
    if shutil.which(engine) is None:
        return False
    try:
        import subprocess

        probe = subprocess.run([engine, "info"], capture_output=True, timeout=10)
        return probe.returncode == 0
    except Exception:  # availability probe: any failure means "not available"
        return False
