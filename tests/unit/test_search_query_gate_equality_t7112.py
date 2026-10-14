"""T7.112 (б): гейт почти-повторов web.search по РАВЕНСТВУ основ содержательных слов.

Правило T7.111a («ни одного нового содержательного слова относительно прежнего поиска»,
⊆) заменено равенством: отказ только когда множество основ нового запроса равно основам
какого-то выполненного ранее запроса сессии (ADR-0036 §1а, решение пользователя 2026-10-14).
Сужение и расширение набора слов — законные поиски (случай стендовой сессии 73c33c72: после
широкого запроса про несколько банков сужения «Газпромбанк …», «ВТБ …» отклонялись и
блокировали вторую линию проверки; теперь выполняются). Перестановка, падежные формы и
буквальный повтор — по-прежнему отказ. Слова «год/года/году/годы/г» добавлены в закрытый
список служебных: период в запросе несёт число, отдельным содержательным словом не является
(STATUS T7.112)."""

from __future__ import annotations

import pytest

from apps.orchestrator.tool_context import SEARCH_QUERY_SERVICE_WORDS, search_near_duplicate

pytestmark = pytest.mark.unit

# широкая линия поиска прогнозов (воспроизведение запроса сессии 73c33c72)
BROAD_73 = "прогноз инфляции 2026 год Сбербанк ВТБ Альфа-Банк Газпромбанк аналитики"
NARROW_GAZPROM = "Газпромбанк прогноз инфляции 2026 год"
NARROW_VTB_ANALYSTS = "ВТБ прогноз инфляции 2026 год аналитики"

SALARY_PRIOR = "Росстат реальные заработные платы 2026 год"
SALARY_COPY = "Росстат реальные заработные платы 2026"
SALARY_REORDER = "реальные заработные платы Россия 2026 Росстат"


# ─── т5: сужение и расширение выполняются ────────────────────────────────────────────────


def test_narrowing_from_broad_actor_query_runs() -> None:
    # было (T7.111a): сужение без нового слова — отказ; стало (T7.112б): основы не равны
    # (в прежнем запросе больше акторов) — выполняется (случай 73c33c72, STATUS T7.112)
    assert search_near_duplicate(NARROW_GAZPROM, [(BROAD_73, 2)]) is None
    assert search_near_duplicate(NARROW_VTB_ANALYSTS, [(BROAD_73, 2)]) is None


def test_expansion_with_a_new_content_word_runs() -> None:
    prior = "Росстат уровень безработицы Россия 2026"
    assert search_near_duplicate(prior + " август", [(prior, 1)]) is None
    # и обратно: прежний запрос был сужением нового — расширение тоже выполняется
    assert search_near_duplicate(prior, [(prior + " август", 1)]) is None


# ─── т5: перестановка, словоформы и буквальный повтор остаются отказом ────────────────────


def test_permutation_and_word_forms_are_still_denied() -> None:
    prior = "SberCIB прогноз инфляции 2026 6,5 Альфа-Банк 7,5"
    assert search_near_duplicate("SberCIB 6,5% Альфа-Банк 7,5% прогноз инфляции 2026", [(prior, 1)]) is not None
    prior_forms = "Росстат независимая оценка зарплат в России"
    outcome = search_near_duplicate("оценки зарплатах Россия Росстат независимые", [(prior_forms, 1)])
    assert outcome is not None and outcome[2] < 1.0  # Жаккарр назван справочно, отказ по равенству основ


def test_true_salary_repeat_is_denied_even_without_the_service_year_word() -> None:
    # «год» — служебное слово (T7.112б): копии с «год» и без него имеют равные основы
    assert {"год", "года", "году", "годы"} <= SEARCH_QUERY_SERVICE_WORDS
    outcome = search_near_duplicate(SALARY_COPY, [(SALARY_PRIOR, 1)])
    assert outcome is not None and outcome[1] == 1
    assert search_near_duplicate(SALARY_REORDER, [(SALARY_PRIOR, 1)]) is None  # новое слово «Россия»


def test_exact_copy_denies_at_first_repeat_and_across_every_prior() -> None:
    query = "инФОМ инфляционные ожидания населения сентябрь 2026"
    priors = [("инфляция в России прогноз 2026", 1), (query, 3)]
    outcome = search_near_duplicate(query, priors)
    assert outcome is not None and outcome[1] == 3
