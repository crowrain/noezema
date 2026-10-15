"""Unit: T7.34 (ADR-0018) — resolve_claim_reference, the fail-closed
resolution of the model's reference to an EXISTING claim (full UUID or
a unique prefix among the session-visible claims).

Pure function — no DB: the visible set is a list of UUIDs (at the
curator boundary — the context pack's [c:<uuid>] lines; at the commit
boundary — the claims with a head in the session's snapshot, T7.9).

T7.116 (часть в): ведущая упаковка ссылки «c:…» и «[c:…]» (скобки, пробелы) снимается до
разбора; сами правила разбора, наборы ошибок и их тексты не изменены.
"""

from __future__ import annotations

import uuid

import pytest

from packages.memory.service import resolve_claim_reference

pytestmark = [pytest.mark.unit]

#: A and B share the first 16 hex chars ("6e70379ee1f52284" — the
#: observed truncation length, EVAL-4d pack 7); C does not match them.
A = uuid.UUID("6e70379e-e1f5-2284-aaaa-bbbbccccdddd")
B = uuid.UUID("6e70379e-e1f5-2284-0000-111122223333")
C = uuid.UUID("11111111-2222-3333-4444-555566667777")


def test_full_uuid_visible_resolves() -> None:
    resolved, problem = resolve_claim_reference(str(A), [A, C])
    assert resolved == A and problem is None


def test_full_uuid_uppercase_resolves() -> None:
    # the model may echo the id in any case
    resolved, problem = resolve_claim_reference(str(A).upper(), [A, C])
    assert resolved == A and problem is None


def test_full_uuid_not_visible_rejected() -> None:
    other = uuid.uuid4()
    resolved, problem = resolve_claim_reference(str(other), [A, C])
    assert resolved is None
    assert problem is not None and "not visible" in problem


def test_32_hex_without_dashes_is_full_uuid() -> None:
    nodash = str(A).replace("-", "")
    resolved, problem = resolve_claim_reference(nodash, [A, C])
    assert resolved == A and problem is None
    # ...and the same 32-hex string of a NON-visible claim is rejected
    other = uuid.uuid4()
    resolved, problem = resolve_claim_reference(str(other).replace("-", ""), [A, C])
    assert resolved is None and "not visible" in problem


def test_unique_prefix_resolves() -> None:
    # the observed truncation length (EVAL-4d pack 7): 16 hex chars
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [A, C])
    assert resolved == A and problem is None


def test_prefix_with_dash_in_canonical_form_resolves() -> None:
    # a truncation that keeps the canonical dash ("6e70379e-e1f5…")
    resolved, problem = resolve_claim_reference("6e70379e-e1f5", [A, C])
    assert resolved == A and problem is None


def test_min_prefix_length_is_eight() -> None:
    resolved, problem = resolve_claim_reference("6e70379e", [A, C])
    assert resolved == A and problem is None
    resolved, problem = resolve_claim_reference("6e70379", [A, C])
    assert resolved is None and "unparseable" in problem


def test_ambiguous_prefix_rejected() -> None:
    # A and B share the first 16 hex chars — the prefix must NOT guess
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [A, B, C])
    assert resolved is None
    assert problem is not None and "ambiguous" in problem


def test_no_match_prefix_rejected() -> None:
    resolved, problem = resolve_claim_reference("ffffffffffffffff", [A, C])
    assert resolved is None
    assert problem is not None and "no visible claim matches" in problem


def test_garbage_rejected() -> None:
    # T7.116 (часть в): префикс «c:» из контекст-пака больше не мусор — он нормализуется
    # (см. тесты T7.116 ниже); список мусора уменьшен ровно на этот случай, остальное без изменений.
    for raw in ("6e70379e-e1f5-xxxx", "6e70379ee1f5228g", "123", "c:", "[c:", "c:x", "к:6e70379e"):
        resolved, problem = resolve_claim_reference(raw, [A, C])
        assert resolved is None
        assert problem is not None and "unparseable" in problem, raw


def test_empty_visible_set_rejects_everything() -> None:
    resolved, problem = resolve_claim_reference(str(A), [])
    assert resolved is None and "not visible" in problem
    resolved, problem = resolve_claim_reference("6e70379ee1f52284", [])
    assert resolved is None and "no visible claim matches" in problem


def test_whitespace_tolerated() -> None:
    resolved, problem = resolve_claim_reference(f"  {A!s}  ", [A, C])
    assert resolved == A and problem is None


# ─── T7.116 (часть в): форма ссылки из контекст-пака «c:<uuid>» / «[c:<uuid>]» ──
#
# Стенд (сессия 53be7fd6): оператор передал куратору relied_claim_ids в форме конспекта
# «c:44bd1460-bb72-4521», «c:c6729d4c-72a7-4f74» — прежний резолвер отвечал «unparseable
# claim reference». Нормализация снимает ведущие скобки и «c:» (пробелы по краям — тоже);
# после этого разбор прежний: полный uuid или однозначный hex-префикс не короче 8 (дефисы
# игнорируются). Ошибки и их тексты не изменены.

STAND_A = uuid.UUID("44bd1460-bb72-4521-0000-000000000000")
STAND_B = uuid.UUID("c6729d4c-72a7-4f74-0000-000000000000")


def test_c_prefixed_full_uuid_resolves() -> None:
    resolved, problem = resolve_claim_reference("c:44bd1460-bb72-4521-0000-000000000000", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None


def test_c_prefixed_truncated_uuid_resolves() -> None:
    """Стендовые значения «c:44bd1460-bb72-4521» и «c:c6729d4c-72a7-4f74» — обрезанные uuid:
    префикс из них однозначный (16 hex), обе ссылки разрешаются."""
    resolved, problem = resolve_claim_reference("c:44bd1460-bb72-4521", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None
    resolved, problem = resolve_claim_reference("c:c6729d4c-72a7-4f74", [STAND_A, STAND_B])
    assert resolved == STAND_B and problem is None


def test_bracketed_pack_form_resolves() -> None:
    resolved, problem = resolve_claim_reference("[c:44bd1460]", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None
    resolved, problem = resolve_claim_reference("[c:c6729d4c-72a7-4f74-0000-000000000000]", [STAND_A, STAND_B])
    assert resolved == STAND_B and problem is None


def test_c_prefix_with_spaces_resolves() -> None:
    resolved, problem = resolve_claim_reference("  c: 44bd1460-bb72-4521  ", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None
    resolved, problem = resolve_claim_reference("[ c: 44bd1460 ]", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None


def test_c_prefix_is_case_insensitive_and_single_only() -> None:
    resolved, problem = resolve_claim_reference("C:44BD1460-BB72-4521", [STAND_A, STAND_B])
    assert resolved == STAND_A and problem is None
    # «c:» снимается только один раз: двойная префиксная форма — прежний отказ
    resolved, problem = resolve_claim_reference("c:c:44bd1460-bb72-4521", [STAND_A, STAND_B])
    assert resolved is None and problem is not None and "unparseable" in problem, resolved


def test_c_prefixed_ambiguous_prefix_still_refused() -> None:
    """Нормализация не разрешает догадку: неоднозначный префикс (у A и B из шапки файла
    общий префикс первых 16 hex) остаётся прежним отказом `ambiguous`."""
    resolved, problem = resolve_claim_reference("c:6e70379ee1f52284", [A, B, C])
    assert resolved is None and problem is not None and "ambiguous" in problem


def test_c_prefixed_nonvisible_uuid_still_refused() -> None:
    resolved, problem = resolve_claim_reference("c:" + str(uuid.uuid4()), [A, C])
    assert resolved is None and problem is not None and "not visible" in problem
