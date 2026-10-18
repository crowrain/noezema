"""Scenario: активация config-v16 на узле (T7.76).

Проверяет не «файл правильный» (это закреплено юнит-тестами payload'а), а то, что активация этим
payload'ом оставляет в БД ровно тот снимок, который обещает файл:

* активный снапшот — config-v16 по canonical-хешу (в `config_snapshots.payload_sha256` попадает
  canonical, не хеш файла — AGENTS §8);
* пин исследователя в снимке — explorer-v8 с sha256 самого файла (ADR-0019 content pinning), куратор
  остаётся curator-v8 прежним пином: v16 меняет только инструкции модели и лимит шагов;
* `session_limits.max_explorer_steps` в снимке = 16 (оркестратор читает лимит из снапшота, не из кода —
  §5.2 «правила и бюджеты живут в snapshot»), остальные лимиты прежние;
* правила оценки (пороги, `claim_type_rules`, окна, research_proxy, policy) совпадают с config-v15:
  двусторонний поиск не меняет то, как хост считает независимость, он меняет то, что модель ищет;
* откат = активация payload'а config-v15: он остаётся закоммичен и применим.

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
V16_PATH = REPO_ROOT / "docs" / "eval" / "config-v16-payload.json"
V15_PATH = REPO_ROOT / "docs" / "eval" / "config-v15-payload.json"

#: canonical-хеш payload'а config-v16 (то, что попадает в config_snapshots.payload_sha256)
V16_CANONICAL = "740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3"
#: canonical-хеш payload'а config-v15 — прежний head и штатный откат
V15_CANONICAL = "b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73"

CURATOR_V8_SHA = "c14603eed9119c474f85c2848b68ab5a1e661398f930f0effe5dbb50caadde71"
EXPLORER_V8_SHA = "eff45071d38dbc4d5eb87d4eccf26e48bbd9a2bd79e9f125e09e9a0eed33fb2e"
EXPLORER_V7_SHA = "41d3f7a22f5704f2b9302057fc944ab8cd069c8cfb3063fc5973df28f8ea40fa"

#: пины хранятся разделами (§14.1: payload разложен по колонкам снимка), отдельной
#: колонки «payload» в `config_snapshots` нет
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
async def test_activating_config_v16_stores_the_pinned_explorer_and_step_limit(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    _, engine = migrated_db
    result = await _run_online(engine, _payload(V16_PATH))
    assert result.state == "active", result

    row = await _active(engine)
    assert row is not None
    # снимок опознаётся по canonical-хешу payload'а (не по хешу файла)
    assert row[1] == V16_CANONICAL, row[1]

    limits = dict(row[2])
    v16_limits = _payload(V16_PATH)["session_limits"]
    assert limits == v16_limits, limits
    # именно этот лимит читает оркестратор (`max_explorer_steps`), а не константа кода
    assert limits["max_explorer_steps"] == 16, limits
    for key in ("session_timeout_seconds", "phase_deadline_seconds"):
        assert limits[key] == v16_limits[key], key

    assert row[3] == "curator-v8" and row[4] == CURATOR_V8_SHA
    assert row[5] == "explorer-v8" and row[6] == EXPLORER_V8_SHA

    # промпты достаются из снимка по его путям — тексты именно закреплённых версий
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v8"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V8_SHA
    explorer_text = loaded[Role.EXPLORER].text
    assert "Спорное число" in explorer_text
    assert "сторонами поиска, а не одной" in explorer_text
    assert "независимая оценка инфляции 2025" in explorer_text
    assert "Бюджет шага" in explorer_text
    assert loaded[Role.CURATOR].version == "curator-v8"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V8_SHA

    # правила узла не изменились: v16 = v15 кроме пина исследователя и лимита шагов. Снимок хранит
    # payload разделами по колонкам (§14.1), поэтому сверяется каждый раздел отдельной строкой.
    v15 = _payload(V15_PATH)
    stored = {key: dict(getattr(snapshot, key)) for key in sorted(set(v15) - {"prompts", "schema_version"})}
    for key in sorted(set(v15) - {"prompts", "schema_version", "session_limits"}):
        assert stored[key] == v15[key], key
    assert stored["session_limits"]["max_explorer_steps"] != v15["session_limits"]["max_explorer_steps"]
    assert dict(snapshot.prompts)["explorer"] != dict(v15["prompts"]["explorer"])
    assert dict(snapshot.prompts)["curator"] == dict(v15["prompts"]["curator"])
    # пороги и правила независимости — именно они дают «Проверено» или честный E1; v16 их не трогает
    assert snapshot.claim_type_rules == _payload(V16_PATH)["claim_type_rules"]
    assert snapshot.claim_type_rules["external_fact"]["min_independence_groups"] == 2
    assert snapshot.policy["capabilities"]["tools"] == v15["policy"]["capabilities"]["tools"]
    assert dict(snapshot.research_proxy) == dict(v15["research_proxy"])


@pytest.mark.asyncio
async def test_config_v15_remains_available_as_the_rollback(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Откат остаётся рабочим: payload v15 не переписан, и активация им возвращает head на прежний
    canonical-хеш (именно это делает менеджер при откате стенда)."""
    _, engine = migrated_db
    assert (await _run_online(engine, _payload(V16_PATH))).state == "active"
    assert (await _active(engine))[1] == V16_CANONICAL

    assert (await _run_online(engine, _payload(V15_PATH))).state == "active"
    row = await _active(engine)
    assert row is not None
    assert row[1] == V15_CANONICAL
    assert row[2]["max_explorer_steps"] == 10, row[2]
    assert row[3] == "curator-v8" and row[5] == "explorer-v7"
    # прежний пин по-прежнему резолвится: файлы промптов не переписываются никогда
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v7"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V7_SHA
