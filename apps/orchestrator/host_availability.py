"""Host availability hygiene (T7.111, часть 2; решение пользователя 2026-10-14).

Замер менеджера (фикстура step-budget-2026-10-14.txt): за 8 сессий стенда 14 отказов чтения, из них
5 — повторный заход к хосту, который уже отказывал в этой же сессии (alfabank.ru: 403 → повторный
fetch → 403 → 429; stats.hh.ru и tass.ru: два отказа подряд), плюс 3 чтения заведомо мёртвых хостов,
повторявшихся между сессиями. Отказы — не доказательства недоступности: страница может отдаться с
третьей попытки (T7.13 lesson 3), поэтому здесь нет ни карантина хостов в прокси, ни изменения
лимитов; есть два аддитивных механизма:

(а) внутри сессии — хост, отказавший `research.fetch` с 401/403/429/таймаутом, больше в этой сессии
    не читается: второй заход к нему отклоняется до исполнения в той же форме, что и повтор вызова
    (T7.12), шаг по-прежнему тратится (учёт T7.76 не менялся). 404, нечитаемый артефакт и переразмер
    страницы — свойства страницы, а не хоста: они не помечают хост.

(б) между сессиями — на старте сессии по журналу за последние 7 суток считаются хосты с >= 2
   qualifying отказами и без успешного чтения после последнего отказа; они называются короткой секцией
    контекста «Недавно недоступны». Только название: такие хосты не запрещаются (могли починиться).
    Один read-only запрос к существующим записям журнала (action_started / action_failed /
    research_content_read) — без новых таблиц, миграций и типов событий.

Классификация отказа строится по тексту ошибки, который порождает сам узел (`packages/research_proxy/
fetch.py`: `non-200 status: <код>`, `timeout: ...`); новых кодов ошибок не вводилось."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType
from packages.domain.models.events import ORMAuditEvent

#: HTTP-коды, означающие «хост сам отказывает» (401/403 — доступ, 429 — лимит запросов).
HTTP_DENY_CODES: frozenset[str] = frozenset({"401", "403", "429"})

#: Окно журнала между сессиями, в котором считаются отказы. Замер фикстуры: мёртвые хосты
#: повторялись в сессиях одного вечера; неделя — верхняя граница «недавно».
HOST_UNAVAILABLE_WINDOW_DAYS = 7

#: Отказов в окле, чтобы хост попал в секцию. Один отказ ничего не утверждает
#: (T7.13 lesson 3); повторный отказ без успеха между ними — устойчивый сигнал.
HOST_UNAVAILABLE_MIN_FAILURES = 2

#: Потолки секции контекста: сколько хостов называть и сколько байт занимает вся секция.
#: Целые строки выносятся из секции при переполнении, а не обрезаются (урок T7.76: обрезанное
#: наблюдение хуже целого). Секция не входит в бюджет надбавок контекста по образцу раздела
#: «Стратегия против цикла» (T5.4) и ограничена собственным потолком.
HOST_AVAILABILITY_MAX_HOSTS = 8
HOST_AVAILABILITY_SECTION_CHAR_LIMIT = 1_200

_NON200_RE = re.compile(r"^non-200 status: (\d{3})$")


@dataclass(frozen=True)
class HostAvailability:
    """Хост, недавно недоступный по журналу (для секции контекста)."""

    host: str
    kind: str  # "401"|"403"|"429"|"timeout" (последний по времени отказ группы)
    last_failure_at: datetime
    failures: int


@dataclass(frozen=True)
class JournalRow:
    """Строка журнала в reduced-виде ровно в том порядке, который отдаёт SELECT ниже."""

    type: str
    session_id: str
    sequence: int
    at: datetime
    payload: dict[str, Any]


def fetch_url_host(url: str) -> str:
    """Хост адреса для группировки: lowercase, без схемы, порта, пути и одного ведущего `www.`
    (то же соглашение, что у замера фикстуры: `www.cbr.ru` и `cbr.ru` — один хост, `stats.hh.ru`
    и `hh.ru` — разные). Пустая строка, если хост не различим (адрес без схемы)."""
    text = str(url or "").strip()
    host = urlsplit(text).hostname or ""
    if not host:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def classify_fetch_error(error: str) -> str | None:
    """Вид отказа по тексту ошибки хоста: "401"|"403"|"429"|"timeout" либо None.

    Только то, что говорит о хосте: `non-200 status: <код>` с кодом из HTTP_DENY_CODES и `timeout: ...`
    (оба текста порождает packages/research_proxy/fetch.py). 404 и другие коды, нечитаемый артефакт,
    отказ allowlist/лимита запросов и транспортные сбои — None: хост они не помечают."""
    text = str(error or "")
    match = _NON200_RE.match(text.strip())
    if match is not None and match.group(1) in HTTP_DENY_CODES:
        return match.group(1)
    if text.startswith("timeout:"):
        return "timeout"
    return None


def _event_time(row: JournalRow) -> tuple[float, str, int]:
    """Порядок событий: момент строки, а внутри одного мгновения — session id и sequence журнала
    (sequence уникален в пределах сессии, §14.4)."""
    return (row.at.timestamp(), row.session_id, row.sequence)


def _url_of_started(row: JournalRow) -> str:
    """URL из action_started: payload["arguments"] — каноническая JSON-строка (_cap_args оркестратора);
    при непригодном или обрезанном по потолку JSON остаётся поисковый fallback по `"url": "…` —
    аргумент research.fetch один, и начало URL даёт тот же хост, даже если кавычка отрезана."""
    raw = (row.payload or {}).get("arguments")
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        url = parsed.get("url")
        if isinstance(url, str):
            return url
    match = re.search(r'"url":\s*"([^"]+)', raw)
    return match.group(1) if match else ""


def summarize_host_journal(rows: Iterable[JournalRow]) -> list[HostAvailability]:
    """Правила секции «Недавно недоступны» из строк журнала (SELECT уже ограничивает окно — см.
    `recently_unavailable_hosts`): хост попадает, если в окне >= HOST_UNAVAILABLE_MIN_FAILURES
    qualifying-отказов `research.fetch` (401/403/429/таймаут) и после последнего отказа нет успешного
    чтения (`research_content_read`) того же хоста. Успех до отказа окно не обнуляет: считается лишь
    наличие успеха ПОСЛЕ последнего отказа. Отсортировано от самого свежего отказа."""
    started_url: dict[str, str] = {}
    failures: dict[str, list[JournalRow]] = {}
    successes: dict[str, list[JournalRow]] = {}
    for row in rows:
        payload = row.payload or {}
        if row.type == AuditEventType.ACTION_STARTED.value:
            action_id = payload.get("action_id")
            if isinstance(action_id, str) and action_id:
                started_url[action_id] = _url_of_started(row)
        elif row.type == AuditEventType.ACTION_FAILED.value:
            action_id = payload.get("action_id")
            url = started_url.get(action_id if isinstance(action_id, str) else "", "")
            if not url:
                continue  # отказ не относится к research.fetch этого окна
            kind = classify_fetch_error(str(payload.get("error") or ""))
            host = fetch_url_host(url)
            if kind is None or not host:
                continue
            failures.setdefault(host, []).append(row)
        elif row.type == AuditEventType.RESEARCH_CONTENT_READ.value:
            read_url = payload.get("url")
            host = fetch_url_host(str(read_url or ""))
            if host:
                successes.setdefault(host, []).append(row)

    reports: list[HostAvailability] = []
    for host, rows_for_host in failures.items():
        if len(rows_for_host) < HOST_UNAVAILABLE_MIN_FAILURES:
            continue
        last_fail = max(rows_for_host, key=_event_time)
        if any(_event_time(s) > _event_time(last_fail) for s in successes.get(host, [])):
            continue  # после последнего отказа хост уже отдавал страницу — он починился
        kind = classify_fetch_error(str(last_fail.payload.get("error") or "")) or ""
        reports.append(
            HostAvailability(
                host=host,
                kind=kind,
                last_failure_at=last_fail.at,
                failures=len(rows_for_host),
            )
        )
    reports.sort(key=lambda r: r.last_failure_at.timestamp(), reverse=True)
    return reports


def _kind_phrase(kind: str) -> str:
    return "таймаут" if kind == "timeout" else kind


def render_host_availability_section(entries: Iterable[HostAvailability]) -> str:
    """Секция контекста «Недавно недоступны» (пункт (б) решения): `хост — код/таймаут, когда`.
    Пустая строка при пустом списке (секции нет). Не более HOST_AVAILABILITY_MAX_HOSTS хостов и не
    более HOST_AVAILABILITY_SECTION_CHAR_LIMIT байт: при переполнении целиком выносятся самые старые
    строки. Секция только называет хосты — запретов она не вводит (могли починиться)."""
    items = list(entries)[:HOST_AVAILABILITY_MAX_HOSTS]
    if not items:
        return ""
    header = "# Недавно недоступны"
    tail = (
        "Отказы этих хостов видел журнал прошлых сессий; запретом это не является — хост мог "
        "починиться. Не стройте поиск вокруг них: ищите те же данные на другом сайте."
    )
    lines = [
        f"- {entry.host} — {_kind_phrase(entry.kind)}, {entry.last_failure_at:%Y-%m-%d} "
        f"(отказов: {entry.failures})"
        for entry in items
    ]
    while lines and len("\n".join([header, *lines, tail]).encode("utf-8")) > (
        HOST_AVAILABILITY_SECTION_CHAR_LIMIT
    ):
        lines.pop()  # целиком вынести самый старый хост
    return "\n".join([header, *lines, tail])


def render_host_unavailable_note(step: int, host: str, kind: str, failed_step: int) -> str:
    """Наблюдение модели об отклонённом в этой сессии чтении недоступного хоста (пункт (а)): названы
    вид отказа и шаг первой неудачи, предложено искать те же данные на другом сайте."""
    return (
        f"[{step}] research.fetch ОТКЛОНЕНО: {host} уже отказывал в этой сессии на шаге "
        f"{failed_step} ({_kind_phrase(kind)}). Повторный заход к тому же хосту нового не обещает — "
        "ищи те же данные на другом сайте и читай там. Отклонённое действие тоже стоит один шаг."
    )


async def recently_unavailable_hosts(db: AsyncSession, current_session_id: uuid.UUID | None) -> list[HostAvailability]:
    """Один read-only SELECT по существующему журналу (без новых таблиц и типов событий): строки
    последнего окна HOST_UNAVAILABLE_WINDOW_DAYS, относящиеся к research.fetch (action_started этого
    инструмента, action_failed этих действий, research_content_read), за исключением событий самой
    этой сессии. Пары отказ→URL и правила секции считает summarize_host_journal на Python — SQL
    только выбирает строки окна (ошибка JSON в payload не должна решаться базой)."""
    since = datetime.now(UTC) - timedelta(days=HOST_UNAVAILABLE_WINDOW_DAYS)
    started = AuditEventType.ACTION_STARTED.value
    failed = AuditEventType.ACTION_FAILED.value
    read_ok = AuditEventType.RESEARCH_CONTENT_READ.value
    error_text = ORMAuditEvent.payload["error"].astext
    stmt = (
        select(
            ORMAuditEvent.type,
            ORMAuditEvent.session_id,
            ORMAuditEvent.sequence,
            ORMAuditEvent.occurred_at,
            ORMAuditEvent.payload,
        )
        .where(
            ORMAuditEvent.occurred_at >= since,
            ORMAuditEvent.session_id.is_distinct_from(current_session_id),
            or_(
                (ORMAuditEvent.type == started) & (ORMAuditEvent.payload["tool"].astext == "research.fetch"),
                (ORMAuditEvent.type == failed)
                & or_(
                    error_text.in_([f"non-200 status: {code}" for code in sorted(HTTP_DENY_CODES)]),
                    error_text.like("timeout:%"),
                ),
                ORMAuditEvent.type == read_ok,
            ),
        )
        .order_by(ORMAuditEvent.session_id, ORMAuditEvent.sequence)
    )
    rows = [
        JournalRow(
            type=str(row.type),
            session_id=str(row.session_id or ""),
            sequence=int(row.sequence),
            at=row.occurred_at,
            payload=dict(row.payload or {}),
        )
        for row in (await db.execute(stmt)).all()
    ]
    return summarize_host_journal(rows)
