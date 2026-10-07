"""Unit: curator-v8 — перепроверка не обнуляет опору и связывает всё использованное
(T7.73, уточнение ADR-0018).

Подставка .92: куратор перепроверил уже подтверждённый `temporal_fact`, принёс
третий источник — и в предложении не оказалось ни опорной даты, ни второго
связанного доказательства. Хост прежней ветки пересчитал дату из вопроса
перепроверки (`null`), движок правил честно доложил `as_of_missing`, и вывод
упал с E3 supported до E1 hypothesis (STATUS T7.73). curator-v8 закрывает обе
половины ошибки на уровне инструкции модели:

* дата якоря сохраняется (`as_of: null` = «сохранить»), новое значение —
  сознательная ревизия с обоснованием, молчаливой подмены нет; `scope` не
  отменяется;
* каждый использованный источник обязан быть привязан в `evidence_links`.

curator-v7 закоммичен и не переписывается: его байты закреплены хешем (его
тесты полноты матрицы и обязательной привязки остаются в силе), а v8 обязан
сохранять всякий его шаг — проверка «v7 ⊆ v8 построчно, порядок тот же».
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V7 = REPO_ROOT / "prompts" / "curator" / "curator-v7.md"
CURATOR_V8 = REPO_ROOT / "prompts" / "curator" / "curator-v8.md"
V15_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v15-payload.json"

#: curator-v7 заморожен (пин config-v11…v14): его байты не меняются никогда
CURATOR_V7_SHA256 = "19d6c6e8e2d890ae4cf5c42e5e0ec6bd864a2c6ef12fdb2766e01f977bc89a3a"
#: curator-v8 — новый файл, его идентичность закреплена (ADR-0019 content pinning)
CURATOR_V8_SHA256 = "c14603eed9119c474f85c2848b68ab5a1e661398f930f0effe5dbb50caadde71"


def _v8() -> str:
    assert CURATOR_V8.is_file(), "curator-v8.md is missing"
    return CURATOR_V8.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Текст промпта одной строкой: формулировка правила переносится, проверяем смысл."""
    return re.sub(r"\s+", " ", text)


def _numbered_rules(text: str) -> list[str]:
    """Строки-заголовки нумерованных правил промпта («4. `temporal_fact` обязателен…»):
    правила — единица, смысл которой закреплен; их набор и порядок проверяются тестом."""
    return [line.strip() for line in text.splitlines() if re.match(r"^\d+\. ", line)]


# ─── идентичность и неизменность прежней версии ───────────────────────────────


@pytest.mark.unit
def test_curator_v8_is_a_new_file_and_v7_stays_untouched() -> None:
    assert _v8().startswith("version: curator-v8\n")
    assert hashlib.sha256(CURATOR_V8.read_bytes()).hexdigest() == CURATOR_V8_SHA256
    # прежняя версия не переписана под новую (промпты задним числом не правятся)
    assert hashlib.sha256(CURATOR_V7.read_bytes()).hexdigest() == CURATOR_V7_SHA256


@pytest.mark.unit
def test_v8_keeps_every_rule_of_v7_and_adds_only_two() -> None:
    """v8 ничего из v7 не снимает: правила 1–7 сохранены дословно (заголовки), добавлены ровно
    8 и 9. Снятое или переформулированное правило краснит этот тест, а не остаётся незамеченным."""
    v7_rules = _numbered_rules(CURATOR_V7.read_text(encoding="utf-8"))
    v8_rules = _numbered_rules(_v8())
    assert len(v7_rules) == 7, v7_rules
    assert v8_rules[:7] == v7_rules, f"прежние правила изменены: {v8_rules[:7]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v8_rules]
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 8, 9], numbers


@pytest.mark.unit
def test_matrix_and_evidence_binding_wording_are_unchanged() -> None:
    """Матрица «тип → виды evidence» и обязательная привязка evidence из v7 переезжают
    дословно: их закрепляют тесты полноты (test_curator_prompt_matrix) и rules engine."""
    v7 = _flat(CURATOR_V7.read_text(encoding="utf-8"))
    v8 = _flat(_v8())
    for phrase in (
        "| `temporal_fact` | `source_assertion`, `quote_integrity` |",
        "**Каждый claim обязан иметь минимум одну запись здесь**",
        "`claim_type` = тип существующего claim'а",
        "Слова — не операция",
    ):
        assert phrase in v7, phrase
        assert phrase in v8, phrase


# ─── правило 8: опора перепроверки принадлежит якорю ──────────────────────────


@pytest.mark.unit
def test_reverify_may_leave_the_date_empty_and_keeps_the_anchor_date() -> None:
    v8 = _flat(_v8())
    assert "`as_of: null` при `existing_claim_id` означает «сохранить дату якоря»" in v8
    # пустое поле — не основание понизить оценку (решение ADR-0018, уточнённое в T7.73)
    assert "пустое поле предложения не является основанием понизить оценку" in v8
    assert "Понижение оценки возможно по делу (противоречие, устаревание, отзыв источника)" in v8


@pytest.mark.unit
def test_a_different_date_is_a_revision_that_must_be_explained() -> None:
    """Другое непустое значение — ревизия: молчаливой подмены хост не делает (ADR-0018)."""
    v8 = _flat(_v8())
    assert "если ты уверен, что прежняя дата больше не действует (ревизия)" in v8
    assert "принеси новое значение и объясни в `summary`, почему прежнее снято" in v8
    assert "без обоснования молча не применяется" in v8
    assert "остаётся дата якоря, а отказанное значение видно в журнале" in v8


@pytest.mark.unit
def test_a_question_that_names_the_date_still_moves_the_anchor() -> None:
    """Вопрос перепроверки с явной или относительной датой переносит привязку — это законно
    и попадает в журнал (ADR-0016/ADR-0017)."""
    v8 = _flat(_v8())
    assert "если вопрос перепроверки называет дату явно" in v8
    assert "требует текущей («на сегодня», «на текущую дату»)" in v8
    assert "хост перенесёт привязку на неё" in v8


@pytest.mark.unit
def test_scope_of_the_anchor_is_kept_and_extended() -> None:
    v8 = _flat(_v8())
    assert "`scope` существующего claim'а при перепроверке сохраняется и пополняется" in v8
    assert "не отменяет прежнее" in v8


@pytest.mark.unit
def test_temporal_fact_still_requires_a_date_for_new_claims() -> None:
    """Ослабление касается ровно одного случая — перепроверки существующего. Новый
    `temporal_fact` без даты по-прежнему отказывается (тест границы схемы)."""
    v8 = _flat(_v8())
    assert "4. `temporal_fact` обязателен `as_of`; без него используй `external_fact`." in v8
    assert "Исключение одно — перепроверка (`existing_claim_id`)" in v8
    assert "Новым временным утверждениям дата по-прежнему обязательна" in v8


@pytest.mark.unit
def test_changing_the_type_to_escape_the_date_is_called_out_as_useless() -> None:
    """Оценка считается по типу якоря (ADR-0018): переоформление temporal_fact в
    external_fact ради «избавиться от даты» — путь, который вёл к дефекту."""
    v8 = _flat(_v8())
    assert "Перепроверку существующего claim'а (`existing_claim_id`) оформляй типом существующего" in v8
    assert "оценку хост считает по типу якоря" in v8


# ─── правило 9: каждый использованный источник привязан ───────────────────────


@pytest.mark.unit
def test_every_used_source_must_be_linked() -> None:
    """Кейс подставки: ria.ru был прочитан и упомянут в summary, но не привязан —
    доказательств оказалось меньше, чем источников, и независимость считалась по меньшему
    числу. Prompt обязан этого не поддерживать."""
    v8 = _flat(_v8())
    assert "Каждый ИСПОЛЬЗОВАННЫЙ источник обязан иметь запись в `evidence_links`" in v8
    assert "она не участвует в оценке, и независимость считается по меньшему числу источников" in v8
    # обратная половина правила 1 сохранена: неиспользованное evidence не тянем
    assert "неиспользованное evidence к claim'у не тяни" in v8


@pytest.mark.unit
def test_deduplicated_evidence_is_still_worth_declaring() -> None:
    """Хост снимает дубль по идентичному содержимому — prompt обязан говорить, что это не повод
    источник не указывать (иначе модель «экономит» ссылки)."""
    v8 = _flat(_v8())
    assert "Если хост сочтёт доказательство повторяющимся" in v8
    assert "это не повод его не указывать" in v8


# ─── пин в config-v15 ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_v15_pins_curator_v8_and_corrupted_pin_fails_closed() -> None:
    from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

    payload = json.loads(V15_PAYLOAD.read_text(encoding="utf-8"))
    pin = payload["prompts"]["curator"]
    assert pin["version"] == "curator-v8"
    assert pin["path"] == "prompts/curator/curator-v8.md"
    assert pin["sha256"] == CURATOR_V8_SHA256

    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.CURATOR].version == "curator-v8"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V8_SHA256

    tampered = json.loads(json.dumps(payload["prompts"]))
    tampered["curator"]["sha256"] = "0" * 64
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
