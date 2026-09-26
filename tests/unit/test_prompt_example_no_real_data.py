"""T7.39: prompt examples must not carry real run data.

T7.38 acceptance: the reverify example of curator-v5 used a REAL claim
id (``35d5b7ae-…``, SMOKE-V8-K2) and that run's real fact and ``as_of``.
On the next smoke on a FRESH DB the host would reject the copied
reference and — T7.34 — an unresolved reference rejects the WHOLE
proposal: gate 5 reads 0 again, and "the model does not use reverify"
is indistinguishable from "the model copied the example".

General protection (any future prompt version is covered):

1. no full UUID that appears in ANY prompt file (curator AND explorer,
   T7.50) may also appear in ``docs/`` — the reports and analyses there
   contain the real run ids, so an intersection is a leaked real id.
   Sole exception: curator-v5, frozen (content-pinned by config-v9)
   with its known leak pinned to the exact (uuid, doc) pairs — see
   ``FROZEN_KNOWN_LEAKS``;
2. the reverify example statements of every curator prompt (ALL
   ```` ```json ```` blocks parsed out of the prompt) must not occur in
   any question-set corpus — if the example were a corpus question's
   fact, the model could recognize the example in its own question and
   copy the example's ids/``as_of``. T7.43: rule 7 carries a second
   (negative) JSON example, so EVERY block is checked, not just the
   first; the ids inside the examples are full UUIDs of the prompt and
   are covered by check 1 (prompt UUIDs ∩ docs UUIDs = ∅);
3. T7.50: the explorer completion examples (the positive and the
   negative pair of explorer-v5) carry free text a model can match its
   own question against: every free-text field of every JSON example
   (``public_rationale``, ``expected_information``, the free-text
   ``decision.reason`` of the negative example) and the example topic
   itself must not occur in any question-set corpus (v1–v4 in the
   repo, the smoke set when present).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"
DOCS_DIR = REPO_ROOT / "docs"
# the smoke corpus lives outside the repo (run working directory) —
# checked when present
SMOKE_CORPUS = REPO_ROOT.parent / "smoke-v8" / "question-set-smoke.jsonl"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)

# T7.38 acceptance: the ONLY frozen historical exception. curator-v5 is
# content-pinned by config-v9 (payloads and historical prompts are
# never rewritten), and its example UUID is the real SMOKE-V8-K2 claim
# id quoted in docs/eval/SMOKE-V8-K2-report.md — precisely the leak
# T7.39 fixed in curator-v6. The exception is pinned to the exact
# (uuid, doc) pairs: any drift (a new leaked uuid, or the docs losing
# the quote) fails the test instead of being silently absorbed.
FROZEN_KNOWN_LEAKS: dict[str, set[tuple[str, str]]] = {
    "curator-v5.md": {
        ("35d5b7ae-4fad-4327-ac38-c26f0eea4306", "docs/eval/SMOKE-V8-K2-report.md"),
    },
}


def _uuids(text: str) -> set[str]:
    return {u.lower() for u in UUID_RE.findall(text)}


def _curator_prompts() -> list[Path]:
    files = sorted((PROMPTS_DIR / "curator").glob("curator-v*.md"))
    assert files, "no curator prompt versions found"
    return files


def _explorer_prompts() -> list[Path]:
    files = sorted((PROMPTS_DIR / "explorer").glob("explorer-v*.md"))
    assert files, "no explorer prompt versions found"
    return files


def _all_prompts() -> list[Path]:
    """T7.50: the guard covers every prompt role — the curator examples
    carry claim ids, the explorer completion examples carry free text;
    both are model-matchable run data."""
    return _curator_prompts() + _explorer_prompts()


def _corpora() -> list[Path]:
    corpora = sorted((DOCS_DIR / "eval").glob("question-set-*.jsonl"))
    assert corpora, "no question-set corpora found in docs/eval"
    if SMOKE_CORPUS.is_file():
        corpora.append(SMOKE_CORPUS)
    return corpora


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _all_prompts(), ids=lambda p: p.name)
def test_prompt_uuids_do_not_intersect_docs(prompt_file: Path) -> None:
    """A prompt example UUID that also appears in docs/ is a leaked
    real run id: on a DB where that claim does not exist the host
    rejects the copied reference (fail-closed, T7.34). T7.50: the
    explorer files are checked too (explorer-v5's examples carry no
    UUIDs — the check passes and pins that for the future)."""
    prompt_uuids = _uuids(prompt_file.read_text(encoding="utf-8"))
    leaked: set[tuple[str, str]] = set()
    for doc in sorted(DOCS_DIR.rglob("*")):
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for uuid in prompt_uuids & _uuids(text):
            leaked.add((uuid, doc.relative_to(REPO_ROOT).as_posix()))
    expected = FROZEN_KNOWN_LEAKS.get(prompt_file.name, set())
    assert leaked == expected, (
        f"{prompt_file.name}: example UUIDs present in docs "
        f"(leaked real run ids): found {sorted(leaked)}, "
        f"documented exception {sorted(expected)}"
    )


def _example_blocks(text: str) -> list[dict]:
    """ALL ```` ```json ```` blocks of the prompt, in file order. T7.43:
    rule 7 carries two examples (the positive reverify and the negative
    ``existing_claim_id: null`` one), so EVERY block is checked —
    ``re.search`` would return the first only."""
    return [json.loads(block) for block in EXAMPLE_JSON.findall(text)]


def _rule7_section(text: str) -> str:
    """The reverify rule (``7. Перепроверка``) — the last rule of the
    prompt; its example ids live in the ``[c:<uuid>]`` context lines
    and the ```` ```json ```` blocks from that line to the end."""
    idx = text.find("7. Перепроверка")
    return text[idx:] if idx != -1 else ""


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_reverify_example_statement_not_in_any_corpus(prompt_file: Path) -> None:
    """The example statements are the only things a model can match its
    own question against. Every statement of every JSON example
    (positive AND negative, T7.43) must name a topic absent from every
    question-set corpus (v1–v4 in the repo, the smoke set when
    present), so no example can ever be recognized as a corpus
    question."""
    text = prompt_file.read_text(encoding="utf-8")
    blocks = _example_blocks(text)
    if not blocks:
        pytest.skip(f"{prompt_file.name}: no rule-7 JSON example")
    corpora = [(c, c.read_text(encoding="utf-8")) for c in _corpora()]
    for i, example in enumerate(blocks):
        for j, claim in enumerate(example.get("claims", [])):
            statement = claim["statement"]
            for corpus, content in corpora:
                assert statement not in content, (
                    f"{prompt_file.name}: example[{i}].claims[{j}] "
                    f"statement {statement!r} appears in {corpus.name}"
                )


# T7.50: the topic of the explorer completion examples (both the
# positive and the negative JSON example of explorer-v5). Deliberately
# a fact absent from every question-set corpus (v1–v4 + the smoke set)
# — the same property the curator examples are pinned to.
EXPLORER_EXAMPLE_TOPIC = "столица Новой Зеландии — Веллингтон"


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _explorer_prompts(), ids=lambda p: p.name)
def test_explorer_example_topics_not_in_any_corpus(prompt_file: Path) -> None:
    """T7.50: the completion examples are free text a model can match
    its own question against. Every free-text field of every JSON
    example (``public_rationale``, ``expected_information``, and the
    free-text ``decision.reason`` of the negative example) and the
    example topic itself must not occur in any question-set corpus
    (v1–v4 in the repo, the smoke set when present) — if the example
    were a corpus question's fact, the model could recognize it in its
    own question and copy the example. The UUID side is covered by
    test_prompt_uuids_do_not_intersect_docs (T7.50: the explorer files
    are checked too)."""
    text = prompt_file.read_text(encoding="utf-8")
    blocks = _example_blocks(text)
    if not blocks:
        pytest.skip(f"{prompt_file.name}: no JSON example")
    corpora = [(c, c.read_text(encoding="utf-8")) for c in _corpora()]
    for i, example in enumerate(blocks):
        fields = [example.get("public_rationale"), example.get("expected_information")]
        decision = example.get("decision")
        if isinstance(decision, dict):
            fields.append(decision.get("reason"))
        for value in fields:
            if not isinstance(value, str):
                continue
            for corpus, content in corpora:
                assert value not in content, (
                    f"{prompt_file.name}: example[{i}] free-text field "
                    f"{value!r} appears in {corpus.name}"
                )
    # the topic itself (a short fact, not the full example sentences):
    # the prompt still carries it (the constant tracks the prompt), and
    # no corpus contains it
    assert EXPLORER_EXAMPLE_TOPIC in text, (
        f"{prompt_file.name}: the pinned example topic "
        f"{EXPLORER_EXAMPLE_TOPIC!r} is no longer in the prompt — "
        f"update EXPLORER_EXAMPLE_TOPIC"
    )
    for corpus, content in corpora:
        assert EXPLORER_EXAMPLE_TOPIC not in content, (
            f"{prompt_file.name}: example topic {EXPLORER_EXAMPLE_TOPIC!r} "
            f"appears in {corpus.name}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_rule7_example_ids_do_not_intersect_docs(prompt_file: Path) -> None:
    """T7.43: the ids of BOTH rule-7 examples must not be real run ids —
    the positive one (full UUID inside the JSON block) and the negative
    one (UUID of the ``[c:…]`` context line). A UUID that also appears
    in docs/ is a leaked id: on a fresh DB the host rejects the copied
    reference and the whole proposal fails (T7.34). File-level check 1
    covers every UUID of the file; this test pins the rule-7 examples
    explicitly."""
    section = _rule7_section(prompt_file.read_text(encoding="utf-8"))
    if not section:
        pytest.skip(f"{prompt_file.name}: no rule 7")
    section_uuids = _uuids(section)
    if not section_uuids:
        pytest.skip(f"{prompt_file.name}: rule 7 carries no UUIDs")
    leaked: set[tuple[str, str]] = set()
    for doc in sorted(DOCS_DIR.rglob("*")):
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for uuid in section_uuids & _uuids(text):
            leaked.add((uuid, doc.relative_to(REPO_ROOT).as_posix()))
    expected = FROZEN_KNOWN_LEAKS.get(prompt_file.name, set())
    assert leaked <= expected, (
        f"{prompt_file.name}: rule-7 example UUIDs present in docs "
        f"(leaked real run ids): {sorted(leaked - expected)}"
    )
