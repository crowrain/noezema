"""Unit (pure): словарь первоисточников эпохи T7.120 и починка pairing (решение
пользователя 2026-10-17 «recognize retellings by non-state producers before expanding
the curator», ADR-0029 дополнение).

Что закреплено этим файлом:

1. Десять новых записей `PRIMARY_SOURCES` (superjob, hh, sbercib, vtb, alfa, gpb, psb,
   infom, cmakp, sberindex) самосогласованы: домашний хост совпадает с домашним URI,
   имя матчит собственного алиаса, страница производителя называет себя self_primary,
   чужая страница с шаблоном атрибуции — derivative на правильный якорь. С T7.120a
   (ADR-0029 §24) «правильный якорь» для инФОМ — корень его семьи `cbr` (опрос заказан и
   опубликован Банком России), остальные девять записей вне семей.
2. Короткие имена матчены только отдельными словами с границами: «ВТБ24» и «ВТБР» —
   другие имена (алиас не попадает); «ВТБ Мои Инвестиции» и «ВТБ Капитал» — тот же ключ;
   голое «Сбер»/«Сбербанк» не приписывается SberCIB («Сбер CIB», «Сбербанк CIB» — приписывается).
3. Стендовый случай 2 (83e7a1c8): утверждение с «голым» десятичным значением обязано
   подтвердить свою форму числа в фрагменте атрибуции — прежнее derivative→Росстат по
   фрагменту «в декабре 2008 … выросло на четверть», где числа 9,6 нет, заменено честным
   отказом. Целочисленные утверждения прежнего режима не затронуты.
4. Relay-гвард (б) получает белый список «аналитики + алиас словаря эпохи T7.120» —
   подразделение названного производителя; гвард над алиасами государственных ведомств
   прежний («аналитики ЦБ» остаётся третьим актором).
5. Стендовый случай 1 (5a75ebf6): пересказ SuperJob на Rambler приписывается SuperJob,
   группа чистого графа (якорь + страница + Rambler + ЦБ по страничной записи) одна,
   подпись действующей оценки «Опубликовано производителем (SuperJob)», Ф3 по формулировке
   с названным SuperJob молчит.
6. Консервативная сторона: «по данным hh.ru» у ДРУГОГО числа не переносит значение на
   hh.ru; «hh.ru регистрируют … 9,6» остаётся честным отказом (шаблон «регистрирует» в
   закрытый список намеренно не добавлен).

Тесты не обращаются в сеть и не поднимают сессионных зависимостей.
"""

from __future__ import annotations

import uuid

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    PRIMARY_SOURCES,
    _producer_analyst_actor_spans,
    detect_source_attribution,
    is_home_host,
    named_primary_keys,
    primary_source,
)
from apps.research_proxy.value_attribution import (
    PAIRING_VALUE_AND_PRIMARY,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
    claim_match_values,
)
from packages.memory.honesty_report import producer_not_named_flag, producer_signature
from packages.memory.independence import group_source_graph
from packages.memory.source_graph import GraphNode, SourceGraphAssembly, grouping_arguments

pytestmark = pytest.mark.unit

NEW_KEYS = ("superjob", "hh", "sbercib", "vtb", "alfa", "gpb", "psb", "infom", "cmakp", "sberindex")

NEWS_URI = "https://news.example.ru/t7120"


def _decide(text: str, claim: str, uri: str = NEWS_URI):
    return attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=claim)


def _sentence(name: str) -> str:
    """Предложение атрибуции имени первоисточника. Имена с точкой (hh.ru) страничный
    фрагментатор режет по точке — для них обратный порядок, где значение и шаблон остаются
    в одном фрагменте вместе с усечённым алиасом (page normalization не менялась)."""
    if "." in name:
        return f"Инфляция в России ускорилась до 3,5%, как сообщает {name}."
    return f"Как сообщает {name}, инфляция в России ускорилась до 3,5%."


# ─── версия метода: решения v10/v15 отличаются от v9/v14 (T7.120a поднимает обе метки) ──


def test_method_versions_bumped_together() -> None:
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v10"
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v17"


# ─── 1: самосогласованность десяти новых записей (t7) ───────────────────────────────────


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_entries_are_self_consistent(key: str) -> None:
    spec = primary_source(key)
    assert spec is not None and spec.key == key, key
    sentence = _sentence(spec.name)
    # домашний URI обязан быть домашним хостом записи (тот же предикат, что у детекторов)
    page = detect_source_attribution(canonical_uri=spec.home_uri, text=sentence)
    assert (page.status, page.primary_key) == ("self_primary", None), (key, page.status)
    # имя производителя матчит собственного алиаса словаря
    assert key in named_primary_keys(spec.name), (key, spec.name)
    assert is_home_host(spec.home_uri, spec.home_hosts), key


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_entries_are_named_primaries_on_foreign_pages(key: str) -> None:
    """Чужая страница с шаблоном атрибуции и именем производителя — derivative на якорь записи."""
    spec = primary_source(key)
    assert spec is not None
    sentence = _sentence(spec.name)
    # T7.120a (ADR-0029 §24): якорь решения — корень семьи записи (для `infom` это `cbr`),
    # остальные записи вне семей — свой собственный ключ
    root = spec.family_key or key
    page = detect_source_attribution(canonical_uri=NEWS_URI, text=sentence)
    assert (page.status, page.primary_key) == ("derivative", root), (key, page.status)
    # основание — фрагмент с именем (для `hh.ru` — усечённый фрагментатором алиас «hh»)
    assert page.basis_fragment and spec.name.split(".", 1)[0] in page.basis_fragment, key
    value = _decide(sentence, "Инфляция в России в 2026 году ускорилась до 3,5%.")
    assert (value.status, value.primary_key) == ("derivative", root), (key, value.status)
    assert value.pairing == PAIRING_VALUE_AND_PRIMARY, key


def test_registry_home_uris_are_distinct_from_official_anchors() -> None:
    """Ни одна новая запись не совпадает домашним URI с прежними (никто не «склеивается»
    с официальным ведомством на уровне реестра)."""
    old = {s.home_uri for s in PRIMARY_SOURCES if s.key not in NEW_KEYS}
    new = {primary_source(k).home_uri for k in NEW_KEYS if primary_source(k)}
    assert not (old & new), old & new
    # инФОМ сохраняет собственный домашний URI (реестр его не «склеивает» с ЦБ на уровне
    # записей), но T7.120a (ADR-0029 §24) состоит в семье `cbr`: отдельный URI ≠ отдельный
    # производитель — решения по инФОМ сворачиваются к корню семьи
    infom = primary_source("infom")
    cbr = primary_source("cbr")
    assert infom.home_uri != cbr.home_uri
    assert infom.family_key == "cbr", "семейная склейка возвращена после заблуждения T7.120"


# ─── 2: границы коротких имён (t4) ───────────────────────────────────────────────────────


def test_vtb24_and_vtbr_are_not_vtb() -> None:
    """«ВТБ24» и «ВТБР» — другие имена: алиас `\bвтб\b` в них не попадает (граница слова
    у цифр и букв)."""
    for name in ("ВТБ24 отчитался о прибыли в 120 млрд рублей.", "ВТБР сохранил условия по вкладам."):
        assert "vtb" not in named_primary_keys(name), name


def test_vtb_sub_brands_are_the_same_producer() -> None:
    for name in (
        "ВТБ Мои Инвестиции повысил прогноз инфляции до 5,5% в 2026 году.",
        "ВТБ Капитал сохранил оценку роста ВВП на уровне 1,3%.",
    ):
        assert "vtb" in named_primary_keys(name), name


def test_plain_sberbank_is_not_sbercib() -> None:
    """Голое «Сбербанк»/«Сбер» алиасом SberCIB не стало: без аббревиатуры CIB — никакого
    приписывания (риск неразличимости назван в ADR-0029 дополнение)."""
    for name in (
        "Сбербанк повысил ставки по некоторым ипотекам до 30%.",
        "Сбер отчитался о чистой прибыли 1,9 трлн рублей.",
    ):
        keys = named_primary_keys(name)
        assert "sbercib" not in keys and "sberindex" not in keys, (name, keys)


def test_sbercib_requires_the_cib_suffix() -> None:
    assert "sbercib" in named_primary_keys("Сбер CIB повысил прогноз инфляции до 5,9%.")
    assert "sbercib" in named_primary_keys("SberCIB опубликовал обзор: инфляция 5,9%.")
    assert "sbercib" in named_primary_keys("Сбербанк CIB сохранил оценку 5,9%.")


def test_sber_index_alias_variants() -> None:
    assert "sberindex" in named_primary_keys("СберИндекс замерил потребительскую активность на уровне 9,6%.")
    assert "sberindex" in named_primary_keys("по данным Сбер Индекса активность составила 9,6%.")


def test_infom_alias_does_not_match_informatsiya() -> None:
    keys = named_primary_keys("По данным Информационного источника, инфляция 5,59%.")
    assert "infom" not in keys
    assert "infom" in named_primary_keys("опрос, проводимый «инФОМ» по заказу Банка России")


def test_hh_short_alias_matches_index_names() -> None:
    assert "hh" in named_primary_keys("hh-индекс вырос на 1,9%.")
    assert "hh" in named_primary_keys("hh.индекс замедлился до 8,7%.")
    assert "hh" in named_primary_keys("По данным HeadHunter, вакансии подорожали на 3%.")


# ─── 3: починка pairing для «голого» значения (t2; стендовый случай 83e7a1c8) ──────────


CASE2_TEXT = (
    "По данным Росстата, в декабре 2008 года число безработных выросло на четверть.\n\n"
    "Аналитики платформы hh.ru регистрируют hh-индекс рынка труда в России на уровне 9,6 "
    "в январе 2026 года."
)
CASE2_CLAIM = "Индекс рынка труда hh.ru (hh-индекс) в России в январе 2026 года составил 9,6."


def test_case2_bare_value_must_stand_in_the_same_fragment() -> None:
    """Стендовый случай 2. Утверждение называет значение числом без единицы (голое 9,6),
    в росстатовском фрагменте основания этого числа нет — pairing обязан снять пару.

    Было (v13): derivative→Росстат, basis «По данным Росстата, в декабре 2008 года число
    безработных выросло на четверть» — значение 9,6 в том фрагменте отсутствовало.
    Стало (v15): честный отказ `value_not_attributed` — записи о производителе нет;
    запись не ставится ни Росстату, ни hh.ru (у hh.ru нет ни шаблона, ни relay-посылки)."""
    decision = _decide(CASE2_TEXT, CASE2_CLAIM, "https://hr-ratings.example.ru/hh-index")
    assert decision.status == "value_not_attributed", decision
    assert decision.primary_key is None, decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
    # профиль голого значения существует и участвует в сравнении (иначе тест не о дефекте)
    assert "9.6" in _bare_of(CASE2_CLAIM)


def _bare_of(statement: str) -> frozenset[str]:
    from apps.research_proxy.value_attribution import _claim_bare_values

    return _claim_bare_values(statement)


def test_case2_no_bare_integer_legacy_regime_preserved() -> None:
    """Целые значения («на четверть», годы) в голые значения не собираются: прежнее
    поведение строгого pairing для утверждений без десятичного числа не изменено."""
    claim = "Число безработных в России выросло на четверть к декабрю 2008 года."
    assert _bare_of(claim) == frozenset()
    decision = _decide(CASE2_TEXT.split("\n\n")[0], claim, "https://news.example.ru/legacy")
    assert (decision.status, decision.primary_key) == ("derivative", "rosstat"), decision


def test_bare_values_never_add_pairings() -> None:
    """Фильтр голого значения только снимает пары: там, где значение стоит в названном
    фрагменте, derivative остаётся (название hh.ru и индекс в одном фрагменте — strict
    pairing; из-за фрагментаторской резки «hh.ru» по точке взят обратный порядок формы)."""
    text = "Индекс рынка труда России в январе 2026 года вырос на 9,6 пункта, по данным hh.ru."
    decision = _decide(text, CASE2_CLAIM, "https://news.example.ru/bare-ok")
    assert (decision.status, decision.primary_key) == ("derivative", "hh"), decision


def test_dotted_alias_is_cut_by_the_fragmenter_residual() -> None:
    """Честный residual, зафиксирован вместо лечения: `_FRAGMENT_SPLIT` режет «hh.ru» по
    точке, и в прямой форме «По данным hh.ru, … число» пара «шаблон+алиас+число» разрывается —
    приписывания нет вовсе (no_value_attribution). Нормализация страницы — стоп-критерий
    T7.120 и не менялась; рабочие формы алиаса — `hh` с дефисом («hh-индекс»), `headhunter`
    и обратный порядок предложения."""
    text = "По данным hh.ru, индекс напряжённости рынка труда в январе вырос на 1,9%."
    assert "hh" in named_primary_keys(text)
    page = detect_source_attribution(canonical_uri=NEWS_URI, text=text)
    assert page.status == "no_value_attribution", page.status


def test_hh_registriruet_form_stays_honest_refusal() -> None:
    """«hh.ru регистрируют … на уровне 9,6%» — честный отказ: глагол «регистрировать»
    в закрытые списки (шаблоны атрибуции и измерительные маркеры) намеренно не добавлен,
    relay требует слово прогноза; шаблона атрибуции в форме тоже нет — атрибуции вообще
    нет (`no_value_attribution`). Случай остаётся без записи (ADR-0029 дополнение)."""
    text = "Аналитики платформы hh.ru регистрируют hh-индекс рынка труда на уровне 9,6% в январе 2026 года."
    claim = "Индекс рынка труда России в январе 2026 года составил 9,6%."
    assert "hh" in named_primary_keys(text)  # производитель назван — записи о нём это не меняет
    decision = _decide(text, claim, "https://hr-ratings.example.ru/hh-index-2")
    assert decision.status == "no_value_attribution", decision
    assert decision.primary_key is None, decision


# ─── 4: белый список relay-гварда (б) (t3) ──────────────────────────────────────────────


CLAIM_VTB = "Прогноз инфляции в России на 2026 год составляет 5–6%."


def test_template_form_attributes_vtb() -> None:
    text = "По оценкам аналитиков ВТБ, инфляция в России ускорится до 5–6% в 2026 году."
    decision = _decide(text, CLAIM_VTB)
    assert (decision.status, decision.primary_key) == ("derivative", "vtb"), decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


def test_bare_analyst_form_of_named_producer_is_a_retelling() -> None:
    """«Аналитики ВТБ ожидают … 5–6%» — подразделение самого ВТБ: relay разрешён белым
    списком гварда (б), значение приписывается ВТБ. Было (v8/v13): честный отказ
    `value_not_attributed` («аналитики» — третий актор, а ВТБ вне словаря)."""
    text = "Аналитики ВТБ ожидают, что инфляция в России ускорится до 5–6% в 2026 году."
    decision = _decide(text, CLAIM_VTB)
    assert (decision.status, decision.primary_key) == ("derivative", "vtb"), decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


def test_whitelist_spans_are_scoped_to_t7120_keys() -> None:
    """Белый список действует только для добавленных T7.120 ключей: «аналитики» перед
    алиасом государственного ведомства или вне словаря остаётся действующим лицом."""
    assert _producer_analyst_actor_spans("Аналитики ВТБ ожидают ускорения") != []
    assert _producer_analyst_actor_spans("Аналитики Альфа-Банка прогнозируют рост") != []
    assert _producer_analyst_actor_spans("Аналитики ЦБ прогнозируют 6,3%") == []
    assert _producer_analyst_actor_spans("Аналитики Сбербанка ожидают 6,5%") == []
    # вплотности нет — конец предложения между лицом и алиасом
    assert _producer_analyst_actor_spans("Аналитики ждут новых данных. ВТБ повысил прогноз.") == []


def test_official_analyst_veto_still_wins() -> None:
    """Отрицательный контроль белого списка (та же форма, что T7.85a случай (5)):
    «аналитики ЦБ» в чужом окне — прежний ambiguous, склейки нет."""
    text = (
        "По данным опроса Росстата, инфляция в 2025 году составила 5,59%. "
        "Аналитики ЦБ прогнозируют инфляцию 6,3% в 2026 году."
    )
    decision = _decide(text, "Прогноз инфляции 6,3%.")
    assert decision.status == "ambiguous_primaries", decision


def test_alfa_forecast_relay_is_recognized() -> None:
    """Форма страницы со словом прогноза («Альфа-Банк повысил прогноз инфляции … до 7,5%») —
    relay T7.98 про названного производителя: derivative→alfa (было: честный отказ,
    Альфа-Банк был третьим актором вне словаря; стенд 07126d06)."""
    text = (
        "С учётом ограничений со стороны предложения Альфа-Банк повысил прогноз инфляции "
        "по итогам 2026 года до 7,5%."
    )
    claim = "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%."
    decision = _decide(text, claim)
    assert (decision.status, decision.primary_key) == ("derivative", "alfa"), decision


# ─── 5: консервативная сторона не ослаблена (t6) ─────────────────────────────────────────


def test_hh_near_another_number_does_not_take_the_claim_value() -> None:
    """«По данным hh.ru» стоит у ДРУГОГО числа (1,9%), а значение утверждения (9,6%) — в
    фрагменте без атрибуции: приписывания нет, честный отказ прежний. Форма с прямой
    ссылкой на hh.ru дополнительно упирается в резку алиаса фрагментатором (см.
    `test_dotted_alias_is_cut_by_the_fragmenter_residual`) — отказа это не отменяет."""
    text = (
        "По данным hh.ru, индекс напряжённости рынка труда в январе вырос на 1,9%. "
        "Активность работодателей в марте 2026 года составила 9,6%."
    )
    claim = "Активность работодателей в России в марте 2026 года составила 9,6%."
    assert "hh" in named_primary_keys(text)
    assert claim_match_values(claim) == frozenset({"9.6"})
    decision = _decide(text, claim)
    assert decision.status == "no_value_attribution", decision
    assert decision.primary_key is None, decision


# ─── 6: стендовый случай 1 (5a75ebf6): пересказ SuperJob, одна группа, подпись (t1) ─────


RAMBLER_SUPERJOB_FRAGMENT = (
    "По \nданным SuperJob\n, по итогам первого полугодия 2026 года количество вакансий на рынке "
    "сократилось на 19% год к году, а число резюме выросло на 33%. За два года разрыв стал "
    "ещё заметнее: вакансий меньше на 28%, резюме больше в 1,5 раза."
)
RAMBLER_CLAIM = (
    "По данным SuperJob, количество вакансий на рынке труда сократилось на 19%, а число "
    "резюме выросло на 33% по итогам первого полугодия 2026 года."
)


def test_rambler_retelling_is_attributed_to_superjob() -> None:
    decision = _decide(
        RAMBLER_SUPERJOB_FRAGMENT,
        RAMBLER_CLAIM,
        "https://www.rambler.ru/economics/54792081-superjob-ocenila-dolyu-kompaniy-gotovyh-povysit-zarplaty/",
    )
    assert (decision.status, decision.primary_key) == ("derivative", "superjob"), decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
    assert "SuperJob" in decision.basis_fragment


def test_one_group_anchor_page_retelling_and_parent_record() -> None:
    """Чистая сборка графа (тот же вход, что T7.117): якорь SuperJob, страница SuperJob
    (её страничная запись ведёт к ЦБ — прежний residual), Rambler (запись значения ведёт
    к якорю SuperJob — решение текущего метода v10). Все четверо — одна группа: доменное склеивание
    якоря со страницей и связи по записям значения. Основания групп названы."""
    anchor = GraphNode(source_id=uuid.uuid4(), canonical_uri="https://www.superjob.ru/",
                       content_hash="h-anchor", pointer_parent=None)
    page = GraphNode(source_id=uuid.uuid4(), canonical_uri="https://www.superjob.ru/pro/7000/",
                     content_hash="h-page", pointer_parent=None)
    cbr = GraphNode(source_id=uuid.uuid4(), canonical_uri="https://www.cbr.ru/",
                    content_hash="h-cbr", pointer_parent=None)
    rambler = GraphNode(source_id=uuid.uuid4(),
                        canonical_uri="https://www.rambler.ru/economics/54792081/",
                        content_hash="h-rambler", pointer_parent=None)
    assembly = SourceGraphAssembly(
        member_ids=tuple(n.source_id for n in (anchor, page, cbr, rambler)),
        nodes=(anchor, page, cbr, rambler),
        edges=(),
        corrections=(),
        effective_parent={page.source_id: cbr.source_id, rambler.source_id: anchor.source_id},
    )
    result = group_source_graph(*grouping_arguments(assembly))
    groups = result.groups
    assert groups[rambler.source_id] == groups[anchor.source_id] == groups[page.source_id]
    assert result.bases[rambler.source_id].startswith("parent:")


def test_producer_signature_and_f3_for_the_superjob_claim() -> None:
    """Подпись действующей оценки по единственной улике-записи (пересказ SuperJob):
    «Опубликовано производителем (SuperJob)» при одной группе и причине
    insufficient_independence (E1 назначает rules engine; его прежние пины не тронуты).
    Ф3 молчит, когда формулировка называет производителя, и включается без имени."""
    signature = producer_signature(
        names=[("superjob", "SuperJob")],
        group_count=1,
        has_counters=False,
        reasons=["insufficient_independence"],
    )
    assert signature == ("SuperJob", "superjob"), signature
    assert producer_not_named_flag(signature, RAMBLER_CLAIM) is None
    flag = producer_not_named_flag(signature, "Компании готовы повышать зарплаты, показал опрос.")
    assert flag is not None and flag.code == "f3", flag
