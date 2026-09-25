# SMOKE-V11-HALOGEN — разбор смоук-прогона (T7.44b)

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, промпты,
корпуса, ARCHITECTURE.md не тронуты). Прогон — повторный смоук на
config-v11 (curator-v7) после SMOKE-V10B-K2 (T7.43); остановка воркера
и разбор — T7.44b. Дата: 2026-09-25.

> **Ограничение (важно):** в этом прогоне изменились ДВЕ переменные
> разом — промпт куратора curator-v6 → curator-v7 И модель K2 →
> halogen-flash-next. Это **не чистый эксперимент**: эффект правки
> промпта и эффект смены модели не разделены. Там, где это невозможно
> разделить однозначно, это явно помечено (§7). Для чистого сравнения
> нужен дополнительный смоук curator-v7 + K2 (§8, вывод). **Выполнен —
> SMOKE-V12-K2 (T7.45b); итог — в §10 ниже и в SMOKE-V12-K2-report.md.**

## 1. Условия прогона

| Параметр | Значение |
|---|---|
| код | `4442f48` (ветка `impl/from-scratch`, дерево чистое, = origin; 964 passed + 8 skipped) |
| модель | `halogen-flash-next` (движок .48:8080) |
| профиль схемы | `halogen` (T7.23, ADR-0012; профиль без `format`/`pattern`, ответ валидируется хостом полной pydantic-моделью) |
| payload | `docs/eval/config-v11-payload.json`: file sha256 `67468a2e…`, canonical `a407ce83…`; пины: curator-v7 `19d6c6e8…`, explorer-v4 `5829a55c…`, planner-v1 / verifier-v1 / extractor-v1 — отличие от config-v10 (SMOKE-V10B-K2) ТОЛЬКО `prompts.curator` (curator-v6 → curator-v7: обязательное связывание evidence + правило 7 — негативный пример и случай слабого claim'а, T7.43) |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha256 `b3e05ad5d206…`) — побайтово тот же, что в v8/v10b: 2 пака под перепроверку (Python: якорь 90 + FU 80 «Сверь ответ с ранее зафиксированной версией»; notes/plan.md: якорь 90 + FU 80), ООН (100, явная дата), Go (0), Спутник-1 (0, без даты) |
| правила | rules-v2 (rules_hash не менялся с v10) |
| БД | `noezema-smoke-v11-halogen` (создана запуском, миграции до 0025) |
| run | id `9d256ed9-0ae1-422a-b413-033ae5bce943`, snapshot `2db645a0-…`, seed 20260924 (общий для серии v8/v10b/v11 — сопоставимость blind-выборки), slo 3600 s, blind-size 10 |
| время | 2026-09-25 09:16:41Z → 10:14:10Z (57,5 мин), eval-run EXIT=0 |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s |

Воркер остановлен T7.44b: до остановки — SELECT: 7/7 сессий в
терминальном состоянии (6 succeeded + 1 succeeded_partial), 7/7
commit_attempts = `committed`, 0 нетерминальных; `systemctl --user stop
smoke-v11-halogen-worker` → inactive; юнит `smoke-v11-halogen-run` уже
unload (transient, `could not be found`); все 6 юнитов `smoke-v8-k2-*` /
`smoke-v10-k2-*` / `smoke-v10b-k2-*` — inactive; процессов hostctl /
worker.sh / tick'ов не осталось; orphan-лог-тейлер прошлой задачи
(task37) убран.

## 2. Сводка

7 сессий: 6 succeeded (1, 2, 3, 4, 5, 6), 1 succeeded_partial (7).
`outcome=insufficient_sample` (N=7 < MIN_SAMPLE 20) — ожидаемо при N=7;
это НЕ приёмка §22.2.

| # | сессия | вопрос (приоритет) | состояние | шаги, время | claims | причина partial (аудит) |
|---|---|---|---|---|---|---|
| 1 | `c9c122b3` | ООН, 15.04.2026 (100) | succeeded | 4, 268 с | 1 | — (goal_reached точным равенством) |
| 2 | `c27a9620` | Python-якорь (90) | succeeded | 6, 770 с | 1 | — |
| 3 | `2244cebe` | plan.md-якорь (90) | succeeded | 4, 132 с | 1 | — (claim с 1 evidence → E2, §4) |
| 4 | `b52c29bb` | plan.md-FU (80) | succeeded | 2, 47 с | 1 | — (goal_reached; НОВЫЙ дубль-claim вместо перепроверки, §5) |
| 5 | `75295140` | Python-FU (80) | succeeded | 3, 226 с | 0 новых | — (goal_reached; перепроверка через dedup, §4) |
| 6 | `04fbf59c` | Спутник-1 (0) | succeeded | 5, 581 с | 1 | — |
| 7 | `1bcf6776` | Go (0) | succeeded_partial | 7, 1422 с | 1 | complete_reason — `goal_reached: <текст>` (не точное равенство), §6 |

Терминал — тем же правилом (`apps/orchestrator/orchestrator.py:826`):
`complete_reason == "goal_reached"` точным равенством → `succeeded`,
иначе (без unknown) → `succeeded_partial`.

## 3. Гейт 5 = 1/6 (против 1/5 в v10b; знаменатель 6, а не 5)

Пересчитан собственным SELECT по формуле `_gate_reuse`
(`packages/evaluation/gates.py:408-479`, пять UNION-веток) — совпало с
сохранённым значением (`evaluation_runs.gates`: numerator 1, denominator
6, ci95 [0.0301, 0.5635]).

Значимые claims (head `current`, supported|disputed|refuted, grade ≥ E2):

| claim | сессия | тип | grade | значим |
|---|---|---|---|---|
| `970c1ac8` (ООН 193) | 1 | temporal_fact | E3/supported | ✅ |
| `5208f25c` (Python 3.14.7) | 2 | temporal_fact | E3/supported | ✅ |
| `de62dddd` (plan.md, якорь) | 3 | local_observation | **E2/supported** | ✅ (в v10b был E0 → НЕ значим) |
| `62611669` (plan.md, FU) | 4 | local_observation | E2/supported | ✅ |
| `891d13b7` (Спутник 4.10.1957) | 6 | temporal_fact | E3/supported | ✅ |
| `a68ef805` (Go 1.27.1) | 7 | temporal_fact | E3/supported | ✅ |

**Почему знаменатель 6, а не 5 (в v10b было 5):** состав claim'ов
идентичен (те же 6 тем: ООН, Python, plan-якорь, plan-FU, Спутник, Go);
единственное отличие — якорь plan.md. В v10b он был **E0/hypothesis**
(claim создан без evidence, дефект Д1) → не значим → вычеркнут из
знаменателя (5). В v11 правка curator-v7 (а) заставила якорь связать
evidence → он **E2/supported** → значим → вошёл в знаменатель (6).
**Знаменатель вырос ровно на 1 — прямое следствие сработавшей правки
(а).** Числитель остался 1: значимых claim'ов, тронутых ≥2 сессиями, —
ровно Python-якорь `5208f25c` (сессии 2 + 5) — и по ветке evidence
(новое source_assertion от FU), и по ветке claim_assessments
(строка-перепроверка `5cd864cb`). plan-пак в числителе НЕ участвует:
якорь `de62dddd` тронут только сессией 3, а FU (4) создал отдельный
дубль-claim `62611669`, якоря не тронув.

## 4. ПЕРВЫЙ ПАК (Python): перепроверка записана — но через DEDUP, а не через `existing_claim_id`

Якорь `5208f25c-…` (полный UUID — в БД), сессия 2 (`c27a9620`):
`evidence_links` = 2 записи (python.org + chocolatey), committed-claim
E3/supported, evidence-строк 3 (2 якоря + 1 FU).

FU `75295140` (сессия 5, prio 80 «…Сверь ответ с ранее зафиксированной
версией»):

1. **Якорь был в паке FU** (`context_packed`, аудит seq 8):
   `included_chunks: {"claims_evidence": ["claim:5208f25c-…"]}`.
2. **Куратор FU НЕ заполнил `existing_claim_id`** (аудит seq 24,
   payload claim-операции: `"existing_claim_id": null`). В `summary`
   написано «…Предлагаю операцию перепроверки существующего claim, а не
   новый claim» — **снова слова, не поле** (класс Д2 из v10b, правка (б)
   его не устранила).
3. **Но statement FU побайтово = statement якоря** (md5
   `6eecaa4b…` у обоих: «Последняя стабильная версия Python — 3.14.7»):
   дедуп T7.9 (побайтовый statement+type) сработал. Коммит FU (аудит seq
   32): `memory: {claims_reused: 1, claims_created: 0, evidence_added: 1,
   evidence_deduped: 1, assessments: 1}`.
4. **Запись перепроверки = новая строка claim_assessments** `5cd864cb-…`
   с `created_in_session = 75295140` (якорная строка `e95a347b-…` —
   `c27a9620`). Head переехал на `5cd864cb`. Оценка не изменилась
   (E3/supported/0.75 → E3/supported/0.75), `evidence_set_hash`
   сменился — в набор добавилось новое evidence FU (`8738f150…`);
   перезапрос python.org схлопнулся в строку якоря по identity
   (`evidence_deduped: 1`).
5. **События `claim_reverified` в ране — 0** (в v10b было 1, у
   Python-FU с `existing_claim_id`). Путь явной перепроверки (ADR-0018,
   `existing_claim_id`) на halogen **не сработал ни разу за весь ран**
   (0/7 claim-операций с непустым `existing_claim_id`); запись
   существует только потому, что дедуп по совпадению statement'ов тоже
   порождает assessment-строку (ADR-0018: «dedup-переиспользование …
   тоже становится измеримым гейтом 5»).

Вывод: гейт 5 засчитал Python-пак, но **механизм другой, чем в v10b**:
v10b — явная перепроверка (`existing_claim_id` полным UUID — робастный
путь), v11 — неявный дедуп (statement совпал побайтово — хрупкий путь:
достаточно одного слова перефразировки, и записи не появилось бы). На
halogen модель цитирует statement якоря дословно, а K2 в v10b
перефразировал (и потому был вынужден использовать `existing_claim_id`).

## 5. ВТОРОЙ ПАК (plan.md): якорь исправлен (правило (а) сработало), FU снова не перепроверил

Якорь `de62dddd-…` (сессия 3, `2244cebe`): **`evidence_links` = 1
запись** (local_observation — содержимое созданного им же файла,
`workspace.read`, evidence_index 0), committed-claim **E2/supported,
1 evidence** (`b9906f40…`). **В v10b этот же якорь `c6aa88ea` был
создан с `evidence_links: []` → E0/hypothesis (Д1) и провалил
blind_provenance (5/6); здесь правка (а) curator-v7 сработала: claim с
обязательным evidence, E0-класса нет, blind_provenance 6/6.**
Выдуманных dependency-id в этом ране нет (в v10b — 3, `efd81621-…`).

FU `b52c29bb` (сессия 4, prio 80 «Прочитай файл notes/plan.md… и
подтверди, сколько пунктов»):

1. **Якорь `de62dddd-…` БЫЛ в паке FU** (`context_packed`, seq 7):
   `included_chunks: {"claims_evidence": ["claim:de62dddd-…"]}`.
2. **Куратор FU НЕ заполнил `existing_claim_id`** (аудит seq 17:
   `"existing_claim_id": null`), хотя в `summary` — «Предлагаю
   перепроверку существующего claim, а не создание нового дубля».
3. **Statement FU НЕ совпал с якорем побайтово**: якорь — «В файле
   notes/plan.md **содержится** три пункта плана встречи», FU — «В
   файле notes/plan.md **записано** три пункта плана встречи» (md5
   различаются) → дедуп НЕ сработал.
4. **Итог — новый дубль-claim** `62611669-…` (local_observation,
   E2/supported, 1 evidence), якорь не тронут, перепроверки нет,
   гейт 5 план-пак не засчитал. **Тот же класс, что Д2 v10b: намерение
   «перепроверка» в свободном тексте, поле не заполнено, statement
   перефразирован.**

**Классификация дефектов curator-v7 на halogen:**

- **(а) обязательное связывание evidence — СРАБОТАЛО.** Оба якоря
  связали evidence (Python: 2, plan.md: 1); E0-claims нет;
  blind_provenance 6/6; выдуманных id нет. Улика: аудит `2244cebe` seq
  22 (`evidence_links` на evidence_index 0) + committed-claim
  `de62dddd` E2/1-evidence (против v10b `de32a56e` seq 19/26:
  `evidence_links: []` → E0).
- **(б) правило 7 — негативный пример + «слова не операция» — НЕ
  сработало.** 0/7 claim-операций с непустым `existing_claim_id`
  (в v10b было 1/7); оба FU написали «перепроверка» в summary и
  оставили поле null. Улика: аудит `75295140` seq 24 и `b52c29bb`
  seq 17 (`existing_claim_id: null` + «предлагаю перепроверку» в
  summary); `claim_reverified` = 0 за ран. Python-пак спасло
  побайтовое совпадение statement'ов (dedup), plan-пак — нет (дубль).

Хост во всех точках — по замыслу (dedup T7.9, rules engine,
fail-closed). Дефект модельно-промптовый: halogen, как и K2 в v10b,
выражает намерение перепроверки словами, не заполняя поле.

## 6. Последняя сессия (7, Go) — succeeded_partial, 1422 с

**Это вопрос Go, а НЕ Спутник** (предположение в постановке неверно:
Спутник — сессия 6, 581 с, succeeded). Go-сессия `1bcf6776` (prio 0,
7 шагов, 09:50:28 → 10:14:10 = 1422 с — самая долгая за все смоуки):

Цикл по аудиту:
1. 2 `research.fetch` — go.dev/dl + ru.wikipedia.org `Go_(язык_программирования` (оба успешно, seq 11–17);
2. **policy deny `artifact.create`** (seq 19) — инструмент нет в профиле curated (хост fail-closed корректен);
3. **ранний complete ОТКЛОНЁН** (seq 20, `source_coverage_incomplete`: «1 of 2 named sources not yet fetched» — uncovered: wikipedia-URL);
4. **повторный fetch — 404** (seq 23, `non-200 status: 404`): URL в вызове **обрезан** — `https://ru.wikipedia.org/wiki/Go_(язык_программирования` **без закрывающей скобки** (тот же класс, что Go-сессия K2 в v10b: обрезанная скобка → 404);
5. **refetch** (seq 25–27) — успешный, тот же content-hash `3843e2e8…` (страница не изменилась);
6. explorer завершил `goal_reached: Последняя стабильная версия Go — 1.27.1` (seq 28) — **свободный текст с префиксом, не точное равенство** → `succeeded_partial`;
7. staging 5 операций → куратор: 1 claim + 1 вопрос → fenced commit → claim `a68ef805` E3/supported (relative, reverify_after = коммит + 30 д).

Признаки затруднения: deny `artifact.create`, отклонённый ранний
complete (coverage), 404 по обрезанному URL, refetch. Длительность
(1422 с против 342 с у K2 в v10b) — комбинация (а) базовой медлительности
halogen и (б) именно этого цикла отказов/повторов на 2-источниковом
вопросе. Созданный вопрос `444efb44` («Почему в Википедии дата выпуска
Go 1.27.1 указана как 1 сентября 2026, что позже текущей даты
2026-06-15?») — легитимный (обнаружено противоречие дат), не дубликат.

## 7. Что относится к правкам curator-v7, что к смене модели K2→halogen

**К правкам curator-v7 (промпт; должно воспроизводиться независимо от модели):**
- (а) обязательное связывание evidence — **сработало** (оба якоря;
  plan-якорь E0→E2; E0-класс исчез; blind_provenance 5/6 → 6/6;
  знаменатель гейта 5 5→6 — прямое следствие). В v10b (curator-v6, K2)
  того же правила не было — якорь план.md не связал evidence; здесь
  halogen его связал. Направленный эффект правки виден, но строго
  «промпт, а не модель» — только при контроле на K2 (§8).
- (б) негативный пример + «слова не операция» — **не сработало**
  (0/7 `existing_claim_id`, оба FU — «слова в summary»). Но
  **нельзя однозначно** отнести к промпту: в v10b K2 под curator-v6
  заполнил `existing_claim_id` (1/7). Либо правка (б) слабее v6-правила
  7 (промповый дефект), либо halogen просто реже использует поле
  (модельная особенность). Разделить нельзя без curator-v7+K2.
- Выдуманных id нет (0, против 3 в v10b): может быть и следствием (а)
  (убран стимул путать evidence_links/dependencies), и модельной
  особенностью — не разделить.

**К смене модели K2→halogen (специфично для halogen):**
- Скорость: halogen существенно медленнее (таблица §8.3; итог 57,5 мин
  против 18,0).
- Точность `goal_reached`: halogen 6/7 (K2 v10b 4/7) — partial только Go
  (`goal_reached: <текст>`); у K2 в v10b 3 partial с полностью
  свободным текстом. Halogen стабильнее даёт точный код.
- `artifact.create` (unknown tool) — 5 deny (K2 v10b: 1); плюс 1 fetch-404
  с обрезанным URL (у K2 v10b был тот же класс ×1) — слабость к
  tool-схемам сохраняется, класс шире.
- Выходные токены: максимум 7419 (лимит 8192 — близко) против 3109 у K2.
- Поведение statement'ов: halogen цитирует statement якоря побайтово
  (Python-FU) → дедуп; K2 перефразировал → был вынужден к
  `existing_claim_id`. Механизм записи перепроверки Python-пака
  (dedup vs reverify) — модельно-зависим.

**Не разделено однозначно (требует curator-v7+K2):** эффект правки (а)
(промпт vs общая «дисциплинированность» halogen), причина не-использования
`existing_claim_id` (дефект правки (б) vs особенность модели),
отсутствие выдуманных id.

## 8. Сравнение с SMOKE-V10B-K2 по пунктам

### 8.1 Типы claim'ов (формально и по существу)

| claim | тип | формально | по существу |
|---|---|---|---|
| ООН `970c1ac8` | temporal_fact (explicit, as_of 2026-04-15) | ✅ | ✅ (в v10b то же) |
| Python `5208f25c` | temporal_fact (relative, as_of 2026-09-25) | ✅ | ✅ |
| plan.md `de62dddd` (якорь) | local_observation (none) | ✅ | ✅ |
| plan.md `62611669` (FU) | local_observation (none) | ✅ | ✅ (дубль якоря, §5) |
| Спутник `891d13b7` | temporal_fact (none, as_of 1957-10-04) | ✅ (as_of есть) | **спорно** — фиксированный исторический факт → natural type `external_fact` (наблюдение то же, что в v10b — не дефект) |
| Go `a68ef805` | temporal_fact (relative, as_of 2026-09-25) | ✅ | ✅ |

6/6 формально согласованы (0 отказов rules engine за весь ран: 0
`curator_rejected_by_rules`, 0 `request_rejected`, 0 `reverify_unresolved`);
5/6 бесспорно по существу, 1 спорен (Спутник).

### 8.2 T7.35 — пины промптов

- model_runs всего **38** (7 consolidating + 31 exploring — как в v10b);
- **38/38** несут `prompt_version` И `prompt_sha256` (NULL — 0);
- совпадение с пинами config-v11: **38/38, расхождений 0** (curator-v7
  `19d6c6e8e2d8…` × 7 — = файлу репо побайтово; explorer-v4
  `5829a55c…` × 31 — = пину v10);
- `tool_schema_hash`: 31/31 exploring `f7628473…`; у 7 consolidating —
  NULL по замыслу (куратор без инструментов).

### 8.3 T7.36 — схема под профилем halogen, токены; длительности

- ошибки схемы: **0** по всем паттернам («unsupported keyword» halogen — 0;
  «failed to parse grammar» K2 — 0; «HTTP 400» — 0;
  `output_schema_valid=false` — 0; `request_rejected`/`curator_error`/
  `PromptPinError` — 0 в логах и в аудите);
- `finish_reason`: **38/38 `stop`** (length — 0);
- выходные токены (лимит 8192): мин 243, медиана 1796,5, **макс 7419**
  (у K2 v10b: мин 168, медиана 521, макс 3109) — halogen ближе к лимиту,
  усечений нет.

Длительности session-by-session (v10b K2 → v11 halogen):

| # | вопрос | v10b (K2) | v11 (halogen) |
|---|---|---|---|
| 1 | ООН | 114 с | 268 с |
| 2 | Python-якорь | 185 с | 770 с |
| 3 | plan.md-якорь | 30 с | 132 с |
| 4 | plan.md-FU | 26 с | 47 с |
| 5 | Python-FU | 288 с | **226 с** (быстрее) |
| 6 | Спутник | 87 с | 581 с |
| 7 | Go | 342 с | 1422 с (цикл отказов, §6) |
| итого | | 18,0 мин | 57,5 мин |

Halogen медленнее на 5 из 6 первых сессий (×1,8…×6,7); исключение —
Python-FU (226 с против 288 с: у v10b FU делал полную явную перепроверку
с 2 источниками и доп-запросом, здесь — дедуп-коммит за 3 шага).

### 8.4 T7.30/T7.32 — якорь даты, as_of, reverify_after, freshness

| claim | тип | якорь | as_of | reverify_after | freshness |
|---|---|---|---|---|---|
| `970c1ac8` (ООН) | temporal_fact | **explicit** | 2026-04-15 | **NULL** | evergreen |
| `5208f25c` (Python) | temporal_fact | **relative** | 2026-09-25 | 2026-10-25 09:40:46 = МОМЕНТ ПЕРЕПРОВЕРКИ (09:40:46) + 30 д | fresh |
| `de62dddd` (plan якорь) | local_observation | **none** | NULL | **NULL** | evergreen |
| `62611669` (plan FU) | local_observation | **none** | NULL | **NULL** | evergreen |
| `891d13b7` (Спутник) | temporal_fact | **none** | 1957-10-04 | **NULL** | evergreen |
| `a68ef805` (Go) | temporal_fact | **relative** | 2026-09-25 | 2026-10-25 10:14:10 = коммит 10:14:10 + 30 д | fresh |

Ожидаемые формы: explicit → NULL/evergreen ✅; none → NULL/evergreen ✅;
relative → as_of = дата сессии, срок = момент проверки + 30 д ✅ (у
Python — момент перепроверки). **Отклонений: 0/6** (как в v10b).

### 8.5 Статусы сессий: 6 succeeded / 1 partial (в v10b — 4/3)

Все 6 succeeded — `termination_reason` = ровно `goal_reached`. Единственный
partial — Go (7): `goal_reached: Последняя стабильная версия Go — 1.27.1`
(префикс + текст, не точное равенство). **Halogen стабильнее K2**: точный
код в 6/7 (K2 v10b — 4/7; v8 — 2/7). Это модельная особенность: host-фикс
нормализации `complete_reason` (T7.37b п.3) так и не сделан, точное
равенство `orchestrator.py:826` — единственное правило; halogen просто
реже «дописывает» текст после кода.

Отказы инструментов: 5 policy deny (`artifact.create` unknown tool ×5 —
класс слабости tool-схем шире, чем у K2 v10b (1)) + 1 `action_failed`
fetch 404 (обрезанный URL, §6). Хост fail-closed корректен во всех точках.

## 9. Итоговые гейты прогона (как есть; N=7 — не приёмка)

Из `evaluation_runs.gates` (run id `9d256ed9-…`), Wilson ci95:

| гейт | исход | числ/знам | ci95 | порог |
|---|---|---|---|---|
| new_supported_refuted_e2 | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.8 |
| external_temporal_e3 | insufficient_sample | 4/4 | [0.5101, 1.0] | 1.0 |
| eligible_sessions_with_outcome | insufficient_sample | 7/7 | [0.6457, 1.0] | 0.6 |
| near_duplicate_questions | insufficient_sample | 0/7 | [0.0, 0.3543] | 0.15 |
| **significant_claim_reuse** | insufficient_sample | **1/6** | [0.0301, 0.5635] | 0.25 |
| due_stale_time_sensitive | insufficient_sample | 0/2 | [0.0, 0.6576] | 0.2 |
| reassessment_slo | insufficient_sample | 0/0 | — | 1.0 |
| current_pending_invalid_ancestor | insufficient_sample | 0/6 | — | 0 |
| high_severity_incidents | **passed** | 0/None | — | 0 |
| blind_provenance_path (структурный) | insufficient_sample | **6/6** | [0.6097, 1.0] | 0.9 |
| blind_scope (структурный) | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.8 |

`outcome=insufficient_sample` (N=7 < 20). Blind-гейты — структурная
проверка; приёмка §22.2 требует ручной ревизии
(`noezemactl blind-sample --run 9d256ed9-0ae1-422a-b413-033ae5bce943`).
blind_provenance 6/6 — регрессия v10b (5/6, claim без evidence)
**устранена** (правило (а)).

Воркер: 233 reassessment-tick + 233 reconcile-tick (466 тиков), после
каждого — тот же traceback-блок «Event loop is closed» (932 строки) —
teardown-дефект hostctl CLI (dispose на втором `asyncio.run`) НЕ исправлен
в `4442f48` (задача не стояла); вред тот же — лог-шум, тики завершались
штатно.

## 10. Сводное сравнение SMOKE-V8-K2 (curator-v4) → SMOKE-V10B-K2 (curator-v6) → SMOKE-V11-HALOGEN (curator-v7)

| | v8 (v4, K2) | v10b (v6, K2) | v11 (v7, halogen) |
|---|---|---|---|
| succeeded / partial | 2 / 5 | 4 / 3 | **6 / 1** |
| claims | 7 | 6 | 6 |
| значимые claims (знаменатель гейта 5) | 5 | 5 | **6** (plan-якорь E0→E2) |
| **significant_claim_reuse (гейт 5)** | 0/5 | 1/5 | **1/6** |
| механизм записи перепроверки Python-пака | — (0) | `existing_claim_id` полный UUID | **dedup** (statement побайтово; `existing_claim_id` null) |
| `existing_claim_id` заполнен | 0/7 | 1/7 | **0/7** (правка (б) не сработала) |
| `claim_reverified` в аудите | 0 | 1 | **0** |
| план-якорь | отклонён rules (claim'а нет) | E0/hypothesis, 0 evidence | **E2/supported, 1 evidence** (правило (а) сработало) |
| план-FU | новый claim (якоря не было) | дубль (Д2) | **дубль (Д2 повторился)** |
| отказы rules engine | 2 (убили оба пака) | 0 | 0 |
| выдуманные id | 1 | 3 | **0** |
| blind_provenance_path | 7/7 | 5/6 | **6/6** |
| model_runs | 46 | 38 | 38 |
| пины расходятся | 0 | 0 | 0 |
| finish_reason=stop | 46/46 | 38/38 | 38/38 |
| ошибки схемы | 0 | 0 | 0 |
| policy deny (tool-схемы) | 9 | 3 | 5 (+1 fetch-404) |
| worker «Event loop is closed» | 206 блоков | 160 блоков | 932 строки (466 блоков) |
| длительность | 23,9 мин | 18,0 мин | 57,5 мин (halogen) |

**Вывод: сработали ли правки curator-v7.**
- **(а) обязательное связывание evidence — сработало** (и это главное
  достижение прогона): E0-якорь исчез, план-якорь E2, blind_provenance
  6/6, знаменатель гейта 5 вырос 5→6, выдуманных id нет.
- **(б) правило 7 (негативный пример + «слова не операция») — не
  сработало на halogen**: 0/7 `existing_claim_id`, 0
  `claim_reverified`; оба FU — «слова в summary». Числитель гейта 5
  остался 1 только благодаря побайтовому совпадению statement'ов
  Python-пака (dedup) — хрупкий путь; план-пак снова дал дубль.

**Можно ли считать это подтверждённым — НЕТ, не чистым.** Две переменные
изменились разом (промпт И модель): (1) «сработало (а)» может быть
эффектом промпта — но только при контроле: K2 под curator-v6 того же
правил не выполнял, halogen под curator-v7 выполняет, а при какой именно
переменной это произошло, не разведено; (2) «не сработало (б)»
неоднозначно: либо правка (б) слабее, чем v6-правило 7 (K2 заполнял поле),
либо halogen реже использует `existing_claim_id` (в v10b K2 — 1/7,
в v11 halogen — 0/7). **Нужен дополнительный смоук curator-v7 + K2** —
**подтверждаю необходимость** (оценка менеджера верна): он разделит
эффект промпта и эффект модели — (а) если K2 под v7 свяжет evidence и
заполнит `existing_claim_id`, обе правки подтверждены промптом (а
неиспользование поля halogen — модельная особенность, правка (б)
достаточна); (б) если K2 под v7 тоже не заполнит `existing_claim_id` —
правка (б) недостаточна (промповый дефект) и требует доработки;
(в) если K2 под v7 не свяжет evidence план-якоря — эффект (а) был
модельный, а не промптовый. Медлительность halogen (57,5 мин) — отдельный
модельный факт, к правкам отношения не имеет.

> **Выполнено — SMOKE-V12-K2 (T7.45b, curator-v7 + K2, чистый контроль
> против SMOKE-V10B-K2; единственная переменная — промпт):** (а) K2 под v7
> связал evidence план-якоря (E2, не E0; blind 6/6) — эффект (а)
> модельно-независим, промптовый; (б) K2 под v7 заполнил
> `existing_claim_id` в **2/2 FU** (полные UUID, 2 `claim_reverified`,
> включая план-пару, дававшую дубль в v10b и в этом прогоне) —
> неиспользование поля halogen — **модельная особенность, правка (б)
> достаточна**. Гейт 5 = 2/5. Инфраструктурный побочный факт: Python-якорь
> в том прогоне потерян на NUL-байт дефекте конвейера (документировано в
> SMOKE-V12-K2-report.md §3, НЕ исправлено — отдельная задача);
> статистически Python-пара не потеряна — перепроверку записал retry-якорь.

## 11. Не тронуто (инварианты)

- Код, тесты, payload'ы (v2…v11), промпты, корпуса, ARCHITECTURE.md — без
  изменений (задача — только анализ); HEAD `4442f48` до коммита отчёта.
- БД `noezema-smoke-v11-halogen`, `noezema-smoke-v10b-k2`,
  `noezema-smoke-v10-k2`, `noezema-smoke-v8-k2`, `noezema-eval*` —
  **только SELECT**.
- Прогоны не запускались, к LLM на 192.168.1.48 обращений не было;
  фоновых процессов не осталось (воркер остановлен, все юниты inactive,
  orphan-лог-тейлер убран).
