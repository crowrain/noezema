"""T7.133: PDF -> текст через pypdf и честные причины отказа нормализации.

Фикстуры — настоящие страницы стенда (срез менеджера 16–22.10, каталог
fixtures-t7133, только чтение): Росстат (37.rosstat.gov.ru employment_0926),
Банк России (CPD_2026-9), ежемесячный отчёт hh (stats.hh.ru, PDF по API-адресу
без расширения), заглушки защиты от ботов frankmedia/bcs-express/rusbonds/
banki.ru. Синтетические PDF собираются минимальным генератором ниже (Helvetica,
ASCII) — пределы проверяются на документах, которых в природе нет.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from pathlib import Path

import pytest
from pypdf import PdfWriter

from apps.orchestrator.host_availability import classify_fetch_error
from apps.research_proxy.normalization import (
    PDF_MAX_PAGES,
    PDF_MAX_TEXT_CHARS,
    PDF_TRUNCATION_MARKER,
    REFUSAL_BOT_CHALLENGE_STUB,
    REFUSAL_EMPTY_PAGE,
    REFUSAL_PDF_NO_TEXT_LAYER,
    REFUSAL_PDF_UNREADABLE,
    REFUSAL_UNSUPPORTED_FORMAT,
    normalize_content,
    refusal_observation_message,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "t7133"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _make_pdf(page_texts: Sequence[str]) -> bytes:
    """Минимальный многостраничный PDF (один текст на страницу, Helvetica, ASCII).

    Ровно ровно тот синтаксис, который читает pypdf: каталог, дерево страниц,
    шрифт, страницы с потоком содержимого `BT … Tj ET`, xref-таблица. Нужен,
    чтобы проверять ПРЕДЕЛЫ (сотни страниц, миллион знаков) без гигантских
    фикстур в репозитории.
    """
    n = len(page_texts)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []

    def add(obj: bytes) -> None:
        offsets.append(len(out))
        out.extend(obj)
        out.extend(b"\n")

    add(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj")
    add(f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {n} >>\nendobj".encode())
    add(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj")
    for index, text in enumerate(page_texts):
        page_num = 4 + 2 * index
        content_num = page_num + 1
        stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
        add(
            (
                f"{page_num} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>\nendobj"
            ).encode()
        )
        add(
            f"{content_num} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj"
        )
    xref_pos = len(out)
    size = len(offsets) + 1
    out.extend(f"xref\n0 {size}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


# ─── (а) реальные PDF фикстуры дают непустой текст с ожидаемыми фрагментами ──────


def test_rosstat_employment_pdf_becomes_text() -> None:
    result = normalize_content("application/pdf", _fixture("employment-0926.pdf"))
    assert result.refusal is None
    assert result.transform_chain == ("fetch", "normalize:pdf-text", "pdf-pages:4")
    assert result.text is not None
    assert "ЗАНЯТОСТЬ И БЕЗРАБОТИЦА ВО II КВАРТАЛЕ 2026" in result.text
    assert "456 тыс" in result.text
    assert result.text_sha256 == hashlib.sha256(result.text.encode("utf-8")).hexdigest()


def test_cbr_cpd_pdf_becomes_text() -> None:
    result = normalize_content("application/pdf", _fixture("CPD-2026-9.pdf"))
    assert result.refusal is None
    assert result.transform_chain == ("fetch", "normalize:pdf-text", "pdf-pages:37")
    assert result.text is not None
    assert "ИНФЛЯЦИЯ" in result.text
    assert "6,35%" in result.text


def test_hh_report_pdf_is_read_through_wrong_headers() -> None:
    """stats.hh.ru отдаёт PDF по API-адресу: подпись тела важнее заголовков —
    и когда типа нет вовсе, и когда он врал бы («text/html»)."""
    data = _fixture("hh-monthly-report.pdf")
    for declared in (None, "", "text/html; charset=utf-8"):
        result = normalize_content(declared, data)
        assert result.refusal is None
        assert result.transform_chain == ("fetch", "normalize:pdf-text", "pdf-pages:24")
        assert result.text is not None
        assert "hh.индекс" in result.text
        assert "8,5" in result.text


def test_pdf_pages_are_separated() -> None:
    result = normalize_content("application/pdf", _fixture("employment-0926.pdf"))
    assert result.text is not None
    assert result.text.count("<<<PAGE ") == 3  # 4 страницы — 3 разделителя
    assert "<<<PAGE 2>>>" in result.text


# ─── (б) пределы и честные отказы PDF ────────────────────────────────────────────


def test_pdf_page_limit_truncates_with_marker() -> None:
    data = _make_pdf([f"STRANITSA {i}" for i in range(1, PDF_MAX_PAGES + 2)])
    result = normalize_content("application/pdf", data)
    assert result.refusal is None
    assert result.text is not None
    assert f"pdf-truncated:pages:{PDF_MAX_PAGES}" in result.transform_chain
    assert "pdf-pages:401" in result.transform_chain
    assert "STRANITSA 400" in result.text
    assert "STRANITSA 401" not in result.text
    assert result.text.endswith(PDF_TRUNCATION_MARKER.strip())


def test_pdf_text_limit_truncates_with_marker() -> None:
    data = _make_pdf(["X" * (PDF_MAX_TEXT_CHARS + 100_000)])
    result = normalize_content("application/pdf", data)
    assert result.refusal is None
    assert result.text is not None
    assert f"pdf-truncated:chars:{PDF_MAX_TEXT_CHARS}" in result.transform_chain
    assert result.text.startswith("XXXX")
    assert result.text.endswith(PDF_TRUNCATION_MARKER.strip())
    assert len(result.text) <= PDF_MAX_TEXT_CHARS + len(PDF_TRUNCATION_MARKER)


def test_broken_pdf_refuses_without_exception() -> None:
    result = normalize_content("application/pdf", "%PDF-1.4 мусор без xref".encode())
    assert result.text is None
    assert result.text_sha256 is None
    assert result.transform_chain == ("fetch",)
    assert result.refusal == REFUSAL_PDF_UNREADABLE


def test_declared_pdf_that_is_not_pdf_refuses_honestly() -> None:
    """rusbonds отдаёт HTML-заглушку по адресу .pdf: объявленный тип не должен
    утаскивать нормализацию в падение — честный отказ с причиной."""
    result = normalize_content("application/pdf", b"<!DOCTYPE html><html><body>x</body></html>")
    assert result.text is None
    assert result.refusal == REFUSAL_PDF_UNREADABLE


def test_password_protected_pdf_refuses_honestly() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    result = normalize_content("application/pdf", buffer.getvalue())
    assert result.text is None
    assert result.refusal == REFUSAL_PDF_UNREADABLE


def test_scanned_pdf_without_text_layer_has_its_own_reason() -> None:
    result = normalize_content("application/pdf", _make_pdf(["", "", ""]))
    assert result.text is None
    assert result.transform_chain == ("fetch",)
    assert result.refusal == REFUSAL_PDF_NO_TEXT_LAYER


# ─── (в) заглушки защиты от ботов против настоящей пустой страницы ────────────────


@pytest.mark.parametrize(
    "fixture_name",
    ["frankmedia-stub.html", "bcs-express-stub.html", "rusbonds-stub.html", "banki-stub.html"],
)
def test_bot_challenge_stubs_are_named_stubs(fixture_name: str) -> None:
    """Все четыре стендовые заглушки дают пустой текст — ровно как раньше (тот же
    sha пустой строки, та же цепочка), но теперь с закрытым признаком заглушки."""
    result = normalize_content("text/html; charset=UTF-8", _fixture(fixture_name))
    assert result.text == ""
    assert result.text_sha256 == EMPTY_SHA256
    assert result.transform_chain == ("fetch", "normalize:html-text")
    assert result.refusal == REFUSAL_BOT_CHALLENGE_STUB


def test_blank_html_page_is_empty_page_not_a_stub() -> None:
    result = normalize_content("text/html", b"<html><head></head><body> </body></html>")
    assert result.text == ""
    assert result.refusal == REFUSAL_EMPTY_PAGE


def test_empty_plain_text_is_empty_page() -> None:
    result = normalize_content("text/plain", b"   \n ")
    assert result.text == ""
    assert result.refusal == REFUSAL_EMPTY_PAGE


# ─── (г) сообщения: разные, понятные и не помечают хост ──────────────────────────


def test_unsupported_type_reports_reason() -> None:
    docx_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    result = normalize_content(docx_type, b"PK\x03\x04...")
    assert result.text is None
    assert result.transform_chain == ("fetch",)
    assert result.refusal == REFUSAL_UNSUPPORTED_FORMAT


def test_refusal_messages_are_distinct_and_honest() -> None:
    messages = {
        code: refusal_observation_message(code, "application/pdf")
        for code in (
            REFUSAL_UNSUPPORTED_FORMAT,
            REFUSAL_PDF_UNREADABLE,
            REFUSAL_PDF_NO_TEXT_LAYER,
            REFUSAL_BOT_CHALLENGE_STUB,
            REFUSAL_EMPTY_PAGE,
        )
    }
    assert len(set(messages.values())) == 5
    assert "не поддерживается нормализатором" in messages[REFUSAL_UNSUPPORTED_FORMAT]
    assert "application/pdf" in messages[REFUSAL_UNSUPPORTED_FORMAT]
    assert "PDF без текстового слоя" in messages[REFUSAL_PDF_NO_TEXT_LAYER]
    assert "заглушка защиты от ботов" in messages[REFUSAL_BOT_CHALLENGE_STUB]
    assert "не возвращайся к этому сайту в этой сессии" in messages[REFUSAL_BOT_CHALLENGE_STUB]
    assert messages[REFUSAL_EMPTY_PAGE].startswith("пустая страница")
    assert "PDF" in messages[REFUSAL_PDF_UNREADABLE]
    # конверт без причины (до T7.133) — прежняя формулировка, тесты её сохраняют
    assert refusal_observation_message(None, None) == "no normalized text available for this content"


def test_refusal_messages_do_not_mark_the_host() -> None:
    """Свойство страницы, а не отказ сайта: ни одно новое сообщение не должно
    закрывать хост для чтения (классификатор T7.111 не тронут)."""
    for code in (
        REFUSAL_UNSUPPORTED_FORMAT,
        REFUSAL_PDF_UNREADABLE,
        REFUSAL_PDF_NO_TEXT_LAYER,
        REFUSAL_BOT_CHALLENGE_STUB,
        REFUSAL_EMPTY_PAGE,
    ):
        assert classify_fetch_error(refusal_observation_message(code, "application/pdf")) is None


# ─── (е) регрессия: HTML/plain/JSON — тексты и хеши байт в байт прежние ──────────
#
# Значения вычислены кодом aa94dce (красность/совпадение доказаны прогоном этих
# тестов во временном worktree на aa94dce): PDF-ветка и причины отказа не сдвинули
# ни одного байта в существующих ветвях.

_PRE_T7133_PINS: dict[str, tuple[str | None, tuple[str, ...], str]] = {
    # имя: (content_type, цепочка, хеш нормализованного текста)
    "html_utf8": (
        "text/html; charset=utf-8",
        ("fetch", "normalize:html-text"),
        "da8802e36177a73e28770ce9d22f1803a3fc84dc657adb53629d5cf59f2c15e4",
    ),
    "html_win1251": (
        "text/html",
        ("fetch", "normalize:html-text", "decode:windows-1251"),
        "395957df698d323f5be2123c251d3bed3f23a3ef3844f6a3a33e76fef414176c",
    ),
    "plain": (
        "text/plain; charset=utf-8",
        ("fetch", "normalize:decode-utf8"),
        "51ffbf487324c3ec1b0b8e4a9d1c34b743f1bd1fb19eb3580181ce1861800d37",
    ),
    "json": (
        "application/json",
        ("fetch", "normalize:json-canonical"),
        "148afffd2d2ff760c09cc96114d889650f73aaa9a44de7c3ddcea172939d8fde",
    ),
    "json_broken": (
        "application/json",
        ("fetch", "normalize:json-canonical"),
        "02dea0c63386824df43613991661d093aa9489863e77506bc4679e86587a6104",
    ),
}


def _pre_t7133_input(name: str) -> tuple[str | None, bytes]:
    if name == "html_utf8":
        return (
            "text/html; charset=utf-8",
            "<html><head><title>Отчёт</title><script>х=1;</script></head><body>"
            "<h1>Заголовок</h1><p>Текст с числом 7,8 %.</p></body></html>".encode(),
        )
    if name == "html_win1251":
        return (
            "text/html",
            b'<html><head><meta charset="windows-1251"></head><body><p>'
            + "Индекс 9,6 пункта".encode("cp1251")
            + b"</p></body></html>",
        )
    if name == "plain":
        return "text/plain; charset=utf-8", ("простой текст\n" "вторая строка").encode()
    if name == "json":
        return "application/json", rb'{"b": 1, "a": "\u044d"}'
    return "application/json", '{"не json'.encode()


@pytest.mark.parametrize("pin_name", sorted(_PRE_T7133_PINS))
def test_html_plain_json_are_byte_identical_to_pre_t7133(pin_name: str) -> None:
    content_type, chain, sha = _PRE_T7133_PINS[pin_name]
    declared, data = _pre_t7133_input(pin_name)
    assert declared == content_type
    result = normalize_content(declared, data)
    assert result.transform_chain == chain
    assert result.text_sha256 == sha
    assert result.refusal is None
