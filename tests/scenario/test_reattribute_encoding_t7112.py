"""Scenario (DB): T7.112 (а2) — переатрибуция перечитывает испорченный текст страницы из
сохранённого оригинала (решение пользователя 2026-10-14, ADR-0029 дополнение).

Состояние стендового дефекта (страницы с телом в windows-1251, как finmarket.ru/news/6727171):
прежняя нормализация декодировала всё как UTF-8 errors="replace", кириллица сохранённого
нормализованного текста превратилась в U+FFFD, и детекторы атрибуции не видели на странице ни
алиасов, ни шаблонов. Оригинальные байты при этом всегда хранились как есть.

Переатрибуция `--evidence-level` обязана решить по перечитанному из оригинала тексту: доля
U+FFFD выше CORRUPT_TEXT_REPLACEMENT_SHARE И кодировка оригинала объявлена не-UTF-8 в закрытом
порядке (а1). Сохранённые артефакты, их хеши и сами улики при этом НЕ перезаписываются —
перечитывание меняет только решение. Повторный прогон пуст.

Эпоха дефекта моделируется настоящим fetch-путём: FetchClient отдаёт подлинные байты окна
(meta-объявление charset=windows-1251, как на живых страницах), нормализация вызова эпохи
(объявление кодировки не читается, декодирование UTF-8 errors="replace" — ровно то, что делал
код до T7.112) подменяется monkeypatch'ом; переатрибуция работает уже нынешним кодом.

Живая страница finmarket целиком после починки текста попала бы в pre-existing вето
документа own_assessment (disclaimer комментариев «может не совпадать с мнением редакции» и
заголовок чужой новости со словом «противоречивый») — это отдельный незакрытый вопрос,
разбор в STATUS T7.112; здесь проверяется механизм перечитывания, а не та страница.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.normalization import NormalizedContent
from apps.research_proxy.normalization import normalize_content as CURRENT_NORMALIZE
from apps.research_proxy.reattribution import reattribute_evidence_window
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_research_evidence import (
    _apply,
    _research_obs,
    _scalar,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

SINCE = datetime(2000, 1, 1, tzinfo=UTC)

URL_PAGE = "https://news.example/6727171"

SENTENCE = (
    "Росстат зафиксировал уровень безработицы в России в сентябре 2026 года "
    "на отметке 2,1–2,2%. Ранее служба сообщала о 2,3%."
)
CLAIM = "Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2%."

# страница эпохи дефекта: charset только в meta-объявлении тела (как finmarket.ru/news/6727171)
PAGE_BYTES = (
    b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1251">'
    b"</head><body>"
    + SENTENCE.encode("windows-1251")
    + b"</body></html>"
)


class FakeFetchClient:
    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",  # в заголовке charset нет: объявление только в meta
            data=PAGE_BYTES,
            sha256=hashlib.sha256(PAGE_BYTES).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


def _era_normalize(content_type: str | None, data: bytes) -> NormalizedContent:
    """Нормализация вызова эпохи (до T7.112 а1): объявленной кодировки код не видел, декодировал
    всё как UTF-8 errors="replace". Результат моделируется подменой объявления — тот же итог,
    что у прежнего кода: кириллица тела превращается в U+FFFD (проверяется в тесте)."""
    return CURRENT_NORMALIZE(content_type, data.replace(b"charset=windows-1251", b"charset=UTF-8"))


@pytest.fixture()
def corrupted_era_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    monkeypatch.setattr(svc, "normalize_content", _era_normalize)


def _quoted(statement: str) -> str:
    return "'" + statement.replace("'", "''") + "'"


async def _evidence_row(scratch_url: str) -> dict[str, Any]:
    row = await _scalar(
        scratch_url,
        "SELECT e.id::text, e.scope -> 'value_attribution' AS origin, s.metadata ->> 'normalized_sha256' AS nsha "
        "FROM evidence e JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(CLAIM),
    )
    assert row is not None
    return {"id": str(row[0]), "origin": row[1], "nsha": str(row[2])}


async def _seed_corrupted_era(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, str]:
    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from apps.research_proxy.service import ResearchProxyService

    service = ResearchProxyService(factory, store)
    try:
        env = dict(await service.fetch(URL_PAGE))
    finally:
        await engine.dispose()

    original_sha = str(env["original_sha256"])
    normalized_sha = str(env["normalized_sha256"])
    corrupted = store.get(normalized_sha).decode("utf-8")
    share = corrupted.count("\ufffd") / max(1, len(corrupted))
    assert share > 0.5, "эпоха обязана сохранить испорченный текст (тогда и проверяется перечитывание)"

    from apps.orchestrator.evidence import observation_to_evidence

    record = observation_to_evidence(
        _research_obs(
            URL_PAGE,
            str(env["source_id"]),
            original_sha,
            normalized_sha,
            normalized_text=corrupted,
        ),
        {"url": URL_PAGE},
    )
    await _apply(scratch_url, [record], CLAIM)
    return {"original": original_sha, "normalized": normalized_sha, "corrupted": corrupted}


def _factory(scratch_url: str) -> tuple[async_sessionmaker[Any], Any]:
    engine = create_async_engine(scratch_url)
    return async_sessionmaker(engine, expire_on_commit=False), engine


@pytest.mark.asyncio
async def test_corrupted_page_text_is_re_read_from_the_original_artifact(
    migrated_db: tuple[str, Any], tmp_path: Path, corrupted_era_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    seeded = await _seed_corrupted_era(scratch_url, store)

    # состояние эпохи: улика без записи о происхождении значения (детектор не видел ничего)
    before = await _evidence_row(scratch_url)
    assert before["origin"] is None, before
    assert before["nsha"] == seeded["normalized"]

    factory, engine = _factory(scratch_url)
    try:
        plan = await reattribute_evidence_window(factory, store, since=SINCE, dry_run=True)
        rows = [r for r in plan.rows if r.claim_statement == CLAIM]
        assert len(rows) == 1, plan.rows
        assert rows[0].action == "будет записано происхождение значения улики", plan.rows
        assert rows[0].now_status == "derivative" and rows[0].primary_name == "Росстат", plan.rows
        assert plan.changed_evidence == 0
    finally:
        await engine.dispose()

    factory, engine = _factory(scratch_url)
    try:
        report = await reattribute_evidence_window(factory, store, since=SINCE)
        assert report.changed_evidence == 1, report.rows
        assert report.worker is not None
    finally:
        await engine.dispose()

    # т4 главное: решение принято по перечитанному из оригинала тексту — строгая пара с Росстатом
    after = await _evidence_row(scratch_url)
    record = dict(after["origin"])
    assert record["primary_key"] == "rosstat" and record["primary_name"] == "Росстат"
    assert record["method"] == "host-value-attribution-v13"
    assert record["pairing"] == "value_and_primary_in_fragment"
    assert "зафиксировал" in record["basis_fragment"].lower()  # шаблон а3 прочитан живым текстом
    assert "\ufffd" not in record["basis_fragment"]

    # сохранённые артефакты и хеши НЕ перезаписаны: нормализованный текст эпохи живёт под тем же
    # хешем байт в байт, оригинал — подлинная страница windows-1251 (стоп-критерий а2)
    assert after["nsha"] == seeded["normalized"]
    assert store.get(seeded["normalized"]).decode("utf-8") == seeded["corrupted"]
    assert store.get(seeded["original"]) == bytes(PAGE_BYTES)
    chunk = await _scalar(
        scratch_url,
        "SELECT c.content_hash FROM artifact_chunks c JOIN artifacts a ON a.id = c.artifact_id "
        "WHERE a.sha256 = :sha",
        {"sha": seeded["original"]},
    )
    assert str(chunk[0]) == seeded["original"]

    factory, engine = _factory(scratch_url)
    try:
        again = await reattribute_evidence_window(factory, store, since=SINCE)
        assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
        rows = [r for r in again.rows if r.claim_statement == CLAIM]
        assert len(rows) == 1 and rows[0].action == "изменений нет: решение уже записано ранее", again.rows
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_clean_utf8_pages_are_reattributed_without_re_read(
    migrated_db: tuple[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контроль границы перечитывания: чистая UTF-8 страница (доля U+FFFD ниже порога) решается
    по сохранённому тексту, как прежде; никакого «починенного» решения на ней не появляется."""

    import apps.research_proxy.service as svc

    class CleanFetchClient(FakeFetchClient):
        async def fetch(self, url: str) -> FetchResult:
            data = f"<html><body><p>{SENTENCE}</p></body></html>".encode()
            return FetchResult(
                url=url,
                final_url=url,
                content_type="text/html",
                data=data,
                sha256=hashlib.sha256(data).hexdigest(),
                redirects=0,
                elapsed_ms=1,
            )

    monkeypatch.setattr(svc, "FetchClient", CleanFetchClient)
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from apps.research_proxy.service import ResearchProxyService

    service = ResearchProxyService(factory, store)
    try:
        env = dict(await service.fetch(URL_PAGE))
    finally:
        await engine.dispose()

    from apps.orchestrator.evidence import observation_to_evidence

    clean = store.get(str(env["normalized_sha256"])).decode("utf-8")
    assert "\ufffd" not in clean
    record = observation_to_evidence(
        _research_obs(
            URL_PAGE,
            str(env["source_id"]),
            str(env["original_sha256"]),
            str(env["normalized_sha256"]),
            normalized_text=clean,
        ),
        {"url": URL_PAGE},
    )
    await _apply(scratch_url, [record], CLAIM)

    # fetch нынешним кодом уже применил детекторы (метка v8/v11 записи уже нет — её нет вовсе:
    # страничное решение на чистой странице есть, записи о значении при staging нет)
    factory, engine = _factory(scratch_url)
    try:
        report = await reattribute_evidence_window(factory, store, since=SINCE)
        assert report.changed_evidence == 1, report.rows
    finally:
        await engine.dispose()
    origin = dict((await _evidence_row(scratch_url))["origin"])
    assert origin["primary_key"] == "rosstat" and origin["pairing"] == "value_and_primary_in_fragment"
    # хеш нормализованного текста чистой страницы не менялся никогда: перечитывания не было
    env_after = await _scalar(
        scratch_url,
        "SELECT s.metadata ->> 'normalized_sha256' FROM sources s WHERE s.canonical_uri = :u",
        {"u": URL_PAGE},
    )
    assert str(env_after[0]) == hashlib.sha256(clean.encode("utf-8")).hexdigest()
