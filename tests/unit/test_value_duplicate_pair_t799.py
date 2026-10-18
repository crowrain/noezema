"""Unit: чистый предикат пары «дубль по значению» для витрины и отбор свёртки (T7.99).

Решение пользователя 2026-10-12: уже записанные дубли показывать свёрнутыми на карточке
ответа (ADR-0033 §13). Критерий пары — ровно критерий строгого кандидата гейта T7.83/T7.83a
(type + values + period + indicator, включая чтение диапазонов T7.98) БЕЗ условия опоры
`relied_claim_ids` и без условий улик: `relied` и улики описывают СКЛЕЙКУ при записи —
витрина ничего не записывает и не склеивает, она решает только показ.

Гейт (`find_value_duplicates`) этот предикат не вызывает: его решения с причинами по
каждому шагу закреплены прежними тестами (`test_value_duplicate*.py`) и здесь не меняются.

Таблица построена на подлинном снимке 20 действующих утверждений стенда .92
(`fixtures-t799/claims_live.tsv`, срез 2026-10-12): сворачиваются ровно две пары —
`9266248e… ↔ e189c80d…` (5,59%, temporal_fact) и `c6729d4c… ↔ 136a0ca0…` (6–7%,
external_fact); ложных свёрток нет: тройка «5,6%» (c970bc08/73d52835/e2c52955) отсекается
вето показателя, байт-в-байт одинаковые формулировки разного типа (2d010d53/58e2d5d4) —
условием равенства типа.
"""

from __future__ import annotations

import datetime as dt
import inspect

import pytest

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_value_duplicates,
    value_duplicate_pair,
)
from apps.web import answer as web_answer
from apps.web.estimates import FoldRecord, fold_value_duplicate_pairs

pytestmark = [pytest.mark.unit]

# ─── подлинный срез стенда .92 (fixtures-t799/claims_live.tsv, 20 строк) ──────

LIVE_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("d8155167-22a0-451f-aabf-864cdfe581e3", "external_fact", "2026-10-06 16:51:44.337467+00",
     "ООН состоит из 193 государств-членов."),
    ("9266248e-2433-4a12-a3f7-b51ff10ad803", "temporal_fact", "2026-10-06 20:18:52.021529+00",
     "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
     "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"),
    ("c970bc08-6278-4766-9d74-7e541284be2a", "external_fact", "2026-10-07 17:02:35.596716+00",
     "Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: инфляция в "
     "России по итогам 2025 года составила 5,6% — расхождений с официальными данными нет"),
    ("7cc2b578-e1e9-4474-8405-04ca3b9d0ea4", "external_fact", "2026-10-07 17:02:35.596716+00",
     "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже всех "
     "прогнозов аналитиков: прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%, "
     "консенсус-прогноз ЦБ — 6,6%"),
    ("73d52835-2ed8-4ac3-9505-59845536f2a9", "external_fact", "2026-10-08 02:43:20.543976+00",
     "В пресс-релизе Банка России (event id 28251) годовая инфляция в 2025 году указана как 5,6%."),
    ("e2c52955-864c-43f1-806b-4e93cfa08d25", "external_fact", "2026-10-08 02:43:20.543976+00",
     "В информационно-аналитическом комментарии Банка России «Инфляция в России» № 12 (120) "
     "за декабрь 2025 г. годовая инфляция за 2025 год приведена как 5,6%."),
    ("6d9a12ff-7a60-48ad-8e72-973ff72530b1", "external_fact", "2026-10-08 21:32:57.95803+00",
     "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года "
     "составила 13,1% для граждан с накоплениями и 15,6% для граждан без накоплений "
     "(по данным опроса «инФОМ»)."),
    ("2d010d53-8818-4362-9475-387b71b63bb5", "external_fact", "2026-10-08 21:32:57.95803+00",
     "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
     "декабрьского макроэкономического опроса Банка России составил 6,3%."),
    ("391c4388-c0dc-461b-a843-23d4d6bfcd17", "temporal_fact", "2026-10-09 03:09:24.516258+00",
     "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
     "(по данным опроса инФОМ для Банка России)."),
    ("e189c80d-8544-443f-b295-681c0d1b6ed4", "temporal_fact", "2026-10-09 03:09:24.516258+00",
     "Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
     "составила 5,59% (по данным Росстата)."),
    ("58e2d5d4-fed4-443f-aba3-b5835a61f638", "temporal_fact", "2026-10-09 14:17:10.596233+00",
     "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
     "декабрьского макроэкономического опроса Банка России составил 6,3%."),
    ("c6729d4c-72a7-4f74-a432-e9f41de6f13c", "external_fact", "2026-10-11 12:06:36.64334+00",
     "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."),
    ("2f456e85-6f68-46dc-b4d3-fce63f95f0d9", "external_fact", "2026-10-11 12:06:36.64334+00",
     "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 6,6% "
     "(по данным макроэкономического опроса Банка России за сентябрь 2026 года)."),
    ("cc75e97f-a3d9-4938-9095-261764f6621e", "external_fact", "2026-10-11 12:06:36.64334+00",
     "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% "
     "(по данным опроса ООО «инФОМ»)."),
    ("7c8c9afe-28ff-401b-aa26-46d61c95b963", "external_fact", "2026-10-11 12:06:36.64334+00",
     "Наблюдаемая населением годовая инфляция в России в сентябре 2026 года увеличилась "
     "до 15,1% (по данным опроса ООО «инФОМ»)."),
    ("44bd1460-bb72-4521-8d54-ffd884a93b20", "external_fact", "2026-10-12 06:14:18.713214+00",
     "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года на уровне 6,5%."),
    ("07126d06-8bf2-4f8f-bdc4-71944ef1d905", "external_fact", "2026-10-12 06:14:18.713214+00",
     "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%."),
    ("7d810f8c-4ace-4b79-a757-02181db94dd6", "external_fact", "2026-10-12 06:14:18.713214+00",
     "Софья Донец из Т-Инвестиций оценивает, что повышение НДС добавит 1,5% к инфляции на "
     "горизонте 5-6 месяцев в 2026 году."),
    ("e61b4a58-449f-4362-b3f5-33259c52cc5d", "external_fact", "2026-10-12 06:14:18.713214+00",
     "Евгений Егоров из ACI Russia оценивает, что цены вырастут на дополнительные 2% из-за "
     "налоговых инициатив в 2026 году."),
    ("136a0ca0-f27b-4e5c-834e-064d62d558b6", "external_fact", "2026-10-12 06:14:18.713214+00",
     "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%."),
)


def _row(claim_id: str) -> tuple[str, str, str, str]:
    return next(row for row in LIVE_ROWS if row[0] == claim_id)


def _live_records() -> list[FoldRecord]:
    return [
        FoldRecord(
            claim_id=claim_id,
            statement=statement,
            claim_type=claim_type,
            created_at=dt.datetime.fromisoformat(created_at.replace("+00", "+00:00")),
        )
        for claim_id, claim_type, created_at, statement in LIVE_ROWS
    ]


def _pair(*, op_id: str, candidate_id: str) -> bool:
    op = _row(op_id)
    cand = _row(candidate_id)
    return value_duplicate_pair(
        op_claim_type=op[1],
        op_statement=op[3],
        op_as_of_year=None,
        candidate_claim_type=cand[1],
        candidate_statement=cand[3],
        candidate_as_of_year=None,
    )


# ─── таблица по 20 живым утверждениям стенда: ровно две пары, ложных нет ──────


def test_live_table_folds_exactly_two_pairs() -> None:
    pairs = fold_value_duplicate_pairs(_live_records())
    assert pairs == [
        ("9266248e-2433-4a12-a3f7-b51ff10ad803", "e189c80d-8544-443f-b295-681c0d1b6ed4"),
        ("c6729d4c-72a7-4f74-a432-e9f41de6f13c", "136a0ca0-f27b-4e5c-834e-064d62d558b6"),
    ], pairs


def test_indicator_veto_near_misses_are_not_folded() -> None:
    """Тройка «5,6%» (внешние пересказы пресс-релиза, комментария ЦБ и оценок Сбера):
    тип равен, значение {5,6} равно, период покрыт — но словарь показателей пересекается
    слабее INDICATOR_OVERLAP_MIN (1/8, 1/8, 3/11 против 3/5): вето T7.83a/T7.96 обязано
    сохранить все три записи отдельными строками."""
    folded = set(fold_value_duplicate_pairs(_live_records()))
    for left, right in (
        ("c970bc08-6278-4766-9d74-7e541284be2a", "73d52835-2ed8-4ac3-9505-59845536f2a9"),
        ("c970bc08-6278-4766-9d74-7e541284be2a", "e2c52955-864c-43f1-806b-4e93cfa08d25"),
        ("73d52835-2ed8-4ac3-9505-59845536f2a9", "e2c52955-864c-43f1-806b-4e93cfa08d25"),
    ):
        assert (_pair(op_id=right, candidate_id=left) is False) or (
            _pair(op_id=left, candidate_id=right) is False
        )
        assert (left, right) not in folded and (right, left) not in folded


def test_byte_identical_statements_of_different_types_are_not_folded() -> None:
    """2d010d53 (external_fact) и 58e2d5d4 (temporal_fact) — байт-в-байт одна формулировка
    (прецедент T7.9: побайтовый дедуп различает типы). Равенство типа — условие и гейта,
    и свёртки: разные записи остаются разными строками карточки."""
    assert _pair(
        op_id="58e2d5d4-fed4-443f-aba3-b5835a61f638", candidate_id="2d010d53-8818-4362-9475-387b71b63bb5"
    ) is False
    folded = set(fold_value_duplicate_pairs(_live_records()))
    assert (
        "2d010d53-8818-4362-9475-387b71b63bb5",
        "58e2d5d4-fed4-443f-aba3-b5835a61f638",
    ) not in folded


def test_range_awareness_of_the_gate_is_reused_by_the_showcase() -> None:
    """Ловушка AGENTS T7.98(4): витрина значений (`_value_occurrences`) целые диапазоны
    не читает, гейт — читает. Свёртка обязана пользоваться гейтовым `statement_values`:
    «6–7%» (целый диапазон) и «6,0–7,0%» (десятильный) — одно множество значений."""
    assert _pair(
        op_id="136a0ca0-f27b-4e5c-834e-064d62d558b6", candidate_id="c6729d4c-72a7-4f74-a432-e9f41de6f13c"
    ) is True


# ─── предикат = критерий гейта минус условие записи ───────────────────────────


def test_predicate_has_no_write_time_conditions() -> None:
    """У предиката нет параметров `relied`, `has_support_link`, `has_counter_link`:
    они про склейку при записи (территория гейта), а витрина ничего не записывает."""
    params = inspect.signature(value_duplicate_pair).parameters
    for forbidden in ("relied", "has_support_link", "has_counter_link"):
        assert forbidden not in params


def test_gate_does_not_call_the_showcase_predicate() -> None:
    """Гейт не переписан на новый предикат: его тело с причинами по каждому шагу
    осталось прежним (поведение гейта закреплено прежними тестами побайтово)."""
    assert "value_duplicate_pair" not in inspect.getsource(find_value_duplicates)


def test_predicate_matches_gate_when_relied_is_absent() -> None:
    """Пара живых карт: гейт без опоры молчит (нет решения), витрина сворачивает —
    ровно это и есть «критерий гейта минус relied» на поведении, а не на словах."""
    op = _row("e189c80d-8544-443f-b295-681c0d1b6ed4")
    cand = _row("9266248e-2433-4a12-a3f7-b51ff10ad803")
    claim = ValueDuplicateClaim(
        index=0, statement=op[3], claim_type=op[1], as_of_year=None,
        has_support_link=True, has_counter_link=False,
    )
    candidate = ValueDuplicateCandidate(
        claim_id=cand[0], statement=cand[3], claim_type=cand[1], as_of_year=None, relied=False
    )
    kept = find_value_duplicates([claim], [candidate])
    # гейт без опоры не склеивает: решение есть, но это «kept» (запись остаётся отдельной)
    assert kept and kept[0]["action"] == "kept", kept
    assert value_duplicate_pair(
        op_claim_type=op[1], op_statement=op[3], op_as_of_year=None,
        candidate_claim_type=cand[1], candidate_statement=cand[3], candidate_as_of_year=None,
    ) is True


def test_predicate_agrees_with_gate_decision_when_gate_merges() -> None:
    """Та же пара с объявленной опорой: гейт даёт `reverified` — предикат истинен
    и в этом случае (условия type+values+period+indicator общие)."""
    op = _row("e189c80d-8544-443f-b295-681c0d1b6ed4")
    cand = _row("9266248e-2433-4a12-a3f7-b51ff10ad803")
    claim = ValueDuplicateClaim(
        index=0, statement=op[3], claim_type=op[1], as_of_year=None,
        has_support_link=True, has_counter_link=False,
    )
    candidate = ValueDuplicateCandidate(
        claim_id=cand[0], statement=cand[3], claim_type=cand[1], as_of_year=None, relied=True
    )
    decisions = find_value_duplicates([claim], [candidate])
    assert decisions and decisions[0]["action"] == "reverified"
    assert value_duplicate_pair(
        op_claim_type=op[1], op_statement=op[3], op_as_of_year=None,
        candidate_claim_type=cand[1], candidate_statement=cand[3], candidate_as_of_year=None,
    ) is True


def test_predicate_refuses_empty_values_and_empty_period() -> None:
    # целые/годы без десятильного значения — тихий отказ (T7.9 владеет побайтовыми дублями)
    assert value_duplicate_pair(
        op_claim_type="temporal_fact", op_statement="Инфляция ускорилась до 8% в 2025 году.",
        op_as_of_year=None,
        candidate_claim_type="temporal_fact", candidate_statement="Инфляция ускорилась до 8% в 2025 году.",
        candidate_as_of_year=None,
    ) is False
    # период операции пуст — консервативная тишина гейта сохранена
    assert value_duplicate_pair(
        op_claim_type="external_fact", op_statement="Консенсус-прогноз аналитиков составляет 6,6%.",
        op_as_of_year=None,
        candidate_claim_type="external_fact",
        candidate_statement="Консенсус-прогноз аналитиков составляет 6,6% в 2026 году.",
        candidate_as_of_year=None,
    ) is False


def test_predicate_direction_is_the_gate_direction() -> None:
    """Направление периода — как у гейта: период операции покрыт периодом кандидата.
    Более широкий период у более поздней записи — отказ (сравнение неопределённого
    периода с более узким честнее не становится)."""
    narrow = "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2%."
    wide = (
        "Инфляционные ожидания населения России в сентябре 2026 года и в сентябре 2025 года "
        "составили 14,2%."
    )
    assert value_duplicate_pair(
        op_claim_type="external_fact", op_statement=narrow, op_as_of_year=None,
        candidate_claim_type="external_fact", candidate_statement=wide, candidate_as_of_year=None,
    ) is True
    assert value_duplicate_pair(
        op_claim_type="external_fact", op_statement=wide, op_as_of_year=None,
        candidate_claim_type="external_fact", candidate_statement=narrow, candidate_as_of_year=None,
    ) is False


def test_declared_metric_mismatch_refuses_pair() -> None:
    """Обе стороны назвали метку показателя — метки обязаны совпасть (правило (а)
    `indicator_check`): витрина наследует и это условие гейта."""
    assert value_duplicate_pair(
        op_claim_type="external_fact",
        op_statement="Ожидания населения: инфляция через год 14,2%.",
        op_as_of_year=None,
        candidate_claim_type="external_fact",
        candidate_statement="Наблюдаемая инфляция: 14,2% за год.",
        candidate_as_of_year=None,
        op_metric="инфляционные ожидания",
        candidate_metric="наблюдаемая инфляция",
    ) is False


# ─── отбор якоря: ранняя запись, детерминизм, без цепочек под свёрнутыми ──────


def _synthetic(claim_id: str, statement: str, created_at: str, claim_type: str = "external_fact") -> FoldRecord:
    return FoldRecord(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type,
        created_at=dt.datetime.fromisoformat(created_at),
    )


_S1 = "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2%."
_S2 = (
    "Инфляционные ожидания населения России в сентябре 2026 года и в сентябре 2025 года "
    "составили 14,2%."
)


def test_anchor_is_the_earlier_record_and_tie_breaks_by_id() -> None:
    early = _synthetic("a" * 36, _S1, "2026-01-01T00:00:00+00:00")
    late = _synthetic("b" * 36, _S1, "2026-02-01T00:00:00+00:00")
    assert fold_value_duplicate_pairs([late, early]) == [(early.claim_id, late.claim_id)]

    same_time_a = _synthetic("m" * 36, _S1, "2026-01-01T00:00:00+00:00")
    same_time_b = _synthetic("n" * 36, _S1, "2026-01-01T00:00:00+00:00")
    assert fold_value_duplicate_pairs([same_time_b, same_time_a]) == [
        (same_time_a.claim_id, same_time_b.claim_id)
    ]


def test_chain_of_duplicates_collects_under_the_earliest() -> None:
    a = _synthetic("a" * 36, _S1, "2026-01-01T00:00:00+00:00")
    b = _synthetic("b" * 36, _S1, "2026-02-01T00:00:00+00:00")
    c = _synthetic("c" * 36, _S1, "2026-03-01T00:00:00+00:00")
    assert fold_value_duplicate_pairs([a, b, c]) == [(a.claim_id, b.claim_id), (a.claim_id, c.claim_id)]


def test_folded_record_does_not_become_an_anchor() -> None:
    """Свёрнутая запись не может принять ещё одну: пара показана один раз, блок плоский."""
    a = _synthetic("a" * 36, _S1, "2026-01-01T00:00:00+00:00")
    b = _synthetic("b" * 36, _S1, "2026-02-01T00:00:00+00:00")
    c = _synthetic("c" * 36, _S1, "2026-03-01T00:00:00+00:00")
    pairs = fold_value_duplicate_pairs([a, b, c])
    assert pairs == [(a.claim_id, b.claim_id), (a.claim_id, c.claim_id)]
    assert b.claim_id not in {primary for primary, _ in pairs}


def test_period_direction_decides_the_fold() -> None:
    """Поздняя запись с более широким периодом не сворачивается под ранней с узким;
    поздняя с покрытым периодом — сворачивается."""
    narrow_early = _synthetic("a" * 36, _S1, "2026-01-01T00:00:00+00:00")
    wide_late = _synthetic("b" * 36, _S2, "2026-02-01T00:00:00+00:00")
    assert fold_value_duplicate_pairs([narrow_early, wide_late]) == []
    wide_early = _synthetic("a" * 36, _S2, "2026-01-01T00:00:00+00:00")
    narrow_late = _synthetic("b" * 36, _S1, "2026-02-01T00:00:00+00:00")
    assert fold_value_duplicate_pairs([wide_early, narrow_late]) == [
        (wide_early.claim_id, narrow_late.claim_id)
    ]


def test_card_without_duplicates_is_untouched() -> None:
    records = [
        _synthetic("a" * 36, _S1, "2026-01-01T00:00:00+00:00"),
        _synthetic(
            "b" * 36,
            "Инфляционные ожидания населения России в сентябре 2026 года составили 14,1%.",
            "2026-02-01T00:00:00+00:00",
        ),
        _synthetic("c" * 36, _S1, "2026-03-01T00:00:00+00:00", claim_type="temporal_fact"),
    ]
    assert fold_value_duplicate_pairs(records) == []


# ─── свёртка карточки: список, блок, честный счётчик (без БД) ─────────────────


def _item(claim_id: str, statement: str, relation: str) -> dict[str, object]:
    return {
        "id": claim_id,
        "statement": statement,
        "claim_type": "external_fact",
        "relation": relation,
        "relation_label": "создано этим вопросом" if relation == "created" else "использовано из знаний",
        "type_label": "факт о периоде",
        "grade_label": "E1",
        "reliability": {"label": "Подтверждено слабо", "level": "weak", "color": "amber", "hint": "…"},
    }


def test_fold_moves_duplicate_under_primary_and_counts_it() -> None:
    primary = _item("a" * 36, _S1, "relied")
    duplicate = _item("b" * 36, _S1, "created")
    claims = [duplicate]
    relied = [primary]
    facts = [
        FoldRecord(
            claim_id=primary["id"], statement=_S1, claim_type="external_fact", created_at=dt.datetime(2026, 1, 1)
        ),
        FoldRecord(
            claim_id=duplicate["id"], statement=_S1, claim_type="external_fact", created_at=dt.datetime(2026, 2, 1)
        ),
    ]
    folded = web_answer._fold_value_duplicates(claims=claims, relied_claims=relied, facts=facts)

    assert claims == [] and relied == [primary]
    assert folded == [duplicate]  # убранный из claims — учтён честным счётчиком
    block = primary["duplicates"]
    assert isinstance(block, list) and len(block) == 1
    entry = block[0]
    assert entry["id"] == duplicate["id"]
    assert entry["text"] == f"Повторная запись того же значения: {_S1}"
    assert entry["duplicate_label"] == "Повторная запись того же значения"
    assert entry["link_label"] == "открыть повторную запись"
    assert entry["relation_label"] == "создано этим вопросом"  # своя связь с вопросом сохранена
    assert entry["reliability"]["label"] == "Подтверждено слабо"  # своя оценка, не оценка основной
    assert primary["duplicates_lead"] == "и 1 повторная запись того же значения"


def test_fold_without_duplicates_changes_nothing() -> None:
    claims = [_item("a" * 36, _S1, "created"), _item("b" * 36, "Другой показатель: 14,1%.", "created")]
    snapshot = [dict(item) for item in claims]
    facts = [
        FoldRecord(
            claim_id=claims[0]["id"], statement=_S1, claim_type="external_fact", created_at=dt.datetime(2026, 1, 1)
        ),
        FoldRecord(
            claim_id=claims[1]["id"],
            statement="Другой показатель: 14,1%.",
            claim_type="external_fact",
            created_at=dt.datetime(2026, 2, 1),
        ),
    ]
    folded = web_answer._fold_value_duplicates(claims=claims, relied_claims=[], facts=facts)
    assert folded == []
    assert claims == snapshot
    assert not any("duplicates" in item for item in claims)


def test_counter_phrase_uses_honest_plural_forms() -> None:
    assert web_answer._duplicates_lead(1) == "и 1 повторная запись того же значения"
    assert web_answer._duplicates_lead(2) == "и 2 повторные записи того же значения"
    assert web_answer._duplicates_lead(5) == "и 5 повторных записей того же значения"


def test_duplicate_keys_are_a_closed_set() -> None:
    assert web_answer.DUPLICATE_KEYS == (
        "duplicate_record",
        "duplicate_link",
        "duplicate_count_one",
        "duplicate_count_few",
        "duplicate_count_many",
    )
