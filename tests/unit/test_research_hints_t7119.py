"""Unit (T7.119): классификация «официальные + независимые», каталог подсказок и потолок шагов.

Классификация — чистая функция `apps/orchestrator/research_hints.py` на тех же нормализующих
функциях, что гейты почти-дубликатов (`content_word_stems` / `normalize_search_query`,
T7.111a/T7.112): новых словарей нормализации нет, маркеры — закрытые списки, их основы получены
той же функцией. Столбцы таблицы — контрольные вопросы `docs/eval/control-set-v1.jsonl` (c1–c5) и
тексты шести фикстур отчёта честности (STATUS T7.118, sessions 38d9a727, 5cc4b7bb, 6a4f4c34,
847f3c44, d0159cf1, d2afe4a6): вопрос c3 с пометками повторов и инфляция Росстата с независимыми
оценками (наблюдаемая инфляция инФОМ + аналитики банков).

Ни LLM, ни сети, ни БД: тесты только чистые функции и файл payload config-v27.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from apps.orchestrator.research_hints import (
    HINTS_SECTION_HEADER,
    RESEARCH_HINTS_SECTION_CHAR_LIMIT,
    classify_official_independent,
    effective_explorer_step_cap,
    independent_markers_found,
    official_markers_found,
    parse_independent_hints,
    render_independent_hints_section,
    select_hint_topics,
)
from packages.domain.config import optional_session_limits_problems

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]
V26_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v26-payload.json"
V27_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v27-payload.json"

# тексты — дословно docs/eval/control-set-v1.jsonl (T7.118)
C1: Final = (
    "Как изменились реальные заработные платы в России в 2026 году по данным Росстата и что "
    "показывают независимые источники (hh.ru, SuperJob, аналитики банков)? Приведи официальные "
    "цифры и независимые оценки раздельно, с источниками и периодами."
)
C2: Final = (
    "Каков уровень безработицы в России по свежим данным Росстата, и что показывают независимые "
    "индикаторы рынка труда (индексы hh.ru, оценки аналитиков банков)? Приведи официальные цифры "
    "и независимые оценки раздельно, с источниками."
)
C3: Final = (
    "Какие прогнозы инфляции в России на 2026 год дают аналитики крупных банков (Сбербанк, ВТБ, "
    "Альфа-Банк, Газпромбанк) и чем они отличаются от прогноза Банка России? Приведи прогноз "
    "каждого с источником и датой."
)
C4: Final = (
    "Какова годовая инфляция в России в сентябре 2026 года по данным Росстата? Приведи "
    "официальную цифру с источником."
)
C5: Final = "Сколько простых чисел меньше 10000? Проверь вычислением."

# тексты фикстур — дословно поле question.text ответов честности (fixtures-t7118, STATUS T7.118)
FIXTURE_QUESTIONS: Final[dict[str, str]] = {
    "38d9a727": C3,
    "5cc4b7bb": (
        "Какова годовая инфляция в России в сентябре 2026 года по данным Росстата и что показывают "
        "независимые оценки (наблюдаемая инфляция инФОМ, оценки аналитиков банков)? Приведи "
        "официальные цифры и независимые оценки раздельно, с источниками и периодами."
    ),
    "6a4f4c34": C2 + " (повтор 3)",
    "847f3c44": C1 + " (повтор)",
    "d0159cf1": C3 + " (повтор 2)",
    "d2afe4a6": C3 + " (повтор)",
}


def _v27_limits() -> dict[str, Any]:
    return json.loads(V27_PAYLOAD.read_text(encoding="utf-8"))["session_limits"]


def _v26_limits() -> dict[str, Any]:
    return json.loads(V26_PAYLOAD.read_text(encoding="utf-8"))["session_limits"]


def _catalog_topics() -> Any:
    topics, problems = parse_independent_hints(_v27_limits())
    assert problems == []
    return topics


# ─── классификация: c1–c5 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (C1, True),  # официальный маркер (Росстат) + независимые (независимые, аналитики, hh, SuperJob)
        (C2, True),  # Росстат + независимые индикаторы/оценки/аналитики
        (C3, True),  # «Банка России» как название + аналитики банков
        (C4, False),  # только официальная цифра: независимого маркера нет
        (C5, False),  # вычислительный вопрос: ни одного маркера
    ],
)
def test_classification_control_questions(text: str, expected: bool) -> None:
    assert classify_official_independent(text) is expected


def test_classification_c4_has_official_marker_only() -> None:
    """c4 — официальный вопрос без независимой стороны: именно отсутствие независимых маркеров
    держит его на обычном потолке (не ошибки распознавания официального маркера)."""
    assert official_markers_found(C4) is True
    assert independent_markers_found(C4) is False


@pytest.mark.parametrize("fixture_id", sorted(FIXTURE_QUESTIONS))
def test_classification_fixtures_all_official_independent(fixture_id: str) -> None:
    """Все шесть фикстур отчёта честности — вопросы вида «официальные + независимые»."""
    assert classify_official_independent(FIXTURE_QUESTIONS[fixture_id]) is True


def test_classification_marker_boundaries() -> None:
    """Границы закрытых маркеров (детерминированно, без LLM)."""
    # официальная сторона: «ЦБ», Минфин в косвенном падеже, доменная метка из OFFICIAL_HOSTS
    assert official_markers_found("Каков прогноз ЦБ по инфляции?") is True
    assert official_markers_found("Что говорит Минфина о зарплатах?") is True
    assert official_markers_found("По данным rosstat.gov.ru и оценке аналитиков.") is True
    # множественное «банки России» официальной меткой не считается (название — единственное число)
    assert official_markers_found("Прогнозы банков России по инфляции") is False
    # независимая сторона: опрос третьей стороны и оценки
    assert independent_markers_found("Опрос работодателей и оценки аналитиков") is True
    # пересказ-ловушка: официальные оценки Росстата формально попадают в класс (принятая
    # включательность: вопрос всё равно ведёт вторую линию, см. ADR-0037)
    assert classify_official_independent("Каковы оценки Росстата по инфляции?") is True


# ─── итоговый потолок шагов ────────────────────────────────────────────────────


def test_cap_raised_only_for_classified_with_key() -> None:
    limits = _v27_limits()
    assert limits["max_explorer_steps"] == 16
    assert limits["max_explorer_steps_official_independent"] == 20
    assert effective_explorer_step_cap(limits, classified=True, named_count=0) == 20
    assert effective_explorer_step_cap(limits, classified=False, named_count=0) == 16


def test_cap_absent_key_is_previous_behavior() -> None:
    """Отсутствие нового ключа (config-v26 и старше) = прежнее поведение при любом классе."""
    limits = _v26_limits()
    assert "max_explorer_steps_official_independent" not in limits
    assert effective_explorer_step_cap(limits, classified=True, named_count=0) == 16
    assert effective_explorer_step_cap(limits, classified=False, named_count=0) == 16


def test_cap_floor_t721_still_applies() -> None:
    """Пол T7.21 (названные источники + 3) действует как прежде и поверх повышения."""
    limits = _v27_limits()
    # пол T7.21 перекрывает и обычный лимит, и повышение: 19 названных источников -> 22
    assert effective_explorer_step_cap(limits, classified=True, named_count=19) == 22
    # обычный вопрос: пол остаётся прежним
    assert effective_explorer_step_cap(limits, classified=False, named_count=18) == 21
    # прежний пол на v26 (16) не сдвинулся
    assert effective_explorer_step_cap(_v26_limits(), classified=False, named_count=18) == 21


def test_cap_default_without_limits_section() -> None:
    assert effective_explorer_step_cap({}, classified=True, named_count=0) == 10
    assert effective_explorer_step_cap({}, classified=True, named_count=8) == 11


# ─── каталог подсказок (session_limits.research_hints) ─────────────────────────


def test_catalog_from_payload_parses_clean() -> None:
    topics = _catalog_topics()
    assert [topic.topic_id for topic in topics] == ["labor_wages", "labor_unemployment", "inflation"]
    wages = topics[0]
    assert {source.domain for source in wages.sources} == {
        "hh.ru",
        "superjob.ru",
        "sberindex.ru",
        "forecast.ru",
    }
    unemployment = topics[1]
    assert {source.domain for source in unemployment.sources} == {
        "hh.ru",
        "superjob.ru",
        "forecast.ru",
        "dcenter.hse.ru",
    }
    inflation = topics[2]
    assert {source.domain for source in inflation.sources} == {
        "interfax.ru",
        "forecast.ru",
        "dcenter.hse.ru",
        "sberbank.com",
        "vtb.ru",
        "alfabank.ru",
        "gazprombank.ru",
        "raiffeisen.ru",
    }
    # основы слов вопроса заданы списком и нормализуются той же функцией (многословная основа
    # «оплат труд» разбирается на отдельные основы)
    assert "оплат труд" in json.loads(V27_PAYLOAD.read_text(encoding="utf-8"))[
        "session_limits"
    ]["research_hints"]["independent_sources"][0]["stems"]
    assert wages.stems == {"зарабо", "зарпла", "доход", "оплат", "труд"}


def test_topic_selection_by_word_stems() -> None:
    topics = _catalog_topics()
    assert [t.topic_id for t in select_hint_topics(C1, topics)] == ["labor_wages"]
    assert [t.topic_id for t in select_hint_topics(C2, topics)] == ["labor_unemployment"]
    assert [t.topic_id for t in select_hint_topics(C3, topics)] == ["inflation"]
    assert select_hint_topics(C5, topics) == []
    # вычислительный вопрос не получает подсказки и в собранном виде (классификация + подбор)
    selected = select_hint_topics(C5, topics) if classify_official_independent(C5) else []
    assert selected == []


def test_hints_section_is_short_and_carries_the_framing() -> None:
    topics = _catalog_topics()
    for question in (C1, C2, C3):
        section = render_independent_hints_section(select_hint_topics(question, topics))
        assert section
        assert len(section) <= RESEARCH_HINTS_SECTION_CHAR_LIMIT
        assert "подсказка, не ограничение" in section
        assert "пересказ официального релиза независимым источником не считается" in section
        lines = section.split("\n")
        assert lines[0] == HINTS_SECTION_HEADER
        assert all(line.startswith("- ") for line in lines[1:])


def test_hints_section_empty_without_topics() -> None:
    assert render_independent_hints_section([]) == ""
    # отсутствие ключа research_hints (config-v26) = пустой каталог без проблем
    topics, problems = parse_independent_hints(_v26_limits())
    assert topics == [] and problems == []
    assert render_independent_hints_section(topics) == ""


def test_hints_section_drops_whole_lines_on_overflow() -> None:
    """Переполнение снимает целые строки с хвоста: обрезанных строк нет (образец секции
    «Недавно недоступны», T7.111 2б)."""
    sources = [
        {"domain": f"h{i:02d}.example", "what": f"что искать на источнике номер {i:02d}"} for i in range(30)
    ]
    catalog = {"research_hints": {"independent_sources": [{"id": "many", "stems": ["текст"], "sources": sources}]}}
    topics, problems = parse_independent_hints(catalog)
    assert problems == []
    section = render_independent_hints_section(topics)
    assert len(section) <= RESEARCH_HINTS_SECTION_CHAR_LIMIT
    lines = section.split("\n")
    assert lines[0] == HINTS_SECTION_HEADER
    assert len(lines) > 2  # пустой секции переполнение не делает
    assert lines[1].endswith("источнике номер 00")  # первые строки целые, они включены
    # хвостовые источники (ближайшие к пределу) вынесены целиком, обрезанных строк нет:
    # каждая включённая строка совпадает со своей исходной строкой каталога знак в знак
    for line in lines[1:]:
        index = int(line.split("номер ")[-1])
        assert line == f"- h{index:02d}.example — что искать на источнике номер {index:02d}"
    included_indexes = {int(line.split("номер ")[-1]) for line in lines[1:]}
    assert max(included_indexes) < 29  # последние источники не вошли: включены не все 30


def test_catalog_validation_names_problems_and_keeps_valid_topics() -> None:
    catalog = {
        "research_hints": {
            "independent_sources": [
                {"id": "good", "stems": ["текст"], "sources": [{"domain": "a.example", "what": "что-то"}]},
                {"stems": ["текст"], "sources": [{"domain": "b.example", "what": "что-то"}]},  # нет id
                {
                    "id": "bad-domain",
                    "stems": ["текст"],
                    "sources": [{"domain": "https://HH.example/", "what": "что-то"}],
                },
                {
                    "id": "extra-key",
                    "stems": ["текст"],
                    "sources": [{"domain": "c.example", "what": "что-то", "extra": 1}],
                },
                {"id": "good", "stems": ["другое"], "sources": [{"domain": "d.example", "what": "что-то"}]},
                {"id": "no-sources", "stems": ["текст"], "sources": []},
            ]
        }
    }
    topics, problems = parse_independent_hints(catalog)
    assert [topic.topic_id for topic in topics] == ["good"]  # валидная тема живёт
    assert len(problems) == 5
    assert any("id" in problem for problem in problems)
    assert any("домен" in problem for problem in problems)
    assert any("повтор id" in problem for problem in problems)
    assert any("ровно domain и what" in problem or "domain и what" in problem for problem in problems)
    assert any("sources" in problem for problem in problems)


def test_catalog_section_shape_problems() -> None:
    topics, problems = parse_independent_hints({"research_hints": "не словарь"})
    assert topics == [] and problems == ["research_hints: не словарь"]
    topics, problems = parse_independent_hints({"research_hints": {}})
    assert topics == []
    assert problems == ["research_hints.independent_sources: ключ отсутствует"]


# ─── валидация необязательных ключей (packages/domain/config.py) ──────────────


def test_optional_key_validation_accepts_v27_and_v26() -> None:
    assert optional_session_limits_problems(_v27_limits()) == []
    assert optional_session_limits_problems(_v26_limits()) == []  # отсутствие ключей = прежнее поведение


def test_optional_key_validation_rejects_broken_values() -> None:
    problems = optional_session_limits_problems(
        {"max_explorer_steps_official_independent": "двадцать", "research_hints": [1, 2]}
    )
    assert len(problems) == 2
    problems = optional_session_limits_problems({"max_explorer_steps_official_independent": 0})
    assert problems == [
        "session_limits.max_explorer_steps_official_independent: должно быть целое положительное число"
    ]
