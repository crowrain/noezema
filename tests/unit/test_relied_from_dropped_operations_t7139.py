"""T7.139 (часть 2, ADR-0038 §16): перенос опоры снятой перепроверки — чистая функция.

Стенд 008d93ac (run 256b16b-v30, вопрос c3): куратор предложил четыре перепроверки,
хост снял три (гейт даты прогноза, гейт периода). Записанные утверждения под
`existing_claim_id` остались в знании, но карточка потеряла и опору на них: ответ
перестал ссылаться на Альфа-Банк, ВТБ и старый прогноз Банка России. Функция
`relied_from_dropped_operations` переносит id снятых операций в опору ответа;
здесь — закрытые правила переноса на данных стендовых фикстур.

Красность на 256b16b: модуля `apps/orchestrator/relied_supplement.py` там нет вовсе.
"""

from __future__ import annotations

import uuid

from apps.orchestrator.orchestrator import RELIED_CLAIMS_LIMIT
from apps.orchestrator.relied_supplement import (
    claims_evidence_ids,
    relied_from_dropped_operations,
)

# ── id стенда 008d93ac (фикстуры tests/fixtures/t7139) ────────────────────────

ALFA = "07126d06-8bf2-4f8f-bdc4-71944ef1d905"  # Альфа-Банк, прогноз 7,5%
VTB_UNDATED = "c263e93c-2e02-4a37-80bd-6d5ce091362e"  # ВТБ 5–6%, без даты
CBR_JULY = "136a0ca0-f27b-4e5c-834e-064d62d558b6"  # Банк России, июль 2026, 6–7%
SBERCIB = "27806b5b-967b-434f-805d-54f5382eda2e"  # SberCIB, 6,8% (действующая операция)
GAZPROM = "471bd83d-1ea3-449c-bfd1-b4e9e413f95e"  # Газпромбанк, без даты
VTB_HI = "ab8234ae-3fb9-4565-a696-1ddcedda4318"  # ВТБ Мои Инвестиции, 7–7,5%

# снятые операции стенда 008d93ac: индексы 0, 1, 3 (операция 2 осталась действующей)
DROPPED_008 = [(0, ALFA), (1, VTB_UNDATED), (3, CBR_JULY)]
RELIED_008 = [GAZPROM, VTB_HI]  # свою опору куратор назвал ровно этими двумя id
PACK_008 = [uuid.UUID(cid) for cid in (ALFA, VTB_UNDATED, CBR_JULY, SBERCIB, GAZPROM, VTB_HI)]


def test_stand_008d93ac_transfers_three_recorded_claims() -> None:
    added, rejected = relied_from_dropped_operations(
        DROPPED_008, PACK_008, RELIED_008, {uuid.UUID(SBERCIB)}, RELIED_CLAIMS_LIMIT
    )
    assert added == [ALFA, VTB_UNDATED, CBR_JULY]
    assert rejected == []


def test_stand_09b513d2_transfers_all_three_including_unnamed_by_curator() -> None:
    """Стенд 09b513d2: куратор вообще не назвал опоры; сняты операции 0, 1, 3."""
    added, rejected = relied_from_dropped_operations(
        [(0, ALFA), (1, VTB_HI), (3, GAZPROM)], PACK_008, [], {uuid.UUID(SBERCIB)}, RELIED_CLAIMS_LIMIT
    )
    assert added == [ALFA, VTB_HI, GAZPROM]
    assert rejected == []


def test_invisible_and_pending_claim_ids_refuse_by_name() -> None:
    """Правило (а): разрешение — только по секции `claims_evidence` пака. id из
    `pending_claims`/`contradictions` (их нет в этом множестве) и просто невидимый id —
    именованный отказ, а не тихий пропуск и не догадка."""
    pending_only = str(uuid.uuid4())
    added, rejected = relied_from_dropped_operations(
        [(0, pending_only), (1, "00000000-0000-4000-8000-000000000000")],
        PACK_008,
        [],
        set(),
        RELIED_CLAIMS_LIMIT,
    )
    assert added == []
    assert rejected == [
        f"dropped_operation[0]: claim {pending_only} is not visible to the session",
        "dropped_operation[1]: claim 00000000-0000-4000-8000-000000000000 is not visible to the session",
    ]


def test_unparseable_reference_refuses_by_name() -> None:
    added, rejected = relied_from_dropped_operations([(2, "не id")], PACK_008, [], set(), RELIED_CLAIMS_LIMIT)
    assert added == []
    assert len(rejected) == 1 and rejected[0].startswith("dropped_operation[2]: ")


def test_unique_prefix_is_enough_full_pack_id_not_required() -> None:
    """Модель копипастит id из строки пака и обрывает их: уникальный hex-префикс
    разрешается тем же ресолвером, перенос идёт полным id."""
    added, rejected = relied_from_dropped_operations(
        [(0, "c263e93c")], PACK_008, [], set(), RELIED_CLAIMS_LIMIT
    )
    assert added == [VTB_UNDATED]
    assert rejected == []


def test_id_already_in_relied_is_silent_and_active_operation_is_not_duplicated() -> None:
    """Правило (б): уже в опоре — молча; id остался действующей операцией сессии —
    в опору не дублируется (опора и операция одной записью не размножаются)."""
    added, rejected = relied_from_dropped_operations(
        [(0, GAZPROM), (1, SBERCIB)], PACK_008, RELIED_008, {uuid.UUID(SBERCIB)}, RELIED_CLAIMS_LIMIT
    )
    assert added == []
    assert rejected == []


def test_shared_ceiling_refuses_by_name_with_operation_index() -> None:
    """Стенд 26b0b309 и правило (в): опора куратора уже на потолке RELIED_CLAIMS_LIMIT —
    перенос переполняет общий потолок и называется отказом по индексу операции."""
    relied_full = [
        "8499b8c5-47a7-4cce-a2d0-285dbcd523a3",
        "316fb4bf-030c-4f6b-9677-adb06dededd5",
        "341cc38f-7bf0-4f4a-86c3-6f8cf2e115d6",
        "def34bd8-1d50-450f-86dd-be0902a2d036",
        "738548ca-df21-48f7-a99c-b382a9149992",
        "f47c4435-9ffd-4ff8-aa37-ddfb8c44bba6",
        "3f2d9fb5-52c0-4588-944d-491c747a135c",
        "518d94db-046a-4533-a2da-0721eab2f454",
    ]
    pack = [*PACK_008, *(uuid.UUID(cid) for cid in relied_full)]
    added, rejected = relied_from_dropped_operations(
        [(0, ALFA), (2, CBR_JULY)], pack, relied_full, set(), RELIED_CLAIMS_LIMIT
    )
    assert added == []
    assert rejected == [
        f"dropped_operation[0]: more than {RELIED_CLAIMS_LIMIT} relied ids",
        f"dropped_operation[2]: more than {RELIED_CLAIMS_LIMIT} relied ids",
    ]


def test_ceiling_counts_already_carried_transfer_first_come_first_served() -> None:
    """Потолок считается по фактической опоре: свободен один слот — переносится первый
    по индексу, второй называется отказом."""
    relied_seven = [
        *RELIED_008,
        "8499b8c5-47a7-4cce-a2d0-285dbcd523a3",
        "316fb4bf-030c-4f6b-9677-adb06dededd5",
        "341cc38f-7bf0-4f4a-86c3-6f8cf2e115d6",
        "def34bd8-1d50-450f-86dd-be0902a2d036",
        "738548ca-df21-48f7-a99c-b382a9149992",
    ]
    pack = [*PACK_008, *(uuid.UUID(cid) for cid in relied_seven[2:])]
    added, rejected = relied_from_dropped_operations(
        [(0, ALFA), (1, VTB_UNDATED)], pack, relied_seven, set(), RELIED_CLAIMS_LIMIT
    )
    assert added == [ALFA]
    assert rejected == [f"dropped_operation[1]: more than {RELIED_CLAIMS_LIMIT} relied ids"]


def test_no_dropped_operations_yields_empty_lists() -> None:
    added, rejected = relied_from_dropped_operations([], PACK_008, RELIED_008, set(), RELIED_CLAIMS_LIMIT)
    assert added == []
    assert rejected == []


def test_claims_evidence_ids_reads_only_claim_prefixed_chunks_in_order() -> None:
    """Секция пака называется по префиксу `claim:`; порядок сохраняется, мусор и
    дублики выпадают — это множество видимости правила (а)."""
    assert claims_evidence_ids(
        [f"claim:{ALFA}", "c:c263e93c", f"claim:{CBR_JULY}", f"claim:{ALFA}", "страница:1"]
    ) == [uuid.UUID(ALFA), uuid.UUID(CBR_JULY)]
