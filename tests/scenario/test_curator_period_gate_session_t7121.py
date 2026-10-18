"""T7.121 (части 2–3, ADR-0038 §2–§3): сценарные тесты гейтов честности формулировки.

Гейт периода: каждый явный период формулировки операции-наблюдения обязан встречаться
в тексте той же связанной страницы в окне значения (`PUBLICATION_WINDOW_CHARS` = 400,
тот же порог вето T7.113 — новых констант нет). «14 октября 2026 года 11:46» и «14 октября.»
— датировка страницы, периодом не является: стендовый случай с1 (finmarket-6727171,
безработица 2,2%) законно снимается именно поэтому.

Гейт даты прогноза: формулировка с маркером прогноза/ожидания обязана назвать дату самого
прогноза — отличную от целевого периода — либо честно написать «дата прогноза на странице
не указана» (стендовый случай с2, прогноз ВТБ 5–6% на ysia-vtb-forecast).

Что закреплено сценарием (граница куратора, ADR-0034 вариант B + вариант C):
- снятая отдельно операция — запись `rejected_operations` в payload уже существующего
  события `claim_created` с именованной причиной `claim[<i>]: период «…» не найден рядом со
  значением на связанной странице — возьми период из текста или напиши, что период не указан;
  остальное предложение применяется; нового AuditEventType нет;
- когда сняты ВСЕ операции предложения — отказ целиком тем же событием `session_state_changed`
  с аддитивным `curator_reject_kind` = "claim_period_not_near_value" и РОВНО ОДИН раунд
  исправления: исправленное предложение (период взят из текста страницы) принимается;
- отказ по дате прогноза называется отдельно и тоже лечится одним раундом;
- законные периоды (названное полугодие, диапазон месяцев квартала) гейт не трогает:
  ни отказов, ни раунда исправления.

Фикстурные страницы — побайтовые нормализованные тексты реальных стендовых страниц
(STATUS T7.121); утверждения — либо реальные формулировки зафиксированных предложений
(аудит-фикстура), либо вымышленные случаи на вымышленных числах и страницах.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.fetch import FetchResult
from tests.conftest import FakeLLM
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import _curated_payload, _fetch, _run_session
from tests.scenario.test_scope_coverage import _all, _seed_question
from tests.unit.test_curator_period_gate_t7121 import C1_CREATED, C1_NO_PERIOD, C2_VTB

pytestmark = pytest.mark.scenario

REPO_ROOT = Path(__file__).resolve().parents[2]

URL_FINMARKET = "https://finmarket.example/news/6727171"
URL_YSIA = "https://ysia.example/vtb-forecast-2026"
URL_TARM_HALF = "https://tarmstat.example/half-year-review"

# побайтовые страницы стендовой фикстуры: нормализованный артефакт равен им построчно
PAGES: dict[str, list[str]] = {
    URL_FINMARKET: (REPO_ROOT / "tests/fixtures/t7121/finmarket-6727171.normalized.txt")
    .read_text(encoding="utf-8")
    .splitlines(),
    URL_YSIA: (REPO_ROOT / "tests/fixtures/t7121/ysia-vtb-forecast.normalized.txt")
    .read_text(encoding="utf-8")
    .splitlines(),
    # вымышленная страница вымышленного ведомства: названное полугодие и квартал диапазоном
    URL_TARM_HALF: [
        "Полугодовой обзор Тарменской службы статистики. Рост цен в промышленности: "
        "1-е полугодие 2026 года — ровно 5,0% к предыдущему полугодию.",
        "ВВП за апрель–июнь 2026 года вырос на 1,5%.",
    ],
}


class FakeEngineClient:
    """Подмена HTTP-слоя research proxy (тот же приём, что у тестов T7.108): страницы
    отдаются абзацами `<p>` — нормализованный текст артефакта равен фикстуре построчно."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url] if line.strip())
        data = f"<html><body>{paragraphs}</body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _payload() -> dict[str, Any]:
    payload = copy.deepcopy(_curated_payload())
    payload["research_proxy"]["rate_limit_max"] = 20
    # T7.121a д5: гейт периода/даты прогноза включается конфигурацией — без ключа он молчит.
    payload["session_limits"]["curator_record_gates"] = True
    return payload


def _claim(statement: str) -> dict[str, Any]:
    return {"statement": statement, "claim_type": "external_fact", "scope": {}}


def _links(*pairs: tuple[int, int], relation: str = "supports") -> list[dict[str, Any]]:
    return [{"evidence_index": e, "claim_index": c, "relation": relation} for e, c in pairs]


def _answer(
    claims: list[dict[str, Any]], links: list[dict[str, Any]], *, summary: str
) -> dict[str, Any]:
    return {
        "content": {
            "summary": summary,
            "claims": claims,
            "evidence_links": links,
            "new_questions": [],
        }
    }


def _complete(rationale: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": rationale,
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


async def _run(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_text: str,
    script: list[dict[str, Any]],
    tag: str,
) -> Any:
    question_id = await _seed_question(scratch_url, question_text)
    fake_llm.script(script)
    return await _run_session(
        scratch_url, fake_llm, tmp_path / f"ws{tag}", tmp_path / f"art{tag}", question_id
    )


async def _claim_created_payloads(scratch_url: str, session_id: Any) -> list[dict[str, Any]]:
    rows = await _all(
        scratch_url,
        "SELECT payload, public_summary FROM audit_events WHERE session_id = :s "
        "AND type = 'claim_created' AND payload ? 'claims' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(dict(row["payload"]), public_summary=row["public_summary"]) for row in rows]


async def _fixation_count(scratch_url: str, session_id: Any) -> int:
    rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM audit_events WHERE session_id = :s "
        "AND payload ? 'curator_correction_requested'",
        {"s": str(session_id)},
    )
    return int(dict(rows[0])["n"])


def _fixation_requests(fake_llm: FakeLLM) -> list[str]:
    return [
        str(r.get("last_user", ""))
        for r in fake_llm.requests()
        if "# Исправление предложения" in str(r.get("last_user", ""))
    ]


async def _correction_requested_payload(scratch_url: str, session_id: Any) -> dict[str, Any]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s "
        "AND payload ? 'curator_correction_requested' ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(rows) == 1, rows
    return dict(dict(rows[0]["payload"])["curator_correction_requested"])


async def _statements(scratch_url: str) -> list[str]:
    rows = await _all(scratch_url, "SELECT statement FROM claims ORDER BY statement")
    return [str(row["statement"]) for row in rows]


QUESTION_FINMARKET = (
    "Каков официальный уровень безработицы в России по методологии МОТ: приведи цифру "
    f"и первоисточник. Источники: {URL_FINMARKET}"
)
QUESTION_YSIA = (
    "Какой прогноз инфляции в России на 2026 год дают аналитики ВТБ: приведи значение "
    f"и первоисточник. Источники: {URL_YSIA}"
)
QUESTION_TARM = (
    "Каковы рост цен в промышленности и ВВП Тармении за первое полугодие 2026 года: "
    f"приведи обе цифры. Источники: {URL_TARM_HALF}"
)

# цитаты исследователя — якоря окна улики; обе подстроки существуют в фикстурных текстах
RATIONALE_FINMARKET = (
    'Прочитал обзор: "уровень безработицы" и значение 2,2% по методологии МОТ. Цифра в тексте есть.'
)
RATIONALE_YSIA = (
    'Прочитал прогноз ВТБ: "5–6 %" по итогам 2026 года. Значение в тексте есть.'
)
RATIONALE_TARM = (
    'Прочитал обзор: "Рост цен в промышленности" и «ВВП за апрель–июнь 2026 года вырос на 1,5%». '
    "Обе цифры названы."
)

# ── утверждения: реальные формулировки Audit-фикстуры (C1/C2) и их исправленные версии ──
SENTINEL_CLAIM = "Уровень безработицы в России в сентябре 2026 года составил 2,2%."
C2_FIXED = C2_VTB[:-1] + " (прогноз от 26 февраля 2026 года)."


# ── т5 (а): весь отказ по периоду + ровно один раунд исправления ────────────────


@pytest.mark.asyncio
async def test_claim_period_rejected_whole_with_one_correction_round(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Стендовый с1 в чистом виде: связная страница несёт значение «2,2», но единственный
    месяц рядом со значением там — датировка публикации («14 октября»), а не период.
    Предложение из одной операции отклоняется целиком (именованный `curator_reject_kind`),
    исправленное предложение (период взят из текста страницы) принимается."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    bad = _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Итог (первый ответ).")
    fixed = _answer([_claim(SENTINEL_CLAIM)], _links((0, 0)), summary="Итог (исправленный ответ).")
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [_fetch(URL_FINMARKET), _complete(RATIONALE_FINMARKET), bad, fixed],
        "t7121a",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # ровно один раунд исправления; повторный отказ финальный — третьего вызова нет
    assert await _fixation_count(scratch_url, session.session_id) == 1
    fixations = _fixation_requests(fake_llm)
    assert len(fixations) == 1
    assert "не найден рядом со значением на связанной странице" in fixations[0]
    assert "октябре 2026" in fixations[0]
    assert fake_llm.state()["queue_left"] == 0

    # вариант C (T7.106): итог сессии — УСПЕХ, поэтому журнала `curator_rejected` нет;
    # единственный след отказа — событие раунда с именованными причинами и дайджестом
    # отклонённого первого предложения, и аддитивный `correction_round` в событии принятого
    assert await _rejection_events(scratch_url, session.session_id) == []
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [
        "claim[0]: период «октябре 2026» не найден рядом со значением на связанной странице — "
        "возьми период из текста или напиши, что период не указан"
    ], correction["reasons"]
    assert "rejected_proposal" in correction

    # первое предложение ничего не записало; принято только исправленное
    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in claims] == [SENTINEL_CLAIM], claims
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    assert "rejected_operations" not in payloads[0]
    assert payloads[0]["correction_round"]["reasons"] == correction["reasons"]
    assert payloads[0]["public_summary"] == "curator: 1 claims, 0 questions"


# ── т5 (б): частичный отказ — остальное предложение применяется как предложено ────


@pytest.mark.asyncio
async def test_claim_period_rejection_is_per_operation_not_whole_proposal(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Две операции на одной странице: период первой рядом со значением не подтверждён,
    второй называет период из текста страницы. Вариант B (ADR-0034): снимается только
    первая операция, её причина именована в `rejected_operations` события `claim_created`;
    отказа предложения целиком и раунда исправления нет."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _answer(
                [_claim(C1_CREATED), _claim(SENTINEL_CLAIM)],
                _links((0, 0), (0, 1)),
                summary="Итог по источникам.",
            ),
        ],
        "t7121b",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _statements(scratch_url) == [SENTINEL_CLAIM]
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []

    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert payload["claims_proposed"] == 2 and payload["claims_applied"] == 1
    entry = payload["rejected_operations"][0]
    assert entry["claim_index"] == 0
    assert entry["reason"].startswith("claim[0]: период «октябре 2026»")
    assert "не найден рядом со значением на связанной странице" in entry["reason"]
    assert {"claim_index": 0, "evidence_index": 0, "relation": "supports"} in entry[
        "evidence_links_rejected"
    ]
    # снятая операция описана своей записью; её связи отдельной записью не дублируются
    assert "rejected_links" not in payload
    assert payload["public_summary"] == (
        "curator: 1 of 2 claims applied, 0 questions, 1 operations rejected by host validation"
    )


# ── т5 (в): период можно не называть вовсе — честная формулировка принимается ─────


@pytest.mark.asyncio
async def test_claim_without_period_is_accepted_on_the_same_page(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """«…напиши, что период не указан»: формулировка без явного периода молчит (гейт
    требует только названное) — та же страница принимает её без отказа и без исправлений."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [
            _fetch(URL_FINMARKET),
            _complete(RATIONALE_FINMARKET),
            _answer([_claim(C1_NO_PERIOD)], _links((0, 0)), summary="Итог по источникам."),
        ],
        "t7121c",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _statements(scratch_url) == [C1_NO_PERIOD]
    assert await _fixation_count(scratch_url, session.session_id) == 0
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert "rejected_operations" not in payload and "rejected_links" not in payload


# ── т5 (г): неисправленное предложение — финальный отказ с именованным kind ───────


@pytest.mark.asyncio
async def test_uncorrected_period_rejection_is_final_with_named_reject_kind(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Второе предложение повторяет первое: раунд израсходован, отказ финальный. Тот же
    event и прежние ключи журнала (`curator_rejected`) плюс аддитивный
    `curator_reject_kind` = "claim_period_not_near_value" и `correction_round` — третьего
    вызова модели нет, знание не меняется."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    bad = _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Итог (первый ответ).")
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_FINMARKET,
        [_fetch(URL_FINMARKET), _complete(RATIONALE_FINMARKET), bad, bad],
        "t7121f",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _fixation_count(scratch_url, session.session_id) == 1
    assert fake_llm.state()["queue_left"] == 0

    rejections = await _rejection_events(scratch_url, session.session_id)
    assert len(rejections) == 1, rejections
    payload = rejections[0]
    reason = (
        "claim[0]: период «октябре 2026» не найден рядом со значением на связанной странице — "
        "возьми период из текста или напиши, что период не указан"
    )
    assert payload["curator_rejected"] == [reason]
    assert payload["curator_reject_kind"] == "claim_period_not_near_value"
    assert "rejected_proposal" in payload
    assert payload["correction_round"]["reasons"] == [reason]

    # staging не исполнялся ни разу
    assert await _all(scratch_url, "SELECT id FROM claims") == []
    assert await _claim_created_payloads(scratch_url, session.session_id) == []


# ── т6 (а): дата прогноза — целый отказ с одним раундом исправления (стендовый с2) ──


@pytest.mark.asyncio
async def test_forecast_claim_without_forecast_date_rejects_with_one_correction_round(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Стендовый с2: «Аналитики ВТБ ожидают … по итогам 2026 года составит 5–6%» — маркер
    ожидания есть, даты самого прогноза нет. Целевого периода это не заменяет: отказ
    называется отдельно, лечится одной правкой — на ysia-vtb-forecast дата прогноза стоит
    датировочной строкой («26 февраля»), и здесь она подтверждает дату, а не период."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    bad = _answer([_claim(C2_VTB)], _links((0, 0)), summary="Итог (первый ответ).")
    fixed = _answer([_claim(C2_FIXED)], _links((0, 0)), summary="Итог (исправленный ответ).")
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_YSIA,
        [_fetch(URL_YSIA), _complete(RATIONALE_YSIA), bad, fixed],
        "t7121d",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    assert await _fixation_count(scratch_url, session.session_id) == 1
    fixations = _fixation_requests(fake_llm)
    assert len(fixations) == 1
    assert "обязано называть дату" in fixations[0]

    # вариант C: след первого отказа — событие раунда; журнала отказов нет (сессия успешна)
    assert await _rejection_events(scratch_url, session.session_id) == []
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert len(correction["reasons"]) == 1, correction["reasons"]
    assert "обязано называть дату" in correction["reasons"][0]
    assert "дата прогноза на странице не указана" in correction["reasons"][0]
    assert "rejected_proposal" in correction

    # первое предложение ничего не записало; принято исправленное
    claims = await _all(scratch_url, "SELECT statement FROM claims")
    assert [str(row["statement"]) for row in claims] == [C2_FIXED], claims
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    assert "rejected_operations" not in payloads[0]
    assert payloads[0]["correction_round"]["reasons"] == correction["reasons"]


# ── т6 (б): законные названные периоды не провоцируют правок ───────────────────


@pytest.mark.asyncio
async def test_named_half_year_and_quarter_periods_are_accepted_without_fixation(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    """Вымышленная страница вымышленного ведомства: названное «1-е полугодие 2026» и
    квартал диапазоном «апрель–июнь 2026» — подтверждения. Гейт не должен трогать законные
    формулировки: ни отказа, ни `rejected_operations`, ни раунда исправления."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_TARM,
        [
            _fetch(URL_TARM_HALF),
            _complete(RATIONALE_TARM),
            _answer(
                [
                    _claim("Рост цен в промышленности Тармении за 1-е полугодие 2026 года составил ровно 5,0%."),
                    _claim("ВВП Тармении во II квартале 2026 года вырос на 1,5%."),
                ],
                _links((0, 0), (0, 1)),
                summary="Итог по обзору.",
            ),
        ],
        "t7121e",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    statements = await _statements(scratch_url)
    assert len(statements) == 2, statements
    assert any("1-е полугодие 2026" in s for s in statements), statements
    assert any("II квартале 2026" in s for s in statements), statements
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert "rejected_operations" not in payload and "rejected_links" not in payload
    assert len(payload["claims"]) == 2


# ── контроль: фикстурные цитаты исследователя существуют в текстах страниц ──────


def test_rationale_quotations_exist_in_fixture_pages() -> None:
    finmarket = "\n".join(PAGES[URL_FINMARKET])
    ysia = "\n".join(PAGES[URL_YSIA])
    assert "уровень безработицы" in finmarket.lower(), "якорь улики finmarket съехал"
    assert re.search(r"5–6\s?%", ysia), "якорь улики ysia съехал"
