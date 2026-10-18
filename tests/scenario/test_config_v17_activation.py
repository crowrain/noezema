"""Scenario: активация config-v17 на узле (T7.77).

Активируется payload, в котором изменён ровно один пин — ``prompts.explorer`` → explorer-v9
(правило 12: прогноз до события не выдаётся за независимую оценку реализованного значения).
Сценарий проверяет то, что видно только на активированном снапшоте:

* активный снимок опознаётся canonical-хешем config-v17 (в БД попадает canonical, не хеш файла — AGENTS §8);
* пин исследователя в снимке — explorer-v9 с sha256 самого файла (ADR-0019), куратор и остальные роли —
  прежние пины; лимит шагов и все правила узла совпадают с config-v16: правка живёт в инструкциях модели;
* тексты промптов достаются именно закреплённых версий, и правило 12 присутствует в тексте исследователя;
* откат = активация payload'а config-v16: он закоммичен, непрередактируем и возвращает head на прежний
  canonical-хеш с explorer-v8.

Внешних запросов нет; БД — scratch-база фикстуры.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.services.config import ConfigService
from packages.llm_gateway.roles import Role, resolve_prompts
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]
V17_PATH = REPO_ROOT / "docs" / "eval" / "config-v17-payload.json"
V16_PATH = REPO_ROOT / "docs" / "eval" / "config-v16-payload.json"

#: canonical-хеш payload'а config-v17 (то, что попадает в config_snapshots.payload_sha256)
V17_CANONICAL = "5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717"
#: canonical-хеш payload'а config-v16 — прежний head и штатный откат
V16_CANONICAL = "740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3"

CURATOR_V8_SHA = "c14603eed9119c474f85c2848b68ab5a1e661398f930f0effe5dbb50caadde71"
EXPLORER_V9_SHA = "bf5169a06656ffce86bc3e9ea0d0c264824c11b2162a39a68b69321ce9d766af"
EXPLORER_V8_SHA = "eff45071d38dbc4d5eb87d4eccf26e48bbd9a2bd79e9f125e09e9a0eed33fb2e"

ACTIVE = (
    "SELECT cs.id, cs.payload_sha256, cs.session_limits, "
    "cs.prompts->'curator'->>'version', cs.prompts->'curator'->>'sha256', "
    "cs.prompts->'explorer'->>'version', cs.prompts->'explorer'->>'sha256' "
    "FROM runtime_config_heads h JOIN config_snapshots cs ON cs.id = h.active_config_snapshot_id "
    "WHERE h.scope = 'global'"
)


async def _active(engine: AsyncEngine) -> Any:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(text(ACTIVE))).first()


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_activating_config_v17_stores_the_pinned_explorer_v9(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    _, engine = migrated_db
    result = await _run_online(engine, _payload(V17_PATH))
    assert result.state == "active", result

    row = await _active(engine)
    assert row is not None
    assert row[1] == V17_CANONICAL, row[1]

    # лимиты не изменились вовсе: v17 не трогает бюджет шагов (в отличие от v16)
    limits = dict(row[2])
    assert limits == _payload(V17_PATH)["session_limits"], limits
    assert limits["max_explorer_steps"] == 16, limits

    assert row[3] == "curator-v8" and row[4] == CURATOR_V8_SHA
    assert row[5] == "explorer-v9" and row[6] == EXPLORER_V9_SHA

    # промпты достаются из снимка по его путям — тексты именно закреплённых версий
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v9"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V9_SHA
    explorer_text = loaded[Role.EXPLORER].text
    # новое правило в тексте, прежние — на месте (правило 10 из v8 перенесено дословно)
    assert "Прогноз до события — не независимая оценка реализованного значения" in explorer_text
    assert "Спорное число" in explorer_text
    assert loaded[Role.CURATOR].version == "curator-v8"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V8_SHA

    # правила узла не изменились: v17 = v16 кроме пина исследователя. Снимок хранит payload
    # разделами по колонкам (§14.1), поэтому сверяется каждый раздел отдельной строкой.
    v16 = _payload(V16_PATH)
    stored = {key: dict(getattr(snapshot, key)) for key in sorted(set(v16) - {"prompts", "schema_version"})}
    for key in sorted(set(v16) - {"prompts", "schema_version"}):
        assert stored[key] == v16[key], key
    # пороги и правила независимости — именно они дают «Проверено» или честный E1; v17 их не трогает
    assert snapshot.claim_type_rules == _payload(V17_PATH)["claim_type_rules"]
    assert snapshot.claim_type_rules["external_fact"]["min_independence_groups"] == 2
    assert dict(snapshot.research_proxy) == dict(v16["research_proxy"])


@pytest.mark.asyncio
async def test_config_v16_remains_available_as_the_rollback(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Откат остаётся рабочим: payload v16 не переписан, и активация им возвращает head на прежний
    canonical-хеш (именно это делает менеджер при откате стенда)."""
    _, engine = migrated_db
    assert (await _run_online(engine, _payload(V17_PATH))).state == "active"
    assert (await _active(engine))[1] == V17_CANONICAL

    assert (await _run_online(engine, _payload(V16_PATH))).state == "active"
    row = await _active(engine)
    assert row is not None
    assert row[1] == V16_CANONICAL
    assert row[2]["max_explorer_steps"] == 16, row[2]
    assert row[5] == "explorer-v8" and row[6] == EXPLORER_V8_SHA
    # прежний пин по-прежнему резолвится: файлы промптов не переписываются никогда
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v8"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V8_SHA
