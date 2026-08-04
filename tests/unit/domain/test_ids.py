"""Tests for trusted-host typed identifiers."""

from packages.domain import ActionId, IdempotencyKey, SessionId


def test_identifier_types_are_runtime_distinct_and_unique() -> None:
    first_session = SessionId.new()
    second_session = SessionId.new()
    action = ActionId.new()

    assert type(first_session) is SessionId
    assert type(action) is ActionId
    assert first_session != second_session
    assert str(first_session) != str(action)


def test_identifier_round_trips_as_one_json_string() -> None:
    key = IdempotencyKey.new()

    restored = IdempotencyKey.model_validate_json(key.model_dump_json())

    assert restored == key
    assert key.model_dump_json() == f'"{key}"'
