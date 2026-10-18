"""T7.118: юнит-тесты чистых правил отчёта честности + закрепление зеркала нормализации.

Зеркало (`packages/memory/honesty_report.py`) обязано совпадать с гейтами почти-повторов
(`apps/orchestrator/tool_context.normalize_search_query`, `content_word_stems`) и схлопыванием
основ (`apps/orchestrator/assertion_window._prefix_stem`) — расхождение краснит этот тест:
Ф2 обязана делить темы ровно тем же способом, каким ворота T7.111a/T7.112 делят запросы,
без новых словарей и своих нормализаций.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import apps.orchestrator.tool_context as tool_context
from apps.orchestrator.assertion_window import _prefix_stem as gate_prefix_stem
from packages.memory.honesty_report import (
    _CONTROL_TAG_RE,
    _FLAG_LABELS,
    _SEARCH_QUERY_SERVICE_WORDS,
    CONTROL_SUFFIX_HEAD,
    PRODUCER_LABEL,
    SCHEMA_VERSION,
    HonestyReportError,
    _control_suffix,
    _prefix_stem,
    classify_fetch_error,
    content_word_stems,
    is_official_host,
    load_baseline,
    normalize_report_words,
    off_topic_flag,
    parse_since,
    producer_not_named_flag,
    producer_signature,
    search_query_official,
    shared_producer_flag,
    significant_stems,
    url_host,
)

pytestmark = [pytest.mark.unit]

SALARY_QUESTION = "Средние зарплаты в России в 2026 году"
OFF_TOPIC_CLAIM = "Уровень безработицы в России в 2026 году составил около трёх процентов"
ON_TOPIC_CLAIM = "Средняя начисленная зарплата в России превысила 70 тысяч рублей в 2026 году"

_CORPUS = [
    SALARY_QUESTION,
    "Инфляция в России по оценке аналитиков SberCIB",
    "hh.ru прогноз 2,1% напряжённость труда",
    "год года году годы г",
    "Ёлка ёлка ROSSTAT",
    "",
    "а\x00б",
    "ГАЗПРОМБАНК банк банка",
]


# ─── зеркало нормализации: совпадает с гейтом, расхождение = дефект ────────────────────


def test_normalization_mirror_matches_gate_functions() -> None:
    for text in _CORPUS:
        assert normalize_report_words(text) == tool_context.normalize_search_query(text), text
        assert content_word_stems(text) == tool_context.content_word_stems(text), text


def test_prefix_stem_mirror_matches_assertion_window() -> None:
    terms = ["я", "до", "год", "цена", "цены", "росси", "россия", "зарплата", "безработица",
             "1234567", "а" * 12, "абвгдеёжз"]
    for term in terms:
        assert _prefix_stem(term) == gate_prefix_stem(term), term


def test_service_words_mirror_is_exact_copy_of_gate_dict() -> None:
    assert set(tool_context.SEARCH_QUERY_SERVICE_WORDS) == set(_SEARCH_QUERY_SERVICE_WORDS)
    stems = content_word_stems("год года году годы г в для через")
    assert stems == frozenset()


# ─── основы значимых слов (Ф2) ─────────────────────────────────────────────────────────


def test_significant_stems_drop_pure_numbers() -> None:
    assert "2026" in content_word_stems(SALARY_QUESTION)
    assert "2026" not in significant_stems(SALARY_QUESTION)
    assert significant_stems("2026 2,1") == frozenset()


def test_off_topic_flag_fires_for_unrelated_claim() -> None:
    flag = off_topic_flag("Уровень безработицы составил около трёх процентов", "Средние цены на бензин")
    assert flag is not None and flag.code == "f2"
    assert "пересечение значимых основ слов утверждения с вопросом пусто" in (flag.message or "")


def test_off_topic_flag_fires_when_only_generic_anchor_connects() -> None:
    flag = off_topic_flag(OFF_TOPIC_CLAIM, SALARY_QUESTION)
    assert flag is not None and flag.code == "f2"
    assert "росси" in (flag.message or "")


def test_off_topic_flag_quiet_for_on_topic_claim() -> None:
    assert off_topic_flag(ON_TOPIC_CLAIM, SALARY_QUESTION) is None


def test_off_topic_flag_quiet_below_guards() -> None:
    # одна значимая основа у утверждения — не проверяем; две основы при одном общем якоре — тоже
    assert off_topic_flag("Инфляция", SALARY_QUESTION) is None
    assert off_topic_flag("Инфляция в России", SALARY_QUESTION) is None


# ─── Ф1: общий производитель в двух группах E2+ ─────────────────────────────────────────


def test_f1_variant_a_two_groups_share_parent() -> None:
    flag = shared_producer_flag(
        grade="E3",
        groups={"g1": {"srcB"}, "g2": {"srcD"}},
        effective_parent={"srcB": "parentX", "srcD": "parentX"},
    )
    assert flag is not None and flag.code == "f1"
    assert "parentx" in (flag.message or "").lower()


def test_f1_variant_b_source_and_its_copy_in_different_groups() -> None:
    flag = shared_producer_flag(
        grade="E2",
        groups={"g1": {"srcP"}, "g2": {"srcCopy"}},
        effective_parent={"srcCopy": "srcP"},
    )
    assert flag is not None and flag.code == "f1"


def test_f1_quiet_for_low_grade_single_group_or_independent_parents() -> None:
    assert shared_producer_flag(grade="E1", groups={"g1": {"a"}, "g2": {"b"}},
                                 effective_parent={"a": "p", "b": "p"}) is None
    assert shared_producer_flag(grade=None, groups={"g1": {"a"}, "g2": {"b"}},
                                effective_parent={"a": "p", "b": "p"}) is None
    assert shared_producer_flag(grade="E4", groups={"g1": {"a"}},
                                effective_parent={"a": "p"}) is None
    assert shared_producer_flag(grade="E3", groups={"g1": {"a"}, "g2": {"b"}},
                                effective_parent={"a": "p1", "b": "p2"}) is None
    assert shared_producer_flag(grade="E3", groups={"g1": {"a"}, "g2": {"b"}},
                                effective_parent={}) is None


# ─── подпись производителя и Ф3 ─────────────────────────────────────────────────────────


def test_producer_signature_requires_single_named_group_without_counters() -> None:
    names = [("sbercib", "SberCIB Investment Research")]
    signature = producer_signature(
        names=names, group_count=1, has_counters=False, reasons=["insufficient_independence"]
    )
    assert signature == ("SberCIB Investment Research", "sbercib")
    assert producer_signature(names=names, group_count=2, has_counters=False,
                              reasons=["insufficient_independence"]) is None
    assert producer_signature(names=names, group_count=1, has_counters=True,
                              reasons=["insufficient_evidence"]) is None
    assert producer_signature(names=names, group_count=1, has_counters=False,
                              reasons=["two_independent_groups"]) is None
    assert producer_signature(names=[], group_count=1, has_counters=False,
                              reasons=["insufficient_independence"]) is None
    assert producer_signature(names=[("a", "X"), ("b", "Y")], group_count=1, has_counters=False,
                              reasons=["insufficient_independence"]) is None  # имена разошлись
    assert producer_signature(names=[("a", "Икс"), ("a", "Игрек")], group_count=1, has_counters=False,
                              reasons=["insufficient_independence"]) is None


def test_f3_fires_when_signature_author_absent_from_statement() -> None:
    signature = ("SberCIB Investment Research", "sbercib")
    flag = producer_not_named_flag(signature, "Инфляция в России по оценке аналитиков составит пять процентов")
    assert flag is not None and flag.code == "f3"
    assert PRODUCER_LABEL in (flag.message or "")
    assert "SberCIB Investment Research" in (flag.message or "")


def test_f3_quiet_when_author_named_directly_or_via_key() -> None:
    assert producer_not_named_flag(
        ("SberCIB Investment Research", "sbercib"),
        "Инфляция по оценке SberCIB составит пять процентов",
    ) is None
    assert producer_not_named_flag(
        ("Неизвестное Имя", "gazprombank"),
        "Газпромбанк gazprombank.com опубликовал прогноз",
    ) is None
    assert producer_not_named_flag(None, "Что угодно") is None


def test_f3_fail_closed_when_name_has_no_comparable_words() -> None:
    # имя без слов (нечего сравнивать) — флаг не ставим, ложных срабатываний не генерируем
    assert producer_not_named_flag(("№7 (!)", "№7 (!)"), "Инфляция пять процентов") is None


def test_f3_known_limitation_short_popular_name_is_a_documented_false_positive_risk() -> None:
    # «Росстат» в формулировке не связывается со словарным именем записи: словарь producers
    # живёт в apps/, packages/ его не знает. Ограничение закреплено здесь как ожидаемое.
    flag = producer_not_named_flag(
        ("rosstat", "Федеральная служба государственной статистики"),
        "Росстат опубликовал оценку",
    )
    assert flag is not None and flag.code == "f3"


def test_flag_labels_cover_all_flag_codes() -> None:
    codes = {
        (off_topic_flag(OFF_TOPIC_CLAIM, SALARY_QUESTION) or pytest.fail("нет ф2")).code,
        (shared_producer_flag(grade="E2", groups={"g1": {"a"}, "g2": {"b"}},
                              effective_parent={"a": "p", "b": "p"}) or pytest.fail("нет ф1")).code,
        (producer_not_named_flag(("SberCIB X", "sbercib"), "Что-то другое") or pytest.fail("нет ф3")).code,
    }
    assert codes == {"f1", "f2", "f3"} == set(_FLAG_LABELS)


# ─── отбор: метка контрольного прогона и ISO-дата ───────────────────────────────────────


def test_control_suffix_shape_and_validation() -> None:
    assert _control_suffix("v118a") == f"{CONTROL_SUFFIX_HEAD}v118a)"
    assert _CONTROL_TAG_RE.fullmatch("v118a") is not None
    assert _CONTROL_TAG_RE.fullmatch("плохой тег") is None
    assert _control_suffix("tag_1.2-3") == " (контроль tag_1.2-3)"
    for bad in ("", "плохой тег", "тег!", "v118a)", " прогон 1 "):
        with pytest.raises(HonestyReportError):
            _control_suffix(bad)


def test_parse_since_converts_to_utc() -> None:
    naive = parse_since("2026-10-14")
    assert naive == datetime(2026, 10, 14, tzinfo=UTC)
    offset = parse_since("2026-10-14T08:00:00+03:00")
    assert offset == datetime(2026, 10, 14, 5, 0, tzinfo=UTC)
    for bad in ("", "вчера", "2026-13-01"):
        with pytest.raises(HonestyReportError):
            parse_since(bad)


# ─── хосты и разделение поисков (та же хост-константа, что у менеджера) ────────────────


def test_classify_fetch_error_mirror() -> None:
    assert classify_fetch_error("non-200 status: 403") == "403"
    assert classify_fetch_error("  non-200 status: 429 ") == "429"
    assert classify_fetch_error("timeout: connect") == "timeout"
    assert classify_fetch_error("non-200 status: 404") is None
    assert classify_fetch_error("non-200 status: 500") is None
    assert classify_fetch_error("artifact unreadable") is None
    assert classify_fetch_error("") is None


def test_url_host_normalization_like_manager() -> None:
    assert url_host("https://www.Rosstat.gov.ru/page?x=1") == "rosstat.gov.ru"
    assert url_host("https://m.cbr.ru/press/") == "m.cbr.ru"
    assert url_host("") == ""
    assert url_host("не url") == ""


def test_official_host_constant_matches_exact_and_subdomains_only() -> None:
    assert is_official_host("rosstat.gov.ru")
    assert is_official_host("data.gks.ru")
    assert is_official_host("cbr.ru")
    assert not is_official_host("cbr.ru.info")
    assert not is_official_host("notrosstat.gov.ru")
    assert not is_official_host("hh.ru")


def test_search_official_split_by_domain_token_and_documented_limitation() -> None:
    assert search_query_official("средние зарплаты rosstat 2026")
    assert search_query_official("прогноз cbr.ru инфляция")
    assert search_query_official("economy.gov.ru прогноз")
    # кириллическое «Росстат» без домена — в официальные НЕ попадает: ограничение принято
    assert not search_query_official("Росстат безработица 2026")
    assert not search_query_official("hh.ru зарплаты")


# ─── baseline-файл ──────────────────────────────────────────────────────────────────────


def test_load_baseline_accepts_matching_schema(tmp_path) -> None:
    good = tmp_path / "report.json"
    good.write_text(f'{{"schema_version": "{SCHEMA_VERSION}", "totals": {{"questions": 2}}}}',
                    encoding="utf-8")
    baseline = load_baseline(str(good))
    assert baseline["totals"]["questions"] == 2


def test_load_baseline_rejects_wrong_schema_not_object_and_missing(tmp_path) -> None:
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"schema_version": "другая", "totals": {}}', encoding="utf-8")
    with pytest.raises(HonestyReportError, match="схема baseline не совпадает"):
        load_baseline(str(wrong))
    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(HonestyReportError, match="JSON-объект"):
        load_baseline(str(not_object))
    with pytest.raises(HonestyReportError, match="baseline не читается"):
        load_baseline(str(tmp_path / "нет-файла.json"))
