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
    _ALIASES,
    _MEASUREMENTS,
    _OWN_ASSESSMENT,
    _TEMPLATES,
    ATTRIBUTION_WINDOW_CHARS,
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
    named_primary_keys,
    normalize_scan_text,
    primary_source,
)
from packages.memory.scope import (
    VALUE_ATTRIBUTION_PAIRING_PUBLICATION,
    VALUE_ATTRIBUTION_PAIRING_STRICT,
)

#: версия метода поуровневой атрибуции: попадает в запись улики и в аудит, чтобы решение можно было
#: отличить от страничного решения детектора (у него своя версия) и от последующих версий.
#: v2 (T7.85): добавлен узкий второй режим pairing — «атрибуция публикации» (см. `_publication_basis`):
#: значение утверждения и указание на публикацию первоисточника могут стоять в одном блоке
#: страницы, но в разных предложениях (замер kommersant.ru/forbes.ru: «…следует из опубликованных
#: 17 декабря данных опроса, проводимого „инФОМ“ по заказу Банка России. … Оценка текущих темпов
#: роста цен при этом осталась на уровне 14,5%»). Строгий режим не ослаблен: он по-прежнему
#: решает первым и первым же отказывает; запись несёт поле `pairing`, поэтому решение v2
#: отличается от решения v1 и переатрибуция обязана его пересмотреть.
#: v3 (T7.85a, замечание приёмки): в окне публикации действует вето «другого источника числа»
#: (`_other_source_sign`): если между концом фразы атрибуции публикации и числом стоит другое
#: действующее лицо, глагол оценки/прогноза или название вне словаря — приписывание запрещено
#: (замер на `792d2c4`: «…отчёта Росстата, инфляция составила 5,59%. Аналитики Сбербанка ожидают
#: инфляцию 6,3%» приписывалось Росстату). Поведение метода изменилось → номер поднят, и
#: переатрибуция пересматривает записи, помеченные `host-value-attribution-v2`. Строгий режим
#: не тронут: его пара собирается внутри одного фрагмента и вето к ней не применяется.
VALUE_ATTRIBUTION_METHOD_VERSION: Final = "host-value-attribution-v3"

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

#: T7.85: второй, более узкий режим pairing — «атрибуция публикации». Страница нередко объявляет
#: источник всех приводимых чисел одним указанием на публикацию первоисточника («…следует из
#: опубликованных 17 декабря данных опроса, проводимого „инФОМ“ по заказу Банка России»,
#: «…следует из опроса „инФОМ“, опубликованного ЦБ»), а нужное значение называет в следующем
#: предложении. Строгий режим («значение и первоисточник в одном фрагменте») не ослаблен: он
#: решает первым, и только его честный отказ даёт право рассмотреть окно публикации. Окно
#: ограничено блоком вёрстки (одиночный перевод строки — граница блока: за неё окно не уходит)
#: и длиной PUBLICATION_WINDOW_CHARS от числа назад.
PUBLICATION_WINDOW_CHARS: Final = 400
_PUBLICATION_TAIL_CHARS: Final = 40
_BLOCK_BREAK: Final = "\n"

#: маркеры публикации первоисточника — указание на материал, который первоисточник опубликовал
#: или провёл. Сам шаблон атрибуции («по данным») маркером не считается: иначе окно публикации
#: разрешало бы склейку по любому упоминанию ведомства рядом с чужим числом.
PUBLICATION_MARKERS: Final[tuple[str, ...]] = (
    r"\bопрос[а-яё]*",
    r"\bопубликов[а-яё]*",
    r"\bпубликац[а-яё]*",
    r"\bмониторинг[а-яё]*",
    r"\bобзор[а-яё]*",
    r"\bисследовани[а-яё]*",
    r"\bотч[ёе]т[а-яё]*",
    r"\bрелиз[а-яё]*",
    r"\bsurvey\w*",
    r"\bpublicat\w*",
    r"\breport\w*",
)
_PUBLICATIONS: Final = tuple(re.compile(pattern, re.IGNORECASE) for pattern in PUBLICATION_MARKERS)

# ─── T7.85a: вето «другого источника числа» внутри окна публикации ──────────────────────
# Замечание приёмки T7.85: окно публикации проверяло маркер публикации, атрибуцию и число
# названных первоисточников, но не видело СМЕНУ ИСТОЧНИКА внутри окна: если между концом фразы
# атрибуции публикации и числом появилось другое действующее лицо или чужая оценка/прогноз,
# число принадлежит уже не названной публикации (замер на `792d2c4`: «По данным опубликованного
# Росстатом отчёта, инфляция составила 5,59%. Аналитики Сбербанка ожидают инфляцию 6,3%» —
# приписывание Росстату; то же для «экономисты Райффайзенбанка оценивают 14,5%» после опроса ЦБ).
# Вето действует только в режиме окна публикации и только в сторону отказа (сомнение — не
# приписывать: ADR-0029 «Риски», ложная склейка дороже ложного пропуска); строгий режим не тронут.

#: действующие лица, которые могут быть другим источником числа. Поиск идёт по окну, где алиасы
#: словаря первоисточников замазаны (см. `_mask_primary_aliases`): строгие формы «Банк России»,
#: «Банка России», «Центробанк» в действующее лицо-банк не превращаются, а «Сбербанк(а)»,
#: «Райффайзенбанк(а)», голое «банки/банка» — превращаются (граница слова у алиаса «банк»
#: намеренно отсутствует: имя банка-актера может содержать его внутри себя).
OTHER_SOURCE_ACTOR_MARKERS: Final[tuple[str, ...]] = (
    r"\bаналит[а-яё]*",
    r"\bэкономист[а-яё]*",
    r"\bэксперт[а-яё]*",
    r"\bспециалист[а-яё]*",
    r"\bкомпани[а-яё]*",
    r"\bагентств[а-яё]*",
    r"\bинститут[а-яё]*",
    r"\bцентр[а-яё]*",
    # без `\b` в начале: «Сбербанка», «Райффайзенбанка» — действующее лицо; алиасы словаря замазаны
    r"банк[а-яё]*",
)

#: глаголы и обороты оценки/прогноза. Ключевое различение (закреплено формой Коммерсанта из
#: фикстур T7.85): «Оценка текущих темпов роста цен при этом осталась на уровне 14,5%» —
#: существительное «оценка» здесь ИМЯ показателя, опубликованного вместе с первоисточником, а не
#: чьё-то действие над значением. Поэтому в списке нет голого существительного «оценк*»: только
#: глагольные формы («оценивают», «оценил») и обороты «по оценке», «по расчётам», «по мнению».
#: Так же различаются «ожидания»: «Показатель ожидаемой инфляции вырос» — имя показателя опроса;
#: «аналитики ожидают 6,3%» — чужой прогноз (перечисленные конечные формы глагола).
OTHER_SOURCE_ESTIMATION_MARKERS: Final[tuple[str, ...]] = (
    r"\bоцен(?:ив[а-яё]*|ил[а-яё]*|ит\b)",  # оценивают/оценивала/оценили/оценит; «оценка» — мимо
    r"\bрассчит(?:ыва[а-яё]*|ал[а-яё]*)",  # рассчитывают/рассчитали
    r"\bпо\s*расч[её]т[а-яё]*",  # «по расчётам» — чей-то расчёт (голое «расчёт» — мимо)
    r"\bпо\s*оценк[а-яё]*",  # «по оценке ВШЭ» — чужая оценка (голая «оценка» — имя показателя)
    r"\bпо\s*мнени[а-яё]*",  # «по мнению аналитиков»
    r"\bсчита(?:[ею][а-яё]*|л[а-яё]*)",  # считают/считаем/считал
    r"\bполага(?:[ею][а-яё]*|л[а-яё]*)",  # полагают/полагал
    # конечные формы глагола ожидания: «ожидает/ожидали» — чужой прогноз;
    # «ожидаемой(ой)/ожидания/ожиданий» — имя публикуемого показателя (замер kommersant.ru)
    r"\boжида(?:ет|ут|ют|ем|ит|л[а-яё]*)\b",
    # прогноз в живом тексте всегда чей-то: собственного издания у прогноза нет (ср. PUBLICATION_MARKERS)
    r"\bпрогноз[а-яё]*",
)

#: отдельный пункт замечания приёмки — «исследование/опрос ДРУГОГО заказчика». Сами по себе эти
#: существительные — маркеры публикации (`PUBLICATION_MARKERS`), и голое их вето ломало бы саму
#: фразу атрибуции («…данных опроса, проводимого „инФОМ“ по заказу Банка России»). Признак
#: другого заказчика — примыкающее ЗАГЛАВНОЕ имя собственное ПОСЛЕ существительного, отсутствующее
#: в словаре первоисточников: «Исследование Ромир показало 14,5%», «опрос ВШЭ дал 14,5%».
#: Обратный порядок («Годовой опрос показал») не проверяется: он даёт ложные срабатывания на
#: заглавных прилагательных начала предложения; порядок «Имя + глагол + число» покрывается
#: оборотами оценки и кавычками. Ограничение сознательное (риск описан в STATUS T7.85a).
SURVEY_WITH_ANOTHER_CUSTOMER_MARKERS: Final[tuple[str, ...]] = (
    r"(?:[Ии]сследовани[а-яё]*|[Оо]прос[а-яё]*)\s+(?P<customer>[А-ЯЁ][а-яё\-]{2,})",
)

#: название организации в кавычках вне начала предложения (пункт «кавычки» замечания приёмки).
#: Заглавные слова БЕЗ кавычек не проверяются: слишком много ложных срабатываний на начале
#: предложения и именах показателя — надёжность даёт связка «действие + заглавное имя» выше;
#: риск описан в STATUS T7.85a и ADR-0029 (дополнение).
_QUOTED_NAME: Final = re.compile(r"«([^»\n]{1,60})»|\"([^\"]{2,60})\"")
_SURVEY_CUSTOMER: Final = tuple(
    re.compile(pattern)  # без IGNORECASE: заглавная буква имени — часть признака
    for pattern in SURVEY_WITH_ANOTHER_CUSTOMER_MARKERS
)
_SENTENCE_BREAK: Final = re.compile(r"[.!?\u2026]+")

#: какой pairing дал решение — записывается в улику, чтобы более слабый режим был виден оператору.
#: Словарь значений и проверка формы живёт в одном месте — `packages/memory/scope.py`
#: (`VALUE_ATTRIBUTION_PAIRINGS`): у детектора и у записи не должно быть двух мнений о том,
#: как называется режим pairing.
PAIRING_VALUE_AND_PRIMARY: Final = VALUE_ATTRIBUTION_PAIRING_STRICT
PAIRING_PUBLICATION_WINDOW: Final = VALUE_ATTRIBUTION_PAIRING_PUBLICATION


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
    #: какой pairing дал решение: строгий режим или окно публикации (T7.78/T7.85). Записывается
    #: в улику (`packages/memory/scope.py`), чтобы более слабое основание было видно оператору
    pairing: str = PAIRING_VALUE_AND_PRIMARY

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


def _measured_value_spans(text: str, values: set[str]) -> list[tuple[int, int]]:
    """Позиции в тексте каждого измеренного числа утверждения (то же сравнение чисел, что и
    `_measured_numbers`): окно публикации привязывается к месту, где значение стоит на странице."""
    return [
        (match.start(), match.end())
        for match in VALUE_NUMBER_PATTERN.finditer(text)
        if _UNIT_SCAN.match(text[match.end() : match.end() + _UNIT_TAIL_CHARS])
        and _canonical_number(match.group()) in values
    ]


def _publication_window(text: str, start: int, end: int) -> tuple[str, int]:
    """Окно второго режима вокруг значения: не дальше `PUBLICATION_WINDOW_CHARS` назад от числа,
    не дальше `PUBLICATION_TAIL_CHARS` вперёд, и не за границу блока (одиночный перевод строки —
    граница блока вёрстки нормализованного текста). Возвращает окно и его начало в тексте: зоны
    вето считаются в координатах окна, а позиции значений — в координатах документа."""
    lower = max(0, start - PUBLICATION_WINDOW_CHARS)
    break_before = text.rfind(_BLOCK_BREAK, 0, start)
    if break_before >= lower:
        lower = break_before + 1
    upper = min(len(text), end + _PUBLICATION_TAIL_CHARS)
    break_after = text.find(_BLOCK_BREAK, end)
    if 0 <= break_after < upper:
        upper = break_after
    return text[lower:upper], lower


def _mask_primary_aliases(zone: str) -> str:
    """Копия зоны вето, где алиасы словаря первоисточников замазаны пробелами (длина та же —
    координаты не плывут). Нужно, чтобы строгое название первоисточника не читалось как
    действующее лицо: «Банк России сохранил оценку» — это первоисточник, а не «банк-актер»,
    «Сбербанка» после замазывания остаётся и опознаётся признаком."""
    chars = list(zone)
    for _key, (_spec, patterns) in _ALIASES.items():
        for pattern in patterns:
            for match in pattern.finditer(zone):
                for index in range(match.start(), min(match.end(), len(chars))):
                    chars[index] = " "
    return "".join(chars)


def _is_dictionary_alias_name(name: str) -> bool:
    """Имя (из кавычек или после «опрос/исследование») опознаётся словарём первоисточников."""
    stripped = name.strip().lower()
    if not stripped:
        return False  # пустое нутро кавычек от замазанного алиаса — не другое имя
    return any(
        any(pattern.search(stripped) for pattern in patterns) for _key, (_spec, patterns) in _ALIASES.items()
    )


def _other_source_sign(zone: str) -> bool:
    """Есть ли в зоне (от конца фразы атрибуции публикации до числа включительно с его
    предложением) хоть один признак ДРУГОГО источника этого числа: действующее лицо, глагол или
    оборот оценки/прогноза, название в кавычках вне словаря, «опрос/исследование» с чужим
    заказчиком. Любой признак — сомнение → приписывание запрещено."""
    masked = _mask_primary_aliases(zone)
    for pattern in OTHER_SOURCE_ACTOR_MARKERS:
        if re.search(pattern, masked, re.IGNORECASE):
            return True
    for pattern in OTHER_SOURCE_ESTIMATION_MARKERS:
        if re.search(pattern, masked, re.IGNORECASE):
            return True
    for match in _QUOTED_NAME.finditer(masked):
        quoted = (match.group(1) if match.group(1) is not None else match.group(2)) or ""
        quoted = quoted.strip()
        if not quoted:
            continue  # в кавычках был алиас словаря (нутро замазано) — это не другое имя
        if not _is_dictionary_alias_name(quoted):
            return True  # название в кавычках вне словаря: например «инФОМ» вне фразы атрибуции
    for survey_pattern in _SURVEY_CUSTOMER:
        for survey_match in survey_pattern.finditer(masked):
            if not _is_dictionary_alias_name(survey_match.group("customer")):
                return True  # «Исследование Ромир показало 14,5%» — чужой заказчик
    return False


def _attribution_regions(text: str) -> list[tuple[str, int, int]]:
    """Позиции пар «шаблон атрибуции + алиас первоисточника» в окне: то же условие pairing,
    что у `_attributed_keys` (зазор не больше `ATTRIBUTION_WINDOW_CHARS`, измеренное число и
    маркер измерения в тексте), но с сохранением позиций — вето должно знать, ГДЕ кончается
    фраза атрибуции. Тот же словарь алиасов и та же дистанция: второе мнение о первоисточнике
    здесь не заводится, отличается только представление (ключ → ключ+координаты)."""
    if not any(pattern.search(text) for pattern in _TEMPLATES):
        return []
    if not VALUE_NUMBER_PATTERN.search(text):
        return []
    if not any(pattern.search(text) for pattern in _MEASUREMENTS):
        return []
    regions: set[tuple[str, int, int]] = set()
    for key, (_spec, patterns) in _ALIASES.items():
        for alias in patterns:
            for alias_match in alias.finditer(text):
                for template in _TEMPLATES:
                    for template_match in template.finditer(text):
                        near = max(
                            template_match.start() - alias_match.end(),
                            alias_match.start() - template_match.end(),
                            0,
                        )
                        if near <= ATTRIBUTION_WINDOW_CHARS:
                            regions.add(
                                (
                                    key,
                                    min(alias_match.start(), template_match.start()),
                                    max(alias_match.end(), template_match.end()),
                                )
                            )
    # детерминированный порядок: по концу фразы (нужен для выбора ближайшей к числу фразы)
    return sorted(regions, key=lambda region: (region[2], region[1], region[0]))


def _sentence_end(text: str, position: int) -> int:
    """Конец предложения, в котором стоит `position` (включая терминальную пунктуацию):
    первое граница-предложение, заканчивающаяся после позиции."""
    for match in _SENTENCE_BREAK.finditer(text):
        if match.end() > position:
            return match.end()
    return len(text)


def _veto_zones(
    window: str, regions: list[tuple[int, int]], value_start: int, value_end: int
) -> list[str]:
    """Зоны вето для одного первоисточника окна (координаты — относительные окна): текст от
    конца каждой фразы атрибуции публикации, закончившейся ДО числа, до конца предложения с
    числом включительно. Проверяются ВСЕ такие фразы: ближайшая к числу «ведёт» это значение,
    но и более ранняя фраза того же первоисточника не имеет права пропускать смену источника
    по дороге к числу. Отдельный случай — пары, начинающиеся у числа или после него («…составила
    14,5%, как сообщил ЦБ»): их зона — остаток собственного предложения. Композитные пары, где
    шаблон атрибуции стоит ближе к чужому обороту («По оценке ВШЭ … Банк России»), тоже дают
    зоны: сама такая склейка подозрительна, а её остаток к числу проверяется на признаки."""
    zones: list[str] = []
    zone_end = _sentence_end(window, value_end)
    preceding = [region for region in regions if region[1] <= value_start]
    if preceding:
        for _region_start, region_end in preceding:
            zones.append(window[region_end:zone_end])
        return zones
    for _region_start, region_end in regions:
        if region_end > value_start:
            zones.append(window[region_end : _sentence_end(window, max(region_end, value_end))])
    return zones


def _publication_decision(
    *, scanned: str, canonical_uri: str | None, values: tuple[str, ...]
) -> ValueAttributionDecision | None:
    """Второй режим pairing (T7.85): страница объявила публикацию первоисточника в предыдущих
    предложениях того же блока, а значение называет позже.

    Условия строже строгого режима по всем остальным пунктам: то же окно «шаблон + алиас»
    (`ATTRIBUTION_WINDOW_CHARS`), то же требование измеренного числа и маркера измерения, то же
    правило «страница первоисточника не бывает его пересказом», тот же отказ при разночтении.
    Дополнительно: в окне обязано быть название публикации первоисточника (`PUBLICATION_MARKERS`),
    и если в одном окне назван хоть один второй первоисточник — решение не принимается вовсе.

    T7.85a добавляет вето «другого источника числа» (`_other_source_sign`): если между концом
    фразы атрибуции публикации и числом — включительно с его предложением — стоит другое
    действующее лицо, глагол или оборот оценки/прогноза, название в кавычках вне словаря или
    «опрос/исследование» с чужим заказчиком, первоисточник не допускается кандидатом на это
    значение. Вето только снимает кандидатов и никогда их не добавляет: разночтение окон
    остаётся отказом даже там, где один из кандидатов был затронут вето."""
    if not values:
        return None  # значения нет: расширять pairing нечем (строгий режим строже)
    candidates_all: dict[str, str] = {}  # кандидаты до вето — прежнее поведение режима (T7.85)
    candidates: dict[str, str] = {}  # кандидаты, пережившие вето «другого источника числа» (T7.85a)
    ambiguity_basis: str | None = None
    for start, end in _measured_value_spans(scanned, set(values)):
        window, window_lower = _publication_window(scanned, start, end)
        if not any(pattern.search(window) for pattern in _PUBLICATIONS):
            continue  # публикация первоисточника не названа: окно не расширяем
        attributed = _attributed_keys(window)
        if not attributed:
            continue
        named = set(named_primary_keys(window))
        if len(named) > 1:
            # в одном окне с значением названы два разных первоисточника — разночтение
            ambiguity_basis = window
            continue
        regions_of_key: dict[str, list[tuple[int, int]]] = {}
        for key, region_start, region_end in _attribution_regions(window):
            if key in attributed:
                regions_of_key.setdefault(key, []).append((region_start, region_end))
        for key, basis in attributed.items():
            if is_home_host(canonical_uri, _home_hosts(key)):
                continue  # страница первоисточника не бывает его пересказом (то же правило)
            candidates_all[key] = basis
            zones = _veto_zones(
                window, regions_of_key.get(key, []), start - window_lower, end - window_lower
            )
            if not any(_other_source_sign(zone) for zone in zones):
                candidates[key] = basis
    if ambiguity_basis is not None:
        return ValueAttributionDecision(
            status=STATUS_AMBIGUOUS,
            basis_fragment=_basis(ambiguity_basis),
            claim_values=values,
            pairing=PAIRING_PUBLICATION_WINDOW,
        )
    if not candidates_all:
        return None
    if len(candidates_all) > 1:
        # разночтение окон остаётся отказом независимо от вето (вето не улучшает решение)
        return ValueAttributionDecision(
            status=STATUS_AMBIGUOUS,
            basis_fragment=_basis(next(iter(candidates_all.values()))),
            claim_values=values,
            pairing=PAIRING_PUBLICATION_WINDOW,
        )
    key = next(iter(candidates_all))
    if key not in candidates:
        return None  # вето сняло единственного первоисточника на всех его occurrences: сомнение — не приписывать
    spec = primary_source(key)
    if spec is None:  # защита: решение не строится на несуществующей записи словаря
        return None
    return ValueAttributionDecision(
        status=STATUS_DERIVATIVE,
        primary_key=spec.key,
        primary_name=spec.name,
        parent_uri=spec.home_uri,
        basis_fragment=_basis(candidates[key]),
        claim_values=values,
        pairing=PAIRING_PUBLICATION_WINDOW,
    )


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

    def fallback(refusal: ValueAttributionDecision) -> ValueAttributionDecision:
        """Честный отказ строгого режима ещё не значит «пересказа нет»: страница могла объявить
        публикацию первоисточника в предыдущих предложениях того же блока. Второй режим
        рассматривается ТОЛЬКО после отказа строгого — строгий режим не ослаблен ни на пункт.

        `own_assessment` и `self_primary` сюда не приходят: первое — вето на весь документ,
        второе уже утверждает, что значение принадлежит прочитанной странице (отрицательный
        контроль T7.85: собственная страница ЦБ по опросу «инФОМ» не становится пересказом)."""
        wider = _publication_decision(
            scanned=scanned, canonical_uri=canonical_uri, values=values
        )
        return refusal if wider is None else wider

    if not attributed:
        return fallback(
            ValueAttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION, claim_values=values)
        )

    # страница самого первоисточника не бывает его пересказом
    candidates = {
        key: basis
        for key, basis in matched.items()
        if not is_home_host(canonical_uri, _home_hosts(key))
    }
    if not candidates:
        if not matched:
            # атрибуции в основании были, но все — про другие числа утверждения
            return fallback(
                ValueAttributionDecision(status=STATUS_VALUE_NOT_ATTRIBUTED, claim_values=values)
            )
        own_only = all(is_home_host(canonical_uri, _home_hosts(key)) for key in matched)
        if own_only:
            return ValueAttributionDecision(
                # значение утверждения приписано самой прочитанной странице — она первоисточник
                status=STATUS_SELF_PRIMARY,
                claim_values=values,
            )
        return fallback(
            ValueAttributionDecision(status=STATUS_VALUE_NOT_ATTRIBUTED, claim_values=values)
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
