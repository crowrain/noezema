"""T7.106 (часть б): один раунд исправления куратора (ADR-0034 вариант C поверх B).

Решение пользователя 2026-10-13: когда ответ куратора был бы ОТКЛОНЁН ЦЕЛИКОМ (любой из
четырёх целых гейтов после модельного ответа), хост возвращает предложение модели ровно на
ОДИН раунд исправления на вызов куратора (= на сессию): второй запрос — тот же системный
промпт, тот же user + раздел `build_correction_section` (причины дословно, отклонённое
предложение JSON'ом, перечень claims с индексами с нуля). Различия зафиксированы тестами:

  * partial-отказ варианта B (снята часть операций) раунд НЕ запускает;
  * отказ ПЕРВОГО вызова (truncate/request_rejected/unavailable) раунд НЕ запускает;
  * второй ответ принят — staging как обычно, в `claim_created` аддитивный ключ
    `correction_round` {reasons, rejected_proposal}; второго отказа нет;
  * второй ответ снова отклонён — прежний payload отказа ВТОРОГО ответа + аддитивный
    `correction_round` (причины и дайджест ПЕРВОГО); третьего вызова нет;
  * ошибка ВТОРОГО вызова — отказ по payload ПЕРВОГО отказа + аддитивный
    `correction_error` {kind, error ≤300 (+truncated_attempts)}; третьего вызова нет;
  * событие раунда — SESSION_STATE_CHANGED с ключом `curator_correction_requested`
    (единственный новый ключ журнала), поэтому прежние фильтры отказа видят ровно один
    финальный отказ;
  * T7.106a (ADR-0034 §9): раздел строится ПОЛНЫМ (целиком JSON + перечень по всем
    claims). Если полный раздел не помещается в `CORRECTION_SECTION_MAX_CHARS`, раунда
    НЕТ: прежний отказ ПЕРВОГО ответа прежними ключами плюс аддитивный
    `correction_skipped` {reason, chars, limit}; ровно один вызов куратора, события
    `curator_correction_requested` нет.

Фикстура репроигрывания — случай 3701d1ce (.92, фикстура fixtures-t7106): куратор v13
считал индексы claims с единицы и связь `evidence_link[3]` указывала на несуществующий
`claim index 4`. С v14 (индексы с нуля) та же ошибка воспроизводится той же причиной;
исправленный второй ответ принимается частично: 2 утверждения записаны, 2 операции сняты
гейтом «значение на связи» (T7.108, ADR-0034 §10) — раунд исправления при этом прежний,
его запускает структурная причина первого отказа, а не гейт.

Ни одного реального LLM; `research.fetch` подменён (страницы на разных registrable
domains); события читаются из audit_events, вызовы — из журнала fake-сервера.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.curator_correction import (
    CORRECTION_SECTION_MAX_CHARS,
    CorrectionOverLimit,
    build_correction_section,
)
from packages.domain.models.base import JsonDict
from packages.domain.schemas.staging import CuratorProposal
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_reverify_temporal_scenario import (
    _COMPLETE,
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _seed_question

pytestmark = pytest.mark.scenario

# ── страницы стендового случая (вымышленные акторы, значения как в фикстуре 3701d1ce) ──

_URLS = [
    "http://tarstat.example/labour-aug",
    "http://tarstat.example/labour-jul",
    "http://veer.example/competition",
    "http://veer.example/review-2026",
    "http://tarmenika.example/jobmarket",
    "http://trudoviki.example/vacancies",
]

_PAGES: dict[str, str] = {
    "http://tarstat.example/labour-aug": (
        "Служба занятости Тармении: уровень безработицы в Тармении в августе 2026 года "
        "составил 2,2 процента."
    ),
    "http://tarstat.example/labour-jul": (
        "Служба занятости Тармении: уровень безработицы в Тармении в июле 2026 года "
        "составил 2,3 процента."
    ),
    "http://veer.example/competition": (
        "Сервис «Веер»: конкуренция среди соискателей в Тармении в апреле 2026 года "
        "составила 10,3 резюме на одну вакансию при норме до 8."
    ),
    "http://veer.example/review-2026": (
        "Аналитический обзор сервиса «Веер»: в 2026 году количество вакансий в Тармении "
        "снизилось на 25 процента год к году, число резюме выросло на 36 процента за 9 месяцев."
    ),
    "http://tarmenika.example/jobmarket": (
        "Газета «Тарменика»: рынок труда Тармении в 2026 году — независимый обзор оценок "
        "службы занятости и сервиса «Веер»."
    ),
    "http://trudoviki.example/vacancies": (
        "Портал «Трудовики»: в Тармении за 9 месяцев 2026 года число резюме выросло на 36 "
        "процентов, вакансии сократились на 25 процентов."
    ),
}

_QUESTION = (
    "Официальные данные о безработице в Тармении в июле и августе 2026 года, конкуренция "
    "соискателей на сервисе «Веер» в апреле 2026 года и динамика вакансий и резюме за 9 "
    "месяцев 2026 года: приведи цифры и их первоисточники. Источники: " + " ".join(_URLS)
)

# та же причина, что в дайджесте стендового случая 3701d1ce: куратор считал индексы с 1
_FIXTURE_REASON = "evidence_link[3]: claim index 4 out of range"

_CLAIMS_FIXTURE: list[dict[str, Any]] = [
    {
        "statement": (
            "Уровень безработицы в Тармении в августе 2026 года составил 2,2% (по данным службы занятости)."
        ),
        "claim_type": "temporal_fact",
        "as_of": "2026-09-30T00:00:00+00:00",
    },
    {
        "statement": (
            "Уровень безработицы в Тармении в июле 2026 года составил 2,3% (по данным службы занятости)."
        ),
        "claim_type": "temporal_fact",
        "as_of": "2026-09-02T00:00:00+00:00",
    },
    {
        "statement": (
            "Сервис «Веер»: конкуренция среди соискателей в Тармении в апреле 2026 года составила "
            "10,3 резюме на одну вакансию (при норме до 8)."
        ),
        "claim_type": "external_fact",
    },
    {
        "statement": (
            "Аналитический обзор сервиса «Веер»: в 2026 году наблюдается снижение количества вакансий "
            "на 25% год к году и рост числа резюме на 36% за 9 месяцев."
        ),
        "claim_type": "external_fact",
    },
]


# ── plumbing ───────────────────────────────────────────────────────────

@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменный HTTP-слой research proxy; страницы на шести разных registrable domains."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 20  # с запасом на два целых сценарных ответа
    return payload


def _links(*pairs: tuple[int, int]) -> list[dict[str, Any]]:
    return [
        {"evidence_index": e, "claim_index": c, "relation": "supports"} for e, c in pairs
    ]


def _q(text: str) -> str:
    return text + " Источники: " + " ".join(_SHORT_URLS)


def _curator_answer(
    claims: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    summary: str = "Итог по источникам.",
) -> dict[str, Any]:
    return {
        "content": {
            "summary": summary,
            "claims": claims,
            "evidence_links": links,
            "new_questions": [],
        }
    }


async def _run(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_text: str,
    script: list[dict[str, Any]],
) -> Any:
    question_id = await _seed_question(scratch_url, question_text)
    fake_llm.script(script)
    return await _run_session(
        scratch_url,
        fake_llm,
        tmp_path / f"ws{question_id.hex[:6]}",
        tmp_path / f"art{question_id.hex[:6]}",
        question_id,
    )


async def _events(scratch_url: str, session_id: uuid.UUID, key: str) -> list[JsonDict]:
    rows = await _all(
        scratch_url,
        "SELECT payload, public_summary FROM audit_events WHERE session_id = :s "
        f"AND type = 'session_state_changed' AND payload ? '{key}' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(row) for row in rows]


async def _rejection_events(scratch_url: str, session_id: uuid.UUID) -> list[JsonDict]:
    """Тот же фильтр, что у прежних тестов отказа (T7.84/T7.105): он обязан видеть ровно
    один финальный отказ — событие раунда (`curator_correction_requested`) в него не входит."""
    rows = await _all(
        scratch_url,
        "SELECT payload, public_summary FROM audit_events WHERE session_id = :s "
        "AND type = 'session_state_changed' AND (payload ? 'curator_rejected' "
        "OR payload ? 'curator_rejected_by_rules') "
        "ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(row) for row in rows]


async def _claim_created_payloads(scratch_url: str, session_id: uuid.UUID) -> list[JsonDict]:
    """Событие кураторской границы (session-level, с массивом `claims`): пер-claim события
    записи пишет MemoryService (у них `claim_id`) — они тут не нужны."""
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND payload ? 'claims' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(row["payload"]) for row in rows]


async def _all_state_payloads(scratch_url: str, session_id: uuid.UUID) -> list[JsonDict]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'session_state_changed' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(row["payload"]) for row in rows]


async def _db_statements(scratch_url: str) -> list[str]:
    rows = await _all(scratch_url, "SELECT statement FROM claims ORDER BY statement")
    return [str(row["statement"]) for row in rows]


async def _consolidating_runs(scratch_url: str, session_id: uuid.UUID) -> list[JsonDict]:
    rows = await _all(
        scratch_url,
        "SELECT phase, finish_reason FROM model_runs WHERE session_id = :s AND phase = 'consolidating' ORDER BY id",
        {"s": str(session_id)},
    )
    return [dict(row) for row in rows]


def _curator_calls(fake_llm: FakeLLM) -> list[dict[str, Any]]:
    """Вызовы куратора из журнала fake-сервера: user-сообщение куратора начинается с «# Вопрос»."""
    return [r for r in fake_llm.requests() if str(r.get("last_user", "")).startswith("# Вопрос")]


def _fixation_events(fake_llm: FakeLLM) -> list[dict[str, Any]]:
    return [r for r in _curator_calls(fake_llm) if "# Исправление предложения" in str(r.get("last_user", ""))]


# ── т1: репроигрывание фикстуры 3701d1ce — раунд исправления; ответ записан 2 claims из 4 ──

@pytest.mark.asyncio
async def test_fixture_off_by_one_link_replay_gets_one_correction_round(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    bad_answer = _curator_answer(
        _CLAIMS_FIXTURE,
        _links((2, 1), (3, 2), (5, 3), (4, 4)),  # индексы claims с единицы: link[3] → claim 4
        summary="Официальные данные и оценки рынка труда Тармении.",
    )
    fixed_answer = _curator_answer(
        _CLAIMS_FIXTURE,
        _links((0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (5, 2), (0, 3), (4, 3)),  # тот же ответ, индексы с нуля
        summary="Официальные данные и оценки рынка труда Тармении.",
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _QUESTION,
        [_fetch(url) for url in _URLS] + [_COMPLETE, bad_answer, fixed_answer],
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    # ровно два обращения к куратору: первый ответ и ОДНО исправление
    calls = _curator_calls(fake_llm)
    assert len(calls) == 2

    # второй запрос несёт раздел исправления: причина дословно, перечень claims с нуля,
    # отклонённое предложение JSON'ом; первый запрос — без раздела
    assert "# Исправление предложения" not in str(calls[0]["last_user"])
    second = str(calls[1]["last_user"])
    assert "# Исправление предложения" in second
    assert f"- {_FIXTURE_REASON}" in second
    assert "(всего 4), индексы с нуля" in second
    assert '[3] «Аналитический обзор сервиса «Веер»' in second
    assert '"evidence_index": 4, "claim_index": 4' in second  # JSON отклонённого ответа — дословно

    # событие раунда — одно, прежний фильтр отказа не видит отказа вовсе (второй ответ принят)
    fixations = await _events(scratch_url, session_id, "curator_correction_requested")
    assert len(fixations) == 1
    assert fixations[0]["public_summary"] == "curator proposal returned for one correction round"
    fixation_payload = dict(fixations[0]["payload"])["curator_correction_requested"]
    assert fixation_payload["reasons"] == [_FIXTURE_REASON]
    assert len(fixation_payload["rejected_proposal"]["claims"]) == 4
    assert await _rejection_events(scratch_url, session_id) == []

    # принятое утверждение называет раунд и ПЕРВОЙ отказ (аддитивный ключ claim_created)
    claim_events = await _claim_created_payloads(scratch_url, session_id)
    assert len(claim_events) == 1
    round_key = claim_events[0]["correction_round"]
    assert round_key["reasons"] == [_FIXTURE_REASON]
    assert len(round_key["rejected_proposal"]["claims"]) == 4
    # было (до T7.108): принято целиком, `claims_applied` не появляется, в базе все четыре
    # утверждения. стало (гейт «значение на связи», ADR-0034 §10): у стендового ответа часть
    # связей ведёт на страницы БЕЗ значения утверждения (июльская страница у августовского
    # claim'а; обзоры «Веер» без 2,3 — у июльского; список газеты без цифр — у claim'а про
    # динамику). Связь без значения снимается с названной причиной; claim, у которого не
    # осталось ни одной связи, снимается как операция (механизм T7.105) — отказ частичный,
    # раунд исправления при этом НЕ запускается (снят не весь ответ).
    # Дополнение T7.123 (часть 2): у claim'а «10,3 резюме на одну вакансию» нет измеренного
    # профиля, но есть ГОЛОЕ десятичное значение (тот же профиль, что у pairing-фильтров
    # T7.120) — прежняя молчавшая проверка снимает обе его связи (ни «Тарменика», ни
    # «Трудовики» не называют 10,3): было applied=2, стало applied=1. Это и есть закрытая
    # дыра к1 («9,6» без единицы измерения проходила гейт незамеченной).
    payload = claim_events[0]
    assert len(payload["claims"]) == 4
    assert payload["claims_proposed"] == 4 and payload["claims_applied"] == 1
    assert [entry["reason"] for entry in payload["rejected_operations"]] == [
        "claim[1]: no linked evidence contains the claim value",
        "claim[2]: no linked evidence contains the claim value",
        "claim[3]: no linked evidence contains the claim value",
    ]
    # связь, снятая у Оставшейся операции, названа записью связи; связи снятых операций
    # описаны их записями операций и в `rejected_links` не дублируются
    assert payload["rejected_links"] == [
        {
            "link_index": 1,
            "evidence_index": 1,
            "claim_index": 0,
            "relation": "supports",
            "reason": "evidence_link[1]: evidence 1 does not contain the value of claim 0",
        }
    ]

    # записано утверждение, чья улика несёт значение: август (служба занятости);
    # конкуренция «Веер» снимается с T7.123 — её голое значение 10,3 не названо ни одной
    # из двух связанных страниц
    assert await _db_statements(scratch_url) == [_CLAIMS_FIXTURE[0]["statement"]]

    # оба обращения к куратору — фазовые записи consolidating (шаговый бюджет исследователя
    # раунд НЕ расходует: второй вызов живёт в фазе consolidating)
    runs = await _consolidating_runs(scratch_url, session_id)
    assert len(runs) == 2


# ── т2: исправление снова отклонено — один финальный отказ с дайджестом первого ────────

@pytest.mark.asyncio
async def test_second_answer_still_rejected_yields_single_final_rejection(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    bad_answer = _curator_answer(
        _CLAIMS_FIXTURE, _links((2, 1), (3, 2), (5, 3), (4, 4)), summary="Итог (первый ответ)."
    )
    still_bad = _curator_answer(
        _CLAIMS_FIXTURE, _links((2, 1), (3, 2), (5, 3), (4, 5)), summary="Итог (исправленный ответ)."
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _QUESTION,
        [_fetch(url) for url in _URLS] + [_COMPLETE, bad_answer, still_bad],
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    # ровно два обращения; третьего вызова куратору хост не даёт
    assert len(_curator_calls(fake_llm)) == 2

    # событие раунда — одно; финальный отказ — ровно один (прежний фильтр), и это отказ ВТОРОГО
    # ответа (та же форма ключей, что и всегда) с аддитивным correction_round ПЕРВОГО
    assert len(await _events(scratch_url, session_id, "curator_correction_requested")) == 1
    rejections = await _rejection_events(scratch_url, session_id)
    assert len(rejections) == 1
    payload = dict(rejections[0]["payload"])
    assert payload["curator_rejected"] == ["evidence_link[3]: claim index 5 out of range"]
    round_key = payload["correction_round"]
    assert round_key["reasons"] == [_FIXTURE_REASON]
    assert len(round_key["rejected_proposal"]["claims"]) == 4

    # знание не изменилось: ни одного нового утверждения, staging не исполнялся
    assert await _db_statements(scratch_url) == []
    assert await _claim_created_payloads(scratch_url, session_id) == []


# ── т3: каждый из четырёх целых гейтов получает ровно один раунд ──────────────────────

# T7.123 (часть 2): третья короткая страница — «Веер» со значением 10,3. Гейт значения
# теперь видит и «голые» десятичные значения (дыра к1), поэтому исправленный ответ gate2
# обязан связать утверждение со страницей, где 10,3 названо: прежде утверждение без
# измеренного значения проходило гейт незамеченным.
_SHORT_URLS = [
    "http://tarstat.example/labour-aug",
    "http://tarmenika.example/jobmarket",
    "http://veer.example/competition",
]


def _short_script(*answers: dict[str, Any]) -> list[dict[str, Any]]:
    return [_fetch(url) for url in _SHORT_URLS] + [_COMPLETE, *answers]


@pytest.mark.asyncio
async def test_gate2_unresolved_reverify_reference_gets_one_round(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Неизвестная `existing_claim_id` (единственная операция) — отказ целиком → раунд;
    исправленный ответ без ссылки принимается."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    statement = "Конкуренция соискателей в Тармении в апреле 2026 года составила 10,3 резюме на вакансию."
    bad = _curator_answer(
        [{"statement": statement, "claim_type": "external_fact", "existing_claim_id": str(uuid.uuid4())}],
        _links((0, 0), (1, 0)),
    )
    # было (до T7.123): тот же ответ без ссылки — утверждение без измеренного значения гейт
    # значения не проверял вовсе. стало: голое значение 10,3 проверяется, и исправленный
    # ответ обязан вести связь к странице, где 10,3 названо («Веер», evidence 2)
    fixed = _curator_answer(
        [{"statement": statement, "claim_type": "external_fact"}], _links((2, 0))
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _q("Конкуренция соискателей в Тармении в апреле 2026 года."),
        _short_script(bad, fixed),
    )
    session_id = outcome.session_id

    assert len(_curator_calls(fake_llm)) == 2
    assert fake_llm.state()["queue_left"] == 0  # третий вызов не тратил скрипт
    fixations = await _events(scratch_url, session_id, "curator_correction_requested")
    assert len(fixations) == 1
    reasons = dict(fixations[0]["payload"])["curator_correction_requested"]["reasons"]
    assert len(reasons) == 1 and reasons[0].startswith("claim[0]: reverify reference")
    # второй ответ принят: финального отказа нет, утверждение записано
    assert await _rejection_events(scratch_url, session_id) == []
    assert await _db_statements(scratch_url) == [statement]


@pytest.mark.asyncio
async def test_gate3_dateless_temporal_fact_gets_one_round_and_fixed_date_lands(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    statement = "Уровень безработицы в Тармении в августе 2026 года составил 2,2 процента."
    bad = _curator_answer(
        [{"statement": statement, "claim_type": "temporal_fact"}],  # temporal_fact без as_of
        _links((0, 0), (1, 0)),
    )
    fixed = _curator_answer(
        [{"statement": statement, "claim_type": "temporal_fact", "as_of": "2026-09-30T00:00:00+00:00"}],
        _links((0, 0), (1, 0)),
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _q("Безработица в Тармении в августе 2026 года."),
        _short_script(bad, fixed),
    )
    session_id = outcome.session_id

    assert len(_curator_calls(fake_llm)) == 2
    assert fake_llm.state()["queue_left"] == 0
    fixations = await _events(scratch_url, session_id, "curator_correction_requested")
    assert len(fixations) == 1
    assert dict(fixations[0]["payload"])["curator_correction_requested"]["reasons"] == [
        "claim[0]: temporal_fact requires as_of"
    ]
    assert await _rejection_events(scratch_url, session_id) == []
    assert await _db_statements(scratch_url) == [statement]


@pytest.mark.asyncio
async def test_gate4_rules_kind_mismatch_gets_one_round_and_retyped_claim_lands(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """`empirical_conjecture`, поддержанная уликами чтения (движок правил допускает для неё
    только `experiment_run`) при единственной операции — отказ предложения целиком по правилам
    → раунд; исправленный ответ с `external_fact` принимается."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    # T7.108 (а), ADR-0034 §10: утверждение переформулировано без измеренного значения —
    # иначе обе связки с `_SHORT_URLS` снимались бы гейтом «значение на связи» и тест
    # ловил бы отказ по причине гейта вместо предмета теста (несоответствие `claim_type`
    # правилам). Мера правила не зависит от значения: `empirical_conjecture` с уликами
    # чтения отклоняется движком и с пустым значением.
    statement = "Вероятно, безработица в Тармении к концу 2026 года сохранится на уровне последних месяцев."
    bad = _curator_answer(
        [{"statement": statement, "claim_type": "empirical_conjecture"}], _links((0, 0), (1, 0))
    )
    fixed = _curator_answer(
        [{"statement": statement, "claim_type": "external_fact"}], _links((0, 0), (1, 0))
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        "Оценка безработицы в Тармении к концу 2026 года. Источники: " + " ".join(_SHORT_URLS),
        _short_script(bad, fixed),
    )
    session_id = outcome.session_id

    assert len(_curator_calls(fake_llm)) == 2
    fixations = await _events(scratch_url, session_id, "curator_correction_requested")
    assert len(fixations) == 1
    reasons = dict(fixations[0]["payload"])["curator_correction_requested"]["reasons"]
    assert reasons and reasons[0].startswith("claim 0 (empirical_conjecture)")
    assert await _rejection_events(scratch_url, session_id) == []
    assert await _db_statements(scratch_url) == [statement]


# ── т4: частичный отказ варианта B раунд НЕ запускает ──────────────────────────────────

@pytest.mark.asyncio
async def test_partial_rejection_never_starts_a_correction_round(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    good_claim = {
        "statement": "Уровень безработицы в Тармении в августе 2026 года составил 2,2 процента (служба занятости).",
        "claim_type": "external_fact",
    }
    bad_claim = {
        "statement": "Вероятно, число вакансий в Тармении продолжит сокращаться.",
        "claim_type": "empirical_conjecture",
    }
    answer = _curator_answer([good_claim, bad_claim], _links((0, 0), (1, 0), (0, 1), (1, 1)))
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        "Безработица и вакансии в Тармении в 2026 году. Источники: " + " ".join(_SHORT_URLS),
        _short_script(answer),
    )
    session_id = outcome.session_id

    # один вызов куратора, события раунда нет, снятая операция названа как раньше (T7.105)
    assert len(_curator_calls(fake_llm)) == 1
    assert await _events(scratch_url, session_id, "curator_correction_requested") == []
    claim_events = await _claim_created_payloads(scratch_url, session_id)
    assert len(claim_events) == 1
    payload = claim_events[0]
    assert payload["claims_proposed"] == 2 and payload["claims_applied"] == 1
    assert payload["rejected_operations"]  # снятая операция названа, как в T7.105
    for key in ("correction_round", "correction_error"):
        assert key not in payload
    assert await _db_statements(scratch_url) == [good_claim["statement"]]


# ── т5: принятый первый ответ — журнал байт-прежний, ключей раунда нет ─────────────────

@pytest.mark.asyncio
async def test_accepted_first_answer_carries_no_correction_keys(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    statement = "Уровень безработицы в Тармении в августе 2026 года составил 2,2 процента."
    answer = _curator_answer(
        [{"statement": statement, "claim_type": "temporal_fact", "as_of": "2026-09-30T00:00:00+00:00"}],
        _links((0, 0), (1, 0)),
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        "Безработица в Тармении в августе 2026 года. Источники: " + " ".join(_SHORT_URLS),
        _short_script(answer),
    )
    session_id = outcome.session_id

    assert len(_curator_calls(fake_llm)) == 1
    for key in ("curator_correction_requested", "correction_round", "correction_error"):
        assert not await _events(scratch_url, session_id, key)
    claim_events = await _claim_created_payloads(scratch_url, session_id)
    assert len(claim_events) == 1
    for key in ("correction_round", "correction_error"):
        assert key not in claim_events[0]
    # второй запрос модели вовсе не отправлялся
    assert _fixation_events(fake_llm) == []


# ── т6: ошибка ВТОРОГО вызова — отказ ПЕРВОГО ответа называет сбой исправления ─────────

_CUT_ANSWER = '{"summary": "Итог по безработице: уровень в Тармении в августе 2026 составил 2,2 пр'


def _dateless_bad() -> dict[str, Any]:
    return _curator_answer(
        [
            {
                "statement": "Уровень безработицы в Тармении в августе 2026 года составил 2,2 процента.",
                "claim_type": "temporal_fact",
            }
        ],
        _links((0, 0), (1, 0)),
    )


_CANARY: dict[str, Any] = {
    "content": {"summary": "Канарейка", "claims": [], "evidence_links": [], "new_questions": []},
}


async def _run_second_call_failure(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
    second_entries: list[dict[str, Any]],
) -> tuple[str, JsonDict]:
    """Один прогон с целым первым ответом (гейт as_of) и заданным сбоем второго вызова.

    В скрипте после сбоя стоит НЕИСПОЛЬЗУЕМАЯ «канарейка»: если бы хост сделал ещё одно
    обращение к модели (третий раунд или повтор после отказа), канарейка была бы съедена —
    её остаток в очереди доказывает, что раунд ровно один. Возвращает (scratch_url, payload
    финального отказа)."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _q("Безработица в Тармении в августе 2026 года."),
        _short_script(_dateless_bad(), *second_entries, _CANARY),
    )
    session_id = outcome.session_id

    # раунд начат ровно один раз; раздел исправления модели отправлялся (повторы шлёт сам
    # gateway — их не считаем), но канарейка не тронута: после отказа хост к модели больше
    # не обращался (число попыток транзитных сбоев gateway'ем диктуется, поэтому сравниваем
    # не точное число съеденных записей, а неизрасходованную канарейку)
    assert len(await _events(scratch_url, session_id, "curator_correction_requested")) == 1
    assert any("# Исправление предложения" in str(r.get("last_user", "")) for r in fake_llm.requests())
    assert fake_llm.state()["queue_left"] >= 1

    rejections = await _rejection_events(scratch_url, session_id)
    assert len(rejections) == 1
    payload = dict(rejections[0]["payload"])
    # отказ — прежний payload ПЕРВОГО ответа (те же ключи и причины) плюс аддитивный marker
    assert payload["curator_rejected"] == ["claim[0]: temporal_fact requires as_of"]
    assert "correction_error" in payload and "correction_round" not in payload
    assert await _db_statements(scratch_url) == []
    return scratch_url, payload


@pytest.mark.asyncio
async def test_second_call_unavailable_adds_correction_error_to_first_rejection(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    _scratch, payload = await _run_second_call_failure(
        migrated_db, fake_llm, tmp_path, [{"error": 500}, {"error": 500}, {"error": 500}]
    )
    marker = payload["correction_error"]
    assert marker["kind"] == "unavailable"
    assert 0 < len(str(marker["error"])) <= 300


@pytest.mark.asyncio
async def test_second_call_truncation_names_attempts_and_keeps_rejection(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    cuts = [{"raw_message": _CUT_ANSWER, "finish_reason": "length"} for _ in range(3)]
    scratch_url, payload = await _run_second_call_failure(migrated_db, fake_llm, tmp_path, cuts)
    marker = payload["correction_error"]
    assert marker["kind"] == "truncated_output"
    # каждая отсечённая попытка — строка model_runs (T7.80), и их число названо в отказе
    assert marker["truncated_attempts"] >= 1
    rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM model_runs WHERE finish_reason = 'length' AND output_schema_valid = false",
        {},
    )
    assert int(dict(rows[0])["n"]) >= marker["truncated_attempts"]


@pytest.mark.asyncio
async def test_second_call_request_rejected_names_engine_refusal(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    _scratch, payload = await _run_second_call_failure(migrated_db, fake_llm, tmp_path, [{"error": 400}])
    marker = payload["correction_error"]
    assert marker["kind"] == "request_rejected"
    assert 0 < len(str(marker["error"])) <= 300


# ── т7: сбой ПЕРВОГО вызова раунд НЕ запускает (ADR-0034 §9) ───────────────────────────

@pytest.mark.asyncio
async def test_first_call_failure_never_starts_a_correction_round(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _q("Безработица в Тармении в августе 2026 года."),
        _short_script({"error": 500}, {"error": 500}, {"error": 500}, _CANARY),
    )
    session_id = outcome.session_id

    # раздела исправления модель не получала ни разу; канарейка не съедена — исправление
    # не запрашивалось; ключей раунда нет ни в одном событии
    assert _fixation_events(fake_llm) == []
    assert fake_llm.state()["queue_left"] >= 1
    for key in ("curator_correction_requested", "correction_round", "correction_error"):
        assert not await _events(scratch_url, session_id, key)
    payloads = await _all_state_payloads(scratch_url, session_id)
    assert any(p.get("curator_error_kind") == "unavailable" for p in payloads)
    assert await _db_statements(scratch_url) == []


# ── т8: полный раздел не влезает в потолок → раунда НЕТ, отказ с correction_skipped ─
# (T7.106a, ADR-0034 §9, правило 3)

_LONG_CLAIMS: list[dict[str, Any]] = [
    {
        "statement": f"Утверждение {i}. " + "длинная формулировка показателя со временем и областью "*35,
        "claim_type": "external_fact",
    }
    for i in range(30)
]


@pytest.mark.asyncio
async def test_section_over_cap_skips_the_round_with_correction_skipped(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """30 claims по ~1900 знаков (стенд: ответы куратора доходят до полной длины): целиком
    JSON такого предложения с перечнем по всем claims не влезает в потолок раздела.
    Прежнее правило резало JSON и перечень хвостами и тихо теряло claims; теперь раунда
    нет вовсе — прежний отказ целиком плюс аддитивный `correction_skipped`."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    bad_answer = _curator_answer(
        _LONG_CLAIMS, _links((0, 0), (1, 99)), summary="Длинный ответ без исправимой связи."
    )
    outcome = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        _q("Конкуренция соискателей и безработица в Тармении в 2026 году."),
        _short_script(bad_answer, _CANARY),
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    # ровно ОДИН вызов куратора: раздела исправления модель не видела, канарейка цела
    assert len(_curator_calls(fake_llm)) == 1
    assert _fixation_events(fake_llm) == []
    assert fake_llm.state()["queue_left"] >= 1

    # события раунда нет (раунд не состоялся); ключей прежнего отказа это не меняет
    for key in ("curator_correction_requested", "correction_round", "correction_error"):
        assert not await _events(scratch_url, session_id, key)

    # отказ — целиком ПЕРВЫЙ ответ, прежними ключами, плюс аддитивный correction_skipped
    rejections = await _rejection_events(scratch_url, session_id)
    assert len(rejections) == 1
    payload = dict(rejections[0]["payload"])
    assert payload["curator_rejected"] == ["evidence_link[1]: claim index 99 out of range"]
    assert len(payload["rejected_proposal"]["claims"]) >= 1
    skipped = payload["correction_skipped"]
    assert skipped["reason"] == "section_over_limit"
    assert skipped["limit"] == CORRECTION_SECTION_MAX_CHARS

    # длина названа честно: это длина полного раздела того же предложения (тот же чистый
    # построитель, без LLM и БД), а не произвольное число
    recomputed = build_correction_section(
        payload["curator_rejected"], CuratorProposal.model_validate(bad_answer["content"])
    )
    assert isinstance(recomputed, CorrectionOverLimit)
    assert recomputed.chars == skipped["chars"] > skipped["limit"]

    # summary отказа сохраняет прежнюю голову (отказ прежний) и называет пропуск раунда
    assert str(rejections[0]["public_summary"]).startswith("curator proposal rejected by host validation")
    assert "correction skipped" in str(rejections[0]["public_summary"])

    # знание не изменилось: ни записей, ни staging
    assert await _db_statements(scratch_url) == []
    assert await _claim_created_payloads(scratch_url, session_id) == []

