"""T7.90: фикстура `fake_llm` не может подключиться к чужому серверу.

Исторический флейк: `_free_port()` освобождает порт между bind(0) и bind'ом
подпроцесса (окно ~0,3 с), под xdist два воркера могли получить один номер,
и healthcheck проигравшего видел 200 ЧУЖОГО сервера, пока свой процесс ещё
импортировал uvicorn. Тест тогда скриптовал сценарий в чужую очередь и получал
чужие ответы (сигнатура: `relied ids not resolved host-side: []`).

Фикстура теперь узнаёт СВОЙ сервер по уникальному токену (`--token` →
GET /_noezema/whoami) и при коллизии повторяет запуск на свежем порту.
Эти тесты доказывают: чужой сервер на занятом порту обнаруживается и НИКОГДА
не используется; на нём не остаётся ни нашего script(), ни нашего запроса.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest

import tests.conftest as conftest
from tests.conftest import FakeLLM, _probe_fake_llm, _spawn_fake_llm, _start_fake_llm, _terminate_proc

pytestmark = pytest.mark.unit

STRANGER_TOKEN = "stranger-token-not-mine"


def _bind_free_port() -> int:
    """Свободный порт НАПРЯМУЮ (не через `conftest._free_port`): тесты должны
    оставаться детерминированными, даже если кто-то подменил `_free_port`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pop(handle: FakeLLM) -> str:
    r = httpx.post(
        f"{handle.base_url}/chat/completions",
        json={"model": "m", "messages": []},
        timeout=5.0,
    )
    r.raise_for_status()
    return str(r.json()["choices"][0]["message"]["content"])


@pytest.fixture()
def stranger_server() -> Iterator[tuple[int, str]]:
    """Чужой fake-сервер: свой порт, свой токен, свой сценарий в очереди."""
    port = _bind_free_port()
    root = f"http://127.0.0.1:{port}"
    proc = _spawn_fake_llm(port, STRANGER_TOKEN)
    try:
        deadline = time.monotonic() + 20.0
        while True:
            try:
                if httpx.get(f"{root}/_noezema/whoami", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("stranger fake server did not start")
            time.sleep(0.05)
        httpx.post(f"{root}/_noezema/scenario", json={"responses": [{"message": "stranger-only"}]}, timeout=5.0)
        yield port, root
    finally:
        _terminate_proc(proc)


def test_probe_distinguishes_our_server_from_a_stranger(stranger_server: tuple[int, str]) -> None:
    """Проверка готовности различает СВОЙ и чужой сервер на одном порту.

    Ровно то, чего не хватало в старом healthcheck: 200 от /v1/models был
    «готовностью» для любого фейка, и проигравший коллизию воркер подключался
    к победителю. Здесь «наш процесс» жив (спит), но на порту отвечает чужой —
    проба обязана вернуть 'stranger', а не 'ready'.
    """
    _, root = stranger_server
    alive_dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _probe_fake_llm(alive_dummy, root, "our-own-token") == "stranger"
        # с правильным токеном тот же сервер — уже 'ready' (проверка не сломана)
        assert _probe_fake_llm(alive_dummy, root, STRANGER_TOKEN) == "ready"
    finally:
        alive_dummy.terminate()
        alive_dummy.wait(timeout=5)


def test_start_refuses_stranger_on_the_chosen_port(
    stranger_server: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Коллизия портов воспроизведена детерминированно: первый выбор порта
    указывает на порт чужого сервера — как при настоящем TOCTOU. Фикстура
    обязана ОТКАЗАТЬСЯ от чужого сервера, взять свежий порт и работать со своим.

    На старом коде этот сценарий давал подключение к чужому серверу (или
    «fake LLM server exited during startup»): красное → зелёное.
    """
    port, root = stranger_server
    picks = [port]  # первая попытка — коллизия, дальше — настоящие свободные порты

    def _patched_free_port() -> int:
        if picks:
            return picks.pop(0)
        return _bind_free_port()

    monkeypatch.setattr(conftest, "_free_port", _patched_free_port)

    handle, proc = _start_fake_llm()
    try:
        assert handle.root != root, "фикстура подключилась к чужому серверу"
        served = httpx.get(f"{handle.root}/_noezema/whoami", timeout=5.0).json()
        assert served["token"] != STRANGER_TOKEN

        # тест говорит со СВОИМ сервером: свой сценарий доходит до своего же ответа
        handle.script([{"message": "mine"}])
        assert _pop(handle) == "mine"

        # чужой сервер не получил НИ нашего script(), НИ нашего запроса
        state = httpx.get(f"{root}/_noezema/state", timeout=5.0).json()
        assert state["queue_left"] == 1, "наш script() ушёл в чужую очередь"
        assert state["request_count"] == 0, "наш запрос ушёл в чужой сервер"
    finally:
        handle.close()
        _terminate_proc(proc)


def test_two_servers_are_isolated() -> None:
    """Два сервера, поднятые фикстурой, не видят сценарии друг друга."""
    first, first_proc = _start_fake_llm()
    second, second_proc = _start_fake_llm()
    try:
        assert first.root != second.root
        first.script([{"message": "one"}])
        second.script([{"message": "two"}])
        assert _pop(first) == "one"
        assert _pop(second) == "two"
        assert first.state()["queue_left"] == 0
        assert second.state()["queue_left"] == 0
    finally:
        first.close()
        second.close()
        _terminate_proc(first_proc)
        _terminate_proc(second_proc)
