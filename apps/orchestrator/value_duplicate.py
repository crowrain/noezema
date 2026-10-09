"""T7.83 + T7.83a (ADR-0033): чистый детектор «дубля по значению» в кураторском предложении.

Defect shape (stand .92, session f452a978, config-v19): the curator summary says it
reverified two claims already visible in the context pack, but both proposal ops
arrive without `existing_claim_id`. Byte-exact dedup (T7.9) cannot see a paraphrase,
so the commit writes a SECOND claim carrying the same value of the same indicator for
the same period — and the answer card shows «5,59%» twice with contradictory badges.

Host definition of a value duplicate (conservative, deterministic, no LLM):
a new op (no `existing_claim_id`) and a context-pack candidate are value duplicates
when all three hold:

* equal `claim_type`;
* equal NON-EMPTY set of significant numbers in the two statements — decimal tokens
  with digit boundaries (`\\d+(?:[.,]\\d+)+`, the same boundary semantics as
  assertion_window): «5,59» ≠ «5,6», and «105,59» is ONE token, never a hit on «5,59».
  Integers without a decimal separator are NOT values (years and dates stay out);
* compatible period: the new statement's years (plus its `as_of` year) form a NON-EMPTY
  set that is a SUBSET of the candidate's years (plus its `as_of` year) — the candidate
  may name extra years («опубликовано … 2026» belongs to the candidate, not to the period).

Fixture pair proof (SELECT from noezema-dev): e189c80d ↔ 9266248e is a duplicate;
391c4388 ↔ 6d9a12ff is NOT (values {14.5} vs {13.1, 15.6}).

T7.83a — the acceptance finding: those three conditions are NOT yet «the same fact».
«Инфляционные ожидания населения … составили 13,7%» and «Средняя ключевая ставка Банка
России … составила 13,7%» match type + values + period, but they are two DIFFERENT
INDICATORS: merging them attaches evidence about one indicator to a statement about
another and can RAISE its grade — a dishonest badge. Two host conditions are added on
top of the three above, both conservative (an extra duplicate on the card is cheaper
than a foreign piece of evidence under a claim):

* RELIED FILTER: the host merges only a candidate the curator itself declared as an
  support of this answer (`relied_claim_ids`, T7.82/ADR-0032). A candidate outside that
  list leaves the op exactly as proposed, with the reason recorded. The count of
  «exactly one candidate» is taken AFTER this filter; ≥2 relied candidates are still an
  ambiguity → kept. Rationale: `existing_claim_id` (T7.34) already lets the model merge
  any visible claim explicitly — the relied list is the same declaration written without
  an operation, so it is the only claim whose identity the host may reuse on its own.
* INDICATOR MATCH: if both sides declare a scope metric (`scope.metric` / `indicator` /
  `показатель` / `объект`) the normalized values must be equal; otherwise the two
  statements must share an indicator vocabulary — significant letters-only words, lower-
  cased, compared by a fixed-length stem prefix, with an overlap at least
  `INDICATOR_OVERLAP_MIN`. Additionally two statements that name DIFFERENT territories
  (from the closed `_TERRITORY_GROUPS` dictionary) are never the same indicator.

Decision policy (variant (i)/(ii) of T7.83): exactly ONE candidate matches strictly and
the op's evidence links are all `supports` → the host converts the op into a reverify of
that candidate (variant (i): the new supporting evidence joins the candidate's union and
can only keep or raise its grade — see ADR-0033; the anchor's as_of/scope are preserved
by packages/memory/reverify.py). No strict match, several matches, no reliance, an
indicator mismatch, or counter-evidence in the links → the op is left alone with an
honest reason recorded (variant (ii) territory: without new supporting evidence nothing
justifies touching the candidate, and a dispute must remain a dispute). Zero suspects →
silent: the payload stays byte-identical to pre-T7.83.

T7.85 adds a second, narrower detector in this module — `find_identical_statement_duplicates`:
an op whose statement is BYTE-identical to exactly one context-pack claim but whose `claim_type`
differs. Dedup T7.9 (it requires equal statement AND equal type) and the numeric gate (its first
condition is equal type) both cannot see it, so the stand wrote the same sentence twice with
different grades (2d010d53 external_fact / 58e2d5d4 temporal_fact). Byte-exactness is what makes
this rule safe without T7.83a's `relied_claim_ids` filter: identical text means identical
indicator, period and values by construction, so the host is not choosing between two different
formulations — it keeps the one claim the curator already stated.

The module decides; applying the conversion (staged copies + audit annotation) is the
host's job in apps/orchestrator/orchestrator.py — ops with an explicit `existing_claim_id`
are never passed here (T7.34 already owns them).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from packages.domain.models.base import JsonDict

#: decimal value token: maximal run of digits joined by ',' or '.', with digit
#: boundaries — mirrors apps/orchestrator/assertion_window.py::_numeric_boundary_ok
#: semantics (a value never starts or ends inside another number).
_VALUE_TOKEN_RE = re.compile(r"(?<![0-9])(\d+(?:[.,]\d+)+)(?![0-9])")

#: a period year: a standalone 4-digit token (1000–2099) outside any value token;
#: «год-одиночка» inside the text counts, digits inside a decimal value never do.
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)")

# ── T7.83a: словарь показателя (детерминированный, без LLM) ────────────────

#: основа слова: нижний регистр + ПРЕФИКС ФИКСИРОВАННОЙ ДЛИНЫ. Пять знаков —
#: минимальная длина, которая (а) переживает падеж и род русско/англоязычного
#: показателя («инфляция/инфляции/инфляцией», «ставка/ставки», «средняя/среднее» →
#: один стем) и (б) ещё достаточно длинна, чтобы не сводить разные показатели к
#: одному стему. Сравнение по началу слова, а не по окончанию, выбрано специально:
#: окончания в русском меняются сильнее, чем начала слов.
INDICATOR_STEM_LEN = 5

#: порог перекрытия словарей показателя (отношение пересечения к объединению).
#: подобран на РЕАЛЬНЫХ формулировках фикстуры .92 и на отрицательных примерах
#: приёмки; все замеры — точные рациональные числа, без float:
#:   слияние:  e189c80d↔9266248e = 3/5; перефраз наблюдаемой инфляции = 4/5;
#:             «средняя ключевая ставка Банка России» ↔ её перефраз = 1;
#:   отказ:    ожидания(13,7%)↔ключевая ставка(13,7%) = 0; безработица↔ВВП = 0;
#:             инфляция РФ↔Казахстан = 1/3 (+территориальный конфликт);
#:             «ставка по вкладам»↔«ключевая ставка» = 1/2; ожидания↔инфляция = 1/4.
#: 3/5 — наименьшее значение, которое отделяет реальную пару фикстуры (3/5) от
#: ближайшего ошибочного кандидата (1/2); спорные пары всегда решаются в сторону
#: «kept» (лишний дубль на карточке дешевле чужой улики у утверждения).
INDICATOR_OVERLAP_MIN = Fraction(3, 5)

#: служебные слова: СРАВНИВАЮТСЯ ТОЧНО (иначе предлог «в» убьёт слово «вклада»,
#: «на» — «население», «и» — «инфляция»). Короткие предлоги и связки.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "в", "во", "со", "с", "у", "к", "по", "за", "на", "из", "от", "до", "для",
        "при", "о", "об", "и", "или", "не", "как", "это", "этот", "эта", "эти", "их",
        "его", "её", "ее", "что", "все", "всех", "более", "менее", "около", "примерно",
        "же", "ли", "бы", "а", "no", "of", "the", "a", "in", "for", "and", "or", "to",
        "at", "by", "was", "is",
    }
)

#: служебные основы: сравниваются как ПРЕФИКСЫ по цельным корням служебных слов.
#: «составил*», «год*», «итог*», «данн*» и т. п. из задачи — плюс периодные и
#: атрибутивные маркеры: они описывают значение, период и источник, но не то,
#: КАКОЙ показатель измеряется. Кратчайшая основа — «год» (единственная трёхзначная):
#: иначе «годовая/году» остались бы показателем; она же снимает «город…» — название
#: города показателем не является. Предлоги и связки в этот список не входят: для них
#: есть `_STOP_WORDS`, где сравнение точное (префиксным «в» убило бы «вклада»).
#: «банк» и «росстат» сюда НЕ входят осознанно:
#: «ключевая ставка Банка России» — часть имени показателя.
_STOP_STEMS: frozenset[str] = frozenset(
    {
        # связка «значение = N» и её синонимы
        "составил", "составля", "составит", "достиг", "превысил", "увелич", "снизил",
        "вырос", "упал", "изменил", "показал", "оценка", "оценк",
        # период и его маркеры (годовая/годовые/году — один показатель периода)
        "год", "месяц", "квартал", "период", "итог", "данн", "датир", "число", "дата",
        # отсылка к источнику как таковая
        "опубликов", "сообща", "сообщил", "источник", "согласно", "показател", "значени",
    }
)

#: названия месяцев (в любой форме: декабрь/декабря/декабрю/декабре) — маркеры
#: периода, а не показателя.
_MONTH_PREFIXES: tuple[str, ...] = (
    "январ", "феврал", "март", "апрел", "мая", "мае", "июн", "июл", "август",
    "сентябр", "октябр", "ноев", "декабр",
)

#: ЗАКРЫТЫЙ словарь территорий. Показатель привязан к территории: «инфляция в
#: России» и «инфляция в Казахстане» — разные показатели, даже если все прочие
#: слова совпали (перекрытие 1/3 … 3/5). Правило одностороннее и потому безопасное:
#: конфликт засчитывается ТОЛЬКО когда обе формулировки называют территорию из
#: этого списка и множества территорий не пересекаются; словарь неполный, и это
#: не создаёт ложных склеек (неполнота решает «не увидел конфликта»).
_TERRITORY_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"росси", "россий"}),
    frozenset({"сша", "амери"}),
    frozenset({"европ", "евроз"}),
    frozenset({"миров", "глоба"}),
    frozenset({"белару", "белорус"}),
    frozenset({"казах"}),
    frozenset({"украи"}),
    frozenset({"кита"}),
    frozenset({"турц"}),
    frozenset({"герман"}),
    frozenset({"инди"}),
    frozenset({"япон"}),
)

#: ключи scope, которые хост читает как метку показателя. Реальное употребление:
#: `metric` (tests/scenario/test_commit_boundary.py — единственный свободный scope
#: с явным метрическим ключом), `объект` (`curator` пишет его в предложениях:
#: tests/scenario/test_relied_claims.py), плюс английские синонимы. На строке
#: `claims` колонки scope НЕТ вовсе (packages/domain/models/memory.py::ORMClaim):
#: метку показателя хост может взять только из assessed_scope текущей головы
#: (host-scope-v1 несёт as_of/date_anchor/source_domains, то есть для свежих
#: стендовых claims её нет — решает словарь формулировок) либо из legacy-строк,
#: где assessed_scope был свободным словарём модели (packages/memory/scope.py).
SCOPE_METRIC_KEYS: tuple[str, ...] = ("metric", "indicator", "показатель", "объект")

#: буквы (кириллица и латиница), числа и подчёркивания в слова не попадают
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _normalize_text(statement: str) -> str:
    """Типографика живых страниц (T7.77): NBSP/узкий пробел → обычный пробел,
    софт-гифен убирается, ё→е (иначе «ставка» и «ставка» — разные слова)."""
    text = statement.replace("\u00a0", " ").replace("\u202f", " ").replace("\u00ad", "")
    text = unicodedata.normalize("NFKC", text)
    return text.replace("ё", "е")


def _indicator_tokens(statement: str) -> list[str]:
    """Значимые слова формулировки: только буквы, без чисел/годов/месяцев/дат и
    без служебных слов (см. `_STOP_WORDS`, `_STOP_STEMS`, `_MONTH_PREFIXES`)."""
    tokens: list[str] = []
    for raw in _WORD_RE.findall(_normalize_text(statement)):
        word = raw.lower()
        if len(word) < 3 or word in _STOP_WORDS:
            continue
        if any(word.startswith(stem) for stem in _STOP_STEMS):
            continue
        if any(word.startswith(month) for month in _MONTH_PREFIXES):
            continue
        tokens.append(word)
    return tokens


def statement_indicator_words(statement: str) -> frozenset[str]:
    """Словарь показателя формулировки: основы значимых слов (`INDICATOR_STEM_LEN`)."""
    return frozenset(token[:INDICATOR_STEM_LEN] for token in _indicator_tokens(statement))


def _territory_words(statement: str) -> frozenset[str]:
    """Слова-территории формулировки (закрытый словарь `_TERRITORY_GROUPS`)."""
    found: set[str] = set()
    for word in _indicator_tokens(statement):
        if any(word.startswith(prefix) for group in _TERRITORY_GROUPS for prefix in group):
            found.add(word)
    return frozenset(found)


def _territory_group_ids(words: frozenset[str]) -> set[int]:
    """Номера групп территорий, названных формулировкой (для сравнения «одно и то же
    ли это название»: Россия/Российской — одна группа, Россия/Казахстан — разные)."""
    groups: set[int] = set()
    for index, group in enumerate(_TERRITORY_GROUPS):
        if any(word.startswith(prefix) for prefix in group for word in words):
            groups.add(index)
    return groups


def declared_scope_metric(scope: JsonDict | None) -> str | None:
    """Нормализованная метка показателя из свободного scope (T7.17: свободный scope
    модели живёт в staging-предложении и в assessed_scope legacy-строк).

    None — метки нет: тогда решает словарь формулировок (`indicator_check`)."""
    if not isinstance(scope, dict):
        return None
    for key in SCOPE_METRIC_KEYS:
        raw = scope.get(key)
        if not isinstance(raw, str):
            continue
        normalized = " ".join(_WORD_RE.findall(_normalize_text(raw))).lower()
        if normalized:
            return normalized
    return None


@dataclass(frozen=True)
class IndicatorCheck:
    """Решение сверки показателя + человекочитаемая деталь для причины в payload."""

    matched: bool
    detail: str


def indicator_check(
    claim_statement: str,
    candidate_statement: str,
    claim_metric: str | None = None,
    candidate_metric: str | None = None,
) -> IndicatorCheck:
    """Тот же ли это показатель (T7.83a)? Сомнение → False (гейт остаётся консервативным).

    (а) обе стороны назвали метку показателя — метки обязаны совпасть после
        нормализации (это самый сильный сигнал: он не зависит от формулировки);
    (б) хотя бы у одной метки нет — словари значимых слов обязаны пересечься не
        слабее `INDICATOR_OVERLAP_MIN`, и формулировки не должны называть разные
        территории. Пустой словарь у одной из сторон = сравнить нечего → False."""
    if claim_metric is not None and candidate_metric is not None:
        return IndicatorCheck(
            matched=claim_metric == candidate_metric,
            detail=(
                f"declared scope metric {claim_metric!r} vs {candidate_metric!r}"
                if claim_metric != candidate_metric
                else f"same declared scope metric {claim_metric!r}"
            ),
        )
    claim_words = statement_indicator_words(claim_statement)
    candidate_words = statement_indicator_words(candidate_statement)
    if not claim_words or not candidate_words:
        return IndicatorCheck(
            matched=False,
            detail="one of the statements has no comparable indicator vocabulary",
        )
    claim_territories = _territory_words(claim_statement)
    candidate_territories = _territory_words(candidate_statement)
    if (
        claim_territories
        and candidate_territories
        and not (_territory_group_ids(claim_territories) & _territory_group_ids(candidate_territories))
    ):
        return IndicatorCheck(
            matched=False,
            detail=(
                f"the statements name different territories "
                f"{sorted(claim_territories)} vs {sorted(candidate_territories)}"
            ),
        )
    union = claim_words | candidate_words
    overlap = Fraction(len(claim_words & candidate_words), len(union))
    return IndicatorCheck(
        matched=overlap >= INDICATOR_OVERLAP_MIN,
        detail=(
            f"indicator vocabulary overlap {overlap.numerator}/{overlap.denominator} "
            f"(shared stems: {sorted(claim_words & candidate_words)})"
        ),
    )


def _canonical_value(token: str) -> str | None:
    """Canonical form of one decimal token: separators → '.', float round-trip.

    Returns None for a token that cannot be canonicalized (e.g. «0,53.59») — the
    caller then treats the whole statement as unanalyzable (conservative silence)."""
    normalized = token.replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return repr(value)


def statement_values(statement: str) -> frozenset[str]:
    """Set of significant decimal values; empty set = nothing significant to match.

    A statement containing an unanalyzable numeric run is treated as having NO
    values (conservative: never declare a duplicate on a text we cannot read)."""
    values: set[str] = set()
    for m in _VALUE_TOKEN_RE.finditer(statement):
        canonical = _canonical_value(m.group(1))
        if canonical is None:
            return frozenset()
        values.add(canonical)
    return frozenset(values)


def statement_years(statement: str) -> frozenset[int]:
    """Standalone 4-digit years in the text, after masking decimal value tokens
    (so a year glued to a value — «12,2026» — is neither a value nor a year)."""
    masked = _VALUE_TOKEN_RE.sub(" ", statement)
    return frozenset(int(m.group(1)) for m in _YEAR_TOKEN_RE.finditer(masked))


@dataclass(frozen=True)
class ValueDuplicateClaim:
    """One claim op of the proposal WITHOUT an explicit existing_claim_id.

    `metric` is the op's declared indicator label (T7.83a): the host passes the
    normalized `declared_scope_metric(claim.scope)`."""

    index: int
    statement: str
    claim_type: str
    as_of_year: int | None
    has_support_link: bool
    has_counter_link: bool
    metric: str | None = None


@dataclass(frozen=True)
class ValueDuplicateCandidate:
    """One claim of the session's context pack that has a head in this snapshot.

    `relied` is T7.83a's hard condition for a merge: the curator declared this claim
    in `relied_claim_ids`. `metric` — the label stored with the claim (from the current
    head's assessed_scope; empty for host-scope-v1, see `SCOPE_METRIC_KEYS`)."""

    claim_id: str
    statement: str
    claim_type: str
    as_of_year: int | None
    relied: bool = False
    metric: str | None = None


def _period(
    statement: str, as_of_year: int | None
) -> frozenset[int]:
    years: set[int] = set(statement_years(statement))
    if as_of_year is not None:
        years.add(as_of_year)
    return frozenset(years)


def _entry(
    claim: ValueDuplicateClaim,
    *,
    action: str,
    target: str | None,
    reason: str,
) -> JsonDict:
    return {
        "claim_index": claim.index,
        "target": target,
        "action": action,
        "reason": reason,
    }


# ── T7.85 (ADR-0033 дополнение): побайтово та же формулировка при другом claim_type ─────
#
# Стендовая форма (сессия a09977e1, .92): одно и то же предложение записано дважды — как
# `external_fact` 2d010d53 и как `temporal_fact` 58e2d5d4; формулировки совпадают побайтово, а
# `existing_claim_id` куратор не указал. Дедуп T7.9 (`packages/memory/service.py`: равенство
# `statement` И равенство `claim_type`) такого дубля не видит — типы разные. Числовой гейт
# T7.83/T7.83a тоже не видит: его первое условие — тот же `claim_type`. На карточке появляется
# второе утверждение того же факта с другой оценкой.
#
# Правило аддитивно к числовому гейту и намеренно уже его: формулировки обязаны совпасть
# ПОБАЙТОВО (та же канонизация, что у дедупа T7.9 — сравнение строк без нормализаций), поэтому
# «похожести» здесь быть не может: если текст тот же, то и показатель, и период, и значения те
# же. Из этого же следует, что фильтр `relied_claim_ids` (T7.83a) этому правилу НЕ нужен: он
# запрещал хосту решать за куратора склейку ДВУХ РАЗНЫХ формулировок; побайтовая идентичность
# снимает неопределённость — хост не выбирает чужой факт, он остаётся на том же утверждении.
# Операция конвертируется в перепроверку этого claim, и оценка считается по ТИПУ ЯКОРЯ
# (T7.34/ADR-0018: тип предложения при перепроверке не волен), а не по типу, который модель
# выдумала вторым проходом. Побайтовое совпадение с claim того же типа — территория дедупа T7.9,
# хост туда не лезет; операции с явным `existing_claim_id` сюда не передаются вообще (T7.34).


def find_identical_statement_duplicates(
    claims: Sequence[ValueDuplicateClaim],
    candidates: Sequence[ValueDuplicateCandidate],
) -> list[JsonDict]:
    """Решения только для операций, чья формулировка побайтово совпадает с утверждением
    контекст-пака, а `claim_type` — другой. Остальное молчит (пустой список → нет ключа в
    payload → прежние аудит-события байт-в-байт прежние).

    Ровно один кандидат → перепроверка этого claim (`reverified`). Два и больше → операция
    остаётся как предложена: какой из них «тот же самый» — решает куратор, не хост. Тот же текст
    при том же типе → молчание: это ведёт дедуп T7.9. Ограничения по связям те же, что у
    числового гейта: улика-опровержение не пристёгивается к чужой записи, а без поддерживающей
    улики кандидату нечего добавить."""
    decisions: list[JsonDict] = []
    ordered = sorted(candidates, key=lambda candidate: candidate.claim_id)
    for claim in claims:
        if not claim.statement:
            continue
        same_text = [candidate for candidate in ordered if candidate.statement == claim.statement]
        if not same_text:
            continue  # побайтового совпадения нет — это территория числового гейта выше
        if any(candidate.claim_type == claim.claim_type for candidate in same_text):
            continue  # тот же текст и тот же тип ведёт дедуп T7.9, хост его не подменяет
        different = [c for c in same_text if c.claim_type != claim.claim_type]
        if len(different) >= 2:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"the statement is byte-identical to {len(different)} context-pack claims "
                        f"of another type ({', '.join(c.claim_id for c in different)}) — which one "
                        "is the same claim cannot be decided by the host; left as proposed"
                    ),
                )
            )
            continue
        target = different[0]
        if claim.has_counter_link:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"byte-identical statement of {target.claim_id}, but the op carries "
                        "counterevidence links — a dispute stays a dispute, the candidate is "
                        "not touched"
                    ),
                )
            )
            continue
        if not claim.has_support_link:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"byte-identical statement of {target.claim_id}, but the op has no "
                        "supporting evidence — nothing new to add to the candidate"
                    ),
                )
            )
            continue
        decisions.append(
            _entry(
                claim,
                action="reverified",
                target=target.claim_id,
                reason=(
                    f"same statement, type relabelled {claim.claim_type} → {target.claim_type}: "
                    f"the formulation is byte-identical to {target.claim_id}, so the value, the "
                    "period and the indicator are the same by construction (the reliance filter "
                    "of T7.83a is not needed here — it exists to keep the host from choosing "
                    "between two different formulations); converted to a reverify of that claim, "
                    "assessed under its own claim type"
                ),
            )
        )
    return decisions


def find_value_duplicates(
    claims: Sequence[ValueDuplicateClaim],
    candidates: Sequence[ValueDuplicateCandidate],
) -> list[JsonDict]:
    """Decisions ONLY for ops that have same-type/same-value suspects; everything
    else stays silent (no entry → no payload key → old audits byte-identical).

    T7.83a ordering (deterministic, each refusal names its own honest reason):
    suspects (type+values) → period covered → RELIED candidates (`relied_claim_ids`) →
    INDICATOR match → all-supports / no-counterevidence → merge. The «exactly one
    candidate» count is taken after the relied filter; ≥2 remains an ambiguity."""
    ordered_candidates = sorted(candidates, key=lambda c: (not c.relied, c.claim_id))
    decisions: list[JsonDict] = []
    for claim in claims:
        new_values = statement_values(claim.statement)
        if not new_values:
            continue  # integers/years only — conservative silence (T7.9 owns byte-exact dups)
        new_period = _period(claim.statement, claim.as_of_year)
        suspects = [
            candidate
            for candidate in ordered_candidates
            if candidate.claim_type == claim.claim_type
            and statement_values(candidate.statement) == new_values
        ]
        if not suspects:
            continue
        strict = [
            candidate
            for candidate in suspects
            if new_period and new_period <= _period(candidate.statement, candidate.as_of_year)
        ]
        if not strict:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"value {sorted(new_values)} matches candidate(s) "
                        f"{', '.join(candidate.claim_id for candidate in suspects)} by type and "
                        f"values, but the period {sorted(new_period)} is not covered — left as proposed"
                    ),
                )
            )
            continue
        relied_strict = [candidate for candidate in strict if candidate.relied]
        if not relied_strict:
            # T7.83a: value+type+period совпали, но куратор НЕ объявил ни одного из
            # этих claims опорой своего ответа — хост не имеет права решать за него.
            shared = ", ".join(candidate.claim_id for candidate in strict)
            if len(strict) >= 2:
                reason = (
                    f"ambiguous value duplicate: {len(strict)} candidates share type, values "
                    f"and period ({shared}) and none of them is declared in relied_claim_ids "
                    "— the op is left as proposed"
                )
            else:
                reason = (
                    f"value {sorted(new_values)} matches candidate {strict[0].claim_id} by type, "
                    f"values and period, but that candidate is not in relied_claim_ids — only a "
                    "candidate the curator declared as the support of this answer may be merged; "
                    "left as proposed"
                )
            decisions.append(_entry(claim, action="kept", target=None, reason=reason))
            continue
        if len(relied_strict) >= 2:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        "ambiguous value duplicate: "
                        f"{len(relied_strict)} candidates share type, values and period "
                        f"({', '.join(candidate.claim_id for candidate in relied_strict)}) — "
                        "the op is left as proposed"
                    ),
                )
            )
            continue
        target = relied_strict[0]
        check = indicator_check(claim.statement, target.statement, claim.metric, target.metric)
        if not check.matched:
            # T7.83a defect shape: то же десятичное число и тот же период у ДРУГОГО
            # показателя — склейка привязала бы улику не к тому утверждению.
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"value duplicate of {target.claim_id} by type, values and period, but "
                        f"the indicator does not match ({check.detail}) — the value belongs to "
                        "another indicator; left as proposed"
                    ),
                )
            )
            continue
        if claim.has_counter_link:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"value duplicate of {target.claim_id}, but the op carries "
                        "counterevidence links — a dispute stays a dispute, the "
                        "candidate is not touched"
                    ),
                )
            )
            continue
        if not claim.has_support_link:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        f"value duplicate of {target.claim_id}, but the op has no "
                        "supporting evidence — nothing new to add to the candidate"
                    ),
                )
            )
            continue
        decisions.append(
            _entry(
                claim,
                action="reverified",
                target=target.claim_id,
                reason=(
                    f"value duplicate of {target.claim_id}: same claim type "
                    f"({claim.claim_type}), same value set {sorted(new_values)}, period "
                    f"{sorted(new_period)} covered by the candidate and the same indicator "
                    f"({check.detail}); the candidate is declared in relied_claim_ids — "
                    "converted to a reverify so the value is written once"
                ),
            )
        )
    return decisions
