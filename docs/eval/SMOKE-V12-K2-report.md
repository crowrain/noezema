# SMOKE-V12-K2 — разбор смоук-прогона (T7.45b)

> Повторён после починки NUL/lease в SMOKE-V13-K2 (2026-09-26,
> `docs/eval/SMOKE-V13-K2-report.md`, T7.48): 7/7 без потерь, гейт 5 = 2/5
> тем же механизмом.

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, промпты,
корпуса, ARCHITECTURE.md не тронуты). Прогон — чистый контроль
curator-v7 + K2 после SMOKE-V11-HALOGEN (T7.44b, его §7/§8: «для чистого
сравнения нужен дополнительный смоук curator-v7 + K2»). **Единственная
переменная относительно SMOKE-V10B-K2 — промпт куратора curator-v6 →
curator-v7** (модель K2 в обоих, профиль llamacpp-rocmfpx, корпус и payload
в остальном те же). Остановка воркера и разбор — T7.45b. Дата: 2026-09-25.

## 1. Условия прогона

| Параметр | Значение |
|---|---|
| код | `239db71` (ветка `impl/from-scratch`, дерево чистое, = origin; 964 passed + 8 skipped) |
| модель | `k2-horizon-mova-36b-a4b-rocmfp4-fast` (K2 Horizon MoVA 36B A4B ROCmFP4 FAST; llama.cpp ROCmFPX-k2, 192.168.1.48:8080) |
| профиль схемы | `llamacpp-rocmfpx` (T7.36, ADR-0012) |
| payload | `docs/eval/config-v11-payload.json`: file sha256 `67468a2e…`, canonical `a407ce83…`; пины: curator-v7 `19d6c6e8…`, explorer-v4 `5829a55c…`, planner-v1 `6aeb22bc…`, verifier-v1 `34fe8069…`, extractor-v1 `af5dba62…` — отличие от config-v10 (SMOKE-V10B-K2) ТОЛЬКО `prompts.curator` (curator-v6 → curator-v7: обязательное связывание evidence — правка (а) + правило 7 — негативный пример и случай слабого claim'а — правка (б), T7.43) |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha256 `b3e05ad5d206…`) — побайтово тот же, что в v8/v10b/v11: 2 пака под перепроверку (Python: якорь 90 + FU 80 «Сверь ответ с ранее зафиксированной версией»; notes/plan.md: якорь 90 + FU 80), ООН (100, явная дата), Go (0), Спутник-1 (0, без даты) |
| правила | rules-v2, rules_hash `f96eeffc527c…` (не менялся с v10) |
| БД | `noezema-smoke-v12-k2` (создана запуском, миграции до 0025) |
| run | id `3b53a8c2-8fcb-408c-a647-03d2124750a5`, snapshot `820a9165-…`, seed 20260924 (общий для серии), slo 3600 s, blind-size 10 |
| время | 2026-09-25 11:05:24Z → 11:22:27Z (17,0 мин), eval-run EXIT=0 |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s (138+138 тиков) |

Воркер остановлен T7.45b: **до** остановки SELECT (6/6 сессий в
терминальном состоянии, commit_attempts 6/6 = `committed`, 0
нетерминальных; упавшей сессии `19b3f7a7…` в `sessions` 0 строк — §3.2),
`systemctl --user stop smoke-v12-k2-worker` → `inactive`; все 10 юнитов
`smoke-v8-k2-*` / `smoke-v10-k2-*` / `smoke-v10b-k2-*` /
`smoke-v11-halogen-*` / `smoke-v12-k2-*` — `inactive` (transient-юниты
после остановки unload, `is-active` rc=4 — тот же паттерн, что в
v8/v10b/v11); процессов hostctl / worker.sh / tick'ов не осталось.

## 2. Сводка

7 слотов драйвера: **6 сессий в БД (2 succeeded + 4 succeeded_partial) и
1 ПОТЕРЯНА** на инфраструктурном дефекте NUL-байта (§3). Строка прогона:
`eligible_sessions=7, completed_sessions=6, outcome=insufficient_sample`
(N=7 < MIN_SAMPLE 20) — ожидаемо при N=7; это НЕ приёмка §22.2.

> **Поправка к фактам задачи:** в постановке T7.45b итог записан как «4
> succeeded + 2 succeeded_partial». Факт из БД и `run.log`: **2 succeeded
> (`ce17933b`, `63bccf6c` — оба FU) + 4 succeeded_partial** (`683add6c`,
> `b2b11e9e`, `8feaee93`, `e320398f`). Дальше по фактам.

| # | сессия | вопрос (приоритет) | состояние | шаги, время | claims | причина partial / примечание |
|---|---|---|---|---|---|---|
| 1 | `683add6c` | ООН, 15.04.2026 (100) | succeeded_partial | 3, 95 с | 1 | complete_reason — свободный текст (§6) |
| 2 | `b2b11e9e` | plan.md-якорь (90) | succeeded_partial | 3, 40 с | 2 | complete_reason — свободный текст (§6) |
| 3 | `19b3f7a7` | Python-якорь (90) | **ПОТЕРЯНА (нет строки)** | 8, 246 с | 0 (откат) | **NUL-байт дефект: откат всей транзакции (§3)** |
| 4 | `8feaee93` | Python-якорь (90), **повтор** | succeeded_partial | 10, 349 с | 2 | `budget_exhausted` — новый класс (§6) |
| 5 | `ce17933b` | Python-FU (80) | succeeded | 4, 116 с | 0 новых | goal_reached; ПЕРЕПРОВЕРКА якоря через `existing_claim_id` (§4) |
| 6 | `63bccf6c` | plan.md-FU (80) | succeeded | 3, 44 с | 0 новых | goal_reached; ПЕРЕПРОВЕРКА якоря через `existing_claim_id` (§4/§5) |
| 7 | `e320398f` | Спутник-1 (0) | succeeded_partial | 4, 131 с | 1 | complete_reason — свободный текст (§6) |

Вопрос Go (0) **не выполнялся**: вопрос упавшей сессии `86a81df9…` остался
в `candidate` (транзакция откатилась — смещение состояния вопроса не
сохранено), драйвер в следующем слоте выбрал его **заново** (повтор
`8feaee93`), и 7-й слот кончился на Спутнике; `7968bfb3…` (Go) до сих пор
`candidate` в БД.

Терминал — тем же правилом (`apps/orchestrator/orchestrator.py:826`):
`complete_reason == "goal_reached"` точным равенством → `succeeded`,
иначе (без unknown) → `succeeded_partial`.

## 3. Дефект NUL-байта — документирование (НЕ исправление, отдельная задача)

### 3.1 Текст ошибки (дословно, `smoke-v12/logs/run.log`)

```
session 3: error (DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error)
<class 'asyncpg.exceptions.UntranslatableCharacterError'>: unsupported
Unicode escape sequence
DETAIL:  \u0000 cannot be converted to text.
[SQL: INSERT INTO audit_events (id, session_id, sequence, type,
schema_version, actor, public_summary, payload, visibility) VALUES
($1::UUID, $2::UUID, $3::BIGINT, $4::VARCHAR, $5::INTEGER, $6::VARCHAR,
$7::VARCHAR, $8::JSONB, $9::VARCHAR) RETURNING audit_events.occurred_at]
[parameters: (UUID('71f359e3-…'), UUID('19b3f7a7-2062-495b-8fe7-5f63b46ecc0c'),
42, 'session_state_changed', 1, None, 'report: Предложенный claim:
«Последняя стабильная версия Python на текущую дату — Python 3.14.7».
Обоснование: (1) https://www.python.org/downloads/  ... (465 characters
truncated) ... л нечитаемый результат («2 None»), — это неAuthoritative
evidence, но не требуется, так как оба заданных источника уже fetched и
agree. after 8 steps', '{"steps": 8, "evidence": [{"kind":
"source_assertion", "identity_hash":
"167f483323c0396cc08c27524127bf097ccea6e89a676e3f0ee8790d1446bfcf",
"payload" ... (119862 characters truncated) ... "fetched":
["https://www.python.org/downloads/",
"https://community.chocolatey.org/packages/python314"], "errored": [],
"uncovered": [], "untracked": []}}', 'operator')]
session 3/7: failed (steps=0, 246s, node_state=idle)
```

Запись — **финальное отчётное событие сессии**
(`orchestrator.py:792–808`: `report_payload = {steps, evidence:
[e.model_dump()…], observations, source_coverage}` →
`audit.record(SESSION_STATE_CHANGED, public_summary=f"report: … after {steps}
steps")`), сессия `19b3f7a7…` (Python-якорь, приоритет 90), sequence=42,
actor=operator — после 8 успешных шагов (fetched python.org/downloads и
community.chocolatey.org/packages/python314, evidence собрано, claim про
Python 3.14.7 сформирован куратором). Ошибка — **парсер JSONB на сервере**
(`unsupported Unicode escape sequence` — это ошибка `jsonb_in` Postgres,
а не VARCHAR: NUL в бинарном text-параметре Postgres принимает; asyncpg
эскейпит `\x00` в JSON-байтах как `\u0000`, серверный парсер JSONB его
отклоняет). NUL-байт сидит в строке внутри **payload (JSONB)** события.

### 3.2 Откатилась ВЕСЬ транзакция сессии — фатально (подтверждено фактом)

Все записи сессии (строка `sessions`, 41 предшествующий audit-событие
seq 1–41, model_runs, actions, staging) живут в одной долгой phase-1-транзакции
(ловушка AGENTS.md §7: «created_at строк, рождённых в долгой
phase-1-транзакции (model_runs, audit и т.п.) = старт транзакции»).
Упавший INSERT (seq 42) — её последнее действие → откатилась целиком.
SELECT по БД (факт, не вывод):

| таблица | строк с `session_id=19b3f7a7…` |
|---|---|
| sessions | **0** |
| audit_events | **0** |
| model_runs | **0** |
| session_staging | **0** |
| actions | **0** |
| checkpoints | **0** |

8 шагов реальной работы (2 fetch'а внешних источника, LLM-вызовы,
формирование claim'а) **полностью потеряны** — не просто одна строка.
Выжили только записи вне сессионной транзакции: строки `sources`
(proxy регистрирует их в собственной транзакции) и артефакты на диске
(content-addressed store, `~/dsh1/smoke-v12/data/artifacts/<2>/<64>`).

### 3.3 Источник NUL: не страницы, а вывод инструмента

Исключено (фактами):

1. **Сырой контент обеих страниц чист.** Артефакты потерянной сессии
   найдены по `content_hash` строк `sources` (11:07:53 / 11:08:15 — окно
   11:07:40–11:11:46 упавшей сессии): python.org/downloads `a8951810…`
   (215 042 Б) и chocolatey.org/packages/python314 `a1bb09ed…` (284 499 Б);
   sha256 файлов = `content_hash` (артефакты эти самые); **NUL-байтов: 0 и 0**.
   У `a8951810…` — тот же content-hash, что у якоря в SMOKE-V10B-K2 (страница
   побайтово не изменилась между прогонами; v10b с тем же контентом завершился
   успешно).
2. **Нормализованные тексты чисты.** Пересчёт `normalize_content`
   (`apps/research_proxy/normalization.py`, `decode("utf-8",
   errors="replace")` + HTMLParser) по обоим артефактам: python.org —
   21 255 симв., chocolatey — 143 904 симв., **NUL: 0, управляющих (кроме
   `\n\t\r`): 0**; `text_sha256` совпали с `metadata.normalized_sha256`
   строк `sources` (`82bfee73…`, `533fd7d8…`). Характерные сущности вида
   `&#0;` в сырых HTML отсутствуют (grep). Дополнительный факт: ретрай-сессия
   `8feaee93` закоммитила своё report-событие (seq 57) с теми же
   нормализованными текстами (82bfee73/533fd7d8 + dee1a02a/ebcf7ce5) — они в
   БД без ошибки.
3. evidence[0] в payload ошибки — `source_assertion` identity
   `167f4833…` = `source_assertion_identity(a8951810…, "chunk-0",
   "source_assertion")` (пересчитано) — python.org; evidence[1] — chocolatey
   (`cb9a3db1…`). Т.е. часть evidence = две чистые страницы.

Оставшиеся строки в report-payload: `evidence` от инструментов
`python.execute` / `workspace.read` (`apps/orchestrator/evidence.py`:
computation-payload `{"exit_code", "stdout[:2000]", "code[:4000]"}`,
local_observation `{"path", "content[:2000]"}`) и строки `observations`.
Поочерёдное исключение носителей (фактами):

- **workspace.read исключён:** в workspace прогона на диске ровно один файл
  — `notes/plan.md` (289 Б, создан 11:07 план-якорем; **NUL: 0, C0: 0**);
  потерянная и retry-сессии файлов не создавали (workspace.write — ни у
  одной). Читаемого NUL-файла не существовало.
- **memory.search исключён:** на момент упавшей сессии в памяти были
  только два закоммиченных plan-claim'а (`035e8a14…`, `38e29dc0…`) — их
  statement'ы в БД (закоммичены без ошибки → чистые строки), строки
  retrieval строятся из этих же строк → NUL несут не могут. (У retry — deny
  «extra inputs», т.е. `memory.search` к результату не дошёл вовсе.)
- Остаётся **stdout `python.execute`** — единственный инструмент профиля,
  способный дать произвольный (в т.ч. бинарный) вывод:
  **исполнитель декодирует вывод процесса с `errors="replace"` и NUL
  сохраняет** — `apps/orchestrator/executor.py:148`
  (`stdout.decode("utf-8", "replace")[:MAX_OUTPUT_BYTES]`; stderr — `:149`,
  но stderr в evidence-payload не входит). Байт `0x00` в stdout → U+0000 в
  str, санитизации нигде нет.
- **Улика в самом событии:** `complete_reason` модели (видна в
  `public_summary` ошибки) — «…л **нечитаемый результат («2 None»)**, — это
  неAuthoritative evidence, но не требуется, так как оба заданных источника
  уже fetched и agree»: модель получила от НЕ-источникового инструмента
  нечитаемый (с управляющими байтами) вывод и цитирует видимый осколок
  «2 None». Контекст поведения модели по этому же вопросу: в retry-сессии
  K2 пыталась обойти fetch через `shell.execute` с `curl … | head -c 6000`
  (upала «unreachable» — инструмента нет в stub-исполнителе) и через
  `python.org/api/v2/downloads/` (404) — т.е. модель в этой серии тянется к
  «вручную» достать/распарсить страницу, а это как раз сценарий, где
  `python.execute` может напечатать сырые байты.
- Точный код/аргументы вызова и позиция байта **невосстановимы**: действия
  и model_runs сессии откатились (0 строк), LLM-логи на 192.168.1.48
  трогать запрещено. Консистентно с временем падения (seq 42, а не раньше):
  все предшествующие audit-INSERT'ы сериализовали данные через `_cap_args`
  (`orchestrator.py:2456–2461`, канонический JSON **≤1000 симв.**), тогда как
  evidence-payload (`stdout[:2000]`) сериализуется **впервые** именно в
  report-событии — NUL за границей 1000-символьного кап'а (но ≤2000) не
  попадал ни в один ранний INSERT и проявился только на seq 42.

**Класс дефекта (установлено):** NUL-байт в **stdout `python.execute`**
(единственный оставшийся носитель; «нечитаемый результат («2 None»)» из
собственного отчёта модели) → сохранён `decode("utf-8","replace")`
(`executor.py:148`) → computation evidence-payload
(`evidence.py:104`, `stdout[:2000]`) → report-payload
(`orchestrator.py:794`) → JSONB INSERT → `UntranslatableCharacterError` →
откат всей phase-1-транзакции.

### 3.4 Где должна происходить санитизация (направление исправления; НЕ сделано)

1. **Корень — захват вывода** (`apps/orchestrator/executor.py`,
   `StubToolExecutor._python_execute` / `_workspace_read`; в M2 — тот же
   принцип у sandboxed-исполнителя): при `decode` вычищать `\x00` (и
   прочие C0-управляющие кроме `\n\r\t`) — тогда NUL не попадёт ни в один
   хостовой str.
2. **Граница evidence** (`apps/orchestrator/evidence.py`,
   `observation_to_evidence`): санитизация stdout/content/code на входе в
   durable-запись — покрывает любой инструмент независимо от захвата.
3. **Последняя линия — запись аудита** (`packages/domain/services/audit.py`
   `record` / `packages/domain/repositories/events.py` `create`):
   рекурсивная санитизация str в `payload`/`public_summary` до JSONB-кода —
   именно здесь сбой проявился, и только здесь можно исключить
   **амплификацию** (один NUL где угодно в отчёте → смерть всей
   сессии-транзакции: строки, 41 событие, runs, staging, claims).

### 3.5 Связь с curator-v7: НЕТ — подтверждено

Дефект в конвейере данных (вывод инструмента → evidence → report → audit
JSONB), а не в промпте/правилах: curator-промпт управляет только
консолидационным предложением, которое **не входит** в report-payload
(`{steps, evidence, observations, source_coverage}`); `complete_reason` —
это explorer-v4 (не менялся с v10b); NUL не может быть сгенерирован
куратором — он приходит из сырого вывода инструмента, захваченного
хостом. Дефект проявился бы при ЛЮБОЙ модели и ЛЮБОМ промпте; совпадение с
прогоном curator-v7 случайно (Python-якорь — вопрос, где модель запустила
инструмент и получила нечитаемый вывод).

**Статус: открытый пункт, отдельная задача** (исправление по п.3.4 +
повторный прогон для восстановления полной статистики 7/7 — Go в этом
прогоне не измерялся, §10).

## 4. Гейт 5 = 2/5 — пересчёт и состав (против 1/5 в v10b, 1/6 в v11)

Воспроизведён кодом репозитория (`_gate_reuse`,
`packages/evaluation/gates.py:408`, SELECT only) по строке `3b53a8c2…`:
**stored 2/5 ci95 [0.1176, 0.7693] insufficient_sample == recomputed
2/5 ci95 [0.1176, 0.7693] — MATCH: True** (тот же знаменатель 5, что в v10b;
v11 был 1/6).

**Знаменатель (5 значимых claim'ов: head `current`,
supported|disputed|refuted, grade ≥ E2):**

| claim | сессия-создатель | тип | grade | |
|---|---|---|---|---|
| `035e8a14` | `b2b11e9e` (plan-якорь) | local_observation | E2/supported | «План встречи в notes/plan.md содержит ровно 3 пункта» |
| `38e29dc0` | `b2b11e9e` (plan-якорь) | local_observation | E2/supported | «Файл notes/plan.md содержит план встречи из трёх пунктов: 2025-06-10 — …» |
| `6ad1168b` | `683add6c` (ООН) | temporal_fact | E3/supported | «…в ООН состояло 193 государства-члена…» |
| `aa132f9d` | `8feaee93` (Python-якорь, повтор) | temporal_fact | E3/supported | «Последняя стабильная версия Python — Python 3.14.7» |
| `db975aa8` | `e320398f` (Спутник) | external_fact | E3/supported | «Первый в мире искусственный спутник Земли «Спутник-1» был запущен 4 октября 1957 года…» |

Закоммичено всего 6 claim'ов; 6-й — `63834f34` «Chocolatey-пакет python314
находится на версии 3.14.7» (external_fact, **E1/hypothesis** — одна группа
источников) — НЕ значимый (E1 < E2), из знаменателя исключён. Знаменатель —
5, а не 6 или 7: потерянная сессия не добавила свой claim (откат);
гипотетически её claim («Последняя стабильная версия Python на текущую
дату — Python 3.14.7») не дедуплировался бы с retry-формулировкой
(«Последняя стабильная версия Python — Python 3.14.7» — не побайтово) и
дал бы 6-й значимый; Go не выполнялся.

**Числитель (2) — по пяти путям (`_gate_reuse`, UNION-ветки):**

| claim | 1 evidence | 2 revisions | 3 dep.from | 4 dep.to | 5 assessments | n_sessions |
|---|---|---|---|---|---|---|
| `aa132f9d` (Python) | `8feaee93`×4, **`ce17933b`**×1 | — | — | — | `8feaee93`, **`ce17933b`** | **2** |
| `035e8a14` (plan «ровно 3») | `b2b11e9e` | — | — | — | `b2b11e9e`, **`63bccf6c`** | **2** |
| `38e29dc0` (plan content) | `b2b11e9e` | — | — | — | `b2b11e9e` | 1 |
| `6ad1168b` (ООН) | `683add6c`×2 | — | — | — | `683add6c` | 1 |
| `db975aa8` (Спутник) | `e320398f`×2 | — | — | — | `e320398f` | 1 |

(claim_revisions — мёртвый путь v1, 0 строк; claim_dependencies — 0 строк в
всём прогоне: ни одного fabricated id.)

**ГЛАВНОЕ — план.md-пара: да, FU использовал `existing_claim_id` —
ПОЛНЫЙ UUID, настоящая перепроверка, не dedup.** Аудит FU `63bccf6c`:

- seq 21 (предложение куратора): `claims[0] = {"statement": "План встречи в
  notes/plan.md содержит ровно 3 пункта", "claim_type":
  "local_observation", "existing_claim_id": "035e8a14-cb34-4518-9bd1-
  5230ac541c27", "dependencies": []}` + `evidence_links` ×2 (supports,
  evidence_index 0 и 1) + summary «Факт уже установлен в памяти как claim
  [c:035e8a14-…]; предлагаю операцию перепроверки этого claim'а:
  содержимое файла сверено, количество пунктов совпадает».
- seq 26: **`claim_reverified`** — `reference` == `resolved` ==
  `035e8a14-cb34-4518-9bd1-5230ac541c27` (полный UUID, не префикс, не
  заглушка примера `118b76b3…` из v6 / `26170444…` из v7 — резолюция
  однозначная, `date_anchor: none`).
- Строка `claim_assessments` **с чужой сессией есть**: `created_in_session
  = 63bccf6c` (FU), claim_id = `035e8a14` (якорь) — запись-перепроверка
  (ветка 5 гейта); якорь не изменён (E2/supported/0.55, statement тот же).
- Evidence FU не дали новой строки (файл тот же → identity та же → схлоп
  в строку якоря по §14.3 — поэтому в ветке 1 FU по этому claim'у не
  виден; коммит FU: `claims_created 0, claims_reused 0, assessments 1`).

**Это первый на K2+curator-v7 случай настоящей перепроверки через
`existing_claim_id` — и первый в серии для plan.md-пары** (в v10b и v11
пара давала дубль). Python-FU `ce17933b` сделал то же самое для своей
пары: seq 29 `existing_claim_id = "aa132f9d-f967-421b-86e9-53dfc3d73d64"`
(полный UUID; тип в рефразе — external_fact, оценка — под типом якоря
temporal_fact, ADR-0018), seq 34 `claim_reverified` (reference==resolved,
`date_anchor: relative`, `assessed_as_of: 2026-09-25`), seq 35 оценка без
изменений E3/supported/0.75, коммит: `evidence_added 1` (chocolatey — новый
identity) + `evidence_deduped 2` (оба python.org — тот же content-hash
`a8951810…`).

**Прямо: счёт 2/5 получен НЕ через dedup — оба через робастный путь**
(2/2 FU заполнили `existing_claim_id` полным UUID; в аудите 2 события
`claim_reverified`; дедуп statement'ов в числителе не участвует — в
противовес SMOKE-V11-HALOGEN, где единичная единица шла через побайтовый
дедуп Python-пары, а поле заполняли 0/7).

## 5. Прямое сравнение plan.md-пары v10b → v12 (единственная переменная — промпт)

| | SMOKE-V10B-K2 (K2, curator-v6) | SMOKE-V12-K2 (K2, curator-v7) |
|---|---|---|
| якорь | `c6aa88ea`: claim `evidence_links: []` → **E0/hypothesis** (не значимый) + 3 выдуманных dependency-edge (отклонены) | `b2b11e9e`: 2 claim'а, оба с `evidence_links` (← observation `workspace.read`) → **E2/supported/0.55** (не E0); выдуманных id = 0 |
| якорь в паке FU | был (53 ток.), но E0 | был (оба: 123 ток. в `claims_evidence`) |
| FU | `9ec3807f`: `existing_claim_id: null` + «предлагаю операцию перепроверки» в summary → **НОВЫЙ дубль-claim** `64b1761f` (E2) | `63bccf6c`: **`existing_claim_id` = полный UUID якоря** → `claim_reverified`, дубля нет, якорь не тронут |
| гейт 5 (пара) | 0 (дубль не засчитывается) | **1** (per-claim n_sessions=2 через ветку 5) |
| blind_provenance | 5/6 (якорь 0 evidence) | 6/6 (у всех ≥1 evidence) |

**Вердикт по правке (б): сработала на K2 под curator-v7** — на паре
plan.md, которая была дублем и в v10b (K2+v6), и в v11 (halogen+v7). Та же
модель, другой промпт: при v6 K2 писал слова «предлагаю операцию
перепроверки» с `existing_claim_id: null` (именно негативный пример из
правила 7 v7), при v7 — заполнил поле полным UUID. Эффект промптовый
(негативный пример + «слова — не операция: операция определяется ТОЛЬКО
полем `existing_claim_id`»). Правка (а) подтверждена в этом же сравнении
(якорь связан с evidence: E0 → E2, класс «claim без evidence» устранён) —
и, поскольку в v11 (halogen+v7) якорь тоже был E2, (а) модельно-независима.

## 6. Статусы сессий: причины partial (по аудиту)

4 succeeded_partial из 6 состоявшихся:

| сессия | `termination_reason` | класс |
|---|---|---|
| `683add6c` (ООН) | «Ответ подтверждён двумя заданными источниками: 193 государств-члена ООН на 15 апреля 2026 года. Claim: …» | **free-form** (не точное `goal_reached`) — ожидаемый известный класс (T7.37b п.3 — host-фикс не сделан) |
| `b2b11e9e` (plan-якорь) | «Вопрос отвечен и подтверждён инструментами: файл notes/plan.md создан и прочитан, в плане ровно 3 пункта…» | **free-form** — тот же класс |
| `8feaee93` (Python-якорь, повтор) | **`budget_exhausted`** — точный код, но не `goal_reached` | **НОВЫЙ класс серии**: модель исчерпала 10-шаговый бюджет, не эмитнув goal-код: 7 попыток fetch (5 успешных — python.org, chocolatey, повтор python.org, downloads/source/, chocolatey; 404 на `python.org/api/v2/downloads/`; отклонение repeat-guard'а), deny `memory.search` (лишний аргумент `limit`), `shell.execute` с `curl … \| head -c 6000` → «unreachable» (инструмента нет в stub-исполнителе). Claim при этом **закоммичен** (E3/supported) — semantically цель достигнута, протокол-статус partial |
| `e320398f` (Спутник) | «Вопрос отвечен и подтверждён двумя заданными источниками: первый искусственный спутник Земли «Спутник-1» был запущен 4 октября 1957 года (19:28:34 UTC)…» | **free-form** — тот же класс |

2 succeeded — точное `goal_reached`: `ce17933b` (Python-FU), `63bccf6c`
(plan-FU). Поля tool-схемы: **2 deny** (в v10b — 3, v11 — 5, v8 — 9):
`artifact.create` «unknown tool» (`e320398f`, класс v10b/v11) и
`memory.search` «argument ('limit',): Extra inputs are not permitted»
(`8feaee93` — K2-класс «лишний/нет аргумента»). action_failed всего 5 = 2
deny + `tool_call_repeated` + `unreachable` + 404.

## 7. Числа (как в прошлых отчётах)

**T7.35 (пины model_runs):** 33 model_runs = 6 `consolidating` + 27
`exploring`; **33/33** имеют `prompt_version`+`prompt_sha256` (0 NULL),
**33/33 = пинам config-v11** (curator-v7 `19d6c6e8…` ×6; explorer-v4
`5829a55c…` ×27), **0 расхождений**; `tool_schema_hash`: 27/27 exploring
`f76284735e…`, 6 consolidating — NULL (по замыслу: куратор вызывается без
инструментов). Меньше, чем в v10b/v11 (38 = 7+31), ровно на
откатившиеся runs потерянной сессии + невыполненный Go.

**T7.36-K2 (ошибки схемы):** **оба паттерна 0** — «failed to parse
grammar» (K2): 0; «unsupported keyword»/HTTP 400 (halogen): 0;
`PromptPinError`: 0; `output_schema_valid=false`: 0 (БД);
`request_rejected`/`curator_error`: 0 (аудит); **33/33 finish_reason=stop**;
output_tokens: min 105 / медиана 460 / max 2956 (лимит 8192 — без
обрезаний).

**T7.30/T7.32 (даты/сроки по claim'ам; 6 claim'ов, 0/6 расхождений):**

| claim | anchor | as_of (assessed) | reverify_after | freshness |
|---|---|---|---|---|
| `6ad1168b` (ООН) | **explicit** (15.04.2026) | 2026-04-15 | NULL | evergreen |
| `38e29dc0` (plan) | none | NULL | NULL | evergreen |
| `035e8a14` (plan) | none | NULL | NULL | evergreen |
| `63834f34` (chocolatey) | relative (вопрос без даты) | 2026-09-25 | 2026-10-25 11:17:35.710Z = коммит якоря + 30 д | fresh |
| `aa132f9d` (Python) | relative | 2026-09-25 | 2026-10-25 11:19:31.972Z = **момент перепроверки (FU) + 30 д** (ADR-0017 — сдвиг к моменту проверки) | fresh |
| `db975aa8` (Спутник) | none (вопрос без даты, as_of не задан) | NULL | NULL | evergreen |

**Типы claim'ов:** формально — **6/6** (0 отклонений rules engine:
`curator_rejected_by_rules` 0, `request_rejected` 0; 0 fabricated ids).
По существу — 5/6 бесспорно + `db975aa8` Спутник: в v8/v10b/v11 тип был
temporal_fact (спорно — «фиксированный исторический факт»), здесь модель
выбрала **external_fact** — «естественный» тип по наблюдению v10b §7/§12
(п.6) — наблюдение само разрешилось в пользу естественного типа при
том же (v7) промпте, который типа не диктует.

**claim'ов теперь 6 (значимых 5)** — против 6 (5) в v10b, 6 (6) в v11, 7 (5)
в v8: потерянная сессия не добавила своего, retry-якорь создал 2 (E3 + E1),
plan-якорь — 2 (E2+E2), FU'ы — 0 новых (обе перепроверки).

## 8. Гейты прогона (11, сверено с `evaluation_runs.gates`)

| гейт | outcome | num/den | ci95 | порог |
|---|---|---|---|---|
| new_supported_refuted_e2 | insufficient_sample | 5/5 | [0.5655, 1.0] | 0.8 |
| external_temporal_e3 | insufficient_sample | 3/3 | [0.4385, 1.0] | 1.0 |
| eligible_sessions_with_outcome | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.6 |
| near_duplicate_questions | insufficient_sample | 0/6 | [0.0, 0.3903] | ≤0.15 |
| **significant_claim_reuse** | insufficient_sample | **2/5** | [0.1176, 0.7693] | 0.25 |
| due_stale_time_sensitive | insufficient_sample | 0/1 | [0.0, 0.7935] | <0.2 |
| reassessment_slo | insufficient_sample | 0/0 | — | 1.0 |
| current_pending_invalid_ancestor | insufficient_sample | 0/6 | — | 0 |
| high_severity_incidents | **passed** | 0/— | — | 0 |
| blind_provenance_path | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.9 (структурный) |
| blind_scope | insufficient_sample | 6/6 | [0.6097, 1.0] | 0.8 (структурный) |

blind-структура 6/6: у всех 6 head'ов `current` ≥1 evidence в текущей
оценке (у local_observation — artifact-провенанс без `sources`-строки —
по замыслу; у external/temporal — source-строки: 2/1/1/5/2).

## 9. Сводная таблица серии v8 → v10b → v11 → v12

| | v8 (curator-v4, K2) | v10b (curator-v6, K2) | v11 (curator-v7, halogen) | **v12 (curator-v7, K2)** |
|---|---|---|---|---|
| сессии (succeeded/partial из состоявшихся) | 2 / 5 | 4 / 3 | 6 / 1 | **2 / 4** + 1 потеряна (NUL) |
| claim'ов / значимых | 7 / 5 | 6 / 5 | 6 / 6 | **6 / 5** |
| гейт 5 | 0/5 | 1/5 | 1/6 | **2/5** |
| механизм числителя | — | Python: `existing_claim_id` (полный UUID) | Python: **dedup** (побайтовый statement) | **оба: `existing_claim_id` (полный UUID)** |
| `existing_claim_id` заполнили | 0/7 | 1/7 | 0/7 | **2/7 (2/2 FU!)** |
| `claim_reverified` в аудите | 0 | 1 | 0 | **2** |
| план-якорь | отклонён rules (0 evidence) | E0 (evidence_links: []) + 3 fabricated id | E2 (1 evidence) | **E2 ×2 claim'а (evidence_links есть); 0 fabricated** |
| план-FU | новый claim (якоря не было) | **дубль** (null + слова) | **дубль** | **перепроверка** |
| отклонения rules / fabricated id | 2 / 1 | 0 / 3 | 0 / 0 | **0 / 0** |
| blind_provenance | 7/7 | 5/6 | 6/6 | **6/6** |
| model_runs (пины) | 46 (0 расх.) | 38 (0 расх.) | 38 (0 расх.) | 33 (0 расх.) |
| finish=stop | 46/46 | 38/38 | 38/38 | 33/33 |
| ошибки схемы | 0 | 0 | 0 | 0 |
| policy deny | 9 | 3 | 5 | **2** |
| длительность | 23,9 мин | 18,0 мин | 57,5 мин | 17,0 мин |
| инфраструктурный дефект | — | — | — | **NUL-байт: сессия потеряна (§3)** |
| чистота эксперимента | — | — | 2 переменные (промпт+модель) | **1 переменная (промпт)** |

## 10. Итоговый вывод по серии T7.38–T7.45

1. **Правка (а) (обязательное связывание evidence) — промптовый эффект,
   модельно-независим — доказано.** K2+v6 нарушал (v10b: якорь E0,
   fabricated id, blind 5/6); при v7 соблюдают ОБА модели (v11 halogen:
   якорь E2; v12 K2: якорь E2×2, blind 6/6, 0 fabricated id, 0 отклонений
   rules). Разные модели, один промпт → эффект промпта.
2. **Правка (б) (правило 7: негативный пример + «слова — не операция») —
   достаточна на K2; неиспользование halogen'ом — модельная особенность.**
   Чистое сравнение v10b→v12 (единственная переменная — промпт, та же K2):
   план-пара, дававшая дубль при v6 (`existing_claim_id: null` + «предлагаю
   операцию перепроверки» — дословный негативный пример из v7), при v7
   заполнила поле полным UUID и перепроверила якорь. Python-пара перепроверил
   и при v6 (1/5), и при v7 (через retry-якорь). halogen+v7 — 0/7: тот же
   промпт работает на K2 → правка достаточна, halogen просто не заполняет
   поле. Гейт 5 впервые в серии >50% значимых (2/5), и обе единицы —
   робастным путём (не dedup).
3. **Осталось неопределённым / открыто:** (1) **NUL-байт дефект**
   (инфраструктура, §3 — отдельная задача): до его починки базовая
   статистика корпуса неполная — Go не измерялся в v12 (слот занят
   retry-повтором Python-якоря), 6/7 сессий. (2) `budget_exhausted`
   partial на retry-якоре (K2 сжёг 10 шагов в цикле deny/404/repeat —
   отдельное модельное наблюдение, claim закоммичен). (3) free-form
   `complete_reason` (3/4 partial) — host-фикс T7.37b п.3 так и не сделан
   (не входит в эту серию).
4. **Нужен ли ещё прогон:** для вопроса «эффект curator-v7,
   модельно-независимый» — **нет**: серия T7.38–T7.45 закрыта на данном
   уровне уверенности (обе правки классифицированы: (а) — промптовый
   эффект на двух моделях; (б) — достаточен на K2, halogen — модельная
   особенность). **Да** — один прогон после починки NUL-дефекта:
   восстановить полную статистику 7/7 (Go-пара, чистый baseline) и закрыть
   инфраструктурный пункт; Python-пара в v12 статистически не потеряна
   (retry-якорь + FU записали перепроверку), потеряны одна сессия работы
   (8 шагов/246 с) и один слот корпуса.

## 11. Не тронуто (инварианты)

- Код, тесты, payload'ы (v2…v11), промпты, корпуса, ARCHITECTURE.md — без
  изменений (задача — только анализ); HEAD `239db71` до коммита отчёта.
  NUL-дефект задокументирован (§3) и НЕ исправлен (отдельная задача).
- БД `noezema-smoke-v12-k2`, `noezema-smoke-v11-halogen`,
  `noezema-smoke-v10b-k2`, `noezema-smoke-v10-k2`, `noezema-smoke-v8-k2`,
  `noezema-eval*` — **только SELECT** (пересчёт гейта — кодом репозитория
  в режиме чтения; пересчёт `normalize_content` — по файлам артефактов,
  без записи).
- Прогоны не запускались, к LLM на 192.168.1.48 обращений не было;
  фоновых процессов не осталось (воркер остановлен, все юниты inactive,
  процессов hostctl/worker.sh нет).
