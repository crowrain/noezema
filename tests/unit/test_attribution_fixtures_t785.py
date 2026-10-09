"""T7.85: разбор стендовых текстов CURRENT детектором как закреплённый тест (ADR-0029).

Фикстуры — копии нормализованных артефактов dev-стенда .92 (база `noezema-dev`),
текст лежит в `tests/fixtures/t785/<имя>.normalized.txt`; sha256 этих файлов —
`sha256(normalized)` строк источников стенда (сверено по журналу сессий a09977e1 и
ed36f4a0 и по `research-reattribute --dry-run`):

    cbr_CPD_2025-12     218e9a1ea998f548…  https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/
    cbr_Infl_exp_25-12  7e2eb44100d5279a…  https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/
    cbr_reginfl_64837   a25e3cf8dd473573…  https://www.cbr.ru/press/reginfl/?id=64837
    expert_5-59         7fc081956fffe31d…  https://expert.ru/news/godovaya-inflyatsiya-v-rossii-v-2025-godu-sostavila-5-59
    forbes_552221       f98ef1367a6d2860…  https://www.forbes.ru/finansy/552221-cb-soobsil-o-roste-inflacionnyh-ozidanij-rossian-v-dekabre-2025-goda
    garant_1963171      09d8f784f9bad0ff…  https://www.garant.ru/hotlaw/federal/1963171/
    interfax_1068021_a  adf05eb5dbd622f4…  https://www.interfax.ru/business/1068021 (первая нормализация)
    interfax_1068021_b  0f96906a35274b1f…  https://www.interfax.ru/business/1068021 (вторая нормализация)
    kommersant_8294535  20c19e0a651157d2…  https://www.kommersant.ru/doc/8294535
    nbj_71790           7b5c7f14b4afea60…  https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/
    ria_2068393962      414c0a4405cc3052…  https://ria.ru/2026-01-21/inflyaciya-2068393962/
    sbercib_2025        8be7622923526ccc…  https://sbercib.ru/publication/inflyatsiya-v-2025-godu

Таблица закреплена целиком, включая честные отказы: тест краснеет и если детектор
стал решать там, где должен молчать (ослабление), и если перестал видеть там, где
видел (пропуск). Отрицательные проверки закреплены отдельными строками таблицы.

Важно про вход: детекторы видят ТЕКСТ ОСНОВАНИЯ УЛИКИ, а не страницу целиком. Здесь
намеренно подан весь нормализованный текст страницы — это верхняя граница окна (так
же делает `research-reattribute --evidence-level`, перечитывая артефакт); узкие
фрагменты закреплены в `tests/unit/test_value_attribution.py`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t785"

#: стендовые адреса (sources.canonical_uri базы .92) — метка хоста решает «это сам
#: первоисточник?», поэтому адрес части фиксации, а не украшение теста
STAND_URI: dict[str, str] = {
    "cbr_CPD_2025-12": "https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/",
    "cbr_Infl_exp_25-12": "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/",
    "cbr_reginfl_64837": "https://www.cbr.ru/press/reginfl/?id=64837",
    "expert_5-59": "https://expert.ru/news/godovaya-inflyatsiya-v-rossii-v-2025-godu-sostavila-5-59",
    "forbes_552221": (
        "https://www.forbes.ru/finansy/552221-cb-soobsil-o-roste-inflacionnyh-ozidanij-rossian"
        "-v-dekabre-2025-goda"
    ),
    "garant_1963171": "https://www.garant.ru/hotlaw/federal/1963171/",
    "interfax_1068021_a": "https://www.interfax.ru/business/1068021",
    "interfax_1068021_b": "https://www.interfax.ru/business/1068021",
    "kommersant_8294535": "https://www.kommersant.ru/doc/8294535",
    "nbj_71790": "https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/",
    "ria_2068393962": "https://ria.ru/2026-01-21/inflyaciya-2068393962/",
    "sbercib_2025": "https://sbercib.ru/publication/inflyatsiya-v-2025-godu",
}

#: sha256(normalized) стенда — фикстура обязана остаться той же страницей
STAND_SHA: dict[str, str] = {
    "cbr_CPD_2025-12": "218e9a1ea998f548",
    "cbr_Infl_exp_25-12": "7e2eb44100d5279a",
    "cbr_reginfl_64837": "a25e3cf8dd473573",
    "expert_5-59": "7fc081956fffe31d",
    "forbes_552221": "f98ef1367a6d2860",
    "garant_1963171": "09d8f784f9bad0ff",
    "interfax_1068021_a": "adf05eb5dbd622f4",
    "interfax_1068021_b": "0f96906a35274b1f",
    "kommersant_8294535": "20c19e0a651157d2",
    "nbj_71790": "7b5c7f14b4afea60",
    "ria_2068393962": "414c0a4405cc3052",
    "sbercib_2025": "8be7622923526ccc",
}

#: утверждения стенда (claims.statement) — тот текст, чьё значение ищется в фрагменте
STATEMENT_559 = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
STATEMENT_145 = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ для Банка России)."
)
STATEMENT_131_156 = (
    "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года "
    "составила 13,1% для граждан с накоплениями и 15,6% для граждан без накоплений "
    "(по данным опроса «инФОМ»)."
)
STATEMENT_63 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)


def _text(name: str) -> str:
    path = FIXTURE_DIR / f"{name}.normalized.txt"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest().startswith(STAND_SHA[name]), (
        f"фикстура {name} больше не стендовая страница ({STAND_URI[name]})"
    )
    return raw.decode("utf-8")


@pytest.mark.parametrize("name", sorted(STAND_URI))
def test_fixture_is_the_stand_page(name: str) -> None:
    """Фикстуры — копии страниц стенда, а не пересказ на память."""
    text = _text(name)
    assert name != "cbr_Infl_exp_25-12" or "\xa0" in text  # неразрывные пробелы живы
    assert len(text) > 3_000


def test_page_decisions_on_stand_texts() -> None:
    """Страничный детектор v3 (то же решение, что пишет `research.fetch`)."""
    expected_page = {
        # сам Банк России: страница первоисточника не бывает пересказом самой себя
        "cbr_Infl_exp_25-12": ("self_primary", None),
        # «Росстат» на странице CPD не назван ни разу → undecided, а не «производная CBR»
        "cbr_CPD_2025-12": ("no_value_attribution", None),
        # страница ЦБ про Москву пересказывает Росстат — указатель был и остался
        "cbr_reginfl_64837": ("derivative", "rosstat"),
        "expert_5-59": ("derivative", "rosstat"),
        # «следует из опроса … опубликованного ЦБ» + «ЦБ сообщил»: страница пересказ ЦБ
        "forbes_552221": ("derivative", "cbr"),
        "garant_1963171": ("no_value_attribution", None),
        # две атрибуции рядом (Росстат и консенсус-прогноз «Интерфакса») → честный отказ
        "interfax_1068021_a": ("ambiguous_primaries", None),
        "interfax_1068021_b": ("derivative", "rosstat"),
        # страница без страничной атрибуции: производность признаётся только по значению 14,5
        "kommersant_8294535": ("no_value_attribution", None),
        "nbj_71790": ("no_value_attribution", None),
        "ria_2068393962": ("derivative", "rosstat"),
        # «по данным Росстата» и «по оценкам Банка России» на одной странице → отказ
        "sbercib_2025": ("ambiguous_primaries", None),
    }
    for name, (status, primary) in expected_page.items():
        decision = detect_source_attribution(canonical_uri=STAND_URI[name], text=_text(name))
        assert (decision.status, decision.primary_key) == (status, primary), name
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v3"


@pytest.mark.parametrize(
    ("name", "statement", "status", "primary", "pairing"),
    [
        # ── 5,59: пересказ Росстата там, где текст сам называет Росстат ───────────
        ("expert_5-59", STATEMENT_559, "derivative", "rosstat", PAIRING_VALUE_AND_PRIMARY),
        ("interfax_1068021_a", STATEMENT_559, "derivative", "rosstat", PAIRING_VALUE_AND_PRIMARY),
        ("interfax_1068021_b", STATEMENT_559, "derivative", "rosstat", PAIRING_VALUE_AND_PRIMARY),
        ("ria_2068393962", STATEMENT_559, "derivative", "rosstat", PAIRING_VALUE_AND_PRIMARY),
        ("sbercib_2025", STATEMENT_559, "derivative", "rosstat", PAIRING_VALUE_AND_PRIMARY),
        # ── честные отказы: значение без первоисточника в том же фрагменте ───────
        # CPD Банка России: «годовая инфляция в декабре уменьшилась до 5,59%» — Росстат не назван
        ("cbr_CPD_2025-12", STATEMENT_559, "no_value_attribution", None, PAIRING_VALUE_AND_PRIMARY),
        # cbr.ru про Москву: 5,59 приведено без атрибуции именно этого числа
        ("cbr_reginfl_64837", STATEMENT_559, "value_not_attributed", None, PAIRING_VALUE_AND_PRIMARY),
        # garant.ru: «по данным Росстата» стоит у другой цифры (6,1% месяц) — не атрибуция 5,59
        ("garant_1963171", STATEMENT_559, "no_value_attribution", None, PAIRING_VALUE_AND_PRIMARY),
        # nbj.ru: собственная оценка НБС («по нашим расчётам») → не пересказ вовсе
        ("nbj_71790", STATEMENT_559, "no_value_attribution", None, PAIRING_VALUE_AND_PRIMARY),
        # ── 14,5: опрос инФОМ опубликован Банком России → пересказ ЦБ (окно публикации)
        (
            "forbes_552221",
            STATEMENT_145,
            "derivative",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        (
            "kommersant_8294535",
            STATEMENT_145,
            "derivative",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # отрицательная проверка: страница САМОГО ЦБ — не пересказ самого себя
        ("cbr_Infl_exp_25-12", STATEMENT_145, "value_not_attributed", None, PAIRING_VALUE_AND_PRIMARY),
        # 13,1/15,6 названы в блоке без атрибуции первоисточника → честный отказ
        ("forbes_552221", STATEMENT_131_156, "value_not_attributed", None, PAIRING_VALUE_AND_PRIMARY),
        ("kommersant_8294535", STATEMENT_131_156, "no_value_attribution", None, PAIRING_VALUE_AND_PRIMARY),
        # 6,3: прогноз аналитиков из опроса ЦБ на странице ЦБ → self_primary (отрицательная проверка)
        ("cbr_Infl_exp_25-12", STATEMENT_63, "self_primary", None, PAIRING_VALUE_AND_PRIMARY),
        # на чужих страницах 6,3 без атрибуции этого числа → отказ
        ("forbes_552221", STATEMENT_63, "value_not_attributed", None, PAIRING_VALUE_AND_PRIMARY),
        ("sbercib_2025", STATEMENT_145, "value_not_attributed", None, PAIRING_VALUE_AND_PRIMARY),
    ],
)
def test_value_decisions_on_stand_texts(
    name: str, statement: str, status: str, primary: str | None, pairing: str
) -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI[name], text=_text(name), claim_statement=statement
    )
    assert (decision.status, decision.primary_key) == (status, primary), (name, statement[:40])
    assert decision.pairing == pairing, (name, statement[:40])
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v2"


def test_publication_window_never_overrides_a_refusal_of_its_own_kind() -> None:
    """Оконный режим включается только после честного строгого отказа.

    Отрицательная проверка на живом тексте sbercib.ru: значение 14,5 стоит рядом с
    собственной оценкой редакции и без первоисточника — оконный режим обязан молчать,
    иначе «пересказ ЦБ» появился бы там, где текст говорит «по нашим оценкам».
    """
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI["sbercib_2025"],
        text=_text("sbercib_2025"),
        claim_statement=STATEMENT_145,
    )
    assert not decision.is_derivative
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY
