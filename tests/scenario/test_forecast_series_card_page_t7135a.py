"""Scenario (DB): страница карточки печатает подпись серии прогнозов (T7.135a, ADR-0035 §22.7).

Первое замечание приёмки T7.135: сервер клал `forecast_series_label/hint/text` в
утверждение карточки, но страница (`renderClaim` в `apps/web/api.py`) печатала только
известные ей поля — в интерфейсе подписи не было видно, она жила только в API.

Здесь та же карта, что в `tests/scenario/test_forecast_series_card_t7135.py`:

* сервер отдаёт готовую строку `forecast_series_text` — страница обязана печатать её
  отдельным блоком под мета-строкой утверждения, с `forecast_series_hint` во
  всплывающей подсказке; своей фразы на стороне клиента нет (ни «Актуальный прогноз»,
  ни «Устаревший прогноз», ни «Дата прогноза не указана» в JS не встречаются —
  страница печатает то, что прислал сервер);
* устаревший и недатированный прогноз печатается заметным классом предупреждения,
  актуальный — нейтральным;
* блок условный: он включается серверным полем, поэтому карточка без меток серии
  не печатает ни одной строки подписи.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.scenario.test_forecast_series_card_t7135 import _card, _forecast_claim
from tests.scenario.test_web_answer_api import _client, _make, _seed_events, _seed_question, _seed_session

pytestmark = [pytest.mark.scenario]


def _script(page: str) -> str:
    """Только тело <script> страницы: то, что исполняет браузер."""
    start = page.index("<script>") + len("<script>")
    return page[start : page.index("</script>", start)]


def _series_block(script: str) -> str:
    """Кусок JS от условия на серверную строку серии до конца этого условия."""
    start = script.index("claim.forecast_series_text")
    tail = script[start:]
    return tail[: tail.index("\n  }") + 4]


@pytest.mark.asyncio
async def test_answer_page_prints_the_forecast_series_line_of_the_card(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Было → стало. Было: страница печатала `relation_label`, `type_label`,
    `grade_label`, `freshness_label` и молчала про `forecast_series_text` — подписи
    серии не было видно нигде, кроме API. Стало: отдельный блок под мета-строкой
    утверждения, подсказка — `forecast_series_hint`."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _ = await _card(engine)
        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            card_page = (await client.get(f"/answer/{question_id}")).text

        # сервер отдал готовую строку — страница обязана печатать её, а не свою фразу
        assert card["claims"][0]["forecast_series_text"]
        script = _script(card_page)
        assert "claim.forecast_series_text" in script, "страница не печатает серверную строку серии"
        assert "claim.forecast_series_hint" in script, "страница не печатает подсказку серии"

        block = _series_block(script)
        # строка печатается дословно: витрина не складывает текст из кусочков
        assert re.search(r"textContent\s*=\s*claim\.forecast_series_text", block), block
        assert "title" in block, block
        # своей подписи в JS нет: все три фразы принадлежат серверному словарю
        for phrase in ("Актуальный прогноз", "Устаревший прогноз", "Дата прогноза не указана"):
            assert phrase not in script, phrase
        # актуальная запись — нейтральным стилем, устаревшая и недатированная —
        # заметным классом предупреждения
        assert "warn" in block, block
        assert script.index("claim.forecast_series_text") > script.index("claim.freshness_label")
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_card_without_forecast_marks_gets_no_series_line(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Карточка без меток серии: сервер не посылает поля, печатать нечего — ни одной
    строки подписи в ответе нет, строки карточки прежние."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine,
            session_id=session_id,
            question_id=question_id,
            state="succeeded",
            termination_reason="goal_reached",
        )
        measured = "Годовая инфляция в России по итогам 2025 года составила 5,6% (декабрь к декабрю)."
        await _forecast_claim(
            engine,
            claim_id=uuid.UUID(int=21),
            statement=measured,
            as_of="2025-12-31",
            created_in_session=session_id,
        )
        await _seed_events(engine, session_id, [(30, "claim_created", {"relied_claim_ids": []})])

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            page = (await client.get(f"/answer/{question_id}")).text

        assert len(card["claims"]) == 1
        assert not [key for key in card["claims"][0] if key.startswith("forecast_series")], card["claims"][0].keys()
        # сам условный блок живёт в JS страницы всегда: печатать его он начинает
        # только когда сервер прислал поле
        assert "claim.forecast_series_text" in page
    finally:
        await app_engine.dispose()
