r"""T7.126a (часть д4): усиленный защитный тест на смешанные слова кириллица↔латиница.

Что сканирует (шире T7.110, прежнего не ослабляет):

(а) те же корни, что у `tests/unit/test_homoglyph_source_scan_t7110.py`: `apps/`,
    `packages/`, `hostctl/` и промпты. Промпты здесь — во вложенных каталогах
    (`prompts/**/*.md`): у T7.110 шаблон `prompts/*.md` не матчит ни одного файла (все 30
    промптов лежат в `prompts/<роль>/`), то есть его проверка промптов была пустой;
(б) дополнительно документация, которую читает человек: `README.md`, `AGENTS.md`,
    `docs/adr/`, `deploy/`.

Вне скана (OUT_OF_SCOPE, причина на каждый путь): `tests/fixtures/**` — дословные цитаты
страниц источника, в них подлинные опечатки самих сайтов; `docs/STATUS-archive.md` —
заморожен байт в байт (переносы разделов T7.42 и T7.122); `docs/eval/**` — подлинные
записи прогонов модели; `docs/STATUS.md` — активный журнал: его строки-ссылки содержат
якоря markdown к замороженным заголовкам архива и дословные цитаты разобранных дефектов,
замена буквы в них невозможна, поэтому STATUS.md остаётся зоной ручного скана AGENTS §7
(тот же шаблон); `ARCHITECTURE.md` и `docs/PLAN_FROM_SCRATCH.md` — менять их можно только
по решению пользователя (AGENTS §4), правка заменой буквы в области этой задачи не разрешена.

Правило: ЛЮБОЕ слово (максимальный бег букв кириллицы и латиницы), в котором кириллическая
буква стоит вплотную к латинской или наоборот — шаблон по кириллице и латинице на слове, а
не только 20 букв-двойников T7.110: «monotонно» (латинская n) и «verifiable»-тип (f, v, i)
прежний скан не видел. Перед проверкой из строки вырезаны экранирования (шестнадцатеричные
и `\`+буква) и однобуквенные диапазоны классов (`a-z`, `A-Z`, `а-яё`) — той же функцией
`_clean_line`, что и у T7.110, поэтому дефект «сразу за границей слова» (урок T7.110)
остаётся видимым.

Исключения — закрытый список EXCLUSIONS с комментарием-обоснованием на каждое попадание.
Молча ослаблять проверку нельзя: новое попадание либо исправляется заменой буквы, либо
заносится в список с причиной. Тест краснеет и на бесполезное исключение
(`test_exclusions_are_live`), чтобы список не превращался в склад отговорок; позитивный
контроль ниже собран через chr(), чтобы сам файл теста не содержал смешанного слова.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.scenario import test_web_homoglyphs_t7103 as t7103
from tests.unit import test_homoglyph_source_scan_t7110 as t7110

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

#: корни скана: прежние (T7.110) + документация, которую читает человек
SCAN_DIRS: tuple[str, ...] = ("apps", "packages", "hostctl", "prompts", "docs/adr", "deploy")
SCAN_FILES: tuple[str, ...] = ("README.md", "AGENTS.md")

#: текстовые файлы этих каталогов (пайк-кеши и мипи-кеши отсекаются отдельно)
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".md", ".txt", ".sh", ".json", ".yaml", ".yml", ".service", ".timer", ".target"}
)

#: пути вне скана — с причиной на каждый (подробности в docstring)
OUT_OF_SCOPE: dict[str, str] = {
    "tests/fixtures": "дословные цитаты страниц источника: подлинные опечатки самих сайтов",
    "docs/STATUS-archive.md": "заморожен байт в байт (переносы разделов T7.42 и T7.122)",
    "docs/eval": "подлинные записи прогонов модели (перечень T7.102)",
    "docs/STATUS.md": "активный журнал: якоря к замороженным заголовкам архива и дословные "
    "цитаты разобранных дефектов; остаётся зоной ручного скана AGENTS §7",
    "ARCHITECTURE.md": "правка — только по решению пользователя (AGENTS §4)",
    "docs/PLAN_FROM_SCRATCH.md": "правка вне разрешённой области задачи (AGENTS §1, §4)",
}

CYRILLIC = t7110.CYRILLIC
#: ЛЮБАЯ латинская буква вплотную к кириллической (или наоборот): надмножество 20
#: букв-двойников `t7110.ADJACENT`.
ADJACENT_ANY = re.compile(r"[\u0400-\u052f][A-Za-z]|[A-Za-z][\u0400-\u052f]")
#: слово = максимальный бег букв кириллицы и латиницы (цифра, дефис и кавычка слова не сливают)
WORD_RE = re.compile(r"[A-Za-z\u0400-\u052f]+")

#: Закрытый список законных смешанных написаний: причина обязательна на каждое имя.
EXCLUSIONS: dict[str, str] = {
    # гибридный термин «без даты»: так он назван в ADR-0018 и ADR-0033, в комментариях
    # оркестратора и в перечне законных попаданий T7.102
    "датless": "гибридный термин «без даты» из перечня законных попаданий T7.102",
    # брендовое гибридное имя производителя (SberCIB — инвестиционное исследование Сбера);
    # в фикстурах этим же именем названы страницы источника
    "СберCIB": "брендовое гибридное имя производителя (перечень T7.102, исключение T7.110)",
    # тот же бренд в литерале команды скана AGENTS §7 (`grep -viE "сберcib"`): регистр задан
    # флагом -i, правка буквы смысл фильтра испортит
    "сберcib": "литерал фильтра команды скана AGENTS §7 (тот же бренд, нижний регистр -i)",
    # пропуск пробела между русским словом и именем признака: AGENTS §7 прямо называет
    # пропуски пробелов не гомоглифами — замена буквы к ним неприменима
    "артефактfail": "пропуск пробела (AGENTS §7: это не гомоглиф), а не двойник буквы",
    # гибридные термины того же перечня T7.102: латинская основа плюс русская флексия
    "propagateция": "гибридный термин из перечня законных попаданий T7.102",
    "deriviруется": "гибридный термин из перечня законных попаданий T7.102",
    # гибрид от имени инструмента `research.fetch`, названный в перечне T7.102
    "fetchом": "гибридная флексия имени инструмента fetch (перечень T7.102)",
    # дословно цитируемые в AGENTS §7 примеры дефектного написания: раздел о гомоглифах
    # показывает дефект на живом слове, замена буквы уничтожила бы пример
    "группa": "цитата примера дефекта из AGENTS §7 (раздел о гомоглифах, T7.102)",
    "pин": "цитата примера дефекта из AGENTS §7 (раздел о гомоглифах, T7.102)",
    "сweep": "цитата примера дефекта из AGENTS §7 (раздел о гомоглифах, T7.102)",
    "fоновая": "цитата примера дефекта из AGENTS §7 (раздел о гомоглифах, T7.102)",
}


def _text_files() -> list[Path]:
    """Текстовые файлы области скана: отбор по суффиксу, кеши переводчиков отсечены."""
    found: list[Path] = []
    for rel in SCAN_DIRS:
        root = REPO_ROOT / rel
        if not root.is_dir():
            raise AssertionError(f"корень скана исчез: {rel}")
        found.extend(p for p in root.rglob("*") if p.is_file())
    for rel in SCAN_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            raise AssertionError(f"файл скана исчез: {rel}")
        found.append(path)
    return sorted({p for p in found if p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts})


def _scanned_lines() -> list[tuple[str, int, str]]:
    """(относительный путь, номер строки, строка) по всей области скана."""
    out: list[tuple[str, int, str]] = []
    for path in _text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        out.extend((rel, n, line) for n, line in enumerate(text.splitlines(), 1))
    return out


def _mixed_words(line: str) -> list[str]:
    """Смешанные слова строки: кириллица вплотную к латинице, после вырезания экранирований."""
    if not CYRILLIC.search(line):
        return []
    cleaned = t7110._clean_line(line)
    return [word for word in WORD_RE.findall(cleaned) if ADJACENT_ANY.search(word)]


def _offenders(line: str) -> list[str]:
    """Смешанные слова строки за пределами закрытого списка исключений."""
    return [word for word in _mixed_words(line) if word not in EXCLUSIONS]


def _scan() -> list[str]:
    hits: list[str] = []
    for rel, n, line in _scanned_lines():
        hits.extend(f"{rel}:{n}: «{word}»" for word in _offenders(line))
    return hits


def test_repo_text_has_no_mixed_cyrillic_latin_words() -> None:
    """Ни одного смешанного слова в коде, промптах и документации вне закрытого списка."""
    hits = _scan()
    assert not hits, (
        "смешанные кириллица↔латиница слова: правка заменой буквы либо исключение с "
        "причиной; находки: " + "; ".join(hits)
    )


def test_scan_scope_is_not_empty() -> None:
    """Проверка не должна стать пустой: каждый корень жив, промпты матчатся по-настоящему."""
    files = _text_files()
    counts = dict.fromkeys(SCAN_DIRS, 0)
    for path in files:
        parts = path.relative_to(REPO_ROOT).parts
        for root in SCAN_DIRS:
            if parts[: len(root.split("/"))] == tuple(root.split("/")):
                counts[root] += 1
    for root, count in counts.items():
        assert count > 0, f"корень скана не дал ни одного файла: {root}"
    # находка T7.126a: у T7.110 было `prompts/*.md`, что не матчит ни одного файла этого репозитория
    assert counts["prompts"] >= 20, counts
    assert len(files) >= 200, f"область скана подозрительно мала: {len(files)} файлов"


def test_exclusions_are_justified() -> None:
    """У каждого исключения обязано быть содержательное обоснование и смешанное слово."""
    assert EXCLUSIONS, "список исключений пуст — значит, он не описывает реальные находки"
    for token, reason in EXCLUSIONS.items():
        assert _mixed_words(token), f"исключение {token!r} не является смешанным словом"
        assert len(reason) >= 30, f"исключение {token!r} без обоснования: {reason!r}"


def test_exclusions_are_live() -> None:
    """Каждое исключение действительно встречается в области скана: склад отговорок красен."""
    seen = {word for _rel, _n, line in _scanned_lines() for word in _mixed_words(line)}
    dead = sorted(set(EXCLUSIONS) - seen)
    assert not dead, "мёртвые исключения (их в репозитории больше нет) — удалить: " + ", ".join(dead)


def test_scan_is_not_weaker_than_t7110() -> None:
    """Прежнее покрытие не ослаблено: прежние корни и прежние исключения остались, и любую строку,
    которую красит узкий шаблон T7.110, красит и широкий шаблон этого теста."""
    assert set(t7110.SCAN_DIRS) <= set(SCAN_DIRS), t7110.SCAN_DIRS
    missing = set(t7110.EXCLUSIONS) - set(EXCLUSIONS)
    assert not missing, f"исключение T7.110 пропало из нового списка: {missing}"
    assert not t7110._scan(), "прежний тест T7.110 покраснел — ослаблять его молча нельзя"
    for rel, n, line in _scanned_lines():
        narrow = t7110._clean_line(line)
        for token in t7110.EXCLUSIONS:
            narrow = narrow.replace(token, "")
        if t7110.ADJACENT.search(narrow):
            assert _mixed_words(line), (
                f"{rel}:{n}: T7.110 видит омоглиф, а новый скан — нет: {line.strip()[:110]}"
            )


def test_web_label_test_still_has_no_allowed_hybrids() -> None:
    """T7.103 не ослаблен: его список законных гибридов по-прежнему пуст."""
    assert t7103._ALLOWED_HYBRIDS == {}, t7103._ALLOWED_HYBRIDS


def test_broad_rule_is_wider_than_the_narrow_one() -> None:
    """Усиление доказано: смежность не-двойников (латинская n) прежний скан T7.110 не видел."""
    sample = "monot" + chr(0x043E) + "нно"
    assert not t7110.ADJACENT.search(t7110._clean_line(sample)), "прежний шаблон это уже видит"
    assert _offenders(sample) == [sample], sample


#: образцы собраны через chr(): файл теста не должен сам содержать смешанное слово
_VERIFIABLE = chr(0x0432) + chr(0x0435) + chr(0x0440) + "ifiable"
_OZHIDA = chr(0x6F) + "жида"
_FAZA = chr(0x0424) + chr(0x61) + "зы"
_DATLESS = chr(0x0434) + chr(0x0430) + chr(0x0442) + "less"
_SBERCIB = chr(0x0421) + "бер" + "CIB"


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        # дефект T7.126 packages/domain/models/memory.py:374 (русское «вер» + латиница)
        (f"#: {_VERIFIABLE} basis отзыва", [_VERIFIABLE]),
        # дефект T7.110: латинская o сразу за границей слова в регулярке глагола ожидания
        (rf'        r"\b{_OZHIDA}(?:ет|ут|ют)",', [_OZHIDA]),
        # остаток T7.103: «Фаза» с латинской a внутри (видимая строка веба)
        (f"# карточка {_FAZA} /engineer", [_FAZA]),
        # исправленные написания этого же места: красноты нет
        ("#: проверяемое основание отзыва: вердикт гейта значения", []),
        ("# карточка Фаза /engineer", []),
        # законные написания: чисто кириллические термины
        ("монотонность и материализованный список — смешения букв нет", []),
        # законные гибриды закрытого списка: смешанное слово есть, находки нет
        (f"перепроверка {_DATLESS}-операции", []),
        (f"страница {_SBERCIB} — пересказ Росстата", []),
    ],
)
def test_scanner_distinguishes_defect_from_legit_writings(sample: str, expected: list[str]) -> None:
    assert _offenders(sample) == expected, sample
