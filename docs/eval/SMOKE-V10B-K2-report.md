# SMOKE-V10B-K2 — разбор смоук-прогона (T7.40b)

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, корпуса,
ARCHITECTURE.md не тронуты). Прогон — полный перезапуск прерванного
SMOKE-V10-K2 с нуля на свежей БД (T7.40a); остановка воркера и разбор —
T7.40b. Дата: 2026-09-24.

## 1. Условия прогона

| Параметр | Значение |
|---|---|
| код | `5db8ca3` (ветка `impl/from-scratch`, дерево чистое, = origin) |
| модель | `k2-horizon-mova-36b-a4b-rocmfp4-fast` (K2 Horizon MoVA 36B A4B ROCmFP4 FAST; llama.cpp ROCmFPX-k2, 192.168.1.48:8080) |
| профиль схемы | `llamacpp-rocmfpx` (T7.36, ADR-0012) |
| payload | `docs/eval/config-v10-payload.json`: file sha256 `9ea966b2…`, canonical `de24dbf0…`; пины: curator-v6 `a3dbccdcc6…`, explorer-v4 `5829a55c…`, planner-v1 `6aeb22bc…`, verifier-v1 `34fe8069…`, extractor-v1 `af5dba62…` — отличается от config-v8 (SMOKE-V8-K2) ТОЛЬКО промптом куратора (curator-v4 → curator-v6: матрица type↔evidence T7.38 + безопасный пример правила 7 T7.39 + dependencies T7.38) |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha256 `b3e05ad5d206…`) — побайтово тот же, что в SMOKE-V8-K2: 2 пака под перепроверку (Python: якорь 90 + FU 80 «Сверь ответ с ранее зафиксированной версией»; notes/plan.md: якорь 90 + FU 80), ООН (100, явная дата), Go (0), Спутник-1 (0) |
| правила | rules-v2, rules_hash `f96eeffc527c…` |
| БД | `noezema-smoke-v10b-k2` (создана запуском, миграции до 0025) |
| run | id `213b035c-da7c-474c-bcc1-9357dabc2791`, snapshot `fcc9c355-…`, seed 20260924, slo 3600 s, blind-size 10 |
| время | 2026-09-24 18:59:33Z → 19:17:31Z (18,0 мин), eval-run EXIT=0 |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s |

Воркер остановлен T7.40b: до остановки — SELECT: 7/7 сессий в терминальном
состоянии (4 succeeded + 3 succeeded_partial), 7/7 commit_attempts =
`committed`, 0 нетерминальных; `systemctl --user stop
smoke-v10b-k2-worker` → inactive (transient-юнит после остановки unload:
`could not be found` — как в v8); все 6 юнитов `smoke-v8-k2-*` /
`smoke-v10-k2-*` / `smoke-v10b-k2-run` — inactive; процессов
hostctl/worker.sh не осталось; orphan-лог-тейлеры прошлых задач (task31
smoke-v10, task32 smoke-v10b) убраны.

## 2. Сводка

7 сессий: 4 succeeded (2, 4, 6, 7), 3 succeeded_partial (1, 3, 5).
`outcome=insufficient_sample` (N=7 < MIN_SAMPLE 20) — ожидаемо при N=7;
это НЕ приёмка §22.2.

| # | сессия | вопрос (приоритет) | состояние | шаги, время | claims | причина partial (аудит) |
|---|---|---|---|---|---|---|
| 1 | `da276e7a` | ООН, 15.04.2026 (100) | succeeded_partial | 5, 115 с | 1 | complete_reason — свободный текст, не код |
| 2 | `1db12bec` | Python-якорь (90) | succeeded | 5, 186 с | 1 | — (goal_reached точным равенством) |
| 3 | `de32a56e` | plan.md-якорь (90) | succeeded_partial | 3, 31 с | 1 | complete_reason — свободный текст; claim создан БЕЗ evidence (E0) + 3 выдуманных dependency-edge (отклонены) |
| 4 | `9ec3807f` | plan.md-FU (80) | succeeded | 2, 27 с | 1 | — (goal_reached; НОВЫЙ claim вместо перепроверки, §5) |
| 5 | `6ced7d7e` | Python-FU (80) | succeeded_partial | 5, 288 с | 0 новых | complete_reason — свободный текст; ПЕРЕПРОВЕРКА якоря (§4) |
| 6 | `fc740884` | Спутник-1 (0) | succeeded | 3, 87 с | 1 | — (goal_reached) |
| 7 | `99ca4a8c` | Go (0) | succeeded | 8, 343 с | 1 | — (goal_reached) |

Терминал определяется тем же правилом (`apps/orchestrator/orchestrator.py:826`):
`complete_reason == "goal_reached"` точным равенством → `succeeded`,
иначе (без unknown) → `succeeded_partial`.

## 3. Гейт 5 = 1/5 (против 0/5 в SMOKE-V8-K2, тот же знаменатель 5)

Значимые claims (head `current`, supported|disputed|refuted, grade ≥ E2):

| claim | сессия | тип | grade | значим |
|---|---|---|---|---|
| `29a04a38` (ООН 193) | 1 | temporal_fact | E3/supported | ✅ |
| `e856cd0c` (Python 3.14.7) | 2 | temporal_fact | E3/supported | ✅ |
| `c6aa88ea` (plan.md, якорь) | 3 | local_observation | E0/hypothesis | ❌ (E0 < E2) |
| `64b1761f` (plan.md, FU) | 4 | local_observation | E2/supported | ✅ |
| `e0ce7b02` (Спутник 4.10.1957) | 6 | temporal_fact | E3/supported | ✅ |
| `c862ed69` (Go 1.27.1) | 7 | temporal_fact | E3/supported | ✅ |

Знаменатель = 5 — воспроизведён собственным SELECT по формуле
`_gate_reuse` (`packages/evaluation/gates.py`, пять UNION-веток);
числитель = 1 — ровно `e856cd0c`, тронутый 2 сессиями
(`1db12bec` + `6ced7d7e`) — и по ветке evidence (новое source_assertion
от FU), и по ветке claim_assessments (строка-перепроверка).

*(СЛЕДУЮЩИЙ СМОУК, SMOKE-V11-HALOGEN / T7.44b: гейт 5 = 1/6 — знаменатель
вырос ровно на 1, потому что план-якорь, исключённый здесь как E0
(`c6aa88ea`), в v11 стал E2 (связано evidence) и вошёл в знаменатель;
числитель остался 1. Детали — SMOKE-V11-HALOGEN-report.md §3.)*

## 4. ПЕРВЫЙ ПАК (Python): перепроверка сработала ВПЕРВЫЕ — полный путь

Сессия-перепроверка `6ced7d7e` (follow-up Python, prio 80: «…Сверь ответ
с ранее зафиксированной версией»). Якорь — `e856cd0c-…` (полный UUID —
в БД), созданный сессией `1db12bec`.

1. **Якорь создал значимый claim, видимый в паке.** FU
   `context_packed` (аудит seq 8): `claims_evidence: 61` токена,
   `included_chunks: {"claims_evidence": ["claim:e856cd0c-…"]}` — якорь
   в контекст-паке строкой
   `[c:e856cd0c-…] <statement> (supported, E3, p=0.75)`.
2. **Куратор FU предложил claim-операцию с `existing_claim_id`** =
   `e856cd0c-…` — **полный UUID (36 символов, с дефисами), не
   префикс-резолюция**; это НЕ заглушка из примера промпта (в примере
   curator-v6 — `118b76b3-…`, «Столица Франции — Париж», тема вне
   корпусов — T7.39). `claim_type` = `temporal_fact` (тип якоря).
   Staging: 4 операции (claim + 2 evidence + 1 question), все applied.
3. **Граница куратора — резолюция прошла** (fail-closed, ADR-0018):
   ссылка резолвлена по claim'ам, видимым в пак; rules pre-check
   выполнен под типом якоря (`temporal_fact ← source_assertion` —
   пара допустима) — отказов нет.
4. **Коммит — fenced** (audit: `commit_attempt_prepared` seq 35 →
   `commit_attempt_committed` seq 40, 19:10:20.607→19:10:20.644).
   Новый claim НЕ создан (в claims ровно один Python-claim,
   created_in_session = `1db12bec`).
5. **Аудит записал перепроверку**: отдельное событие
   `claim_reverified` (seq 36) — `reference` = `resolved` = полный
   UUID (однозначное разрешение), `statement` — модельная
   повторная формулировка (audit-only), модельный `as_of` 2026-08-12
   (audit-only; `assessed_as_of` = 2026-09-24 — host-выражение),
   `date_anchor: relative`; и `claim_assessed` (seq 37) в той же
   транзакции, что и коммит.
6. **Запись = новая строка claim_assessments** `b2d843da-…` с
   `created_in_session = 6ced7d7e` (якорная строка `49820130-…` —
   `1db12bec`). Head переехал: `claim_assessment_heads.current_assessment_id`
   = `b2d843da-…`.
7. **Оценка НЕ изменилась** (evidence подтверждающее): якорь
   E3/supported/0.75 (`requirements_met`) → перепроверка
   E3/supported/0.75 (`requirements_met`); `evidence_set_hash` сменился
   (`e8c344d7…` → `8e2b7a8f…`) — в набор добавилось новое evidence.
8. **Evidence — ловушка identity ADR-0018 вживую.** У claim'а 3 строки:
   `167f4833…` (python.org, якорь), `345fb5ff…` (chocolatey, якорь),
   `37e42cdc…` (chocolatey, FU) — новое source_assertion получил
   **иной identity_hash**, чем два evidence якоря: chocolatey-страница
   дрейфнула за ~4 минуты (content-hash `fa3eb6b8…` → `fd8dc92c…`).
   Перезапрос python.org FU схлопнулся в строку якоря (тот же
   content-hash `a8951810…` → та же identity → `UNIQUE(claim_id,
   evidence_kind, identity_hash)` переиспользует строку;
   created_in_session остался якорем) — ровно по §14.3.
9. **reverify_after СДВИНУТ корректно** (T7.32/ADR-0017: relative-якорь
   → момент проверки + окно): `2026-10-24 19:10:20.618225` = МОМЕНТ
   КОММИТА перепроверки (19:10:20.618, между prepared .607 и finished
   .644) + ровно 30 дней окна volatility `temporal_fact`. До
   перепроверки срок шёл от момента якоря (коммит 19:04:34.9xx).
10. **FU заодно создал вопрос** (origin `previous_result`): «Точная
    дата выгрузки страниц сверки не зафиксирована в evidence… Уточнить
    точный момент времени сверки, чтобы привязать факт к as_of» —
    легитимный вопрос, не дубликат.

Вывод: механизм T7.34/ADR-0018 подтверждён на живых данных ВСЮЮ цепью
— от `[c:…]`-строки в паке до head-переезда; правки промпта (матрица
+ пример правила 7) сделали операцию исполнимой: K2 заполнил
`existing_claim_id` полным UUID. **Перепроверка засчитана гейтом 5 —
впервые на живых сессиях (1/5 против 0/5 и 0/29 в EVAL-4d).**

## 5. ВТОРОЙ ПАК (plan.md) перепроверил НЕ — гипотеза менеджера ОПРОВЕРГНУТА

> **SMOKE-V12-K2 (T7.45b) — чистое сравнение с этим прогоном (тот же K2,
> единственная переменная — промпт curator-v6→v7):** на этой паре K2 под
> curator-v7 **перепроверил якорь** — `existing_claim_id` полным UUID,
> событие `claim_reverified`, дубля нет; якорь связан с evidence (E2, не
> E0). Правки (а) и (б) сработали — см. SMOKE-V12-K2-report.md §4–5.

Сессия `9ec3807f` (follow-up plan.md, prio 80: «Прочитай файл
notes/plan.md… и подтверди, сколько пунктов») создала НОВЫЙ claim
`64b1761f-…` (local_observation, E2/supported) с
`existing_claim_id: null`.

**Гипотеза менеджера («якорь E0 → слабые claim'ы не попадают в
контекст-пак, ссылаться было не на что») — опровергнута данными:**

1. **Якорь `c6aa88ea-…` БЫЛ в контекст-паке FU.** `context_packed`
   (аудит seq 7): `claims_evidence: 53` токена,
   `included_chunks: {"claims_evidence": ["claim:c6aa88ea-…"]}` —
   строка `[c:c6aa88ea-…] В workspace создан файл notes/plan.md, …
   (hypothesis, E0, p=0.05)`.
2. **Фильтра по grade/epistemic_status в пак-пути НЕТ.**
   `retrieve()` (`packages/cognition/retrieval.py`) — только FTS-
   релевантность (ts_rank > noise floor, лимит 20 current + 5
   pending) и защита barrier-ancestors; `_fit_to_budget`
   (`packages/cognition/context.py`) — только токен-бюджет секции
   (8192). Отдельный лимит §5.4.2 (метка `[без действующей оценки:
   pending]` в той же строке, all-or-nothing) действует только для
   pending/invalid — якорь здесь `current` с действующей (хоть и E0)
   оценкой. Слабые current-claim'ы подаются — замысел.
3. **Куратор FU сам написал «предлагаю операцию перепроверки»**
   (аудит seq 17, `claim_created`, summary): «Утверждение соответствует
   уже установленному claim'у — предлагаю операцию перепроверки» — но
   `existing_claim_id: null`. Полувыволнение правила 7: намерение в
   свободном тексте, поле не заполнено → хост по конструкции создал
   новый claim (dedup T7.9 не сработал: statement отличается от
   якорного побайтово). Итог — ДВА claim'а на один факт
   (`c6aa88ea` E0 + `64b1761f` E2), перепроверки нет.

**Почему якорь `c6aa88ea-…` получил E0** (аудит `de32a56e` seq 19/24/26):

- куратор якоря предложил claim local_observation с **`evidence_links:
  []`** — и ни одной evidence-операции, хотя observation
  `workspace.read` (содержимое созданного им же файла) хост преобразовал
  в linkable-evidence `local_observation` (orchestrator.py:1689-1691,
  `apps/orchestrator/evidence.py:112-121`) и показал в списке evidence;
- вместо связывания evidence модель предложила 3 dependency-ребра на
  **выдуманные** claim id `efd81621-af47-3803-0000-0000e3f81621/22/23`
  («target missing» — отклонены на коммите, аудит seq 25
  `dependency_edge_rejected`; claim закоммичен без рёбер, fail-closed
  корректен) — тот же класс галлюцинации id, что `c0000000-…` в S1 v8,
  НЕСМОТРЯ ЯВНЫЙ запрет в curator-v6 (правило dependencies: «никогда не
  подставляй заглушки… и не выдумывай id»);
- rules engine на пустом наборе evidence: ветка `no_evidence`
  (`packages/memory/rules_engine.py:185-193`) → **E0/hypothesis,
  confidence 0.05, reasons `no_evidence`** — согласуется с
  `claim_type_rules.local_observation` config-v10 (min_support_evidence
  1, min_independence_groups 1, min_grade_for_supported E2 — всё
  невыполнимо при 0 evidence). Оценка — правильная, rules engine не
  причём.

**Классификация: хост ведёт себя по замыслу во всех точках** (E0 за
no_evidence — замысел; пак без grade-фильтра — замысел §5.4.2;
отказ рёбер — fail-closed). Дефекты — два, оба модельно-промптовые:

- **(Д1, корень пака)** якорь не связал ДАННОЕ evidence с claim
  (evidence_links: []) — промпт curator-v6 правило 1 говорит только
  «Evidence, не связанное с claim, не включай» (обратного — «каждый
  claim обязан иметь связанное evidence сессии, иначе — вопрос» — нет),
  и модель спутала `evidence_links` с `dependencies` (предложила
  зависимости на несуществующие claim'ы вместо ссылки на evidence).
  Улика: аудит `de32a56e` seq 19 (payload claim-операции:
  `evidence_links: []`, `dependencies` ×3 выдуманных) + seq 26
  (E0 `no_evidence`). **Предлагаемое исправление** (промпт, новая
  версия curator + payload): явное правило «claim создавай только с
  хотя бы одним связанным evidence из списка сессии (иначе — вопрос,
  а не claim); `evidence_links` — связь с evidence сессии,
  `dependencies` — только между claim'ами, id из раздела «Знание»».
  *(СЛЕДУЮЩИЙ СМОУК, SMOKE-V11-HALOGEN / T7.44b: исправление
  (curator-v7) СРАБОТАЛО — план-якорь с 1 evidence, E2/supported,
  E0-класс исчез, blind_provenance 6/6; детали — в том отчёте §4–5.)*
- **(Д2)** FU не заполнил `existing_claim_id`, написав в summary
  «перепроверка». Плюс промповый пробел: триггер правила 7 «если факт
  уже установлен» не покрывает случай СЛАБОГО (hypothesis/E0–E1)
  существующего claim'а — правильным действием была бы перепроверка с
  `supports` (подняла бы якорь E0→E2 и не создала бы дубли).
  Улика: аудит `9ec3807f` seq 17 (summary + `existing_claim_id: null`).
  **Предлагаемое исправление** (промпт): в правило 7 — негативный
  пример «перепроверка БЕЗ existing_claim_id — это новый claim, а не
  перепроверка» + явное: «если существующий claim слабый (hypothesis,
  E0–E1) и у тебя свежий evidence — используй перепроверку со
  supports, он поднимет оценку».
  *(СЛЕДУЮЩИЙ СМОУК, SMOKE-V11-HALOGEN / T7.44b: на halogen класс
  НЕ устранён — 0/7 claim-операций с непустым existing_claim_id,
  план-пак снова дал дубль; Python-пак засчитан гейтом 5 через dedup,
  а не через existing_claim_id. Не разделено: дефект правки vs
  модель. Детали — в том отчёте §4–5, §7.)*

## 6. Регрессия blind_provenance_path 7/7 → 5/6 (порог 0.9)

Blind-выборка = все 6 claim'ов рану (blind-size 10 > 6; seeded shuffle
`packages/evaluation/blind.py`). Структурная проверка
`_provenance_complete` (gates.py:648-696): claim → current assessment
→ связанное evidence (assessment_evidence) → source/artifact для
КАЖДОГО evidence; `if not evs: return False`.

| claim | evidence в current assessment | провенанс |
|---|---|---|
| `29a04a38` ООН | 2 (un.org + wikipedia, чужих ссылок 0) | ✅ |
| `e856cd0c` Python | 3 (2 якоря + 1 FU) | ✅ |
| `c6aa88ea` plan.md якорь | **0** | ❌ (`if not evs`) |
| `64b1761f` plan.md FU | 1 (local artifact, существует) | ✅ |
| `e0ce7b02` Спутник | 2 (wikipedia + nasa.gov) | ✅ |
| `c862ed69` Go | 2 (go.dev + wikipedia) | ✅ |

**Не прошёл ровно один claim — `c6aa88ea-…` (plan.md якорь): у его
current assessment (`0dcbe346-…`) НОЛЬ связанного evidence.** Причина —
та же, что в §5 (Д1): модель якоря не связала evidence. Это НЕ
кодовая регрессия и НЕ следствие правок curator-v6 (diff v5→v6 = только
пример правила 7; матрица — правка T7.38, и именно она сделала
local_observation-claim якоря ПРИНИМАЕМЫМ — в v8 всё предложение якоря
отклонялось `computed_result ← local_observation` и claim'а не было).
Механизм: состав claim'ов изменился (якорный claim появился, но без
evidence) → знаменатель 6, числитель 5.

**Почему всего 6 claim'ов против 7 в v8** (знаменатель
current_pending_invalid_ancestor 0/6 против 0/7 — тот же состав
current-heads):

| тема | v8 (7) | v10b (6) |
|---|---|---|
| ООН | 1 (external_fact E3) | 1 (temporal_fact E3) |
| Python | 2 (3.14.7 E3 + «3.15 pre-release» E1/hyp) | 1 (объединённое statement «3.14.7… 3.15 доступна только как pre-release» E3) |
| plan.md | 1 (только FU — якорь отклонён rules) | 2 (якорь E0 + FU E2 — дубли, §5) |
| Спутник | 1 (external_fact E3) | 1 (temporal_fact E3) |
| Go | 2 (1.27.1 E3 + «от 01.09.2026» E1/hyp) | 1 (объединённое statement E3) |
| итого | 7 | 6 = 7 − 2 (объединения Python/Go) + 1 (якорь plan.md принят) |

Модель v10b сжимала двойные факты (версия + дата/пререлиз) в одно
statement — формально корректно; вторые гипотетические claim'ы (E1)
исчезли.

## 7. Матрица тип↔evidence (правка (а) T7.38)

- **0 отказов rules engine во всех 7 сессиях** (в v8 — 2, оба убили
  паки): аудит — 0 строк с `curator_rejected_by_rules`, 0
  `reverify_unresolved`, 0 `request_rejected`; все 7 claim-операций
  закоммичены. Корень гибели обоих паков v8 устранён.
- Корректность типов по существу:

| claim | тип | формально | по существу |
|---|---|---|---|
| ООН `29a04a38` | temporal_fact (as_of 2026-04-15, якорь explicit) | ✅ | ✅ — вопрос «по состоянию на 15.04.2026» = факт на дату (в v8 было external_fact — оба допустимы) |
| Python `e856cd0c` | temporal_fact (relative) | ✅ | ✅ — «последняя стабильная на текущую дату» |
| plan.md `c6aa88ea` | local_observation | ✅ | ✅ — содержимое workspace-файла (сам пример из матрицы промпта) |
| plan.md `64b1761f` | local_observation | ✅ | ✅ — прочитанный файл: «содержимое файла прочитано → local_observation» (ответ на вопрос задачи: ДА, верно) |
| Спутник `e0ce7b02` | temporal_fact (as_of 1957-10-04T19:28:34, якорь none) | ✅ (as_of есть) | **спорно** — фиксированный исторический факт → natural type `external_fact` (как в v8); «temporal» в гайдлайне промпта = про «сейчас»/дату. Поведенческого вреда нет: якорь вопроса `none` → reverify_after NULL/evergreen |
| Go `c862ed69` | temporal_fact (relative) | ✅ | ✅ — «последняя стабильная на текущую дату» |

6/6 формально согласованы с evidence (0 отказов); 5/6 бесспорно
корректны по существу, 1 спорен (Спутник) — не дефект, наблюдение для
гайдлайна.

## 8. Новинки прогона — числами

### 8.1 T7.35 — пины промптов

- model_runs всего **38** (7 consolidating + 31 exploring; в v8 — 46:
  меньше exploration-шагов);
- **38/38** несут `prompt_version` И `prompt_sha256` (NULL — 0);
- совпадение с пинами config-v10: **38/38, расхождений 0** (curator-v6
  `a3dbccdcc6…` × 7; explorer-v4 `5829a55c…` × 31);
- `tool_schema_hash`: 31/31 exploring `f7628473…`; у 7 consolidating —
  NULL **по замыслу** (куратор вызывается без инструментов).

### 8.2 T7.36 — схема llamacpp-rocmfpx, токены

- ошибки грамматики схемы: **0** (run.log/worker.log/preflight.log — 0
  вхождений «failed to parse grammar»/«HTTP 400»/«unsupported keyword»;
  `output_schema_valid=false` — 0; `request_rejected` — 0);
- `finish_reason`: **38/38 `stop`** (`length` — 0);
- выходные токены (лимит 8192): мин 168, **медиана 521, максимум 3109** —
  ни одного приближения к лимиту, усечений нет.

### 8.3 T7.30/T7.32 — якорь даты, as_of, reverify_after, freshness

| claim | тип | якорь | as_of | reverify_after | freshness |
|---|---|---|---|---|---|
| `29a04a38` (ООН) | temporal_fact | **explicit** | 2026-04-15 | **NULL** | evergreen |
| `e856cd0c` (Python) | temporal_fact | **relative** | 2026-09-24 (дата сессии) | 2026-10-24 19:10:20.618 = МОМЕНТ ПЕРЕПРОВЕРКИ + 30 д | fresh |
| `c6aa88ea` (plan.md якорь) | local_observation | **none** | NULL | **NULL** | evergreen |
| `64b1761f` (plan.md FU) | local_observation | **none** | NULL | **NULL** | evergreen |
| `e0ce7b02` (Спутник) | temporal_fact | **none** | 1957-10-04 19:28:34 (модельный, исторический момент) | **NULL** | evergreen |
| `c862ed69` (Go) | temporal_fact | **relative** | 2026-09-24 (дата сессии) | 2026-10-24 19:17:30.960 = коммит 19:17:30.95 + 30 д | fresh |

Якорь из `assessed_scope.date_anchor` сохранённых оценок (host-scope-v1).
Ожидаемые формы: ООН (explicit) → NULL/evergreen ✅; Спутник (none,
без даты в вопросе) → NULL/evergreen ✅; Python/Go (relative) → as_of =
дата сессии, срок = момент проверки + 30 д ✅ (у Python — момент
перепроверки, §4.9). **Отклонений: 0/6.**

## 9. Статусы сессий: 4 succeeded / 3 succeeded_partial (в v8 — 2/5)

Все 4 succeeded — с `termination_reason` = ровно `goal_reached`
(`1db12bec`, `9ec3807f`, `fc740884`, `99ca4a8c`). Все 3 partial —
свободный текст (вывод T7.37b ПОДТВЕРЖДЁН дословно по
`sessions.termination_reason`):

| сессия | `termination_reason` (дословно, начало) |
|---|---|
| 1 (ООН) | `193 государств-члена ООН на 15 апреля 2026 года — подтверждено обоими указанными источниками (un.org: '193 Mem…` |
| 3 (plan.md-якорь) | `Файл создан и прочитан; количество пунктов плана = 3 (строки 2025-06-15, 2025-06-16, 2025-06-17), подтверждено…` |
| 5 (Python-FU) | `Вопрос отвечен и подтверждён: согласно обоим источникам последняя стабильная версия Python — Python 3.14.7; он…` |

Тот же известный класс: точное равенство `orchestrator.py:826`; все 3
сессии по смыслу достигли цели. Замечание: в v8 Python-якорь деградировал
с `goal_reached: <текст>` — в v10b модель дала точный код в 4/4
успешных сессий, но free-form остался (3/7).

Отказы инструментов (3 policy deny, против 9 в v8):

| сессия | инструмент | причина |
|---|---|---|
| 2 (Python-якорь) | python.execute | нет `code`, лишний `script` |
| 7 (Go) | artifact.create | unknown tool (нет в профиле curated-v1) |
| 1 (ООН) | research.fetch | лишний аргумент `format` |

Хост fail-closed корректен; слабость K2 к tool-схемам сохраняется, но
слабеет (9 → 3). Плюс 2 action_failed `non-200 status: 404`:
сессия 5 — лишний допзапрос `python.org/versions/` (страницы нет);
сессия 7 — повторный запрос той же wikipedia-страницы с обрезанной
скобкой в URL (`…Go_(язык_программирования` без `)`) — модель
повторилась корректным URL и завершилась.

## 10. Итоговые гейты прогона (как есть; N=7 — не приёмка)

Из `evaluation_runs.gates` (run id `213b035c-…`), Wilson ci95:

| гейт | исход | числ/знам | ci95 | порог |
|---|---|---|---|---|
| new_supported_refuted_e2 | insufficient_sample | 5/5 | [0.5655, 1.0] | 0.8 |
| external_temporal_e3 | insufficient_sample | 4/4 | [0.5101, 1.0] | 1.0 |
| eligible_sessions_with_outcome | insufficient_sample | 7/7 | [0.6457, 1.0] | 0.6 |
| near_duplicate_questions | insufficient_sample | 0/7 | [0.0, 0.3543] | 0.15 |
| **significant_claim_reuse** | insufficient_sample | **1/5** | [0.0362, 0.6245] | 0.25 |
| due_stale_time_sensitive | insufficient_sample | 0/2 | [0.0, 0.6576] | 0.2 |
| reassessment_slo | insufficient_sample | 0/0 | — | 1.0 |
| current_pending_invalid_ancestor | insufficient_sample | 0/6 | — | 0 |
| high_severity_incidents | **passed** | 0/None | — | 0 |
| blind_provenance_path (структурный) | insufficient_sample | 5/6 | [0.4365, 0.9699] | 0.9 |
| blind_scope (структурный) | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.8 |

`outcome=insufficient_sample` (N=7 < 20). Blind-гейты — структурная
проверка; приёмка §22.2 требует ручной ревизии
(`noezemactl blind-sample --run 213b035c-da7c-474c-bcc1-9357dabc2791`).

Воркер: 80 reassessment-tick + 80 reconcile-tick (160 тиков), после
каждого — тот же traceback-блок «Event loop is closed» (320 строки),
что в v8 §6: teardown-дефект hostctl CLI (dispose на втором
`asyncio.run`) НЕ исправлен в `5db8ca3` (задача не стояла). Вред тот же
— лог-шум; тики завершались штатно (80 × `deferred=True` — сессии в
полёте, 80 × `nothing to do`).

## 11. Сводное сравнение SMOKE-V8-K2 (curator-v4) vs SMOKE-V10B-K2 (curator-v6)

| | v8 | v10b | Δ |
|---|---|---|---|
| succeeded / partial | 2 / 5 | 4 / 3 | +2 succeeded / −2 partial (free-form: 5→3) |
| claims | 7 | 6 | −1 (объединения Python/Go −2, якорь plan.md +1) |
| значимые claims (знаменатель гейта 5) | 5 | 5 | = |
| **significant_claim_reuse (гейт 5)** | **0/5** | **1/5** | **+1 — перепроверка сработала впервые** |
| `existing_claim_id` заполнен | 0/7 | 1/7 | +1 (полный UUID, не префикс) |
| `claim_reverified` в аудите | 0 | 1 | +1 |
| отказы rules engine (предложения куратора) | 2 (S3, S4 — убили оба пака) | 0 | −2 — матрица (а) T7.38 сработала |
| выдуманные id | 1 (S1, dependency `c0000000-…`) | 3 (S3, dependencies `efd81621-…` ×3) | +2 — класс сохраняется |
| blind_provenance_path | 7/7 | 5/6 | −2 (claim без evidence, §6) |
| blind_scope | 7/7 | 6/6 | состав (знаменатель = 6) |
| model_runs | 46 | 38 | −8 (меньше шагов) |
| пины расходятся | 0 | 0 | = |
| finish_reason=stop | 46/46 | 38/38 | = |
| ошибки грамматики | 0 | 0 | = |
| policy deny (tool-схемы) | 9 | 3 | −6 |
| worker «Event loop is closed» | 206 блоков | 160 блоков | = (дефект не исправлен) |
| длительность | 23,9 мин | 18,0 мин | −5,9 мин |

**Вывод: что дали правки промпта (curator-v4 → v6 через T7.38/T7.39):**
(1) матрица type↔evidence — 0 отказов rules engine (корень гибели обоих
паков v8 устранён; якорь plan.md теперь создаёт claim); (2) пример
правила 7 — K2 ИСПОЛЬЗОВАЛ перепроверку впервые: полный путь
(паковый `[c:…]` → `existing_claim_id` полным UUID → резолюция →
fenced commit → `claim_reverified` + новая assessment-строка →
head-переезд → сдвиг `reverify_after` к моменту проверки), гейт 5
1/5; (3) безопасный пример T7.39 — заглушка из промпта НЕ скопирована
(`118b76b3-…` в ране не встречается); (4) «dependencies: []» — S1-класса
заглушки в dependencies больше нет (но появились выдуманные id — см.
ниже). **Что осталось нерешённым:** (а) якорь не связывает evidence с
claim (Д1 → E0 → второй пак всё равно не перепроверил + blind 5/6);
(б) полувыволнение правила 7 (Д2 — «предлагаю операцию перепроверки» в summary, null в
поле); (в) выдуманные dependency-id сохраняются (3 шт.); (г) free-form
`complete_reason` (3/7 partial — host-фикс T7.37b п.3 не сделан);
(д) hostctl teardown-шум (п.4 не сделан).

## 12. Предложения (по приоритету)

1. **(Высокий, Д1) Промпт: обязательное связывание evidence.**
   «claim — только с хотя бы одним связанным evidence из списка сессии;
   иначе — вопрос. `evidence_links` — evidence сессии, `dependencies` —
   только claim'ы из раздела «Знание»». Новая версия curator + payload
   (как T7.38/T7.39). Устранит: E0-якорь, второй неперепроверенный пак
   (дубли), blind_provenance 5/6 и уберёт стимул путать evidence_links
   с dependencies (выдуманные id). — **сделано в T7.43** (curator-v7,
   config-v11); **проверено в SMOKE-V11-HALOGEN (T7.44b): сработало**
   (план-якорь E0→E2, blind_provenance 6/6, выдуманных id 0) и **на K2 в
   SMOKE-V12-K2 (T7.45b)** (план-якорь E2×2, blind 6/6 — эффект
   модельно-независимый, промптовый).
2. **(Высокий, Д2) Промпт: правило 7 — негативный пример + случай
   слабого claim'а.** «перепроверка БЕЗ existing_claim_id — это новый
   claim» + «существующий claim hypothesis/E0–E1 и есть свежий evidence
   → перепроверка со supports поднимет оценку». Улика: `9ec3807f` seq 17.
   — **сделано в T7.43** (curator-v7, config-v11); **проверено в
   SMOKE-V11-HALOGEN (T7.44b): на halogen НЕ сработало** (0/7
   existing_claim_id, 0 claim_reverified; план-пак — дубль; Python-пак
   засчитан через dedup). Разделение «правка vs модель» требовало смоука
   curator-v7+K2 (см. SMOKE-V11-HALOGEN-report.md §7–10) — **выполнен:
   SMOKE-V12-K2 (T7.45b), на K2 сработало** (2/2 FU заполнили
   `existing_claim_id` полным UUID, 2 `claim_reverified`, гейт 5 = 2/5;
   неиспользование поля halogen — модельная особенность, правка достаточна —
   см. SMOKE-V12-K2-report.md §4–5, §10).
3. **(Средний) Мягкая нормализация `complete_reason`** (host-фикс,
   SMOKE-V8-K2 п.3) — 3/7 partial из-за точного равенства.
4. **(Средний) Teardown hostctl CLI** (SMOKE-V8-K2 п.4): dispose в том
   же `asyncio.run` — 160 traceback-блоков.
5. **(Низкий) Следить за tool-схемами K2** (3 deny: `python.execute` с
   `script` вместо `code`, `artifact.create` unknown ×1, `format` у
   fetch ×1) — тренд положительный (9 → 3).
6. **(Низкий) Гайдлайн типа для исторических фактов** (Спутник
   temporal_fact — спорно; «фиксированный исторический факт →
   external_fact») — входит в правку п.1/п.2.

## 13. Не тронуто (инварианты)

- Код, тесты, payload'ы (v2…v10), корпуса, ARCHITECTURE.md — без
  изменений (задача — только анализ); HEAD `5db8ca3` до коммита отчёта.
- БД `noezema-smoke-v10b-k2`, `noezema-smoke-v10-k2` (прерванный ран —
  улика), `noezema-smoke-v8-k2`, `noezema-eval*` — **только SELECT**.
- Прогоны не запускались, к LLM на 192.168.1.48 обращений не было;
  фоновых процессов не осталось (воркер остановлен, все юниты inactive,
  orphan-лог-тейлеры убраны).
