"""T7.123 (часть 3, ADR-0038 Дополнение): сводка объявляет новое утверждение при пустых claims.

Репроигрывание стенда b7e93ff6 (аудит-фикстура audit-b7e93ff6-c3-curator-empty-claims.jsonl):
куратор вернул `claims: []` и `relied_claim_ids: []`, но в сводке объявил намерение записать
новое утверждение («предлагается создать новый claim …», «мы фиксируем новое состояние»).
Хост отклоняет такое предложение ЦЕЛИКОМ с именованной причиной и включает РОВНО ОДИН раунд
исправления (ADR-0034 вариант C, T7.106); второго отказа нет: принятое после раунда пустое
предложение догоняет опору из `[c:…]` финального ответа исследователя (запасной путь T7.115).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.curator_declared_claims import DECLARED_EMPTY_REASON
from tests.conftest import FakeLLM
from tests.scenario.test_curator_link_gates_replay_t7123 import (
    EXTRA_PAGES,
    PAGES,
    FakeEngineClient,
    _answer,
    _claim_created_payloads,
    _complete,
    _correction_requested_payload,
    _curator_calls,
    _fetch_page,
    _fixation_count,
    _fixation_requests,
    _payload,
    _payload_plain,
    _run,
)
from tests.scenario.test_dateless_curator_reverify_order import (
    _PAGES as DATELESS_PAGES,
)
from tests.scenario.test_dateless_curator_reverify_order import (
    _rejection_events,
    _seed_candidates,
)
from tests.scenario.test_online_activation import _run_online

pytestmark = pytest.mark.scenario

URL_CBR_FC = "https://cbrview.example/dkp-forecast-2026"
FC_PAGE = ["Аналитики ВТБ повысили прогноз инфляции на 2026 год до 7–7,5% (октябрь 2026 года)."]


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же HTTP-слой, что у T7.121/T7.123 (страницы — абзацы `<p>`): страницы репроигрывания
    T7.123 + посевные страницы T7.84 + страница прогноза этого сценария."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)
    for url, lines in EXTRA_PAGES.items():
        monkeypatch.setitem(PAGES, url, lines)
    for url, body in DATELESS_PAGES.items():
        monkeypatch.setitem(PAGES, url, [body])
    monkeypatch.setitem(PAGES, URL_CBR_FC, FC_PAGE)


STAND_K3_SUMMARY = (
    "Существующий claim противоречит новым данным, поэтому предлагается создать новый claim "
    "с актуальным значением и датой прогноза. Однако, согласно правилам, если факт изменился, "
    "мы фиксируем новое состояние."
)


@pytest.mark.asyncio
async def test_declared_empty_claims_get_exactly_one_round_then_relied_from_rationale(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    assert (await _run_online(engine, _payload())).state == "active"
    curator_calls_before = len(_curator_calls(fake_llm))

    question = (
        "Какие прогнозы инфляции в России на 2026 год дают аналитики ВТБ и какова официальная "
        f"годовая инфляция за 2025 год по данным Росстата: приведи значения. Источники: {URL_CBR_FC}"
    )
    empty_answer = _answer([], [], summary=STAND_K3_SUMMARY)
    fixed_empty = _answer(
        [], [], summary="Новых утверждений нет; итог опирается на уже записанные утверждения."
    )
    script = [
        _fetch_page(URL_CBR_FC),
        _complete(
            "Новый прогноз ВТБ (7–7,5%) противоречит записанной оценке 5,59% "
            f"[c:{ids['dup']}]; официальная оценка подтверждена [c:{ids['official']}]."
        ),
        empty_answer,
        fixed_empty,
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k3")

    # РОВНО ОДИН раунд: именованный отказ объявленного намерения при пустом списке claims
    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [DECLARED_EMPTY_REASON]
    assert len(_fixation_requests(fake_llm)) == 1
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2  # третьего вызова нет
    assert fake_llm.state()["queue_left"] == 0
    # второго финального отказа нет: принятое пустое предложение догоняет опору из rationale
    assert await _rejection_events(scratch_url, session.session_id) == []

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["claims"] == []
    assert payload["correction_round"]["reasons"] == [DECLARED_EMPTY_REASON]
    relied = [str(x) for x in payload["relied_claim_ids"]]
    relied_explorer = [str(x) for x in payload["relied_from_explorer"]]
    assert str(ids["dup"]) in relied and str(ids["official"]) in relied
    assert relied_explorer == relied  # куратор своих id не назвал — опора целиком из ответа
