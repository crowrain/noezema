"""Guard (T7.85a): отрицательные controls нового вето внутри окна публикации.

Вето могло только ЗАПРЕЩАТЬ атрибуцию числа в окне публикации. Всё остальное обязано остаться
ровно прежним: строгий pairing (он решает первым и вето не видит), границы окна, семантика
ключей `_attributed_keys`, различение «оценка» как названия показателя и «оценка» как чужой
оценки, замазывание алиасов словаря (Банк России не бывает «банком-актором»). Каждый провал
здесь означает, что вето просочилось за пределы оконного режима или ослабила его."""

from __future__ import annotations

import pytest

import apps.research_proxy.value_attribution as va
from apps.research_proxy.source_attribution import (
    ATTRIBUTION_WINDOW_CHARS,
    _attributed_keys,
    normalize_scan_text,
)
from apps.research_proxy.value_attribution import attribute_value_in_fragment
from tests.unit.test_attribution_fixtures_t785 import (
    STAND_URI,
    STATEMENT_63,
    STATEMENT_131_156,
    STATEMENT_145,
    STATEMENT_559,
    _text,
)

pytestmark = pytest.mark.unit

#: строгие решения той же таблицы (tests/unit/test_attribution_fixtures_t785.py): обе строки
#: парного окна публикации (forbes 14,5 и kommersant 14,5) сюда сознательно НЕ включены —
#: они решаются оконным режимом; всё остальное обязан давать строгий режим без окна вообще
STRICT_TABLE: tuple[tuple[str, str, str, str | None], ...] = (
    ("expert_5-59", STATEMENT_559, "derivative", "rosstat"),
    ("interfax_1068021_a", STATEMENT_559, "derivative", "rosstat"),
    ("interfax_1068021_b", STATEMENT_559, "derivative", "rosstat"),
    ("ria_2068393962", STATEMENT_559, "derivative", "rosstat"),
    ("sbercib_2025", STATEMENT_559, "derivative", "rosstat"),
    ("cbr_CPD_2025-12", STATEMENT_559, "no_value_attribution", None),
    ("cbr_reginfl_64837", STATEMENT_559, "value_not_attributed", None),
    ("garant_1963171", STATEMENT_559, "no_value_attribution", None),
    ("nbj_71790", STATEMENT_559, "no_value_attribution", None),
    ("cbr_Infl_exp_25-12", STATEMENT_145, "value_not_attributed", None),
    # страница самого ЦБ по опросу «инФОМ»: значение приписано самой странице — не пересказ
    ("cbr_Infl_exp_25-12", STATEMENT_63, "self_primary", None),
    ("forbes_552221", STATEMENT_131_156, "value_not_attributed", None),
    ("kommersant_8294535", STATEMENT_131_156, "no_value_attribution", None),
)


# ─── 1: строгий режим не тронут: без оконного режима решения таблиц остаются теми же ──


@pytest.mark.parametrize(("name", "statement", "status", "primary"), STRICT_TABLE)
def test_strict_decisions_survive_without_the_publication_window(
    monkeypatch: pytest.MonkeyPatch, name: str, statement: str, status: str, primary: str | None
) -> None:
    """Оконный режим (и живущее в нём вето) выключен entirely — строгие решения не изменились.

    Это прямой контроль утверждения T7.85a «строгий режим не тронут»: вето вызывается только из
    `_publication_decision`, а монkeypatch'ом мы оставляем от окна один лишь честный отказ."""

    monkeypatch.setattr(va, "_publication_decision", lambda **kwargs: None)
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI[name], text=_text(name), claim_statement=statement
    )
    assert (decision.status, decision.primary_key) == (status, primary), (name, statement[:40])
    assert decision.pairing == "value_and_primary_in_fragment", (name, statement[:40])


def test_publication_window_gates_are_unchanged() -> None:
    """Границы окна и окно строгого pairing не расширены и не сужены вето-изменениями."""

    assert va.PUBLICATION_WINDOW_CHARS == 400
    assert va._PUBLICATION_TAIL_CHARS == 40
    assert ATTRIBUTION_WINDOW_CHARS == 120


# ─── 2: ключи оконных областей ⊇ ключей строгого pairing (вето не плодит кандидатов) ──


def test_attribution_regions_cover_every_strictly_attributed_key() -> None:
    """На живых стендовых текстах (по строке-блоку и по всему тексту) каждая ключевая атрибуция
    `_attributed_keys` имеет хотя бы одну свою область в `_attribution_regions`.

    Обратное включение не требуется и не закрепляется: области строятся всеми вхождениями пар
    (шаблон, алиас), их может быть больше — но они никогда не участвуют в кандидатах, пока ключ
    не атрибутирован `_attributed_keys` (`if key in attributed` в `_publication_decision`)."""

    for name in sorted(STAND_URI):
        text = normalize_scan_text(_text(name))[: va.MAX_SCAN_CHARS]
        probes = [text, *text.split("\n")]
        for probe in probes:
            attributed = _attributed_keys(probe)
            if not attributed:
                continue
            covered = {key for key, _, _ in va._attribution_regions(probe)}
            assert set(attributed) <= covered, (name, list(attributed))


# ─── 3: «оценка» как имя показателя — не признак чужой оценки ────────────────────────

INDICATOR_NOUNS_CLEAN: tuple[str, ...] = (
    " Оценка наблюдаемой населением годовой инфляции в декабре 2025 года составила ",
    " Показатель ожидаемой на горизонте 12 месяцев инфляции вырос с 13,3% в ноябре до 13,7% в декабре. ",
    " Инфляционные ожидания продолжили расти — показатель увеличился до 13,7%. ",
    " Текущая оценка инфляции осталась на уровне прошлого месяца. ",
)

ASSESSMENT_SIGNS: tuple[str, ...] = (
    # глагольные формы и обороты чужой оценки
    " По оценке ВШЭ реальная инфляция составила ",
    " Аналитики Сбербанка ожидают инфляцию ",
    " Экономисты Райффайзенбанка оценивают инфляцию ",
    " Эксперты ЦМАКП рассчитали индекс ",
    " Независимые экономисты считают инфляцию ",
    " По мнению аналитиков точная оценка невозможна ",
    " По расчётам ведомства рост замедлится ",
    # актёры-оценщики даже без глагола: сама роль — признаки смены источника числа
    " Компания Ромир зафиксировала инфляцию ",
    # прогноз как отдельный артефакт чужой оценки
    " Прогноз Минфина на 2026 год пересмотрен ",
)


@pytest.mark.parametrize("zone", INDICATOR_NOUNS_CLEAN)
def test_indicator_nouns_are_not_an_assessment(zone: str) -> None:
    """Существительное «оценка» (имя показателя) и причастие/прилагательное «ожидаемой» — не
    маркеры. Именно на этом различии стендовая форма Коммерсанта («…при этом осталась на уровне
    14,5%») остаётся пересказом, а дефектные обороты — снимаются (ADR-0029)."""

    assert not va._other_source_sign(zone), zone


@pytest.mark.parametrize("zone", ASSESSMENT_SIGNS)
def test_assessment_phrases_and_actor_roles_are_a_sign(zone: str) -> None:
    assert va._other_source_sign(zone), zone


# ─── 4: алиасы словаря замазаны: «Банк России» не банк-актор, кавычки словаря не чужое имя ──


def test_dictionary_aliases_are_masked_in_the_zone() -> None:
    assert not va._other_source_sign(" Как сообщил Банк России в опубликованном отчёте, ")
    assert not va._other_source_sign(" По данным ЦБ, оценка темпов осталась на уровне 14,5%. ")
    # чужой банк не замазан — банк-актор (овещание Сбербанка/Райффайзенбанка) — признак
    assert va._other_source_sign(" Сбербанк опубликовал оценку инфляции в 6,3%. ")
    # название вне словаря в кавычках — признак (например «инФОМ» вне фразы атрибуции),
    # название словаря в кавычках — нет
    assert va._other_source_sign(" Данные опроса «Ромир» показали инфляцию ")
    assert not va._other_source_sign(" Как сообщил «Росстат» в опубликованном отчёте, ")


def test_survey_customer_outside_the_dictionary_is_a_sign() -> None:
    assert va._other_source_sign(" Исследование Ромир показало наблюдаемую инфляцию ")
    # заказчик-словарь замазан: после «исследования» не остаётся чужого имени — не признак
    assert not va._other_source_sign(" Исследование Росстата показало наблюдаемую инфляцию ")
