"""Серия прогнозов карточки ответа (T7.135, ADR-0035 §22).

Презентационный слой: карточка ответа показывает несколько записей одного и того же
прогноза, записанных в разное время, и читатель не может понять, какая из них
самая свежая. Это правило только показывает: знания, оценки, связи и свёртки оно
не меняет, ничего не скрывает и не удаляет — самая поздняя запись получает подпись
«актуальный прогноз», более ранние — «устаревший прогноз» с названной датой более
свежего, недатированные — «дата прогноза не указана».

Молчит витрина и в ТИХОЙ серии (T7.135a): если у всех записей серии названа одна и
та же дата прогноза — или датированных записей нет вовсе, — меток нет ни у кого.
Сравнивать по свежести нечего, а подписи только шумели бы на экране.

Что такое «одна серия» (три признака, все — из уже существующих словарей):

* тот же производитель — закрытый словарь `PRIMARY_SOURCES`
  (`named_primary_keys` + `family_primary_key`, корень семьи); формулировка обязана
  называть РОВНО ОДИН производитель словаря: названы два или ни одного — витрина
  молчит, чужое авторство прогнозу она не приписывает;
* тот же показатель — объявленные метки `assessed_scope`, если обе заданы; иначе
  общая основа показателя из существующего словаря (`statement_indicator_words`)
  по закрытому списку `_SERIES_INDICATOR_STEMS`, плюс существующие вето территорий
  и уточнителей (`value_duplicate`, T7.96) и запрет на «уточнитель назван только в
  одной формулировке»;
* тот же горизонт — год целевой фразы («по итогам N года», «на конец N года»,
  «к концу N года», «за N год»); это уже распознаваемые гейтом периода цели
  (`curator_period_gate`), своего словаря горизонтов здесь нет. Формулировка без
  явной цели (`«на 2026 год»` гейт целью не считает) в сравнение не идёт.

Откуда берётся дата прогноза (порядок фиксирован):

1. дата, названная в самой формулировке после маркера даты документа, — шаблоны
   «прогноз по состоянию на …», «прогноз от …», «по данным публикации от …»
   (токены гейта с `doc_date`, T7.121a);
2. записанная якорная дата утверждения `as_of`;
3. иначе запись считается недатированной.

Даты карточка никогда не берёт из `created_at`: время создания строки в базе — это
момент, когда запись появилась в знаниях, а не момент, когда прогноз был сделан.
Поэтому `created_at` в эту функцию не передаётся вовсе, а порядок при равных датах
прогноза решает id записи (канонический порядок, как в подборщике оценок).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from apps.orchestrator.curator_period_gate import (
    PeriodToken,
    has_forecast_marker,
    statement_period_tokens,
)
from apps.orchestrator.value_duplicate import (
    _indicator_qualifier_group_ids,
    _indicator_qualifier_words,
    _territory_group_ids,
    _territory_words,
)
from apps.research_proxy.source_attribution import (
    PRIMARY_SOURCES,
    family_primary_key,
    named_primary_keys,
)
from apps.research_proxy.value_attribution import statement_indicator_words
from apps.web import labels as ui_labels
from apps.web.estimates import value_roles
from packages.domain.models.base import JsonDict

#: Закрытый набор подписей витрины (категория словаря `forecast_series`): состояния
#: серии плюс служебные начала фразы, из которых складывается строка текста.
FORECAST_SERIES_KEYS: Final[tuple[str, ...]] = (
    "actual",
    "outdated",
    "undated",
    "fresher_lead",
    "dated_lead",
)

#: Состояния, которые передвигаются в списке после актуальной записи серии.
STALE_KINDS: Final[tuple[str, ...]] = ("outdated", "undated")

#: Имена производителей витрина берёт из того же закрытого словаря, что и бейджи.
_SOURCE_NAMES: Final[Mapping[str, str]] = {spec.key: spec.name for spec in PRIMARY_SOURCES}

#: Закрытый список основ показателя, для которого витрина называет серию. Он НЕ
#: заменяет словарь показателя: это список «что витрина вообще умеет называть»,
#: свернутый на живые формулировки стенда. Показатель вне списка — сравнения нет
#: (консервативное молчание, тот же выбор, что у вето территорий).
_SERIES_INDICATOR_STEMS: Final[tuple[str, ...]] = (
    "инфля",
    "ставк",
    "зарплат",
    "безработ",
    "доход",
)

#: Родительный падеж месяцев для «от <дата>».
_MONTH_GENITIVE: Final[tuple[str, ...]] = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


@dataclass(frozen=True)
class ForecastRecord:
    """Действующая запись карточки в том виде, в каком её видит витрина.

    `created_at` здесь нет намеренно: это не дата прогноза (см. docstring модуля).
    """

    claim_id: str
    statement: str
    as_of: dt.datetime | dt.date | None = None
    metric: str | None = None


@dataclass(frozen=True)
class ForecastMark:
    """Метка записи: состояние серии и данные актуальной записи этой серии."""

    claim_id: str
    kind: str
    series: str
    producer: str
    date_label: str


@dataclass(frozen=True)
class _Forecast:
    record: ForecastRecord
    producer_key: str
    producer_name: str
    horizon: frozenset[int]
    date: dt.date | None
    date_label: str


def _as_date(value: dt.datetime | dt.date | None) -> dt.date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, dt.datetime) else value


def _render_date(day: int | None, month: int | None, year: int) -> str:
    """Дата по-русски в родительном падеже: «20 октября 2026», «октябрь 2026»."""
    if month is not None and 1 <= month <= 12:
        name = _MONTH_GENITIVE[month - 1]
        return f"{day} {name} {year}" if day is not None else f"{name} {year}"
    return str(year)


def _token_date(token: PeriodToken) -> tuple[dt.date, str] | None:
    if token.year is None:
        return None
    day = token.day if token.kind == "date" else None
    month = token.month
    try:
        when = dt.date(token.year, month or 1, day or 1)
    except ValueError:
        return None
    return when, _render_date(day, month, token.year)


def _forecast_date(statement: str, as_of: dt.datetime | dt.date | None) -> tuple[dt.date, str] | None:
    """Дата прогноза: сначала названная в формулировке, потом записанный якорь `as_of`."""
    candidates = [
        found
        for token in statement_period_tokens(statement)
        if token.doc_date and token.is_forecast_date_capable
        and (found := _token_date(token)) is not None
    ]
    if candidates:
        return max(candidates, key=lambda found: found[0])
    anchored = _as_date(as_of)
    if anchored is None:
        return None
    return anchored, _render_date(anchored.day, anchored.month, anchored.year)


def _producer(statement: str) -> tuple[str, str] | None:
    """Производитель формулировки по закрытому словарю (ровно один, корень семьи)."""
    roots = {family_primary_key(key) for key in named_primary_keys(statement)}
    if len(roots) != 1:
        return None
    root = next(iter(roots))
    return root, _SOURCE_NAMES.get(root, root)


def _horizon(statement: str) -> frozenset[int]:
    """Горизонт прогноза: годы целевых фраз, уже распознаваемых гейтом периода."""
    return frozenset(
        token.year
        for token in statement_period_tokens(statement)
        if token.kind == "target" and token.year is not None
    )


def _names_indicator(stems: frozenset[str], core: str) -> bool:
    return any(stem.startswith(core) for stem in stems)


def _groups_disjoint(left: set[int], right: set[int]) -> bool:
    return bool(left) and bool(right) and not (left & right)


def _same_indicator(left: ForecastRecord, right: ForecastRecord) -> bool:
    """Тот же ли это показатель — по существующим словарям, без своего словаря."""
    if left.metric and right.metric:
        return left.metric == right.metric
    left_statement, right_statement = left.statement, right.statement
    territories_left = _territory_group_ids(_territory_words(left_statement))
    territories_right = _territory_group_ids(_territory_words(right_statement))
    if _groups_disjoint(territories_left, territories_right):
        return False
    qualifiers_left = _indicator_qualifier_words(left_statement)
    qualifiers_right = _indicator_qualifier_words(right_statement)
    if _groups_disjoint(
        _indicator_qualifier_group_ids(qualifiers_left),
        _indicator_qualifier_group_ids(qualifiers_right),
    ):
        return False
    if bool(qualifiers_left) != bool(qualifiers_right):
        # уточнитель назван только в одной формулировке: одна измеряет «наблюдаемую»
        # инфляцию, другая — просто инфляцию; тождество показателя не доказано.
        return False
    left_stems = statement_indicator_words(left_statement)
    right_stems = statement_indicator_words(right_statement)
    return any(
        _names_indicator(left_stems, core) and _names_indicator(right_stems, core)
        for core in _SERIES_INDICATOR_STEMS
    )


def _read(record: ForecastRecord) -> _Forecast | None:
    """Прогноз ли это, кто его сделал, на какой горизонт и когда он датирован."""
    statement = record.statement
    if not statement or not has_forecast_marker(statement):
        return None
    if value_roles(statement).realized:
        # запись называет реализованное значение — это запись измерения, а не прогноз
        # (то же решение ролей, что у подраздела «Независимые оценки», T7.88b/T7.94)
        return None
    producer = _producer(statement)
    if producer is None:
        return None
    horizon = _horizon(statement)
    if not horizon:
        return None
    found = _forecast_date(statement, record.as_of)
    return _Forecast(
        record=record,
        producer_key=producer[0],
        producer_name=producer[1],
        horizon=horizon,
        date=None if found is None else found[0],
        date_label="" if found is None else found[1],
    )


def _series_is_quiet(dated: Sequence[_Forecast], group: Sequence[_Forecast]) -> bool:
    """Тихая серия — та, где по свежести различать нечего (T7.135a, ADR-0035 §22.7).

    Метка отвечает на один вопрос: «какая запись серии свежее». Если у всех записей
    названа ОДНА И ТА ЖЕ дата прогноза, на этот вопрос нет ответа: «актуальный» и
    «устаревший» были бы здесь ложью, а две подписи рядом — шумом (на наборе стенда
    это видно на паре дублей одного декабрьского опроса Банка России, обе от
    24 декабря 2025). Сравнение есть, только когда в серии хотя бы две разные даты
    либо датированная запись стоит рядом с недатированной.
    """
    if len(dated) != len(group):
        # есть недатированная запись: датированная против неё — различие
        return False
    return len({forecast.date for forecast in dated}) < 2


def forecast_marks(records: Sequence[ForecastRecord]) -> dict[str, ForecastMark]:
    """Метки серии для записей одной карточки (чистая функция, без БД).

    Записи группируются по производителю, показателю и горизонту; внутри группы с
    хотя бы одной датой самая поздняя по дате прогноза — «актуальный прогноз»,
    более ранние датированные — «устаревший прогноз», бездатные — «дата прогноза не
    указана». Группа из одной записи и группа без единой даты меток не получают:
    сравнения нет, выдумывать его витрина не вправе. Так же молчит и ТИХАЯ серия —
    группа, у всех записей которой названа одна и та же дата прогноза (T7.135a,
    ADR-0035 §22.7): различать по свежести нечего, и подписи только шумят.
    """
    forecasts = [forecast for forecast in (_read(record) for record in records) if forecast is not None]
    groups: list[list[_Forecast]] = []
    for forecast in sorted(forecasts, key=lambda item: item.record.claim_id):
        for group in groups:
            head = group[0]
            if (
                head.producer_key == forecast.producer_key
                and bool(head.horizon & forecast.horizon)
                and _same_indicator(head.record, forecast.record)
            ):
                group.append(forecast)
                break
        else:
            groups.append([forecast])

    marks: dict[str, ForecastMark] = {}
    for index, group in enumerate(groups):
        if len(group) < 2:
            continue
        dated = [forecast for forecast in group if forecast.date is not None]
        if not dated:
            continue
        if _series_is_quiet(dated, group):
            # тихая серия: сравнивать по свежести нечего, меток нет ни у кого
            continue
        latest_date = max(forecast.date for forecast in dated if forecast.date is not None)
        reference = sorted(
            (forecast for forecast in dated if forecast.date == latest_date),
            key=lambda forecast: forecast.record.claim_id,
        )[0]
        series = f"{reference.producer_key}:{index}"
        for forecast in group:
            when = forecast.date
            if when is None:
                kind = "undated"
            elif when >= latest_date:
                kind = "actual"
            else:
                kind = "outdated"
            marks[forecast.record.claim_id] = ForecastMark(
                claim_id=forecast.record.claim_id,
                kind=kind,
                series=series,
                producer=reference.producer_name,
                date_label=reference.date_label,
            )
    return marks


def _mark_text(mark: ForecastMark, entry: JsonDict) -> str:
    """Готовая строка текста: JS печатает её дословно, своей фразы на стороне клиента нет."""
    if mark.kind == "actual":
        return f"{entry['label']}: {mark.producer} от {mark.date_label}"
    if mark.kind == "outdated":
        lead = ui_labels.describe("forecast_series", "fresher_lead")["label"]
        return f"{entry['label']}: {lead} {mark.producer} от {mark.date_label}"
    lead = ui_labels.describe("forecast_series", "dated_lead")["label"]
    return f"{entry['label']}; {lead} {mark.producer} от {mark.date_label}"


def _stale_after_current(items: list[JsonDict], marks: Mapping[str, ForecastMark]) -> list[JsonDict]:
    """Устаревшие и недатированные записи серии идут после актуальной этой же серии.

    Перестановка минимальная: запись переезжает сразу за последнюю запись своей
    серии, остальные сохраняют относительный порядок (детерминированно: обход в
    текущем порядке списка).
    """
    out = list(items)
    for entry in list(items):
        claim_id = str(entry.get("id") or "")
        mark = marks.get(claim_id)
        if mark is None or mark.kind not in STALE_KINDS:
            continue
        positions = [
            position
            for position, candidate in enumerate(out)
            if (entry_mark := marks.get(str(candidate.get("id") or ""))) is not None
            and entry_mark.series == mark.series
        ]
        here = next(position for position, candidate in enumerate(out) if str(candidate.get("id") or "") == claim_id)
        last = max(positions, default=here)
        if here < last:
            out.pop(here)
            out.insert(last, entry)
    return out


def apply_forecast_series(items: list[JsonDict], marks: Mapping[str, ForecastMark]) -> list[JsonDict]:
    """Серверные поля `forecast_series*` + порядок списка карточки.

    Записи без метки не получают НИ ОДНОГО нового поля: карточка без прогнозов
    остаётся байт в байт прежней.
    """
    for item in items:
        mark = marks.get(str(item.get("id") or ""))
        if mark is None:
            continue
        entry = ui_labels.describe("forecast_series", mark.kind)
        item["forecast_series"] = mark.kind
        item["forecast_series_label"] = entry["label"]
        item["forecast_series_hint"] = entry["hint"]
        item["forecast_series_text"] = _mark_text(mark, entry)
    return _stale_after_current(items, marks)
