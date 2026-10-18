"""T7.97: реконструкция окна улики SberCIB из стендовой фикстуры (ADR-0035 §15).

Фикстура — копия нормализованного артефакта dev-стенда .92 (сессия bdd9ef8d, вопрос
5a68172e, 2026-10-11, HEAD b21172c, config-v20):
`tests/fixtures/t797/sbercib_2026_34ecbe51.normalized.txt`; его sha256 равен
`normalized_sha256` строки источника стенда (аудит seq 32 `research_content_read`,
truncated=true, bytes 39999). Текст вопроса и видимая часть финальной публичной
rationale (seq 51; payload аудита урезан до 500 знаков) — из того же журнала.

Окна вырезает сам хост (assertion_window + evidence), без LLM. Что закреплено:

1. fetch-time окна (rationale шага чтения в журнал не попадает — сигнал пуст):
   абзац собственной оценки SberCIB («повысили прогноз инфляции на 2026 год — с 5
   до 6,5%», смещение 14161 бюджетированного текста) остаётся за обоими окнами —
   это закреплённый дефект: куратор никогда не видел собственную оценку аналитиков;
2. реселекция по ВИДИМОЙ части реальной финальной rationale (факт сессии): якоря —
   числа консенсуса 6,6/6,2/6,0/7,0, окно 2 уезжает в недельную секцию текста — «6,5» там
   по-прежнему нет; дефект закреплён и на этом шаге;
3. реселекция по rationale, называющей собственную оценку актора (правило 13
   explorer-v10): дословная цитата пришпиливает окно 2 к абзацу SberCIB; если
   значение названо последним в сигнале — якорь берётся более раннее собственное
   прогнозное предложение («достигнет 6,5%»). Механизм T7.82(а) для этого уже
   работает: тесты 3–4 — охранники механизма (краснеют, если он сломается), тесты
   1–2 документируют, где именно терялась оценка в реальной сессии.

Проверка (б) — где теряется: наблюдение SberCIB (39999+служебное > половины
EXPLORER_CONTEXT_BUDGET 52400) снимается обрезкой по свежести (T7.10) уже на шаге
повторного чтения cbr.ru (seq 36), поэтому финальная rationale физически не могла
назвать прочитанную ранее оценку — закреплено отдельным тестом ниже.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from apps.orchestrator.assertion_window import (
    question_value_terms,
    researcher_quote_terms,
    select_assertion_windows,
)
from apps.orchestrator.evidence import (
    _FRAGMENT_SEPARATOR,
    SOURCE_ASSERTION_MAX_WINDOWS,
    SOURCE_ASSERTION_TEXT_BUDGET,
    reselect_assertion_fragment,
)
from apps.orchestrator.orchestrator import RESEARCH_CONTEXT_BUDGET
from apps.orchestrator.tool_context import TOOL_CONTEXT_ADDENDA_CHARS

pytestmark = pytest.mark.unit

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "t797" / "sbercib_2026_34ecbe51.normalized.txt"
)

#: normalized_sha256 строки источника стенда (аудит seq 32) — фикстура обязана быть тем же текстом
STAND_NORMALIZED_SHA256 = "34ecbe512b5120523c9850679df3ec159e296da66896ba701929918bde16f521"

#: вопрос 5a68172e (формулировка оператора, fixtures-t797/README.txt)
QUESTION = (
    "Какую инфляцию в России на 2026 год прогнозируют Банк России и НЕЗАВИСИМЫЕ аналитики, "
    "и какие инфляционные ожидания населения показывает опрос инФОМ? Сравни с официальной "
    "инфляцией за 2025 год и отдели прогнозы от измерений."
)

#: видимая часть финальной публичной rationale исследователя (аудит seq 51, срез 500 знаков)
RATIONALE_VISIBLE = (
    "Вопрос закрыт по подтверждённым инструментами данным. Прогноз Банка России по годовой "
    "инфляции на 2026 год: 6,0–7,0% (комментарии Банка России, evidence [1], [2]). Независимые "
    "аналитики в макроэкономическом опросе Банка России (сентябрь 2026 года, медиана прогнозов "
    "33 экономистов) ожидают ИПЦ декабрь к декабрю 2026 на уровне 6,6%, повысив оценку с 6,2% "
    "в июльском опросе (evidence [0], [4]); эта медиана попадает в официальный диапазон ЦБ "
    "6,0–7,0%, расхождений нет. Опрос «инФОМ» по заказу Банка Ро"
)

#: маркер абзаца собственной оценки SberCIB (смещение 14161 бюджетированного текста)
OWN_FORECAST_MARK = "повысили прогноз инфляции на 2026 год"

#: маркер более раннего прогнозного предложения того же актора («…к концу 2026 года достигнет 6,5%»)
OWN_FORECAST_EARLY_MARK = "достигнет 6,5%"


def _budgeted_text() -> str:
    """Тот же вход, что видит хост: артефакт, обрезанный RESEARCH_CONTEXT_BUDGET по байтам."""
    raw = FIXTURE.read_text(encoding="utf-8")
    return raw.encode("utf-8")[:RESEARCH_CONTEXT_BUDGET].decode("utf-8", "ignore")


def _fetch_time_windows(text: str) -> list[str]:
    """Окна как при чтении страницы (evidence.py:220): сигнал шага чтения в журнал не пишется."""
    windows = select_assertion_windows(
        text,
        QUESTION,
        SOURCE_ASSERTION_TEXT_BUDGET,
        max_windows=SOURCE_ASSERTION_MAX_WINDOWS,
        value_terms=question_value_terms(QUESTION),
        quote_terms=researcher_quote_terms("", text),
    )
    # окно — дословный вырез исходного нормализованного текста (fold-map возвращает оригинал)
    return [w.text for w in windows]


def _fragment(windows: list[str]) -> str:
    return _FRAGMENT_SEPARATOR.join(windows)


def test_fixture_is_the_stand_normalized_artifact() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == STAND_NORMALIZED_SHA256
    # ровно то же усечение, что записал аудит: 39999 байт из 40000 (граница символа)
    assert len(_budgeted_text().encode("utf-8")) == 39_999


def test_fetch_time_window_misses_sbercib_own_forecast() -> None:
    """Закреплённый дефект (а): окно чтения не видит абзац собственной оценки SberCIB."""
    text = _budgeted_text()
    assert OWN_FORECAST_MARK in text  # сам абзац на странице есть
    fragment = _fragment(_fetch_time_windows(text))
    assert OWN_FORECAST_MARK not in fragment
    assert "6,5" not in fragment


def test_reselect_with_visible_rationale_still_misses_it() -> None:
    """Закреплённый дефект (а, шаг реселекции): реальная финальная rationale не называет SberCIB."""
    text = _budgeted_text()
    fragment = reselect_assertion_fragment(text, QUESTION, "", RATIONALE_VISIBLE)
    assert fragment is not None  # точный якорь (6,6/6,2/6,0/7,0) в источнике есть
    assert OWN_FORECAST_MARK not in fragment
    assert "6,5" not in fragment


def test_reselect_pins_paragraph_when_rationale_quotes_it() -> None:
    """Охранник механизма (в): цитата правила 13 пришпиливает окно 2 к абзацу SberCIB."""
    text = _budgeted_text()
    signal = (
        "Независимые оценки: Банк России — 6,0–7,0%; консенсус макроопроса ЦБ — 6,6%. "
        "SberCIB: «В июле аналитики SberCIB повысили прогноз инфляции на 2026 год — с 5 до 6,5%»."
    )
    fragment = reselect_assertion_fragment(text, QUESTION, "", signal)
    assert fragment is not None
    assert OWN_FORECAST_MARK in fragment
    assert "6,5" in fragment
    # окно остаётся дословным вырезом источника: текст модели в окно не попадает
    assert "Независимые оценки: Банк России" not in fragment


def test_reselect_pins_own_forecast_when_value_named_last() -> None:
    """Охранник механизма (в): значение, названное последним в сигнале, — свежайший якорь."""
    text = _budgeted_text()
    signal = (
        "ЦБ 6,0–7,0%; консенсус макроопроса ЦБ 6,6%; ожидания населения 14,2%. "
        "Собственный прогноз аналитиков SberCIB на 2026 год — 6,5%."
    )
    fragment = reselect_assertion_fragment(text, QUESTION, "", signal)
    assert fragment is not None
    assert "6,5" in fragment
    assert OWN_FORECAST_EARLY_MARK in fragment  # якорь — на собственном прогнозе актора
    assert "SberCIB" in fragment


def test_sbercib_observation_is_trimmed_from_final_context() -> None:
    """Проверка (б): наблюдение SberCIB не могло быть в контексте финального шага.

    Стендовые размеры наблюдений (аудит research_content_read, seq 14/18/28/32/36/49):
    19202 + 11935 + 8800 + 39999 + 19202 + 3900. Обрезка по свежести (T7.10) снимает
    старые наблюдения, пока сумма не влезает в EXPLORER_CONTEXT_BUDGET; служебное
    ограждение каждого fetch (провенанс+fence+футер) и надбавка контекста инструментов
    учтены нижней оценкой. После seq 36 (повторное чтение cbr.ru) SberCIB выпадает —
    даже при нулевом остальном контексте.
    """
    budget = RESEARCH_CONTEXT_BUDGET + 8_000 + TOOL_CONTEXT_ADDENDA_CHARS
    stand_bytes = [19_202, 11_935, 8_800, 39_999, 19_202, 3_900]
    fence_overhead = 420  # нижняя оценка провенанса/fence'ов одного fetch-наблюдения
    kept: list[int] = []
    for size in stand_bytes:
        kept.append(size)
        while len(kept) > 1 and sum(n + fence_overhead for n in kept) > budget:
            kept.pop(0)
    assert 39_999 not in kept  # SberCIB снят ещё до финального шага
    # и с любым прочим содержимым контекста (протокол, инструменты, evidence-строки)
    assert 39_999 + 19_202 + 2 * fence_overhead > budget
