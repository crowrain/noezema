"""Производность источника: пересказ релиза против первоисточника (T7.75, ADR-0029 вариант B).

Хост читает страницу и видит только её текст. Этот модуль отвечает на один вопрос: называет ли
страница **первоисточник** вместе со значением — «по данным Росстата инфляция составила 5,59%»,
«как сообщил Банк России, ставка равна 21%», "according to Rosstat, inflation reached 5.59%".
Если называет, страница — пересказ, а не второе независимое наблюдение; если она приводит свою
оценку или спорящее значение, она самостоятельна и склеивать её нельзя.

Логика держится чистой функцией намеренно (AGENTS §4, образец —
`apps/orchestrator/search_view.py`): ни БД, ни сети, ни сессии; вход — canonical URI прочитанной
страницы и её нормализованный текст, выход — решение со статусом и фрагментом-основанием. Решает
только хост: модель к производности не допускается: оценки назначает rules engine, а не модель.

Консервативность — главное свойство (ADR-0029 «Риски»: ложное слияние занижает независимость и
роняет честные работы до E1, пропуск производности не хуже нынешнего состояния). Поэтому
отметка ставится лишь при собранной паре «значение + первоисточник» в одном фрагменте, а любое
сомнение (своя оценка, два разных первоисточника, страница сама является первоисточником) —
отказ. Разворачивание решения в записи БД делает `apps/research_proxy/service.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from packages.domain.sanitization import mask_nul

#: версия метода: попадает в metadata источника и в аудит, чтобы решение можно было
#: отличить от решения другой версии детектора (и от человеческой коррекции).
#: v2 (T7.77): сканирование по копии текста с нормализованной типографикой — v1 не
#: распознавал неразрывные пробелы внутри шаблонов и алиасов, мягкий перенос внутри
#: слова и перевод строки внутри фразы (факт подставки: «По\xa0данным Росстата» на
#: sbercib.ru остался no_value_attribution). Это различие версий использует
#: переатрибуция (`apps/research_proxy/reattribution.py`).
ATTRIBUTION_METHOD_VERSION: Final = "host-source-attribution-v2"

#: статусы решения. `derivative` — единственный, при котором хост ставит указатель;
#: остальные четыре означают «не помечаем» и записываются в аудит как честный отказ.
STATUS_DERIVATIVE: Final = "derivative"
STATUS_OWN_ASSESSMENT: Final = "own_assessment"
STATUS_AMBIGUOUS: Final = "ambiguous_primaries"
STATUS_NO_VALUE_ATTRIBUTION: Final = "no_value_attribution"
STATUS_SELF_PRIMARY: Final = "self_primary"

NEGATIVE_STATUSES: Final[tuple[str, ...]] = (
    STATUS_OWN_ASSESSMENT,
    STATUS_AMBIGUOUS,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_SELF_PRIMARY,
)

#: детектор читает текст страницы, а не весь артефакт: скан ограничен, чтобы решение
#: зависело от начала документа, а не от длины (детерминизм важнее полноты)
MAX_SCAN_CHARS: Final = 40_000
#: сколько фрагмента-основания сохраняется в provenance и показывается оператору
BASIS_FRAGMENT_CHARS: Final = 300
#: максимальный зазор между шаблоном атрибуции и алиасом первоисточника внутри фрагмента:
#: «по данным <алиас>», «<алиас> сообщает» — это соседние слова, а не разные предложения
ATTRIBUTION_WINDOW_CHARS: Final = 120

#: T7.77: живая вёрстка набирает словесные промежутки неразрывными и узкими пробелами,
#: рвёт слова мягким переносом и ZW-символами. Скан идёт по копии текста с приведённой
#: типографикой; хранимый текст страницы и его хеш не меняются (ADR-0029: детектор читает
#: копию, провенанс хранит подлинник). Отображение фрагмента-основания согласовано с v1:
#: `_basis` уже схлопывал пробелы и переводил переводы строк в обычные.
_SCAN_TRANSLATE: Final[dict[int, str | None]] = {
    **dict.fromkeys(range(0x2000, 0x200B), " "),  # en…hair spaces, figure and thin space
    0x00A0: " ",  # NO-BREAK SPACE — виновник стендового пропуска (sbercib.ru)
    0x202F: " ",  # narrow NBSP
    0x205F: " ",  # medium mathematical space
    # невидимая типографика удаляется: софт-гифен (перенос слова), ZWSP/ZWNJ/ZWJ, word joiner, BOM
    **dict.fromkeys((0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF), None),
}
_CONSECUTIVE_SPACES: Final = re.compile(r" {2,}")


def normalize_scan_text(text: str) -> str:
    """Копия текста для сканирования: типографические пробелы становятся обычными,
    невидимые разделители слов удаляются, идущие подряд пробелы схлопываются в один.
    Нормализация применяется ровно один раз — на входе детектора."""
    collapsed = _CONSECUTIVE_SPACES.sub(" ", text.translate(_SCAN_TRANSLATE))
    # перенос строки остаётся видимым: это граница строки документа, а не межсловный промежуток
    return re.sub(r"[ \t]+(\r?\n)[ \t]+", r"\1", collapsed)


@dataclass(frozen=True)
class PrimarySource:
    """Одна запись словаря первоисточников."""

    key: str
    name: str
    home_uri: str
    #: домашние хосты первоисточника (сравниваются как хост, не как registrable domain —
    #: PSL-список `packages/memory/independence.py` coarse: *.gov.ru даёт один registrable
    #: domain "gov.ru", и по нему Росстат, Минфин и ФНС стали бы «одним сайтом»)
    home_hosts: tuple[str, ...]
    alias_patterns: tuple[str, ...]


#: минимальный расширяемый словарь первоисточников. Новая запись = один элемент кортежа;
#: тест `test_registry_is_wellformed_and_extensible` проверяет самосогласованность записи:
#: домашний URI принадлежит домашнему хосту, алиасы соответствуют имени, шаблон не голый.
PRIMARY_SOURCES: Final[tuple[PrimarySource, ...]] = (
    PrimarySource(
        key="rosstat",
        name="Росстат",
        home_uri="https://rosstat.gov.ru/",
        home_hosts=("rosstat.gov.ru",),
        alias_patterns=(
            r"росстат[а-яё]*",
            r"федеральн[а-яё]* служб[а-яё]* государственн[а-яё]* статистик[а-яё]*",
            r"\brosstat\b",
            r"federal state statistics service",
            r"rosstat\.gov\.ru",
        ),
    ),
    PrimarySource(
        key="cbr",
        name="Банк России",
        home_uri="https://cbr.ru/",
        home_hosts=("cbr.ru",),
        alias_patterns=(
            r"банк[а-яё]* росс[а-яё]*",
            r"центр[а-яё]* банк[а-яё]* росс[а-яё]*",
            r"\bцб\s*рф\b",
            r"центробанк[а-яё]*",
            r"bank of russia",
            r"central bank of the russian federation",
            r"cbr\.ru",
        ),
    ),
    PrimarySource(
        key="minfin",
        name="Минфин России",
        home_uri="https://minfin.gov.ru/",
        home_hosts=("minfin.gov.ru",),
        alias_patterns=(
            r"минфин[а-яё]*",
            r"министерств[а-яё]* финансов[а-яё]* росс[а-яё]*",
            r"министерств[а-яё]* финансов[а-яё]* рф",
            r"ministry of finance of the russian federation",
            r"minfin\.gov\.ru",
        ),
    ),
    PrimarySource(
        key="fns",
        name="ФНС России",
        home_uri="https://www.nalog.gov.ru/",
        home_hosts=("nalog.gov.ru",),
        alias_patterns=(
            r"\bфнс[а-яё]*",
            r"федеральн[а-яё]* налог[а-яё]* служб[а-яё]*",
            r"federal tax service",
            r"nalog\.gov\.ru",
        ),
    ),
)

#: шаблоны указания на первоисточник: страница сама говорит, откуда взято значение
ATTRIBUTION_TEMPLATES: Final[tuple[str, ...]] = (
    r"по данным",
    r"по информации",
    r"по сообщени[а-яё]*",
    r"сообща[а-яё]*",
    r"как сообщил[а-яё]*",
    r"как сообщает",
    r"согласно",
    r"со ссылкой на",
    r"со слов",
    r"цитиру[а-яё]*",
    r"ссылка[а-яё]* на",
    r"по оценк[а-яё]*",
    r"релиз[а-яё]*",
    r"according to",
    r"as reported by",
    r"per data from",
    r"data from",
    r"citing",
    r"cites",
    r"statement from",
)

#: маркеры значения: единица измерения или глагол-атрибут. Голое число (год, номер статьи,
#: дата публикации) значением не считается — пара обязана быть «значение + первоисточник»
MEASUREMENT_MARKERS: Final[tuple[str, ...]] = (
    r"%",
    r"\bпроцент[а-яё]*",
    r"\bп\.?п\.",
    r"\bмлрд\b",
    r"\bмлн\b",
    r"\bтыс[а-яё]*",
    r"\bрубл[а-яё]*",
    r"\bдоллар[а-яё]*",
    r"\bевро\b",
    r"\bpercent\w*",
    r"\brose\b",
    r"\bfell\b",
    r"\bincreas\w*",
    r"\bdecreas\w*",
    r"\bstood at",
    r"\breached",
    r"\bamounted to",
    r"\bcame to",
    r"\brecorded",
    r"\bсоставл[а-яё]*",
    r"\bвырос[а-яё]*",
    r"\bувелич[а-яё]*",
    r"\bсниз[а-яё]*",
    r"\bповыс[а-яё]*",
    r"\bопустил[а-яё]*",
    r"\bподнял[а-яё]*",
    r"\bупал[а-яё]*",
    r"\bсократ[а-яё]*",
    r"\bпревысил[а-яё]*",
    r"\bдостиг[а-яё]*",
)

#: маркеры собственной оценки/альтернативного расчёта: они отвергают производность всей страницы.
#: Список намеренно широкий — ложный пропуск безопаснее ложной склейки (ADR-0029 «Риски»)
OWN_ASSESSMENT_MARKERS: Final[tuple[str, ...]] = (
    r"по нашим расч[а-яё]*",
    r"по нашей оценк[а-яё]*",
    r"наш[а-яё]* расч[а-яё]*",
    r"наш[а-яё]* оценк[а-яё]*",
    r"наш[а-яё]* методик[а-яё]*",
    r"мы оцениваем",
    r"независим[а-яё]* оценк[а-яё]*",
    r"независим[а-яё]* расч[а-яё]*",
    r"самостоятельн[а-яё]* оценк[а-яё]*",
    r"оценка редакции",
    r"в отличие от",
    r"расхождени[а-яё]*",
    r"не совпада[а-яё]*",
    r"противореч[а-яё]*",
    r"our own estimate",
    r"by our estimate",
    r"we estimate",
    r"independent estimate",
    r"in contrast to",
    r"contradict\w*",
    r"disagree\w*",
    r"differs from",
)

VALUE_NUMBER_PATTERN: Final = re.compile(r"\d[\d.,\u00a0\s]{0,15}\d|\d+")

#: фрагмент = предложение (или строка нормализованного текста): пара «значение + первоисточник»
#  должна собраться внутри одного фрагмента. T7.77: жёсткие границы — окончания предложения
#  и пустая строка (две подряд — граница блока); одиночный перевод строки — типографический
#  перенос или склейка HTML-блоков, он фразу не рвёт (пара по-прежнему ограничена окном
#  ATTRIBUTION_WINDOW_CHARS, а вето собственной оценки действует на уровне документа).
_FRAGMENT_SPLIT: Final = re.compile(r"([.!?\u2026]+|\n{2,}|\n)")


def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_ALIASES: Final[dict[str, tuple[PrimarySource, tuple[re.Pattern[str], ...]]]] = {
    spec.key: (spec, _compiled(spec.alias_patterns)) for spec in PRIMARY_SOURCES
}
_TEMPLATES: Final[tuple[re.Pattern[str], ...]] = _compiled(ATTRIBUTION_TEMPLATES)
_MEASUREMENTS: Final[tuple[re.Pattern[str], ...]] = _compiled(MEASUREMENT_MARKERS)
_OWN_ASSESSMENT: Final[tuple[re.Pattern[str], ...]] = _compiled(OWN_ASSESSMENT_MARKERS)


@dataclass(frozen=True)
class AttributionDecision:
    """Что хост понял о происхождении прочитанной страницы."""

    status: str
    primary_key: str | None = None
    primary_name: str | None = None
    parent_uri: str | None = None
    #: фрагмент документа, на котором основано решение (маскированный, обрезанный); пусто,
    #: когда ничего не найдено
    basis_fragment: str = ""

    @property
    def is_derivative(self) -> bool:
        return self.status == STATUS_DERIVATIVE


def primary_source(key: str) -> PrimarySource | None:
    """Запись словаря по ключу — нужно продюсеру указателя (`apps/research_proxy/service.py`)."""
    entry = _ALIASES.get(key)
    return None if entry is None else entry[0]


def host_of(uri: str | None) -> str | None:
    """Хост canonical URI страницы (без порта), либо None."""
    if not uri:
        return None
    try:
        host = urlsplit(uri).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().removeprefix("www.")


def is_home_host(uri: str | None, home_hosts: tuple[str, ...]) -> bool:
    """Страница принадлежит первоисточнику: хост равен домашнему или является его поддоменом."""
    host = host_of(uri)
    if host is None:
        return False
    return any(host == home or host.endswith(f".{home}") for home in home_hosts)


def _fragments(text: str) -> list[str]:
    """Фрагменты сканируемого текста. Разделители с окончанием предложения (или пустая
    строка) закрывают фрагмент; одиночный перевод строки склеивает соседние куски —
    типографический перенос внутри фразы не разрывает пару «шаблон + алиас» (T7.77)."""
    tokens = _FRAGMENT_SPLIT.split(text)
    fragments: list[str] = []
    current = ""
    for index in range(0, len(tokens), 2):
        piece = tokens[index]
        current = f"{current} {piece}" if current else piece
        separator = tokens[index + 1] if index + 1 < len(tokens) else ""
        hard_break = separator == "" or any(char in ".!?\u2026" for char in separator) or "\n\n" in separator
        if hard_break:
            if current.strip():
                fragments.append(current.strip())
            current = ""
    return fragments


def _basis(fragment: str) -> str:
    """Фрагмент-основание для записи: одна строка, NUL маскируется, литералы fence'а
    нейтрализуются (§11.2: чужой текст не должен уметь подделывать границу данных)."""
    text = mask_nul(fragment).replace("\r", " ").replace("\n", " ")
    for marker in ("<<<UNTRUSTED DATA BEGIN>>>", "<<<UNTRUSTED DATA END>>>"):
        text = text.replace(marker, "[fence-маркер в данных заменён]")
    return " ".join(text.split())[:BASIS_FRAGMENT_CHARS]


def _attributed_keys(fragment: str) -> dict[str, str]:
    """Ключи первоисточников, которым в этом фрагменте присвоено значение."""
    templates = [m for m in (t.search(fragment) for t in _TEMPLATES) if m is not None]
    if not templates:
        return {}
    if not VALUE_NUMBER_PATTERN.search(fragment):
        return {}
    if not any(m.search(fragment) for m in _MEASUREMENTS):
        return {}
    found: dict[str, str] = {}
    for key, (_spec, patterns) in _ALIASES.items():
        for alias in patterns:
            match = alias.search(fragment)
            if match is None:
                continue
            near = any(
                max(t.start() - match.end(), match.start() - t.end(), 0) <= ATTRIBUTION_WINDOW_CHARS
                for t in templates
            )
            if near:
                found[key] = fragment
                break
    return found


def detect_source_attribution(*, canonical_uri: str | None, text: str | None) -> AttributionDecision:
    """Доказана ли производность прочитанной страницы и от кого именно она пересказывает.

    `text` — нормализованный текст страницы (то, что хост вообще видит); `canonical_uri` — её
    финальный URL. Возвращает одно из: `derivative` (+ первоисточник и фрагмент-основание),
    `own_assessment`, `ambiguous_primaries`, `no_value_attribution`, `self_primary`.
    """
    if not text:
        return AttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION)

    # T7.77: скан идёт по копии текста с нормализованной типографикой (один раз на входе);
    # потолок скана применяется к копии — решение зависит от начала документа, а не от длины
    scanned = normalize_scan_text(text)[:MAX_SCAN_CHARS]
    if any(pattern.search(scanned) for pattern in _OWN_ASSESSMENT):
        # своя оценка/спорящий расчёт: страница — самостоятельное исследование, склейка запрещена
        return AttributionDecision(status=STATUS_OWN_ASSESSMENT)

    matches: dict[str, str] = {}
    for fragment in _fragments(scanned):
        for key, basis in _attributed_keys(fragment).items():
            matches.setdefault(key, basis)

    if not matches:
        return AttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION)

    # страница не может быть пересказом самой себя
    candidates = {
        key: basis
        for key, basis in matches.items()
        if not is_home_host(canonical_uri, _ALIASES[key][0].home_hosts)
    }
    if not candidates:
        return AttributionDecision(status=STATUS_SELF_PRIMARY)

    if len(candidates) > 1:
        # значению страницы присвоили разные первоисточники: это разночтение, а не один пересказ
        return AttributionDecision(
            status=STATUS_AMBIGUOUS,
            basis_fragment=_basis(next(iter(candidates.values()))),
        )

    key, basis = next(iter(candidates.items()))
    spec = _ALIASES[key][0]
    return AttributionDecision(
        status=STATUS_DERIVATIVE,
        primary_key=spec.key,
        primary_name=spec.name,
        parent_uri=spec.home_uri,
        basis_fragment=_basis(basis),
    )
