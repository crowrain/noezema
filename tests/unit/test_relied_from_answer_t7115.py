"""Unit (без LLM и БД): чистый ресолвер опоры из финального ответа исследователя
(T7.115, ADR-0032 дополнение).

Проверяются форма ссылки (`c:<полный uuid>` / `c:<hex-префикс от 8>`), разрешение ТОЛЬКО
по id контекст-пака (тот же `resolve_claim_reference`), дедуп с опорой куратора,
исключение операций сессии, общий потолок и то, что не-форма (короткий префикс, кириллическая
«с:», ссылка внутри слова) молча не считается ссылкой.
"""

from __future__ import annotations

import uuid

import pytest

from apps.orchestrator.orchestrator import (
    _ANSWER_CLAIM_REF_RE,
    RELIED_CLAIMS_LIMIT,
    _relied_from_answer,
)

pytestmark = pytest.mark.unit

PACK_A = uuid.UUID("118b76b3-fe02-44d0-b1de-6d036c48646b")
PACK_B = uuid.UUID("229c8de4-1a2b-4c3d-8e4f-00000000000b")
PACK_C = uuid.UUID("3f2b1c0d-0001-4a2b-8c3d-00000000000a")
PACK_D = uuid.UUID("3f2b1c0d-0002-4a2b-8c3d-00000000000b")  # с PACK_C общий префикс 3f2b1c0d
OUTSIDE = uuid.UUID("99999999-1111-4111-8111-999999999999")

PACK = [PACK_A, PACK_B, PACK_C, PACK_D]


def _run(text: str, *, taken: set[uuid.UUID] | None = None, ops: set[uuid.UUID] | None = None,
         limit: int = RELIED_CLAIMS_LIMIT) -> tuple[list[str], list[str]]:
    return _relied_from_answer(text, PACK, taken or set(), ops or set(), limit)


def test_full_uuid_and_unique_prefix_are_resolved() -> None:
    added, rejected = _run(f"итог: c:{PACK_A} и c:{str(PACK_B)[:8]}")
    assert added == [str(PACK_A), str(PACK_B)]  # префикс разрешён в полный id
    assert rejected == []


def test_reference_outside_the_pack_is_rejected_honestly() -> None:
    added, rejected = _run(f"итог: c:{OUTSIDE}")
    assert added == []
    assert len(rejected) == 1 and rejected[0].startswith("answer[0]:")
    assert str(OUTSIDE) in rejected[0]


def test_ambiguous_prefix_is_rejected_not_guessed() -> None:
    added, rejected = _run("итог: c:3f2b1c0d")
    assert added == []
    assert "ambiguous" in rejected[0] and "3f2b1c0d" in rejected[0]


def test_uppercase_and_hyphenated_prefix_forms_resolve() -> None:
    added, rejected = _run(f"итог: C:{str(PACK_A).upper()}")  # не форма (заглавная C) — молчание
    assert added == [] and rejected == []
    added, rejected = _run(f"итог: c:{str(PACK_A)[:13]}")  # префикс с дефисом внутри
    assert added == [str(PACK_A)] and rejected == []


def test_not_a_reference_form_is_silent() -> None:
    # короткий префикс (<8), кириллическая «с:», ссылка приклеена к слову, пустой текст
    added, rejected = _run("с:aabbccdd, a:c:1234567, 1c:118b76b3fe, просто итог.")
    assert added == [] and rejected == []
    added, rejected = _run("")
    assert added == [] and rejected == []


def test_curator_reliance_priority_dedups_without_noise() -> None:
    added, rejected = _run(f"итог: c:{PACK_A} и снова c:{str(PACK_A)[:8]}", taken={PACK_A})
    assert added == []  # id уже в опоре куратора — дубль не шумит в отказы
    assert rejected == []


def test_operation_of_the_session_is_not_a_reliance() -> None:
    added, rejected = _run(f"итог: c:{PACK_B}", ops={PACK_B})
    assert added == []
    assert len(rejected) == 1 and "operation of this session" in rejected[0]


def test_shared_limit_counts_curator_ids_first() -> None:
    added, rejected = _run(
        f"итог: c:{PACK_B} и c:{PACK_C} и c:{PACK_D}",
        taken={PACK_A},
        limit=2,
    )
    assert added == [str(PACK_B)]  # вместе с taken={A} это потолок 2; C и D за лимитом
    assert any("more than 2 relied ids" in reason for reason in rejected)
    assert len(rejected) == 2


def test_regex_rejects_trailing_dash_capture() -> None:
    assert _ANSWER_CLAIM_REF_RE.findall("c:118b76b3-") == ["118b76b3"]
    assert _ANSWER_CLAIM_REF_RE.findall("c:118b76b3-fe02-44d0-b1de-6d036c48646b") == [
        "118b76b3-fe02-44d0-b1de-6d036c48646b"
    ]
