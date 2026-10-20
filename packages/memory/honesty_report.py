"""T7.118: отчёт честности по уже записанным фактам (read-only, ADR-0026 как принцип).

Модуль отвечает на вопрос «чем именно узел ответил и на что он оперся» так, чтобы это
мог проверить человек: состав карточки (созданные / переиспользованные / опертые
утверждения), действующая оценка в активном снимке правил (E-уровень, статус, причины из
ленты), состав групп независимости для E2+ (источник → родитель по записи значения или
страничный указатель) и шаги исследователя из ленты событий. Ничего не пересчитывает
повторно там, где факт уже записан, и НИЧЕГО не пишет: в модуле нет ни одного INSERT /
UPDATE / DELETE, ни audit-событий, ни новых типов событий (закрытый AuditEventType не
расширяется), ни миграций.

Слои (AGENTS §7, «packages/ не импортирует apps/»): карточные запросы `apps/web/answer.py`
и `apps/web/knowledge.py` здесь ПОВТОРЕНЫ текстом запроса (CTE причастности утверждений,
опорные утверждения из payload `claim_created`, причины оценок из ленты, JOIN'ы фактов
действующей оценки) — поведение карточки от этого не меняется, потому что карточку модуль
не вызывает. Приоритет родителя значения для флага Ф1 берут готовые помощники
`packages/memory/source_graph.py`.

Эвристики (ФЛАГИ) — не вердикты: каждая помечена «требует ручной проверки» и описана с
ограничением:

* Ф1 «E2+ при общем производителе»: ≥2 записанных групп независимости содержат источники с
  одним и тем же эффективным родителем (запись значения приоритетнее страничного
  указателя — T7.117), либо в одной группе лежит сам первоисточник, а в другой — его
  перепечатка. Вторая ветка подписи (текст группы называет производителя своими словами)
  требует словарный детектор атрибуции из `apps/` и здесь СОЗНАТЕЛЬНО НЕ реализована.
* Ф2 «возможно не по вопросу»: нормализация основ слов — зеркало ворот почти-дубликатов
  T7.111/T7.112 (`apps/orchestrator/tool_context.py`, ADR-0036): те же слова служебного
  списка, тот же приём фиксированного префикса (ADR-0029 T7.112), без новых словарей.
  Флаг ставится, когда пересечение значимых основ (без чисто числовых токенов — годы и
  проценты не различают тему) пусто, либо вся связь держится на одной общей основе-якоре
  («росси») при трёх и более значимых основах утверждения.
* Ф3 «производитель не назван»: подпись «Опубликовано производителем (…)» выводится из
  ЗАПИСАННЫХ улик (запись о значении `value_attribution` / запись о собственной публикации
  T7.101) и причин оценки; витрина умеет назвать производителя ещё и по страничному
  указателю через словарь `apps/research_proxy/source_attribution.py`, куда `packages/` не
  ходит — поэтому подпись модуля консервативнее витрины, а проверка «назван ли
  производитель в формулировке» делается по основам слов имени записи. Короткие народные
  имена («Росстат» против «Федеральная служба государственной статистики») словарь не
  связывает: флаг может сработать и там, где производитель назван общепринятым именем.
  С T7.118a имя считается названным, только если в формулировке найдены ВСЕ значимые
  основы имени (те же падежные допуски), где значимые — без чисел и без общей
  основы-якоря страны («росси», та же, что у Ф2): раньше хватало одной основы, и подпись
  «Банк России» глохла от одного слова «России», которое есть почти в каждой формулировке
  (дефект найден на реальных данных стенда). Если после исключения якоря у имени основ не
  осталось («Россия») — сравнение по полному набору, как прежде. Ключ словаря остаётся
  отдельной альтернативой проверки.

Зеркало нормализации закреплено тестом `tests/unit/test_honesty_report_flags_t7118.py`:
расхождение с функциями гейта — падение теста (это дефект, а не расхождение текстов).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType
from packages.domain.sanitization import mask_nul
from packages.memory.scope import parse_self_publication, parse_value_attribution
from packages.memory.source_graph import load_source_graph_assembly

#: Стабильная схема JSON-выхода. Меняется только при осознанном изменении формы отчёта.
#: Аддитивные метрики (T7.127: `searches_rate_limited`) версию НЕ поднимают специально:
#: baseline прежней формы обязан читаться и показывать по новой метрике «нет данных», а не
#: отказывать — поднятие версии лишило бы сравнения все прежние прогоны стоек.
SCHEMA_VERSION: Final = "t7118-honesty-report-v1"

#: Суффикс контрольного прогона (часть 3): тексты вопросов заканчиваются на него,
#: выбор `--control-tag` берёт вопросы по точному окончанию текста.
CONTROL_SUFFIX_HEAD: Final = " (контроль "
_CONTROL_TAG_RE: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,40}")

#: Список официальных хостов — та же константа, что у менеджера в `step_budget.py`
#: (fixtures-t7118), для деления поисков/чтений на «официальные» и «прочие».
OFFICIAL_HOSTS: Final[tuple[str, ...]] = (
    "rosstat.gov.ru",
    "gks.ru",
    "cbr.ru",
    "minfin.gov.ru",
    "economy.gov.ru",
    "nalog.gov.ru",
)

#: Действующий снимок правил — та же выборка, что `apps/web/knowledge.py` (реплика текста).
EFFECTIVE_SNAPSHOT_SQL: Final = (
    "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
)

#: Типы событий оценки — реплика `ASSESSMENT_EVENT_TYPES` из `apps/web/knowledge.py`.
ASSESSMENT_EVENT_TYPES: Final[tuple[str, ...]] = (
    AuditEventType.CLAIM_ASSESSED.value,
    AuditEventType.REASSESSMENT_JOB_COMPLETED.value,
)

#: Причины, при которых витрина показывает подпись о производителе (реплика условий
#: `apps/web/producer_view.producer_publication_case` по части причин).
PRODUCER_REASON_CODES: Final[frozenset[str]] = frozenset(
    {"insufficient_independence", "insufficient_evidence"}
)
PRODUCER_LABEL: Final = "Опубликовано производителем"

#: Уровни оценки «E2+» (EffectiveGrade.level >= 2) — границы флагов не изобретают шкалу.
_HIGH_GRADE_LEVELS: Final[frozenset[str]] = frozenset({"E2", "E3", "E4"})

#: Входы отбора и размерные потолки (fail-closed: больше — отказ с объяснением).
MAX_QUESTIONS: Final = 100
MAX_SESSIONS: Final = 400
MAX_CLAIMS: Final = 400
MAX_AUDIT_ROWS_PER_SESSION: Final = 8000

#: Причина отказа поиска по лимиту поиска узла в журнале прокси (T7.127). Строка та же, какую
#: пишет `apps/research_proxy/service.py`; нового AuditEventType не заводилось, поэтому
#: привязка отказа к сессии — по интервалу её жизни и тексту запроса (см. `_session_steps`).
RATE_LIMIT_REJECTION_REASON: Final = "upstream_rate_limit_exceeded"

_TEXT_HEAD_CHARS: Final = 120
_STATEMENT_HEAD_CHARS: Final = 160
_URI_HEAD_CHARS: Final = 90

_QUESTION_STATE_LABELS: Final[dict[str, str]] = {
    "candidate": "ждёт обработки",
    "selected": "выбран",
    "researching": "исследуется",
    "partially_answered": "отвечен частично",
    "verified": "отвечен (закреплён)",
    "rejected": "отклонён",
    "deferred": "отложен",
}

_SESSION_STATE_LABELS: Final[dict[str, str]] = {
    "succeeded": "завершена успешно",
    "succeeded_partial": "завершена частично",
    "failed": "провалена",
    "exploring": "исследование",
    "verifying": "проверка",
    "reporting": "отчёт",
    "committing": "закрепление",
    "aborted": "прервана",
}


class HonestyReportError(Exception):
    """Отчёт не собран: некорректный отбор, превышение потолков, битая схема baseline."""


# ─── зеркало нормализации ворот почти-дубликатов (без новых словарей) ─────────────────
# Копия `SEARCH_QUERY_SERVICE_WORDS` + `normalize_search_query` + `content_word_stems`
# (`apps/orchestrator/tool_context.py`) и `_prefix_stem` (`apps/orchestrator/assertion_window.py`).
# `packages/` не импортирует `apps/`, поэтому приём повторён здесь; тест
# tests/unit/test_honesty_report_flags_t7118.py сверяет зеркало с оригиналами.

_SEARCH_QUERY_SERVICE_WORDS: Final[frozenset[str]] = frozenset(
    [
        "в", "во", "с", "со", "у", "на", "за", "под", "над", "по", "из", "от", "до", "к", "ко",
        "о", "об", "при", "для", "без", "через", "между", "и", "а", "но", "или", "да", "как",
        "что", "чтобы", "если", "тоже", "также", "не", "же", "бы", "ли", "этот", "эта", "эти",
        "его", "её", "ее", "их",
        "год", "года", "году", "годы", "г",
    ]
)

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-zа-я]+")


def normalize_report_words(source: str) -> frozenset[str]:
    """Слова текста для сравнения — зеркало `tool_context.normalize_search_query`."""
    lowered = mask_nul(str(source or "")).lower().replace("ё", "е")
    return frozenset(_WORD_RE.findall(lowered))


def _prefix_stem(term: str) -> str:
    """Зеркало `assertion_window._prefix_stem`: длина 7+ — первые 6 знаков, 5–6 — пять, else целое."""
    if len(term) >= 7:
        return term[:6]
    if len(term) >= 5:
        return term[:5]
    return term


def content_word_stems(source: str) -> frozenset[str]:
    """Зеркало `tool_context.content_word_stems`: служебные слова вон, числа как есть."""
    stems: set[str] = set()
    for token in normalize_report_words(source):
        if token in _SEARCH_QUERY_SERVICE_WORDS:
            continue
        stems.add(token if token.isdigit() else _prefix_stem(token))
    return frozenset(stems)


def significant_stems(source: str) -> frozenset[str]:
    """Основы значимых слов утверждения/вопроса для Ф2: содержательные основы БЕЗ чисто
    числовых токенов. Годы и проценты — содержательные слова для гейта поиска (разный угол
    поиска), но тему утверждения они не различают: на стенде почти все вопросы и утверждения
    говорят про один и тот же год. Ограничение принято сознательно и описано в модуле."""
    return frozenset(stem for stem in content_word_stems(source) if not stem.isdigit())


# ─── классификация хостов и ошибок (реплики приёмов hosts-гейта и менеджера) ─────────

_NON200_RE: Final[re.Pattern[str]] = re.compile(r"^non-200 status: (\d{3})$")
_HTTP_DENY_CODES: Final[frozenset[str]] = frozenset({"401", "403", "429"})


def classify_fetch_error(error: str) -> str | None:
    """Зеркало `apps/orchestrator/host_availability.classify_fetch_error`: вид отказа по
    тексту ошибки хоста — "401"|"403"|"429"|"timeout" либо None (хост не помечается)."""
    value = str(error or "")
    match = _NON200_RE.match(value.strip())
    if match is not None and match.group(1) in _HTTP_DENY_CODES:
        return match.group(1)
    if value.startswith("timeout:"):
        return "timeout"
    return None


def url_host(raw: str) -> str:
    """Хост URL как у менеджера (`step_budget.host`): lower, без www."""
    host = (urlsplit(str(raw or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_official_host(host: str) -> bool:
    """Официальный ли хост — по хост-константе модуля (тот же тест равенства/поддомена)."""
    return any(host == official or host.endswith("." + official) for official in OFFICIAL_HOSTS)


def search_query_official(query: str) -> bool:
    """Поиск отнесён к «официальным», если запрос называет официальный хост: слово-токен
    запроса совпадает с первой меткой официального домена (`rosstat`, `cbr`, …). У поиска
    нет URL — это единственный честный способ поделить поиски по хост-константе;
    формулировки «Росстат» без домена в «официальные» не попадают (ограничение принято)."""
    words = normalize_report_words(query)
    return any(official.split(".")[0] in words for official in OFFICIAL_HOSTS)


# ─── чистые правила флагов ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FlagOutcome:
    """Итог эвристики: код, человеческое имя и текст-пояснение (или None — флага нет)."""

    code: str
    name: str
    message: str | None


def off_topic_flag(statement: str, question_text: str) -> FlagOutcome | None:
    """Ф2 «возможно не по вопросу»: пересечение значимых основ слов утверждения с вопросом
    пусто, либо вся связь держится на ОДНОМ общем слове-якоре (например «росси») при трёх и
    более значимых основах утверждения. Утверждения с <2 значимыми основами не проверяются.

    Почему не «доля общих основ < 1/3»: утверждение всегда длиннее вопроса, и честное
    утверждение по теме («Средняя зарплата в России … превысила 70 тысяч рублей») даёт долю
    2/7 — доля флагует тему, которой нет; якорное правило ловит ровно то, для чего флаг
    задуман: утверждение цепляется к вопросу одним общим словом («Россия»), а его
    содержательные слова принадлежат другой теме. Ограничение принято сознательно и описано
    в шапке модуля."""
    stems = significant_stems(statement)
    if len(stems) < 2:
        return None
    shared = stems & significant_stems(question_text)
    if not shared:
        return FlagOutcome(
            code="f2",
            name="возможно не по вопросу",
            message="пересечение значимых основ слов утверждения с вопросом пусто",
        )
    if len(shared) == 1 and len(stems) >= 3:
        anchor = sorted(shared)[0]
        return FlagOutcome(
            code="f2",
            name="возможно не по вопросу",
            message=(
                f"связь с вопросом держится на одной общей основе «{anchor}» при "
                f"{len(stems)} значимых основах утверждения — возможно, это вывод по другой теме"
            ),
        )
    return None


#: Общая основа-якорь страны «росси» — ровно та основа, которую Ф2 называет единственной
#: общей основой-якорем (пример в `off_topic_flag`; там якорь берётся из данных, а не из
#: словаря, поэтому здесь он выведен тем же `_prefix_stem` из примера Ф2 — без новых
#: словарей). Для Ф3 совпадение только с ней — НЕ доказательство названности имени:
#: слово «России» есть почти в каждой формулировке (T7.118a, найдено на данных стенда).
_COUNTRY_ANCHOR_STEM: Final[str] = _prefix_stem("россии")


def _same_word_stem(stem: str, other: str) -> bool:
    """Двусторонний префиксный допуск падежных форм одного слова («банк» и «банка»)."""
    return stem == other or stem.startswith(other) or other.startswith(stem)


def _name_mentioned(name: str, statement: str) -> bool:
    """Названо ли имя производителя в формулировке (T7.118a): должны найтись ВСЕ значимые
    основы имени — с двусторонним префиксным допуском падежей. Значимые основы: без чисел и
    без общей основы-якоря страны (`_COUNTRY_ANCHOR_STEM`, та же основа, что у Ф2): раньше
    хватало одной совпавшей основы, и подпись «Банк России» молчала там, где в формулировке
    было только «России» (дефект Ф3 на реальных данных стенда). Если после исключения
    якоря основ не осталось (имя «Россия») — сравнение по полному набору, как прежде.
    Словарных народных имён модуль не знает (словарь producers живёт в `apps/`) — это
    ограничение флага, описанное в шапке модуля."""
    stems = [
        stem for stem in map(_prefix_stem, normalize_report_words(name)) if not stem.isdigit()
    ]
    if not stems:
        return True  # нечего сравнивать — флаг не ставим (fail-closed на флаге)
    significant = [stem for stem in stems if not _same_word_stem(stem, _COUNTRY_ANCHOR_STEM)]
    compared = significant or stems
    statement_stems = {_prefix_stem(word) for word in normalize_report_words(statement)}
    return all(
        any(_same_word_stem(stem, word_stem) for word_stem in statement_stems)
        for stem in compared
    )


def producer_signature(
    *,
    names: Sequence[tuple[str, str]],
    group_count: int,
    has_counters: bool,
    reasons: Sequence[str],
) -> tuple[str, str] | None:
    """Подпись «Опубликовано производителем (X)» по ЗАПИСАННЫМ уликам действующей оценки.

    `names` — имена улик-подтверждений (запись о значении или запись о собственной
    публикации): пары (ключ словаря, имя). Условия — как у витрины (`producer_publication_case`),
    но консервативнее: каждая улика обязана иметь запись-имя (витрина дополнительно умеет
    называть производителя по страничному указателю через словарь `apps/`), ровно одна
    группа, причин недостаточности достаточно любых (у витрины — конкретные коды), счётчиков
    нет. Возвращает (имя, ключ) либо None.
    """
    if not names or group_count != 1 or has_counters:
        return None
    if not any(reason in PRODUCER_REASON_CODES for reason in reasons):
        return None
    keys = {key for key, _ in names}
    display = {name for _, name in names}
    if len(keys) != 1 or len(display) != 1:
        return None  # имена разошлись — общий производитель не опознан (fail-closed)
    key, name = names[0]
    return name, key


def producer_not_named_flag(signature: tuple[str, str] | None, statement: str) -> FlagOutcome | None:
    """Ф3: подпись есть, а производитель в формулировке не опознан по основам слов."""
    if signature is None:
        return None
    name, key = signature
    if _name_mentioned(name, statement) or _name_mentioned(key, statement):
        return None
    return FlagOutcome(
        code="f3",
        name="производитель не назван в формулировке",
        message=(
            f"подпись «{PRODUCER_LABEL} ({name})», имя производителя в формулировке не найдено "
            "(проверьте: возможно, он назван другим именем)"
        ),
    )


def shared_producer_flag(
    *,
    grade: str | None,
    groups: Mapping[str, Iterable[str]],
    effective_parent: Mapping[str, str | None],
) -> FlagOutcome | None:
    """Ф1 «E2+ при общем производителе» (записанные группы + записанные родители).

    `groups` — записанные группы независимости действующей оценки (group_id → id источников),
    `effective_parent` — эффективный родителя источника: запись значения приоритетнее
    страничного указателя (T7.117, реплика `grouping_arguments`). Флаг, если:
    (а) два разных рода содержат источники с одним и тем же родителем; либо
    (б) в одной группе лежит источник P, а в другой — источник, у которого P — родитель
    (первоисточник и его перепечатка числятся независимыми группами).
    Вторая ветка подписи (текст группы называет производителя словами) требует детектор
    атрибуции из `apps/` и здесь не реализована — см. шапку модуля."""
    if grade is None or grade not in _HIGH_GRADE_LEVELS:
        return None
    members = {group: set(source_ids) for group, source_ids in groups.items()}
    parents_by_group: dict[str, set[str]] = {}
    for group, sources in members.items():
        parents_by_group[group] = {
            parent for parent in (effective_parent.get(sid) for sid in sources) if parent is not None
        }
    group_ids = sorted(members)
    for i, group_a in enumerate(group_ids):
        for group_b in group_ids[i + 1 :]:
            common = parents_by_group[group_a] & parents_by_group[group_b]
            if common:
                parent = sorted(common)[0]
                return FlagOutcome(
                    code="f1",
                    name="E2+ при общем производителе",
                    message=(
                        f"группы {group_a} и {group_b} содержат улики с общим родителем "
                        f"{parent[:8]} — независимость групп под вопросом"
                    ),
                )
            if (parents_by_group[group_b] & members[group_a]) or (
                parents_by_group[group_a] & members[group_b]
            ):
                touched = sorted((parents_by_group[group_b] | parents_by_group[group_a]) & (
                    members[group_a] | members[group_b]))
                parent = touched[0] if touched else "?"
                return FlagOutcome(
                    code="f1",
                    name="E2+ при общем производителе",
                    message=(
                        f"группа {group_a} содержит источник {parent[:8]}, а группа {group_b} — "
                        f"его производную публикацию: первоисточник и перепечатка в разных группах"
                    ),
                )
    return None


# ─── размер отчёта ─────────────────────────────────────────────────────────────────────


@dataclass
class ReportSource:
    source_id: str
    canonical_uri: str
    host: str
    #: как источник связан с родителем: "value_record" (запись значения), "page_pointer"
    #: (страничный указатель), None (родителя нет)
    parent_via: str | None
    parent_source_id: str | None
    parent_uri: str | None
    parent_host: str | None
    #: имя производителя, если улика этого утверждения — собственная публикация (T7.101)
    self_publication: str | None


@dataclass
class ReportGroup:
    group_id: str
    sources: list[ReportSource]


@dataclass
class ReportFlag:
    code: str
    name: str
    message: str
    manual_review: bool = True


@dataclass
class ReportClaim:
    claim_id: str
    statement: str
    statement_head: str
    claim_type: str
    relation: str  # created | reused | reverified | relied
    head_state: str
    grade: str | None
    epistemic_status: str | None
    confidence: float | None
    assessment_id: str | None
    reasons: list[str]
    producer_signature: str | None
    groups: list[ReportGroup]
    flags: list[ReportFlag]


@dataclass
class SessionSteps:
    searches_total: int = 0
    searches_official: int = 0
    searches_other: int = 0
    #: поиски, отклонённые ЛИМИТОМ ПОИСКА УЗЛА (T7.127): узел не спросил движок, поэтому
    #: такой поиск не может быть «пустым» — он не доходил до поисковика
    searches_rate_limited: int = 0
    wasted_searches: int = 0
    fetch_ok_total: int = 0
    fetch_ok_official: int = 0
    fetch_ok_other: int = 0
    fetch_fail_total: int = 0
    #: [{"host": h, "kind": kind|None, "count": n}] — отказы чтения по хостам
    fetch_failures_by_host: list[dict[str, Any]] = field(default_factory=list)
    repeated_failed_host_fetches: int = 0
    fetch_known_bad_host: int = 0
    repeated_reads: int = 0
    gate_near_duplicate: int = 0
    gate_host_unavailable: int = 0
    gate_tool_repeated: int = 0
    other_tools: int = 0
    args_unparsed: int = 0


@dataclass
class ReportSession:
    session_id: str
    state: str
    state_label: str
    termination_reason: str | None
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    steps: SessionSteps


@dataclass
class QuestionReport:
    question_id: str
    text: str
    text_head: str
    state: str
    state_label: str
    priority: int
    created_at: str | None
    answered: bool  # ответ есть / ответ не записан
    missing_sessions: bool
    sessions: list[ReportSession]
    claims: dict[str, list[ReportClaim]]  # created/reused/reverified/relied
    flag_counts: dict[str, int]


@dataclass
class HonestyReport:
    schema_version: str
    generated_at: str
    selection: dict[str, Any]
    questions: list[QuestionReport]
    totals: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── служебное ─────────────────────────────────────────────────────────────────────────


def _in_params(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    names = [f"{prefix}{index}" for index in range(len(values))]
    clause = ", ".join(f":{name}" for name in names)
    params: dict[str, Any] = dict(zip(names, values, strict=True))
    return clause, params


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _head(text_value: str, limit: int) -> str:
    collapsed = " ".join(str(text_value or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _as_uuid(value: Any) -> uuid.UUID | None:
    """asyncpg возвращает свой UUID-тип (AGENTS §7): guard перед приведением."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def parse_since(raw: str) -> datetime:
    """ISO-дата `--since` с обязательным переходом в UTC (naive-время трактуется как UTC)."""
    value = str(raw or "").strip()
    if not value:
        raise HonestyReportError("--since пуст")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HonestyReportError(f"--since не ISO-дата: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _control_suffix(control_tag: str) -> str:
    tag = str(control_tag or "").strip()
    if not _CONTROL_TAG_RE.fullmatch(tag):
        raise HonestyReportError(
            "--control-tag должен состоять из букв, цифр, точки, подчеркивания или дефиса"
        )
    return f"{CONTROL_SUFFIX_HEAD}{tag})"


# ─── шаги исследователя: лента → счётчики (реплики правил менеджера и ворот T7.111/T7.12) ──


def search_query_from_upstream_url(url: str) -> str | None:
    """По upstream-адресу отказа лимита восстановить запрос поиска (T7.127).

    Журнал отказов прокси (`research_fetch_rejected`) хранит только адрес обращённого запроса
    (`upstream_request_url` прокси: `...?format=json&q=<запрос>`), поэтому запрос достаётся из
    адресной строки. Адрес без параметра `q` (чужая строка журнала) — None: такой отказ ни к
    одному поиску сессии не привязывается.
    """
    if not url:
        return None
    values = parse_qs(urlsplit(url).query).get("q") or []
    return str(values[0]) if values else None


def attribute_rate_limited_searches(
    refused_search_queries: Sequence[tuple[str, str]],
    rejection_queries: Sequence[str],
) -> set[str]:
    """Какие отказавшие `web.search` отклонены ЛИМИТОМ ПОИСКА УЗЛА (чистая функция, T7.127).

    Вход: `refused_search_queries` — пары (action_id, запрос) поисков этой сессии, дошедших до
    прокси и получивших `action_failed` (воротные отказы в этот список не попадают), в порядке
    ленты; `rejection_queries` — запросы строк журнала отказов лимита, уже отобранных по
    интервалу жизни сессии, в хронологическом порядке журнала.

    Правило сопоставления: точное равенство текста запроса и ОДНОКРАТНОЕ потребление строки
    журнала — одна запись журнала не даёт два отказа двум вызовам, один вызов не расходует две
    записи. Строка журнала без распознанного запроса остаётся неучтённой.
    """
    remaining = list(rejection_queries)
    matched: set[str] = set()
    for action_id, query in refused_search_queries:
        if not query or not remaining:
            continue
        for index, rejected in enumerate(remaining):
            if rejected == query:
                matched.add(action_id)
                del remaining[index]
                break
    return matched


async def _rate_limit_rejection_queries(
    db: AsyncSession, *, started_at: Any, finished_at: Any
) -> list[str]:
    """Запросы отказов лимита из журнала прокси (read-only) внутри интервала жизни сессии.

    Строки `research_fetch_rejected` пишутся без session_id (их пишет сам прокси, сессии не
    зная), поэтому единственная граница, отделяющая чужие отказы, — время жизни этой сессии;
    нового AuditEventType и новой колонки задача не заводила. Ограничение честно: если два
    пересекающихся по времени окна сессий содержат ОДИНАКОВЫЙ запрос, неотличимо, какой именно
    сессии принадлежит отказ — но суммарное число отказов от этого не двоится (строка журнала
    потребляется один раз).
    """
    if started_at is None:
        return []
    conditions = ["type = :t", "payload->>'reason' = :reason", "occurred_at >= :from_ts"]
    params: dict[str, Any] = {
        "t": AuditEventType.RESEARCH_FETCH_REJECTED.value,
        "reason": RATE_LIMIT_REJECTION_REASON,
        "from_ts": started_at,
    }
    if finished_at is not None:
        conditions.append("occurred_at <= :to_ts")
        params["to_ts"] = finished_at
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT payload FROM audit_events WHERE {' AND '.join(conditions)} "
                    "ORDER BY occurred_at, sequence LIMIT :cap"
                ),
                {**params, "cap": MAX_AUDIT_ROWS_PER_SESSION},
            )
        )
        .mappings()
        .all()
    )
    queries: list[str] = []
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        query = search_query_from_upstream_url(str(payload.get("url") or ""))
        if query is not None:
            queries.append(query)
    return queries


async def _session_steps(
    db: AsyncSession,
    session_id: uuid.UUID,
    globally_bad_hosts: set[str],
    *,
    started_at: Any = None,
    finished_at: Any = None,
) -> tuple[SessionSteps, set[str]]:
    """Счётчики шагов одной сессии по ленте `audit_events` (порядок — только `sequence`).

    Отражённые счётчики и их определения (сознательные реплики правил, а не новые смыслы):
    * `searches_*` — выполненные `web.search` (получившие `action_started`); отбор по хост-
      константе модуля делает `search_query_official`;
    * `searches_rate_limited` (T7.127) — из них отклонённые ЛИМИТОМ ПОИСКА УЗЛА: узел не спросил
      движок, поэтому такой поиск не может считаться «пустым»; привязка отказа к сессии —
      `_rate_limit_rejection_queries` + `attribute_rate_limited_searches`;
    * `wasted_searches` — «поиск без последующего чтения»: зеркало паса 2 менеджера
      (`step_budget.py`) c поправкой T7.127: отказа лимита среди следующих действий не считать
      пустым поиском (у отклонённого поиска результатов нет, и читать после него нечего);
    * `fetch_failures_by_host` — отказы выполненных `research.fetch`, сгруппированные по
      хосту; вид отказа — зеркало `classify_fetch_error` (401/403/429/timeout) либо None;
    * `repeated_failed_host_fetches` — fetch к хосту, который уже отказывал этим видом в
      ЭТОЙ сессии (то же множество хостов, что закрывает гейт T7.111);
    * `fetch_known_bad_host` — fetch к хосту, уже отказавшему в РАНЕЕ обработанной сессии
      отчёта (зеркало `fetch_known_bad_host` менеджера; сессии идут по started_at);
    * `repeated_reads` — зеркало `fetch_dup`: `research.fetch`, прочитавший URL или хэш
      нормализованного текста, уже встречавшиеся в этой сессии (хэши — из событий
      `research_content_read`);
    * `gate_*` — отказы ворот ДО исполнения (`action_failed` с `denied: True`):
      почти-дубликат поиска, «недавно недоступен» хост, повторный вызов инструмента.
    Возвращает (счётчики, множество хостов, отказавших этим видом в этой сессии).
    """
    step_types = (
        AuditEventType.ACTION_STARTED.value,
        AuditEventType.ACTION_COMPLETED.value,
        AuditEventType.ACTION_FAILED.value,
        AuditEventType.RESEARCH_CONTENT_READ.value,
    )
    rows = (
        (
            await db.execute(
                text(
                    "SELECT sequence, type, payload FROM audit_events "
                    "WHERE session_id = :s AND type IN (:t0, :t1, :t2, :t3) "
                    "ORDER BY sequence"
                ),
                {
                    "s": session_id,
                    "t0": step_types[0],
                    "t1": step_types[1],
                    "t2": step_types[2],
                    "t3": step_types[3],
                },
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > MAX_AUDIT_ROWS_PER_SESSION:
        raise HonestyReportError(
            f"слишком длинная лента сессии {str(session_id)[:8]}: {len(rows)} строк — отчёт ограничен"
        )

    started: dict[str, tuple[str, dict[str, Any]]] = {}
    order: list[str] = []
    failed: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    sha_by_url: dict[str, str] = {}
    gate_counts: dict[str, int] = {
        "search_query_near_duplicate": 0,
        "host_unavailable_in_session": 0,
        "tool_call_repeated": 0,
    }
    steps = SessionSteps()
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        event_type = str(row["type"])
        if event_type == AuditEventType.ACTION_STARTED.value:
            action_id = str(payload.get("action_id") or "")
            tool = str(payload.get("tool") or "")
            raw_args = payload.get("arguments")
            args: dict[str, Any] = {}
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    args = parsed
            if not args:
                steps.args_unparsed += 1
            started[action_id] = (tool, args)
            order.append(action_id)
        elif event_type == AuditEventType.ACTION_COMPLETED.value:
            if payload.get("ok") is True:
                completed.add(str(payload.get("action_id") or ""))
        elif event_type == AuditEventType.ACTION_FAILED.value:
            if payload.get("denied") is True:
                reason = str(payload.get("reason") or "")
                if reason in gate_counts:
                    gate_counts[reason] += 1
                continue
            failed[str(payload.get("action_id") or "")] = payload
        elif event_type == AuditEventType.RESEARCH_CONTENT_READ.value:
            url = str(payload.get("url") or "")
            sha = str(payload.get("normalized_sha256") or "")
            if url and sha:
                sha_by_url.setdefault(url, sha)

    steps.gate_near_duplicate = gate_counts["search_query_near_duplicate"]
    steps.gate_host_unavailable = gate_counts["host_unavailable_in_session"]
    steps.gate_tool_repeated = gate_counts["tool_call_repeated"]

    # T7.127: поиски, отклонённые лимитом поиска узла. Журнал отказов прокси пишется без
    # session_id, поэтому привязка — интервал жизни сессии + текст запроса (однократное
    # потребление строки журнала). Хронология ленты не используется: у строк одной
    # phase-1-транзакции `occurred_at` совпадают (AGENTS §7), порядок держит `sequence`.
    refused: list[tuple[str, str]] = []
    for action_id in order:
        tool, args = started[action_id]
        if tool == "web.search" and action_id not in completed and action_id in failed:
            refused.append((action_id, str(args.get("query") or "")))
    rejection_queries = await _rate_limit_rejection_queries(
        db, started_at=started_at, finished_at=finished_at
    )
    rate_limited_ids = attribute_rate_limited_searches(refused, rejection_queries)

    session_bad_hosts: dict[str, str] = {}
    seen_urls: set[str] = set()
    seen_shas: set[str] = set()
    for index, action_id in enumerate(order):
        tool, args = started[action_id]
        if tool == "web.search":
            query = str(args.get("query") or "")
            steps.searches_total += 1
            if search_query_official(query):
                steps.searches_official += 1
            else:
                steps.searches_other += 1
            if action_id in rate_limited_ids:
                steps.searches_rate_limited += 1
                continue
            next_tool = started[order[index + 1]][0] if index + 1 < len(order) else None
            if next_tool != "research.fetch":
                steps.wasted_searches += 1
            continue
        if tool == "research.fetch":
            fetch_url = str(args.get("url") or "")
            host = url_host(fetch_url)
            if host in session_bad_hosts:
                steps.repeated_failed_host_fetches += 1
            if host in globally_bad_hosts:
                steps.fetch_known_bad_host += 1
            failure = failed.get(action_id)
            if failure is not None and action_id not in completed:
                steps.fetch_fail_total += 1
                kind = classify_fetch_error(str(failure.get("error") or ""))
                for item in steps.fetch_failures_by_host:
                    if item["host"] == host and item["kind"] == kind:
                        item["count"] += 1
                        break
                else:
                    steps.fetch_failures_by_host.append(
                        {"host": host, "kind": kind, "count": 1}
                    )
                if kind is not None:
                    session_bad_hosts.setdefault(host, kind)
                continue
            steps.fetch_ok_total += 1
            if is_official_host(host):
                steps.fetch_ok_official += 1
            else:
                steps.fetch_ok_other += 1
            read_sha = sha_by_url.get(fetch_url)
            if fetch_url in seen_urls or (read_sha is not None and read_sha in seen_shas):
                steps.repeated_reads += 1
            seen_urls.add(fetch_url)
            if read_sha:
                seen_shas.add(read_sha)
            continue
        steps.other_tools += 1
    return steps, set(session_bad_hosts)


# ─── карточные запросы (реплики apps/web/answer.py; поведение карточки не трогают) ──────


async def _sessions_of_questions(
    db: AsyncSession, question_ids: Sequence[uuid.UUID], since: datetime | None
) -> list[Any]:
    clause, params = _in_params("q", [str(qid) for qid in question_ids])
    since_filter = ""
    if since is not None:
        since_filter = "AND s.started_at >= :since"
        params["since"] = since
    return list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT s.id, s.question_id, s.state, s.termination_reason,
                           s.started_at, s.finished_at
                    FROM sessions s
                    WHERE s.question_id IN ({clause}) {since_filter}
                    ORDER BY s.started_at NULLS LAST, s.id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )


async def _touched_claims(
    db: AsyncSession, session_ids: Sequence[uuid.UUID]
) -> list[Any]:
    """Утверждения, к которым причастны эти сессии, с отношением created/reverified/reused.

    Реплика `touched_claims_cte` из `apps/web/answer.py` (T7.74): отношение строится только
    по долговременным записям (`claims.created_in_session`; оценка сессией существующего
    утверждения + событие `claim_reverified` → reverified, без него → reused), самое
    убедительное отношение на пару — одно.
    """
    if not session_ids:
        return []
    clause, params = _in_params("s", [str(sid) for sid in session_ids])
    return list(
        (
            await db.execute(
                text(
                    f"""
                    WITH sess AS (
                        SELECT s.id AS session_id FROM sessions s WHERE s.id IN ({clause})
                    ),
                    touches AS (
                        SELECT c.id AS claim_id, c.created_in_session AS session_id,
                               'created' AS relation
                        FROM claims c
                        JOIN sess ON sess.session_id = c.created_in_session
                        UNION ALL
                        SELECT a.claim_id, a.created_in_session AS session_id,
                               CASE WHEN rv.matched IS NULL THEN 'reused' ELSE 'reverified' END AS relation
                        FROM claim_assessments a
                        JOIN sess ON sess.session_id = a.created_in_session
                        LEFT JOIN claims c_own
                               ON c_own.id = a.claim_id
                              AND c_own.created_in_session = a.created_in_session
                        LEFT JOIN LATERAL (
                            SELECT 1 AS matched
                            FROM audit_events e
                            WHERE e.session_id = a.created_in_session
                              AND e.type = 'claim_reverified'
                              AND e.payload->>'claim_id' = a.claim_id::text
                            LIMIT 1
                        ) rv ON TRUE
                        WHERE c_own.id IS NULL
                    ),
                    ranked AS (
                        SELECT DISTINCT ON (t.claim_id) t.claim_id, t.session_id, t.relation
                        FROM touches t
                        ORDER BY t.claim_id,
                                 CASE t.relation WHEN 'created' THEN 0 WHEN 'reverified' THEN 1 ELSE 2 END,
                                 t.session_id DESC
                    )
                    SELECT r.claim_id, r.relation FROM ranked r ORDER BY r.claim_id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )


async def _relied_claims(db: AsyncSession, session_ids: Sequence[uuid.UUID]) -> list[Any]:
    """Опорные утверждения — реплика `_relied_claims_rows` из `apps/web/answer.py` (T7.82):
    payload `relied_claim_ids` события `claim_created` этих сессий."""
    if not session_ids:
        return []
    clause, params = _in_params("s", [str(sid) for sid in session_ids])
    params["t"] = AuditEventType.CLAIM_CREATED.value
    return list(
        (
            await db.execute(
                text(
                    f"""
                    WITH ev AS (
                        SELECT jsonb_array_elements_text(a.payload -> 'relied_claim_ids') AS claim_ref
                        FROM audit_events a
                        WHERE a.type = :t AND a.session_id IN ({clause})
                        ORDER BY a.sequence DESC
                        LIMIT 400
                    )
                    SELECT DISTINCT claim_ref FROM ev
                    WHERE claim_ref ~* '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
                    ORDER BY claim_ref
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )


async def _claim_heads(db: AsyncSession, claim_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
    """Действующая оценка утверждений в активном снимке правил (head → current assessment)."""
    if not claim_ids:
        return {}
    clause, params = _in_params("c", [str(cid) for cid in claim_ids])
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT c.id, c.statement, c.claim_type, c.created_at,
                           COALESCE(h.assessment_state, 'none') AS head_state,
                           h.epistemic_status, a.id AS assessment_id, a.effective_grade,
                           a.epistemic_status, a.confidence, a.source_independence_snapshot_id
                    FROM claims c
                    LEFT JOIN claim_assessment_heads h
                           ON h.claim_id = c.id
                          AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    WHERE c.id IN ({clause})
                    ORDER BY c.id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {str(row["id"]): row for row in rows}


async def _assessment_reasons(
    db: AsyncSession, assessment_ids: Sequence[str], floor: datetime | None
) -> dict[str, list[str]]:
    """Причины оценок из ленты — реплика `assessment_reason_rows` (`apps/web/knowledge.py`):
    самое свежее событие оценки на оценку, порядок — `occurred_at, sequence`."""
    if not assessment_ids:
        return {}
    clause, params = _in_params("aid", assessment_ids)
    types, type_params = _in_params("etype", ASSESSMENT_EVENT_TYPES)
    params.update(type_params)
    time_floor = ""
    if floor is not None:
        time_floor = "AND occurred_at >= :floor"
        params["floor"] = floor
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT DISTINCT ON (payload->>'assessment_id') payload
                        FROM audit_events
                        WHERE type IN ({types})
                          AND payload->>'assessment_id' IN ({clause})
                          {time_floor}
                        ORDER BY payload->>'assessment_id', occurred_at DESC, sequence DESC
                    )
                    SELECT l.payload->>'assessment_id' AS assessment_id, r.reason
                    FROM latest l
                    LEFT JOIN LATERAL jsonb_array_elements_text(
                        COALESCE(l.payload->'reasons', '[]'::jsonb)
                    ) WITH ORDINALITY AS r(reason, n) ON TRUE
                    ORDER BY 1, r.n
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    reasons: dict[str, list[str]] = {}
    for row in rows:
        reason = row["reason"]
        if reason is None:
            continue
        reasons.setdefault(str(row["assessment_id"]), []).append(str(reason))
    return reasons


#: Реплика `current_assessment_evidence_joins` (`apps/web/knowledge.py`) — facts only.
_CURRENT_ASSESSMENT_EVIDENCE_JOINS: Final[str] = f"""
            LEFT JOIN LATERAL (
                SELECT a.id AS current_assessment_id,
                       a.source_independence_snapshot_id AS snapshot_id
                FROM claim_assessment_heads h
                JOIN claim_assessments a ON a.id = h.current_assessment_id
                WHERE h.claim_id = {{claim_ref}}
                  AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
                  AND h.assessment_state = 'current'
            ) cur_a ON TRUE
            LEFT JOIN assessment_evidence cur_ae
                   ON cur_ae.assessment_id = cur_a.current_assessment_id
                  AND cur_ae.evidence_id = {{evidence_ref}}
            LEFT JOIN source_independence_members cur_sim
                   ON cur_sim.snapshot_id = cur_a.snapshot_id
                  AND cur_sim.source_id = {{source_ref}}
"""


async def _evidence_rows(db: AsyncSession, claim_ids: Sequence[uuid.UUID]) -> dict[str, list[Any]]:
    """Улики утверждений + записанные роли и группы действующей оценки (реплика карточного
    запроса `apps/web/answer.py` про улики, без артефактов: артефакты отчёту не нужны)."""
    if not claim_ids:
        return {}
    clause, params = _in_params("c", [str(cid) for cid in claim_ids])
    joins = _CURRENT_ASSESSMENT_EVIDENCE_JOINS.format(
        claim_ref="e.claim_id", evidence_ref="e.id", source_ref="e.source_id"
    )
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT e.claim_id, e.relation, e.evidence_kind, e.scope, e.source_id,
                           s.canonical_uri, s.parent_source_id,
                           ps.canonical_uri AS parent_uri,
                           cur_a.current_assessment_id AS assessment_id,
                           cur_ae.role AS assessment_role, cur_sim.group_id
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN sources ps ON ps.id = s.parent_source_id
                    {joins}
                    WHERE e.claim_id IN ({clause})
                    ORDER BY e.claim_id, e.created_at, e.id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    by_claim: dict[str, list[Any]] = {}
    for row in rows:
        by_claim.setdefault(str(row["claim_id"]), []).append(row)
    return by_claim


async def _group_members(db: AsyncSession, snapshot_ids: Sequence[uuid.UUID]) -> dict[str, list[Any]]:
    """Записанные группы независимости снимков (участники с uri источников)."""
    if not snapshot_ids:
        return {}
    clause, params = _in_params("sn", [str(sid) for sid in snapshot_ids])
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT m.snapshot_id, m.source_id, m.group_id, m.basis, s.canonical_uri
                    FROM source_independence_members m
                    JOIN sources s ON s.id = m.source_id
                    WHERE m.snapshot_id IN ({clause})
                    ORDER BY m.snapshot_id, m.group_id, m.source_id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    by_snapshot: dict[str, list[Any]] = {}
    for row in rows:
        by_snapshot.setdefault(str(row["snapshot_id"]), []).append(row)
    return by_snapshot


async def _model_run_stats(db: AsyncSession, session_ids: Sequence[uuid.UUID]) -> dict[str, tuple[int, int, int]]:
    if not session_ids:
        return {}
    clause, params = _in_params("s", [str(sid) for sid in session_ids])
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT session_id, count(*) AS calls,
                           COALESCE(sum(input_tokens), 0) AS input_tokens,
                           COALESCE(sum(output_tokens), 0) AS output_tokens
                    FROM model_runs WHERE session_id IN ({clause})
                    GROUP BY session_id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        str(row["session_id"]): (int(row["calls"]), int(row["input_tokens"]), int(row["output_tokens"]))
        for row in rows
    }


# ─── сборка отчёта ─────────────────────────────────────────────────────────────────────


async def collect_honesty_report(
    db: AsyncSession,
    *,
    since: datetime | None = None,
    question_ids: Sequence[uuid.UUID] = (),
    control_tag: str | None = None,
) -> HonestyReport:
    """Собрать отчёт (ТОЛЬКО ЧТЕНИЕ). Ровно один критерий отбора обязателен."""
    if since is None and not question_ids and control_tag is None:
        raise HonestyReportError(
            "нужен хотя бы один критерий отбора: --since, --questions или --control-tag"
        )
    conditions: list[str] = []
    params: dict[str, Any] = {}
    if question_ids:
        clause, id_params = _in_params("qid", [str(qid) for qid in question_ids])
        conditions.append(f"q.id IN ({clause})")
        params.update(id_params)
    if control_tag is not None:
        suffix = _control_suffix(control_tag)
        params["suffix"] = suffix
        params["suffix_len"] = len(suffix)
        conditions.append("RIGHT(q.text, :suffix_len) = :suffix")
    if since is not None:
        params["since"] = since
        conditions.append(
            "EXISTS (SELECT 1 FROM sessions s WHERE s.question_id = q.id AND s.started_at >= :since)"
        )
    question_rows = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT q.id, q.text, q.state, q.priority, q.created_at
                    FROM questions q
                    WHERE {' OR '.join(conditions)}
                    ORDER BY q.created_at, q.id
                    LIMIT :max_questions
                    """
                ),
                {**params, "max_questions": MAX_QUESTIONS + 1},
            )
        )
        .mappings()
        .all()
    )
    if len(question_rows) > MAX_QUESTIONS:
        raise HonestyReportError(
            f"отбор дал больше {MAX_QUESTIONS} вопросов — уточните --since/--control-tag"
        )

    all_question_ids = [uuid.UUID(str(row["id"])) for row in question_rows]
    selected_ids = {str(qid) for qid in all_question_ids}
    requested_missing = sorted(
        str(qid) for qid in question_ids if str(qid) not in selected_ids
    )

    report_questions: list[QuestionReport] = []
    totals: dict[str, float] = {}
    globally_bad_hosts: set[str] = set()

    for row in question_rows:
        question_id = uuid.UUID(str(row["id"]))
        session_rows = await _sessions_of_questions(db, [question_id], since)
        if len(session_rows) > MAX_SESSIONS:
            raise HonestyReportError(
                f"у вопроса {str(question_id)[:8]} больше {MAX_SESSIONS} сессий — отчёт ограничен"
            )
        session_ids = [uuid.UUID(str(r["id"])) for r in session_rows]
        model_stats = await _model_run_stats(db, session_ids)

        sessions_report: list[ReportSession] = []
        for s_row in session_rows:
            session_id = uuid.UUID(str(s_row["id"]))
            started_at = s_row["started_at"]
            finished_at = s_row["finished_at"]
            steps, bad_hosts = await _session_steps(
                db,
                session_id,
                globally_bad_hosts,
                started_at=started_at,
                finished_at=finished_at,
            )
            globally_bad_hosts |= bad_hosts
            duration: float | None = None
            if started_at is not None and finished_at is not None:
                duration = (finished_at - started_at).total_seconds()
            calls, input_tokens, output_tokens = model_stats.get(
                str(session_id), (0, 0, 0)
            )
            sessions_report.append(
                ReportSession(
                    session_id=str(session_id)[:8],
                    state=str(s_row["state"]),
                    state_label=_SESSION_STATE_LABELS.get(str(s_row["state"]), str(s_row["state"])),
                    termination_reason=s_row["termination_reason"],
                    started_at=_iso(started_at),
                    finished_at=_iso(finished_at),
                    duration_seconds=round(duration, 1) if duration is not None else None,
                    model_calls=calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    steps=steps,
                )
            )

        touched = await _touched_claims(db, session_ids)
        relied_refs = await _relied_claims(db, session_ids)
        claimed_rows = [r for r in relied_refs if str(r["claim_ref"]) not in {str(t["claim_id"]) for t in touched}]
        claim_by_relation: dict[str, list[str]] = {
            "created": [str(t["claim_id"]) for t in touched if t["relation"] == "created"],
            "reused": [str(t["claim_id"]) for t in touched if t["relation"] == "reused"],
            "reverified": [str(t["claim_id"]) for t in touched if t["relation"] == "reverified"],
            "relied": [str(r["claim_ref"]) for r in claimed_rows],
        }
        all_claim_ids = sorted(
            {
                uuid.UUID(cid)
                for bucket in claim_by_relation.values()
                for cid in bucket
            }
        )
        if len(all_claim_ids) > MAX_CLAIMS:
            raise HonestyReportError(
                f"у вопроса {str(question_id)[:8]} больше {MAX_CLAIMS} утверждений — отчёт ограничен"
            )

        heads = await _claim_heads(db, all_claim_ids)
        assessment_ids = [
            str(head["assessment_id"]) for head in heads.values() if head["assessment_id"] is not None
        ]
        claim_floor: datetime | None = None
        for head in heads.values():
            created = head["created_at"]
            if isinstance(created, datetime) and (claim_floor is None or created < claim_floor):
                claim_floor = created
        reasons = await _assessment_reasons(db, assessment_ids, claim_floor)
        evidence_by_claim = await _evidence_rows(db, all_claim_ids)
        snapshot_ids = sorted(
            {
                uuid.UUID(str(head["source_independence_snapshot_id"]))
                for head in heads.values()
                if head["source_independence_snapshot_id"] is not None
            }
        )
        members_by_snapshot = await _group_members(db, snapshot_ids)

        high_claim_ids = [
            cid
            for cid in all_claim_ids
            if (head := heads.get(str(cid))) is not None
            and str(head["effective_grade"] or "") in _HIGH_GRADE_LEVELS
        ]
        assembly_parents: dict[str, dict[str, str | None]] = {}
        assembly_uris: dict[str, dict[str, str]] = {}
        for cid in high_claim_ids:
            assembly = await load_source_graph_assembly(db, claim_id=cid)
            if assembly is None:
                continue
            pointer_by_node = {str(n.source_id): n.pointer_parent for n in assembly.nodes}
            parents: dict[str, str | None] = {}
            uri_by_id = {str(n.source_id): (n.canonical_uri or "") for n in assembly.nodes}
            for source_id in {str(mid) for mid in assembly.member_ids} | set(pointer_by_node):
                record_parent = assembly.effective_parent.get(_as_uuid(source_id))
                pointer = pointer_by_node.get(source_id)
                if record_parent is not None:
                    parents[source_id] = str(record_parent)
                elif pointer is not None:
                    parents[source_id] = str(pointer)
            assembly_parents[str(cid)] = parents
            assembly_uris[str(cid)] = uri_by_id

        claims_buckets: dict[str, list[ReportClaim]] = {}
        flag_counts: dict[str, int] = {"f1": 0, "f2": 0, "f3": 0}
        for relation, bucket in claim_by_relation.items():
            claims: list[ReportClaim] = []
            for claim_id_text in sorted(bucket):
                head = heads.get(claim_id_text)
                if head is None:
                    continue
                evidence_rows = evidence_by_claim.get(claim_id_text, [])
                question_text = str(row["text"])
                statement = str(head["statement"])
                flags: list[ReportFlag] = []

                groups_map: dict[str, set[str]] = {}
                snapshot_id = head["source_independence_snapshot_id"]
                for member in members_by_snapshot.get(str(snapshot_id) if snapshot_id else "", []):
                    groups_map.setdefault(str(member["group_id"]), set()).add(str(member["source_id"]))

                grade = str(head["effective_grade"]) if head["effective_grade"] is not None else None
                parents = assembly_parents.get(claim_id_text, {})
                if not parents and grade is not None and grade in _HIGH_GRADE_LEVELS:
                    # группа есть, а assembly не собрался: эффективные родители по указателям строк
                    parents = {
                        str(e["source_id"]): (str(e["parent_source_id"]) if e["parent_source_id"] else None)
                        for e in evidence_rows
                        if e["source_id"] is not None
                    }
                f1 = shared_producer_flag(grade=grade, groups=groups_map, effective_parent=parents)

                report_groups: list[ReportGroup] = []
                if grade is not None and grade in _HIGH_GRADE_LEVELS and groups_map:
                    uri_by_id = assembly_uris.get(claim_id_text, {})
                    for group_id in sorted(groups_map):
                        sources: list[ReportSource] = []
                        for source_id in sorted(groups_map[group_id]):
                            via = "value_record" if parents.get(source_id) else None
                            parent_uri = uri_by_id.get(parents.get(source_id) or "", "")
                            parent_row_uri = next(
                                (
                                    str(e["parent_uri"])
                                    for e in evidence_rows
                                    if str(e["source_id"] or "") == source_id and e["parent_uri"]
                                ),
                                "",
                            )
                            if via is None and any(
                                str(e["source_id"] or "") == source_id and e["parent_source_id"]
                                for e in evidence_rows
                            ):
                                via = "page_pointer"
                            self_pub: str | None = None
                            record_name: str | None = None
                            for ev_row in evidence_rows:
                                if str(ev_row["source_id"] or "") != source_id:
                                    continue
                                publication = parse_self_publication(
                                    ev_row["scope"] if isinstance(ev_row["scope"], dict) else None
                                )
                                if publication is not None and self_pub is None:
                                    self_pub = publication.primary_name
                                record = parse_value_attribution(
                                    ev_row["scope"] if isinstance(ev_row["scope"], dict) else None
                                )
                                if record is not None and record_name is None:
                                    record_name = record.primary_name
                            sources.append(
                                ReportSource(
                                    source_id=source_id[:8],
                                    canonical_uri=_head(uri_by_id.get(source_id, ""), _URI_HEAD_CHARS),
                                    host=url_host(uri_by_id.get(source_id, "")),
                                    parent_via=via,
                                    parent_source_id=(parents.get(source_id) or "")[:8] or None,
                                    parent_uri=_head(parent_uri or parent_row_uri, _URI_HEAD_CHARS) or None,
                                    parent_host=url_host(parent_uri or parent_row_uri) or None,
                                    self_publication=self_pub,
                                )
                            )
                        report_groups.append(ReportGroup(group_id=group_id, sources=sources))

                support_names: list[tuple[str, str]] = []
                has_counters = False
                for evidence in evidence_rows:
                    role = str(evidence["assessment_role"]) if evidence["assessment_role"] else ""
                    scope = evidence["scope"] if isinstance(evidence["scope"], dict) else None
                    if role == "counter" or str(evidence["relation"]) == "counters":
                        has_counters = True
                    if role != "support":
                        continue
                    record = parse_value_attribution(scope)
                    publication = parse_self_publication(scope)
                    if record is not None:
                        support_names.append((record.primary_key, record.primary_name))
                    elif publication is not None:
                        support_names.append((publication.primary_key, publication.primary_name))
                claim_reasons = reasons.get(str(head["assessment_id"] or ""), [])
                signature = producer_signature(
                    names=support_names,
                    group_count=len(groups_map),
                    has_counters=has_counters,
                    reasons=claim_reasons,
                )
                flag_outcomes = (
                    f1,
                    off_topic_flag(statement, question_text),
                    producer_not_named_flag(signature, statement),
                )
                for outcome in flag_outcomes:
                    if outcome is None:
                        continue
                    flags.append(ReportFlag(code=outcome.code, name=outcome.name, message=outcome.message or ""))
                    flag_counts[outcome.code] += 1

                claims.append(
                    ReportClaim(
                        claim_id=claim_id_text[:8],
                        statement=statement,
                        statement_head=_head(statement, _STATEMENT_HEAD_CHARS),
                        claim_type=str(head["claim_type"]),
                        relation=relation,
                        head_state=str(head["head_state"]),
                        grade=grade,
                        epistemic_status=(
                            str(head["epistemic_status"]) if head["epistemic_status"] is not None else None
                        ),
                        confidence=(
                            float(head["confidence"]) if head["confidence"] is not None else None
                        ),
                        assessment_id=str(head["assessment_id"])[:8] if head["assessment_id"] else None,
                        reasons=claim_reasons,
                        producer_signature=(
                            f"{PRODUCER_LABEL} ({signature[0]})" if signature is not None else None
                        ),
                        groups=report_groups,
                        flags=flags,
                    )
                )
            claims_buckets[relation] = claims

        answered = any(claims_buckets[relation] for relation in ("created", "reused", "reverified"))
        report_questions.append(
            QuestionReport(
                question_id=str(question_id)[:8],
                text=str(row["text"]),
                text_head=_head(str(row["text"]), _TEXT_HEAD_CHARS),
                state=str(row["state"]),
                state_label=_QUESTION_STATE_LABELS.get(str(row["state"]), str(row["state"])),
                priority=int(row["priority"]),
                created_at=_iso(row["created_at"]),
                answered=answered,
                missing_sessions=not session_ids,
                sessions=sessions_report,
                claims=claims_buckets,
                flag_counts=flag_counts,
            )
        )

    totals = _compute_totals(report_questions)
    return HonestyReport(
        schema_version=SCHEMA_VERSION,
        generated_at=_iso(datetime.now(UTC)) or "",
        selection={
            "since": _iso(since),
            "control_tag": control_tag,
            "question_ids": [str(qid) for qid in question_ids],
            "requested_missing": requested_missing,
        },
        questions=report_questions,
        totals=totals,
    )


def _compute_totals(questions: Sequence[QuestionReport]) -> dict[str, float]:
    """Суммы и доли прогона (JSON-плоские числа, доли — от 0 до 1)."""
    sums: dict[str, int] = {
        "questions": len(questions),
        "questions_answered": 0,
        "sessions": 0,
        "model_calls": 0,
        "model_input_tokens": 0,
        "model_output_tokens": 0,
        "searches_total": 0,
        "searches_official": 0,
        "searches_other": 0,
        "searches_rate_limited": 0,
        "wasted_searches": 0,
        "fetch_ok_total": 0,
        "fetch_ok_official": 0,
        "fetch_ok_other": 0,
        "fetch_fail_total": 0,
        "repeated_failed_host_fetches": 0,
        "fetch_known_bad_host": 0,
        "repeated_reads": 0,
        "gate_near_duplicate": 0,
        "gate_host_unavailable": 0,
        "gate_tool_repeated": 0,
        "other_tools": 0,
        "args_unparsed": 0,
        "claims_created": 0,
        "claims_reused": 0,
        "claims_reverified": 0,
        "claims_relied": 0,
        "claims_e2plus": 0,
        "flags_f1": 0,
        "flags_f2": 0,
        "flags_f3": 0,
        "signatures_producer": 0,
    }
    for question in questions:
        sums["questions_answered"] += 1 if question.answered else 0
        sums["sessions"] += len(question.sessions)
        for session in question.sessions:
            sums["model_calls"] += session.model_calls
            sums["model_input_tokens"] += session.input_tokens
            sums["model_output_tokens"] += session.output_tokens
            step_fields = (
                "searches_total", "searches_official", "searches_other",
                "searches_rate_limited", "wasted_searches",
                "fetch_ok_total", "fetch_ok_official", "fetch_ok_other", "fetch_fail_total",
                "repeated_failed_host_fetches", "fetch_known_bad_host", "repeated_reads",
                "gate_near_duplicate", "gate_host_unavailable", "gate_tool_repeated",
                "other_tools", "args_unparsed",
            )
            for name in step_fields:
                sums[name] += int(getattr(session.steps, name))
        for bucket in question.claims.values():
            for claim in bucket:
                key = f"claims_{claim.relation}"
                if key in sums:
                    sums[key] += 1
                if claim.grade is not None and claim.grade in _HIGH_GRADE_LEVELS:
                    sums["claims_e2plus"] += 1
                if claim.producer_signature is not None:
                    sums["signatures_producer"] += 1
                for flag in claim.flags:
                    sums[f"flags_{flag.code}"] += 1

    def share(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    totals: dict[str, float] = {key: float(value) for key, value in sums.items()}
    # T7.127: доля «пустых» поисков считается от поисков, ДОШЕДШИХ до поискового движка. Отказ
    # лимита поиска узла не даёт исследователю никаких результатов, и называть такой поиск
    # «поиском без последующего чтения» — приписывать ему то, чего прочесть было нельзя:
    # wasted_searches / (searches_total - searches_rate_limited).
    reached_engine = sums["searches_total"] - sums["searches_rate_limited"]
    totals["share_wasted_searches"] = share(sums["wasted_searches"], reached_engine)
    totals["share_fetch_fail"] = share(sums["fetch_fail_total"], sums["fetch_ok_total"] + sums["fetch_fail_total"])
    totals["share_reused_claims"] = share(
        sums["claims_reused"] + sums["claims_reverified"],
        sums["claims_created"] + sums["claims_reused"] + sums["claims_reverified"],
    )
    totals["share_official_searches"] = share(sums["searches_official"], sums["searches_total"])
    totals["share_official_fetches"] = share(sums["fetch_ok_official"], sums["fetch_ok_total"])
    return totals


# ─── рендер Markdown (по-русски) и сравнение с baseline ────────────────────────────────


#: Человеческие имена флагов в Markdown (JSON-код остаётся ASCII — стабильная схема).
_FLAG_LABELS: Final[dict[str, str]] = {"f1": "Ф1", "f2": "Ф2", "f3": "Ф3"}


def _fmt_share(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: HonestyReport, baseline: Mapping[str, Any] | None = None) -> str:
    lines: list[str] = []
    lines.append("# Отчёт честности (T7.118, только чтение)")
    selection = report.selection
    parts: list[str] = []
    if selection.get("since"):
        parts.append(f"с {selection['since']}")
    if selection.get("control_tag"):
        parts.append(f"контрольный прогон «{selection['control_tag']}»")
    if selection.get("question_ids"):
        parts.append("по id: " + ", ".join(str(qid)[:8] for qid in selection["question_ids"]))
    lines.append(f"Отбор: {', '.join(parts) if parts else '—'}")
    if selection.get("requested_missing"):
        lines.append(
            "Вопросов не найдено по запрошенным id: "
            + ", ".join(str(qid)[:8] for qid in selection["requested_missing"])
        )
    lines.append("")

    for question in report.questions:
        answered = "Ответ есть" if question.answered else "Ответ не записан"
        lines.append(f"## Вопрос {question.question_id} — {answered}")
        lines.append(f"текст: {question.text_head}")
        lines.append(f"состояние: {question.state_label} ({question.state}); приоритет {question.priority}")
        if question.missing_sessions:
            lines.append("сессий под критерий отбора нет — карточка пуста по этому срезу")
        lines.append("")
        for session in question.sessions:
            duration = (
                f"{session.duration_seconds:.0f} c" if session.duration_seconds is not None else "—"
            )
            lines.append(
                f"### Сессия {session.session_id} · {session.state_label}"
                f" · завершение: {session.termination_reason or '—'} · длительность: {duration}"
            )
            steps = session.steps
            lines.append(
                f"- модель: вызовов {session.model_calls}, токены {session.input_tokens}+{session.output_tokens}"
            )
            lines.append(
                f"- поиски: {steps.searches_total} (официальных {steps.searches_official}, "
                f"прочих {steps.searches_other}), без последующего чтения: {steps.wasted_searches}, "
                f"отклонено лимитом поиска узла: {steps.searches_rate_limited}"
            )
            failures = ", ".join(
                f"{item['host']} ({item['kind'] or 'другая причина'})×{item['count']}"
                for item in steps.fetch_failures_by_host
            )
            lines.append(
                f"- чтения: успешно {steps.fetch_ok_total} (официальных {steps.fetch_ok_official}), "
                f"отказы: {steps.fetch_fail_total}" + (f" — {failures}" if failures else "")
            )
            lines.append(
                f"- повторы: отказавшего хоста снова {steps.repeated_failed_host_fetches}, "
                f"уже известного плохого хоста {steps.fetch_known_bad_host}, "
                f"повторных чтений {steps.repeated_reads}"
            )
            lines.append(
                f"- отказы ворот: почти-дубликат поиска {steps.gate_near_duplicate}, "
                f"«недавно недоступен» {steps.gate_host_unavailable}, "
                f"повтор вызова {steps.gate_tool_repeated}; других инструментов {steps.other_tools}"
            )
        for relation, label in (
            ("created", "созданные"),
            ("reverified", "перепроверенные"),
            ("reused", "переиспользованные"),
            ("relied", "опертые (без перепроверки)"),
        ):
            bucket = question.claims.get(relation, [])
            lines.append(f"### Утверждения ({label}): {len(bucket)}")
            for claim in bucket:
                grade = claim.grade or "нет оценки"
                status = claim.epistemic_status or "—"
                lines.append(
                    f"- {claim.claim_id} · {grade} · {status} · голова: {claim.head_state} — {claim.statement_head}"
                )
                if claim.reasons:
                    lines.append(f"  причины оценки: {', '.join(claim.reasons)}")
                if claim.producer_signature is not None:
                    lines.append(f"  подпись: {claim.producer_signature}")
                if claim.grade is not None and claim.grade in _HIGH_GRADE_LEVELS and claim.groups:
                    for group in claim.groups:
                        source_notes = []
                        for source in group.sources:
                            note = f"{source.host or source.canonical_uri}"
                            if source.self_publication:
                                note += f" [собственная публикация: {source.self_publication}]"
                            elif source.parent_via == "value_record":
                                parent_note = source.parent_host or source.parent_source_id or "—"
                                note += f" [родитель по записи значения: {parent_note}]"
                            elif source.parent_via == "page_pointer":
                                note += f" [страничный указатель: {source.parent_host or source.parent_source_id}]"
                            source_notes.append(note)
                        lines.append(f"  группа {group.group_id}: " + "; ".join(source_notes))
                for flag in claim.flags:
                    label = _FLAG_LABELS.get(flag.code, flag.code.upper())
                    lines.append(f"  флаг {label} «{flag.name}»: {flag.message} — требует ручной проверки")
        lines.append("")

    totals = report.totals
    lines.append("## Итог прогона")
    lines.append(
        f"- вопросов {int(totals['questions'])}, с записанным ответом {int(totals['questions_answered'])}; "
        f"сессий {int(totals['sessions'])}; модель: вызовов {int(totals['model_calls'])}, "
        f"токены {int(totals['model_input_tokens'])}+{int(totals['model_output_tokens'])}"
    )
    lines.append(
        f"- поисков {int(totals['searches_total'])} (официальных {_fmt_share(totals['share_official_searches'])}), "
        f"отклонено лимитом поиска узла {int(totals['searches_rate_limited'])}, "
        f"без последующего чтения {int(totals['wasted_searches'])} "
        f"({_fmt_share(totals['share_wasted_searches'])} — доля считается от поисков, дошедших до движка: "
        "wasted_searches / (searches_total - searches_rate_limited))"
    )
    ok_share = _fmt_share(totals["share_official_fetches"])
    fail_share = _fmt_share(totals["share_fetch_fail"])
    lines.append(
        f"- чтений успешно {int(totals['fetch_ok_total'])} (официальных {ok_share}), "
        f"отказывали хосты {int(totals['fetch_fail_total'])} раз ({fail_share})"
    )
    lines.append(
        f"- повторные: отказавшего хоста {int(totals['repeated_failed_host_fetches'])}, "
        f"известного плохого {int(totals['fetch_known_bad_host'])}, "
        f"чтений того же URL/текста {int(totals['repeated_reads'])}"
    )
    lines.append(
        f"- ворота: почти-дубликатов {int(totals['gate_near_duplicate'])}, "
        f"«недавно недоступны» {int(totals['gate_host_unavailable'])}, "
        f"повторов вызова {int(totals['gate_tool_repeated'])}"
    )
    lines.append(
        f"- утверждений: создано {int(totals['claims_created'])}, переиспользовано/перепроверено "
        f"{int(totals['claims_reused'] + totals['claims_reverified'])} "
        f"({_fmt_share(totals['share_reused_claims'])} — повторный прогон частично опирается на накопленное), "
        f"оперто {int(totals['claims_relied'])}; E2+ {int(totals['claims_e2plus'])}"
    )
    lines.append(
        f"- флаги (эвристики, требуют ручной проверки): Ф1 {int(totals['flags_f1'])}, "
        f"Ф2 {int(totals['flags_f2'])}, Ф3 {int(totals['flags_f3'])}; "
        f"подписей о производителе {int(totals['signatures_producer'])}"
    )

    if baseline is not None:
        lines.append("")
        lines.append("## Было → стало (сравнение с baseline)")
        lines.append("| метрика | было | стало | изменение |")
        lines.append("| --- | ---: | ---: | ---: |")
        raw_totals = baseline.get("totals")
        baseline_totals: Mapping[str, Any] = raw_totals if isinstance(raw_totals, Mapping) else {}
        for key in sorted(totals):
            old_raw = baseline_totals.get(key)
            if not isinstance(old_raw, (int, float)) or isinstance(old_raw, bool):
                # T7.127: в baseline прежней формы аддитивной метрики просто нет — сравнивать не
                # с чем, и это «нет данных», а не 0: нулём отсутствующая метрика притворяться не
                # должна, а тихая потеря строки прякала бы изменение шкалы от читателя.
                lines.append(f"| {key} | нет данных | {totals[key]:g} | — |")
                continue
            new_value = totals[key]
            lines.append(
                f"| {key} | {old_raw:g} | {new_value:g} | {new_value - old_raw:+g} |"
            )
    lines.append("")
    lines.append(
        "Флаги — эвристики отчёта, а не вердикты оценки: каждая требует ручной проверки "
        "(см. шапку `packages/memory/honesty_report.py` про ограничения Ф1–Ф3)."
    )
    return "\n".join(lines)


def load_baseline(path: str) -> Mapping[str, Any]:
    """Прочитать baseline-JSON и проверить схему (иначе — честный отказ, не тихая ошибка)."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise HonestyReportError(f"baseline не читается: {exc}") from exc
    if not isinstance(raw, dict):
        raise HonestyReportError("baseline должен быть JSON-объектом")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise HonestyReportError(
            f"схема baseline не совпадает: в файле {version!r}, ожидается {SCHEMA_VERSION!r}"
        )
    return raw
