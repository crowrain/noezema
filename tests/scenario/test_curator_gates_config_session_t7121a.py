"""Scenario (T7.121a часть д5, т5): один и тот же промпт-сценарий — гейт молчит без ключа
и работает с ключом `session_limits.curator_record_gates`.

Фикстура — стендовый с1 T7.121 (finmarket-6727171, побайтовая страница): предложение из
одной операции с периодом «октябре 2026», который на странице написан только как
датировка статей. Разница только в конфигурации активированного payload:

- без ключа (payload'ы до config-v28) — гейт периода не применяется вовсе: предложение
  принимается как есть, ни отказов, ни раунда исправления (прежнее поведение);
- с ключом `true` (config-v28) — предложение отклоняется целиком с именованной причиной
  и одним раундом исправления (поведение включённого гейта, закреплённое T7.121).

Гейты склейки дублей и значения (T7.108/T7.121) здесь не задействованы — они от
конфигурации не зависят и закреплены своими тестами.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_curator_period_gate_session_t7121 import (
    C1_CREATED,
    QUESTION_FINMARKET,
    RATIONALE_FINMARKET,
    SENTINEL_CLAIM,
    URL_FINMARKET,
    FakeEngineClient,
    _answer,
    _claim,
    _claim_created_payloads,
    _complete,
    _correction_requested_payload,
    _fixation_count,
    _links,
    _payload,
    _run,
)
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import _fetch
from tests.scenario.test_scope_coverage import _all

pytestmark = pytest.mark.scenario


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _payload_without_gates() -> dict[str, Any]:
    payload = copy.deepcopy(_payload())
    payload["session_limits"].pop("curator_record_gates", None)
    return payload


@pytest.mark.asyncio
async def test_period_gate_silent_without_config_key(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Тот же сценарий T7.121 т5(а) без ключа: гейт молчит — ранее отклоняемая операция
    проходит, предложения-исправления не нужны."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_without_gates())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Итог (первый ответ)."),
        ],
        "t7121a_off",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in claims] == [C1_CREATED], claims
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    # ключи снятий (`rejected_operations` со счётчиками) пишутся только при непустом снятии —
    # сессия без отказов остаётся байт-в-байт прежней (паттерн T7.105)
    assert "rejected_operations" not in payloads[0]
    assert "correction_round" not in payloads[0]


@pytest.mark.asyncio
async def test_period_gate_active_with_config_key(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Тот же payload с ключом `true`: отклонение целиком именованной причиной и ровно один
    раунд исправления — поведение включённого гейта прежнее (T7.121), переключатель — только
    в конфигурации."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    bad = _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Итог (первый ответ).")
    fixed = _answer([_claim(SENTINEL_CLAIM)], _links((0, 0)), summary="Итог (исправленный ответ).")
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [_fetch(URL_FINMARKET), _complete(RATIONALE_FINMARKET), bad, fixed],
        "t7121a_on",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [
        "claim[0]: период «октябре 2026» не найден рядом со значением на связанной странице — "
        "возьми период из текста или напиши, что период не указан"
    ], correction["reasons"]

    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in claims] == [SENTINEL_CLAIM], claims
