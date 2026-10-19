"""Scenario (DB): аудит «значение на связи» у УЖЕ записанных связей (T7.124, ADR-0038 §13).

Что закреплено (номера т1–т6 из постановки T7.124):

- т1: репроигрывание стенда 64837cb5 (`83e7a1c8`, «hh-индекс … составил 9,6») — три записанные
  связи: две к странице «решение ЦБ по ключевой ставке» (значения 9,6 на ней нет) и одна к
  странице hr-ratings.com (значение есть). План называет ровно эти две «нет значения», называет
  третью «значение есть», показывает группы «было (2) → стало (1)» и оценку «E3 supported →
  E1 hypothesis» (оценка НЕ остаётся E3), и ни одной записи не делает.
- т2: связь, чья страница называет значение утверждения, в плане не появляется: утверждение с
  такой единственной связью вообще не попадает в строки плана.
- т3: сохранённого текста страницы нет — связь называется «текста нет» и НЕ включается в
  пересчёт (ни групп, ни оценки она не меняет): «текста нет» — не «значения нет».
- т4: режим по умолчанию (без параметров) не пишет ни одной строки: счётники ключевых таблиц до
  и после идентичны, новых audit_events и reassessment_jobs нет.
- т5: идемпотентность: повторный прогон даёт тот же план (те же строки, те же исходы, те же
  пересчёты) и по-прежнему ничего не пишет.
- т6: утверждение под действующей коррекцией оператора (ADR-0031) пропускается с названной
  причиной и в строках плана не появляется — ровно та же защита, что у `research-reattribute`.

Посев «уже записанных» связей честный: границы куратора (гейт значения T7.108/T7.123) такие
связи сегодня НЕ пропустили бы — они написаны ДО появления гейта, поэтому посев идёт staging
+ `MemoryService.apply_claim_staging` (тот же commit-boundary код, что у финальной транзакции),
как на стенде 2026-08-29. Сеть — FakeFetchClient (§6); прокси, группировка, правила и staging
настоящие; страницы — побайтовые нормализованные тексты стендовых страниц
(tests/fixtures/t7123). Ни одного внешнего запроса, ни одного LLM-вызова.

CliRunner-ловушка T7.59: команда поднимает свой event loop, поэтому тест не берёт engine
фикстуры — каждый read теста открывает свой engine из scratch-URL и `dispose()` его.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.link_audit import (
    VERDICT_NO_TEXT,
    VERDICT_VALUE_ABSENT,
    VERDICT_VALUE_PRESENT,
    LinkAuditReport,
    audit_recorded_links,
)
from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.schemas.observation import Observation
from tests.scenario.test_research_evidence import (
    _apply,
    _fetch_sources,
    _scalar,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]

URL_SBERCIB = "https://www.sbercib.ru/publication/reshenie-banka-rossii-po-klyuchevoi-stavke-ot-1"
URL_HR = "https://hr-ratings.com/analytics/rynok-truda-bolshoj-krizis-v-istorii-rossii"


def _fixture_lines(name: str) -> list[str]:
    return (REPO_ROOT / "tests/fixtures/t7123" / name).read_text(encoding="utf-8").splitlines()


#: побайтовые стендовые страницы: на первой значения «9,6» нет (замер T7.124), на второй оно есть
PAGES: dict[str, list[str]] = {
    URL_SBERCIB: _fixture_lines("sbercib-key-rate.normalized.txt"),
    URL_HR: _fixture_lines("hr-ratings-hh-index.normalized.txt"),
}

#: формулировка `83e7a1c8` со стенда (голое десятичное значение, профиль T7.123 — «9.6»)
STATEMENT_83 = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 9,6."
#: контрастное утверждение: его единственная связь ведёт на страницу, где значение названо
STATEMENT_KEY_RATE = "По данным SberCIB, ключевая ставка Банка России в сентябре 2026 года составила 11,6."

COUNT_TABLES = (
    "sources",
    "evidence",
    "claims",
    "claim_assessments",
    "claim_assessment_heads",
    "reassessment_jobs",
    "audit_events",
    "source_independence_snapshots",
    "session_staging",
)

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в T7.85/T7.120a)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> Any:
        from apps.research_proxy.fetch import FetchResult

        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url] if line.strip())
        data = f"<html><body>{paragraphs}</body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ нормализатором: подменена только сеть."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


# ─── посев ────────────────────────────────────────────────────────────────────────


def _record(env: dict[str, str], chunk: str) -> Any:
    """Улика `source_assertion` прочитанной страницы (та же форма наблюдения, что у
    `_research_fetch`, но с разным окном: две связи одной страницы — две строки `evidence`)."""

    from apps.orchestrator.evidence import observation_to_evidence

    obs = Observation(
        tool="research.fetch",
        ok=True,
        data={
            "url": env["url"],
            "mode": "curated",
            "fenced": True,
            "content": "<<<UNTRUSTED DATA BEGIN>>>\n...\n<<<UNTRUSTED DATA END>>>\n",
            "normalized_text": "\n".join(PAGES[env["url"]]),
            "source_id": env["source_id"],
            "original_sha256": env["original_sha256"],
            "normalized_sha256": env["normalized_sha256"],
            "chunk_id": chunk,
        },
    )
    rec = observation_to_evidence(obs, {"url": env["url"]})
    assert rec is not None, "research.fetch не дал улики"
    return rec


async def _seed_replay(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, Any]:
    """Стендовое состояние 64837cb5: утверждение `83e7a1c8` с тремя записанными связями —
    две на страницу без значения, одна на страницу со значением; оценка головы E3 supported.

    Граница куратора (гейт значения) такие связи сегодня не пропустила бы: посев идёт
    staging + `apply_claim_staging` — тем же commit-boundary кодом, которым такие связи были
    записаны до T7.108."""

    await _set_section(scratch_url, _section())
    envs = await _fetch_sources(scratch_url, store, (URL_SBERCIB, URL_HR))
    records = [
        _record(envs[0], "chunk-0"),
        _record(envs[0], "chunk-1"),
        _record(envs[1], "chunk-0"),
    ]
    await _apply(scratch_url, records, STATEMENT_83)
    return {"sbercib": envs[0], "hr": envs[1]}


async def _seed_second_claim(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, Any]:
    """Контрастное утверждение: единственная связь на страницу, где значение названо (т2)."""

    await _set_section(scratch_url, _section())
    envs = await _fetch_sources(scratch_url, store, (URL_SBERCIB, URL_HR))
    await _apply(scratch_url, [_record(envs[0], "chunk-0")], STATEMENT_KEY_RATE)
    return {"sbercib": envs[0], "hr": envs[1]}


async def _with_session(scratch_url: str, fn: Callable[[Any], Any]) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            return await fn(db)
    finally:
        await engine.dispose()


async def _audit(scratch_url: str, store: FilesystemArtifactStore, **kwargs: Any) -> LinkAuditReport:
    return await _with_session(scratch_url, lambda db: audit_recorded_links(db, store, **kwargs))


async def _counts(scratch_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        row = await _scalar(scratch_url, f"SELECT count(*) AS n FROM {table}")
        assert row is not None
        counts[table] = int(row[0])
    return counts


async def _assessment_of(scratch_url: str, statement: str) -> tuple[str, str]:
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.assessment_state = 'current' AND c.statement = :s",
        {"s": statement},
    )
    assert row is not None, "у утверждения нет текущей оценки"
    return str(row[0]), str(row[1])


async def _rows(scratch_url: str, sql: str, params: dict | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).mappings().all())
    finally:
        await engine.dispose()


async def _group_uris(scratch_url: str, statement: str) -> set[str]:
    rows = await _rows(
        scratch_url,
        """
        SELECT s.canonical_uri AS uri FROM source_independence_members m
        JOIN source_independence_snapshots sn ON sn.id = m.snapshot_id
        JOIN claim_assessments a ON a.source_independence_snapshot_id = sn.id
        JOIN claim_assessment_heads h ON h.current_assessment_id = a.id
        JOIN claims c ON c.id = h.claim_id
        JOIN sources s ON s.id = m.source_id WHERE c.statement = :s""",
        {"s": statement},
    )
    return {str(r["uri"]) for r in rows}


def _row_of(report: LinkAuditReport, statement: str) -> Any:
    rows = [row for row in report.rows if row.statement == statement]
    assert len(rows) == 1, report.rows
    return rows[0]


# ─── т1: репроигрывание 83e7a1c8 — связи названы, группы и оценка пересчитаны, записей нет ──


@pytest.mark.asyncio
async def test_replayed_standalone_links_are_named_and_assessment_recomputed_read_only(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Было на стенде: E3 «Проверено» на двух группах независимости, одна из которых — связи к
    странице ключевой ставки, где значения 9,6 нет. План T7.124 называет эти связи «нет
    значения», связь на hr-ratings — «значение есть», и показывает пересчёт: две группы → одна,
    E3 supported → E1 hypothesis. Ни одной строки не появляется."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ids = await _seed_replay(scratch_url, store)

    before = await _counts(scratch_url)
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_SBERCIB, URL_HR}

    report = await _audit(scratch_url, store)
    row = _row_of(report, STATEMENT_83)

    # ровно та же проверка значения, что у гейта записи: две связи проваливают, одна проходит
    verdicts = {link.verdict for link in row.links}
    assert verdicts == {VERDICT_VALUE_ABSENT, VERDICT_VALUE_PRESENT}, row.links
    assert len(row.value_absent_links) == 2 and len(row.missing_text_links) == 0, row.links
    absent_uris = {link.canonical_uri for link in row.value_absent_links}
    assert absent_uris == {URL_SBERCIB}, row.links
    kept = next(link for link in row.links if link.verdict == VERDICT_VALUE_PRESENT)
    assert kept.canonical_uri == URL_HR, kept
    assert "9.6" in kept.value_tokens and "9.6" in row.value_absent_links[0].value_tokens

    # обе провалившие связи вели на ОДИН источник — уходит один участник, группа одна остаётся
    assert row.excluded_source_uris == (URL_SBERCIB,), row.excluded_source_uris
    assert row.groups_changed is True
    assert len(row.old_groups) == 2 and len(row.new_groups) == 1, (row.old_groups, row.new_groups)
    assert all(URL_HR in uri for group in row.new_groups for uri in group), row.new_groups

    # оценка «стало» — тот же rules engine: одна группа и одна улика не дают E3
    assert (row.persisted_grade, row.persisted_status) == ("E3", "supported")
    assert row.evaluation_computed is True
    assert (row.new_grade, row.new_status) == ("E1", "hypothesis"), (row.new_grade, row.new_status)
    assert row.new_grade != "E3" and row.assessment_changed is True

    # read-only: ни одной новой строки и прежняя оценка головы на месте
    after = await _counts(scratch_url)
    assert after == before, (before, after)
    assert await _assessment_of(scratch_url, STATEMENT_83) == ("E3", "supported")
    assert await _group_uris(scratch_url, STATEMENT_83) == {URL_SBERCIB, URL_HR}
    assert str(ids["hr"]["source_id"]) and str(ids["sbercib"]["source_id"])


# ─── т2: связь, чья страница называет значение, не тронута ────────────────────────


@pytest.mark.asyncio
async def test_link_whose_page_names_the_value_is_not_touched(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Контрастное утверждение с единственной связью на страницу, где значение названо целым
    токеном, в план не попадает: план перечисляет только то, что проваливает тот же гейт.
    Связь стендового утверждения на hr-ratings («значение есть») также не входит в снимаемые."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_replay(scratch_url, store)
    await _seed_second_claim(scratch_url, store)

    report = await _audit(scratch_url, store)
    statements = {row.statement for row in report.rows}
    assert STATEMENT_KEY_RATE not in statements, report.rows
    assert statements == {STATEMENT_83}, report.rows

    row = _row_of(report, STATEMENT_83)
    assert URL_HR not in row.excluded_source_uris, row.excluded_source_uris
    # утверждение с честной связью проверено, но изменений по нему нет
    assert report.claims_checked == 2, report.claims_checked

    assert await _assessment_of(scratch_url, STATEMENT_KEY_RATE) == ("E1", "hypothesis")
    after = await _counts(scratch_url)
    assert after["evidence"] == 4 and after["claims"] == 2, after


# ─── т3: текста нет — связь называется и не трогается ─────────────────────────────


@pytest.mark.asyncio
async def test_link_without_saved_page_text_is_named_and_left_alone(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Строка источника эпохи до нормализованного артефакта (тестовая эмуляция: у строки
    `sources` убран `normalized_sha256`, из payload существующего `research_fetch_completed` —
    тоже; запасной путь поиска текста при этом исчерпан). Связь называется «текста нет», в
    пересчёт не входит: группы и оценка утверждения не меняются, записей нет."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_second_claim(scratch_url, store)
    row = await _scalar(
        scratch_url, "SELECT id FROM sources WHERE canonical_uri = :u", {"u": URL_SBERCIB}
    )
    assert row is not None
    source_id = uuid.UUID(str(row[0]))
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE sources SET metadata = metadata - 'normalized_sha256' WHERE id = :id"),
                {"id": str(source_id)},
            )
            await conn.execute(
                text(
                    "UPDATE audit_events SET payload = payload - 'normalized_sha256' "
                    "WHERE type = 'research_fetch_completed' AND payload->>'source_id' = :id"
                ),
                {"id": str(source_id)},
            )
    finally:
        await engine.dispose()

    before = await _counts(scratch_url)
    report = await _audit(scratch_url, store)
    row = _row_of(report, STATEMENT_KEY_RATE)

    assert len(row.links) == 1 and row.links[0].verdict == VERDICT_NO_TEXT, row.links
    assert "не записан" in row.links[0].note, row.links
    assert row.value_absent_links == () and row.excluded_source_uris == (), row.links
    assert row.groups_changed is False, (row.old_groups, row.new_groups)
    assert row.old_groups == row.new_groups, (row.old_groups, row.new_groups)
    # «текста нет» не притворяется пересчётом: оценка остаётся записанной
    assert (row.persisted_grade, row.persisted_status) == ("E1", "hypothesis")
    assert row.new_grade == "E1" and row.assessment_changed is False
    assert report.links_missing_text == 1 and report.links_without_value == 0, report

    after = await _counts(scratch_url)
    assert after == before, (before, after)


# ─── т4: режим по умолчанию ничего не пишет ───────────────────────────────────────


@pytest.mark.asyncio
async def test_default_run_writes_not_a_single_row(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Без параметров: прогон по всем утверждениям effective-снапшота. Счётники ключевых таблиц
    (включая audit_events и reassessment_jobs) до и после идентичны — предпросмотр не каскад."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_replay(scratch_url, store)
    await _seed_second_claim(scratch_url, store)

    before = await _counts(scratch_url)
    report = await _audit(scratch_url, store)
    after = await _counts(scratch_url)

    assert len(report.rows) == 1 and report.links_without_value == 2, report.rows
    assert report.config_snapshot_id is not None
    assert after == before, (before, after)
    assert report.skipped == (), report.skipped


# ─── т5: идемпотентность ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeated_run_yields_the_same_plan_and_still_writes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Менять нечего: повторный прогон даёт побайтово тот же план (те же связи, те же исходы,
    те же пересчёты) и снова не пишет ни одной строки."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_replay(scratch_url, store)

    first = await _audit(scratch_url, store)
    between = await _counts(scratch_url)
    second = await _audit(scratch_url, store, since=EPOCH)
    after = await _counts(scratch_url)

    assert second.rows == first.rows, (first.rows, second.rows)
    assert second.config_snapshot_id == first.config_snapshot_id
    assert (second.links_checked, second.links_without_value) == (
        first.links_checked,
        first.links_without_value,
    )
    assert after == between == after, (between, after)


# ─── т6: утверждение под коррекцией оператора пропускается ────────────────────────


@pytest.mark.asyncio
async def test_claim_under_operator_correction_is_skipped_with_named_reason(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Действующая коррекция оператора (ADR-0031) касается источника связи — автоматический
    прогон не перешагивает решение оператора молча: утверждение уходит в «пропущено» с
    названной причиной и не попадает в строки плана."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ids = await _seed_replay(scratch_url, store)
    sbercib_id = uuid.UUID(str(ids["sbercib"]["source_id"]))
    hr_id = uuid.UUID(str(ids["hr"]["source_id"]))

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO source_graph_corrections "
                    "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
                    "VALUES (:id, :a, 'merge', :f, :t, 'rules-v1', true)"
                ),
                {"id": str(uuid.uuid4()), "a": "operator:hostctl", "f": str(sbercib_id), "t": str(hr_id)},
            )
    finally:
        await engine.dispose()

    before = await _counts(scratch_url)
    report = await _audit(scratch_url, store)

    assert report.rows == (), report.rows
    assert len(report.skipped) == 1 and "коррекцией оператора" in report.skipped[0].reason
    assert URL_SBERCIB in report.skipped[0].reason or URL_HR in report.skipped[0].reason
    after = await _counts(scratch_url)
    assert after == before, (before, after)


# ─── параметры окна: --since и --claims ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_parameters_select_claims_without_changing_the_plan(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """`--since` — утверждения, у которых есть связь, записанная не раньше даты (связь вне окна
    → утверждения в плане нет); `--claims` — явный список uuid. Ни один вариант не пишет."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_replay(scratch_url, store)
    row_id = await _scalar(
        scratch_url, "SELECT id FROM claims WHERE statement = :s", {"s": STATEMENT_83}
    )
    assert row_id is not None
    claim_id = uuid.UUID(str(row_id[0]))

    future = await _audit(scratch_url, store, since=datetime(2100, 1, 1, tzinfo=UTC))
    assert future.rows == () and future.claims_checked == 0, future.rows

    windowed = await _audit(scratch_url, store, since=EPOCH)
    assert {row.claim_id for row in windowed.rows} == {claim_id}, windowed.rows

    only_other = await _audit(scratch_url, store, claim_ids=[uuid.uuid4()])
    assert only_other.rows == () and only_other.claims_checked == 0, only_other.rows

    selected = await _audit(scratch_url, store, claim_ids=[claim_id])
    assert {row.claim_id for row in selected.rows} == {claim_id}, selected.rows
    # тот же план, что без фильтра: окно и список меняют охват, а не вывод
    whole = await _audit(scratch_url, store)
    assert selected.rows == whole.rows, (selected.rows, whole.rows)


# ─── cli-контракт T7.59 (CliRunner, свой event loop у команды) ─────────────────────


def _with_engine(url: str, fn: Callable[[Any], Any]) -> Any:
    async def _main() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def test_cli_link_audit_prints_the_plan_and_writes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    asyncio.run(_seed_replay(scratch_url, store))
    before = _with_engine(scratch_url, lambda engine: _counts(scratch_url))

    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url, "NOEZEMA_DATA_ROOT": str(tmp_path)}

    result = runner.invoke(main, ["link-audit"], env=env)
    assert result.exit_code == 0, result.output
    out = result.output
    assert "link-audit: только чтение" in out
    assert "затронутых утверждений: 1" in out
    assert f"связь {URL_SBERCIB}" in out and VERDICT_VALUE_ABSENT in out
    assert f"связь {URL_HR}" in out and VERDICT_VALUE_PRESENT in out
    assert "значения 9.6 на сохранённом тексте страницы нет" in out
    assert "значение 9.6 на сохранённом тексте страницы есть" in out
    assert "уходили бы из участников групп: " + URL_SBERCIB in out
    assert "группы было (2)" in out and "группы стало (1)" in out
    assert "оценка было: E3 supported" in out and "стало: E1 hypothesis" in out
    assert "связей без значения: 2" in out
    assert "запись не выполнена" in out

    after = _with_engine(scratch_url, lambda engine: _counts(scratch_url))
    assert after == before, (before, after)

    again = runner.invoke(main, ["link-audit", "--since", "2000-01-01T00:00:00Z"], env=env)
    assert again.exit_code == 0 and "затронутых утверждений: 1" in again.output
    assert _with_engine(scratch_url, lambda engine: _counts(scratch_url)) == before

    filtered = runner.invoke(main, ["link-audit", "--since", "2100-01-01T00:00:00Z"], env=env)
    assert filtered.exit_code == 0 and "затронутых утверждений: 0" in filtered.output


def test_cli_link_audit_is_read_only_by_default_and_rejects_bad_parameters(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Без `--apply` команда — только план, и она не пишет ничего; неполное `--apply`
    (без `--evidence`) отклоняется как отказ с названной причиной, тоже без записей.

    HISTORY (T7.124): тогда `--apply` не было вовсе, и опция отклонялась как неизвестная —
    заглушки, которая всегда отказывает, AGENTS §4 запрещает. С T7.125 (ADR-0039) применение
    есть, но только по явному перечню улик; сам путь применения и его отказы закреплены в
    `tests/scenario/test_link_revocations_t7125.py`, а здесь остаётся проверка того, что
    read-only режим остался read-only."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    asyncio.run(_seed_replay(scratch_url, store))
    before = _with_engine(scratch_url, lambda engine: _counts(scratch_url))

    runner = CliRunner()
    env = {"NOEZEMA_DATABASE_URL": scratch_url, "NOEZEMA_DATA_ROOT": str(tmp_path)}

    applied = runner.invoke(main, ["link-audit", "--apply"], env=env)
    assert applied.exit_code == EXIT_USAGE_ERROR, applied.output
    assert "--apply" in applied.output
    assert _with_engine(scratch_url, lambda engine: _counts(scratch_url)) == before

    bad_since = runner.invoke(main, ["link-audit", "--since", "вчера"], env=env)
    assert bad_since.exit_code == EXIT_USAGE_ERROR and "--since" in bad_since.output

    bad_claims = runner.invoke(main, ["link-audit", "--claims", "83e7a1c8"], env=env)
    assert bad_claims.exit_code == EXIT_USAGE_ERROR and "--claims" in bad_claims.output

    assert _with_engine(scratch_url, lambda engine: _counts(scratch_url)) == before


def test_cli_link_audit_without_database_url_exits_two() -> None:
    result = CliRunner().invoke(main, ["link-audit"], env={})
    assert result.exit_code == EXIT_USAGE_ERROR
