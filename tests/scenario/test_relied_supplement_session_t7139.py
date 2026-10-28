"""T7.139 (тест г, ADR-0038 §16): частичное снятие перепроверки — опора и свежий прогноз на карточке.

Стенд 008d93ac (run 256b16b-v30, вопрос c3): куратор предложил четыре перепроверки, хост
снял три из них гейтами формулировки (ADR-0034 вариант B — сняты ОПЕРАЦИИ, раунда исправления
при частичном отказе нет). Записанные утверждения под `existing_claim_id` остались в знании,
но ответ перестал на них ссылаться: карточка потеряла Альфа-Банк, ВТБ и июльский прогноз
Банка России, а свежий октябрьский прогноз той же серии (as_of 2026-10-23) в ответе не
появился вовсе.

Сценарий воспроизводит форму дефекта на вымышленных числах:

* сессию-посев двух прогнозов Центрального банка (июльский 6–7% и октябрьский 7,1–7,6%);
* вторую сессию, где куратор перепроверяет июльский прогноз, но единственный его
  `supports`-связь ведёт на страницу без значения утверждения — гейт значения T7.108 снимает
  операцию (`claim[0]: no linked evidence contains the claim value`), остальное предложение
  применяется, раунда исправления нет;
* после этого payload `claim_created` несёт `relied_from_dropped_operations` (id снятой
  перепроверки — «использовано без перепроверки»), а `relied_fresher_forecast` добавляет в
  опору свежий прогноз той же серии с указанием замещаемой записи;
* карточка `/api/v1/questions/{id}/answer` показывает блок опоры с витринными метками:
  июльский прогноз — `outdated`, октябрьский — `actual`;
* сессия БЕЗ снятых перепроверок и устаревших серий — payload не содержит НИ ОДНОГО нового
  ключа (байтовая совместимость прежних сессий).

HTTP-слой research proxy подменён (внешних запросов нет), гейты записи формулировок выключены
(`_payload_plain`): снимает операцию только безусловный гейт значения — причина ровно одна.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_curator_link_gates_replay_t7123 import (
    COMPANION_CLAIM,
    EXTRA_PAGES,
    PAGES,
    URL_STATGRAD,
    FakeEngineClient,
    _answer,
    _claim,
    _claim_created_payloads,
    _complete,
    _fetch_page,
    _fixation_count,
    _links,
    _payload_plain,
    _run,
)
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _all
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = pytest.mark.scenario

# ── вымышленные страницы двух прогнозов одной серии (Центральный банк, 2026 год) ──

URL_CBR_OLD = "https://cbrnews.example/iiulskiy-vypusk"
URL_CBR_FRESH = "https://cbrnews.example/oktyabrskiy-vypusk"
URL_METRON = "https://metron.example/indeks-nastroeniy"

CBR_OLD_CLAIM = "Банк России повысил прогноз инфляции в России на 2026 год до 6–7%."
CBR_FRESH_CLAIM = "Банк России повысил прогноз инфляции в России на 2026 год до 7,1–7,6%."

CANDIDATE_PAGES: dict[str, list[str]] = {
    URL_CBR_OLD: [
        "Банк России в июле 2026 года повысил прогноз инфляции в России на 2026 год до 6–7%.",
        "Нижняя граница прогноза составляет 6%, верхняя граница — 7%.",
    ],
    URL_CBR_FRESH: [
        "Банк России 23 октября 2026 года повысил прогноз инфляции в России на 2026 год до 7,1–7,6%.",
        "Нижняя граница прогноза составляет 7,1%, верхняя граница — 7,6%.",
    ],
    # страница без значения утверждения: индекс настроений, ни 6, ни 7 токенов рядом нет —
    # ровно тот же механизм снятия T7.108, что на стенде 26b0b309 (перепроверка, снятая значением)
    URL_METRON: ["Портал «Метрон»: индекс потребительских настроений в сентябре 2026 года составил 92,4 пункта."],
}


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)
    for url, lines in EXTRA_PAGES.items():
        monkeypatch.setitem(PAGES, url, lines)
    for url, lines in CANDIDATE_PAGES.items():
        monkeypatch.setitem(PAGES, url, lines)


SEED_QUESTION = (
    "Какой прогноз инфляции в России на 2026 год у Центрального банка: "
    f"приведи значения и первоисточник. Источники: {URL_CBR_OLD} {URL_CBR_FRESH}"
)
SESSION_QUESTION = (
    "Какой прогноз инфляции в России на 2026 год у Центрального банка и какой индекс деловой "
    f"активности даёт «Статград»: приведи значения. Источники: {URL_METRON} {URL_STATGRAD}"
)


async def _seed_forecasts(scratch_url: str, fake_llm: FakeLLM, tmp_path: Path) -> dict[str, str]:
    """Посев: две записанные прогнозные записи одной серии с якорями as_of (посевная сессия
    пишет их обычным стейджингом — прямого INSERT нет)."""
    script = [
        _fetch_page(URL_CBR_OLD),
        _fetch_page(URL_CBR_FRESH),
        _complete("Прочитал оба выпуска: «до 6–7%» в июльском и «до 7,1–7,6%» в октябрьском."),
        _answer(
            [
                {**_claim(CBR_OLD_CLAIM), "as_of": "2026-07-24T00:00:00+00:00"},
                {**_claim(CBR_FRESH_CLAIM), "as_of": "2026-10-23T00:00:00+00:00"},
            ],
            _links((0, 0), (1, 1)),
            summary="Два выпуска прогноза.",
        ),
    ]
    await _run(scratch_url, fake_llm, tmp_path, SEED_QUESTION, script, "seed7139")
    rows = await _all(
        scratch_url,
        "SELECT id, statement FROM claims ORDER BY statement",
        {},
    )
    by_statement = {str(row["statement"]): str(row["id"]) for row in rows}
    assert by_statement == {CBR_OLD_CLAIM: by_statement[CBR_OLD_CLAIM], CBR_FRESH_CLAIM: by_statement[CBR_FRESH_CLAIM]}
    return by_statement


@pytest.mark.asyncio
async def test_dropped_reverify_keeps_reliance_and_fresh_forecast_reaches_the_card(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    seeded = await _seed_forecasts(scratch_url, fake_llm, tmp_path)
    old_id, fresh_id = seeded[CBR_OLD_CLAIM], seeded[CBR_FRESH_CLAIM]

    # вопрос называет прогнозы лексикой записанных утверждений — оба попадают в claims_evidence пака
    script = [
        _fetch_page(URL_METRON),  # улика перепроверки: значения 6–7 на странице нет
        _fetch_page(URL_STATGRAD),
        _complete("Прочитал индекс: «101,7 пункта»; про прогноз — страницу без значения."),
        _answer(
            [
                {**_claim(CBR_OLD_CLAIM), "existing_claim_id": old_id},
                _claim(COMPANION_CLAIM),
            ],
            _links((0, 0), (1, 1)),
            summary="Прогноз и индекс.",
        ),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, SESSION_QUESTION, script, "main7139")

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    claim_events = [p for p in payloads if "claims" in p]
    assert len(claim_events) == 1
    payload = claim_events[0]

    # (г-1) частичное снятие: операция перепроверки названа с причиной гейта значения,
    # раунда исправления при частичном отказе нет (вариант C трогает только полный отказ)
    reasons = [entry["reason"] for entry in payload["rejected_operations"]]
    assert reasons == ["claim[0]: no linked evidence contains the claim value"]
    assert payload["rejected_operations"][0]["operation"]["existing_claim_id"] == old_id
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []

    # (г-2) часть 2: id снятой перепроверки перенесён в опору и назван аддитивным ключом
    assert payload["relied_from_dropped_operations"] == [old_id]

    # (г-3) часть 3: свежий прогноз той же серии добавлен в опору с указанием замещения
    fresh_entry = payload["relied_fresher_forecast"]
    assert len(fresh_entry) == 1
    assert fresh_entry[0]["claim_id"] == fresh_id
    assert fresh_entry[0]["series"].startswith("cbr:")
    assert fresh_entry[0]["replaces"] == [old_id]

    # общий список опоры события: перенесённый id + добавленный свежий прогноз
    assert payload["relied_claim_ids"] == [old_id, fresh_id]

    # (г-4) карточка вопроса: блок опоры показан витриной — июльский устарел, октябрьский актуален
    app, app_engine = await _make(scratch_url, tmp_path / "host7139", tmp_path / "unit7139.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{session.question_id}/answer")
            assert response.status_code == 200
            card = dict(response.json())
    finally:
        await app_engine.dispose()
    assert card["result"]["kind"] == "answered"
    assert [str(item["statement"]) for item in card["claims"]] == [COMPANION_CLAIM]
    relied = {str(item["id"]): item for item in card["relied_claims"]}
    assert set(relied) == {old_id, fresh_id}
    assert relied[old_id]["forecast_series"] == "outdated"
    assert relied[fresh_id]["forecast_series"] == "actual"

    # знание не изменилось ничем, кроме записи честной операции: перепроверка снята,
    # прежняя формулировка июльского прогноза осталась
    statements = sorted(str(row["statement"]) for row in await _all(scratch_url, "SELECT statement FROM claims", {}))
    assert statements == sorted([CBR_OLD_CLAIM, CBR_FRESH_CLAIM, COMPANION_CLAIM])


@pytest.mark.asyncio
async def test_session_without_dropped_reverifies_and_stale_series_keeps_payload_byte_identical(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """(д) иммутабельность: нет снятых перепроверок и устаревших серий на карточке —
    payload `claim_created` не содержит НИ ОДНОГО нового ключа T7.139."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    await _seed_forecasts(scratch_url, fake_llm, tmp_path)

    script = [
        _fetch_page(URL_STATGRAD),
        _complete("Прочитал индекс: «101,7 пункта»."),
        _answer([_claim(COMPANION_CLAIM)], _links((0, 0)), summary="Только индекс."),
    ]
    question = (
        "Какой индекс деловой активности даёт «Статград» в марте 2026 года: "
        f"приведи цифру и первоисточник. Источники: {URL_STATGRAD}"
    )
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "quiet7139")

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    claim_events = [p for p in payloads if "claims" in p]
    assert len(claim_events) == 1
    payload = claim_events[0]
    assert "rejected_operations" not in payload
    for key in ("relied_from_dropped_operations", "relied_fresher_forecast", "relied_claim_ids"):
        assert key not in payload
