"""T7.39: prompt examples must not carry real run data.

T7.38 acceptance: the reverify example of curator-v5 used a REAL claim
id (``35d5b7ae-…``, SMOKE-V8-K2) and that run's real fact and ``as_of``.
On the next smoke on a FRESH DB the host would reject the copied
reference and — T7.34 — an unresolved reference rejects the WHOLE
proposal: gate 5 reads 0 again, and "the model does not use reverify"
is indistinguishable from "the model copied the example".

General protection (any future prompt version is covered):

1. no full UUID that appears in ANY prompt file (curator AND explorer,
   T7.50) may also appear in ``docs/`` — the reports and analyses there
   contain the real run ids, so an intersection is a leaked real id.
   Sole exception: curator-v5, frozen (content-pinned by config-v9)
   with its known leak pinned to the exact (uuid, doc) pairs — see
   ``FROZEN_KNOWN_LEAKS``;
2. the reverify example statements of every curator prompt (ALL
   ```` ```json ```` blocks parsed out of the prompt) must not occur in
   any question-set corpus — if the example were a corpus question's
   fact, the model could recognize the example in its own question and
   copy the example's ids/``as_of``. T7.43: rule 7 carries a second
   (negative) JSON example, so EVERY block is checked, not just the
   first; the ids inside the examples are full UUIDs of the prompt and
   are covered by check 1 (prompt UUIDs ∩ docs UUIDs = ∅);
3. T7.50: the explorer completion examples (the positive and the
   negative pair of explorer-v5) carry free text a model can match its
   own question against: every free-text field of every JSON example
   (``public_rationale``, ``expected_information``, the free-text
   ``decision.reason`` of the negative example) and the example topic
   itself must not occur in any question-set corpus (v1–v4 in the
   repo, the smoke set when present).

4. T7.97a: свободный ТЕКСТ ПРАВИЛ промпта нынешнего payload не имеет права
   воспроизводить реальный случай (имя актора или связку «значение +
   период»), которым этот промпт будут проверять: пример-копия делает
   исполнение правила неотличимым от переписывания примера. Критерий,
   его обоснование и область действия — в разделе «T7.97a» ниже.
   T7.102: у нынешнего curator-промпта (ныне curator-v12, пин config-v22)
   унаследованных исключений нет — пример перефразы правила 7 заменён
   вымышленным случаем той же формы; запись для curator-v11 остаётся как
   история выпущенного пина config-v21.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"
DOCS_DIR = REPO_ROOT / "docs"
# the smoke corpus lives outside the repo (run working directory) —
# checked when present
SMOKE_CORPUS = REPO_ROOT.parent / "smoke-v8" / "question-set-smoke.jsonl"

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)

# T7.38 acceptance: the ONLY frozen historical exception. curator-v5 is
# content-pinned by config-v9 (payloads and historical prompts are
# never rewritten), and its example UUID is the real SMOKE-V8-K2 claim
# id quoted in docs/eval/SMOKE-V8-K2-report.md — precisely the leak
# T7.39 fixed in curator-v6. The exception is pinned to the exact
# (uuid, doc) pairs: any drift (a new leaked uuid, or the docs losing
# the quote) fails the test instead of being silently absorbed.
FROZEN_KNOWN_LEAKS: dict[str, set[tuple[str, str]]] = {
    "curator-v5.md": {
        ("35d5b7ae-4fad-4327-ac38-c26f0eea4306", "docs/eval/SMOKE-V8-K2-report.md"),
    },
}


def _uuids(text: str) -> set[str]:
    return {u.lower() for u in UUID_RE.findall(text)}


def _curator_prompts() -> list[Path]:
    files = sorted((PROMPTS_DIR / "curator").glob("curator-v*.md"))
    assert files, "no curator prompt versions found"
    return files


def _explorer_prompts() -> list[Path]:
    files = sorted((PROMPTS_DIR / "explorer").glob("explorer-v*.md"))
    assert files, "no explorer prompt versions found"
    return files


def _all_prompts() -> list[Path]:
    """T7.50: the guard covers every prompt role — the curator examples
    carry claim ids, the explorer completion examples carry free text;
    both are model-matchable run data."""
    return _curator_prompts() + _explorer_prompts()


def _corpora() -> list[Path]:
    corpora = sorted((DOCS_DIR / "eval").glob("question-set-*.jsonl"))
    assert corpora, "no question-set corpora found in docs/eval"
    if SMOKE_CORPUS.is_file():
        corpora.append(SMOKE_CORPUS)
    return corpora


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _all_prompts(), ids=lambda p: p.name)
def test_prompt_uuids_do_not_intersect_docs(prompt_file: Path) -> None:
    """A prompt example UUID that also appears in docs/ is a leaked
    real run id: on a DB where that claim does not exist the host
    rejects the copied reference (fail-closed, T7.34). T7.50: the
    explorer files are checked too (explorer-v5's examples carry no
    UUIDs — the check passes and pins that for the future)."""
    prompt_uuids = _uuids(prompt_file.read_text(encoding="utf-8"))
    leaked: set[tuple[str, str]] = set()
    for doc in sorted(DOCS_DIR.rglob("*")):
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for uuid in prompt_uuids & _uuids(text):
            leaked.add((uuid, doc.relative_to(REPO_ROOT).as_posix()))
    expected = FROZEN_KNOWN_LEAKS.get(prompt_file.name, set())
    assert leaked == expected, (
        f"{prompt_file.name}: example UUIDs present in docs "
        f"(leaked real run ids): found {sorted(leaked)}, "
        f"documented exception {sorted(expected)}"
    )


def _example_blocks(text: str) -> list[dict]:
    """ALL ```` ```json ```` blocks of the prompt, in file order. T7.43:
    rule 7 carries two examples (the positive reverify and the negative
    ``existing_claim_id: null`` one), so EVERY block is checked —
    ``re.search`` would return the first only."""
    return [json.loads(block) for block in EXAMPLE_JSON.findall(text)]


def _rule7_section(text: str) -> str:
    """The reverify rule (``7. Перепроверка``) — the last rule of the
    prompt; its example ids live in the ``[c:<uuid>]`` context lines
    and the ```` ```json ```` blocks from that line to the end."""
    idx = text.find("7. Перепроверка")
    return text[idx:] if idx != -1 else ""


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_reverify_example_statement_not_in_any_corpus(prompt_file: Path) -> None:
    """The example statements are the only things a model can match its
    own question against. Every statement of every JSON example
    (positive AND negative, T7.43) must name a topic absent from every
    question-set corpus (v1–v4 in the repo, the smoke set when
    present), so no example can ever be recognized as a corpus
    question."""
    text = prompt_file.read_text(encoding="utf-8")
    blocks = _example_blocks(text)
    if not blocks:
        pytest.skip(f"{prompt_file.name}: no rule-7 JSON example")
    corpora = [(c, c.read_text(encoding="utf-8")) for c in _corpora()]
    for i, example in enumerate(blocks):
        for j, claim in enumerate(example.get("claims", [])):
            statement = claim["statement"]
            for corpus, content in corpora:
                assert statement not in content, (
                    f"{prompt_file.name}: example[{i}].claims[{j}] "
                    f"statement {statement!r} appears in {corpus.name}"
                )


# T7.50: the topic of the explorer completion examples (both the
# positive and the negative JSON example of explorer-v5). Deliberately
# a fact absent from every question-set corpus (v1–v4 + the smoke set)
# — the same property the curator examples are pinned to.
EXPLORER_EXAMPLE_TOPIC = "столица Новой Зеландии — Веллингтон"


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _explorer_prompts(), ids=lambda p: p.name)
def test_explorer_example_topics_not_in_any_corpus(prompt_file: Path) -> None:
    """T7.50: the completion examples are free text a model can match
    its own question against. Every free-text field of every JSON
    example (``public_rationale``, ``expected_information``, and the
    free-text ``decision.reason`` of the negative example) and the
    example topic itself must not occur in any question-set corpus
    (v1–v4 in the repo, the smoke set when present) — if the example
    were a corpus question's fact, the model could recognize it in its
    own question and copy the example. The UUID side is covered by
    test_prompt_uuids_do_not_intersect_docs (T7.50: the explorer files
    are checked too)."""
    text = prompt_file.read_text(encoding="utf-8")
    blocks = _example_blocks(text)
    if not blocks:
        pytest.skip(f"{prompt_file.name}: no JSON example")
    corpora = [(c, c.read_text(encoding="utf-8")) for c in _corpora()]
    for i, example in enumerate(blocks):
        fields = [example.get("public_rationale"), example.get("expected_information")]
        decision = example.get("decision")
        if isinstance(decision, dict):
            fields.append(decision.get("reason"))
        for value in fields:
            if not isinstance(value, str):
                continue
            for corpus, content in corpora:
                assert value not in content, (
                    f"{prompt_file.name}: example[{i}] free-text field "
                    f"{value!r} appears in {corpus.name}"
                )
    # the topic itself (a short fact, not the full example sentences):
    # the prompt still carries it (the constant tracks the prompt), and
    # no corpus contains it
    assert EXPLORER_EXAMPLE_TOPIC in text, (
        f"{prompt_file.name}: the pinned example topic "
        f"{EXPLORER_EXAMPLE_TOPIC!r} is no longer in the prompt — "
        f"update EXPLORER_EXAMPLE_TOPIC"
    )
    for corpus, content in corpora:
        assert EXPLORER_EXAMPLE_TOPIC not in content, (
            f"{prompt_file.name}: example topic {EXPLORER_EXAMPLE_TOPIC!r} "
            f"appears in {corpus.name}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("prompt_file", _curator_prompts(), ids=lambda p: p.name)
def test_rule7_example_ids_do_not_intersect_docs(prompt_file: Path) -> None:
    """T7.43: the ids of BOTH rule-7 examples must not be real run ids —
    the positive one (full UUID inside the JSON block) and the negative
    one (UUID of the ``[c:…]`` context line). A UUID that also appears
    in docs/ is a leaked id: on a fresh DB the host rejects the copied
    reference and the whole proposal fails (T7.34). File-level check 1
    covers every UUID of the file; this test pins the rule-7 examples
    explicitly."""
    section = _rule7_section(prompt_file.read_text(encoding="utf-8"))
    if not section:
        pytest.skip(f"{prompt_file.name}: no rule 7")
    section_uuids = _uuids(section)
    if not section_uuids:
        pytest.skip(f"{prompt_file.name}: rule 7 carries no UUIDs")
    leaked: set[tuple[str, str]] = set()
    for doc in sorted(DOCS_DIR.rglob("*")):
        if not doc.is_file():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for uuid in section_uuids & _uuids(text):
            leaked.add((uuid, doc.relative_to(REPO_ROOT).as_posix()))
    expected = FROZEN_KNOWN_LEAKS.get(prompt_file.name, set())
    assert leaked <= expected, (
        f"{prompt_file.name}: rule-7 example UUIDs present in docs "
        f"(leaked real run ids): {sorted(leaked - expected)}"
    )


# ─── T7.97a: свободный текст ПРАВИЛ нынешнего payload не должен совпадать со случаем,
#     которым этот промпт будут проверять ────────────────────────────────────────────
#
# Проверки 1–3 видят UUID и блоки ```json```, но пример, записанный строкой внутри текста
# правила, от них вне поля зрения. Дефект curator-v11: правило 11 приводило формулировку
# «Аналитики SberCIB: прогноз инфляции в России на 2026 год — 6,5% …», то есть ДОСЛОВНО
# ожидаемый ответ контрольного вопроса витрины 5a68172e (та же сущность, показатель, период и
# значение прочитанной страницы SberCIB), а explorer-v10 и curator-v11 называли реальный
# источник того же случая. После активации config-v21 менеджер повторит ровно этот вопрос, и
# «куратор прочитал улику» было бы неотличимо от «куратор переписал пример». Тот же класс
# дефекта, от которого защищают T7.39 (реальный id в примере) и T7.50 (свободный текст
# примеров explorer), — поэтому проверка живёт в этом же файле.
#
# Критерий и почему именно такой.
#
# (а) Имена акторов. Вынуть «названия организаций» из произвольного русского текста надёжно
#     нельзя, поэтому список имён закреплён в тесте ЯВНО (`REAL_ACTOR_PATTERNS`), а его
#     полнота над структурированными реальными данными — проверкой
#     `test_real_actor_patterns_cover_the_claims_fixture`: каждая строка `claims_live.tsv`
#     обязана дать хотя бы одно имя из списка, иначе тест краснеет и список придётся менять
#     осознанно. Имена взяты из реальных данных: утверждения витрины (`claims_live.tsv`),
#     контрольный вопрос (`control_question.txt`) и отчёты `docs/eval` (Росстат,
#     Минэкономразвития, ЦБ, «Коммерсантъ»); регулярки накрывают падежи (нормализация
#     регистра и «ё»), иначе «Банка России» проскакивает мимо «Банк России».
# (б) Связка «значение + период». Вычисляется ИЗ ДАННЫХ без словаря: из строк claims и из
#     нормализованной страницы того же стендового случая (закреплена T7.97) берутся
#     десятичные значения при знаке % и годы 20xx той же строки источника; правило нового
#     промпта не может содержать такую пару. Это ловит и подмену имени с сохранением случая:
#     актор переименован, а «6,5% … 2026» осталось. Пары считаются только по НОВЫМ строкам —
#     иначе унаследованная цифра замороженного текста давала бы фантомные связки с новым.
#
# Область действия. Промпты, закреплённые ПОСЛЕДНИМ payload (ныне config-v22: curator-v12), и новее: новый
# текст этого выпуска написать «на живом примере» ещё можно — именно это запрещается. Строки,
# байт в байт взятые из прежней закреплённой версии того же промпта (ADR-0019: прежнее
# содержимое не переписывается), из критерия вынесены, но их попадания закреплены явно
# (`INHERITED_REAL_DATA`): новая строка с реальными данными краснит тест, и молча разрастающийся
# унаследованный зазор — тоже. Прежние промпты (пины прежних payload) проверяются как раньше:
# проверки 1–3. С T7.102 закреплён дополнительный инвариант нынешнего curator-промпта: у него
# унаследованных исключений быть не может (`INHERITED_REAL_DATA` для него пуст) — остаточная
# протечка правила 7 закрыта заменой примера перефразы на вымышленный случай.

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
CLAIMS_LIVE_FIXTURE = FIXTURES_DIR / "t797a" / "claims_live.tsv"
CONTROL_QUESTION_FIXTURE = FIXTURES_DIR / "t797a" / "control_question.txt"
#: нормализованная страница стендового случая закреплена в репозиторий раньше (T7.97)
STAND_PAGE_FIXTURES = [FIXTURES_DIR / "t797" / "sbercib_2026_34ecbe51.normalized.txt"]

#: Имена реальных акторов (T7.97a). Источник каждого имени — реальные данные, а не выдумка теста.
REAL_ACTOR_PATTERNS: dict[str, str] = {
    # claims_live.tsv c970bc08 «Независимые оценки Сбера/СберCIB…» и страница случая T7.97
    "sbercib": r"sbercib|сберcib",
    # латинское написание того же актора (именно так он написан на странице случая): промпт
    # мог бы привести его транслитом — это та же утечка
    "сбер": r"\bсбер\b|\bсбера\b|\bсберу\b|\bсбером\b|\bсбербан\w*|\bsber\b|\bsbera\b",
    # claims_live.tsv (Банк России — автор прогнозов и опроса) и контрольный вопрос 5a68172e
    "банк_россии": r"\bбанк\w*\s+росси\w*|\bбанков\w*\s+росси\w*",
    # claims_live.tsv 7cc2b578 «консенсус-прогноз ЦБ»
    "цб": r"\bцб\b",
    # claims_live.tsv e189c80d, 9266248e; отчёты docs/eval
    "росстат": r"\bросстат\w*",
    # claims_live.tsv 7cc2b578 «Минэкономразвития»
    "минэкономразвития": r"минэкономразвити\w*",
    # контрольный вопрос 5a68172e и claims_live.tsv cc75e97f/7c8c9afe (ООО «инФОМ»)
    "инфом": r"\bинф[оо]м\w*",
    # claims_live.tsv d8155167 (ООН) — актор реального утверждения витрины
    "онн": r"\bоон\b",
    # отчёты docs/eval и корпус question-set-v4 (kommersant.ru как источник того же показателя)
    "коммерсант": r"коммерсант\w*|kommersant\w*",
    # реальный актор наблюдений, зафиксированных в замерах (tests/fixtures/t785, STATUS T7.85)
    "левада": r"\bлевада\w*|\blevada\w*",
}

_YEAR_RE = re.compile(r"\b20\d{2}\b")
#: десятичное значение при % (в том числе в диапазоне «6,5–7%»). Голые числа — порог 0.75 или
#: номер задачи T7.97 — в отпечаток не входят: без привязки к % они дали бы ложные связки.
_VALUE_RE = re.compile(r"(?<![\d.,])(\d{1,3}[.,]\d+)(?=\s*(?:%|п\. п\.)|[–-]\s*\d)")

#: Унаследованные попадания: строки нынешнего промпта, байт в байт взятые из прежней
#: закреплённой версии. Прежнее содержимое не переписывается (ADR-0019), поэтому новый критерий
#: к ним не применяется — но они закреплены здесь явно: любая новая строка с реальными данными
#: краснит тест вместо тихого разрастания зазора. Это остаточный дефект класса T7.39 в
#: замороженном тексте: пример перефразы-дубля правила 7 curator-v10 (и curator-v11 как его
#: наследник) называет Росстат и 5,59 % за 2025 год — реальный случай витрины.
#: С T7.102 дефект закрыт в curator-v12: абзац перефразы правила 7 заменён вымышленным
#: случаем той же формы, поэтому у нынешнего промпта унаследованных исключений нет (пустая
#: запись ниже — проверка обязана быть чистой, см. test_current_curator_prompt_has_no_inherited_exceptions).
#: Запись curator-v11 остаётся как история: v11 закреплён config-v21 и не переписывается;
#: его попадание проверяет test_released_curator_v11_record_stays_pinned.
INHERITED_REAL_DATA: dict[str, set[tuple[str, str]]] = {
    "curator-v11.md": {("актор", "росстат"), ("пара", "5,59+2025")},
    "curator-v12.md": set(),
}


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _actor_hits(text: str) -> set[tuple[str, str]]:
    """(имя паттерна, найденный фрагмент) по каждому реальному актору."""
    normalized = _norm(text)
    hits: set[tuple[str, str]] = set()
    for name, pattern in REAL_ACTOR_PATTERNS.items():
        for match in re.finditer(pattern, normalized):
            hits.add((name, normalized[match.start() : match.end()]))
    return hits


def _fixture_lines(path: Path) -> list[str]:
    """Строки реальных данных: у TSV — колонка statement без шапки, у текстовых фикстур — строки
    без комментариев."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.suffix == ".tsv":
        return [row.split("\t")[-1] for row in lines[1:] if row.strip()]
    return [line for line in lines if not line.lstrip().startswith("#")]


def _real_value_year_pairs() -> set[tuple[str, str]]:
    """Отпечатки «значение + период» реального случая: пара берётся из ОДНОЙ строки источника —
    иначе любая связка года и числа считалась бы копией случая."""
    pairs: set[tuple[str, str]] = set()
    for path in [CLAIMS_LIVE_FIXTURE, *STAND_PAGE_FIXTURES]:
        for line in _fixture_lines(path):
            if "%" not in line:
                continue
            values = set(_VALUE_RE.findall(_norm(line)))
            years = set(_YEAR_RE.findall(_norm(line)))
            pairs |= {(value, year) for value in values for year in years}
    return pairs


def _payload_files() -> list[Path]:
    files = sorted((DOCS_DIR / "eval").glob("config-v*-payload.json"))
    assert len(files) >= 2, "нужны минимум два payload, чтобы отличить новый текст от унаследованного"

    def version(path: Path) -> int:
        match = re.search(r"config-v(\d+)-payload\.json", path.name)
        assert match, f"не опознан номер payload: {path.name}"
        return int(match.group(1))

    return sorted(files, key=version)


def _current_prompt_targets() -> list[tuple[Path, Path]]:
    """(нынешний закреплённый промпт, прежняя закреплённая версия того же промпта) по последнему
    payload. Прежняя версия ищется походом назад по payload'ам (T7.111): роль без смены пина в
    последнем payload получает базой пин из последнего payload, где её пин отличался (закреплённые
    прежние версии промптов не переписываются, ADR-0019, и лежат на диске); пин отсутствующего на
    диске файла — исторический след прежнего пути — пропускается. Роль без предшествующего пина
    (или с одним лишь таким следом) получает базой самое себя: нового текста у неё нет — проверка
    это подтверждает и ждёт следующей редакции."""
    files = _payload_files()
    latest = json.loads(files[-1].read_text(encoding="utf-8"))
    targets: list[tuple[Path, Path]] = []
    for role in sorted(latest["prompts"]):
        pin = latest["prompts"][role]
        path = REPO_ROOT / pin["path"]
        assert path.is_file(), f"{path} закреплён последним payload, но отсутствует на диске"
        base = path
        for previous_path in reversed(files[:-1]):
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            prev_pin = previous["prompts"].get(role)
            if not prev_pin or (prev_pin["path"], prev_pin.get("sha256")) == (pin["path"], pin.get("sha256")):
                continue
            candidate = REPO_ROOT / prev_pin["path"]
            if candidate.is_file():
                base = candidate
                break
        assert base.is_file(), f"{base}: прежний пин роли {role} отсутствует на диске"
        targets.append((path, base))
    return targets


def _new_lines(current: Path, base: Path) -> list[str]:
    """Строки, написанные этой версией: построчное сравнение с прежней закреплённой версией с
    сохранением кратности (повтор уже существовавшей строки не считается новым текстом)."""
    lines = current.read_text(encoding="utf-8").splitlines()
    remaining = Counter(lines)
    for line in base.read_text(encoding="utf-8").splitlines():
        if remaining[line]:
            remaining[line] -= 1
    new: list[str] = []
    for line in lines:
        if remaining[line]:
            remaining[line] -= 1
            new.append(line)
    return new


def _inherited_lines(current: Path, base: Path) -> list[str]:
    pending = Counter(_new_lines(current, base))
    inherited: list[str] = []
    for line in current.read_text(encoding="utf-8").splitlines():
        if pending[line]:
            pending[line] -= 1
        else:
            inherited.append(line)
    return inherited


def _pair_hits(text: str, pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    values = set(_VALUE_RE.findall(_norm(text)))
    years = set(_YEAR_RE.findall(_norm(text)))
    return {pair for pair in pairs if pair[0] in values and pair[1] in years}


@pytest.mark.unit
def test_real_data_fixtures_of_the_guard_are_present() -> None:
    """Страж не должен «озеленеть» из-за тихого исчезновения реальных данных, на которых он
    построен: их минимальный объём закреплён."""
    assert CLAIMS_LIVE_FIXTURE.is_file(), f"{CLAIMS_LIVE_FIXTURE} отсутствует"
    rows = _fixture_lines(CLAIMS_LIVE_FIXTURE)
    assert len(rows) >= 15, f"в {CLAIMS_LIVE_FIXTURE.name} ожидалось не меньше 15 строк claims"
    assert CONTROL_QUESTION_FIXTURE.is_file(), f"{CONTROL_QUESTION_FIXTURE} отсутствует"
    question = "\n".join(_fixture_lines(CONTROL_QUESTION_FIXTURE))
    assert "инФОМ" in question and "2026" in question, question
    for path in STAND_PAGE_FIXTURES:
        assert path.is_file(), path
        assert len(path.read_text(encoding="utf-8")) > 1_000, path


@pytest.mark.unit
def test_real_actor_patterns_cover_the_claims_fixture() -> None:
    """Список имён акторов обязан покрывать структурированные реальные данные: каждая строка
    claims витрины даёт хотя бы одно имя из `REAL_ACTOR_PATTERNS`. Новая строка фикстуры без
    разбора списка — красный тест; молча сузить список тоже нельзя (см. обоснование критерия (а))."""
    uncovered = [row[:60] for row in _fixture_lines(CLAIMS_LIVE_FIXTURE) if not _actor_hits(row)]
    assert not uncovered, f"claims без опознанного актора: {uncovered}"


@pytest.mark.unit
def test_real_value_year_pairs_exist_for_the_t797_case() -> None:
    """Отпечаток «значение + период» существует ровно для реального случая (6,5 % на 2026 год со
    страницы SberCIB), а не получается из воздуха; и вымышленное значение примера ни с чьим
    реальным случаем не пересекается. Без этого проверка 4 была бы пустой."""
    pairs = _real_value_year_pairs()
    assert ("6,5", "2026") in pairs, sorted(pairs)[:10]
    assert not any(value == "3,4" for value, _year in pairs), sorted(pairs)


@pytest.mark.unit
def test_the_guard_catches_the_defect_it_was_written_for() -> None:
    """Самопроверка сканера (тот же приём, что в T7.65): синтетический текст прежнего правила 11
    curator-v11 обязан дать и акторное попадание, и связку «значение + период». Зелёный тест на
    пустом сканере ничего не значит."""
    old_rule_11 = (
        "11. Когда вопрос просит независимые оценки или прогнозы: собственная оценка "
        "не-официального актора записывается отдельным claim'ом — «Аналитики SberCIB: прогноз "
        "инфляции в России на 2026 год — 6,5% (базовый прогноз на конец 2026 года)», а медиана "
        "макроэкономического опроса аналитиков Банка России остаётся публикацией ведомства."
    )
    actor_hits = _actor_hits(old_rule_11)
    pair_hits = _pair_hits(old_rule_11, _real_value_year_pairs())
    assert {"sbercib", "банк_россии"} <= {name for name, _ in actor_hits}, sorted(actor_hits)
    assert ("6,5", "2026") in pair_hits, sorted(pair_hits)

    # падежи и кириллические написания: тот же сканер обязан опознать актора в косой форме
    cyrillic_case = (
        "Независимые оценки Сбера совпали с официальной цифрой Росстата; консенсус-прогноз ЦБ — "
        "6,6%, Минэкономразвития — 6,8%; опрос инФОМ дал 14,2%; ООН и «Коммерсантъ» тут ни при чём."
    )
    assert {
        "сбер",
        "росстат",
        "цб",
        "минэкономразвития",
        "инфом",
        "онн",
        "коммерсант",
    } <= {name for name, _ in _actor_hits(cyrillic_case)}, sorted(_actor_hits(cyrillic_case))


@pytest.mark.unit
@pytest.mark.parametrize(
    "prompt_file,base_file",
    _current_prompt_targets(),
    ids=lambda value: value.name,
)
def test_current_prompt_rules_carry_no_real_case_data(prompt_file: Path, base_file: Path) -> None:
    """Правила промпта нынешнего payload не называют реальных акторов витрины и не воспроизводят
    связку «значение + период» реального случая — пример-копия делает исполнение правила
    неотличимым от переписывания примера ровно на том вопросе, которым его проверяют (T7.97a).
    Попадания унаследованного текста закреплены в `INHERITED_REAL_DATA` побайтно: их рост и
    исчезновение краснят тест."""
    new_text = "\n".join(_new_lines(prompt_file, base_file))
    actor_hits = _actor_hits(new_text)
    pair_hits = _pair_hits(new_text, _real_value_year_pairs())
    assert not actor_hits, (
        f"{prompt_file.name}: новый текст правил называет реальных акторов {sorted(actor_hits)} "
        "— пример должен быть вымышленным (T7.97a)"
    )
    assert not pair_hits, (
        f"{prompt_file.name}: новый текст правил воспроизводит «значение + период» реального "
        f"случая {sorted(pair_hits)} — возьмите другое значение и другой год (T7.97a)"
    )

    inherited = _inherited_lines(prompt_file, base_file)
    found: set[tuple[str, str]] = set()
    for line in inherited:
        found |= {("актор", name) for name, _snippet in _actor_hits(line)}
        found |= {("пара", f"{value}+{year}") for value, year in _pair_hits(line, _real_value_year_pairs())}
    declared = INHERITED_REAL_DATA.get(prompt_file.name, set())
    assert found == declared, (
        f"{prompt_file.name}: унаследованные реальные данные не совпадают с закреплённым "
        f"исключением: найдено {sorted(found)}, закреплено {sorted(declared)}"
    )


@pytest.mark.unit
def test_current_curator_prompt_has_no_inherited_exceptions() -> None:
    """T7.102: у нынешнего curator-промпта (пин последнего payload) унаследованных исключений
    быть не может: остаточная протечка правила 7 (Росстат и 5,59 % за 2025 год) закрыта заменой
    примера перефразы на вымышленный случай той же формы. Непустая запись `INHERITED_REAL_DATA`
    для нынешнего промпта означает, что протечка вернулась (или в новую версию заморожен новый
    реальный текст) — тест краснеет."""
    cur_path, _base = next((pair for pair in _current_prompt_targets() if "curator" in pair[0].name), (None, None))
    assert cur_path is not None, "curator не закреплён последним payload"
    declared = INHERITED_REAL_DATA.get(cur_path.name, set())
    assert declared == set(), (
        f"{cur_path.name}: нынешний curator-промпт опирается на унаследованные реальные данные "
        f"{sorted(declared)} — с T7.102 нынешний curator-промпт обязан быть чистым"
    )


@pytest.mark.unit
def test_released_curator_v11_record_stays_pinned() -> None:
    """История закреплена, а не ослаблена: curator-v11 (пин config-v21) не переписывается — его
    новый текст (правило 11) чист, а унаследованные попадания замороженного правила 7 совпадают
    с закреписью `INHERITED_REAL_DATA` побайтно. Переписать v11 «под сканер» или разрастить
    зазор — красный тест."""
    v21 = json.loads((DOCS_DIR / "eval" / "config-v21-payload.json").read_text(encoding="utf-8"))
    v20 = json.loads((DOCS_DIR / "eval" / "config-v20-payload.json").read_text(encoding="utf-8"))
    cur = REPO_ROOT / v21["prompts"]["curator"]["path"]
    base = REPO_ROOT / v20["prompts"]["curator"]["path"]
    assert cur.name == "curator-v11.md" and base.name == "curator-v10.md", (cur.name, base.name)
    new_text = "\n".join(_new_lines(cur, base))
    assert not _actor_hits(new_text), sorted(_actor_hits(new_text))
    assert not _pair_hits(new_text, _real_value_year_pairs()), sorted(_pair_hits(new_text, _real_value_year_pairs()))
    found: set[tuple[str, str]] = set()
    for line in _inherited_lines(cur, base):
        found |= {("актор", name) for name, _snippet in _actor_hits(line)}
        found |= {("пара", f"{value}+{year}") for value, year in _pair_hits(line, _real_value_year_pairs())}
    declared = INHERITED_REAL_DATA.get(cur.name, set())
    assert found == declared, (
        f"{cur.name}: унаследованные реальные данные выпущенного промпта не совпадают с "
        f"закреплённой историей: найдено {sorted(found)}, закреплено {sorted(declared)}"
    )


@pytest.mark.unit
def test_curator_example_is_fictional_and_matches_no_real_case() -> None:
    """Пример формулировки оценки в нынешнем curator-промпте помечен как вымышленный случай, его
    собственные имена не встречаются ни в claims витрины, ни в странице стендового случая, ни в
    контрольном вопросе, ни в корпусах вопросов, и его «значение + период» не является отпечатком
    реального случая (T7.97a)."""
    cur_path, base_path = next((pair for pair in _current_prompt_targets() if "curator" in pair[0].name), (None, None))
    assert cur_path is not None and base_path is not None, "curator не закреплён последним payload"
    new_lines = _new_lines(cur_path, base_path)
    new_text = "\n".join(new_lines)
    examples = [example for example in re.findall(r"«([^»]+)»", new_text) if "%" in example]
    assert examples, f"{cur_path.name}: в новом тексте правила нет примера формулировки оценки"
    assert any("вымышлен" in _norm(line) for line in new_lines), (
        f"{cur_path.name}: пример правила не помечен как вымышленный случай (T7.97a)"
    )
    sources = [
        (path.name, "\n".join(_fixture_lines(path)))
        for path in [CLAIMS_LIVE_FIXTURE, CONTROL_QUESTION_FIXTURE, *STAND_PAGE_FIXTURES]
    ]
    sources += [(corpus.name, corpus.read_text(encoding="utf-8")) for corpus in _corpora()]
    for example in examples:
        # собственные имена примера (первое слово фразы — не имя актора): они не имеют права
        # встречаться в реальных данных, иначе пример опознаётся как живой случай
        names = list(re.findall(r"[А-ЯЁ][а-яё]{3,}", example))[1:]
        names += re.findall(r"„([^”]+)“", example)
        assert names, f"пример {example!r}: нет собственного имени актора"
        for source_name, content in sources:
            normalized = _norm(content)
            for name in names:
                assert _norm(name) not in normalized, (
                    f"пример {example!r}: имя {name!r} встречается в реальных данных {source_name}"
                )
        assert not _pair_hits(example, _real_value_year_pairs()), (
            f"пример {example!r}: совпадает со связкой «значение + период» реального случая"
        )

