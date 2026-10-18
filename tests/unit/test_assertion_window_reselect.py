"""Unit: T7.82 (A) — реселекция окон source_assertion-улики по финальной rationale.

Дефект стенда .92 (сессия f0d7e844, config-v18): окно улики выбирается в момент
чтения страницы — сигналами, накопленными ДО того, как шаг прочитал ключевой факт.
На сохранённой странице cbr.ru («Инфляционные ожидания и потребительские настроения»
№ 12 (108), фикстура CBR_INFL_SHA) фраза «Наблюдаемая населением годовая инфляция …
составляла 14,5%.» стоит на смещении 2452; оба окна чтения (терминологическое от
2976 и запасной value-якорь от 0) её не накрывали, и куратор фразу не увидел. Финальная
rationale исследователя («…первично указано, что наблюдаемая населением годовая инфляция
составила 14,5%…») содержит это число — хост пересобирает окна по ней тем же модулем.

Правила реселекции (дополнение к ADR-0011):
- точные якоря — дословные цитаты исследователя и названные им числа, которые есть в
  ЭТОМ источнике (граница числа, окрестность оглавления/навигации — прежние фильтры);
- порядок значимости: цитаты → значения доверенного вопроса → значения вывода
  исследователя (свежие раньше прежних) → даты → общий value-якорь;
- нет точного якоря — окна остаются как при чтении (честный отказ);
- бюджет, число окон, вербатим nature фрагмента и идентичность улики не меняются.

Две правки самого модуля окон (T7.82):
(а) цитата принимается только если встречается в тексте РОВНО один раз: на стенде
    цитирование ЗАГОЛОВКА материала (два вхождения — шапка и навигация) забрало слот
    окном [0..2000) и не пустило якорь к факту;
(б) «оглавление/навигация» для точного якоря судится по окрестности вхождения, а не по
    всему extent окна: служебное слово «подраздел» на смещении 3957 (через 1500 знаков
    после факта, внутри того же абзаца) браковало окно [2152..4152), накрывающее 14,5%.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from apps.orchestrator.assertion_window import (
    anchor_signals,
    explorer_value_terms,
    has_exact_anchor,
    question_value_terms,
    researcher_quote_terms,
    select_assertion_windows,
)
from apps.orchestrator.evidence import (
    SOURCE_ASSERTION_TEXT_BUDGET,
    observation_to_evidence,
    reselect_assertion_fragment,
)
from apps.orchestrator.executor import Observation

ARTIFACTS = Path(__file__).parent.parent / "fixtures" / "artifacts"

#: сохранённый нормализованный текст страницы cbr.ru (материал «Инфляционные ожидания
#: и потребительские настроения» № 12 (108), декабрь 2025) из сессии f0d7e844 стенда .92;
#: content-addressed фикстура (sha256 файла == имя). Замеренные смещения сохранены.
CBR_INFL_SHA = "7e2eb44100d5279a3c50cbf6213a082ecaa962e56036a78fc3925d4beac93144"

#:verbatim начало финальной публичной rationale исследователя той же сессии (дамп журнала
#: стенда): в ней есть и цитата заголовка (два вхождения — не сигнал), и «14,5%»,
#: и «5,59%» (того числа на этой странице нет).
CBR_INFL_FINAL_RATIONALE = (
    "Вопрос закрыт в доступных пределах. Официальная годовая инфляция в России за 2025 год "
    "(декабрь 2025 к декабрю 2024) подтверждена ранее как 5,59% по данным Росстата, "
    "опубликованным в сводке Банка России/Росстата в январе 2026 г. Независимая от Росстата "
    "оценка по опросу населения: в информационно-аналитическом материале Банка России "
    "«Инфляционные ожидания и потребительские настроения» № 12 (108) за декабрь 2025 г. "
    "первично указано, что наблюдаемая населением годовая инфляция составила 14,5%, а ожи"
)

#: формулировка вопроса сессии f0d7e844 (доверенный операторский вход), дословно.
CBR_INFL_QUESTION = (
    "Какой была годовая инфляция в России за 2025 год по официальным данным Росстата и какие "
    "есть независимые оценки той же величины (например, наблюдаемая инфляция по опросам "
    "населения, альтернативные расчёты экономистов)? Покажи расхождение между ними и укажи "
    "первоисточник каждой цифры; пересказы официальных данных не считай независимыми."
)

CBR_KEY_SENTENCE = "составляла 14,5%"


def _read_artifact(sha: str) -> str:
    path = ARTIFACTS / sha[:2] / sha
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha  # content-addressed
    # текст страницы как сохранён (11 CRLF-последовательностей внутри); смещения ниже
    # замерены по этому тексту. В выгрузке журнала стенда (только LF) те же маркеры —
    # 2452 и 3957; разница ровно от них, смысл измерений тот же.
    return raw.decode("utf-8")


def _fetch_record(normalized_text: str, question: str, explorer_text: str):
    obs = Observation(
        tool="research.fetch",
        ok=True,
        data={
            "url": "https://cbr.ru/analytics/Infl_exp_25-12/",
            "normalized_text": normalized_text,
            "question": question,
            "plan": "",
            "source_id": "0" * 36,
            "original_sha256": "ab" * 32,
            "normalized_sha256": "cd" * 32,
            "chunk_id": "chunk-0",
        },
    )
    return observation_to_evidence(
        obs, {"url": "https://cbr.ru/analytics/Infl_exp_25-12/"}, explorer_text=explorer_text
    )


# ─── репродюсер дефекта на живой странице ────────────────────────────────


def test_real_cbr_page_fetch_windows_miss_the_key_fact() -> None:
    """Репродюсер (измеренные предпосылки стенда): фраза с 14,5% — на смещении 2452,
    служебное слово «подраздел» того же текста — на 3957; ни одно окно чтения фразу не
    накрывает, куратор значения не видит."""
    page = _read_artifact(CBR_INFL_SHA)
    assert CBR_KEY_SENTENCE in page
    assert page.index("годовая инфляция не\xa0изменилась и\xa0составляла 14,5%") == 2410
    assert page.lower().index("подраздел") == 3960
    record = _fetch_record(page, CBR_INFL_QUESTION, explorer_text="Ищу независимые оценки населения.")
    assert "14,5%" not in record.payload["assertion_text"]


def test_real_cbr_page_final_rationale_pulls_the_fact_into_the_fragment() -> None:
    """Реселекция по финальной rationale (тот же модуль, тот же слот): окно встало на
    2154 = raw_start(2454) − lead(300), фраза с 14,5% внутри фрагмента; бюджет и число
    окон прежние, окна — дословные вырезы нормализованного текста."""
    page = _read_artifact(CBR_INFL_SHA)
    fragment = reselect_assertion_fragment(page, CBR_INFL_QUESTION, "", CBR_INFL_FINAL_RATIONALE)
    assert fragment is not None
    assert "14,5%" in fragment
    windows = select_assertion_windows(
        page,
        CBR_INFL_QUESTION,
        SOURCE_ASSERTION_TEXT_BUDGET,
        max_windows=2,
        value_terms=question_value_terms(CBR_INFL_QUESTION),
        quote_terms=researcher_quote_terms(CBR_INFL_FINAL_RATIONALE, page),
        explorer_value_terms=explorer_value_terms(CBR_INFL_FINAL_RATIONALE),
    )
    assert 1 <= len(windows) <= 2
    pinned = [w for w in windows if "14,5%" in w.text]
    assert any(w.start == 2154 for w in pinned), [w.start for w in windows]
    for window in windows:
        assert len(window.text) <= SOURCE_ASSERTION_TEXT_BUDGET
        assert window.text == page[window.start : window.start + len(window.text)]


def test_reselect_never_touches_evidence_identity() -> None:
    """Идентичность улики (§14.3) не зависит от фрагмента и сигналов: та же страница,
    разные сигналы — один identity_hash; реселекция payload identity не трогает."""
    page = _read_artifact(CBR_INFL_SHA)
    quiet = _fetch_record(page, CBR_INFL_QUESTION, explorer_text="Ищу оценки.")
    loud = _fetch_record(page, CBR_INFL_QUESTION, explorer_text=CBR_INFL_FINAL_RATIONALE)
    assert quiet is not None and loud is not None
    assert quiet.identity_hash == loud.identity_hash


# ─── честный отказ: нет точного якоря — окна остаются ─────────────────────


def test_reselect_refuses_values_absent_from_the_source() -> None:
    """Числа финальной rationale, которых в этом источнике нет («13,1%» ФОМ, «5,59»), —
    не якоря: реселекция возвращает None, окна остаются как при чтении."""
    page = _read_artifact(CBR_INFL_SHA)
    assert "13,1" not in page and "5,59" not in page
    signal = (
        "Независимые оценки: 13,1% и 15,6% по опросам ФОМ; официальное значение 5,59% "
        "подтверждено ранее — в этом материале его нет."
    )
    assert reselect_assertion_fragment(page, CBR_INFL_QUESTION, "", signal) is None


def test_reselect_empty_signal_changes_nothing() -> None:
    page = _read_artifact(CBR_INFL_SHA)
    assert reselect_assertion_fragment(page, CBR_INFL_QUESTION, "", "") is None
    assert reselect_assertion_fragment("", CBR_INFL_QUESTION, "", "14,5%") is None


# ─── (а) цитата-заголовок, повторённая в шапке и навигации, — не сигнал ───


def test_quote_repeated_in_header_and_nav_is_not_an_anchor() -> None:
    """Измерено на стенде: «Инфляционные ожидания и потребительские настроения»
    встречается на странице дважды (h1 + навигация) и забирало второй слот окном
    [0..2000). Ровно одно вхождение — сигнал; повторённая подстрока — страницный хром."""
    page = _read_artifact(CBR_INFL_SHA)
    key_sentence = "Наблюдаемая населением годовая инфляция не\xa0изменилась и\xa0составляла 14,5%."
    signal = (
        "В информационно-аналитическом материале Банка России "
        "«Инфляционные ожидания и потребительские настроения» № 12 (108) прямо сказано: "
        f"«{key_sentence}»"
    )
    quotes = researcher_quote_terms(signal, page)
    folded_key = "".join(key_sentence.lower().split())
    assert folded_key in quotes, "уникальная фраза источника не принята как цитата"
    folded_title = "инфляционные ожидания и потребительские настроения".replace(" ", "")
    assert all(quote != folded_title for quote in quotes), [quotes]


def test_anchor_signal_order_cites_then_question_values_then_explorer_values() -> None:
    """Зафиксированный порядок классов точных сигналов (T7.82): цитаты → значения
    вопроса → значения вывода исследователя → даты; внутри значений — по специфичности."""
    signals = anchor_signals(
        ["годовая инфляция снизилась до 5,59%"],
        ["21.01.2026", "6,6"],
        ["14,5"],
    )
    assert [(s.kind, s.needle) for s in signals] == [
        ("quote", "годовая инфляция снизилась до 5,59%"),
        ("value", "6,6"),
        ("explorer_value", "14,5"),
        ("date", "21.01.2026"),
    ]


def test_explorer_value_terms_are_fresh_first_deduped_and_typed() -> None:
    """Значения вывода: дубли не повторяются, свежее (позже в тексте) идёт раньше;
    год-одиночка — не значение; дата д.м.гггг остаётся сигналом низшего класса."""
    terms = explorer_value_terms(
        "Прогнозировали 6,3% и округлённо 6,6%. Факт: наблюдаемая инфляция 14,5%; "
        "повтор для уверенности 14,5%. Год 2025 — не значение. Отчёт от 21.01.2026."
    )
    # порядок — по свежести появления в сигнальном тексте (последние первыми), дубли сняты
    assert terms == ["21.01.2026", "14,5", "6,6", "6,3"]
    assert "2025" not in terms
    kinds = {s.kind for s in anchor_signals([], [], terms)}
    assert "date" in kinds and "explorer_value" in kinds


# ─── (б) границы числа и окрестность оглавления для якоря вывода ──────────


def test_explorer_number_needle_respects_number_boundaries() -> None:
    """«14,5» не цепляет «114,5%», «14,52» и «4,5»: отдельное число или ничего."""
    variants = (
        "Отчёт приводит значения 114,5% и 14,52%; уточнение (4,5) относится к другому показателю.\n"
        * 6
    )
    signal = "Наблюдаемая инфляция составила 14,5% в декабре."
    terms = explorer_value_terms(signal)
    assert terms == ["14,5"]
    assert not has_exact_anchor(variants, explorer_value_terms=terms)
    assert reselect_assertion_fragment(variants, "Какое значение инфляции?", "", signal) is None

    single = (
        "Отчёт регулярный. " * 6
        + "Ключевая строка отчёта: наблюдаемая населением инфляция 14,5% в декабре.\n"
        + "Подвал с контактами. " * 6
    )
    assert has_exact_anchor(single, explorer_value_terms=terms)
    fragment = reselect_assertion_fragment(single, "Какое значение инфляции?", "", signal)
    assert fragment is not None and "14,5%" in fragment


def test_toc_veto_judges_the_neighbourhood_of_the_anchor_not_the_whole_window() -> None:
    """Служебное слово в 1500 знаков ПОСЛЕ факта (внутри того же контентного блока) не
    бракует якорь; маркер оглавления впритя к значению — по-прежнему бракует."""
    body = "Ключевое значение отчёта составило 6,3% за период. " * 8
    filler = "Методика расчёта и примечания к строкам отчёта. " * 25
    far_toc = (
        "Отчёт регулятора начинается здесь. " * 10
        + body
        + filler
        + "Дальше по тексту встречается служебное слово подраздел и перечисление разделов. " * 4
    )
    signal = "В отчёте прямо указано: 6,3% за период."
    terms = explorer_value_terms(signal)
    anchor_at = far_toc.index("6,3%")
    marker_after = far_toc.index("подраздел")
    assert marker_after - anchor_at > 800  # маркер далеко от якоря, но внутри окна чтения (2000)
    assert has_exact_anchor(far_toc, explorer_value_terms=terms)

    near_toc = (
        "Отчёт регулятора начинается здесь. " * 10
        + body
        + "Отобразить подраздел статистики. " * 3
    )
    assert not has_exact_anchor(near_toc, explorer_value_terms=terms)


def test_question_values_keep_priority_over_explorer_values() -> None:
    """Значение доверенного вопроса сильнее значения вывода модели: когда оба указывают
    на разные одиночные вхождения страницы, слот занимает вопросное."""
    page = (
        "Раздел о спросе. " * 8
        + "Справочная таблица приводит значение 6,7% как отдельный показатель.\n"
        + "Заключение исследования. " * 8
        + "Вывод исследователя опирался на значение 8,9%, названное им в отчете шага.\n"
    )
    windows = select_assertion_windows(
        page,
        "Какое значение 6,7% приводит таблица?",
        SOURCE_ASSERTION_TEXT_BUDGET,
        max_windows=2,
        value_terms=question_value_terms("Какое значение 6,7% приводит таблица?"),
        explorer_value_terms=["8,9"],
    )
    anchored = [w for w in windows if "6,7%" in w.text]
    assert anchored, [w.start for w in windows]
