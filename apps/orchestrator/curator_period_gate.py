"""T7.121 (части 2–3, ADR-0038): гейты честности формулировки — период и дата прогноза.

Два пооперационных гейта границы куратора (тот же механизм отказа операции, что у
гейта значения на связи T7.108/T7.113 и as_of-гейта T7.84(б): ADR-0034 вариант B,
снятие одной операции с именованной причиной; снятие всех операций — отказ предложения
целиком с аддитивным `curator_reject_kind` и догоняющим раундом исправления варианта C):

1. ГЕЙТ ПЕРИОДА. У операции-наблюдения (`external_fact` / `temporal_fact`) каждый ЯВНЫЙ
   период формулировки — месяц (+год), квартал, полугодие, целевая фраза «по итогам /
   на конец / за N год» и дата — должен встречаться в тексте той же связанной страницы
   (фрагмент улики или сохранённый полный текст), где стоит измеренное значение
   утверждения, в окне `PUBLICATION_WINDOW_CHARS` (= 400 знаков, тот же окно, что у
   вето T7.113 — новых порогов нет) вокруг места значения. Иначе — снятие операции с
   причиной «период … не найден рядом со значением на связанной странице».
   Дата публикации страницы периодом НЕ является: в окне не подтверждают месяц записи
   «день + месяц» («14 октября 2026 года 11:46», «14 октября.» — датировка статьи),
   подтверждают месяц без дня («в октябре 2026», табличные колонки Росстата). Фикстура
   finmarket-6727171: рядом с «2,2%» на странице стоит только датировочная строка
   «14 октября 2026 года 11:46» — период утверждения «в октябре 2026 … 2,2%» страницей
   не подтверждён. Формулировка без явного периода молчит: гейт требует только то,
   что названо.

2. ГЕЙТ ДАТЫ ПРОГНОЗА. Утверждение с маркером прогноза/ожидания (закрытый список
   форм, сверенный со сказуемым ожидания витрины `apps/web/estimates.py`
   `_EXPECTATION_FORMS` и глаголами атрибуции `apps/research_proxy/source_attribution.py`;
   существующие имена показателей — «инфляционные ожидания» — маркером НЕ являются)
   обязано содержать дату, когда прогноз составлен (месяц+год либо дату), ОТЛИЧНУЮ от
   целевого периода («на 2026 год», «по итогам 2026 года» — цель прогноза, не дата),
   ЛИБО явную пометку «дата прогноза на странице не указана». Названная дата проверяется
   на странице так же, как период гейта 1 (здесь датировочная строка страницы ДА
   подтверждает дату: «26 февраля» на странице ЯСИА — дата публикации прогноза ВТБ).

Функции чистые: ни БД, ни LLM (та же дисциплина, что у apps/orchestrator/curator_rejections.py).
Якоря значения здесь ЛОКАЛИЗУЮТ место значения в тексте; подтверждением связи владеет
гейт T7.108 — якорный поиск может быть шире подтверждающего и ничего не подтверждает.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.research_proxy.source_attribution import MAX_SCAN_CHARS, normalize_scan_text
from apps.research_proxy.value_attribution import (
    PUBLICATION_WINDOW_CHARS,
    claim_match_values,
    claim_range_tokens,
)

if TYPE_CHECKING:
    from packages.domain.schemas.staging import ClaimProposal

#: Окно поиска периода вокруг места значения — существующий порог вето T7.113,
#: новых констант не вводилось (`PUBLICATION_WINDOW_CHARS` = 400).
PERIOD_WINDOW_CHARS = PUBLICATION_WINDOW_CHARS

#: Типы операций, которые проверяются гейтами (наблюдение факта). `local_observation`
#: и прочие типы — вне гейта: их период — свой язык формулировки.
GATED_CLAIM_TYPES: frozenset[str] = frozenset({"external_fact", "temporal_fact"})

#: Явная пометка честного молчания гейта даты прогноза (куратор пишет её, когда
#: страница дату не называет; дословная форма закреплена ADR-0038 и промптом curator-v16).
FORECAST_DATE_UNSTATED_PHRASE: str = "дата прогноза на странице не указана"

# ── закрытые словари форм ────────────────────────────────────────────────────

#: Месяцы — закрытые основы словоформ (тот же принцип, что у `_MONTH_PREFIXES` гейта
#: дублей в apps/orchestrator/value_duplicate.py, но с точными основами: там ноябрь
#: покрывается основой «ноев» как маркер периода, а для номера месяца нужна основа
#: «нояб» — иначе «ноевидение» читалось бы ноябрём; и там основам сопоставляется
#: роль маркера, а не номер, поэтому своя карта stem -> номер месяца). Новых
#: нормализующих словарей нет: только основа слова -> номер 1..12.
_MONTH_STEMS: dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "мая": 5,
    "мае": 5,
    "маю": 5,
    "маем": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "нояб": 11,
    "декабр": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTH_STEMS, key=len, reverse=True))
# граница слова обязательна: без неё «независимая» читалась бы как «мая» (замер
# INDEP_STATEMENT в test_relied_claims); длина флексии ограничена — «Маяковского»
# месяцем не считается
_MONTH_WORD_RE = re.compile(
    rf"(?<![а-яё])({_MONTH_ALT})(?:[а-яё]{{1,6}})?(?![а-яё])",
    re.IGNORECASE,
)
_LONGEST_STEMS = sorted(_MONTH_STEMS, key=len, reverse=True)

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

#: «день месяц [года]»: датировочная форма (статья, публикация, релиз).
_DAY_MONTH_RE = re.compile(
    rf"(?<!\d)(\d{{1,2}})[ ]+({_MONTH_ALT})[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_DAY_MONTH_YEAR_DOT_RE = re.compile(r"(?<!\d)(\d{1,2})[.](\d{1,2})[.](\d{4})(?!\d)")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

_QUARTER_WORD_RE = re.compile(
    r"\b(перв[а-яё]*|втор[а-яё]*|трет[ь]?[а-яё]*|четвер[ы]?[а-яё]*|чётвер[ы]?[а-яё]*)[ ]+квартал[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_QUARTER_DIGIT_RE = re.compile(
    r"\b([1-4])[-\s]?й?[ ]+квартал[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_QUARTER_ROMAN_RE = re.compile(
    r"\b(IV|III|II|I)[-\u2010 ]*квартал[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_HALF_WORD_RE = re.compile(
    r"\b(перв[а-яё]*|втор[а-яё]*)[ ]+полугоди[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_HALF_DIGIT_RE = re.compile(
    r"\b([12])[-\s]?е[ ]+полугоди[а-яё]*"
    r"(?:[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*|[ ]+((?:19|20)\d{2}))?",
    re.IGNORECASE,
)

#: Целевые фразы периода: «по итогам N года», «на конец N года», «к концу N года»,
#: «за N год» — это цель наблюдения, а не дата публикации (ADR-0038).
_TARGET_PHRASE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bпо итогам[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*", re.IGNORECASE),
    re.compile(r"\bна конец[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*", re.IGNORECASE),
    re.compile(r"\bк концу[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*", re.IGNORECASE),
    re.compile(r"\bза[ ]+((?:19|20)\d{2})[ ]+год[а-яё]*", re.IGNORECASE),
)

#: Маркеры прогноза — ЗАКРЫТЫЕ формы. Глаголы ожидания/предположения перечислены
#: теми же точными формами, что уже использует витрина (`_EXPECTATION_FORMS` в
#: apps/web/estimates.py — сверка по решению T7.121) плюс глаголы оценки
#: («оценивает…» из перечня основ T7.121). Существительные «ожидания», «оценка» —
#: ИМЁНА измеряемых показателей опросов (инФОМ, Росстат) и маркерами не являются
#: (тот же предел, что у витрины T7.88 и у словаря показателя гейта дублей T7.96).
_FORECAST_VERB_FORMS: frozenset[str] = frozenset(
    {
        "ожидают",
        "ожидает",
        "ожидается",
        "ожидаем",
        "ожидаю",
        "ожидали",
        "ожидал",
        "ожидала",
        "ожидало",
        "предполагают",
        "предполагает",
        "предполагается",
        "предполагали",
        "предполагал",
        "предполагала",
        "предполагало",
        "рассчитывают",
        "рассчитывает",
        "рассчитывали",
        "рассчитывал",
        "рассчитывала",
        "оценивают",
        "оценивает",
        "оценивали",
        "оценивал",
        "оценивала",
        "оценивало",
    }
)
_FORECAST_STEM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bпрогноз[а-яё]*", re.IGNORECASE),  # прогноз/прогнозы/прогнозирует…
    re.compile(r"\bконсенсус[а-яё]*", re.IGNORECASE),  # консенсус/консенсуса/консенсус-прогноз
    re.compile(r"\bпредсказ[а-яё]*", re.IGNORECASE),
)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# ── токены периода формулировки ──────────────────────────────────────────────


@dataclass(frozen=True)
class PeriodToken:
    """Явный период формулировки утверждения.

    `kind`: "month" | "date" | "quarter" | "half" | "target"; `ordinal` — номер
    квартала/полугодия; `label` — поверхность из самой формулировки (для причины отказа).
    """

    kind: str
    month: int | None = None
    day: int | None = None
    year: int | None = None
    ordinal: int | None = None
    label: str = ""

    @property
    def is_forecast_date_capable(self) -> bool:
        """Токен годится как «дата, когда прогноз сделан»: дата либо месяц+год.

        Целевая фраза («по итогам 2026 года») и одиночный месяц без года — нет.
        """
        return self.kind == "date" or (self.kind == "month" and self.year is not None)


def _month_number(word: str) -> int | None:
    lowered = word.lower()
    for stem in _LONGEST_STEMS:
        if lowered.startswith(stem):
            return _MONTH_STEMS[stem]
    return None


def named_month_numbers(text: str) -> frozenset[int]:
    """Номера 1..12 месяцев, названных в тексте словом (T7.121 часть 4, ADR-0038 §4).

    Публичная чистая функция для гейта дублей (`apps/orchestrator/value_duplicate.py`):
    guard названных месяцев не вправе заводить вторую карту основ -> номеров (тот же
    запрет зеркальных словарей, что у `_search_terms`/`content_word_stems`). Датировочные
    строки («14 октября») здесь тоже месяц — консервативное направление guard'а: лишний
    месяц размывает строгую несовместимость месяцев, но не создаёт её.
    """
    return frozenset(
        number
        for match in _MONTH_WORD_RE.finditer(text)
        if (number := _month_number(match.group(1))) is not None
    )


def _match_year(match: re.Match[str]) -> int | None:
    """Год из последней четырёхзначной группы периодных regex."""
    for group in reversed(match.groups()):
        if group is not None and re.fullmatch(r"(?:19|20)\d{2}", group) is not None:
            return int(group)
    return None


def _attach_year_after(text: str, end: int) -> int | None:
    """Год, приписанный к периодному слову вплотную («октябре 2026», «октябре 2026 года»)."""
    match = re.compile(
        r"[ ]*((?:19|20)\d{2})[ ]+год[а-яё]*", re.IGNORECASE
    ).match(text, end, end + 30) or re.compile(r"[ ]*((?:19|20)\d{2})", re.IGNORECASE).match(
        text, end, end + 16
    )
    return None if match is None else int(match.group(1))


def statement_period_tokens(statement: str) -> list[PeriodToken]:
    """Явные периоды формулировки (ADR-0038 §2). Одиночный год без фразы-цели —
    не период («на 2026 год» — цель прогноза; «в 2026 году» без маркера — молчание)."""
    tokens: list[PeriodToken] = []
    consumed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and c_start < end for c_start, c_end in consumed)

    for match in _DAY_MONTH_YEAR_DOT_RE.finditer(statement):
        consumed.append(match.span())
        tokens.append(
            PeriodToken(
                kind="date",
                month=int(match.group(2)),
                day=int(match.group(1)),
                year=int(match.group(3)),
                label=match.group(0).lower(),
            )
        )
    for match in _ISO_DATE_RE.finditer(statement):
        if overlaps(*match.span()):
            continue
        consumed.append(match.span())
        tokens.append(
            PeriodToken(
                kind="date",
                month=int(match.group(2)),
                day=int(match.group(3)),
                year=int(match.group(1)),
                label=match.group(0).lower(),
            )
        )
    for match in _DAY_MONTH_RE.finditer(statement):
        if overlaps(*match.span()):
            continue
        month = _month_number(match.group(2))
        if month is None:
            continue
        consumed.append(match.span())
        year_raw = match.group(3) or match.group(4)
        tokens.append(
            PeriodToken(
                kind="date",
                month=month,
                day=int(match.group(1)),
                year=None if year_raw is None else int(year_raw),
                label=match.group(0).lower(),
            )
        )
    for pattern, flavour in (
        (_QUARTER_ROMAN_RE, "roman"),
        (_QUARTER_WORD_RE, "word"),
        (_QUARTER_DIGIT_RE, "digit"),
    ):
        for match in pattern.finditer(statement):
            if overlaps(*match.span()):
                continue
            consumed.append(match.span())
            tokens.append(
                PeriodToken(
                    kind="quarter",
                    ordinal=_quarter_ordinal(match.group(1), flavour),
                    year=_match_year(match),
                    label=match.group(0).lower(),
                )
            )
    for pattern, flavour in ((_HALF_WORD_RE, "word"), (_HALF_DIGIT_RE, "digit")):
        for match in pattern.finditer(statement):
            if overlaps(*match.span()):
                continue
            consumed.append(match.span())
            first = match.group(1).lower()
            ordinal = 1 if (flavour == "word" and first.startswith("перв")) or first == "1" else 2
            tokens.append(
                PeriodToken(kind="half", ordinal=ordinal, year=_match_year(match), label=match.group(0).lower())
            )
    for pattern in _TARGET_PHRASE_RES:
        for match in pattern.finditer(statement):
            if overlaps(*match.span()):
                continue
            consumed.append(match.span())
            tokens.append(
                PeriodToken(kind="target", year=int(match.group(1)), label=match.group(0).lower())
            )
    for match in _MONTH_WORD_RE.finditer(statement):
        if overlaps(*match.span()):
            continue
        month = _month_number(match.group(0))
        if month is None:
            continue
        consumed.append(match.span())
        year = _attach_year_after(statement, match.end())
        label = match.group(0).lower()
        if year is not None:
            label = f"{label} {year}"
        tokens.append(
            PeriodToken(
                kind="month",
                month=month,
                year=year,
                label=label,
            )
        )
    return tokens


_ROMAN_QUARTER = {"I": 1, "II": 2, "III": 3, "IV": 4}
_ORDINAL_QUARTER_STEMS = (("перв", 1), ("втор", 2), ("трет", 3), ("четвер", 4), ("чётвер", 4))


def _quarter_ordinal(raw: str, flavour: str) -> int | None:
    if flavour == "roman":
        return _ROMAN_QUARTER.get(raw.upper())
    if flavour == "digit":
        return int(raw)
    lowered = raw.lower()
    for stem, ordinal in _ORDINAL_QUARTER_STEMS:
        if lowered.startswith(stem):
            return ordinal
    return None


def has_forecast_marker(statement: str) -> bool:
    """Маркер прогноза/ожидания в формулировке (закрытый список ADR-0038 §3)."""
    lowered = statement.lower()
    if any(pattern.search(lowered) for pattern in _FORECAST_STEM_RES):
        return True
    return any(word in _FORECAST_VERB_FORMS for word in _WORD_RE.findall(lowered))


# ── якоря значения в тексте страницы (локализация, не подтверждение) ─────────

_SCANNED_NUMBER_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ ]\d{3})*(?:[.,]\d+)?)(?!\d)(?![.,]\d)"
)
_SCANNED_RANGE_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ ]\d{3})*(?:[.,]\d+)?)[ ]{0,3}[-\u2013\u2014][ ]{0,3}"
    r"(\d{1,3}(?:[ ]\d{3})*(?:[.,]\d+)?)"
    r"|(?<![\d.,])(\d{1,3}(?:[ ]\d{3})*(?:[.,]\d+)?)[ ]+до[ ]+"
    r"(\d{1,3}(?:[ ]\d{3})*(?:[.,]\d+)?)"
)


def _token_value(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _value_anchor_spans(scanned: str, statement: str) -> list[tuple[int, int]]:
    """Места измеренных значений утверждения в скан-тексте: одиночные числа и
    диапазоны целиком (плюс их концы). Подтверждением связи владеет гейт T7.108;
    здесь якорь только локализует окно периода, поэтому допускает и конец чужого
    диапазона (окно от этого лишь шире — строгость подтверждения не меняется)."""
    match_values = claim_match_values(statement)
    if not match_values:
        return []
    ranges = claim_range_tokens(match_values)
    float_singles = {
        value_float
        for value in (match_values - ranges)
        if (value_float := _token_value(value)) is not None
    }
    range_pairs: list[tuple[float, float]] = []
    for token in ranges:
        left, _, right = token.partition("-")
        left_float, right_float = _token_value(left), _token_value(right)
        if left_float is not None and right_float is not None:
            range_pairs.append((left_float, right_float))
    if not float_singles and not range_pairs:
        return []
    spans: list[tuple[int, int]] = []
    for match in _SCANNED_NUMBER_RE.finditer(scanned):
        value = _token_value(match.group(1))
        if value is not None and value in float_singles:
            spans.append((match.start(), match.end()))
    for match in _SCANNED_RANGE_RE.finditer(scanned):
        for left_group, right_group in ((1, 2), (3, 4)):
            left_raw, right_raw = match.group(left_group), match.group(right_group)
            if left_raw is None or right_raw is None:
                continue
            pair = (_token_value(left_raw), _token_value(right_raw))
            if pair[0] is None or pair[1] is None:
                continue
            if pair in range_pairs:
                spans.append((match.start(0), match.end(0)))
            elif pair[0] in float_singles:
                spans.append((match.start(left_group), match.end(left_group)))
            elif pair[1] in float_singles:
                spans.append((match.start(right_group), match.end(right_group)))
    return sorted(set(spans))


def _windows(scanned: str, statement: str) -> list[tuple[int, int]]:
    """Окна ±`PERIOD_WINDOW_CHARS` вокруг каждого места значения. Пусто, если значения
    утверждения в этом тексте нет: текст без значения не подтверждает период (ADR-0038 §2;
    тот же предел, что у гейта значения T7.108)."""
    return [
        (max(0, start - PERIOD_WINDOW_CHARS), min(len(scanned), end + PERIOD_WINDOW_CHARS))
        for start, end in _value_anchor_spans(scanned, statement)
    ]


# ── сторона страницы: периодные формы ────────────────────────────────────────


def _month_occurrences(scanned: str) -> list[tuple[int, int, int, bool]]:
    """(start, end, месяц, stamped) по всем месяцам текста. stamped — «день + месяц»
    (датировочная запись страницы, T7.121: публикация периодом не является)."""
    found: list[tuple[int, int, int, bool]] = []
    for match in _MONTH_WORD_RE.finditer(scanned):
        month = _month_number(match.group(0))
        if month is None:
            continue
        date_match = _DAY_MONTH_RE.search(scanned, max(0, match.start() - 8), match.end())
        stamped = date_match is not None and date_match.start() < match.start() and date_match.end() >= match.end()
        found.append((match.start(), match.end(), month, stamped))
    return found


def _year_occurrences(scanned: str) -> list[tuple[int, int, int]]:
    """(start, end, год)."""
    return [(match.start(), match.end(), int(match.group(0))) for match in _YEAR_RE.finditer(scanned)]


def _date_occurrences(scanned: str) -> list[tuple[int, int, int, int, int | None]]:
    """(start, end, день, месяц, явный год|None) — «день месяц [года]», «д.м.гггг», «гггг-м-д»."""
    found: list[tuple[int, int, int, int, int | None]] = []
    for match in _DAY_MONTH_YEAR_DOT_RE.finditer(scanned):
        found.append((match.start(), match.end(), int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _ISO_DATE_RE.finditer(scanned):
        found.append((match.start(), match.end(), int(match.group(3)), int(match.group(2)), int(match.group(1))))
    for match in _DAY_MONTH_RE.finditer(scanned):
        month = _month_number(match.group(2))
        if month is None:
            continue
        year_raw = match.group(3) or match.group(4)
        found.append(
            (match.start(), match.end(), int(match.group(1)), month, None if year_raw is None else int(year_raw))
        )
    return found


def _range_month_spans(scanned: str) -> list[tuple[int, int, int, int]]:
    """Пара месяцев через соединитель («январь–июнь», «с января по март»): span + границы."""
    spans: list[tuple[int, int, int, int]] = []
    for match in re.finditer(
        rf"(?<![\wа-яё])({_MONTH_ALT})[а-яё]*[ ]{{0,3}}[-\u2013\u2014][ ]{{0,3}}({_MONTH_ALT})[а-яё]*"
        rf"|(?<![\wа-яё])({_MONTH_ALT})[а-яё]*[ ]+по[ ]+({_MONTH_ALT})[а-яё]*",
        scanned,
        re.IGNORECASE,
    ):
        left_raw = match.group(1) or match.group(3)
        right_raw = match.group(2) or match.group(4)
        left, right = _month_number(left_raw), _month_number(right_raw)
        if left is None or right is None:
            continue
        spans.append((match.start(), match.end(), left, right))
    return spans


def _period_name_occurrences(scanned: str, kind: str) -> list[tuple[int, int, int | None]]:
    """Названные кварталы/полугодия на странице: (start, end, ordinal)."""
    patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        ((_QUARTER_ROMAN_RE, "roman"), (_QUARTER_WORD_RE, "word"), (_QUARTER_DIGIT_RE, "digit"))
        if kind == "quarter"
        else ((_HALF_WORD_RE, "word"), (_HALF_DIGIT_RE, "digit"))
    )
    found: list[tuple[int, int, int | None]] = []
    for pattern, flavour in patterns:
        for match in pattern.finditer(scanned):
            if kind == "quarter":
                ordinal = _quarter_ordinal(match.group(1), flavour)
            else:
                first = match.group(1).lower()
                ordinal = 1 if (flavour == "word" and first.startswith("перв")) or first == "1" else 2
            if ordinal is None:
                continue
            found.append((match.start(), match.end(), ordinal))
    return found


def _span_in_windows(span: tuple[int, int], windows: list[tuple[int, int]]) -> bool:
    return any(span[0] >= window_start and span[1] <= window_end for window_start, window_end in windows)


def _has_year_in_window(scanned: str, windows: list[tuple[int, int]], year: int) -> bool:
    return any(
        match_start >= window_start and match_end <= window_end
        for window_start, window_end in windows
        for match_start, match_end, match_year in _year_occurrences(scanned)
        if match_year == year
    )


def _token_months(token: PeriodToken) -> tuple[int, ...]:
    if token.kind == "quarter":
        ordinal = token.ordinal or 1
        return tuple(range(3 * ordinal - 2, 3 * ordinal + 1))
    return (1, 2, 3, 4, 5, 6) if token.ordinal == 1 else (7, 8, 9, 10, 11, 12)


def _period_token_found(token: PeriodToken, scanned: str, windows: list[tuple[int, int]]) -> bool:
    """Найдено ли подтверждение токена периода в окнах значений того же текста."""
    if token.kind == "month":
        for start, end, month, stamped in _month_occurrences(scanned):
            if month != token.month or stamped:
                continue  # «14 октября» — датировка статьи, не период значения
            if _span_in_windows((start, end), windows) and (
                token.year is None or _has_year_in_window(scanned, windows, token.year)
            ):
                return True
        return False
    if token.kind == "date":
        for start, end, day, month, explicit_year in _date_occurrences(scanned):
            if day != token.day or month != token.month:
                continue
            if token.year is not None and explicit_year is not None and explicit_year != token.year:
                continue
            if not _span_in_windows((start, end), windows):
                continue
            if token.year is not None and explicit_year is None and not _has_year_in_window(
                scanned, windows, token.year
            ):
                continue
            return True
        return False
    if token.kind in ("quarter", "half"):
        months = _token_months(token)
        for start, end, ordinal in _period_name_occurrences(scanned, token.kind):
            if ordinal != token.ordinal:
                continue
            if _span_in_windows((start, end), windows) and (
                token.year is None or _has_year_in_window(scanned, windows, token.year)
            ):
                return True
        for start, end, left, right in _range_month_spans(scanned):
            if not (left <= min(months) and max(months) <= right):
                continue
            if _span_in_windows((start, end), windows) and (
                token.year is None or _has_year_in_window(scanned, windows, token.year)
            ):
                return True
        months_in_window = {
            month
            for start, end, month, stamped in _month_occurrences(scanned)
            if not stamped and _span_in_windows((start, end), windows)
        }
        return bool(
            set(months) <= months_in_window
            and (token.year is None or _has_year_in_window(scanned, windows, token.year))
        )
    if token.kind == "target":
        return token.year is not None and _has_year_in_window(scanned, windows, token.year)
    return False


# ── entry point: пооперационные отказы ───────────────────────────────────────


def _reject_period(labels: Sequence[str]) -> str:
    quoted = "», «".join(labels)
    return (
        f"период «{quoted}» не найден рядом со значением на связанной странице — "
        "возьми период из текста или напиши, что период не указан"
    )


def _reject_forecast_date() -> str:
    return (
        "утверждение с прогнозом или ожиданием обязано называть дату, когда прогноз сделан "
        f"(месяц и год либо дату), или писать «{FORECAST_DATE_UNSTATED_PHRASE}»"
    )


def claim_period_rejections(
    *,
    claims: Sequence[ClaimProposal],
    texts_by_claim: Mapping[int, Sequence[str]],
    already_dropped: Collection[int],
) -> dict[int, str]:
    """Индекс операции -> именованная причина снятия гейтами периода/даты прогноза.

    `texts_by_claim` — тексты оставшихся `supports`-связей `source_assertion`
    операции (фрагмент улики и сохранённый полный текст страницы). Снятые ранее
    операции и перепроверки не проверяются (у снятой уже есть причина; перепроверку
    ведёт якорь). Причина снятия одна: сначала называется пропущенная дата прогноза,
    затем периоды, которых нет рядом со значением. Формулировка без явного периода
    и без маркера прогноза молчит (ADR-0038 §2–§3).
    """
    rejections: dict[int, str] = {}
    for index, claim in enumerate(claims):
        if index in already_dropped or claim.existing_claim_id is not None:
            continue
        if str(claim.claim_type.value) not in GATED_CLAIM_TYPES:
            continue
        statement = str(claim.statement or "")
        if not statement:
            continue
        tokens = statement_period_tokens(statement)
        lowered = statement.lower()
        texts = [
            normalize_scan_text(text)[:MAX_SCAN_CHARS] for text in texts_by_claim.get(index, ()) if text
        ]
        # тексты, где измеренного значения утверждения нет, период не подтверждают;
        # утверждение вовсе без измеренного значения — вне гейта периода (T7.108)
        usable = [scanned for scanned in texts if _windows(scanned, statement)]
        problems: list[str] = []
        if (
            has_forecast_marker(statement)
            and FORECAST_DATE_UNSTATED_PHRASE not in lowered
            and not any(token.is_forecast_date_capable for token in tokens)
        ):
            problems.append(_reject_forecast_date())
        missing = [
            token.label
            for token in tokens
            if usable
            and not any(
                _period_token_found(token, scanned, _windows(scanned, statement)) for scanned in usable
            )
        ]
        if missing:
            problems.append(_reject_period(missing[:3]))
        if problems:
            # та же форма строки журнала снятых операций, что у гейта значения и as_of (T7.105)
            rejections[index] = f"claim[{index}]: " + "; ".join(problems)
    return rejections
