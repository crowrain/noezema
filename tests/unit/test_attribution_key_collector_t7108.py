"""T7.108 (часть б, ADR-0029 дополнение): сборщик ключей второго прохода связки.

`apps/orchestrator/attribution_keys.py::known_primary_key_from_decision` — единственный
источник ключей «известной производности» для режима `bibliography_known_producer` внутри
предложения: решения **строгого режима и режима публикации**. Записи третьего режима ключей
не дают (страх цепочек, ADR-0029 §T7.107): связка через список источников обязана опираться
на независимое строгое/публикационное решение той же фразы или на якорь перепроверки.
"""

from __future__ import annotations

from apps.orchestrator.attribution_keys import known_primary_key_from_decision
from apps.research_proxy.value_attribution import (
    PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER,
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    STATUS_SELF_PRIMARY,
    STATUS_VALUE_NOT_ATTRIBUTED,
    ValueAttributionDecision,
)


def _derivative(pairing: str, key: str | None) -> ValueAttributionDecision:
    return ValueAttributionDecision(
        status=STATUS_DERIVATIVE, primary_key=key, primary_name="Росстат", pairing=pairing
    )


def test_strict_pairing_key_is_collected() -> None:
    assert known_primary_key_from_decision(_derivative(PAIRING_VALUE_AND_PRIMARY, "rosstat")) == "rosstat"


def test_publication_pairing_key_is_collected() -> None:
    decision = _derivative(PAIRING_PUBLICATION_WINDOW, "rosstat")
    assert known_primary_key_from_decision(decision) == "rosstat"


def test_bibliography_pairing_never_adds_a_key() -> None:
    # цепочка невозможна по построению: решение третьего режима ключ не отдаёт
    assert known_primary_key_from_decision(
        _derivative(PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER, "rosstat")
    ) is None


def test_derivative_without_key_is_not_collected() -> None:
    assert known_primary_key_from_decision(_derivative(PAIRING_VALUE_AND_PRIMARY, None)) is None


def test_non_derivative_statuses_are_not_collected() -> None:
    for status in (
        STATUS_SELF_PRIMARY,
        STATUS_AMBIGUOUS,
        STATUS_OWN_ASSESSMENT,
        STATUS_NO_VALUE_ATTRIBUTION,
        STATUS_VALUE_NOT_ATTRIBUTED,
    ):
        decision = ValueAttributionDecision(
            status=status,
            primary_key="rosstat",
            pairing=PAIRING_VALUE_AND_PRIMARY,
        )
        assert known_primary_key_from_decision(decision) is None, status
