"""T7.113 (часть 2, ADR-0034 §10): гейт «значение на связи» для диапазонов утверждения.

Значение claim'а — диапазон (`claim_match_values`, составной токен `2.1-2.2`) — подтверждает
supports-связь только целиком:
(1) диапазон как одно значение на тексте улики (те же формы и эквивалентность T7.109/T7.109a:
    «2,1–2,2%», «2.1-2.2%», «от 2,1 до 2,2%» — одно и то же `2.1-2.2`); либо
(2) оба конца отдельными числами (таблица «мин./макс.»: ячейки «2,1» и «2,2»).
Одна граница диапазона связью не подтверждается — стендовая связка 4e7ba8c2 («2,1–2,2% по данным
Росстата») ↔ finmarket с одним «2,2%» проходила гейт эпохи T7.108a и подтверждала половину
утверждения: теперь связь снимается с прежней причиной. Одиночные значения claim'а гейт читает
по-прежнему (T7.108a), их поведение не изменено ни на пункт.

Тесты не обращаются к LLM, сети и стенду; finmarket — локальная фикстура T7.112.
"""

from __future__ import annotations

from pathlib import Path

from apps.orchestrator.curator_rejections import links_without_claim_value, rejected_link_entries
from apps.research_proxy.normalization import normalize_content
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

_CLAIM_22 = (
    "Официальный уровень безработицы в России в августе 2026 года составил 2,2% по данным Росстата."
)
_CLAIM_RANGE = (
    "Официальный уровень безработицы в России в сентябре 2026 года составил 2,1–2,2% "
    "по данным Росстата."
)


def _proposal(statement: str) -> CuratorProposal:
    return CuratorProposal(
        summary="итог",
        claims=[
            ClaimProposal(
                statement=statement,
                claim_type="temporal_fact",
                as_of="2026-09-30T00:00:00+00:00",
                scope={"metric": "безработица", "region": "Россия"},
            )
        ],
        evidence_links=[EvidenceLink(evidence_index=0, claim_index=0, relation="supports")],
    )


def _check(statement: str, basis: str | None, page: str | None) -> tuple[dict[int, str], dict[int, str]]:
    proposal = _proposal(statement)
    return links_without_claim_value(proposal=proposal, dropped={}, link_texts={0: (basis, page)})


# ─── т5 (главный случай): одна граница диапазона на странице — связь снимается ─────────


def test_single_range_end_is_not_a_support_anymore() -> None:
    removed, claim_drops = _check(
        _CLAIM_RANGE,
        "По последним данным Росстата, уровень безработицы сохраняется на отметках - 2,2%.",
        "Страница с обзором рынка труда: 2,2% по методологии МОТ, рекордный минимум.",
    )
    # прежняя форма причины не изменена (T7.108)
    assert removed == {0: "evidence_link[0]: evidence 0 does not contain the value of claim 0"}
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}
    entries = rejected_link_entries(proposal=_proposal(_CLAIM_RANGE), removed_links=removed)
    assert entries and entries[0]["reason"] == removed[0]


def test_finmarket_page_alone_does_not_confirm_the_range_claim() -> None:
    # ровно стендовая связка 4e7ba8c2 ↔ finmarket.ru/news/6727171
    raw = (
        Path(__file__).resolve().parents[2] / "tests/fixtures/t7112/finmarket-6727171.raw.html"
    ).read_bytes()
    page = normalize_content("text/html", raw).text
    removed, claim_drops = _check(_CLAIM_RANGE, "уровень безработицы - 2,2% по методологии", page)
    assert removed == {0: "evidence_link[0]: evidence 0 does not contain the value of claim 0"}
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}


# ─── диапазон подтверждён целиком: форма (1) — как одно значение ───────────────────────


def test_whole_range_on_page_keeps_the_link() -> None:
    removed, claim_drops = _check(_CLAIM_RANGE, "обзор рынка труда", "на отметке 2,1–2,2% за сентябрь")
    assert removed == {} and claim_drops == {}


def test_ascii_range_forms_are_equivalent() -> None:
    # та же эквивалентность T7.109/T7.109a: дефис/тире и десятичный разделитель не значимы
    removed, claim_drops = _check(_CLAIM_RANGE, None, "Sensitivity band: 2.1-2.2% of labor force")
    assert removed == {} and claim_drops == {}
    removed, claim_drops = _check(_CLAIM_RANGE, "вилка по сентябрю: от 2,1 до 2,2%", None)
    assert removed == {} and claim_drops == {}


# ─── диапазон подтверждён целиком: форма (2) — оба конца отдельными числами ────────────


def test_table_cells_of_both_ends_keep_the_link() -> None:
    # таблица «мин./макс.» без единицы у концов (единица со стороны текста не требуется)
    page = "Показатель Мин Макс\nБезработица, % 2,1 2,2\nИнфляция 6,1 6,4\n"
    removed, claim_drops = _check(_CLAIM_RANGE, None, page)
    assert removed == {} and claim_drops == {}


def test_ends_split_between_fragment_and_page_keep_the_link() -> None:
    # оба конца есть на текстах улики в целом (фрагмент ИЛИ страница) — связь жива
    removed, claim_drops = _check(
        _CLAIM_RANGE, "нижняя граница вилки: 2,1% по методологии", "верхняя граница ряда: 2,2%"
    )
    assert removed == {} and claim_drops == {}


# ─── одиночные значения: поведение T7.108a не изменено ни на пункт ─────────────────────


def test_single_value_claim_is_checked_as_before() -> None:
    removed, claim_drops = _check(_CLAIM_22, "уровень безработицы - 2,2% по методологии", None)
    assert removed == {} and claim_drops == {}
    removed, claim_drops = _check(_CLAIM_22, "обзор рынка труда без числа", "страница без значения")
    assert removed == {0: "evidence_link[0]: evidence 0 does not contain the value of claim 0"}
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}
