"""Дополнительный корневой сертификат для egress research-proxy (T7.77).

`research.fetch` — единственный исходящий канал узла (§5.12). httpcore строит стандартный SSL-контекст
из certifi, а российского корневого УЦ (Минцифры/Госуслуги) в certifi нет: на стенде https://rosstat.gov.ru
давал CERTIFICATE_VERIFY_FAILED (STATUS T7.77; SSL_CERT_FILE при этом инертен — httpcore его не читает).

Оператор может добавить свой PEM через ``NOEZEMA_RESEARCH_EXTRA_CA_FILE``. Границы решения:

* файл ДОБАВЛЯЕТСЯ к стандартному набору доверия (certifi + системные корни), ничего не заменяет;
* проверка TLS не ослабляется ни в каком режиме: ``check_hostname`` и ``verify_mode=CERT_REQUIRED``
  остаются как в стандартном контексте httpcore;
* переменная не задана или пуста — возвращается ровно ``None``, то есть поведение побайтно прежнее
  (pool получает дефолтный контекст httpcore);
* переменная задана, но файл недоступен или не является PEM-сертификатом — понятная ошибка
  (``ResearchTlsError``) в момент сборки клиента/сервиса: узел не стартует с половинной настройкой.

Влияние строго локальное: только connection pool `FetchClient` (research.fetch и web.search upstream).
Системное доверие, LLM-трафик и всё остальное этот модуль не касается.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Final

#: имя переменной окружения стенда/узла (README dev-стенда, раздел про CA)
EXTRA_CA_FILE_ENV: Final = "NOEZEMA_RESEARCH_EXTRA_CA_FILE"

_PEM_MARKER: Final = b"-----BEGIN CERTIFICATE-----"


class ResearchTlsError(RuntimeError):
    """Конфигурация дополнительного CA неверна: файл не читается или не является PEM-сертификатом."""


def extra_ca_ssl_context() -> ssl.SSLContext | None:
    """SSL-контекст для egress research-proxy, либо ``None`` (политика прежняя).

    Зеркалит стандартный контекст httpcore (`create_default_context` + certifi) и добавляет к нему
    операторский PEM. Исключения — только ResearchTlsError с указанием переменной и пути.
    """
    raw = os.environ.get(EXTRA_CA_FILE_ENV)
    if raw is None or not raw.strip():
        return None
    path = Path(raw.strip())
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ResearchTlsError(
            f"{EXTRA_CA_FILE_ENV}: не удалось прочитать файл {str(path)!r}: {exc}"
        ) from exc
    if _PEM_MARKER not in data:
        raise ResearchTlsError(
            f"{EXTRA_CA_FILE_ENV}: файл {str(path)!r} не содержит PEM-сертификата "
            f"(нет маркера {_PEM_MARKER.decode()!r})"
        )
    context = ssl.create_default_context()
    try:
        # то же ядро доверия, что ставит httpcore по умолчанию (certifi), — затем операторский PEM сверху
        import certifi

        context.load_verify_locations(cafile=certifi.where())
        context.load_verify_locations(cafile=str(path))
    except ssl.SSLError as exc:
        raise ResearchTlsError(
            f"{EXTRA_CA_FILE_ENV}: файл {str(path)!r} не является корректным PEM-сертификатом: {exc}"
        ) from exc
    # проверка hostname и обязательность верификации — как в стандартном контексте, без послаблений
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def ensure_extra_ca_setting() -> None:
    """Проверка конфигурации при сборке сервисов: узел не должен подниматься с битой настройкой CA."""
    extra_ca_ssl_context()
