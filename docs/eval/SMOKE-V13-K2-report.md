# SMOKE-V13-K2 — разбор смоук-прогона (T7.48b)

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, промпты,
корпуса, ARCHITECTURE.md не тронуты). Прогон — чистое повторение
SMOKE-V12-K2 после починки NUL/lease-дефектов (T7.46a/b, T7.47a/b):
**единственная переменная относительно SMOKE-V12-K2 — код**
(`239db71` → `4d5549c`: T7.46a NUL в выводе инструментов + T7.46b
time bomb в restore-drill, T7.47a NUL в тексте модели, T7.47b гонка
lease-heartbeat; поведение промптов не менялось). Модель K2, профиль
llamacpp-rocmfpx, payload config-v11 (curator-v7 + explorer-v4) и
корпус — побайтово те же, что в V12 и во всех прошлых смоуках. Дата:
2026-09-26.

## 1. Условия прогона

| Параметр | Значение |
|---|---|
| код | `4d5549c` (ветка `impl/from-scratch`, дерево чистое, = origin; 999 passed + 8 skipped) |
| модель | `k2-horizon-mova-36b-a4b-rocmfp4-fast` (K2 Horizon MoVA 36B A4B ROCmFP4 FAST; llama.cpp ROCmFPX-k2, 192.168.1.48:8080) |
| профиль схемы | `llamacpp-rocmfpx` (T7.36, ADR-0012) |
| payload | `docs/eval/config-v11-payload.json`: file sha256 `67468a2e…`, canonical `a407ce83…` (пересчитаны запуском, совпали с T7.43); пины: curator-v7 `19d6c6e8e2d890ae4c…`, explorer-v4 `5829a55c4e5d9276…`, planner-v1 `6aeb22bc…`, verifier-v1 `34fe8069…`, extractor-v1 `af5dba62…` — побайтово тот же, что в SMOKE-V12-K2 (config-v11) |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha256 `b3e05ad5d206…`) — побайтово тот же, что в v8/v10b/v11/v12 |
| правила | rules-v2, rules_hash `f96eeffc527c…` (не менялся с v10) |
| БД | `noezema-smoke-v13-k2` (создана запуском, миграции до 0025) |
| run | id `d0b66d37-1aa9-4bd6-9aa1-466ca8600e71`, snapshot `64b3d4b0-…`, seed 20260924 (общий для серии), slo 3600 s, blind-size 10 |
| время | 2026-09-26 07:08:54Z → 07:26:18Z (17,4 мин), eval-run EXIT=0 |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s (174 блока тиков) |

Запуск — T7.48a (`~/dsh1/task43-smoke-v13-launch.log`): `smoke-v13/
launch.sh` = v12 + строки имён (БД, base, label/node-owner/юниты
`smoke-v13-k2-*`, reason) + список предстартовой проверки юнитов прошлых
смоуков (v8/v10/v10b-k2, v11-halogen, v12-k2); env не тронут (T7.45a:
ровно 2 ключа — K2-модель + llamacpp-rocmfpx). Preflight зелёный
(корпус `b3e05ad5…`; пины 5/5; searxng ok; 10 юнитов прошлых смоуков
inactive; миграции до 0025).

Воркер остановлен T7.48b: **до** остановки SELECT (7/7 сессий в
терминальном состоянии — 2 succeeded + 5 succeeded_partial,
commit_attempts 7/7 = `committed`, 0 нетерминальных),
`systemctl --user stop smoke-v13-k2-worker` → `inactive`
(transient-юнит после остановки unload, `is-active` rc=4 — тот же
паттерн, что в v8/v10b/v11/v12); все 12 юнитов `smoke-v8-k2-*` /
`smoke-v10-k2-*` / `smoke-v10b-k2-*` / `smoke-v11-halogen-*` /
`smoke-v12-k2-*` / `smoke-v13-k2-*` — `inactive`; процессов hostctl /
worker.sh / tick'ов не осталось.

## 2. Сводка

7 слотов драйвера: **7/7 сессий в БД (2 succeeded + 5
succeeded_partial), потерь нет** — в отличие от SMOKE-V12-K2 (6/7 + 1
потеряна на NUL-дефекте). Go-пара **выполнена** (в v12 не выполнялась —
слот ушёл на повтор Python-якоря). Строка прогона:
`eligible_sessions=7, completed_sessions=7, outcome=insufficient_sample`
(N=7 < MIN_SAMPLE 20) — ожидаемо при N=7; это НЕ приёмка §22.2.

| # | сессия | вопрос (приоритет) | состояние | шаги, время | claims | примечание |
|---|---|---|---|---|---|---|
| 1 | `67a6f0a4` | ООН, 15.04.2026 (100) | succeeded_partial | 3, 86 с | 1 | complete_reason — свободный текст (§6) |
| 2 | `1a7dc49c` | Python-якорь (90) | **succeeded** | 5, 262 с | 1 | goal_reached точным равенством |
| 3 | `0f1a8831` | plan.md-якорь (90) | succeeded_partial | 4, 43 с | 1 | complete_reason — свободный текст (§6) |
| 4 | `038db566` | plan.md-FU (80) | succeeded_partial | 3, 39 с | 0 новых | ПЕРЕПРОВЕРКА якоря через `existing_claim_id` (§5) |
| 5 | `5c4183e6` | Python-FU (80) | **succeeded** | 5, 137 с | 0 новых | goal_reached; ПЕРЕПРОВЕРКА якоря + новое evidence (§5) |
| 6 | `0d0c1b3f` | Go (0) | succeeded_partial | 7, 353 с | 1 | `goal_reached — <текст>` (код + суффикс) (§6) |
| 7 | `312922b0` | Спутник-1 (0) | succeeded_partial | 4, 122 с | 1 | complete_reason — свободный текст (§6) |

Терминал — тем же правилом (`apps/orchestrator/orchestrator.py:826`):
`complete_reason == "goal_reached"` точным равенством → `succeeded`,
иначе (без unknown) → `succeeded_partial`. Итого: **5 claim'ов, 10
evidence, 7 assessments, 8 вопросов** (7 корпуса + 1 модельный
`2efa681f…`, candidate — вопрос Python-якоря «Какова точная дата
„на actuelle“…»; модель сама пометила его `origin: "seeded"` —
наблюдение, §8 п.4). Все 7 сессий закоммичены (commit_attempts 7/7 =
`committed`, fenced commit без ошибок).

## 3. Живая проверка фиксов (T7.46a/T7.47a/T7.47b)

Метод: серверные литералы через `chr(92)||'x00'` (4-символьный маркер
`\x00`) и `chr(0)` (страховка от реального NUL) — литерал `\u0000` в
команде декодируется транспортом в реальный NUL и пачкает запрос
(установлено T7.48a, повторено при проверке). Проверены:
`audit_events.payload` / `public_summary`, `session_staging.payload`,
`sessions.plan` / `verification` / `extraction` / `termination_reason`,
17 файлов артефактов на диске (`data/artifacts`: 9 сырых страниц + 8
нормализованных текстов) и `workspace/notes/plan.md`.

| канал (фикс) | маркеры `\x00` | реальные NUL |
|---|---|---|
| audit payload / public_summary (граница 3, T7.46a) | 0 | н/п (text-столбцы) |
| session_staging payload (граница 2, T7.46a/47a) | 0 | н/п |
| sessions.plan / verification / extraction (T7.47a) | 0 (колонки NULL во всех 7 сессиях — каналы в текущем профиле не заполняются) | н/п |
| sessions.termination_reason (T7.47a) | 0 | 0 |
| артефакты на диске + workspace (перехват выводов, T7.46a) | 0 | 0 |

(a) **NUL в выводе инструментов (T7.46a): прогон НЕ задействовал
канал.** В прогоне **не было ни одного вызова `python.execute`** —
единственного инструмента профиля, дающего бинарный вывод (источник
NUL в SMOKE-V12-K2). Инструменты: research.fetch 15 (2 × 404),
workspace.read 3, workspace.list 2, artifact.create 2, workspace.write 1.
Python-сессии (якорь + FU) работали через fetch python.org + chocolatey;
stdout-канала не было, маркеру нечего было маскировать.
(b) **NUL в тексте модели (T7.47a): модель выдавала чистый текст** —
маркеров 0 во всех заполненных каналах; план/verification/extraction
NULL (не задействованы профилем).
(c) **Гонка heartbeat (T7.47b): 0 следов** — `LeaseLost`,
`PendingRollbackError`, lease-ошибки в worker.log, run.log и audit — 0;
174 блока тиков + 7 fenced commit'ов без lease-инцидентов.
(d) **UntranslatableCharacterError / CharacterNotInRepertoireError: 0**
в БД и во всех логах (и на моменте запуска — проверка T7.48a).

**Честный вывод: прогон НЕ доказывает фиксы «в бою» — в нём не
появилось ни одного NUL и ни одной lease-ошибки, т.е. защищаемые
ситуации не воспроизводились** (история: NUL-событие v12 — единичный
случай на конкретном stdout `python.execute`, который модель вызвала не
всегда). Прогон подтверждает: (1) **отсутствие регрессии** — пайплайн
на коде `4d5549c` работает 7/7, 7/7 committed, 0 NUL/lease-ошибок;
(2) фиксы покрыты красными→зелёными тестами (T7.46a: 11 тестов,
T7.47a: 10 тестов, T7.47b: детерминированный регрессионный тест
PendingRollbackError → PASSED) — «в бою» подтверждение придёт при
следующем реальном прогоне, где модель вызовет `python.execute` с
бинарным выводом (маркер `\x00` в evidence/audit станет уликой).
Отсутствие NUL в этом прогоне за подтверждение фикса НЕ выдаётся.

Побочное наблюдение: worker.log — 174 traceback-блока «Event loop is
closed» (348 строк; 1 блок на такт — teardown-дефект `hostctl/cli.py`,
dispose на втором `asyncio.run`; безвредный шум, маскирует ошибки — тот
же паттерн, что в v8/v10b/v12; дефект не исправлен).

## 4. Полнота 7/7: Python-пара и Go-пара

**Python-пара (якорь + FU) — состоялась полностью и впервые в серии
дала 2 × succeeded:**

- якорь `1a7dc49c` (90, 5 шагов, 262 с): fetched python.org/downloads
  (`a8951810…` — тот же content-hash, что в v10b и v12) +
  community.chocolatey.org/packages/python314 (raw `51cfee61…`,
  normalized `ff656e12…`), 1 × 404 + повтор python.org; explorer
  `goal_reached`; куратор: claim `64e6a300…` (temporal_fact,
  `evidence_links` ×3 — python.org ×2 + chocolatey; commit:
  `evidence_added 2, evidence_deduped 1`) → **E3/supported/0.75**,
  `as_of=2026-09-26`, срок `2026-10-26 07:18:22` (момент перепроверки
  +30 д, ADR-0017). В отличие от v12 (первый якорь потерян на NUL, повтор
  — `budget_exhausted`), здесь якорь эмитнул точный `goal_reached` →
  `succeeded`.
- FU `5c4183e6` (80, 5 шагов, 137 с): повтор fetch обеих страниц
  (python.org — тот же content-hash `a8951810…`; chocolatey —
  **новый raw-hash `22822a35…`**, normalized тот же `ff656e12…` —
  «дрейф» chocolatey, живой таймстамп в сырых байтах, как в v10b §4.8);
  `goal_reached`; куратор: `existing_claim_id` = полный UUID якоря
  (§5) → `claim_reverified`, оценка без изменений E3/supported/0.75,
  commit: `evidence_added 1` (chocolatey — новый identity `283bb93d…`)
  + `evidence_deduped 2` (оба python.org — тот же content-hash).

**Go-пара — впервые в серии выполнена** (в v12 вопрос Go остался
`candidate`, слот ушёл на повтор Python-якоря): `0d0c1b3f` (0,
7 шагов, 353 с — самая долгая сессия): fetched go.dev/dl
(`a533670a…`) + ru.wikipedia «Go (язык программирования)»
(`fe05677a…`), 1 × 404; claim `dec02bf7…` «Последняя стабильная версия
Go на текущую дату — Go 1.27.1 (выпущена 1 сентября 2026 года)»
(temporal_fact, 2 evidence) → **E3/supported/0.75**, `as_of=2026-09-26`,
срок `2026-10-26 07:24:15` (commit +30 д). Статус — partial:
`complete_reason = "goal_reached — утверждение: …"` (код + суффикс,
класс v8-S2 / v11-S7).

**Число claim'ов: 5** (в v12 — 6; в v10b — 6; в v8 — 7): по одному на
каждую тему (ООН, Python, plan, Go, Спутник), **0 дублей и 0 E1-
гипотез** (в v12 у якорей были по второму E1-claim'у — chocolatey-
гипотеза Python и «plan 3 пункта» без даты). Все 5 — head `current`,
`supported`, ≥1 evidence (blind 5/5). 7/7 committed.

## 5. Гейт 5 = 2/5 (пересчёт — MATCH)

Пересчитан кодом репозитория `_gate_reuse`
(`packages/evaluation/gates.py`, SELECT only; helper
`~/dsh1/smoke-v13/analyze-gate5-v13.py` — копия analyze-gate5.py с
указанием на `noezema-smoke-v13-k2` + run id):

```
=== stored gate (evaluation_runs.gates) ===
  outcome=insufficient_sample numerator=2 denominator=5 ci95=[0.1176, 0.7693]
=== recomputed _gate_reuse (repo code, SELECT only) ===
  outcome=insufficient_sample numerator=2 denominator=5 ci95=[0.1176, 0.7693]
  MATCH: True
```

Совпадает с `run.log` (строка гейта) и с сохранённым значением.

**Знаменатель 5 — все 5 значимых claim'ов** (head `current`,
supported, grade ≥ E2):

| claim | тема | тип | grade | тронут сессиями | в числителе |
|---|---|---|---|---|---|
| `87d128c7` | ООН 193 | temporal_fact | E3 | `67a6f0a4` | нет (1 сессия) |
| `64e6a300` | Python 3.14.7 | temporal_fact | E3 | `1a7dc49c` + `5c4183e6` | **да** |
| `6c3aa2c6` | plan.md 3 пункта | local_observation | E2 | `0f1a8831` + `038db566` | **да** |
| `dec02bf7` | Go 1.27.1 | temporal_fact | E3 | `0d0c1b3f` | нет (1 сессия) |
| `53d13976` | Спутник 4.10.1957 | temporal_fact | E3 | `312922b0` | нет (1 сессия) |

**Числитель 2 — через какие из 5 путей `_gate_reuse`:**

- `64e6a300` (Python) — **ветка 1 (evidence)**: строки от `1a7dc49c`
  (×2) и `5c4183e6` (×1, новое chocolatey-identity) **и ветка 5
  (claim_assessments)**: строки якоря + строка-перепроверка FU
  `830f76fb…` (`created_in_session=5c4183e6`); distinct = 2.
- `6c3aa2c6` (plan) — **только ветка 5 (claim_assessments)**:
  `0f1a8831` + `038db566` (строка-перепроверка `7876da11…`); distinct =
  2. По ветке 1 FU не виден: evidence FU схлопнулись (файл тот же →
  identity та же → `evidence_deduped 2`, commit FU: `claims_created 0,
  claims_reused 0, assessments 1, evidence_added 0`).
- ООН/Go/Спутник — 1 сессия (ветки 1 и 5 с той же сессией) — не
  засчитаны. Ветки 2/3/4 (claim_revisions /
  claim_dependencies.from / .to) — **0 строк** (в БД `claim_revisions`
  0, `claim_dependencies` 0).

**Правило 7 (curator-v7): сработало на обеих парах — полный UUID.**
Staging FU: `038db566` → `existing_claim_id =
"6c3aa2c6-bdcd-4784-bb1d-9af502e561c8"`; `5c4183e6` →
`existing_claim_id = "64e6a300-9d76-430c-9612-7ce3be7028a8"` — **полные
UUID, не префиксы**, не заглушки примера промпта. `claim_reverified`
(audit): `reference == resolved` == полный UUID якоря (у Python
`date_anchor: relative`, `assessed_as_of: 2026-09-26`; у plan
`date_anchor: none`). **Настоящая перепроверка, не dedup**: строки
`claim_assessments` с чужой сессией есть (обе выше), в аудите 2 события
`claim_reverified`; дедуп (план: `evidence_deduped 2`; python:
`deduped 2` + `added 1`) в числителе НЕ участвует — числитель целиком
ветка 5. В рефразе Python-FU `claim_type = external_fact` при якорном
`temporal_fact` — оценка под типом якоря (ADR-0018; то же, что в v12).

**Сравнение пар с V12 и V10B:**

| | V10B (curator-v6) | V12 (curator-v7) | V13 (curator-v7, код 4d5549c) |
|---|---|---|---|
| plan-пара | якорь E0 (без evidence) + FU **дубль** (`existing_claim_id: null`) | якорь E2 + FU **перепроверка** (полный UUID `035e8a14…`) | якорь E2 + FU **перепроверка** (полный UUID `6c3aa2c6…`) |
| Python-пара | якорь E3 + FU **перепроверка** (полный UUID `b2d4d55b…`) | якорь E3 (retry) + FU **перепроверка** (полный UUID `aa132f9d…`) | якорь E3 + FU **перепроверка** (полный UUID `64e6a300…`) |
| гейт 5 (пары) | 1 (Python) | 2 | 2 |
| `existing_claim_id` заполнили | 1/7 | 2/7 | 2/7 |

В V12 и V13 — одна модель, один промпт, один payload: различия между
ними (статусы якорей, 5 claim'ов против 6, Go-пара выполнена против не
выполнена, 2 succeeded среди разных сессий) — **вариативность
выборки модели (temperature), а не влияние фиксов**: T7.47a маскирует
только NUL (чистый текст проходит побайтово — ADR-0020; в этом прогоне
NUL не было ни одного, код фикса — пассивен), T7.46a/b и T7.47b не
меняют поведение на чистых данных. Гейт 5 стабилен на 2/5 в двух
независимых выборках K2+curator-v7; отличие от v10b (1/5) — эффект
промпта curator-v7 (правка (б)), подтверждённый в v12 и воспроизведённый
в v13.

## 6. Статусы: 2 succeeded + 5 succeeded_partial — причины по аудиту

| сессия | `termination_reason` (начало) | класс |
|---|---|---|
| `67a6f0a4` (ООН) | «Вопрос отвечен и подтверждён двумя указанными источниками: 193 государства-члена ООН на 15 апреля 2026 года…» | **free-form** (не точное `goal_reached`) — класс T7.37b (host-фикс не сделан) |
| `0f1a8831` (plan-якорь) | «Вопрос ответен и подтверждён: файл notes/plan.md содержит ровно 3 пункта плана (2025-01-20 Обсуждение целей проекта — Иван Иванов; …)» | **free-form** — тот же класс |
| `038db566` (plan-FU) | «notes/plan.md содержит ровно 3 пункта плана встречи: … (observation 6843a2de0b9d). Предложенный claim: … (supported, E2, p=0.9, dependencies: [c:6c3aa2c6-…])» | **free-form** — тот же класс |
| `0d0c1b3f` (Go) | «**goal_reached —** утверждение: последняя стабильная версия Go на текущую дату — Go 1.27.1 (выпущена 1 сентября 2026 года); подтверждено двумя независимыми источниками…» | **код + суффикс** (`goal_reached — <текст>`; точное равенство не срабатывает) — класс v8-S2 / v11-S7 (`goal_reached: <текст>`) |
| `312922b0` (Спутник) | «Первый искусственный спутник Земли „Спутник-1“ был запущен 4 октября 1957 года (19:28:34 UTC). Эта дата подтверждена строго по обоим заданным источникам…» | **free-form** — тот же класс |

**5/5 partial — из-за `complete_reason`, не совпавшего с токеном
`goal_reached`** (4 free-form + 1 «код + суффикс»). `budget_exhausted`
в этом прогоне не было (в v12 был 1 — повтор Python-якоря исчерпал
10-шаговый бюджет). Состав классов (free-form / budget / суффикс)
отличный от v12 (3 free-form + 1 budget) — это вариативность текста
модели: T7.47a маскирует только NUL, обычный текст проходит побайтово
(ADR-0020), в прогоне NUL не было — фиксы на класс причин не влияют.
Все 5 сессий при этом **сделали работу** (claims/evidence закоммичены,
вопросы отвечены) — partial чисто по формальному правилу терминала
(известный класс T7.37b; host-нормализация `complete_reason` не
сделана).

## 7. Числа (как в прошлых отчётах)

| проверка | результат |
|---|---|
| T7.35 (пины model_runs) | 38 model_runs (consolidating 7 + exploring 31); **38/38 с пинами config-v11**: curator-v7 `19d6c6e8e2d890ae4c…` (7) + explorer-v4 `5829a55c4e5d9276…` (31); 0 расхождений, 0 NULL `prompt_sha256`; tool_schema_hash exploring `f76284735e…` (consolidating NULL — по дизайну) |
| T7.36-K2 (ошибки схемы) | оба паттерна **0**: `output_schema_valid` 38/38; 0 schema-событий в audit; 0 «failed to parse grammar» в логах |
| T7.30/T7.32 (даты/сроки) | 5/5 согласованы, 0 расхождений: ООН `as_of=2026-04-15` (explicit, evergreen — дата из вопроса); Python `as_of=2026-09-26` (relative, fresh, срок `2026-10-26 07:18:22` = перепроверка +30 д, ADR-0017); plan `as_of=NULL` (none, evergreen); Go `as_of=2026-09-26` (relative, fresh, срок `2026-10-26 07:24:15` = commit +30 д); Спутник `as_of=1957-10-04 19:28:34` (none, evergreen — модель привязала к дате запуска). due_stale 0/2 |
| типы claim'ов | temporal_fact ×4 (ООН, Python, Go, Спутник), local_observation ×1 (plan); 0 external_fact / 0 E1 (в v12: external_fact 1 — chocolatey-гипотеза E1) |
| токены | output: max **3646** / медиана 516,5 / min 108 — все ≤ 8192 (0 прогонов ≥ 8192); input: max 21094 / медиана 6376 |
| finish_reason | **stop 38/38** (0 length) |
| blind gates (структурная проверка) | `blind_provenance_path` **5/5**, `blind_scope` **5/5** — у всех 5 значимых claim'ов ≥1 evidence (включая plan-якорь, у которого 1 local_observation); 0 отклонений rules, 0 fabricated; ручной разбор рендера — не выполнен (как во всех прошлых смоуках: §22.2 определяет blind-выборку как ручной просмотр) |
| deny-счётчик | **3** (v12: 2, v10b: 3, v11: 5, v8: 9): workspace.list «path '/' outside profile read roots» ×1 (`5c4183e6`, новый класс — попытка листинга корня) + artifact.create «unknown tool» ×2 (`0d0c1b3f`, `312922b0` — tool-схемы K2); хост fail-closed сработал корректно |
| прочие отказы | 2 × research.fetch 404 (Python-якорь, Go; точные URL не сохраняются — только `arguments_hash`) |

## 8. Сводная таблица серии v8 → v13

| | v8 (curator-v4, K2) | v10b (curator-v6, K2) | v11 (curator-v7, halogen) | v12 (curator-v7, K2) | **v13 (curator-v7, K2, фиксы NUL/lease)** |
|---|---|---|---|---|---|
| гейт 5 | 0/5 | 1/5 (Python — перепроверка, полный UUID) | 1/6 (Python — dedup; plan — дубль; 0/7 `existing_claim_id`) | 2/5 (обе пары — перепроверка, полный UUID) | **2/5 (обе пары — перепроверка, полный UUID)** |
| статусы | 2 succeeded + 5 partial (7/7) | 4 + 3 (7/7) | 6 + 1 (7/7) | 2 + 4 + **1 потеряна** (6/7) | **2 + 5 (7/7)** |
| потерянные сессии | 0 | 0 | 0 | 1 (NUL в stdout `python.execute` — откат всей транзакции) | **0** |
| дефекты (а)/(б) | (а) не проявился (якорь E0 + 3 выдуманных dependency-edge); (б) правило 7 в v4 отсутствует | (а) E0-якорь plan; (б) plan-дубль (`existing_claim_id: null`) | (а) E2 — модельно-независим; (б) 0/7 — модельная особенность halogen | (а) E2/E3 — сработал; (б) 2/2 — сработал; **инфраструктура: NUL-дефект (открыт)** | NUL/lease **закрыты** (T7.46a/b, T7.47a/b); (а) 5/5 claim'ов с evidence (blind 5/5); (б) 2/2; в «бою» не проверялись (NUL в прогоне не было) |
| длительность | 23,9 мин | 18,0 мин | 57,5 мин (halogen) | 17,0 мин | **17,4 мин** |
| claim'ов (значимых) | 7 (5) | 6 (5) | 6 (6) | 6 (5) | **5 (5)** |
| Go-пара | выполнена | выполнена | выполнена | **не выполнялась** (слот ушёл на повтор) | **выполнена** |

**Итог: серия T7.38–T7.47 закрыта на данном уровне уверенности.**
(1) Вопрос про эффект промпта (правки (а)/(б)) — закрыт в v12 и
подтверждён повторением v13: 2/5 через робастный путь (перепроверка
полным UUID на обеих парах) в двух независимых выборках K2+curator-v7;
halogen — модельная особенность (0/7), модельно-независимости правки
(б) не требует. (2) Инфраструктурный пункт NUL/lease — закрыт: фиксы
закоммичены (T7.46a/b, T7.47a/b + ADR-0020), чистое повторение
выполнено 7/7 (потерь нет, 7/7 committed, 0 NUL/lease-ошибок), Go-пара
выполнена и закоммичена.

**Осталось открытым (не требует повторного прогона этой серии):**

1. **NUL-фикс не подтверждён «в бою»** — в прогоне не было ни одного
   NUL (и ни одного `python.execute`), т.е. защищаемая ситуация не
   воспроизводилась; подтверждение — красные→зелёные тесты (T7.46a: 11,
   T7.47a: 10, T7.47b: регрессионный тест). Следующий реальный прогон,
   где модель вызовет `python.execute` с бинарным выводом, закрепит
   маркером `\x00` в evidence/audit (или отсутствием ошибок) — это
   наблюдение следующего прогона, а не дефект фикса.
2. **Т7.37b — нормализация `complete_reason` на хосте** (free-form /
   «код + суффикс» → `succeeded`) не сделана: доля partial 5/7 (v12:
   4/6, v10b: 3/7, v11: 1/7) — всё по формальному правилу терминала,
   работа сессий при этом полная. *(Пометка: сделано в T7.49 —
   host-нормализация, ADR-0022, класс B (3/20 partial); основная доля —
   класс D (15/20) — вылечена у источника в T7.50: explorer-v5 /
   config-v12, вариант A ADR-0022; проверка эффекта — отдельный смоук
   V14.)*
3. **Якорные claim'ы без evidence — устранены** (v13: 5/5 с evidence,
   blind 5/5; в v10b был E0-якорь plan).
4. Малое наблюдение: модельный вопрос `2efa681f…` создан с
   `origin: "seeded"` (модель сама пометила чужой origin; в v10b
   модель использовала `unverified_claim`/`previous_result`) —
   curiosity-сторона, не дефект; текст вопроса содержит артефакт
   шаблонизации «на actuelle» и хвост `</ifm|arg_value>` (текст
   модели, не хоста).

**Рекомендация: повторный прогон НЕ нужен.** Гейт 5 стабилен (2/5 в
двух выборках K2+curator-v7), полнота 7/7 подтверждена, дефектов не
найдено, фиксы покрыты тестами; ещё один прогон на тех же условиях
добавит только ещё одну выборку temperature без новых данных.
Следующий шаг — другая работа: (1) host-нормализация `complete_reason`
(класс T7.37b) — уберёт ложные partial (главная доля — 5/7 в v13)
*(— пометка: сделано в T7.49 (хост) + T7.50 (у источника: explorer-v5 /
config-v12, вариант A ADR-0022); проверка — отдельный смоук V14)*;
(2) teardown «Event loop is closed» в `hostctl/cli.py` (dispose в том
же `asyncio.run`) — 174 блока шума в каждом воркере маскируют ошибки.
«В бою» проверку NUL-фикса ждать в естественном потоке (следующий
реальный прогон с `python.execute`), специально под это смоук не
запускать — на этом корпусе модель python.execute не вызывает.

## 9. Не тронуто (инварианты)

- Код, тесты, payload'ы (v2…v11), промпты, корпуса, ARCHITECTURE.md —
  без изменений (задача — только анализ); HEAD `4d5549c` до коммита
  отчёта; дифф — docs-only.
- БД `noezema-smoke-v13-k2`, `noezema-smoke-v12-k2`,
  `noezema-smoke-v11-halogen`, `noezema-smoke-v10b-k2`,
  `noezema-smoke-v10-k2`, `noezema-smoke-v8-k2`, `noezema-eval*` —
  **только SELECT** (пересчёт гейта — кодом репозитория в режиме
  чтения; NUL-сканирование — по файлам артефактов и `tr`, без записи).
- Прогоны не запускались, к LLM на 192.168.1.48 обращений не было;
  фоновых процессов не осталось (воркер остановлен, все 12 юнитов
  inactive, процессов hostctl/worker.sh нет).
