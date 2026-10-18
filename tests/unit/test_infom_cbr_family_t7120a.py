"""Unit: дефект приёмки T7.120 (T7.120a, ADR-0029 §24) — инФОМ не отдельный производитель.

Банк России заказывает и публикует опрос инФОМ: это ОДИН производитель. Словарь
первоисточников получает поле `family_key`: запись-член семьи (`infom` → корень `cbr`)
решается под именем корня, её кандидаты склеиваются с корнем, а домашний хост проверяется
по хостам всей семьи. Отменяет решение пользователя 2026-10-17 о «без склейки», найденное
дефектом при приёмке T7.120.

Закреплено (номера взяты из постановки T7.120a):
- т1: интерфейс 1117709 с формулировкой `cc75e97f` — пересказ Банка России, а не ambiguous;
- т2: страница cbr.ru/press/event/?id=32879 — собственная публикация Банка России (не пересказ инФОМ);
- т3: стенд T7.85 (kommersant 8294535 + forbes 552221, формулировка `391c4388`) — снова пересказ ЦБ;
- т8: пересказы, называющие только инФОМ, получают ОДНОГО родителя — корень семьи cbr;
- т9: самосогласованность словаря: семья без цепочек, корень обязан быть корнем.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    PRIMARY_SOURCES,
    collapse_family_candidates,
    detect_source_attribution,
    family_home_hosts,
    family_primary_key,
    primary_source,
)
from apps.research_proxy.value_attribution import (
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    STATUS_DERIVATIVE,
    STATUS_SELF_PRIMARY,
    attribute_value_in_fragment,
    self_publication_candidate,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t7120a"

STAND_URI: dict[str, str] = {
    "interfax_1117709": "https://www.interfax.ru/business/1117709",
    "cbr_press_event_32879": "https://cbr.ru/press/event/?id=32879",
}
#: sha256(normalized) стенда — фикстура обязана остаться той же страницей
STAND_SHA: dict[str, str] = {
    "interfax_1117709": "f75430effb3d9efe",
    "cbr_press_event_32879": "bd8895678bc46ab8",
}

#: стендовые страницы T7.85 (те же копии, что `tests/unit/test_attribution_fixtures_t785.py`)
STAND_T785_SHA: dict[str, tuple[str, str]] = {
    "kommersant_8294535": ("https://www.kommersant.ru/doc/8294535", "20c19e0a651157d2"),
    "forbes_552221": (
        "https://www.forbes.ru/finansy/552221-cb-soobsil-o-roste-inflacionnyh-ozidanij-rossian"
        "-v-dekabre-2025-goda",
        "f98ef1367a6d2860",
    ),
}

#: формулировка `cc75e97f` со стенда (утверждение, чьё значение ищется в фрагменте)
STATEMENT_142 = "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2%."
#: формулировка `391c4388` со стенда T7.85
STATEMENT_145 = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ для Банка России)."
)


def _text(name: str) -> str:
    path = FIXTURE_DIR / f"{name}.normalized.txt"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest().startswith(STAND_SHA[name]), (
        f"фикстура {name} больше не стендовая страница ({STAND_URI[name]})"
    )
    return raw.decode("utf-8")


def _text_t785(name: str) -> str:
    path = Path(__file__).parent.parent / "fixtures" / "t785" / f"{name}.normalized.txt"
    raw = path.read_bytes()
    uri, sha = STAND_T785_SHA[name]
    assert hashlib.sha256(raw).hexdigest().startswith(sha), f"фикстура {name} больше не {uri}"
    return raw.decode("utf-8")


# ─── т9: самосогласованность словаря с новым полем семьи ──────────────────────────


def test_registry_family_field_is_self_consistent() -> None:
    """Семейная запись обязана указывать на существующий корень, а корень — быть корнем:
    цепочек семей словарь не допускает (иначе `family_primary_key` решал бы «член члена»)."""
    keys = {spec.key for spec in PRIMARY_SOURCES}
    assert len(keys) == len(PRIMARY_SOURCES), "в словаре две записи с одним ключом"
    for spec in PRIMARY_SOURCES:
        if spec.family_key is None:
            continue  # корень (или одиночная запись) — поля семьи нет, это корректно
        assert spec.family_key in keys, f"{spec.key}: семья ссылается на ключ вне словаря"
        assert spec.family_key != spec.key, f"{spec.key}: семья не может быть сама себе корнем"
        root = primary_source(spec.family_key)
        assert root is not None, f"{spec.key}: ссылка на отсутствующий корень {spec.family_key}"
        assert root.family_key is None, f"{spec.key}: корень {root.key} сам состоит в семье — цепочка"
        assert family_primary_key(spec.key) == root.key
        assert family_primary_key(root.key) == root.key  # идемпотентно: корень — он же корень
        own = set(spec.home_hosts)
        assert own <= set(family_home_hosts(spec.key)), "домашние хосты записи теряются в семье"


def test_infom_is_the_only_family_member_and_its_root_is_cbr() -> None:
    """Семей в словаре ровно одна, и она заведена именно этим решением: инФОМ → Банк России.
    Закрытый список — новое семейное склеивание возможно только как явное решение (ADR-0029 §24)."""
    members = {spec.key: spec.family_key for spec in PRIMARY_SOURCES if spec.family_key is not None}
    assert members == {"infom": "cbr"}, members
    hosts = family_home_hosts("infom")
    assert "infom.org.ru" in hosts and "cbr.ru" in hosts, hosts
    # записи вне семьи семья не расширяет: ни одна прежняя домашняя проверка не изменилась
    for key in ("rosstat", "minec"):
        spec = primary_source(key)
        assert spec is not None
        assert family_home_hosts(key) == spec.home_hosts, key
    assert family_primary_key("нет-такого-ключа") == "нет-такого-ключа"


# ─── т1: интерфейс 1117709 и формулировка cc75e97f → Банк России, не ambiguous ─────


def test_interfax_infom_poll_for_cbr_is_derivative_to_cbr_not_ambiguous() -> None:
    """Одновременно названные «инФОМ» и «Банк России» — не разночтение двух первоисточников:
    опрос заказан и опубликован Банком России, решение носит имя корня семьи."""
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI["interfax_1117709"],
        text=_text("interfax_1117709"),
        claim_statement=STATEMENT_142,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.primary_name == "Банк России", decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
    # основание — дословная связка опроса с заказчиком/публикатором ЦБ, а не отказ-разночтение
    assert 'с 13,7% до 14,2%, следует из опроса "инФОМ", проведенного по заказу Банка России' in (
        decision.basis_fragment
    ), decision.basis_fragment


def test_cbr_family_does_not_split_interfax_page_into_two_primaries() -> None:
    """Фрагменты страницы, называющие семью, не плодят независимые группы: кандидат один — корень."""
    collapsed = collapse_family_candidates({"infom": "основание опроса", "cbr": "основание ЦБ"})
    assert collapsed == {"cbr": "основание ЦБ"}, collapsed  # корень назван прямо — его основание
    collapsed = collapse_family_candidates({"infom": "основание опроса"})
    assert collapsed == {"cbr": "основание опроса"}, collapsed  # член семьи решает под именем корня


# ─── т2: страница cbr.ru, называющая опрос инФОМ, — собственная публикация ЦБ ──────


def test_cbr_press_event_page_is_own_publication_of_cbr_not_retelling_of_infom() -> None:
    """Страница на домашнем хосте любой части семьи — страница производителя, а не пересказ
    (T7.120a): раньше она получала `derivative → инФОМ`, теперь — собственная публикация ЦБ."""
    text = _text("cbr_press_event_32879")
    page = detect_source_attribution(canonical_uri=STAND_URI["cbr_press_event_32879"], text=text)
    assert page.status == STATUS_SELF_PRIMARY, page  # ни указателя, ни «пересказа инФОМ»
    value = attribute_value_in_fragment(
        canonical_uri=STAND_URI["cbr_press_event_32879"],
        text=text,
        claim_statement=STATEMENT_142,
    )
    assert value.status == STATUS_SELF_PRIMARY, value
    candidate = self_publication_candidate(
        canonical_uri=STAND_URI["cbr_press_event_32879"],
        text=text,
        claim_statement=STATEMENT_142,
    )
    assert candidate is not None, value
    assert candidate.primary_key == "cbr", candidate  # записывает ЦБ, а не «публикацию инФОМ»
    assert candidate.primary_name == "Банк России", candidate


# ─── т3: стенд T7.85 (kommersant + forbes, формулировка 391c4388) → снова ЦБ ──────


def test_t785_stand_retellings_are_derivative_to_cbr_again() -> None:
    """Дефект `391c4388` возвращается в прежнее исправное состояние: обе relay-страницы —
    пересказы опубликованного ЦБ опроса, а не «ambiguous из-за двух названных имён»."""
    for name in ("kommersant_8294535", "forbes_552221"):
        uri = STAND_T785_SHA[name][0]
        decision = attribute_value_in_fragment(
            canonical_uri=uri, text=_text_t785(name), claim_statement=STATEMENT_145
        )
        assert decision.status == STATUS_DERIVATIVE, (name, decision)
        assert decision.primary_key == "cbr", (name, decision)
        assert decision.primary_name == "Банк России", (name, decision)
        # значение стоит дальше фразы атрибуции — окно публикации, как и в эпоху T7.85
        assert decision.pairing == PAIRING_PUBLICATION_WINDOW, (name, decision)
        assert "инФОМ" in decision.basis_fragment, (name, decision.basis_fragment)
        # падеж живой фразы («по заказу Банка России», «опубликованного ЦБ») не подменяем
        assert any(
            marker in decision.basis_fragment
            for marker in ("Банк России", "Банка России", "Банком России", "ЦБ")
        ), (name, decision.basis_fragment)


# ─── т8: пересказ, называющий только инФОМ, получает одного родителя — корень семьи ─


def test_retellings_naming_only_infom_share_a_single_cbr_parent_key() -> None:
    """Раньше страница, называющая только инФОМ, вела к родителю «инФОМ», а страница,
    называющая ЦБ, — к родителю «Банк России»: две склейки вместо одной семьи. Теперь обе
    ведут к одному ключу-корню — группировщик T7.117 видит одного родителя."""
    decisions = [
        attribute_value_in_fragment(
            canonical_uri=STAND_URI["interfax_1117709"],
            text=_text("interfax_1117709"),
            claim_statement=STATEMENT_142,
        ),
        attribute_value_in_fragment(
            canonical_uri=STAND_T785_SHA["kommersant_8294535"][0],
            text=_text_t785("kommersant_8294535"),
            claim_statement=STATEMENT_145,
        ),
    ]
    assert all(d.status == STATUS_DERIVATIVE for d in decisions), decisions
    assert {d.primary_key for d in decisions} == {"cbr"}, decisions
    assert family_primary_key("infom") == "cbr"
