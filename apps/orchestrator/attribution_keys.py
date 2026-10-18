"""T7.108 (часть б, ADR-0029 дополнение): сборщик ключей «известной производности».

Третий режим детектора значений (`bibliography_known_producer`) знает о производителе
утверждения только из ключей, собранных с решений строгого режима и режима публикации того
же утверждения. Гвард «без цепочек» (ADR-0029, решение T7.107): запись третьего режима
ключей НЕ даёт — иначе блог, сославшийся «Источники: Росстат», сам становился бы источником
ключа для следующего блога.

Этот чистый сборщик — единственное место, где цикл формулировки (два прохода, T7.108) решает,
какое решение добавляет ключ. Тест закрепляет его молчание на всех остальных статусах и
pairing'ах.
"""

from __future__ import annotations

from apps.research_proxy.value_attribution import (
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    ValueAttributionDecision,
)


def known_primary_key_from_decision(decision: ValueAttributionDecision | None) -> str | None:
    """Ключ производности, который даёт это решение, или None.

    Ключ дают ТОЛЬКО решения строгого режима (`value_and_primary_in_fragment`) и режима
    публикации (`publication_window`): они называют производителя по тексту самой улики.
    Третий режим, собственная публикация, отказ и неоднозначность — None (без цепочек).
    """
    if decision is None or not decision.is_derivative or decision.primary_key is None:
        return None
    if decision.pairing in (PAIRING_VALUE_AND_PRIMARY, PAIRING_PUBLICATION_WINDOW):
        return decision.primary_key
    return None
