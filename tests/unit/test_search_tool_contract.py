"""Unit: контракт инструмента web.search (T7.71, §5.7, §5.12.1, ADR-0027 §4).

Закрепляется четыре вещи:

1. реестр — инструмент есть, аргумент ровно один (`query`), лишние поля отклоняются,
   класс идемпотентности OBSERVATION (ретраев нет: поиск уже мог уйти к движку);
2. потолок профиля — `web.search` есть в curated и open_lab, НО не в sealed: снимок правил
   не может выдать его sealed-сессии (`effective_profile` поднимает ProfileError), а движок
   политики отклоняет вызов у узкого профиля. Sealed-поиск — это `memory.search`
   (локальный индекс); обоснование расхождения с §5.7 — ADR-0028;
3. наблюдения поиска НИКОГДА не становятся evidence: у `observation_to_evidence` нет
   отображения для web.search, и оно не должно появиться (проверяется здесь напрямую);
4. config-v14 как документированный контракт: чем именно он отличается от config-v13
   и что в нём совпадает с реестром (инструментов, которых нет в реестре, в возможностях
   больше нет — это и была живая поломка на стенде).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.executor import Observation
from packages.domain.models.enums import IdempotencyClass, PolicyDecision
from packages.policy.engine import PolicyEngine
from packages.policy.profiles import ProfileError, effective_profile, load_profile
from packages.policy.tools import all_tools, get_tool, model_tools_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
V13 = REPO_ROOT / "docs" / "eval" / "config-v13-payload.json"
V14 = REPO_ROOT / "docs" / "eval" / "config-v14-payload.json"

pytestmark = pytest.mark.unit


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ─── 1. реестр ────────────────────────────────────────────────────────────


def test_web_search_is_registered_with_one_argument() -> None:
    spec = get_tool("web.search")
    assert spec is not None
    assert spec.name == "web.search"
    # поиск уже мог уйти к движку: повторный ретрай означал бы второй платный запрос
    assert spec.idempotency_class is IdempotencyClass.OBSERVATION
    assert spec.path_args == ()


def test_web_search_arguments_are_the_query_only() -> None:
    spec = get_tool("web.search")
    assert spec is not None
    ok = spec.args_model.model_validate({"query": "стоимость владения сервером 2026"})
    assert ok.query.startswith("стоимость")

    for bad in (
        {"query": ""},  # пустой запрос: движку отправлять нечего
        {"query": "x" * 501},  # потолок длины запроса
        {},  # запрос обязателен
        {"query": "запрос", "url": "https://example.com"},  # поиск не умеет открывать страницы
        {"query": "запрос", "max_results": 50},  # количество отдаёт узел, не модель
    ):
        with pytest.raises(ValidationError):
            spec.args_model.model_validate(bad)


def test_registry_stays_sorted_and_contains_the_new_tool() -> None:
    names = [spec.name for spec in all_tools()]
    assert names == sorted(names)
    assert "web.search" in names
    # прежние инструменты не потерялись
    for name in ("research.fetch", "memory.search", "workspace.read"):
        assert name in names


# ─── 2. потолок профиля и движок политики ─────────────────────────────────


def test_profile_ceilings_grant_search_only_outside_sealed() -> None:
    assert not load_profile("sealed").tool_allowed("web.search")
    assert load_profile("curated").tool_allowed("web.search")
    assert load_profile("open_lab").tool_allowed("web.search")


def test_model_tool_schema_hides_search_from_sealed() -> None:
    sealed = {t["name"] for t in model_tools_schema(load_profile("sealed"))["tools"]}
    curated = {t["name"] for t in model_tools_schema(load_profile("curated"))["tools"]}
    open_lab = {t["name"] for t in model_tools_schema(load_profile("open_lab"))["tools"]}
    assert "web.search" not in sealed
    assert "web.search" in curated
    assert "web.search" in open_lab


def test_snapshot_cannot_grant_search_to_a_sealed_session() -> None:
    """Потолок профиля — потолок: снять его снимком правил нельзя (§5.6, §11.2)."""
    caps = {
        "access_profile": "sealed",
        "capabilities": {
            "tools": ["workspace.read", "memory.search", "web.search"],
            "write_paths": ["/workspace"],
        },
    }
    with pytest.raises(ProfileError) as excinfo:
        effective_profile(caps)
    assert "web.search" in str(excinfo.value)


def test_engine_allows_search_in_curated_and_denies_it_without_a_grant() -> None:
    curated = effective_profile(
        {
            "access_profile": "curated",
            "capabilities": {
                "tools": ["workspace.read", "memory.search", "research.fetch", "web.search"],
                "write_paths": ["/workspace"],
            },
        }
    )
    engine = PolicyEngine(curated)

    allowed = engine.evaluate("web.search", {"query": "уровень моря 2026 измерения"})
    assert allowed.decision is PolicyDecision.ALLOW

    narrow = effective_profile(
        {
            "access_profile": "curated",
            "capabilities": {"tools": ["workspace.read"], "write_paths": ["/workspace"]},
        }
    )
    denied = PolicyEngine(narrow).evaluate("web.search", {"query": "уровень моря"})
    assert denied.decision is PolicyDecision.DENY
    assert any("not allowed by profile" in reason for reason in denied.reasons)

    bad_args = engine.evaluate("web.search", {"query": ""})
    assert bad_args.decision is PolicyDecision.DENY
    assert any("argument" in reason for reason in bad_args.reasons)

    smuggled = engine.evaluate("web.search", {"query": "уровень моря", "url": "http://127.0.0.1:8888"})
    assert smuggled.decision is PolicyDecision.DENY


def test_sealed_engine_denies_search_even_though_the_tool_is_known() -> None:
    """Инструмент известен реестру, но профилю недоступен — вызов отклоняется, не исполняется."""
    engine = PolicyEngine(load_profile("sealed"))
    decision = engine.evaluate("web.search", {"query": "что угодно"})
    assert decision.decision is PolicyDecision.DENY
    assert any("not allowed by profile" in reason for reason in decision.reasons)


# ─── 3. наблюдения поиска не становятся доказательством ────────────────────

_SEARCH_DATA: dict[str, Any] = {
    "query": "уровень моря 2026",
    "mode": "curated",
    "profile": "curated",
    "upstream_attempted": True,
    "upstream_count": 1,
    "local_count": 1,
    "truncated": False,
    "evidence": False,
    "fenced": True,
    "content": (
        "[поиск: уровень моря 2026]\nВнешние источники (навигация):\n"
        "<<<UNTRUSTED DATA BEGIN>>>\n1. Уровень моря\n   url: https://example.org/a\n"
        "   фрагмент: рост 3.4 мм в год\n<<<UNTRUSTED DATA END>>>"
    ),
}


def test_search_observation_produces_no_evidence() -> None:
    obs = Observation(tool="web.search", ok=True, data=dict(_SEARCH_DATA))
    assert observation_to_evidence(obs, {"query": "уровень моря 2026"}) is None

    failed = Observation(tool="web.search", ok=False, error="upstream rate limit exceeded")
    assert observation_to_evidence(failed, {"query": "уровень моря"}) is None


def test_search_hits_do_not_become_evidence_even_with_fetch_like_fields() -> None:
    """Похожие поля (url, отрывок текста) не должны случайно попасть в отображение fetch."""
    obs = Observation(
        tool="web.search",
        ok=True,
        data={
            "url": "https://example.org/a",
            "normalized_text": "рост 3.4 мм в год",
            "source_id": "",
            "content": _SEARCH_DATA["content"],
        },
    )
    assert observation_to_evidence(obs, {"query": "уровень моря"}) is None


# ─── 4. config-v14 как контракт ────────────────────────────────────────────


def test_config_v14_snapshot_offers_only_tools_that_exist_in_the_registry() -> None:
    """Живая поломка на стенде: возможности снимали инструмент, которого нет в реестре,
    и шаг падал с «unknown tool: artifact.create». Реестр — единственный источник контракта."""
    registry = {spec.name for spec in all_tools()}
    caps = _payload(V14)["policy"]["capabilities"]["tools"]
    assert "artifact.create" not in caps
    unknown = [name for name in caps if name not in registry]
    assert unknown == [], f"snapshot grants tools outside the registry: {unknown}"


def test_config_v14_differs_from_v13_only_by_the_search_grant_and_the_explorer_pin() -> None:
    v13, v14 = _payload(V13), _payload(V14)
    for section in sorted(set(v13) | set(v14)):
        if section in ("policy", "prompts"):
            continue
        assert v13[section] == v14[section], section

    assert v13["policy"]["access_profile"] == v14["policy"]["access_profile"]
    assert v13["policy"]["capabilities"]["network"] == v14["policy"]["capabilities"]["network"]
    assert v13["policy"]["limits"] == v14["policy"]["limits"]
    assert v13["policy"]["capabilities"]["write_paths"] == v14["policy"]["capabilities"]["write_paths"]

    removed = [t for t in v13["policy"]["capabilities"]["tools"] if t not in v14["policy"]["capabilities"]["tools"]]
    added = [t for t in v14["policy"]["capabilities"]["tools"] if t not in v13["policy"]["capabilities"]["tools"]]
    assert removed == ["artifact.create"]
    assert added == ["web.search"]

    # единственный изменённый пин промпта — explorer (текстexplorer-v5 остаётся нетронутым)
    assert v13["prompts"]["explorer"] != v14["prompts"]["explorer"]
    unchanged_roles = sorted(set(v13["prompts"]) - {"explorer"})
    for role in unchanged_roles:
        assert v13["prompts"][role] == v14["prompts"][role], role
    assert v14["prompts"]["explorer"]["version"] == "explorer-v6"
    assert v14["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v6.md"
    pinned = (REPO_ROOT / v14["prompts"]["explorer"]["path"]).read_text(encoding="utf-8")
    import hashlib

    assert hashlib.sha256(pinned.encode("utf-8")).hexdigest() == v14["prompts"]["explorer"]["sha256"]


def test_config_v13_is_not_rewritten_and_still_carries_the_old_pin() -> None:
    """Прежние payload'ы не переписываются никогда (AGENTS §8)."""
    v13 = _payload(V13)
    assert v13["prompts"]["explorer"]["version"] == "explorer-v5"
    assert "artifact.create" in v13["policy"]["capabilities"]["tools"]
    assert "web.search" not in v13["policy"]["capabilities"]["tools"]


def test_config_v14_grants_stay_inside_the_profile_ceiling() -> None:
    payload = copy.deepcopy(_payload(V14))
    profile = effective_profile(payload["policy"])
    assert "web.search" in profile.tools
    assert "artifact.create" not in profile.tools
