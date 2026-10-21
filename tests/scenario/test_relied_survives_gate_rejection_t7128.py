"""T7.128 (часть 2, ADR-0034 §11): опора переживает отказ предложения целиком (группа (а)).

Стендовый случай c1 (сессия a53d85b0, config-v29): предложение куратора из ОДНОЙ новой
операции (прогноз МЭР 2,2%) и семи `relied_claim_ids`. Единственную операцию снял гейт
периода/даты прогноза (`curator_reject_kind = claim_period_not_near_value`) — гейт, который
судит ровно свою операцию. Так как новых операций в предложении не осталось, хост отказал
предложению ЦЕЛИКОМ: и в первом ответе, и после раунда исправления. Опора при отказе целиком
не записывается вовсе (CLAIM_CREATED не пишется), и карточка вопроса сказала «Ответ не
записан», хотя ответ жил в памяти — те же утверждения контекст-пака.

Что закреплено тестом:
- (а) гейт группы (а) снял ВСЕ операции предложения, а опоры разрешились -> ПОСЛЕ раунда
  исправления хост не отказывает целиком, а пишет обычную частичную запись T7.105:
  `relied_claim_ids`, `rejected_operations` со снятой операцией и её причиной,
  `claims_proposed: 1`, `claims_applied: 0`; журнала отказа (`curator_rejected`) нет;
  снятая операция в знание НЕ попадает, новых событий и ключей нет; раунд исправления
  по-прежнему предлагается ровно один. Карточка вопроса — `answered_from_relied` (T7.121);
- (б) тот же сценарий без опор -> прежний отказ целиком (тот же event, тот же
  `curator_reject_kind`, additive `correction_round`), карточка `no_answer`;
- (в) группа (б): неразрешимая перепроверочная ссылка отказывает предложению целиком и после
  раунда — объявленная опора не спасает.

Никаких внешних запросов: HTTP-слой research proxy подменён (тот же приём, что у тестов
T7.108/T7.121); finmarket — побайтовый нормализованный текст стендовой страницы.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_curator_period_gate_session_t7121 import (
    RATIONALE_FINMARKET,
    URL_FINMARKET,
    FakeEngineClient,
    _answer,
    _claim,
    _claim_created_payloads,
    _complete,
    _links,
    _payload,
)
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import _fetch, _run_session
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question
from tests.scenario.test_web_answer_api import _client, _make
from tests.unit.test_curator_period_gate_t7121 import C1_CREATED

pytestmark = pytest.mark.scenario

GATED_STATEMENT = C1_CREATED  # период «октябре 2026» не подтверждён рядом со значением на finmarket
PERIOD_REASON_FRAGMENT = "не найден рядом со значением на связанной странице"
BOGUS_REF = "5f0e1a44-1b2c-4d3e-8f40-000000000000"  # id нет в контекст-паке этой сессии

SEED_QUESTION = (
    "Каков официальный уровень безработицы в России по методологии МОТ: приведи цифру "
    f"и первоисточник. Источники: {URL_FINMARKET}"
)
ASK_QUESTION = (
    "Каков официальный уровень безработицы в России по методологии МОТ в октябре 2026 года: "
    f"приведи цифру и первоисточник. Источники: {URL_FINMARKET}"
)

# законная формулировка той же страницы (период подтверждён рядом со значением) — её пишет
# первая сессия; вторая на неё опирается
SEED_STATEMENT = "Уровень безработицы в России в сентябре 2026 года составил 2,2%."


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _payload_with_gate() -> dict[str, Any]:
    """config-v28: гейты периода/даты прогноза включены (`curator_record_gates: true`)."""
    payload = copy.deepcopy(_payload())
    payload["research_proxy"]["rate_limit_max"] = 20
    return payload


async def _seed_claim(scratch_url: str, fake_llm: FakeLLM, tmp_path: Path) -> uuid.UUID:
    """Первая сессия записывает утверждение той же темы — оно попадёт в контекст-пак второй."""
    qid = await _seed_question(scratch_url, SEED_QUESTION)
    fake_llm.script(
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _answer(
                [_claim(SEED_STATEMENT)],
                _links((0, 0)),
                summary="Запись первоисточника (первая сессия).",
            ),
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-seed", tmp_path / "art-seed", qid)
    assert outcome.final_state.value == "succeeded"
    rows = await _all(
        scratch_url, "SELECT id::text FROM claims WHERE statement = :t", {"t": SEED_STATEMENT}
    )
    assert len(rows) == 1
    return uuid.UUID(str(rows[0]["id"]))


def _gated_answer(*, relied: list[str], statement: str = GATED_STATEMENT) -> dict[str, Any]:
    """Предложение стенда c1: одна новая операция (период не подтверждён страницей) + опоры."""
    answer = _answer([_claim(statement)], _links((0, 0)), summary="Итог куратора.")
    if relied:
        answer["content"]["relied_claim_ids"] = relied
    return answer


def _unresolvable_answer(*, relied: list[str]) -> dict[str, Any]:
    """Группа (б): единственная операция — перепроверка со ссылкой, которой нет в контекст-паке."""
    claim = _claim(GATED_STATEMENT)
    claim["existing_claim_id"] = BOGUS_REF
    answer = _answer([claim], _links((0, 0)), summary="Итог куратора (перепроверка).")
    if relied:
        answer["content"]["relied_claim_ids"] = relied
    return answer


async def _ask_session(
    scratch_url: str, fake_llm: FakeLLM, tmp_path: Path, script: list[dict[str, Any]]
) -> tuple[Any, uuid.UUID]:
    qid = await _seed_question(scratch_url, ASK_QUESTION)
    fake_llm.script(script)
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-ask", tmp_path / "art-ask", qid)
    return outcome, qid


async def _correction_reasons(scratch_url: str, session_id: Any) -> list[str]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s "
        "AND payload ? 'curator_correction_requested' ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(rows) == 1, rows
    return [str(reason) for reason in dict(rows[0]["payload"])["curator_correction_requested"]["reasons"]]


async def _card_result(scratch_url: str, tmp_path: Path, qid: uuid.UUID) -> dict[str, Any]:
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{qid}/answer")
            assert response.status_code == 200
            return dict(response.json()["result"])
    finally:
        await app_engine.dispose()


# ── (а) опора спасает ответ: гейт группы (а) снял все операции предложения ──────


@pytest.mark.asyncio
async def test_relies_survive_whole_proposal_refusal_of_the_period_gate(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_with_gate())).state == "active"
    seed_id = await _seed_claim(scratch_url, fake_llm, tmp_path / "seed")

    outcome, qid = await _ask_session(
        scratch_url,
        fake_llm,
        tmp_path,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _gated_answer(relied=[str(seed_id)]),
            _gated_answer(relied=[str(seed_id)]),  # раунд исправления: та же операция, те же опоры
        ],
    )
    assert outcome.final_state.value == "succeeded", outcome.termination_reason
    session_id = outcome.session_id

    # раунд исправления был — ровно один (поведение варианта C не изменено)
    reasons = await _correction_reasons(scratch_url, session_id)
    assert len(reasons) == 1 and PERIOD_REASON_FRAGMENT in reasons[0], reasons

    # итогового отказа нет: `curator_rejected` в журнале отсутствует
    assert await _rejection_events(scratch_url, session_id) == []

    # обычный частичный журнал T7.105: снятая операция названа, опора записана, применено 0
    payloads = await _claim_created_payloads(scratch_url, session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert list(payload.get("relied_claim_ids") or []) == [str(seed_id)]
    assert payload["claims_applied"] == 0 and payload["claims_proposed"] == 1
    operations = list(payload["rejected_operations"])
    assert len(operations) == 1
    assert PERIOD_REASON_FRAGMENT in str(operations[0]["reason"]), operations
    assert payload["correction_round"]["reasons"] == reasons
    assert payload["public_summary"] == (
        "curator: 0 of 1 claims applied, 0 questions, 1 operations rejected by host validation"
    )

    # снятая операция в знание не попала: строка та же, что записала первая сессия
    rows = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in rows] == [SEED_STATEMENT], rows
    created = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claims WHERE created_in_session = :s",
        {"s": str(session_id)},
    )
    assert created is not None and created[0] == 0

    # карточка вопроса: ответ есть — «Ответ по уже записанным знаниям» (T7.121)
    result = await _card_result(scratch_url, tmp_path, qid)
    assert result["kind"] == "answered_from_relied", result


# ── (б) без опор прежний отказ целиком сохраняется ─────────────────────────────


@pytest.mark.asyncio
async def test_same_gated_proposal_without_relies_still_rejects_whole_proposal(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_with_gate())).state == "active"
    await _seed_claim(scratch_url, fake_llm, tmp_path / "seed")

    outcome, qid = await _ask_session(
        scratch_url,
        fake_llm,
        tmp_path,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _gated_answer(relied=[]),
            _gated_answer(relied=[]),
        ],
    )
    assert outcome.final_state.value == "succeeded", outcome.termination_reason
    session_id = outcome.session_id

    reasons = await _correction_reasons(scratch_url, session_id)
    assert len(reasons) == 1 and PERIOD_REASON_FRAGMENT in reasons[0], reasons

    # прежний итог: отказ ВТОРОГО ответа одним событием `session_state_changed` с тем же kind
    rejections = await _rejection_events(scratch_url, session_id)
    assert len(rejections) == 1, rejections
    assert rejections[0]["curator_reject_kind"] == "claim_period_not_near_value"
    assert rejections[0]["correction_round"]["reasons"] == reasons

    # CLAIM_CREATED нет: при отказе целиком опора и операции не пишутся
    assert await _claim_created_payloads(scratch_url, session_id) == []
    rows = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in rows] != []  # seed не тронут
    result = await _card_result(scratch_url, tmp_path, qid)
    assert result["kind"] == "no_answer", result


# ── (в) группа (б): опора не спасает отказ, который судит не только снятые операции ──


@pytest.mark.asyncio
async def test_unresolvable_reverify_reference_is_not_rescued_by_relies(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_with_gate())).state == "active"
    seed_id = await _seed_claim(scratch_url, fake_llm, tmp_path / "seed")

    outcome, _ = await _ask_session(
        scratch_url,
        fake_llm,
        tmp_path,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _unresolvable_answer(relied=[str(seed_id)]),
            _unresolvable_answer(relied=[str(seed_id)]),
        ],
    )
    assert outcome.final_state.value == "succeeded", outcome.termination_reason
    session_id = outcome.session_id

    await _correction_reasons(scratch_url, session_id)  # раунд был
    rejections = await _rejection_events(scratch_url, session_id)
    assert len(rejections) == 1, rejections
    assert rejections[0]["curator_reject_kind"] == "reverify_unresolved"
    assert "correction_round" in rejections[0]
    # опора не записана: при отказе группы (б) CLAIM_CREATED нет
    assert await _claim_created_payloads(scratch_url, session_id) == []
