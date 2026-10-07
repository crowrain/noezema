"""Unit: NOEZEMA_RESEARCH_EXTRA_CA_FILE — дополнительный корневой сертификат egress (T7.77).

Стендовый факт (STATUS T7.77): https://rosstat.gov.ru на подставке давал CERTIFICATE_VERIFY_FAILED —
российского корневого УЦ в certifi нет, а SSL_CERT_FILE инертен: httpcore строит контекст сам
(`create_default_context` + certifi) и переменных окружения OpenSSL не читает. Решение — операторский
PEM поверх стандартного доверия, ровно для research-proxy (fetch и web.search upstream идут через
FetchClient), с тремя обязательными свойствами, которые проверяют эти тесты:

1. без переменной поведение прежнее: self-signed сервер отвергается как раньше;
2. с переменной соединение проходит, но проверка не ослаблена: сертификат, выпущенный НЕ для
   127.0.0.1, по-прежнему отвергается (check_hostname + CERT_REQUIRED сохранены), а доверие —
   добавка к стандартному набору, не замена;
3. битая настройка (нет файла / файл не PEM) — понятный отказ в момент сборки клиента и сервиса,
   узел не поднимается с половинной конфигурацией.

Внешних запросов нет: TLS терминируется локальным сервером на 127.0.0.1, сертификаты генерируются
в tmp_path (тестовые ключи в репозиторий не попадают). openssl обязателен; если бинарника нет —
тест помечается скипом и это сообщается отдельной строкой отчёта (AGENTS §7, как с shellcheck).
"""

from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from apps.research_proxy.fetch import FetchClient, FetchError
from apps.research_proxy.service import ResearchProxyService
from apps.research_proxy.ssrf_guard import SSRFPolicy
from apps.research_proxy.tls import EXTRA_CA_FILE_ENV, ResearchTlsError, extra_ca_ssl_context

OPENSSL = shutil.which("openssl")

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(OPENSSL is None, reason="openssl не установлен — генерация сертификата невозможна"),
]

POLICY = SSRFPolicy(
    max_response_bytes=4096,
    max_redirects=3,
    timeout_seconds=15,
    user_agent="noezema-test/1.0",
    private_allowlist=("127.0.0.1",),
)


def _gen_cert(tmp_path: Path, name: str, san: str) -> tuple[Path, Path]:
    """Самоподписанный сертификат + ключ (тестовые, срок 10 лет — wall-clock не значим)."""
    cert = tmp_path / f"{name}.pem"
    key = tmp_path / f"{name}.key"
    subprocess.run(
        [
            str(OPENSSL), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
            "-keyout", str(key), "-out", str(cert),
            "-subj", f"/CN={name}.test",
            "-addext", f"subjectAltName={san}",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


class _TlsEchoServer:
    """HTTPS-сервер на 127.0.0.1:0, отвечает b\"ok\". TLS терминирует сервом со своим сертификатом."""

    def __init__(self, cert: Path, key: Path) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        return f"https://127.0.0.1:{self._server.server_address[1]}/page"

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class _HttpEchoServer:
    """Тот же сервер без TLS — проверка, что без переменной обычный путь не изменился."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}/page"

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class _EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"ok"
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # тестовый сервер не шумит в stdout
        return


async def _fetch(url: str) -> bytes:
    client = FetchClient(POLICY)
    try:
        result = await client.fetch(url)
        return result.data
    finally:
        await client.aclose()


# ─── 1. без переменной поведение прежнее ────────────────────────────────────────


def test_without_extra_ca_self_signed_is_rejected_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(EXTRA_CA_FILE_ENV, raising=False)
    assert extra_ca_ssl_context() is None  # нет настройки — контекста нет, httpcore ставит свой
    cert, key = _gen_cert(tmp_path, "localhost-ok", "IP:127.0.0.1")
    with _TlsEchoServer(cert, key) as url:
        with pytest.raises(FetchError) as raised:
            asyncio.run(_fetch(url))
        assert "CERTIFICATE_VERIFY_FAILED" in str(raised.value), raised.value


def test_plain_http_path_is_untouched_without_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXTRA_CA_FILE_ENV, raising=False)
    with _HttpEchoServer() as url:
        assert asyncio.run(_fetch(url)) == b"ok"


# ─── 2. с переменной: доверие добавляется, проверка не ослабляется ──────────────


def test_extra_ca_makes_verification_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cert, key = _gen_cert(tmp_path, "localhost-ok", "IP:127.0.0.1")
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(cert))
    with _TlsEchoServer(cert, key) as url:
        assert asyncio.run(_fetch(url)) == b"ok"


def test_extra_ca_still_rejects_certificate_for_a_different_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Оба сертификата добавлены в доверие (объединённый PEM), и всё равно соединение с сервером,
    чей сертификат выпущен не для 127.0.0.1, отвергается: check_hostname жив."""
    good_pem, good_key = _gen_cert(tmp_path, "localhost-ok", "IP:127.0.0.1")
    other_pem, other_key = _gen_cert(tmp_path, "elsewhere", "IP:198.51.100.7")  # TEST-NET-2
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_bytes(good_pem.read_bytes() + other_pem.read_bytes())
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(bundle))

    with _TlsEchoServer(good_pem, good_key) as good_url:
        assert asyncio.run(_fetch(good_url)) == b"ok"  # доверие к добавленному корню работает
    with _TlsEchoServer(other_pem, other_key) as other_url:
        with pytest.raises(FetchError) as raised:
            asyncio.run(_fetch(other_url))
        message = str(raised.value)
        assert "CERTIFICATE_VERIFY_FAILED" in message, message
        assert "Hostname mismatch" in message or "not valid for" in message, message


def test_extra_ca_context_keeps_strict_verification_and_adds_to_the_default_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cert, _key = _gen_cert(tmp_path, "localhost-ok", "IP:127.0.0.1")
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(cert))
    context = extra_ca_ssl_context()
    assert context is not None
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED

    baseline = ssl.create_default_context()
    import certifi as _certifi

    baseline.load_verify_locations(cafile=_certifi.where())  # ядро доверия httpcore
    assert len(context.get_ca_certs()) > len(baseline.get_ca_certs()), (
        "операторский PEM обязан ДОБАВЛЯТЬСЯ к стандартному набору доверия, а не заменять его"
    )


# ─── 3. битая настройка — понятный отказ при сборке ────────────────────────────


def test_missing_ca_file_is_a_clear_startup_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(tmp_path / "нет-такого-файла.pem"))
    with pytest.raises(ResearchTlsError, match=EXTRA_CA_FILE_ENV):
        extra_ca_ssl_context()
    with pytest.raises(ResearchTlsError, match=EXTRA_CA_FILE_ENV):
        FetchClient(POLICY)  # egress-клиент не строится с битой настройкой
    with pytest.raises(ResearchTlsError, match=EXTRA_CA_FILE_ENV):
        ResearchProxyService(session_factory=None, artifact_store=None)  # вход research-proxy


def test_non_pem_file_is_rejected_with_an_explanation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    junk = tmp_path / "not-a-cert.pem"
    junk.write_text("тут явно не сертификат", encoding="utf-8")
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(junk))
    with pytest.raises(ResearchTlsError, match="PEM"):
        extra_ca_ssl_context()

    empty = tmp_path / "empty.pem"
    empty.write_bytes(b"")
    monkeypatch.setenv(EXTRA_CA_FILE_ENV, str(empty))
    with pytest.raises(ResearchTlsError, match="PEM"):
        extra_ca_ssl_context()
