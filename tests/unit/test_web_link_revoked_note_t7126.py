"""Unit: витрина называет отозванную связь (остаток ADR-0039 §6, закрыт в T7.126 части в).

Решение пользователя 2026-10-22: на карточке обязана быть строка «связь отозвана оператором:
<причина> (отзыв <id>, <дата>)», и отозванная связь не считается ни уликой, ни группой.
Здесь закреплено то, что не требует базы: сама строка, её отсутствие там, где отзыва нет,
фильтр счётчика подтверждений карточки ответа и разметка обеих карточек. Следствие отзыва
для оценки и групп на живом посеве (JSON карточки и «происхождения») — сценарный тест
`tests/scenario/test_web_link_revocation_card_t7126.py`.

Fail-closed: `link_revocation_view` читает колонки JOIN'а действующего отзыва. Строка без
этих колонок (другой запрос, другой вызов) означает «отзыва нет», а не «отзыв неизвестен»:
витрина не додумывает скрытый отзыв — ровно как правило производителя T7.87 молчит без
записанных ролей.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from apps.web.answer import evidence_kind_counts
from apps.web.knowledge import (
    LINK_REVOCATION_FACT_COLUMNS,
    REVOKED_LINK_LEAD,
    link_is_revoked,
    link_revocation_join,
    link_revocation_view,
    revoked_link_note,
    revoked_link_notes,
)

pytestmark = pytest.mark.unit

REVOCATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
REASON = "на странице «решение ЦБ по ключевой ставке» значения hh-индекса нет"
REVOKED_AT = dt.datetime(2026, 10, 21, 14, 5, tzinfo=dt.UTC)
SBERCIB_URI = "https://www.sbercib.ru/publication/reshenie-banka-rossii-po-klyuchevoi-stavke-ot-1"


def _revoked_row(**over: object) -> dict[str, object]:
    """Строка улики с колонками `link_revocation_join`: связь снята действующим отзывом."""
    row: dict[str, object] = {
        "claim_id": "00000000-0000-4000-8000-000000000001",
        "relation": "supports",
        "evidence_kind": "source_assertion",
        "source_uri": SBERCIB_URI,
        "revocation_id": REVOCATION_ID,
        "revocation_reason": REASON,
        "revocation_actor": "hostctl",
        "revoked_at": REVOKED_AT,
    }
    row.update(over)
    return row


def test_the_line_names_the_reason_the_revocation_id_and_its_date() -> None:
    """Формат строки задан решением пользователя: причина, id записи отзыва и дата записи."""
    line = revoked_link_note(link_revocation_view(_revoked_row()))
    assert line == f"{REVOKED_LINK_LEAD}: {REASON} (отзыв {REVOCATION_ID}, 2026-10-21)", line


def test_notes_follow_the_order_of_links_and_name_only_revoked_ones() -> None:
    live = {"relation": "supports", "evidence_kind": "source_assertion", "revocation_id": None}
    notes = revoked_link_notes([live, _revoked_row(), _revoked_row(revocation_id=uuid.uuid4())])
    assert len(notes) == 2, notes
    assert all(note.startswith(f"{REVOKED_LINK_LEAD}: {REASON} (отзыв ") for note in notes)
    assert notes[0].endswith(f"{REVOCATION_ID}, 2026-10-21)")


def test_a_revocation_with_blank_reason_is_named_as_blank() -> None:
    """Причина отзыва — обязательная колонка (`btrim(reason) <> ''`), но витрина обязана
    назвать строку и при пустом тексте: молчание было бы ложью о том, что решения нет."""
    line = revoked_link_note(link_revocation_view(_revoked_row(revocation_reason="   ")))
    assert line is not None and "причина не названа" in line, line


def test_a_link_without_revocation_columns_is_not_a_revoked_link() -> None:
    plain = {"relation": "supports", "evidence_kind": "source_assertion", "source_uri": SBERCIB_URI}
    assert link_revocation_view(plain) is None
    assert link_is_revoked(plain) is False
    assert revoked_link_note(None) is None
    assert revoked_link_notes([plain]) == []


def test_the_answer_card_counter_does_not_count_a_revoked_support() -> None:
    """Было: отозванная связь попадала в «чем подтверждено» как обычная улика. Стало: нет."""
    live = {"relation": "supports", "evidence_kind": "source_assertion", "revocation_id": None}
    counter = {"relation": "counters", "evidence_kind": "source_assertion", "revocation_id": None}
    assert evidence_kind_counts([live, _revoked_row(), counter]) == {"source_assertion": 1}


def test_both_cards_print_the_server_line_and_skip_the_revoked_uri() -> None:
    """JS карточек печатает серверный текст и не подставляет адрес отозванной связи в форму
    первоисточника (прежний разбор формы — T7.114, `primaryUris`)."""
    from apps.web.api import _ANSWER_HTML, _CLAIM_HTML

    assert "claim.revoked_links" in _ANSWER_HTML
    assert "d.revoked_links" in _CLAIM_HTML
    assert "e.link_revocation.note" in _CLAIM_HTML
    assert "if(e.link_revocation) continue;" in _CLAIM_HTML


def test_one_sql_fragment_serves_every_card_of_the_claim() -> None:
    """Карточка утверждения, «происхождение», список знаний и карточка ответа читают один текст
    JOIN'а: расхождение витрин в том, что считать уликой — тот же риск, что расхождение бейджей
    (ADR-0035 §4 п.3). Действующий отзыв — `cancelled_at IS NULL`, тот же признак, что у
    фильтра входа группировки (ADR-0039 §5)."""
    join = link_revocation_join("e.id")
    assert "evidence_link_revocations r" in join
    assert "r.evidence_id = e.id" in join
    assert "r.cancelled_at IS NULL" in join
    for column in ("revocation_id", "revocation_reason", "revocation_actor", "revoked_at"):
        assert column in LINK_REVOCATION_FACT_COLUMNS
