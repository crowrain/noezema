"""Подбор независимых оценок в карточке ответа (T7.88, ADR-0035 вариант D).

Чистый модуль presentation-слоя: ни базы, ни LLM, ни state. Он переводит уже
вычисленные факты знания (формулировки, записанные группы независимости, записанных
производителей) в человеческие строки подраздела «Независимые оценки» карточки.

Три исхода для каждого утверждения карты с существенными значениями:

1. «независимая оценка того же показателя» — запись знаний того же показателя за
   сопоставимый период, не прогноз, и независимость ВЫВОДИМА из записанных фактов:
   группы независимости не пересекаются (общий снимок) либо записанные производители
   разные. Расхождение значений называется открыто (п.п. для процентов).
2. «другая запись того же показателя» — тот же показатель и период, но независимость
   по записанным группам/производителям не читается (или записи явно из одной
   группы/кластера). Слово «независимая» к такой строке не применяется.
3. если независимых записей того же показателя за этот период нет — честная строка
   «в знаниях системы нет независимых измерений этого показателя за этот период».
   Она не утверждает, что независимых измерений не существует в мире: учёт поиска
   — отдельная задача (T7.89).

Смежный показатель — запись той же карточки с частичным пересечением словаря
показателя (порог ниже гейта T7.83a), но не тот же показатель; прогноз помечается
отдельно и никогда не считается независимым измерением (explorer-v9 правило 12).

Единый различитель показателя — ОДИН на весь проект (ADR-0033 §7,
apps/orchestrator/value_duplicate.py): здесь нет второго словаря.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from apps.orchestrator.value_duplicate import (
    INDICATOR_OVERLAP_MIN,
    IndicatorCheck,
    _territory_group_ids,
    _territory_words,
    indicator_check,
    statement_indicator_words,
    statement_values,
    statement_years,
)
from apps.web.labels import describe

__all__ = [
    "ADJACENT_OVERLAP_MIN",
    "ESTIMATE_ROW_KEYS",
    "ESTIMATE_SECTION_KEYS",
    "ESTIMATE_VALUE_KEYS",
    "MAX_ESTIMATE_ROWS_PER_CLAIM",
    "EstimateCandidate",
    "EstimateRow",
    "EstimateSource",
    "estimate_rows_for_source",
]

# ─── закрытые перечисления подписей (полнота сверяется тестом словаря) ──────

ESTIMATE_ROW_KEYS: tuple[str, ...] = (
    "independent",
    "other_record",
    "adjacent_metric",
    "forecast_adjacent",
    "no_independent",
)
ESTIMATE_VALUE_KEYS: tuple[str, ...] = (
    "value_word",
    "match_with",
    "differ_pp_lead",
    "unit_pp",
    "differ_plain",
    "units_unclear",
)
ESTIMATE_SECTION_KEYS: tuple[str, ...] = ("heading",)

# ─── пороги и потолки (обоснование — ADR-0035 §11, STATUS T7.88) ────────────

# Смежный показатель требует пересечения словаря сильнее шума («росси»+одно общее
# слово даёт ≈1/7), но слабее гейта дублей T7.83a (3/5). На парах стенда .92:
# «наблюдаемая населением инфляция» ↔ официальный ИПЦ = 2/9; прогноз 6,3% ↔
# официальный ИПЦ = 1/5 — обе должны остаться смежными; unrelated-пары (ставка↔ВВП,
# территория+одно слово) — 0…1/7. 1/6 разделяет эти классы.
ADJACENT_OVERLAP_MIN: Fraction = Fraction(1, 6)

MAX_ESTIMATE_ROWS_PER_CLAIM: int = 6

# Прогноз отличаем по основам слов «прогноз/предсказание» (explorer-v9 правило 12:
# прогноз реализованного значения — не независимое измерение). «Инфляционные
# ожидания» так НЕ помечается: опрос ожиданий — измерение ДРУГОГО показателя, и
# его отделяет словарь показателя (смежный), а не метка прогноза.
_FORECAST_STEMS: frozenset[str] = frozenset({"прогн", "предсказ"})

_SNIPPET_MAX_CHARS: int = 80
_SNIPPET_UNSAFE_RE = re.compile(r"(?<!не )\bпроверен|[0-9a-fA-F]{8,}")
_PERCENT_WORD_PREFIX: str = "процент"

_YEAR_SPACING_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class EstimateSource:
    """Утверждение карточки, к которому подбирают оценки. Все поля — записанные факты."""

    claim_id: str
    statement: str
    metric: str | None  # нормализованная метка из assessed_scope текущей головы (или None)
    as_of_year: int | None
    producers: frozenset[str]  # имена производителей из записанных указателей (пусто = не читается)
    snapshot_id: str | None  # снимок независимости текущей оценки этого утверждения
    groups: frozenset[str]  # группы этого снимка, к которым относятся улики утверждения


@dataclass(frozen=True)
class EstimateCandidate:
    """Кандидат из знаний (та же форма записанных фактов)."""

    claim_id: str
    statement: str
    metric: str | None
    as_of_year: int | None
    producers: frozenset[str]
    snapshot_id: str | None
    groups: frozenset[str]


@dataclass(frozen=True)
class EstimateRow:
    """Готовая строка подраздела: kind — ключ словаря, text — цельная фраза сервера."""

    kind: str
    text: str
    candidate_id: str | None


# ─── вспомогательные решения (только по записанным фактам) ──────────────────


def _normalized_words(statement: str) -> frozenset[str]:
    return statement_indicator_words(statement)


def _is_forecast(statement: str) -> bool:
    lowered = statement.lower()
    words = re.findall(r"[a-zа-яё]+", lowered)
    return any(word.startswith(stem) for word in words for stem in _FORECAST_STEMS)


def _is_percent(statement: str) -> bool:
    if "%" in statement:
        return True
    lowered = statement.lower()
    return any(word.startswith(_PERCENT_WORD_PREFIX) for word in re.findall(r"[a-zа-яё]+", lowered))


def _territory_conflict(left: str, right: str) -> bool:
    """Та же conservative-проверка, что в `indicator_check` (тот же словарь территорий)."""
    left_words = _territory_words(left)
    right_words = _territory_words(right)
    if not left_words or not right_words:
        return False
    return not (_territory_group_ids(left_words) & _territory_group_ids(right_words))


def _overlap(left: str, right: str) -> Fraction | None:
    """Доля общего словаря показателя; None — сравнивать нечего (пустой словарь)."""
    left_words = _normalized_words(left)
    right_words = _normalized_words(right)
    if not left_words or not right_words:
        return None
    union = left_words | right_words
    return Fraction(len(left_words & right_words), len(union))


def _period(source: EstimateSource | EstimateCandidate) -> frozenset[int]:
    """Годы формулировки плюс якорная дата — та же семантика периода, что в гейте T7.83a."""
    years = set(statement_years(source.statement))
    if source.as_of_year is not None:
        years.add(source.as_of_year)
    return frozenset(years)


def _periods_comparable(
    source: EstimateSource | EstimateCandidate, candidate: EstimateCandidate
) -> bool:
    """Период сопоставим, когда один целиком накрывает другой (как ⊆ в гейте дублей,
    но зеркально в обе стороны): пересечение «без покрытия» отклоняется —
    conservative-молчание безопаснее ложного сравнения."""
    source_years = _period(source)
    candidate_years = _period(candidate)
    if not source_years or not candidate_years:
        return False
    return candidate_years <= source_years or source_years <= candidate_years


def _group_facts(
    source: EstimateSource, candidate: EstimateCandidate
) -> frozenset[str] | None:
    """Пересечение записанных групп независимости.

    Группы осмысленны только внутри одного снимка: разные снимки — разные картины,
    они не сравниваются (None — «по группам вывести ничего нельзя»).
    """
    if not source.snapshot_id or not candidate.snapshot_id:
        return None
    if source.snapshot_id != candidate.snapshot_id:
        return None
    if not source.groups or not candidate.groups:
        return None
    return source.groups & candidate.groups


def _independence_derivable(
    source: EstimateSource, candidate: EstimateCandidate, same_cluster: bool
) -> bool:
    """Независимость выводится только из положительных записанных фактов:
    непересекающиеся группы общего снимка либо разные записанные производители."""
    if same_cluster:
        return False
    groups = _group_facts(source, candidate)
    if groups is not None and not groups:
        return True
    return bool(source.producers) and bool(candidate.producers)


# ─── человекочитаемая сборка строк (тексты — только из словаря подписей) ─────


def _phrase(key: str) -> str:
    return describe("estimate_value", key)["label"]


def _display_value(canonical: str, percent: bool) -> str:
    text = canonical
    if text.endswith(".0"):
        text = text[:-2]
    text = text.replace(".", ",")
    return f"{text}%" if percent else text


def _display_values(values: frozenset[str], percent: bool) -> str:
    return ", ".join(_display_value(value, percent) for value in sorted(values, key=Decimal))


def _delta_pp(source_value: str, candidate_value: str) -> str:
    delta = abs(Decimal(candidate_value) - Decimal(source_value)).normalize()
    text = format(delta, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} {_phrase('unit_pp')}"


def _values_verdict(source: EstimateSource, candidate: EstimateCandidate) -> str:
    source_percent = _is_percent(source.statement)
    candidate_percent = _is_percent(candidate.statement)
    if source_values_match(source, candidate):
        return (
            f"{_phrase('match_with')} "
            f"{_display_values(statement_values(source.statement), source_percent)}"
        )
    source_values = statement_values(source.statement)
    candidate_values = statement_values(candidate.statement)
    if len(source_values) == 1 and len(candidate_values) == 1:
        (source_value,) = tuple(source_values)
        (candidate_value,) = tuple(candidate_values)
        if source_percent and candidate_percent:
            return f"{_phrase('differ_pp_lead')} {_delta_pp(source_value, candidate_value)}"
        return _phrase("differ_plain")
    return _phrase("units_unclear")


def source_values_match(source: EstimateSource, candidate: EstimateCandidate) -> bool:
    """Значения записей одинаковы (канонические десятичные наборы)."""
    return statement_values(source.statement) == statement_values(candidate.statement)


def _snippet(statement: str) -> str | None:
    """Краткая формулировка записи. Формулировки со словом «проверено» и hex-подобными
    обрывками не цитируются вовсе: подраздел не имеет права утверждать проверку (T7.65)."""
    cleaned = _YEAR_SPACING_RE.sub(" ", statement).strip()
    unsafe = _SNIPPET_UNSAFE_RE.search(cleaned)
    if unsafe is not None:
        return None
    if len(cleaned) <= _SNIPPET_MAX_CHARS:
        return cleaned
    cut = cleaned[:_SNIPPET_MAX_CHARS]
    boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    return f"{cut}…"


def _same_indicator_row(
    source: EstimateSource, candidate: EstimateCandidate, kind: str
) -> EstimateRow | None:
    lead = describe("estimate_row", kind)["label"]
    snippet = _snippet(candidate.statement)
    if snippet is None:  # conservative-исключение (см. _snippet)
        return None
    candidate_percent = _is_percent(candidate.statement)
    value_part = (
        f"{_phrase('value_word')} "
        f"{_display_values(statement_values(candidate.statement), candidate_percent)}"
    )
    verdict = _values_verdict(source, candidate)
    return EstimateRow(
        kind=kind,
        text=f"{lead}: {snippet}, {value_part} — {verdict}",
        candidate_id=candidate.claim_id,
    )


def _adjacent_row(source: EstimateSource, candidate: EstimateCandidate, kind: str) -> EstimateRow | None:
    entry = describe("estimate_row", kind)
    snippet = _snippet(candidate.statement)
    if snippet is None:
        return None
    tail = entry["hint"]
    return EstimateRow(
        kind=kind, text=f"{entry['label']}: {snippet} — {tail}", candidate_id=candidate.claim_id
    )


# ─── основная чистая функция подбора ─────────────────────────────────────────


def estimate_rows_for_source(
    source: EstimateSource, candidates: list[EstimateCandidate]
) -> tuple[EstimateRow, ...]:
    """Строки подраздела «Независимые оценки» для одного утверждения карточки.

    Порядок строк: независимые оценки → другие записи того же показателя → смежные
    показатели → прогнозы; при отсутствии первых двух категорий добавляется честная
    строка «независимых измерений в знаниях нет». кандидаты сортируются по id записи
    (канонический порядок, как в гейте дублей), поэтому набор и порядок детерминированы.
    """
    independent_rows: list[EstimateRow] = []
    other_rows: list[EstimateRow] = []
    adjacent_rows: list[EstimateRow] = []
    forecast_rows: list[EstimateRow] = []
    saw_same_indicator = False

    ordered = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.claim_id != source.claim_id and statement_values(candidate.statement)
        ),
        key=lambda candidate: candidate.claim_id,
    )
    seen_statements: set[str] = set()
    for candidate in ordered:
        if candidate.statement in seen_statements:
            # две записи с одним текстом дают одну и ту же строку подраздела — дубль не нужен
            continue
        snippet = _snippet(candidate.statement)
        if snippet is None:
            # формулировку нельзя процитировать честно (ловушка T7.65) → запись исключается
            continue
        seen_statements.add(candidate.statement)
        check: IndicatorCheck = indicator_check(
            source.statement, candidate.statement, source.metric, candidate.metric
        )
        if _territory_conflict(source.statement, candidate.statement):
            # формулировки называют разные территории: это не измерение того же показателя.
            # (при равных объявленных метках `indicator_check` территорию не смотрит — guard здесь)
            continue
        if check.matched:
            if not _periods_comparable(source, candidate):
                # другой период — не показываем (ADR-0035 §11)
                continue
            if _is_forecast(candidate.statement):
                # прогноз того же показателя — не измерение и не независимая оценка
                row = _adjacent_row(source, candidate, "forecast_adjacent")
                if row is not None:
                    forecast_rows.append(row)
                continue
            saw_same_indicator = True
            groups = _group_facts(source, candidate)
            same_cluster = bool(groups) or bool(source.producers & candidate.producers)
            independent = _independence_derivable(source, candidate, same_cluster)
            kind = "independent" if independent else "other_record"
            row = _same_indicator_row(source, candidate, kind)
            if row is None:
                continue
            if independent:
                independent_rows.append(row)
            else:
                other_rows.append(row)
            continue

        overlap = _overlap(source.statement, candidate.statement)
        if (
            overlap is not None
            and overlap >= ADJACENT_OVERLAP_MIN
            and overlap < INDICATOR_OVERLAP_MIN
        ):
            if _is_forecast(candidate.statement):
                row = _adjacent_row(source, candidate, "forecast_adjacent")
            else:
                row = _adjacent_row(source, candidate, "adjacent_metric")
            if row is not None:
                (forecast_rows if _is_forecast(candidate.statement) else adjacent_rows).append(row)

    rows = list(independent_rows + other_rows + adjacent_rows + forecast_rows)[:MAX_ESTIMATE_ROWS_PER_CLAIM]
    if not saw_same_indicator:
        # Честная строка только когда записей-измерений того же показателя за этот
        # период нет вовсе (прогнозы и смежные показатели её не заменяют и не отменяют).
        entry = describe("estimate_row", "no_independent")
        rows.append(EstimateRow(kind="no_independent", text=entry["hint"], candidate_id=None))
    return tuple(rows)


def estimate_section_heading() -> str:
    return describe("estimate_section", ESTIMATE_SECTION_KEYS[0])["label"]
