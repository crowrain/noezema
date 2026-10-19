"""T7.121 (часть 4, ADR-0038 §4): mutual-кластер строгих кандидатов и guard месяцев.

Стендовый случай с3 (STATUS T7.121): в контекст-паке ДВЕ уже записанные формулировки
одного факта (тот же показатель, то же значение, те же годы), предложение куратора
совпадает с обеими, `relied_claim_ids` не объявлен ни один. Прежде гейт T7.83a говорил
«ambiguous ... left as proposed» и коммит писал ТРЕТЬЮ запись того же числа. Теперь спор
«какой из кандидатов тот же самый» испаряется, когда кандидаты являются дублями ДРУГ
ДРУГА (предикат витрины T7.99 в хронологическом направлении — при неизвестной дате в обе
стороны — плюс guard названных месяцев): хост
склеивает операцию в ранейшую запись (якорь — по `created_at`, ties — `claim_id`), третья
строка не пишется.

Guard месяцев (тот же стенд, консервативная граница): годовой фильтр T7.83 не ослаблен;
месяц смотрятся ТОЛЬКО когда их называют обе стороны, и конфликт — только при пустом
пересечении. «август 2026» против «июля 2026» одного числа — наблюдения РАЗНЫХ периодов:
кандидат уходит из строгой выборки (даже если он объявлен опорой — месяц сильнее ссылки),
а взаимного кластера такие кандидаты не образуют. Сторона без названного месяца — молчание.

Закреплены и прежние решения (консервативные замки, поведение НЕ меняется): ≥2 relied
кандидатов — прежний ambiguous; ≥2 строгих необъявленных кандидатов без взаимного кластера —
прежний ambiguous; counters и индикаторное вето применяются уже ПОСЛЕ выбора якоря кластера и
по-прежнему снимают склейку.

Дополнение T7.126 (часть б) правит ровно одно решение этого файла: одиночный необъявленный
кандидат больше НЕ отказ — «отказ по опоре» был придуман защищать хоста от ВЫБОРА между
кандидатами, а выбирать не из чего. Витринный предикат `value_duplicate_pair` (T7.99) тоже
изменён T7.126: значения операции обязаны быть ПОДМНОЖЕСТВОМ значений кандидата; прежнее
равенство — частный случай.

Все формулировки и числа в тесте — вымышленные (Тармения); создатели строк дат —
фиктивные моменты времени.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.curator_period_gate import named_month_numbers
from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_value_duplicates,
)

pytestmark = pytest.mark.unit

# ── вымышленная фикстура с3: две Mutual-формулировки одного факта (тот же словарь
# показателя, чтобы indicator_check пары действительно проходил) ──────────────────────

CAND_OLD = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (обзорная группа и ведомство, опубликовано 16–21 января 2030 г.)"
)
CAND_NEW = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (обзорная группа)."
)
OP_CLAIM = (
    "Официальная годовая инфляция в Тармении по итогам 2029 года "
    "(декабрь 2029 к декабрю 2028) составила 4,7% (по данным обзорной группы)."
)
OP_MONTHLESS = (
    "Годовая инфляция в Тармении по итогам 2029 года составила 4,7% (обзорная группа)."
)


def _claim(
    statement: str,
    claim_type: str = "temporal_fact",
    as_of_year: int | None = None,
    index: int = 0,
    supports: bool = True,
    counters: bool = False,
    metric: str | None = None,
) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        has_support_link=supports,
        has_counter_link=counters,
        metric=metric,
    )


def _candidate(
    claim_id: str,
    statement: str,
    claim_type: str = "temporal_fact",
    as_of_year: int | None = None,
    relied: bool = False,
    metric: str | None = None,
    created_at: datetime | None = None,
) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type,
        as_of_year=as_of_year,
        relied=relied,
        metric=metric,
        created_at=created_at,
    )


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


# ── т1: mutual-кластер без опоры склеивается в ранейшую запись (стендовый с3) ─────


def test_mutual_cluster_without_reliance_merges_into_earliest_record() -> None:
    """Было (6372138): ambiguous, операция остаётся → третья запись того же числа.
    Стало: reverified в ранейшую запись; спор испарился, кандидаты — дубли друг друга."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [
            _candidate("c-new", CAND_NEW, created_at=_utc(2026, 3, 10)),
            _candidate("c-old", CAND_OLD, created_at=_utc(2026, 2, 1)),
        ],
    )
    assert len(decisions) == 1, decisions
    entry = decisions[0]
    assert entry["action"] == "reverified", entry
    assert entry["target"] == "c-old", entry
    assert "mutual value duplicates" in entry["reason"]
    assert "a third copy is not written" in entry["reason"]
    assert "relied_claim_ids" in entry["reason"]  # отсутствие опоры названо честно


def test_cluster_anchor_tie_breaks_by_claim_id_when_dates_equal() -> None:
    """`created_at` строк одной phase-1 транзакции равны (ловушка AGENTS §7) — порядок
    детерминирован по claim_id, а не по удаче."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [
            _candidate("c-b", CAND_NEW, created_at=_utc(2026, 2, 1)),
            _candidate("c-a", CAND_OLD, created_at=_utc(2026, 2, 1)),
        ],
    )
    assert decisions[0]["action"] == "reverified"
    assert decisions[0]["target"] == "c-a"


def test_cluster_anchor_without_known_dates_requires_reciprocal_coverage() -> None:
    """Без известной хронологии (старые unit-фикстуры без `created_at`) кластер требует
    полного взаимного покрытия: симметричная пара склеивается по минимальному claim_id,
    асимметричная — остаётся ambiguous (хост не придумывает порядок записей)."""
    sym_a = (
        "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
        "составила 4,7% (обзорная группа)."
    )
    sym_b = (
        "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
        "составила 4,7% (вторичный обзор той же обзорной группы)."
    )
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [_candidate("c-z", sym_b), _candidate("c-m", sym_a)],
    )
    assert decisions[0]["action"] == "reverified"
    assert decisions[0]["target"] == "c-m"


def test_undated_member_does_not_join_dated_anchor_asymmetrically() -> None:
    """Хронология известна лишь наполовину → нужна полная взаимность; асимметричная пара
    (якорь с годом публикации «2030», кандидат без него) остаётся ambiguous."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [
            _candidate("c-a", CAND_OLD, created_at=_utc(2026, 2, 1)),
            _candidate("c-b", CAND_NEW, created_at=None),
        ],
    )
    assert decisions[0]["action"] == "kept"
    assert "ambiguous value duplicate" in decisions[0]["reason"]


# ── т2: не-mutual кандидаты (разные годовые покрытия) остаются ambiguous ─────────


def test_candidates_with_asymmetric_period_coverage_stay_ambiguous() -> None:
    """Прежний fixture-замок (test_value_duplicate.py::test_two_matching_candidates…):
    кандидат с дополнительным годом 2028 НЕ является дублем другого в обе стороны →
    хост по-прежнему не выбирает за куратора."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [
            _candidate("c-1", CAND_OLD),
            _candidate("c-2", "Инфляция в Тармении (декабрь 2029 к декабрю 2028) — 4,7% (обзор)."),
        ],
    )
    assert decisions[0]["action"] == "kept"
    assert decisions[0]["target"] is None
    assert "ambiguous value duplicate" in decisions[0]["reason"]
    assert "left as proposed" in decisions[0]["reason"]


# ── т3: guard названных месяцев ───────────────────────────────────────────────────

OP_AUG = "Инфляция в Тармении в августе 2029 года составила 4,7% (месячный обзор)."
CAND_AUG = "Инфляция в Тармении в августе 2029 года — 4,7% (первичный обзор)."
CAND_JUL = "Инфляция в Тармении в июле 2029 года составила 4,7% (июльский обзор)."


def test_month_conflicting_candidate_leaves_strict_even_when_relied() -> None:
    """Было (T7.121): candidate c-jul объявлен опорой → reverified в НЕНАЗВАННЫЙ месяц операции.
    Стало: месяц операции («август») противоречит месяцу кандидата («июль») — кандидат
    уходит из строгой выборки даже будучи объявленным опорой. Дополнение T7.126 (часть б):
    оставшийся необъявленный c-aug — ЕДИНСТВЕННЫЙ кандидат, и он становится целью перепроверки
    (прежний отказ «not in relied_claim_ids» для одного кандидата снят). Guard месяцев не
    ослаблен: c-jul по-прежнему не получает ни чужого месяца, ни чужой улики."""
    decisions = find_value_duplicates(
        [_claim(OP_AUG)],
        [
            _candidate("c-aug", CAND_AUG, relied=False),
            _candidate("c-jul", CAND_JUL, relied=True),
        ],
    )
    assert decisions[0]["action"] == "reverified", decisions
    assert decisions[0]["target"] == "c-aug"
    assert "c-jul" not in decisions[0]["reason"]


def test_all_candidates_month_conflicting_reason_names_months() -> None:
    decisions = find_value_duplicates(
        [_claim(OP_AUG)],
        [_candidate("c-jul", CAND_JUL, relied=True)],
    )
    assert decisions[0]["action"] == "kept"
    reason = decisions[0]["reason"]
    # прежняя подстрока прежнего отказа сохранена («not covered —») и дополнена months-частью
    assert "is not covered — named months do not overlap" in reason
    assert "names months [7], the op names [8]" in reason
    assert "left as proposed" in reason


def test_monthless_op_keeps_previous_year_only_silence() -> None:
    """Операция без названного месяца — guard молчит (месяц смотрятся только когда их
    называют обе стороны), оба кандидата остаются в строгой выборке. Месяцы кандидатов
    несовместны друг с другом → mutual-кластера нет → прежний ambiguous."""
    decisions = find_value_duplicates(
        [_claim(OP_MONTHLESS)],
        [_candidate("c-aug", CAND_AUG), _candidate("c-jul", CAND_JUL)],
    )
    assert decisions[0]["action"] == "kept"
    assert "ambiguous value duplicate" in decisions[0]["reason"]


# ── т4: вето после выбора якоря не ослаблено ─────────────────────────────────────


def test_indicator_veto_applies_after_cluster_anchor_is_chosen() -> None:
    """Кластер выбран, но показатель операции — другой («ключевая ставка» против
    «инфляции»): склейки нет, и причина называет ИНДИКАТОР, а не ambiguity."""
    decisions = find_value_duplicates(
        [_claim("Ключевая ставка в Тармении по итогам 2029 года составила 4,7% (обзор).")],
        [
            _candidate("c-old", CAND_OLD, created_at=_utc(2026, 2, 1)),
            _candidate("c-new", CAND_NEW, created_at=_utc(2026, 3, 10)),
        ],
    )
    assert decisions[0]["action"] == "kept"
    assert "indicator does not match" in decisions[0]["reason"]


def test_counterevidence_blocks_cluster_merge() -> None:
    """Операция с опровержением не склеивается ни с каким якорем — спор остаётся спором
    (прежнее вето T7.83a, только теперь оно названо после выбора якоря)."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM, supports=True, counters=True)],
        [
            _candidate("c-old", CAND_OLD, created_at=_utc(2026, 2, 1)),
            _candidate("c-new", CAND_NEW, created_at=_utc(2026, 3, 10)),
        ],
    )
    assert decisions[0]["action"] == "kept"
    assert "counterevidence links" in decisions[0]["reason"]
    assert "dispute stays a dispute" in decisions[0]["reason"]


def test_two_relied_mutual_candidates_stay_ambiguous() -> None:
    """Консервативный замок: если куратор ОБЪЯВИЛ опорой оба дубликата, выбор за него —
    по-прежнему не дело хоста (ветка relied не тронута правкой)."""
    decisions = find_value_duplicates(
        [_claim(OP_CLAIM)],
        [
            _candidate("c-old", CAND_OLD, relied=True),
            _candidate("c-new", CAND_NEW, relied=True),
        ],
    )
    assert decisions[0]["action"] == "kept"
    assert "ambiguous value duplicate" in decisions[0]["reason"]


def test_single_not_relied_candidate_is_merged_by_the_t7126_rule() -> None:
    """Одиночный необъявленный кандидат (T7.126 часть б). Было: «kept … not in relied_claim_ids»
    и коммит писал ВТОРУЮ строку того же числа. Стало: выбирать не из чего — перепроверка.
    Замки на месте: индикаторное вето и counters проверяются и здесь (тесты выше)."""
    decisions = find_value_duplicates([_claim(OP_CLAIM)], [_candidate("c-old", CAND_OLD)])
    assert decisions[0]["action"] == "reverified"
    assert decisions[0]["target"] == "c-old"
    assert "ONLY candidate" in decisions[0]["reason"]


# ── т5: разбор названных месяцев (общие closed-основы гейта периода) ─────────────


def test_named_month_numbers_closed_word_grammar() -> None:
    # у CAND_OLD строка публикации добавляет январь — датировочная строка тоже месяц
    # (консервативное направление guard'а: лишний месяц размывает несовместимость,
    # но не создаёт её)
    assert named_month_numbers(CAND_OLD) == frozenset({1, 12})
    assert named_month_numbers(CAND_NEW) == frozenset({12})
    assert named_month_numbers("декабрьского обзора") == frozenset({12})
    # датировочная строка — тоже месяц: консервативное направление guard'а (лишний месяц
    # размывает строгую несовместимость, но не создаёт её)
    assert named_month_numbers("Опубликовано 21 января 2026 года.") == frozenset({1})
    # слово, НЕ являющееся месяцем, не читается месяцем (та же граница слова, что у гейта
    # периода: «Независимая» заканчивается на «мая», «Маяковского» длиннее флексии)
    assert named_month_numbers("Независимая оценка Маяковского.") == frozenset()
    assert named_month_numbers("Мама смотрела на экран.") == frozenset()
    assert named_month_numbers(OP_MONTHLESS) == frozenset()
