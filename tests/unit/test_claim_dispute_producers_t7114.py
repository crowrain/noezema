"""Unit: производитель как кандидат первоисточника спора (T7.114, ADR-0031 дополнение).

Решение пользователя: кандидатами на роль ПЕРВОИСТОЧНИКА, кроме источников улик
утверждения, являются также источники-производители, к которым хост УЖЕ приписал
улики этого утверждения (запись значения `value_attribution` и страничный указатель
`parent_source_id` — их читает `claim_producer_sources`). Роль ПЕРЕСКАЗА остаётся
прежней: только источник собственной улики утверждения.

Здесь — чистый разбор пары адресов без базы; рабочий контур (запись коррекции,
каскад, «уже сгруппированы») закреплён сценарием
`tests/scenario/test_claim_dispute_producers_t7114.py`.
"""

from __future__ import annotations

import uuid

import pytest

from packages.memory import dispute


def _source(uri: str, tag: str) -> dispute.ClaimSource:
    return dispute.ClaimSource(
        source_id=uuid.uuid5(uuid.NAMESPACE_URL, tag), canonical_uri=uri
    )


CALIBER = _source("https://caliber.example/economics/without-rabochikh", "caliber")
FINMARKET = _source("https://finmarket.example/news/6727171", "finmarket")
PRODUCER = _source("https://rosstat.example/press/unemployment-sep-2026", "rosstat")
EVIDENCE = [CALIBER, FINMARKET]


def test_producer_is_accepted_as_primary() -> None:
    """Первоисточник спора — производитель, которому хост уже приписал улику."""
    pair = dispute.resolve_dispute_pair(
        EVIDENCE,
        primary_uri="https://rosstat.example/press/unemployment-sep-2026",
        retelling_uri=FINMARKET.canonical_uri or "",
        producer_sources=[PRODUCER],
    )
    assert pair.primary.source_id == PRODUCER.source_id
    assert pair.retelling.source_id == FINMARKET.source_id


def test_retelling_role_stays_evidence_only() -> None:
    """Пересказом нельзя назвать производителя: отказ, как и прежде."""
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            EVIDENCE,
            primary_uri=CALIBER.canonical_uri or "",
            retelling_uri="https://rosstat.example/press/unemployment-sep-2026",
            producer_sources=[PRODUCER],
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND
    assert exc.value.detail.startswith("retelling:")


def test_address_neither_evidence_nor_producer_keeps_old_refusal() -> None:
    """Адреса нет ни среди улик, ни среди производителей — прежний честный отказ."""
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            EVIDENCE,
            primary_uri="https://absent.example/press",
            retelling_uri=FINMARKET.canonical_uri or "",
            producer_sources=[PRODUCER],
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND
    assert exc.value.detail == "primary: https://absent.example/press"


def test_producer_equal_to_an_evidence_is_counted_once() -> None:
    """Тот же источник (та же строка `sources`) в обеих ролях — один кандидат, не двусмысленность."""
    pair = dispute.resolve_dispute_pair(
        EVIDENCE,
        primary_uri=FINMARKET.canonical_uri or "",
        retelling_uri=CALIBER.canonical_uri or "",
        producer_sources=[FINMARKET],  # он же и улика, и производитель чужой улики
    )
    assert pair.primary.source_id == FINMARKET.source_id
    assert pair.retelling.source_id == CALIBER.source_id


def test_two_different_sources_with_one_address_stay_ambiguous() -> None:
    """Две РАЗНЫЕ строки источников с одним адресом — прежний отказ «неоднозначно»."""
    lookalike = dispute.ClaimSource(
        source_id=uuid.uuid5(uuid.NAMESPACE_URL, "lookalike"),
        canonical_uri=CALIBER.canonical_uri,
    )
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            EVIDENCE,
            primary_uri=CALIBER.canonical_uri or "",
            retelling_uri=FINMARKET.canonical_uri or "",
            producer_sources=[lookalike],
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND
    assert "неоднозначно" in exc.value.detail


def test_empty_producers_keep_the_old_narrow_pair() -> None:
    """Без производителей разбор остаётся прежним (поздние вызовы не расширяются сами)."""
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            EVIDENCE,
            primary_uri=PRODUCER.canonical_uri or "",
            retelling_uri=FINMARKET.canonical_uri or "",
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND


def test_producer_named_as_both_roles_is_the_same_source_refusal() -> None:
    """Нельзя объявить источник пересказом самого себя — и для производителя тоже."""
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            EVIDENCE,
            primary_uri=FINMARKET.canonical_uri or "",
            retelling_uri=FINMARKET.canonical_uri or "",
            producer_sources=[PRODUCER],
        )
    assert exc.value.code == dispute.REFUSAL_SAME_SOURCE
