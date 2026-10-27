"""Unit (T7.137 часть 1а): закрытый каталог акторов, распознавание в тексте вопроса и запроса,
трекер «какой названный актор уже имел отдельный поиск» и решение гейта повторного сводного поиска.

Замер менеджера по фикстурам 71 завершённой сессии (2026-10-27, ADR-0036 §2): из 423 выполненных
поисков 41 — сводные (запрос называет >= 3 акторов, названных вопросом), и 22 из них (54 %) не
принесли ни одного чтения следом — против 30 % у поисков по одному актору. Каталог ЗАКРЫТ И ЖИВЁТ
В КОДЕ (не конфиг): вопросы стенда называют Банк России, Сбер/SberCIB, ВТБ, Альфа-Банк,
Газпромбанк, Росстат, hh.ru, SuperJob, Минэкономразвития, Минфин; вопрос про актора вне каталога
гейт не трогает (ограничение записано в ADR-0036 §2). Распознавание — на тех же основах слов, что
гейт почти-дубликата; «Банка России», «SberCIB», «Альфа-Банка», «Газпромбанка» находятся,
ГОЛЫЙ «банк»/«банки»/«банков» актором не является.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.question_actors import (
    ACTOR_CATALOG,
    MULTI_ACTOR_MIN,
    ActorSearchTracker,
    named_actors,
    named_query_actors,
    named_question_actors,
    uncovered_for,
)

pytestmark = [pytest.mark.unit]

# реальный текст вопроса c3 со стенда (контрольная фраза «по прогнозу Банка России»)
C3_QUESTION = (
    "Какие прогнозы инфляции в России на 2026 год дают аналитики крупных банков "
    "(Сбербанк, ВТБ, Альфа-Банк, Газпромбанк)? Сравните с прогнозом Банка России."
)
SALARY_QUESTION = (
    "Как изменились реальные заработные платы в России в 2026 году по данным Росстата "
    "и независимых оценок (hh.ru, SuperJob)?"
)


# ─── распознавание (часть 1а) ──────────────────────────────────────────────────


def test_catalog_is_closed_and_display_names_are_stable() -> None:
    assert [actor.name for actor in ACTOR_CATALOG] == [
        "Банк России",
        "Сбербанк",
        "ВТБ",
        "Альфа-Банк",
        "Газпромбанк",
        "Росстат",
        "hh.ru",
        "SuperJob",
        "Минэкономразвития",
        "Минфин",
    ]
    assert MULTI_ACTOR_MIN == 3


def test_question_text_recognizes_all_five_actors_in_catalog_order() -> None:
    assert named_question_actors(C3_QUESTION) == (
        "Банк России",
        "Сбербанк",
        "ВТБ",
        "Альфа-Банк",
        "Газпромбанк",
    )
    assert named_question_actors(SALARY_QUESTION) == ("Росстат", "hh.ru", "SuperJob")


def test_inflections_and_latin_forms_are_recognized() -> None:
    # формы из стендовых запросов: «Банка России», «SberCIB», «Альфа-Банка», «Газпромбанка»
    assert named_query_actors("прогноз инфляции 2026 по прогнозу Банка России") == ("Банк России",)
    assert named_query_actors("прогнозы инфляции России 2026 Сбербанк SberCIB ВТБ") == (
        "Сбербанк",
        "ВТБ",
    )
    assert named_query_actors("аналитики Альфа-Банка и Газпромбанка") == ("Альфа-Банк", "Газпромбанк")
    # «ЦБ», «cbr.ru», «Центробанк» — тот же актор
    assert named_query_actors("ключевая ставка ЦБ") == ("Банк России",)
    assert named_query_actors("cbr.ru прогноз инфляции") == ("Банк России",)
    assert named_query_actors("Центробанк повысил ставку") == ("Банк России",)
    # домены вакансий делятся точкой (как в гейте почти-дубликата): stats.hh.ru — hh.ru
    assert named_query_actors("stats.hh.ru оценка зарплат") == ("hh.ru",)
    assert named_query_actors("HeadHunter индекс зарплат") == ("hh.ru",)
    assert named_query_actors("SuperJob оценка зарплат") == ("SuperJob",)
    assert named_query_actors("Росстата выпуск цен") == ("Росстат",)
    assert named_query_actors("минфин обзор") == ("Минфин",)
    assert named_query_actors("Минэкономразвития прогноз") == ("Минэкономразвития",)


def test_bare_bank_is_not_an_actor() -> None:
    # «аналитики крупных банков» называет банки, но НЕ названные акторы — свойство замера
    assert named_query_actors("аналитики крупнейших банков прогноз инфляции 2026") == ()
    assert named_query_actors("банк повысил ставку") == ()
    # «банки России» — не «Банк России» (та же регулярка единственного числа, что в T7.119)
    assert named_query_actors("банки России отчитались") == ()


def test_empty_text_has_no_actors() -> None:
    assert named_actors("") == ()
    assert ActorSearchTracker(named=()).uncovered() == ()


# ─── трекер: отдельный поиск и сводный (часть 1а) ──────────────────────────────


def test_separate_search_marked_only_up_to_two_named_actors() -> None:
    tracker = ActorSearchTracker(named=named_question_actors(C3_QUESTION))
    # отдельный поиск по одному актору — покрытие растёт
    tracker.mark_executed("ВТБ прогноз инфляции 2026", 1)
    assert tracker.uncovered() == ("Банк России", "Сбербанк", "Альфа-Банк", "Газпромбанк")
    # отдельный поиск ровно по двум — покрытие растёт (правило: не больше одного другого)
    tracker.mark_executed("Альфа-Банк Газпромбанк прогноз инфляции 2026", 2)
    assert tracker.uncovered() == ("Банк России", "Сбербанк")
    # сводный (>= 3) — никого не покрывает
    tracker.mark_executed("Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026", 3)
    assert tracker.uncovered() == ("Банк России", "Сбербанк")
    # запрос без названных акторов — тоже не покрытие
    tracker.mark_executed("ключевая ставка прогноз", 4)
    assert tracker.uncovered() == ("Банк России", "Сбербанк")


def test_tracker_first_aggregate_arms_then_refuses_aggregates_only() -> None:
    tracker = ActorSearchTracker(named=named_question_actors(C3_QUESTION))
    # до первого сводного — любой запрос законен
    assert tracker.repeat_refusal("Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026") is None
    tracker.mark_executed("Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026", 1)
    # второй сводный — отказ с прежним запросом и его шагом
    refusal = tracker.repeat_refusal("прогноз инфляции 2026 Сбербанк SberCIB ВТБ Альфа-Банк")
    assert refusal == ("Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026", 1)
    # повторный ОТСЮДА не вооружает иначе: прежний запрос остаётся названным
    tracker.mark_executed("ипотечные ставки 2026", 2)
    assert tracker.repeat_refusal("ВТБ Альфа-Банк Газпромбанк обзор прогнозов") == refusal


def test_gate_silent_for_questions_with_fewer_than_three_named_actors() -> None:
    # вопрос называет 2 акторов каталога — даже запрос с >= 3 акторами каталога не отказывается
    tracker = ActorSearchTracker(named=("Росстат", "hh.ru"))
    tracker.mark_executed("Сбербанк ВТБ Альфа-Банк прогноз инфляции", 1)
    assert tracker.repeat_refusal("Сбербанк ВТБ Газпромбанк прогноз инфляции") is None
    # вопрос без акторов — трекер молчит всегда
    empty = ActorSearchTracker(named=())
    empty.mark_executed("Сбербанк ВТБ Альфа-Банк прогноз", 1)
    assert empty.repeat_refusal("Сбербанк ВТБ Газпромбанк прогноз") is None


def test_aggregate_uses_intersection_with_question_named_actors() -> None:
    # запрос называет >= 3 акторов КАТАЛОГА, но ни одного НАЗВАННОГО вопросом — не сводный
    tracker = ActorSearchTracker(named=("Росстат", "hh.ru", "SuperJob"))
    tracker.mark_executed("Сбербанк ВТБ Альфа-Банк прогноз зарплат", 1)
    assert tracker.repeat_refusal("Сбербанк ВТБ Газпромбанк прогноз зарплат") is None


def test_uncovered_for_helper_matches_tracker_rule() -> None:
    named = named_question_actors(C3_QUESTION)
    executed = [
        ("Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026", 1),
        ("ВТБ прогноз инфляции 2026 год", 2),
    ]
    assert uncovered_for(named, executed) == ("Банк России", "Сбербанк", "Альфа-Банк", "Газпромбанк")
