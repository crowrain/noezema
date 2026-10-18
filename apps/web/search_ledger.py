"""Учёт поиска независимых оценок в журнале сессий (T7.89, ADR-0035 §14).

Чистый модуль presentation-слоя: ни базы, ни LLM, ни state. Он интерпретирует только
уже записанные audit-события `action_started` (`payload.tool = "web.search"`,
`payload.arguments`) — никаких новых событий и колонок: факт «в сессиях этого утверждения
поиск независимых оценок выполнялся / не выполнялся» читается из журнала прошлых сессий.

Признак запроса — консервативный и детерминированный по тексту самого запроса: только
явные основы независимости (`INDEPENDENT_SEARCH_STEMS`) и названия независимых индексов цен
(`INDEPENDENT_SEARCH_PHRASES`). Основы «оценка + аналитик/экономист» признаком НЕ считаются:
на стенде .92 такими словами сформулированы сборы прогнозов («оценка инфляции декабрь 2025
прогнозы банков Сбербанк ВТБ Тинькофф», «итоги инфляции 2025 год оценка аналитиков ВТБ Т-Банк
5,5 5,6») — без слова независимости это не альтернативный расчёт реализованного значения, и
засчитать их означало бы догадку о намерении вместо записанного текста. «наблюдаемая инфляция
инФОМ…» — тоже не признак: это запрос про смежный показатель (опрос, заказанный и опубликованный
Банком России, AGENTS §7 T7.85), а не поиск альтернативной оценки ИПЦ; для утверждения самой
наблюдаемой инфляции такой запрос был бы вообще поиском собственного первоисточника.

Оригинальность связи с утверждением — членство сессии в записанных связях claim↔session
(`claims.created_in_session`, `claim_assessments.created_in_session`), а НЕ текст запроса:
текст запроса отвечает только на вопрос «был ли этот запрос поиском независимых оценок».
"""

from __future__ import annotations

import json
import re

__all__ = [
    "INDEPENDENT_SEARCH_PHRASES",
    "INDEPENDENT_SEARCH_STEMS",
    "recorded_search_query",
    "search_query_targets_independent_evaluation",
]

# Основы-префиксы слов независимости (покрывают «независимая/независимые»,
# «альтернативная/альтернативные», «неофициальная»; английские формы — explorer-v9 правило 10
# разрешает запросы вида «inflation estimate independent Russia 2025»). Префикс, а не точное
# слово: падежи и приставки не должны тихо снимать записанный маркер.
INDEPENDENT_SEARCH_STEMS: frozenset[str] = frozenset(
    {"независим", "альтернативн", "неофициальн", "independent", "alternativ"}
)

# Названия независимых индексов цен, названных прямо в запросе (замеры стенда .92: сессия
# `f0d7e844` искала «Чек-индекс» как альтернативный индекс цен). Только дефисная форма:
# «онлайн чек» раздельно — уже не название индекса, а бытовое слово; придумывать за автора
# запроса мы не вправе.
INDEPENDENT_SEARCH_PHRASES: frozenset[str] = frozenset({"чек-индекс", "онлайн-чек"})

_TOKEN_RE = re.compile(r"[a-zа-яё]+")


def _normalized_query(query: str) -> str:
    """Копия запроса для разбора: та же типографская нормализация, что у формулировок (T7.77)."""
    text = query.replace("\u00a0", " ").replace("\u202f", " ").replace("\u00ad", "").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def search_query_targets_independent_evaluation(query: str) -> bool:
    """Запрос сформулирован про независимую оценку: по записанному тексту, без догадок."""
    text = _normalized_query(query)
    if not text:
        return False
    if any(phrase in text for phrase in INDEPENDENT_SEARCH_PHRASES):
        return True
    tokens = _TOKEN_RE.findall(text)
    return any(token.startswith(stem) for token in tokens for stem in INDEPENDENT_SEARCH_STEMS)


def recorded_search_query(arguments: object) -> str | None:
    """Текст запроса из audit-_payload действия `action_started`.

    Хост пишет `arguments` каноническим JSON с потолком 1000 знаков (`_cap_args`); схема
    `web.search` ограничивает сам запрос 500 знаками, поэтому записанный текст читается целым.
    Нечитаемый payload (не JSON, не объект, не строка в `query`) — None: витрина не имеет права
    цитировать или засчитывать то, что не может прочитать из записи.
    """
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            return None
    elif isinstance(arguments, dict):
        parsed = arguments
    else:
        return None
    if not isinstance(parsed, dict):
        return None
    query = parsed.get("query")
    return query if isinstance(query, str) and query.strip() else None
