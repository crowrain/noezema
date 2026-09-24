"""Unit tests: engine JSON Schema compatibility transform (T7.23, ADR-0012).

The transform strips exactly the keywords a given engine refuses from
the schema SENT TO THE ENGINE. It must be pure, idempotent, and must
touch nothing but the named keywords — everything else (types, enums,
anyOf/$defs structure, budgets) is what the model still has to obey,
and the host still validates the FULL pydantic model on the answer.
"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from packages.domain.schemas.staging import CuratorProposal
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.schema_compat import (
    HALOGEN_UNSUPPORTED_KEYWORDS,
    LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS,
    SCHEMA_PROFILES,
    strip_schema_keywords,
)

#: A schema exercising every keyword shape our schemas use, with the
#: unsupported ones nested at every level: top, $defs, properties,
#: anyOf branches, array items.
NESTED_SCHEMA: dict = {
    "$defs": {
        "Inner": {
            "additionalProperties": False,
            "properties": {
                "id": {"format": "uuid", "title": "Id", "type": "string"},
                "code": {"pattern": "^AB-\\d+$", "type": "string"},
                "when": {
                    "anyOf": [
                        {"format": "date-time", "type": "string"},
                        {"type": "null"},
                    ],
                    "default": None,
                    "title": "When",
                },
            },
            "required": ["id"],
            "title": "Inner",
            "type": "object",
        }
    },
    "additionalProperties": False,
    "properties": {
        "items": {
            "items": {"$ref": "#/$defs/Inner"},
            "maxItems": 5,
            "minItems": 1,
            "title": "Items",
            "type": "array",
        },
        "name": {"format": "email", "maxLength": 100, "minLength": 1, "type": "string"},
        "n": {"maximum": 10, "minimum": 0, "type": "integer"},
    },
    "required": ["items"],
    "title": "Top",
    "type": "object",
}


def _count(node: object, key: str) -> int:
    if isinstance(node, dict):
        return sum(1 for k in node if k == key) + sum(_count(v, key) for v in node.values())
    if isinstance(node, list):
        return sum(_count(v, key) for v in node)
    return 0


def _assert_only_keys_removed(full: object, stripped: object, keys: frozenset[str]) -> None:
    """stripped == full with exactly `keys` removed at every level and
    nothing else changed (no values, no order, no extra keys)."""
    if isinstance(full, list):
        assert isinstance(stripped, list)
        assert len(full) == len(stripped), "list length changed"
        for a, b in zip(full, stripped, strict=True):
            _assert_only_keys_removed(a, b, keys)
        return
    if not isinstance(full, dict):
        assert full == stripped, f"leaf value changed: {full!r} -> {stripped!r}"
        return
    assert isinstance(stripped, dict)
    for key, value in full.items():
        if key in keys:
            assert key not in stripped, f"{key} must be removed"
            continue
        assert key in stripped, f"{key} must be preserved"
        _assert_only_keys_removed(value, stripped[key], keys)
    for key in stripped:
        assert key in full, f"unexpected key {key} (extra keys are not allowed)"


@pytest.mark.unit
def test_strips_at_every_level_including_anyof_branches() -> None:
    out = strip_schema_keywords(NESTED_SCHEMA, {"format", "pattern"})
    assert _count(out, "format") == 0
    assert _count(out, "pattern") == 0
    # the anyOf branch lost its format but kept its type
    assert out["$defs"]["Inner"]["properties"]["when"]["anyOf"][0] == {"type": "string"}
    # the array items ref and the whole structure are intact
    assert out["properties"]["items"]["items"] == {"$ref": "#/$defs/Inner"}
    assert out["properties"]["n"] == {"maximum": 10, "minimum": 0, "type": "integer"}
    assert out["required"] == ["items"]
    assert out["additionalProperties"] is False


@pytest.mark.unit
def test_nothing_else_changes() -> None:
    out = strip_schema_keywords(NESTED_SCHEMA, {"format", "pattern"})
    _assert_only_keys_removed(NESTED_SCHEMA, out, {"format", "pattern"})


@pytest.mark.unit
def test_idempotent() -> None:
    once = strip_schema_keywords(NESTED_SCHEMA, HALOGEN_UNSUPPORTED_KEYWORDS)
    twice = strip_schema_keywords(once, HALOGEN_UNSUPPORTED_KEYWORDS)
    assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)


@pytest.mark.unit
def test_input_not_mutated() -> None:
    snapshot = copy.deepcopy(NESTED_SCHEMA)
    strip_schema_keywords(NESTED_SCHEMA, HALOGEN_UNSUPPORTED_KEYWORDS)
    assert snapshot == NESTED_SCHEMA


@pytest.mark.unit
def test_empty_keywords_is_identity() -> None:
    # profile "none" must not even copy: byte-for-byte the same object
    assert strip_schema_keywords(NESTED_SCHEMA, SCHEMA_PROFILES["none"]) is NESTED_SCHEMA


@pytest.mark.unit
def test_curator_proposal_schema_round_trip() -> None:
    full = CuratorProposal.model_json_schema()
    stripped = strip_schema_keywords(full, HALOGEN_UNSUPPORTED_KEYWORDS)
    # the two `format`s (uuid, date-time) are gone...
    assert _count(full, "format") == 2
    assert _count(stripped, "format") == 0
    assert _count(stripped, "pattern") == 0
    # ...everything else is preserved key by key
    _assert_only_keys_removed(full, stripped, HALOGEN_UNSUPPORTED_KEYWORDS)


@pytest.mark.unit
def test_profiles_registry() -> None:
    assert SCHEMA_PROFILES["none"] == frozenset()
    assert SCHEMA_PROFILES["halogen"] == HALOGEN_UNSUPPORTED_KEYWORDS
    assert frozenset({"format", "pattern"}) == HALOGEN_UNSUPPORTED_KEYWORDS
    assert SCHEMA_PROFILES["llamacpp-rocmfpx"] == LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS
    assert frozenset({"minLength", "maxLength"}) == LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS


@pytest.mark.unit
def test_unknown_schema_profile_fails_fast() -> None:
    with pytest.raises(ValidationError):
        LLMGatewayConfig(schema_profile="bogus-engine")
    # the default is the current behavior
    assert LLMGatewayConfig().schema_profile == "none"
    assert LLMGatewayConfig(schema_profile="halogen").schema_profile == "halogen"
    assert (
        LLMGatewayConfig(schema_profile="llamacpp-rocmfpx").schema_profile == "llamacpp-rocmfpx"
    )


@pytest.mark.unit
def test_curator_proposal_schema_round_trip_llamacpp_rocmfpx() -> None:
    """T7.36: the ROCmFPX-k2 profile removes exactly the length bounds and
    keeps ``format``/``pattern`` (that engine compiles those)."""
    full = CuratorProposal.model_json_schema()
    stripped = strip_schema_keywords(full, LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS)
    assert _count(full, "maxLength") > 0
    assert _count(stripped, "minLength") == 0
    assert _count(stripped, "maxLength") == 0
    assert _count(stripped, "format") == _count(full, "format") == 2
    assert _count(stripped, "pattern") == _count(full, "pattern")
    _assert_only_keys_removed(full, stripped, LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS)

