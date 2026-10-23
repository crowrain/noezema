"""T7.133a (часть 3): бюджет времени разбора PDF.

Разбор идёт постранично и проверяет срок между страницами; при исчерпании текст
усекается с меткой `pdf-truncated:time` в цепочке (та же форма, что
`pdf-truncated:pages` / `pdf-truncated:chars`). Часы подставные — детерминированный
разбор без гонки со стенными часами. Красный на `ae930dd`: константы
`PDF_MAX_SECONDS` и именованных аргументов разбора там нет.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from apps.research_proxy.normalization import (
    PDF_MAX_SECONDS,
    PDF_PAGE_SEPARATOR,
    PDF_TRUNCATION_MARKER,
    _normalize_pdf,
    normalize_content,
)
from packages.domain.services.lease import DEFAULT_LEASE_TTL

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7133"
PDFS = ("CPD-2026-9.pdf", "employment-0926.pdf", "hh-monthly-report.pdf")


class _AdvancingClock:
    """Подставные часы: каждый следующий вызов возвращает время на step позже."""

    def __init__(self, step: float) -> None:
        self.step = step
        self._now = 0.0

    def __call__(self) -> float:
        value = self._now
        self._now += self.step
        return value


def test_pdf_time_budget_constant_is_closed_and_inside_lease() -> None:
    """Бюджет закрыт (20 с) и заведомо короче аренды сессии: во время исполнения
    инструмента аренда не продлевается, один разбор обязан укладываться в TTL."""
    assert PDF_MAX_SECONDS == 20
    assert DEFAULT_LEASE_TTL.total_seconds() > PDF_MAX_SECONDS


def test_pdf_time_budget_exhausted_truncates_with_time_label() -> None:
    """Часы, шагнувшие за бюджет после первой страницы: разбор остановлен, текст
    усечён уже разобранным, в цепочке — `pdf-truncated:time`."""
    data = (FIXTURES / "employment-0926.pdf").read_bytes()
    full = normalize_content("application/pdf", data)
    assert PDF_PAGE_SEPARATOR.format(page=2) in full.text  # полный разбор — все страницы

    result = _normalize_pdf(data, budget_seconds=0.5, clock=_AdvancingClock(1.0))

    assert result.text is not None
    assert result.transform_chain[:3] == ("fetch", "normalize:pdf-text", "pdf-pages:4")
    assert "pdf-truncated:time:0.5" in result.transform_chain
    assert result.text.endswith(PDF_TRUNCATION_MARKER.strip())
    # усечение честное: разбор остановлен ПОСЛЕ страницы 1 (её текст сохранён)
    assert result.text.startswith(full.text[:100])
    assert PDF_PAGE_SEPARATOR.format(page=2) not in result.text
    # полный текст = усечённый до маркера + остальное (ничего не переврано)
    head = result.text[: -len(PDF_TRUNCATION_MARKER)]
    assert full.text.startswith(head)
    assert result.text_sha256 == hashlib.sha256(result.text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", PDFS)
def test_pdf_time_budget_not_exceeded_keeps_result_byte_identical(name: str) -> None:
    """Часы стоят — срок не исчерпан: текст, цепочка и хеш байт в байт прежние,
    метки времени в цепочке нет."""
    data = (FIXTURES / name).read_bytes()
    baseline = normalize_content("application/pdf", data)
    result = _normalize_pdf(data, budget_seconds=1_000_000.0, clock=lambda: 0.0)
    assert result == baseline
    assert not any(label.startswith("pdf-truncated:time") for label in baseline.transform_chain)
