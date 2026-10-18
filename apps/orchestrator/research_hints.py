"""T7.119: классификация вопросов вида «официальные + независимые» и каталог подсказок
независимых источников (решение пользователя 2026-10-16, пункты 2 и 3).

Замер отчёта честности по сессиям стенда 2026-10-13…15 (STATUS T7.118): из 9 сессий по вопросам
вида «официальные + независимые» 6 закончились `budget_exhausted` на 16 шагах; 45 % поисков не
доходят до чтения; прогноз ВТБ не найден в трёх сессиях подряд. Решение пользователя: потолок
шагов исследователя поднимают такие вопросы (не все сессии), а независимые источники помогают
вопросу подсказкой, а не белым списком: поиск остаётся свободным, каталог ничего не запрещает
и не фильтрует.

Классификация — чистая функция по тексту вопроса, без LLM и сети. Нормализация — те же функции
ворот почти-дубликатов (T7.111a/T7.112: `content_word_stems` / `normalize_search_query` из
`apps/orchestrator/tool_context.py`), новых словарей нормализации нет; маркеры — закрытые списки
слов-маркеров (имена ведомств и признаковые слова), их основы получены той же функцией.

Правило: вопрос вида «официальные + независимые», если одновременно
 (о) есть маркер официального источника: основа одного из имён официальных ведомств
     (Росстат, ГКС, ЦБ, Минфин, Минэкономразвития), основа слова «официальный», соседство
     «<Банк> России» в тексте или латинский домен-метка из OFFICIAL_HOSTS отчёта честности
     (константа переиспользована, не продублирована);
 (н) есть маркер независимого источника: основа слов «независимый», «аналитик», «оценка»,
     «опрос», «индикатор» или бренд-основа hh / SuperJob.

Сочетание с «полом» T7.21 (`max(max_steps, len(named) + 3)`): итоговый потолок — максимум
обычного лимита, повышенного лимита для классифицированных вопросов и хостового пола; порядок
максимума не важен. Отсутствие ключа `max_explorer_steps_official_independent` = прежнее
поведение (как в config-v26).

Каталог подсказок живёт в `session_limits.research_hints` (не новой верхнесекционной секцией):
`config_snapshots` хранит payload поимёнными колонками (`packages/memory/activation.py`), новая
верхняя секция потребовала бы новой колонки — миграции БД, чем задача не занимается.
`session_limits` переносится целиком и читается только выборочно, поэтому новый необязательный
ключ в ней инертен для прежних потребителей.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from apps.orchestrator.tool_context import content_word_stems, normalize_search_query
from packages.memory.honesty_report import OFFICIAL_HOSTS

# ─── классификация «официальные + независимые» ────────────────────────────────

#: Закрытый список маркеров официального источника: имена ведомств (те же учреждения, чьи хосты
#: перечислены в OFFICIAL_HOSTS отчёта честности) и признаковое слово «официальный». Формы слов
#: подбираются так, чтобы их основы (тот же `_prefix_stem` внутри `content_word_stems`) покрыли
#: падежные формы вопросных формулировок: «Минфин» и «Минфина» дают разные основы (5/6 знаков),
#: поэтому длинных форм в списке нет — только те, чьи основы различаются.
OFFICIAL_MARKER_WORDS: Final[tuple[str, ...]] = (
    "росстат",
    "росстата",
    "гкс",
    "цб",
    "минфин",
    "минфина",
    "минэкономразвития",
    "официальный",
)
OFFICIAL_MARKER_STEMS: Final[frozenset[str]] = content_word_stems(" ".join(OFFICIAL_MARKER_WORDS))

#: Закрытый список маркеров независимого источника. «Банки по имени» отдельными маркерами не
#: заводятся: именованные банки в контрольных вопросах и фикстурах всегда идут вместе со словом
#: «аналитики», а маркер-имя банка глушил бы официальные вопросы без независимой стороны.
INDEPENDENT_MARKER_WORDS: Final[tuple[str, ...]] = (
    "независимый",
    "аналитик",
    "оценка",
    "оценки",
    "оценок",
    "опрос",
    "индикатор",
    "hh",
    "superjob",
)
INDEPENDENT_MARKER_STEMS: Final[frozenset[str]] = content_word_stems(" ".join(INDEPENDENT_MARKER_WORDS))

#: «Банк России» как название (не «банки России»): соседство слова «банк» в единственном числе
#: с «России». Падежные формы единственного числа перечислены явно; множественное («банки
#: России», «банков России») официальной меткой не считается.
_OFFICIAL_CENTRAL_BANK_RE: Final = re.compile(r"\bбанк(?:а|у|ом|е)?\s+россии\b")


def official_markers_found(text: str) -> bool:
    """(о) маркер официального источника в тексте вопроса (чистая функция, без LLM)."""
    stems = content_word_stems(text)
    if stems & OFFICIAL_MARKER_STEMS:
        return True
    if _OFFICIAL_CENTRAL_BANK_RE.search(text.lower().replace("ё", "е")):
        return True
    # переиспользование константы официальных хостов отчёта честности (T7.118): вопрос может
    # называть источник доменом — тогда его латинская метка уже в нормализованных словах.
    words = normalize_search_query(text)
    return any(host.split(".")[0] in words for host in OFFICIAL_HOSTS)


def independent_markers_found(text: str) -> bool:
    """(н) маркер независимого источника в тексте вопроса (чистая функция, без LLM)."""
    return bool(content_word_stems(text) & INDEPENDENT_MARKER_STEMS)


def classify_official_independent(text: str) -> bool:
    """Вопрос вида «официальные + независимые»: оба маркера одновременно (T7.119)."""
    return official_markers_found(text) and independent_markers_found(text)


# ─── итоговый потолок шагов исследователя ─────────────────────────────────────


def effective_explorer_step_cap(limits: dict[str, Any], *, classified: bool, named_count: int) -> int:
    """Потолок шагов: обычный лимит; для классифицированного вопроса — максимум с
    `max_explorer_steps_official_independent` (отсутствие ключа = прежнее поведение); затем
    хостовый пол T7.21 (`названных источников + 3`), который действует как прежде."""
    cap = int(limits.get("max_explorer_steps", 10))
    if classified:
        raised = limits.get("max_explorer_steps_official_independent")
        if raised is not None:
            cap = max(cap, int(raised))
    if named_count:
        cap = max(cap, named_count + 3)
    return cap


# ─── каталог подсказок (session_limits.research_hints) ────────────────────────

#: Потолок длины секции контекста — символах (не байтах): целые строки выносятся при
#: переполнении, как в секции «Недавно недоступны» (T7.111 2б), обрезков строк нет.
RESEARCH_HINTS_SECTION_CHAR_LIMIT: Final = 600

#: Обязательная формулировка рамки подсказки (решение пользователя): каталог не ограничение,
#: пересказ официального релиза независимым источником не считается (ADR-0029).
HINTS_SECTION_HEADER: Final = (
    "# Независимые источники по теме (подсказка, не ограничение; пересказ официального релиза "
    "независимым источником не считается)"
)


@dataclass(frozen=True)
class HintSource:
    """Источник каталога: домен и одна строка «что там искать»."""

    domain: str
    what: str


@dataclass(frozen=True)
class HintTopic:
    """Тема каталога: id, основы ключевых слов темы (получены из слов `keywords` той же
    функцией, что и основы слов вопроса — T7.119a), источники."""

    topic_id: str
    stems: frozenset[str]
    sources: tuple[HintSource, ...]


def _domain_ok(domain: str) -> bool:
    """Домен — голое имя хоста: без схемы, пути и пробелов, в нижнем регистре."""
    if not domain or domain != domain.lower() or "://" in domain:
        return False
    if any(ch.isspace() for ch in domain) or "/" in domain:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", domain))


def parse_independent_hints(limits: dict[str, Any]) -> tuple[list[HintTopic], list[str]]:
    """Каталог из `session_limits.research_hints.independent_sources` — валидация и разбор.

    Отсутствие ключа (любая конфигурация до config-v27) = пустой каталог без проблем.
    Неисправные темы/источники отбрасываются с названной проблемой: сессия не падает и не
    получает половинчатый каталог молча — проблемы возвращаются вызывающему коду и пишутся
    в полезную нагрузку существующего события выбора вопроса.
    """
    section = limits.get("research_hints")
    if section is None:
        return [], []
    problems: list[str] = []
    if not isinstance(section, dict):
        return [], ["research_hints: не словарь"]
    raw_topics = section.get("independent_sources")
    if raw_topics is None:
        return [], ["research_hints.independent_sources: ключ отсутствует"]
    if not isinstance(raw_topics, list):
        return [], ["research_hints.independent_sources: не список"]
    topics: list[HintTopic] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_topics):
        if not isinstance(raw, dict):
            problems.append(f"тема #{index}: не словарь")
            continue
        extra_keys = sorted(set(raw) - {"id", "keywords", "sources"})
        if extra_keys:
            problems.append(f"тема #{index}: неизвестные ключи {extra_keys}")
            continue
        topic_id = raw.get("id")
        if not isinstance(topic_id, str) or not topic_id.strip():
            problems.append(f"тема #{index}: id отсутствует или пуст")
            continue
        if topic_id in seen_ids:
            problems.append(f"тема {topic_id}: повтор id")
            continue
        keywords_raw = raw.get("keywords")
        if not isinstance(keywords_raw, list) or not keywords_raw:
            problems.append(f"тема {topic_id}: keywords отсутствует или пуст")
            continue
        bad_keywords = [k for k in keywords_raw if not isinstance(k, str) or not k.strip()]
        if bad_keywords:
            problems.append(f"тема {topic_id}: keywords содержит не-строку или пустое слово")
            continue
        sources_raw = raw.get("sources")
        if not isinstance(sources_raw, list) or not sources_raw:
            problems.append(f"тема {topic_id}: sources отсутствует или пуст")
            continue
        sources: list[HintSource] = []
        topic_ok = True
        for source_raw in sources_raw:
            if not isinstance(source_raw, dict) or set(source_raw) != {"domain", "what"}:
                problems.append(f"тема {topic_id}: источник должен нести ровно domain и what")
                topic_ok = False
                break
            domain = source_raw["domain"]
            what = source_raw["what"]
            if not isinstance(domain, str) or not _domain_ok(domain):
                problems.append(f"тема {topic_id}: неисправный домен {domain!r}")
                topic_ok = False
                break
            if not isinstance(what, str) or not what.strip():
                problems.append(f"тема {topic_id}: пустая строка 'что искать' у {domain}")
                topic_ok = False
                break
            sources.append(HintSource(domain=domain, what=what))
        if not topic_ok:
            continue
        seen_ids.add(topic_id)
        # T7.119a (дефект замера менеджера на config-v27): в каталоге хранятся СЛОВА темы, и они
        # проходят ТОТ ЖЕ content_word_stems ОДИН раз, что и слова вопроса. Прежние «основы» в
        # каталоге нормализовались вторично: уже-основа «инфляц» превращалась в «инфля» и не
        # совпадала с основой вопроса «инфляция» -> «инфляц». Многословное ключевое слово
        # («оплата труда») разбирается на отдельные основы.
        stems = content_word_stems(" ".join(str(k) for k in keywords_raw))
        if not stems:
            problems.append(f"тема {topic_id}: keywords не даёт ни одной значимой основы")
            continue
        topics.append(HintTopic(topic_id=topic_id, stems=stems, sources=tuple(sources)))
    return topics, problems


def select_hint_topics(question_text: str, topics: list[HintTopic]) -> list[HintTopic]:
    """Тема подбирается, если основа содержательного слова вопроса РАВНА основе ключевого слова
    темы при ОДИНАКОВОЙ нормализации обеих сторон (T7.119a): и слова каталога, и слова вопроса
    проходят `content_word_stems` ровно один раз. Основы слов, различающихся в первых пяти-шести
    знаках («рынок»/«рынка»), остаются различными — принятое ограничение сравнения (ADR-0037 §1а);
    ложных тем из-за усечения основы нет: «центр» и «ценность» не равны основе «цена»/«цены»/«цен»."""
    stems = content_word_stems(question_text)
    return [topic for topic in topics if topic.stems & stems]


def render_independent_hints_section(topics: list[HintTopic]) -> str:
    """Короткая секция контекста исследователя по образцу `render_host_availability_section`.

    Пусто при отсутствии тем; при переполнении целые строки снимаются с хвоста. Никаких
    запретов и фильтров каталог не вводит: это название того, где обычно можно найти
    независимую оценку (решение пользователя: подсказка, не белый список).
    """
    if not topics:
        return ""
    lines: list[str] = []
    for topic in topics:
        for source in topic.sources:
            candidate = "\n".join([HINTS_SECTION_HEADER, *lines, f"- {source.domain} — {source.what}"])
            if len(candidate) > RESEARCH_HINTS_SECTION_CHAR_LIMIT:
                return _clip_with_limit(topics, lines)
            lines.append(f"- {source.domain} — {source.what}")
    return "\n".join([HINTS_SECTION_HEADER, *lines])


def _clip_with_limit(topics: list[HintTopic], lines: list[str]) -> str:
    """Запасной путь переполнения: собрать секцию, снимая целые строки с хвоста."""
    while lines:
        candidate = "\n".join([HINTS_SECTION_HEADER, *lines])
        if len(candidate) <= RESEARCH_HINTS_SECTION_CHAR_LIMIT:
            return candidate
        lines.pop()
    return HINTS_SECTION_HEADER
