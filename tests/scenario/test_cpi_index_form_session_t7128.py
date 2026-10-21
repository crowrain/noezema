"""T7.128 (часть 3, ADR-0034 §10 дополнение): сценарий c4 — годовое значение в индексной форме.

Стендовый случай c4 (сессия 2d682ab0, config-v29): исследователь прочитал релиз Росстата
«Об индексе потребительских цен в сентябре 2026 года» — единственное годовое значение на
странице дано индексной формой: строка таблицы «Индекс потребительских цен» и ячейка «106,35»
в столбце «к сентябрю 2025 г.». Куратор предложил «годовая инфляция … составила 6,35%», гейт
значения («значение на связи», T7.108) снял связь: отдельного «6,35» на странице нет, «6,35»
внутри «106,35» граница текстовой формы не считает. Снятие всех связей = снятие операции =
отказ предложения целиком (все операции сняты), затем раунд исправления, пустой ответ куратора
и карточка «Ответ не записан», хотя ответ жил в памяти.

Сценарий закрепляет: та же страница и та же формулировка проходят гейт значения — связь и
операция остаются, утверждение записано, журнала снятий (`rejected_links`,
`rejected_operations`) нет. Гейт периода при этом молчит в той же мере, что и прежде: он судит
связи, на которых значение утверждения уже найдено (юрисдикция гейта значения).

HTTP-слой research proxy подменён; страница — побайтовый нормализованный текст реального
стендового релиза (tests/fixtures/t7128/rosstat-cpi-2026-09.normalized.txt).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_curator_period_gate_session_t7121 import (
    PAGES,
    FakeEngineClient,
    _claim_created_payloads,
    _complete,
    _payload,
)
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import _fetch, _run_session
from tests.scenario.test_scope_coverage import _all, _seed_question

pytestmark = pytest.mark.scenario

REPO_ROOT = Path(__file__).resolve().parents[2]

URL_CPI = "https://rosstat.example/press-157-cpi-2026-09"
CPI_PAGE = (REPO_ROOT / "tests/fixtures/t7128/rosstat-cpi-2026-09.normalized.txt").read_text(
    encoding="utf-8"
).splitlines()

C4_CLAIM = (
    "Официальная годовая инфляция в России в сентябре 2026 года составила 6,35% "
    "(по данным Росстата)."
)
C4_RATIONALE = (
    'Прочитал релиз Росстата: "Индекс потребительских цен" и значение 106,35 процентов '
    "к сентябрю 2025 года. Годовой рост — 6,35%."
)
QUESTION = (
    "Какова официальная годовая инфляция в России в сентябре 2026 года: приведи цифру "
    f"и первоисточник. Источники: {URL_CPI}"
)


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)
    monkeypatch.setitem(PAGES, URL_CPI, CPI_PAGE)


def _payload_with_gate() -> dict[str, Any]:
    payload = dict(_payload())
    payload["research_proxy"]["rate_limit_max"] = 20
    return payload


@pytest.mark.asyncio
async def test_cpi_index_form_page_confirms_the_percent_claim(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload_with_gate())).state == "active"

    qid = await _seed_question(scratch_url, QUESTION)
    fake_llm.script(
        [
            _fetch(URL_CPI),
            _complete(C4_RATIONALE),
            {
                "content": {
                    "summary": "Годовая инфляция по релизу Росстата за сентябрь 2026 года.",
                    "claims": [
                        {
                            "statement": C4_CLAIM,
                            "claim_type": "external_fact",
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
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "art", qid)
    assert outcome.final_state.value == "succeeded", outcome.termination_reason

    # связь и операция прошли гейт значения: журнала снятий нет вовсе
    payloads = await _claim_created_payloads(scratch_url, outcome.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert "rejected_operations" not in payload, payload
    assert "rejected_links" not in payload, payload
    assert payload["public_summary"] == "curator: 1 claims, 0 questions"

    # утверждение записано и связано со страницей релиза
    rows = await _all(
        scratch_url,
        "SELECT c.id::text AS id, s.canonical_uri AS uri FROM claims c "
        "JOIN evidence e ON e.claim_id = c.id "
        "JOIN sources s ON s.id = e.source_id "
        "WHERE c.statement = :t AND e.relation = 'supports'",
        {"t": C4_CLAIM},
    )
    assert len(rows) == 1, rows
    assert str(rows[0]["uri"]).startswith("https://rosstat.example")

    # карточка вопроса: ответ записан этой сессией (одна строка знания), а не «Ответ не записан»
    applied = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM claims WHERE created_in_session = :s",
        {"s": str(outcome.session_id)},
    )
    assert int(dict(applied[0])["n"]) == 1
