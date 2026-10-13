"""Unit: curator-v14 и config-v24 — номера claim с нуля и период как на странице (T7.106 часть а).

Стендовый случай фикстур T7.106 (сессия c89f7aba, вопрос 3701d1ce, .92, HEAD 5ce9365,
config-v23, curator-v13): куратор правильно типизировал оценки hh.ru (`external_fact` —
правило 12 сработало), но посчитал claims С ЕДИНИЦЫ: evidence_links указали claim_index
1,2,3,4 при четырёх claims → `evidence_link[3]: claim index 4 out of range` → отказ
предложения ЦЕЛИКОМ (ADR-0034 вариант B: диапазон индексов — свойство предложения) →
0 утверждений. Вторая ошибка того же ответа: период значения подменён годом вопроса
(страница: «−25% май 2025 vs май 2024», «+36% за 9 месяцев январь→сентябрь 2025»,
а claim сказал «в 2026 году») — хост период не проверяет, это только ответственность
промпта.

Решение пользователя от 2026-10-13 («действуй по плану»): curator-v14 = curator-v13
байт в байт, кроме строки `version:` и четырёх правок:
(а1) `claim_index` — позиция в массиве `claims` С НУЛЯ + сверка перед ответом +
     вымышленный пример на 2 claims и 3 связи;
(а2) новое правило 13: период в формулировке и в `scope` — ровно тот, что назван
     на странице рядом со значением; смысл `as_of` (правило 4) не меняется;
(а3) parenthetical матрицы типов исправлен под вариант B (T7.105): пара вне матрицы
     отклоняет ЭТУ ОПЕРАЦИЮ, остальные применяются; если отклонены все — предложение;
(а4) новое правило 14: раздел «# Исправление предложения» в конце запроса (раунд
     исправления ADR-0034 вариант C, T7.106 часть б) — вернуть полное предложение
     заново, ничего не добавляя ради исправления.

config-v24 = config-v23 ровно с одной правкой: пин `prompts.curator` → curator-v14.
config-v23 и прежние payload'ы не тронуты; дефолт bootstrap'а dev-стенда (config-v20)
не изменён; активация config-v24 — решение менеджера.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.domain.canonical import canonical_sha256
from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts
from tests.unit.test_prompt_example_no_real_data import (
    _actor_hits,
    _new_lines,
    _pair_hits,
    _real_value_year_pairs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V13 = REPO_ROOT / "prompts" / "curator" / "curator-v13.md"
CURATOR_V14 = REPO_ROOT / "prompts" / "curator" / "curator-v14.md"
V23_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v23-payload.json"
V24_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v24-payload.json"

#: curator-v13 заморожен (пин config-v23): его байты не меняются (ADR-0019)
CURATOR_V13_SHA256 = "1d6809407c47b70a48f34d813e25d4a6c69a18fbb08def73113be936e6154e54"
#: curator-v14 — новый файл (пин config-v24)
CURATOR_V14_SHA256 = "c582d6fd1cad9eebf51d44241586e108e88721a34cec2f115649d996c88d6eeb"

#: canonical payload'ов: v23 заморожен (эталон отчёта T7.105), v24 закреплён (эталон T7.106)
V23_CANONICAL_SHA256 = "4cc1c296e6ee5cb0036d909b05a67a2a30406fdff8cb7f8841d48164ec64778f"
V24_CANONICAL_SHA256 = "9f0edb1436602ec0dcd90b3aba3da1c07677a47ee2ef08ee11c44a8912a44530"

#: ровно эти четыре правки applied to v13 -> v14 (байт в байт, больше — никаких)
EDITS: list[tuple[str, str]] = [
    (
        "version: curator-v13",
        "version: curator-v14",
    ),
    (
        "  - допустимые пары «тип claim'а → вид evidence» (пара вне матрицы →\n"
        "    rules engine отклоняет ВСЁ предложение целиком):",
        "  - допустимые пары «тип claim'а → вид evidence» (пара вне матрицы →\n"
        "    rules engine отклоняет эту операцию; остальные операции применяются;\n"
        "    если отклонены все операции — отклоняется предложение целиком):",
    ),
    (
        "- `evidence_links` (верхний уровень предложения) — связь каждого\n"
        "  evidence (по индексу из списка) с claim: `evidence_index` →\n"
        "  `claim_index`, `relation`: `supports | counters`.\n"
        "  **Каждый claim обязан иметь минимум одну запись здесь**: claim с",
        "- `evidence_links` (верхний уровень предложения) — связь каждого\n"
        "  evidence (по индексу из списка) с claim: `evidence_index` →\n"
        "  `claim_index`, `relation`: `supports | counters`.\n"
        "  `claim_index` — позиция claim в массиве `claims`, считая С НУЛЯ\n"
        "  (первый claim — 0, последний — число claims минус 1); `evidence_index` —\n"
        "  число в квадратных скобках строки раздела Evidence ([0], [1], …).\n"
        "  Перед ответом сверь: каждый `claim_index` меньше числа claims, каждый\n"
        "  claim имеет хотя бы одну связь, и связь указывает ровно на тот claim,\n"
        "  который это evidence действительно подтверждает. Пример — ВЫМЫШЛЕННЫЙ\n"
        "  случай (два claims, три связи; evidence [0] — страница сервиса\n"
        "  „Гелиос“, [1] — отчёт группы „Веер“):\n"
        "\n"
        "  ```json\n"
        "  {\"claims\": [\n"
        "    {\"statement\": \"Сервис „Гелиос“ (Тармения): индекс конкуренции соискателей на вакансии — 5,7%\", "
        "\"claim_type\": \"external_fact\"},\n"
        "    {\"statement\": \"Группа „Веер“ (Кварления): индекс напряжённости рынка труда — 4,2%\", "
        "\"claim_type\": \"external_fact\"}],\n"
        "   \"evidence_links\": [\n"
        "    {\"evidence_index\": 0, \"claim_index\": 0, \"relation\": \"supports\"},\n"
        "    {\"evidence_index\": 1, \"claim_index\": 0, \"relation\": \"counters\"},\n"
        "    {\"evidence_index\": 1, \"claim_index\": 1, \"relation\": \"supports\"}]}\n"
        "  ```\n"
        "\n"
        "  **Каждый claim обязан иметь минимум одну запись здесь**: claim с",
    ),
    (
        "   (для `empirical_conjecture` допустимо только `experiment_run`). Официальная публикация\n"
        "   ведомства остаётся публикацией ведомства (правило 11).",
        "   (для `empirical_conjecture` допустимо только `experiment_run`). Официальная публикация\n"
        "   ведомства остаётся публикацией ведомства (правило 11).\n"
        "13. Период значения — в формулировке claim и в `scope` — ровно тот, который указан\n"
        "   на странице рядом с этим значением: если страница пишет «май 2030 к маю 2029», так\n"
        "   и запиши; «январь–сентябрь 2030» — девять месяцев 2030 года, а не весь год.\n"
        "   Не подставляй в период год вопроса, год публикации, год чтения или «текущий»\n"
        "   год: страница назвала период — бери его дословно (пример — ВЫМЫШЛЕННЫЙ случай:\n"
        "   «Сервис „Гелиос“: рост конкуренции соискателей на вакансии в Тармении в мае 2030\n"
        "   года к маю 2029 года — 7,8%»); период на странице не указан — так и скажи в\n"
        "   формулировке и не выдумывай период. Для `temporal_fact` смысл `as_of` определяет\n"
        "   правило 4, оно не меняется.\n"
        "14. Если в конце этого запроса есть раздел «# Исправление предложения» — хост вернул\n"
        "   твоё предложение на ОДИН раунд исправления. Верни ПОЛНОЕ предложение заново, по\n"
        "   той же схеме ответа, исправив названные хостом проблемы (индексы claims и\n"
        "   evidence — с нуля). Ничего не добавляй ради исправления: ни новых claims, ни\n"
        "   новых evidence, ни новых вопросов; значения и периоды бери только из текста\n"
        "   улик выше.",
    ),
]


def _apply_edits(base: str) -> str:
    text = base
    for old, new in EDITS:
        assert text.count(old) == 1, f"якорь правки встречается не ровно один раз: {old[:60]!r}"
        text = text.replace(old, new)
    return text


def _numbered_rules(text: str) -> list[str]:
    """Тело каждого нумерованного правила — от его строки до следующей строки `N.` или EOF."""
    starts = [
        (int(m.group(1)), m.start())
        for m in re.finditer(r"(?m)^(\d+)\. ", text)
    ]
    rules: list[str] = []
    for pos, (_number, start) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(text)
        rules.append(text[start:end].rstrip())
    return rules


# ─── байтовая правка v13 → v14 ────────────────────────────────────────────────


@pytest.mark.unit
def test_curator_v14_is_exact_bytes_of_v13_with_the_four_edits() -> None:
    v13 = CURATOR_V13.read_text(encoding="utf-8")
    assert hashlib.sha256(v13.encode()).hexdigest() == CURATOR_V13_SHA256, "curator-v13 переписан"
    v14 = CURATOR_V14.read_text(encoding="utf-8")
    assert hashlib.sha256(v14.encode()).hexdigest() == CURATOR_V14_SHA256
    assert v14 == _apply_edits(v13), "curator-v14 отличается от v13 не ровно четырьмя закреплёнными правками"


@pytest.mark.unit
def test_curator_v14_rules_1_to_12_are_bytes_of_v13_and_two_new_rules_exist() -> None:
    old = _numbered_rules(CURATOR_V13.read_text(encoding="utf-8"))
    new = _numbered_rules(CURATOR_V14.read_text(encoding="utf-8"))
    assert len(old) == 12, old[:3]
    assert len(new) == 14, len(new)
    assert new[:12] == old, "правила 1–12 curator-v14 не байт в байт curator-v13 (ADR-0019)"
    assert new[12].startswith("13.") and "Период значения" in new[12]
    assert new[13].startswith("14.") and "# Исправление предложения" in new[13]
    # смысл правила 4 (as_of) не тронут: правило 4 байт в байт v13
    assert new[3] == old[3]


@pytest.mark.unit
def test_a1_zero_based_claim_index_rule_is_present_and_example_is_two_claims_three_links() -> None:
    text = CURATOR_V14.read_text(encoding="utf-8")
    links_section = text.split("- `evidence_links`")[1].split("- `new_questions`")[0]
    assert "считая С НУЛЯ" in links_section and "первый claim — 0" in links_section
    assert "число в квадратных скобках" in links_section
    assert "меньше числа claims" in links_section and "хотя бы одну связь" in links_section
    match = re.search(r"```json\n(.*?)```", links_section, re.S)
    assert match, "пример на 2 claims и 3 связи отсутствует"
    example = json.loads(match.group(1))
    assert len(example["claims"]) == 2
    links = example["evidence_links"]
    assert len(links) == 3
    assert sorted(link["claim_index"] for link in links) == [0, 0, 1]
    assert all(link["evidence_index"] in (0, 1) for link in links)
    # пример вымышленный: собственные имена не пересекаются с реальными акторами витрины
    assert not _actor_hits(match.group(1))


@pytest.mark.unit
def test_a3_matrix_parenthetical_now_describes_variant_b() -> None:
    text = CURATOR_V14.read_text(encoding="utf-8")
    assert "ВСЁ предложение целиком" not in text, (
        "старая формулировка отказа всем предложением вернулась в промпт (правило с T7.105 — пооперационный отказ)"
    )
    assert "отклоняет эту операцию; остальные операции применяются" in text
    assert "если отклонены все операции — отклоняется предложение целиком" in text


@pytest.mark.unit
def test_a2_period_rule_forbids_substituted_year_and_keeps_as_of_meaning() -> None:
    rule = _numbered_rules(CURATOR_V14.read_text(encoding="utf-8"))[12]
    assert "ровно тот, который указан" in rule and "рядом с этим значением" in rule
    assert "«май 2030 к маю 2029»" in rule and "январь–сентябрь 2030" in rule
    assert "год вопроса" in rule and "год публикации" in rule and "«текущий»" in rule
    assert "не выдумывай период" in rule
    # смысл as_of не переписывается: правило прямо отсылает к правилу 4
    assert "правило 4" in rule


# ─── отсутствие реальных данных в новом тексте (страж T7.97a/T7.102, без исключений) ──


@pytest.mark.unit
def test_curator_v14_new_text_carries_no_real_case_data() -> None:
    """Прямое закрепление того, что общий страж `test_prompt_example_no_real_data.py`
    применяет к v14 без исключений: новый текст правил не называет акторов витрины,
    не воспроизводит связку «значение + период» и не противоречит примерам корпуса."""
    new_text = "\n".join(_new_lines(CURATOR_V14, CURATOR_V13))
    assert new_text, "правого сравнения v14↔v13 не получилось"
    hits = _actor_hits(new_text)
    assert not hits, f"новый текст curator-v14 называет реальных акторов {sorted(hits)}"
    pairs = _pair_hits(new_text, _real_value_year_pairs())
    assert not pairs, f"новый текст curator-v14 воспроизводит «значение + период» реального случая {sorted(pairs)}"
    for corpus in sorted((REPO_ROOT / "docs" / "eval").glob("question-set-*.jsonl")):
        content = corpus.read_text(encoding="utf-8")
        for block in re.findall(r"```json\n(.*?)```", CURATOR_V14.read_text(encoding="utf-8"), re.S):
            example = json.loads(block)
            for claim in example.get("claims", []):
                assert claim["statement"] not in content, (
                    f"пример v14 {claim['statement']!r} встречается в корпусе {corpus.name}"
                )


# ─── пины config-v24 и неизменность config-v23 ───────────────────────────────


@pytest.mark.unit
def test_config_v24_is_v23_with_only_the_curator_pin() -> None:
    v23 = json.loads(V23_PAYLOAD.read_text(encoding="utf-8"))
    v24 = json.loads(V24_PAYLOAD.read_text(encoding="utf-8"))
    assert canonical_sha256(v23) == V23_CANONICAL_SHA256, "canonical config-v23 изменился"
    assert canonical_sha256(v24) == V24_CANONICAL_SHA256
    assert v24["schema_version"] == v23["schema_version"]
    untouched = {key for key in v23 if key != "prompts"}
    for key in untouched:
        assert v24[key] == v23[key], f"config-v24 изменил раздел {key}"
    assert set(v24) == set(v23), "config-v24 изменил набор разделов"
    for role, pin in v23["prompts"].items():
        if role == "curator":
            continue
        assert v24["prompts"][role] == pin, f"config-v24 изменил пин {role}"
    assert v24["prompts"]["curator"]["path"] == "prompts/curator/curator-v14.md"
    assert v24["prompts"]["curator"]["version"] == "curator-v14"
    assert v24["prompts"]["curator"]["sha256"] == CURATOR_V14_SHA256
    # на диске пин совпадает с байтами файла (content pinning, ADR-0019)
    disk = CURATOR_V14.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == v24["prompts"]["curator"]["sha256"]


@pytest.mark.unit
def test_config_v23_and_previous_payloads_stay_frozen_on_disk() -> None:
    """config-v23 остаётся пином curator-v13 побайтно; прежние payload'ы не переписаны
    (эталон canonical зафиксирован тестом T7.105)."""
    v23 = json.loads(V23_PAYLOAD.read_text(encoding="utf-8"))
    assert v23["prompts"]["curator"]["version"] == "curator-v13"
    assert v23["prompts"]["curator"]["sha256"] == CURATOR_V13_SHA256
    disk = CURATOR_V13.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == CURATOR_V13_SHA256
    v22 = json.loads((REPO_ROOT / "docs" / "eval" / "config-v22-payload.json").read_text(encoding="utf-8"))
    assert canonical_sha256(v22) == "05ada8a415fa376dc4b443b1f073057cbf7d35627035eea551a5f2a2c34513fa"


@pytest.mark.unit
def test_pinned_prompts_of_config_v24_resolve_and_tamper_fails_closed() -> None:
    payload = json.loads(V24_PAYLOAD.read_text(encoding="utf-8"))
    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.CURATOR].version == "curator-v14"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V14_SHA256
    assert "# Исправление предложения" in loaded[Role.CURATOR].text
    tampered = json.loads(json.dumps(payload))
    tampered["prompts"]["curator"] = dict(tampered["prompts"]["curator"], sha256="0" * 64)
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered["prompts"], REPO_ROOT)


# ─── стенд: дефолт bootstrap'а не тронут, config-v24 нигде не активирован ────


@pytest.mark.unit
def test_dev_stand_scripts_do_not_activate_config_v24() -> None:
    """Активация config-v24 — решение менеджера: скрипты dev-стенда не ссылаются на v24,
    дефолт bootstrap'а и reset-db.sh — прежний (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v24" not in text, f"{name} уже ссылается на config-v24"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
