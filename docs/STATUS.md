# NOEZEMA — Статус реализации

Ветка: `impl/from-scratch`. План: [PLAN_FROM_SCRATCH.md](PLAN_FROM_SCRATCH.md).
Каждый пункт §22.1/§22.2 ARCHITECTURE.md получает ссылку на тест при закрытии.

## Прогресс по вехам

| Веха | Статус | Tag | Примечание |
|---|---|---|---|
| M0 каркас | ✅ выполнена | — | чистое дерево, скелет, CI, fake LLM, ADR-0001/0002/0003 |
| M1 контракты + LLM | ✅ выполнена | noezema-m1 | PR #4–#10; gate пройден: 112 тестов (86 unit ≥ 40), Sealed-сессия question→action→evidence→commit на fake LLM |
| M2 изоляция + commit | ✅ выполнена | noezema-m2 | PR #11–#16: sandbox+runtime, policy engine, tool broker, artifact store+staging+freeze, commit boundary (prepared→fenced final tx) + reconciliation; gate пройден: 206 тестов, failpoints (kill до/после COMMIT, open final tx, stale finalizer, kill mid-action), security (сеть off, cap-drop, injection, ro rootfs) |
| M3 память + web slice (MVP) | ✅ Gate M3 пройден (T3.29 закрыл пункт 1 §22.1); T3.30 — дефект lease из первой реальной сессии | noezema-m3 (на `ec6b4b0`, T3.29 — закрытие gate); noezema-mvp остаётся на `5d94b27` (создан до T3.29 — см. раздел Gate M3) | PR #17: память — модель (0004), evidence identity (§14.3), rules engine v1, independence (PSL+overlap), lifecycle heads (§14.1), apply в fenced tx. PR #18: context pack §5.4 + retrieval (fulltext russian, pending/invalid — отдельный лимит и метка в той же строке §5.4.2). PR #19: host recovery — noezemactl CLI, recovery policy schema v1 (jitter=0, JCS-хэш), fsync-safe transition journal + head + boot reconcile, offline rules (advisory lock, cohort+seal, atomic publish с UUIDv5 invalid-вопросами), fail-closed admission, resume-классификация (transient→retry_wait/0, permanent→resume_blocked/78, unclassified→degraded) + idempotent audit replay, policy change head + event stream, unit-state publisher, systemd units + CI-verify. PR #20: web slice — Query/Command + admin-token auth, fail-closed Command API на нездоровом hostе (423), SSE timeline (committed outbox + max_events), session detail, message TTL→expired, Host Status Adapter (recovery banner: none/retry_wait/degraded/blocked), минимальные HTML-страницы main/session. PR #21: failpoints/инварианты/resume/scenario-тесты. T3.29: wake scheduling + wake admission + backoff/pause (§5.2.1, пункт 1 §22.1): `wake_schedule` в snapshot (миграция 0005) + `wake_scheduler_state`, `noezemactl wake-tick` + `noezema-wake.timer`, admission (6 gates, skip с точной причиной в audit `wake_skipped`), экспоненциальный backoff, авто-pause после 3 неудач, wake_now — без расписания но с admission. T3.30: фоновый heartbeat lease во время долгих LLM-вызовов + `clock_timestamp()` в lease (дефект из первой реальной MVP-сессии — см. раздел ниже); 374 тест |
| M4 зависимости + переоценка | 🔄 T4.1 закрыт (claim_dependencies: DAG cycle check при commit, graph revision, kind `research` по §8.6); T4.2–T4.9 — впереди | — | пороги M4 из замеров серии 2026-09-14 зафиксированы в PLAN (батч 32, SLO P95 200 с); 391 тест |
| M5 расширенный цикл | ⬜ не начата | — | |
| M6 Research Proxy | ⬜ не начата | — | |
| M7 полный веб + эксплуатация | ⬜ не начата | — | |

## Gate M2 (§19, этап 2) — пройден (noezema-m2)

| Критерий | Тест |
|---|---|
| неизвестный ответ COMMIT reconciled | tests/scenario/test_reconciler.py (4 failpoint-сценария) |
| нет mixed state (старый либо полный checkpoint) | tests/scenario/test_failpoints.py::test_unknown_action_fails_session_at_commit (staging не применён, ревизия не поднимается) + test_orchestrator.py::test_full_sealed_session (полный: staging + manifest + ревизия одной транзакцией) |
| partial success только на safe boundary | tests/scenario/test_orchestrator.py::test_budget_exhausted_partial + test_failpoints.py (unknown action → failed, не partial) |
| живой finalizer не принят за rollback | tests/scenario/test_reconciler.py::test_open_final_tx_yields_finalizer_in_progress |

## Матрица §22.1 (техническая приёмка)

| # | Критерий | Класс | Статус | Тест |
|---|---|---|---|---|
| 1 | пробуждение по расписанию, pause/backoff | MVP | ✅ | T3.29 (задача добавлена ретроспективно — пропуск плана): `apps/orchestrator/scheduler.py` + миграция 0005 (`wake_schedule` в snapshot, `wake_scheduler_state`), `noezemactl wake-tick` + `noezema-wake.timer`. Тесты: test_wake_schedule.py (unit: fail-closed-валидация `wake_schedule`, timing: первый tick/интервал/мин. gap/backoff-окно, экспоненциальный backoff + cap) + test_wake_scheduler.py (scenario: каждый admission gate — paused/nonterminal/unresolved commit/activation slot/disk quota/GPU fail-closed → skip с точной причиной + audit `wake_skipped`; backoff после failed; авто-pause после 3 неудач (sticky); success/cancel/partial сбрасывают; operator pause не сбивается; битый `wake_schedule` → fail-closed) + test_web_api.py (wake_now обходит timing но не admission — REJECTED с reason; resume сбрасывает failure-бухгалтерию; /status.wake) |
| 2 | локальная LLM с fingerprint | MVP | ✅ | test_llm_gateway.py (OpenAI-compatible gateway для локальных бэкендов llama.cpp/Ollama/vLLM, retries только транзиентные, token/latency/finish_reason, test_fingerprint_is_deterministic_and_versioned) + test_compat_and_roles.py (compat suite T1.11, versioned prompts, tool schema hash). Fingerprint (профиль модели + prompt version + tool schema hash + policy version → canonical sha256) пишется в `model_runs.model_fingerprint` (NOT NULL) на каждом explorer/curator-вызове (T1.7–T1.8). Оговорка MVP: artifact/tokenizer-хэши опциональны (None до пиннинга артефакта); ModelProfile собирается в коде из gateway-settings — секция `model` снапшота не подключена (T1.7 «через config snapshot» — частично) |
| 3 | causal/idempotency ID в trusted host | MVP | ✅ | test_orchestrator.py (turn_id/action_id/idempotency_key генерирует хост) |
| 4 | typed actions в sandbox | MVP | ✅ | test_sandbox_runtime.py + test_tool_broker_sandbox.py (одноразовый контейнер, cap-drop/network/ro-rootfs, shell/python в sandbox, overlay) + test_sandbox_security.py |
| 5 | claim только с согласованным lifecycle | MVP | ✅ | test_memory_service.py (head current ⇔ assessment+epistemic_status NOT NULL, CHECK §14.1; dedup claim+evidence; supersede) + test_orchestrator.py (apply в fenced tx: claim→evidence→assessment→head одной транзакцией) |
| 6 | один fenced commit attempt | MVP | ✅ | test_orchestrator.py (prepared-строка до финального tx; fencing predicate: lease+revision+attempt=prepared; partial unique §14.2) + test_reconciler.py |
| 7 | lost COMMIT → reconciliation | MVP | ✅ | test_reconciler.py (kill before COMMIT→aborted; after commit→accepted; open final tx→finalizer_in_progress; stale finalizer→fenced) + reconcile_with_retries (backoff+jitter, fresh conn) |
| 8 | failpoints → старый/полный checkpoint | MVP | ✅ | test_failpoints.py (kill mid-action → outcome_unknown → session failed, staging не применён, ревизия не поднимается = полный старый checkpoint) + test_reconciler.py |
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | ✅ | test_web_api.py + test_web_mvp.py (status+host/timeline+SSE/messages/commands; admin-token auth на Command, queries open; assessment view — M3 memory) |
| 10 | раздельные messages/stop/abort/controls | MVP | ✅ | test_web_api.py (раздельные endpoints; closed enum; idempotency key; stop/abort флаги сессии) |
| 11 | нет вслепую-ретраев | MVP | ✅ | test_tool_broker.py (§5.7 retry-классы: pure=2, idempotent=1, non_idempotent/observation=0 без вслепую-ретраев; idempotency key + different hash=incident/alert) + test_llm_gateway.py |
| 12 | random backup point + root set | v1 | ⬜ | — |
| 13 | partial success на safe boundary | MVP | ✅ | test_orchestrator.py::test_budget_exhausted_partial (succeeded_partial) |
| 14 | каскадная инвалидация | v1 | 🔄 T4.1 | основа (граф + цикл): test_claim_dependencies.py (unit: cycle check — чистая функция и через apply_claim_staging: циклическое evidential-ребро отклоняется с audit `dependency_edge_rejected`, claim всё же коммитится; research-ребро не в цикле и не двигает graph revision; evidential на non-current цель — отклонено §8.6; bad kind/self/missing/unparseable — отклонены) + test_orchestrator.py (scenario: полный цикл — curator-зависимость коммитится с bump `domain_revisions(dependency_graph)` 0→1 и audit-полями; цикл — ребро отклонено, graph revision не меняется) + test_staging_schema.py (ClaimDependencyProposal: closed kind, UUID, budget ≤10, дубликаты). Остаток: barrier/closure/manifest — T4.2 |
| 15 | pending/invalid не current | MVP | ✅ | test_memory_service.py (lifecycle CHECK: pending/invalid ⇒ assessment/status NULL) + test_context_builder.py/test_retrieval.py (§5.4.2: отдельный лимит pending, метка в той же строке, исключение целиком если не хватает на метку) + test_invariants.py (pending/invalid не подаётся как current) + test_offline_rules.py (deferred→pending, removed-type→invalid) |
| 16 | worker: priority, retry, no starvation | v1 | ⬜ | — |
| 17 | repeatability/reproducibility/replication | v1 | ⬜ | — |
| 18 | counterevidence resolutions | v1 | ⬜ | — |
| 19 | unresolved attempt блокирует wake/GC | MVP | ✅ | test_reconciler.py (unresolved prepared → aborted/finalizer_in_progress; reconciling_commit = non-terminal ⇒ FIFO не стартует новую сессию, GC не трогает, critical alert, §14.2) |
| 20 | FIFO полный минимальный путь | MVP | ✅ | test_web_api.py::test_wake_now_runs_full_session + test_orchestrator.py::test_full_sealed_session + test_question_selector.py (durable knowledge — M3) |
| 21 | sync head update + offline flip | MVP | ✅ | test_orchestrator.py + test_memory_service.py (sync head update в fenced tx: новый assessment + head→current, old superseded) + test_offline_rules.py (offline flip: atomic publish pointer + UUIDv5 invalid-вопросы одной tx; deferred→pending, removed-type→invalid) |
| 22 | barrier crash-resume | v1 | ⬜ | — |
| 23 | session limits + host reserve | MVP | ✅ | test_orchestrator.py (max_explorer_steps из config → partial success на safe boundary) + test_staging_reserve.py (host reserve: staging_budget_exceeded ДО записи) |
| 24 | online activation | v1 | ⬜ | — |
| 25 | activating slot / terminal-cleanup | v1 | ⬜ | — |
| 26 | quiesce через writer gate | v1 | ⬜ | — |
| 27 | recovery по pointer tuple | v1 | ⬜ | — |
| 28 | offline rules change | MVP | ✅ | test_offline_rules.py (idempotent upsert по (base,payload), cohort+seal, atomic publish: pointer + UUIDv5 invalid-вопросы одной tx; уже-активный payload → success; active session → reject) |
| 29 | repair runner CAS | v1 | ⬜ | — |
| 30 | bootstrap migration fail-closed | MVP | ✅ | test_bootstrap_migration.py (пересчёт payload-хэша, abort на mismatch; offline candidate) |
| 31 | target quiesce + admission | MVP | ✅ | test_host_admission_resume.py (fail-closed: head tuple + bootstrap hash, незавершённый host transition, active policy change, stale marker, invalid policy) |
| 32 | host transition protocol | MVP | ✅ | test_host_journal.py (fsync-safe tmp→fsync→rename→fsync(dir), immutable events, head с immutable identity, boot reconcile 0/1/≥2, head на resolved → full replay + drop) |
| 33 | recovery policy protocol | MVP | ✅ | test_host_policy.py (schema v1, jitter=0, диапазоны, JCS-хэш, symlink/missing reject) + test_policy_change_unit_state.py (install/resolve, head + event stream, terminal effective_hash, accept_current/install_replacement) |
| 34 | web degraded observer | MVP | ✅ | test_web_mvp.py (Host Status Adapter: recovery_state none/retry_wait/degraded/blocked; fail-closed Command API 423 на unresolved transition; status.host; stale/missing unit-state; SSE + session detail + pages) |

## Матрица §22.2 (познавательная оценка)

Запускается после §22.1 на замороженной конфигурации; пороги фиксируются до run.

| Gate | Порог | Итог (passed/failed/insufficient_sample) |
|---|---|---|
| E2+ у новых supported/refuted | ≥80% | — |
| external/temporal facts E3 | 100% в выборке ≥20 | — |
| eligible sessions с результатом | ≥60% | — |
| near-duplicate вопросы | ≤15% | — |
| переиспользование значимых claims | ≥25% / 20 сессий | — |
| due/stale time-sensitive | <20% | — |
| reassessment SLO | зафиксировано до run | — |
| current assessments с pending/invalid ancestor | 0 | — |
| high-severity incidents | 0 | — |
| blind-выборка: provenance path | ≥90% | — |
| blind-выборка: не выходит за scope | ≥80% | — |

## Gate M3 (этап 3a + MVP-критерии §22.1)

Состояние: **ПРОЙДЕН** (T3.29 закрыл последний открытый пункт 1 §22.1). Gate закрыт
коммитом `ec6b4b0` (T3.29) — на нём tag `noezema-m3`. Tag `noezema-mvp` создан до
T3.29 и указывает на commit до wake-scheduler; не переставляется — решение за
пользователем (перенос — явное действие). Merge MVP в `main` выполнен отдельным
решением пользователя (merge commit на `main`, `impl/from-scratch` синхронизирован).
ruff + mypy (strict) + pytest (374 после T3.30) зелёные; `systemd-analyze verify` +
hash-pin baseline-политики в CI.

Закрыты со ссылками на тесты: строки 1–11, 13, 15, 19, 20, 21, 23, 28, 30–34 матрицы
(см. матрицу выше). Ключевые failpoint/invariant-наборы:
- `tests/security/test_invariants.py` — duplicate evidence не повышает grade; counterevidence
  меняет head в том же commit; offline publish — старый либо полный новый pointer+вопросы;
  pending/invalid не подаётся как current; grade только rules engine.
- `tests/security/test_offline_failpoints.py` — повторный run = тот же candidate (нет второй
  config), нет дублей вопросов; kill после seal до publish сохраняет pointer и candidate.
- `tests/security/test_host_resume.py` — outage DB → retry_wait (exit 0, без расхода
  start-limit); permanent → resume_blocked (exit 78); retained history ≠ active.
- `tests/scenario/test_sealed_day.py` — полный Sealed-день (N сессий подряд): FIFO, staging,
  assessment, fenced commit, timeline.

### Закрыто T3.29 (пункт 1 §22.1)

**Пункт 1 — «пробуждение по расписание, pause/backoff» (§5.2.1) — закрыт T3.29**
(задача добавлена в план ретроспективно — в M0–M3 явной задачи wake-scheduler не было,
это пропуск плана). Реализация:
- `apps/orchestrator/scheduler.py` — `WakeSchedule` (fail-closed-валидация секции
  `wake_schedule`), чистые `evaluate_schedule_timing`/`backoff_delay_seconds`,
  `WakeScheduler.decide` (timing → admission → audit) и `record_session_result`
  (backoff, авто-pause, sticky operator pause);
- миграция `0005_wake` — `config_snapshots.wake_schedule` (backfill bootstrap) +
  `wake_scheduler_state`; секция в `BOOTSTRAP_PAYLOAD` (interval 3600, min gap 600,
  backoff 60×2→86400, max 3 неудач, disk quota 1024 MB, gpu_required=false);
- `noezemactl wake-tick` + `noezema-wake.service/.timer` (тик 5 мин — расписание
  живёт в snapshot, таймер только будит; wait/skip — exit 0, fail-closed — 78);
- web Command API: `wake_now` проходит тот же admission (REJECTED с точной причиной),
  `pause` помечает `paused_reason=operator`, `resume` сбрасывает failure-бухгалтерию,
  /status отдаёт `wake` (consecutive_failures, backoff_until, paused_reason).

Решения MVP (неочевидные из кода): периодический интервал = MVP-случай «cron» §5.2.1
(полный cron-синтаксис — расширение v1); GPU gate fail-closed (интроспекция GPU в MVP
нет); `wake_now` обходит весь timing (интервал + min gap + backoff), но никогда —
admission; авто-pause sticky до операторного resume; `record_session_result` никогда
не снимает operator pause; audit пишется только для skip (wait — не событие);
`wake_scheduler_state` ключируется по `NOEZEMA_NODE_OWNER` (default `local-node`),
disk quota = размер `NOEZEMA_DATA_ROOT` (default /var/lib/noezema). Admission gates
рабочих процессов (worker/repair) — T4.4 (M4); этот gate — wake-адмиссия §5.2.1.

**Пункт 2 — «локальная LLM с fingerprint» — закрыт** (строка 2 матрицы ✅): реализован в
M1 (PR #4–#10, T1.7–T1.11) и покрыт тестами. Оговорка MVP: хэши артефакта/токенизера
опциональны (заполняются при пиннинге артефакта); ModelProfile собирается в коде из
gateway-settings, а не из секции `model` снапшота (T1.7 «через config snapshot» —
частично) — не блокирует пункт, но учтено при запуске реальной модели.

### Закрыто T3.30 (дефект из первой реальной MVP-сессии)

Первая реальная (не fake-LLM) MVP-сессия 2026-09-14 (qwen36-35b-a3b-q6-mtp, llama.cpp на
192.168.1.48, вопрос «Сколько будет 6*7?»): sandbox `python.execute` (56 мс) → ответ модели
за 2 шага → куратор не валиден по схеме (см. ниже) → **fenced commit отклонён**:
`commit_lease_lost`, attempt → `aborted`, reconciler → `reconciled_abort`,
`consecutive_failures=1`. Причина — не одна, а две:

1. **`now()` ≠ реальное время в долгой транзакции.** Postgres `now()`
   (= `transaction_timestamp`) — константа старта транзакции. Phase-1-транзакция живёт
   всю сессию, поэтому каждый heartbeat (и acquire) писал
   `lease_expires_at = txn_start + 30 s` — одно и то же значение. Любой сессии, живущей
   дольше TTL, leased commit был обречён независимо от частоты heartbeat; fake-LLM
   сценарии не ловили дефект (все тестовые сессии < 30 с от старта транзакции).
   **Исправление:** все lease-записи и лiveness-условия в `packages/domain/services/lease.py`
   — `clock_timestamp()` (реальное время). Fenced check в `commit.py` и reconciler работают
   в коротких новых транзакциях — для них `now()` эквивалентен, не менялись.
2. **Heartbeat только на границах шагов.** Реальные задержки локальной модели 15–90 с
   (в сессии: 16.6 с и 46.9 s; TTL 30 s) — между шагами lease всё равно истёк бы.
   **Исправление:** `LeaseHeartbeatGuard` (§5.2.3 «TTL равен нескольким heartbeat
   intervals с запасом на scheduler jitter»): background-task вокруг explorer/curator
   LLM-вызовов, интервал = `ttl/3` (30 с → 10 с), продление — в транзакции вызывающего
   (отдельное соединение блокировалось бы на row-lock строки сессии, который держит
   phase-1), `progress=False` — `last_progress_at` (progress watchdog) не искажается;
   отказ продления (phase deadline) → `LeaseLost` на выходе guard → abort, резолвит
   reconciler (никогда не угаданный rollback). Оркестратор: `Orchestrator(lease_ttl=...)`
   — инъекция TTL для тестов.

Операционное finding (не код): модели с `reasoning_content` расходуют reasoning на
`max_output_tokens` — куратор при бюджете 2048 отдавал пустой `content`
(`finish_reason=length`, 3 ретрая ~84 с). Для реальных сессий `max_output_tokens ≥ 4096`
(explorer-вызовы в 2048 укладывались: 1172/1035 токенов output с reasoning, schema_valid).

Тесты: `tests/unit/test_lease.py` (guard: продление переживает операцию дольше TTL;
продление не двигает `last_progress_at`; отказ продления → `LeaseLost` на выходе) +
`tests/scenario/test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease` (регрессия:
4 LLM-вызова по 2.5 с при TTL 1 с → SUCCEEDED; без фикса — `commit_lease_lost`) +
`tests/fakes/fake_openai_server.py`: `delay_seconds` у scripted response (имитация медленной
модели). Полная проверка: ruff + mypy strict + pytest 374.

**Повторная реальная сессия после T3.30 — SUCCEEDED (фикс подтверждён на реальной модели).**
Сессия `e6a30c19` (qwen36-35b-a3b-q6-mtp, `max_output_tokens=4096`, «Сколько будет 6*7?»),
длительность 4 мин 1 с: explorer 2 вызова (in 888/973, out 545/1195, **68.9 с и 143.4 с** —
второй в 4.8 раза длиннее TTL 30 с, guard пережил), sandbox `python.execute` → «42»,
куратор (in 600, out 1841, 24.4 с, schema_valid) → staging claim+evidence → fenced commit
(prepared 16:25:45.234 → committed 16:25:45.280, 46 мс, staging_hash `b03167b7…`) →
claim «6 * 7 = 42» (computed_result) + evidence computation (identity_hash `d49ab144…`) +
assessment **E2 / supported / confidence 0.55** (только rules engine, rules_hash
`19aed59f…`) → manifest `cf155a67…` (1 файл, 25 Б) → outbox 26 событий, вопрос →
`verified`, wake: `consecutive_failures=0, node_state=idle`. БД `noezema_mvp` сохранена как
доказательство (фореинзика — в отчёте по сессии).

**Серия реальных MVP-сессий и замеры нагрузки (M4-precondition, 2026-09-14)** — 4 вопроса
в БД `noezema_mvp` (знание накапливается между сессиями; контекст-пак видит предыдущие
claims). Проход 1 при `max_output_tokens=4096` — **4/4 failed**: `reasoning_content` съел
весь бюджет на первом explorer-вызове (проб минимального промпта: 15 000+ знаков
reasoning, `finish_reason=length`, content пуст; 3 ретрая, wall 196–286 с/сессию).
Длина reasoning модели дрейфует (4100–4250 токенов на тривиальном вопросе против
545–1841 во 2-й сессии) — 4096 оказалось меньше P99. Авто-pause wake-планировщика
сработал по spec (4 неудачи → sticky `paused`); operator resume (web RESUME:
node_state→idle + сброс failure-бухгалтерии; `reset_failure_state()` планировщика —
только бухгалтерия, node_state снимает host/web-слой) восстановил работу; отказавшие
сессии чисто откатились (в таблицах — только wake-ledger + хост-лог).
Проход 2 при **8192** — **3 succeeded + 1 succeeded_partial** (wall 72–184 с):
`42:6` → partial (модель вызвала чужой инструмент `message.reply` с неверными
аргументами → policy DENIED; free-text complete-reason вне closed enum →
`succeeded_partial` + `partially_answered`, claim закоммичен), `12·13`, `сумма 1..10`,
`7!` → succeeded. Замеры (проход 2, 15 LLM-вызовов): throughput ≈ 68 ток/с
(40 585 out-токенов за 592 с); задержка вызова 8–66 с (линейно от длины вывода);
in-токены 600–1110; fenced commit 21–50 мс; sandbox ≤1 мс на тривиальном коде.
Итог по БД: 5 закоммиченных сессий, 5 claims (computed_result, E2/supported/0.55),
4 verified + 1 partially_answered, node_state=idle. Операционное решение:
`max_output_tokens=8192` — обязательный минимум для qwen36-35b-a3b-q6-mtp.
Материал для порогов M4 (очередь, батч, SLO) собран (PLAN, блок «Серия реальных
MVP-сессий»); старт M4 — решение пользователя.

**Закрыто T4.1 (M4 старт, §8.6, §14.1, §5.2.2)** — `claim_dependencies` стал рабочей
частью памяти:
- **Staging**: `ClaimProposal.dependencies: list[ClaimDependencyProposal]`
  (`{claim_id: UUID, kind: evidential|research}`, budget ≤10, closed kind, дубликаты
  отклоняются host-валидацией). Модель видит существующие claims с ID: строки
  контекст-пака теперь `[c:<claim_id>] …` (retrieval.line; §5.4.2 all-or-nothing
  покрывает ID и метку), куратор получает раздел «Знание» с этими строками.
- **Cycle check при commit** (§8.6): в `MemoryService.apply_claim_staging` (fenced tx,
  шаг 1b) — evidential-граф = существующие рёбра + предложенные; новое ребро
  `a → b` циклично iff `a` достижимо из `b`. Чистая функция
  `find_evidential_cycles` + применение: циклическое ребро отклоняется (audit
  `dependency_edge_rejected` + problems), claim всё же коммитится — инвариант DAG
  не нарушается, знание не теряется. Research-рёбра в cycle check не участвуют
  (явная пометка по §8.6; гипотеза — только research-зависимость).
- **Валидация целей**: target должен существовать; self-ref запрещена; evidential на
  non-current (pending/invalid head под effective snapshot) — отклонён («pending/invalid
  не действуют как зависимости», §8.6); research — разрешён.
- **Graph revision** (§14.1): `commit.finalize` — `touches_dependency_graph` вычисляется
  по recorded staging (есть evidential-зависимость) → канонический порядок locks
  включает строку `domain_revisions(dependency_graph)`; fencing-предикат проверяет
  `graph_revision = base` (из prepared-строки) только при touches; bump
  `dependency_graph` — только если evidential-ребро реально записано (research-only
  и fully-rejected commit граф не двигают). Audit `commit_attempt_committed` несёт
  `dependency_graph_revision`.
- **Миграция 0006**: CHECK kind — `('evidential','research')` (M3-scaffold имел
  `'investigative'`; spec §8.6 называет именно research-пометку; таблица пуста —
  writers не было до T4.1).
- **Prompt** `curator-v2` (+ pin в BOOTSTRAP_PAYLOAD: bootstrap snapshot — новый
  payload-hash; тестовые БД мигрируют с нуля, production `noezema_mvp` не
  пересидится — snapshot имутабелен, изменения конфигурации — путь T4.5+).
- Решения: (1) циклическое/невалидное ребро = edge-level reject, а не отказ всей
  сессии — консервативно: инвариант сохраняется, claim с evidence не теряется,
  причина в audit; (2) `touches` определяется по staging ДО apply (замок берётся
  даже если все рёбра отвергнуты — fencing честен, bump нет).
- Тесты: test_claim_dependencies.py (8: 5 unit cycle-check + 3 DB-сценария через
  apply_claim_staging), test_staging_schema.py (+7), test_orchestrator.py (+2
  scenario: bump 0→1 с audit; цикл — отклонено, revision не изменился). 391 тест.

Merge в `main` — отдельное решение (не выполняется автоматически).
