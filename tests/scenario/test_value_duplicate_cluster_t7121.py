"""Scenario (fake LLM, curated research): mutual-кластер дублей без опоры (T7.121 часть 4,
ADR-0038 §4) и guard названных месяцев на уровне живой сессии.

Стендовая форма с3 (STATUS T7.121): контекст-пак содержит ДВЕ уже записанные формулировки
одного факта (тот же показатель, то же значение, то же годовое покрытие), куратор приносит
третью формулировку того же числа и НЕ объявляет ни одну из записанных опорой ответа.
Прежний гейт T7.83a говорил «ambiguous ... left as proposed» — коммит писал ТРЕТЬЮ запись
того же значения (на витрине — три бейджа у одного числа). Теперь хост проверяет, что
кандидаты являются дублями друг друга (предикат витрины T7.99 в хронологическом
направлении + guard месяцев), и перепроверяет операцию в ранейшую запись: третья строка
не появляется, перепроверка сохраняет якорь (дату, scope) и union-улики monotone.

Guard месяцев (тот же стенд, консервативная граница): предложение про СЕНТЯБРЬ 2030 против
записанных ИЮЛЯ и АВГУСТА того же числа — наблюдения других периодов; оба кандидата уходят
из строгой выборки, операция остаётся как предложено, а причина в payload честно называет
месяцы обеих сторон.

Все тексты и числа вымышленные (Тармения, 2029–2030); страницы — через подменный HTTP-слой
(tests.scenario.test_research_evidence.FakeFetchClient), внешних запросов нет.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_reverify_temporal_scenario import (
    _COMPLETE,
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question

pytestmark = pytest.mark.scenario

# ── вымышленная фикстура с3: две Mutual-записи одного факта ───────────────────────
#
# Фикстура перестроена под дополнение T7.126 (часть б). Две ОТДЕЛЬНЫЕ сид-сессии больше не
# дают_pair взаимных дублей: вторая сессия принесла бы операцию, чьи значения и период
# покрываются первой записью, и гейт склеил бы её в перепроверку — ровно то новое поведение,
# ради которого задача и делалась. Поэтому пара записывается ОДНОЙ сессией (две операции одного
# предложения: гейт сравнивает операцию только с уже записанными кандидатами, а не операции между
# собой) — это честная модель «строк, записанных до правила» (стендовая форма с3).
# Обе формулировки называют ОДИНАКОВЫЙ годовой период и одно значение, поэтому mutual-покрытие
# выполняется в обе стороны и якорь не зависит от того, какая из строк оказалась ранейшей:
# при равных created_at одной phase-1 транзакции (ловушка AGENTS §7) он детерминирован по
# claim_id — тест вычисляет его так же, а не угадывает.

CLUSTER_OLD = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (обзорная группа и ведомство)."
)
CLUSTER_NEW = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (обзорная группа)."
)
DUP_OP = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (итог сессии по данным обзорной группы)."
)
SEED_AS_OF = "2029-11-30T00:00:00"  # обе сид-записи: якорь хранит эту дату
OP_AS_OF = "2029-12-31T00:00:00"  # предложение позже якоря → as_of_conflict

_MONTH_PAGES: dict[str, str] = {
    "http://alpha.example/old": (
        "Tarmania official note: annual consumer price growth for 2029 "
        "(December to December) was 4.7 percent. "
        "Период наблюдения: декабрь 2029 к декабрю 2028. Отчёт опубликован 21 января 2030 года."
    ),
    "http://beta.example/new": (
        "Review of the same Tarmanian group: annual inflation for 2029 "
        "(December to December) printed as 4.7 percent. "
        "Период наблюдения: декабрь 2029 к декабрю 2028."
    ),
    "http://eta.example/cluster-verify": (
        "Combined note of the Tarmanian review group: annual inflation for 2029 "
        "(December to December) confirmed at 4.7 percent. "
        "Период наблюдения: декабрь 2029 к декабрю 2028. Отчёт опубликован 21 января 2030 года."
    ),
    # месяц-conflict часть: июль и август 2030 записаны, предложение — про сентябрь
    "http://gamma.example/jul": (
        "Tarmania monthly note, July 2030: inflation rate 4.7 percent. "
        "Наблюдение за июлем 2030 года."
    ),
    "http://delta.example/aug": (
        "Tarmania monthly note, August 2030: inflation rate 4.7 percent. "
        "Наблюдение за августом 2030 года."
    ),
    "http://zeta.example/sep": (
        "Tarmania monthly note, September 2030: inflation rate 4.7 percent. "
        "Наблюдение за сентябрём 2030 года."
    ),
}


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же подменный HTTP-слой (тот же registrable domain на страницу)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _MONTH_PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 10  # 1 fetch на сессию × (2 сида + 1 тест)
    return payload


def _seed_claim_curator(statement: str, as_of: str | None) -> dict[str, Any]:
    return {
        "summary": "Запись вымышленной статистики Тармении.",
        "claims": [
            {
                "statement": statement,
                "claim_type": "temporal_fact",
                "as_of": as_of,
                "scope": {},
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }


async def _seed_one(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    *,
    tag: str,
    question: str,
    url: str,
    statement: str,
    as_of: str | None,
) -> uuid.UUID:
    """Одна записанная формулировка = отдельная сессия (и отдельная phase-1 транзакция):
    только так `created_at` строк получается разным — в одной транзакции оно совпадает
    (ловушка AGENTS §7), и якорь кластера пришлось бы выбирать по claim_id."""
    qid = await _seed_question(scratch_url, question)
    fake_llm.script(
        [
            _fetch(url),
            _COMPLETE,
            {"content": _seed_claim_curator(statement, as_of)},
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / f"ws-{tag}", tmp_path / f"art-{tag}", qid
    )
    assert outcome.final_state.value == "succeeded"
    rows = await _all(
        scratch_url, "SELECT id::text FROM claims WHERE statement = :t", {"t": statement}
    )
    assert len(rows) == 1
    return uuid.UUID(rows[0]["id"])


async def _seed_pair(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    *,
    question: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Две формулировки одного факта записаны ОДНОЙ сессией (две операции одного предложения).
    Гейт дублей сравнивает операцию только с уже записанными кандидатами контекст-пака, поэтому
    такая пара законна и ровно так выглядели стендовые строки, записанные до правила T7.126:
    отдельная вторая сессия сегодня написала бы не новую строку, а перепроверку первой.
    Обе строки рождены в одной phase-1 транзакции → `created_at` равны (ловушка AGENTS §7),
    якорь кластера детерминирован по claim_id."""
    qid = await _seed_question(scratch_url, question)
    fake_llm.script(
        [
            _fetch("http://alpha.example/old"),
            _fetch("http://beta.example/new"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Запись вымышленной статистики Тармении: две формулировки.",
                    "claims": [
                        {
                            "statement": CLUSTER_OLD,
                            "claim_type": "temporal_fact",
                            "as_of": SEED_AS_OF,
                            "scope": {},
                        },
                        {
                            "statement": CLUSTER_NEW,
                            "claim_type": "temporal_fact",
                            "as_of": SEED_AS_OF,
                            "scope": {},
                        },
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 1, "claim_index": 1, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws-pair", tmp_path / "art-pair", qid
    )
    assert outcome.final_state.value == "succeeded"
    rows = await _all(
        scratch_url,
        "SELECT id::text, statement FROM claims WHERE statement IN (:old, :new)",
        {"old": CLUSTER_OLD, "new": CLUSTER_NEW},
    )
    assert len(rows) == 2, rows
    return (
        uuid.UUID(rows[0]["id"]) if rows[0]["statement"] == CLUSTER_OLD else uuid.UUID(rows[1]["id"]),
        uuid.UUID(rows[1]["id"]) if rows[0]["statement"] == CLUSTER_OLD else uuid.UUID(rows[0]["id"]),
    )


# ── т3: две Mutual-записи + третье предложение без опоры → перепроверка в якорь ──


@pytest.mark.asyncio
async def test_mutual_duplicate_pair_absorbs_third_proposal_into_the_anchor_record(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Было (6372138): «ambiguous ... left as proposed» → третья строка того же числа
    рядом с двумя уже записанными. Стало: кандидаты — дубли друг друга, хост перепроверяет
    операцию в якорную запись (ранейшую по `created_at`, при равенстве дат — по claim_id,
    ловушка AGENTS §7); строк в claims по-прежнему две. Якорь тест вычисляет тем же правилом,
    каким его выбирает гейт, а не предполагает заранее."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    old_id, new_id = await _seed_pair(
        scratch_url,
        fake_llm,
        tmp_path,
        question=(
            "Годовая инфляция в Тармении по итогам 2029 года (обзорная группа и ведомственный "
            "обзор): приведи цифру и первоисточник. Источники: http://alpha.example/old, "
            "http://beta.example/new"
        ),
    )
    # ожидание якоря — то же правило, что у `_cluster_anchor`: минимум по (created_at, claim_id)
    seed_rows = await _all(
        scratch_url,
        "SELECT id::text, created_at::text AS created FROM claims "
        "WHERE statement IN (:old, :new) ORDER BY id::text",
        {"old": CLUSTER_OLD, "new": CLUSTER_NEW},
    )
    assert len(seed_rows) == 2, seed_rows
    anchor_id = min(seed_rows, key=lambda row: (row["created"], row["id"]))["id"]
    other_id = new_id if anchor_id == str(old_id) else old_id

    qid = await _seed_question(
        scratch_url,
        "Сравни уже записанные оценки годовой инфляции в Тармении по итогам 2029 года "
        "и приведи итоговую цифру. Источники: http://eta.example/cluster-verify",
    )
    fake_llm.script(
        [
            _fetch("http://eta.example/cluster-verify"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Итоговая оценка годовой инфляции Тармении за 2029 год.",
                    "claims": [
                        {
                            "statement": DUP_OP,
                            "claim_type": "temporal_fact",
                            "as_of": "2029-12-31T00:00:00",
                            "scope": {},
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"}
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-op", tmp_path / "art-op", qid)
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    # (а) третьей записи нет: в claims ровно две прежние строки
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 2, rows
    assert {row["statement"] for row in rows} == {CLUSTER_OLD, CLUSTER_NEW}

    # (б) staging: операция проведена перепроверкой ЯКОРЯ — той самой записи, которую
    # выбрал `_cluster_anchor` (минимум по (created_at, claim_id))
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [anchor_id]

    # (в) перепроверка сохранила якорь: дата якорной записи не сдвинута, предложенная refused
    rev = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert len(rev) == 1
    rv_payload = dict(rev[0]["payload"])
    assert rv_payload["resolved"] == anchor_id
    assert rv_payload["anchor_kept"] is True
    assert str(rv_payload["stored_as_of"]).startswith("2029-11-30")
    assert "as_of_conflict" in rv_payload

    # (г) union-улика добавлена к якорю: 1 прежняя + свежий пересказ; вторая запись не тронута
    ev_anchor = await _scalar(
        scratch_url, "SELECT count(*) FROM evidence WHERE claim_id = :c", {"c": anchor_id}
    )
    assert ev_anchor is not None and ev_anchor[0] == 2
    ev_other = await _scalar(
        scratch_url, "SELECT count(*) FROM evidence WHERE claim_id = :c", {"c": str(other_id)}
    )
    assert ev_other is not None and ev_other[0] == 1

    # (д) честная причина в payload: mutual-дубли, отсутствие опоры названо, третьей копии нет
    payload_rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array' ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(payload_rows) == 1
    payload = dict(payload_rows[0]["payload"])
    decisions = payload.get("value_duplicates")
    assert decisions is not None and len(decisions) == 1
    entry = decisions[0]
    assert entry["claim_index"] == 0
    assert entry["target"] == anchor_id
    assert entry["action"] == "reverified"
    assert "mutual value duplicates" in entry["reason"]
    assert "relied_claim_ids" in entry["reason"]
    assert "a third copy is not written" in entry["reason"]


# ── т3b: guard месяцев — сентябрьское наблюдение не притягивается к июлю/августу ──


@pytest.mark.asyncio
async def test_month_conflicting_candidates_do_not_absorb_a_different_month_op(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Записаны июль 2030 и август 2030 одного показателя (оба 4,7%); предложение — про
    сентябрь 2030. Годы покрыты, но названные месяцы не пересекаются: кандидаты уходят из
    строгой выборки, операция остаётся отдельной записью, а payload называет месяцы."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    jul_id = await _seed_one(
        scratch_url,
        fake_llm,
        tmp_path,
        tag="jul",
        question="Инфляция в Тармении в июле 2030 года: приведи цифру. Источники: http://gamma.example/jul",
        url="http://gamma.example/jul",
        statement="Инфляция в Тармении в июле 2030 года составила 4,7% (месячный обзор).",
        as_of="2030-07-31T00:00:00",
    )
    aug_id = await _seed_one(
        scratch_url,
        fake_llm,
        tmp_path,
        tag="aug",
        question="Инфляция в Тармении в августе 2030 года: приведи цифру. Источники: http://delta.example/aug",
        url="http://delta.example/aug",
        statement="Инфляция в Тармении в августе 2030 года составила 4,7% (месячный обзор).",
        as_of="2030-08-31T00:00:00",
    )

    qid = await _seed_question(
        scratch_url,
        "Инфляция в Тармении в сентябре 2030 года: приведи цифру и первоисточник. "
        "Источники: http://zeta.example/sep",
    )
    fake_llm.script(
        [
            _fetch("http://zeta.example/sep"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Сентябрьская оценка инфляции Тармении.",
                    "claims": [
                        {
                            "statement": "Инфляция в Тармении в сентябре 2030 года составила 4,7% (итог сессии).",
                            "claim_type": "temporal_fact",
                            "as_of": "2030-09-30T00:00:00",
                            "scope": {},
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"}
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-sep", tmp_path / "art-sep", qid)
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    # (а) сентябрь заводится отдельной строкой: июль и август не тронуты
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 3, rows
    assert {str(jul_id), str(aug_id)} <= {row["id"] for row in rows}
    new_rows = [row for row in rows if "сентябре" in row["statement"]]
    assert len(new_rows) == 1

    # (б) перепроверки нет
    rev = await _all(
        scratch_url,
        "SELECT id FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert rev == []

    # (в) причина называет месяцы обеих сторон — «наблюдение другого периода»
    payload_rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array' ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(payload_rows) == 1
    decisions = dict(payload_rows[0]["payload"]).get("value_duplicates")
    assert decisions is not None and len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept"
    assert entry["target"] is None
    assert "is not covered — named months do not overlap" in entry["reason"]
    assert "left as proposed" in entry["reason"]
