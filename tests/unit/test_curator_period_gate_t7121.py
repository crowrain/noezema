"""T7.121 (части 2–3, ADR-0038): unit-гейт периода и даты прогноза в формулировке куратора.

Проверяются ЧИСТЫЕ функции apps/orchestrator/curator_period_gate.py — ни БД, ни LLM.
Смысловые основания закреплены ADR-0038 §2–§3:

- явный период формулировки обязан находиться рядом со значением на связанной странице
  (окно `PUBLICATION_WINDOW_CHARS` = 400 — существующий порог вето T7.113, новых нет);
- «день + месяц» («14 октября 2026 года 11:46», «14 октября.») — датировка страницы,
  периодом НЕ является; месяц без дня («в октябре», «в сентябре прошлого года») — является;
- формулировка без периода молчит;
- утверждение с маркером прогноза/ожидания обязано назвать дату прогноза (месяц+год или
  дату), отличную от целевого периода («по итогам 2026 года»), либо честно написать
  «дата прогноза на странице не указана»; названная дата проверяется на странице так же,
  как период, и датировочная строка страницы её подтверждает (здесь она — сама дата);
- квартал/полугодие подтверждаются названной формой, диапазоном месяцев («январь–июнь»,
  «июль–декабрь», «с X по Y») или всеми месяцами периода;
- имена показателей опросов («инфляционные ожидания населения») — не маркер прогноза.

Фикстуры — нормализованные тексты реальных стендовых страниц (STATUS T7.121):
finmarket-6727171 (безработица 2,2%, датировка «14 октября 2026 года 11:46» и «14 октября.»)
и ysia-vtb-forecast (прогноз ВТБ 5–6 %, строка «26 февраля»). Все формулировки утверждений в
тестах — либо реальные формулировки зафиксированных предложений куратора (они являются частью
аудит-фикстуры), либо синтетические случаи на вымышленных числах и страницах.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.orchestrator.curator_period_gate import (
    FORECAST_DATE_UNSTATED_PHRASE,
    PERIOD_WINDOW_CHARS,
    claim_period_rejections,
    has_forecast_marker,
    statement_period_tokens,
)
from apps.research_proxy.source_attribution import MAX_SCAN_CHARS, normalize_scan_text

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "t7121"


def _scan(name: str) -> str:
    return normalize_scan_text((FIXTURES / name).read_text(encoding="utf-8"))[:MAX_SCAN_CHARS]


FINMARKET = _scan("finmarket-6727171.normalized.txt")
YSIA = _scan("ysia-vtb-forecast.normalized.txt")


def _claim(statement: str, *, claim_type: str = "external_fact", existing_claim_id: str | None = None) -> Any:
    return SimpleNamespace(
        claim_type=SimpleNamespace(value=claim_type),
        statement=statement,
        existing_claim_id=existing_claim_id,
    )


def _check(
    statement: str,
    texts: list[str],
    *,
    claim_type: str = "external_fact",
    existing_claim_id: str | None = None,
    already_dropped: frozenset[int] = frozenset(),
) -> dict[int, str]:
    return claim_period_rejections(
        claims=[_claim(statement, claim_type=claim_type, existing_claim_id=existing_claim_id)],
        texts_by_claim={0: texts},
        already_dropped=already_dropped,
    )


# реальные формулировки зафиксированных предложений куратора (аудит-фикстура 1e4ef28d…)
C1_CREATED = (
    "Уровень безработицы в России в октябре 2026 года составил 2,2% по методологии МОТ "
    "(по данным Росстата, цитируемым вице-премьером Т. Голиковой)."
)
C1_NO_PERIOD = (
    "Уровень безработицы в России составил 2,2% по методологии МОТ (по данным Росстата, "
    "цитируемым вице-премьером Т. Голиковой)."
)
C2_VTB = "Аналитики ВТБ ожидают, что инфляция в России по итогам 2026 года составит 5–6%."

# ── токены формулировки ───────────────────────────────────────────────────────


def test_period_tokens_of_the_created_claim() -> None:
    tokens = statement_period_tokens(C1_CREATED)
    assert [(t.kind, t.month, t.year) for t in tokens] == [("month", 10, 2026)]
    assert tokens[0].label == "октябре 2026"


def test_forecast_marker_lists_are_closed_and_noun_is_not_a_marker() -> None:
    assert has_forecast_marker("Аналитики ВТБ ожидают, что инфляция составит 5–6%.")
    assert has_forecast_marker("SberCIB прогнозирует безработицу на уровне 2,3%.")
    assert has_forecast_marker("Консенсус-прогноз аналитиков оценивает инфляцию в 5%.")
    assert has_forecast_marker("Экономисты предполагали другой исход.")
    # имя показателя опроса — не маркер (урок T7.88a/b, тот же предел у витрины)
    assert not has_forecast_marker("Инфляционные ожидания населения в августе 2026 года — 13,1%.")
    assert not has_forecast_marker("По оценке Росстата, индекс потребительских цен вырос на 0,4%.")


def test_target_phrases_are_period_tokens_and_bare_year_is_not() -> None:
    assert {(t.kind, t.year) for t in statement_period_tokens("По итогам 2026 года инфляция составит 5%.")} == {
        ("target", 2026)
    }
    assert {t.kind for t in statement_period_tokens("Инфляция в 2026 году составила 5%.")} == set()
    assert {t.kind for t in statement_period_tokens("На конец 2026 года зарезервировано 3%.")} == {"target"}
    assert {(t.kind, t.year) for t in statement_period_tokens("За 2029 год ожидается 4%.")} == {("target", 2029)}


# ── т1: стендовый случай finmarket — период «октябре 2026» рядом со значением не подтверждён ──


def test_claim_period_rejected_on_finmarket_page() -> None:
    reasons = _check(C1_CREATED, [FINMARKET])
    assert list(reasons) == [0]
    reason = reasons[0]
    assert reason.startswith("claim[0]: период «октябре 2026»")
    assert "не найден рядом со значением на связанной странице" in reason
    assert "возьми период из текста или напиши, что период не указан" in reason


def test_publication_stamp_does_not_confirm_period_but_bare_month_does() -> None:
    # «14 октября 2026 года 11:46» и «14 октября.» — датировки статьи; в окне значения «2,2»
    # на finmarket больше месяца нет вовсе — отказ (т1 выше). А табличное «в сентябре 2026»
    # (bare-месяц в окне значения того же текста) — подтверждение: тот же текст, другой период.
    assert _check(C1_CREATED, [FINMARKET])
    assert not _check("Уровень безработицы в России в сентябре 2026 года составил 2,2%.", [FINMARKET])


def test_claim_without_period_is_silent_on_the_same_page() -> None:
    assert _check(C1_NO_PERIOD, [FINMARKET]) == {}


def test_valueless_claim_and_other_types_are_outside_the_gate() -> None:
    assert _check("Уровень безработицы в октябре 2026 года остался минимальным.", [FINMARKET]) == {}
    assert _check(C1_CREATED, [FINMARKET], claim_type="local_observation") == {}


def test_reverify_is_checked_and_already_dropped_operations_are_skipped() -> None:
    # было (T7.121): перепроверки не проверялись («перепроверку ведёт якорь»);
    # стало (T7.123, ADR-0038 §2): освобождение — дыра стенда 64837cb5/claim[0]
    # (обращённый дубль прошёл гейт значения в порочащую связь, период не проверился):
    # перепроверка проверяется наравне с новым claim'ом — та же строка причины.
    reasons = _check(C1_CREATED, [FINMARKET], existing_claim_id="136a0ca0-0000-0000-0000-000000000000")
    assert list(reasons) == [0] and "октябре 2026" in reasons[0]
    # снятая ранее операция по-прежнему не проверяется: у неё уже есть причина
    assert _check(C1_CREATED, [FINMARKET], already_dropped=frozenset({0})) == {}


def test_period_far_from_value_is_rejected() -> None:
    # период назван на странице, но дальше окна `PERIOD_WINDOW_CHARS` от места значения
    filler = "заполнитель строки обзора рынка. " * 40
    text = f"В октябре 2026 года обзор опубликован. {filler} По данным наблюдений индекс составил 7,3%."
    assert PERIOD_WINDOW_CHARS == 400
    reasons = _check("Индекс рынка труда в октябре 2026 года составил 7,3%.", [text])
    assert list(reasons) == [0] and "октябре 2026" in reasons[0]


def test_claim_without_measured_value_is_silent() -> None:
    # нет измеренного значения — нет и окна; период не проверяется (тот же предел, что у T7.108)
    assert _check("Рынок труда в октябре 2026 года оставался напряжённым.", ["заполнитель " * 400]) == {}


# ── т2: стендовый случай ysia — прогноз обязан назвать дату прогноза ────────────


def test_forecast_claim_without_date_is_rejected_on_ysia_page() -> None:
    reasons = _check(C2_VTB, [YSIA])
    assert list(reasons) == [0]
    assert reasons[0].startswith("claim[0]: утверждение с прогнозом или ожиданием")
    assert FORECAST_DATE_UNSTATED_PHRASE in reasons[0]


def test_forecast_date_from_page_stamp_passes() -> None:
    fixed = C2_VTB[:-1] + " (прогноз от 26 февраля 2026 года)."
    assert _check(fixed, [YSIA]) == {}


def test_forecast_honest_phrase_is_accepted() -> None:
    honest = C2_VTB[:-1] + f" ({FORECAST_DATE_UNSTATED_PHRASE})."
    assert _check(honest, [YSIA]) == {}


def test_forecast_date_absent_from_page_is_rejected() -> None:
    invented = C2_VTB[:-1] + " (прогноз от 3 марта 2026 года)."
    reasons = _check(invented, [YSIA])
    assert list(reasons) == [0] and "3 марта 2026 года" in reasons[0]


def test_forecast_month_with_year_is_a_date_and_target_phrase_alone_is_not() -> None:
    # дата прогноза может быть названа месяцем+годом — но тогда этот месяц обязан
    # стоять на странице без дня рядом со значением (на ysia «февраля» есть только
    # датировочно — значит там дата прогноза читается днём «26 февраля», см. тест выше)
    dated = C2_VTB[:-1] + " (оценку опубликовали в феврале 2026 года)."
    page = (
        "Банк фиксирует диапазон: оценка аналитиков ВТБ по итогам 2026 года — 5–6%. "
        "Материал опубликован в феврале 2026 года. Остальные строки страницы не о показателе."
    )
    assert _check(dated, [page]) == {}
    bare_target_only = "Аналитики ВТБ ожидают инфляцию в России на 2026 год на уровне 5–6%."
    reasons = _check(bare_target_only, [YSIA])
    assert list(reasons) == [0] and "дату" in reasons[0]


# ── т6: квартал и полугодие ─────────────────────────────────────────────────────


def test_half_year_confirmed_by_month_range_on_page() -> None:
    claim = "Индекс деловой активности во втором полугодии 2026 года составил 5%."
    text = (
        "заполнитель методических пояснений. " * 30
        + "Опрос измерялся в июле–декабре 2026 года, итог периода — ровно 5."
    )
    assert _check(claim, [text]) == {}


def test_half_year_confirmed_by_named_form() -> None:
    claim = "Индекс деловой активности в первом полугодии 2026 года составил 5%."
    text = (
        "Методика: «первое полугодие 2026 — январь–июнь». Итог наблюдения: 1-е полугодие 2026 — 5%."
    )
    assert _check(claim, [text]) == {}


def test_half_year_not_confirmed_by_wrong_range() -> None:
    claim = "Индекс деловой активности в первом полугодии 2026 года составил 5%."
    text = "Опрос шёл в июле–декабре 2026 года. Итог наблюдения — ровно 5." + " пояснения " * 30
    reasons = _check(claim, [text])
    assert list(reasons) == [0] and "первом полугодии 2026" in reasons[0]


def test_quarter_confirmed_by_named_form_and_by_month_range() -> None:
    claim = "ВВП вырос во II квартале 2026 года на 1,7%."
    named = "Оценка за II квартал 2026 года: рост 1,7 процента." + " пояснения " * 30
    ranged = (
        "Рост ВВП в апреле–июне 2026 года составил 1,7 процента."
        + " пояснения " * 30
        + "квартал в тексте не назван словом"
    )
    assert _check(claim, [named]) == {}
    assert _check(claim, [ranged]) == {}


def test_quarter_not_confirmed_by_foreign_range_or_wrong_year() -> None:
    claim = "ВВП вырос во II квартале 2026 года на 1,7%."
    foreign = "Рост ВВП в январе–марте 2026 года составил 1,7 процента." + " пояснения " * 30
    wrong_year = "Рост ВВП во II квартале 2025 года составил 1,7 процента." + " пояснения " * 30
    assert list(_check(claim, [foreign])) == [0]
    assert list(_check(claim, [wrong_year])) == [0]


def test_month_confirmed_by_bare_month_with_matching_year() -> None:
    claim = "Оценка вакансий в июле 2026 года составила 7,8%."
    good = "Сервис «Гелиос» (Тармения): в июле 2026 года показатель снизился до 7,8."
    wrong = "Сервис «Гелиос» (Тармения): в июле 2025 года показатель снизился до 7,8."
    assert _check(claim, [good]) == {}
    assert list(_check(claim, [wrong])) == [0]


def test_date_forms_accepted_and_mismatched_day_rejected() -> None:
    text = "Выпуск от 26 февраля 2026 года: индекс 5,4 пункта." + " пояснения " * 10
    assert _check("Индекс выпущен 26 февраля 2026 года и составил 5,4%.", [text]) == {}
    assert _check("Индекс выпущен 26.02.2026 и составил 5,4%.", [text]) == {}
    assert list(_check("Индекс выпущен 27 февраля 2026 года и составил 5,4%.", [text])) == [0]


def test_multiple_texts_any_confirmation_counts() -> None:
    claim = "Оценка вакансий в июле 2026 года составила 7,8%."
    fragment_without_period = "Показатель сервиса снизился до 7,8."
    page_with_period = "«Гелиос»: в июле 2026 года 7,8 и дальше рост." + " пояснения " * 10
    assert _check(claim, [fragment_without_period, page_with_period]) == {}


def test_reject_reason_lists_at_most_three_missing_periods() -> None:
    claim = "В июле 2031, августе 2031 и сентябре 2031 года и в октябре 2031 года индекс составил 7,8%."
    text = "Наблюдение зафиксировало ровно 7,8%." + " пояснения " * 10
    reason = _check(claim, [text])[0]
    assert reason.count("период «") == 1 and reason.count("2031") >= 3
