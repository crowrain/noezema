"""T7.123 (часть 2, ADR-0038 Дополнение): репроигрывание стендовых отказов на границе куратора.

Сценарий закрепляет исправленные дыры честности на ЖИВОЙ границе (FakeLLM + подменный
HTTP-слой, ни одного внешнего запроса):

- к1 (64837cb5): «hh-индекс … составил 9,6» — голое десятичное значение теперь проверяется
  гейтом значения: связь к странице ключевой ставки (значения нет) снимается с названной
  причиной, операция остаётся со связью на страницу, где значение названо (стендовая форма:
  обе связи вели на порочащую страницу — операция снимается полностью, это второй тест);
- к2 (64837cb5/finmarket): перепроверка и новая формулировка проверяются гейтом периода по
  каждой связи: связь без периода рядом со значением снимается; операция умирает, только
  если не осталось ни одной честной связи — ровно один раунд исправления, период из текста;
- явная перепроверка проверяется формулировкой ЦЕЛИ (ADR-0033): честная страница цели
  принимается без отказов; страница без целевого периода — законный отказ с одним раундом;
- к4 (33a62678): отказ законен — период «II квартал 2026 года» назван только в заголовке
  страницы, дальше окна значения; формулировка с периодом из текста («по итогам мая 2026»)
  принимается. Строка отказа — байт-в-байт стендовая.

Случай к3 (объявленное в сводке намерение при пустом `claims`) репроигрывается отдельным
сценарием части 3 — `test_curator_declared_empty_session_t7123.py`.

Гейты периода/даты прогноза включаются ключом `session_limits.curator_record_gates`
(config-v28); без ключа — молчат (прежнее поведение закреплено отдельным тестом).
Фикстурные страницы — побайтовые нормализованные тексты реальных стендовых страниц
(tests/fixtures/t7121, tests/fixtures/t7123); формулировки claims — дословные строки
аудит-фикстур 64837cb5 и 33a62678.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_curator_period_gate_session_t7121 import (
    C1_CREATED,
    C1_NO_PERIOD,
    PAGES,
    URL_FINMARKET,
    FakeEngineClient,
    _answer,
    _claim,
    _claim_created_payloads,
    _complete,
    _correction_requested_payload,
    _fixation_count,
    _fixation_requests,
    _links,
    _payload,
    _run,
    _statements,
)
from tests.scenario.test_dateless_curator_reverify_order import (
    _PAGES as DATELESS_PAGES,
)
from tests.scenario.test_dateless_curator_reverify_order import (
    CANDIDATE_OBSERVED,
    CANDIDATE_OFFICIAL,
    DUP_559,
    _rejection_events,
    _seed_candidates,
)
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import _fetch
from tests.scenario.test_scope_coverage import _all

pytestmark = pytest.mark.scenario

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fixture_lines(name: str) -> list[str]:
    return (REPO_ROOT / "tests/fixtures/t7123" / name).read_text(encoding="utf-8").splitlines()


# ── страницы стендов: реальные нормализованные тексты + вымышленные дополнения ──

URL_SBERCIB = "https://sbercib.example/publication/klyuchevaya-stavka-11-09-2026"
URL_HR = "https://hr-ratings.example/analytics/rynok-truda-krizis"
URL_TERBUNY = "https://terbuny.superjob.example/research/articles/115899"
URL_STATGRAD = "https://statgrad.example/activity-index"
URL_REV_OK = "https://rosstat-review.example/revision-2025"
URL_REV_NOYEAR = "https://rosstat-review.example/revision-undated"

EXTRA_PAGES: dict[str, list[str]] = {
    URL_SBERCIB: _fixture_lines("sbercib-key-rate.normalized.txt"),
    URL_HR: _fixture_lines("hr-ratings-hh-index.normalized.txt"),
    URL_TERBUNY: _fixture_lines("terbuny-superjob.normalized.txt"),
    URL_STATGRAD: [
        "Портал «Статград»: индекс деловой активности в марте 2026 года составил 101,7 пункта."
    ],
    URL_REV_OK: [
        "Обзор пересмотра: официальная годовая инфляция в России за 2025 год "
        "(декабрь к декабрю) составила 5,59 процента."
    ],
    URL_REV_NOYEAR: [
        "Обзор пересмотра: годовая инфляция составила 5,59 процента по обновлённой методике."
    ],
}


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же HTTP-слой, что у тестов T7.121 (страницы отдаются абзацами `<p>`);
    фикстурные страницы стендов и посевные страницы T7.84 добавляются в общий словарь
    подмены (`_seed_candidates` читает их тем же клиентом)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)
    for url, lines in EXTRA_PAGES.items():
        monkeypatch.setitem(PAGES, url, lines)
    for url, body in DATELESS_PAGES.items():
        monkeypatch.setitem(PAGES, url, [body])


def _payload_plain() -> dict[str, Any]:
    payload = copy.deepcopy(_payload())
    payload["session_limits"].pop("curator_record_gates", None)
    return payload


def _fetch_page(url: str) -> dict[str, Any]:
    return _fetch(url)


def _curator_calls(fake_llm: FakeLLM) -> list[dict[str, Any]]:
    return [r for r in fake_llm.requests() if str(r.get("last_user", "")).startswith("# Вопрос")]


SUPERJOB_CLAIM = (
    "SuperJob: медианная заработная плата в банковской сфере Москвы выросла на 7,5% "
    "за 2026 год к 2025 году (по данным обзора за II квартал 2026 года)."
)
SUPERJOB_AS_OF = "2026-07-24T00:00:00+00:00"
# дословная строка отказа стенда 33a62678 (event claim_created, rejected_operations[0])
SUPERJOB_REJECT_REASON = (
    "claim[0]: период «ii квартал 2026 года» не найден рядом со значением на связанной "
    "странице — возьми период из текста или напиши, что период не указан"
)
SUPERJOB_HONEST = (
    "По данным обзора SuperJob, прибавка к медианной заработной плате в банковской сфере "
    "Москвы составила 7,5% (по Москве) по итогам мая 2026 года."
)
# дословная формулировка claim[3] предложения куратора 64837cb5
HH_CLAIM = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 9,6."
COMPANION_CLAIM = "Портал «Статград»: индекс деловой активности в марте 2026 года составил 101,7 пункта."

#: посевные утверждения стенда T7.84 (после `_seed_candidates` знание ровно такое)
SEEDED_STATEMENTS = sorted([CANDIDATE_OFFICIAL, CANDIDATE_OBSERVED, DUP_559])

# ── к1: голое значение проверяется; связь снимается, операция живёт ───────────


@pytest.mark.asyncio
async def test_k1_bare_value_link_is_removed_operation_keeps_honest_link(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question = (
        "Каков hh-индекс (количество резюме на вакансию) в России по данным HR-Ratings "
        f"в 2026 году: приведи цифру и первоисточник. Источники: {URL_SBERCIB} {URL_HR}"
    )
    script = [
        _fetch_page(URL_SBERCIB),
        _fetch_page(URL_HR),
        _complete(
            "Обзор HR-Ratings: «hh-индекс (количество резюме на одну вакансию) на уровне 9,6 "
            "в январе 2026 года». Страница ЦБ о ключевой ставке значения hh-индекса не несёт."
        ),
        _answer([_claim(HH_CLAIM)], _links((0, 0), (1, 0)), summary="Оценки рынка труда."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k1a")

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    # связь с порочащей страницей (evidence 0 — ключевая ставка, значения 9,6 нет) названа;
    # операция осталась со связью на страницу, где значение названо рядом с hh-индексом
    assert payload["rejected_links"] == [
        {
            "link_index": 0,
            "evidence_index": 0,
            "claim_index": 0,
            "relation": "supports",
            "reason": "evidence_link[0]: evidence 0 does not contain the value of claim 0",
        }
    ]
    assert "rejected_operations" not in payload
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []
    assert await _statements(scratch_url) == [HH_CLAIM]


@pytest.mark.asyncio
async def test_k1_audit_variant_operation_dies_without_value_anywhere(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Стендовая форма к1: обе связи claim[3] вели на страницы ключевой ставки (9,6 нет нигде).
    Теперь операция снимается полностью с прежней причиной T7.108; остальное предложение
    применяется (отказ частичный, раунда исправления нет)."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question = (
        "Каков hh-индекс в России по данным HR-Ratings в 2026 году и индекс деловой активности "
        f"по «Статграду» в марте 2026 года: приведи обе цифры. Источники: {URL_SBERCIB} {URL_STATGRAD}"
    )
    script = [
        _fetch_page(URL_SBERCIB),
        _fetch_page(URL_STATGRAD),
        _complete("Цитата обзора: «hh-индекс … на уровне 9,6»; «Статград»: «101,7 пункта»."),
        _answer(
            [_claim(HH_CLAIM), _claim(COMPANION_CLAIM)],
            _links((0, 0), (1, 1)),
            summary="Оценки рынка труда.",
        ),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k1b")

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    reasons = [entry["reason"] for entry in payload["rejected_operations"]]
    assert reasons == ["claim[0]: no linked evidence contains the claim value"]
    op = payload["rejected_operations"][0]["operation"]
    assert op["claim_index"] == 0 and op["statement"] == HH_CLAIM
    assert payload["rejected_operations"][0]["evidence_links_rejected"] == [
        {"evidence_index": 0, "claim_index": 0, "relation": "supports"}
    ]
    # связь снятой операции описана её записью операции и в `rejected_links` не дублируется
    assert payload.get("rejected_links", []) == []
    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _statements(scratch_url) == [COMPANION_CLAIM]


# ── к2: гейт периода по каждой связи; без честной связи — один раунд ──────────


@pytest.mark.asyncio
async def test_k2_period_gate_rejects_link_and_round_fixes_statement(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question = (
        "Каков официальный уровень безработицы в России в октябре 2026 года по методологии МОТ: "
        f"приведи цифру и первоисточник. Источники: {URL_FINMARKET}"
    )
    script = [
        _fetch_page(URL_FINMARKET),
        _complete("Финмаркет: «безработица … 2,2%»; датировка страницы — «14 октября.», не период."),
        _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Официальная оценка."),
        _answer([_claim(C1_NO_PERIOD)], _links((0, 0)), summary="Официальная оценка без периода."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k2a")

    # единственная связь снята гейтом периода (значения 2,2 на странице есть, периода рядом нет);
    # операция без связей снимается полностью — отказ целиком + РОВНО ОДИН раунд
    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [
        "claim[0]: период «октябре 2026» не найден рядом со значением на связанной "
        "странице — возьми период из текста или напиши, что период не указан"
    ]
    assert len(_fixation_requests(fake_llm)) == 1
    assert len(_curator_calls(fake_llm)) == 2  # третьего вызова хост не даёт
    assert await _rejection_events(scratch_url, session.session_id) == []

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["correction_round"]["reasons"] == correction["reasons"]
    assert await _statements(scratch_url) == [C1_NO_PERIOD]


@pytest.mark.asyncio
async def test_k2_period_gate_silent_without_config_key(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Прежнее поведение payload'ов до config-v28 закреплено: без ключа гейт периода молчит —
    та же формулировка и та же страница записываются без отказов (гейт значения безусловен,
    значение на странице есть)."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"

    question = (
        "Каков официальный уровень безработицы в России в октябре 2026 года по методологии МОТ: "
        f"приведи цифру и первоисточник. Источники: {URL_FINMARKET}"
    )
    script = [
        _fetch_page(URL_FINMARKET),
        _complete("Финмаркет: «безработица … 2,2%»."),
        _answer([_claim(C1_CREATED)], _links((0, 0)), summary="Официальная оценка."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k2b")

    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert "rejected_links" not in payloads[0] and "rejected_operations" not in payloads[0]
    assert await _statements(scratch_url) == [C1_CREATED]


# ── явная перепроверка: проверяется формулировкой цели (ADR-0033) ─────────────


@pytest.mark.asyncio
async def test_explicit_reverify_checked_with_target_statement(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Перепроверка с пересказом БЕЗ цифр проверяется формулировкой ЦЕЛИ: честная страница
    цели (значение + целевой период) принимается без отказов; страница без целевого периода —
    законный отказ с ровно одним раундом (было: перепроверки не проверялись вовсе)."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    assert (await _run_online(engine, _payload())).state == "active"

    assessments_before = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM claim_assessments WHERE claim_id = :c",
        {"c": str(ids["dup"])},
    )
    before = int(dict(assessments_before[0])["n"])

    question = (
        "Официальная годовая инфляция в России за 2025 год (декабрь к декабрю) по данным "
        f"Росстата: перепроверь значение по свежему обзору пересмотра. Источники: {URL_REV_OK}"
    )
    reverify = {
        "statement": "Перепроверка официальной оценки годовой инфляции по свежей публикации.",
        "claim_type": "temporal_fact",
        "as_of": "2025-12-31T00:00:00+00:00",
        "existing_claim_id": str(ids["dup"]),
    }
    script = [
        _fetch_page(URL_REV_OK),
        _complete("Обзор пересмотра: «официальная годовая инфляция в России за 2025 год "
                  "(декабрь к декабрю) составила 5,59 процента»."),
        _answer([reverify], _links((0, 0)), summary="Перепроверка подтверждена."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "rev1")

    assert await _fixation_count(scratch_url, session.session_id) == 0
    assert await _rejection_events(scratch_url, session.session_id) == []
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    assert "rejected_links" not in payloads[0] and "rejected_operations" not in payloads[0]
    # знание не изменилось числом строк: перепроверка — новая оценка цели, не новый claim
    assert await _statements(scratch_url) == SEEDED_STATEMENTS
    after_rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM claim_assessments WHERE claim_id = :c",
        {"c": str(ids["dup"])},
    )
    assert int(dict(after_rows[0])["n"]) == before + 1


@pytest.mark.asyncio
async def test_explicit_reverify_without_period_round_fixes_link(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_plain())).state == "active"
    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    assert (await _run_online(engine, _payload())).state == "active"
    curator_calls_before = len(_curator_calls(fake_llm))

    question = (
        "Официальная годовая инфляция в России за 2025 год (декабрь к декабрю) по данным "
        f"Росстата: перепроверь значение по свежему обзору пересмотра. Источники: {URL_REV_NOYEAR} {URL_REV_OK}"
    )
    reverify = {
        "statement": "Перепроверка официальной оценки годовой инфляции по свежей публикации.",
        "claim_type": "temporal_fact",
        "as_of": "2025-12-31T00:00:00+00:00",
        "existing_claim_id": str(ids["dup"]),
    }
    script = [
        _fetch_page(URL_REV_NOYEAR),
        _fetch_page(URL_REV_OK),
        _complete("Обзоры пересмотра: 5,59 процента."),
        _answer([reverify], _links((0, 0)), summary="Перепроверка по обзору без периода."),
        _answer([reverify], _links((1, 0)), summary="Перепроверка по обзору с периодом."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "rev2")

    # целевая формулировка (DUP_559: «за 2025 год») не найдена рядом со значением на первой
    # странице — связь снята, единственная операция снята полностью: отказ целиком + один раунд
    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [
        "claim[0]: период «за 2025 год» не найден рядом со значением на связанной "
        "странице — возьми период из текста или напиши, что период не указан"
    ]
    # ровно один раунд исправления: куратор вызывается дважды (посевные вызовы не считаем)
    assert len(_curator_calls(fake_llm)) - curator_calls_before == 2
    assert await _rejection_events(scratch_url, session.session_id) == []

    assessments = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM claim_assessments WHERE claim_id = :c AND created_in_session = :s",
        {"c": str(ids["dup"]), "s": str(session.session_id)},
    )
    assert int(dict(assessments[0])["n"]) == 1


# ── к4: честный отказ закреплен против реальной страницы (решение — не менять гейт) ──


@pytest.mark.asyncio
async def test_k4_reject_reason_is_honest_and_period_from_text_is_accepted(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Стенд 33a62678/claim[4]: период «II квартал 2026 года» назван в заголовке страницы,
    дальше окна значения; целевой период «за 2026 год» подтверждён. Отказ законен — его строка
    закрепляется байт-в-байт; исправленная формулировка берёт период из текста рядом со
    значением («по итогам мая 2026») и принимается. Гейт и пороги НЕ меняются."""
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    question = (
        "Как изменилась медианная заработная плата в банковской сфере Москвы в 2026 году "
        f"по данным SuperJob: приведи цифру и первоисточник. Источники: {URL_TERBUNY}"
    )
    quarter_claim = {
        "statement": SUPERJOB_CLAIM,
        "claim_type": "temporal_fact",
        "as_of": SUPERJOB_AS_OF,
        "scope": {},
    }
    honest_claim = {
        "statement": SUPERJOB_HONEST,
        "claim_type": "temporal_fact",
        "as_of": SUPERJOB_AS_OF,
        "scope": {},
    }
    script = [
        _fetch_page(URL_TERBUNY),
        _complete("Цитата таблицы обзора SuperJob: «+7,5%»; «прибавка … (по Москве 7,5 и 12,6% соответственно)»."),
        _answer([quarter_claim], _links((0, 0)), summary="Данные обзора SuperJob."),
        _answer([honest_claim], _links((0, 0)), summary="Данные обзора SuperJob, период из текста."),
    ]
    session = await _run(scratch_url, fake_llm, tmp_path, question, script, "k4")

    assert await _fixation_count(scratch_url, session.session_id) == 1
    correction = await _correction_requested_payload(scratch_url, session.session_id)
    assert correction["reasons"] == [SUPERJOB_REJECT_REASON]  # байт-в-байт стендовая строка
    assert len(_curator_calls(fake_llm)) == 2
    assert await _rejection_events(scratch_url, session.session_id) == []

    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert len(payloads) == 1
    assert payloads[0]["correction_round"]["reasons"] == [SUPERJOB_REJECT_REASON]
    assert await _statements(scratch_url) == [SUPERJOB_HONEST]
