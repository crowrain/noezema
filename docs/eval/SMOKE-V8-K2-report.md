# SMOKE-V8-K2 — разбор смоук-прогона (T7.37b)

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, корпуса,
ARCHITECTURE.md не тронуты). Прогон запущен T7.37a, остановка воркера и
разбор — T7.37b. Дата: 2026-09-24.

## 1. Условия прогона

| Параметр | Значение |
|---|---|
| код | `7446d26` (ветка `impl/from-scratch`, дерево чистое) |
| модель | `k2-horizon-mova-36b-a4b-rocmfp4-fast` (K2 Horizon MoVA 36B A4B ROCmFP4 FAST; llama.cpp ROCmFPX-k2, 192.168.1.48:8080, `--ctx-size 262144`, `--parallel 1`) |
| профиль схемы | `llamacpp-rocmfpx` (T7.36, ADR-0012: снимает `minLength`/`maxLength`, сохраняет `format`/`pattern`) |
| payload | `docs/eval/config-v8-payload.json`: file sha256 `135ebe09fa35…`, canonical `9f1fc79ab1f1…`; пины: curator-v4 `6e129ded…`, explorer-v4 `5829a55c…`, planner-v1 `6aeb22bc…`, verifier-v1 `34fe8069…`, extractor-v1 `af5dba62…` |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha256 `b3e05ad5d206…`): 2 пака под перепроверку T7.34 (Python: якорь 90 + FU 80; notes/plan.md: якорь 90 + FU 80), ООН (100, явная дата), Go (0, относительная), Спутник-1 (0, без даты) |
| правила | rules-v2, rules_hash `f96eeffc527c…` |
| БД | `noezema-smoke-v8-k2` (создана запуском, миграции до 0025) |
| run | id `a0a84ce9-a327-4531-aede-6f488a1d3d2a`, snapshot `da1b0165-2ab6-49d5-86d1-d482a104cc62`, seed 20260924, slo 3600 s, blind-size 10 |
| время | 2026-09-24 12:18:12Z → 12:42:04Z (23,9 мин), eval-run EXIT=0 |
| env | `NOEZEMA_LLM_MODEL`/`NOEZEMA_LLM_SCHEMA_PROFILE` переключены T7.37a (бэкап `noezema-llm.env.bak.2026-09-24T121733Z`); env оставлен как есть — K2 остаётся моделью NOEZEMA |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s |

Воркер остановлен T7.37b: до остановки — SELECT: 7/7 сессий в терминальном
состоянии, 7/7 commit_attempts = `committed`; `systemctl --user stop
smoke-v8-k2-worker`; оба юнита inactive (transient-юниты после остановки
unload: `LoadState=not-found`), процессов hostctl/worker.sh не осталось,
последняя запись worker.log 12:45:05Z.

## 2. Сводка

7 сессий: 2 succeeded (4/7, 5/7), 5 succeeded_partial (1, 2, 3, 6, 7).
`outcome=insufficient_sample` (N=7 < MIN_SAMPLE 20) — ожидаемо при N=7;
это НЕ приёмка §22.2.

| # | сессия | вопрос (приоритет) | состояние | claims | model_runs | причина partial (аудит) |
|---|---|---|---|---|---|---|
| 1 | `ecf51eaa` | ООН, 15.04.2026 (100) | succeeded_partial | 1 | 5 | complete_reason — свободный текст, не код |
| 2 | `c79140b5` | Python-якорь (90) | succeeded_partial | 2 | 7 | complete_reason = `goal_reached: <текст>` (код + суффикс) |
| 3 | `26fa0a1f` | plan.md-якорь (90) | succeeded_partial | 0 | 4 | complete_reason — свободный текст; ВСЁ предложение куратора отклонено rules engine |
| 4 | `75b7c0f4` | Python-FU (80) | succeeded | 0 | 10 | — (правило 7 curator-v4 не исполнено, см. §3) |
| 5 | `50f034d0` | plan.md-FU (80) | succeeded | 1 | 3 | — |
| 6 | `e78c7747` | Спутник-1 (0) | succeeded_partial | 1 | 6 | complete_reason — свободный текст |
| 7 | `fa43bb73` | Go (0) | succeeded_partial | 2 | 11 | budget_exhausted после 10 шагов |

Терминал определяется ровно одним правилом (`orchestrator.py:826`):
`complete_reason == "goal_reached"` (точное равенство) и нет
unknown-action → `succeeded`, иначе (без unknown) → `succeeded_partial`.
Отклонённые dependency-ребра/operations на терминал НЕ влияют.

## 3. Гейт 5 = 0/5 — разбор двух паков (главное)

### 3.1 Формула гейта (T7.34/ADR-0018)

`_gate_reuse` (`packages/evaluation/gates.py`): знаменатель — значимые
claims (head `current`, status supported|disputed|refuted, grade ≥ E2);
числитель — claims, «тронутые» ≥2 сессиями по пяти путям:
evidence / claim_revisions / claim_dependencies (from+to) /
**claim_assessments (reverify-путь, T7.34)**.

SQL:

```sql
-- знаменатель: 5
SELECT h.claim_id, a.effective_grade, a.epistemic_status
FROM claim_assessment_heads h
JOIN claim_assessments a ON a.id = h.current_assessment_id
WHERE h.assessment_state='current'
  AND h.epistemic_status IN ('supported','disputed','refuted');
```

| claim | сессия | тип | grade | значим |
|---|---|---|---|---|
| `373353ce` (ООН 193) | 1 | external_fact | E3/supported | ✅ |
| `35d5b7ae` (Python 3.14.7) | 2 | temporal_fact | E3/supported | ✅ |
| `6a2ca3e9` (Python 3.15 pre-release) | 2 | temporal_fact | E1/hypothesis | ❌ |
| `5c01173a` (plan.md 3 записи) | 5 | local_observation | E2/supported | ✅ |
| `229d17fa` (Спутник 4.10.1957) | 6 | external_fact | E3/supported | ✅ |
| `157d4e11` (Go 1.27.1) | 7 | external_fact | E3/supported | ✅ |
| `9c040862` (Go 1.27.1 от 01.09.2026) | 7 | external_fact | E1/hypothesis | ❌ |

Знаменатель = 5 — совпадает с гейтом. Числитель = 0 по ВСЕМ пяти путям:

```sql
-- evidence: все 11 строк — из сессии, создавшей claim (n_sessions = 1 у каждого)
SELECT c.id, count(DISTINCT e.created_in_session)
FROM evidence e JOIN claims c ON c.id = e.claim_id GROUP BY c.id;
-- → 7 строк, все n_sessions = 1
-- claim_revisions: 0 строк (писателя нет — T7.33, по построению)
SELECT count(*) FROM claim_revisions;          -- → 0
-- claim_dependencies: 0 строк (единственное предложенное ребро отклонено)
SELECT count(*) FROM claim_dependencies;       -- → 0
-- claim_assessments: 7 строк, каждая created_in_session = сессия-создатель
SELECT claim_id, created_in_session FROM claim_assessments;
-- → ни одной строки с created_in_session = сессия-перепроверка
```

### 3.2 Пак Python (якорь S2 `c79140b5` → follow-up S4 `75b7c0f4`)

1. **Якорь создал значимый claim.** 2 claim'а: `35d5b7ae` —
   temporal_fact, **E3/supported** (значимый), `6a2ca3e9` — temporal_fact,
   E1/hypothesis.
2. **Follow-up видел claim якоря в контекст-паке.** `context_packed`
   (S4): `claims_evidence: 90` токенов, включены оба claim'а якоря; id
   показан **полным UUID** в строке формата
   `[c:<uuid>] <statement> (<status>, <grade>, p=<conf>)`
   (`packages/cognition/retrieval.py:95`):
   `[c:35d5b7ae-4fad-4327-ac38-c26f0eea4306] … (supported, E3, p=0.75)` и
   `[c:6a2ca3e9-3875-4ec7-b20c-35c8f88ab005] … (hypothesis, E1, p=0.15)`.
   Модель якорь УЗНАЛА: rationale завершения S4 цитирует
   «совпадает с ранее зафиксированным claim'ом
   [c:35d5b7ae-4fad-4327-ac38-c26f0eea4306]».
3. **`existing_claim_id` НЕ использован.** Staging S4 — 0 операций.
   Куратор предложил НОВЫЙ claim типа **local_observation** со
   source_assertion-evidence; rules pre-check (T7.9, до записи staging)
   отклонил **ВСЁ предложение** (`orchestrator.py:2155–2176`), аудит
   seq 38: `claim 0 (local_observation): support evidence kind
   'source_assertion' not allowed for local_observation`.
4. **Аудит `reverify_unresolved` — 0 событий**: reverify-путь (резолюция
   `existing_claim_id`, `orchestrator.py:2085–2143`) вообще не входился —
   модель поле не заполнила.
5. **Строки `claim_assessments` по `35d5b7ae` с `created_in_session` = S4:
   нет.** Для `35d5b7ae` ровно одна assessment (created_in_session = S2).

**Классификация: модель не воспользовалась операцией** (+ усилитель:
неверный claim_type предложенного claim'а → отказ всего предложения).
Это класс (ви.1) T7.33 («промпт type↔evidence»): модель не знает
матрицу type↔evidence_kind, proposes local_observation на source-
evidence; rules engine (единственный оценщик) отказывает по правилу.
Механизм T7.34 не срабатывал не по своей вине — до него не дошло.

### 3.3 Пак notes/plan.md (якорь S3 `26fa0a1f` → follow-up S5 `50f034d0`)

1. **Якорь НЕ создал ни одного claim.** Предложение куратора отклонено
   rules engine целиком (аудит seq 18 S3): `claim 1 (computed_result):
   support evidence kind 'local_observation' not allowed for
   computed_result` — T7.9 pre-commit: один невалидный claim в
   предложении = отказ всего (claim 0, вероятно корректный, потерян
   вместе с ним). Коммит: 0 claims, 0 assessments.
2. **Follow-up не мог перепроверять — нечего.** `context_packed` (S5):
   `claims_evidence: 0` (в базе на тот момент значимых claim'ов по теме
   нет). S5 создал НОВЫЙ claim `5c01173a` (local_observation,
   **E2/supported**) с `existing_claim_id: null` + 1 question.
3. claim_assessments «по claim'у якоря» — N/A (claim якоря не существует).

**Классификация: якорь не дал значимого claim'а** — собственное
предложение якоря отклонено rules engine (та же пара
`computed_result ← local_observation`, что и в EVAL-4d — 6 таких
отказов, класс (ви.1) T7.33).

### 3.4 Сверка с обещаниями T7.34

T7.34 обещал: перепроверка существующего claim'а **записывается и
засчитывается** — операция `claim` + `existing_claim_id` (host-issued id,
префикс — только однозначный), запись = сессионная строка
`claim_assessments`, пятый путь гейта 5, curator-v4 правило 7
«Перепроверка», payload config-v6→v8.

Проверено на живых данных:
- механизм НА МЕСТЕ и fail-closed на обеих границах (код сверен:
  `orchestrator.py:2085–2143` куратор-граница, `service.py`
  commit-граница, пятая UNION-ветка в `_gate_reuse`);
- **но ни одна из 7 сессий операцию не использовала**
  (`existing_claim_id` ≠ null — 0 строк по всему рану);
- оба пака, построенные под перепроверку, упали ДО входа в reverify-путь
  на модельных отказах: Python — «операция не использована + неверный
  claim_type → всё предложение отклонено», plan.md — «якорь не создал
  claim (своё предложение отклонено)».

**Вывод: 0/5 гейта 5 — не дефект механизма T7.34, а модельное поведение
K2 + отсутствие в промпте матрицы type↔evidence (T7.33 (ви.1)).**
Чтобы reverify-путь сработал на живых сессиях, модели нужно сначала
(а) пройти rules pre-check с правильным claim_type и (б) исполнить
правило 7 (использовать `existing_claim_id` вместо нового claim'а).

**Пометка (2026-09-24, T7.40b):** проверено повторным смоуком
SMOKE-V10B-K2 (curator-v6, config-v10, та же БД-корпус): гейт 5 = 1/5 —
перепроверка сработала впервые (полный путь — `SMOKE-V10B-K2-report.md`
§4); отказы rules engine — 0; второй пак всё равно не перепроверил —
другая причина (якорь без evidence, E0; §5 того же отчёта).

## 4. Выдуманные id и причины succeeded_partial

### 4.1 Выдуманные/заглушечные id: 1 за весь ран

| сессия | поле | значение | исход |
|---|---|---|---|
| 1 (ООН) | `dependencies[0].claim_id` | `c0000000-0000-0000-0000-000000000000` | хост отверг: `dependency_edge_rejected` — «dependency target … missing»; claim закоммичен без ребра |

`existing_claim_id` ≠ null — **0 строк** (поле не использовалось вовсе),
выдуманных значений в нём нет. Все остальные 6 claim-операций —
`dependencies: []`. Связь с контекстом: S1 — первая сессия рану,
`claims_evidence: 0` — модель не видела НИ ОДНОГО id и заполнила поле
нулевым UUID-плейсхолдером (тот же класс галлюцинации, что
`curator_error` с 16-символьным UUID в EVAL-4d, T7.33 §3.2). В S4 id в
контексте были (полные UUID, `[c:…]`) — выдуманных значений при видимом
контексте нет (операция просто не использована).

### 4.2 Причины 5 succeeded_partial (аудит + `orchestrator.py:826`)

| сессия | `complete_reason` (дословно, начало) |
|---|---|
| 1 (ООН) | `Оба заданных источника (un.org/about-us и ru.wikipedia.org) прямо подтверждают, что к 2026 году в ООН 193 члена; ответ 193.` — свободный текст, не код |
| 2 (Python-якорь) | `goal_reached: По обоим заданным источникам последняя стабильная версия Python — 3.14.7 …` — код с суффиксом; точное равенство не срабатывает |
| 3 (plan.md-якорь) | `Вопрос выполнен: notes/plan.md создан с тремя пунктами плана, затем прочитан; подтверждено 3 пункта …` — свободный текст |
| 6 (Спутник) | `Вопрос ответен и подтверждён двумя заданными источниками: первый искусственный спутник Земли «Спутник-1» был запущен 4 октября 1957 года …` — свободный текст |
| 7 (Go) | `budget_exhausted` после 10 шагов |

Схема решения (`decision.py:29–32`) допускает «host-defined extension»,
и `Decision.normalized_reason` умеет маппить на enum — но терминал
сравнивает сырую строку точным равенством. 4 из 7 сессий (S1/S2/S3/S6)
деградировали в partial из-за формулировки модели, а не из-за
невыполнения цели: все 4 завершили работу с goal-статусом по смыслу.

**Уточнение к T7.37a:** partial S1 был приписан отклонённому
dependency-ребру; по коду терминал от него не зависит — причина
free-form `complete_reason`. Ребро записано в аудите и отклонено
корректно (fail-closed), но на терминал не влияет.

### 4.3 Отказы инструментов (9 policy deny) — ошибки схемы модели

| сессия | инструмент | причина |
|---|---|---|
| 1 | research.fetch | лишний аргумент `format` |
| 2 | artifact.create | unknown tool (инструмента нет в профиле) |
| 2 | question.create | аргументы claim-операции (`claim`, `dependencies`, `search_statements`), нет `text` |
| 4 | workspace.list | путь `…/workspace/notes` вне read-корней профиля |
| 4 | memory.search ×2 | лишний `search_statements`; затем нет `query` |
| 6 | research.fetch | лишний аргумент `workspace_path` |
| 7 | artifact.create | unknown tool |
| 7 | memory.search | нет `query`, лишний `search_statements` |

Хост ведёт себя корректно (fail-closed, явные причины, модель видит
отказ в tool-result). Сигнал слабости K2 к tool-схемам — тот же класс,
что `memory.search` с `limit` в T7.33.

## 5. Новинки прогона — числами

### 5.1 T7.35 — пины промптов

```sql
SELECT m.phase, m.prompt_version, m.prompt_sha256, count(*)
FROM model_runs m GROUP BY 1,2,3;
```

- model_runs всего **46** (7 consolidating + 39 exploring);
- **46/46** строк несут `prompt_version` И `prompt_sha256`
  (пустых/NULL — 0; исторические ранов было пусто — T7.35/ADR-0019);
- совпадение с пинами config-v8 для своей роли: **46/46, расхождений 0**
  (curator-v4 `6e129ded…` × 7; explorer-v4 `5829a55c…` × 39);
  роли planner/verifier/extractor в ране не вызывались;
- `tool_schema_hash`: 39/39 exploring (`f7628473…`); у 7 consolidating —
  NULL **по замыслу** (куратор вызывается без инструментов,
  `orchestrator.py:1990/2054` — `tool_schema_hash=None`).

### 5.2 T7.36 — схема llamacpp-rocmfpx, токены

- ошибки грамматики схемы (HTTP 400 «failed to parse grammar»): **0**
  (run.log/worker.log — 0 вхождений; `output_schema_valid=false` — 0
  строк; audit `request_rejected` — 0);
- `finish_reason`: **46/46 `stop`** (`length` — 0, `schema_error` — 0);
- выходные токены (лимит `NOEZEMA_LLM_MAX_OUTPUT_TOKENS`=8192):
  мин 107, **медиана 582,5, максимум 4902**, среднее 788 — ни одного
  приближения к лимиту, усечений нет;
- reasoning: **не учитывается отдельно нигде** — клиент гейтвея
  (`packages/llm_gateway/client.py:199–203`) читает только
  `usage.prompt_tokens/completion_tokens` и `finish_reason`;
  `reasoning_content` не парсится и не сохраняется (сырые ответы в БД не
  ретейнятся: `raw_response_artifact_id` пуст во всех 46 строках).
  Токены reasoning входят в `output_tokens` (замер T7.36: тривиальный
  ответ куратора = 2308 токенов, ~8,7 тыс. символов reasoning). При
  максимуме 4902 при лимите 8192 риска `finish_reason=length` в этом
  ране не реализовалось.

### 5.3 T7.30/T7.32 — якорь даты, as_of, reverify_after, freshness

| claim | тип | якорь | as_of | reverify_after | freshness |
|---|---|---|---|---|---|
| `373353ce` (ООН) | external_fact | **explicit** | 2026-04-15 | **NULL** | evergreen |
| `35d5b7ae` (Python) | temporal_fact | **relative** | 2026-09-24 (дата сессии) | 2026-10-24 12:23:03.46 = коммит 12:23:03.46 + 30 д | fresh |
| `6a2ca3e9` (Python) | temporal_fact | **relative** | 2026-09-24 | то же | fresh |
| `5c01173a` (plan.md) | local_observation | **none** | NULL | **NULL** | evergreen |
| `229d17fa` (Спутник) | external_fact | **none** | NULL | **NULL** | evergreen |
| `157d4e11` (Go) | external_fact | **relative** | 2026-09-24 (дата сессии) | 2026-10-24 12:42:04.69 = коммит 12:42:04.68 + 30 д | fresh |
| `9c040862` (Go) | external_fact | **relative** | 2026-09-24 | то же | fresh |

Якорь читан из `assessed_scope.date_anchor` сохранённых оценок
(host-scope-v1). Ожидаемые формы: ООН (explicit) → NULL/evergreen ✅;
Спутник (none, без даты) → NULL/evergreen ✅ (модель предложила
`as_of: null` — хост-фолбэк на модельный as_of, срока нет по якорю);
Python/Go (relative) → as_of = дата начала сессии, срок = МОМЕНТ
ПРОВЕРКИ + 30-дневное окно volatility ✅. **Отклонений: 0/7.**

## 6. Воркер: «RuntimeError: Event loop is closed»

Числа: 103 reassessment-tick + 103 reconcile-tick; после КАЖДОЙ строки
тика ровно один traceback-блок: 206 × «Exception closing connection»,
206 × «Task … AsyncEngine.dispose() … attached to a different loop»,
412 × «Event loop is closed» (2 строки на блок). 1:1:1 корреляция.

Причина (код, `hostctl/cli.py`, паттерн повторён во ~10 командах, напр.
строки 192–194):

```python
code = asyncio.run(_run())        # цикл A: на нём созданы engine/пул/asyncpg-коннекты
finally:
    asyncio.run(engine.dispose()) # НОВЫЙ цикл B: dispose пула, привязанного к циклу A
```

dispose выполняется на втором цикле: greenlet-механика SQLAlchemy
сообщает «Future attached to a different loop», а при закрытии
завязанных на закрытый цикл A asyncpg-коннектов протокол вызывает
`call_soon` по уже закрытому циклу → `RuntimeError('Event loop is
closed')`.

Классификация: **безвредный шум, но реальный teardown-дефект CLI**.
Тик завершается штатно до traceback'а (строка тика печатается первой),
процесс уходит с exit 0, сокет закрывает ядро при смерти процесса; на
данные не влияет (в этом ране воркер вообще не работал: 103 ×
`deferred=True` — сессии в полёте, 103 × `nothing to do`). Вред —
лог-шум: ~6 КБ traceback'а на такт (206 блоков за 24 мин) маскируют
реальные ошибки. Исправление (не сделано — отдельная задача):
`await engine.dispose()` внутри того же `asyncio.run` (в `_run()` или
finally внутри одного раннера).

## 7. Итоговые гейты прогона (как есть; N=7 — не приёмка)

Из `evaluation_runs.gates` (run id `a0a84ce9…`), Wilson ci95:

| гейт | исход | числ/знам | ci95 | порог |
|---|---|---|---|---|
| new_supported_refuted_e2 | insufficient_sample | 5/5 | [0.5655, 1.0] | 0.8 |
| external_temporal_e3 | insufficient_sample | 4/4 | [0.5101, 1.0] | 1.0 |
| eligible_sessions_with_outcome | insufficient_sample | 7/7 | [0.6457, 1.0] | 0.6 |
| near_duplicate_questions | insufficient_sample | 0/7 | [0.0, 0.3543] | 0.15 |
| **significant_claim_reuse** | insufficient_sample | **0/5** | [0.0, 0.4345] | 0.25 |
| due_stale_time_sensitive | insufficient_sample | 0/2 | [0.0, 0.6576] | 0.2 |
| reassessment_slo | insufficient_sample | 0/0 | — | 1.0 |
| current_pending_invalid_ancestor | insufficient_sample | 0/7 | — | 0 |
| high_severity_incidents | **passed** | 0/None | — | 0 |
| blind_provenance_path (структурный) | insufficient_sample | 7/7 | [0.6457, 1.0] | 0.9 |
| blind_scope (структурный) | insufficient_sample | 7/7 | [0.6457, 1.0] | 0.8 |

`outcome=insufficient_sample` (N=7 < 20). Blind-гейты — структурная
проверка; приёмка §22.2 требует ручной ревизии рендеренного образца
(`noezemactl blind-sample --run a0a84ce9-a327-4531-aede-6f488a1d3d2a`).
Интерпретировать как приёмку НЕЛЬЗЯ (N=7).

## 8. Выводы и предложения (по приоритету)

1. **(Высокий) Матрица type↔evidence в промпте куратора** (T7.33
   класс (ви.1), подтверждён на живых данных: 2/7 сессий отклонены —
   S3 `computed_result←local_observation`, S4 `local_observation←
   source_assertion`; в EVAL-4d было 8 таких отказов). Правка текста
   curator-v4 → **новый payload** (меняется protocol_hash) — отдельная
   задача по решению пользователя. Без неё и якоря, и follow-up'ы
   пака-перепроверки гибнут до reverify-пути. — **сделано в T7.38**
   (curator-v5, config-v9).
2. **(Высокий) Reverify-операция не использована (0/7).** K2 видела
   `[c:<uuid>]`-строки, цитировала якорь в rationale — и тем не менее
   предложила новый claim вместо `existing_claim_id`. Механизм T7.34 не
   является бутылочным горлышком (обе границы и пятый путь гейта на
   месте); предложение — усилить правило 7 curator-v4 конкретным
   примером (statement → `existing_claim_id` + evidence_links) в новом
   payload'е. — **сделано в T7.38** (curator-v5, config-v9).
3. **(Средний) Мягкая нормализация `complete_reason`** (host-фикс,
   маленький): модель выдаёт `goal_reached: <текст>` (S2) и свободный
   текст (S1/S3/S6) — точное равенство `orchestrator.py:826` деградирует
   4/7 сессий до partial. Схема (`decision.py`) и `normalized_reason`
   допускают расширения; сравнение должно использовать нормализацию
   (код, либо код + разделитель).
4. **(Средний) Teardown hostctl CLI**: `asyncio.run(engine.dispose())`
   на втором цикле → 206 traceback'ов. Dispose в рамках того же
   `asyncio.run`.
5. **(Низкий) Заглушка `c0000000-…` в dependencies** (S1, нет видимых
   id): правило «`dependencies: []`, если зависимостей нет» в промпте
   куратора (входит в п.1/п.2 при правке промпта). — **сделано в
   T7.38** (curator-v5, config-v9).
6. **(Низкий) Слабость K2 к tool-схемам** (9 deny: неизвестный
   инструмент `artifact.create` ×2, `memory.search` с чужими аргументами
   ×4, лишние аргументы у fetch ×2, чужой путь ×1): хост fail-closed и
   корректен; следить в следующих сериях.

**Пометка (2026-09-24, T7.40b) — повторный смоук SMOKE-V10B-K2
(curator-v6, config-v10):** п.1/п.2/п.5 (сделаны в T7.38/T7.39)
подтверждены живыми данными — 0 отказов rules engine, перепроверка
использована впервые (гейт 5 1/5), заглушка примера промпта не
скопирована; п.3 не сделан — 3/7 partial по-прежнему free-form
`complete_reason`; п.4 не сделан — тот же teardown-шум (160
traceback-блоков); п.6 — 3 deny (9 → 3). Новые наблюдения: якорь
plan.md не связал evidence с claim (E0, второй пак не перепроверил) и
выдуманные dependency-id (3 шт.) — `SMOKE-V10B-K2-report.md` §5, §12.

## 9. Не тронуто (инварианты)

- Код, тесты, payload'ы (v2…v8), корпуса, ARCHITECTURE.md — без
  изменений (задача — только анализ); HEAD `7446d26` до коммита отчёта.
- БД прошлых прогонов и `noezema-smoke-v8-k2` — **только SELECT**.
- `noezema-llm.env` — как есть (K2 остаётся моделью NOEZEMA).
- Прогоны не запускались, к LLM на 192.168.1.48 обращений не было;
  фоновых процессов не осталось (воркер остановлен, юниты inactive,
  orphan-лог-тейлеры T7.37a убраны).
