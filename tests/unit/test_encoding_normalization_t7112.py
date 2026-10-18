"""T7.112 (а1): закрытый порядок объявления кодировки при нормализации (ADR-0029 дополнение).

Закрытый декларативный порядок: параметр charset из Content-Type -> BOM -> HTML `<meta>`
charset в первых META_CHARSET_WINDOW_BYTES байтов тела -> UTF-8 errors="replace".
Статистического угадывания нет. Фактически применённая кодировка записывается в цепочку
преобразований (`decode:<имя>`), неизвестное объявленное имя получает пометку
(`decode:unknown:<имя>`) и декодируется как UTF-8. Страницы, дошедшие до обычного UTF-8,
сохранают прежние цепочки, текст и хеши байт в байт (случай finmarket.ru/news/6727171).
"""

from __future__ import annotations

import codecs
import hashlib

import pytest

from apps.research_proxy.normalization import (
    META_CHARSET_WINDOW_BYTES,
    detect_declared_encoding,
    normalize_content,
)

pytestmark = pytest.mark.unit

CYR = "Уровень безработицы составил 2,1–2,2%."


# ─── т1: закрытый порядок и запись применённой кодировки ────────────────────────────────


def test_header_charset_wins_over_bom_and_meta() -> None:
    # заголовок объявляет koi8-r; тело содержит и UTF-8 BOM, и meta windows-1251 —
    # оба сильнее отсутствующего заголовка, но слабее заголовка: решает заголовок
    koi_text = "Уровень безработицы в России составил 2,2%."  # без тире: koi8-r его не несёт
    data = (
        b"\xef\xbb\xbf<html><head><meta charset=\"windows-1251\"></head><body>"
        + koi_text.encode("koi8-r")
        + b"</body></html>"
    )
    res = normalize_content("text/html; charset=koi8-r", data)
    assert res.text is not None and "безработицы" in res.text
    assert res.transform_chain == ("fetch", "normalize:html-text", "decode:koi8-r")


def test_bom_decides_when_no_header_charset() -> None:
    # BOM решает, когда charset в заголовке нет (codec utf-16 сам разбирает порядок байт)
    data = (
        codecs.BOM_UTF16_LE
        + "<html><body>".encode("utf-16-le")
        + CYR.encode("utf-16-le")
        + b"</body></html>"
    )
    res = normalize_content("text/html", data)
    assert res.text is not None and "безработицы" in res.text
    assert res.transform_chain == ("fetch", "normalize:html-text", "decode:utf-16")


def test_meta_charset_decides_within_the_window() -> None:
    # случай finmarket.ru/news/6727171: charset только в meta-объявлении тела
    html = (
        b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251"></head><body>'
        + CYR.encode("windows-1251")
        + b"</body></html>"
    )
    res = normalize_content("text/html", html)
    assert res.text is not None and "безработицы" in res.text
    assert "2,1–2,2%" in res.text
    assert res.transform_chain == ("fetch", "normalize:html-text", "decode:windows-1251")


def test_meta_outside_the_window_is_not_read() -> None:
    # объявление дальше META_CHARSET_WINDOW_BYTES байтов не читается: окна сканирования нет
    padding = b"<!--" + b"x" * (META_CHARSET_WINDOW_BYTES + 300) + b"-->"
    html = b"<html><body>" + padding + CYR.encode("windows-1251") + b"</body></html>"
    res = normalize_content("text/html", html)
    assert res.text is not None
    assert "\ufffd" in res.text  # прежний честный utf-8 replace, без угадывания
    assert res.transform_chain == ("fetch", "normalize:html-text")


def test_unknown_declared_name_is_marked_and_decodes_as_utf8() -> None:
    res = normalize_content("text/plain; charset=windows-1291", CYR.encode("windows-1251"))
    assert res.text is not None and "\ufffd" in res.text
    assert res.transform_chain == ("fetch", "normalize:decode-utf8", "decode:unknown:windows-1291")


def test_declared_utf8_adds_no_label() -> None:
    res = normalize_content("text/plain; charset=UTF-8", CYR.encode("utf-8"))
    assert res.text == CYR
    assert res.transform_chain == ("fetch", "normalize:decode-utf8")


def test_detect_declared_encoding_is_closed_names_pass_through_unnormalized() -> None:
    assert detect_declared_encoding("text/html; charset=Windows-1251", b"<html></html>") == "windows-1251"
    assert detect_declared_encoding(None, codecs.BOM_UTF8 + "текст".encode()) == "utf-8-sig"
    assert detect_declared_encoding("text/html", b"<meta charset='KOI8-R'>") == "koi8-r"
    # имя не канонизируется: неизвестное остаётся известным о неизвестном для вызывающего
    assert detect_declared_encoding("text/html; charset=weird-8", b"") == "weird-8"
    # не-HTML тело без заголовка и BOM: meta не читается, никакого угадывания
    assert detect_declared_encoding("application/octet-stream", b"<meta charset=windows-1251>") is None
    # HTML без объявлений вовсе -> None (голый UTF-8)
    assert detect_declared_encoding("text/html; charset=", b"<html><body>ok</body></html>") is None


# ─── т2: UTF-8 страницы остаются байт в байт прежними ───────────────────────────────────


def test_utf8_pages_keep_v1_text_hash_and_chain_byte_identical() -> None:
    # нормализация подлинной UTF-8 страницы не изменилась (свип решений 1fd0d29..HEAD — ноль):
    # цепочка v1 без меток, sha256 считается по нормализованному тексту utf-8
    raw = (CYR + "\n" + "Росстат зафиксировал уровень безработицы.").encode("utf-8")
    html = b"<html><body>" + raw.replace(b"\n", b"</p><p>") + b"</body></html>"
    res = normalize_content("text/html", html)
    assert res.transform_chain == ("fetch", "normalize:html-text")
    assert res.text is not None and "безработицы" in res.text
    assert res.text_sha256 == hashlib.sha256(res.text.encode("utf-8")).hexdigest()

    plain = normalize_content("text/plain", CYR.encode("utf-8"))
    assert plain.transform_chain == ("fetch", "normalize:decode-utf8")
    assert plain.text == CYR

    # utf-8-sig (BOM) — осознанная метка, текст тот же: BOM снимается кодировщиком
    sig = normalize_content("text/plain", codecs.BOM_UTF8 + CYR.encode("utf-8"))
    assert sig.transform_chain == ("fetch", "normalize:decode-utf8", "decode:utf-8-sig")
    assert sig.text == CYR


def test_caliber_fixture_page_is_underivable_as_clean_utf8() -> None:
    # фикстура caliber.az — подлинная UTF-8: при нормализации голого тела меток декодирования нет
    from pathlib import Path

    fixture = Path(__file__).parent.parent / "fixtures" / "t7112" / "caliber-az-rosstat-unemployment.normalized.txt"
    body = fixture.read_bytes()
    res = normalize_content("text/html; charset=UTF-8", b"<html><body>" + body + b"</body></html>")
    assert res.transform_chain == ("fetch", "normalize:html-text")


def test_finmarket_fixture_pinned_corruption_and_clean_repair() -> None:
    # подлинная страница finmarket.ru/news/6727171 (windows-1251, charset только в meta):
    # прежний код не читал объявление вовсе и декодировал всё как UTF-8 errors="replace" —
    # нормализованный текст эпохи даёт ровно 8679 U+FFFD на 11811 знаков (доля 0,73): от этой
    # живой меры и считается порог 0,01 перечитывания а2; закрытый порядок читает тело чисто
    from pathlib import Path

    raw = (Path(__file__).parent.parent / "fixtures" / "t7112" / "finmarket-6727171.raw.html").read_bytes()
    era = normalize_content("text/html; charset=UTF-8", raw).text  # тот же итог, что у прежнего кода
    assert era is not None and (era.count("\ufffd"), len(era)) == (8679, 11811)
    clean = normalize_content("text/html", raw)
    assert clean.text is not None and clean.text.count("\ufffd") == 0
    assert clean.transform_chain[-1] == "decode:windows-1251"
    assert "Росстат" in clean.text
