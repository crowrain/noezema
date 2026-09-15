"""Unit tests: the untrusted extraction profile schema (T5.5, §11.2).

The central boundary: the model proposes verbatim quotes only; the
host generates the provenance (document hash, per-chunk quote hash,
index) and validates verbatimness — a non-verbatim quote is a
host-side rejection, not a model correction.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.schemas.extraction import (
    ExtractionChunk,
    ExtractionError,
    ExtractionReport,
    build_extraction_record,
    extraction_observation_data,
    validate_extraction,
)

pytestmark = pytest.mark.unit

DOCUMENT = "Альфа-бета: 42 узла. Гамма-дельта: 7 узлов. Зета: 1 узел."


def _report(quotes: list[str] | None = None, **kw) -> ExtractionReport:
    if quotes is None:
        quotes = ["42 узла", "7 узлов"]
    base: dict = {
        "public_rationale": "Извлечены числовые факты",
        "chunks": [ExtractionChunk(quote=q) for q in quotes],
    }
    base.update(kw)
    return ExtractionReport(**base)


def test_valid_report_parses():
    report = _report()
    assert len(report.chunks) == 2
    assert report.chunks[0].note is None


def test_schema_is_closed():
    with pytest.raises(ValidationError):
        ExtractionReport.model_validate(
            {"public_rationale": "x", "chunks": [{"quote": "y"}], "grade": "E4"}
        )
    with pytest.raises(ValidationError):
        ExtractionChunk.model_validate({"quote": "y", "confidence": 0.9})
    with pytest.raises(ValidationError):
        ExtractionChunk(quote="")
    with pytest.raises(ValidationError):
        ExtractionChunk(quote="x" * 2001)
    with pytest.raises(ValidationError):
        _report(quotes=None, chunks=[])


def test_verbatim_validation():
    validate_extraction(_report(), DOCUMENT, max_chunks=8)
    with pytest.raises(ExtractionError, match="not verbatim"):
        validate_extraction(_report(quotes=["42 узла", "семь узлов"]), DOCUMENT, max_chunks=8)


def test_budget_validation():
    with pytest.raises(ExtractionError, match="max_chunks"):
        validate_extraction(_report(), DOCUMENT, max_chunks=1)
    with pytest.raises(ExtractionError, match=">= 1"):
        validate_extraction(_report(), DOCUMENT, max_chunks=0)


def test_duplicate_quotes_rejected():
    with pytest.raises(ExtractionError, match="duplicate"):
        validate_extraction(_report(quotes=["42 узла", "42 узла"]), DOCUMENT, max_chunks=8)


def test_record_carrys_host_provenance():
    record = build_extraction_record("doc.txt", "docsha", _report(quotes=["42 узла"], ))
    assert record["path"] == "doc.txt"
    assert record["document_sha256"] == "docsha"
    assert len(record["chunks"]) == 1
    chunk = record["chunks"][0]
    assert chunk["index"] == 0
    assert chunk["quote"] == "42 узла"
    assert chunk["quote_sha256"]  # host-generated
    assert "quote_sha256" not in ExtractionChunk.model_fields  # the model never sends it
    assert "quote_sha256" not in ExtractionReport.model_fields


def test_note_preserved_and_optional():
    report = ExtractionReport(
        public_rationale="r",
        chunks=[ExtractionChunk(quote="42 узла", note="числовой факт")],
    )
    record = build_extraction_record("d", "s", report)
    assert record["chunks"][0]["note"] == "числовой факт"
    record2 = build_extraction_record("d", "s", _report(quotes=["42 узла"]))
    assert "note" not in record2["chunks"][0]


def test_observation_data_never_contains_raw_document():
    record = build_extraction_record("doc.txt", "docsha", _report())
    data = extraction_observation_data(record)
    blob = str(data)
    assert DOCUMENT not in blob  # the raw content is gone
    assert "42 узла" in blob  # but the extracted chunks remain
    assert data["document_sha256"] == "docsha"
    assert data["extraction_profile"] == "untrusted"
    assert "недоверенные данные" in data["note"]
