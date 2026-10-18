"""Unit: чистая логика операторского спора (T7.81, §11.3).

Модуль `packages/memory/dispute.py` специально разделён: разбор пары адресов,
границы причины и текст вопроса на перепроверку — чистые функции, они тестируются
без базы. Рабочий контур (коррекция + каскад + журнал) вызывается из CLI и Command
API и проверяется стендовым сценарием `tests/scenario/test_claim_dispute.py`.

Отдельно закреплено: у каждого кода отказа спора есть подпись в едином словаре
(`apps/web/labels.py`) — новый код без подписи краснит этот тест (T7.64).
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from apps.web import labels as ui_labels
from packages.memory import dispute


def _source(uri: str, tag: str) -> dispute.ClaimSource:
    return dispute.ClaimSource(
        source_id=uuid.uuid5(uuid.NAMESPACE_URL, tag), canonical_uri=uri
    )


# ─── причина спора ────────────────────────────────────────────────────────


def test_reason_is_required_and_bounded() -> None:
    """«Оспорено оператором» без причины — не спор: причина обязательна и видна людям."""
    assert dispute.validate_dispute_reason("  страница повторяет абзацы первоисточника ") == (
        "страница повторяет абзацы первоисточника"
    )
    for bad in ("", "   ", "аб", None, 7):
        with pytest.raises(dispute.DisputeError) as exc:
            dispute.validate_dispute_reason(bad)
        assert exc.value.code == dispute.REFUSAL_REASON_INVALID


def test_reason_length_is_the_command_api_bound() -> None:
    """Потолок причины тот же, что у причины команды оператора: один источник."""
    long_reason = "а" * (dispute.REASON_MAX_CHARS + 1)
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.validate_dispute_reason(long_reason)
    assert exc.value.code == dispute.REFUSAL_REASON_INVALID
    assert len(dispute.validate_dispute_reason("а" * dispute.REASON_MAX_CHARS)) == dispute.REASON_MAX_CHARS


# ─── адрес источника ──────────────────────────────────────────────────────


def test_uri_is_normalized_like_the_source_graph() -> None:
    """Оператор вставляет адрес как его видит: схема, хвостовой слэш и www не важны."""
    assert dispute.normalize_dispute_uri("https://www.rosstat.example/press/") == (
        "https://rosstat.example/press"
    )
    assert dispute.normalize_dispute_uri("rosstat.example/press") == "https://rosstat.example/press"
    assert dispute.normalize_dispute_uri("HTTP://SberCIB.example/Economy/?id=7") == (
        "http://sbercib.example/Economy?id=7"
    )
    assert dispute.normalize_dispute_uri("https://x.example:443/a") == "https://x.example/a"
    for bad in ("", "  ", "https://", None):
        with pytest.raises(dispute.DisputeError) as exc:
            dispute.normalize_dispute_uri(bad)
        assert exc.value.code == dispute.REFUSAL_URI_INVALID


def test_pair_resolves_only_among_the_sources_of_this_claim() -> None:
    """Fail-closed: адреса берутся из улик именно этого утверждения (§11.3)."""
    sources = [
        _source("https://rosstat.example/press-1409", "primary"),
        _source("https://sbercib.example/inflation-5-6", "retelling"),
    ]
    pair = dispute.resolve_dispute_pair(
        sources,
        primary_uri="https://rosstat.example/press-1409",
        retelling_uri="https://sbercib.example/inflation-5-6",
    )
    assert pair.primary.source_id == sources[0].source_id
    assert pair.retelling.source_id == sources[1].source_id

    # адрес, которого нет среди источников утверждения
    for kwargs in (
        {"primary_uri": "https://rosstat.example/press-1409", "retelling_uri": "https://ria.ru/x"},
        {"primary_uri": "https://vedomosti.example/y", "retelling_uri": "https://sbercib.example/inflation-5-6"},
    ):
        with pytest.raises(dispute.DisputeError) as exc:
            dispute.resolve_dispute_pair(sources, **kwargs)
        assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND

    # один и тот же источник нельзя объявить пересказом самого себя
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            sources,
            primary_uri="https://rosstat.example/press-1409",
            retelling_uri="http://www.rosstat.example/press-1409/",
        )
    assert exc.value.code == dispute.REFUSAL_SAME_SOURCE


def test_ambiguous_address_is_refused_not_guessed() -> None:
    """Два источника с одним адресом — отказ, а не произвольный выбор оператора."""
    dupes = [_source("https://same.example/page", "a"), _source("https://same.example/page", "b")]
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            dupes, primary_uri="https://same.example/page", retelling_uri="https://same.example/page"
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND


def test_source_without_canonical_uri_is_not_a_target() -> None:
    """Источник без адреса (локальное наблюдение) спором не покрывается."""
    anonymous = dispute.ClaimSource(source_id=uuid.uuid4(), canonical_uri=None)
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            [anonymous], primary_uri="https://x.example/a", retelling_uri="https://y.example/b"
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND


# ─── вопрос на перепроверку ───────────────────────────────────────────────


def test_recheck_question_quotes_the_claim_statement() -> None:
    """Ловушка T7.73: лексика FTS обязана достать якорь, поэтому statement цитируется."""
    statement = "Инфляция в России по данным за сентябрь 2026 года составила около 5,6 процента"
    text = dispute.recheck_question_text(statement)
    assert text.startswith(dispute.QUESTION_TEXT_PREFIX)
    assert statement in text
    # детерминированно: повтор спора даёт тот же текст (дедуп приёма вопросов)
    assert text == dispute.recheck_question_text(statement)


def test_recheck_question_fits_the_intake_bound_and_collapses_whitespace() -> None:
    from packages.domain.services.question_intake import MAX_QUESTION_TEXT_CHARS

    long_statement = "  слово   " * 400
    text = dispute.recheck_question_text(long_statement)
    assert len(text) <= MAX_QUESTION_TEXT_CHARS
    assert "   " not in text
    # пустое утверждение не рождает вопрос без якоря: префикс остаётся подписанным
    assert dispute.recheck_question_text("").startswith(dispute.QUESTION_TEXT_PREFIX)


# ─── отказы подписаны словарём (краснота по построению) ───────────────────


def _refusal_codes_from_code() -> list[str]:
    """Коды отказа вынуты из самого модуля, а не из списка теста (T7.64)."""
    source = inspect.getsource(dispute)
    return sorted(
        {
            value
            for name, value in vars(dispute).items()
            if name.startswith("REFUSAL_") and isinstance(value, str)
        }
        | {
            token
            for token in _quoted_reason_literals(source)
            if token.startswith("dispute_")
        }
    )


def _quoted_reason_literals(source: str) -> set[str]:
    import re

    return set(re.findall(r'=\s*"(dispute_[a-z_]+)"', source))


def test_every_dispute_refusal_has_a_label() -> None:
    codes = _refusal_codes_from_code()
    assert codes, "модуль спора обязан называть коды отказов константами"
    for code in codes:
        entry = ui_labels.describe_refusal(code)
        assert entry["hint"], f"отказ {code} без подписи: словарь его не знает"
        assert entry["label"] and entry["action"]


def test_dispute_labels_are_the_same_dictionary_the_ui_uses() -> None:
    """Витрина рисует состояние спора только подписями из единого словаря."""
    for category, values in (
        ("graph_correction_kind", {"merge", "split"}),
        ("dispute_state", {"disputed", "withdrawn"}),
        ("dispute_actor", {dispute.ACTOR_WEB, dispute.ACTOR_HOSTCTL}),
    ):
        for value in values:
            entry = ui_labels.describe(category, value)
            assert entry["hint"], f"{category}/{value} без подписи"
            assert entry["label"] != value  # запасной путь = выдуманной подписи нет


def test_dispute_uses_no_new_enum_value_and_no_operator_head_writer() -> None:
    """Стоп-критерий T7.81: спор не вводит новых enum-значений домена и не пишет
    голову утверждения «от имени оператора» (актор головы — закрытый CHECK)."""
    from pathlib import Path

    from packages.domain.models.enums import OperatorCommandType

    command_types = {e.value for e in OperatorCommandType}
    assert "dispute" not in " ".join(command_types)

    # актор головы утверждения — закрытый список миграции; «оператора» там нет,
    # поэтому спор и идёт через каскад графа источников, а не прямым изменением головы
    migration = Path("migrations/versions/0022_session_admissions.py").read_text(encoding="utf-8")
    prepared_by_block = migration.split("claim_assessment_heads_prepared_by_check")[1]
    assert "'operator" not in prepared_by_block.split(")", 1)[0], prepared_by_block[:400]

    source = inspect.getsource(dispute)
    # единственная запись head-таблицы в модуле — через каскад §11.3, не UPDATE'ом
    assert "UPDATE claim_assessment_heads" not in source
    assert "apply_source_graph_change" in source


def test_cancel_keeps_the_correction_row_instead_of_deleting_it() -> None:
    """Отмена спора обратима и не стирает историю: `valid = false`, строка остаётся."""
    source = inspect.getsource(dispute.cancel_dispute)
    assert "correction.valid = False" in source
    assert "delete(" not in source.lower().replace("del_", "")
