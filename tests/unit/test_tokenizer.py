"""Unit: deterministic token estimator + budget math (T3.8, §5.4.1)."""

from __future__ import annotations

from packages.cognition.tokenizer import (
    TOKENIZER_FINGERPRINT,
    TokenBudgets,
    estimate_tokens,
)


def test_estimate_tokens_is_deterministic():
    assert estimate_tokens("hello world") == estimate_tokens("hello world")
    assert estimate_tokens("") == 0


def test_estimate_tokens_counts_words_and_symbols():
    assert estimate_tokens("a b c") == 3
    # symbols count as tokens
    assert estimate_tokens("a+b") == 3
    assert estimate_tokens("one") == 1


def test_input_budget_math():
    b = TokenBudgets(
        {"protocol": 4096},
        context_window=32768,
        backend_context_limit=32768,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
    )
    assert b.input_budget == 26624


def test_input_budget_uses_min_of_window_and_backend():
    b = TokenBudgets(
        {"protocol": 100},
        context_window=32768,
        backend_context_limit=10000,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
    )
    assert b.input_budget == 10000 - 4096 - 2048


def test_validate_rejects_over_budget_sum():
    b = TokenBudgets(
        {"a": 30000, "b": 100},
        context_window=32768,
        backend_context_limit=32768,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
    )
    assert b.validate()  # non-empty problems


def test_validate_accepts_fitting_sum():
    b = TokenBudgets(
        {"a": 1000, "b": 1000},
        context_window=32768,
        backend_context_limit=32768,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
    )
    assert b.validate() == []


def test_validate_rejects_negative_limit():
    b = TokenBudgets(
        {"a": -5},
        context_window=32768,
        backend_context_limit=32768,
        max_output_tokens=4096,
        safety_margin_tokens=2048,
    )
    assert any("negative" in p for p in b.validate())


def test_fingerprint_is_stable():
    assert TOKENIZER_FINGERPRINT == "word-symbol-v1"
