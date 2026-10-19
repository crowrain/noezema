"""T7.124 (часть 3, т7): таблица исходов гейта значения по стендовой фикстуре страниц.

Фикстура `tests/fixtures/t7121a/stand_claims_with_pages.jsonl` — 53 утверждений стенда и
сохранённые нормализованные тексты страниц, на которые указывают их связи (аудит-фикстура
T7.121a). Проба менеджера (T7.121a, ADR-0038 §11) назвала 6 связей, которые не несут значения
своих утверждений. Тест закрепляет, что ЧИСТЫЙ гейт значения — тот же, что у записи новой
связи (T7.108, профили T7.113 и T7.123), — называет ровно эти шесть связей и ничего больше,
и что каждая из шести объясняется фактом страницы, а не случайностью.

Это не «починка» гейта: три случая из шести — ограничение проверки (округление значения,
индексная форма числа, диапазон, подтверждаемый только целиком), и они названы, а не исправлены
(правка порогов/окон — стоп-критерий T7.124). Связи, которые гейт называет ложными, на этом
этапе только называются: снятие уже записанной связи требует отдельного решения (ADR-0038 §13).

Гейт вызывается ровно так, как его вызывает аудит уже записанных связей: тексты связи =
(None, нормализованный текст страницы) — целая страница, потому что фрагмент улики есть окно
этого же текста (§10 ADR-0034).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.curator_rejections import (
    VALUE_GATE_ABSENT,
    VALUE_GATE_PRESENT,
    VALUE_GATE_UNCHECKED,
    link_value_gate,
    links_without_claim_value,
    statement_value_profile,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent.parent / "fixtures" / "t7121a" / "stand_claims_with_pages.jsonl"

#: стенд 64837cb5 / `83e7a1c8` — форма утверждения с «голым» десятичным значением
STATEMENT_83 = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 9,6."

#: шесть связей, названных пробой менеджера (утверждение → страница)
EXPECTED_ABSENT: frozenset[tuple[str, str]] = frozenset(
    {
        ("9266248e", "https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/"),
        ("e189c80d", "https://www.garant.ru/hotlaw/federal/1963171/"),
        ("7cfbe942", "https://rosstat.gov.ru/storage/mediabank/Rab_sila_2026.htm"),
        ("7cfbe942", "https://www.rosstat.gov.ru/opendata/7708234640-unemploymentrate2"),
        ("7cfbe942", "https://rosstat.gov.ru/labour_force"),
        ("4e7ba8c2", "http://www.finmarket.ru/news/6727171"),
    }
)

#: утверждения без измеренного числового профиля: гейт значения их не проверяет (T7.108)
EXPECTED_UNCHECKED: frozenset[str] = frozenset({"d8155167", "0780d73c"})


def _records() -> list[dict[str, Any]]:
    """Записи фикстуры читаются `.split` по переводу строки, НЕ `splitlines()`: внутри
    normalized_text стоит сырой U+2028 (ловушка T7.121a)."""

    raw = FIXTURE.read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.split("\n") if line.strip()]


def _verdicts() -> dict[tuple[str, str], tuple[str, tuple[str, ...]]]:
    """Таблица «утверждение → страница»: исход гейта и профиль значения утверждения.

    Страницы утверждения дедуплицируются по uri: связь определяется страницей, а повтор одной
    и той же страницы в фикстуре — повтор строки источника, не новая проверка."""

    table: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for record in _records():
        claim_id = str(record["claim_id"])[:8]
        statement = str(record["statement"])
        profile = statement_value_profile(statement)
        for page in record["pages"]:
            uri = str(page["uri"])
            key = (claim_id, uri)
            if key in table:
                continue
            verdict = link_value_gate(statement=statement, texts=(None, str(page["normalized_text"])))
            table[key] = (verdict, profile)
    return table


def _page(claim_prefix: str, uri: str) -> str:
    for record in _records():
        if str(record["claim_id"])[:8] != claim_prefix:
            continue
        for page in record["pages"]:
            if str(page["uri"]) == uri:
                return str(page["normalized_text"])
    raise AssertionError(f"страницы {uri} нет у утверждения {claim_prefix}")


# ─── т7а: гейт называет ровно шесть связей пробы и ничего больше ──────────────────


def test_the_value_gate_names_exactly_the_six_links_of_the_manager_probe() -> None:
    table = _verdicts()

    absent = {key for key, (verdict, _) in table.items() if verdict == VALUE_GATE_ABSENT}
    assert absent == EXPECTED_ABSENT, sorted(absent - EXPECTED_ABSENT) or sorted(EXPECTED_ABSENT - absent)

    unchecked = {key[0] for key, (verdict, _) in table.items() if verdict == VALUE_GATE_UNCHECKED}
    assert unchecked == EXPECTED_UNCHECKED, unchecked

    # профиль значения назван у каждого проверенного утверждения: «нет значения» проверяется
    # по конкретным токенам, а не «на глаз»
    checked = {key: profile for key, (verdict, profile) in table.items() if verdict != VALUE_GATE_UNCHECKED}
    assert all(checked.values()), {k: v for k, v in checked.items() if not v}
    present = {key for key, (verdict, _) in table.items() if verdict == VALUE_GATE_PRESENT}
    assert len(absent) == 6 and len(present) == len(table) - 8, (len(absent), len(present))
    assert len(checked) == len(table) - 2, (len(checked), len(table))

    # профили шести названных связей — те же числа, что в формулировках утверждений
    profiles = {key: profile for key, (_, profile) in table.items() if key in EXPECTED_ABSENT}
    assert profiles[("9266248e", "https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/")] == ("5.59",)
    assert profiles[("e189c80d", "https://www.garant.ru/hotlaw/federal/1963171/")] == ("5.59",)
    assert profiles[("4e7ba8c2", "http://www.finmarket.ru/news/6727171")] == ("2.1-2.2",)
    for claim_id, uri in EXPECTED_ABSENT:
        if claim_id == "7cfbe942":
            assert profiles[(claim_id, uri)] == ("2.2",), (claim_id, uri)


# ─── т7б: каждая названная связь объясняется фактом страницы ───────────────────────


def test_three_of_the_six_are_false_links_and_three_are_named_limitations_of_the_check() -> None:
    """Ложные связи (значения утверждения на странице нет нигде) и ограничения проверки
    (значение на странице есть, но в другой форме) разделены и названы. Ограничения НЕ
    исправляются: правка окон и порогов — стоп-критерий T7.124."""

    # (1) rosstat ×3 — ложные связи: ни одного standalone-значения «2,2» на страницах нет
    standalone = re.compile(r"(?<![\d.,])2,2(?![\d,])")
    for uri in (
        "https://rosstat.gov.ru/storage/mediabank/Rab_sila_2026.htm",
        "https://www.rosstat.gov.ru/opendata/7708234640-unemploymentrate2",
        "https://rosstat.gov.ru/labour_force",
    ):
        page = _page("7cfbe942", uri)
        assert standalone.search(page) is None, uri
        # ни одной страницы выпуска с числами здесь нет: остаются годы выпусков, номера
        # разделов и размеры файлов, а «2.2» встречается только приклеенным к чужим числам
        # (дата «31.12.2030», размер «52.22 Кб») — целый токен гейт не берёт (T7.108)
    # контраст той же связки: interfax называет 2,2 несколько раз — эта связь проходит гейт
    assert standalone.search(_page("7cfbe942", "https://www.interfax.ru/business/1119461")) is not None
    assert re.search(r"2[.,]2", _page("7cfbe942", "https://rosstat.gov.ru/storage/mediabank/Rab_sila_2026.htm")) is None
    assert "31.12.2030" in _page("7cfbe942", "https://www.rosstat.gov.ru/opendata/7708234640-unemploymentrate2")
    assert "52.22 Кб" in _page("7cfbe942", "https://rosstat.gov.ru/labour_force")

    # (2) nbj.ru — ограничение: страница называет округлённое значение (5,6%), а не 5,59%
    nbj = _page("9266248e", "https://nbj.ru/publs/obshchaya_inflyatsiya_po_itogam_2025_goda_/71790/")
    assert standalone_559(nbj) is None
    assert "9,5% до 5,6%" in nbj, nbj[:200]

    # (3) garant.ru — ограничение: 5,59 стоит только приклеенным к индексной форме (105,59%)
    garant = _page("e189c80d", "https://www.garant.ru/hotlaw/federal/1963171/")
    assert standalone_559(garant) is None
    assert "105,59%" in garant
    # отдельный дефект этой страницы: нормализованный текст сохранён с replacement-символами
    # (распознавание кодировки), и он здесь не лечится — числа читаются, русские слова нет
    assert "\ufffd" in garant

    # (4) finmarket.ru — ограничение T7.113: диапазон 2,1–2,2 подтверждается только целиком,
    # страница называет 2,2 и не называет 2,1
    finmarket = _page("4e7ba8c2", "http://www.finmarket.ru/news/6727171")
    assert re.search(r"(?<![\d.,])2,2(?![\d,])", finmarket) is not None
    assert re.search(r"(?<![\d.,])2,1(?![\d,])", finmarket) is None

    # контраст: у того же утверждения 9266248e есть страницы, где значение названо целым токеном
    interfax = _page("9266248e", "https://www.interfax.ru/business/1068021")
    assert standalone_559(interfax) is not None


def standalone_559(text: str) -> re.Match[str] | None:
    """Целой токен «5,59» (не часть «105,59» и не часть «5,590»)."""

    return re.search(r"(?<![\d.,])5,59(?![\d,])", text)


# ─── т7в: аудит пользуется тем же гейтом, что и запись новой связи ─────────────────


def test_pure_gate_and_commit_gate_agree_link_by_link() -> None:
    """Паритет: чистая функция `link_value_gate`, из которой аудит строит исходы записанных
    связей, даёт ровно те же решения, что рабочий гейт записи `links_without_claim_value`
    (извлечение чистой функции не изменило его поведение)."""

    sbercib = Path(__file__).parent.parent / "fixtures" / "t7123" / "sbercib-key-rate.normalized.txt"
    hr = Path(__file__).parent.parent / "fixtures" / "t7123" / "hr-ratings-hh-index.normalized.txt"
    texts: dict[int, tuple[str | None, str | None]] = {
        0: (None, sbercib.read_text(encoding="utf-8")),
        1: (None, hr.read_text(encoding="utf-8")),
        2: (None, sbercib.read_text(encoding="utf-8")),
    }
    links = [
        EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
        EvidenceLink(evidence_index=1, claim_index=0, relation="supports"),
        EvidenceLink(evidence_index=0, claim_index=0, relation="counters"),
    ]
    proposal = CuratorProposal(
        summary="итог",
        claims=[
            ClaimProposal(
                statement=STATEMENT_83,
                claim_type="external_fact",
                scope={"metric": "hh-индекс", "region": "Россия"},
            )
        ],
        evidence_links=links[:2],
    )

    own = {
        index: link_value_gate(statement=STATEMENT_83, texts=texts[index])
        for index in (0, 1)
    }
    assert own == {0: VALUE_GATE_ABSENT, 1: VALUE_GATE_PRESENT}, own

    removed, claim_drops = links_without_claim_value(
        proposal=proposal, dropped={}, link_texts={index: texts[index] for index in (0, 1)}
    )
    assert set(removed) == {index for index, verdict in own.items() if verdict == VALUE_GATE_ABSENT}
    assert set(removed) == {0}
    # связь снята, но у операции осталась честная связь — операция не снимается
    assert claim_drops == {}, claim_drops
    assert removed[0] == (
        "evidence_link[0]: evidence 0 does not contain the value of claim 0"
    ), removed

    # counters вне юрисдикции гейта: та же страница без значения остаётся
    counters = CuratorProposal(
        summary="итог",
        claims=proposal.claims,
        evidence_links=[EvidenceLink(evidence_index=0, claim_index=0, relation="counters")],
    )
    removed_counters, _drops = links_without_claim_value(
        proposal=counters, dropped={}, link_texts={0: texts[0]}
    )
    assert removed_counters == {}, removed_counters
