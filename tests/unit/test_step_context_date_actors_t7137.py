"""Unit (T7.137 часть 4): короткая секция контекста шага «Дата и названные акторы».

(a) строка «Дата узла: ГГГГ-ММ-ДД» — дата приходит ПАРАМЕТРОМ (хостовые часы оркестратора),
поэтому тесты детерминированы; (b) если вопрос называет >= 2 акторов закрытого каталога —
строка «Названные в вопросе акторы без отдельного поиска: …» либо «Все названные акторы искались
отдельно.» Блок ничего не запрещает (запрет — гейт части 3) и обязан помещаться в существующие
бюджеты контекста: ceiling отдельный, EXPLORER_CONTEXT_BUDGET задача НЕ поднимала — фиксированные
секции host/hints уже добавлялись без поднятия бюджета.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.question_actors import named_question_actors
from apps.orchestrator.tool_context import (
    STEP_CONTEXT_CHAR_LIMIT,
    render_step_context,
)

pytestmark = [pytest.mark.unit]

C3_NAMED = ("Банк России", "Сбербанк", "ВТБ", "Альфа-Банк", "Газпромбанк")


def test_date_line_is_first_and_date_comes_from_parameter() -> None:
    section = render_step_context("2026-10-27", C3_NAMED, ("Сбербанк",))
    assert section.splitlines()[0] == "# Дата и названные акторы"
    assert section.splitlines()[1].startswith("Дата узла: 2026-10-27.")
    # та же дата в тесте всегда: никакого datetime в самой функции
    assert render_step_context("2000-01-01", (), ()) == render_step_context("2000-01-01", (), ())
    assert render_step_context("", C3_NAMED, ()) == ""


def test_actors_line_lists_uncovered_and_alternates_when_all_covered() -> None:
    with_gaps = render_step_context("2026-10-27", C3_NAMED, ("Банк России", "Сбербанк"))
    assert (
        "Названные в вопросе акторы без отдельного поиска: Банк России, Сбербанк." in with_gaps
    )
    covered = render_step_context("2026-10-27", C3_NAMED, ())
    assert "Все названные акторы искались отдельно." in covered


def test_actor_line_requires_two_named_actors() -> None:
    # 1 актор — только дата; 2 актора — уже строка про покрытие
    single = render_step_context("2026-10-27", ("Росстат",), ("Росстат",))
    assert "без отдельного поиска" not in single and "искались отдельно" not in single
    both = render_step_context("2026-10-27", ("Росстат", "hh.ru"), ("Росстат",))
    assert "Названные в вопросе акторы без отдельного поиска: Росстат." in both
    # вопрос стенда (безработица): ровно 2 названных актора каталога
    unemployment = (
        "Каков уровень безработицы в России по свежим данным Росстата, "
        "и что показывают независимые оценки hh.ru?"
    )
    assert named_question_actors(unemployment) == ("Росстат", "hh.ru")


def test_section_stays_inside_its_char_limit_with_long_actor_lists() -> None:
    # худший случай каталога: все 10 акторов непокрыты
    all_names = (
        "Банк России", "Сбербанк", "ВТБ", "Альфа-Банк", "Газпромбанк",
        "Росстат", "hh.ru", "SuperJob", "Минэкономразвития", "Минфин",
    )
    section = render_step_context("2026-10-27", all_names, all_names)
    assert len(section) <= STEP_CONTEXT_CHAR_LIMIT
    # даже если список акторов снят целиком при переполнении — строка даты остаётся
    assert "Дата узла: 2026-10-27." in section


def test_section_fits_explorer_context_budget_without_raising_it() -> None:
    """Бюджет контекста исследователя (EXPLORER_CONTEXT_BUDGET = RESEARCH_CONTEXT_BUDGET + 8000 +
    TOOL_CONTEXT_ADDENDA_CHARS) эта секция НЕ поднимает: она короче 400 знаков и становится
    фиксированной секцией рядом с host (1200, T7.111) и hints (600, T7.119) — те тоже поднимали
    бюджет."""
    from apps.orchestrator.host_availability import HOST_AVAILABILITY_SECTION_CHAR_LIMIT
    from apps.orchestrator.orchestrator import (
        EXPLORER_CONTEXT_BUDGET,
        RESEARCH_CONTEXT_BUDGET,
    )
    from apps.orchestrator.research_hints import RESEARCH_HINTS_SECTION_CHAR_LIMIT
    from apps.orchestrator.tool_context import TOOL_CONTEXT_ADDENDA_CHARS

    assert EXPLORER_CONTEXT_BUDGET == RESEARCH_CONTEXT_BUDGET + 8_000 + TOOL_CONTEXT_ADDENDA_CHARS
    assert STEP_CONTEXT_CHAR_LIMIT <= RESEARCH_HINTS_SECTION_CHAR_LIMIT
    assert STEP_CONTEXT_CHAR_LIMIT <= HOST_AVAILABILITY_SECTION_CHAR_LIMIT


def test_section_stands_before_observations_and_survives_trimming() -> None:
    """Фиксированная позиция, как у бюджета шага/host/hints: при переполнении контекста вырезаются
    старые НАБЛЮДЕНИЯ, секция остаётся (прецедент T7.111 2б / T7.119 3)."""
    from apps.orchestrator.orchestrator import RESEARCH_CONTEXT_BUDGET, Orchestrator, SessionContext

    orch = Orchestrator.__new__(Orchestrator)  # чистый билдер, атрибуты self не читает
    big = "[research source: https://example.com/page]\n" + "x" * RESEARCH_CONTEXT_BUDGET
    ctx = SessionContext(question_text="q", plan="p", observations=[big] * 15)
    section = render_step_context("2026-10-27", C3_NAMED, ("Банк России", "Сбербанк"))
    rendered = orch._explorer_context(
        ctx, ["web.search"], None, step=5, step_limit=16, step_context_note=section
    )
    assert "Дата узла: 2026-10-27." in rendered
    assert "Названные в вопросе акторы без отдельного поиска: Банк России, Сбербанк." in rendered
