"""T7.105 (ADR-0034 вариант B): пооперационный отказ на границе куратора.

Прежняя политика: любая отклонённая проверка отменяла ВСЁ предложение куратора
(fail-closed `return 0, 0`) — законные операции гибли вместе с бракованной. Решение
пользователя 2026-10-12 («Обе части», ADR-0034 §7) зафиксировало вариант B:

* проверки, называющие ОДНУ операцию claim (предпроверка правил `validate_claim_proposal`,
  `temporal_fact` без `as_of`, неразрешимая ссылка перепроверки и якорь без головы,
  `search_statements`), снимают только эту операцию с именованной причиной;
* проверки свойства предложения целиком (индексы уликовых связей вне списка, бюджеты
  новых вопросов, дубли dependencies) по-прежнему отклоняют предложение полностью;
* если сняты ВСЕ операции — поведение как раньше (отказ предложения целиком, прежний
  event, прежние ключи, дайджест отклонённого предложения);
* уликовые связи снятой операции снимаются вместе с ней (названы в записи отказа),
  наблюдения и источники остаются в базе; связи оставшихся операций НЕ переносятся
  на другие claim; `dependencies` снятой операции снимаются с той же причиной;
* запись отказа — аддитивный ключ `rejected_operations` в payload уже существующего
  события `claim_created` (паттерн `value_duplicates`), счётчики разделены и названы
  (`claims_proposed` / `claims_applied`); нового AuditEventType и миграции нет;
* витрина честно называет снятые операции замечанием карточки
  («часть предложенного отклонена правилами: N»).

Закрепленный случай стенда .92 (e6c8959b, config-v22): куратор принёс два
`temporal_fact` с данными Росстата (записаны) и `empirical_conjecture` — независимую
оценку сервиса вакансий, подтверждённую чужим утверждением (rules engine её отвергает:
для `empirical_conjecture` допустимо только `experiment_run`). Прежний исход: 0
утверждений, вопрос `partially_answered`, карточка «Ответ не записан». Новый исход:
два официальных утверждения записаны, оценка отклонена НАЗВАННОЙ операцией.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_commit_boundary import (
    _complete,
    _curator,
    _make_orchestrator,
    _seed_question,
    _tool,
)
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_reverify_temporal_scenario import (
    _COMPLETE,
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _scalar
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = pytest.mark.scenario


async def _fixation_count(scratch_url: str, session_id: Any) -> int:
    """Сколько событий возврата предложения на исправление (T7.106, ADR-0034 вариант C)."""
    rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM audit_events WHERE session_id = :s "
        "AND payload ? 'curator_correction_requested'",
        {"s": str(session_id)},
    )
    return int(dict(rows[0])["n"])


def _correction_requests(fake_llm: FakeLLM) -> int:
    """Сколько раз модель получала раздел «# Исправление предложения»."""
    return sum(1 for r in fake_llm.requests() if "# Исправление предложения" in str(r.get("last_user", "")))

# ── фикстура стендового случая e6c8959b (fixtures-t7105, SELECT с витрины .92) ──

STAND_QUESTION = (
    "Какой уровень безработицы в России по последним данным Росстата и какие независимые "
    "оценки безработицы или напряжённости на рынке труда дают другие источники (аналитики "
    "банков, hh.ru)? Отдели официальные цифры от независимых оценок."
)
CLAIM_MAY = (
    "Официальный уровень безработицы в России в мае 2026 года составил 2,1%, что является "
    "историческим минимумом за всю историю наблюдений с 1991 года (по данным Росстата)."
)
CLAIM_AUG = (
    "Официальный уровень безработицы в России в августе 2026 года составил 2,2% "
    "(по данным Росстата)."
)
#: та самая оценка третьей стороны: прочитана на странице сервиса рейтингов;
#: подтверждена чужим утверждением — для `empirical_conjecture` недопустимо
CLAIM_ESTIMATE = (
    "Независимая оценка рынка труда (hr-ratings.com на основе данных hh.ru): в начале 2026 "
    "года количество активных резюме выросло на 39–42%, а количество вакансий упало на 27–30% "
    "в годовом выражении, что свидетельствует о рекордно высокой конкуренции среди соискателей "
    "при низкой официальной безработице."
)
NEW_QUESTION_TEXT = (
    "Что объясняет расхождение официальной безработицы 2,2% и роста конкуренции соискателей "
    "по данным сервиса вакансий?"
)

_PAGES: dict[str, str] = {
    "http://news-one.example/labor-may": (
        "Уровень безработицы в России в мае 2026 года снизился до 2,1 процента."
    ),
    "http://labor-stats.example/may-2026": (
        "Официальная оценка безработицы в мае 2026 года — 2,1 процента, исторический минимум."
    ),
    "http://news-two.example/labor-july": (
        "В июле 2026 года уровень безработицы составил 2,3 процента."
    ),
    "http://labor-stats.example/aug-2026": (
        "Официальная оценка безработицы в августе 2026 года — 2,2 процента."
    ),
    "http://job-ratings.example/analytics": (
        "Рынок труда 2026: количество активных резюме выросло на 39–42 процента, количество "
        "вакансий упало на 27–30 процента в годовом выражении; индекс конкуренции соискателей — "
        "рекордно высокий."
    ),
}
_FETCH_URLS = list(_PAGES)


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменный HTTP-слой research proxy (пять страниц на разных доменах)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = copy.deepcopy(_curated_payload())
    payload["research_proxy"]["rate_limit_max"] = 10
    return payload


def _stand_curator_answer() -> dict[str, Any]:
    """Предложение стендовой сессии e6c8959b тахом в тах (форма из фикстуры audit)."""
    return {
        "content": {
            "summary": (
                "Зафиксированы официальные данные о безработице: в мае 2026 года 2,1% "
                "(исторический минимум), в августе 2026 года — 2,2%. Также зафиксирована "
                "независимая оценка от HR-аналитики."
            ),
            "claims": [
                {
                    "statement": CLAIM_MAY,
                    "claim_type": "temporal_fact",
                    "as_of": "2026-07-01T00:00:00+00:00",
                    "scope": {"region": "Россия", "metric": "безработица"},
                },
                {
                    "statement": CLAIM_AUG,
                    "claim_type": "temporal_fact",
                    "as_of": "2026-09-30T00:00:00+00:00",
                    "scope": {"region": "Россия", "metric": "безработица"},
                },
                {
                    "statement": CLAIM_ESTIMATE,
                    "claim_type": "empirical_conjecture",
                    "scope": {"region": "Россия", "metric": "напряжённость рынка труда"},
                },
            ],
            "evidence_links": [
                {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
                {"evidence_index": 3, "claim_index": 1, "relation": "supports"},
                {"evidence_index": 4, "claim_index": 2, "relation": "supports"},
            ],
            "new_questions": [{"text": NEW_QUESTION_TEXT, "origin": "model_proposal"}],
        }
    }


# ── 1. воспроизведение случай стенда: две записи, оценка отклонена названно ──


@pytest.mark.asyncio
async def test_stand_case_replay_two_claims_written_estimate_bounced_by_name(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Случай e6c8959b на границе куратора (ADR-0034 §1): было — «Ответ не записан»
    и 0 утверждений; стало — два официальных утверждения записаны, а оценка третьей
    стороны отклонена КАК ОТДЕЛЬНАЯ ОПЕРАЦИЯ с прежней именованной причиной."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    question_id = await _seed_question(scratch_url, STAND_QUESTION)
    fake_llm.script(
        [
            *[_fetch(url) for url in _FETCH_URLS],
            _COMPLETE,
            _stand_curator_answer(),
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "art", question_id)
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 3  # предложено — всё предложение целиком

    # отказа предложения целиком нет: прежних отказных событий не написано
    assert await _rejection_events(scratch_url, session_id) == []

    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(session_id)},
    )
    assert len(rows) == 1
    payload = dict(rows[0]["payload"])
    assert payload["claims_proposed"] == 3
    assert payload["claims_applied"] == 2
    # прежнее предложение остаётся видимым в журнале целиком (снятое не вырезано)
    assert [c["statement"] for c in payload["claims"]] == [CLAIM_MAY, CLAIM_AUG, CLAIM_ESTIMATE]
    # частичный публичный summary называет факт отказа честно (ADR-0034 §8 п.6)
    summary_rows = await _all(
        scratch_url,
        "SELECT public_summary FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(session_id)},
    )
    assert summary_rows[0]["public_summary"] == (
        "curator: 2 of 3 claims applied, 1 questions, 1 operations rejected by host validation"
    )

    rejected = payload["rejected_operations"]
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry["claim_index"] == 2
    # прежняя причина движка правил — та же строка, что стояла в отказе всего предложения
    assert "claim 2 (empirical_conjecture):" in entry["reason"]
    assert "source_assertion" in entry["reason"]
    assert "not allowed for empirical_conjecture" in entry["reason"]
    assert entry["operation"]["claim_type"] == "empirical_conjecture"
    assert "hr-ratings" in entry["operation"]["statement"]
    # связь снятой операции снята вместе с ней и названа
    assert entry["evidence_links_rejected"] == [
        {"claim_index": 2, "evidence_index": 4, "relation": "supports"}
    ]

    # знание: ровно два официальных утверждения; оценки в базе нет
    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert sorted(row["statement"] for row in claims) == sorted([CLAIM_MAY, CLAIM_AUG])
    count = await _scalar(scratch_url, "SELECT count(*) FROM claims WHERE statement = :s", {"s": CLAIM_ESTIMATE})
    assert count is not None and count[0] == 0

    # улики: два утверждения подтверждены; наблюдения страниц (действия и артефакты)
    # остаются в базе, но ни одна связь снятой операции не прикреплена к чужому claim
    evidence = await _scalar(scratch_url, "SELECT count(*) FROM evidence")
    assert evidence is not None and evidence[0] == 2
    linked = await _scalar(
        scratch_url, "SELECT count(*) FROM evidence e JOIN claims c ON c.id = e.claim_id"
    )
    assert linked is not None and linked[0] == 2
    fetch_actions = await _scalar(
        scratch_url,
        "SELECT count(*) FROM actions WHERE session_id = :s AND tool = 'research.fetch'",
        {"s": str(session_id)},
    )
    assert fetch_actions is not None and fetch_actions[0] == len(_FETCH_URLS)

    # новый вопрос применён (new_questions — не операция claim, снятием не затронут)
    questions = await _all(
        scratch_url, "SELECT text, origin FROM questions WHERE id <> :q", {"q": str(question_id)}
    )
    assert len(questions) == 1
    assert questions[0]["text"] == NEW_QUESTION_TEXT

    # витрина: вопрос отвечает, карточка честно называет отклонённое
    state_rows = await _all(scratch_url, "SELECT state FROM questions WHERE id = :q", {"q": str(question_id)})
    assert state_rows[0]["state"] == "verified"
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{question_id}/answer")
            assert response.status_code == 200
            card = dict(response.json())
    finally:
        await app_engine.dispose()
    assert sorted(item["statement"] for item in card["claims"]) == sorted([CLAIM_MAY, CLAIM_AUG])
    assert card["result"]["kind"] == "answered"
    assert any("отклонена правилами: 1" in note for note in card["honesty"]), card["honesty"]

    # T7.106 (ADR-0034 вариант C): частичный отказ варианта B раунд исправления НЕ запускает
    assert await _fixation_count(scratch_url, session_id) == 0
    assert _correction_requests(fake_llm) == 0
    assert fake_llm.state()["queue_left"] == 0
    assert payload.get("correction_round") is None


# ── 2. search_statements: проверка одной операции снимает только её ──────────


@pytest.mark.asyncio
async def test_bad_search_statement_bounces_only_that_claim(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7 и сколько будет 6+6?")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _tool("python.execute", {"code": "print(6+6)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                        "search_statements": ["6 times 7 equals 42", ""],
                    },
                    {
                        "statement": "6+6 равно 12",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6+6"},
                    },
                ],
                [
                    {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    {"evidence_index": 1, "claim_index": 1, "relation": "supports"},
                ],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 2
    assert await _rejection_events(scratch_url, outcome.session_id) == []

    rows = await _all(scratch_url, "SELECT statement FROM claims")
    assert [row["statement"] for row in rows] == ["6+6 равно 12"]

    events = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(outcome.session_id)},
    )
    payload = dict(events[0]["payload"])
    assert payload["claims_proposed"] == 2
    assert payload["claims_applied"] == 1
    rejected = payload["rejected_operations"]
    assert len(rejected) == 1
    assert rejected[0]["claim_index"] == 0
    assert rejected[0]["reason"] == "claim[0].search_statements[1]: empty"
    # уликовая связь снятой операции названа снятой, а не перенесённой на выживший claim
    assert rejected[0]["evidence_links_rejected"] == [
        {"claim_index": 0, "evidence_index": 0, "relation": "supports"}
    ]
    staging = await _all(
        scratch_url,
        "SELECT op, payload FROM session_staging WHERE session_id = :s ORDER BY op",
        {"s": str(outcome.session_id)},
    )
    evidence_ops = [row for row in staging if row["op"] == "evidence"]
    assert len(evidence_ops) == 1
    # связь выжившей операции перенумерована по позиции применённого claim (0),
    # чужих связей не появилось
    assert evidence_ops[0]["payload"] == {
        "evidence_index": 1,
        "claim_index": 0,
        "relation": "supports",
    }
    # применённое утверждение получило РОВНО свою улику (print(6+6))
    linked = await _all(
        scratch_url,
        "SELECT c.statement AS s, e.evidence_kind AS k FROM evidence e JOIN claims c ON c.id = e.claim_id",
    )
    assert len(linked) == 1
    assert linked[0]["s"] == "6+6 равно 12"
    assert linked[0]["k"] == "computation"

    # T7.106: частичный отказ варианта B — без раунда исправления
    assert await _fixation_count(scratch_url, outcome.session_id) == 0
    assert _correction_requests(fake_llm) == 0
    assert fake_llm.state()["queue_left"] == 0
    assert payload.get("correction_round") is None


# ── 3. свойства предложения целиком по-прежнему отказывают предложение полностью ──


@pytest.mark.asyncio
async def test_out_of_range_link_still_rejects_whole_proposal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Связь с индексом вне списка улик — свойство предложения целиком: даже при одном
    безупречном claim предложение отклоняется полностью (прежний отказ, прежний event).
    T7.106 (ADR-0034 вариант C): поверх — ровно ОДИН раунд исправления; идентичный скриптованный
    ответ отклоняется вторично, финальный отказ один и несёт `correction_round`."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7?")
    bad_answer = _curator(
        [
            {
                "statement": "6*7 равно 42",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
            }
        ],
        [
            {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
            {"evidence_index": 99, "claim_index": 0, "relation": "supports"},
        ],
    )
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            bad_answer,
            bad_answer,  # ОДНО исправление — та же недоступная связь, отказ вторично
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.claims_proposed == 0
    events = await _rejection_events(scratch_url, outcome.session_id)
    assert len(events) == 1
    assert events[0]["curator_rejected"] == ["evidence_link[1]: index 99 out of range"]
    assert "claims" in events[0]["rejected_proposal"]
    staging = await _scalar(
        scratch_url, "SELECT count(*) FROM session_staging WHERE session_id = :s", {"s": str(outcome.session_id)}
    )
    assert staging is not None and staging[0] == 0
    claims = await _scalar(scratch_url, "SELECT count(*) FROM claims")
    assert claims is not None and claims[0] == 0

    # T7.106: раунд ровно один, дайджест ПЕРВОГО ответа — в аддитивном ключе отказа
    assert events[0]["correction_round"]["reasons"] == ["evidence_link[1]: index 99 out of range"]
    assert await _fixation_count(scratch_url, outcome.session_id) == 1
    assert _correction_requests(fake_llm) == 1
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.asyncio
async def test_questions_budget_overflow_still_rejects_whole_proposal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Бюджет новых вопросов — свойство предложения целиком (ADR-0032 §4): превышение
    отклоняет всё предложение, несмотря на законную claim-операцию."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7?")
    answer: dict[str, Any] = {
        "content": {
            "summary": "Ответ и пять новых вопросов.",
            "claims": [
                {
                    "statement": "6*7 равно 42",
                    "claim_type": "computed_result",
                    "scope": {"expr": "6*7"},
                }
            ],
            "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            "new_questions": [
                {"text": f"Вопрос {index}?", "origin": "model_proposal"} for index in range(5)
            ],
        }
    }
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            answer,
            # T7.106: ОДИН раунд исправления — те же пять вопросов, отказ вторично
            answer,
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.claims_proposed == 0
    events = await _rejection_events(scratch_url, outcome.session_id)
    assert len(events) == 1
    assert events[0]["curator_rejected"] == ["new_questions: 5 > 4"]
    staging = await _scalar(
        scratch_url, "SELECT count(*) FROM session_staging WHERE session_id = :s", {"s": str(outcome.session_id)}
    )
    assert staging is not None and staging[0] == 0

    # T7.106: раунд ровно один; бюджет вопросов — не то, что модель «исправит» молча
    assert events[0]["correction_round"]["reasons"] == ["new_questions: 5 > 4"]
    assert await _fixation_count(scratch_url, outcome.session_id) == 1
    assert _correction_requests(fake_llm) == 1
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.asyncio
async def test_mixed_search_statement_and_out_of_range_link_rejects_whole_proposal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Если в предложении есть и пооперационный брак, и свойство целиком — отказывает
    всё предложение прежним событием со ВСЕМИ строками причин (порядок `_walk` сохранён)."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7?")
    bad_answer = _curator(
        [
            {
                "statement": "6*7 равно 42",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
                "search_statements": [""],
            }
        ],
        [{"evidence_index": 9, "claim_index": 0, "relation": "supports"}],
    )
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            bad_answer,
            # T7.106: ОДИН раунд исправления — те же две причины, отказ вторично
            bad_answer,
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.claims_proposed == 0
    events = await _rejection_events(scratch_url, outcome.session_id)
    assert len(events) == 1
    assert events[0]["curator_rejected"] == [
        "evidence_link[0]: index 9 out of range",
        "claim[0].search_statements[0]: empty",
    ]
    # T7.106: обе строки причин — и в раундовом событии (модель получила их дословно)
    assert events[0]["correction_round"]["reasons"] == [
        "evidence_link[0]: index 9 out of range",
        "claim[0].search_statements[0]: empty",
    ]
    assert await _fixation_count(scratch_url, outcome.session_id) == 1
    assert _correction_requests(fake_llm) == 1
    assert fake_llm.state()["queue_left"] == 0
    staging = await _scalar(
        scratch_url, "SELECT count(*) FROM session_staging WHERE session_id = :s", {"s": str(outcome.session_id)}
    )
    assert staging is not None and staging[0] == 0


# ── 4. снятая операция не оставляет висячих зависимостей ────────────────────


@pytest.mark.asyncio
async def test_dropped_claim_dependencies_dropped_with_it(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """`dependencies` снятой операции снимаются с той же причиной: ребро «новый claim →
    якорь» не возникает вовсе, висячих записей в графе нет."""
    scratch_url, _engine = migrated_db
    # якорь: computed_result-утверждение прежней сессии
    anchor_qid = await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            _curator(
                [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        await orch.run_session(anchor_qid)
    finally:
        await gateway.close()
        await engine.dispose()
    anchor_rows = await _all(scratch_url, "SELECT id::text FROM claims")
    assert len(anchor_rows) == 1
    anchor_id = anchor_rows[0]["id"]

    # новая сессия: claim без даты (снятие по as_of) с зависимостью от якоря + законный claim
    question_id = await _seed_question(scratch_url, "Сколько будет 6+6? И что про 6*7?")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6+6)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "Годовая инфляция в мае 2031 года равна 4,5%.",
                        "claim_type": "temporal_fact",
                        "scope": {"metric": "инфляция"},
                        "dependencies": [{"claim_id": anchor_id, "kind": "evidential"}],
                    },
                    {
                        "statement": "6+6 равно 12",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6+6"},
                    },
                ],
                [
                    {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    {"evidence_index": 0, "claim_index": 1, "relation": "supports"},
                ],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws2")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.claims_proposed == 2
    events = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(outcome.session_id)},
    )
    payload = dict(events[0]["payload"])
    rejected = payload["rejected_operations"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "claim[0]: temporal_fact requires as_of"
    assert rejected[0]["dependencies_rejected"] == [{"claim_id": anchor_id, "kind": "evidential"}]

    # граф зависимостей не тронут снятой операцией: ни ребра от несуществовавшего claim,
    # ни рёбер законной операции (у неё зависимостей не было)
    deps = await _scalar(scratch_url, "SELECT count(*) FROM claim_dependencies")
    assert deps is not None and deps[0] == 0
    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert sorted(row["statement"] for row in claims) == sorted(["6*7 равно 42", "6+6 равно 12"])

    # T7.106: и здесь частичный отказ — без раунда исправления
    assert await _fixation_count(scratch_url, outcome.session_id) == 0
    assert _correction_requests(fake_llm) == 0
    assert fake_llm.state()["queue_left"] == 0
