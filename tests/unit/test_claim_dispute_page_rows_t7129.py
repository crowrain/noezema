"""Unit: спор по адресу, которому соответствуют НЕСКОЛЬКО строк `sources` (T7.129 часть 4).

Стендовый случай `.92`: у smart-lab.ru/company/mozgovik/blog/1359553.php три строки
источников (два прочтения http и одно https, разные `content_hash`) — это ОДНА страница в
нескольких прочтениях. Прежний разбор пары считал две строки одним адресом двусмысленностью
и отвечал `dispute_source_not_found` («неоднозначно»), то есть страницу нельзя было оспорить
ни при каком раскладе. Теперь роль держит ВСЕ строки адреса, а коррекция заводится на каждую
(рабочий контур — сценарий `tests/scenario/test_claim_dispute_page_rows_t7129.py`).

Что здесь закреплено:
- все строки одного адреса попадают в роль, порядок детерминирован (по id);
- пары коррекций — произведение строк пересказа на строки первоисточника;
- http/https различие схемы той же страницы не превращает её в другую страницу;
- РАЗНЫЕ адреса по-прежнему не путаются: адрес, которого нет среди источников, — прежний
  отказ, и пересказ не становится первоисточником «из-за соседней строки».
"""

from __future__ import annotations

import uuid

import pytest

from packages.memory import dispute

pytestmark = pytest.mark.unit

SMART_LAB_HTTP = "http://smart-lab.ru/company/mozgovik/blog/1359553.php"
SMART_LAB_HTTPS = "https://smart-lab.ru/company/mozgovik/blog/1359553.php"
ROSBALT_HTTP = "http://rosbalt.ru/news/2026-10-09/mozgovik"


def _source(uri: str, tag: str) -> dispute.ClaimSource:
    return dispute.ClaimSource(source_id=uuid.uuid5(uuid.NAMESPACE_URL, tag), canonical_uri=uri)


# три прочтения одной страницы (разные `content_hash` — разные строки `sources`) + чужая страница
SMART_LAB_ROWS = [
    _source(SMART_LAB_HTTP, "smart-lab-http-1"),
    _source(SMART_LAB_HTTP, "smart-lab-http-2"),
    _source(SMART_LAB_HTTPS, "smart-lab-https"),
]
OTHER_EVIDENCE = _source(ROSBALT_HTTP, "rosbalt")
ANCHOR = _source("https://hh.ru/research/hh-index-september-2026", "hh-anchor")


def test_all_rows_of_one_address_become_one_role() -> None:
    """Три строки одного адреса — одна роль спора, а не отказ «неоднозначно»."""
    pair = dispute.resolve_dispute_pair(
        [*SMART_LAB_ROWS, OTHER_EVIDENCE, ANCHOR],
        primary_uri="https://hh.ru/research/hh-index-september-2026",
        retelling_uri=SMART_LAB_HTTPS,
    )
    assert {source.source_id for source in pair.retelling_rows} == {
        source.source_id for source in SMART_LAB_ROWS
    }
    assert pair.primary.source_id == ANCHOR.source_id
    # порядок детерминирован: один и тот же вызов даёт тот же кортеж
    again = dispute.resolve_dispute_pair(
        [*SMART_LAB_ROWS, OTHER_EVIDENCE, ANCHOR],
        primary_uri="https://hh.ru/research/hh-index-september-2026",
        retelling_uri=SMART_LAB_HTTP,
    )
    assert again.retelling_rows == pair.retelling_rows
    assert list(again.retelling_rows) == sorted(
        SMART_LAB_ROWS, key=lambda source: str(source.source_id)
    )


def test_correction_per_row_not_one_arbitrary_row() -> None:
    """Пар коррекций столько же, сколько версий страницы: оспорена страница целиком."""
    pair = dispute.resolve_dispute_pair(
        [*SMART_LAB_ROWS, OTHER_EVIDENCE, ANCHOR],
        primary_uri="https://hh.ru/research/hh-index-september-2026",
        retelling_uri=SMART_LAB_HTTP,
    )
    assert len(pair.merge_pairs) == 3
    assert {retelling.source_id for retelling, _ in pair.merge_pairs} == {
        source.source_id for source in SMART_LAB_ROWS
    }
    assert {primary.source_id for _, primary in pair.merge_pairs} == {ANCHOR.source_id}
    assert pair.source_ids == {source.source_id for source in SMART_LAB_ROWS} | {ANCHOR.source_id}


def test_other_address_of_the_same_host_stays_a_different_page() -> None:
    """Соседняя страница того же хоста — другой адрес: её строки в роль не попадают."""
    pair = dispute.resolve_dispute_pair(
        [*SMART_LAB_ROWS, OTHER_EVIDENCE, ANCHOR],
        primary_uri=ANCHOR.canonical_uri or "",
        retelling_uri=ROSBALT_HTTP,
    )
    assert [source.source_id for source in pair.retelling_rows] == [OTHER_EVIDENCE.source_id]


def test_absent_address_keeps_the_old_refusal() -> None:
    """Адреса нет среди источников — прежний честный отказ (ничего не угадывается)."""
    with pytest.raises(dispute.DisputeError) as exc:
        dispute.resolve_dispute_pair(
            [*SMART_LAB_ROWS, OTHER_EVIDENCE],
            primary_uri="https://absent.example/hh-index",
            retelling_uri=SMART_LAB_HTTP,
        )
    assert exc.value.code == dispute.REFUSAL_SOURCE_NOT_FOUND
    assert "неоднозначно" not in exc.value.detail
