"""T7.108 (часть а, ADR-0034 §10): чистый гейт «значение на связи».

`apps/orchestrator/curator_rejections.py::links_without_claim_value` — разбор связей
предложения куратора на границе: связь `supports` с уликой `source_assertion` снимается,
если **ни фрагмент, ни весь сохранённый текст страницы** не содержат ни одного измеренного
значения утверждения отдельной текстовой формой (та же канонизация значений, что у детектора
атрибуции; границы формы — T7.108a, ADR-0034 §10). Связь называется записью отказа
(`rejected_link_entries`); если у операции сняты все её связи, операция снимается своей
причиной (вариант B, ADR-0034 §7). Движок правил и гейт дубликатов вызываются уже после
фильтрации — они видят отфильтрованные связи.

Причины (ровно те строки, что пишутся в журнал):
- `evidence_link[<k>]: evidence <e> does not contain the value of claim <i>`
- `claim[<i>]: no linked evidence contains the claim value`
"""

from __future__ import annotations

from apps.orchestrator.curator_rejections import (
    REJECTED_OPERATIONS_LIMIT,
    links_without_claim_value,
    rejected_link_entries,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

_CLAIM = (
    "Официальный уровень безработицы в России в августе 2026 года составил 2,2% "
    "по данным Росстата."
)
_CLAIM_NO_VALUE = "Сервис «Гелиос» зафиксировал снижение числа вакансий после июня 2031 года."


def _proposal(
    claims: list[ClaimProposal], links: list[EvidenceLink]
) -> CuratorProposal:
    return CuratorProposal(summary="итог", claims=claims, evidence_links=links)


def _claim(statement: str = _CLAIM) -> ClaimProposal:
    return ClaimProposal(
        statement=statement,
        claim_type="temporal_fact",
        as_of="2026-09-30T00:00:00+00:00",
        scope={"metric": "безработица", "region": "Россия"},
    )


# ── связь снимается, когда значения нет ни во фрагменте, ни на странице ──────


def test_support_link_without_value_anywhere_is_removed() -> None:
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={
            0: (
                "Раздел «Труд и занятость». Оперативные данные в открытом реестре.",
                "Выгрузка доступна в форматах CSV и XLSX. Последнее наблюдение: август 2026 года.",
            )
        },
    )
    assert removed == {0: "evidence_link[0]: evidence 0 does not contain the value of claim 0"}
    # все связи операции сняты — операция снимается своей причиной
    assert claim_drops == {0: "claim[0]: no linked evidence contains the claim value"}


def test_value_in_fragment_keeps_the_link() -> None:
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: ("снизился до 2,2% с 2,3% в июле", "полный текст той же страницы")},
    )
    assert removed == {} and claim_drops == {}


def test_value_only_in_full_page_keeps_the_link() -> None:
    # окно фрагмента могло не дотянуться до значения (T7.82) — страница проверяется целиком
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: ("обзор рынка труда региона", "Приложение. Столбец B: 2.2.")},
    )
    assert removed == {} and claim_drops == {}


def test_table_row_page_keeps_the_link() -> None:
    # T7.108a: табличная строка первоисточника — соседние ячейки через пробел и перевод
    # строки; прежде жадный maximal-токен склеивал «2026 2,3 2,2» в одно число, значение
    # не находилось и связь с первичной страницей-таблицой снималась
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: ("Таблица 3. Уровень безработицы по месяцам", "Год Июль Август\n2026 2,3 2,2\n")},
    )
    assert removed == {} and claim_drops == {}


# ── что не проверяется ───────────────────────────────────────────────────────


def test_counters_link_is_never_removed_by_this_gate() -> None:
    proposal = _proposal(
        [_claim()],
        [
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=0, relation="counters"),
        ],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={
            0: ("фрагмент без числа", "страница без значения"),
            1: ("опровержение без значения", "полная страница опровержения"),
        },
    )
    # supports снят, counters жив: операция остаётся (оценка спорится, но не снимается)
    assert removed == {0: "evidence_link[0]: evidence 0 does not contain the value of claim 0"}
    assert claim_drops == {}


def test_link_to_non_source_assertion_evidence_is_not_checked() -> None:
    # link_texts None — улика не `source_assertion` (например `local_observation`): гейт молчит
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal, dropped={}, link_texts={0: None}
    )
    assert removed == {} and claim_drops == {}


def test_claim_without_measured_value_is_not_checked() -> None:
    # утверждение без измеренного значения (нет числа с единицей) — гейт не применяется
    links = [EvidenceLink(evidence_index=0, claim_index=0, relation="supports")]
    proposal = _proposal([_claim(_CLAIM_NO_VALUE)], links)
    removed, claim_drops = links_without_claim_value(
        proposal=proposal, dropped={}, link_texts={0: ("текст без цифр", "страница без цифр")}
    )
    assert removed == {} and claim_drops == {}


def test_links_of_already_dropped_operations_are_untouched() -> None:
    # снятую операцию описывает запись операции (T7.105); связи отдельной записью не удваиваются
    proposal = _proposal(
        [_claim(), _claim("Уровень безработицы в мае 2026 года составил 2,1%.")],
        [
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=1, relation="supports"),
        ],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={0: "claim[0]: temporal_fact requires as_of"},
        link_texts={0: ("нет числа", "нет числа"), 1: ("снизился до 2,1%", "тот же текст")},
    )
    assert removed == {} and claim_drops == {}


# ── операция снимается только когда сняты ВСЕ её связи ───────────────────────


def test_partial_removal_keeps_the_operation() -> None:
    proposal = _proposal(
        [_claim()],
        [
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=0, relation="supports"),
        ],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={0: ("нет значения", "страница без значения"), 1: ("до 2,2%", "тот же текст")},
    )
    assert list(removed) == [0]
    assert claim_drops == {}


def test_all_supports_removed_and_counters_survive_keeps_operation() -> None:
    proposal = _proposal(
        [_claim()],
        [
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=2, claim_index=0, relation="counters"),
        ],
    )
    removed, claim_drops = links_without_claim_value(
        proposal=proposal,
        dropped={},
        link_texts={
            0: ("нет значения", "нет значения"),
            1: ("нет значения", "нет значения"),
            2: ("опровержение", "опровержение"),
        },
    )
    assert removed == {
        0: "evidence_link[0]: evidence 0 does not contain the value of claim 0",
        1: "evidence_link[1]: evidence 1 does not contain the value of claim 0",
    }
    assert claim_drops == {}


def test_out_of_range_claim_index_is_skipped_not_keyerror() -> None:
    # структурный гейт снимает такие предложения раньше; функция обязана не падать
    proposal = _proposal([_claim()], [EvidenceLink(evidence_index=0, claim_index=5, relation="supports")])
    removed, claim_drops = links_without_claim_value(
        proposal=proposal, dropped={}, link_texts={0: ("нет значения", "нет значения")}
    )
    assert removed == {} and claim_drops == {}


# ── форма записей снятых связей ───────────────────────────────────────────────


def test_rejected_link_entries_shape_and_order() -> None:
    proposal = _proposal(
        [_claim(), _claim("Уровень безработицы в мае 2026 года составил 2,1%.")],
        [
            EvidenceLink(evidence_index=0, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=1, claim_index=0, relation="supports"),
            EvidenceLink(evidence_index=2, claim_index=1, relation="supports"),
        ],
    )
    entries = rejected_link_entries(
        proposal=proposal,
        removed_links={
            0: "evidence_link[0]: evidence 0 does not contain the value of claim 0",
            2: "evidence_link[2]: evidence 2 does not contain the value of claim 1",
        },
    )
    assert entries == [
        {
            "link_index": 0,
            "evidence_index": 0,
            "claim_index": 0,
            "relation": "supports",
            "reason": "evidence_link[0]: evidence 0 does not contain the value of claim 0",
        },
        {
            "link_index": 2,
            "evidence_index": 2,
            "claim_index": 1,
            "relation": "supports",
            "reason": "evidence_link[2]: evidence 2 does not contain the value of claim 1",
        },
    ]


def test_rejected_link_entries_are_capped_like_operation_records() -> None:
    claims = [_claim(f"Значение {index}.") for index in range(12)]
    links = [
        EvidenceLink(evidence_index=index, claim_index=index, relation="supports") for index in range(12)
    ]
    entries = rejected_link_entries(
        proposal=_proposal(claims, links),
        removed_links={
            index: f"evidence_link[{index}]: evidence {index} does not contain the value of claim {index}"
            for index in range(12)
        },
    )
    assert len(entries) == REJECTED_OPERATIONS_LIMIT
    assert [entry["link_index"] for entry in entries] == list(range(REJECTED_OPERATIONS_LIMIT))
