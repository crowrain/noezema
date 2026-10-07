"""Unit: детектор производности источника (T7.75, ADR-0029 вариант B).

Здесь нет ни БД, ни сети: проверяется чистая функция `detect_source_attribution` — тот же вход,
который хост имеет в момент записи provenance (canonical URI прочитанной страницы и её
нормализованный текст). Смысл тестов — границы консервативности (ADR-0029 «Риски»):

- пересказ с парой «значение + первоисточник» должен быть опознан (русский и английский);
- собственная оценка, голое упоминание, разночтение двух первоисточников и сама страница
  первоисточника — отметку ставить **нельзя**;
- ложное слияние дороже пропуска, поэтому отказ должен быть явным статусом, а не молчанием.
"""

from __future__ import annotations

import re

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    ATTRIBUTION_TEMPLATES,
    BASIS_FRAGMENT_CHARS,
    MAX_SCAN_CHARS,
    NEGATIVE_STATUSES,
    OWN_ASSESSMENT_MARKERS,
    PRIMARY_SOURCES,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    STATUS_SELF_PRIMARY,
    PrimarySource,
    detect_source_attribution,
    host_of,
    is_home_host,
    primary_source,
)
from packages.memory.independence import registrable_domain

pytestmark = pytest.mark.unit


def _derivative(uri: str, text: str):
    decision = detect_source_attribution(canonical_uri=uri, text=text)
    assert decision.status == STATUS_DERIVATIVE, decision.status
    return decision


# ─── пересказ релиза опознаётся (русский) ─────────────────────────────────


@pytest.mark.parametrize(
    ("uri", "text"),
    [
        (
            "https://www.interfax.ru/news/1015289841",
            "Инфляция в России по итогам 2025 года составила 5,59%, сообщается в релизе Росстата.",
        ),
        (
            "https://expert.ru/economics/protsent",
            "По данным Росстата, потребительские цены в 2025 году выросли на 5,59%.",
        ),
        (
            "https://ria.ru/20260116/inflyaciya",
            "Как сообщает Федеральная служба государственной статистики, "
            "потребительские цены за год выросли на 5,59%.",
        ),
        (
            "https://www.rbc.ru/finance/16/01/2026",
            "Инфляция, по оценкам Росстата, достигла 5,59% к концу 2025 года.",
        ),
        (
            "https://example.test/minfin",
            "Согласно релизу Минфина России, дефицит федерального бюджета вырос до 1,8 трлн рублей.",
        ),
    ],
)
def test_retelling_of_a_release_is_derivative(uri: str, text: str) -> None:
    decision = _derivative(uri, text)
    assert decision.primary_key is not None and decision.primary_name
    spec = primary_source(decision.primary_key or "")
    assert spec is not None and decision.parent_uri == spec.home_uri
    assert "http" in decision.parent_uri
    # основание решения записывается: оператор может проверить, чем хост руководствовался
    assert decision.basis_fragment
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v2"


# ─── пересказ опознаётся и по-английски ──────────────────────────────────


@pytest.mark.parametrize(
    ("uri", "text", "expected_key"),
    [
        (
            "https://newswire.example.com/economy",
            "According to Rosstat, inflation in Russia reached 5.59% in 2025.",
            "rosstat",
        ),
        (
            "https://wire.example.org/data",
            "Inflation, per data from the Federal State Statistics Service, stood at 5.59%.",
            "rosstat",
        ),
        (
            "https://wire.example.org/rates",
            "Reuters cites the Bank of Russia, which said the key rate came to 21.00%.",
            "cbr",
        ),
    ],
)
def test_english_retelling_is_derivative_too(uri: str, text: str, expected_key: str) -> None:
    decision = _derivative(uri, text)
    assert decision.primary_key == expected_key


# ─── самостоятельная страница не склеивается ни при каких условиях ────────


def test_own_estimate_mentioning_the_primary_is_not_derivative() -> None:
    text = (
        "Независимая оценка: по нашей методике инфляция 2025 года составила 6,4% — "
        "в отличие от официальных данных Росстата."
    )
    decision = detect_source_attribution(canonical_uri="https://research.example.org/method", text=text)
    assert decision.status == STATUS_OWN_ASSESSMENT
    assert decision.status != STATUS_DERIVATIVE


def test_english_own_estimate_is_not_derivative_even_when_it_names_rosstat() -> None:
    text = (
        "By our own estimate, differs from the official figure: we estimate inflation at 6.4%, "
        "while according to Rosstat it was 5.59%."
    )
    decision = detect_source_attribution(canonical_uri="https://study.example.org/inflation", text=text)
    assert decision.status == STATUS_OWN_ASSESSMENT


def test_own_assessment_veto_is_document_level() -> None:
    # маркер собственной оценки и атрибуция значения стоят в разных предложениях —
    # страница всё равно не помечается: сомнение трактуется в пользу независимости
    text = (
        "По данным Росстата, инфляция составила 5,59%. "
        "Мы оцениваем этот показатель иначе и приводим собственную методику."
    )
    assert detect_source_attribution(
        canonical_uri="https://lab.example.org/notes", text=text
    ).status == STATUS_OWN_ASSESSMENT


def test_bare_mention_of_the_primary_without_a_value_is_not_derivative() -> None:
    text = "Росстат опубликовал релиз 16 января 2026 года и сообщил о графике публикаций."
    decision = detect_source_attribution(canonical_uri="https://news.example.test/a", text=text)
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION


def test_dates_and_counts_alone_are_not_a_value() -> None:
    # «значение» требует единицы измерения или глагола-атрибута: дата публикации — не значение
    text = "Согласно данным Росстата, отчётность сдали 1 200 организаций в 2026 году."
    assert detect_source_attribution(
        canonical_uri="https://news.example.test/b", text=text
    ).status == STATUS_NO_VALUE_ATTRIBUTION


def test_ambiguity_between_two_primaries_is_refused() -> None:
    text = (
        "По данным Росстата, инфляция выросла до 5,59%, а по оценке ЦБ РФ ключевая ставка "
        "составляет 21%."
    )
    decision = detect_source_attribution(canonical_uri="https://news.example.test/c", text=text)
    assert decision.status == STATUS_AMBIGUOUS
    # отказ остаётся объяснимым: фрагмент-основание сохраняется
    assert decision.basis_fragment


def test_refusal_statuses_are_explicit_and_exhaustive() -> None:
    assert set(NEGATIVE_STATUSES) == {
        STATUS_OWN_ASSESSMENT,
        STATUS_AMBIGUOUS,
        STATUS_NO_VALUE_ATTRIBUTION,
        STATUS_SELF_PRIMARY,
    }
    for status in NEGATIVE_STATUSES:
        assert not detect_source_attribution(
            canonical_uri="https://news.example.test/x",
            text=f"{status} не является текстом страницы",
        ).is_derivative


# ─── первоисточник не бывает пересказом самого себя ───────────────────────


def test_primary_page_is_never_derivative_of_itself() -> None:
    text = "По данным Росстата, инфляция в регионе составила 6,1% за месяц."
    decision = detect_source_attribution(
        canonical_uri="https://rosstat.gov.ru/region/stat/inflyaciya", text=text
    )
    assert decision.status == STATUS_SELF_PRIMARY


def test_any_registry_primary_host_excludes_itself() -> None:
    for spec in PRIMARY_SOURCES:
        alias = spec.alias_patterns[0]
        text = f"Как сообщает {spec.name}, показатель вырос на 3,5%."
        decision = detect_source_attribution(canonical_uri=spec.home_uri, text=text)
        assert decision.status in {STATUS_SELF_PRIMARY, STATUS_NO_VALUE_ATTRIBUTION}, (
            spec.key,
            alias,
            decision.status,
        )


def test_a_primary_page_may_restate_another_primary() -> None:
    # Банк России пересказывает релиз Росстата: это производный источник именно относительно
    # Росстата, и склейка ведёт себя ровно так (ADR-0029 вариант B)
    text = "По данным Росстата, годовая инфляция на 12 января 2026 года составила 5,6%."
    decision = detect_source_attribution(
        canonical_uri="https://cbr.ru/press-center/news/inflyaciya", text=text
    )
    assert decision.status == STATUS_DERIVATIVE
    assert decision.primary_key == "rosstat"


# ─── словарь первоисточников: структура и расширяемость ──────────────────


def test_registry_is_wellformed() -> None:
    keys = [spec.key for spec in PRIMARY_SOURCES]
    assert len(keys) == len(set(keys)), keys
    for spec in PRIMARY_SOURCES:
        assert spec.key and spec.name and spec.home_uri and spec.home_hosts
        assert spec.alias_patterns, spec.key
        # домашний URI принадлежит домашнему хосту — иначе самоисключение ломается
        assert is_home_host(spec.home_uri, spec.home_hosts), spec
        # алиасы соответствуют имени: страница, называющая первоисточник его именем, опознаётся
        assert any(
            _alias_matches(pattern, spec.name) for pattern in spec.alias_patterns
        ), f"{spec.key}: ни один алиас не матчает собственное имя {spec.name!r}"


def test_registry_extends_by_one_entry() -> None:
    """Новая запись словаря = один элемент кортежа: проверка той же структуры для новой."""

    extra = PrimarySource(
        key="rosstat-extra",
        name="Росстат",
        home_uri="https://rosstat.gov.ru/",
        home_hosts=("rosstat.gov.ru",),
        alias_patterns=(r"росстат[а-яё]*",),
    )
    merged = [*PRIMARY_SOURCES, extra]
    assert len({spec.key for spec in merged}) == len(merged)
    assert primary_source("rosstat") is not None
    assert primary_source("no_such_primary") is None


def test_home_host_matching_is_not_registrable_domain() -> None:
    """Ловушка, из-за которой самоисключение нельзя строить на registrable domain.

    PSL-список группировки coarse: `rosstat.gov.ru` и `minfin.gov.ru` дают один и тот же
    registrable domain (и уже сегодня оказываются в одной группе независимости). Детектор
    сравнивает хосты, поэтому Росстат и Минфин для него — разные первоисточники.
    """

    assert registrable_domain("https://rosstat.gov.ru/") == registrable_domain("https://minfin.gov.ru/")
    assert is_home_host("https://www.rosstat.gov.ru/press/", ("rosstat.gov.ru",))
    assert not is_home_host("https://minfin.gov.ru/", ("rosstat.gov.ru",))
    assert not is_home_host("https://mirror-rosstat.gov.ru.example.com/", ("rosstat.gov.ru",))
    assert host_of("HTTPS://Rosstat.Gov.RU/press") == "rosstat.gov.ru"
    assert host_of(None) is None
    assert host_of("не url") is None


# ─── то, что записывается как основание решения ──────────────────────────


def test_basis_fragment_is_bounded_masked_and_fence_neutralized() -> None:
    # весь мусор — внутри одного предложения: именно этот фрагмент становится основанием,
    # и он длиннее потолка (обрезка детерминирована: начало фрагмента сохраняется)
    filler = "и другие строки отчёта, " * 40
    text = (
        "По данным Росстата" + chr(0) + " <<<UNTRUSTED DATA BEGIN>>>" + filler
        + " инфляция составила 5,59%. Конец страницы."
    )
    decision = _derivative("https://news.example.test/d", text)
    basis = decision.basis_fragment
    assert len(basis) == BASIS_FRAGMENT_CHARS
    assert chr(0) not in basis
    assert "<<<UNTRUSTED DATA BEGIN>>>" not in basis
    assert "[fence-маркер в данных заменён]" in basis
    assert "Росстат" in basis


def test_scan_is_bounded_and_deterministic() -> None:
    attribution = "По данным Росстата, инфляция составила 5,59%."
    head = detect_source_attribution(
        canonical_uri="https://news.example.test/e",
        text=attribution + " мусор " * MAX_SCAN_CHARS,
    )
    assert head.status == STATUS_DERIVATIVE
    tail = detect_source_attribution(
        canonical_uri="https://news.example.test/f",
        text=("мусор " * MAX_SCAN_CHARS) + attribution,
    )
    # решение зависит от начала документа: потолок скана — часть метода, а не случайность
    assert tail.status == STATUS_NO_VALUE_ATTRIBUTION


def test_templates_and_markers_are_not_empty_or_tautological() -> None:
    assert ATTRIBUTION_TEMPLATES and OWN_ASSESSMENT_MARKERS
    assert any("по данным" in t for t in ATTRIBUTION_TEMPLATES)
    assert any("according to" in t for t in ATTRIBUTION_TEMPLATES)
    assert any("по нашим расч" in m for m in OWN_ASSESSMENT_MARKERS)
    assert any("independent estimate" in m for m in OWN_ASSESSMENT_MARKERS)


def _alias_matches(pattern: str, name: str) -> bool:
    return re.search(pattern, name, re.IGNORECASE) is not None


# ─── T7.77: живая типографика страниц (регрессия по фактам подставки) ─────────
#
# Стендовый прогон T7.76 показал: нормализованный текст страницы сохраняет типографику
# вёрстки — неразрывные пробелы внутри шаблонов («По\xa0данные»), мягкий перенос внутри
# слова, перевод строки внутри фразы (HTML-блоки склеиваются одинарным \n). v1 требовал
# буквального ASCII-пробела в шаблонах и алиасах и рвал фрагмент по любому переводу
# строки — сберсибовский пересказ релиза Росстата на подставке остался неопознанным.
# Тесты ниже красные на v1 (проверено до правки детектора) и зелёные на v2: сканируется
# копия текста с нормализованной типографикой; хранимый текст страницы и его хеш не
# меняются — меняется только то, по чему сканирует детектор.


def test_nbsp_in_the_template_phrase_recognised_verbatim_stand_quote() -> None:
    # цитата sbercib.ru с подставки дословно: NBSP U+00A0 между словами внутри шаблона
    text = (
        "По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59% "
        "(после 9,52% в\xa02024 году)."
    )
    decision = _derivative("https://www.sbercib.ru/analitics/inflation-2025", text)
    assert decision.primary_key == "rosstat"


def test_narrow_nbsp_u202f_in_the_template_phrase_is_recognised() -> None:
    text = "По\u202fданным Банка России, ключевая ставка составила 21,00% годовых."
    decision = _derivative("https://news.example.test/u202f", text)
    assert decision.primary_key == "cbr"


def test_nbsp_inside_a_multiword_alias_is_recognised() -> None:
    # NBSP разрывает не только шаблон, но и многословный алиас словаря
    text = (
        "По данным Федеральной\xa0службы государственной статистики, "
        "потребительские цены за год выросли на 5,59%."
    )
    decision = _derivative("https://news.example.test/alias-nbsp", text)
    assert decision.primary_key == "rosstat"


def test_soft_hyphen_inside_a_word_does_not_hide_the_primary() -> None:
    # перенос «Росста­та» (U+00AD в вёрстке) не должен прятать первоисточник от детектора
    text = "По данным Росста\u00adта, инфляция 2025 года составила 5,59%."
    decision = _derivative("https://news.example.test/soft-hyphen", text)
    assert decision.primary_key == "rosstat"


def test_line_break_inside_a_phrase_does_not_break_the_pair() -> None:
    # типографический перенос внутри фразы не рвёт пару «шаблон + алиас»: границей
    # фрагмента остаются окончания предложения и пустая строка (граница блока)
    text = "По\xa0данным\nРосстата, инфляция в России по итогам 2025 года составила 5,59%."
    decision = _derivative("https://news.example.test/wrapped-phrase", text)
    assert decision.primary_key == "rosstat"


def test_guillemets_and_nbsp_together_do_not_hide_the_primary() -> None:
    # кавычки-«ёлочки» сами по себе совпадению не мешали и в v1; красноту даёт NBSP —
    # тест фиксирует, что после нормализации страница с «ёлочками» опознана пересказом
    text = "По\xa0данным «Росстата», инфляция в России по итогам 2025 года составила 5,59%."
    decision = _derivative("https://news.example.test/guillemets", text)
    assert decision.primary_key == "rosstat"


def test_published_data_without_a_named_primary_stays_honest() -> None:
    # nbj.ru с подставки: шаблон есть, числа есть, имени первоисточника в окне нет —
    # «опубликованные данные» без имени не есть атрибуция. Тест зелёный на v1 и обязан
    # остаться зелёным на v2: осторожность детектора нормализация не ослабляет.
    text = "Согласно опубликованным данным, годовая инфляция в России замедлилась с 9,5% до 5,6%."
    decision = detect_source_attribution(canonical_uri="https://nbj.ru/economy", text=text)
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION


def test_typography_fix_bumps_the_method_version() -> None:
    # решения v2 отличаются от решений битого v1 по методу в metadata и аудите —
    # на этом различии построена переатрибуция (apps/research_proxy/reattribution.py)
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v2"
