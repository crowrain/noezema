"""Unit: curator-v16 и config-v28 — честность записей куратора в промпте (T7.121 часть 6–7).

Три хостовых гейта T7.121 (период рядом со значением, дата у прогноза, склейка
взаимных дублей) работают при любой конфигурации; промпт обязан объяснять моделью эти
границы заранее — по образцу T7.108 (curator-v15 и гейт «значение на связи»).

Решение по шаблону ADR-0019: curator-v16 = curator-v15 байт в байт, кроме строки
`version:`, дополнения к правилу 13 (гейт периода + отсылка к правилу 15) и трёх новых
правил 15–17 (дата прогноза; дубль-формулировки несутся одной операцией с
`existing_claim_id`; честный ответ из уже записанного знания — пустые `claims` с
`relied_claim_ids`). Примеры новые — только вымышленные (Тармения, „Веер“); реальных
значений, акторов и связок «значение + период» стенда в новом тексте нет.

config-v28 = config-v27 ровно с одной правкой: пин `prompts.curator` → curator-v16
(путь, sha256, version). config-v27 и прежние payload'ы не тронуты; дефолт bootstrap'а
dev-стенда (config-v20) не изменён; активация config-v28 — решение менеджера, здесь она
НЕ выполняется.
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
    INHERITED_REAL_DATA,
    _actor_hits,
    _inherited_lines,
    _new_lines,
    _pair_hits,
    _real_value_year_pairs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V15 = REPO_ROOT / "prompts" / "curator" / "curator-v15.md"
CURATOR_V16 = REPO_ROOT / "prompts" / "curator" / "curator-v16.md"
V27_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v27-payload.json"
V28_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v28-payload.json"

#: curator-v15 заморожен (пин config-v25): его байты не меняются (ADR-0019)
CURATOR_V15_SHA256 = "d229c3235424e98ac423aa400575fd646c84a9cea8f605e0b8e7f0513464920b"
#: curator-v16 — новый файл (пин config-v28)
CURATOR_V16_SHA256 = "9a32abf0e5d0b6e094ec5761d1de26b73f39cf8e84dd0087fe6dc34bd28a7d15"

#: canonical payload'ов: v27 заморожен (эталон T7.119a), v28 закреплён (эталон T7.121)
V27_CANONICAL_SHA256 = "9d9ebba4937392da1b03156c9a39d2e5a9b244337720fee677148d1c29d8c83c"
V28_CANONICAL_SHA256 = "2b77f24993edbe708b7cda97c6974e23af3798f4b7437460cce44f3058cc1cf5"
#: хеш файла config-v28 (с каноническим сравнением не путать: байты файла — свой формат)
V28_FILE_SHA256 = "c65a27bf53747eb736e0c2ba65b9c80ba13ff9136cba1669f083c349d761853c"

#: реальные значения витрины запрещены новому тексту промпта в любом виде
FORBIDDEN_LITERALS = ("5,59", "6,35", "6–7", "5–6", "14 октября")


def _numbered_rules(text: str) -> list[str]:
    """Тело каждого нумерованного правила — от его строки до следующей строки `N.` или EOF."""
    starts = [(int(m.group(1)), m.start()) for m in re.finditer(r"(?m)^(\d+)\. ", text)]
    rules: list[str] = []
    for pos, (_number, start) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(text)
        rules.append(text[start:end].rstrip())
    return rules


# ─── байтовая заморозка v15 и additive-форма v16 ──────────────────────────────


@pytest.mark.unit
def test_curator_v15_stays_frozen_and_v16_is_a_new_file() -> None:
    v15 = CURATOR_V15.read_bytes()
    assert hashlib.sha256(v15).hexdigest() == CURATOR_V15_SHA256, "curator-v15 переписан"
    v16 = CURATOR_V16.read_bytes()
    assert hashlib.sha256(v16).hexdigest() == CURATOR_V16_SHA256
    assert CURATOR_V16.read_text(encoding="utf-8").startswith("version: curator-v16\n")


@pytest.mark.unit
def test_curator_v16_keeps_rules_1_to_12_and_14_byte_identical_and_adds_three_rules() -> None:
    old = _numbered_rules(CURATOR_V15.read_text(encoding="utf-8"))
    new = _numbered_rules(CURATOR_V16.read_text(encoding="utf-8"))
    assert len(old) == 14, old[:3]
    assert len(new) == 17, "curator-v16 обязан добавить ровно правила 15–17"
    assert new[:12] == old[:12], "правила 1–12 curator-v16 не байт в байт curator-v15 (ADR-0019)"
    assert new[13] == old[13], "правило 14 curator-v16 не байт в байт curator-v15 (ADR-0019)"
    # правило 13 = прежний текст + закреплённое дополнение (прежнее — префикс нового)
    assert new[12].startswith(old[12]), "старый текст правила 13 переписан"
    addition = new[12][len(old[12]) :]
    assert "claim_period_not_near_value" in addition
    assert "один раунд исправления (правило 14)" in addition
    # границы гейта периода названы честно: безпериодные формулировки вне проверки,
    # месяц засчитывается и датой, период называет страница, а не модель
    assert "Формулировка без периода" in addition
    assert "или датой, которая его" in addition
    assert "период берётся ровно тот, что" in addition
    # новое дополнение не обещает лишнего: гейт локализует значение, а подтверждает связь
    # по-прежнему гейт значения T7.108 (правило 9 здесь не тронуто)
    assert new[8] == old[8], "правило 9 (гейт значения на связи) трогать нельзя"


@pytest.mark.unit
def test_new_rules_15_to_17_carry_the_three_commitments() -> None:
    rules = _numbered_rules(CURATOR_V16.read_text(encoding="utf-8"))
    r15, r16, r17 = rules[14], rules[15], rules[16]
    # правило 15: дата прогноза обязательна и берётся со страницы; отказа нет — прогноз
    # не записывается; «сегодня» вместо даты прогноза запрещено
    assert "дата прогноза" in r15 and "по состоянию на" in r15
    assert "обязано называть дату, когда прогноз сделан" in r15
    assert "Даты нет — прогноз не" in r15 and "подставлять вместо даты прогноза нельзя" in r15
    # вымышленный пример правила 15: „Веер“, Тармения
    assert "Веер" in r15 and "Тармении" in r15 and not _actor_hits(r15)
    # правило 16: дублирующая формулировка — одна операция с existing_claim_id;
    # склейка взаимных дублей хостом названа страховкой, а не правом модели
    assert "existing_claim_id" in r16 and "страховка хоста" in r16
    assert "склеивает их в самую раннюю запись" in r16
    # правило 17: честный ответ из записанного знания — пустые claims + relied_claim_ids,
    # это законный итог, а не отказ
    assert "пустой список `claims`" in r17 and "relied_claim_ids" in r17
    assert "законный итог" in r17


# ─── отсутствие реальных данных в новом тексте (страж T7.97a/T7.102/T7.108) ───


@pytest.mark.unit
def test_curator_v16_new_text_carries_no_real_case_data() -> None:
    new_text = "\n".join(_new_lines(CURATOR_V16, CURATOR_V15))
    assert new_text, "правого сравнения v16↔v15 не получилось"
    hits = _actor_hits(new_text)
    assert not hits, f"новый текст curator-v16 называет реальных акторов {sorted(hits)}"
    pairs = _pair_hits(new_text, _real_value_year_pairs())
    assert not pairs, f"новый текст воспроизводит «значение + период» реального случая {sorted(pairs)}"
    for literal in FORBIDDEN_LITERALS:
        assert literal not in new_text, f"в новом тексте промпта реальное значение витрины {literal!r}"
    # uuid из правил v15 в новом тексте не размножаются
    u = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    assert not u.search(new_text)


@pytest.mark.unit
def test_curator_v16_inherits_no_real_case_data() -> None:
    assert INHERITED_REAL_DATA.get(CURATOR_V16.name, set()) == set()
    found: set[tuple[str, str]] = set()
    for line in _inherited_lines(CURATOR_V16, CURATOR_V15):
        found |= {("актор", name) for name, _snippet in _actor_hits(line)}
        found |= {
            ("пара", f"{value}+{year}") for value, year in _pair_hits(line, _real_value_year_pairs())
        }
    assert not found, f"унаследованные реальные данные в curator-v16: {sorted(found)}"


# ─── пины config-v28 и неизменность config-v27 ────────────────────────────────


@pytest.mark.unit
def test_config_v28_is_v27_with_only_the_curator_pin() -> None:
    v27 = json.loads(V27_PAYLOAD.read_text(encoding="utf-8"))
    v28 = json.loads(V28_PAYLOAD.read_text(encoding="utf-8"))
    assert canonical_sha256(v27) == V27_CANONICAL_SHA256, "canonical config-v27 изменился"
    assert canonical_sha256(v28) == V28_CANONICAL_SHA256
    assert (
        hashlib.sha256(V28_PAYLOAD.read_bytes()).hexdigest() == V28_FILE_SHA256
    ), "байты файла config-v28-payload.json изменены не закреплённой правкой"
    assert v28["schema_version"] == v27["schema_version"]
    untouched = {key for key in v27 if key != "prompts"}
    for key in untouched:
        assert v28[key] == v27[key], f"config-v28 изменил раздел {key}"
    assert set(v28) == set(v27), "config-v28 изменил набор разделов"
    for role, pin in v27["prompts"].items():
        if role == "curator":
            continue
        assert v28["prompts"][role] == pin, f"config-v28 изменил пин {role}"
    assert v28["prompts"]["curator"]["path"] == "prompts/curator/curator-v16.md"
    assert v28["prompts"]["curator"]["version"] == "curator-v16"
    assert v28["prompts"]["curator"]["sha256"] == CURATOR_V16_SHA256
    # на диске пин совпадает с байтами файла (content pinning, ADR-0019)
    disk = CURATOR_V16.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == v28["prompts"]["curator"]["sha256"]


@pytest.mark.unit
def test_config_v27_and_previous_payloads_stay_frozen_on_disk() -> None:
    v27 = json.loads(V27_PAYLOAD.read_text(encoding="utf-8"))
    assert v27["prompts"]["curator"]["version"] == "curator-v15"
    assert v27["prompts"]["curator"]["sha256"] == CURATOR_V15_SHA256
    disk = CURATOR_V15.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == CURATOR_V15_SHA256
    v26 = json.loads((REPO_ROOT / "docs" / "eval" / "config-v26-payload.json").read_text(encoding="utf-8"))
    assert canonical_sha256(v26) == "3d93df81d40012dc640689982983fe1c4018982b317833f94acce89a1db56d6f"


@pytest.mark.unit
def test_pinned_prompts_of_config_v28_resolve_and_tamper_fails_closed() -> None:
    payload = json.loads(V28_PAYLOAD.read_text(encoding="utf-8"))
    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.CURATOR].version == "curator-v16"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V16_SHA256
    assert "claim_period_not_near_value" in loaded[Role.CURATOR].text
    tampered = json.loads(json.dumps(payload))
    tampered["prompts"]["curator"] = dict(tampered["prompts"]["curator"], sha256="0" * 64)
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered["prompts"], REPO_ROOT)


@pytest.mark.unit
def test_dev_stand_scripts_do_not_activate_config_v28() -> None:
    """Активация config-v28 — решение менеджера: скрипты dev-стенда не ссылаются на v28,
    дефолт bootstrap'а и reset-db.sh — прежний (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v28" not in text, f"{name} уже ссылается на config-v28"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
