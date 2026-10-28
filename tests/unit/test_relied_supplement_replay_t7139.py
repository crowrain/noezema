"""T7.139 (тест в, ADR-0038 §16): реплей ВСЕХ решений стендовых фикстур чистыми функциями.

Фикстуры `tests/fixtures/t7139/` — срез стенда run 256b16b-v30 (вопрос c3): 196 событий
сессий (`sessions.jsonl`: pack_claim_ids — секция `claims_evidence` пака, decision — payload
события `claim_created`) и 82 затронутых утверждения (`claims.jsonl`). Рплей прогоняет КАЖДОЕ
предложение куратора через функции частей 2–3 ровно так, как их вызывает `_curator`, и
сверяет итог с замером менеджера: переносы опоры снятых перепроверок — 9 кандидатов в
4 сессиях, из них 7 переносятся, 2 (26b0b309) упираются в общий потолок (опора куратора
уже ровно 8 id — `rely[k]`-отказы того же payload'а), additions части 3 — ровно 2.

Разметка карточки воспроизводит сборку `_curator`: действующие операции предложения
(конверсии гейта value-dup — с записанным id, новые — под ключом `new<i>`), затем вся опора;
паковые записи — id секции вне карточки.

Красность на 256b16b: модуля `apps/orchestrator/relied_supplement.py` там нет вовсе.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from apps.orchestrator.orchestrator import RELIED_CLAIMS_LIMIT
from apps.orchestrator.relied_supplement import (
    SupplementRecord,
    fresher_forecast_supplement,
    relied_from_dropped_operations,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7139"


def _events() -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / "sessions.jsonl").read_text(encoding="utf-8").splitlines()]


def _claims() -> dict[str, dict[str, Any]]:
    return {
        row["id"]: row
        for row in map(json.loads, (FIXTURES / "claims.jsonl").read_text(encoding="utf-8").splitlines())
    }


def _as_of(raw: Any) -> dt.date | dt.datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.date.fromisoformat(text[:10])


def _replay() -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
    """(переносы по сессиям, отказы по сессиям, additions части 3 по сессиям)."""
    claims = _claims()

    def record(claim_id: str) -> SupplementRecord:
        row = claims[claim_id]
        return SupplementRecord(claim_id, str(row["statement"]), _as_of(row.get("as_of")))

    transfers: dict[str, list[str]] = {}
    refusals: dict[str, list[str]] = {}
    additions: dict[str, list[dict[str, Any]]] = {}
    events = _events()
    assert len(events) == 196
    for event in events:
        decision = event.get("decision") or {}
        if "claims" not in decision:
            continue  # событие применения (claim_id/assessed_scope) — предложения нет
        session = str(event["session_id"])
        pack_ids = [str(cid) for cid in event["pack_claim_ids"]]
        dropped_ops = decision.get("rejected_operations") or []
        # часть 2: refs ВЗЯТЫ ИЗ ПРЕДЛОЖЕНИЯ (operation.existing_claim_id в digest
        # rejected_operations — исходное значение операции, не конверсия гейта)
        refs = sorted(
            (int(op["claim_index"]), str(op["operation"]["existing_claim_id"]))
            for op in dropped_ops
            if op["operation"].get("existing_claim_id") is not None
        )
        dropped_idx = {int(op["claim_index"]) for op in dropped_ops}
        # конверсии гейта value-dup: операция-дубль становится перепроверкой записанного id
        converted = {
            int(entry["claim_index"]): str(entry["target"])
            for entry in decision.get("value_duplicates") or []
            if entry.get("action") in ("reverified", "merged") and entry.get("target")
        }
        declared = [
            *decision.get("relied_claim_ids", []),
            *(decision.get("relied_from_explorer") or []),
        ]
        operation_ids = {
            uuid.UUID(target)
            for index, claim in enumerate(decision["claims"])
            if index not in dropped_idx
            for target in (converted.get(index) or (claim.get("existing_claim_id") or ""),)
            if target and str(target) in pack_ids
        }
        added, rejected = relied_from_dropped_operations(
            refs, [uuid.UUID(cid) for cid in pack_ids], declared, operation_ids, RELIED_CLAIMS_LIMIT
        )
        if added:
            transfers[session] = added
        if rejected:
            refusals[session] = rejected

        # часть 3: карточка — действующие операции (конверсии — под записанным id, новые —
        # `new<i>`), затем вся опора после части 2; пак — секция вне карточки
        card_ids = list(
            dict.fromkeys(
                converted.get(index) or str(claim["existing_claim_id"])
                for index, claim in enumerate(decision["claims"])
                if index not in dropped_idx and (converted.get(index) or claim.get("existing_claim_id"))
            )
        )
        relied_all = list(dict.fromkeys([*declared, *added]))
        card = [record(cid) for cid in card_ids if cid in claims]
        card.extend(record(cid) for cid in relied_all if cid in claims)
        for index, claim in enumerate(decision["claims"]):
            if index not in dropped_idx and not (converted.get(index) or claim.get("existing_claim_id")):
                card.append(SupplementRecord(f"new{index}", str(claim["statement"]), _as_of(claim.get("as_of"))))
        on_card = {item.claim_id for item in card}
        pack_records = [record(cid) for cid in pack_ids if cid not in on_card and cid in claims]
        fresh, fresh_rejected = fresher_forecast_supplement(card, pack_records, len(relied_all), RELIED_CLAIMS_LIMIT)
        if fresh:
            additions[session] = fresh
        if fresh_rejected:
            refusals.setdefault(session, []).extend(fresh_rejected)
    return transfers, refusals, additions


# ── ожидание менеджера (ЧАСТЬ 1 брифа) + потолок правила (в) ──────────────────

EXPECTED_TRANSFERS = {
    "09b513d2": ["07126d06-8bf2-4f8f-bdc4-71944ef1d905", "ab8234ae-3fb9-4565-a696-1ddcedda4318",
                 "471bd83d-1ea3-449c-bfd1-b4e9e413f95e"],
    "bb8a9dd5": ["3f2d9fb5-52c0-4588-944d-491c747a135c"],
    "008d93ac": ["07126d06-8bf2-4f8f-bdc4-71944ef1d905", "c263e93c-2e02-4a37-80bd-6d5ce091362e",
                 "136a0ca0-f27b-4e5c-834e-064d62d558b6"],
}
# 26b0b309: оба кандидата (780ea456…, 21eaef05…) — ЗАКРЫТЫЙ отказ потолка: опора куратора
# уже ровно RELIED_CLAIMS_LIMIT id (в payload той же сессии — отказа rely[8..10] о том же).
EXPECTED_REFUSALS = {
    "26b0b309": [
        "dropped_operation[0]: more than 8 relied ids",
        "dropped_operation[2]: more than 8 relied ids",
    ]
}
EXPECTED_ADDITIONS = {
    "008d93ac": [{"claim_id": "bcf49d07-7f7d-477f-81ff-937ce2150879", "series": "cbr:1",
                  "replaces": ["136a0ca0-f27b-4e5c-834e-064d62d558b6"]}],
    "c7e825e6": [{"claim_id": "759d6363-8120-4193-8c1e-21aa0619528a", "series": "sbercib:4",
                  "replaces": ["new0"]}],
}


def _by_session(result: dict[str, Any]) -> dict[str, Any]:
    return {sid[:8]: value for sid, value in result.items()}


def test_replay_all_fixture_decisions_matches_expected_numbers() -> None:
    transfers, refusals, additions = _replay()
    assert _by_session(transfers) == EXPECTED_TRANSFERS
    assert _by_session(refusals) == EXPECTED_REFUSALS
    assert _by_session(additions) == EXPECTED_ADDITIONS
    assert sum(len(v) for v in transfers.values()) == 7  # 9 кандидатов минус 2 отказа потолка
    assert sum(len(v) for v in additions.values()) == 2


def test_replay_c3_stand_silence_cases_stay_quiet() -> None:
    """Тишина подтверждена реплеем: ни одной паковой добавки в 53be7fd6/1e4ef28d
    (недатированная карточная запись серии) и 70b19b00 (актуальная на карточке)."""
    _transfers, _refusals, additions = _replay()
    quiet = {sid[:8] for sid in additions}
    assert quiet == set(EXPECTED_ADDITIONS)
