"""Представление надёжности ответа (T7.64, ADR-0026).

Чистая презентационная функция: она НЕ оценивает знание. Grade и
epistemic_status производит rules engine (`packages/memory/rules_engine.py`,
ARCHITECTURE §3.7, §8.7); здесь уже вычисленные значения переводятся в
понятную шкалу «насколько можно верить» и в человеческую фразу «как
проверено». Никаких обращений к базе и к модели: только переданные поля.

Шкала (проект упрощения интерфейса, решение пользователя):

* проверено — вывод принят правилами (статус supported);
* подтверждено слабо — предположение с частичными подтверждениями;
* не проверено — подтверждений или оценки нет;
* спорно — есть неподтверждённое возражение;
* опровергнуто — вывод опровергнут;
* отложено — оценку не удалось корректно завершить.

Если данных не хватает, функция честно сообщает об этом вместо того, чтобы
изображать проверку (проект упрощения интерфейса, раздел про профиль без
сети).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

LEVEL_VERIFIED: Final = "verified"
LEVEL_WEAK: Final = "weak"
LEVEL_UNVERIFIED: Final = "unverified"
LEVEL_DISPUTED: Final = "disputed"
LEVEL_REFUTED: Final = "refuted"
LEVEL_DEFERRED: Final = "deferred"

RELIABILITY_LEVELS: Final[tuple[str, ...]] = (
    LEVEL_VERIFIED,
    LEVEL_WEAK,
    LEVEL_UNVERIFIED,
    LEVEL_DISPUTED,
    LEVEL_REFUTED,
    LEVEL_DEFERRED,
)

_LEVEL_LABEL: Final[dict[str, str]] = {
    LEVEL_VERIFIED: "Проверено",
    LEVEL_WEAK: "Подтверждено слабо",
    LEVEL_UNVERIFIED: "Не проверено",
    LEVEL_DISPUTED: "Спорно",
    LEVEL_REFUTED: "Опровергнуто",
    LEVEL_DEFERRED: "Отложено",
}

#: Цвет — логический токен страницы (конкретные оттенки выбирает CSS этапа T7.65).
_LEVEL_COLOR: Final[dict[str, str]] = {
    LEVEL_VERIFIED: "green",
    LEVEL_WEAK: "yellow",
    LEVEL_UNVERIFIED: "gray",
    LEVEL_DISPUTED: "orange",
    LEVEL_REFUTED: "red",
    LEVEL_DEFERRED: "gray",
}

_LEVEL_HINT: Final[dict[str, str]] = {
    LEVEL_DISPUTED: "Есть возражение, которое ещё не разобрано: на такой вывод опираться нельзя.",
    LEVEL_REFUTED: "Противоречащие доказательства перевесили: вывод признан ошибочным.",
    LEVEL_DEFERRED: "Оценку отложили: для этого типа утверждений не хватает обязательных данных.",
}

#: Запасные пороги `supported` по типам утверждений. Зеркалят правила
#: ARCHITECTURE §8.7 и payload действующего снимка правил; тест сверяет их с
#: `docs/eval/config-v13-payload.json`, чтобы презентация не разъезжалась с
#: правилами. Если вызывающий код передал порог из снапшота, используется он.
TYPE_MIN_GRADE: Final[dict[str, str]] = {
    "local_observation": "E2",
    "computed_result": "E2",
    "self_model": "E2",
    "empirical_conjecture": "E3",
    "procedural": "E3",
    "external_fact": "E3",
    "temporal_fact": "E3",
    "formal_theorem": "E4",
}

#: Чего не хватает по типу утверждения (формулировка строго из правил §8.7).
TYPE_REQUIREMENT: Final[dict[str, str]] = {
    "local_observation": "нужно подтверждение наблюдением в зафиксированных условиях (уровень E2)",
    "computed_result": "нужно подтверждение вычислением в зафиксированных условиях (уровень E2)",
    "self_model": "нужно измеряемое наблюдение о самом узле (уровень E2)",
    "empirical_conjecture": "нужны минимум два эксперимента в независимо полученных условиях (уровень E3)",
    "procedural": "нужны минимум две успешные повторения независимо полученным путём (уровень E3)",
    "external_fact": "нужно минимум два независимых источника (уровень E3)",
    "temporal_fact": "нужны два независимых источника (уровень E3) и ясная привязка ко времени",
    "formal_theorem": "нужно формальное доказательство (уровень E4)",
}

#: Уровни, при которых предположение показывается жёлтым («частично подтверждено»).
_WEAK_GRADES: Final[frozenset[str]] = frozenset({"E1", "E2"})

_HEAD_STATE_HINT: Final[dict[str, str]] = {
    "none": "Оценки нет: подтверждений по действующим правилам не зафиксировано.",
    "pending": "Новая оценка подготовлена, но действующей ещё не стала — опираться на неё нельзя.",
    "invalid": "Прежняя оценка потеряла силу: нужна перепроверка утверждения.",
}

NO_DETAILS: Final = "подробности проверки недоступны"


def reliability(
    claim_type: str | None,
    epistemic_status: str | None,
    effective_grade: str | None,
    *,
    head_state: str | None = None,
    min_grade_for_supported: str | None = None,
) -> dict[str, str]:
    """{"level","label","hint","color"} по уже вычисленной оценке.

    `effective_grade` и `epistemic_status` берутся из действующей оценки
    (head current); для pending/invalid/none они NULL — и ответ честно
    показывает «не проверено» с объяснением, а не выдумывает уровень.
    Порог типа берётся из переданного снапшота правил, иначе из запасной
    таблицы этого модуля.
    """
    grade = effective_grade if isinstance(effective_grade, str) and effective_grade else None
    status = epistemic_status if isinstance(epistemic_status, str) and epistemic_status else None

    if head_state in _HEAD_STATE_HINT:
        return _build(LEVEL_UNVERIFIED, _HEAD_STATE_HINT[str(head_state)])

    if status == "supported":
        hint = (
            f"Вывод принят правилами оценки: уровень подтверждения {grade}."
            if grade is not None
            else "Вывод принят правилами оценки."
        )
        return _build(LEVEL_VERIFIED, hint)

    if status in (LEVEL_DISPUTED, LEVEL_REFUTED, LEVEL_DEFERRED):
        return _build(status, _LEVEL_HINT[status])

    if status == "hypothesis":
        if grade is None or grade == "E0":
            return _build(
                LEVEL_UNVERIFIED,
                "Пригодных для оценки подтверждений пока нет: уровень подтверждения E0.",
            )
        if grade in _WEAK_GRADES:
            key = str(claim_type or "")
            threshold = min_grade_for_supported or TYPE_MIN_GRADE.get(key, "")
            requirement = TYPE_REQUIREMENT.get(key) or (
                "подтверждений по правилам этого типа не хватает"
            )
            missing_by_grade = ""
            if threshold and _grade_rank(grade) < _grade_rank(threshold):
                missing_by_grade = f" Сейчас уровень {grade} ниже порога {threshold}."
            return _build(LEVEL_WEAK, f"Пока это предположение: {requirement}.{missing_by_grade}")
        # Подозрительная запись: уровень выше, чем rules-v2 даёт предположению.
        return _build(
            LEVEL_UNVERIFIED,
            "Запись об уровне не согласуется с правилами оценки: утверждению нужна переоценка.",
        )

    return _build(LEVEL_UNVERIFIED, NO_DETAILS)


def _build(level: str, hint: str) -> dict[str, str]:
    return {
        "level": level,
        "label": _LEVEL_LABEL[level],
        "hint": hint,
        "color": _LEVEL_COLOR[level],
    }


def _grade_rank(grade: str) -> int:
    order = ("E0", "E1", "E2", "E3", "E4")
    return order.index(grade) if grade in order else -1


# ─── «как проверено» ──────────────────────────────────────────────────────

_KIND_PHRASE: Final[dict[str, str]] = {
    "computation": "выполнено вычисление",
    "experiment_run": "проведён опыт",
    "formal_check": "вывод проверен формально",
    "local_observation": "получено наблюдение на узле",
    "quote_integrity": "цитата сверена с источником",
    "source_assertion": "использовано утверждение источника",
}


def describe_verification(
    evidence_rows: Iterable[Mapping[str, Any]] | None,
    *,
    source_groups: Iterable[Mapping[str, Any]] | None = None,
    environment_groups: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Короткие честные фразы «как проверено» из переданных полей доказательств.

    `evidence_rows` — строки доказательств (relation, evidence_kind, ссылка на
    источник и/или артефакт). None означает «данных не передали»: возвращается
    одна честная фраза, а не правдоподобная проверка. Ничего сверх переданного
    не утверждается: изоляция среды, независимость источников и факт сверки
    упоминаются только когда соответствующее поле действительно есть.
    """
    if evidence_rows is None:
        return [NO_DETAILS]

    rows = [row for row in evidence_rows if isinstance(row, Mapping)]
    if not rows:
        return ["подтверждений нет: ни одного свидетельства не зафиксировано"]

    supports = [row for row in rows if row.get("relation") == "supports"]
    counters = [row for row in rows if row.get("relation") == "counters"]
    phrases: list[str] = []

    kinds: dict[str, int] = {}
    for row in supports:
        kind = row.get("evidence_kind")
        if isinstance(kind, str) and kind in _KIND_PHRASE:
            kinds[kind] = kinds.get(kind, 0) + 1

    for kind in sorted(kinds):
        phrase = f"{_KIND_PHRASE[kind]}: {kinds[kind]}"
        if kind == "computation" and any(
            row.get("evidence_kind") == kind and _has_artifact(row) for row in supports
        ):
            phrase += ", артефакт результата сохранён"
        phrases.append(phrase)

    source_refs = {_source_ref(row) for row in supports if _source_ref(row) is not None}
    if len(source_refs) > 1:
        phrases.append(f"источников прочитано: {len(source_refs)}")
    elif len(source_refs) == 1:
        phrases.append("использован один источник")

    groups = _distinct_groups(source_groups)
    if groups >= 2:
        phrases.append(f"источники разнесены по {groups} независимым группам")

    env_groups = _distinct_groups(environment_groups)
    if env_groups >= 2:
        phrases.append(f"повторения получены независимо: {env_groups} групп условий")

    if counters:
        phrases.append(f"есть возражения: {len(counters)}")

    if not phrases:
        return [NO_DETAILS]
    return phrases


def _has_artifact(row: Mapping[str, Any]) -> bool:
    artifact = row.get("artifact")
    return bool(artifact) or bool(row.get("artifact_sha256")) or bool(row.get("observation_artifact_id"))


def _source_ref(row: Mapping[str, Any]) -> str | None:
    source = row.get("source")
    if isinstance(source, Mapping):
        for key in ("canonical_uri", "id", "content_hash"):
            value = source.get(key)
            if value:
                return str(value)
    for key in ("source_uri", "source_id", "canonical_uri"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _distinct_groups(rows: Iterable[Mapping[str, Any]] | None) -> int:
    if rows is None:
        return 0
    groups = {
        row.get("group_id")
        for row in rows
        if isinstance(row, Mapping) and row.get("group_id") is not None
    }
    return len(groups)
