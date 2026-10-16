"""T7.118 (т9): контрольный набор docs/eval/control-set-v1.jsonl — структура и закрепленные тексты.

Набор из пяти вопросов читает `deploy/dev-stand/control-run.sh` (запускает оператор) и по нему
сверяются ожидания в docs/eval/CONTROL-SET.md. Тексты закреплены здесь ровно теми, что
записаны в файле: изменение текста — это смена контрольного замера, оно требует явной правки
этого теста и STATUS-записи, а не тихой правки JSONL. Суффикс метки « (контроль …)» в текстах
НЕЗАПРЕЩЁН: его добавляет скрипт при отправке, иначе повторный прогон другой меткой не
отличим от первого.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

CONTROL_SET = Path(__file__).resolve().parents[2] / "docs" / "eval" / "control-set-v1.jsonl"

FIXED_IDS = ["c1", "c2", "c3", "c4", "c5"]
FIXED_KINDS = ["official+independent", "official+independent", "independent-forecasts",
               "official-figure", "computation"]
FIXED_TEXTS = [
    "Как изменились реальные заработные платы в России в 2026 году по данным Росстата и что "
    "показывают независимые источники (hh.ru, SuperJob, аналитики банков)? Приведи официальные "
    "цифры и независимые оценки раздельно, с источниками и периодами.",
    "Каков уровень безработицы в России по свежим данным Росстата, и что показывают независимые "
    "индикаторы рынка труда (индексы hh.ru, оценки аналитиков банков)? Приведи официальные "
    "цифры и независимые оценки раздельно, с источниками.",
    "Какие прогнозы инфляции в России на 2026 год дают аналитики крупных банков (Сбербанк, ВТБ, "
    "Альфа-Банк, Газпромбанк) и чем они отличаются от прогноза Банка России? Приведи прогноз "
    "каждого с источником и датой.",
    "Какова годовая инфляция в России в сентябре 2026 года по данным Росстата? Приведи "
    "официальную цифру с источником.",
    "Сколько простых чисел меньше 10000? Проверь вычислением.",
]


def _rows() -> list[dict[str, str]]:
    assert CONTROL_SET.is_file(), f"нет файла {CONTROL_SET}"
    raw = CONTROL_SET.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert raw.endswith("\n"), "JSONL обязан заканчиваться переводом строки"
    return rows


def test_control_set_has_exactly_five_rows_with_all_fields() -> None:
    rows = _rows()
    assert len(rows) == 5
    for row in rows:
        assert set(row) == {"id", "text", "kind", "expect"}
        for field in row.values():
            assert isinstance(field, str) and field.strip()


def test_control_set_ids_are_unique_and_stable() -> None:
    rows = _rows()
    ids = [row["id"] for row in rows]
    assert ids == FIXED_IDS
    assert len(set(ids)) == 5


def test_control_set_texts_are_pinned_to_the_plan() -> None:
    rows = _rows()
    assert [row["text"] for row in rows] == FIXED_TEXTS
    assert [row["kind"] for row in rows] == FIXED_KINDS


def test_control_set_texts_carry_no_control_suffix_and_fit_ask_ceiling() -> None:
    for row in _rows():
        assert "(контроль" not in row["text"]  # суффикс добавляет скрипт при отправке
        assert len(row["text"]) <= 2000  # потолок text у question_intake (AGENTS §6, T7.59)


def test_computation_question_pins_expected_result() -> None:
    rows = {row["id"]: row for row in _rows()}
    assert "1229" in rows["c5"]["expect"]
    assert rows["c5"]["kind"] == "computation"
