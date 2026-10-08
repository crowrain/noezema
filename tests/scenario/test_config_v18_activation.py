"""Scenario: активация config-v18 на узле (T7.80, ADR-0030).

config-v18 = config-v17 ровно с одной правкой: в раздел ``model`` добавлен
``reasoning_by_phase`` — политика «рассуждать/не рассуждать» для каждой фазы ВЫЗОВА.
Сценарий проверяет то, что видно только на активированном снимке:

* активный снимок опознаётся canonical-хешем config-v18 (в БД попадает canonical, не хеш файла);
* политика читается из ЭФФЕКТИВНОГО снимка (§3) ровно теми режимами, которые в нём записаны,
  и разрешается тем же кодом, которым ею пользуются вызовы шлюза;
* всё остальное — байт в байт config-v17: пины промптов, лимит шагов, пороги, правила
  независимости, бюджеты токенов и ``model.max_output_tokens`` (8192 — комната ответа halogen
  не расширяется: замер T7.80 показывает, что срез переезжает вместе с потолком);
* кривая политика отвергается ДО публикации (§5.4.1), head не меняется;
* откат = активация payload'а config-v17: canonical-хеш прежний, политики нет ни у одной фазы.

Внешних запросов нет; БД — scratch-база фикстуры.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.services.config import ConfigService
from packages.llm_gateway.reasoning_compat import (
    PHASE_CONSOLIDATION,
    PHASE_EXPLORATION,
    PHASE_EXTRACTION,
    PHASE_PLANNING,
    PHASE_VERIFICATION,
    resolve_reasoning_policy,
)
from packages.llm_gateway.roles import Role, resolve_prompts
from packages.memory.activation import ActivationError
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]
V18_PATH = REPO_ROOT / "docs" / "eval" / "config-v18-payload.json"
V17_PATH = REPO_ROOT / "docs" / "eval" / "config-v17-payload.json"

#: canonical-хеш payload'а config-v18 (то, что попадает в config_snapshots.payload_sha256)
V18_CANONICAL = "b5605e4eb04d610ca387f732bfa35f4571750fd4370013f2f2a0e8e7e45f9dec"
#: canonical-хеш payload'а config-v17 — прежний head и штатный откат
V17_CANONICAL = "5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717"

ACTIVE = (
    "SELECT cs.id, cs.payload_sha256, cs.model, cs.session_limits, "
    "cs.prompts->'curator'->>'version', cs.prompts->'explorer'->>'version' "
    "FROM runtime_config_heads h JOIN config_snapshots cs ON cs.id = h.active_config_snapshot_id "
    "WHERE h.scope = 'global'"
)


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _active(engine: AsyncEngine) -> Any:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(text(ACTIVE))).first()


@pytest.mark.asyncio
async def test_activating_config_v18_stores_the_reasoning_policy(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    result = await _run_online(engine, _payload(V18_PATH))
    assert result.state == "active", result

    row = await _active(engine)
    assert row is not None
    assert row[1] == V18_CANONICAL, row[1]

    # снимок хранит payload разделами по колонкам (§14.1): политика — в колонке model
    model = dict(row[2])
    assert model == _payload(V18_PATH)["model"], sorted(model)
    assert model["reasoning_by_phase"] == {
        "consolidation": "off",
        "extraction": "off",
        "exploration": "on",
        "planning": "on",
        "verification": "off",
    }
    # потолок вывода НЕ поднят: замер T7.80 — halogen держит фиксированную комнату ответа,
    # и срез переезжает вместе с max_output_tokens, а не исчезает
    assert model["max_output_tokens"] == 8192, model["max_output_tokens"]

    # ровно тот код, которым пользуется оркестратор, читает политику из снимка
    policy = resolve_reasoning_policy(model)
    assert policy.mode_for(PHASE_CONSOLIDATION) == "off"
    assert policy.mode_for(PHASE_EXTRACTION) == "off"
    assert policy.mode_for(PHASE_VERIFICATION) == "off"
    assert policy.mode_for(PHASE_EXPLORATION) == "on"
    assert policy.mode_for(PHASE_PLANNING) == "on"

    # лимит шагов и пины не изменились: v18 не трогает ни бюджет шагов, ни инструкции модели
    limits = dict(row[3])
    assert limits == _payload(V17_PATH)["session_limits"], limits
    assert limits["max_explorer_steps"] == 16, limits
    assert row[4] == "curator-v8" and row[5] == "explorer-v9"

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snapshot = await ConfigService.get_effective(db)
    loaded = resolve_prompts(dict(snapshot.prompts), REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v9"
    assert loaded[Role.CURATOR].version == "curator-v8"

    # остальные разделы — дословно v17: пороги и правила независимости задача не меняла
    v17 = _payload(V17_PATH)
    for key in sorted(set(v17) - {"prompts", "schema_version"}):
        if key == "model":
            continue  # единственный изменённый раздел — сверен выше
        assert dict(getattr(snapshot, key)) == v17[key], key
    assert snapshot.claim_type_rules == v17["claim_type_rules"]
    assert snapshot.claim_type_rules["external_fact"]["min_independence_groups"] == 2


@pytest.mark.asyncio
async def test_malformed_reasoning_policy_is_refused_before_it_becomes_effective(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Политика читается на каждом вызове шлюза, поэтому кривой payload обязан быть отвергнут
    ДО публикации (§5.4.1): иначе узел будет жить с битыми сессиями вместо понятного отказа
    активации."""
    _, engine = migrated_db
    assert (await _run_online(engine, _payload(V18_PATH))).state == "active"

    broken = copy.deepcopy(_payload(V18_PATH))
    broken["model"]["reasoning_by_phase"] = {"consolidation": "off", "curating": "sometimes"}
    with pytest.raises(ActivationError):
        await _run_online(engine, broken)

    row = await _active(engine)
    assert row is not None and row[1] == V18_CANONICAL, "отвергнутая активация сдвинула head"

    unknown_phase = copy.deepcopy(_payload(V18_PATH))
    unknown_phase["model"]["reasoning_by_phase"] = {"consolidation": True}
    with pytest.raises(ActivationError):
        await _run_online(engine, unknown_phase)
    row = await _active(engine)
    assert row is not None and row[1] == V18_CANONICAL, row[1]


@pytest.mark.asyncio
async def test_config_v17_remains_available_as_the_rollback(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Откат остаётся рабочим: payload v17 не переписан, активация им возвращает head на прежний
    canonical-хеш, и ни одна фаза не получает политику (запросы шлюза — ровно прежние)."""
    _, engine = migrated_db
    assert (await _run_online(engine, _payload(V18_PATH))).state == "active"
    assert (await _active(engine))[1] == V18_CANONICAL

    assert (await _run_online(engine, _payload(V17_PATH))).state == "active"
    row = await _active(engine)
    assert row is not None
    assert row[1] == V17_CANONICAL
    model = dict(row[2])
    assert "reasoning_by_phase" not in model
    policy = resolve_reasoning_policy(model)
    for phase in (PHASE_CONSOLIDATION, PHASE_EXTRACTION, PHASE_VERIFICATION, PHASE_EXPLORATION, PHASE_PLANNING):
        assert policy.mode_for(phase) is None, phase
    assert row[4] == "curator-v8" and row[5] == "explorer-v9"
