"""T7.85c: окно публикации живёт только в русском тексте, латинское имя вне словаря — вето.

Третье замечание приёмки (замер менеджера на `1dacf7c`, canonical_uri news.example.com /
news.example.ru). Общий префикс английских примеров:

    E = «According to a survey published by the Bank of Russia, expectations were stable. »

(1) E + «Bloomberg Economics estimates observed inflation at 14.5%.»   → derivative → Банк России
(2) E + «Analysts at Goldman Sachs expect inflation of 14.5%.»         → derivative → Банк России
(3) E + «Gallup found observed inflation of 14.5%.»                    → derivative → Банк России
(4) E + «A Reuters poll of economists put inflation at 14.5%.»         → derivative → Банк России
(5) «Согласно опубликованному Банком России обзору, ожидания стабильны. Gallup reported observed
    inflation of 15,2%.»                                               → derivative → Банк России

Причина одна и та же в двух половинах. (1)–(4): маркеры публикации окна содержали английские формы
(`survey`, `publicat*`, `report*`), а признаки вето этого режима — русские списки (`аналит*`,
`экономист*`, «по оценке», кавычки, аббревиатуры): ни один из них чужую английскую фразу не видел,
и режим склеивал чужое измерение с публикацией ЦБ. (5): латинское имя организации в русском тексте
не считалось действующим лицом — «Gallup» не аббревиатура (одна заглавная буква), действующие лица
и обороты оценки русские, а связка «имя␠глагол» требовала кириллической заглавной буквы.

FIX (тот же оконный режим, строгий режим не тронут ни на пункт): маркеры публикации — только
русские, то есть окно применяется там, где его собственное вето действительно действует; и новый
признак вето — латинское имя собственное длиной ≥2 с заглавной буквы, не замазанное как алиас
словаря и не входящее в белый список обозначений показателей/географии. Сомнение — не приписывать:
остаётся честный отказ строгого режима `no_value_attribution` (ADR-0029 «Риски»: лишний отказ
допустим, ложная склейка — нет).

Отрицательные controls закреплены отдельными строками: алиас словаря латиницей («Rosstat»,
«Bank of Russia») остаётся первоисточником, белый список (HICP, EU, CPI) — не действующее лицо,
строчное начало латинского слова признаком не является, английская пара «значение + первоисточник»
в одном фрагменте решается строгим режимом как и раньше.
"""

from __future__ import annotations

import re

import pytest

import apps.research_proxy.value_attribution as va
from apps.research_proxy.source_attribution import primary_source
from apps.research_proxy.value_attribution import (
    INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS,
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    PUBLICATION_MARKERS,
    attribute_value_in_fragment,
)
from tests.unit.test_attribution_fixtures_t785 import (
    STAND_URI,
    STATEMENT_145,
    STATEMENT_559,
    _text,
)

pytestmark = pytest.mark.unit

EN_URI = "https://news.example.com/a"
RU_URI = "https://news.example.ru/a"

#: общий префикс английских примеров замечания приёмки (измерение ЦБ названо в первом предложении,
#: чужое измерение — во втором; ровно та форма, где оконный режим срабатывал без всякого вето)
E_EN = "According to a survey published by the Bank of Russia, expectations were stable. "

#: тот же префикс по-русски: публикация ЦБ названа, а число оценивает другой оценщик
E_RU = "Согласно опубликованному Банком России обзору, ожидания стабильны. "


#: синтаксика регулярного выражения, которую нельзя принять за букву маркера
_REGEX_SYNTAX = re.compile(r"\\b|\[[^\]]*\]|[?*+()]")


def _decide(text: str, claim: str, uri: str) -> object:
    return attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=claim)


# ─── 1: defect (1)–(4): английское окно публикации не охраняется русским вето ────────────


@pytest.mark.parametrize(
    ("tail", "claim"),
    [
        ("Bloomberg Economics estimates observed inflation at 14.5%.", "Observed inflation was 14.5%."),
        ("Analysts at Goldman Sachs expect inflation of 14.5%.", "Inflation was 14.5%."),
        ("Gallup found observed inflation of 14.5%.", "Observed inflation was 14.5%."),
        ("A Reuters poll of economists put inflation at 14.5%.", "Inflation was 14.5%."),
        # латинского имени в хвосте нет вообще: отказ даёт уже сам режим — английское окно не
        # включается, потому что его вето по-русски молчит
        ("Economists put inflation at 14.5%.", "Inflation was 14.5%."),
    ],
)
def test_english_publication_window_is_not_a_mode_anymore(tail: str, claim: str) -> None:
    decision = _decide(E_EN + tail, claim, EN_URI)
    assert not decision.is_derivative, decision
    assert decision.primary_key is None, decision
    assert decision.status == "no_value_attribution", decision
    # решение вынес строгий режим и сам отказался расширять pairing до окна
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


# ─── 2: defect (5): латинское имя организации в русском тексте — другой источник числа ──


@pytest.mark.parametrize(
    ("tail", "claim"),
    [
        # ровно воспроизведённый пример замечания приёмки
        ("Gallup reported observed inflation of 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
        # то же имя по-русски: русская зона, латинское имя (главное для живых страниц)
        ("Gallup показал наблюдаемую инфляцию 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
        ("По данным Gallup, наблюдаемая инфляция составила 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
        ("Reuters писал о наблюдаемой инфляции 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
        # «оценка» без предлога — не прежний признак T7.85a; признаком становится имя
        ("Bloomberg Economics дал оценку наблюдаемой инфляции в 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
    ],
)
def test_latin_organization_name_in_russian_text_refuses_pairing(tail: str, claim: str) -> None:
    decision = _decide(E_RU + tail, claim, RU_URI)
    assert not decision.is_derivative, decision
    assert decision.primary_key is None, decision
    assert decision.status == "no_value_attribution", decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


# ─── 3: отрицательные controls нового признака (не сломать то, что решалось верно) ──────


@pytest.mark.parametrize(
    ("tail", "status", "primary", "pairing"),
    [
        # алиас словаря латиницей — НЕ чужое имя: он замазан вместе с именем. Здесь в окне
        # названы два разных первоисточника (ЦБ и Росстат) → прежняя «разночтение окон», не вето
        ("Rosstat опубликовал наблюдение: инфляция 15,2%.", "ambiguous_primaries", None, PAIRING_PUBLICATION_WINDOW),
        # алиас латиницей + глагол фиксации без действующего лица: окно решает по-прежнему
        (
            "Bank of Russia зафиксировал оценку текущих темпов на уровне 15,2%.",
            "derivative",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # белый список расширен латинскими именами показателя и территории: не действующие лица
        ("Показатель HICP в EU вырос на 15,2%.", "derivative", "cbr", PAIRING_PUBLICATION_WINDOW),
        # граница признака: строчное начало — не имя собственное (тот же приём, что у `_NAME_SLOT`)
        ("по данным gallup, инфляция составила 15,2%.", "derivative", "cbr", PAIRING_PUBLICATION_WINDOW),
        # прежнее вето T7.85b intact: сокращённое имя + глагол фиксации
        ("ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.", "no_value_attribution", None, PAIRING_VALUE_AND_PRIMARY),
    ],
)
def test_control_rows_of_the_window_mode(
    tail: str, status: str, primary: str | None, pairing: str
) -> None:
    decision = _decide(E_RU + tail, "Наблюдаемая инфляция составила 15,2%.", RU_URI)
    assert decision.status == status, decision
    assert decision.primary_key == primary, decision
    assert decision.pairing == pairing, decision


def test_english_strict_pairing_still_decides() -> None:
    """Строгий режим английских страниц не тронут: значение и первоисточник в одном фрагменте —
    пересказ, как и до правки. (Дробная запись «5.59%» распадает фрагментом на части — это
    прежняя граница pairing, здесь она не менялась.)"""

    rosstat = _decide(
        "According to Rosstat, inflation in Russia rose by 6 percent in 2025.",
        "Inflation in Russia rose by 6%.",
        EN_URI,
    )
    assert rosstat.is_derivative and rosstat.primary_key == "rosstat", rosstat
    assert rosstat.pairing == PAIRING_VALUE_AND_PRIMARY, rosstat

    cbr = _decide(
        "Citing the Bank of Russia, the newspaper said the key rate stood at 21 percent.",
        "The key rate was 21%.",
        EN_URI,
    )
    assert cbr.is_derivative and cbr.primary_key == "cbr", cbr
    assert cbr.pairing == PAIRING_VALUE_AND_PRIMARY, cbr

    decimal = _decide(
        "According to Rosstat, annual inflation in Russia was 5.59%.",
        "Annual inflation in Russia was 5.59%.",
        EN_URI,
    )
    assert not decimal.is_derivative, decimal


# ─── 4: признак на уровне зоны вето ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "zone",
    [
        " Gallup показал наблюдаемую инфляцию 15,2% ",
        " Reuters писал о наблюдаемой инфляции ",
        " Bloomberg Economics дал оценку 14,5% ",
        # SberCIB виден и прежним правилом сокращённого имени (T7.85b), и новым признаком
        " SberCIB опубликовал обзор инфляции ",
    ],
)
def test_latin_name_in_the_veto_zone_is_a_sign(zone: str) -> None:
    assert va._other_source_sign(zone), zone


@pytest.mark.parametrize(
    "zone",
    [
        # алиас словаря латиницей замазан ДО проверки → не чужое имя
        " Rosstat опубликовал релиз о 5,59% ",
        " Bank of Russia зафиксировал оценку текущих темпов на уровне 14,5% ",
        " cbr.ru опубликовал обзор ожиданий ",
        # белый список имён показателя и территории (в том числе новые позиции)
        " Показатель HICP в EU вырос на 5,59% ",
        " CPI и PPI в РФ выросли на 5,59% ",
        # строчное начало — не имя собственное
        " по данным gallup, инфляция составила 15,2% ",
        # пассивная форма без действующего лица (прежний отрицательный контроль T7.85b)
        " Наблюдаемая инфляция зафиксирована на уровне 14,5% ",
    ],
)
def test_alias_whitelist_and_lowercase_are_not_signs(zone: str) -> None:
    assert not va._other_source_sign(zone), zone


# ─── 5: язык маркеров окна = язык признаков вето (ловушка AGENTS §7) ─────────────────────


def test_publication_markers_are_russian_only() -> None:
    """Маркеры окна не могут быть шире его охраны: признаки вето (`OTHER_SOURCE_ACTOR_MARKERS`,
    `OTHER_SOURCE_ESTIMATION_MARKERS`, кавычки, аббревиатуры, `_NAME_SLOT`) — русские, поэтому и
    маркер публикации, включающий режим, обязан быть русским."""

    # литерал маркера — без регулярной синтаксики (`\b`, классы, квантификаторы)
    letters = "".join(_REGEX_SYNTAX.sub("", pattern) for pattern in PUBLICATION_MARKERS)
    foreign = [pattern for pattern in PUBLICATION_MARKERS if re.search(r"[A-Za-z]", _REGEX_SYNTAX.sub("", pattern))]
    assert foreign == [], (foreign, letters)


def test_english_publication_words_do_not_open_the_window() -> None:
    for word in ("survey", "published", "publication", "report", "poll", "research"):
        assert not any(pattern.search(word) for pattern in va._PUBLICATIONS), word


def test_russian_publication_words_still_open_the_window() -> None:
    """Режим не сузился в своей области: русские формы маркеров целы (с них начинался T7.85)."""

    for word in (
        "опрос",
        "опроса",
        "опубликованного",
        "публикации",
        "мониторинга",
        "обзору",
        "исследовании",
        "отчёте",
        "релиз",
    ):
        assert any(pattern.search(word) for pattern in va._PUBLICATIONS), word


# ─── 6: белый список: имена показателей и территорий, но не организации ──────────────────


def test_whitelist_extensions_are_indicators_not_organizations() -> None:
    """Новые позиции того же принципа, что ИПЦ/ВВП/CPI: латинская запись имени показателя (HICP)
    и латинская запись той же территории (EU). Организация в список не попадает ни в какой записи:
    собственное измерение организации — ровно тот другой источник, ради которого вето и существует."""

    assert "HICP" in INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS
    assert "EU" in INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS
    for organization in ("OECD", "Gallup", "Reuters", "Bloomberg", "SberCIB"):
        assert organization not in INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS, organization


def test_every_whitelist_entry_is_not_an_actor_and_not_a_primary_source() -> None:
    for entry in sorted(INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS):
        assert not va._is_dictionary_alias_name(entry), entry
        assert primary_source(entry.lower()) is None, entry
        assert not va._other_source_sign(f" {entry} вырос на 5,59% "), entry
        assert not va._fixation_verb_sign(f" {entry} зафиксировал значение показателя "), entry


@pytest.mark.parametrize("name", ["Gallup", "Reuters", "Bloomberg", "OECD"])
def test_latin_organizations_outside_the_dictionary_are_actors(name: str) -> None:
    assert name not in INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS, name
    assert va._other_source_sign(f" {name} показал значение показателя "), name


# ─── 7: строгий режим не тронут (тот же guard-приём T7.85a/T7.85b) ────────────────────────


@pytest.mark.parametrize(
    ("text", "claim", "uri"),
    [
        (E_EN + "Bloomberg Economics estimates observed inflation at 14.5%.", "Observed inflation was 14.5%.", EN_URI),
        (E_RU + "Gallup reported observed inflation of 15,2%.", "Наблюдаемая инфляция составила 15,2%.", RU_URI),
    ],
)
def test_new_sign_does_not_leak_into_the_strict_mode(
    monkeypatch: pytest.MonkeyPatch, text: str, claim: str, uri: str
) -> None:
    """Оконный режим выключен entirely — решение то же, что с окном. Значит правка живёт строго
    внутри оконного режима и строгий pairing (в том числе английский) не тронута."""

    with_window = _decide(text, claim, uri)
    monkeypatch.setattr(va, "_publication_decision", lambda **kwargs: None)
    without_window = _decide(text, claim, uri)
    assert (without_window.status, without_window.pairing) == (
        with_window.status,
        with_window.pairing,
    )


# ─── 8: живые тексты стенда не сломаны (окно ЦБ и строгое решение СберCIB) ────────────────


def test_cbr_window_positives_on_live_texts_survive() -> None:
    for name in ("forbes_552221", "kommersant_8294535"):
        decision = _decide(_text(name), STATEMENT_145, STAND_URI[name])
        assert (decision.status, decision.primary_key) == ("derivative", "cbr"), name
        assert decision.pairing == PAIRING_PUBLICATION_WINDOW, name


def test_sbercib_strict_decision_survives_its_own_latin_name() -> None:
    """На странице СберCIB есть «SberCIB» — латинское имя вне словаря, и оно действительно признак
    другого источника числа. Но решение о 5,59% выносит строгий pairing ДО оконного режима, и зона
    вето для него не строится вовсе: строгое решение Росстата не тронуто."""

    assert va._other_source_sign(" SberCIB выпустил обзор инфляции ")
    decision = _decide(_text("sbercib_2025"), STATEMENT_559, STAND_URI["sbercib_2025"])
    assert (decision.status, decision.primary_key) == ("derivative", "rosstat"), decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
