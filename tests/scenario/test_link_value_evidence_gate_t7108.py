"""T7.108 (часть а, ADR-0034 §10): связка обязана нести значение утверждения.

Стендовый случай 2026-08-29 (claim 7cfbe942 «…составил 2,2% по данным Росстата»): куратор
привязал supports-связи к трём страницам Росстата (раздел, открытый реестр, список файлов),
ни на одной из которых значения 2,2 нет — карточка и summary утверждали сверку с источником,
которого по факту нет. Граница куратора теперь снимает такую связь с именованной причиной:
`supports` остаётся, только если фрагмент ИЛИ весь сохранённый текст страницы содержит хотя
бы одно измеренное значение утверждения отдельной текстовой формой (та же канонизация чисел,
что у детектора атрибуции и relay-гейта; границы формы — T7.108a, ADR-0034 §10). Страница без
значения не становится уликой; наблюдение и источник остаются в базе.

Что закреплено тестом:
- снятая связь — аддитивный ключ `rejected_links` в payload уже существующего события
  `claim_created` (паттерн `rejected_operations` T7.105); нового AuditEventType нет;
- если у операции сняты ВСЕ её связи — операция снимается причиной
  `claim[<i>]: no linked evidence contains the claim value` (запись варианта B, связи названы
  в ней; отдельной записью связи уже не дублируются);
- снятые связи видны гейту дубликатов и предпроверке правил: движок видит уже
  отфильтрованные связи (ADR-0034 §10);
- `counters` гейт не трогает (опровержение обязано жить, даже когда опровергающий текст
  значения не называет); утверждение без измеренного значения не проверяется вовсе;
- размер файла («12,2 Мб») и прочие граничные числа страницы — не подтверждение;
- витрина при снятых связях без снятых операций остаётся прежней (summary не меняется);
- T7.108a: табличная строка первоисточника «2026 2,3 2,2» (соседние ячейки через пробел и
  перевод строки) подтверждает значение 2,2 — связь с такой страницей остаётся.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.fetch import FetchResult
from tests.conftest import FakeLLM
from tests.scenario.test_dateless_curator_reverify_order import _rejection_events
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_reverify_temporal_scenario import (
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question

pytestmark = pytest.mark.scenario

CLAIM_AUG = (
    "Официальный уровень безработицы в России в августе 2026 года составил 2,2% "
    "по данным Росстата."
)
CLAIM_MAY = (
    "Официальный уровень безработицы в России в мае 2026 года составил 2,1% "
    "по данным Росстата."
)
CLAIM_TARM = (
    "Официальный уровень безработицы в Тармении по итогам квартала 2031 года составил 2,2%."
)
CLAIM_NO_VALUE = (
    "Сервис «Гелиос» зафиксировал снижение числа вакансий в Тармении после июня 2031 года."
)

URL_INTERFAX = "https://interfax.example/business/1119461"
URL_SMARTLAB = "https://smart-lab.example/blog/1349924"
URL_ROSSTAT_SECTION = "https://rosstat.example/labour_force"
URL_OPENDATA = "https://opendata.example/unemploymentrate2"
URL_FILES = "https://files.example/Rab_sila_2026"
URL_VEER = "https://veer-review.example/labor-2026"
URL_MAY = "https://interfax.example/business/may-2026-report"
URL_TABLE = "https://tarmstat.example/quarterly-table"
URL_GELIOS = "https://gelios-service.example/news/vacancies-2031"
URL_ROSSTAT_TABLE = "https://rosstat.example/monthly-unemployment-table"

_HEAD = (
    "Обзор рынка труда Тармении. Уровень занятости растёт третий квартал подряд. "
    "Безработица обсуждается в региональных сводках и на профильных форумах. "
)
URL_LONGREVIEW = "https://tarmstat.example/long-review"

PAGES: dict[str, list[str]] = {
    URL_INTERFAX: [
        # грамматику «как сообщает <алиас>» понимает страничный детектор первоисточника
        # (ATTRIBUTION_TEMPLATES); «свидетельствуют данные» — нет: фикстура обязана быть
        # строгой уликой, иначе сценарий проверяет не то, что задумано
        "Уровень безработицы в России в августе 2026 года снизился до 2,2% с 2,3% в июле, "
        "как сообщает Росстат.",
        "Это минимальное значение за всё время наблюдений.",
    ],
    URL_SMARTLAB: [
        "Блог инвестора. Коротко о рынке труда.",
        "Безработица в России — 2,2% по итогам августа.",
        "Настроения на рынке труда медленно улучшаются третий квартал подряд.",
        "Данная публикация является личным мнением автора. Мнение владельца сайта может "
        "не совпадать с мнением автора.",
        "Источники: Росстат и обзоры рынка труда.",
    ],
    URL_ROSSTAT_SECTION: [
        "Раздел «Труд и занятость». Официальные публикации ведомства.",
        "Методика оценки уровня безработицы описана в приложении к выпуску "
        "от 15 сентября 2026 года.",
        "Оперативные данные публикуются в открытом реестре; таблица месяца выходит "
        "отдельным файлом.",
    ],
    URL_OPENDATA: [
        "Открытые данные: набор «Уровень безработицы».",
        "Последнее наблюдение: август 2026 года.",
        "Выгрузка доступна в форматах CSV и XLSX.",
    ],
    URL_FILES: [
        "Файлы раздела «Труд и занятость».",
        "Rab_sila_2026.xlsx (52.22 Кб).",
        "Уровень безработицы по месяцам: таблица (12,2 Мб).",
        "Архив выпусков за период 2019–2026.",
    ],
    URL_VEER: [
        "Независимый обзор «Веер»: оценка напряжённости рынка труда выше официальной.",
        "Рынок труда охлаждается медленнее, чем следует из сводок.",
    ],
    URL_MAY: [
        "Уровень безработицы в России в мае 2026 года снизился до 2,1%, "
        "следует из данных Росстата.",
    ],
    URL_TABLE: [
        "Ежеквартальная таблица Тарменской службы статистики.",
        "Приложение к таблице. Столбец A: 4,1. Столбец B: 2.2. Столбец C: 9,7.",
    ],
    URL_GELIOS: [
        "Новости сервиса «Гелиос»: количество вакансий в Тармении сокращается.",
        "Работодатели сокращают найм после лета.",
    ],
    URL_ROSSTAT_TABLE: [
        # T7.108a: табличная строка первоисточника — соседние ячейки через пробелы и
        # перевод строки (строка годов и строка значений), как в таблицах Росстата
        "Таблица 3. Уровень безработицы по месяцам, процентов.",
        "Год Июль Август",
        "2026 2,3 2,2",
    ],
}
# значение стоит только в хвосте длинной страницы — вне окон фрагмента (T7.82)
PAGES[URL_LONGREVIEW] = [_HEAD * 60, "Приложение к обзору. Столбец A: 4,1. Столбец B: 2.2. Столбец C: 9,7."]


class FakeEngineClient:
    """Подмена HTTP-слоя research proxy: страницы отдаются абзацами `<p>` — нормализованный
    текст артефакта равен фикстуре построчно (строка «Источники:» обязана быть строкой)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
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
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _payload() -> dict[str, Any]:
    payload = copy.deepcopy(_curated_payload())
    payload["research_proxy"]["rate_limit_max"] = 20
    return payload


def _claim(statement: str, *, as_of: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "statement": statement,
        "claim_type": "temporal_fact" if as_of else "external_fact",
    }
    if as_of:
        entry["as_of"] = as_of
    return entry


def _links(*pairs: tuple[int, int], relation: str = "supports") -> list[dict[str, Any]]:
    return [
        {"evidence_index": e, "claim_index": c, "relation": relation} for e, c in pairs
    ]


def _answer(
    claims: list[dict[str, Any]], links: list[dict[str, Any]], *, summary: str = "Итог по источникам."
) -> dict[str, Any]:
    return {
        "content": {
            "summary": summary,
            "claims": claims,
            "evidence_links": links,
            "new_questions": [],
        }
    }


def _complete(rationale: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": rationale,
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


async def _run(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_text: str,
    script: list[dict[str, Any]],
    tag: str,
) -> Any:
    question_id = await _seed_question(scratch_url, question_text)
    fake_llm.script(script)
    return await _run_session(
        scratch_url,
        fake_llm,
        tmp_path / f"ws{tag}",
        tmp_path / f"art{tag}",
        question_id,
    )


async def _claim_created_payloads(scratch_url: str, session_id: Any) -> list[dict[str, Any]]:
    rows = await _all(
        scratch_url,
        "SELECT payload, public_summary FROM audit_events WHERE session_id = :s "
        "AND type = 'claim_created' AND payload ? 'claims' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(dict(row["payload"]), public_summary=row["public_summary"]) for row in rows]


async def _fixation_count(scratch_url: str, session_id: Any) -> int:
    rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM audit_events WHERE session_id = :s "
        "AND payload ? 'curator_correction_requested'",
        {"s": str(session_id)},
    )
    return int(dict(rows[0])["n"])


def _fixation_requests(fake_llm: FakeLLM) -> list[str]:
    return [
        str(r.get("last_user", ""))
        for r in fake_llm.requests()
        if "# Исправление предложения" in str(r.get("last_user", ""))
    ]


async def _evidence_statements(scratch_url: str) -> list[str]:
    rows = await _all(
        scratch_url,
        "SELECT c.statement AS statement FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "ORDER BY c.statement, e.id",
    )
    return [str(row["statement"]) for row in rows]


async def _pairing_of(scratch_url: str, statement: str, canonical_uri: str) -> dict[str, Any] | None:
    row = await _scalar(
        scratch_url,
        "SELECT e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN claims c ON c.id = e.claim_id JOIN sources s ON s.id = e.source_id "
        "WHERE c.statement = '" + statement.replace("'", "''") + "' "
        "AND s.canonical_uri = '" + canonical_uri.replace("'", "''") + "'",
    )
    assert row is not None, f"улики {canonical_uri} у утверждения нет"
    return None if row[0] is None else dict(row[0])


def _reason(link_index: int, evidence_index: int, claim_index: int) -> str:
    return (
        f"evidence_link[{link_index}]: evidence {evidence_index} "
        f"does not contain the value of claim {claim_index}"
    )


def _entry(link_index: int, evidence_index: int, claim_index: int, relation: str = "supports") -> dict[str, Any]:
    return {
        "link_index": link_index,
        "evidence_index": evidence_index,
        "claim_index": claim_index,
        "relation": relation,
        "reason": _reason(link_index, evidence_index, claim_index),
    }


QUESTION_AUG = (
    "Какой официальный уровень безработицы в России по последним данным Росстата "
    "и какие независимые оценки рынка труда существуют? Отдели официальные цифры от "
    "независимых оценок."
)

RATIONALE_AUG = (
    'Прочитал официальные и независимые тексты: "как сообщает Росстат." и '
    '"Безработица в России — 2,2% по итогам августа." "Оперативные данные публикуются '
    'в открытом реестре" Числа совпадают, сверка готова.'
)
#: перепроверка explorer требует, чтобы названные в rationale числа встречались в прочитанных
#: страницах: для сценариев без страницы со значением значения в rationale не называем
RATIONALE_MAY = (
    'Прочитал официальные тексты: "следует из данных Росстата." и "Оперативные данные '
    'публикуются в открытом реестре" Уровень мая назван на странице сводки, сверка готова.'
)
RATIONALE_NO_VALUE = (
    'Прочитал официальные страницы: "Оперативные данные публикуются в открытом реестре" и '
    '"Последнее наблюдение: август 2026 года". Числовых значений показателя в цитатах нет.'
)


# ── т1: стендовый случай — три страницы без значения теряют supports-связи ─────


@pytest.mark.asyncio
async def test_valueless_rosstat_attachments_lose_support_links(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG,
        [
            _fetch(URL_INTERFAX),
            _fetch(URL_SMARTLAB),
            _fetch(URL_ROSSTAT_SECTION),
            _fetch(URL_OPENDATA),
            _fetch(URL_FILES),
            _complete(RATIONALE_AUG),
            _answer(
                [_claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00")],
                _links((0, 0), (1, 0), (2, 0), (3, 0), (4, 0)),
            ),
        ],
        "t7108a1",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # утверждение записано; улики — только те две страницы, где значение есть
    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_AUG, CLAIM_AUG], evidence

    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    # снятые связи — не снятые операции: счётчиков отказов нет (паттерн T7.105 — ключи
    # `claims_proposed`/`claims_applied` появляются только когда снята операция)
    assert "claims_proposed" not in payload and "claims_applied" not in payload
    assert "rejected_operations" not in payload
    assert payload["rejected_links"] == [
        _entry(2, 2, 0),
        _entry(3, 3, 0),
        _entry(4, 4, 0),
    ]
    # снятые связи — не снятые операции: публичная строка события прежняя
    assert payload["public_summary"] == "curator: 1 claims, 0 questions"

    # атрибуция живёт только на оставшихся связях: строгий режим на издании,
    # блог со списком источников (T7.107), порядок связей прямой.
    # T7.129a (ADR-0029 §26.3): на блоге было `bibliography_known_producer` (список источников
    # страницы), стало `statement_names_producer`: утверждение само называет Росстат, а блог
    # это значение носит. Первоисточник не изменился (rosstat); пятый режим не требует уже
    # записанного первоисточника той же улики, поэтому решает раньше библиографии
    strict = await _pairing_of(scratch_url, CLAIM_AUG, URL_INTERFAX)
    assert strict is not None and strict["pairing"] == "value_and_primary_in_fragment", strict
    blog = await _pairing_of(scratch_url, CLAIM_AUG, URL_SMARTLAB)
    assert blog is not None and blog["pairing"] == "statement_names_producer", blog
    assert blog["primary_key"] == "rosstat", blog
    assert fake_llm.state()["queue_left"] == 0


# ── т2: значение в полном тексте страницы, вне окон фрагмента — связь остаётся ───


@pytest.mark.asyncio
async def test_value_in_full_page_outside_fragment_windows_keeps_link(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    rationale = (
        'Прочитал обзор: "Уровень занятости растёт третий квартал подряд." Про безработицу '
        "в обзоре есть числовое приложение; значения в цитируемых строках обзора я не называю."
    )
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        "Какой уровень безработицы в Тармении по итогам квартала 2031 года?",
        [
            _fetch(URL_LONGREVIEW),
            _complete(rationale),
            _answer(
                [_claim(CLAIM_TARM, as_of="2031-10-01T00:00:00+00:00")],
                _links((0, 0)),
            ),
        ],
        "t7108a2",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_TARM], "связь снята, хотя значение есть на сохранённой странице"
    payloads = await _claim_created_payloads(scratch_url, session.session_id)
    assert "rejected_links" not in payloads[0]


# ── т3: граничные числа страницы — не подтверждение; голое число таблицы — да ──


@pytest.mark.asyncio
async def test_file_sizes_are_not_the_claim_value_bare_table_number_is(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG + " По Тармении есть ежеквартальная таблица.",
        [
            _fetch(URL_INTERFAX),
            _fetch(URL_FILES),
            _fetch(URL_TABLE),
            _complete(RATIONALE_AUG),
            _answer(
                [
                    _claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00"),
                    _claim(CLAIM_TARM, as_of="2031-10-01T00:00:00+00:00"),
                ],
                _links((0, 0), (1, 0), (2, 1)),
            ),
        ],
        "t7108a3",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    evidence = await _evidence_statements(scratch_url)
    assert evidence == sorted([CLAIM_AUG, CLAIM_TARM]), evidence
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    # «52.22 Кб» и «12,2 Мб» — не «2,2%»; голое «2.2» в таблице — подтверждение
    assert payload["rejected_links"] == [_entry(1, 1, 0)]


# ── т4: утверждение без измеренного значения гейт не проверяется ───────────────


@pytest.mark.asyncio
async def test_claim_without_measured_value_is_not_checked(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        "Как меняется число вакансий на сервисе «Гелиос» в Тармении?",
        [
            _fetch(URL_GELIOS),
            _complete('Прочитал новости сервиса: "количество вакансий в Тармении сокращается."'),
            _answer([_claim(CLAIM_NO_VALUE)], _links((0, 0))),
        ],
        "t7108a4",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_NO_VALUE], evidence
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert "rejected_links" not in payload


# ── т5: опровержение без значения живёт; снятая supports-связь не снимает операцию ─


@pytest.mark.asyncio
async def test_counterevidence_survives_and_operation_stays(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG,
        [
            _fetch(URL_INTERFAX),
            _fetch(URL_ROSSTAT_SECTION),
            _fetch(URL_VEER),
            _complete(RATIONALE_AUG),
            _answer(
                [_claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00")],
                _links((0, 0), (1, 0)) + _links((2, 0), relation="counters"),
            ),
        ],
        "t7108a5",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # supports без значения снят (запись), counters без значения жив (гейт его не трогает),
    # операция остаётся: снята не все её связи
    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_AUG, CLAIM_AUG], evidence
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert payload["rejected_links"] == [_entry(1, 1, 0)]
    assert "rejected_operations" not in payload
    # операция цела: счётчиков отказов в событии нет
    assert "claims_applied" not in payload and "claims_proposed" not in payload


# ── т6: у операции сняты все связи — операция снимается названной причиной ──────


@pytest.mark.asyncio
async def test_operation_with_all_links_removed_is_dropped_by_name(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG,
        [
            _fetch(URL_ROSSTAT_SECTION),
            _fetch(URL_MAY),
            _complete(RATIONALE_MAY),
            _answer(
                [
                    _claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00"),
                    _claim(CLAIM_MAY, as_of="2026-06-01T00:00:00+00:00"),
                ],
                _links((0, 0), (1, 1)),
            ),
        ],
        "t7108a6",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_MAY], evidence
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert payload["claims_proposed"] == 2 and payload["claims_applied"] == 1
    entry = payload["rejected_operations"][0]
    assert entry["claim_index"] == 0
    assert entry["reason"] == "claim[0]: no linked evidence contains the claim value"
    assert entry["evidence_links_rejected"] == [{"claim_index": 0, "evidence_index": 0, "relation": "supports"}]
    # связи снятой операции названы её записью — отдельной записью связи не дублируются
    assert "rejected_links" not in payload
    assert payload["public_summary"] == (
        "curator: 1 of 2 claims applied, 0 questions, 1 operations rejected by host validation"
    )


# ── т7: все операции сняты из-за связей без значения — отказ целиком + один раунд ─


@pytest.mark.asyncio
async def test_all_operations_dropped_by_links_rejects_once_with_correction_round(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    bad = _answer(
        [_claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00")],
        _links((0, 0), (1, 0), (2, 0)),
        summary="Итог (первый ответ).",
    )
    still_bad = _answer(
        [_claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00")],
        _links((0, 0), (1, 0), (2, 0)),
        summary="Итог (исправленный ответ).",
    )
    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG,
        [
            _fetch(URL_ROSSTAT_SECTION),
            _fetch(URL_OPENDATA),
            _fetch(URL_FILES),
            _complete(RATIONALE_NO_VALUE),
            bad,
            still_bad,
        ],
        "t7108a7",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # ровно один раунд исправления; повторный тот же отказ — финальный, третьего вызова нет
    assert await _fixation_count(scratch_url, session.session_id) == 1
    fixations = _fixation_requests(fake_llm)
    assert len(fixations) == 1
    assert "no linked evidence contains the claim value" in fixations[0]
    assert "does not contain the value of claim 0" in fixations[0]
    assert fake_llm.state()["queue_left"] == 0

    rejections = await _rejection_events(scratch_url, session.session_id)
    assert len(rejections) == 1
    payload = rejections[0]
    assert payload["curator_rejected"] == ["claim[0]: no linked evidence contains the claim value"]
    assert payload["curator_reject_kind"] == "evidence_value_absent"
    assert "rejected_proposal" in payload
    assert payload["rejected_links"] == [_entry(0, 0, 0), _entry(1, 1, 0), _entry(2, 2, 0)]
    reasons = payload["correction_round"]["reasons"]
    assert "claim[0]: no linked evidence contains the claim value" in reasons

    # знание не изменилось: staging не исполнялся
    assert await _evidence_statements(scratch_url) == []
    assert await _claim_created_payloads(scratch_url, session.session_id) == []


# ── т8 (T7.108a): строка таблицы первоисточника «2026 2,3 2,2» — связь остаётся ──


@pytest.mark.asyncio
async def test_rosstat_table_row_page_keeps_the_link(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, fake_engine: None, tmp_path: Path
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, _payload())).state == "active"

    session = await _run(
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_AUG + " Есть ежемесячная таблица Росстата с уровнями безработицы по месяцам.",
        [
            _fetch(URL_ROSSTAT_TABLE),
            _fetch(URL_FILES),
            _complete(
                'Прочитал таблицу: "Уровень безработицы по месяцам." Цифр показателя '
                "в цитируемых строках не называю."
            ),
            _answer(
                [_claim(CLAIM_AUG, as_of="2026-09-30T00:00:00+00:00")],
                _links((0, 0), (1, 0)),
            ),
        ],
        "t7108a8",
    )
    assert session.final_state.value == "succeeded", session.termination_reason

    # T7.108a (дефект, найденный менеджером на приёмке beda40a): прежде максимально
    # жадный токен склеивал «2026 2,3 2,2» в одно число — значение 2,2 не находилось, и
    # гейт снимал бы законную связь именно с первичной страницы-таблицы Росстата. Теперь
    # значение найдено во второй ячейке строки, связь жива; страница размеров файлов
    # («52.22 Кб», «12,2 Мб») по-прежнему снимается
    evidence = await _evidence_statements(scratch_url)
    assert evidence == [CLAIM_AUG], evidence
    payload = (await _claim_created_payloads(scratch_url, session.session_id))[0]
    assert "rejected_operations" not in payload
    assert payload["rejected_links"] == [_entry(1, 1, 0)]
