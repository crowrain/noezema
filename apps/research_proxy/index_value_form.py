"""T7.128 (часть 3, ADR-0034 §10 дополнение): индексная форма значения в гейте «значение на связи».

Стендовый случай c4 (сессия 2d682ab0, config-v29): исследователь прочитал релиз Росстата
«Об индексе потребительских цен в сентябре 2026 года», где годовое значение дано ИНДЕКСНОЙ
формой — строка таблицы «Индекс потребительских цен» и ячейка «106,35» в столбце «к сентябрю
2025 г.». Куратор предложил «годовая инфляция в сентябре 2026 года составила 6,35%», и гейт
значения снял связь: отдельного числа «6,35» на странице нет, а «106,35» граница текстовой
формы (`_form_is_standalone`) числом 6,35 не считает (это хвост другого числа). После раунда
исправления куратор вернул пустое предложение — ответ потерялся, хотя 106,35 и есть та же
величина, записанная индексом с базой 100.

Правило (ровно одно, консервативное): значение утверждения X, записанное С ПРОЦЕНТНОЙ ЕДИНИЦЕЙ
(«6,35%»), подтверждается связью и в индексной форме — если на странице отдельным числом стоит
100 + X («106,35») И рядом с этим числом (в окне `INDEX_MARKER_WINDOW_CHARS`) стоит маркер
индексной формы: подпись строки таблицы или шапка столбца, которые говорят, что число — индекс
с базой 100, а не самостоятельный замер.

Границы правила (остатки названы в ADR и STATUS):
* только проценты: размерность единицы у числа формулировки — «percent» (тот же измеритель
  `_unit_dimension_after`, что у детекторов атрибуции); «6,35 рублей» индексной формой не
  проверяется;
* только положительные X с дробной частью и X < 100: «6%» (индекс «106») и «105%» индексной
  формой не подтверждаются;
* число формулировки со знаком минуса перед ним («-0,5%», «−0,5%») вне правила: форма
  100 − X (убыль как «99,5») НЕ вводится — это остаток, убыль индексной формой не проверяется;
* обратное направление (утверждение «106,35», страница «6,35%») НЕ вводится: оно приписало бы
  утверждению об индексе самостоятельный замер роста;
* решение гейта по-прежнему ОДНО на связь, причина снятия прежняя, новых журнальных ключей нет.

Числа сравнивает только `_canonical_number`, формы и границы — те же, что у T7.108a
(`_value_text_forms`, `_form_is_standalone`): новой числовой логики модуль не заводил.

Модуль чистый: ни БД, ни LLM (та же дисциплина, что у curator_rejections.py).

T7.129a (перенос без изменения поведения): модуль лежит в `apps/research_proxy`, а не в
`apps/orchestrator`. Причина — ровно то препятствие, на котором встало правило v17 (ADR-0029 §26.4):
то же сравнение значения (гейт «значение на связи» и новый режим атрибуции значения) нужен детектору
значений `apps/research_proxy/value_attribution.py`, а импорт оркестратора из research_proxy
развернул бы направление зависимости; вторая копия гейта запрещена правилом об одном месте решения
(ADR-0038 §13, тот же разбор, что у переноса словаря основ показателя — ADR-0029 §25.3). Направление
импорта прежнее: оркестратор читает `research_proxy` (`apps/orchestrator/curator_rejections.py`),
обратного импорта в цепочке атрибуции нет — его держит тест
`tests/unit/test_statement_names_producer_t7129a.py::test_value_attribution_chain_does_not_import_orchestrator`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Final

from apps.research_proxy.source_attribution import (
    MAX_SCAN_CHARS,
    VALUE_NUMBER_PATTERN,
    _canonical_number,
    normalize_scan_text,
)
from apps.research_proxy.value_attribution import (
    PUBLICATION_WINDOW_CHARS,
    _form_is_standalone,
    _unit_dimension_after,
    _value_text_forms,
)

#: размерность процентной единицы (`_unit_dimension` в apps/research_proxy/value_attribution.py:
#: «%», «процент», «percent» — одна размерность)
PERCENT_DIMENSION: Final = "percent"

#: окно поиска маркера индексной формы вокруг числа 100+X. Той же длины, что окно периода и окно
#: публикации: `PUBLICATION_WINDOW_CHARS` = 400 (новой константы нет — тот же приём, что у
#: `PERIOD_WINDOW_CHARS` в curator_period_gate.py). Выбран по фикстуре c4: в нормализованном
#: тексте релиза Росстата подпись строки «Индекс потребительских цен» стоит в 41 знаке ДО числа
#: 106,35, шапка таблицы «в процентах» — в 303 знаках до него; маркеров ПОСЛЕ числа в этой таблице
#: нет вовсе, поэтому окно симметричное и широкое безопасно: лишние маркеры только разжижают
#: условие, а узкое окно (например 100) уже не накрывает шапку «в процентах».
INDEX_MARKER_WINDOW_CHARS: Final = PUBLICATION_WINDOW_CHARS

#: закрытый список маркеров индексной формы (регистр не значим). Маркер обязан называть индекс или
#: базу сравнения числа; голые числа и тексты без них правилом не подтверждаются.
#: Разделители слов — `\s+` включая перевод строки: шапка таблицы в нормализованном тексте
#: разрезана строками («сентябрю\n2026 г.»).
INDEX_FORM_MARKERS: Final[tuple[str, ...]] = (
    r"индекс\w*",  # «Индекс потребительских цен», «индексы цен», «базовый индекс»
    r"в\s*%\s*к",  # «в % к предыдущему месяцу»
    r"в\s+процентах",  # «Индексы потребительских цен в процентах»
    r"к\s+соответствующему\s+периоду",  # «к соответствующему периоду предыдущего года»
    r"к\s+[а-яё]{3,}\s*\d{4}\s*г\.?",  # заголовок столбца «к сентябрю 2025 г.»
)

_MARKER_RES: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in INDEX_FORM_MARKERS
)

#: каноническое значение с дробной частью: целые («6») правилом не подтверждаются
_FRACTIONAL_DECIMAL: Final = re.compile(r"\d+\.\d*[1-9]")

#: знак минус перед числом формулировки: «-0,5%», «−0,5%», «– 0,5%» — убыль, вне правила
_MINUS_BEFORE: Final = re.compile(r"[-\u2212\u2013\u2014][ \t]{0,3}$")

#: индексная форма имеет смысл только для прироста: 0 < X < 100
_INDEX_X_LIMIT: Final = Decimal(100)


def percent_statement_values(statement: str | None) -> frozenset[str]:
    """Канонические значения формулировки, записанные с процентной единицей («6,35%»).

    Измеритель — тот же, что у детекторов (`VALUE_NUMBER_PATTERN` + `_unit_dimension_after`).
    Число со знаком минуса перед ним («-0,5%») в множество НЕ попадает: отрицательные значения
    индексной формой не проверяются (остаток правила)."""
    if not statement:
        return frozenset()
    scanned = normalize_scan_text(statement)[:MAX_SCAN_CHARS]
    found: set[str] = set()
    for match in VALUE_NUMBER_PATTERN.finditer(scanned):
        if _unit_dimension_after(scanned, match.end()) != PERCENT_DIMENSION:
            continue
        if _MINUS_BEFORE.search(scanned[: match.start()]):
            continue
        found.add(_canonical_number(match.group()))
    return frozenset(found)


def index_form_value(value: str) -> str | None:
    """Каноническая форма «100 + X» для значения утверждения; None — если значение вне правила.

    Вне правила: целые значения (нет дробной части), X <= 0, X >= 100 и всё, что не читается
    как одно десятичное число."""
    if not _FRACTIONAL_DECIMAL.fullmatch(value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not Decimal(0) < number < _INDEX_X_LIMIT:
        return None
    return str(number + _INDEX_X_LIMIT)


def index_form_values_found_in_texts(
    *, values: Iterable[str], texts: Iterable[str | None]
) -> frozenset[str]:
    """Какие значения подтверждены в текстах ИНДЕКСНОЙ формой 100 + X рядом с маркером индекса.

    Число 100 + X ищется теми же текстовыми формами и границами, что и обычное значение гейта
    (`_value_text_forms`, `_form_is_standalone`): «106,35», «106.35», «106,350» — одна форма,
    число внутри другого числа («1106,35») подтверждением не считается. Возвращаются ИСХОДНЫЕ
    значения X, а не их индексные формы."""
    wanted: dict[str, str] = {}
    for value in values:
        form_value = index_form_value(value)
        if form_value is not None:
            wanted[value] = form_value
    if not wanted:
        return frozenset()
    forms: dict[str, tuple[str, ...]] = {
        value: _value_text_forms(form_value) for value, form_value in wanted.items()
    }
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        scanned = normalize_scan_text(text)[:MAX_SCAN_CHARS]
        hits: list[tuple[str, int, int]] = []
        for value, value_forms in forms.items():
            if value in found:
                continue
            for form in value_forms:
                glued_space = " " in form
                start = scanned.find(form)
                while start != -1:
                    end = start + len(form)
                    if _form_is_standalone(scanned, start, end, glued_space=glued_space):
                        hits.append((value, start, end))
                        break
                    start = scanned.find(form, start + 1)
        if not hits:
            continue
        markers = [
            match.span() for pattern in _MARKER_RES for match in pattern.finditer(scanned)
        ]
        for value, start, end in hits:
            if any(
                marker_end >= start - INDEX_MARKER_WINDOW_CHARS
                and marker_start <= end + INDEX_MARKER_WINDOW_CHARS
                for marker_start, marker_end in markers
            ):
                found.add(value)
    return frozenset(found)


def index_form_confirms_value(
    *, statement: str | None, values: Iterable[str], texts: Iterable[str | None]
) -> frozenset[str]:
    """Индексная форма только для процентных значений самой формулировки (фильтр по `statement`).

    `values` — профиль значений операции гейта (`claim_value_numbers`): правило применяется лишь
    к тем из них, которые в формулировке стоят с процентной единицей."""
    percent = set(percent_statement_values(statement)) & set(values)
    if not percent:
        return frozenset()
    return index_form_values_found_in_texts(values=percent, texts=texts)
