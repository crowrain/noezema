"""Unit: кириллическая передача имени hh.ru в словаре первоисточников (T7.129 часть 3).

Стендовый случай `.92` (утверждение a7cac406 «По данным hh.ru, hh-индекс в сентябре 2026
года составил 8,5», ложное «E3 · Проверено»): страница smart-lab.ru/company/mozgovik/
blog/1359553.php называет производителя РУССКОЙ передачей имени — «Хэдхантер» в первой
строке и «Хедхантер» в тексте того же поста (две передачи одного имени). Латинские алиасы
записи `hh` («hh.ru», «hh», «headhunter») такую страницу не опознавали, поэтому хост не
видел на странице производителя вовсе.

Границы правки (они же — проверки этого файла):
- добавлена ОДНА кириллическая форма с классом `[эе]`: обе передачи встречаются в одном
  тексте, вторую выбирать «на глаз» было бы вымыслом; класс из двух букв не создаёт новых
  омоглиф (AGENTS §7, T7.110/T7.126a);
- домашние хосты `hh` НЕ пополнены setka.ru: сохранённая страница поста setka.ru —
  публикация карьерного консультанта («Ольга Мокриенко, Карьерный консультант») в социальной
  сети от hh.ru, то есть пересказ пользователя, а не публикация самого производителя.
  Самоисключение по домашнему хосту здесь приписало бы странице авторство её же данных.
"""

from __future__ import annotations

import pytest

from apps.research_proxy.source_attribution import (
    PRIMARY_SOURCES,
    detect_source_attribution,
    is_home_host,
    named_primary_keys,
    primary_source,
)

pytestmark = pytest.mark.unit

SMART_LAB_URI = "https://smart-lab.ru/company/mozgovik/blog/1359553.php"
SETKA_URI = "https://setka.ru/posts/01a11c97-43db-7b60-a22a-036755095369"

#: дословные строки сохранённой страницы smart-lab (фикстура менеджера fixtures-t7129)
SMART_LAB_TITLE = "Хэдхантер. Рынок труда в сентябре: hh.индекс снизился до 8,5"
SMART_LAB_NOTE = "Хедхантер публикует ежемесячно"
SMART_LAB_FOOTER = "HeadHunter (Хэдхантер)"


def test_cyrillic_spelling_names_the_producer() -> None:
    """Обе кириллические передачи одного имени опознаются как `hh`."""
    assert named_primary_keys(SMART_LAB_TITLE) == ("hh",)
    assert named_primary_keys(SMART_LAB_NOTE) == ("hh",)
    assert named_primary_keys(SMART_LAB_FOOTER) == ("hh",)


def test_page_attribution_reads_the_cyrillic_name_on_a_foreign_host() -> None:
    """Страница чужого хоста, называющая производителя по-русски, — derivative на `hh`."""
    decision = detect_source_attribution(
        canonical_uri=SMART_LAB_URI,
        text="Как сообщает Хэдхантер, hh-индекс в сентябре снизился до 8,5 пункта.",
    )
    assert (decision.status, decision.primary_key) == ("derivative", "hh")
    assert decision.basis_fragment


def test_latin_aliases_are_unchanged() -> None:
    """Латинские формы имени были в словаре и до правки — они не потеряны."""
    for text in ("По данным hh.ru, hh-индекс составил 8,5.", "HeadHunter публикует отчёт."):
        assert named_primary_keys(text) == ("hh",), text


def test_setka_is_not_a_home_host_of_hh() -> None:
    """setka.ru остаётся чужим хостом для hh (причина — в шапке модуля)."""
    spec = primary_source("hh")
    assert spec is not None
    assert spec.home_hosts == ("hh.ru", "stats.hh.ru")
    assert not is_home_host(SETKA_URI, spec.home_hosts)
    assert is_home_host("https://hh.ru/", spec.home_hosts)
    assert is_home_host("https://stats.hh.ru/", spec.home_hosts)


def test_only_one_entry_gained_the_alias() -> None:
    """Словарь не менялся больше: записей столько же, имена и ключи прежние."""
    assert len(PRIMARY_SOURCES) == len({spec.key for spec in PRIMARY_SOURCES})
    hh = primary_source("hh")
    assert hh is not None and hh.name == "hh.ru"
    assert "х[эе]дхантер[а-яё]*" in hh.alias_patterns
