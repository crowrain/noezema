"""Engine JSON Schema compatibility profiles (T7.23).

Some OpenAI-compatible engines accept only a SUBSET of JSON Schema
keywords inside ``response_format.json_schema.schema`` and refuse the
whole request (HTTP 400) when an unsupported keyword is present. A
capability profile names the keywords a given engine refuses; the
gateway strips exactly those from the schema it SENDS.

Host-side validation is NOT weakened: the model's answer is still parsed
and validated against the FULL pydantic model (uuid, date-time, pattern,
all length/budget constraints). Stripping only relaxes what the engine
sees; the trusted host remains the validator (§3.7, ADR-0012).
"""

from __future__ import annotations

from packages.domain.models.base import JsonDict

#: Keywords halogen (Qwen3.8 Flash Next, ``.hgn``) refuses with HTTP 400
#: ``json_schema not supported: unsupported keyword `...```.
#:
#: Probed directly against the engine (T7.23, one keyword per request,
#: 2026-09-20): ``format`` (uuid / date-time) and ``pattern`` are
#: refused; type/properties/required/additionalProperties/enum/
#: minLength/maxLength/minItems/maxItems/minimum/maximum/$defs/$ref/
#: anyOf/default/title/description and ``strict`` (true or false) are
#: all accepted. The engine reports only the FIRST unsupported keyword,
#: so every keyword our schemas use was probed individually.
HALOGEN_UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset({"format", "pattern"})

#: Named capability profiles (key in LLMGatewayConfig.schema_profile,
#: env NOEZEMA_LLM_SCHEMA_PROFILE). "none" = no transformation (the
#: default; the schema is sent byte-for-byte as pydantic produces it).
SCHEMA_PROFILES: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "halogen": HALOGEN_UNSUPPORTED_KEYWORDS,
}


def strip_schema_keywords(schema: JsonDict, keywords: frozenset[str]) -> JsonDict:
    """Return a copy of ``schema`` with ``keywords`` removed at EVERY
    level (top, $defs, properties, anyOf/oneOf/allOf branches, items,
    nested objects). All other keys and values are preserved as-is.

    Pure and idempotent: the input is not mutated, and applying the
    function twice yields the same result as applying it once. An empty
    ``keywords`` set returns the input unchanged (byte-identical).
    """
    if not keywords:
        return schema
    stripped = _strip(schema, keywords)
    # a dict input maps to a dict output (keys are filtered, never
    # re-rooted), so this narrowing is exact, not a cast.
    assert isinstance(stripped, dict)
    return stripped


def _strip(node: object, keywords: frozenset[str]) -> object:
    if isinstance(node, dict):
        return {key: _strip(value, keywords) for key, value in node.items() if key not in keywords}
    if isinstance(node, list):
        return [_strip(item, keywords) for item in node]
    return node
