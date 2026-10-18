r"""T7.110: защитный тест на буквы-омоглифы в исходниках (расширение T7.103 на код).

T7.102/T7.103 защищали документацию и видимые строки веба. Дефект T7.110 прожил с T7.98
именно потому, что ни одна проверка не смотрела в регулярные шаблоны кода: прежний скан
AGENTS §8 отбрасывал строки с обратной косой чертой (`grep -v '\\'`), а омоглиф сидит внутри
регулярки сразу после `\b`. Этот тест сканирует исходники `apps/`, `packages/`, `hostctl/`
и промпты `prompts/*.md`: в каждой строке, содержащей кириллицу, после вырезания
экранирований (`\xHH`, `\uXXXX`, `\`+буква) и однобуквенных диапазонов внутри квадратных
скобок (`a-z`, `A-Z`, `а-яё` …) ищется латинская буква-двойник (a c e o p x y
A B C E H K M O P T X), стоящая вплотную к кириллической.

Исключения — явный словарь с причиной на каждое; он минимален (см. EXCLUSIONS).
Позитивный контроль (ниже) доказывает, что сканер не пустой: он ловит и прежний дефектный
шаблон глагола ожидания (первая буква после границы слова — латинская o), и «Фаза» из
T7.103 — контроль собран через chr(), чтобы сам файл теста не содержал омоглифа.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

#: корни скана (как в измерении менеджера T7.110)
SCAN_DIRS: tuple[str, ...] = ("apps", "packages", "hostctl")
SCAN_FILES: tuple[str, ...] = tuple(sorted((REPO_ROOT / "prompts").glob("*.md")))

CYRILLIC = re.compile(r"[\u0400-\u052f]")
#: 20 букв-двойников (пары «латинская–кириллическая»: a–а, c–с, e–е, o–о, p–р, x–х, y–у,
#: A–А, B–В, C–С, E–Е, H–Н, K–К, M–М, O–О, P–Р, T–Т, X–Х)
ADJACENT = re.compile(r"[\u0400-\u052f][aceopxyABCEHKMOPTX]|[aceopxyABCEHKMOPTX][\u0400-\u052f]")

#: законные гибридные написания вне дефекта; причина обязательна
EXCLUSIONS: dict[str, str] = {
    # «СберCIB» — гибридное брендовое имя (Сбер + CIB), законное попадание по перечню T7.102
    "СберCIB": "брендовое гибридное имя, законное попадание (перечень T7.102)",
}


def _clean_line(line: str) -> str:
    """Экранирования и диапазоны классов вырезаны; текст в квадратных скобках со пробелом
    классом не считается (прозаическая скобка не сливает слова)."""
    line = re.sub(r"\\x[0-9a-fA-F]{2}", "", line)
    line = re.sub(r"\\u[0-9a-fA-F]{4}", "", line)
    line = re.sub(r"\\[a-zA-Z]", "", line)
    return re.sub(
        r"\[[^\[\] ]*\]",
        lambda m: re.sub(r"[\u0400-\u052fA-Za-z]-[\u0400-\u052fA-Za-z]", "", m.group(0)),
        line,
    )


def _scan() -> list[str]:
    hits: list[str] = []
    files = [p for d in SCAN_DIRS for p in (REPO_ROOT / d).rglob("*") if p.is_file()]
    files += list(SCAN_FILES)
    for path in sorted(files):
        if "__pycache__" in str(path) or path.suffix not in (".py", ".md"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if not CYRILLIC.search(line):
                continue
            cleaned = _clean_line(line)
            for token in EXCLUSIONS:
                cleaned = cleaned.replace(token, "")
            if ADJACENT.search(cleaned):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()[:110]}")
    return hits


def test_sources_have_no_cyrillic_latin_homoglyphs() -> None:
    """Ни одной латинской буквы-двойника вплотную к кириллице в apps/, packages/, hostctl/, prompts/*.md."""
    assert not _scan(), "омоглифы в исходниках (см. список выше) — правка заменой буквы"


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        # сам дефект T7.110 (собран через chr(), чтобы этот файл оставался чистым):
        # латинская o после \b в RELAY_FORECAST_VERBS и в OTHER_SOURCE_ESTIMATION_MARKERS
        ("r\"\\b" + chr(0x6F) + "жида(?:ет|ут|ют|ем|ит|л[а-яё]*)\"", True),
        ("r\"\\b" + chr(0x6F) + "жида(?:ет|ут|ют|ем|ит|л[а-яё]*)\\b\"", True),
        # остаток T7.103: «Фаза» с латинской a внутри
        ("# карточка \u0424" + chr(0x61) + "зы /engineer", True),
        # исправленные формы и законные написания — не краснеют
        (r"r\"\bожида(?:ет|ут|ют|ем|ит|л[а-яё]*)\"", False),
        ("#relay-кандидат — прогноз первоисточника", False),
        ('"[fence-маркер в данных заменён]"', False),
        ('"По\\xa0данным Росстата"', False),
        ('re.compile(r"[А-ЯЁA-Z][А-ЯЁA-Za-zа-яё]*")', False),
        ('"страница СберCIB — пересказ Росстата"', False),
    ],
)
def test_scanner_distinguishes_defect_from_legit_writings(sample: str, expected: bool) -> None:
    cleaned = _clean_line(sample)
    for token in EXCLUSIONS:
        cleaned = cleaned.replace(token, "")
    assert bool(ADJACENT.search(cleaned)) is expected, sample


def test_real_repo_lines_matching_the_original_defect_are_caught() -> None:
    """Прямой контроль красноты: синтетическая «строка файла» с прежним шаблоном скан поймал бы."""
    bad = '        r"\\b' + chr(0x6F) + 'жида(?:ет|ут|ют|ем|ит|л[а-яё]*)",'
    cleaned = _clean_line(bad)
    assert ADJACENT.search(cleaned), "сканер не видит прежний омоглиф — он бесполезен"
