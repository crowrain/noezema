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

Частичное пересечение словаря показателя (порог ниже гейта T7.83a) НЕ доказывает, что
показатель другой: витрина не имеет права это утверждать (T7.88a, ADR-0035 §12). Такая
запись либо получает честную категорию «вероятно, тот же показатель» — когда период
совместим и значение является округлением значения карточки до меньшего числа знаков
(или совпадает) — либо остаётся нейтральной «связанной записью» без утверждений о
показателе и о значении. Различие показателя признаётся только по записанным меткам
(`assessed_scope`): когда обе оценки объявили разные метки, `indicator_check` молчит
намеренно и витрина молчит вместе с ним. Прогноз помечается отдельно и никогда не
считается независимым измерением (explorer-v9 правило 12).

Связанных строк (вероятно тот же показатель / прогноз / связанная запись) — не больше
`MAX_ADJACENT_ROWS_PER_CLAIM`; остаток сворачивается в одну строку со счётчиком. Строка
«независимых измерений в знаниях нет» обязательна, когда в подразделе нет ни одной
независимой оценки, стоит последней и не вытесняется никаким потолком.

Прогнозом считается УТВЕРЖДЕНИЕ, у которого все существенные значения прогнозны (T7.88b,
ADR-0035 §13). Роль разбирается по каждому значению: основа прогноза должна относиться к нему
(стоять перед ним в том же отрезке, не дальше `FORECAST_BASIS_WINDOW_CHARS`, либо вести весь
отрезок из своей головы), а маркер факта перед значением снимает прогнозную атрибуцию. Значение,
к которому основа прогноза не относится, — реализованное: именно им запись и сопоставляется,
прогнозные числа при сравнении с исходным значением игнорируются («Фактическая годовая инфляция
… (5,59%) оказалась ниже всех прогнозов аналитиков: …» — это измерение 5,59 %, а не прогноз).

Признаками прогноза у значения, помимо явной основы «прогн*»/«предсказ*», считаются сказуемое
ожидания про само значение («аналитики ожидают, что инфляция составит 4,5%», «ожидаемая инфляция на
2026 год — 4,5%») и будущая форма глагола-сказуемого этого значения («составит», «достигнет»,
«вырастет»): это закрытые списки точных форм ниже. Прошедшее и настоящее время («составила»,
«составляет», «оказалась») признаками не считаются: роль различается формой глагола, а не его
основой — стем дал бы ровно прежний дефект (T7.88b). Измерение чужих ожиданий —
показатель опроса, а не прогноз реализованного значения: обороты вида «инфляционные ожидания
населения», «ожидаемая населением инфляция», «население ожидает» прогнозную атрибуцию НЕ создают
(`_MEASURED_EXPECTATION_RE`); явная основа прогноза и будущее время остаются признаками и внутри
такого оборота (ADR-0035 §13.7).

Единый различитель показателя — ОДИН на весь проект (ADR-0033 §7,
apps/orchestrator/value_duplicate.py): здесь нет второго словаря.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction

from apps.orchestrator.value_duplicate import (
    _VALUE_TOKEN_RE,
    INDICATOR_OVERLAP_MIN,
    IndicatorCheck,
    _canonical_value,
    _normalize_text,
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
    "FORECAST_BASIS_WINDOW_CHARS",
    "FORECAST_HEAD_CHARS",
    "MAX_ADJACENT_ROWS_PER_CLAIM",
    "MAX_ESTIMATE_ROWS_PER_CLAIM",
    "ROUNDING_MAX_PLACE_GAP",
    "EstimateCandidate",
    "EstimateRow",
    "EstimateSource",
    "ValueRoles",
    "estimate_rows_for_source",
    "value_roles",
]

# ─── закрытые перечисления подписей (полнота сверяется тестом словаря) ──────

ESTIMATE_ROW_KEYS: tuple[str, ...] = (
    "independent",
    "other_record",
    "likely_same_indicator",
    "forecast_adjacent",
    "adjacent_metric",
    "related_overflow",
    "no_independent",
    "independent_search_done",
    "independent_search_not_done",
)
ESTIMATE_VALUE_KEYS: tuple[str, ...] = (
    "value_word",
    "match_with",
    "after_rounding",
    "differ_pp_lead",
    "unit_pp",
    "differ_plain",
    "units_unclear",
    "search_sessions_scope",
    "search_was",
    "search_queries_one",
    "search_queries_few",
    "search_queries_many",
    "search_queries_more",
    "search_measurements_absent",
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

# T7.88a (ADR-0035 §12): связанных строк не больше трёх. На карте 7ceb8047 против
# `9266248e` семь связанных записей (в том числе три пересказа одного и того же
# 5,6%); против `391c4388` — тоже семь: подраздел превращался в простой перечень.
# Три строки — максимум, при котором читатель видит и вероятный пересказ,
# и прогноз, и нейтрально связанную запись; остальное сворачивается в одну строку со
# счётчиком (тексты записей там не нужны: они ничего не утверждают). Остаток считается
# по каноническому порядку id, поэтому набор детерминирован.
MAX_ADJACENT_ROWS_PER_CLAIM: int = 3

# Округление засчитывается только если грубая запись получена из тонкой округлением до
# её числа знаков (5,59 → 5,6) и разница не больше двух знаков (порог зафиксирован
# константой): 5,587 ↔ 5,6 — да, 5,58715 ↔ 5,59 — нет (сворачивать пять знаков в два
# значит «совпало» на глаз уже нельзя). Сравниваются записи ровно с одним значением:
# если значений несколько, витрина не знает, какое относится к показателю карточки.
ROUNDING_MAX_PLACE_GAP: int = 2

# Прогноз отличаем по основам слов «прогноз/предсказание» (explorer-v9 правило 12:
# прогноз реализованного значения — не независимое измерение). С T7.88b эти основы относятся к
# ЗНАЧЕНИЮ, а не к формулировке целиком (ADR-0035 §13): «Фактическая инфляция … (5,59%) оказалась
# ниже всех прогнозов» — измерение 5,59%, а не прогноз. «Инфляционные ожидания» так НЕ помечается:
# опрос ожиданий — измерение ДРУГОГО показателя, и его отделяет словарь показателя (смежный),
# а не метка прогноза.
_FORECAST_STEMS: frozenset[str] = frozenset({"прогн", "предсказ"})

# Маркер факта: ближайший перед значением — снимает прогнозную атрибуцию (в `7cc2b578` между
# «Фактическая» и 5,59% основы прогноза нет, а основа «ниже всех прогнозов аналитиков» стоит
# ПОСЛЕ значения). Особые глаголы маркерами не считаются: «составил» бывает и у прогноза
# («прогноз аналитиков … составил 6,3%»).
_REALIZED_STEMS: frozenset[str] = frozenset({"факт"})

# ─── T7.94 (дополнение ADR-0035 §13.7): ожидание и будущее время тоже признаки прогноза ──
# Прогноз высказан и тогда, когда его не называют прогнозом: «Аналитики ожидают, что инфляция в
# 2026 году составит 4,5%» даёт роль значения «фактическое», и запись может попасть в
# «Независимую оценку того же показателя». Признак — сказуемое ожидания про само значение либо
# будущая форма глагола-сказуемого этого значения. ЗАКРЫТЫЕ СПИСКИ ТОЧНЫХ ФОРМ, а не стемы:
# «составила»/«составляет»/«оказалась» (прошедшее и настоящее) — измерение, «составит»/«окажется»
# (будущее) — прогноз. Стем («состав», «каз») схлопнул бы эти формы и вернул дефект T7.88a/T7.88b:
# в подлинной формулировке `7cc2b578` стояло «оказалась» — измерение.

#: Сказуемое ожидания. Прошедшее время входит: ожидание, высказанное раньше («аналитики ожидали
#: инфляцию 4,5%»), — всё ещё прогноз реализованного значения, а не его измерение.
_EXPECTATION_FORMS: frozenset[str] = frozenset({
    "ожидают", "ожидает", "ожидается", "ожидаем", "ожидаю",
    "ожидали", "ожидал", "ожидала", "ожидало",
    "предполагают", "предполагает", "предполагается", "предполагали", "предполагал", "предполагала",
    "рассчитывают", "рассчитывает", "рассчитывали", "рассчитывал", "рассчитывала",
    "допускают", "допускает", "допускается", "допускали", "допускал", "допускала",
})

#: Причастная основа того же сказуемого: «ожидаемая инфляция на 2026 год — 4,5%» — prediction,
#: свёрнутая в определение числа. Существительное «ожидания» признаком НЕ является: на живых
#: страницах и в опросах это ИМЯ измеряемого показателя («Инфляционные ожидания населения —
#: 13,7%» — показания инФОМ), а не предсказание значения; там же, где «по ожиданиям» действительно
#: означает прогноз, рядом всегда стоит сказуемое ожидания или будущая форма глагола.
_EXPECTATION_STEMS: frozenset[str] = frozenset({"ожидаем", "предполагаем"})

#: Будущие формы глаголов, которые сказуемым относятся к самому значению показателя: совершенный
#: вид («составит», «достигнет») и аналитическое будущее («будет», «будут»). Единственное и
#: множественное число перечислены обеими формами: «инфляция снизится» и «цены снизятся». Прошедшие
#: и настоящие формы того же глагола в список НЕ входят — они оставляют значение реализованным.
_FUTURE_FORMS: frozenset[str] = frozenset({
    "составит", "составят", "будет", "будут",
    "достигнет", "достигнут", "превысит", "превысят",
    "снизится", "снизятся", "замедлится", "замедлятся", "ускорится", "ускорятся",
    "вырастет", "вырастут", "окажется", "окажутся", "сохранится", "сохранятся",
})

#: Измерение чужих ожиданий — показания опроса, а не прогноз реализованного значения: explorer-v9
#: правило 12 перечисляет «наблюдаемая и воспринимаемая инфляция по опросам домохозяйств» именно как
#: независимое ИЗМЕРЕНИЕ. Такой оборот снимает атрибуцию ожидания с самого оборота («инфляционные
#: ожидания населения», «ожидаемая населением инфляция», «население ожидает» — инФОМ), но НЕ снимает
#: явную основу прогноза и будущее время: «Прогноз аналитиков по инфляционным ожиданиям населения на
#: 2026 год — 13,7%» и «Ожидаемая населением инфляция в России вырастет до 12,4%» остаются прогнозами
#: (о значении показателя опроса). Замер по подлинным формулировкам дампа claims_live.tsv: `391c4388`
#: («Наблюдаемая населением годовая инфляция … составила 14,5%») и `6d9a12ff` («… наблюдаемой
#: инфляции населением … составила 13,1% … 15,6%») — измерение и до правки, и после неё. Снятие
#: атрибуции не перекрашивает запись в независимую оценку: отношение к показателю по-прежнему решает
#: различитель T7.83a, а не эта регулярка.
_MEASURED_EXPECTATION_RE = re.compile(
    r"(?:ожидани|ожидаем|ожидающ)[а-яё]*\s+(?:населени|домохозяйств|респондент)[а-яё]*"
    r"|(?:населени|домохозяйств|респондент)[а-яё]*\s+"
    r"(?:ожидани|ожидаем|ожидающ|ожидае[пт]|ожидал|ожидаю)[а-яё]*"
)

# Сильные границы отрезка, внутри которого разбирается роль значения. Двоеточие и запятая
# границей НЕ считаются: так устроены стендовые формулировки — «Прогноз: годовая инфляция … —
# 6,3%» и «прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%» относятся к одному отрезку.
_CLAUSE_BREAK_CHARS: frozenset[str] = frozenset({".", ";", "!", "?", "\n"})

# Расстояние, на котором основа прогноза ещё относится к значению. Замер по подлинным
# формулировкам карты 7ceb8047 (fixtures-t788a/claims_live.tsv): «консенсус-прогноз ЦБ — 6,6%» —
# 6 знаков до значения, «прогноз Банка России — 6,5–7%» — 16, продолжение того же перечисления
# «Минэкономразвития — 6,8%» — 44; ближайший маркер факта в этой же формулировке («Фактическая
# …») — 48 знаков. 60 покрывает все настоящие атрибуции и не даёт основе прогноза из другой
# части предложения присвоить себе чужое значение: без окна одно слово «прогноз» делало бы
# прогнозом всю запись (дефект живого экрана, найденный после T7.88a).
FORECAST_BASIS_WINDOW_CHARS: int = 60

# Расстояние от начала отрезка, на котором основа прогноза ведёт весь отрезок: «Прогноз
# аналитиков … составил 6,3%» — основа в самом начале (0 знаков), значение через 132 знака, окном
# не покрыть. Второй путь присваивает роль только если между основой и значением нет другого
# существенного значения — иначе это перечисление либо уже другое измерение.
FORECAST_HEAD_CHARS: int = 12

_MARKER_WORD_RE: re.Pattern[str] = re.compile(r"[a-zа-яё]+")

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
    # T7.89 (ADR-0035 §14): тексты записанных запросов с признаком поиска независимых оценок
    # в сессиях этого утверждения (журнал `action_started`/web.search). None — связных сессий
    # у утверждения не найдено (о поиске ничего не утверждаем); пустой кортеж — сессии есть,
    # записанного поиска независимых оценок в них нет.
    independent_searches: tuple[str, ...] | None = None


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


@dataclass(frozen=True)
class ValueRoles:
    """Роли существенных десятичных значений одной формулировки (T7.88b, ADR-0035 §13).

    `realized` — значения, записанные как реализованные (измерения); `forecast` — значения,
    записанные как прогноз; `ambiguous` — одно и то же число в формулировке записано и так, и
    так: роль такому значению не приписывается произвольно, и оно не сопоставляется.
    """

    realized: frozenset[str]
    forecast: frozenset[str]
    ambiguous: frozenset[str]

    @property
    def is_forecast_only(self) -> bool:
        """Утверждение — прогноз только когда ВСЕ его существенные значения прогнозны."""
        return bool(self.forecast) and not self.realized and not self.ambiguous


# ─── вспомогательные решения (только по записанным фактам) ──────────────────


def _normalized_words(statement: str) -> frozenset[str]:
    return statement_indicator_words(statement)


def _normalized_copy(statement: str) -> str:
    """Копия формулировки для разбора (живые страницы набраны NBSP и узким пробелом, T7.77).

    Нормализация — ровно та же функция, что использует `statement_values` и словарь основ
    (`value_duplicate._normalize_text`), поэтому позиции значений в копии совпадают с теми
    значениями, которые витрина потом сопоставляет. Хранимая формулировка и её хеш не меняются:
    разбор идёт по копии, lowercase длины не меняет.
    """
    return _normalize_text(statement).lower()


def _value_occurrences(text: str) -> tuple[tuple[int, int, str], ...] | None:
    """Вхождения существенных значений с позициями; `None` — нечитаемый числовой ряд.

    Тот же скан и та же каноизация, что у `statement_values` (ADR-0033 §7): роли разбираются
    ровно по тем значениям, которые витрина потом сопоставляет. Диапазон «6,5–7%» даёт ОДНО
    вхождение — 6,5; целое «7» после тире значением не считается (то же, что у гейта дублей).
    """
    occurrences: list[tuple[int, int, str]] = []
    for match in _VALUE_TOKEN_RE.finditer(text):
        canonical = _canonical_value(match.group(1))
        if canonical is None:
            return None
        occurrences.append((match.start(), match.end(), canonical))
    return tuple(occurrences)


def _role_markers(text: str) -> tuple[tuple[int, int, bool], ...]:
    """Основы прогноза и маркеры факта с позициями (основа прогноза = `True`).

    Признак прогноза (T7.88b + T7.94): явная основа «прогн*»/«предсказ*»; сказуемое ожидания про само
    значение (`_EXPECTATION_FORMS` и причастная основа `_EXPECTATION_STEMS`); будущая форма
    глагола-сказуемого этого значения (`_FUTURE_FORMS`). Прошедшие и настоящие формы («составила»,
    «составляет», «оказалась», «указана как», «приведена как») — способ записи числа, а не способ
    атрибуции (T7.85): маркерами не считаются. Маркер факта ближе к значению, чем основа прогноза, —
    значение реализованное: «… ниже всех прогнозов аналитиков» после 5,59% не делает измерение
    прогнозом. Обороты измерения чужих ожиданий (`_MEASURED_EXPECTATION_RE`) не дают атрибуции
    ожидания вовсе: показания опроса — уже состоявшийся факт, а не предсказание значения.
    """
    measured = tuple(_MEASURED_EXPECTATION_RE.finditer(text))

    def expectation_measured(start: int) -> bool:
        return any(match.start() <= start < match.end() for match in measured)

    markers: list[tuple[int, int, bool]] = []
    for match in _MARKER_WORD_RE.finditer(text):
        word = match.group(0)
        explicit_basis = any(word.startswith(stem) for stem in _FORECAST_STEMS)
        expectation_basis = not expectation_measured(match.start()) and (
            word in _EXPECTATION_FORMS
            or word in _FUTURE_FORMS
            or any(word.startswith(stem) for stem in _EXPECTATION_STEMS)
        )
        if explicit_basis or expectation_basis:
            markers.append((match.start(), match.end(), True))
        elif any(word.startswith(stem) for stem in _REALIZED_STEMS):
            markers.append((match.start(), match.end(), False))
    return tuple(markers)


def _clause_start(text: str, position: int) -> int:
    """Начало отрезка, в котором разбирается роль значения: после последней сильной границы."""
    start = 0
    for index, char in enumerate(text[:position]):
        if char in _CLAUSE_BREAK_CHARS:
            start = index + 1
    return start


def value_roles(statement: str) -> ValueRoles:
    """Разделение значений формулировки на реализованные и прогнозные (ADR-0035 §13).

    Значение прогнозное, если ближайший маркер ПЕРЕД ним в том же отрезке — основа прогноза и
    стоит не дальше `FORECAST_BASIS_WINDOW_CHARS` («прогноз Банка России — 6,5–7%»,
    «консенсус-прогноз ЦБ — 6,6%»). Второй путь: основа стоит в голове отрезка (не дальше
    `FORECAST_HEAD_CHARS` от его начала) и между ней и значением нет другого существенного
    значения («Прогноз аналитиков … составил 6,3%» — 132 знака между основой и значением). Оба пути
    одинаковы для всех признаков прогноза (T7.88b + T7.94): явной основы, сказуемого ожидания и
    будущего времени глагола-сказуемого значения — см. `_role_markers`. Всё остальное —
    реализованное значение: перед ним маркер факта («фактическ*»), маркеров в отрезке нет вовсе
    (нейтральная формулировка измерения, в том числе показания опроса ожиданий), либо основа прогноза
    относится к другому значению.
    """
    text = _normalized_copy(statement)
    occurrences = _value_occurrences(text)
    if occurrences is None or not occurrences:
        # Нечитаемый числовой ряд либо только годы и целые: ролей разбирать нечего.
        return ValueRoles(frozenset(), frozenset(), frozenset())
    markers = _role_markers(text)
    roles: dict[str, set[bool]] = {}
    for start, _end, canonical in occurrences:
        clause_start = _clause_start(text, start)
        before = [marker for marker in markers if marker[1] <= start and marker[0] >= clause_start]
        near = [marker for marker in before if start - marker[1] <= FORECAST_BASIS_WINDOW_CHARS]
        if near:
            is_forecast = near[-1][2]
        elif (
            before
            and before[0][0] - clause_start <= FORECAST_HEAD_CHARS
            and not any(other[1] <= start and other[0] >= before[0][1] for other in occurrences)
        ):
            is_forecast = before[0][2]
        else:
            is_forecast = False
        roles.setdefault(canonical, set()).add(is_forecast)
    realized = frozenset(value for value, flags in roles.items() if flags == {False})
    forecast = frozenset(value for value, flags in roles.items() if flags == {True})
    ambiguous = frozenset(value for value, flags in roles.items() if len(flags) > 1)
    return ValueRoles(realized, forecast, ambiguous)


def _comparison_values(statement: str) -> frozenset[str]:
    """Значения, которыми витрина сопоставляет запись (ADR-0035 §13): только реализованные.

    Прогнозные числа при сравнении с исходным значением игнорируются. Если реализованных нет
    вовсе и неоднозначных тоже — запись целиком прогнозная: прежний набор значений сохраняет
    поведение T7.88/T7.88a для прогнозов (кандидаты-прогнозы всё равно не доходят до
    сравнения значений: они идут в строку «смежный прогноз»). Неоднозначные значения никогда не
    сопоставляются: витрина не решает, к какой из двух ролей относится число.
    """
    roles = value_roles(statement)
    if roles.realized or roles.ambiguous:
        return roles.realized
    return statement_values(statement)


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
            f"{_display_values(_comparison_values(source.statement), source_percent)}"
        )
    source_values = _comparison_values(source.statement)
    candidate_values = _comparison_values(candidate.statement)
    if len(source_values) == 1 and len(candidate_values) == 1:
        (source_value,) = tuple(source_values)
        (candidate_value,) = tuple(candidate_values)
        if source_percent and candidate_percent:
            return f"{_phrase('differ_pp_lead')} {_delta_pp(source_value, candidate_value)}"
        return _phrase("differ_plain")
    return _phrase("units_unclear")


def source_values_match(source: EstimateSource, candidate: EstimateCandidate) -> bool:
    """Значения записей одинаковы (канонические наборы СОПОСТАВЛЯЕМЫХ значений, §13).

    У смешанной записи сравнивается её реализованное значение: прогнозная часть формулировки в
    сравнение не входит (`_comparison_values`). Пустой набор сопоставлять нельзя: у записи, чьи значения
    только неоднозначны, витрина не знает ни одного значения — «совпадает» она утверждать не имеет права
    (иначе строка сказала бы «значение — совпадает с» без чисел).
    """
    source_values = _comparison_values(source.statement)
    candidate_values = _comparison_values(candidate.statement)
    if not source_values or not candidate_values:
        return False
    return source_values == candidate_values


def _decimal_places(value: str) -> int:
    """Число значащих знаков после запятой у канонического значения ('5.59' → 2, '14.5' → 1)."""
    exponent = Decimal(value).as_tuple().exponent
    if not isinstance(exponent, int):  # NaN/Infinity: знаков после запятой нет — сравнение не здесь
        return 0
    return -exponent if exponent < 0 else 0


def _rounding_agreement(source: EstimateSource, candidate: EstimateCandidate) -> str | None:
    """Совпадает ли значение кандидата со значением карточки: «equal», «rounded» или None.

    Только для записей ровно с одним СОПОСТАВЛЯЕМЫМ значением (см. `_comparison_values`): у
    смешанной записи это её реализованное значение, прогнозные числа в сравнение не входят.
    Если значений несколько, витрина не знает, какое из них относится к показателю карточки, и
    не имеет права называть запись тем же измерением (conservative-молчание). Округление
    принимается, когда грубая запись получается из тонкой округлением до её числа знаков
    (ROUND_HALF_UP, как печатают источники) и разница в числе знаков не больше
    ROUNDING_MAX_PLACE_GAP.
    """
    source_values = _comparison_values(source.statement)
    candidate_values = _comparison_values(candidate.statement)
    if len(source_values) != 1 or len(candidate_values) != 1:
        return None
    (source_value,) = tuple(source_values)
    (candidate_value,) = tuple(candidate_values)
    source_decimal = Decimal(source_value)
    candidate_decimal = Decimal(candidate_value)
    if _is_percent(source.statement) != _is_percent(candidate.statement):
        # разная единица измерения: сравнивать числа нельзя (conservative-молчание)
        return None
    if source_decimal == candidate_decimal:
        return "equal"
    source_places = _decimal_places(source_value)
    candidate_places = _decimal_places(candidate_value)
    gap = abs(source_places - candidate_places)
    if gap > ROUNDING_MAX_PLACE_GAP:
        return None
    fine, coarse = (
        (source_decimal, candidate_decimal)
        if source_places > candidate_places
        else (candidate_decimal, source_decimal)
    )
    quantum = Decimal(1).scaleb(-min(source_places, candidate_places))
    try:
        rounded = fine.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return "rounded" if rounded == coarse else None


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
    """Строка про ту же метку показателя (`independent` / `other_record`).

    Если у кандидата нет ни одного сопоставляемого значения (каждое его число записано и прогнозом, и
    фактом — §13), отношение к показателю это не отменяет: его вывел различитель по меткам. Но про
    значения строка обязана молчать честно: «единицы значений неясны», без перечня чисел и без «совпадает».
    """
    lead = describe("estimate_row", kind)["label"]
    snippet = _snippet(candidate.statement)
    if snippet is None:  # conservative-исключение (см. _snippet)
        return None
    candidate_comparable = _comparison_values(candidate.statement)
    if not candidate_comparable:
        return EstimateRow(
            kind=kind,
            text=f"{lead}: {snippet} — {_phrase('units_unclear')}",
            candidate_id=candidate.claim_id,
        )
    candidate_percent = _is_percent(candidate.statement)
    value_part = (
        f"{_phrase('value_word')} "
        f"{_display_values(candidate_comparable, candidate_percent)}"
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


def _likely_same_row(
    source: EstimateSource, candidate: EstimateCandidate, agreement: str
) -> EstimateRow | None:
    """Вероятный пересказ того же показателя: период совместим, значение то же (или
    округлённое до меньшего числа знаков). Независимость при этом НЕ устанавливается:
    из записанных фактов она не следует, и витрина об этом говорит прямо.

    Смешанная запись (реализованное значение плюс приведённые прогнозы) участвует своим
    реализованным значением: 5,59 % против 5,59 % — это та же цифра, а прогнозные числа той же
    формулировки в сравнение не входят (§13)."""
    entry = describe("estimate_row", "likely_same_indicator")
    snippet = _snippet(candidate.statement)
    if snippet is None:  # conservative-исключение (см. _snippet)
        return None
    candidate_percent = _is_percent(candidate.statement)
    candidate_values = _display_values(_comparison_values(candidate.statement), candidate_percent)
    source_percent = _is_percent(source.statement)
    source_values = _display_values(_comparison_values(source.statement), source_percent)
    value_part = f"{_phrase('value_word')} {candidate_values}"
    rounding = f" {_phrase('after_rounding')}" if agreement == "rounded" else ""
    return EstimateRow(
        kind="likely_same_indicator",
        text=f"{entry['label']}: {snippet}, {value_part} {_phrase('match_with')} "
        f"{source_values}{rounding}; {entry['hint']}",
        candidate_id=candidate.claim_id,
    )


def _overflow_row(count: int) -> EstimateRow:
    """Одна строка вместо перечня скрытых записей: только число, без текстов записей."""
    entry = describe("estimate_row", "related_overflow")
    return EstimateRow(kind="related_overflow", text=f"{entry['label']}: {count}.", candidate_id=None)


def _honest_row() -> EstimateRow:
    entry = describe("estimate_row", "no_independent")
    return EstimateRow(kind="no_independent", text=entry["hint"], candidate_id=None)


# ─── учёт поиска независимых оценок (T7.89, ADR-0035 §14) ────────────────────

# Цитируем не больше трёх запросов — тот же потолок, что у связанных строк; остальные —
# числом. Текст запроса — запись журнала, а не формулировка знания: он цитируется с той же
# безопасностью, что сниппеты утверждений (резка целыми словами и проверка небезопасного
# текста); небезопасный запрос не цитируется вовсе, но в счёт входит.
MAX_SEARCH_QUERY_QUOTES: int = 3
SEARCH_QUOTE_MAX_CHARS: int = 60

# Тот же небезопасный текст, что отбраковывает сниппеты утверждений, плюс `§`:
# цитата чужого запроса не должна притащить в карточку то, что запрещает сканер страниц.
_SEARCH_UNSAFE_RE = re.compile(r"(?<!не )\bпроверен|[0-9a-fA-F]{8,}|§")


def _search_queries_word(count: int) -> str:
    """Русская форма счётного слова из закрытых записей словаря (грамматику код не выдумывает)."""
    if count % 10 == 1 and count % 100 != 11:
        return _phrase("search_queries_one")
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return _phrase("search_queries_few")
    return _phrase("search_queries_many")


def _search_quote(query: str) -> str | None:
    """Цитируемый фрагмент записанного запроса; `None` — процитировать нельзя."""
    text = _YEAR_SPACING_RE.sub(" ", query.replace("\u00a0", " ").replace("\u202f", " ")).strip()
    text = text.replace("«", "").replace("»", "")
    if not text or _SEARCH_UNSAFE_RE.search(text):
        return None
    if len(text) > SEARCH_QUOTE_MAX_CHARS:
        cut = text[:SEARCH_QUOTE_MAX_CHARS]
        space = cut.rfind(" ")
        text = (cut[:space] if space > 20 else cut) + "…"
    return text


def _search_done_row(queries: tuple[str, ...]) -> EstimateRow:
    entry = describe("estimate_row", "independent_search_done")
    quotes: list[str] = []
    quoted_texts: set[str] = set()
    for query in queries:
        quote = _search_quote(query)
        if quote is not None and quote not in quoted_texts and len(quotes) < MAX_SEARCH_QUERY_QUOTES:
            quotes.append(quote)
            quoted_texts.add(quote)
    body = f"{len(queries)} {_search_queries_word(len(queries))}"
    if quotes:
        body += ": " + ", ".join(f"«{quote}»" for quote in quotes)
        hidden = len(queries) - len(quotes)
        if hidden > 0:
            body += f"; {_phrase('search_queries_more')} {hidden}"
    tail = describe("estimate_value", "search_measurements_absent")["hint"]
    text = (
        f"{entry['label']} {_phrase('search_sessions_scope')}: {_phrase('search_was')} ({body}); {tail}."
    )
    return EstimateRow(kind="independent_search_done", text=text, candidate_id=None)


def _closing_honest_row(source: EstimateSource) -> EstimateRow:
    """Последняя честная строка: учёт журнала разделяет два разных записанных случая (T7.89)."""
    queries = source.independent_searches
    if queries is None:
        # Связанных с утверждением сессий не записано: о поиске сказать нечего,
        # остаётся прежняя общая честная формулировка.
        return _honest_row()
    if queries:
        return _search_done_row(queries)
    entry = describe("estimate_row", "independent_search_not_done")
    return EstimateRow(kind="independent_search_not_done", text=entry["hint"], candidate_id=None)


# ─── основная чистая функция подбора ─────────────────────────────────────────


def estimate_rows_for_source(
    source: EstimateSource, candidates: list[EstimateCandidate]
) -> tuple[EstimateRow, ...]:
    """Строки подраздела «Независимые оценки» для одного утверждения карточки.

    Порядок и приоритет подбора — один и тот же (ADR-0035 §12): независимые оценки →
    другие записи того же показателя → вероятно тот же показатель → прогнозы →
    нейтрально связанные записи → строка-счётчик → честная строка. Независимые оценки и
    другие записи потолка связанных строк не касаются; из связанных строк показываются
    не больше `MAX_ADJACENT_ROWS_PER_CLAIM`, остаток — одна строка со счётчиком. Честная
    строка добавляется, когда в подразделе нет ни одной независимой оценки, и стоит
    последней: её не вытесняет ни один потолок. кандидаты сортируются по id записи
    (канонический порядок, как в гейте дублей), поэтому набор и порядок детерминированы.
    """
    independent_rows: list[EstimateRow] = []
    other_rows: list[EstimateRow] = []
    likely_rows: list[EstimateRow] = []
    adjacent_rows: list[EstimateRow] = []
    forecast_rows: list[EstimateRow] = []

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
        roles = value_roles(candidate.statement)
        if _territory_conflict(source.statement, candidate.statement):
            # формулировки называют разные территории: это не измерение того же показателя.
            # (при равных объявленных метках `indicator_check` территорию не смотрит — guard здесь)
            continue
        if check.matched:
            if not _periods_comparable(source, candidate):
                # другой период — не показываем (ADR-0035 §11)
                continue
            if roles.is_forecast_only:
                # прогноз того же показателя — не измерение и не независимая оценка
                # (T7.88b, ADR-0035 §13: прогнозом бывает утверждение, у которого прогнозны ВСЕ
                # значения; смешанная запись остаётся записью измерения)
                row = _adjacent_row(source, candidate, "forecast_adjacent")
                if row is not None:
                    forecast_rows.append(row)
                continue
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
            if roles.is_forecast_only:
                # прогноз остаётся отдельной категорией при любом пересечении словаря:
                # он не является измерением реализованного значения (explorer-v9 правило 12).
                # Смешанная запись здесь уже НЕ прогноз: её прогнозные числа игнорируются, а
                # реализованное значение участвует в сравнении нараве всех остальных записей (§13)
                row = _adjacent_row(source, candidate, "forecast_adjacent")
                if row is not None:
                    forecast_rows.append(row)
                continue
            agreement = _rounding_agreement(source, candidate)
            metrics_declared = source.metric is not None and candidate.metric is not None
            if (
                agreement is not None
                and not metrics_declared
                and _periods_comparable(source, candidate)
            ):
                # тот же период и то же значение с точностью до округления: это скорее
                # пересказ того же показателя, чем другой показатель — но независимость
                # из записанных фактов не следует, и строка это говорит.
                # Обе объявленные метки показателя (и разные) — уже запись о другом:
                # `indicator_check` молчит намеренно, витрина не перебивает её («вероятно,
                # тот же») и остаётся нейтральной (ADR-0035 §12).
                row = _likely_same_row(source, candidate, agreement)
                if row is not None:
                    likely_rows.append(row)
                    continue
            row = _adjacent_row(source, candidate, "adjacent_metric")
            if row is not None:
                adjacent_rows.append(row)

    head_rows = independent_rows + other_rows
    linked_rows = likely_rows + forecast_rows + adjacent_rows
    budget = min(MAX_ADJACENT_ROWS_PER_CLAIM, MAX_ESTIMATE_ROWS_PER_CLAIM - len(head_rows))
    shown = linked_rows[: max(budget, 0)]
    hidden = len(linked_rows) - len(shown)
    rows = list(head_rows[:MAX_ESTIMATE_ROWS_PER_CLAIM] + shown)
    if hidden > 0:
        rows.append(_overflow_row(hidden))
    if not independent_rows:
        # Честная строка нужна, когда независимой оценки в подразделе нет вообще: соседство с
        # другой записью того же показателя (дублем), вероятным пересказом, прогнозом или
        # связанной записью её не отменяет и не заменяет. Она вне потолков и всегда последняя.
        # С T7.89 честная строка различает два записанных случая: поиск независимых оценок
        # в сессиях утверждения был (с перечнем запросов) и его не записано (ADR-0035 §14).
        rows.append(_closing_honest_row(source))
    return tuple(rows)


def estimate_section_heading() -> str:
    return describe("estimate_section", ESTIMATE_SECTION_KEYS[0])["label"]
