"""T7.131 (часть 4а): `control-run.sh` ждёт сессию СВОЕГО вопроса и повторяет wake_now.

Скрипт запускается НАСТОЯЩИМ, но против ПОДСТАВНОГО API: на PATH ставятся `curl` и `sleep`
(тот же приём, что у `tests/unit/test_dev_stand_scripts.py`), времена ожидания уменьшены
переменными, которые скрипт читает только ради тестов. Ни одного обращения к стенду, ни одного
настоящего сценарного запуска — агент стенда не касается (AGENTS §6, T7.61(б)).

Что закреплено:
- (а) «вопрос ещё `candidate`, узел `idle`» -> повторный `wake_now`, не больше трёх, каждый в
  журнал строкой `WAKE`; после потолка — честный `TIMEOUT`, а не молчание;
- `DONE` печатается только когда у ЭТОГО вопроса появилась сессия в терминальном состоянии
  (нетерминальная сессия — не конец работы), в строке дописаны id сессии и её состояние,
  прежний префикс `ASKED`/`DONE`/`TIMEOUT` сохранён;
- сессия ПРЕЖНЕГО прогона того же вопроса (дедуп текста даёт тот же id) не считается ответом
  этого прогона: ложного `DONE` нет;
- узел на паузе -> `PAUSED`, штурм wake_now не устраивается;
- токен из env-файла не попадает ни в stdout, ни в stderr.

Красность на `929bdcc` доказана detached worktree (прежний скрипт ждал только признак узла
`node_state` и печатал `DONE`, как только узел возвращал `idle`, — то есть до конца своей сессии).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROL_RUN = REPO_ROOT / "deploy" / "dev-stand" / "control-run.sh"

FAKE_ADMIN_TOKEN = "faux-admin-token-must-never-be-printed"
QUESTION_ID = "7c0c0872-1111-4111-8111-111111111131"
SESSION_ID = "7c0c0872-2222-4222-8222-222222222131"
OLD_SESSION_ID = "aaaa1111-3333-4333-8333-333333333131"

#: `curl` читает сценарий из каталога STUB_API_DIR: `status` — состояния узла (строка за вызов,
#: последняя повторяется), `card` — карточки вопроса (JSON за вызов). Команды (questions,
#: commands) считаются в файлах-счётчиках.
_CURL_STUB = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

d = pathlib.Path(os.environ["STUB_API_DIR"])


def nxt(name):
    lines = (d / name).read_text(encoding="utf-8").splitlines()
    marker = d / (name + ".idx")
    idx = int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(idx + 1))
    return lines[idx] if idx < len(lines) else lines[-1]


args = " ".join(sys.argv[1:])
if "/api/v1/status" in args:
    print(json.dumps({"node_state": nxt("status")}))
elif "/answer" in args:
    print(nxt("card"))
elif "-X POST" in args and "/api/v1/commands" in args:
    with (d / "wakes").open("a", encoding="utf-8") as handle:
        handle.write("wake\\n")
    print('{"state":"completed"}')
elif "-X POST" in args and "/api/v1/questions" in args:
    print(json.dumps({"id": os.environ["STUB_QUESTION_ID"]}))
else:
    print("{}")
"""

_SLEEP_STUB = """#!/usr/bin/env bash
exit 0
"""


def _card(question_state: str, sessions: list[dict[str, Any]]) -> str:
    return json.dumps({"question": {"state": question_state}, "sessions": sessions})


@pytest.fixture()
def api(tmp_path: Path) -> dict[str, Any]:
    """Подставной API: каталог сценария + PATH со стабами `curl` и `sleep`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    for name, body in (("curl", _CURL_STUB), ("sleep", _SLEEP_STUB)):
        script = bin_dir / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    env_file = tmp_path / "dev.env"
    env_file.write_text(f"NOEZEMA_ADMIN_TOKEN={FAKE_ADMIN_TOKEN}\n", encoding="utf-8")
    control_set = tmp_path / "control-set.jsonl"
    control_set.write_text(
        json.dumps({"id": "c1", "text": "Какая инфляция в Тармении?"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"bin": bin_dir, "api": api_dir, "env_file": env_file, "set": control_set, "tmp": tmp_path}


def _run(
    api: dict[str, Any],
    *,
    status: list[str],
    card: list[str],
    poll: int = 1,
    retry_after: int = 3,
    wake_retries: int = 3,
    max_wait: int = 40,
) -> subprocess.CompletedProcess[str]:
    (api["api"] / "status").write_text("\n".join(status) + "\n", encoding="utf-8")
    (api["api"] / "card").write_text("\n".join(card) + "\n", encoding="utf-8")
    for marker in (api["api"] / "status.idx", api["api"] / "card.idx", api["api"] / "wakes"):
        if marker.exists():
            marker.unlink()
    env = {
        "PATH": f"{api['bin']}:/usr/bin:/bin",
        "HOME": str(api["tmp"]),
        # стенд работает в UTF-8 (локаль юнита); без явной локали bash режет `${qtext:0:60}`
        # по байтам и печатает половину кириллического знака — вывод должен быть валидным и там
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NOEZEMA_DEV_ENV_FILE": str(api["env_file"]),
        "CONTROL_RUN_SET": str(api["set"]),
        "STUB_API_DIR": str(api["api"]),
        "STUB_QUESTION_ID": QUESTION_ID,
        "CONTROL_RUN_PRE_POLL_SECONDS": "1",
        "CONTROL_RUN_PRE_IDLE_LIMIT_SECONDS": "5",
        "CONTROL_RUN_POLL_SECONDS": str(poll),
        "CONTROL_RUN_CANDIDATE_RETRY_SECONDS": str(retry_after),
        "CONTROL_RUN_WAKE_RETRIES": str(wake_retries),
        "CONTROL_RUN_MAX_WAIT_SECONDS": str(max_wait),
    }
    return subprocess.run(
        ["bash", str(CONTROL_RUN), "t7131"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _wakes(api: dict[str, Any]) -> int:
    wakes = api["api"] / "wakes"
    return len(wakes.read_text(encoding="utf-8").splitlines()) if wakes.exists() else 0


def _journal(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def _tagged(journal: str, tag: str) -> list[str]:
    """Строки журнала с этим тегом (CONTROL_DONE не путается с DONE — префикс со пробелом)."""
    return [line for line in journal.splitlines() if line.startswith(f"{tag} ")]


# ─── (а) повторный wake_now, пока вопрос стоит в очереди ────────────────────────────────


def test_repeated_wake_while_question_stays_candidate(api: dict[str, Any]) -> None:
    # Узел всегда idle, карточка всегда «candidate»: ровно тот стенд-случай c5 (3a49586…),
    # где wake был принят, а сессия так и не началась.
    result = _run(
        api,
        status=["idle"],
        card=[_card("candidate", [])],
        poll=1,
        retry_after=3,
        wake_retries=3,
        max_wait=20,
    )
    journal = _journal(result)
    lines = _tagged(journal, "WAKE")
    assert len(lines) == 3, journal
    assert [line.split("повторный wake_now ")[1] for line in lines] == ["2/4", "3/4", "4/4"], journal
    assert all("node=idle вопрос ещё candidate" in line for line in lines), journal
    # 1 первоначальный wake + 3 повтора, не больше.
    assert _wakes(api) == 4, journal
    assert _tagged(journal, "DONE") == [], journal
    timeout = _tagged(journal, "TIMEOUT")[0]
    assert "сессия этого вопроса не завершилась" in timeout and "вопрос=candidate" in timeout, journal
    assert "wake_now вызовов=4" in timeout, journal


# ─── DONE только после терминальной сессии своего вопроса ───────────────────────────────


def test_done_waits_for_terminal_session_of_this_question(api: dict[str, Any]) -> None:
    pending = {"id": SESSION_ID, "state": "searching", "started_at": "2999-01-01T00:00:00+00:00"}
    done = {
        "id": SESSION_ID,
        "state": "succeeded",
        "termination_reason": "goal_reached",
        "started_at": "2999-01-01T00:00:00+00:00",
    }
    result = _run(
        api,
        status=["session_running", "idle"],
        card=[
            _card("candidate", []),
            _card("researching", []),
            _card("researching", [pending]),  # сессия есть, но не закончена — ждать дальше
            _card("verified", [done]),
        ],
        poll=1,
        retry_after=10,
        wake_retries=3,
        max_wait=40,
    )
    journal = _journal(result)
    assert re.search(rf"^ASKED c1 {QUESTION_ID} \d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ :: ", journal, re.M), journal
    finished = _tagged(journal, "DONE")
    assert len(finished) == 1, journal
    # прежняя форма строки сохранена (`DONE  <qid> <id> <время> node=…`), дописаны сессия и её состояние
    assert re.match(
        rf"^DONE  c1 {QUESTION_ID} \d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ node=\S+ session={SESSION_ID} "
        r"state=succeeded$",
        finished[0],
    ), journal
    assert _tagged(journal, "TIMEOUT") == [], journal
    # узел успел уйти в idle раньше конца сессии — прежний скрипт на этом месте напечатал бы DONE
    assert _wakes(api) == 1, journal


def test_session_of_previous_run_does_not_finish_this_run(api: dict[str, Any]) -> None:
    # Дедуп текста (T7.59) даёт повтору прогона тот же id вопроса; его терминальная сессия
    # прошлых выходных — не ответ этого прогона, и `DONE` здесь был бы ложью.
    result = _run(
        api,
        status=["idle"],
        card=[_card("verified", [{"id": OLD_SESSION_ID, "state": "succeeded",
                                 "started_at": "2000-01-01T00:00:00+00:00"}])],
        poll=1,
        retry_after=10,
        wake_retries=1,
        max_wait=10,
    )
    journal = _journal(result)
    assert _tagged(journal, "DONE") == [], journal
    assert OLD_SESSION_ID not in journal, journal
    assert _tagged(journal, "TIMEOUT") and "вопрос=verified" in journal, journal


def test_paused_node_is_reported_without_wake_storm(api: dict[str, Any]) -> None:
    result = _run(
        api,
        status=["idle", "idle", "paused"],
        card=[_card("candidate", [])],
        poll=1,
        retry_after=2,
        wake_retries=3,
        max_wait=40,
    )
    journal = _journal(result)
    paused = _tagged(journal, "PAUSED")
    assert len(paused) == 1 and "node=paused" in paused[0] and "resume-runtime" in paused[0], journal
    assert _tagged(journal, "DONE") == [] and _tagged(journal, "WAKE") == [], journal
    assert _wakes(api) == 1, journal


# ─── секреты ───────────────────────────────────────────────────────────────────────────


def test_admin_token_never_printed(api: dict[str, Any]) -> None:
    result = _run(api, status=["idle"], card=[_card("candidate", [])], poll=1, max_wait=5)
    assert FAKE_ADMIN_TOKEN not in _journal(result)
    assert FAKE_ADMIN_TOKEN not in (result.stdout + result.stderr)
