"""T7.39: prompt examples must not carry real run data.

T7.38 acceptance: the reverify example of curator-v5 used a REAL claim
id (``35d5b7ae-…``, SMOKE-V8-K2) and that run's real fact and ``as_of``.
On the next smoke on a FRESH DB the host would reject the copied
reference and — T7.34 — an unresolved reference rejects the WHOLE
proposal: gate 5 reads 0 again, and "the model does not use reverify"
is indistinguishable from "the model copied the example".

General protection (any future prompt version is covered):

1. no full UUID that appears in ANY prompt file may also appear in
   ``docs/`` — the reports and analyses there contain the real run ids,
   so an intersection is a leaked real id. Sole exception: curator-v5,
   frozen (content-pinned by config-v9) with its known leak pinned to
   the exact (uuid, doc) pairs — see ``FROZEN_KNOWN_LEAKS``;
2. the reverify example statement of every curator prompt (parsed out
   of the rule-7 ```` ```json ```` block) must not occur in any
   question-set corpus — if the example were a corpus question's fact,
   the model could recognize the example in its own question and copy
   the example's ids/``as_of``.
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


def _corpora() -> list[Path]:
    corpora = sorted((DOCS_DIR / "eval").glob("question-set-*.jsonl"))
    assert corpora, "no question-set corpora found in docs/eval"
    if SMOKE_CORPUS.is_file():
        corpora.append(SMOKE_CORPUS)
    return corpora


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_prompt_uuids_do_not_intersect_docs(prompt_file: Path) -> None:
    """A prompt example UUID that also appears in docs/ is a leaked
    real run id: on a DB where that claim does not exist the host
    rejects the copied reference (fail-closed, T7.34)."""
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


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_reverify_example_statement_not_in_any_corpus(prompt_file: Path) -> None:
    """The example statement is the only thing a model can match its
    own question against. It must name a topic absent from every
    question-set corpus (v1–v4 in the repo, the smoke set when
    present), so the example can never be recognized as a corpus
    question."""
    text = prompt_file.read_text(encoding="utf-8")
    m = EXAMPLE_JSON.search(text)
    if m is None:
        pytest.skip(f"{prompt_file.name}: no rule-7 JSON example")
    example = json.loads(m.group(1))
    statement = example["claims"][0]["statement"]
    for corpus in _corpora():
        assert statement not in corpus.read_text(encoding="utf-8"), (
            f"{prompt_file.name}: example statement {statement!r} "
            f"appears in {corpus.name}"
        )
