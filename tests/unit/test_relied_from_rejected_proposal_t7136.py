"""T7.136 (часть 3, ADR-0038 дополнение): перенос опоры от ОТКЛОНЁННОГО первого предложения.

Стенды 2d682ab0 и abef06b7: хост отказал первому предложению ЦЕЛИКОМ (гейт периода/даты
прогноза), первое предложение называло `relied_claim_ids`, а пустой ответ раунда исправления —
нет. Финальный ответ исследователя ссылок `c:` не содержал, поэтому запасной путь T7.115
молчал, и опора терялась совсем: в итоговом событии `claim_created` не было ни
`relied_claim_ids`, ни `relied_from_explorer`, карточка вопроса — «Ответ не записан».

Хост переносит только то, что проходит тот же разрешатель `resolve_claim_reference` и тот же
набор видимости контекст-пака, что у опоры куратора T7.82: невидимый, неоднозначный или
неразбираемый id НЕ переносится и называется отказом с префиксом `rejected_proposal[k]:`.
Функция чистая (ни БД, ни LLM); сценарная часть — в
`tests/scenario/test_curator_summary_cut_correction_t7136.py`.
"""

from __future__ import annotations

import uuid

import pytest

from apps.orchestrator.orchestrator import RELIED_CLAIMS_LIMIT, _relied_from_rejected_proposal

pytestmark = pytest.mark.unit

VISIBLE = uuid.UUID("060d979a-26d2-4822-bdc6-2dd1f31dafa5")  # опора стенда 2d682ab0
VISIBLE_2 = uuid.UUID("07126d06-8bf2-4f8f-bdc4-71944ef1d905")  # опора стенда abef06b7
INVISIBLE = uuid.UUID("136a0ca0-0000-4000-8000-000000000000")  # в паке этой сессии его нет


def test_visible_full_uuid_is_carried() -> None:
    carried, rejected = _relied_from_rejected_proposal([str(VISIBLE)], [VISIBLE, VISIBLE_2], 8)
    assert carried == [str(VISIBLE)]
    assert rejected == []


def test_unique_hex_prefix_is_carried_as_full_id() -> None:
    carried, rejected = _relied_from_rejected_proposal(["07126d06"], [VISIBLE, VISIBLE_2], 8)
    assert carried == [str(VISIBLE_2)]
    assert rejected == []


def test_invisible_id_is_not_carried_and_is_named() -> None:
    carried, rejected = _relied_from_rejected_proposal(
        [str(VISIBLE), str(INVISIBLE)], [VISIBLE, VISIBLE_2], 8
    )
    assert carried == [str(VISIBLE)]
    assert len(rejected) == 1
    assert rejected[0].startswith("rejected_proposal[1]:")
    assert str(INVISIBLE) not in carried


def test_ambiguous_prefix_is_not_carried_and_is_named() -> None:
    twin_a = uuid.UUID("abcd1234-0000-4000-8000-000000000001")
    twin_b = uuid.UUID("abcd1234-0000-4000-8000-000000000002")
    carried, rejected = _relied_from_rejected_proposal(["abcd1234"], [twin_a, twin_b], 8)
    assert carried == []
    assert len(rejected) == 1 and rejected[0].startswith("rejected_proposal[0]:")


def test_malformed_reference_is_not_carried_and_is_named() -> None:
    carried, rejected = _relied_from_rejected_proposal(["не-id"], [VISIBLE], 8)
    assert carried == []
    assert rejected == ["rejected_proposal[0]: unparseable claim reference 'не-id'"]


def test_duplicates_collapse_and_empty_input_carries_nothing() -> None:
    carried, rejected = _relied_from_rejected_proposal(
        [str(VISIBLE), str(VISIBLE), str(VISIBLE_2)], [VISIBLE, VISIBLE_2], 8
    )
    assert carried == [str(VISIBLE), str(VISIBLE_2)]
    assert rejected == []
    assert _relied_from_rejected_proposal([], [VISIBLE], 8) == ([], [])


def test_shared_ceiling_is_named_not_silently_dropped() -> None:
    visible = [VISIBLE, VISIBLE_2]
    carried, rejected = _relied_from_rejected_proposal([str(VISIBLE), str(VISIBLE_2)], visible, 1)
    assert carried == [str(VISIBLE)]
    assert rejected == ["rejected_proposal[1]: more than 1 relied ids"]
    assert RELIED_CLAIMS_LIMIT == 8
