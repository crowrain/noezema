"""Unit: чистое правило «кластер вокруг производителя» и его подпись (T7.87, ADR-0035 A).

Здесь проверяется только правило отображения: оно читает уже записанные факты оценки
(роли улик, записанные группы независимости, записки атрибуции) и либо называет
производителя, либо молчит. Оценок здесь никто не вычисляет (ADR-0026), и краснота этих
тестов — про то же: правило не имеет права додумывать ни группу, ни имя.

С T7.116 (часть б, решение пользователя 2026-10-15) страничный указатель (якорь-родитель)
называет строку только когда формулировка утверждения называет того же производителя
словарным алиасом; записи `value_attribution`/`value_self_publication` называют по-прежнему.
Здесь это затронуло тесты, где имя давал один указатель: формулировка передаётся явно
(стендовая форма «…по данным Росстата…») — намерение каждого теста сохранено; полный
набор новых случаев закрепляет `tests/unit/test_producer_view_page_pointer_t7116.py`.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.web.labels import MAX_HINT_CHARS, MAX_LABEL_CHARS
from apps.web.producer_view import (
    EVIDENCE_SHORTFALL_REASONS,
    INDEPENDENCE_SHORTFALL_REASONS,
    PRODUCER_CASE_REASONS,
    producer_publication_case,
    producer_publication_name,
)
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL, reliability
from packages.memory.scope import VALUE_ATTRIBUTION_SCHEMA

pytestmark = pytest.mark.unit

SHORTFALL: list[str] = ["insufficient_independence"]
REPO_ROOT = Path(__file__).resolve().parents[2]

ROSSTAT = "https://rosstat.gov.ru/"
CBR = "https://cbr.ru/"


def _row(
    *,
    group: str | None = "g0",
    role: str | None = "support",
    primary_key: str | None = None,
    primary_name: str | None = None,
    primary_uri: str | None = None,
    parent_uri: str | None = None,
    attribution_schema: str = VALUE_ATTRIBUTION_SCHEMA,
) -> dict[str, Any]:
    """Одна строка улики действующей оценки — ровно те колонки, которые отдаёт карточка."""
    scope: dict[str, Any] = {"scope_schema": "host-scope-v1", "source_domain": "interfax.example"}
    if primary_key is not None:
        scope["value_attribution"] = {
            "schema": attribution_schema,
            "primary_key": primary_key,
            "primary_name": primary_name,
            "primary_uri": primary_uri or ROSSTAT,
            "parent_source_id": str(uuid.uuid4()),
            "method": "host-value-attribution-v5",
            "basis_fragment": "По данным Росстата",
            "pairing": "value_and_primary_in_fragment",
        }
    return {
        "assessment_role": role,
        "group_id": group,
        "scope": scope,
        "parent_source_id": str(uuid.uuid4()) if parent_uri is not None else None,
        "parent_uri": parent_uri,
    }


def _name(
    *rows: dict[str, Any], reasons: list[str] = SHORTFALL, statement: str | None = None
) -> str | None:
    return producer_publication_name(list(rows), reasons=reasons, claim_statement=statement)


#: Называющая формулировка (T7.116 часть б): указатель даёт имя только ей (стендовая форма
#: подписи «…по данным Росстата»). В тестах этого файла она подставляет ровно то имя,
#: которое и раньше бралось из указателя, — намерение тестов не меняется.
STATEMENT_ROSSTAT: str = "Инфляция в России по итогам 2025 года составила 5,59% по данным Росстата."


# ─── условие (а): причину обязана давать нехватка подтверждений (T7.87 + T7.100) ────


def test_the_rule_holds_on_too_few_independence_groups() -> None:
    """Базовый случай правила: одна записанная группа и указатель на одного производителя."""
    assert _name(
        _row(parent_uri=ROSSTAT),
        _row(parent_uri="https://www.garant.ru/cpi"),
        statement=STATEMENT_ROSSTAT,
    ) == "Росстат"  # garant.ru не словарный хост: имя даёт Росстат-указатель
    assert frozenset({"insufficient_independence"}) == INDEPENDENCE_SHORTFALL_REASONS


def test_single_evidence_is_the_same_case() -> None:
    """T7.100 (ADR-0035 §18): `insufficient_evidence` — тот же витринный случай.

    Правила записывают этот код, когда поддерживающих улик меньше `min_support_evidence`
    (для external_fact/temporal_fact — ровно одна). Подтверждение при этом есть — это
    публикация производителя; метка не должна зависеть от того, какой из двух кодов
    записали правила. Решение пользователя 2026-10-12.
    """
    assert _name(
        _row(parent_uri=ROSSTAT), reasons=["insufficient_evidence"], statement=STATEMENT_ROSSTAT
    ) == "Росстат"
    assert (
        _name(_row(primary_key="rosstat", primary_name="Росстат"), reasons=["insufficient_evidence"])
        == "Росстат"
    )
    assert frozenset({"insufficient_evidence"}) == EVIDENCE_SHORTFALL_REASONS
    assert PRODUCER_CASE_REASONS == INDEPENDENCE_SHORTFALL_REASONS | EVIDENCE_SHORTFALL_REASONS


def test_single_evidence_without_a_pointer_stays_weak() -> None:
    """Fail-closed (T7.100): единственная улика без записанного указателя — называть
    производителя нечем. Хост прочитанной страницы в указатели не входит (правило читает
    только `value_attribution` и якорь-родителя), поэтому страница самого ЦБ без записанного
    решения о происхождении значения остаётся на прежней подписи."""
    assert _name(_row(), reasons=["insufficient_evidence"]) is None
    assert _name(_row(group=None), reasons=["insufficient_evidence"]) is None


def test_single_evidence_with_two_groups_stays_weak() -> None:
    """Fail-closed (T7.100): указатель есть, но групп две — случай не признан."""
    assert (
        _name(
            _row(group="g0", parent_uri=ROSSTAT),
            _row(group="g1", parent_uri=ROSSTAT),
            reasons=["insufficient_evidence"],
        )
        is None
    )


def test_case_carries_the_support_count() -> None:
    """T7.100: правило отдаёт и число подтверждающих улик — из тех же записанных строк."""
    single = producer_publication_case(
        [_row(parent_uri=ROSSTAT)],
        reasons=["insufficient_evidence"],
        claim_statement=STATEMENT_ROSSTAT,
    )
    assert single is not None and (single.name, single.supports) == ("Росстат", 1)
    cluster = producer_publication_case(
        [_row(parent_uri=ROSSTAT), _row(parent_uri="https://www.garant.ru/cpi")],
        reasons=SHORTFALL,
        claim_statement=STATEMENT_ROSSTAT,
    )
    assert cluster is not None and (cluster.name, cluster.supports) == ("Росстат", 2)


def test_other_shortfalls_keep_the_previous_wording() -> None:
    """Другие причины оценки — не витринный случай производителя.

    `insufficient_evidence` убран из этого списка правкой T7.100 (ADR-0035 §18): теперь
    это причина случая, если выполнены условия (б)–(г). `no_evidence` (улик нет вовсе)
    случаем не становится.
    """
    for reasons in (["requirements_met"], ["as_of_missing"], ["no_evidence"], []):
        assert _name(_row(parent_uri=ROSSTAT), reasons=reasons) is None, reasons


def test_reason_gate_is_tied_to_the_rules_engine() -> None:
    """Причина, на которой держится подпись, обязана существовать в rules engine.

    Отдельно проверяется, что семейство `independence_<relation>_not_met` сюда НЕ входит:
    оно строится из `required_independence` (отношение сред, §8.7.3), а не из числа групп
    независимости источников, и объясняет другой дефект оценки. С T7.100 проверяются обе
    причины случая и то, что `no_evidence` в гейт не входит.
    """
    source = (REPO_ROOT / "packages/memory/rules_engine.py").read_text(encoding="utf-8")
    for reason in PRODUCER_CASE_REASONS:
        assert f'"{reason}"' in source, reason
    assert 'f"independence_{rule.required_independence}_not_met"' in source
    assert not any(reason.startswith("independence_") for reason in PRODUCER_CASE_REASONS)
    assert "no_evidence" not in PRODUCER_CASE_REASONS


# ─── условие (в): указатель на производителя ──────────────────────────────────


def test_one_producer_through_the_page_pointer() -> None:
    assert _name(
        _row(parent_uri=ROSSTAT),
        _row(parent_uri="https://rosstat.gov.ru/press/cpi.html"),
        statement=STATEMENT_ROSSTAT,
    ) == "Росстат"  # хост самой улики именем по-прежнему не считается


def test_one_producer_through_the_recorded_attribution() -> None:
    rows = [
        _row(primary_key="rosstat", primary_name="Росстат"),
        _row(primary_key="rosstat", primary_name="Росстат"),
    ]
    assert _name(*rows) == "Росстат"


def test_recorded_attribution_wins_over_the_page_pointer() -> None:
    """T7.109 (часть б3, решение пользователя 2026-10-13): запись о происхождении значения
    приоритетнее страничного указателя.

    Было (T7.87): «запись говорит „Росстат“, указатель ведёт на Банк России — один
    производитель не доказан» → None. Стало: запись — решение детектора значения, прочитанного
    прямо на этой улике; якорь-родитель ЭТОЙ ЖЕ строки в выборке имени не участвует. Прежний
    fail-closed сохранён по краям: разночтение записей двух разных строк (тест ниже) и строки
    без записи (указатель ведёт, как вёл)."""
    assert _name(_row(primary_key="rosstat", primary_name="Росстат", parent_uri=CBR)) == "Росстат"


def test_two_producers_keep_the_previous_wording() -> None:
    rows = [
        _row(primary_key="rosstat", primary_name="Росстат"),
        _row(primary_key="cbr", primary_name="Банк России"),
    ]
    assert _name(*rows) is None


def test_a_pointer_without_a_dictionary_producer_disqualifies() -> None:
    """Имя вне словаря первоисточников — не производитель: правила молчат, текст прежний."""
    assert _name(_row(parent_uri="https://blog.example.org/cpi.html")) is None
    assert _name(_row(primary_key="unknown", primary_name="Неизвестный")) is None
    assert _name(_row(primary_key="rosstat", primary_name="Росстатstatistics")) is None


def test_a_broken_attribution_record_is_not_evidence_of_a_producer() -> None:
    assert _name(_row(primary_key="rosstat", primary_name="Росстат", attribution_schema="v0")) is None


def test_no_pointer_names_nobody() -> None:
    """Группа есть, но назвать производителя нечем: подсказка «производитель» была бы выдумкой."""
    assert _name(_row(), _row()) is None


# ─── условие (б): ровно одна записанная группа ────────────────────────────────


def test_two_recorded_groups_keep_the_previous_wording() -> None:
    rows = [_row(group="g0", parent_uri=ROSSTAT), _row(group="g1", parent_uri=ROSSTAT)]
    assert _name(*rows) is None


def test_missing_group_membership_is_not_guessed() -> None:
    """Улика без записанной группы — пробел в данных, а не «вторая группа» и не «одна группа»."""
    assert _name(_row(group="g0", parent_uri=ROSSTAT), _row(group=None, parent_uri=ROSSTAT)) is None
    assert _name(_row(group="", parent_uri=ROSSTAT)) is None


# ─── условие (г): возражение ──────────────────────────────────────────────────


def test_counterevidence_in_this_assessment_disqualifies() -> None:
    rows = [_row(parent_uri=ROSSTAT), _row(parent_uri="https://www.interfax.ru/x", role="counter")]
    assert _name(*rows) is None


def test_evidence_of_another_assessment_is_ignored() -> None:
    """Строки без роли — улики не этой оценки: они не участвуют ни в группах, ни в указателях."""
    rows = [_row(parent_uri=ROSSTAT), _row(role=None, group="g7", parent_uri=CBR)]
    assert _name(*rows, statement=STATEMENT_ROSSTAT) == "Росстат"


# ─── подпись: уровень сохранён, меняется только название случая ────────────────


def test_the_producer_badge_keeps_the_level_and_the_color() -> None:
    plain = reliability("temporal_fact", "hypothesis", "E1", head_state="current")
    badge = reliability("temporal_fact", "hypothesis", "E1", head_state="current", single_producer="Росстат")
    assert badge["level"] == plain["level"] == LEVEL_WEAK
    assert badge["color"] == plain["color"]  # жёлтый: зелёный зарезервирован за «Проверено»
    assert badge["label"] == PRODUCER_LABEL
    assert "Росстат" in badge["hint"]
    assert "предположение" not in badge["hint"]


def test_the_producer_hint_stays_within_the_label_limits() -> None:
    assert len(PRODUCER_LABEL) <= MAX_LABEL_CHARS
    for name in ("Росстат", "Банк России", "Федеральная служба государственной статистики"):
        hint = reliability("temporal_fact", "hypothesis", "E2", head_state="current", single_producer=name)["hint"]
        assert len(hint) <= MAX_HINT_CHARS, name
        assert "истинность не проверена" in hint


def test_single_evidence_hint_names_one_publication() -> None:
    """T7.100: при единственной подтверждающей улике подсказка говорит «одна публикация»,
    а не «все подтверждения — один кластер»: кластер предполагает несколько подтверждений.
    При нескольких уликах и при непереданном числе (прежний контракт) — прежняя формулировка."""
    single = reliability(
        "external_fact", "hypothesis", "E1", head_state="current", single_producer="Росстат", producer_supports=1
    )
    assert single["label"] == PRODUCER_LABEL and single["level"] == LEVEL_WEAK
    assert "одна публикация" in single["hint"] and "кластер" not in single["hint"]
    assert "истинность не проверена" in single["hint"]
    assert len(single["hint"]) <= MAX_HINT_CHARS
    many = reliability(
        "external_fact", "hypothesis", "E1", head_state="current", single_producer="Росстат", producer_supports=8
    )
    assert "один кластер" in many["hint"] and "одна публикация" not in many["hint"]
    legacy = reliability("external_fact", "hypothesis", "E1", head_state="current", single_producer="Росстат")
    assert legacy == many  # прежний контракт T7.87 не изменился


def test_single_evidence_hint_stays_within_the_limit_and_keeps_the_level() -> None:
    for name in ("Росстат", "Банк России", "Федеральная служба государственной статистики"):
        badge = reliability(
            "temporal_fact", "hypothesis", "E2", head_state="current", single_producer=name, producer_supports=1
        )
        assert len(badge["hint"]) <= MAX_HINT_CHARS, name
        assert "истинность не проверена" in badge["hint"]
        plain = reliability("temporal_fact", "hypothesis", "E2", head_state="current")
        assert badge["level"] == plain["level"] == LEVEL_WEAK
        assert badge["color"] == plain["color"]


def test_suspicious_name_single_case_prints_no_name() -> None:
    """Запасная подсказка единственной улики тоже не выдумывает имя."""
    for name in ("Росстат 559", "claim_assessed", "https://rosstat.gov.ru/"):
        badge = reliability(
            "temporal_fact", "hypothesis", "E1", head_state="current", single_producer=name, producer_supports=1
        )
        assert badge["label"] == PRODUCER_LABEL
        assert name not in badge["hint"]
        assert "одна публикация" in badge["hint"]
        assert len(badge["hint"]) <= MAX_HINT_CHARS


def test_a_suspicious_producer_name_is_not_printed() -> None:
    """Имя с цифрами/кодами наружу не выводится: подпись остаётся, но без имени."""
    for name in ("Росстат 559", "claim_assessed", "https://rosstat.gov.ru/"):
        badge = reliability("temporal_fact", "hypothesis", "E1", head_state="current", single_producer=name)
        assert badge["label"] == PRODUCER_LABEL
        assert name not in badge["hint"]
        assert len(badge["hint"]) <= MAX_HINT_CHARS


def test_no_new_text_claims_verification() -> None:
    """Ни одна новая подпись не утверждает проверку: слово «проверено» — только у уровня verified."""
    texts = [PRODUCER_LABEL]
    for grade in ("E1", "E2"):
        for name in ("Росстат", "Федеральная служба государственной статистики"):
            for supports in (None, 1, 8):
                badge = reliability(
                    "temporal_fact", "hypothesis", grade, head_state="current",
                    single_producer=name, producer_supports=supports,
                )
                texts += [badge["label"], badge["hint"]]
    for text in texts:
        assert re.search(r"(?<!не )\bпроверен", text.lower(), flags=re.IGNORECASE) is None, text


def test_verified_and_disputed_cases_are_untouched() -> None:
    """Две группы и E3 (случай E3 supported) остаются на «Проверено»: имя производителя там не участвует."""
    for status in ("supported", "disputed", "refuted", "deferred"):
        plain = reliability("temporal_fact", status, "E3", head_state="current")
        with_name = reliability(
            "temporal_fact", status, "E3", head_state="current", single_producer="Росстат"
        )
        assert with_name == plain, status
    supported = reliability("temporal_fact", "supported", "E3", head_state="current")
    assert supported["label"] == "Проверено"
    for grade in (None, "E0", "E3"):
        plain = reliability("temporal_fact", "hypothesis", grade, head_state="current")
        with_name = reliability(
            "temporal_fact", "hypothesis", grade, head_state="current", single_producer="Росстат"
        )
        assert with_name == plain, grade


def test_without_the_rule_result_nothing_changes() -> None:
    """Прежний текст остаётся, если правило молчит (среди прочего — при pending-голове)."""
    for state in ("none", "pending", "invalid"):
        plain = reliability("temporal_fact", "hypothesis", "E1", head_state=state)
        with_name = reliability(
            "temporal_fact", "hypothesis", "E1", head_state=state, single_producer="Росстат"
        )
        assert with_name == plain, state
