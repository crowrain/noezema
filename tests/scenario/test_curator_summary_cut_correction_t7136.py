"""T7.136 (части 2, 3 и 5, ADR-0038 дополнение): оборванная на потолке сводка куратора.

Стенд 33298707 (контрольный прогон `aaca104-v30`, вопрос c3 «прогнозы инфляции на 2026 год от
аналитиков крупных банков»): рассуждение фазы consolidation ВЫКЛЮЧЕНО в конфигурации стенда
(config-v18…v30, ADR-0030), и куратор повёл рассуждение прямо в поле `summary`, уперся в
`max_length` этого поля, оборвал текст на «…Нельзя за» и вернул `claims: []`,
`relied_claim_ids: []`, `evidence_links: []`. JSON валиден, `finish_reason = stop` — хост
принял это как честный итог правила 16, раунда исправления не было, карточка вопроса сказала
«Ответ не записан», а новая оценка Банка России (7,1–7,6% от 23.10.2026) потерялась.

Что закреплено тестами (модель — заглушка `FakeLLM`, HTTP-слой research proxy подменён,
внешних запросов нет; сводки — дословные фикстуры `tests/fixtures/t7136/`):

* (а/е) оборванная сводка при пустых списках -> РОВНО ОДИН раунд исправления с новой причиной и
  новым `curator_reject_kind`; исправленный ответ записывает утверждение, карточка вопроса —
  не «Ответ не записан»;
* (д) второй вызов этим основанием не проверяется: тот же оборванный пустой ответ после раунда
  принимается как пустой, третьего вызова нет;
* (ж) пустой ответ раунда исправления не теряет опору ОТКЛОНЁННОГО первого предложения
  (стенды 2d682ab0, abef06b7): переносится только id, видимый в контекст-паке этой сессии,
  невидимый id не переносится и назван отказом.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.curator_declared_claims import SUMMARY_CUT_REASON
from tests.conftest import FakeLLM
from tests.scenario.test_curator_link_gates_replay_t7123 import (
    EXTRA_PAGES,
    PAGES,
    FakeEngineClient,
    _answer,
    _claim,
    _claim_created_payloads,
    _complete,
    _correction_requested_payload,
    _curator_calls,
    _fetch_page,
    _fixation_count,
    _fixation_requests,
    _links,
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
from tests.scenario.test_scope_coverage import _all
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = pytest.mark.scenario

REPO_ROOT = Path(__file__).resolve().parents[2]

#: дословная сводка куратора стенда 33298707 (вопрос c3): 2000 знаков, обрыв на «Нельзя за»
C3_TRUNCATED_SUMMARY = (
    REPO_ROOT / "tests/fixtures/t7136/summary-33298707-c3-cut-at-limit.txt"
).read_text(encoding="utf-8")

#: страница стенда (вымышленный адрес, правдоподобный текст): новая оценка Банка России
URL_CBR_NEWS = "https://cbrnews.example/prognoz-inflyacii-2026-10-23"
CB_FORECAST_CLAIM = (
    "Банк России 23 октября 2026 года повысил прогноз инфляции на 2026 год до 7,1–7,6%."
)
CB_PAGE = [
    "Банк России 23 октября 2026 года повысил прогноз инфляции на 2026 год до 7,1–7,6%.",
    "Нижняя граница прогноза составляет 7,1%, верхняя граница — 7,6%.",
]

C3_QUESTION = (
    "Какие прогнозы инфляции на 2026 год дают аналитики крупных банков и Банк России: "
    f"приведи значения. Источники: {URL_CBR_NEWS}"
)
C3_RATIONALE = (
    "Банк России 23 октября 2026 года повысил прогноз инфляции на 2026 год до 7,1–7,6%; "
    "ссылок на уже записанные утверждения в этом ответе нет."
)


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же HTTP-слой, что у тестов T7.121/T7.123 (страницы — абзацы `<p>`)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)
    for url, lines in EXTRA_PAGES.items():
        monkeypatch.setitem(PAGES, url, lines)
    for url, body in DATELESS_PAGES.items():
        monkeypatch.setitem(PAGES, url, [body])
    monkeypatch.setitem(PAGES, URL_CBR_NEWS, CB_PAGE)


def _empty_answer(summary: str) -> dict[str, Any]:
    """Предложение куратора без операций, без опоры и без вопросов (ровно как на стенде)."""
    return _answer([], [], summary=summary)


async def _card_result(scratch_url: str, tmp_path: Path, question_id: Any) -> dict[str, Any]:
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{question_id}/answer")
            assert response.status_code == 200
            return dict(response.json()["result"])
    finally:
        await app_engine.dispose()


# ── (а) + (е): стенд c3 — один раунд, исправленный прогноз записан ─────────────


@pytest.mark.asyncio
async def test_stand_c3_truncated_summary_gets_one_round_and_the_forecast_is_recorded(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    curator_calls_before = len(_curator_calls(fake_llm))

    script = [
        _fetch_page(URL_CBR_NEWS),
        _complete(C3_RATIONALE),
        _empty_answer(C3_TRUNCATED_SUMMARY),  # дословный первый ответ стенда
        _answer(  # раунд исправления: та же улика, но названное утверждение
            [_claim(CB_FORECAST_CLAIM)],
            _links((0, 0)),
            summary="Банк России повысил прогноз инфляции на 2026 год до 7,1–7,6%.",
        ),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, C3_QUESTION, script, "c3")
    assert session.final_state.value == "succeeded", session.termination_reason

    # РОВНО ОДИН раунд и новая именованная причина (с числом знаков из схемы)
    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [SUMMARY_CUT_REASON]
    assert len(_fixation_requests(fake_llm)) == 1
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2  # третьего вызова нет
    assert fake_llm.state()["queue_left"] == 0
    assert await _rejection_events(scratch_url, session.session_id) == []

    # исправленное предложение принято: утверждение записано, аддитивный ключ раунда на месте
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert [claim["statement"] for claim in payload["claims"]] == [CB_FORECAST_CLAIM]
    assert payload["correction_round"]["reasons"] == [SUMMARY_CUT_REASON]
    statements = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in statements] == [CB_FORECAST_CLAIM]

    # карточка вопроса больше не «Ответ не записан»
    result = await _card_result(scratch_url, tmp_path, session.question_id)
    assert result["kind"] != "no_answer", result


# ── (д): второй вызов тем же основанием не проверяется ────────────────────────


@pytest.mark.asyncio
async def test_truncated_empty_answer_after_the_round_is_accepted_without_third_call(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    curator_calls_before = len(_curator_calls(fake_llm))

    script = [
        _fetch_page(URL_CBR_NEWS),
        _complete(C3_RATIONALE),
        _empty_answer(C3_TRUNCATED_SUMMARY),
        _empty_answer(C3_TRUNCATED_SUMMARY),  # модель не исправилась: тот же оборванный ответ
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, C3_QUESTION, script, "c3b")
    assert session.final_state.value == "succeeded", session.termination_reason

    # раунд был ровно один, второго отказа тем же основанием нет, третьего вызова нет
    assert await _fixation_count(scratch_url, session.session_id) == 1
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2
    assert fake_llm.state()["queue_left"] == 0
    assert await _rejection_events(scratch_url, session.session_id) == []

    # принятое пустое предложение — прежняя пустая запись (ничего не выдумано и не удалено)
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["claims"] == []
    assert payload["summary"] == C3_TRUNCATED_SUMMARY
    assert payload["correction_round"]["reasons"] == [SUMMARY_CUT_REASON]
    assert "relied_from_rejected_proposal" not in payload  # опоры первого ответа не было
    assert await _all(scratch_url, "SELECT id FROM claims") == []


# ── (ж): опора отклонённого первого предложения переживает пустой ответ ───────


@pytest.mark.asyncio
async def test_relies_of_the_rejected_first_proposal_survive_the_empty_correction_answer(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    curator_calls_before = len(_curator_calls(fake_llm))
    # id, которого в контекст-паке этой сессии нет (тот же приём, что у теста T7.128)
    invisible = "5f0e1a44-1b2c-4d3e-8f40-000000000000"

    first = _empty_answer("Официальная оценка уже записана; нужны уточнения по периодам.")
    # структурный отказ целиком: бюджет новых вопросов (тот же отказ, что на стенде 96a0c535)
    first["content"]["new_questions"] = [
        {"text": f"Уточнение {i}: какой период значения?", "origin": "model_proposal"}
        for i in range(5)
    ]
    first["content"]["relied_claim_ids"] = [str(ids["official"]), invisible]

    script = [
        _fetch_page(URL_CBR_NEWS),
        _complete(C3_RATIONALE),
        first,
        _empty_answer("Новых утверждений нет; записанная официальная оценка достаточна."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, C3_QUESTION, script, "rel")
    assert session.final_state.value == "succeeded", session.termination_reason

    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert len(correction["reasons"]) == 1 and "new_questions" in correction["reasons"][0]
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2
    assert await _rejection_events(scratch_url, session.session_id) == []

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["claims"] == []
    # (ж) видимый id перенесён, невидимый — нет; аддитивный ключ называет перенос
    assert [str(x) for x in payload["relied_claim_ids"]] == [str(ids["official"])]
    assert [str(x) for x in payload["relied_from_rejected_proposal"]] == [str(ids["official"])]
    rejected = [str(line) for line in payload["relied_claims_rejected"]]
    assert len(rejected) == 1 and rejected[0].startswith("rejected_proposal[1]:")
    assert invisible not in str(payload["relied_claim_ids"])
    # ответ исследователя ссылок `c:` не содержал: путь T7.115 не при чём
    assert "relied_from_explorer" not in payload

    # карточка вопроса: ответ есть — по уже записанным знаниям (T7.121)
    result = await _card_result(scratch_url, tmp_path, session.question_id)
    assert result["kind"] == "answered_from_relied", result


# ── регресс отказов прежних оснований сохранён (не ослаблен T7.136) ───────────


@pytest.mark.asyncio
async def test_declared_intent_on_short_summary_still_uses_the_t7123_kind(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"
    curator_calls_before = len(_curator_calls(fake_llm))
    declared = (
        "Существующий claim противоречит новым данным, поэтому предлагается создать новый claim "
        "с актуальным значением прогноза."
    )
    script = [
        _fetch_page(URL_CBR_NEWS),
        _complete(C3_RATIONALE),
        _empty_answer(declared),
        _empty_answer("Новых утверждений нет."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, C3_QUESTION, script, "k3")

    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [
        "в сводке объявлено новое утверждение, но список claims пуст"
    ]
    assert SUMMARY_CUT_REASON not in correction["reasons"]
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2
    assert await _rejection_events(scratch_url, session.session_id) == []
