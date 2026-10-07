"""Атрибуция конкретного значения во фрагменте (T7.78, ADR-0029: поуровневая производность).

Детектор T7.75/T7.77 (`apps/research_proxy/source_attribution.py`) отвечает на вопрос о **странице**:
если странице присвоены два разных первоисточника, он честно отказывается решать
(«ambiguous_primaries») и указатель не ставит. Но доказательство утверждения — не страница, а её
фрагмент: для `По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59%`
страница СберCIB — пересказ Росстата, и только он; для `По оценкам Банка России, инфляция в январе
замедлилась до 10,7%` та же страница — пересказ Банка России. Этот модуль отвечает на второй вопрос:
**какому первоисточнику принадлежит значение, на котором стоит конкретное утверждение**.

Логика — чистая функция (AGENTS §4): ни БД, ни сети; вход — canonical URI прочитанной страницы,
текст основания улики и формулировка утверждения; выход — первоисточник или отказ с причиной.
Словарь первоисточников, нормализация типографики, шаблоны атрибуции, маркеры измерения, вето
собственной оценки и разбиение на фрагменты переиспользуются из детектора страниц: один словарь и
одна нормализация на оба уровня (два списка означали бы два разных мнения о том, что такое
«первоисточник»).

Консервативность сохранена в ту же сторону, что и у страничного детектора (ADR-0029 «Риски»: ложная
склейка дороже ложного пропуска):

- пара «значение + первоисточник» обязана собраться внутри одного фрагмента (окно
  `ATTRIBUTION_WINDOW_CHARS` — то же, что у страничного детектора);
- маркеры собственной оценки в основании улики отвергают производность;
- число, отличное от числа утверждения («другое число»), не даёт атрибуции этому утверждению;
- голое упоминание первоисточника без значения — не атрибуция;
- страница самого первоисточника не бывает пересказом себя;
- два первоисточника для одного и того же значения утверждения — отказ.

Решение принимает только хост: модель к производности не допускается, оценки назначает rules engine.
Разворачивается решение в записях БД в `apps/orchestrator/orchestrator.py` (новая улика) и
`apps/research_proxy/reattribution.py` (уже существующие улики).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from apps.research_proxy.source_attribution import (
    _OWN_ASSESSMENT,
    MAX_SCAN_CHARS,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    STATUS_SELF_PRIMARY,
    VALUE_NUMBER_PATTERN,
    _attributed_keys,
    _basis,
    _fragments,
    is_home_host,
    normalize_scan_text,
    primary_source,
)

#: версия метода поуровневой атрибуции: попадает в запись улики и в аудит, чтобы решение можно было
#: отличить от страничного решения детектора (у него своя версия) и от последующих версий.
VALUE_ATTRIBUTION_METHOD_VERSION: Final = "host-value-attribution-v1"

#: честный отказ этого детектора: первоисточник в основании назван, но значение там другое — не то,
#: на котором стоит утверждение. Отличие от `no_value_attribution` (атрибуции нет вовсе) нужно,
#: чтобы переатрибуция показала оператору разницу.
STATUS_VALUE_NOT_ATTRIBUTED: Final = "value_not_attributed"

NEGATIVE_STATUSES: Final[tuple[str, ...]] = (
    STATUS_OWN_ASSESSMENT,
    STATUS_AMBIGUOUS,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_SELF_PRIMARY,
    STATUS_VALUE_NOT_ATTRIBUTED,
)

#: единицы измерения. Именно они отличают значение от года, номера статьи или даты публикации:
#: `2025` без единицы значением не считается (тот же смысл, что у MEASUREMENT_MARKERS страничного
#: детектора, но применённый к числу, а не к фрагменту).
VALUE_UNIT_PATTERNS: Final[tuple[str, ...]] = (
    r"%",
    # «п.п.» и «п. п.» — одно и то же измерение; NBSP между частями нормализован на входе
    r"\bп\.?\s?п\.",
    r"\bпроцент\w*",
    r"\bмлрд\b",
    r"\bмлн\b",
    r"\bтыс\w*",
    r"\bрубл\w*",
    r"\bдоллар\w*",
    r"\bевро\b",
    r"\bpercent\w*",
    r"\bpoints?\b",
)

#: зазор между числом и единицей: «5,59%», «5,59 %», «10,7 п.п.» — соседние символы
_UNIT_SCAN: Final = re.compile(
    r"^[ \t\u00a0]{0,3}(" + "|".join(VALUE_UNIT_PATTERNS) + ")", re.IGNORECASE
)
#: сколько символов после числа рассматривается как единица: единица может содержать пробел
#  внутри («п. п.»), поэтому окно чуть шире самой короткой единицы
_UNIT_TAIL_CHARS: Final = 8


@dataclass(frozen=True)
class ValueAttributionDecision:
    """Что хост понял о происхождении **этого значения**: чей пересказ читает утверждение."""

    status: str
    primary_key: str | None = None
    primary_name: str | None = None
    parent_uri: str | None = None
    #: фрагмент, на котором основано решение (маскированный, обрезанный — как у страничного детектора)
    basis_fragment: str = ""
    #: значения утверждения, по которым выбран фрагмент; пусто — когда утверждение не называет
    #: измеренного значения, и тогда выбор идёт по всему основанию улики (строже к отказу)
    claim_values: tuple[str, ...] = ()

    @property
    def is_derivative(self) -> bool:
        return self.status == STATUS_DERIVATIVE


def _canonical_number(raw: str) -> str:
    """Число для сравнения: типографика убрана, десятичная запятая становится точкой. `5,59`,
    `5.59` и `5,\u00a059` — одно значение; `2025` к значениям не относится (нет единицы)."""
    text = re.sub(r"[\s\u00a0]+", "", raw)
    return text.replace(",", ".").strip(".")


def _measured_numbers(text: str) -> set[str]:
    """Числа текста, которые являются значениями: число с единицей измерения рядом."""
    found: set[str] = set()
    for match in VALUE_NUMBER_PATTERN.finditer(text):
        tail = text[match.end() : match.end() + _UNIT_TAIL_CHARS]
        if _UNIT_SCAN.match(tail):
            found.add(_canonical_number(match.group()))
    return found


def claim_value_numbers(statement: str | None) -> tuple[str, ...]:
    """Значения утверждения (детерминированно, тем же сравнением чисел). Пусто — когда утверждение
    не называет измеренного значения: тогда выбор фрагмента идёт по всему основанию улики, и отказ
    при двух первоисточниках становится строже."""
    if not statement:
        return ()
    return tuple(sorted(_measured_numbers(normalize_scan_text(statement))))


def attribute_value_in_fragment(
    *,
    canonical_uri: str | None,
    text: str | None,
    claim_statement: str | None = None,
) -> ValueAttributionDecision:
    """Кому принадлежит значение, на котором стоит утверждение.

    `text` — основание улики: фрагмент прочитанной страницы (для новой улики это `assertion_text`,
    для уже существующей — весь сохранённый нормализованный текст страницы: долговременного фрагмента
    у строки улики нет, ограничение описано в STATUS T7.78). `canonical_uri` — адрес прочитанной
    страницы, `claim_statement` — формулировка утверждения (может отсутствовать).
    """
    if not text:
        return ValueAttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION)

    scanned = normalize_scan_text(text)[:MAX_SCAN_CHARS]
    values = claim_value_numbers(claim_statement)

    if any(pattern.search(scanned) for pattern in _OWN_ASSESSMENT):
        # своя оценка/альтернативный расчёт в основании улики: склеивать нельзя
        return ValueAttributionDecision(status=STATUS_OWN_ASSESSMENT, claim_values=values)

    attributed: dict[str, str] = {}
    matched: dict[str, str] = {}
    for fragment in _fragments(scanned):
        keys = _attributed_keys(fragment)
        if not keys:
            continue
        attributed.update(dict.fromkeys(keys, fragment))
        if values and not (_measured_numbers(fragment) & set(values)):
            continue  # «другое число»: первоисточник назван, но не про значение утверждения
        matched.update(dict.fromkeys(keys, fragment))

    if not attributed:
        return ValueAttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION, claim_values=values)

    # страница самого первоисточника не бывает его пересказом
    candidates = {
        key: basis
        for key, basis in matched.items()
        if not is_home_host(canonical_uri, _home_hosts(key))
    }
    if not candidates:
        if not matched:
            # атрибуции в основании были, но все — про другие числа утверждения
            return ValueAttributionDecision(
                status=STATUS_VALUE_NOT_ATTRIBUTED, claim_values=values
            )
        own_only = all(is_home_host(canonical_uri, _home_hosts(key)) for key in matched)
        return ValueAttributionDecision(
            # значение утверждения приписано самой прочитанной странице — она первоисточник
            status=STATUS_SELF_PRIMARY if own_only else STATUS_VALUE_NOT_ATTRIBUTED,
            claim_values=values,
        )

    if len(candidates) > 1:
        # одному и тому же значению утверждения присвоены разные первоисточники — разночтение
        return ValueAttributionDecision(
            status=STATUS_AMBIGUOUS,
            basis_fragment=_basis(next(iter(candidates.values()))),
            claim_values=values,
        )

    key, basis = next(iter(candidates.items()))
    spec = primary_source(key)
    if spec is None:  # защита: решение не строится на несуществующей записи словаря
        return ValueAttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION, claim_values=values)
    return ValueAttributionDecision(
        status=STATUS_DERIVATIVE,
        primary_key=spec.key,
        primary_name=spec.name,
        parent_uri=spec.home_uri,
        basis_fragment=_basis(basis),
        claim_values=values,
    )


def _home_hosts(key: str) -> tuple[str, ...]:
    spec = primary_source(key)
    return () if spec is None else spec.home_hosts
