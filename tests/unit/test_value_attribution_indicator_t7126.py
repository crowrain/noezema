"""Unit (pure): pairing «значение + производитель» обязан сверять ПОКАЗАТЕЛЬ утверждения с
показателем фрагмента (T7.126 часть а, ADR-0029 дополнение; решение пользователя 2026-10-19
«тупо по числу не матчевать: значение + производитель считают только если фрагмент совместим с
показателем утверждения»).

Стендовая форма дефекта (`.92`, claim `b262da08`): утверждение «По данным hh.ru, в августе 2026
года hh-индекс (конкуренция соискателей на вакансию) составил 8,7.» получило запись производителя
«Опубликовано производителем (Росстат)», а основанием стал фрагмент той же страницы о росте
реальных доходов на 1,5% год к году, где рядом стоит чужое «против 8,7% за тот же период 2025
года». Прежний pairing читал только «в фрагменте есть имя первоисточника» и «в фрагменте есть
это число» — двух разных фактов одной страницы этого не различало.

Правило (только запреты, прежние режимы не ослаблены):
(а) значение с ТОЙ ЖЕ размерностью единицы совместимо всегда («2,2%» ↔ «2,2%» — совместимо,
    как бы ни различались соседние слова);
(б) иначе — пересечение значимых основ показателя утверждения и фрагмента обязано быть непустым
    (тот же словарь основ, что у гейта почти-дубликатов: `apps/orchestrator/value_duplicate.py`
    теперь берёт его отсюда, новых словарей не заводили).

Что закреплено этим файлом: стендовая пара больше не склеивается; уже записанное строка-основание
отзывается действующим гейтом `record_pairing_gate_passes` (путь пересмотра T7.120a,
`apps/research_proxy/reattribution.py`); значение без единицы измерения остаётся совместным, если
основы показателя пересекаются; «то же число, другой показатель» — честный отказ.

Тесты не обращаются в сеть: страница — побайтовая фикстура `tests/fixtures/t7126/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.value_attribution import (
    PAIRING_VALUE_AND_PRIMARY,
    attribute_value_in_fragment,
    record_pairing_gate_passes,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RAMBLER_URL = (
    "https://www.rambler.ru/pro/zarplata/57077146-u-kogo-upali-realnye-zarplaty-v-2026-godu/"
)
RAMBLER_PAGE = (
    REPO_ROOT / "tests/fixtures/t7126/rambler_57077146_realnye_zarplaty_2026.normalized.txt"
).read_text(encoding="utf-8")

# формулировка стендового утверждения b262da08 (SELECT из noezema-dev)
HH_CLAIM = (
    "По данным hh.ru, в августе 2026 года hh-индекс (конкуренция соискателей на вакансию) "
    "составил 8,7."
)
# основание уже записанной строки значения (SELECT из value_attributions эпохи v15)
ROSSTAT_BASIS = (
    "По данным Росстата , реальные располагаемые доходы россиян выросли в первом полугодии "
    "2026 года лишь на 1,5% год к году — против 8,7% за тот же период 2025 года"
)


# ── т1: стендовая пара — Росстат снят ─────────────────────────────────────────────────


def test_rambler_rosstat_fragment_is_no_longer_the_producer_of_the_hh_value() -> None:
    """Было: `derivative rosstat`, основание — фрагмент о реальных доходах. Стало: либо
    производитель не приписан вовсе, либо это НЕ Росстат. На этой странице hh.ru не может быть
    назван фрагментом атрибуции («По данным hh.ru.» рвётся по точке, усечённый алиас «hh» не
    несёт значения), поэтому честный исход именно «записи нет» — придумать ей производителя
    означало бы ослабить pairing, что задачей не было."""
    decision = attribute_value_in_fragment(
        canonical_uri=RAMBLER_URL, text=RAMBLER_PAGE, claim_statement=HH_CLAIM
    )
    assert decision.primary_key != "rosstat", decision
    assert "Росстат" not in (decision.basis_fragment or ""), decision
    if decision.status == "derivative":
        # единственный законный derivative на этой странице — производитель из реестра T7.120
        assert decision.primary_key == "hh", decision


def test_record_pairing_gate_revokes_the_stand_rosstat_basis() -> None:
    """Действующий гейт пересмотра (T7.120a) обязывает снять уже записанную строку значения,
    чьё собственное основание больше не проходит pairing: именно так `reattribution.py` под
    методом v16 убирает «Опубликовано производителем (Росстат)» с карточки утверждения."""
    assert (
        record_pairing_gate_passes(
            claim_statement=HH_CLAIM,
            pairing=PAIRING_VALUE_AND_PRIMARY,
            basis_fragment=ROSSTAT_BASIS,
        )
        is False
    )


# ── т3: «то же значение с той же единицей» совместимо всегда ────────────────────────────


def test_same_unit_pairing_survives_different_neighbouring_words() -> None:
    """«2,2%» против «2,2%» — совместимо, даже если соседние слова показателя разные:
    размерность единицы решает (а), и словарь основ к ней не применяется. Прежнее pairing
    этого случая не трогал; правка его не должна была тронуть."""
    claim = "Уровень безработицы в России в августе 2026 года составил 2,2% по данным Росстата."
    fragment = "По данным Росстата, численность рабочей силы в августе 2026 года снизилась на 2,2%."
    decision = attribute_value_in_fragment(
        canonical_uri="https://rosstat.example.ru/labor", text=fragment, claim_statement=claim
    )
    assert (decision.status, decision.primary_key) == ("derivative", "rosstat"), decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


def test_bare_values_with_crossing_indicator_basis_stay_pairable() -> None:
    """Разрешающий случай: значение без единицы измерения, основы показателя пересекаются —
    pairing работает ровно как работал (стендовый пересказ hh на чужой странице). Правка не
    должна была снять ни одной законной пары; это проверка именно на «не ослаблено»."""
    claim = "Индекс конкуренции соискателей на вакансию России в августе 2026 года составил 8,7."
    fragment = (
        "Индекс конкуренции соискателей на вакансию в августе 2026 года вырос до 8,7, "
        "как сообщает hh.ru."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://hh.example.ru/press", text=fragment, claim_statement=claim
    )
    assert (decision.status, decision.primary_key) == ("derivative", "hh"), decision


def test_same_number_different_indicator_with_unit_conflict_is_refused() -> None:
    """Стендовая форма «тупого матча по числу»: утверждение про безразмерный индекс против
    фрагмента про проценты. Размерности не совпали, основы показателя не пересеклись — честный
    отказ вместо приписывания значения производителю чужого факта."""
    claim = (
        "Индекс потребительских настроений домохозяйств России в августе 2026 года составил 8,7."
    )
    fragment = (
        "По данным Росстата, наблюдаемая инфляция в августе 2026 года ускорилась до 8,7%."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://rosstat.example.ru/prices", text=fragment, claim_statement=claim
    )
    assert decision.status == "value_not_attributed", decision
    assert decision.primary_key != "rosstat", decision


def test_number_the_page_never_measured_is_left_alone_as_before() -> None:
    """Диагональ: то же голое число в тексте о другом показателе, но и единицы измерения ни с
    одной стороны не названы — отказа тут не было и быть не должно (прежний режим решает сам).
    Статус зафиксирован прежним, чтобы правка не превратилась в новый режим приписывания."""
    claim = (
        "Индекс потребительских настроений домохозяйств России в августе 2026 года составил 8,7."
    )
    fragment = "По данным Росстата, наблюдаемая инфляция в августе 2026 года ускорилась до 8,7."
    decision = attribute_value_in_fragment(
        canonical_uri="https://rosstat.example.ru/prices", text=fragment, claim_statement=claim
    )
    assert decision.status == "no_value_attribution", decision


def test_percent_value_does_not_pair_with_dimensionless_claim() -> None:
    """Значение утверждения без единицы измерения против процентной строки страницы: то же
    самое число, другой показатель — приписывания нет."""
    claim = "hh-индекс конкуренции соискателей в августе 2026 года составил 8,7."
    fragment = (
        "По данным Росстата, реальные располагаемые доходы россиян выросли в первом полугодии "
        "2026 года на 1,5% год к году — против 8,7% за тот же период 2025 года."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://rosstat.example.ru/income", text=fragment, claim_statement=claim
    )
    assert decision.primary_key != "rosstat", decision


# ── словарь основ: один на два гейта (новое владение) ──────────────────────────────────


def test_indicator_vocabulary_is_owned_by_value_attribution() -> None:
    """Основы показателя живут в `apps/research_proxy/value_attribution.py` — единственный
    слой, который могут импортировать и pairing, и гейт дублей (`apps/orchestrator/*` не может
    импортироваться наоборот). Проверка — по объектам функций, а не по тексту модуля; импорт
    локальный, чтобы на прежнем коде тест падал утверждением, а не сборкой."""
    from apps.orchestrator import value_duplicate
    from apps.research_proxy.value_attribution import statement_indicator_words

    assert value_duplicate.statement_indicator_words is statement_indicator_words
