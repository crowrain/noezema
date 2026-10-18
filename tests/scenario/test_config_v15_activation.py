"""Scenario: активация config-v15 на узле (T7.73).

Проверяет не «файл правильный» (это закреплено юнит-тестами payload'а), а то, что
активация этим payload'ом оставляет в БД ровно тот снимок, который обещает файл:

* активный снапшот — config-v15 по canonical-хешу (в `config_snapshots.payload_sha256`
  попадает canonical, не хеш файла — AGENTS §8);
* пины промптов в снимке — curator-v8 и explorer-v7 с sha256 самих файлов: резолвер
  достает их текст по пути из снапшота (ADR-0019 content pinning), а не по имени роли;
* правила оценки (пороги, `claim_type_rules`, окна, research_proxy, policy) — те же, что у
  config-v14: v15 меняет только инструкции модели;
* откат = активация payload'а config-v14: он остаётся закоммичен и применим.

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
V15_PATH = REPO_ROOT / "docs" / "eval" / "config-v15-payload.json"
V14_PATH = REPO_ROOT / "docs" / "eval" / "config-v14-payload.json"

#: canonical-хеш payload'а config-v15 (то, что попадает в config_snapshots.payload_sha256)
V15_CANONICAL = "b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73"
#: canonical-хеш payload'а config-v14 — прежний head и штатный откат
V14_CANONICAL = "22903be78602cf7897f0de57b99514b66c58eca83960fa05458fd341e0104df4"

CURATOR_V8_SHA = "c14603eed9119c474f85c2848b68ab5a1e661398f930f0effe5dbb50caadde71"
EXPLORER_V7_SHA = "41d3f7a22f5704f2b9302057fc944ab8cd069c8cfb3063fc5973df28f8ea40fa"

#: пины хранятся разделами (§14.1: payload разложен по колонкам снимка), отдельной
#: колонки «payload» в `config_snapshots` нет
ACTIVE = (
    "SELECT cs.id, cs.payload_sha256, cs.prompts->'curator'->>'version', "
    "cs.prompts->'curator'->>'sha256', "
    "cs.prompts->'explorer'->>'version', "
    "cs.prompts->'explorer'->>'sha256' "
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
async def test_activating_config_v15_stores_the_pinned_prompts(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    result = await _run_online(engine, _payload(V15_PATH))
    assert result.state == "active", result

    row = await _active(engine)
    assert row is not None
    # снимок опознаётся по canonical-хешу payload'а (не по хешу файла)
    assert row[1] == V15_CANONICAL, row[1]
    assert row[2] == "curator-v8" and row[3] == CURATOR_V8_SHA
    assert row[4] == "explorer-v7" and row[5] == EXPLORER_V7_SHA

    # промпты достаются из снимка по его путям — тексты именно закреплённых версий
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.CURATOR].version == "curator-v8"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V8_SHA
    assert "as_of: null` при `existing_claim_id`" in loaded[Role.CURATOR].text
    assert loaded[Role.EXPLORER].version == "explorer-v7"
    assert "ПЕРВОИСТОЧНИК" in loaded[Role.EXPLORER].text

    # правила узла не изменились: v15 = v14 кроме блока prompts. Снимок хранит payload
    # разделами по колонкам (§14.1), поэтому сверяется каждый раздел отдельной строкой.
    v14 = _payload(V14_PATH)
    v15 = _payload(V15_PATH)
    assert set(v14) == set(v15)
    assert v14["schema_version"] == v15["schema_version"]
    stored = {key: dict(getattr(snapshot, key)) for key in sorted(set(v14) - {"prompts", "schema_version"})}
    for key, value in stored.items():
        assert value == v14[key], key
    assert dict(snapshot.prompts) != v14["prompts"]
    assert snapshot.policy["capabilities"]["tools"] == v14["policy"]["capabilities"]["tools"]
    assert snapshot.claim_type_rules["temporal_fact"]["requires_as_of"] is True
    assert snapshot.claim_type_rules["temporal_fact"] == v14["claim_type_rules"]["temporal_fact"]


@pytest.mark.asyncio
async def test_config_v14_remains_available_as_the_rollback(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Откат остаётся рабочим: payload v14 не переписан, и активация им возвращает head на
    прежний canonical-хеш (именно это делает менеджер при откате стенда)."""
    _, engine = migrated_db
    assert (await _run_online(engine, _payload(V15_PATH))).state == "active"
    assert (await _active(engine))[1] == V15_CANONICAL

    assert (await _run_online(engine, _payload(V14_PATH))).state == "active"
    row = await _active(engine)
    assert row is not None
    assert row[1] == V14_CANONICAL
    assert row[2] == "curator-v7" and row[4] == "explorer-v6"
