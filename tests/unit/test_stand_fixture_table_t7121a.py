"""Unit (T7.121 часть 5 / T7.121a часть т6): полная таблица стенда-фикстуры «было → стало».

53 утверждения стендовой пробы менеджера (probe d9d5fd5) с их побайтовыми текстами
связанных страниц — зафиксированы здесь (`tests/fixtures/t7121a/stand_claims_with_pages.jsonl`,
тот же приём, что у нормализованных страниц T7.121). Таблица закрепляет результат правок
д1–д4 (ADR-0038 §8): 5 формулировок сняты с отказов законно, отказы периода над
e5a64868 и 6d9a12ff остаются с обоснованием, 17 формулировок без названной даты прогноза
отклоняются и раньше, и теперь; коллатеральных регрессий нет (ни одна прежняя «ok»
строка не стала отказом).

Счётчик пробы: было 24 отказа (19 — дата прогноза, 5 — период), стало 19 (17 + 2).
Расхождение с цифрами постановки T7.121a (21/4) отмечено в отчёте: авторитетны файлы
пробы менеджера (`manager-probe-d9d5fd5.txt`) и повторенный прогон на d9d5fd5.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from apps.orchestrator import curator_period_gate as gate
from packages.domain.schemas.staging import ClaimProposal, ClaimType

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/t7121a/stand_claims_with_pages.jsonl"
FIXTURE_SHA256 = "a9cf6116120f91e071390e917f1089c094a7505043048bb52a76095c1391b5d5"

BYLO_D9D5FD5: dict[str, str] = {  # было: прогон гейта на коммите d9d5fd5 (до правок д1–д4)
    "d8155167": "ok",
    "9266248e": "период «21 января 2026»",
    "c970bc08": "ok",
    "7cc2b578": "нет даты прогноза",
    "73d52835": "ok",
    "e2c52955": "ok",
    "6d9a12ff": "период «декабре 2025»",
    "2d010d53": "нет даты прогноза",
    "391c4388": "ok",
    "e189c80d": "ok",
    "58e2d5d4": "нет даты прогноза",
    "2f456e85": "ok",
    "c6729d4c": "нет даты прогноза",
    "cc75e97f": "ok",
    "7c8c9afe": "период «сентябре 2026»",
    "44bd1460": "нет даты прогноза",
    "07126d06": "нет даты прогноза",
    "7d810f8c": "нет даты прогноза",
    "e61b4a58": "нет даты прогноза",
    "136a0ca0": "период «июле 2026»",
    "0780d73c": "ok",
    "fc8720df": "нет даты прогноза",
    "7cfbe942": "ok",
    "cded6b54": "ok",
    "e31b6d55": "ok",
    "def34bd8": "ok",
    "dad11e2f": "ok",
    "1614865d": "ok",
    "21eaef05": "нет даты прогноза",
    "060d979a": "ok",
    "acd7081a": "ok",
    "9eb38c86": "ok",
    "822072db": "нет даты прогноза",
    "344d0359": "ok",
    "5b034571": "ok",
    "a5560f1b": "ok",
    "c80b682d": "ok",
    "4e7ba8c2": "ok",
    "0a9e220d": "нет даты прогноза",
    "471bd83d": "нет даты прогноза",
    "44ca2c79": "нет даты прогноза",
    "a3d430d8": "нет даты прогноза",
    "85220305": "нет даты прогноза",
    "cd461250": "ok",
    "c31e75a6": "ok",
    "316fb4bf": "нет даты прогноза",
    "5a75ebf6": "ok",
    "5538afc2": "ok",
    "e5a64868": "период «октябре 2026»",
    "83590565": "нет даты прогноза",
    "c263e93c": "нет даты прогноза",
    "8499b8c5": "ok",
    "83e7a1c8": "ok",
}
STALO_T7121A: dict[str, str] = {  # стало: прогон гейта после правок д1–д4 (этот файл)
    "d8155167": "ok",
    "9266248e": "ok",
    "c970bc08": "ok",
    "7cc2b578": "ok",
    "73d52835": "ok",
    "e2c52955": "ok",
    "6d9a12ff": "период «декабре 2025»",
    "2d010d53": "нет даты прогноза",
    "391c4388": "ok",
    "e189c80d": "ok",
    "58e2d5d4": "нет даты прогноза",
    "2f456e85": "ok",
    "c6729d4c": "нет даты прогноза",
    "cc75e97f": "ok",
    "7c8c9afe": "ok",
    "44bd1460": "нет даты прогноза",
    "07126d06": "нет даты прогноза",
    "7d810f8c": "нет даты прогноза",
    "e61b4a58": "нет даты прогноза",
    "136a0ca0": "ok",
    "0780d73c": "ok",
    "fc8720df": "ok",
    "7cfbe942": "ok",
    "cded6b54": "ok",
    "e31b6d55": "ok",
    "def34bd8": "ok",
    "dad11e2f": "ok",
    "1614865d": "ok",
    "21eaef05": "нет даты прогноза",
    "060d979a": "ok",
    "acd7081a": "ok",
    "9eb38c86": "ok",
    "822072db": "нет даты прогноза",
    "344d0359": "ok",
    "5b034571": "ok",
    "a5560f1b": "ok",
    "c80b682d": "ok",
    "4e7ba8c2": "ok",
    "0a9e220d": "нет даты прогноза",
    "471bd83d": "нет даты прогноза",
    "44ca2c79": "нет даты прогноза",
    "a3d430d8": "нет даты прогноза",
    "85220305": "нет даты прогноза",
    "cd461250": "ok",
    "c31e75a6": "ok",
    "316fb4bf": "нет даты прогноза",
    "5a75ebf6": "ok",
    "5538afc2": "ok",
    "e5a64868": "период «октябре 2026»",
    "83590565": "нет даты прогноза",
    "c263e93c": "нет даты прогноза",
    "8499b8c5": "ok",
    "83e7a1c8": "ok",
}

def _rows() -> list[dict[str, object]]:
    assert (
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    ), "фикстура stand-пробы изменена — сверься с probe-файлами менеджера"
    # разделитель строк фикстуры — ровно символ перевода строки; splitlines() ложно резал
    # бы по U+2028, который встречается в текстах страниц внутри JSON-строк
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").split("\n") if line]


def _kind(row: dict[str, object]) -> str:
    claim = ClaimProposal(statement=str(row["statement"]), claim_type=ClaimType(str(row["claim_type"])))
    texts = [str(page["normalized_text"]) for page in row["pages"]]  # type: ignore[index]
    rejections = gate.claim_period_rejections(
        claims=[claim], texts_by_claim={0: texts}, already_dropped=frozenset()
    )
    reason = rejections.get(0, "")
    if not reason:
        return "ok"
    parts = []
    if "обязано называть дату" in reason:
        parts.append("нет даты прогноза")
    for match in re.finditer(r"период «([^»]+)»", reason):
        parts.append(f"период «{match.group(1)}»")
    for match in re.finditer(r"дата документа «([^»]+)»", reason):
        parts.append(f"дата документа «{match.group(1)}»")
    return "; ".join(parts)


@pytest.mark.unit
def test_stand_fixture_table_was_became_t7121a() -> None:
    """Таблица claim_id → исход прогона по всей фикстуре (т6). Закреплена результат-таблица;
    BYLO_D9D5FD5 — справочная (снимок прежнего поведения, полученный прогоном на d9d5fd5)."""
    rows = _rows()
    assert len(rows) == 53
    actual = {str(row["claim_id"])[:8]: _kind(row) for row in rows}
    assert actual == STALO_T7121A, {k: (STALO_T7121A[k], v) for k, v in actual.items() if v != STALO_T7121A[k]}


@pytest.mark.unit
def test_flips_and_remaining_rejections_t7121a() -> None:
    """Снятие отказов — ровно ожидаемые 5 (д1–д4), прежних «ok» строк не стало отказами;
    оставшиеся отказы периода — ровно два, оба обоснованы (дополнение ADR-0038 §11, д1/д2):

    - e5a64868: finmarket-6727171 — «октябре 2026» в формулировке, на странице месяц
      написан только датировками статей («14 октября 2026 года 11:46», «14 октября.»,
      «11:45, 14 октября 2026»); датировочная строка периодом не является (д1, т7);
    - 6d9a12ff: forbes — «декабре 2025»: ни одно название декабря на странице не лежит в
      окне значения (ближайшее «в начале декабря» заканчивается за 81 знак до окна), а
      подтверждение месяца рядом со значением — неизменное требование гейта периода;
      pairing-строка шапки «в декабре 2025 года» подтвердила бы только год (д2), что без
      подтверждённого месяца силы не имеет — отказ законен и прежний.
    """
    rows = _rows()
    actual = {str(row["claim_id"])[:8]: _kind(row) for row in rows}
    flipped = {cid for cid in BYLO_D9D5FD5 if BYLO_D9D5FD5[cid] != "ok" and actual.get(cid) == "ok"}
    assert flipped == {"9266248e", "7cc2b578", "7c8c9afe", "136a0ca0", "fc8720df"}
    regressions = {cid for cid in BYLO_D9D5FD5 if BYLO_D9D5FD5[cid] == "ok" and actual.get(cid, "ok") != "ok"}
    assert regressions == set()
    remaining_period = {
        cid: label for cid, label in actual.items() if label.startswith("период «")
    }
    assert remaining_period == {"e5a64868": "период «октябре 2026»", "6d9a12ff": "период «декабре 2025»"}
    no_date = {cid for cid, label in actual.items() if label == "нет даты прогноза"}
    assert len(no_date) == 17
    # c263e93c остаётся отказом по дате прогноза (не периодом): его «26 февраля 2026» —
    # датировка статьи YSIA, в самой формулировке даты прогноза нет
    assert STALO_T7121A["c263e93c"] == "нет даты прогноза"


@pytest.mark.unit
def test_dateless_forecasts_pass_with_named_forecast_date_t7121a() -> None:
    """о3/о6: те же утверждения с названной датой прогноза проходят гейты целиком —
    дата подтверждается страницей целиком (д3), окно значения ни при чём:

    - c263e93c + «(прогноз от 26 февраля 2026 года)» — страница YSIA несёт «17:00, 26
      февраля» (штамп без явного года) и год 2026 в заголовке;
    - c6729d4c + «(прогноз от 8 октября 2026 года)» — «8 октября 2026» названо на страницах.
    """
    variants = {
        "c263e93c": " (прогноз от 26 февраля 2026 года)",
        "c6729d4c": " (прогноз от 8 октября 2026 года)",
    }
    for row in _rows():
        cid = str(row["claim_id"])[:8]
        if cid not in variants:
            continue
        statement = str(row["statement"]) + variants[cid]
        claim = ClaimProposal(statement=statement, claim_type=ClaimType(str(row["claim_type"])))
        texts = [str(page["normalized_text"]) for page in row["pages"]]  # type: ignore[index]
        rejections = gate.claim_period_rejections(
            claims=[claim], texts_by_claim={0: texts}, already_dropped=frozenset()
        )
        assert rejections == {}, (cid, rejections)
