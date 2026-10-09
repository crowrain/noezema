"""T7.85b: в окне публикации чужое сокращённое имя и чужой глагол фиксации — тоже вето (ADR-0029).

Дефект, найденный менеджером при приёмке T7.85a и воспроизведённый на `7981f08`
(canonical_uri = https://news.example.ru/a):

    text   = «Согласно опубликованному Банком России обзору, ожидания стабильны.
              ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.»
    claim  = «Наблюдаемая инфляция составила 15,2%.»
    было   → derivative → Банк России (pairing=publication_window)
    должно → не пересказ

Причина: аббревиатура организации вне словаря первоисточников написана БЕЗ кавычек, а глагол
фиксации («зафиксировал») не входил ни в список действующих лиц, ни в список оборотов оценки —
то есть окно публикации не видело ни одного признака другого источника числа.

Правило FIX (та же зона вето и ТОТ же режим: только «окно публикации», строгий режим не тронут):
признаком другого источника числа становятся также (а) аббревиатура организации — токен из ≥2
заглавных букв, не замазанный как алиас словаря и не входящий в белый список обозначений
показателей/географии; (б) глагол фиксации/измерения КАК ДЕЙСТВИЕ ДРУГОГО ЛИЦА — то есть стоящий
вплотную к незамазанному имени собственному. Направление риска прежнее: лишний отказ допустим,
ложная склейка — нет.

Отрицательный контроль (п.2 задачи): «Росстат показал…», «Банк России зафиксировал…» — это сам
первоисточник. Алиасы словаря замазываются ДО проверки признака, и на месте имени остаются пробелы
длиной с имя: соседства «имя␠глагол» нет → вето молчит. Так же молчит белый список (ИПЦ, ВВП, CPI,
РФ, США) и пассивная форма глагола без действующего лица.
"""

from __future__ import annotations

import pytest

import apps.research_proxy.value_attribution as va
from apps.research_proxy.source_attribution import primary_source
from apps.research_proxy.value_attribution import (
    INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS,
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)

pytestmark = pytest.mark.unit

NEWS_URI = "https://news.example.ru/a"

#: общий префикс оконного режима: публикация первоисточника названа в первом предложении блока,
#: а число стоит дальше — ровно та форма, где T7.85a уже умеет решать и где живёт дефект
PREFIX_CBR = "Согласно опубликованному Банком России обзору, ожидания стабильны. "


def _decide(text: str, claim: str) -> object:
    return attribute_value_in_fragment(
        canonical_uri=NEWS_URI, text=text, claim_statement=claim
    )


# ─── 1: дефект — сокращённое имя оценщика и глагол фиксации внутри окна ────────────────


@pytest.mark.parametrize(
    ("tail", "claim", "expected_status"),
    [
        # сам воспроизведённый дефект приёмки: аббревиатура вне словаря + «зафиксировал»
        (
            "ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.",
            "Наблюдаемая инфляция составила 15,2%.",
            "no_value_attribution",
        ),
        # смешанная аббревиатура + «насчитал»
        ("ФОМ насчитал наблюдаемую инфляцию 14,1%.", "Наблюдаемая инфляция составила 14,1%.", "no_value_attribution"),
        # придаточная форма без «по оценке»: одна аббревиатура, глагола нет — вето всё равно должно быть
        ("По данным РАНХиГС, инфляция составила 12,3%.", "Инфляция составила 12,3%.", "no_value_attribution"),
        # «выяснил» + аббревиатура: придаточная форма с глаголом измерения
        ("ЦСР выяснил, что инфляция составила 11,9%.", "Инфляция составила 11,9%.", "no_value_attribution"),
        # имя с дефисом и глагол «показал» (без кавычек)
        (
            "Левада-Центр показал наблюдаемую инфляцию 13,4%.",
            "Наблюдаемая инфляция составила 13,4%.",
            "no_value_attribution",
        ),
        # «замеры … показали»: измерительное действие другого лица
        ("Замеры НИУ ВШЭ показали инфляцию 12,8%.", "Инфляция составила 12,8%.", "no_value_attribution"),
    ],
)
def test_another_author_inside_the_publication_window_is_not_retelling(
    tail: str, claim: str, expected_status: str
) -> None:
    decision = _decide(PREFIX_CBR + tail, claim)
    assert not decision.is_derivative, decision
    assert decision.primary_key is None, decision
    assert decision.status == expected_status, decision
    # вето умеет только снимать кандидатов: решение остаётся честным отказом строгого режима
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


# ─── 2: сохранённые пересказы (п.2 задачи) — вето не должно срабатывать ────────────────


@pytest.mark.parametrize(
    ("text", "claim", "expected_key", "expected_pairing"),
    [
        # форма Коммерсанта: «Оценка … осталась на уровне» — имя показателя, а не чужая оценка;
        # «инФОМ» стоит ВНУТРИ фразы атрибуции (до конца зоны) → не признак
        (
            "Об этом следует из опубликованных 17 декабря данных опроса, проводимого «инФОМ» "
            "по заказу Банка России. Оценка текущих темпов роста цен при этом осталась на уровне 14,5%.",
            "Наблюдаемая населением годовая инфляция в декабре 2025 года составила 14,5%.",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # релиз Росстата: ИПЦ — имя показателя из белого списка, вето по нему быть не может
        (
            "По данным опубликованного Росстатом релиза, ИПЦ вырос на 5,59%.",
            "ИПЦ вырос на 5,59%.",
            "rosstat",
            PAIRING_VALUE_AND_PRIMARY,
        ),
        # то же окно про другое число блока (6,64%): чужих действующих лиц в зоне нет
        (
            "По данным опубликованного Банком России обзора, годовая инфляция в декабре составила 5,59%. "
            "В ноябре она составляла 6,64%.",
            "Годовая инфляция в ноябре 2025 года составила 6,64%.",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # «Росстат показал…» — сам первоисточник: алиас замазан, глагол остался без имени-актора
        (
            "По данным опубликованного Росстатом релиза, годовая инфляция замедлилась до 5,59%. "
            "Росстат показал наблюдаемую инфляцию 15,2%.",
            "Наблюдаемая инфляция составила 15,2%.",
            "rosstat",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # обратный порядок «…показал Росстат» — тот же контроль
        (
            "По данным опубликованного Росстатом релиза, годовая инфляция замедлилась. "
            "Наблюдаемую инфляцию показал Росстат: 15,2%.",
            "Наблюдаемая инфляция составила 15,2%.",
            "rosstat",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # «Банк России зафиксировал…» — сам первоисточник (алиас маскируется раньше)
        (
            PREFIX_CBR + "Банк России зафиксировал оценку текущих темпов на уровне 14,5%.",
            "Наблюдаемая инфляция составила 14,5%.",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
        # «ЦБ РФ зарегистрировал…» — алиас словаря («цб рф»), не чужое действующее лицо
        (
            "Как следует из опубликованного ЦБ РФ мониторинга, ожидания выросли. "
            "ЦБ РФ зарегистрировал снижение на 1,2 п.п.",
            "Снижение составило 1,2 п.п.",
            "cbr",
            PAIRING_PUBLICATION_WINDOW,
        ),
    ],
)
def test_retellings_of_the_named_primary_survive(
    text: str, claim: str, expected_key: str, expected_pairing: str
) -> None:
    decision = _decide(text, claim)
    assert decision.is_derivative, decision
    assert decision.primary_key == expected_key, decision
    assert decision.pairing == expected_pairing, decision


# ─── 3: белый список обозначений показателей и географии не вызывает вето ──────────────


@pytest.mark.parametrize(
    ("tail", "claim"),
    [
        ("ИПЦ в РФ вырос, годовая инфляция составила 5,59%.", "Годовая инфляция составила 5,59%."),
        ("ВВП вырос на 1,9%, а инфляция составила 5,59%.", "Инфляция составила 5,59%."),
        (
            "По обзору CPI и PPI в РФ динамика та же: инфляция составила 5,59%.",
            "Инфляция составила 5,59%.",
        ),
        ("В США инфляция ускорилась, а в РФ годовая инфляция составила 5,59%.", "Инфляция в РФ составила 5,59%."),
        # пассивная форма глагола фиксации без действующего лица — тоже не признак
        ("Наблюдаемая инфляция зафиксирована на уровне 14,5%.", "Наблюдаемая инфляция составила 14,5%."),
    ],
)
def test_indicator_and_geography_abbreviations_are_not_an_actor(tail: str, claim: str) -> None:
    decision = _decide(PREFIX_CBR + tail, claim)
    assert decision.is_derivative, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == PAIRING_PUBLICATION_WINDOW, decision


def test_whitelist_entries_are_not_primary_sources_and_not_actors() -> None:
    """Ни одна запись белого списка не является первоисточником словаря (иначе вето нельзя было
    бы снять), и ни одна не проходит как действующее лицо в позиции имени."""

    assert INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS  # список обязан жить в модуле, а не в тесте
    for entry in INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS:
        assert not va._is_dictionary_alias_name(entry), entry
        assert primary_source(entry.lower()) is None, entry
        assert not va._fixation_verb_sign(f" {entry} зафиксировал наблюдаемую инфляцию "), entry


# ─── 4: признаки на уровне зоны (без окна) — ровно то, что добавлено ────────────────────


@pytest.mark.parametrize("zone", [" ВЦИОМ наблюдал инфляцию ", " РАНХиГС дала 12,3% ", " По данным ЦМАКП, 12,3% "])
def test_abbreviation_outside_the_dictionary_is_a_sign(zone: str) -> None:
    assert va._other_source_sign(zone), zone


@pytest.mark.parametrize(
    "zone",
    [
        # алиас словаря замазан → аббревиатурой не считается («цб», «цб рф», «фнс» в верхнем регистре)
        " По данным ЦБ РФ, оценка осталась на уровне 14,5%. ",
        " РОССТАТ опубликовал релиз о 5,59% ",
        # показатель и география — не действующие лица
        " ИПЦ вырос, ВВП снизился, CPI составил 5,59% ",
        " годовая инфляция в РФ и США составила 5,59% ",
        # одна заглавная буква: начало предложения или обычное существительное
        " Инфляция ускорилась, оценка осталась на уровне 14,5%. ",
    ],
)
def test_masked_aliases_and_whitelist_are_not_a_sign(zone: str) -> None:
    assert not va._other_source_sign(zone), zone


@pytest.mark.parametrize(
    "zone",
    [
        " ВЦИОМ зафиксировал наблюдаемую инфляцию ",
        " ФОМ насчитал 14,1% ",
        " Левада-Центр показал наблюдаемую инфляцию ",
        " замеры НИУ ВШЭ показали инфляцию ",
        " ЦСР выяснил значение показателя ",
        " Ромир измерил инфляцию ",
        " снижение зарегистрировал ФОМ ",  # обратный порядок: глагол перед именем
    ],
)
def test_fixation_verb_next_to_a_foreign_name_is_a_sign(zone: str) -> None:
    assert va._other_source_sign(zone), zone


@pytest.mark.parametrize(
    "zone",
    [
        # первоисточник замазан → соседства «имя␠глагол» нет, глагол без действующего лица молчит
        " По данным опубликованного Росстата релиза, годовую инфляцию показал 15,2% ",
        " Как сообщил Банк России, оценку зафиксировал 14,5% ",
        # пассив без имени-актора
        " наблюдаемая инфляция зафиксирована на уровне 14,5% ",
        # глагол фиксации отделён от имени вторым оборотом: связка обязана быть соседней
        " Инфляция, по опубликованным данным релиза, показала замедление ",
        # «показатель» — имя показателя, а не глагол «показал»
        " Показатель ожидаемой инфляции вырос до 13,7% ",
    ],
)
def test_primary_or_anonymous_fixation_verb_is_not_a_sign(zone: str) -> None:
    assert not va._other_source_sign(zone), zone


# ─── 5: вето живёт только внутри оконного режима; номер метода поднят ───────────────────


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        (PREFIX_CBR + "ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.", "Наблюдаемая инфляция составила 15,2%."),
        (PREFIX_CBR + "ФОМ насчитал наблюдаемую инфляцию 14,1%.", "Наблюдаемая инфляция составила 14,1%."),
    ],
)
def test_strict_mode_is_untouched_by_the_new_signs(
    monkeypatch: pytest.MonkeyPatch, text: str, claim: str
) -> None:
    """Оконный режим (и живущее в нём вето) выключен entirely — решения строгого режима те же,
    что и до T7.85b: новые признаки не просочились за пределы `_publication_decision`."""

    monkeypatch.setattr(va, "_publication_decision", lambda **kwargs: None)
    decision = attribute_value_in_fragment(canonical_uri=NEWS_URI, text=text, claim_statement=claim)
    assert not decision.is_derivative, decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


def test_publication_window_gates_are_unchanged() -> None:
    """Гейты окна не расширены и не сужены: изменён только список признаков внутри зоны вето."""

    assert va.PUBLICATION_WINDOW_CHARS == 400
    assert va._PUBLICATION_TAIL_CHARS == 40
    assert va.ATTRIBUTION_WINDOW_CHARS == 120


def test_method_version_is_v5_after_t785c() -> None:
    """Поведение метода изменилось снова (T7.85c: окно публикации только для русского текста и
    латинское имя собственного вне словаря) → номер поднят; переатрибуция пересматривает записи
    эпох v2, v3 и v4 по тому же предикату «метка записи ≠ текущий метод»
    (tests/scenario/test_reattribute_stale_records.py)."""

    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v5"
