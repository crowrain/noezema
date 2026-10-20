# NOEZEMA — Статус реализации

Ветка: `impl/from-scratch`. План: [PLAN_FROM_SCRATCH.md](PLAN_FROM_SCRATCH.md).
Каждый пункт §22.1/§22.2 ARCHITECTURE.md получает ссылку на тест при закрытии.
Архив закрытых разделов вех (M0–M6, M7 T7.1–T7.105, исходные ячейки таблицы): [STATUS-archive.md](STATUS-archive.md).

## Прогресс по вехам

| Веха | Статус | Tag | Примечание |
|---|---|---|---|
| M0 каркас | ✅ выполнена | — | чистое дерево, скелет, CI, fake LLM, ADR-0001/0002/0003 — [дословно](STATUS-archive.md#m0-каркас) |
| M1 контракты + LLM | ✅ выполнена | noezema-m1 | PR #4–#10; gate пройден: 112 тестов (86 unit ≥ 40), Sealed-сессия question→action→evidence→commit на fake LLM — [дословно](STATUS-archive.md#m1-контракты--llm) |
| M2 изоляция + commit | ✅ выполнена | noezema-m2 | PR #11–#16: sandbox+runtime, policy engine, tool broker, artifact store+staging+freeze, commit boundary (prepared→fenced final tx) + reconciliation; gate пройден: 206 тестов, failpoints (kill до/после COMMIT, open final tx, stale finalizer, kill mid-action), security (сеть off, cap-drop, injection, ro rootfs) — [дословно](STATUS-archive.md#m2-изоляция--commit) |
| M3 память + web slice (MVP) | ✅ Gate M3 пройден (T3.29 закрыл пункт 1 §22.1); T3.30 — дефект lease из первой реальной сессии | noezema-m3 (на `ec6b4b0`, T3.29 — закрытие gate); noezema-mvp остаётся на `5d94b27` (создан до T3.29 — см. раздел Gate M3 в архиве) | PR #17–#21: память, context pack + retrieval, host recovery (noezemactl), web slice, failpoints; T3.29 — wake scheduling (пункт 1 §22.1), T3.30 — lease-дефект; 374 тест — [дословно](STATUS-archive.md#m3-память--web-slice-mvp) |
| M4 зависимости + переоценка | ✅ Gate M4 пройден: T4.1–T4.9 закрыты (claim_dependencies + cycle check, cascade invalidation, worker reassessment, writer admission, online activation §8.7.2, env manifests §14 v2, source graph §11.3, counter-resolutions, failpoints) — [дословно](STATUS-archive.md#m4-зависимости--переоценка) | — | пороги M4 из замеров серии 2026-09-14 зафиксированы в PLAN (батч 32, SLO P95 200 с); 501 тест |
| M5 расширенный цикл | ✅ Gate M5 пройден (§19, этап 4): T5.1–T5.6 закрыты (curiosity, планирование, verifier, повтор, untrusted extraction, long-horizon) — [дословно](STATUS-archive.md#m5-расширенный-цикл) | — | 651 тест |
| M6 Research Proxy | ✅ Gate M6 пройден (§19, этап 5): T6.1–T6.4 закрыты (research proxy: egress, режимы, provenance, injection/отравление) — [дословно](STATUS-archive.md#m6-research-proxy) | noezema-m6 (после gate) | см. раздел M6 в архиве | 651 тест |
| M7 полный веб + эксплуатация | ✅ Gate M7 пройден (T7.1–T7.6: knowledge graph + provenance, backup/PITR §15.3, GC root set, security gate + §16 metrics, evaluation run §22.2, ADR-0004; noezema-m7); после gate — дефектные серии EVAL-1/2/3/3b/4/4d: T7.7–T7.28 закрыты (ADR-0005–0015), активная фаза после gate продолжается: задачи T7.29–T7.105 (ADR-0016…ADR-0035) закрыты и перенесены побайтово в архив — в их числе T7.68 (единый сборщик оркестратора), T7.70 (SearXNG на dev-стенде) и T7.71 (`web.search` для модели, config-v14, explorer-v6); активные разделы — от T7.106, индекс перенесённого — ниже; [дословно](STATUS-archive.md#m7-полный-веб--эксплуатация) | noezema-m7 (на `2e1631c`) | см. раздел M7 в архиве | 942 тест |

## Матрица §22.1 (техническая приёмка)

| # | Критерий | Класс | Статус | Тест |
|---|---|---|---|---|
| 1 | пробуждение по расписанию, pause/backoff | MVP | ✅ | T3.29 (задача добавлена ретроспективно — пропуск плана): `apps/orchestrator/scheduler.py` + миграция 0005 (`wake_schedule` в snapshot, `wake_scheduler_state`), `noezemactl wake-tick` + `noezema-wake.timer`. Тесты: test_wake_schedule.py (unit: fail-closed-валидация `wake_schedule`, timing: первый tick/интервал/мин. gap/backoff-окно, экспоненциальный backoff + cap) + test_wake_scheduler.py (scenario: каждый admission gate — paused/nonterminal/unresolved commit/activation slot/disk quota/GPU fail-closed → skip с точной причиной + audit `wake_skipped`; backoff после failed; авто-pause после 3 неудач (sticky); success/cancel/partial сбрасывают; operator pause не сбивается; битый `wake_schedule` → fail-closed) + test_web_api.py (wake_now обходит timing но не admission — REJECTED с reason; resume сбрасывает failure-бухгалтерию; /status.wake) |
| 2 | локальная LLM с fingerprint | MVP | ✅ | test_llm_gateway.py (OpenAI-compatible gateway для локальных бэкендов llama.cpp/Ollama/vLLM, retries только транзиентные, token/latency/finish_reason, test_fingerprint_is_deterministic_and_versioned) + test_compat_and_roles.py (compat suite T1.11, versioned prompts, tool schema hash). Fingerprint (профиль модели + prompt version + tool schema hash + policy version → canonical sha256) пишется в `model_runs.model_fingerprint` (NOT NULL) на каждом explorer/curator-вызове (T1.7–T1.8). Оговорка MVP: artifact/tokenizer-хэши опциональны (None до пиннинга артефакта); ModelProfile собирается в коде из gateway-settings — секция `model` снапшота не подключена (T1.7 «через config snapshot» — частично) |
| 3 | causal/idempotency ID в trusted host | MVP | ✅ | test_orchestrator.py (turn_id/action_id/idempotency_key генерирует хост) |
| 4 | typed actions в sandbox | MVP | ✅ | test_sandbox_runtime.py + test_tool_broker_sandbox.py (одноразовый контейнер, cap-drop/network/ro-rootfs, shell/python в sandbox, overlay) + test_sandbox_security.py + test_orchestrator_sandbox_executor.py (T7.58: продуктивный путь `NOEZEMA_TOOL_EXECUTOR=sandbox`, контейнер на сессию в `run_session`) + test_tool_broker_sandbox.py::test_broker_command_killed_from_inside_is_not_unknown (T7.91: команда, убившая себя сигналом в ЖИВОМ контейнере, — обычная ошибка с настоящим кодом, не unknown; контейнер после неё продолжает работать) |
| 5 | claim только с согласованным lifecycle | MVP | ✅ | test_memory_service.py (head current ⇔ assessment+epistemic_status NOT NULL, CHECK §14.1; dedup claim+evidence; supersede) + test_orchestrator.py (apply в fenced tx: claim→evidence→assessment→head одной транзакцией) |
| 6 | один fenced commit attempt | MVP | ✅ | test_orchestrator.py (prepared-строка до финального tx; fencing predicate: lease+revision+attempt=prepared; partial unique §14.2) + test_reconciler.py |
| 7 | lost COMMIT → reconciliation | MVP | ✅ | test_reconciler.py (kill before COMMIT→aborted; after commit→accepted; open final tx→finalizer_in_progress; stale finalizer→fenced) + reconcile_with_retries (backoff+jitter, fresh conn) |
| 8 | failpoints → старый/полный checkpoint | MVP | ✅ | test_failpoints.py (kill mid-action → outcome_unknown → session failed, staging не применён, ревизия не поднимается = полный старый checkpoint) + test_reconciler.py + T7.91: окно `docker inspect` сразу после kill закрыто — test_tool_broker.py::test_signal_death_while_engine_still_says_running_is_unknown (137 при ещё «Running=true» → outcome_unknown, а не «exit code 137») и ::test_container_gone_at_once_is_unknown_without_waiting (прежний быстрый путь T2.22) + T7.92: та же перепроверка теперь включена и в ветке `SandboxError` — test_tool_broker.py::test_engine_failure_while_container_flips_inside_window_is_unknown и ::test_engine_failure_flips_to_unknown_on_python_execute_too (таймаут движка при ещё «Running=true», который внутри окна переворачивает в «не запущен» → outcome_unknown), ::test_engine_failure_with_container_already_gone_is_unknown_without_waiting |
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | ✅ | test_web_api.py + test_web_mvp.py (status+host/timeline+SSE/messages/commands; admin-token auth на Command, queries open; assessment view — M3 memory) + dependencies (v1-часть): test_web_knowledge.py (T7.1: claims/heads по effective snapshot, зависимости в обе стороны §8.6, provenance-навигация source→parent/artifact/группы) |
| 10 | раздельные messages/stop/abort/controls | MVP | ✅ | test_web_api.py (раздельные endpoints; closed enum; idempotency key; stop/abort флаги сессии) |
| 11 | нет вслепую-ретраев | MVP | ✅ | test_tool_broker.py (§5.7 retry-классы: pure=2, idempotent=1, non_idempotent/observation=0 без вслепую-ретраев; idempotency key + different hash=incident/alert) + test_llm_gateway.py + test_search_tool_contract.py (T7.71: новый `web.search` объявлен в реестре классом OBSERVATION → 0 ретраев, повтор идентичного вызова отсекает guard T7.12) + T7.91 (ограниченная перепроверка состояния контейнера после неуспешного exec — это вопрос о СОСТОЯНИИ, а не вторая попытка команды: `exec_calls == 1` закреплено тестами, unknown по-прежнему не ретраится; страж от ложного unknown — ::test_plain_failure_with_live_container_is_not_unknown, ::test_signal_death_in_a_live_container_stays_a_plain_failure) + T7.92 (тот же ограниченный второй взгляд добавлен в ветку `SandboxError`: перепроверка по-прежнему спрашивает о СОСТОЯНИИ и не повторяет команду — ::test_engine_failure_while_container_flips_inside_window_is_unknown, ::test_engine_failure_flips_to_unknown_on_python_execute_too (оба с `exec_calls == 1`); неизменность прежнего поведения охраняют ::test_engine_failure_with_live_container_stays_transient_after_second_look (живой контейнер → прежний transient, ровно `1 + окно/интервал` опросов), ::test_broken_state_probe_after_engine_failure_does_not_wait, ::test_health_error_is_not_rechecked_and_stays_transient, ::test_engine_failure_outside_container_tools_probes_nothing) |
| 12 | random backup point + root set | v1 | ✅ T7.2 + T7.3 | backup/PITR-сторона: test_backup_pitr.py (22 кейса / 10 уникальных, T7.46b: create_backup — recovery point `pg_current_wal_lsn()` + content-addressed artifact inventory + host-contour state с явным `host_ops_absent`-evidence, DB CHECK shape/consistency, audit `backup_created` той же tx; restore drill — случайная точка retention window (expired не выбирается; T7.46b/ADR-0021: drill — на инъекционном часе операции (выбор точки + `verified_at` + аудит, `now=None` → host-час), drill-тесты параметризованы сдвигом now +0/+1/+5 лет — не-зависимость от даты запуска + страж injected-vs-wall-clock), re-hash inventory + всех referenced objects + registry drift check, policy files, boot reconciliation + admission ДО старта runtime (записанный в манифест active head = ожидаемое degraded состояние, сюрприз-хед → failed), `verified_at` только при pass + audit `backup_restore_drill` (outcome/problems), corruption → failed без штампа; CLI `noezemactl backup`/`restore-drill`; diagnostics `backups`-блок). Root set (GC-сторона §15.3): test_gc.py (4: полный root set §15.3 — все классы корней выживают при apply-sweep, expired orphan + unpinned удаляются, checkpointed manifest живёт, expired backup REPORTED (не удаляется), terminal committed attempt + его manifest удаляются; запрет GC при reconciling_commit (rows сессии skipped, terminal attempt сессии не кандидат); dry-run ничего не удаляет + audit `gc_sweep` apply=false; expired backup + terminal config attempt в отчёте). CLI `noezemactl gc [--apply]` |
| 13 | partial success на safe boundary | MVP | ✅ | test_orchestrator.py::test_budget_exhausted_partial (succeeded_partial) + T7.49/ADR-0022 (нормализация complete_reason: succeeded ТОЛЬКО из явного токена `goal_reached` в начале reason — `normalize_complete_reason`; test_complete_reason_normalization.py unit-таблица (20 позитивных: токен + разделитель + суффикс / кавычки / регистр; 13 негативных: суффикс без границы слова, токен не в начале, free-form, не-строки; приоритет первого токена) + test_complete_reason_normalization.py scenario (postgres+fake LLM: «goal_reached — <текст>» → succeeded + VERIFIED + `termination_reason="goal_reached"` + audit `complete_reason` (сырой) + `normalized_reason` (токен); free-form → succeeded_partial + сырая `termination_reason` + `normalized_reason=null` (прежнее поведение); unknown_actions + «goal_reached — …» → failed/`unknown_action_outcome` (T2.21)) + T7.50 (класс D — у источника: explorer-v5 пинится к enum `CompleteReason` и нормализатору — test_explorer_prompt_completion.py: токены промпта = enum минус `operator_stop` (токен хоста), оба JSON-примера валидны, правильный → `GOAL_REACHED`, неправильный (free-text) → `None`; test_freeze_payloads.py::test_config_v12_pins_explorer_v5_and_differs_from_v11_only_there; страж примеров — test_prompt_example_no_real_data.py, распространён на explorer) |
| 14 | каскадная инвалидация | v1 | ✅ T4.1+T4.2+T4.3+T4.4+T4.5+T4.6+T4.7+T4.8+T4.9 | T4.1 (граф + цикл): test_claim_dependencies.py (unit: cycle check — чистая функция и через apply_claim_staging: циклическое evidential-ребро отклоняется с audit `dependency_edge_rejected`, claim всё же коммитится; research-ребро не в цикле и не двигает graph revision; evidential на non-current цель — отклонено §8.6; bad kind/self/missing/unparseable — отклонены) + test_orchestrator.py (scenario: полный цикл — curator-зависимость коммитится с bump `domain_revisions(dependency_graph)` 0→1 и audit-полями; цикл — ребро отклонено, graph revision не меняется) + test_staging_schema.py (ClaimDependencyProposal: closed kind, UUID, budget ≤10, дубликаты). T4.2 (barrier/closure/manifest): test_cascade.py (8: closure-ходы; inline cascade; idempotent replay; barrier lifecycle + crash-resume; graph-change → new generation; tamper → blocked; retrieval ancestor check; moved graph при старте). T4.3 (worker reassessment_jobs): test_reassessment.py (13: runnable-предикат — activating slot/чужой snapshot/backoff-отсрочка; head promotion через rules engine с audit и knowledge bump; insufficient data → invalid + UUIDv5 question; transient → retry с backoff; permanent → blocked + alert; expired-lease recovery; admission metrics; bounded batch + priority; mid-batch loss admission). T4.4 (writer admission): test_writer_admission.py (14: gate CAS §14.1 — acquire/release/expired-takeover/NOWAIT-конфликт + CHECK holder-полей; session intent — live lease, idempotent, clear, stale-очистка reconciler'ом после fencing; worker — deferral с jitter при gate-конфликте, уступка intent на входе и mid-batch (попытка не сгорает), release после батча; activation берёт gate до pointer; scheduler — T_escalate/T_worker_admission skip `reassessment_backlog`, свежая/blocked очереди не блокируют, fail-closed секция, SLO-метрики). T4.5 (online activation §8.7.2): test_online_activation.py (12: fenced lease — acquire/resume/takeover fence+1; quiesce — gate wait timeout, active session; shadow heads — fast path carry старой оценки / pending + activation jobs; deterministic UUIDv5 вопросы post-publish; atomic flip — pointer + bootstrap immutable; crash resume без смены fence; transient backoff + slot held; exhaustion → post_publish_blocked + alert + repair runner (repair CAS, phase=repair); superseded-закрытие без reactivation; T_repair_admission — skip repair_backlog; sealed-интервал триггер 45000). T4.6 (environment independence §8.7.3): test_env_independence.py unit (17: ключ группы — только (protocol, implementation, dataset lineage), GPU/seed/data order группу не создают; untracked fail-closed; 6 исходов классификации; shared dataset lineage убивает independence; strongest-pair с variation; engine — E3 только через independent_replication ≥2 группы, repeatability/reproducibility/variation не проходят, причины insufficient_independence / independence_*_not_met; unknown relation → ValueError) + scenario (6: полный манифест §14 + manifest_hash + снапшот на оценке; 2 сессии = 1 манифест, нет ложной independence; repeatability — одна группа, E2; другой GPU — reproducibility, гипотеза; независимые implementation'ы — E3; shared lineage — variation + independence_independent_replication_not_met). T4.7 (source graph §11.3): test_source_independence.py unit (10 новых: v2 — content_hash/parent/edge/correction базы; split отменяет прямую edge, но не domain; split бьёт merge (fail-closed); invalid correction игнорируется; unknown lineage; порядок-инвариантность) + scenario test_source_graph.py (6: E3 через два независимых источника + снапшот independence-v2; зеркала одного domain — одна группа; merge correction → head pending + job + ревизия 0→1 → recompute гипотеза (свежий снапшот, audit source_graph_changed); split correction → группы расходятся → E3; unknown lineage — одна группа; staging-commit — снапшот на оценке). T4.8 (counterevidence §8.7.4): test_counter_resolutions.py unit (9: claim_depends_on — транзитивность, циклы, направление) + test_rules_engine.py (+3: resolved counter не cap'ит, только-resolved — не refuted, один unresolved — disputed) + scenario test_counter_resolutions.py (5: disputed E1 с counter'ом; evidence-basis → каскад → E3 + audit; инварианты XOR/counter/scope/транзитивная зависимость/уникальность/DB CHECK; correction-basis → E3, отзыв correction → invalid + disputed; прямая invalidation + idempotent no-op). T4.9 (failpoints): test_failpoints_m4.py (7: crash после flip — pointer tuple + один publish + idempotent resume; crash между батчами — durable cursor, без дублей UUIDv5; stale activator после takeover — fence-отказ без writes; следующий flip закрывает blocked backlog (find_repair_backlog → None); barrier crash после каждого батча — 3 batch audits + 1 resolved; group merge + expired worker lease → recovery + hypothesis на свежем снапшоте; worker завершает после смерти intent-lease). GATE M4: retrieval ancestor check (T4.2) + worker starvation оба направления (T4.4+T4.9) + group merge recompute (T4.7+T4.9) |
| 15 | pending/invalid не current | MVP | ✅ | test_memory_service.py (lifecycle CHECK: pending/invalid ⇒ assessment/status NULL) + test_context_builder.py/test_retrieval.py (§5.4.2: отдельный лимит pending, метка в той же строке, исключение целиком если не хватает на метку) + test_invariants.py (pending/invalid не подаётся как current) + test_offline_rules.py (deferred→pending, removed-type→invalid) |
| 16 | worker: priority, retry, no starvation | v1 | ✅ T4.3+T4.4+T4.9 | test_reassessment.py (priority, retry/backoff, blocked) + test_writer_admission.py (worker уступает session intent на входе и mid-batch, release после батча; deferral с jitter) + test_failpoints_m4.py::test_worker_not_starved_after_intent_lease_expiry (worker завершает после смерти intent-lease) — строка 14, T4.3/T4.4/T4.9 |
| 17 | repeatability/reproducibility/replication | v1 | ✅ T4.6 | test_env_independence.py (unit 17 + scenario 6: группы по (protocol, implementation, dataset lineage); repeatability/reproducibility/independent_replication/variation/untracked; E3 только через independent_replication) — строка 14, T4.6 |
| 18 | counterevidence resolutions | v1 | ✅ T4.8 | test_counter_resolutions.py (unit 9 + scenario 5: XOR/partial-unique, инварианты basis, каскад create/invalidate → recompute, engine считает только unresolved) + test_rules_engine.py (+3) — строка 14, T4.8 |
| 19 | unresolved attempt блокирует wake/GC | MVP | ✅ | test_reconciler.py (unresolved prepared → aborted/finalizer_in_progress; reconciling_commit = non-terminal ⇒ FIFO не стартует новую сессию, GC не трогает, critical alert, §14.2); T7.24: точка входа примирителя — `hostctl reconcile-tick` (fenced row-lock, живой finalizer ≠ rollback, transient → retry c backoff; stuck committing + prepared + истёкшая аренда → разрешено, сессия терминальна, следующая допускается — test_reconciler.py::test_reconcile_tick_resolves_stuck_committing_session) |
| 20 | FIFO полный минимальный путь | MVP | ✅ | test_web_api.py::test_wake_now_runs_full_session + test_orchestrator.py::test_full_sealed_session + test_question_selector.py (durable knowledge — M3) |
| 21 | sync head update + offline flip | MVP | ✅ | test_orchestrator.py + test_memory_service.py (sync head update в fenced tx: новый assessment + head→current, old superseded) + test_offline_rules.py (offline flip: atomic publish pointer + UUIDv5 invalid-вопросы одной tx; deferred→pending, removed-type→invalid) |
| 22 | barrier crash-resume | v1 | ✅ T4.2+T4.9 | test_cascade.py (barrier lifecycle + crash-resume с durable курсором, idempotent replay, tamper → blocked) + test_failpoints_m4.py::test_barrier_crash_after_every_batch (crash после КАЖДОГО батча — 3 batch audits + 1 resolved) — строка 14, T4.2/T4.9 |
| 23 | session limits + host reserve | MVP | ✅ | test_orchestrator.py (max_explorer_steps из config → partial success на safe boundary) + test_staging_reserve.py (host reserve: staging_budget_exceeded ДО записи) |
| 24 | online activation | v1 | ✅ T4.5+T7.26 | test_online_activation.py (12: fenced lease/takeover, quiesce, shadow heads, deterministic UUIDv5 вопросы, atomic flip, crash resume, exhaustion → post_publish_blocked) + test_failpoints_m4.py (crash после flip / между батчами) + T7.26/ADR-0013: drain-протокол (intent до quiesce-проверки, bounded ожидание окна, cancel по таймауту) — test_activation_drain.py (4: флип между сессиями серии, drain-таймаут → intent снят, crash во время drain → lease-aware slot + takeover fence+1, admission gate `ActivationInFlightError`) — строка 14, T4.5/T4.9; M7, T7.26 |
| 25 | activating slot / terminal-cleanup | v1 | ✅ T4.5 | test_online_activation.py (slot held при transient, terminal cleanup при exhaustion, superseded-закрытие без reactivation) + test_failpoints_m4.py::test_next_flip_closes_blocked_backlog — строка 14, T4.5/T4.9 |
| 26 | quiesce через writer gate | v1 | ✅ T4.4+T4.5+T7.20+T7.26 | test_writer_admission.py (gate CAS NOWAIT §14.1, session intent, worker deferral) + test_online_activation.py (activation берёт gate до pointer; quiesce — gate wait timeout, active session) + test_quiesce_race.py (T7.20, ADR-0009: quiesce-барьер in-flight сессии — committed admission-запись + trigger на terminal, sweep истёкших, backstop carry-over на commit при drift указателя) + T7.26/ADR-0013: drain-протокол — intent до quiesce-проверки, gate wait после ожидания окна, повторная quiesce-проверка под head-lock в flip-tx (инвариант T7.20 сохранён) — test_activation_drain.py — строка 14, T4.4/T4.5; M7, T7.20/T7.26 |
| 27 | recovery по pointer tuple | v1 | ✅ T4.5+T4.9 | test_online_activation.py (crash resume: fence не меняется, один publish) + test_failpoints_m4.py::test_crash_after_flip_recovers_pointer_tuple (pointer tuple durable, idempotent resume) — строка 14, T4.5/T4.9 |
| 28 | offline rules change | MVP | ✅ | test_offline_rules.py (idempotent upsert по (base,payload), cohort+seal, atomic publish: pointer + UUIDv5 invalid-вопросы одной tx; уже-активный payload → success; active session → reject) |
| 29 | repair runner CAS | v1 | ✅ T4.5 | test_online_activation.py (repair runner: repair CAS, phase=repair, T_repair_admission — skip repair_backlog) + test_failpoints_m4.py::test_next_flip_closes_blocked_backlog (find_repair_backlog → None) — строка 14, T4.5/T4.9 |
| 30 | bootstrap migration fail-closed | MVP | ✅ | test_bootstrap_migration.py (пересчёт payload-хэша, abort на mismatch; offline candidate) |
| 31 | target quiesce + admission | MVP | ✅ | test_host_admission_resume.py (fail-closed: head tuple + bootstrap hash, незавершённый host transition, active policy change, stale marker, invalid policy) |
| 32 | host transition protocol | MVP | ✅ | test_host_journal.py (fsync-safe tmp→fsync→rename→fsync(dir), immutable events, head с immutable identity, boot reconcile 0/1/≥2, head на resolved → full replay + drop) |
| 33 | recovery policy protocol | MVP | ✅ | test_host_policy.py (schema v1, jitter=0, диапазоны, JCS-хэш, symlink/missing reject) + test_policy_change_unit_state.py (install/resolve, head + event stream, terminal effective_hash, accept_current/install_replacement) |
| 34 | web degraded observer | MVP | ✅ | test_web_mvp.py (Host Status Adapter: recovery_state none/retry_wait/degraded/blocked; fail-closed Command API 423 на unresolved transition; status.host; stale/missing unit-state; SSE + session detail + pages) |

> **Оговорки к MVP-строкам (честные).** Строка 1 — задача добавлена в план
> ретроспективно (пропуск плана, T3.29); строка 2 — ModelProfile частично
> (секция `model` снапшота не подключена). Третья оговорка (2026-09-16,
> post-mortem EVAL-3b): **curated-путь до прогона EVAL-3b не исполнялся
> end-to-end ни разу** — research.search/fetch через SearXNG/fetch →
> source_assertion → external/temporal claim: все тесты M6 — на фейках
> (FakeFetchClient, fake SearXNG), реальные MVP-сессии работали в sealed-
> профиле (computed_result), корпуса EVAL-1/EVAL-2 не содержали URL-вопросов.
> MVP-приёмка (§22.2 `external_temporal_e3`) и gate M3 опираются на этот
> путь; первая реальная попытка (EVAL-3b) показала дефекты самого пути —
> `docs/eval/EVAL-3b-postmortem.md`, задачи T7.8–T7.17.

## Матрица §22.2 (познавательная оценка)

Запускается после §22.1 на замороженной конфигурации; пороги фиксируются до run.

Механизм (frozen config + 11 gates + three outcomes + blind sample +
overall outcome) — ADR-0004, `packages/evaluation/service.py`,
миграция `0020_evaluation`; тесты механизма: `tests/scenario/
test_evaluation.py` (4) + `tests/scenario/test_web_evaluation.py` (2).
Пороги фиксируются до серии (§16.3); SLO и пороги меняются только до
нового evaluation run с новой config version (§22.2).

Фактические серии: EVAL-1/EVAL-2 (корпус v1, sealed — ADR-0005/0006,
overall failed по `significant_claim_reuse`) → **EVAL-3d** (2026-09-19,
корпус v2, curated-профиль, mid-run-активация v2→v3; 50/50 сессий,
0 failed, 0 LeaseLost): исходы 11 гейтов, overall **insufficient_sample**
(приёмка §22.2 не пройдена), разбор, варианты выбора выборки следующего
прогона и слепая выборка для ручной проверки — **ADR-0008**
(`docs/eval/EVAL-3d-blind-sample.md`).

| Gate | Порог | Итог (passed/failed/insufficient_sample) |
|---|---|---|
| E2+ у новых supported/refuted | ≥80% | mechanism: ADR-0004 + test_evaluation.py (gates jsonb); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| external/temporal facts E3 | 100% в выборке ≥20 | mechanism: ADR-0004 + test_evaluation.py (insufficient_sample при N<20); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| eligible sessions с результатом | ≥60% | mechanism: ADR-0004 + test_evaluation.py (eligible/completed sessions); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| near-duplicate вопросы | ≤15% | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| переиспользование значимых claims | ≥25% / 20 сессий | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008; EVAL-4d — **0/29 failed**: разбор (три пути гейта — evidence/revisions/dependencies, 0 срабатываний; 11 паков; 14 memory.search, replay 8/14; связывающее ограничение — «follow-up'ы не коммитят знание» (22/33 паковых сессий) + шов побайтового statement+type; у claim'а нет identity (только точный statement+type), у evidence — есть (§14.3); варианты) — **T7.33**, `docs/eval/EVAL-4d-reuse-analysis.md`; **T7.34/ADR-0018 (2026-09-23, решение пользователя — направление (vii), включая минимальную правку ARCHITECTURE.md §14.1)**: перепроверка существующего claim'а теперь записывается — `claim`-операция staging с `existing_claim_id` (host-issued id, модель только ссылается; префикс — только однозначный среди видимых, fail-closed; T7.9-условие на обеих границах), запись = сессионная строка `claim_assessments` (независимо от evidence — ловушка identity: 12/20 identities якорей схлопываются, ЕС — полное), гейт 5 — пятый путь `claim_assessments.created_in_session` (порог 0.25/`at_least` не тронуты), relative-срок сдвигается к моменту перепроверки (ADR-0017, единое правило), curator-v4 + config-v6-payload (diff 1 строка); форма EVAL-4d в фикстуре: полная 8/29 = 0.276 → **passed**, строгая (5 по id) 5/29 = 0.172 → failed (сохранённый 0/29 не тронут) — тесты test_claim_reference.py (12), test_staging_schema.py (+5), test_memory_service.py (+8), test_reverify_curator.py (2), test_evaluation_gates.py (+3), раздел T7.34; **T7.38** (2026-09-24, решение пользователя: предложения (1)/(2)/(5) разбора SMOKE-V8-K2): curator-v5 + config-v9 — матрица type↔evidence в промпте (единый источник истины с `claim_type_rules` — test_curator_prompt_matrix.py), правило 7 «Перепроверка» с конкретным примером, `dependencies: []` без заглушек (корень гибели обоих паков смоука: 0/7 `existing_claim_id`, пары `computed_result←local_observation`/`local_observation←source_assertion` отбивали ВСЁ предложение) — только текст промпта + bootstrap-пин, схема/rules_hash/payload'ы v2…v8 не тронуты; проверка — повторный смоук (отдельная задача), раздел T7.38; **T7.39** (2026-09-24, решение пользователя): curator-v6 + config-v10 — пример перепроверки заменён на свободный от данных (тема вне корпусов + свежий UUID; приёмка T7.38: в v5 был настоящий id claim'а SMOKE-V8-K2), тест-страж «в примерах промптов нет реальных данных» — test_prompt_example_no_real_data.py, раздел T7.39 |
| due/stale time-sensitive | <20% | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py + test_freshness.py (T7.27/ADR-0014: правило §8.6/T3.7 по reverify_after на момент расчёта гейта, не по сохранённому полю — без зависимости от переоценки/флипа; retrieval — то же правило при чтении); T7.28/ADR-0015: строгое направление `below` (`ratio < threshold`) по спеке «<20%» — на границе 6/30 = 0.200 → failed, 6/31 → passed (до T7.28 — `at_most`, расхождение на границе); граничные тесты всех долевых гейтов — test_evaluation_gates.py (+18); actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008; EVAL-4d — passed 0/28 по сохранённому полю (артефакт), по правилу failed 22/28 — ADR-0014; сохранённых результатов g6 ровно на пороге 0.20 в прошлых прогонах нет (SELECT по noezema-eval*); T7.30/ADR-0016: опорная дата as_of строки claim — хост-деривация (явная дата вопроса > относительная форма → дата сессии по часам хоста > модельный as_of при бездатовом вопросе; модельная дата — только аудит: staging + claim_created `as_of`/`assessed_as_of`), формула reverify_after не изменена — test_scope.py (+6), test_as_of_commit.py (7: коммит relative/явная/бездат/полночь-UTC, переоценка без возврата модельной даты, re-деривация as_of при переиспользовании, форма EVAL-4d 19 relative + 6 явных старых + 3 бездатных = 28 → g6 8/28 = 0.2857 → failed — пересчёт T7.29); T7.31 (2026-09-23): заморозка EVAL-5 — корпус v4 (55 вопросов: 34 v2 + 13 v3 дословно + 8 новых; бездатных URL-вопросов 0 — все с явной датой/относительной формой; K = 2 заложенных due: ООН @ 2026-04-15, ЕС @ 2026-01-01; sha256 `b08009ff…`); предрегистрация g6 — **passed** (2/22.3 = 0.090; маржа 2: проходит при ≤ 4 due, падает при 5; N < 20 → insufficient_sample — гейт не оценён, не failed); предрегистрация g5 — failed при наблюдаемом R = 0 (EVAL-4d), но арифметически проходим в отличие от v3 (пул 28–34 ≤ 4R = 40: 10/28 = 0.357 … 10/34 = 0.294 ≥ 0.25; незакрытый в v3 конфликт g5/g6 — 11/47 = 0.234 — снят); EVAL-5-freeze.md, раздел T7.31; T7.32/ADR-0017 (2026-09-23): срок перепроверки существует ТОЛЬКО у утверждения о настоящем (якорь `relative` → now + окно volatility; якоря `explicit`/`none` — срока нет, NULL) — класс «просрочен навсегда» (улика `de9855eb`, Спутник 1957) исчезает по построению; статус `evergreen` (NULL = «срока нет по построению»; retrieval-вес 1.0 = fresh — неизменный факт не опускается как unknown); знаменатель гейта 6 = только `temporal_fact` с `reverify_after IS NOT NULL` (способные просрочиться; фиксированные моменты не проходят гейт композицией); порог 0.20 и направление `below` не тронуты; миграция 0024; предрегистрация ПЕРЕСЧИСЛЕНА: K = 0, знаменатель relative-only 20.3–22.6, ожидаемо **passed** 0/20.3 … 0/22.6 (проходит при ≤ 4 due — 4/20.3 = 0.197 < 0.20, падает при 5 — 0.246/0.221; N < 20 → insufficient_sample); тесты: test_scope.py (+3), test_freshness.py (+1), test_rules_engine.py (+1), test_as_of_commit.py (+1), test_evaluation_gates.py (+1), test_retrieval.py/test_memory_service.py/test_reassessment.py (ожидания по правилу ADR-0017) — ADR-0017, пометка в ADR-0014, EVAL-5-freeze.md §3.1, раздел T7.32 |
| reassessment SLO | зафиксировано до run | mechanism: ADR-0004 + test_evaluation.py (reassessment_slo_seconds в thresholds, фиксация до run); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (SLO 3600 с, зафиксировано); EVAL-3d — ADR-0008 |
| current assessments с pending/invalid ancestor | 0 | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| high-severity incidents | 0 | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py; actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| blind-выборка: provenance path | ≥90% | mechanism: ADR-0004 + test_evaluation.py (blind_sample_seed + size, стратификация); расчёт: gates.py + test_evaluation_gates.py (seeded, детерминизм); actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |
| blind-выборка: не выходит за scope | ≥80% | mechanism: ADR-0004 + test_evaluation.py (blind_sample_seed + size, стратификация); расчёт: gates.py + test_evaluation_gates.py (seeded, детерминизм); actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008 |

Сверка направлений сравнения с формулировками спеки (T7.28, 2026-09-22;
`_GATE_DIRECTION` + `_gate`, `packages/evaluation/gates.py`;
подробно — ADR-0015):

| Гейт | Формулировка спецификации (ARCHITECTURE.md:2602–2611) | Направление в коде | Совпадает |
|---|---|---|---|
| new_supported_refuted_e2 | «≥80% новых supported/refuted claims имеют valid E2+» | `at_least` (`ratio >= threshold`) | ✅ |
| external_temporal_e3 | «каждый supported/refuted `external_fact \| temporal_fact` в достаточной выборке выполняет E3 rule» (N<20 → insufficient_sample) | `at_least`, порог 1.00 («каждый» = 100%) | ✅ |
| eligible_sessions_with_outcome | «≥60% eligible sessions создают evidence, закрывают/уточняют вопрос или пересматривают claim» | `at_least` (`ratio >= threshold`) | ✅ |
| near_duplicate_questions | «≤15% вопросов — near-duplicates без нового метода» | `at_most` (`ratio <= threshold`) | ✅ |
| significant_claim_reuse | «≥25% значимых claims переиспользуются/перепроверяются» | `at_least` (`ratio >= threshold`) | ✅ |
| due_stale_time_sensitive | «due/stale time-sensitive claims <20%» — **строго** меньше | `below` (`ratio < threshold`) — T7.28/ADR-0015; до T7.28 было `at_most` — расхождение: ровно 20% проходило (6/30 → passed вместо failed по спеке) | ✅ после T7.28 (до — ❌) |
| reassessment_slo | «runnable dependency-critical reassessment jobs укладываются в предварительно зафиксированный wall-clock SLO» (все runnable) | `at_least`, порог 1.00 («все» = 100%) | ✅ |
| current_pending_invalid_ancestor | «zero current assessments с pending/invalid ancestor» | `count == 0` | ✅ |
| high_severity_incidents | «zero unresolved high-severity policy/idempotency/command boundary incidents» | `count == 0` | ✅ |
| blind_provenance_path | «в слепой выборке ≥90% имеют provenance path» | `at_least` (`ratio >= threshold`) | ✅ |
| blind_scope | «≥80% не выходят за evidence scope» | `at_least` (`ratio >= threshold`) | ✅ |

Расхождение с формулировками спеки было ровно у одного гейта
(due_stale_time_sensitive, `<` vs `<=` на границе) — исправлено T7.28.
Граничные тесты для каждого долевого гейта ровно на пороге и рядом с
ним (включая 6/30 → failed и 6/31 → passed; Wilson-95 зафиксирован) —
`tests/scenario/test_evaluation_gates.py` (+18).

Направления — **закрытый набор** (T7.28, follow-up):
`GateDirection = Literal["at_least", "at_most", "below"]`
(`_GATE_DIRECTION: dict[str, GateDirection]`, параметр `direction` в
`_gate()`) — mypy strict статически отвергает неизвестное значение на
всех 9 call-sites; `_gate()` дополнительно fail-closed с
`ValueError` (название гейта + полученное значение) для значения,
дошедшего без типизации — молчаливого значения по умолчанию
("at_most") нет (`tests/unit/test_gate_direction.py`).


## Индекс перенесённого

Разделы ниже перенесены побайтово в [STATUS-archive.md](STATUS-archive.md) (T7.42, 2026-09-25 и T7.122, 2026-10-19):
строка = заголовок, диапазон дат, одна строка сути, ссылка. Оглавление с якорями — в начале архива.

- **Gate M2 (§19, этап 2) — пройден (noezema-m2)** (2026-09, PR #11–#16) — таблица gate: 4 критерия со ссылками на тесты (reconciler, failpoints, orchestrator) → [архив](STATUS-archive.md#gate-m2-19-этап-2--пройден-noezema-m2)
- **Gate M3 (этап 3a + MVP-критерии §22.1)** (2026-09-14, PR #17–#21) — gate пройден (T3.29, `ec6b4b0`, tag noezema-m3; noezema-mvp на `5d94b27`); в том же разделе — история M4: T4.1–T4.9 + Gate M4 пройден (501 тест) → [архив](STATUS-archive.md#gate-m3-этап-3a--mvp-критерии-221)
- **Закрыто T3.29 (пункт 1 §22.1)** (2026-09-14) — wake scheduling + wake admission + backoff/pause (§5.2.1, миграция 0005) → [архив](STATUS-archive.md#закрыто-t329-пункт-1-221)
- **Закрыто T3.30 (дефект из первой реальной MVP-сессии)** (2026-09-14) — heartbeat lease + `clock_timestamp()`; повторная реальная сессия SUCCEEDED; серия MVP-сессий и замеры нагрузки (max_output_tokens=8192) → [архив](STATUS-archive.md#закрыто-t330-дефект-из-первой-реальной-mvp-сессии)
- **M5. Расширенный познавательный цикл (этап 4)** (2026-09-15) — T5.1–T5.6 (curiosity, планирование, verifier, повторы, untrusted extraction, long-horizon) + Gate M5 пройден; в том же разделе — M6: T6.1–T6.4 + Gate M6 пройден (651 тест) → [архив](STATUS-archive.md#m5-расширенный-познавательный-цикл-этап-4)
- **Gate M6 (§19, этап 5) — пройден** (2026-09-15) — таблица gate: 5 критериев со ссылками на тесты (egress, режимы, provenance, poison, SSRF) → [архив](STATUS-archive.md#gate-m6-19-этап-5--пройден)
- **M7. Полный веб + эксплуатация (этапы 6-full + 7)** (2026-09-15–16) — T7.1–T7.6 (knowledge graph, backup/PITR, GC, security gate, evaluation §22.2, ADR-0004) + Gate M7 пройден (noezema-m7, `2e1631c`); EVAL-1/2 (ADR-0005/0006), двуязычный поиск, research.fetch→source_assertion, EVAL-3 (freeze + failure), EVAL-3b (P.1–P.5), T7.8–T7.12 → [архив](STATUS-archive.md#m7-полный-веб--эксплуатация-этапы-6-full--7)
- **T7.13 — `message.reply` не предлагать при пустом inbox + самоочевидная схема (EVAL-3b P.6)** (2026-09-17) — per-step-фильтр `message.reply` при пустом inbox + самоочевидная схема инструмента → [архив](STATUS-archive.md#t713--messagereply-не-предлагать-при-пустом-inbox--самоочевидная-схема-eval-3b-p6)
- **T7.14 — правила в промптах (EVAL-3b P.2/P.4)** (2026-09-17–18) — explorer-v3/curator-v3; + вмятые подразделы: T7.15 — E2E-валидация E3 на черновой БД, T7.16 — вопрос-зависимое окно фрагмента (26 тест) → [архив](STATUS-archive.md#t714--правила-в-промптах-eval-3b-p2p4)
- **T7.17 — устойчивый scope: оценка по хост-деривации, rules-v2 (EVAL-3/T7.15, §3.7, §8.7, §11.2)** (2026-09-18) — scope-деривация хостом, rules-v2, ADR-0007; 765 тест → [архив](STATUS-archive.md#t717--устойчивый-scope-оценка-по-хост-деривации-rules-v2-eval-3t715-37-87-112)
- **Merge T7.8–T7.17 в `main` (2026-09-18)** (2026-09-18) — merge `4b49f00`, проверки зелёные (765 passed) → [архив](STATUS-archive.md#merge-t78t717-в-main-2026-09-18)
- **T7.18 — относительная опорная дата выводится хостом (шаг 1 перезапуска EVAL-3c, ADR-0007, §3.7, §8.7)** (2026-09-19–20) — опорная дата «на текущую дату» = дата сессии по часам хоста; + вмятый подраздел T7.19 — гейты считают ровно один head на claim (777 тест) → [архив](STATUS-archive.md#t718--относительная-опорная-дата-выводится-хостом-шаг-1-перезапуска-eval-3c-adr-0007-37-87)
- **Merge T7.18–T7.20 в `main` (2026-09-20)** (2026-09-20) — merge `56518b7`, 782 passed → [архив](STATUS-archive.md#merge-t718t720-в-main-2026-09-20)
- **T7.22 — окно assertion-фрагмента: второе (value) окно для группы A EVAL-3d (§6.4, ADR-0011)** (2026-09-20–21) — value-окно вокруг позиции цифры в факт-зоне; 7 из 12 промахов группы A чинимы → [архив](STATUS-archive.md#t722--окно-assertion-фрагмента-второе-value-окно-для-группы-a-eval-3d-64-adr-0011)
- **T7.23 — совместимость структурированного вывода с движками, не принимающими часть ключевых слов JSON Schema (halogen: format/pattern), fail-closed audit (§5.2.2, §6.5, ADR-0012)** (2026-09-20–21) — schema profiles (halogen/llamacpp-rocmfpx) + fail-closed audit; freeze EVAL-4 (ADR-0010) → [архив](STATUS-archive.md#t723--совместимость-структурированного-вывода-с-движками-не-принимающими-часть-ключевых-слов-json-schema-halogen-formatpattern-fail-closed-audit-522-65-adr-0012)
- **T7.24 — обрыв EVAL-4 (2026-09-21): commit-boundary dead end — reconcile-tick + staging-последовательность + known rollback → terminal (§5.2.2, §6.5, §6.7)** (2026-09-21) — reconcile-tick (noezemactl), миграция 0023 (seq), known rollback → terminal → [архив](STATUS-archive.md#t724--обрыв-eval-4-2026-09-21-commit-boundary-dead-end--reconcile-tick--staging-последовательность--known-rollback--terminal-522-65-67)
- **T7.25 — переиспользование между днями claim'а с относительной датой: решение (а) — корректный fail-closed, не дефект (ADR-0007, §3.7, §8.2, §8.6, §8.7, §11.3)** (2026-09-21) — решение (а) — код не тронут; EVAL-4c закрыт прерванным → [архив](STATUS-archive.md#t725--переиспользование-между-днями-claimа-с-относительной-датой-решение-а--корректный-fail-closed-не-дефект-adr-0007-37-82-86-87-113)
- **Корпус v3 (EVAL-4) — 2026-09-20** (2026-09-20) — 69 вопросов (50 v2 дословно + 19 новых), sha256 `20db6f0d…`; test_question_set_v3_corpus_shape → [архив](STATUS-archive.md#корпус-v3-eval-4--2026-09-20)
- **T7.26 — drain-протокол online-активации: ожидание «окна» в живой серийной серии без гонки T7.20 (EVAL-4d, §8.7.2, уточнение ADR-0009 — ADR-0013)** (2026-09-21–22) — drain-ожидание в acquire_activation; 836 тест → [архив](STATUS-archive.md#t726--drain-протокол-online-активации-ожидание-окна-в-живой-серийной-серии-без-гонки-t720-eval-4d-872-уточнение-adr-0009--adr-0013)
- **T7.27 — свежесть claim'ов: правило §8.6/T3.7 вычисляется на момент чтения в гейте и retrieval; сохранённое поле — дисплейный кэш (EVAL-4d, исправление под спецификацию — ADR-0014)** (2026-09-22) — freshness на момент чтения; 847 тест → [архив](STATUS-archive.md#t727--свежесть-claimов-правило-86t37-вычисляется-на-момент-чтения-в-гейте-и-retrieval-сохранённое-поле--дисплейный-кэш-eval-4d-исправление-под-спецификацию--adr-0014)
- **T7.28 — гейт `due_stale_time_sensitive`: строгое направление `<` по спецификации (ADR-0015, уточнение ADR-0004)** (2026-09-22) — строго `<`; 865 тест → [архив](STATUS-archive.md#t728--гейт-due_stale_time_sensitive-строгое-направление--по-спецификации-adr-0015-уточнение-adr-0004)
- **T7.28 (follow-up) — направления гейтов — закрытый набор: `GateDirection` Literal + fail-closed в `_gate()`** (2026-09-22) — Literal + ValueError на неизвестное направление; 877 тест → [архив](STATUS-archive.md#t728-follow-up--направления-гейтов--закрытый-набор-gatedirection-literal--fail-closed-в-_gate)
- **Таблица вех — исходные ячейки «Статус»/«Примечание» (дословно)** (T7.42, 2026-09-25) — дословные ячейки M0–M7 до сжатия таблицы в T7.42 → [архив](STATUS-archive.md#таблица-вех--исходные-ячейки-статуспримечание-дословно)

Задачи T7.29–T7.105 перенесены побайтово (T7.122, 2026-10-19):

- **Активная история (T7.29–T7.53)** (2026-09-22…2026-10-04) — групповой раздел активных задач T7.29–T7.53 → [архив](STATUS-archive.md#активная-история-t729t753)
- **T7.29 — разбор поля `as_of` у `temporal_fact` (EVAL-4d): определение по спеке, улика по 22 claim'ам, варианты решения — без изменения поведения** (2026-09-22) — разбор `as_of` по спеке и улика по 22 claim'ам, варианты решения; поведение не менялся → [архив](STATUS-archive.md#t729--разбор-поля-as_of-у-temporal_fact-eval-4d-определение-по-спеке-улика-по-22-claimам-варианты-решения--без-изменения-поведения)
- **T7.30 — базовая дата свежести `as_of` выводится хостом (решение пользователя 2026-09-22: варианты (iii)+(iv) T7.29) — ADR-0016** — базовую дату свежести выводит хост, модельная дата — аудиторская (ADR-0016) → [архив](STATUS-archive.md#t730--базовая-дата-свежести-as_of-выводится-хостом-решение-пользователя-2026-09-22-варианты-iiiiv-t729--adr-0016)
- **T7.31 — корпус v4 + заморозка EVAL-5 (2026-09-23; прогон НЕ выполнялся — решение о запуске за пользователем)** — корпус v4 и заморозка EVAL-5; прогон не выполнялся → [архив](STATUS-archive.md#t731--корпус-v4--заморозка-eval-5-2026-09-23-прогон-не-выполнялся--решение-о-запуске-за-пользователем)
- **T7.32 — срок перепроверки только у утверждения о настоящем (решение пользователя 2026-09-23; ADR-0017)** — срок перепроверки — только у утверждений о настоящем (ADR-0017) → [архив](STATUS-archive.md#t732--срок-перепроверки-только-у-утверждения-о-настоящем-решение-пользователя-2026-09-23-adr-0017)
- **T7.33 — разбор нулевого переиспользования гейта 5 (EVAL-4d): связывающее ограничение + варианты — без изменения поведения** (2026-09-23) — разбор нуля переиспользования гейта 5 (EVAL-4d); поведение не менялся → [архив](STATUS-archive.md#t733--разбор-нулевого-переиспользования-гейта-5-eval-4d-связывающее-ограничение--варианты--без-изменения-поведения)
- **T7.34 — перепроверка существующего claim'а записывается (решение пользователя 2026-09-23: направление (vii) из EVAL-4d §7.5, включая минимальную правку ARCHITECTURE.md; ADR-0018)** — перепроверка записывается записью, привязанной к сессии (ADR-0018) → [архив](STATUS-archive.md#t734--перепроверка-существующего-claimа-записывается-решение-пользователя-2026-09-23-направление-vii-из-eval-4d-75-включая-минимальную-правку-architecturemd-adr-0018)
- **T7.35 — промпт привязан к snapshot'у по СОДЕРЖИМОМУ (ADR-0019, §8.7.1, §12, §14)** (2026-09-23…2026-09-24) — промпт привязан к снапшоту содержимым, закреплённым хэшем (ADR-0019) → [архив](STATUS-archive.md#t735--промпт-привязан-к-snapshotу-по-содержимому-adr-0019-871-12-14)
- **T7.35 (follow-up) — config-v8: пин `explorer-v4` для смоук-прогона (ADR-0019)** (2026-09-23…2026-09-24) — config-v8: пин explorer-v4 для смоук-прогона → [архив](STATUS-archive.md#t735-follow-up--config-v8-пин-explorer-v4-для-смоук-прогона-adr-0019)
- **T7.36 — профиль схемы `llamacpp-rocmfpx` для K2 Horizon MoVA на .48 (ADR-0012)** (2026-09-24) — профиль схемы `llamacpp-rocmfpx` для движка на .48 (ADR-0012) → [архив](STATUS-archive.md#t736--профиль-схемы-llamacpp-rocmfpx-для-k2-horizon-mova-на-48-adr-0012)
- **T7.37 — SMOKE-V8-K2: смоук-прогон новых T7.30/T7.32/T7.34/T7.35/T7.36 на K2 и его разбор (2026-09-24)** — смоук-прогон новых задач T7.30–T7.36 на K2 и его разбор → [архив](STATUS-archive.md#t737--smoke-v8-k2-смоук-прогон-новых-t730t732t734t735t736-на-k2-и-его-разбор-2026-09-24)
- **T7.38 — curator-v5 и config-v9: матрица type↔evidence, пример перепроверки, dependencies (решение пользователя 2026-09-24: предложения (1), (2), (5) разбора SMOKE-V8-K2 — одна правка промпта, один новый payload)** — curator-v5/config-v9: матрица type↔evidence, пример перепроверки, dependencies → [архив](STATUS-archive.md#t738--curator-v5-и-config-v9-матрица-typeevidence-пример-перепроверки-dependencies-решение-пользователя-2026-09-24-предложения-1-2-5-разбора-smoke-v8-k2--одна-правка-промпта-один-новый-payload)
- **T7.39 — curator-v6 и config-v10: безопасный пример перепроверки (решение пользователя 2026-09-24)** — curator-v6/config-v10: безопасный пример перепроверки → [архив](STATUS-archive.md#t739--curator-v6-и-config-v10-безопасный-пример-перепроверки-решение-пользователя-2026-09-24)
- **T7.40 — повторный смоук SMOKE-V10B-K2: закрытие прерванного прогона, перезапуск и разбор (решение пользователя 2026-09-24)** — перезапуск прерванного смоук-прогона и разбор → [архив](STATUS-archive.md#t740--повторный-смоук-smoke-v10b-k2-закрытие-прерванного-прогона-перезапуск-и-разбор-решение-пользователя-2026-09-24)
- **T7.41 — полная проверка параллельно: pytest-xdist `-n auto` (решение пользователя 2026-09-25: «займёмся оптимизацией»)** — полная проверка распараллелена pytest-xdist `-n auto` → [архив](STATUS-archive.md#t741--полная-проверка-параллельно-pytest-xdist--n-auto-решение-пользователя-2026-09-25-займёмся-оптимизацией)
- **T7.43 — curator-v7 и config-v11: обязательное связывание evidence + правило 7 — негативный пример и случай слабого claim'а (решение пользователя 2026-09-25: два дефекта SMOKE-V10B-K2 — предложения (1) и (2) высокого приоритета)** — curator-v7/config-v11: обязательное связывание evidence + правило 7 → [архив](STATUS-archive.md#t743--curator-v7-и-config-v11-обязательное-связывание-evidence--правило-7--негативный-пример-и-случай-слабого-claimа-решение-пользователя-2026-09-25-два-дефекта-smoke-v10b-k2--предложения-1-и-2-высокого-приоритета)
- **T7.44 — SMOKE-V11-HALOGEN: повторный смоук на config-v11 (curator-v7) + halogen и его разбор (a: переключение на halogen и запуск; b: остановка воркера и разбор в сравнении с SMOKE-V10B-K2)** (2026-09-25) — смоук config-v11 (curator-v7) + halogen и его разбор → [архив](STATUS-archive.md#t744--smoke-v11-halogen-повторный-смоук-на-config-v11-curator-v7--halogen-и-его-разбор-a-переключение-на-halogen-и-запуск-b-остановка-воркера-и-разбор-в-сравнении-с-smoke-v10b-k2)
- **T7.45 — SMOKE-V12-K2: чистый смоук curator-v7 + K2 и его разбор (a: переключение обратно на K2 и запуск; b: остановка воркера, разбор в сравнении с SMOKE-V10B-K2 + документирование NUL-байт дефекта как открытого пункта)** (2026-09-25) — чистый смоук curator-v7 + K2 и его разбор → [архив](STATUS-archive.md#t745--smoke-v12-k2-чистый-смоук-curator-v7--k2-и-его-разбор-a-переключение-обратно-на-k2-и-запуск-b-остановка-воркера-разбор-в-сравнении-с-smoke-v10b-k2--документирование-nul-байт-дефекта-как-открытого-пункта)
- **T7.46 — починка двух дефектов SMOKE-V12-K2 (a: NUL-байт роняет commit сессии; b: time bomb в restore-drill) — ADR-0020, ADR-0021** (2026-09-25) — NUL в выводе инструмента больше не роняет commit; drill на инъекционном часе (ADR-0020, ADR-0021) → [архив](STATUS-archive.md#t746--починка-двух-дефектов-smoke-v12-k2-a-nul-байт-роняет-commit-сессии-b-time-bomb-в-restore-drill--adr-0020-adr-0021)
- **T7.47a — NUL в тексте МОДЕЛИ (четвёртый канал, вне трёх границ T7.46a): граница на входе ответа + защитная линия staging — ADR-0020 (дополнение T7.47a)** (2026-09-25) — граница NUL на входе ответа модели + защитная линия staging (дополнение ADR-0020) → [архив](STATUS-archive.md#t747a--nul-в-тексте-модели-четвёртый-канал-вне-трёх-границ-t746a-граница-на-входе-ответа--защитная-линия-staging--adr-0020-дополнение-t747a)
- **T7.47b — flake `test_slow_llm_does_not_lose_commit_lease` под xdist: НЕ голодание — реальная гонка в `LeaseHeartbeatGuard` (cancel посреди execute отравляет общую сессию)** (2026-09-25) — flake аренды — гонка в `LeaseHeartbeatGuard`, не голодание → [архив](STATUS-archive.md#t747b--flake-test_slow_llm_does_not_lose_commit_lease-под-xdist-не-голодание--реальная-гонка-в-leaseheartbeatguard-cancel-посреди-execute-отравляет-общую-сессию)
- **T7.48 — SMOKE-V13-K2: чистое повторение после починки NUL/lease (запуск T7.48a, анализ T7.48b)** (2026-09-26) — чистое повторение смоука после починки NUL/lease и разбор → [архив](STATUS-archive.md#t748--smoke-v13-k2-чистое-повторение-после-починки-nullease-запуск-t748a-анализ-t748b)
- **T7.49 — host-нормализация `complete_reason` (решение по рекомендации T7.37b/SMOKE-V13-K2-report §8; ADR-0022)** (2026-09-26) — host-нормализация `complete_reason` (ADR-0022) → [архив](STATUS-archive.md#t749--host-нормализация-complete_reason-решение-по-рекомендации-t737bsmoke-v13-k2-report-8-adr-0022)
- **T7.50 — explorer-v5 + config-v12: класс D (free-form `complete_reason`) лечится у источника — промптом (решение пользователя 2026-09-26: вариант A ADR-0022)** — explorer-v5/config-v12: класс D лечится у источника промптом (вариант A ADR-0022) → [архив](STATUS-archive.md#t750--explorer-v5--config-v12-класс-d-free-form-complete_reason-лечится-у-источника--промптом-решение-пользователя-2026-09-26-вариант-a-adr-0022)
- **T7.51 — flake `test_slow_llm_does_not_lose_commit_lease` под `-n auto`: ВТОРОЙ режим отказа — голодание продления аренды (не гонка T7.47b); лечение — масштаб времени теста, TTL 1 с → 3 с** (2026-10-03) — второй режим flake аренды — голодание продления; масштаб времени тестов → [архив](STATUS-archive.md#t751--flake-test_slow_llm_does_not_lose_commit_lease-под--n-auto-второй-режим-отказа--голодание-продления-аренды-не-гонка-t747b-лечение--масштаб-времени-теста-ttl-1-с--3-с)
- **T7.52 — SMOKE-V14-EXL3: первый бой EXL3 + explorer-v5/config-v12 (a: переключение модели и запуск; b: остановка воркера и разбор) — КОНФАУНД: две переменные сразу** (2026-10-04) — первый смоук на EXL3 + explorer-v5/config-v12; конфаунд двух переменных отмечен → [архив](STATUS-archive.md#t752--smoke-v14-exl3-первый-бой-exl3--explorer-v5config-v12-a-переключение-модели-и-запуск-b-остановка-воркера-и-разбор--конфаунд-две-переменные-сразу)
- **T7.53 — SMOKE-V14B-EXL3: контрольный прогон EXL3 + explorer-v4/config-v11 (a: запуск; b: остановка воркера и разбор) — конфаунд T7.52 разведён по одному фактору** (2026-10-04) — контрольный прогон EXL3 + explorer-v4/config-v11 — конфаунд разведён по одному фактору → [архив](STATUS-archive.md#t753--smoke-v14b-exl3-контрольный-прогон-exl3--explorer-v4config-v11-a-запуск-b-остановка-воркера-и-разбор--конфаунд-t752-разведён-по-одному-фактору)
- **T7.54 — CI (GitHub Actions) красная на каждом пуше с ≥2026-09-25: две независимые причины — незакреплённая версия ruff и отсутствующая сборка sandbox-образа в ci.yml** — чистка CI: незакреплённый ruff и отсутствие сборки sandbox-образа в ci.yml → [архив](STATUS-archive.md#t754--ci-github-actions-красная-на-каждом-пуше-с-2026-09-25-две-независимые-причины--незакреплённая-версия-ruff-и-отсутствующая-сборка-sandbox-образа-в-ciyml)
- **T7.55 (часть 1, коммит с фикстурой) — migrated_db: клон scratch-БД из шаблона вместо `alembic upgrade head` на каждый тест** (2026-10-04) — scratch-БД клонируются из session-шаблонов вместо миграции на каждый тест → [архив](STATUS-archive.md#t755-часть-1-коммит-с-фикстурой--migrated_db-клон-scratch-бд-из-шаблона-вместо-alembic-upgrade-head-на-каждый-тест)
- **T7.56 — flake lease-семейства под `-n auto`: лечение абсолютных времён unit-guard тестов (TTL 0,6→3,0 с) + последовательный stage `timing` в полной проверке и CI; код продукта не тронут** (2026-10-04) — lease-тесты вынесены в последовательный stage `timing` полной проверки → [архив](STATUS-archive.md#t756--flake-lease-семейства-под--n-auto-лечение-абсолютных-времён-unit-guard-тестов-ttl-0630-с--последовательный-stage-timing-в-полной-проверке-и-ci-код-продукта-не-тронут)
- **T7.57 — два независимых дефекта eval: extract_question_urls и скобки URL (SMOKE-V14 §4.3) + диагностика shell.execute в Stub-исполнителе** (2026-10-04) — дефекты eval: обрезка URL по скобке и диагностика shell.execute в stub → [архив](STATUS-archive.md#t757--два-независимых-дефекта-eval-extract_question_urls-и-скобки-url-smoke-v14-43--диагностика-shellexecute-в-stub-исполнителе)
- **T7.58 — инструментарный исполнитель сессий: переключатель stub | sandbox, одноразовый контейнер в жизненном цикле `run_session` (реализация рекомендации «Б» T7.57; ADR-0023)** (2026-10-04) — исполнитель инструментов: переключатель stub|sandbox, одноразовый контейнер на сессию (ADR-0023) → [архив](STATUS-archive.md#t758--инструментарный-исполнитель-сессий-переключатель-stub--sandbox-одноразовый-контейнер-в-жизненном-цикле-run_session-реализация-рекомендации-б-t757-adr-0023)
- **T7.59(а) — приём вопроса оператором: CLI `ask`, `POST/GET /api/v1/questions`, форма на главной странице (ADR-0024)** (2026-10-04) — приём вопроса оператором: `ask`, API и форма на главной (ADR-0024) → [архив](STATUS-archive.md#t759а--приём-вопроса-оператором-cli-ask-postget-apiv1questions-форма-на-главной-странице-adr-0024)
- **T7.59(б) — пакет dev-стенда `deploy/dev-stand/`: bootstrap, dev-юниты, status/reset, web bind из env и fail-closed** (2026-10-04) — пакет dev-стенда `deploy/dev-stand`: bootstrap, юниты, status/reset, web bind из env → [архив](STATUS-archive.md#t759б--пакет-dev-стенда-deploydev-stand-bootstrap-dev-юниты-statusreset-web-bind-из-env-и-fail-closed)
- **T7.59(в) — дефекты, найденные при развёртывании dev-стенда на 192.168.1.92** (2026-10-04) — дефекты, найденные при развёртывании dev-стенда → [архив](STATUS-archive.md#t759в--дефекты-найденные-при-развёртывании-dev-стенда-на-192168192)
- **T7.61 — узел не запускает две сессии одновременно** (2026-10-05) — одна сессия на узел — session-level advisory lock (ADR-0025) → [архив](STATUS-archive.md#t761--узел-не-запускает-две-сессии-одновременно)
- **T7.61(а) — гонка «wake now» + запланированного тика: разбор (до правки кода)** (2026-10-05) — разбор гонки wake now + планового тика, до правки кода → [архив](STATUS-archive.md#t761а--гонка-wake-now--запланированного-тика-разбор-до-правки-кода)
- **T7.61(б) — стенд: сессию запускает оператор; артефакты research_proxy в data root узла** (2026-10-05) — сессии на стенде запускает оператор; артефакты research_proxy — в data root узла → [архив](STATUS-archive.md#t761б--стенд-сессию-запускает-оператор-артефакты-research_proxy-в-data-root-узла)
- **T7.62 — флаки статусных тестов: барьеры вместо «условия для каждого снимка» (тесты+доки; продукт не тронут)** (2026-10-05) — барьеры вместо «условия для каждого снимка»; продукт не тронут → [архив](STATUS-archive.md#t762--флаки-статусных-тестов-барьеры-вместо-условия-для-каждого-снимка-тестыдоки-продукт-не-тронут)
- **T7.63 — teardown scratch-БД: «database is being accessed by other users» и утечка `noezema_mig_*`** (2026-10-05) — teardown scratch-БД без утечек `noezema_mig_*` → [архив](STATUS-archive.md#t763--teardown-scratch-бд-database-is-being-accessed-by-other-users-и-утечка-noezema_mig_)
- **T7.64 — единый серверный словарь подписей и шкала надёжности; аддитивные `label`/`hint` в JSON API (M7, этап 1 из 3)** (2026-10-05) — единый словарь подписей и шкала надёжности; `label`/`hint` в JSON API → [архив](STATUS-archive.md#t764--единый-серверный-словарь-подписей-и-шкала-надёжности-аддитивные-labelhint-в-json-api-m7-этап-1-из-3)
- **T7.65 — простая главная «Вопросы и ответы», карточка ответа и её JSON-API (M7, этап 2 из 3)** (2026-10-05) — простая главная «Вопросы и ответы» и карточка ответа с JSON-API → [архив](STATUS-archive.md#t765--простая-главная-вопросы-и-ответы-карточка-ответа-и-её-json-api-m7-этап-2-из-3)
- **T7.66 — внешний доступ (research proxy): анализ живой ошибки «not configured» и проект включения (M7; без кода)** (2026-10-05) — анализ внешней ошибки research proxy и проект включения (без кода) → [архив](STATUS-archive.md#t766--внешний-доступ-research-proxy-анализ-живой-ошибки-not-configured-и-проект-включения-m7-без-кода)
- **T7.68 — единый сборщик сессионного оркестратора: web «wake now» получает research proxy (M7; ADR-0027 §1, G1 закрыт). 2026-10-06** — единый сборщик сессионного оркестратора: web wake now с research proxy (ADR-0027 §1) → [архив](STATUS-archive.md#t768--единый-сборщик-сессионного-оркестратора-web-wake-now-получает-research-proxy-m7-adr-0027-1-g1-закрыт-2026-10-06)
- **T7.69 — серия подбора модели NOEZEMA (a/c — прогоны, b — разбор). 2026-10-06** — серия подбора модели NOEZEMA и её разбор → [архив](STATUS-archive.md#t769--серия-подбора-модели-noezema-ac--прогоны-b--разбор-2026-10-06)
- **T7.70/T7.71 — поиск на dev-стенде (SearXNG) и инструмент поиска для модели: анализ до кода (M7; G3, G8/G9)** (2026-10-06) — анализ до кода: SearXNG на dev-стенде и инструмент `web.search` для модели → [архив](STATUS-archive.md#t770t771--поиск-на-dev-стенде-searxng-и-инструмент-поиска-для-модели-анализ-до-кода-m7-g3-g8g9)
- **T7.67a — «Мои вопросы»: нумерация, порядок «последние сверху», краткий ответ;** (2026-10-06) — «Мои вопросы»: нумерация, порядок «последние сверху», краткий ответ → [архив](STATUS-archive.md#t767a--мои-вопросы-нумерация-порядок-последние-сверху-краткий-ответ)
- **T7.73 — честность понижения оценки при перепроверке (стенд .92: кейс 66c7901a → da2abfc1). Анализ до кода** (2026-10-07) — анализ честности понижения оценки при перепроверке (стендовый кейс) → [архив](STATUS-archive.md#t773--честность-понижения-оценки-при-перепроверке-стенд-92-кейс-66c7901a--da2abfc1-анализ-до-кода)
- **T7.74 — карточка нового вопроса видит то, что он перепроверил (стенд .92: вопрос №14 `7734e5b9`). Анализ до кода** (2026-10-07) — анализ: карточка нового вопроса видит то, что он перепроверил → [архив](STATUS-archive.md#t774--карточка-нового-вопроса-видит-то-что-он-перепроверил-стенд-92-вопрос-14-7734e5b9-анализ-до-кода)
- **T7.75 — производные источники: пересказ релиза не даёт второй группы независимости (ADR-0029, вариант B)** (2026-10-07) — производная публикация не даёт второй группы независимости (ADR-0029, вариант B) → [архив](STATUS-archive.md#t775--производные-источники-пересказ-релиза-не-даёт-второй-группы-независимости-adr-0029-вариант-b)
- **T7.76 — двусторонний поиск: почему узел ищет только официальную сторону и не видит ни своего бюджета, ни схем инструментов. Анализ до правок** (2026-10-07) — анализ одностороннего поиска и невидимости бюджета/схем инструментов → [архив](STATUS-archive.md#t776--двусторонний-поиск-почему-узел-ищет-только-официальную-сторону-и-не-видит-ни-своего-бюджета-ни-схем-инструментов-анализ-до-правок)
- **T7.77 — типографика детектора производности, переоценка затронутых утверждений и российский корневой сертификат. Анализ до правок** (2026-10-07) — анализ: типографика детектора производности, переоценка затронутых, корневой сертификат → [архив](STATUS-archive.md#t777--типографика-детектора-производности-переоценка-затронутых-утверждений-и-российский-корневой-сертификат-анализ-до-правок)
- **T7.78 — производность не страницы, а конкретного значения: анализ до правок** (2026-10-07) — анализ производности конкретного значения, а не страницы → [архив](STATUS-archive.md#t778--производность-не-страницы-а-конкретного-значения-анализ-до-правок)
- **T7.79 — окно по значению вопроса и честный итог вопроса без утверждений: анализ до правок** (2026-10-07) — анализ: окно по значению вопроса и честный итог вопроса без утверждений → [архив](STATUS-archive.md#t779--окно-по-значению-вопроса-и-честный-итог-вопроса-без-утверждений-анализ-до-правок)
- **T7.79a — точный сигнал фиксирует окно вместо выброса: разбор стендового промаха 2026-10-08** — точный сигнал фиксирует окно вместо выброса: разбор стендового промаха → [архив](STATUS-archive.md#t779a--точный-сигнал-фиксирует-окно-вместо-выброса-разбор-стендового-промаха-2026-10-08)
- **T7.80 — профиль рассуждения движка по фазам вызова и обрезанный лимитом ответ: анализ до правок** (2026-10-08) — анализ профиля рассуждения движка по фазам вызова (ADR-0030) → [архив](STATUS-archive.md#t780--профиль-рассуждения-движка-по-фазам-вызова-и-обрезанный-лимитом-ответ-анализ-до-правок)
- **T7.81 — инструмент оператора для спорного утверждения (стенд .92, `c970bc08`): анализ до правок** (2026-10-08) — анализ инструментального спора утверждения оператором (ADR-0031) → [архив](STATUS-archive.md#t781--инструмент-оператора-для-спорного-утверждения-стенд-92-c970bc08-анализ-до-правок)
- **T7.82 — куратор слеп к ключевому факту прочитанной страницы и к собственной опоре на прежнее знание (стенд .92, сессия `f0d7e844`): два дефекта и две правки** (2026-10-08) — два дефекта: ключевой факт прочитанной страницы и опора на прежнее знание (relied_claim_ids, config-v19) → [архив](STATUS-archive.md#t782--куратор-слеп-к-ключевому-факту-прочитанной-страницы-и-к-собственной-опоре-на-прежнее-знание-стенд-92-сессия-f0d7e844-два-дефекта-и-две-правки)
- **T7.83 — одно и то же значение показателя записывается дважды рядом с собой: хостовый гейт дублей по значению и абзац о перефразе в промпте куратора (стенд .92, сессия `f452a978`)** (2026-10-09) — хостовый гейт дублей по значению + абзац о перефразе в промпте куратора (ADR-0033) → [архив](STATUS-archive.md#t783--одно-и-то-же-значение-показателя-записывается-дважды-рядом-с-собой-хостовый-гейт-дублей-по-значению-и-абзац-о-перефразе-в-промпте-куратора-стенд-92-сессия-f452a978)
- **T7.83a — уточнение гейта дублей по значению по замечанию приёмки: склейка только с опорным** (2026-10-09) — склейка дублей только с опорным утверждением (замечание приёмки) → [архив](STATUS-archive.md#t783a--уточнение-гейта-дублей-по-значению-по-замечанию-приёмки-склейка-только-с-опорным)
- **T7.84 — отказ куратора становится объяснимым, а проверка опорной даты — после определения вида операции (стенд .92, сессия `ed36f4a0`; ADR-0033 §8, ADR-0034)** (2026-10-09) — объяснимый отказ куратора + проверка as_of после вида операции (ADR-0033 §8, ADR-0034) → [архив](STATUS-archive.md#t784--отказ-куратора-становится-объяснимым-а-проверка-опорной-даты--после-определения-вида-операции-стенд-92-сессия-ed36f4a0-adr-0033-8-adr-0034)
- **T7.85 — пересказ узнаётся по конкретному значению в живых страницах, прежние решения пересматриваются, дубль той же формулировки склеивается честно (стенд .92; ADR-0029 дополнение, ADR-0033 §9)** (2026-10-09) — пересказ узнаётся по конкретному значению; склейка дубля формулировки честная (ADR-0029) → [архив](STATUS-archive.md#t785--пересказ-узнаётся-по-конкретному-значению-в-живых-страницах-прежние-решения-пересматриваются-дубль-той-же-формулировки-склеивается-честно-стенд-92-adr-0029-дополнение-adr-0033-9)
- **T7.85a — окно публикации не видит смены источника внутри окна: вето «другого источника числа» (замечание приёмки T7.85; ADR-0029 дополнение)** (2026-10-09) — вето «другого источника числа» внутри окна публикации (замечание приёмки) → [архив](STATUS-archive.md#t785a--окно-публикации-не-видит-смены-источника-внутри-окна-вето-другого-источника-числа-замечание-приёмки-t785-adr-0029-дополнение)
- **T7.85b — окно публикации не видит сокращённое имя оценщика и глагол фиксации: второе замечание приёмки T7.85a (ADR-0029 дополнение)** (2026-10-09) — сокращённое имя оценщика и глагол фиксации в окне публикации (замечание приёмки) → [архив](STATUS-archive.md#t785b--окно-публикации-не-видит-сокращённое-имя-оценщика-и-глагол-фиксации-второе-замечание-приёмки-t785a-adr-0029-дополнение)
- **T7.85c — окно публикации включается на языке, который его вето не охраняет; латинское имя вне словаря не считалось действующим лицом (ADR-0029 дополнение)** (2026-10-09) — язык окна вне охраняемого вето; латинское имя не считалось действующим лицом → [архив](STATUS-archive.md#t785c--окно-публикации-включается-на-языке-который-его-вето-не-охраняет-латинское-имя-вне-словаря-не-считалось-действующим-лицом-adr-0029-дополнение)
- **T7.86 — Анализ: официальная цифра опубликована ≠ цифра соответствует действительности (код не менялся; ADR-0035 proposed)** (2026-10-10) — анализ: публикация официальной цифры ≠ её соответствие действительности (ADR-0035 proposed) → [архив](STATUS-archive.md#t786--анализ-официальная-цифра-опубликована--цифра-соответствует-действительности-код-не-менялся-adr-0035-proposed)
- **T7.87 — Причина оценки видна на витрине, и слабая оценка подписана «Опубликовано производителем» (ADR-0035 вариант A; коммит `e105215`)** (2026-10-10) — причина оценки на витрине и подпись «Опубликовано производителем» (ADR-0035, вариант A) → [архив](STATUS-archive.md#t787--причина-оценки-видна-на-витрине-и-слабая-оценка-подписана-опубликовано-производителем-adr-0035-вариант-a-коммит-e105215)
- **T7.88 — Подраздел карточки «Независимые оценки» (ADR-0035 вариант D; коммит `52c6a94`)** (2026-10-10) — подраздел карточки «Независимые оценки» (ADR-0035, вариант D) → [архив](STATUS-archive.md#t788--подраздел-карточки-независимые-оценки-adr-0035-вариант-d-коммит-52c6a94)
- **T7.88a — тексты подраздела «Независимые оценки» перестают утверждать то, чего не знают (ADR-0035 §12; коммиты `9ce0822`, `e2b0bc0`, `06ee41c` и завершающий уточняющий)** (2026-10-10) — честные формулировки подраздела «Независимые оценки» (ADR-0035 §12) → [архив](STATUS-archive.md#t788a--тексты-подраздела-независимые-оценки-перестают-утверждать-то-чего-не-знают-adr-0035-12-коммиты-9ce0822-e2b0bc0-06ee41c-и-завершающий-уточняющий)
- **T7.88b — «прогноз» стал свойством ЗНАЧЕНИЯ, а не формулировки (ADR-0035 §13; коммиты `15613dc` и `4c0a3e1`)** (2026-10-10) — прогноз стал свойством значения, а не формулировки (ADR-0035 §13) → [архив](STATUS-archive.md#t788b--прогноз-стал-свойством-значения-а-не-формулировки-adr-0035-13-коммиты-15613dc-и-4c0a3e1)
- **T7.89 — карточка различает «искали независимые оценки — не нашли» и «не искали» (ADR-0035 §14; коммит `7cb2f10`)** (2026-10-10) — карточка различает «искали — не нашли» и «не искали» (ADR-0035 §14) → [архив](STATUS-archive.md#t789--карточка-различает-искали-независимые-оценки--не-нашли-и-не-искали-adr-0035-14-коммит-7cb2f10)
- **T7.90 — фикстура `fake_llm` больше не подключается к чужому серверу: закрыт TOCTOU в `_free_port` (тестовая инфраструктура)** (2026-10-11) — фикстура fake LLM: закрыт TOCTOU в `_free_port` → [архив](STATUS-archive.md#t790--фикстура-fake_llm-больше-не-подключается-к-чужому-серверу-закрыт-toctou-в-_free_port-тестовая-инфраструктура)
- **T7.91 — убийство контейнера не может быть отчитано как «чистая» ошибка: закрыто окно `docker inspect` после kill** (2026-10-11) — убийство контейнера не отчитывается «чистой» ошибкой: окно `docker inspect` после kill → [архив](STATUS-archive.md#t791--убийство-контейнера-не-может-быть-отчитано-как-чистая-ошибка-закрыто-окно-docker-inspect-после-kill)
- **T7.92 — ветка `SandboxError` в брокере тоже получает ограниченный второй взгляд на состояние контейнера (решение пользователя от 2026-10-11)** — ветка `SandboxError` получает ограниченный второй взгляд на состояние контейнера → [архив](STATUS-archive.md#t792--ветка-sandboxerror-в-брокере-тоже-получает-ограниченный-второй-взгляд-на-состояние-контейнера-решение-пользователя-от-2026-10-11)
- **T7.93 — ceiling на движковый CLI ставится на весь вызов (spawn + ожидание завершения): зависший клиент больше не вешает сессию (решение пользователя от 2026-10-11)** — ceiling на движковый CLI ставится на весь вызов (spawn + ожидание) → [архив](STATUS-archive.md#t793--ceiling-на-движковый-cli-ставится-на-весь-вызов-spawn--ожидание-завершения-зависший-клиент-больше-не-вешает-сессию-решение-пользователя-от-2026-10-11)
- **T7.94 — ожидание и будущее время стали признаками прогноза у значения (дополнение ADR-0035 §13.7; решение пользователя от 2026-10-11 «запускай детектор прогнозов»)** — ожидание и будущее время — признаки прогноза у значения (ADR-0035 §13.7) → [архив](STATUS-archive.md#t794--ожидание-и-будущее-время-стали-признаками-прогноза-у-значения-дополнение-adr-0035-137-решение-пользователя-от-2026-10-11-запускай-детектор-прогнозов)
- **T7.95 — отмена движкового вызова не оставляет сироту: CLI убивается, переданное движку имя убирается, каталог сессии уходит (решение пользователя от 2026-10-11)** — отмена движкового вызова не оставляет сироту: CLI убивается, имя убирается → [архив](STATUS-archive.md#t795--отмена-движкового-вызова-не-оставляет-сироту-cli-убивается-переданное-движку-имя-убирается-каталог-сессии-уходит-решение-пользователя-от-2026-10-11)
- **T7.95a — полировка T7.95 на реальном docker 29: уборка действительна только при подтверждении движком; каталог сессии не переживает сорванный start (заключение менеджера по `800c8b5`/`d0e27c1`; код — коммит `60d7357`)** (2026-10-11) — уборка действительна только при подтверждении движком (полировка на docker 29) → [архив](STATUS-archive.md#t795a--полировка-t795-на-реальном-docker-29-уборка-действительна-только-при-подтверждении-движком-каталог-сессии-не-переживает-сорванный-start-заключение-менеджера-по-800c8b5d0e27c1-код--коммит-60d7357)
- **T7.96 — «наблюдаемая населением инфляция» и «инфляционные ожидания» перестали быть одним показателем: третий замкнутый словарь различителя (замечание с витрины стенда `.92` от 2026-10-11; код — коммит `6a7e313`)** — «наблюдаемая населением инфляция» и «инфляционные ожидания» разделены словарём → [архив](STATUS-archive.md#t796--наблюдаемая-населением-инфляция-и-инфляционные-ожидания-перестали-быть-одним-показателем-третий-замкнутый-словарь-различителя-замечание-с-витрины-стенда-92-от-2026-10-11-код--коммит-6a7e313)
- **T7.96a — «ожидания населения» и «ожидания предприятий», «годовой горизонт» и «5 лет вперёд»: ещё два замкнутых словаря различителя (замечания приёмки T7.96 со стенда `.92`; ADR-0033 §11)** (2026-10-11) — ещё два словаря различителя: ожидания групп и горизонт прогноза → [архив](STATUS-archive.md#t796a--ожидания-населения-и-ожидания-предприятий-годовой-горизонт-и-5-лет-вперёд-ещё-два-замкнутых-словаря-различителя-замечания-приёмки-t796-со-стенда-92-adr-0033-11)
- **T7.97 — собственная оценка не-официального актора доходит до карточки: explorer-v10, curator-v11, config-v21; «Смежный прогноз» только сопоставимого периода (решение пользователя от 2026-10-11 «готовь бриф T7.97 по независимым прогнозам»; код — коммит `614f515`)** — собственная оценка не-официального актора доходит до карточки (explorer-v10/curator-v11/config-v21) → [архив](STATUS-archive.md#t797--собственная-оценка-не-официального-актора-доходит-до-карточки-explorer-v10-curator-v11-config-v21-смежный-прогноз-только-сопоставимого-периода-решение-пользователя-от-2026-10-11-готовь-бриф-t797-по-независимым-прогнозам-код--коммит-614f515)
- **T7.97a — пример внутри правила не имеет права совпадать со случаем, которым это правило проверяют: вымышленные примеры explorer-v10 и curator-v11 и расширение стража T7.39/T7.50 (замечание менеджера по `6c04d92`; код — коммит `f92997c`)** (2026-10-11) — пример внутри правила заменён вымышленным случаем; расширение стража примеров → [архив](STATUS-archive.md#t797a--пример-внутри-правила-не-имеет-права-совпадать-со-случаем-которым-это-правило-проверяют-вымышленные-примеры-explorer-v10-и-curator-v11-и-расширение-стража-t739t750-замечание-менеджера-по-6c04d92-код--коммит-f92997c)
- **T7.98 — пересказ прогноза первоисточника без шаблона: relay-кандидат, снятие ложного указателя, чтение диапазонов гейтом (дефекты контрольного вопроса `c3d5cd03` со стенда `.92`; ADR-0029/0033/0035 дополнения; код — коммит `b17326f`)** (2026-10-12) — relay-кандидат пересказа прогноза без шаблона; чтение диапазонов гейтом → [архив](STATUS-archive.md#t798--пересказ-прогноза-первоисточника-без-шаблона-relay-кандидат-снятие-ложного-указателя-чтение-диапазонов-гейтом-дефекты-контрольного-вопроса-c3d5cd03-со-стенда-92-adr-002900330035-дополнения-код--коммит-b17326f)
- **T7.99 — свёртка уже записанных дублей значения на карточке ответа (решение пользователя 2026-10-12; ADR-0033 §13, ADR-0035 §17; код и тесты — коммит `3faa8e2`)** — свёртка уже записанных дублей значения на карточке ответа (ADR-0033 §13, ADR-0035 §17) → [архив](STATUS-archive.md#t799--свёртка-уже-записанных-дублей-значения-на-карточке-ответа-решение-пользователя-2026-10-12-adr-0033-13-adr-0035-17-код-и-тесты--коммит-3faa8e2)
- **T7.100 — метка «Опубликовано производителем» при единственной улике (решение пользователя 2026-10-12; ADR-0035 §18; код и тесты — коммит `928cc50`)** — бейдж «Опубликовано производителем» при единственной улике (ADR-0035 §18) → [архив](STATUS-archive.md#t7100--метка-опубликовано-производителем-при-единственной-улике-решение-пользователя-2026-10-12-adr-0035-18-код-и-тесты--коммит-928cc50)
- **T7.101 — запись решения о собственной публикации производителя в улике (решение пользователя 2026-10-12; ADR-0035 §19 и дополнение ADR-0029; код и тесты — коммит `cffe2f4`)** — запись решения о собственной публикации производителя в улике (ADR-0035 §19) → [архив](STATUS-archive.md#t7101--запись-решения-о-собственной-публикации-производителя-в-улике-решение-пользователя-2026-10-12-adr-0035-19-и-дополнение-adr-0029-код-и-тесты--коммит-cffe2f4)
- **T7.102 (2026-10-12): вымышленный пример перефразы правила 7 (curator-v12), config-v22; охота на гомоглифы** — вымышленный пример перефразы правила 7 (curator-v12/config-v22) и охота на гомоглифы → [архив](STATUS-archive.md#t7102-2026-10-12-вымышленный-пример-перефразы-правила-7-curator-v12-config-v22-охота-на-гомоглифы)
- **T7.103 (2026-10-12): правка гомоглифа в видимом интерфейсе веба (решение пользователя «с п.1»); защитный тест видимых строк** — гомоглиф в видимом интерфейсе веба исправлен + защитный тест видимых строк → [архив](STATUS-archive.md#t7103-2026-10-12-правка-гомоглифа-в-видимом-интерфейсе-веба-решение-пользователя-с-п1-защитный-тест-видимых-строк)
- **T7.104 — новая сессия пишет запись о собственной публикации производителя (решение пользователя 2026-10-12 «п.1»; ADR-0035 §20; код и тесты — коммит `a73fd3c`)** — новая сессия пишет запись о собственной публикации производителя (ADR-0035 §20) → [архив](STATUS-archive.md#t7104--новая-сессия-пишет-запись-о-собственной-публикации-производителя-решение-пользователя-2026-10-12-п1-adr-0035-20-код-и-тесты--коммит-a73fd3c)
- **T7.105 — опубликованная оценка третьей стороны как `external_fact` (curator-v13/config-v23) и пооперационный отказ на границе куратора (ADR-0034 вариант B; решение пользователя 2026-10-12 «Обе части»; коммиты `a93ebde` — часть а, `0373eaf` — часть б)** — оценка третьей стороны как `external_fact` (curator-v13/config-v23) и пооперационный отказ (ADR-0034, вариант B) → [архив](STATUS-archive.md#t7105--опубликованная-оценка-третьей-стороны-как-external_fact-curator-v13config-v23-и-пооперационный-отказ-на-границе-куратора-adr-0034-вариант-b-решение-пользователя-2026-10-12-обе-части-коммиты-a93ebde--часть-а-0373eaf--часть-б)

## T7.106 — индексы с нуля и период по названию страницы (curator-v14/config-v24) и один раунд исправления при отказе предложения целиком на границе куратора (ADR-0034 вариант C поверх B; решение пользователя 2026-10-13 «действуй по плану»; коммиты `ed1bbfd` — часть а, `9922e18` — часть б)

Симптом части (а) — стендовый случай: вопрос `3701d1ce`, сессия `c89f7aba` (.92, config-v23): куратор
посчитал `claim_index` с единицы → `evidence_link[3]: claim index 4 out of range` → отказ предложения
целиком, а период значения был подменён годом вопроса. Скобка матрицы промпта прежде обещала «отклоняется
всё предложение», что после варианта B (T7.105) неверно для проверок одной операции. Симптом части (б):
отказ целого завершает фазу куратора без единой записи — сессия с off-by-one уликами не записывала и
законные значения. Решение пользователя: вариант C **поверх** B — хост возвращает отклонённое целиком
предложение модели ровно ОДИН раз на вызов куратора (= на сессию); частичный отказ варианта B раунд
не запускает никогда, ошибки ПЕРВОГО обращения к движку — тоже.

### Часть (а): curator-v14 и config-v24 (коммит `ed1bbfd`)

- `prompts/curator/curator-v14.md` — v13 байт в байт (правила 1–12) и ровно четыре правки (тест
  `…is_exact_bytes_of_v13_with_the_four_edits`): строка версии; скобка матрицы — «движок правил
  отклоняет эту операцию; остальные операции применяются; если отклонены все операции — отклоняется
  предложение целиком» (ожидание модели приведено к варианту B); абзац после структуры ответа —
  `claim_index`/`evidence_index` считаются **с нуля** (первый claim — 0) с вымышленным JSON-примером
  «Гелиос»/«Веер» (2 claims, 3 links); блок правил 13–14: период значения — ровно тот, который назван
  на странице рядом с этим значением, год вопроса или публикации не подставляется (пример: «Гелиос»
  май 2030 к маю 2029 — 7,8 %; период на странице не назван — так и сказать; `as_of` правила 4 не
  затронуто), и правило 14 — раздел «# Исправление предложения»: вернуть ПОЛНОЕ исправленное
  предложение (индексы тоже с нуля), ничего не добавляя от себя. Файл: 282 строки, sha256
  `c582d6fd1cad9eebf51d44241586e108e88721a34cec2f115649d996c88d6eeb`.
- `docs/eval/config-v24-payload.json` = config-v23 **ровно с одной правкой** (строки 192–194) — пин
  `prompts.curator` → curator-v14 (`c582d6fd…`); canonical v24 —
  `9f0edb1436602ec0dcd90b3aba3da1c07677a47ee2ef08ee11c44a8912a44530` (хеш файла — другое число,
  `4b11d219…`); canonical v23 (`4cc1c296…`), v22 (`05ada8a4…`) и прежние payload`ы не тронуты; пин
  `prompts.explorer` не менялся (explorer-v10).
- **Дефолт bootstrap'а dev-стенда — по-прежнему config-v20; config-v24 ни на одном стенде не
  активирован** (AGENTS §6): активация config-v21/v22/v23/v24 — решение менеджера
  (`activate-online` с payload`ом v24`).
- Тесты: `tests/unit/test_curator_v14_config_v24_t7106.py` (10): v14 = v13 + четыре правки побайтно;
  правила 1–12 байт в байт v13 и две новые правила на месте; абзац нулевых индексов и пример
  (2 claims, 3 links); скобка матрицы описывает вариант B; правило периода запрещает подставной год и
  сохраняет смысл `as_of`; в новом тексте нет реальных данных; v24 = v23 ровно пином куратора
  (canonical закреплён); v23 и прежние payload`ы байтово заморожены на диске; каноническое разрешение
  пинов v24 и fail-closed на подменённом пине; скрипты dev-стенда не активируют v24. Страж
  `tests/unit/test_prompt_example_no_real_data.py` без правки перестроился сам (параметры по файлам
  промптов: случай на curator-v14.md, пара сравнения правил сдвинулась на v14→v13).
- Прошлые промпты (`curator-v10…v13`) и payload`ы v20…v23 — без изменений.
- **Краснота части (а) на 5ce9365** (тот же временный worktree): 8 из 10 тестов краснеют — там нет
  curator-v14 и config-v24.

### Часть (б): ADR-0034 §9, вариант C поверх B (коммит `9922e18`)

Гейты отказа целого возвращают исход в чистую границу `_curator_boundary` (помощник
`apps/orchestrator/orchestrator.py:2923`; оба исхода — до staging, только чтения): `BoundaryAccepted`
(staged, dropped-кортежи варианта B, значение дублей T7.83/ADR-0033, опорные идентификаторы) или
`BoundaryRejected` (payload + публичный summary + причины дословно) — `apps/orchestrator/curator_correction.py`
(193 строки). `_curator` пишет отказ как раньше, но если отказ случился ПОСЛЕ модельного ответа —
сначала исполняется ровно один раунд исправления.

| Что | Где в коде (orchestrator.py) |
|---|---|
| гейт 1 — структурная валидация (`curator_rejected` + `rejected_proposal`) | :2961–2974 |
| гейт 2 — снятие перепроверкой всех операций (`curator_reject_kind: "reverify_unresolved"`) | :3044–3050 |
| гейт 3 — обязательный `as_of` новой `temporal_fact` (staging-гейт) | :3235–3241 |
| гейт 4 — движок правил (`curator_rejected_by_rules`) | :3277–3281 |
| маркер раунда: SESSION_STATE_CHANGED с ключом `curator_correction_requested` (причины + дайджест ПЕРВОГО ответа), summary «curator proposal returned for one correction round» | :2678–2688 |
| второй запрос: тот же system, к user дописан раздел «# Исправление предложения» (`_curator_chat`) | :2694–2701, помощник :3302 |
| сбой ВТОРОГО обращения: отказ по первому payload + аддитивный `correction_error` (три kind) | :2714–2726, :2736, :2764 |
| LeaseLost во время раунда: SESSION_FAILED + raise | :2775–2783 |
| второй ответ отклонён повторно: его payload + аддитивный `correction_round` (причины и дайджест первого) | :2795 |
| принят после раунда: `claim_created` с аддитивным `correction_round` | :2905–2906 |

- Раздел исправления (`curator_correction.py::build_correction_section`): причины дословно как в payload
  отказа, заголовок «Claims отклонённого предложения (всего N), индексы с нуля», JSON отклонённого
  предложения, потолок `CORRECTION_SECTION_MAX_CHARS = 6000`, заглушки claim'ов не длиннее
  `CORRECTION_CLAIM_SNIPPET_CHARS = 120` знаков; резка целыми записями, пропущенное количество
  называется (маркер «…ещё N причин не показано из-за ограничения длины», ключи `omitted_claims` /
  `omitted_evidence_links` в JSON). Дайджест отказанного предложения — прежний
  (`rejected_proposal_digest`, `apps/orchestrator/rejected_proposal.py`, T7.84), переиспользован без
  изменений.
- Сбой ВТОРОГО обращения: обрыв ответа — отказ по первому payload + `correction_error {kind:
  "truncated_output", error ≤300 знаков, truncated_attempts}` (:2714–2726); невалидируемый ответ —
  `kind: "request_rejected"` (:2736); недоступный движок — `kind: "unavailable"` (:2764);
  `correction_round` при этом отсутствует (раунд не состоялся). LeaseLost во время раунда —
  SESSION_FAILED + raise (:2775–2783). Второй ответ отклонён повторно — отказ по ВТОРОМУ payload +
  аддитивный `correction_round` (причины и дайджест ПЕРВОГО) (:2795), возврат `0, 0` прежний. Принят
  после раунда — обычный staging и `claim_created` с аддитивным `correction_round` (:2905–2906).
  Третьего обращения нет никогда. Ошибки ПЕРВОГО обращения (`:2575/:2606/:2627/:2653`) — байт в байт
  прежние, раунд не запускают. Шаговый бюджет исследователя (`max_explorer_steps`) раунд не расходует
  (ADR-0034 §9 п.5 — закреплено текстом ADR и местом кода, отдельного теста нет).
- Байт-совместимость прежних отказных payload сохранена: ни одна прежняя строка не изменена, ключи
  только аддитивные; фильтры `payload ? 'curator_rejected'` видят ровно один отказ за сессию
  (маркер раунда — не отказ). `packages/domain/services/commit.py`, миграции, `AuditEventType`,
  пороги и бюджеты не тронуты; оркестратор изменён только внутри `_curator` и двумя новыми помощниками
  (`_curator_boundary`, `_curator_chat`).
- Реплей симптома: `tests/scenario/test_curator_correction_round_t7106.py::test_fixture_off_by_one_link_replay_gets_one_correction_round`
  — четыре улики со сдвигом на одну отклонены с причиной `evidence_link[3]: claim index 4 out of range`,
  один раунд, исправление с нулевых индексов (8 связей) принято: один `claim_created` с
  `correction_round`, ноль отказов, ровно одно событие «curator proposal returned for one correction
  round».
- Новые тесты (18): сценарии раунда (11: гейты 2–4 тоже получают раунд и исправленная операция
  приземляется; второй идентичный ответ → финальный отказ с `correction_round` без записей; сбои
  второго обращения трёх видов с `correction_error`; частичный отказ B — без раунда; первый
  ответ принят — ни одного ключевого маркера и один запрос; отказ ПЕРВОГО обращения — без раунда),
  unit раздела исправления (7: маркеры, детерминизм, потолки, целые причины, дайджест, отсутствие
  чужого текста, контракты границ). Переписанные закреплённые тесты (6 файлов): к каждому whole-отказу
  добавлен скрипт ВТОРОГО ответа (обычно идентичного отклонённому — легальный сценарий «модель не
  исправила») и проверки `correction_round` / одного отказа / одной фиксации / `queue_left == 0`
  (третьего обращения нет — по неизрасходованной канареечной записи у сбоев); partial-тесты варианта B
  снабжены no-round-пинами (`test_commit_boundary.py`, `test_curator_rejection_journal.py`,
  `test_reverify_curator.py`, `test_dateless_curator_reverify_order.py`, `test_research_provenance.py`,
  `test_curator_per_operation_rejection_t7105.py`). Прежние whole-сценарии на старом коде зелёнели
  «случайно», упираясь в пустую очередь фейка (ловушка закреплена в AGENTS §7).
- **Краснота на 5ce9365** (worktree + перенос новых и изменённых тестов): **19 FAILED + 1 ошибка
  коллекции (нет модуля `curator_correction`) + 16 passed** — по файлам падения: correction-round 8,
  `test_curator_per_operation_rejection_t7105.py` 3 (whole), `test_curator_rejection_journal.py` 3,
  `test_commit_boundary.py` 2 (whole), `test_reverify_curator.py` 1,
  `test_dateless_curator_reverify_order.py` 1, `test_research_provenance.py` 1; partial/no-round-пины
  зелёны и на старом коде по замыслу.

Полная проверка (§6) перед каждым из трёх коммитов зелёная: ruff — All checks passed; mypy strict
на `packages apps hostctl` — Success: no issues found (154 файла до части б, 155 — с новым модулем);
pytest `-n auto -m "not timing"` — **2451 passed, 12 skipped** перед частью а (собранных 2463 против
2450 у T7.105) и **2469 passed, 12 skipped** перед частями б и в (собранных 2481: +18 — 11 сценариев
и 7 unit); `-m timing` — **4 passed**. Контейнеров **41** до и после, `noezema_mig_*` **40**,
`noezema_tpl_*` **0** (замеры — вне прогонов pytest).

## T7.106a (2026-10-13): раздел исправления не теряет claims; при переполнении раунда нет (дефект, найденный менеджером на приёмке части б T7.106; правило менеджера, записано в ADR-0034 §9 пп. 3–4; код и тесты — коммит `b922557`)

**Дефект.** В `build_correction_section` (`apps/orchestrator/curator_correction.py`) при переполнении
JSON отклонённого предложения резался хвостами с ключами `omitted_claims`/`omitted_evidence_links`,
а перечень `[i]` строился только по оставленным claims: выброшенные claims исчезали из обеих частей
раздела одновременно. Второй вызов — новый запрос (первый ответ модель не видит), и по curator-v14
правилу 14 модель обязана вернуть ПОЛНОЕ предложение: она не может вернуть то, чего ей не показывали.
Пробы на 43552aa: 30 claims по ~890 знаков с одной причиной отказа — раздел 5577 знаков, заголовок
«всего 30», в перечне 4 строки, в JSON 4 claims, `omitted_claims: 26`; 30 claims по 1992 знака плюс
14 длинных причин — 4919 знаков, 1 строка перечня, 1 claim в JSON; реалистичный случай 8 claims
(scope 5–6 ключей, search_statements, as_of) — 3 строки `[i]` вместо 8 и `omitted_claims: 1`.

**Правило (записано в ADR-0034 §9 пп. 3–4).**
1. Раздел никогда не теряет операции и связи: JSON отклонённого предложения ПОЛНЫЙ
   (`proposal.model_dump(mode="json")`, без резки и без omitted-счётчиков), перечень `[i]` — по ВСЕМ
   claims (фрагменты не длиннее `CORRECTION_CLAIM_SNIPPET_CHARS = 120`, как прежде). Целыми записями
   с маркером режутся ТОЛЬКО причины хоста (построитель: `curator_correction.py:129–156`).
2. Потолок `CORRECTION_SECTION_MAX_CHARS = 24_000` знаков (`curator_correction.py:48`, прежний 6000).
   Обоснование от бюджета входа куратора (§5.4.1): `input_budget` config-v24 = min(131072, 131072) −
   8192 − 2048 = 120832 (`packages/cognition/tokenizer.py:48–50`, model-блок `docs/eval/config-v24-payload.json`);
   остальной вход куратора сверху ограничен: суммы token_budget'ов секций контекст-пака 26624
   (`tokenizer.py:62–67`), промпт curator-v14 ≈ 3418 токенов, блок Evidence при потолке
   `max_evidence_items_per_session` 64 × `SOURCE_ASSERTION_MAX_WINDOWS` 2 × `SOURCE_ASSERTION_TEXT_BUDGET`
   2000 знаков (`apps/orchestrator/evidence.py:73,83`) ≈ 463723 знаков ≈ 45568 токенов оценки;
   оценка токенов никогда не превышает числа знаков (`tokenizer.py:28–32`), значит раздел добавляет
   ≤ 24000 токенов: худшая сумма ≈ 99610 < 120832 (запас ≈ 21222) — потолок не выводит запрос куратора
   за бюджет входа, STOP не требуется. Промпт-сторона (curator-v14 правило 14) и config-v24 не изменены:
   полное возвращение предложения промпт уже предписывает.
3. Если полный раздел всё равно не помещается — раунда НЕТ: построитель возвращает
   `CorrectionOverLimit(chars, limit)` (чистая функция, детерминированно, без БД и LLM — длина известна
   до обращения к модели), `_curator` пишет отказ ПЕРВОГО ответа ЦЕЛИКОМ прежними ключами плюс
   аддитивный `correction_skipped {reason: "section_over_limit", chars, limit}`
   (`orchestrator.py:2687–2705`); второго обращения нет, события `curator_correction_requested` нет
   (ветка пропуска предшествует записи события раунда :2706–2716). Журнальный дайджест
   `rejected_proposal_digest` (потолок 4096, свои omitted-счётчики) — отдельная запись журнала,
   модель её не видит; не изменён.

**Длины разделов на новых фикстурах.** Стендовый случай 3701d1ce (4 claims): 2715 знаков — без резки
(влезал и в прежний потолок); test-фикстура 8 claims: 6963 знаков / 1941 токенов оценки — без резки;
12 claims × ~2000 знаков + 40 связей: 44052 знаков — переполнение; сценарий т8 (30 claims × ~1941):
67301 знаков — переполнение, `chars` в payload отказа сверен тестом с этими числами.

**Тесты (красные на 43552aa, без LLM).** Unit (`tests/unit/test_curator_correction_section_t7106.py`,
7 → 8): новый `test_realistic_eight_claim_proposal_keeps_full_json_and_all_list_lines` (полный JSON
совпадает с `model_dump(mode="json")`, все строки `[0]..[7]`, нет `omitted_*`); переписанный закреплявший
резку тест `test_hostile_proposal_stays_under_the_cap_and_parses` →
`test_full_hostile_section_over_cap_returns_over_limit_not_trimmed_text` (возврат `CorrectionOverLimit`,
`limit == 24_000` закреплён, `chars` ограничен снизу полным JSON и сверху правдоподобной оценкой,
детерминизм); тест целострочной резки причин масштабирован до 120 причин (10 причин при новом потолке
уже не режутся); тест NUL/чужого текста переведён на вмещающуюся фикстуру (прежняя hostile-фикстура
теперь переполняет). Сценарий (`tests/scenario/test_curator_correction_round_t7106.py`, 11 → 12):
`test_section_over_cap_skips_the_round_with_correction_skipped` — ровно один вызов куратора, канарейка
не израсходована, нет ни `curator_correction_requested`, ни `correction_round`, ни `correction_error`,
ровно один отказ прежними ключами плюс `correction_skipped` (chars = длина полного раздела того же
построителя, limit = 24000), summary сохраняет прежнюю голову отказа, записей знания нет. Краснота на
43552aa (временный worktree + копии изменённых тестов): unit-файл и сценарный файл — ImportError
(`CorrectionOverLimit` там нет); семантическая проба на тех же реалистичных фикстурах — 3 строки `[i]`
вместо 8 и `omitted_claims: 1`.

Полная проверка (§6) перед обоими коммитами зелёная: ruff — All checks passed; mypy strict на
`packages apps hostctl` — Success: no issues found (155 файлов); pytest `-n auto -m "not timing"` —
**2471 passed, 12 skipped** (собрано 2487: +2 к T7.106 — один unit и один сценарий); `-m timing` —
**4 passed**. Контейнеров **41** до и после, `noezema_mig_*` **40**, `noezema_tpl_*` **0**.

## T7.107 (2026-10-13): плашка площадки не даёт `own_assessment`; третий pairing-режим `bibliography_known_producer` (дефект, найденный менеджером на стенде .92; правка по указанию менеджера — часть а; ADR-0029 дополнение; код и тесты — коммит `a3bbdfc`)

1. **Дефект и замер до правки** (замер менеджера на `.87`, HEAD `bb1123d`, сохранённая фикстура
   страницы `smart-lab.ru/blog/1349924.php` и дамп улики утверждения 7cfbe942 «уровень безработицы
   2,2% по данным Росстата»): страничный детектор и детектор значений давали `own_assessment`,
   сработавший маркер вето — «не совпадать» из шаблонной плашки площадки «Данная публикация является
   личным мнением автора. Мнение владельца сайта может не совпадать с мнением автора.» (позиция
   ≈9021 нормализованного текста, ниже тела поста). Вето вызвано оформлением площадки, а не оценкой
   автора. Улика того же утверждения `interfax.ru` уже несла запись `value_attribution`
   (primary_key rosstat, pairing строгого режима, якорь-родитель ae1ad0a5 — страница opendata Росстата),
   но блог оставался отдельной независимой группой и давал утверждению «E3 · независимое подтверждение ·
   Проверено» — вопреки принципу пользователя (пересказ ≠ независимое измерение).

2. **Маска плашки (правка 1).** Вето собственной оценки на обоих уровнях читает копию скан-текста,
   из которой сохранением длины вырезаны фразы закрытого списка `SITE_DISCLAIMER_PHRASES`
   (`apps/research_proxy/source_attribution.py:298–326`, компиляция :327, функция маски
   `_mask_site_disclaimers` :330–345; страничное вето :623, поуровневое вето —
   `value_attribution.py:897`). Закрытый список — три наблюдаемые формы (плюс зеркальная):
   «…публикация является личным/частным мнением автора», «мнение владельца сайта/редакции/администрации
   может не совпадать с мнением автора» и зеркало. Не перечисленный вариант («может не совпадать с
   позицией редакции») остаётся вето: список расширяется только по наблюдаемому случаю, решение
   менеджера. Инварианты: словарь `OWN_ASSESSMENT_MARKERS` НЕ сокращён (22 записи — тест-замок
   unit-файла T7.107; сам маркер «не совпадать» жив); настоящая оценка («По нашим расчётам…»,
   «…не совпадает с данными Росстата») ветоется по-прежнему — закреплено тестами с плашкой и без неё;
   пары, окно публикации и фрагменты-основания строятся по незамазанному тексту; хранимый текст
   артефакта и его хеш не меняются (маска живёт только на копии для вето).

3. **Третий pairing-режим (правка 2).** Новое значение закрытого множества pairing
   `VALUE_ATTRIBUTION_PAIRING_BIBLIOGRAPHY` (`packages/memory/scope.py:460`, членство :461–466);
   ключ скоупа, схема записи `host-value-attribution-v1` и миграции не тронуты; legacy-запись без
   pairing читается как строгая. Условия (полный перечень с обоснованием —
   `apps/research_proxy/value_attribution.py:634–660`): (i) на странице есть строка-список,
   НАЧИНАЮЩАЯСЯ с закрытого маркера `BIBLIOGRAPHY_LINE_MARKERS` («Источники:», «Источники данных:»,
   «По материалам:», «source:», «sources:» — :663–670; «Источники сообщили» без двоеточия — не
   маркер); (ii) алиас производителя из словаря `PRIMARY_SOURCES` стоит в этой же строке, а значение
   утверждения измерено на странице (`_measured_numbers`); (iii) у ЭТОГО ЖЕ утверждения есть ДРУГАЯ
   улика, записанная pairing строгого режима или режима публикации с `primary_key` = X (множество
   ключей передаёт вызывающий, функция остаётся чистой); (iv) ровно один известный производитель
   назван → `derivative`; два и более различных → `ambiguous_primaries` (НЕ записывается); ни одного
   названного → честный отказ `no_value_attribution` (не `own_assessment`); (v) вето собственной оценки
   (после маски) не сработало, и страница не домашний хост X. Реализация:
   `_bibliography_known_producer_decision` :682–735, строки-списки `_bibliography_lines` :677, разбор
   строки `_BIBLIOGRAPHY_LINE` :671; режим рассматривается ТОЛЬКО после честного отказа строгого режима
   и окна публикации (`fallback()` :903–930) — прежние режимы не ослаблены ни на пункт; `page_text`
   (весь текст страницы) расширяет только третий режим. Запись — той же формой, что у строгого режима
   (тот же каскад `parent_source_id` §11.3 и та же подстановка родителя в
   `source_graph._value_attribution_parents`) — `independence.py`, пороги и оценки не тронуты;
   группировка сворачивает пересказ в группу якоря первоисточника (тест: группы утверждения 2,2% —
   одна после записи, было бы две).

4. **Гвард цепочек.** Сборщики ключей читают pairing только строгого режима и режима публикации;
   запись нового режима ключом не кормится: `apps/research_proxy/reattribution.py::_known_claim_primaries`
   :676–720 (SQL с `COALESCE(…pairing, строгий) IN (строгий, публикация)` и исключением самой улики —
   опираться можно только на ДРУГУЮ улику) и накопитель оркестратора
   (`apps/orchestrator/orchestrator.py::_known_claim_primaries` :2474, `known_primaries_by_claim`
   :2917–2965; в накопитель попадают только записи строгого/публикационного pairing, :2949–2958).
   Посаженная тестом «ядовитая» INSERT-запись нового pairing чужого источника не попала в множество
   ключей (оба сборщика закреплены отдельными проверками сценарного теста).

5. **Как переатрибуция видит смену версии.** Метки подняты: `ATTRIBUTION_METHOD_VERSION` →
   `host-source-attribution-v5`, `VALUE_ATTRIBUTION_METHOD_VERSION` → `host-value-attribution-v7`
   (`source_attribution.py:60`, `value_attribution.py:143`); маркер формы записи не менялся.
   Перечитывание окна сравнивает запись `scope.value_attribution.method` с текущей меткой (гейт
   применения `COALESCE(…, '') <> :method`, `reattribution.py:1126`/`:1169`) — записи эпохи v6
   попадают на пересмотр, решения v7 не перезаписываются (идемпотентность: второй прогон — «изменений
   нет: решение уже записано ранее»). Вызов детектора при планировании — :817–830 (ключи собираются
   из БД, пишет только применение). Docstring переатрибуции исправлен (остаток T7.104 закрыт,
   ADR-0035 §20.7).

6. **Вызывающие.** Переатрибуция `--evidence-level` — полный цикл (сценарный тест A: dry-run → apply
   changed=2 строгими решениями эпохи v7 с перечитанных окон → второй прогон догоняет запись нового
   pairing странице пересказа (changed=1; pairing bibliography, method v7, parent = тот же якорь ae1ad0a5,
   basis содержит «Источники: Росстат»), третий прогон — идемпотентно 0). Оркестратор новой сессии
   (сценарный тест B): interfax-улика пишет строгий pairing, smart-lab-улика — сразу
   `bibliography_known_producer` (тот же parent), группы = 1, последующий dry-run переатрибуции —
   «решение уже записано ранее». Честный gap цикла оркестратора (комментарий :2908–2916): связка
   работает, когда улика с записью уже прочитана (утверждение-якорь из БД через `_known_claim_primaries`)
   или обработана РАНЬШЕ в том же предложении (накопитель); если в одном предложении улика со строгой
   записью идёт ПОЗЖЕ улики-пересказа — та останется в честном отказе, пока переатрибуция `--evidence-level` не
   догонит запись (цикл предложения не перестроен — правка в разрешённых границах). Обхода нет:
   staging-записи цикла не видны в БД до границы коммита.

7. **Рейплей настоящих фикстур (до → после).** Страница целиком: `own_assessment` →
   `no_value_attribution` (маска сняла вето, но пары «значение+первоисточник» на странице нет — ложной
   склейки не появилось). Утверждение 2,2%: `own_assessment` → при `known={}` честный
   `no_value_attribution`; при `known={rosstat}` (ровно состояние стенда: interfax-запись) —
   `derivative`, primary rosstat, pairing bibliography, basis «Источники: Росстат, обзор рынка труда
   hh.ru…», значения ('2.2',). Утверждения hh.ru (вакансии −13%, резюме +22%): до — `own_assessment`
   (тоже из-за плашки); после — `no_value_attribution` (известных производителей для этих утверждений
   нет; hh.ru и HeadHunter вне словаря `PRIMARY_SOURCES` — ложной склейки с Росстатом нет; при
   гипотетической строгой записи того же утверждения режим склеил бы по правилам режима). Итого на
   стенде после операторской переатрибуции: блог становится пересказом Росстата в его группе,
   E3-независимость с него снимается; hh-значения остаются без атрибуции до части (б).

8. **Тесты.** Новые: `tests/unit/test_bibliography_known_producer_t7107.py` (17: маска и три формы
   плашки, настоящая оценка остаётся vetoed с плашкой и без, неперечисленный вариант вето, замок
   словаря 22 маркеров и побайтовый пин закрытых списков, все условия и отказы третьего режима, пин
   версий v5/v7, roundtrip записи через `parse_value_attribution`);
   `tests/scenario/test_reattribute_bibliography_t7107.py` (стенд в миниатюре: прогоны — строгие
   записи, догоняющая запись нового pairing, тот же якорь, сворачивание групп, чужие утверждения
   страницы не тронуты, hh-группа не тронута, `value_self_publication` не заводится, идемпотентность,
   гвард цепочек посадкой, сборщики обоих вызывающих);
   `tests/scenario/test_bibliography_session_t7107.py` (живая сессия: оба pairing сразу). Краснота на
   `bb1123d` (временный detached worktree, удалён после проверки): unit — ImportError (нет
   `SITE_DISCLAIMER_PHRASES`); reattribute-файл :316 и session-файл :174 — ожидался метод v7, а старый
   код пишет v6; проба old-детектора на фикстуре — страница и утверждение `own_assessment`, у нового
   API третьего режима на старом коде нет (TypeError на `known_claim_primaries`). Прежние закреплённые
   прогоны обновлены поднятыми метками: 15 файлов (v4→v5 и v6→v7);
   `tests/scenario/test_self_publication_labels_t7101.py` запечатан эпохой v6 подменой констант
   переатрибуции (прецедент `test_reattribute_stale_records`): стенд 20 утверждений T7.101 обязан
   оставаться таблицей прежних решений и под v7.

9. **Что НЕ закрыто этой правкой.** Часть (б) — улики без значения на своих страницах (rosstat-улики
   7cfbe942 без пары «значение+алиас»): отдельная часть задачи. Стенд `.92` не трогали: активация и
   переатрибуция реальных баз — действия менеджера; без запуска `--evidence-level` прежние страницы
   эпохи v4/v6 сохраняют старые записи (пересмотр начинается только с новой меткой). Prompt/config,
   пороги, шкала оценок, `AuditEventType`, ключи скоупа и миграции не менялись вообще.

10. **Проверка (§6) перед коммитом кода:** ruff — All checks passed; mypy strict на `packages apps
    hostctl` — Success: no issues found (155 файлов); pytest `-n auto -m "not timing"` — **2490 passed,
    12 skipped** (собрано 2502: +19 к T7.106a — 17 unit и 2 сценария); `-m timing` — **4 passed**.
    Контейнеров **41** до и после правки (чужих не создавалось и не удалялось), `noezema_mig_*` **40**,
    `noezema_tpl_*` **0**.

## T7.108 (2026-10-13): гейт «значение на связи» на границе куратора (часть а) и pairing без зависимости от порядка `evidence_links` (часть б) — продолжение T7.107; часть в — curator-v15/config-v25 (решение пользователя 2026-10-13 «да, делай»; ADR-0034 §10 и дополнение ADR-0029; код и тесты — коммиты `4f1d28d` (части а+б) и `f4bca01` (часть в))

1. **Что закрывает часть (а)** (прямое продолжение п.9 T7.107: «улики без значения на своих
   страницах — отдельная часть задачи»): стендовая связка 7cfbe942 несла rosstat-улики, на
   страницах которых измеренного значения утверждения (2,2 %) не было вовсе — только размеры
   файлов вложений («52.22 Кб», «12,2 Мб») и таблицы с другими величинами; такие связи
   раздували независимость до E3. Хостовой гейт на границе куратора (ADR-0034 §10):
   `supports`-связь к улике `source_assertion` остаётся только если измеренное значение
   claim'а встречается целым токеном либо во фрагменте улики, либо во всём сохранённом тексте
   страницы. Проверка — после резолюции перепроверки и до гейта дублей T7.83 и правил:
   остальные проверки видят уже отфильтрованный набор связей. Движок правил, пороги, оценки,
   счётчики, `AuditEventType`, миграции и commit boundary — не тронуты; `counters`-связи гейт
   не трогает никогда; claim без измеренного значения не проверен вовсе (молчание прежнее).

2. **Реализация части (а).** `apps/orchestrator/curator_rejections.py`: чистые
   `links_without_claim_value` (:165 — снимает связь, если ни одно значение claim'а не найдено
   ни во фрагменте, ни на странице; причина пинится байтово
   `evidence_link[<k>]: evidence <e> does not contain the value of claim <i>`) и
   `rejected_link_entries` (:216 — записи снятых связей для ленты). Matching значений — новая
   чистая функция `claim_values_found_in_texts` (`apps/research_proxy/value_attribution.py:445`,
   нормализация + потолок `MAX_SCAN_CHARS` = 40 000, целая токенизация `VALUE_NUMBER_PATTERN`,
   каноническое равенство «2,2» ≡ «2.20», any-of по набору значений claim'а). Гейт в потоке
   `_curator_boundary` (`apps/orchestrator/orchestrator.py:3268–3296`): снятые связи не
   переносятся другим claim'ам; claim, у которого не осталось ни одной supports-связи со
   значением, снимается механизмом T7.105 (`rejected_operations`, причина
   `claim[<i>]: no linked evidence contains the claim value`); снятая связь оставшейся
   операции называется в `rejected_links` payload события `claim_created` (связи снятых
   операций описаны их записями операций и не дублируются); когда сняты ВСЕ операции — отказ
   предложения целиком с `curator_reject_kind: evidence_value_absent`, всеми снятыми связями в
   payload и ровно одним раундом исправления ADR-0034 §9 (вариант C). В staging снятые связи не
   пишутся (`BoundaryAccepted.removed_link_indices`, `apps/orchestrator/curator_correction.py:3555–3558`).

3. **Промпт-сторона (часть в).** curator-v15 = curator-v14 байт в байт, кроме `version:` и
   одного дополнения к правилу 9 (правила 1–8 и 10–14 — без изменений, ADR-0019): абзац гейта
   «значение на связи» с вымышленным примером той же формы («Сервис „Гелиос“ (Тармения) …
   7,8 %»; страницы со «52.22 Кб»/«12,2 Мб» значения не несут) и названными границами
   (counters — вне проверки; claim без значения — не проверен). Страж
   `tests/unit/test_prompt_example_no_real_data.py` применяет к v15 правила T7.97a/T7.102 без
   исключений: новый текст не называет акторов витрины, не воспроизводит «значение + период»
   реального случая, `INHERITED_REAL_DATA` для v15 пуст. config-v25 = config-v24 ровно с одной
   правкой — пин `prompts.curator` → curator-v15 (sha256 `d229c3235424e98ac423aa400575fd646c84a9cea8f605e0b8e7f0513464920b`,
   canonical payload `b86bd7b09517db4581a4f544759dd46ccd5efb4705fe362378b7915e76ad7a9a`, хеш файла
   `e3aa508e3ba2b45729419a3304c4d9c71827c2588ccb448cc8cd46612852e932`); закреплено
   `tests/unit/test_curator_v15_config_v25_t7108.py` (9 тестов: побайтовые EDITS v14→v15,
   неизменность правил, canonical/файловые хеши, resolve+tamper fail-closed, dev-стенд не
   активирует v25). Дефолт bootstrap'а — config-v20; активация config-v25 — решение менеджера.

4. **Часть (б): закрытие честного gap'а T7.107 (п.6 того раздела).** Третий pairing-режим
   кормился ключами «уже известный производитель», которые staging собирал ПО МЕРЕ прохода по
   `evidence_links`: если строгая связь шла в списке ПОЗЖЕ пересказа, блог оставался в честном
   отказе `no_value_attribution` до переатрибуции `--evidence-level` — результат зависел от
   порядка строк ответа модели. Staging связей значения перестроен в два прохода
   (`apps/orchestrator/orchestrator.py:2973–3040`): проход 1 — чистый детектор
   `_value_attribution_decision` (:2515) без ключей, решения в таблицу; накопитель — вынесенная
   чистая функция `known_primary_key_from_decision`
   (`apps/orchestrator/attribution_keys.py:23`) добавляет ключ только из строгого
   pairing/pairing публикации; проход 2 пересчитывает только честные отказы
   (`no_value_attribution` / `value_not_attributed`) со всеми ключами прохода 1 плюс якорь БД
   (`_known_claim_primaries` — только для claim'ов с `existing_claim_id`). Запись режима
   bibliography ключом не кормится (прежний гвард цепочек), и теперь цепочка «блог от
   bibliography» невозможна по самой конструкции проходов. Гейт части (а) стоит раньше: снятые
   связи выпадают из обоих проходов (:2977, :3000). Прежние решения, уже несущие атрибуцию, не
   пересчитываются — для ответов со «строгая связь раньше пересказа» staging побайтно прежний
   (сентинел `test_bibliography_session_t7107.py` зелёный без правок). Версии методов (v5/v7),
   схема записи и закрытое множество pairing не тронуты. Побочный эффект порядок-независимости
   консервативен: больший набор ключей может включить вето `ambiguous_primaries` (два известных
   производителя — не записывается).

5. **Краснота до кода** (detached worktree `/tmp/wt7108` на `40bdad4` с новыми файлами тестов,
   удалён после проверки): unit-файлы — 3 ImportError (`links_without_claim_value`,
   `claim_values_found_in_texts`, `known_primary_key_from_decision`); новые scenario-файлы —
   **7 падений, 3 прохождения** (прошли только сентинелы прежнего поведения: значение вне
   фрагмента но на странице — связь жива; claim без значения не проверен; цепочка невозможна;
   падение `test_bibliography_pairing_survives_when_strict_link_comes_last` зафиксировало сам
   gap части (б) — блог `None`). Полный прогон после правок продукта добавил 6 красных
   прежних тестов (гейт задел фикстуры, где страница не несла значение утверждения):
   `test_curator_correction_round_t7106.py` ×2, `test_dateless_curator_reverify_order.py` ×2,
   `test_reverify_temporal_scenario.py` ×1, `test_scope_coverage.py` ×1 — все приведены к зелёному
   без изменения предмета (п.6).

6. **«Было → стало» в прежних тестах** (обоснование правок): (и)
   `test_curator_correction_round_t7106.py::test_fixture_off_by_one_link_replay_gets_one_correction_round`:
   было — стендовый исправленный ответ принимался целиком (4 claims, `claims_applied` не
   появляется); стало — 2 claims приняты, 2 операции сняты по имени
   (`claim[1]/claim[3]: no linked evidence contains the claim value`), связь июльской страницы
   у августовского claim'а названа в `rejected_links` — раунд исправления по-прежнему ровно один
   и запускается прежней структурной причиной (предмет теста не тронут). (к) тот же файл,
   gate4-тест: утверждение переформулировано без измеренного значения (`около 2 процентов` →
   `на уровне последних месяцев`) — иначе тест ловил бы отказ гейта вместо предмета
   (несоответствие `claim_type` правилам); гейт на пустом значении молчит. (л) zeta-страница
   `test_dateless_curator_reverify_order.py` получила значение NEW_RATE целым токеном
   («21,5 процента»), gamma-страница `test_reverify_temporal_scenario.py` — точное 5.59 вместо
   «near 5.6» («near 5.6» не несёт 5,59 и связь снялась бы гейтом); страницы названных
   источников в `test_scope_coverage.py::..._reaches_E3` подменяются на время теста на несущие
   14,00 % (базовые страницы `test_research_evidence` не тронуты). Предметы всех четырёх тестов
   (порядок проверок as_of, молчание гейта дублей, сохранение E3 при перепроверке, эталон
   относительной даты) закреплены прежними утверждениями байт в байт.

7. **Тесты.** Новые: unit — `test_curator_link_value_gate_t7108.py` (чистые функции снятия
   связей и записей), `test_link_value_helper_t7108.py` (токенизация/каноника/any-of/quirk
   склейки «12 2,2»→122.2/бюджет), `test_attribution_key_collector_t7108.py` (ключ только из
   строгих/публикационных решений), `test_curator_v15_config_v25_t7108.py` (9); scenario —
   `test_link_value_evidence_gate_t7108.py` (7: снятие по размерам файлов; значение на странице
   вне фрагмента; claim без значения; counters выживают; снятие операции по имени; полный отказ
   → один раунд; атрибуция считается по отфильтрованным связям), `test_bibliography_pairing_order_t7108.py`
   (3: склейка выживает при поздней строгой связи; два пересказа склеиваются с поздним строгим
   источником; цепочка невозможна). Итого +46 тестов к собранному набору. Обновлённые прежние
   файлы — п.5/п.6.

8. **Что НЕ закрыто и пределы.** Совпадение any-of: страница с чужим числом того же номинала
   сохраняет связь (гейт констатирует только отсутствие значения вовсе — ложное сохранение
   дешевле ложного снятия, ложная склейка дороже ложного пропуска — тот же принцип ADR-0029);
   склейка разделителей («12 2,2» читается одним числом 122.2) закреплена тестом как осознанный
   quirk; бюджет скана 40 000 символов общий с атрибуционным. Стенд `.92` не тронут: активация
   config-v25 и перезапуск сессий — действия менеджера; прежние сессии без новой конфигурации
   ведут себя как раньше (гейт живёт в коде хоста и действует при любой конфигурации — как
   гейт дублей T7.83). Пороги, шкала оценок, `AuditEventType`, миграции, версии методов
   v5/v7, ключи скоупа не менялись.

9. **Проверка (§6) перед коммитами:** ruff — All checks passed; mypy strict на `packages apps
    hostctl` — Success: no issues found (156 файлов, +1 модуль `attribution_keys.py`); pytest
    `-n auto -m "not timing"` — **2539 passed, 12 skipped** (собрано 2551: +49 к T7.107 —
    46 новых тестов (36 unit и 10 scenario) и +3 параметризованных примера стража структуры
    примеров промптов, появившиеся с парой curator-v15/v14); `-m timing` — **4 passed**. Контейнеров **41** до и после правки, `noezema_mig_*` **40**,
    `noezema_tpl_*` **0** (замеры — вне прогонов pytest). Красный прогон до правки продукта
    зафиксирован п.5 (worktree на `40bdad4`). Зелёный прогон общего дерева — цифры выше;
    снаряд коммита `4f1d28d` отдельно проверен в detached worktree (без prompt/config-файлов
    части в, они в следующем коммите): **2527 passed, 12 skipped** — ровно на 9+3 меньше
    общего числа (тесты config-v25 и его параметризации стража ещё не в снаряде).

## T7.108a (2026-10-13): правка границ сравнения гейта «значение на связи» (дефект найден на приёмке)

1. **Дефект (проба менеджера, не push).** Независимый §6-разбор менеджера на `beda40a`
   (зелёный: 2539 passed + 12 skipped; 4 timing) вскрыл дыру в формулировке гейта ADR-0034 §10:
   `claim_values_found_in_texts` (`apps/research_proxy/value_attribution.py`) искал значение
   максимально жадным `VALUE_NUMBER_PATTERN` — класс символа токена включает пробел, табуляцию,
   перевод строки и точку, поэтому соседние ячейки первоисточника склеивались в одно число.
   Проба до правки на `beda40a` (`t7108a_repro.py` в keep-каталоге агента): «Год Июль Август /
   2026 2,3 2,2», «2026 2,2», «2,3 2,2», «2,3»+табуляция+«2,2», «Таблица 3. 2,2%», «2,20%» —
   значение 2,2 не найдено (гейт снимал законные supports-связи именно с первичных страниц-
   таблиц Росстата); «16 49 тыс. тонн» при значении 1649 наоборот ложно склеивалось и
   принималось. Докстринг помощника обещал «2,2» ≡ «2.20» — код этого не обеспечивал.

2. **Правка (гейт, ADR-0034 §10 п.2).** Сравнение по стороне ТЕКСТА: каждое значение ищется
   своей текстовой формой (`_value_text_forms`) с границами (`_form_is_standalone`): разделитель
   «.» или «,»; хвостовые нули дробной части необязательны до потолка 3 («2,2» ≡ «2.20»);
   группировка разрядов по три цифры через одиночный пробел — после `normalize_scan_text`
   неразрывные и узкие пробелы уже обычные («1 649» ≡ «1649»; «16 49» — не форма). Перед
   формой нет цифры и нет пары «цифра + разделитель»; после формы нет цифры и нет пары
   «разделитель + цифра»; у сгруппированной формы блокируется стык с соседней группой
   («1 12 345» не подтверждает 12345, «1 649 3» — не подтверждает 1649). Пробел, табуляция,
   перевод строки и точка с пробелом ПЕРЕД значением не препятствуют. Соседние ячейки не
   склеиваются: «12 2,2» находит 2,2 во второй ячейке (прежний quirk-тест переписан).
   Патологические токены утверждения с несколькими точками (следствие прежней склейки
   `claim_value_numbers`) остаются пословными. Прочие символы («−», дефис, скобки) границей
   не склеиваются и не блокируют.

3. **Что НЕ изменено.** Решения и версии детекторов атрибуции: `_measured_numbers`,
   `claim_value_numbers`, `VALUE_NUMBER_PATTERN`, гварды relay §7/§9 — прежние; текстовые
   формы читает только гейт. Вызов в `apps/orchestrator/curator_rejections.py` не тронут
   (файл не правился). Промпты и конфигурации не тронуты: curator-v15/config-v25 остаются —
   модель обязана называть значение целым числом, хост принимает его текстовые формы
   (осознанное расхождение закреплено в ADR-0034 §10). Правила движка, оценки,
   `AuditEventType`, миграции — без изменений.

4. **Тесты.** `tests/unit/test_link_value_helper_t7108.py` 10 → 27 случаев: параметризованные
   строки дефекта (6), прежние засчитываемые строки (5, прежний результат), хвостовые нули,
   «2,21» не эквивалентно, группировка («1 649», NBSP, голое «1649»), ложная группировка
   «16 49», стык групп «1 12 345», знак (закреплён факт: `claim_value_numbers` минус
   снимает — «−2,2%» и «-2,2%» дают значение «2.2», тексты со знаком подтверждают); тест
   склейки переписан: было `frozenset()` → стало `{2.2}`; тест граничных чисел переименован
   в `test_digits_inside_a_larger_number_are_not_confirmation` (имя, смысл прежний).
   `tests/unit/test_curator_link_value_gate_t7108.py` 17 → 18 (табличная строка страницы).
   `tests/scenario/test_link_value_evidence_gate_t7108.py` 7 → 8 (т8 + фикстура
   `URL_ROSSTAT_TABLE`: связь со страницей «2026 2,3 2,2» остаётся, страница размеров файлов
   снимается). `tests/scenario/test_curator_correction_round_t7106.py`: докстринг и заголовок
   т1 приведены в соответствие пост-T7.108 утверждениям теста (записано 2 claims из 4;
   прежде заголовки обещали «все четыре») — правка только комментариев, утверждения байт в
   байт. Число тестов нигде не уменьшено.

5. **Краснота до правки.** Новые/переписанные тесты на `beda40a`: detached worktree
   `/tmp/wt7108a` (копии незакоммиченных тестов; импорты из ворктура подтверждены
   `module.__file__` — ловушка AGENTS §7 про чужой .venv не сработала): **11 падений,
   37 прохождений** по трём файлам (helper — 9, unit гейта — 1, сценарий т8 — 1); ворктура
   больше нет. После правки — зелёные в основном дереве.

6. **Проверка (§6) перед коммитами.** Коммит `7c7cc95` (код + тесты): ruff — All checks
   passed (первый прогон поймал SIM103 в новой функции — хвост переписан одним булевым
   выражением, смысл прежний); mypy strict на `packages apps hostctl` — Success; pytest
   `-n auto -m "not timing"` — **2558 passed, 12 skipped** (собрано 2570: +19 к T7.108);
   `-m timing` — **4 passed**. Контейнеров **41** до и после правки, `noezema_mig_*` **40**,
   `noezema_tpl_*` **0** (замеры — вне прогонов pytest). Документальная часть (правка
   ADR-0034 §10 в трёх местах, этот раздел, запись AGENTS §7) — отдельный коммит.

7. **Пределы.** Any-of и «голые» числа страницы — прежние пределы §10 (постороннее число
   того же номинала сохраняет связь); бюджет скана 40 000 общий с атрибуционным;
   группировка читается только одиночным пробелом; знак на стороне утверждения снимается
   (`claim_value_numbers` его не видит — закреплено тестом). Стенд `.92` не тронут: гейт
   живёт в коде хоста и действует при любой конфигурации.

## T7.109 (2026-10-13): равнозначность чисел, оба конца диапазона прогноза, ближайший первоисточник и запись «Минэкономразвития России» (решение пользователя «(а) и (б) вместе»; ADR-0029 и ADR-0035 дополнения; код и тесты — коммит `b514eb4`)

1. **Задача (два дефекта стендовых текстов).** (а) Числа сравнивались строками: утверждение
   «6,0–7,0%» (значение канонизируется в «7.0») не совпадало с измеренным на странице «7» —
   relay guard (е) молчал, пересказ прогноза ЦБ на «Интерфаксе» не атрибутировался; левый конец
   диапазона терялся склейкой greedy-токена (`claim_value_numbers` «6–7%» → «7», «6,0–7,0%» →
   «7.0»). (б) В словаре не было Минэкономразвития: «…превысила верхнюю границу прогноза ЦБ …
   (6-7%) и прогноз Минэкономразвития (6,8%)» приписывало 6,8 Банку России (`derivative cbr`),
   а «Согласно майскому прогнозу Минэкономразвития … 2,2%» на kommersant оставалось без
   атрибуции при страничном указателе на Росстат. Витрина при этом молчала на паре «запись
   атрибуции A + якорь-родитель B» (условие (в) T7.87 читало это как разночтение).

2. **Правка (а): одна точка сравнения и оба конца.** `_canonical_number` в
   `apps/research_proxy/source_attribution.py` — единственная нормализация чисел для обоих
   детекторов и гейта значения на связи: хвостовые нули дробной части не значимы (7 ≡ 7,0;
   2,20 ≡ 2,2), целая часть как прежде («12,2» и «122,2» ≠ 2,2), токены с несколькими
   разделителями («52.22», «3.2.5») не канонизируются. `claim_value_numbers` в
   `apps/research_proxy/value_attribution.py` относит к значениям утверждения оба конца
   диапазона (дефис/короткое/длинное тире, связка « … до …», единица после правого конца);
   сторона страницы не тронута: «6-7%» остаётся одиночным измеренным «7». Гейт T7.108a
   подхвачен тем же помощником; его тесты на хвостовые нули (обе стороны) зелёные.

3. **Правка (б1/б2): словарь и ближайший первоисточник.** Запись `minec` «Минэкономразвития
   России» (home economy.gov.ru; алиасы «минэкономразвити…», полное название, английская форма,
   доменная метка; короткий «минэк» намеренно отсутствует; Минфин ≠ minec — закреплено тестом).
   Новый режим fallback `apps/research_proxy/value_attribution.py::_nearest_primary_decision`
   (после окна публикации, до библиографии): включается только где прежние режимы молчат и во
   фрагментах с двумя+ словарными алиасами (гвард relay (а) их и глушил); кандидат — алиас, чей
   первый измеренный интервал после вхождения содержит значение утверждения (своё другое число
   ближе — приписки нет); гвард третьего актора — та же `_third_actor_present`, что у relay;
   домашний хост отсекается `is_home_host`, режим не производит `self_primary`; два кандидата —
   прежнее `ambiguous_primaries`. Решения пользователя воспроизведены тестами: «прогноз ЦБ
   (6-7%) и прогноз Минэкономразвития (6,8%)» → 6,8 за Минэкономразвитием; 6–7 за Банком России.

4. **Правка (б3): витрина.** `apps/web/producer_view.py::producer_publication_case` —
   построконечный приоритет: названная запись о происхождении значения выводит якорь-родитель
   этой же строки из выбора имени; строк без записей не затронуто; T7.101 (собственные
   публикации) и разночтение записей двух строк — прежние; битая запись — прежний отказ.
   Анализ старых тестовых данных (`tests/fixtures/t7101/evidence_sources_all20.json`): 33 улики,
   из них 10 подписаны записями `value_attribution` (rosstat 6, cbr 4), **только страничным
   указателем подписаны 2** (cbr.ru reginfl → якорь Росстата; forbes 552221 → публикация ЦБ
   forecast_260724.pdf), 21 без подписи. Для этих двух поведение не изменилось: записей о
   значениях у них нет, указатель работает как раньше.

5. **Версии и переатрибуция.** `ATTRIBUTION_METHOD_VERSION` → `host-source-attribution-v6`,
   `VALUE_ATTRIBUTION_METHOD_VERSION` → `host-value-attribution-v8`; `reattribution.py` не
   правился — пересмотр старых улик запускается динамическими константами `CURRENT_*` и прежними
   предикатами «метки нет ИЛИ метка ≠ текущей версии» (без подъёма версий переатрибуция осталась
   бы слепой — урок T7.98). Пины версий обновлены в 16 прежних тестовых файлах (20 вхождений, от 1 до 2 на файл); эпохальные
   пины (стендовая симуляция v6-эпохи в `test_self_publication_labels_t7101.py`, в3/v1-monkeypatch
   сценариев, метки записей фикстур) не тронуты. Схема `host-value-attribution-v1`, замкнутый
   набор pairing, пороги окон, оценки, группы, миграции, промпты — без изменений.

6. **Закреплённые решения, изменившие исход.** Страница `expert_5-59` (таблица
   `tests/unit/test_attribution_t785.py`): было `derivative rosstat` → стало
   `ambiguous_primaries` — новая словарная запись вскрыла вторую атрибуцию той же страницы
   («…ускорилась до 6,27%, следует из обзора Минэкономразвития…»), прежний честный отказ.
   Страница kommersant-фикстуры т4 (новая фикстура): было `derivative rosstat` → стало
   `ambiguous_primaries` (та же причина). Решения по значениям в таблице t785 (5,59 → rosstat,
   14,5 → cbr и отказные строки) не изменились; `test_web_producer_view.py` закреплённый тест
   «атрибуция и якорь должны совпадать» переименован в «записанная атрибуция важнее страничного
   указателя» с ожиданием имени из записи (обоснование в докстринге). Остальные падения за
   время триажа — только пины версий; иных ослаблений прежних правил не найдено.

7. **Тесты.** Новые: `tests/unit/test_attribution_t7109.py` — 33 (фикстурные закрепы, т1–т4
   решения значений, равнозначность в детекторе и гейте включая границу «6,3 ≠ 6,35`, таблица из
   10 форм диапазона с отрицательными строками «2025–2026 годы» и «Между 6,0 и 7,0%.», близость:
   два первоисточника/блокировка чужим числом/третий актор/домашний хост/одиночный алиас,
   словарь minec и минфин-отличие), `tests/unit/test_web_producer_view_t7109.py` — 7 (т7
   приоритет записи, строка без записи прежним указателем, битые края, построконечность),
   `tests/scenario/test_reattribute_value_range_t7109.py` — 1 (т8: утверждение «6,0–7,0%» +
   interfax-улика без записей + собственная страница ЦБ → прогон v8 записывает interfax
   происхождение значения «Банк России» (родитель — прочитанная страница ЦБ того же хоста) и
   группу `parent:…` для неё, cbr-улике — запись о собственной публикации; повторный прогон —
   0 изменений, строки «решение уже записано ранее» / «собственная публикация уже записана»),
   фикстуры `tests/fixtures/t7109/` (interfax 1121468, kommersant 8924678).

8. **Краснота до правки.** Копии новых/изменённых тестов в detached worktree `6b8b5a7`
   (`/tmp/wt7109`, `module.__file__` под `/tmp/wt7109` подтверждён): unit — **30 падений,
   37 прохождений** (красные ровно т1–т6 и витринные перевороты; зелёные — охранные тесты
   прежнего поведения), сценарий т8 — падение на dry-run плане (у interfax-строки
   «изменений нет: атрибуция значения не собрана», новый прогон даёт «будет записано
   происхождение значения улики»). Пробы «было → стало» на `6b8b5a7`: «6,0–7,0%»-утверждение на
   interfax — `value_not_attributed` → `derivative cbr`; 6,8 Минэкономразвития — `derivative cbr`
   (ложная склейка) → `derivative minec`; 2,2 kommersant — `value_not_attributed` → `derivative
   minec`; 3,4 Росстата — `derivative rosstat` без изменений; витрина «атрибуция minec + якорь
   rosstat» — `None` → «Минэкономразвития России». Ворктур удалён после проверки.

9. **Проверка (§6) перед коммитами.** Коммит кода+тестов (`b514eb4`): ruff — All checks passed;
   mypy strict `packages apps hostctl` — Success (156 файлов); pytest `-n auto -m "not timing"` —
   **2599 passed, 12 skipped** (собрано 2611: +41 к T7.108a-базе 2570); `-m timing` — **4 passed**.
   Контейнеров после прогонов — тот же порядок величин (`noezema_mig_*`/`noezema_tpl_*` — см.
   отчёт), тестовая БД прежняя. Документальная часть (дополнения ADR-0029 и ADR-0035, этот
   раздел, записи AGENTS §7) — отдельный коммит.

10. **Пределы.** Росстатовские индексные формы (106,35 ↔ 6,35%) не в объёме задачи; диапазоны
    годов («2025–2026 годы») значениями не становятся (единица не измерена — закреплено тестом);
    режим близости не производит `self_primary` и не приписывает значение странице-первоисточнику
    сверх прежних режимов; переатрибуция по-прежнему не проставляет страничные указатели
    (T7.98: снимает только при противоречии); гейт дублей значений, пороги и оценки — прежние.

## T7.109a (2026-10-13): замечание приёмки T7.109 — ложная склейка одиночного значения с концом диапазона страницы (найдено менеджером; ADR-0029 дополнение; код и тесты — коммит `41c26b7`)

1. **Замер (репродюсер, `tests/fixtures/t7109/interfax_1121468.txt`).** Утверждение
   «Консенсус-прогноз экономистов, опрошенных «Интерфаксом», по инфляции в России на 2026 год
   составляет 7,0%.» давало `derivative` с ключом `cbr`: одиночное число утверждения (канон `7`)
   совпало с ПРАВЫМ концом диапазона прогноза ЦБ «(6-7%)» — фрагменты страницы с позициями
   ≈2222, ≈2288, ≈5455 («…превысила верхнюю границу прогноза ЦБ на 2026 год (6-7%)…», «ЦБ
   11 сентября подтвердил свой прогноз … на уровне 6-7%»). Реле-кандидат (T7.98) сработал по
   цепочке alias + глагол прогноза + число: числом реле стало число утверждения, а не число
   алиаса. Значение консенсуса и конец чужого диапазона — разные значения; по ADR-0029 ложная
   склейка дороже ложного пропуска. Прямой прогон на `b0be621` (detached worktree
   `/tmp/wt7109a`, `module.__file__` подтверждён, удалён после проверки): `derivative cbr`.

2. **Правило.** Диапазон страницы («6-7%», «6–7%», «от 6 до 7%»; соединители те же
   `_RANGE_CONNECTOR`, левый конец без своей единицы, единица после правого) — ОДНО значение,
   составной канонический токен `6-7` (разделитель `_RANGE_TOKEN_SEP`, которого нет в выводе
   `_canonical_number`: составной токен не может совпасть с одиночным числом). Одиночное значение
   утверждения концу диапазона не приписывается (правый конец «5-7%» — не «7», левый конец «6-7%»
   — не «6»); диапазон утверждения совпадает только с диапазоном текста с теми же концами
   (эквивалентность T7.109 сохранена: «6,0–7,0%» ≡ «6–7%» ≡ «от 6 до 7%»); одиночное число
   страницы по-прежнему приписывает одиночное значение утверждения.

3. **Реализация (`apps/research_proxy/value_attribution.py`).** Помощники: `_range_value_spans`
   (:490 — диапазоны текста теми же условиями, что диапазонная ветка `_claim_measured_numbers`),
   `_measured_value_tokens` (:523 — значения текста: составные токены диапазонов + одиночные
   числа, не накрытые диапазоном), `claim_match_values` (:542 — профиль утверждения для
   сравнения). Правка в пяти точках сравнения: фильтр «другого числа» `_pairing_maps`, окно
   публикации (`_measured_value_spans`, :735), режим близости (`_measured_spans`, :755 и
   `_nearest_primary_keys`), библиография (проверка (ii)). Гвард (е) relay не изменён:
   `_relay_forecast_keys` получает плоские `required_numbers`, ложная склейка отсекается фильтром
   вызывающего кода до реле, а для диапазонного утверждения реле работает по концам, как в
   T7.109. `claim_value_numbers`, `claim_values_found_in_texts` (гейт T7.108a) и запись
   `claim_values` остаются плоскими; `VALUE_NUMBER_PATTERN` не изменён.

4. **Решения до/после.** т1 interfax «7,0%»: `derivative cbr` → `value_not_attributed`
   (атрибуции в основании есть, значения утверждения среди значений основания нет). т2
   «6,0–7,0%» и т3 «6–7%»: `derivative cbr` без изменений. т4 interfax 6,8%: `derivative minec`
   без изменений. т6 kommersant 2,2 → minec, 3,4 → rosstat: без изменений. Синтетика (т5):
   диапазон страницы + одиночное утверждение → `value_not_attributed` (было `derivative cbr`);
   одиночное + одиночное → `derivative cbr`; «6–7%» против «6-7%» и против «от 6 до 7%» →
   `derivative cbr`; два алиаса в одном фрагменте (режим близости): «…составит 7%.» →
   `no_value_attribution` (было `derivative cbr`), «…6–7%.» → `derivative cbr`.

5. **Версии.** `VALUE_ATTRIBUTION_METHOD_VERSION` → `host-value-attribution-v9` (решения метода
   значений изменились; переатрибуция пересматривает записи эпох v2…v8 прежним предикатом
   «метка записи ≠ текущий метод», `reattribution.py` не изменялся). Страничный
   `host-source-attribution-v6` без подъёма: значений утверждения с числами страницы он не
   сравнивает, его решения не изменились (закреплено тестом версий). Пины v8→v9 подняты в
   16 тестовых файлах (17 вхождений); три фикстурные метки записей эпохи v8 в
   `tests/unit/test_web_producer_view_t7109.py` оставлены (имитация старых записей, тест версию
   не проверяет).

6. **Тесты и красность.** Новый `tests/unit/test_attribution_range_t7109a.py` (11 тестов):
   профиль сравнения (диапазон не раскладывается; эквивалентность форм соединителя; год без
   единицы — не значение), т1 честный отказ, т2/т3, т4, т6, синтетика т5a–т5f (включая левый
   конец «5-7%» и режим близости), версии v9+v6. Красность на `b0be621`: сборка нового файла —
   ImportError (`_measured_value_tokens` не существует); шесть unit-файлов с пинами версий —
   22 падения (в t785 пин внутри каждого параметризованного случая; сами решения не
   изменились); семантика т1/т5a/т5e на старом коде — `derivative cbr`. Тесты гейта T7.108a и
   прежние проверки решений не ослаблены. Полный §6 перед каждым из двух коммитов зелёный:
   ruff/mypy чисто, pytest `-n auto -m "not timing"` — **2610 passed, 12 skipped** (база T7.109
   — 2599+12, новый файл +11), `-m timing` — **4 passed**.

7. **Записанные пределы (не лечатся здесь; кандидаты отдельных задач).**
   - Одинаковое число в разных контекстах: если первоисточник сам называет одиночное «7%» рядом
     с консенсусным «7,0%», склейка по-прежнему возможна — правило T7.109a снимает только концы
     диапазонов; разбор контекстов значения — отдельная задача.
   - Латинская «o» (U+006F) вместо кириллической после `\b` в шаблонах:
     `\boжида(?:ет|ут|ют|ем|ит|л[а-яё]*)` в `RELAY_FORECAST_VERBS`
     (`apps/research_proxy/source_attribution.py:416`) и в маркерах чужой оценки
     `OTHER_SOURCE_ESTIMATION_MARKERS` (`apps/research_proxy/value_attribution.py:302`) —
     кириллические «ожидает/ожидал» не матчатся, реле по такому глаголу не срабатывает
     (проверено: `_relay_forecast_keys("ЦБ ожидает 7%.") == {}`). Исправление меняет
     pairing-правила (за пределами T7.109a) — отдельная задача; синтетические тесты T7.109a
     намеренно построены на рабочих глаголах («подтвердил», «повысил»).

## T7.110 (2026-10-13): омоглиф в шаблонах глагола ожидания (найдено менеджером; ADR-0029 дополнение; код и тесты — коммит `43ea213`, документация — следующий коммит)

1. **Дефект.** Первый символ после границы слова в двух шаблонах был латинской «o» (U+006F)
   вместо кириллической «о»: шаблон глагола ожидания в `RELAY_FORECAST_VERBS`
   (`apps/research_proxy/source_attribution.py:416`) и тот же шаблон с концевой границей в
   маркерах чужой оценки `OTHER_SOURCE_ESTIMATION_MARKERS`
   (`apps/research_proxy/value_attribution.py:302`). Жил с T7.98 (введение реле) — кириллические
   «ожидает/ожидают/ожидали» не матчились: реле по глаголу ожидания молчало
   (`_relay_forecast_keys("ЦБ ожидает 7%.") == {}` на прежнем коде), вето окна публикации по
   чужому ожиданию не срабатывало. Прежние текстовые проверки коммита не ловили дефект: проверка
   §8 отбрасывала все строки с обратной косой чертой, а омоглиф сидит сразу за границей слова.

2. **Проверка утверждения менеджера (других омоглифов нет).** Скан `apps/`, `packages/`,
   `hostctl/`, `prompts/*.md` (строки с кириллицей; вырезаны экранирования `\xHH`, `\uXXXX`,
   `\`+буква и однобуквенные диапазоны внутри скобок-классов) нашёл ровно два омоглифа —
   исправленные строки 416/302. Иные близкие написания — не омоглифы: «fence-маркер» (дефис
   разделяет), гибридные комментарии с дефисом, смешанные регулярные классы вида
   `[А-ЯЁA-Z][А-ЯЁA-Za-zа-яё]*` (диапазоны, не соседство букв). Единственная законная смежность
   — брендовое имя «СберCIB» (`value_attribution.py:7`, единственное вхождение; исключение с
   причиной в защитном тесте, перечень T7.102). Документационные гибриды («datless»,
   «propagateция») вне скана (в `AGENTS.md`/STATUS), не правятся.

3. **Замер решений до/после.** Свип всех фикстурных текстов тестов атрибуции (стендовые страницы
   T7.85/T7.98/T7.101/T7.109, корпуса `evidence_sources_all20.json`,
   `claims_detail_provenance.json`, `claims_live.tsv`) на 29cafeb против исправленного кода:
   3071 пара страница x утверждение — **ноль изменившихся решений** (статус, ключ, реле-кандидаты,
   pairing). Синтетика сценариев переатрибуции T7.98/T7.109 («ожидается рост цен…», «ранее
   регулятор ожидал…») — тоже ноль (глагол без алиаса в окне реле; алиасы уже матчились другими
   глаголами). Изменяются решения на текстах с названным ожиданием (закреплены новым тестом):

   | случай | было (29cafeb) | стало |
   |---|---|---|
   | «ЦБ ожидает инфляцию в 2026 году на уровне 6–7%.» + утверждение о прогнозе ЦБ 6,0–7,0% — страница | `no_value_attribution` | `derivative cbr` |
   | тот же текст — значение | `no_value_attribution` | `derivative cbr`, pairing строгий |
   | та же фраза + шаблон «По данным Росстата… 5,59%» — страница | `derivative rosstat` | `ambiguous_primaries` |
   | тот же текст — значение 6–7% | `value_not_attributed` | `derivative cbr` |
   | «опубликованный отчёт Росстата… Отдельно регулятор ожидает ускорение инфляции до 6,3%» — значение | `derivative rosstat` (окно) | `value_not_attributed` (вето глагола) |
   | та же страница целиком | `derivative rosstat` | без изменений |
   | «Аналитики ожидают инфляцию 6,3%…» в окне публикации | `value_not_attributed` | без изменений (вето по действующему лицу было и раньше) |
   | «Инфляционные ожидания населения … выросли до 14,2%» (имя показателя) | `derivative cbr` (окно) | без изменений |

   Ложных склейк правка не порождает: новые приписки — собственные числа собственных прогнозов
   названного первоисточника (гварды реле T7.98 сохранены); сняты прежние ложные решения
   (приписывание чужого ожидания опубликованному числу Росстата; ложный одиночный указатель
   «производная Росстата» при названном прогнозе ЦБ — тот же консервативный выход T7.98,
   `ambiguous_primaries`).

4. **Реализация.** Только замена буквы в двух шаблонах (строки 416 и 302 обоих файлов); состав и
   форма словарей, гварды реле (а)–(е), окна, пороги, pairing-правила, движок правил, схемы,
   миграции, `AuditEventType`, группы независимости, промпты и конфигурации — не тронуты.
   `reattribution.py` не изменялся: перемену видит динамическими константами `CURRENT_*`.

5. **Версии.** `VALUE_ATTRIBUTION_METHOD_VERSION` → `host-value-attribution-v10` (решения метода
   значений на текстах с названным ожиданием изменились). Страничный метод → v7: подъём сделан
   потому, что решения страничного метода тоже меняются (реле заговорило: новый `derivative`,
   новое противоречие двух первоисточников); по фикстурным текстам репозитория они не изменились
   (закреплено существующими закрепленными таблицами решений и тестом версий). Переатрибуция
   пересматривает записи значений эпох v2…v9 и страницы эпохи v6 прежним предикатом
   «метка ≠ текущий метод»; развёртывание и одна переатрибуция на стенде — согласованное решение
   менеджера (фактических записей стенда этот коммит не касается). Пины версий подняты в 20
   тестовых файлах (26 вхождений: 18 значений + 8 страниц); метки прежних эпох
   (`host-value-attribution-v1…v6`, v8 — имитация старых записей) оставлены.

6. **Тесты и красность.** Новые: `tests/unit/test_homoglyph_source_scan_t7110.py` (11) —
   защитный скан исходников и промптов; единственное исключение с причиной — «СберCIB»;
   позитивный контроль собран через chr(), чтобы сам файл теста не содержал омоглифа; на 29cafeb
   краснеет и называет ровно `source_attribution.py:416` и `value_attribution.py:302`. Новые:
   `tests/unit/test_expectation_verb_t7110.py` (14) — закреплённые решения таблицы п.3, формы
   слова (глагол матчится, имя показателя — нет), версии v10+v7. Красность обоих на 29cafeb —
   12 падений (отдельный прогон в detached worktree `/tmp/wt7110`, удалён после проверки;
   неизменность «аналитики ожидают» и имени показателя на старом коде зелёная — тесты фиксируют
   её как неизменное поведение). Полный §6 перед каждым из двух коммитов зелёный: ruff/mypy
   чисто, pytest `-n auto -m "not timing"` — **2635 passed, 12 skipped** (база T7.109a —
   2610+12, новые файлы +25), `-m timing` — **4 passed**.

7. **Записанные пределы (не лечатся здесь).** У релевого шаблона нет концевой границы слова
   (прежняя форма словаря, сохранена по условию задачи): прилагательные «ожидаемой», «ожидаемых»
   содержат подслово «ожидаем», а «ожидается» матчится опцией «ет» внутри — все три читаются
   реле как глагол; вето по ним молчит (там концевая граница есть). Имена «ожидания», «ожиданий»
   не матчатся ни там, ни там. Закреплено тестом форм слова; на решениях это не сказывается —
   гварды реле (б)–(е) и home-host проверены свипом (ноль изменений). Добавление границы —
   отдельное решение словаря.

## T7.111 (2026-10-14): гигиена шагов исследователя — почти-повторы поиска, недоступные хосты, промпт explorer-v11 (ADR-0036)

Решение пользователя («Бриф T7.111 с пунктами 1–3»). Учёт шагов и лимиты (T7.76) не изменены:
отклонённое действие стоит шаг; отклонение — без строки `actions`, ровно одно событие
`ACTION_FAILED{denied:true}` со строкой-наблюдением в ленту (тот же путь, что T7.12/T7.98).
Коммиты: `e00f73f` (часть 1), `e0a95cd` (часть 2), `1088e60` (часть 3), `064c8f5` (комментарии:
непроверяемая ссылка «T7.13 lesson 3» заменена на замер EVAL-3b P.5 («404 по ООН — опечатка
модели», самоисправилась; уроков «T7.13 lesson 3» в документах репозитория нет), исправлена
опечатка «в окле» -> «в окне»; поведение кода не менялось, сообщение коммита e0a95cd исторически
не правилось), docs-коммит (этот раздел, ADR-0036, перепин AGENTS config-v26).

1. **Почти-повтор `web.search` отклоняется как T7.12.** Нормализация запроса — чистые функции
   модуля `apps/orchestrator/tool_context.py`: нижний регистр, `ё`->`е`, множества токенов
   `[0-9a-zа-я]+` (`hh.ru` -> {hh,ru}, `2,1%` -> {2,1}). Порог — именованная константа
   `SEARCH_QUERY_NEAR_DUP_JACCARD = 0.6` (значение из постановки задачи) и
   `SEARCH_QUERY_NEAR_DUP_MIN_WORDS = 4`: запросы с меньшим числом различных слов в сравнение
   не идут (короткие запросы об одном показателе дают Жаккарра 0.75–1.0 при законной смене
   угла — «инфляция в России» / «инфляция в России за 2026 год»; замеры ниже). Отклоняется
   запрос, чей Жаккарр >= 0.6 против любого ранее **выполненного** запроса сессии:
   `ACTION_FAILED` c `reason:"search_query_near_duplicate"`, `similar_query`/`similar_step`/
   `similarity` в payload, наблюдение «прочитай найденное (research.fetch) или смени
   формулировку по существу». Запоминаются только успешные поиски — точное повторение
   отклоняется при первом повторе (1.0 >= 0.6). Wiring: `apps/orchestrator/orchestrator.py`
   (гейт цикла до создания действия). Измерение по фикстуре менеджера
   (`fixtures-t7111/step-budget-2026-10-14.txt`, 8 сессий, 54 поисковых строки): гейт ловит
   11 из 11 объявленных почти-повторов `near_dup_query` (в том числе 3 «месячных» с Жаккарром
   0.700/0.667/0.636 — ниже 0.6 не упали); максимальный Жаккарр законной перефразы в фикстуре —
   0.556, минимальный отклонённого — 0.636: порог 0.6 разделяет их с запасом. Промахнулись
   только 2 пары «добавление числа» (0.571/0.500) — они и в фикстуре не объявлены
   почти-повторами. Все примеры запросов фикстуры >= 5 слов — минимальная длина сравнения не
   выключает живые запросы. ПОПРАВКА T7.111a (2026-10-14): константы пункта 1 удалены из кода,
   решение стало качественным («ни одного нового содержательного слова») — см. раздел T7.111a в
   конце файла; способ отказа (T7.12), payload и учёт шага (T7.76) не изменены.

2. **Недоступные хосты (модуль `apps/orchestrator/host_availability.py`).**
   (а) Отказ `research.fetch` с HTTP-кодом из {401, 403, 429} (строка `non-200 status: <код>`)
   или таймаут (`timeout:`) закрывают хост для чтения до конца сессии: следующий fetch на него —
   `ACTION_FAILED{reason:"host_unavailable_in_session"}` с названием кода и шага первого отказа.
   404, нечитаемый артефакт, лимит размера хост не метят (свойства страницы, а не недоступность —
   замер EVAL-3b P.5). (б) На старте каждой сессии — ровно один read-only SELECT к существующему
   журналу `audit_events` (без новых таблиц, миграций и типов событий): пары started/failed и
   успешные `research_content_read` чужих сессий за 7 дней (`HOST_UNAVAILABLE_WINDOW_DAYS=7`);
   хост попадает в секцию контекста «Недавно недоступны» при >= 2 отказах
   (`HOST_UNAVAILABLE_MIN_FAILURES`) и отсутствии успешного чтения после последнего отказа;
   ключ хоста — hostname в нижнем регистре, один ведущий `www.` снимается. Секция ограничена
   (`HOST_AVAILABILITY_MAX_HOSTS=8`, `HOST_AVAILABILITY_SECTION_CHAR_LIMIT=1200`) и идёт в
   контекст после строки бюджета — до наблюдений, поэтому вырезание старых наблюдений её не
   прячет; формат строки: `- <host> — <код>, <дата> (отказов: N)`. Секция **не запрещает**
   чтение. Пример: `- stats.hh.ru — 401, 2026-10-13 (отказов: 2)`. Wiring гейта и сборки секции —
   `apps/orchestrator/orchestrator.py`.

3. **Промпт explorer-v11 и payload config-v26.** `prompts/explorer/explorer-v11.md`
   (sha256 `4bebf129…`) = explorer-v10 (sha256 `2351e76c…`) байт-в-байт кроме строки версии и
   правил 14–17 после правила 13: (а) после удачного поиска следующим шагом читай найденное,
   повтор выполненного запроса — смена угла (добавил/убрал слово), а не перефраз; (б) хосты из
   секции «Недавно недоступны» не обходят запрет, но сессию вокруг них не строить; (в) в
   статистическое ведомство или центральный банк — сразу к выпуску/документу с числами (страница
   без чисел не даёт окна улики); (г) вопрос про независимые оценки — вторая линия
   не-официальных акторов с середины бюджета. Примеры вымышленные; раздел «Примеры завершения»
   байт-в-байт v10. `docs/eval/config-v26-payload.json` = config-v25 ровно с одной правкой —
   пин explorer-v10 -> explorer-v11; canonical `3d93df81…` (v25 — `b86bd7b0…`), хеш файла
   `029f3d86…`. Дефолт bootstrap'а не изменён; активация config-v26 — решение менеджера.
   Тест `tests/unit/test_explorer_v11_config_v26_t7111.py` закрепляет байтовые правки, канонические
   хеши, неизменность прежних payload'ов на диске и невключённость v26 в скрипты dev-стенда.
   Усилен `tests/unit/test_prompt_example_no_real_data.py`: пары «новый промпт -> предыдущий
   закреплённый пин, отсутствующий на диске (исторический путь)» теперь пропускаются, и для
   каждой роли текущего payload находится настоящий прежний текст (пин curator-v14 найден —
   пара v15->v14, как раньше; у explorer первой парой стал v11->v10).

4. **Тесты и краснота на 6852df8.** Новые файлы: `tests/unit/test_search_query_hygiene_t7111.py`
   (8), `tests/scenario/test_explorer_query_hygiene_t7111.py` (1; локальный фейковый поисковый
   движок: отклонены 4 запроса из 7, выполнено 3, шагов 8), `tests/unit/test_host_availability_t7111.py`
   (9), `tests/scenario/test_explorer_host_availability_t7111.py` (3; фейковый fetch-сервис
   живой/мёртвый(403)/медленный(таймаут)/404/артефакт: отказ хоста засчитывается один раз,
   секция строится из журнала ровно одним запросом — счётчик вызовов подтверждён),
   `tests/unit/test_explorer_v11_config_v26_t7111.py` (7). Краснота до реализации доказана на
   detached worktree 6852df8 с копиями новых тестов: 3 ошибки сбора (нет `search_near_duplicate`;
   нет модуля `host_availability`), поведенческий провал `test_explorer_query_hygiene_t7111`
   (`assert 0 == 4` — ни один почти-повтор не отклонён) и 5 падений тестов части 3 (закреплённых
   хешей v11/config-v26 на базовом дереве нет; два инвариантных теста про explorer-v10 заранее
   зелёные по построению). Изменённый существующий тест: `tests/unit/test_prompt_example_no_real_data.py`
   — было 77 закреплённых случаев (65 passed, 12 skipped) с самосвязанной парой
   explorer-v10->explorer-v10; стало 79 (67 passed, 12 skipped): случай заменён настоящей парой
   explorer-v11->explorer-v10, новые файлы добавлены в корпусные сканы. Прежний код при добавлении
   payload v26 падал на сборе (пин отсутствовавшего на диске прежнего промпта extractor) — правка
   учитывает исторические пути. Число тестов возросло: 2635 -> 2665 в параллельном прогоне
   (+28 тестов новых файлов, +2 случая стража).

5. **Проверки §6 перед коммитами.** Перед частью 1 (дерево без части 2): 2644 passed, 12 skipped;
   4 passed timing. Перед частью 2: 2656+12; 4 timing. Перед частью 3: 2665+12 (231 с); 4 timing
   (47,7 с). Перед комментарийным коммитом `064c8f5`: 2665+12 (266 с); 4 timing (47,7 с).
   ruff и mypy — чистые каждый раз.

6. **Открытый вопрос (зафиксирован в ADR-0036, решение не принято).** Отказы частей 1 и 2(а)
   сейчас стоят шаг — как и всё отклонённое по T7.76. Вопрос менеджера: не засчитывать ли шагом
   отказ гейта (действие не исполнялось)? Любая такая правка меняет учёт шагов и бюджета — вне
   этой задачи.

7. **Осознанные пределы.** Точное повторение `web.search` теперь отклоняется с первого повтора
   (прежнее поведение T7.12 для `research.fetch` не тронуто). Пяти-словные пары «только год
   другой» дают Жаккарра 0.667–0.833 и будут отклонены — перефразой это честной не считается,
   правило 14 промпта объясняет смену угла (это снято поправкой T7.111a: смена периода или актора новым словом
    законна; пределы поправки — в разделе T7.111a). Классификация недоступности держится на строках
   ошибок исполнителя (404 хост не метит). Секция показывает последние 8 хостов семисуточного
   окна — восстановившийся хост выпадает из неё при успехе чтения в чужой сессии, заглухший
   давно — через неделю; карантина в прокси нет (это и задумано: память секции короткая).


## T7.111a (2026-10-14): поправка гейта почти-повторов web.search — решение по основам содержательных слов (дефект, найденный менеджером на приёмке T7.111; код и тесты — коммит `77d7297`; ADR-0036 §1а)

**Дефект (воспроизведён на 3e3dd9b).** Порог Жаккара 0.6 и `MIN_WORDS = 4` отклоняли законные
смены угла одним содержательным словом против правила 14 explorer-v11: сравнение с прошлым годом
(«инфляция в России сентябрь 2026 Росстат» -> «…сентябрь 2025 Росстат» — отказ 0.714), смена
месяца («Росстат уровень безработицы в России июль 2026» -> «…август 2026…» — 0.750), подмена
актора («ВТБ прогноз инфляции 2026 Россия аналитики» -> «Газпромбанк …» — 0.714). Это был
закреплённый риск (п. 7 раздела T7.111, ADR-0036 §8(а)) — он оказался сплошным: перебор банков и
сравнение периодов, ради которых делалась независимая сторона проверки, давились гейтом.

**Новое правило (`apps/orchestrator/tool_context.py`).** Почти-повтор — новый запрос, который не
приносит ни одного нового содержательного слова относительно выполненных ранее поисков сессии:
основы его содержательных слов — подмножество основ какого-то прежнего запроса
(`content_word_stems` ⊆). Содержательное слово — токен `normalize_search_query` без закрытого
списка служебных слов (`SEARCH_QUERY_SERVICE_WORDS`: предлоги, союзы, частицы, указательные и
личные местоимения; список не расширяется без отдельного решения), сведённый к основе приёмом
фиксированного префикса `_prefix_stem` из `apps/orchestrator/assertion_window.py` (тот же приём,
что уже закрыл дубли по значению: найди и переиспользуй, не изобретай второй): длина 7 и больше
-> 6 знаков, 5–6 -> 5, меньше 5 -> целое слово. Числа (годы, проценты) — содержательные слова как
есть. Отклоняются: перестановка слов, другие словоформы («Россия»/«России» -> `росси`),
добавление и удаление служебных слов, удаление слов, повтор при любой длине. Выполняется: любое
новое содержательное слово — другой месяц, год, актор, показатель, документ.

**Судьба порога и MIN_WORDS.** `SEARCH_QUERY_NEAR_DUP_JACCARD` и `SEARCH_QUERY_NEAR_DUP_MIN_WORDS`
удалены из кода как условия отказа (порог 0,6 и ловил дефект, и отмерял близость текстов;
MIN_WORDS выключал сравнение коротких повтором). Доля Жаккара считается и остаётся в payload
(`similarity`) и в заметке модели — вспомогательная величина: измеренные значения фикстуры (1.0,
0.833, 0.769, 0.923) закреплены тестами. Заметка модели: «Новых содержательных слов в нём нет …
прочитай найденное (research.fetch) или добавь другого актора, другой показатель или другой
период». Пустой запрос против выполненного поиска — тоже повтор (основ нет, ⊆ любому).

**Пересчёт фикстуры** (`fixtures-t7111/step-budget-2026-10-14.txt`, 54 поиска; нумерация —
порядковый номер поисковой строки `S[` внутри сессии): было 11 отказов, стало 6.

Отклоняются теперь:
1. [b357a12b] поиск 3 <- поиска 1: «Росстат реальные заработные платы 2026» против «…2026 год»
   (убрано слово, 0.833);
2. [b357a12b] поиск 4 <- 1: та же пара (ещё один повтор, 0.833);
3. [b357a12b] поиск 5 <- 2: буквальный повтор «независимые оценки реальных зарплат в России 2026
   hh.ru SuperJob аналитики банков» (1.0);
4. [b357a12b] поиск 6 <- 2: перестановка слов и падеж («Россия 2026 SuperJob hh.ru …») (0.769);
5. [43f799fa] поиск 6 <- 2: «Росстат инфляция в сентябре 2026 6,35% годовая» против «Росстат
   годовая инфляция сентябрь 2026 6,35% rosstat.gov.ru» — НОВЫЙ отлов (прежний Жаккарр 0.556 был
   ниже порога; перефраз без новых слов: `сентяб` схлопывает «сентябрь/сентябре», слова домена
   убраны);
6. [43f799fa] поиск 8 <- 4: «SberCIB 6,5% Альфа-Банк 7,5% прогноз инфляции 2026» — перестановка
   (1.0).

Отпущены (раньше отклонялись):
- [bdd9ef8d] поиск 4: «инФОМ инфляционные ожидания населения сентябрь 2026» (+период) — законно;
- [e6c8959b] поиски 5 и 6: «rosstat.gov.ru безработица май июль 2026 2,2%» и «…август 2026 2,2%»
  (новый месяц; было 0.700/0.667) — законно;
- [a6108aea] поиск 2: «Росстат уровень безработицы в России август 2026 2,2» (было ровно 0.600) —
  законно;
- [a6108aea] поиск 4: «rosstat.gov.ru osn-09-2026.pdf безработица август 2026 2,2» (имя
  документа; было 0.636) — законно;
- [b357a12b] поиск 7: «…аналитики банков прогноз» (было 0.923) — ОЧЕВИДНЫЙ перефраз-повтор: одно
  новое слово «прогноз». Назван, не чинится: расширение правила (например, отклонение запросов,
  накрывающих прежний плюс 1–2 слова, не называющих нового актора/показателя/периода) требует
  отдельного решения пользователя.

**Тесты (было -> стало).** `tests/unit/test_search_query_hygiene_t7111.py`: 8 -> 12 случаев.
Переписанные: `test_named_constants_match_the_decision` ->
`test_service_words_are_a_closed_short_list_and_gate_constants_are_gone` (было: закреплены 0.6 и
4; стало: закрытый список служебных слов и отсутствие удалённых констант);
`test_salary_family_is_caught_with_the_measured_ratios` ->
`test_salary_copies_and_reorder_are_caught_extension_runs` (копия 1.0 и перестановка 0.769
отклонялись — отклоняются; `SALARY_EXTENDED`: было отказ 0.923, стало выполняется);
`test_month_shift_with_value_already_found_is_caught` ->
`test_month_shift_after_value_found_is_allowed` (было: 0.700/0.667 отклонены; стало: выполняются);
`test_short_queries_are_not_compared_and_cannot_collide` ->
`test_short_query_without_new_words_is_a_duplicate_too` (было: короткие не сравнивались; стало:
короткий повтор — отказ, законное уточнение новым словом — проход, пустой запрос — отказ).
Новые: основы слов (`test_content_stems_strip_service_words_collapse_forms_and_keep_numbers_whole`),
пары дефекта (`test_defect_pairs_period_and_actor_shifts_run`), удаление слова и служебные слова
(`test_removed_word_and_service_words_do_not_make_a_query_new`), закрепление пересчёта
(`test_fixture_recount_under_new_rule`). Без изменений по существу: нормализация, сравнение со
всеми выполненными, законные пары фикстуры (текст заметки обновлён).
`tests/scenario/test_explorer_query_hygiene_t7111.py`: 1 -> 2 теста; прежний — было: 4 отказа из
7, выполнено 3; стало: 3 отказа (копия 1.0, повтор без слова 0.833, перестановка 0.769), выполнено
4 («+прогноз» и акторы), шагов 8 (учёт не менялся); новый тест
`test_actor_switch_in_session_runs`: «ВТБ …» -> «Газпромбанк …» — 0 отказов, оба поиска
выполнены. Прочие тесты с повторами запросов зелёные без правок (`tests/scenario/test_two_sided_search_session.py`
— буквальный повтор в одной сессии отклоняется и старым, и новым правилом; повторы в разных
сессиях вне гейта).

**Краснота до реализации (detached worktree 3e3dd9b с копиями новых тестов).** Unit-файл —
ошибка сбора (`ImportError: cannot import name 'SEARCH_QUERY_SERVICE_WORDS'`). Оба сценарных
теста падают: в паре акторов старый код даёт 1 отказ вместо 0 (`assert 1 == 0`), в основном
сценарии — 4 отказа вместо ожидаемых 3. Репродукция дефекта модулем из дерева 3e3dd9b: отказы
0.714 (прошлый год), 0.750 (смена месяца), 0.714 (подмена актора), повтор 0.769; при этом
буквальный повтор «инфляция в России» старым гейтом НЕ отклонялся (MIN_WORDS).

**Проверки §6 перед коммитом `77d7297`:** ruff и mypy чистые; 2670 passed, 12 skipped (262.6 с;
было 2665+12, +5 тестов); 4 passed timing (47.6 с). Контейнеры и БД хоста до и после прогонов:
41 контейнер (`noezema_mig_*` 40, `noezema_tpl_*` 0) — без изменений.

**Пределы поправки (осознанные).** Формы, различающиеся в первых пяти-шести знаках
(«цена»/«цены», «год»/«года»), имеют разные основы — такие перефразы пропускаются; перефраз с
одним новым общим словом («+прогноз») пропускается (назван выше). Все пропуски — в безопасную
сторону: лишний поиск стоит шаг, но не блокирует линию исследования.

## T7.112 (2026-10-14): объявленная кодировка при нормализации, перечитывание испорченного текста и глаголы отчётности первоисточника (часть а); гейт почти-повторов по равенству основ (часть б) (дефекты, найденные менеджером на приёмке; случаи 4e7ba8c2 caliber.az и 73c33c72 стендовая сессия, дефект чтения finmarket.ru/news/6727171 из сессии 14095b8d; код и тесты — коммиты `2568f61` (а) и `9645658` (б); ADR-0029 дополнение, ADR-0036 §1б)

**Что чинилось.** Три дефекта одного блока атрибуции и один гейта оркестратора: (1) страница
caliber.az пересказывает Росстат глаголом «зафиксировал» — словарь шаблонов T7.85 таких глаголов
не знал, страница осталась `no_value_attribution`, живое решение целевого случая (значение
«2,1–2,2%» сентября 2026) не принято; (2) страница finmarket.ru/news/6727171 (сессия 14095b8d) —
тело windows-1251, charset только в meta-объявлении: прежняя нормализация декодировала всё как
UTF-8, сохранённый текст на 73% состоит из U+FFFD (8679 знаков из 11811; замер закреплён тестом),
и переатрибуция решала по этому мусору; (3) гейт почти-повторов T7.111a отклонял законное
сужение запроса: после широкого запроса про несколько банков сужение «Газпромбанк …» не
приносило «нового слова» и отклонялось — вторая линия независимых оценок ломалась формулировкой
(случай стендовой сессии 73c33c72). Случай 73c33c72 воспроизведён тестом по формулировке задачи:
фикстура той сессии локально не сохранилась, стенд не перезапускался (не разрешено).

**Часть а1 — закрытый порядок объявления кодировки** (`apps/research_proxy/normalization.py`,
ADR-0029 дополнение): параметр charset из Content-Type -> BOM -> HTML meta-объявление в первых
4096 байтах тела -> UTF-8 `errors="replace"`. Угадывания нет ни на шаге; применённая кодировка
пишется в цепочку преобразований (`decode:<имя>`), неизвестное имя — `decode:unknown:<имя>` при
декодировании UTF-8. Текст, хеши и прежние цепочки чистых UTF-8 страниц не изменились байт в
байт (`("fetch","normalize:html-text")`, `("fetch","normalize:decode-utf8")`) — сверено unit-тестами
и прогоном решений по всем корпусам репозитория; изменений решений от самой а1 нет (все изменения
приведённой ниже таблицы дают глаголы а3). Метка метода нормализации при этом та же
(`noezema-normalize-v1`): изменился способ чтения, а не формат хранения.

**Часть а2 — перечитывание испорченного текста при переатрибуции** (`reattribution.py`): если
доля U+FFFD в сохранённом нормализованном тексте выше `CORRUPT_TEXT_REPLACEMENT_SHARE` = 0,01
(на два порядка меньше живой меры finmarket 0,73 — порог отделяет реальный брак от единичных
символьных дырок), артефакт оригинала доступен и закрытый порядок (а1) объявляет кодировку
оригинала не-UTF-8, решение принимается по тексту, перечитанному из сохранённых байтов
оригинала. Сохранённые артефакты, их хеши, строки улик и наблюдений не перезаписываются —
закреплено тестом (`store.get(...)` сверяется байт в байт, `artifact_chunks.content_hash` не
меняется); меняются только решения атрибуции. Перечитывание работает для обеих команд
переатрибуции (страничной и `--evidence-level`), повторный прогон пуст (решение помечается
версией метода, предикат «метка ≠ текущий метод» не тронут). Честная неполнота: страницы,
ошибочно объявленные UTF-8 в заголовке и без пригодного meta-объявления, не чинятся —
подмена кодировки без объявления не помогла бы, угадывания нет. Миграции нет: sha256 оригинала и content_type уже
записаны в журнале fetch (payload `RESEARCH_FETCH_COMPLETED`) и строке источника (metadata).

**Остаточный открытый вопрос (этой правкой не закрыт).** Перечитанная живая страница finmarket
в целом попадает под уже существующее вето документа `own_assessment` ветки значений: disclaimer
комментариев «…может не совпадать с мнением редакции ИА „Финмаркет“…» матчится шаблоном
`не совпада[а-яё]*`, а заголовок чужой новости «Фондовая Америка демонстрирует противоречивый
настрой» — ложным срабатыванием `противореч[а-яё]*`; вето считается по всему документу до
pairing (`apps/research_proxy/value_attribution.py`, ветка `_OWN_ASSESSMENT` strict-режима;
шаблоны введены T7.85b, в этой задаче не менялись). Целевой случай приёмки (caliber) закрыт;
для улик finmarket требуется отдельное решение о границах постраничного вето (разбор по
фрагментам документа) — названо, не чинится.

**Часть а3 — глаголы отчётности в закрытом словаре и сужение разночтения.** В
`ATTRIBUTION_TEMPLATES` добавлены `зафиксировал[а-яё]*`, `обнародовал[а-яё]*`,
`опубликовал[а-яё]*`, `отчитал(?:ся|ась|ись)`. Правило пары не ослаблено ни на пункт: у новых
шаблонов те же требования измеренного числа и алиаса словаря в окне одного фрагмента (120
знаков). Настоящее время и причастия не добавлены; вето чужого оценщика и маска сайтов-оценок
не тронуты; голое
упоминание без значения («аналитики опубликовали прогноз 2,2%») — прежний честный отказ. Новый
глагол вносит в строгий режим фрагменты, называющие двух словарных первоисточников (в корпусах
таких мест ровно два, оба caliber): разночтение разрешается существующим правилом владения
числом (T7.109 б2, `_nearest_primary_keys`) как сужение кандидатов внутри строгой ветки —
значение получает алиас, чьё ближайшее собственное число и есть значение утверждения; владельцев
двое — `ambiguous_primaries` без изменений. Тесты-инварианты T7.109 (оба первоисточника без
ближайшего числа, два прогнозиста) остались разночтениями; зеркальный случай «Росстат опубликовал
оценку 7,5% … Минэкономразвития опубликовал прогноз 7,5%» остаётся отказом. Ложных склеек свип
не нашёл ни в одном корпусе. Версии: `host-source-attribution-v7` -> v8,
`host-value-attribution-v10` -> v11; пины в 21 тестовом файле (28 литералов) переведены на новые
версии с комментариями эпохи — по существу прежние закреплённые решения ими не менялись.

**Изменения решений (свип «old vs new»: деревья 1fd0d29 и HEAD на одинаковых фикстурах, 36
страниц и 792 пары страница x утверждение).** Все изменения — на странице caliber.az; корпуса
t785 (окно публикации), t7101 (самопубликации), t7107/t7108 (библиография), t7109 (диапазоны) и
испорченная фикстура finmarket: ноль изменений решений (их тесты краснели только на пинах версий).

| ряд | было | стало |
|---|---|---|
| страница caliber.az (целиком) | `no_value_attribution` | `derivative`/rosstat |
| «Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2%.» x caliber | `no_value_attribution` | `derivative`/rosstat, pairing `value_and_primary_in_fragment` (строгая пара; целевой случай) |
| «ООН состоит из 193 государств-членов.» x caliber | `no_value_attribution` | `derivative`/rosstat — существующая семантика утверждения без измеренных значений (пустое множество значений: фильтр по значениям не участвует, хватает атрибутированных ключей); правило не менялось, отмечается честно |
| 20 утверждений x caliber (прогнозы аналитиков, ожидания, комментарии ЦБ и т.п.) | `no_value_attribution` (отказ страницы целиком каскадом) | `value_not_attributed` — отказ по каждому утверждению; в том числе «…в августе 2026 года … 2,2%» остаётся отказом: составной диапазон 2,1–2,2 не равен голому 2,2 (фильтр T7.109a жив) |

Итого 22 пары из 792 и 1 страница из 36. Прежде закреплённые тестовые строки, меняемые новым
шаблоном напрямую: `tests/unit/test_publication_window_veto_t785c.py` — 2 контрольные строки
оконного режима («Росстат опубликовал наблюдение: инфляция 15,2%», «Банк России зафиксировал
оценку … 15,2%»): было отказ окна, стало строгая пара `value_and_primary_in_fragment`;
`tests/unit/test_publication_window_veto_t785b.py` — 1 строка (`Согласно опубликованному Банком
России обзору… зафиксировал … 14,5%`): было `retelling_refused`, стало строгая пара cbr. Остальные
строки этих файлов (без новых глаголов) не изменились.

**Часть б — равенство основ вместо подмножества** (`apps/orchestrator/tool_context.py`,
ADR-0036 §1б): отказ только когда множество основ содержательных слов нового запроса равно
основам выполненного ранее запроса сессии; сужение и расширение законны; перестановка,
словоформы, буквальная копия и запрос без содержательных слов — прежние отказы. Слова «год»,
«года», «году», «годы», «г» добавлены в `SEARCH_QUERY_SERVICE_WORDS` (обоснованное решение в
составе пункта б; расширение списка §1а требовало отдельного решения пользователя): период в
запросе несёт измеренное число, слово — связка. Без него отказ ослаб бы в опасную сторону: пары
«Росстат реальные заработные платы 2026 год» -> «…2026» (буквальная копия без слова) не имели бы
равных основ и прошли бы.

**Пересчёт фикстуры** `fixtures-t7111/step-budget-2026-10-14.txt` (54 поиска; в скобках доля
Жаккара — справочно): было 6 отказов (T7.111a), стало 5. Отклоняются: [b357a12b] 3 <- 1 и 4 <- 1
(копия без «год», 0,833 обе); 5 <- 2 (буквальная копия, 1,0); 6 <- 2 (перестановка и падеж,
0,769); [43f799fa] 8 <- 4 (перестановка, 1,0). Освобождён один: [43f799fa] 6 <- 2 (0,5) —
«Росстат инфляция в сентябре 2026 6,35% годовая» против выполненного «Росстат годовая инфляция
сентябрь 2026 6,35% rosstat.gov.ru»: в прежнем запросе есть лишние токены домена, основы не
равны — по новому правилу это сужение, законно. Полные списки пар: ADR-0036 §1а (11 -> 6) и §1б
(6 -> 5).

**Тесты (краснота до реализации — detached worktree `1fd0d29` с копиями новых и изменённых
тестов).** Unit: `tests/unit/test_encoding_normalization_t7112.py` (10 случаев) — ошибка сбора
(`ImportError: cannot import name 'META_CHARSET_WINDOW_BYTES'`) — закрытого порядка не было;
`tests/unit/test_attribution_reporting_verbs_t7112.py` (9) — 5 целевых случаев красные (страница
caliber не атрибутирована, строгая пара не находится, сужение двух первоисточников не
разрешается); `tests/unit/test_search_query_gate_equality_t7112.py` (5) — 3 красных (сужение
отклонялось, расширение с потерей слова отклонялось, служебных слов года не было). Изменённые
прежние тесты краснеют ровно закрепленными правками: `test_search_query_hygiene_t7111.py` —
3 переписанных случая; `test_publication_window_veto_t785c.py` — 2 строки;
`test_publication_window_veto_t785b.py` — 1 строка + тест версии метода;
`test_reattribute_value_range_t7109.py` — только пин v11. Scenario:
`tests/scenario/test_reattribute_encoding_t7112.py` (2) — оба красные: старый код решает по
испорченному тексту без перечитывания (сухой прогон не даёт решения) и без глагола не находит
решение даже на чистом контроле; `test_explorer_query_hygiene_t7111.py` — новый тест сужения
красен (1 отказ вместо 0 выполненных сужений).

**Тестовый учёт.** Новые тесты: +10 unit (кодировка), +9 unit (глаголы), +5 unit (гейт),
+2 scenario (перечитывание), +1 scenario (сужение) = +27. Полная §6 перед коммитом `2568f61`:
ruff и mypy чистые, 2697 passed, 12 skipped (241,6 с; было 2670+12), timing 4 passed (47,7 с);
перед коммитом `9645658` — те же числа (234,7 с + 47,7 с). Защитный тест омоглифов T7.110 зелёный
во всех прогонах (новые кириллические шаблоны проверялись и прекоммитной проверкой «2б»).
До коммита (а) на хосте: 32 контейнера; в тестовом кластере БД `noezema_mig_*` 40,
`noezema_tpl_*` 0; после — те же числа (число фикстура-утечек не выросло; прирост прошлых
прогонов убирался штатным teardown'ом).

**Гигиенические заметки.** Фикстура caliber (`tests/fixtures/t7112/`) содержит дословный
заголовок с опечаткой самого сайта «Cамые читаемые» (латинская «C», U+0043) — сохранён байт в
байт (фикстура = нормализованный текст живой страницы; правка сменила бы хеш и решения):
единственное кириллица↔латиница соседство в диффе части а, кроме закреплённого «СберCIB» и
regex-границ `\b` в новых шаблонах (тот же синтаксис, что у прежних шаблонов словаря).
Нормализация не делает статистических догадок о кодировке: таких механизмов в модуле нет вовсе.

**Границы задачи.** Миграций нет, `AuditEventType` не расширялся, сохранённые артефакты/хеши/
улики/аудит не перезаписывались; pairing и вето не ослаблены; промпты и конфигурации (config-v26)
не тронуты — гейт живёт в оркестраторе, как и прежде; учёт шагов не изменён (отказ по-прежнему
стоит шаг, новых счётчиков нет); `ARCHITECTURE.md` не тронут.

## T7.113 (2026-10-14): окрестность числа для вето собственной оценки; плашка посетителей сайта и форма «отчиталось»; диапазонное значение в гейте связей куратора (решение пользователя 2026-10-14 «A + C, плюс пункт 2»; случаи `7cfbe942` и `4e7ba8c2` стендовой приёмки, живая страница finmarket.ru/news/6727171; код и тесты — коммит `f177ce5`; ADR-0029 дополнение, ADR-0034 §10)

**Что чинилось.** Страничный детектор T7.112 перечитал finmarket.ru/news/6727171 честно, но
обе цели приёмки оставались недостижимы: (1) утверждение `7cfbe942` «безработица … 2,2% по
данным Росстата» на странице-пересказе глохло в `own_assessment` — вето ветки значений считалось
по всему документу и срабатывало на плашке комментариев (1361 знак от вхождения 2,2%) и заголовке
чужой новости «…противоречивый настрой» (2506 знаков); (2) связь `supports` утверждения `4e7ba8c2`
«…составил 2,1–2,2% по данным Росстата» с той же страницей проходила гейт T7.108 по одному числу
«2,2%» — связью подтверждалась половина диапазонного утверждения. Плюс закрытый словарь плашек
не знал наблюдаемой формы плашки посетителей (маскировка T7.107 на неё не распространялась), а
шаблон отчётности — средней формы «отчиталось».

**Часть (а) — окрестность значения** (`apps/research_proxy/value_attribution.py`, ADR-0029
дополнение). Вето маркеров `OWN_ASSESSMENT_MARKERS` в ветке значений действует только если
вхождение маркера на маскированном тексте пересекается с окрестностью [старт−400, конец+400] хотя
бы одного вхождения значения утверждения (`_measured_value_spans`; диапазон — один span; конец
диапазона отдельной окрестности не даёт). Константа `OWN_ASSESSMENT_PROXIMITY_CHARS` = 400 —
существующая `PUBLICATION_WINDOW_CHARS`, новых числовых порогов нет; 400 стоит между настоящими
случаями окна публикации (225 знака, тесты T7.85a не изменены) и ложными маркерами finmarket
(1361 и 2506). Границы неизменения: утверждение без измеренного значения — прежнее вето по всему
основанию; ни одного вхождения значения на странице — вето нет; маркер хотя бы в одной окрестности
— вето по всем значениям. Оба места ветки значений перечитаны одинаково: основное
(`attribute_value_in_fragment`, вызов `apps/research_proxy/value_attribution.py:1478`) и
библиографический режим (условие (v) `_bibliography_known_producer_decision`, вызов
`apps/research_proxy/value_attribution.py:1153`); хелпер `_own_assessment_veto` —
`apps/research_proxy/value_attribution.py:858`, константа `OWN_ASSESSMENT_PROXIMITY_CHARS` —
`apps/research_proxy/value_attribution.py:289`. `_veto_zones`
(окно публикации T7.85b/c) не тронут. Детектор страниц решает по всему документу по-прежнему.

**Часть (в) — закрытый словарь плашек и глагол отчётности** (`apps/research_proxy/source_attribution.py`,
ADR-0029). В `SITE_DISCLAIMER_PHRASES` добавлены ровно две конструкции (`source_attribution.py:361–364` константы частей, `:374–375` сами конструкции):
наблюдаемая форма «мнение (посетителей сайта|читателей|пользователей|комментаторов) … может не
совпадать с мнением (редакции|автора|владельца сайта|администрации)» и её зеркало; вставка между
частями ограничена (`[^.\n]{0,80}?` — без точки и перевода строки; наблюдаемая вставка finmarket
— 53 знака). В `ATTRIBUTION_TEMPLATES` форма отчётности дополнена средней формой:
`отчитал(?:ся|ась|ись|ось)` (`source_attribution.py:270`). Ничего больше в словари не добавлено; маскировка — по
прежнему только закрытый список равных по длине пробелов, позиции текста сохраняются. Страничный
метод v8 НЕ поднят: проверено тремя способами — (1) Unicode-скан всех `tests/fixtures/**` не
содержит ни «отчитал», ни «может не совпадать» вне уже решённых мест; (2) unit-прогон: ни одного
отказа по страничным решениям (единственный dict-pin обновлён расширением); (3) свип ниже:
ни одного изменённого страничного решения. Закреплённые тесты: пин словаря
`test_disclaimer_and_bibliography_marker_lists_are_closed_pinned` расширен двумя новыми
литералами (списки по-прежнему закрыты и пинятся побайтово).

**Часть (2) — диапазон в гейте связей** (`apps/orchestrator/curator_rejections.py:212–217` цикл
гейта; помощники `claim_ranges_found_in_texts` — `apps/research_proxy/value_attribution.py:788` и
`claim_range_tokens` — `:831`,
ADR-0034 §10 дополнение). Одиночные значения claim'а гейт читает по-прежнему (T7.108a, поведение
байт в байт). Диапазонное значение подтверждает связь только целиком: тот же диапазон как одно
значение (эквивалентность форм T7.109/T7.109a) ИЛИ оба конца отдельными самостоятельными числами
(таблица «мин./макс.», единица со стороны текста не требуется). Одна граница диапазона связь не
подтверждает: стендовая связка 4e7ba8c2 ↔ finmarket снимается прежней байтово закреплённой
причиной; снятие всех связей -> отказ операции -> один раунд исправления §9 (C) — прежние.

**Версии.** Значения: `host-value-attribution-v11` -> v12 (изменено поведение ветки вето и
гейтового помощника). Страницы: `host-source-attribution-v8` без изменения (см. выше).
Переатрибуция перечитывает записи прежних эпох штатным предикатом «метки нет ИЛИ метка ≠
текущий метод»; повторный прогон пуст (сценарные тесты переатрибуции перепинены номером).

**Свип old->new (3fba6d3 -> HEAD, тот же метод, что T7.110/T7.112).** 36 страниц всех фикстурных
корпусов (t785, t7101, t7109, caliber.az, finmarket по нормализации T7.112) × 24 утверждения
(корпусные + целевые формулировки `7cfbe942`/`4e7ba8c2`) = 864 пары; скрипт
`t7113-keep/sweep7113.py`, результаты `t7113-keep/out-old.json` / `out-new.json`. Страничных
изменений: 0. Изменений значение-level: 46 (23 на finmarket, 23 на странице `t7101/9b74f6f6`
(инфляционный комментарий), 34 страницы идентичны байт в байт). 43 из 46 — переименование отказа
(`own_assessment` -> `value_not_attributed`: страничного pairing значения этих утверждений не было
и до правки; карточек ни раньше ни позже не создаётся). 3 — целевые склейки finmarket (значение
2,2% с атрибуцией Росстата в одном фрагменте -> `derivative`/rosstat). Ни одно прежнее
положительное решение не отменено. Утверждения без измеренного значения на обеих страницах
(«ООН состоит из 193 государств-членов») сохранили прежнее вето по всему основанию — прямое
подтверждение границы (1). Ложных склеек правка не порождает.

| страница | утверждение (сокращено до 60 зн.) | было | стало |
|---|---|---|---|
| t7101/9b74f6f683a2 | Аналитики SberCIB прогнозируют инфляцию в России на конец 20… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Аналитики Альфа-Банка прогнозируют инфляцию в России по итог… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Банк России в июле 2026 года повысил прогноз инфляции на 202… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | В информационно-аналитическом комментарии Банка России «Инфл… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | В пресс-релизе Банка России (event id 28251) годовая инфляци… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Годовая инфляция в России по итогам 2025 года (декабрь 2025 … | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Евгений Егоров из ACI Russia оценивает, что цены вырастут на… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Инфляционные ожидания населения России в сентябре 2026 года … | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Инфляция в России по данным Росстата составляет 2,2%. | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Консенсус-прогноз аналитиков по годовой инфляции в России на… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Медианная оценка годовой наблюдаемой инфляции населением Рос… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Наблюдаемая населением годовая инфляция в России в декабре 2… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Наблюдаемая населением годовая инфляция в России в сентябре … | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Независимые оценки Сбера/СберCIB совпали с официальной цифро… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Официальная годовая инфляция в России по итогам 2025 года (д… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Прогноз Банка России по годовой инфляции в России на 2026 го… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Прогноз аналитиков по годовой инфляции в России на конец 202… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Софья Донец из Т-Инвестиций оценивает, что повышение НДС доб… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Уровень безработицы в России в августе 2026 года составил 2,… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Уровень безработицы в России в сентябре 2026 года составил 2… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Уровень безработицы в России в сентябре 2026 года составил 2… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Уровень безработицы в России по данным Росстата составляет 2… | собственная оценка | отказ (нет приписки) |
| t7101/9b74f6f683a2 | Фактическая годовая инфляция в России по итогам 2025 года (5… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Аналитики SberCIB прогнозируют инфляцию в России на конец 20… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Аналитики Альфа-Банка прогнозируют инфляцию в России по итог… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Банк России в июле 2026 года повысил прогноз инфляции на 202… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | В информационно-аналитическом комментарии Банка России «Инфл… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | В пресс-релизе Банка России (event id 28251) годовая инфляци… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Годовая инфляция в России по итогам 2025 года (декабрь 2025 … | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Евгений Егоров из ACI Russia оценивает, что цены вырастут на… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Инфляционные ожидания населения России в сентябре 2026 года … | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Инфляция в России по данным Росстата составляет 2,2%. | собственная оценка | производная/rosstat |
| t7113/finmarket | Консенсус-прогноз аналитиков по годовой инфляции в России на… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Медианная оценка годовой наблюдаемой инфляции населением Рос… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Наблюдаемая населением годовая инфляция в России в декабре 2… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Наблюдаемая населением годовая инфляция в России в сентябре … | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Независимые оценки Сбера/СберCIB совпали с официальной цифро… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Официальная годовая инфляция в России по итогам 2025 года (д… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Прогноз Банка России по годовой инфляции в России на 2026 го… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Прогноз аналитиков по годовой инфляции в России на конец 202… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Софья Донец из Т-Инвестиций оценивает, что повышение НДС доб… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Уровень безработицы в России в августе 2026 года составил 2,… | собственная оценка | производная/rosstat |
| t7113/finmarket | Уровень безработицы в России в сентябре 2026 года составил 2… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Уровень безработицы в России в сентябре 2026 года составил 2… | собственная оценка | отказ (нет приписки) |
| t7113/finmarket | Уровень безработицы в России по данным Росстата составляет 2… | собственная оценка | производная/rosstat |
| t7113/finmarket | Фактическая годовая инфляция в России по итогам 2025 года (5… | собственная оценка | отказ (нет приписки) |

**Изменённые прежние тесты (ослаблений нет).** 20 литералов версии значения
`host-value-attribution-v11` -> `host-value-attribution-v12` в 19 файлах (8 unit:
`test_attribution_fixtures_t785.py:222`, `test_attribution_range_t7109a.py:202`,
`test_bibliography_known_producer_t7107.py:305`, `test_expectation_verb_t7110.py:194`,
`test_publication_window_veto_t785a.py:214`, `test_publication_window_veto_t785b.py:302`,
`test_relay_forecast_t798.py:252`, `test_value_attribution.py:115`; 11 scenario:
`bibliography_pairing_order_t7108:41`, `bibliography_session_t7107:43`,
`reattribute_bibliography_t7107:56`, `reattribute_encoding_t7112.py:193`,
`reattribute_pointer_clear_t798:331`, `reattribute_stale_records:74`,
`reattribute_value_range_t7109:245,254`, `self_publication_session_t7104:125`,
`self_publication_t7101:57`, `value_attribution_levels:291`, `value_attribution_session:121`)
— только номер версии; исторические метки эпох (`v1`…`v8` в reattribute/badge-тестах) не тронуты.
Dict-pin плашек расширен двумя литералами (замок усилен, не ослаблен). Тесты с прямыми
проверками на
`own_assessment`, `_veto_zones`, публикационное окно, `claim_values_found_in_texts` — без
изменений; ни один прежний тест не удалён (учёт ниже даёт ровно +19).

**Краснота до реализации** (detached worktree `/tmp/wt7113` на `3fba6d3`, копии новых тестов; импорт проверен: `apps.research_proxy.value_attribution.__file__` указывает
на worktree, метод v11). Полный файл `test_veto_proximity_t7113.py` — ошибка сбора
(`ImportError: cannot import name 'OWN_ASSESSMENT_PROXIMITY_CHARS'` — окрестности не было);
копия без пина версий: 3 красных (finmarket 2,2% и сайдбар-шум решались `own_assessment`,
диапазонное утверждение — `own_assessment` вместо `value_not_attributed`), гарды (истинная оценка
рядом, вето без значения, страничный детектор finmarket) зелёные на обеих сторонах.
`test_disclaimer_mask_and_reporting_verb_t7113.py`: 3 красных (плашка и зеркало не маскировались,
«отчиталось» пары не давало). `test_range_link_gate_t7113.py`: 2 красных (связь по одной границе
прежнего кода оставалась, включая живую фикстуру finmarket); гарды одиночных значений — зелёные
на обеих сторонах. Копии лежат в `t7113-keep/`, worktree удалён после прогонов.

**Тестовый учёт.** Новые тесты: +7 unit (окрестность), +5 unit (плашки/глагол), +7 unit (гейт
диапазонов) = +19. Полная §6 перед коммитом кода: ruff и mypy чистые, `2716 passed, 12 skipped (240,5 с; было 2697+12)`, `timing 4 passed (47,8 с)`.
До коммита на хосте: 41 контейнер, в тестовом кластере БД `noezema_mig_*` 40, `noezema_tpl_*` 0;
после — те же числа.

**Гигиенические заметки.** Прекоммитная проверка текста ловила четыре латинских омоглифных
вкрапления в первые черновики новых комментариев (подмена кириллической `у` латинской `y` и
три чисто латинских вкрапления — `kontrol`, `introduces`, `lista`) — все исправлены до
стейджинга; ещё четыре чисто кирилличные опечатки в комментариях (не омоглифы) исправлены
до коммита 1.
В итоговом диффе кода новых соседств кириллица↔латиница нет (regex-границы `\b` —
тот же синтаксис прежних шаблонов), единственный закреплённый случай «СберCIB» встречается только
как цитата корпусного утверждения в таблице свипа этого раздела. Защитный тест омоглифов T7.110
зелён во всех прогонах.

**Границы задачи.** Миграций нет, `AuditEventType` не расширялся, сохранённые артефакты/хеши/
улики/аудит не перезаписывались; `_veto_zones`, публикационное окно, relay-гейт и страничное вето
не тронуты; промпты и конфигурации (curator-v15/config-v26) не тронуты — гейт живёт в
оркестраторе; словарь записей первоисточников не расширялся; `ARCHITECTURE.md` не тронут.

## T7.114 (2026-10-15): производитель улики — кандидат первоисточника операторского спора (решение пользователя 2026-10-15 «вариант 1»; случай `4e7ba8c2`: caliber.az, rosstat.gov.ru и finmarket.ru/news/6727171 из приёмки стенда; код и тесты — коммит `49f4cfb`; дополнение ADR-0031 §9)

**Что чинилось.** Спор `claim-dispute --primary https://rosstat.gov.ru/ --retelling
http://www.finmarket.ru/news/6727171` по утверждению «Уровень безработицы в России в сентябре 2026
года составил 2,1–2,2 % … Росстат» (E3 «Проверено») отказывал `dispute_source_not_found`:
первоисточник искался только среди источников улик утверждения, а Росстат был уже записан хостом
как производитель улики caliber (запись значения `value_attribution`), то есть стоял в графе знания
утверждения, но не был уликой. Факт «finmarket пересказывает Росстат» оставался за пределами
единственного санкционированного lever'а оператора (§11.3).

**Решение.** Кандидатами первоисточника, кроме источников улик, становятся производители, к которым
хост уже приписал улики этого утверждения: страничный указатель `sources.parent_source_id` и запись
значения `evidence.scope.value_attribution.parent_source_id` (любая версия метода — по маркеру формы
`host-value-attribution-v1`, прежние записи читаются, урок T7.85(5)). Это не произвольная страница:
факт производности уже записан хостом. Роль ПЕРЕСКАЗА не изменилась — только источник собственной
улики утверждения.

**Как реализовано (коммит 1).** `resolve_dispute_pair` получил параметр `producer_sources`;
кандидаты первоисточника — улики и производители с дедупликацией по строке `sources` (тот же
источник в обеих ролях считается один раз; две разные строки с одним адресом — прежний отказ
«неоднозначно»). Читатель `claim_producer_sources` (`packages/memory/dispute.py`, SQL по
`evidence`/`sources` + jsonb-разбор записей значения, guard на битый UUID) перечисляет обе формы
указателя. Отказ «уже сгруппированы» для производителя: его группа в снимке независимости
действующей оценки (`source_independence_snapshot`) читается по уликам этого утверждения, которым он
записан родителем (сам производитель в `source_independence_members` строки не имеет — участники
только улик); группировщик склеивает детей общего родителя, поэтому производная группа обычно ровно
одна; ноль или несколько групп — отказа нет (fail-open, согласование сделает пересчёт после каскада).
`_uri_matches`, основание коррекции (артефакт прочтения пересказа), строка коррекции, каскад,
повтор по натуральному ключу, отмена и вопрос оператору — без изменений. Витрина: в provenance
добавлено только поле `dispute_producers` (read-only подсказка, тем же запросом), форма спора
перечисляет производителей в списке первоисточника, пересказ — только улики; подписан отказ
`dispute_source_not_found` и подсказка `--primary` в CLI.

**Повтор случая на тестовой базе (т1–т6 сценария).** Репродюсер формы случая (`external_fact`; у
стендового `temporal_fact` тот же механизм оценки): было — 2 группы независимости, E3/`supported`
(витрина «Проверено»); спор пересказа finmarket с производителем Росстата принимает коррекцию
merge (основание — артефакт прочтения пересказа), каскад и rules engine дают одну группу,
`insufficient_independence`, E1/hypothesis — «Проверено» уходит; отмена и пересчёт возвращают E3 и
две группы. Отказ whole-way проверен и после контрольного спора: абстрактный адрес и пересказ-
производитель не записывают ни одной действующей коррекции. «Уже сгруппированы» для родителя
пинится случаем, где оба пересказа уже приписаны одному производителю.

**Тесты и числа.** Новые: `tests/unit/test_claim_dispute_producers_t7114.py` (7) и
`tests/scenario/test_claim_dispute_producers_t7114.py` (8); на `c6da7fb` красными были 14 из 15
(зелёным остался только тест неизменности прежнего узкого разбора без производителей). Прежние
тесты T7.81 не изменены и зелёные; тестов меньше не стало. Полная проверка перед коммитом `49f4cfb`:
ruff чисто, mypy strict (packages apps hostctl) — 157 файла, образ sandbox на месте;
`pytest -n auto -m "not timing"`: 2731 passed, 12 skipped (прежде 2716 + 12; ровно +15 новых);
отдельный прогон `pytest -m timing`: 4 passed. Защитный тест омоглифов T7.110 зелёный; текстовые
проверки (1)/(2)/(2б) — чисто, соседств кириллица↔латиница в новом коде нет.

**Границы задачи.** Миграций нет; нового вида коррекции и нового `AuditEventType` нет (спор
пользуется существующей коррекцией merge и существующими событиями); `source_graph.py`, группировщик
`independence.py` и rules engine не изменены — расширен только выбор кандидатов пары и чтение уже
сгруппированного состояния; «пересказ — только улика утверждения» не ослаблен; `ARCHITECTURE.md`
не тронут; реальная база стенда `.92` не читалась и не писалась, активации конфигураций и push нет;
оценка по-прежнему назначается только rules engine.

## T7.115 (2026-10-15): опора на записанное знание из финального ответа исследователя (решение пользователя 2026-10-15 «да, готовь T7.115»; случай приёмки стенда `.92` `96a0c535`, вопрос `d2afe4a6`; код и тесты — коммит `d47f99c`; дополнение ADR-0032 §8)

**Что чинилось.** Финальный ответ исследователя на стенде цитировал записанное знание по id
(`c:<uuid>`), а карточка показывала 0 утверждений и ни одной связи «использовано из знаний»:
запрос куратора не содержит финальной rationale исследователя, и в разрешитель опоры
(T7.82, ADR-0032 §3) приходила только декларация куратора. Хост теперь читает честные ссылки
`c:<id>` и из самого финального ответа: карточка такой сессии показывает опертые утверждения,
даже когда куратор предложил 0 claims.

**Как реализовано.** `SessionContext.final_answer_rationale` — public_rationale шага `complete`
сохраняется ЦЕЛИКОМ в памяти сессии (аудит-обрезок [:500] и оконный сигнал `final_rationale_signal`
не пригодны: финальный ответ может быть длиннее окна, а ссылка не должна обрезаться; в БД ничего
нового не пишется — прецедент T7.82 часть а). Отклонённый COMPLETE (гейт покрытия T7.21) — не ответ,
не сохраняется. На границе куратора — чистая функция `_relied_from_answer` и регулярка
`_ANSWER_CLAIM_REF_RE` (форма `c:<полный uuid>` или `c:<hex-префикс не короче 8>`, границы слова,
висячий дефис не захватывается): разрешение ТОЛЬКО по контекст-паку — `resolve_claim_reference`
против `_pack_claim_ids`, ровно как у опоры куратора; неоднозначный префикс и несуществующий id —
честный отказ `answer[k]: ...`, а не догадка; id, который является операцией этой сессии
(перепроверка/склейка, включая конвертированные гейтом value-dup), в опору не дублируется.
Приоритет за куратором: его id уже в списке, потолок `RELIED_CLAIMS_LIMIT` общий и id куратора
считаются первыми. Результат объединяется в прежние ключи `relied_claim_ids` /
`relied_claims_rejected`; аддитивный ключ `relied_from_explorer` называет, что добавил ответ.
Промежуточные шаги не участвуют. Все ключи пишутся только непустыми: сессии без ссылок в финальном
ответе остаются байт в байт прежними (пинится и случай «ссылка есть только в промежуточном шаге»).
Целый отказ предложения (четыре гейта T7.105) по-прежнему `return 0, 0` без CLAIM_CREATED — опора
не записывается никуда: она живёт только в payload события фиксации; поведение не изменено
и зафиксировано в ADR-0032 §8.

**Повтор случая (тестовая база, тест 1).** Было: три строки знания записаны, куратор отвечает
пустым предложением, финальный ответ цитирует два полных id и 8-hex префикс — карточка «Ответ не
записан»: 0 claims, 0 «использовано из знаний» (на `7478935` ровно так, тест красный). Стало: та же
сессия — `relied_claim_ids` содержит все три id (префикс разрешён в полный), `relied_from_explorer`
перечисляет их как добавленные ответом; карточка показывает три строки «использовано из знаний» с
прежними бейджами действующей оценки. Знание при этом не изменилось: ни новой операции, ни строки
staging — опора остаётся меткой честности (нет `claim_reverified`, head прежний). Отказы не ломают
сессию: несуществующий id и неоднозначный префикс записаны в `relied_claims_rejected` (тесты 2–3);
перепроверка этой же сессии не дублируется в опоре (тест 5); опора переживает раунд исправления
куратора (тест 7).

**Тесты и числа.** Новые: `tests/scenario/test_relied_from_answer_t7115.py` (7) и
`tests/unit/test_relied_from_answer_t7115.py` (9); на `7478935` красными были 6 из 7 сценарных
(`KeyError: 'relied_claim_ids'` в payload, отсутствие строк карточки) и весь unit-модуль
(отсутствующий импорт `_relied_from_answer`); тест-гарант байтовой совместимости (ссылка только
в промежуточном шаге) зелёный на обеих сторонах — он и задуман как регрессия. Прежние тесты
T7.82 (`test_relied_claims.py`), T7.105/T7.106 не изменены и зелёные; тестов меньше не стало:
было 2731 passed + 12 skipped, стало 2747 passed + 12 skipped (ровно +16 новых); отдельный прогон
`pytest -m timing`: 4 passed. Полная проверка перед коммитом `d47f99c`: ruff чисто, mypy strict
(packages apps hostctl) — 157 файла, образ sandbox на месте; защитный тест омоглифов T7.110
зелёный; текстовые проверки (1)/(2)/(2б) — чисто.

**Границы задачи.** Нового `AuditEventType` и миграций нет; `CuratorProposal` не изменён (новый
источник id — хостовый код, модель не знает о правке); промпты, конфигурации, rules engine,
граница коммита, staging и карточка не тронуты (карточка читает опору из payload по-прежнему,
подписи те же); `ARCHITECTURE.md` не тронут; реальная база стенда `.92` не читалась и не писалась,
активаций конфигураций и push нет; при целом отказе куратора опора не записывается — прежнее
поведение, зафиксировано документом.

## T7.116 (2026-10-15): атрибуция «известный производитель упомянут на странице» (часть а); указатель называет строку только когда утверждение называет производителя (часть б); снятие упаковки «c:» со ссылок опоры (часть в) (решение пользователя 2026-10-15 «бери T7.116 со всеми тремя пунктами»; стенд session `53be7fd6`: случаи `c6729d4c…`, `85220305…`, `44bd1460-bb72-4521`; код и тесты — коммиты `594783b` (в), `adb3859` (а), `1983872` (б); дополнения ADR-0029 §21, ADR-0035 §21, ADR-0032 §9)

**Стендовые случаи.** (1) Утверждение `c6729d4c…` «Прогноз Банка России по годовой инфляции в
России на 2026 год составляет 6,0–7,0%»: улика interfax.ru attributed строгим режимом, пересказ
lenta.ru (цитата инвестбанкира Когана, внутри — «запланированного Банком России коридора в 6-7
процентов») остаётся неприписанным ни одним прежним режимом. (2) Утверждение `85220305…`
(мнение Когана) витрина подписывала «Опубликовано производителем» (Минфин) по одному якорю на
документ minfin.gov.ru — формулировка Минфина не называет. (3) `relied_claim_ids` куратора
содержал ссылки `c:44bd1460-bb72-4521` и `c:c6729d4c-72a7-4f74` — форма, которую хост сам
печатает в конспекте пака, — разрешитель отвечал `unparseable claim reference`.

**Часть в.** `resolve_claim_reference` (`packages/memory/service.py`) перед разбором снимает
внешние скобки и/или метку `c:` (пробелы по краям), ровно один раз; `c:c:<uuid>` остаётся
непарсируемым; содержимое скобок без `c:` — прежний отказ. Разбор после снятия прежний (T7.34).
Наборы ошибок, тексты, гейт `existing_claim_id` (T7.73) не тронуты. Тесты:
`tests/unit/test_claim_reference.py` 12 -> 19. Краснота до кода на `8ffb4c9` (семь тестов):
`test_c_prefixed_full_uuid_resolves`, `test_c_prefixed_truncated_uuid_resolves`,
`test_bracketed_pack_form_resolves`, `test_c_prefix_with_spaces_resolves`,
`test_c_prefix_is_case_insensitive_and_single_only`, `test_c_prefixed_ambiguous_prefix_still_refused`,
`test_c_prefixed_nonvisible_uuid_still_refused`.

**Часть а.** Новый последний режим цепочки значений (после strict -> refusal -> публикации ->
relay/nearest -> библиографии): pairing `known_producer_mentioned`, условия (i)-(vi),
предложная co-location области с потолком 800 знаков и гвардом одиночного значения T7.109a —
полный разбор ADR-0029 §21. Запись — строгая форма (родительский указатель на источник X);
коллекторы переатрибуции новое pairing-значение не читают.

Свип old-vs-new (worktree на `8ffb4c9`, 38 страниц фикстур × 25 утверждений = 950 пар решений,
штатные нормализация и детекторы страниц, сравнение только ветки значений). Первая версия
требовала co-location «где угодно на странице» — 9 изменений, из них 8 ложных склеек:

| страница × утверждение | доказательная строка страницы | решение |
|---|---|---|
| `t7116/lenta` × прогноз 6–7 | «выйдя за пределы запланированного Банком России коридора в 6-7 процентов» | ИСТИННОЕ, целевое: отказ -> `derivative` cbr/`known_producer_mentioned` |
| `t7113/finmarket` × прогноз 6–7 | «Morgan Stanley на 6% увеличил чужую прибыль»; сырьевой индекс «на 7%» | ложное, убито |
| `t785/sbercib_2025` × прогноз 6–7 | «троллейбусе (+6,0%)», «до −6%» из разных строк | ложное, убито |
| `t785/sbercib_2025` × 14,5 | базовая инфляция «с 14,5% до 11%» в строке без алиаса | ложное, убито |
| `t7101/8be762…` × прогноз 6–7 | чужие прогнозы Минэкономразвития/аналитиков в соседних строках | ложное, убито |
| `t7101/8be762…` × 14,5 | «Такой же была динамика базовой инфляции — с 14,5% до 11%» | ложное, убито |
| `t7101/9b74…` × прогноз 6–7 | «мировые цены … упадут на 7%» про другой предмет | ложное, убито |
| `t785/ria_2068393962` × прогноз 6–7 | «власти ожидали … ниже 6%» | ложное, убито |
| `t7101/34ec…` × 14,5 | значение 14,5 подтверждалось концом составного токена «14,5–14,6%» (прогноз средней ключевой ставки) | ложное, убито гвардом T7.109a |

Промежуточный прогон (предложные области без гварда одиночных) дал 2 изменения — целевое и
`34ec`; финальный код: ровно 1 изменение из 950 (целевое), страничные решения детектора на
всех 38 страницах идентичны во всех прогонах, ни одно прежнее положительное решение значений не
отменено. Принятые остаточные риски (числовая тождественность; пересказы мнений при записанном
производителе) — ADR-0029 §21.5.

Тесты части а: `tests/unit/test_known_producer_mentioned_t7116.py` (20) и
`tests/scenario/test_reattribute_known_producer_mentioned_t7116.py` (1, настоящие interfax/lenta:
атрибуция пересказа новым режимом, коллекторы его не забирают, идемпотентность); фикстуры
`tests/fixtures/t7116/`. Версия метода значений v12 -> v13 (`host-value-attribution-v13`,
страничный метод остаётся v8); пины версии обновлены в 20 тестовых файлах (11 scenario:
bibliography_pairing_order_t7108, bibliography_session_t7107, reattribute_bibliography_t7107,
reattribute_encoding_t7112, reattribute_pointer_clear_t798, reattribute_stale_records,
reattribute_value_range_t7109, self_publication_session_t7104, self_publication_t7101,
value_attribution_levels, value_attribution_session; 9 unit: attribution_fixtures_t785,
attribution_range_t7109a, bibliography_known_producer_t7107, expectation_verb_t7110,
publication_window_veto_t785a, publication_window_veto_t785b, relay_forecast_t798,
value_attribution, veto_proximity_t7113). Краснота до кода на `8ffb4c9`: unit-файл —
`test_lenta_retelling_attributes_by_new_mode`, `test_two_known_producers_on_page_are_ambiguous`,
`test_both_ends_of_range_confirm_separately`, `test_real_new_mode_requires_alias_and_value_co_location`,
`test_new_pairing_is_registered`, `test_value_method_version_bumped`; сценарий —
`test_known_producer_mentioned_mode_attributes_the_retelling_page` (первое падение v12 != v13).
Гварды прежних режимов зелёные на старом коде и не изменены (T7.107 библиография с тестом
`test_known_producer_named_only_outside_the_bibliography_lines_is_refused`, T7.109 nearest,
T7.109a формы чисел, T7.113 вето 400).

**Часть б.** Правило витрины и изменение подписей — полный разбор ADR-0035 §21. Изменённые
прежние подписи (было -> стало): `tests/unit/test_web_producer_view_t7100.py` таблица 21 строки —
`6d9a12ff` и `85220305` «Опубликовано производителем» -> «Подтверждено слабо» (колонка «было»
перебазирована на период после T7.100 / до T7.116; `6d9a12ff` — частичная отмена флипа T7.100,
`7cc2b578` остаётся подписан записью); `tests/scenario/test_self_publication_labels_t7101.py` —
набор «было» четыре подписи вместо пяти, флип `6d9a12ff` пинится отдельными утверждениями;
`tests/scenario/test_web_producer_badge_t7100.py` — две синтетические строки -> «Подтверждено
слабо» (карточка и три вида согласия), новый контрольный тест «формулировка называет — метка
прежняя» (6 тестов вместо 5); `tests/unit/test_web_producer_view_t7109.py` — строка без записи
называется якорем-родителем только при формулировке, называющей того же производителя (было: при
любой), разночтение self-записи и якоря — прежняя форма; там же исправлена историческая опечатка
«построчный» (было «построконечный»). Строка «чем подтверждено» (`evidence_producer_names`)
гейтом не проверяется: факт об якорях улик, не подпись утверждения. Полное правило —
`tests/unit/test_producer_view_page_pointer_t7116.py` (9). Краснота до кода на `8ffb4c9`: все 9
тестов файла (сигнатура без параметра формулировки) + красные строки таблиц/сценариев выше.

**Счёт тестов.** Полная проверка перед каждым коммитом зелёная: baseline до задачи — 2747 passed
+ 12 skipped (+4 timing); после — 2785 passed + 12 skipped (+4 timing), рост ровно на новые:
unit t7116 (20) + unit pointer t7116 (9) + сценарий reattribute t7116 (1) + 7 (claim_reference)
+ 1 (badge). Ослаблений прежних условий нет; изменённые прежние тесты — только пины версии (20
файлов) и подписи, которые меняет само правило части б (было/стало выше).

**Обоснованные попадания при текстовой проверке коммитов.** Фикстура
`tests/fixtures/t7116/lenta-kogan-20261008.txt` — побайтовая копия реальной страницы lenta.ru,
строка меню «Добавить сайт в доверенные источники …» — слово Google с последней «е»,
напечатанной кириллической буквой (дефект исходной страницы, сохранён как есть, на решения не
влияет); бренд «СберCIB» в
текстах стендовых утверждений файла `test_web_producer_view_t7100.py` — законное попадание по
закрытому перечню омоглиф-скана (тест `tests/unit/test_homoglyph_source_scan_t7110.py`, пункт
T7.102).

**Границы.** Промпты и конфигурации не тронуты; миграций нет; новых scope-ключей и подписей
`labels.py` нет (pairing — внутреннее значение, наружу выходит прежними строгой формой и метками
«Опубликовано производителем»/«Подтверждено слабо»); группировка, уровни и оценки не изменены;
`ARCHITECTURE.md` не тронут; push нет.

## T7.117 (2026-10-15): запись значения приоритетнее страничного указателя во входе группировки независимости; read-only предпросмотр смены групп и применение каскадом (решение пользователя 2026-10-15 «вариант 1»; дополнение ADR-0029 §22; коммиты `58e0828` (часть а), `97cdc39` (часть б))

**Правило (вариант 1).** Если по уликам утверждения по записям происхождения значения
(`evidence.scope.value_attribution`) записан ровно один эффективный родитель, во вход
группировщика независимости идёт он; `sources.parent_source_id` (страничный указатель) —
фолбэк, когда записи нет или они конфликтуют (консерватизм T7.78 сохранён). Приоритет
оснований в чистом группировщике, движок правил, пороги оценок, коррекции оператора (ADR-0031),
версии детекторов (страничный v8, значения v13) и алгоритм независимости окружения не тронуты.
Прежнее сочетание `s.parent_source_id or effective_parent.get(s.id)` (ADR-0029, «Решение (Б)»)
заменено; вариант C (снятие лгущего указателя переатрибуцией) остаётся в силе.

**Часть а.** `packages/memory/source_graph.py`: чистая сборка входа `grouping_arguments`
отделена от чтения БД `load_source_graph_assembly`; выбор родителя —
`assembly.effective_parent.get(node.source_id) or node.pointer_parent` (+117/−44). Узлы графа
включают родителей обеих природ: записанный родитель без единого страничного указателя делает
его узлом и склеивает детей разных доменов; «родитель-призрак» без строки `sources` узлом не
становится (прежнее поведение загрузчика). Проигрывание стендового случая настоящими детекторами
(`tests/scenario/test_value_over_pointer_priority_t7117.py`): было 2 группы (cbr+interfax |
lenta по Минфин-якорю), E3 supported 0.75; после записей переатрибуции T7.116 прогона —
стало 1 группа, база строки ленты `parent:<id страницы ЦБ>`, фактическая оценка правил
закреплена: E1 hypothesis (уверенность ниже); указатели строк не переписаны; run повтора
переатрибуции даёт DECIDED (0 изменений, 0 задач) — решение уже записано ранее.

**Часть б.** `packages/memory/independence_preview.py` + команда `noezemactl
independence-preview [--apply]` (CLI help — в docstring команды; отдельного списка команд в
README нет). Read-only по умолчанию: для каждого утверждения с действующей головой под
active config snapshot — «было» из **записанных** строк снимка независимости и оценки головы,
«стало» — пересчёт чистыми `group_source_graph`/`evaluate` на новом входе (env-группы из
записанного снимка окружения); строки смены родителя улики (указатель → запись). Если оценку
пересчитать без записи нельзя (нет правила типа, отказ `evaluate`), отчёт честно это называет
и продолжает показывать группы; выдуманных оценок нет. `--apply` — существующий каскад §11.3
(`apply_source_graph_change`: головы в pending + задачи переоценки) и батч
`run_reassessment_batch`; новых AuditEventType, миграций и собственных правок таблиц нет;
каскад — надмножество (объединение источников улик перечисленных утверждений). Идемпотентность:
после обработки задач повторный предпросмотр пуст; повторный `--apply` на пустом предпросмотре —
no-op; предпросмотр не меняет счётники восьми таблиц (включая `audit_events`).

**Тесты.** Новые: `tests/unit/test_grouping_inputs_priority_t7117.py` (6: т1 свёртка двух
групп в одну на чистом входе, т2 конфликт записей → фолбэк на указатель, т3 записи нет →
указатель, т4 коррекция оператора при новом приоритете не изменена, т6 указатель == запись →
вход неотличим, записанный родитель — узел графа);
`tests/scenario/test_value_over_pointer_priority_t7117.py` (1, проигрывание было→стало с
настоящими детекторами); `tests/scenario/test_independence_preview_t7117.py` (5: т5 перечисляет
ровно изменённое утверждение проигрывания и ничего не пишет; применение каскадом с рабочим и
идемпотентность; cli-контракт T7.59 (CliRunner, свой event loop, пустой повторный
предпросмотр, код 2 без URL); т2/т3 на сборке входа + включение родителя-узла). **Прежние
тесты не переписаны: тестов, закреплявших «страничный указатель приоритетнее», не найдено —
полный прогон на флипнутом коде зелёный без единого ослабления.**

**Счёт тестов.** Полная проверка перед каждым коммитом зелёная: baseline до задачи — 2785
passed + 12 skipped (+4 timing) на `99b4d1b`; после — 2797 passed + 12 skipped (+4 timing),
рост ровно на 12 новых (6 unit + 1 сценарий + 5 preview). Краснота до кода доказана на
`99b4d1b` в отдельном worktree (`/tmp/wt7117`, свой venv, `module.__file__` внутри worktree):
unit — ImportError новых имён, сценарий проигрывания — падение на «стало: одна группа» (две
группы E3 при прежнем приоритете), preview-файл — ModuleNotFoundError; worktree удалён после
доказательства.

**Границы.** `independence.py` (чистый группировщик), rules engine, пороги и grades, схемы и
миграции, детекторы атрибуции, промпты и конфигурации не тронуты; нового AuditEventType нет;
предпросмотр ничего не пишет (в том числе в реальную базу — на реальной базе команда не
запускалась); `ARCHITECTURE.md` не тронут; новых контейнеров и оставленных тестовых баз нет
(после прогонов: 41 контейнер, `noezema_mig_*` = 40, `noezema_tpl_*` = 0 — норма окружения);
push нет.

## T7.118 (2026-10-16): отчёт честности по записанному (read-only наблюдение) + контрольный набор вопросов (решение пользователя «только наблюдение: правил, промптов, карточки и порогов не трогать»; коммиты `93631fb` (часть 2), `1523dd2` (часть 3); документация — отдельный коммит)

**Инструмент.** `packages/memory/honesty_report.py` + команда `noezemactl honesty-report
[--since ISO] [--questions id,id] [--control-tag метка] [--json файл] [--baseline файл]`
(exit 0 — собран, 2 — нет критерия отбора/испорченные параметры/превышены потолки 100/400/400,
1 — baseline не читается или схема не совпадает). Только чтение: в модуле нет ни одного
INSERT/UPDATE/DELETE; тест т6 сверяет счётчики восьми ключевых таблиц (`questions`, `sessions`,
`claims`, `claim_assessments`, `claim_assessment_heads`, `evidence`, `sources`,
`source_independence_snapshots` и др.) до и после отчёта. Показывает: «Ответ есть / не записан»,
сессии (состояние, причина завершения, длительность, `model_runs`), утверждения корзинами
(созданные / переиспользованные / перепроверенные / опертые) с действующей оценкой активного
снимка (E-уровень, статус, причины из ленты, подпись о производителе), группы независимости E2+
(родитель по записи значения → страничный указатель → собственная публикация — та же очерёдность
входа, что T7.117) и шаги исследователя из `audit_events` репликами правил менеджера и ворот
(поиски официальные/прочие по константам хостов модуля, поиски без последующего чтения, отказы
401/403/429/таймаут против «свойств страницы», повторные заходы на отказавший хост, повторные
чтения того же содержимого, отказы обоих гейтов T7.111a/T7.112).

**Флаги-эвристики (не вердикты, «требует ручной проверки»).** Ф1 «E2+ при общем производителе»:
две записанные группы с одним эффективным родителем (пересечение родителей, либо родитель одной
группы — источник другой); ветка «текст группы называет производителя» не реализована —
потребовала бы атрибуционных детекторов из `apps/` (запрещено задачей). Ф2 «возможно не по
вопросу»: зеркало нормализации ворот T7.111a/T7.112 (`normalize_search_query`, `_prefix_stem`,
`content_word_stems`; тестом закреплено равенство зеркал и служебного списка); правило —
пустое пересечение значимых основ ИЛИ одна общая основа-якорь при 3+ значимых основах
утверждения; буквальная «доля пересечения» заменена якорным правилом, потому что доля ложит на
честных развёрнутых ответах (зарплатный ответ даёт пересечение 2/7) — отклонение зафиксировано
тестом и CONTROL-SET.md. Ф3: подпись «Опубликовано производителем (X)» при незаписанном в
формулировке X (двусторонняя сверка по основам; fail-closed, если у имени нет сравнимых слов);
ложное срабатывание «Росстат» vs словарь `apps/` закреплено unit-тестом как ограничение.
Сверка Ф3 по основам заменена в T7.118a (раздел ниже).

**Контрольный набор.** `docs/eval/control-set-v1.jsonl` (c1 зарплаты, c2 безработица,
c3 прогнозы банков против ЦБ, c4 инфляция сентября — официальная цифра, c5 простых чисел меньше
10000 ровно 1229 вычислением; тексты закреплены т9) + `deploy/dev-stand/control-run.sh`
(паттерн `control_t7111.sh`: оператор на `.92`, суффикс ` (контроль <метка>)`, приоритет 9,
wake_now, потолки 10 мин idle / 45 мин сессия, журнал ASKED/DONE/TIMEOUT, печать команды отчёта;
`bash -n` чист; shellcheck в агентской среде `.87` не установлен — третья проверка из трёх по
AGENTS не выполнена, зафиксировано честно). `docs/eval/CONTROL-SET.md` — как запускать, как
читать отчёт, флаги и их границы, таблица ожиданий по фикстурам приёмки (офлайн-расчёт теми же
чистыми функциями: Ф2 ложит на `c80b682d`/`a5560f1b`/`0a9e220d` и не ловит дрейф `344d0359`;
`c6729d4c` — действующая E1; `73c33c72` — 4 поиски без чтения из 7).

**Тесты.** Новые: `tests/unit/test_honesty_report_flags_t7118.py` (25: зеркала нормализации
против `apps/orchestrator/tool_context.py` и `assertion_window._prefix_stem`, чистые правила
Ф1/Ф2/Ф3, парсер даты, классификация отказов, хосты, суффикс метки, отказ чтения baseline);
`tests/scenario/test_honesty_report_t7118.py` (8: т1 пересказ первоисточника в двух группах → Ф1,
т2 честные разные производители → без Ф1, т3 безработица под вопросом о зарплатах → Ф2, т4 подпись
без названного автора → Ф3, т5 счётчики шагов по событиям «как их пишет исследователь» (реальные
AuditEventType), т6 read-only — счётчики таблиц равны, т7 `--control-tag` берёт только
окончивающиеся суффиксом, т8 CLI на scratch-БД: JSON-схема `t7118-honesty-report-v1`, таблица
«Было → стало», коды выхода); `tests/unit/test_control_set_t7118.py` (5: структура набора и
закрепленные тексты). **Краснота до кода:** в worktree на `944af01` оба новых файла падает
`ModuleNotFoundError: packages.memory.honesty_report` (команды `honesty-report` там тоже нет);
worktree удалён после доказательства.

**Счёт тестов.** Полная проверка зелёная перед каждым коммитом (§6; перед коммитами 2 и 3 один
прогон на неизменном дереве). Baseline до задачи на `944af01`: этап 1 — 2788 passed + 21 skipped,
этап 2 — 4 passed; финальный прогон: этап 1 — 2835 passed + 12 skipped, этап 2 — 4 passed.
Суммарно собранное выросло ровно на 38 новых тестов (25 + 8 + 5); перераспределение
passed/skipped внутри этапа 1 (9 тестов, зависевших от наличия sandbox-образа, выполнились) —
среда, не код: прежнее падение не воспроизведено ни одним тестом. `ruff` и `mypy` (159 файлов) чистые;
`test_web_homoglyphs_t7103.py` зелёный.

**Границы.** Новый модуль не пишет в БД (тестом), не создает миграций, enum-значений,
AuditEventType; промпты, конфигурации, rules engine, пороги, детекторы атрибуции, группировщик,
карточка ответа не тронуты; `ARCHITECTURE.md` не тронут; отчёт ни разу не запускался на реальных
базах (только тестовые scratch-БД); `control-run.sh` агентом не запускался (синтаксическая
проверка + парсинг JSONL офлайн); стенда `.92` задача не касалась; новых контейнеров и остаточных
тестовых БД нет; push нет.

## T7.118a (2026-10-16): Ф3 — имя названо только всеми значимыми основами имени; команда отчёта на dev-стенде (дефекты приёмки `.92`)

**Дефект 1 (Ф3, реальные данные стенда).** Прежнее правило `_name_mentioned` — «достаточно одной
совпавшей основы имени» — глушило флаг: подпись «Опубликовано производителем (Банк России)»
не срабатывала там, где имени в формулировке не было вовсе: хватало слова «России», а оно есть
почти в каждой формулировке стенда. Реальные пропуски: `cc75e97f` («Инфляционные ожидания
населения России в сентябре 2026 года составили 14,2% (по данным опроса ООО «инФОМ»).») и
`391c4388` («Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила
14,5% …») — обе подписи «Банк России». Корректный случай `7cc2b578` (подпись «Росстат», имени
в формулировке нет → флаг) сохранён.

**Правка.** `packages/memory/honesty_report.py`: `_name_mentioned` сравнивает ВСЕ значимые
основы имени (тот же двусторонний префиксный допуск падежей, вспомогательная `_same_word_stem`);
значимые — без чисто числовых токенов и без общей основы-якоря страны: `_COUNTRY_ANCHOR_STEM`
выведена тем же `_prefix_stem` из «россии» — та же основа, которую Ф2 называет единственным
общим якорем (у Ф2 якорь вычисляется из данных, постоянной константы не было; новой словарь не
заведён). Если после исключения якоря у имени основ не осталось (имя «Россия») — сравнение по
полному набору, как прежде; ключ словаря — отдельная альтернатива проверки, как прежде. Ф1 и Ф2
не тронуты; условия подписи Ф3 (ровно одна группа без счётчиков, причины оценки из
`PRODUCER_REASON_CODES`, fail-closed при нечем сравнивать) не изменены. Шапка модуля про Ф3
переписана под новое правило.

**Тесты.** `tests/unit/test_honesty_report_f3_t7118a.py` (8): cc75e97f и 391c4388 под подписью
«Банк России» → флаг (красные на f6b8edc); `c6729d4c` и `136a0ca0` (имя в формулировке) и
`822072db` («Минэкономразвития» без слова «России») → молчит; названный «Росстат» → молчит;
`7cc2b578` → флаг сохранён; имя только из якоря сравнивается полным набором; константа якоря
совпадает с основой, которую Ф2 показывает в своём сообщении. **Краснота на f6b8edc** доказана
в отдельном worktree `/tmp/wt7118a` (detached, копия нового тестового файла; импорт разрешался
на модуль worktree — `honesty_report.__file__` внутри него): 3 падения — оба теста «флаг теперь
срабатывает» (старый порядок молчит) и guard константы (её нет); 5 тестов сохранения поведения
зелёные. Worktree удалён после доказательства. Ожидания Ф3 в `tests/scenario/test_honesty_report_t7118.py`
и прежде unit-тесты t7118 не переписаны: ни одно их ожидание не изменилось (в т4 названо полное
имя «SberCIB Investment Research» — все основы в формулировке).

**Дефект 2 (документ).** `docs/eval/CONTROL-SET.md` (пункт 3 «Как запускать») и README
dev-stand (раздел «Контрольный запуск вопросов») печатали команду отчёта как `noezemactl
honesty-report …`, хотя на dev-стенде она запускается из каталога репозитория как
`.venv/bin/python -m hostctl.cli honesty-report …` — так её печатают остальные разделы README.
`control-run.sh` (та же проверка) печатал `hostctl honesty-report …`; исправлен на запускаемую
на стенде форму. В CONTROL-SET.md обновлены пункт Ф3 («Флаги»), таблица ожиданий (строка q43:
молчит на `c6729d4c`/`822072db`, срабатывает на опорах с подписью «Банк России» у опросов
инФОМ) и сводная строка окна Ф3.

**Счёт тестов.** Полная проверка зелёная перед коммитом (§6). Baseline до задачи на `f6b8edc`:
этап 1 — 2835 passed + 12 skipped, этап 2 — 4 passed; финальный прогон: этап 1 — 2843 passed +
12 skipped (0 failed), этап 2 — 4 passed — рост ровно на 8 новых unit-тестов. `ruff` и `mypy`
(159 файлов) чистые; защитные тесты омоглифов (`test_homoglyph_source_scan_t7110.py`,
`test_web_homoglyphs_t7103.py`) зелёные.

**Границы.** Задача не меняла Ф1/Ф2, условия подписи, rules engine, промпты, конфигурации,
пороги, миграции, enum-значения, AuditEventType; `ARCHITECTURE.md` не тронут; записей в БД нет,
отчёт на реальных базах не запускался; стенда `.92` и LLM задача не касалась; `control-run.sh`
агентом не запускался (только `bash -n`); остаточных контейнеров и тестовых баз нет
(после прогонов: 41 контейнер всего, `noezema_mig_*` = 40, `noezema_tpl_*` = 0 — норма); push нет.

## T7.119 (2026-10-16): потолок 20 шагов для вопросов «официальные + независимые» и каталог-подсказка независимых источников (config-v27)

**Решение пользователя от 2026-10-16 (пункты 2 и 3):** поднять потолок шагов исследователя до 20
только для вопросов вида «официальные + независимые»; вести ручной каталог независимых источников как
ПОДСКАЗКУ, не белый список (инФОМ исключён). Код и тесты — коммит `ed4d539`; ADR-0037; промпт
explorer-v11 не тронут.

**Разбор до кода (главный вывод).** `config_snapshots` хранит payload поимёнными JSONB-колонками,
`upsert_online_candidate` перечисляет колонки в INSERT: новая верхняя секция payload потребовала бы
новой колонки, то есть миграции БД — стоп-критерия задачи. Оба новых ключа размещены внутри
существующей секции `session_limits` (`max_explorer_steps_official_independent`, `research_hints`);
precedent необязательных ключей внутри секции — `model.reasoning_by_phase` (config-v18, ADR-0030);
`schema_version` остаётся 1; `BOOTSTRAP_PAYLOAD` не тронут; миграций нет. Новых enum-значений и
`AuditEventType` нет: факты классификации, итогового потолка, id подобранных тем и проблем каталога
пишутся в payload существующего события `QUESTION_SELECTED`.

**Классификация** — чистый модуль `apps/orchestrator/research_hints.py`: нормализующие функции гейтов
почти-дубликатов (`content_word_stems`, `normalize_search_query`, `_prefix_stem`) и константа
`OFFICIAL_HOSTS` honesty-report импортом (зеркальных словарей не заводилось); маркеры официальные
(основы «росстат», «гкс», «цб», «минфин», «минэкономразвития», «официальный», доменные метки; «Банк
России» — регулярка единственного числа, «банки России» не метка) и независимые (основы «независимый»,
«аналитик», «оценка», «опрос», «индикатор», «hh», «superjob»). Класс — когда есть и те, и другие.
Контрольные вопросы: c1/c2/c3 — класс (темы `labor_wages`, `labor_unemployment`, `inflation`); c4
(только официальные) и c5 (арифметика) — вне класса; все шесть фикстур честности T7.118 — класс.
Принятая включательность: «оценки Росстата» попадёт в класс — ошибка в безопасную сторону: ничего не
запрещается. Принятое ограничение: сравнение основ (не лемматизация) — словоформы, расходящиеся в
первых 5–6 знаках, остаются различными словами; правило подбора темы и уточнение ограничения даны
поправкой T7.119a (ADR-0037 §1а; c2 подбирается основами `безраб`, `рынка` и `труда`). Таблица и
разбор: ADR-0037 §1.

**Потолки и пол (T7.21 не сломан).** `effective_explorer_step_cap`: обычный `max_explorer_steps`; при
классе — max(обычный, `max_explorer_steps_official_independent`); затем прежний пол «названные
источники + 3» (учёт только полных URL, T7.21) применяется и поверх повышения. Отсутствие ключа =
прежнее поведение (тест: на payload v26 c1 получает 16, не 20). Для контрольных вопросов пол инертен
(URL в тексте вопросов нет).

**Таймауты (замер T7.118).** 16-шаговые сессии: 705–1459 с (≤91,2 с на шаг); контрольный прогон c3:
1228 с (76,75 с/шаг). 20 шагов в наихудшем замере ≈ 1824 с > прежнего `phase_deadline_seconds = 1800`
— дедлайн не покрывал новый потолок. config-v27: `phase_deadline_seconds` 1800 -> 2400 (+31 % запаса),
`session_timeout_seconds` 1800 -> 2700 (= 2400 + куратор ≤180 с (T7.76) + запас; < `TimeoutStartSec`
3600 юнитов стенда; 45-минутный потолок ожидания `control-run.sh` не меняется).

**Каталог (часть 4).** `session_limits.research_hints.independent_sources`: три темы (`labor_wages`,
`labor_unemployment`, `inflation`) со списками слов темы (T7.119a: ключ `keywords`; в первичном
payload лежали основы, и парсер нормализовал их повторно — дефект, см. ниже); источники — домен + одна строка
«что там искать». Источники названы пользователем; хост домены не проверял по живому вебу и не
добавлял своих. Честное раскрытие содержимого: все домены заданы перечнем пользователя; строки тем
зарплат и инфляции перенесены дословно; в теме `labor_unemployment` в первичном payload дословна была
лишь строка dcenter.hse.ru, а hh.ru, superjob.ru и forecast.ru пересказаны в заданном стиле («индекс
hh.ru и аналитика рынка труда», «исследования SuperJob о занятости и рынке труда», «ЦМАКП, обзоры
рынка труда»); с T7.119a ВСЕ ЧЕТЫРЕ строки labor_unemployment дословны к утверждённому тексту
пользователя («hh.индекс (резюме на вакансию)», «исследования рынка труда», «ЦМАКП», «Центр развития
ВШЭ, комментарии о рынке труда») — инФОМ исключён по решению пользователя. Подсказка —
секция контекста шага (`render_independent_hints_section`, ≤600 знаков, при переполнении снимаются
целые строки с хвоста; образец — «Недавно недоступны» ADR-0036), фиксированная позиция после
информации о бюджете шага и хостовой секции, до наблюдений; заголовок содержит «подсказка, не
ограничение» и дисклеймер про пересказ официального релиза. Пусто: вне класса, без ключа, при проблемах
каталога (отказ по частям с названными проблемами, валидация примитивов —
`optional_session_limits_problems` в `packages/domain/config.py`). Поиску и чтению ничего не
запрещено; гейты ADR-0036 не тронуты.

**config-v27.** `docs/eval/config-v27-payload.json` = config-v26 байт в байт, кроме четырёх ключей
`session_limits`: `max_explorer_steps_official_independent` = 20 (новый), `research_hints` (новый),
`phase_deadline_seconds` 2400, `session_timeout_seconds` 2700; canonical payload
`9d9ebba4937392da1b03156c9a39d2e5a9b244337720fee677148d1c29d8c83c` (пересчитан поправкой T7.119a после
смены ключа каталога и дословных строк; payload коммита `ed4d539` имел canonical
`3f8160f8eababe5149027cdacdbe1f2710a70fc885324ae7c08f02421da77562`, нигде не активированный; хеш файла —
другое число), канон
config-v26 `3d93df81…` зафиксирован тестом как неизменённый. Конфиг НЕ активирован; активация и откат —
команды README dev-stand, решение менеджера; дефолт bootstrap (config-v20) не тронут; скрипты стенда к
v27 не обращаются (guard-тест). Тест закрепляет и неизменность пинов: explorer-v11 резолвится и в v27.

**Тесты.** Юниты 26 + 3 + 6 (`tests/unit/test_research_hints_t7119.py`,
`test_explorer_context_hints_t7119.py`, `test_config_v27_payload_t7119.py`) и 3 сценарных
(`tests/scenario/test_research_hints_session_t7119.py`: реальная сессия на FakeLLM с вымышленным
каналом «Гелиос» и разными текстами страниц; c1 на v27 — `SUCCEEDED` на 18 шагах с 17 выполненными
чтениями (потолок 20) и секцией подсказки в контексте каждого шага; c1 на v26 — 16 чтений и
`budget_exhausted`, ни одной секции; c4 на v27 — без повышения и без секции; факты в payload
`QUESTION_SELECTED`; вымышленные значения страниц не напоминают контрольные замеры). Красность новых
тестов на `c6c9a30` доказана временным detached worktree (юниты — `ModuleNotFoundError` нового модуля,
сценарные — `budget_exhausted` на 16 и отсутствие секции); worktree удалён.
Полная проверка: ruff и mypy strict (160 файлов) чистые; pytest stage 1 (postgreSQL, `-n auto`)
2881 passed + 12 skipped, stage 2 `timing` 4 passed (база коммита — 2843+12; +38 новых тестов).

**Границы.** Конфигурация не активирована; payload config-v26 и прежние не менялись; промптов, правил,
порогов, rules engine, детекторов атрибуции, группировки независимости, research proxy, гейтов
почти-дубликатов задача не касалась; миграций, enum-значений и AuditEventType нет; `ARCHITECTURE.md`
не тронут; LLM/движок `.92`/eval-run/смоуки не запускались; реальных сессий нет (тестовые — только на
фикстурных scratch-базах); остаточных контейнеров нет (после каждого полного прогона: 41 контейнер
всего, `noezema_mig_*` = 40, `noezema_tpl_*` = 0 — норма). push нет.

## T7.119a (2026-10-16): правка подбора тем подсказок по реальным вопросам стенда — keywords в каталоге и дословные строки labor_unemployment

**Дефект (замер менеджера на config-v27, HEAD `170f27a`).** `parse_independent_hints` прогонял основы
слов каталога через `content_word_stems` ВТОРОЙ раз: уже-основа «инфляц» схлопывалась в «инфля», а
слово вопроса «инфляция»/«инфляции» даёт основу «инфляц» — пересечения нет. Из 30 классифицированных
вопросов стенда вида «официальные + независимые» 12 оставались без темы, `inflation` подбиралась почти
только через «прогно». Пример замера: вопросы о годовой инфляции по Росстату/ЦБ без слова «прогноз» не
получали подсказку вовсе.

**Правило (выбран вариант А).** Тема подбирается, если основа содержательного слова вопроса РАВНА
основе ключевого слова темы при ОДИНАКОВОЙ нормализации обеих сторон: в payload хранятся СЛОВА темы
(новый ключ `keywords` вместо `stems`) и проходят тот же `content_word_stems` ровно ОДИН раз, что и
слова вопроса; многословное ключевое слово («оплата труда») разбирается на отдельные основы («оплат»,
«труда»). Вариант Б (двусторонний префиксный допуск >= 4 знака, как `_same_word_stem` в
`packages/memory/honesty_report.py`) отклонён: он требует новой функции сравнения и произвольного
минимума длины, размывает различные слова («цен» ~ «центр»/«ценность») — а корень дефекта именно в
вторичной нормализации каталога, а не в отсутствии допуска. Подпись функций `research_hints.py`
сохранена, `orchestrator.py` не тронут. Переименование ключа безопасно: config-v27 нигде не
активирован (содержимое `session_limits` — произвольный mapping поимённых JSONB-колонок, миграций нет).

**Payload config-v27.** `stems` -> `keywords` у трёх тем: labor_wages — заработная, заработные,
зарплата, доход, оплата, оплаты; labor_unemployment — безработица, вакансия, занятость, рынок, рынка,
кадр, кадры, труд, труда; inflation — инфляция, цена, цены, цен, прогноз, inflation, price. В keywords
labor_wages нет слов «труд»/«труда» (в прежних основах они были): с ними 6 вопросов о безработице
получали бы и `labor_wages`, и `labor_unemployment` одновременно. Строки labor_unemployment — все
четыре дословно к утверждённому тексту пользователя: hh.ru — «hh.индекс (резюме на вакансию)»,
superjob.ru — «исследования рынка труда», forecast.ru — «ЦМАКП», dcenter.hse.ru — «Центр развития ВШЭ,
комментарии о рынке труда» (в первичном payload дословна была лишь строка dcenter.hse.ru). Строки тем
labor_wages и inflation не менялись.

**Замер по фикстуре** (69 вопросов /home/denis/dsh1/fixtures-t7118/stand_questions.json, STATUS
T7.118): классифицировано 30; до правки тема была у 18, без темы — 12; после правки тема есть у всех
30 (21 `inflation`, 6 `labor_unemployment`, 3 `labor_wages`), ноль без темы и ноль с двумя темами.
Из исправленных 12 одиннадцать подбираются основой «инфляц», один (9d30c4b2 — название
индикатора в тексте вопроса по-английски) — основой `price`. Контрольный набор: c1 -> `labor_wages`, c2 ->
`labor_unemployment`, c3 -> `inflation`; c4 и c5 вне класса — как прежде. Пробы «Что такое ценность
денег и центр принятия решений?», «рыночный капитал предприятия», «ценности центра» не получают темы
(основа `цен` не равна основам `центр`, `ценность`, `ценност`).

**Хэши config-v27 (пересчитаны).** canonical:
`9d9ebba4937392da1b03156c9a39d2e5a9b244337720fee677148d1c29d8c83c` (прежний `3f8160f8…` относился к
payload коммита `ed4d539`), хеш файла: `8a2d77cb60ed34031ef06db8a0d103a4926f327fa3e545f9c7fea41f511fb260`
(прежний `edea91f0…`). Обновлены везде, где записаны: пин `tests/unit/test_config_v27_payload_t7119.py`,
ADR-0037 §5, AGENTS.md §6 и §7, раздел README `deploy/dev-stand`, STATUS (этот абзац). config-v26 не
изменён байт в байт; канон `3d93df81…` закреплён прежним тестом.

**Тесты.** Новый `tests/unit/test_hint_topic_selection_t7119a.py` (49 тестов): 69 вопросов стенда
эмбедированы дословно (целостность — закреплённый sha256 переноса), таблица id -> подобранная тема для
всех 30 классифицированных, названные замером примеры инфляции без слова «прогноз» получают
`inflation`, полнота категорий (21/6/3, двойных тем нет), пробы против ложных основ, c1–c5, дословные
строки labor_unemployment и их рендер в секции, отклонение старого ключа `stems` с названной проблемой
`неизвестные ключи ['stems']`. Изменены: `test_research_hints_t7119.py` (26; assertion'ы каталога
payload-управляемые: keywords вместо stems; основы labor_wages были `зарабо/зарпла/доход/оплат/труд`,
стали `зарабо/зарпла/доход/оплат` — правка вызвана payload, не ослаблением), `test_config_v27_payload_t7119.py`
(6; пины хэшей). Сценарные `test_research_hints_session_t7119.py` не изменены: строка labor_wages про
hh.ru прежняя. Красность на `170f27a` доказана временным detached worktree `/tmp/wt7119a` (старый код и
payload + новые тесты): 27 отказов, из них ровно 12 — вопросы фикстуры без темы; worktree удалён.
Полная проверка: ruff и mypy strict (160 файлов) чистые; pytest stage 1 (postgreSQL, `-n auto`) 2930
passed + 12 skipped, stage 2 `timing` 4 passed (база коммита `170f27a` — 2881+12 и 4; новых тестов +49).

**Границы.** `tool_context.py` и `assertion_window.py` не тронуты; сама классификация «официальные +
независимые» (маркеры), потолки и таймауты, config-v26 и прежние payload, промпты — не тронуты;
миграций, enum-значений и `AuditEventType` нет; конфигурация не активирована, откат config-v27 — payload
config-v26; скрипты стенда к v27 не обращаются (guard-тест зелёный); LLM/движок `.92`/eval-run/смоуки
не запускались, реальных сессий нет; остаточных контейнеров нет (41 всего, `noezema_mig_*` = 40,
`noezema_tpl_*` = 0 — норма). push нет.

## T7.120 (2026-10-17): не-государственные первоисточники в словаре атрибуции и починка pairing для «голого» значения (решение пользователя «recognize retellings by non-state producers before expanding the curator»; код и тесты — коммит `c4b6a12`; документация — следующий коммит; ADR-0029 §23)

**Решение.** Менеджер поставил: recognize retellings by non-state producers before expanding
the curator. Пользователь согласился (2026-10-17): пересказы не-государственных производителей
(SuperJob, hh.ru, Сбер CIB, ВТБ, Альфа-Банк, Газпромбанк, ПСБ, инФОМ, ЦМАКП, СберИндекс)
приписываются этим производителям прежде расширения curator. Прежний запрет T7.85 заносить
таких акторов в `PRIMARY_SOURCES` отменен явным решением; его последствия проиграны на тестах
и зафиксированы в ADR-0029 §23 (включая честную отмену запрета в AGENTS §7).

**Словарь (часть 2а).** Десять записей `PRIMARY_SOURCES` добавлены (`superjob`, `hh`, `sbercib`,
`vtb`, `alfa`, `gpb`, `psb`, `infom`, `cmakp`, `sberindex`; имена/домашние URI/алиасы — ADR-0029
§23.2). Короткие алиасы — с границей слова: «ВТБ24» и «ВТБР» не матчатся, «ВТБ Мои Инвестиции»
и «ВТБ Капитал» — тот же ключ; алиас SberCIB требует аббревиатуру CIB («Сбер»/«Сбербанк» — нет);
инФОМ отдельный производитель, без склейки с ЦБ. Закрытые списки шаблонов, измерительных
маркеров и relay-глаголов не расширены: «регистрировать» не добавлен нигде (случай 83e7a1c8
остаётся честным отказом).

**Relay-гвард (б) (часть 2б).** Белый список `_PRODUCER_ANALYST_KEYS` (ровно десять новых
ключей): акторное слово «аналитики/эксперты» вплотности к алиасу словаря эпохи T7.120 —
подразделение названного производителя («Аналитики ВТБ ожидают … 5–6%» → derivative→ВТБ).
Гвард над прежними ключами (в т.ч. ЦБ) не тронут: «аналитики ЦБ прогнозируют 6,3%» остаётся
маркером третьего актора; попытка расширить список на весь словарь красная на T7.85a-контроле
и отклонена.

**Pairing (часть 2в).** `_claim_bare_values` (голые десятичные значения утверждения без единицы;
целые, годы, «в 2 раза» — вне) + инерционный фильтр в `_pairing_maps` и `self_publication_candidate`:
значение обязано стоять отдельно в фрагменте атрибуции. Закрыт дефект 83e7a1c8 (было —
derivative→Росстат с основанием «выросло на четверть», где числа 9,6 нет; стало —
`value_not_attributed`, записей нет). Инвариант: фильтр только снимает pairing, никогда не
добавляет; целочисленный прежний режим побайтово сохранён (тест legacy-формы «на четверть» →
derivative→Росстат зелёный на обеих версиях кода).

**Версии.** `host-source-attribution-v8 → v9`, `host-value-attribution-v13 → v14`; оба детектора
изменены — bump вместе. 36 пинов в 29 тестовых файлах обновлены механической заменой.
Записи эпохи v8/v13 пересматривает прежний коллектор reattribute-stale; переатрибуция стенда
НЕ запускалась (запрет задачи). Ожидание на следующем штатном прогоне: записанные
пересказы для кейсов SuperJob/инФОМ сменят производителя или снимутся.

**Флипы ожидания (было → стало, закреплены комментариями в тестах).** (1) `STATEMENT_145`
(forbes/kommersant, value- и page-уровень, expectation-verb): derivative→cbr → ambiguous_primaries
(инФОМ и ЦБ в одном окне). (2) Коммерсант/инФОМ из «переживающих пересказов» t785b вынесен в
отдельный ambiguous-тест с дословным текстом. (3) ЦМАКП: признак « По данным ЦМАКП, 12,3% »
→ замазанный словарём не-признак (ФОМ занимает его место в параметризации); «Эксперты ЦМАКП
рассчитали … 14,5%»: no_value_attribution+value_and_primary → ambiguous_primaries+publication_window.
(4) Латинский признак SberCIB → не-признак (контроль признака продолжен на Barclays); строгое
derivative→rosstat страницы Сбер CIB сохранено. (5) Альфа-Банк 07126d06: value_not_attributed →
derivative→alfa (relay-глагол «повысил прогноз»). Ни один тест не удалён; два переименованы
(суффикс `t7120`), ни одно ожидание не ослаблено.

**Стендовые кейсы (только офлайн-разбор, стенд не трогался).** Кейс 1 (5a75ebf6, Rambler →
SuperJob, 19%/33%): значение приписано SuperJob (strict pairing), чистая сборка графа
(якорь www.superjob.ru + страница + Rambler + ЦБ по страничной записи) — одна группа;
подпись «Опубликовано производителем (SuperJob)» при E1 reachable (pure-функции, пины
порогов не тронуты). Остаток: сама страница SuperJob остаётся derivative→ЦБ (строгий фрагмент
«по данным ЦБ РФ», ADR-0029 §23.7 п.3). Кейс 2 (83e7a1c8, hr-ratings, hh-индекс 9,6): после
починки — записей нет (честный отказ); «регистрирует» в закрытые списки не добавлен.

**Тесты.** Новый `tests/unit/test_primary_producers_t7120.py` (43: самосогласованность записей,
границы имён, pairing-кейс было→стало + инварианты, белый список с областью десяти ключей,
реле-форма Альфа-Банка, контроли консерватизма, residual резки `hh.ru` фрагментатором, кейс 1 цепочкой
value→grouping→signature/Ф3). Перенастроены юниты `test_attribution_fixtures_t785.py` (3 строки),
`test_publication_window_veto_t785a/b/c.py`, `test_expectation_verb_t7110.py`,
`test_relay_forecast_t798.py` и сценарии `test_reattribute_stale_records.py` (тест переименован
в `…stay_separate_when_infom_is_named_t7120`; эпохи v2/v3/v4 дополнительно моделируются решением
эпохи), `test_reattribute_pointer_clear_t798.py` (улика Альфа-Банка записана с якорем производителя),
`test_self_publication_labels_t7101.py` (стало: 4 записи, 3 подписи, 2 новых якоря, второй прогон
дописывает запись ближайшего первоисточника, идемпотентен третий), `test_value_attribution_levels.py`
(SberCIB — домашний хост производителя) — с комментариями было→стало. Красность новых ожиданий на
`11014ee` доказана detached worktree `/tmp/wt7120` (свой venv, `source_attribution.__file__`
указывает на worktree): новый юнит-файл — ImportError при сборке
(`cannot import name '_producer_analyst_actor_spans'`); перепиненные сценарии — 8 FAILED / 10 passed
на старых решениях (derivative→ЦБ вместо ambiguous, «не собрано» вместо derivative→alfa, метки
эпох v13 вместо v14); worktree удалён.

**Проверки.** ruff чистый; mypy strict (packages apps hostctl, 160 файлов) чистый;
pytest stage 1 (не-timing): 2975 passed, 12 skipped; stage 2 (timing): 4 passed.
Промежуточный замер (к моменту, когда новые юниты уже зелёные, а 12 сценариев
ещё не перепинены): 12 failed, 2963 passed, 12 skipped — все 12 красных сценариев перепинены
этой задачей и теперь зелёные (суммарное число тестов за время задачи выросло на новые
юнит-файл и добавленные проверки перепиненных сценариев).
Остаточных контейнеров нет: 41 всего; scratch-БД `noezema_mig_*` = 40 до и после прогонов,
`noezema_tpl_*` = 0 (замер до §6, после §6 и после замера красности).

**Границы.** `packages/memory/independence.py`, `source_graph.py`, rules engine, пороги и grades,
схема и миграции, `AuditEventType`, промпты и конфигурации, нормализация страницы (`_FRAGMENT_SPLIT`,
`normalize_scan_text`), honesty_report, ARCHITECTURE.md — не тронуты. Eval-run/смоуки/реальные LLM,
сеть, доступ к `.92`, операции переатрибуции/предпросмотра на реальных базах, активация конфигов —
не выполнялись. LLM-ветки в новых тестах отсутствуют. push нет.

## T7.120a (2026-10-18): исправление двух дефектов приёмки T7.120 — инФОМ в семье cbr и снятие ложной записи происхождения (дефекты названы менеджером; код — коммит `064dc75`; ADR-0029 §24)

**Дефект 1 («инФОМ отдельный производитель, без склейки с cbr»).** Опросы инФОМ заказываются
и публикуются Банком России — это один производитель. В реестр добавлено поле
`PrimarySource.family_key` (единственная семья: `infom → "cbr"`), оба детектора сворачивают
кандидатов семьи к корню (`family_primary_key`, `family_home_hosts`,
`collapse_family_candidates` в `apps/research_proxy/source_attribution.py`; `_home_hosts()`
в `value_attribution.py`): страница на home-домене ЦБ — собственная публикация Банка России
(не пересказ инФОМ); страница, называющая только инФОМ, — derivative внутри семьи cbr с одним
корнем (группировка видит одного родителя); инФОМ + Банк России в одном окне — не спор двух
первоисточников, решение — Банк России (derivative→cbr окном публикации). `packages/memory`
не тронут: свёртка происходит до группировки. Версии методов: страничный v9 → v10, значений
v14 → v15; пины пересчитаны в 27 тестовых файлах.

**Дефект 2 (ложная запись 83e7a1c8).** Утверждение «По данным HR-Ratings, hh-индекс … 9,6»
несло запись `derivative → Росстат (host-value-attribution-v13)` с основанием «По данным
Росстата, в декабре 2008 года число безработных выросло на четверть», где значения 9,6 нет.
Правило: сохранённая запись значения, проваливающая pairing-гейт текущего метода против
собственного `basis_fragment`, снимается планом «будет снята: значение утверждения отсутствует
во фрагменте основания прежней записи (T7.120a)» и применяется условным UPDATE скоупа
(снятие ключа `value_attribution`, guarded FOR UPDATE, предикат устаревания) с каскадом T7.117.
Гейт — публичная функция `record_pairing_gate_passes` (`value_attribution.py`); снятие только
для устаревших меток, не затронутых коррекцией оператора, при свежесобранном решении НЕ
derivative. Fail-closed: усечённое основание не проверяется (запись сохраняется);
библиографические и «известный производитель упомянут» pairing вне гейта; прочие «свежий метод
отказался решать» сохраняют запись. Новый AuditEventType и миграции не заводились (прецедент
снятия записанного — T7.98). Инвариант «прежние записи не удаляются никогда» имеет теперь ровно
одно именованное исключение: основание лжёт, это доказано гейтом.

**Решения на живых фикстурах (до → после).** `interfax.ru/business/1117709` (формулировка
cc75e97f): было — ambiguous_primaries (обе метки), стало — derivative→cbr, basis «с 13,7% до
14,2%, следует из опроса "инФОМ", проведенного по заказу Банка России». `cbr.ru/press/event/?id=32879`:
было — value derivative→инФОМ, стало — собственная публикация Банка России (страница на
home-хосте семьи). `kommersant.ru/8294535` и `forbes.ru/552221` (формулировка 391c4388,
STATEMENT_145): было — ambiguous_primaries, стало — derivative→cbr окном публикации.
`hr-ratings.com/analytics/rynok-truda-bolshoj-krizis-v-istorii-rossii` (оба утверждения):
было — own_assessment, стало — own_assessment (изменений нет: hh-индекс назван самим
сайтом; ложность прежней записи Росстата снимается переатрибуцией по уликам, не текстом).

**Флипы возвращённых ожиданий (было T7.120 → стало).** `test_reattribute_stale_records.py`
тест 6 возвращён с `11014ee` дословно (три группы E3 → одна группа E1/hypothesis, план
derivative→cbr «будет записано происхождение значения улики»); `test_publication_window_veto_t785a.py`
(случай 6: ambiguous→derivative→cbr), `t785b` (тест `…one_producer_t7120a`), `t785c`
(live-тексты → derivative→cbr), `test_expectation_verb_t7110.py`:
ambiguous→derivative→cbr, `test_attribution_fixtures_t785.py` (forbes/kommersant строки),
`test_primary_producers_t7120.py` (infom foreign-page → якорь cbr + контроль family_key),
`test_self_publication_labels_t7101.py` (собственные публикации плана 2→3, прочие строгие записи плана 2→1, метка
cc75e97f «Банк России» вместо «инФОМ», якорь один — alfabank.ru, каскад 3→2 (07126d06 и
136a0ca0 делят реле-источник), идемпотентность со второго прогона вместо третьего). Законные
расширения T7.120 (alfa, sbercib, cmakp, hh, superjob, sberindex и их пины; ЦМАКП/SberCIB/
Barclays параметры вето) сохранены без изменений.

**Тесты.** Новые `tests/unit/test_infom_cbr_family_t7120a.py` (7) и
`tests/scenario/test_reattribute_infom_cbr_family_t7120a.py` (4: replay пересказов в одну
группу E1; снятие ложной записи с сохранением честной записи HeadHunter; guard сохранения
при пройденном гейте и свежем отказе; guard коррекции оператора), живые фикстуры
`tests/fixtures/t7120a/` (interfax 1117709, cbr press event 32879, hr-ratings). Красность
на `982ffc5` доказана detached worktree `/tmp/wt7120a` (PYTHONPATH-путь подтверждён): unit —
ImportError (`collapse_family_candidates`), scenario — FAILED replay и FAILED снятие; два
guard-теста зелёные на обеих сторонах (ожидаемое сохранение — честным зелёным, не красным).
Полный прогон: 2987 passed + 12 skipped (not timing), timing 4 passed (было T7.120:
2975+12). Scratch-БД до и после замера: noezema_mig_* 40 → 40, шаблонов 0; контейнеры не
изменились; базы noezema-eval*/noezema-smoke* (артефакты менеджера) не тронуты.

## T7.121 (2026-10-18): границы честности кураторских записей — период рядом со значением, дата прогноза, склейка взаимных дублей в ранейшую запись, честный итог из опоры (стендовые случаи с1–с4; код и тесты — коммиты `f05cecd` (части 2–3), `a3f7f1a` (часть 4), `c35ea2d` (часть 5), `b2ba149` (части 6–7); документация — следующий коммит; ADR-0038, ADR-0033 §14)

**Стендовые случаи (приёмка .92, названы менеджером).**

- **с1** (`finmarket.ru/news/6727171`, безработица 2,2 %): куратор записал «…в октябре 2026 года
  составил 2,2 % по методологии МОТ…» — рядом со значением `2,2` на странице стоит только датировка
  статьи («14 октября 2026 года 11:46», «14 октября.»): месяц формулировки ничем не подтверждён.
  Семья кейса прослежена в T7.112–T7.114 (сессии `4e7ba8c2`, `14095b8d`). Существующие тесты это
  предложение побайтно не пинят (поиск по формулировке Голиковой в tests/ — только новые файлы и
  фикстура), скрытых флипов ожиданий нет.
- **с2** (прогноз ВТБ 5–6 % на 2026): «Аналитики ВТБ ожидают, что инфляция по итогам 2026 года
  составит 5–6 %» — записано без даты самого прогноза; страница несёт «26 февраля», целевой период
  («по итогам 2026 года») датой прогноза не является.
- **с3**: контекст-пак содержит ДВЕ уже записанные формулировки одного факта (тот же показатель, то
  же значение, то же годовое покрытие); куратор приносит третью и не объявляет ни одну опорой —
  прежде гейт T7.83a писал ТРЕТЬЮ запись («ambiguous … left as proposed», на витрине три бейджа).
- **с4**: сессия ответила, опираясь на уже записанное утверждение (`relied_claim_ids`), не записав
  нового; карточка говорила «Ответ не записан».

**Части 2–3: гейт периода и гейт даты прогноза (ADR-0038 §2–§3).** Новый чистый модуль
`apps/orchestrator/curator_period_gate.py` (точка входа `claim_period_rejections` :643; токены
формулировки `statement_period_tokens`, имена месяцев текста `named_month_numbers` :229). Провязка в
оркестраторе (`apps/orchestrator/orchestrator.py` :3657–:3700): после конверсии дублей T7.84(б) и до
блока as_of — прежний порядок проверок сохранён; снятая периодом операция не получает второй причины
(as_of автоподавляется). Окно подтверждения — `PERIOD_WINDOW_CHARS = PUBLICATION_WINDOW_CHARS` = 400
(существующий порог вето T7.113, новых констант не заводилось). Месяц читается `_MONTH_WORD_RE`
(:97) с границами слова по кириллице и подрезкой суффикса `{1,6}`; без подрезки притяжный
«декабрьского» попадал в периоды. «День + месяц» — датировка страницы, не период; названный месяц без
дня — период; тексты без измеренного значения периода не подтверждают; утверждение без измеренного
значения — вне гейта (T7.108); формулировка без периода молчит; перепроверки и уже снятые операции не
проверяются. Отказ периода: «claim[i]: период «…» не найден рядом со значением на связанной
странице — возьми период из текста или напиши, что период не указан». Гейт прогноза (закрытые списки
маркеров прогноз*/консенсус*/предсказ*; имена показателей опросов — не маркеры): дата прогноза
обязательна либо честная фраза `FORECAST_DATE_UNSTATED_PHRASE` (:65) «дата прогноза на странице не
указана»; отказ (:636): «утверждение с прогнозом или ожиданием обязано называть дату, когда прогноз
сделан (месяц и год либо дату), или писать „дата прогноза на странице не указана“». Журнальная форма
— вариант C ADR-0034 §9 без новых типов событий: частичное снятие — `rejected_operations` payload
существующего `claim_created`; полное — `session_state_changed` с аддитивным
`curator_reject_kind: "claim_period_not_near_value"`, ровно один раунд исправления; при повторном
отказе финальное событие несёт второе предложение с `correction_round`.

**Часть 4: месячный страж и mutual-кластер (ADR-0038 §4, ADR-0033 §14).** `ValueDuplicateCandidate.
created_at` (SELECT приносит `c.created_at`), `_months_conflict` (обе стороны называют месяцы и
пересечения нет → кандидат не покрытие этой операции; если сняты все строгие — причина отказа
расширяется пояснением «… named months do not overlap (candidate … names months …)», иначе причина
побайтно прежняя), mutual-кластер: при >= 2 строгих ненадёжных совпадениях кандидаты сверяются ДРУГ с
ДРУГОМ зеркалом витринного направления T7.99 (`_cluster_pair`) и операция перепроверяется в РАНЕЙНЮЮ
запись (`_cluster_anchor`: earliest `created_at`, без даты — последней, при равенстве дат — по
claim_id: внутри phase-1-транзакции все даты равны, AGENTS §7). При отсутствии даты у любого
кандидата требуется полная взаимная покрытость — закреплённое поведение «два совпадения без опоры —
неоднозначность» сохранено тестом. Вето индикатора/опровержений — после выбора якоря. Витринный
предикат `value_duplicate_pair` побайтно не изменён.

**Часть 5: витрина «Ответ по уже записанным знаниям» (с4).** `apps/web/answer.py`:
`build_answer_result(..., relied_claim_count: int = 0)`, новый итог `answered_from_relied` после
отказа сессии и перед `no_answer` (без сессий — прежний `waiting`); карточка передаёт размер
relied-блока, список вопросов — нет (relied-строк там нет по дизайну), итог списка не меняется.
`RESULT_KINDS` дополнен, `ANSWER_KINDS` и все бейджи прежние; подписи в `apps/web/labels.py`:
«Ответ по уже записанным знаниям» / «Новых утверждений сессия не записала; ответ опирается на ранее
записанные утверждения (ниже).» / «Посмотрите блок „Использовано из знаний“ — там те же строки и их
текущие оценки.».

**Части 6–7: curator-v16 и config-v28 (ADR-0038 §7, не активирован).** curator-v16 = v15 байт в байт
кроме строки version, дополнения правила 13 и правил 15–17 (текст отказа прогноза процитирован в
правиле 15 дословно — та же формулировка, что выдаёт гейт); sha256 `9a32abf0…` (пересчитан после
правки цитаты: прежний `e82617b3…` больше неактуален; пересчитан повторно T7.121a после правок
правил 13 и 15 — актуальный `8d9946e4…`, ниже). config-v28 = config-v27 ровно с одним пином
`prompts.curator`; canonical v28 `2b77f249…`, хеш payload-файла `c65a27bf…` (оба пересчитаны
T7.121a: добавлена вторая правка — `session_limits.curator_record_gates: true`; актуальные
canonical `8db816ee…` и хеш файла `82ffe53a…`); canonical v27
`9d9ebba4…` и v26 `3d93df81…` заморожены guard-тестами. Активации нигде нет (скрипты стенда к v28 не
обращаются — guard зелёный); откат v28 — payload v27; раздел активации README — следующий коммит.

**Эволюция фикстур существующих тестов (было → стало, ожидания не ослаблены).**
`test_dateless_curator_reverify_order.py`: страницы ALPHA/BETA/GAMMA получили периоды, которые
цитирует CANDIDATE_OFFICIAL (раньше утверждение цитировало «ноябрь 2029», а страницы его не несли).
`test_self_publication_session_t7104.py`: STATEMENT_607 с маркером «ожидает» получил «(дата прогноза
на странице не указана)»; STATEMENT_63 — «(опрос от декабря 2025 года)» (страница её несёт);
STATEMENT_559 утратил «2024» и «16–21 января 2026 г.» — страница не несёт их рядом со значением
5,59 %. `test_value_duplicate_gate.py`: страницы alpha/epsilon/zeta получили периодные строки, eta —
«декабрь 2025 к декабрю 2024» (ее утверждение именно это и цитирует).
`test_value_duplicate_relabel_session.py`: ANCHOR_FORECAST переписан с честной фразой о дате
прогноза. Все правки снабжены комментариями «было → стало» прямо в файлах.

**Replay с1–с4 (стеновые тесты на нормализованных реальных страницах
`tests/fixtures/t7121/`, fake LLM, без внешних запросов).** с1 прежде: формулировка «в октябре 2026»
записывалась как есть; теперь: снята с названной причиной (пооперационно — остальные операции
предложения применяются; целиком — `curator_reject_kind` + один раунд), исправленная «…в сентябре
2026 года составил 2,2%.» (bare-месяц в окне значения той же страницы) принята; формулировка вовсе
без периода принята как есть; неисправленное предложение — финальный отказ без второго раунда. с2
прежде: прогноз записан без даты; теперь снят, исправление «(прогноз от 26 февраля 2026 года)»
принято (страница её несёт). с3 прежде: третья копия записана; теперь — перепроверка в ранейшую
запись, третья строка не появляется, якорь и union-улики сохранены; страж месяцев: операция про
СЕНТЯБРЬ против записанных ИЮЛЯ/АВГУСТА остаётся предложенной, причина называет месяцы обеих сторон.
с4 прежде: «Ответ не записан»; теперь `answered_from_relied` с блоком «Использовано из знаний»
(записанная строка с прежней оценкой, новая оценка не начислена).

**Красность на `6372138`** доказана временным detached worktree `/tmp/wt7121` (PYTHONPATH-путь
подтверждён; удалён): unit периода и unit дублей и scenario периода — ошибки collection (модуля /
`named_month_numbers`/`created_at` нет); scenario кластера — 2 FAILED (склейки нет); unit карточки —
6 FAILED + 1 зелёный замок «по умолчанию путь прежний» (ожидаемое, честным зелёным); scenario
карточки — 1 FAILED; unit v16/v28 — 7 FAILED (файлов нет) + 2 зелёных замка (заморозка v27/v26 и
отсутствие ссылок скриптов на v28). Самодостаточность промежуточных коммитов проверена временными
worktree `/tmp/wtA` (снимок `f05cecd`: 52 passed целевых прогонов) — удалены.

**Полные прогоны перед каждым из четырёх коммитов:** ruff чистый; mypy strict (161 файлов) чистый;
образ `noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3053 passed + 12
skipped** (collected 3065; база T7.120a — 2987+12, новых тестов +66: период unit 24 + scenario 7,
дубли unit 13 + cluster scenario 2, карточка unit 7 + scenario 1, промпт/конфиг unit 9), stage 2
`timing`: 4 passed. Scratch-БД до и после каждого замера: `noezema_mig_*` 40 → 40, шаблонов
`noezema_tpl_*` 0; контейнеров 41 (без изменений); базы noezema-eval*/noezema-smoke* не тронуты.

**Границы.** Стоп-критерии не тронуты: миграций, schema, `AuditEventType`, rules engine, порогов
оценок, группировки независимости, детекторов атрибуции, промпта explorer, payload config-v27 и
старее, ARCHITECTURE.md — нет. Новые константы окна не заводились. Хостовые гейты работают при любой
конфигурации (с T7.121a уточнение: гейты честности формулировок включаются ключом
`session_limits.curator_record_gates` — см. ниже; гейт значения, as_of и склейка дублей — по-прежнему
безусловны); конфигурация config-v28 не активирована нигде; LLM/движок .92, eval-run, смоуки не
запускались; остаточных контейнеров и worktree нет; push нет.

## T7.121a (2026-10-18): исправление дефектов гейтов честности записей по пробе менеджера на всех 53 стендах — датировка внутри предложения, год уровня страницы, дата документа, область маркера прогноза, включение гейтов ключом (дефекты названы менеджером; код и тесты — коммит `9371d76c`; документация — следующий коммит; ADR-0038 §11)

**Проба менеджера и счёт.** Менеджер прогнал прежний гейт по всем 53 утверждениям стендовой
пробы с побайтовыми текстами связанных страниц (файлы пробы `~/dsh1/fixtures-t7121a/`; в репо
фикстура закреплена: `tests/fixtures/t7121a/stand_claims_with_pages.jsonl`, sha256
`a9cf6116…`). Было: 24 отказа — 19 «дата прогноза не названа» + 5 периода (`9266248e`
«21 января 2026», `6d9a12ff` «декабре 2025», `7c8c9afe` «сентябре 2026», `136a0ca0` «июле
2026», `e5a64868` «октябре 2026»). Стало: 19 — 17 «дата прогноза» + 2 периода; «дата
документа» — ни одного отказа; «вне гейта» (нет периода и нет маркера прогноза) 6 → 7.
Прежних «ok» строк отказы не затронули: ноль коллатеральных регрессий (закреплено тестом).
Расхождение с цифрами постановки (21/4): авторитетны файлы пробы менеджера и повторённый прогон
гейта на detached worktree `d9d5fd5` — там ровно 24 = 19+5; таблица was/стало по всем 53
строкам закреплена `tests/unit/test_stand_fixture_table_t7121a.py`.

**Правки гейта (детали и обоснования — ADR-0038 §11).** д1: штампованное «день+месяц»
подтверждает названный период формулировки только внутри фрагмента предложения со значением;
отдельная датировочная строка — по-прежнему ничего. д2: когда месяц подтверждён в окне
значения, год берётся с уровня страницы pairing-подписью (строка выпуска «№ 9 (117), сентябрь
2026 года» либо датировочная запись месяца с явным годом); одиночный год — не подтверждение;
конфликт (тот же месяц в окне значения назвал другой год) блокирует страничную подпись. д3:
дата после «от …», «опубликов… …», «по состоянию на …» — дата документа (`PeriodToken.doc_date`),
отделена от группы периодов значения; обязана существовать на странице целиком (окно значения
неприменимо), подтверждается pairing-строкой, whole-page годом при свободном штампе или
названием месяца где угодно; отсутствует — отдельная именованная причина «дата документа «…»
не найдена на связанной странице…»; для гейта даты прогноза считается подтверждённой на тех же
whole-page правилах. д4: маркер прогноза — только про собственное значение формулировки:
«оцен*» маркирует лишь с совершенным будущим («добавит», «вырастет»), сравнения факта с чужим
прогнозом («ниже всех прогнозов», «уступает консенсус-прогнозу») подавляются закрытым шаблоном
(проверяется первым); новых словарей нормализации нет. Снятые отказы: `136a0ca0` (д1),
`7c8c9afe` (д2), `9266248e` (д3), `7cc2b578`, `fc8720df` (д4). Оставленные отказы периода —
ровно два, оба законны: `e5a64868` — все вхождения октября в окнах значения датировочные
(д1), голого названия месяца в окнах нет; `6d9a12ff` — ни одно название декабря не лежит в
окне значения (ближайшее — за 81 знак до окна), pairing шапки «в декабре 2025 года»
подтверждал бы только год, а месяца в окне нет — подтверждение месяца рядом со значением
(§2) не выполнено.

**д5 — включение гейтов конфигурацией.** Гейт периода/даты прогноза/даты документа применяется
ровно когда снапшот содержит `session_limits.curator_record_gates: true` (читает оркестратор
в `_curator_boundary`, до построения текстов страниц и вызова `claim_period_rejections`);
отсутствие ключа или false — гейты молчат, поведение прежнее байт-в-байт (сессии без отказов
не получают новых ключей payload — паттерн T7.105). Ключ валидируется `optional_session_limits_problems`
как булев (не-буль — строка проблемы, конфиг-кандидат не принимается); ключ — внутри
`session_limits` (верхняя секция payload = колонка config_snapshots = миграция БД — вне задачи,
schema_version остаётся 1). Гейты значения (T7.108), as_of опоры (T7.84) и mutual-склейки
дублей (T7.83) от ключа не зависят.

**Хеши.** curator-v16: `9a32abf0…` → `8d9946e4b845556aa369556c7067f141f120ddf55d0ae977cb5311cf929003df`
(два правила: период — д1/д2, дата прогноза — д3; правила 1–12 и 14 побайтно прежние).
config-v28 = payload v27 ровно с пином `prompts.curator` И `session_limits.curator_record_gates:
true`: canonical `2b77f249…` → `8db816eede41f44bde7c8cbb72791b27c1f4ad531f4034cbf9ebe964dc67e68b`,
хеш payload-файла `c65a27bf…` → `82ffe53a1a667270428027a42f29240c4d719a90ff932bfe3addc7c9c89da3d2`.
Payload v27 и старше не тронуты (откат v28 — v27); config-v28 нигде не активирован.
Ссылки на прежние хеши обновлены в AGENTS §6, ADR-0038 §7, STATUS выше и README dev-стенда.

**Красность на `d9d5fd5` (detached worktree `/tmp/wt7121a`, удалён после прогона).** 18 отказов
против 14 passed: новые файлы т1–т4 (2+2+7+3 = 14 unit), unit валидации ключа (2), diff payload
v28 (1), сценарий «payload без ключа — гейты молчат, утверждение принято» (1: тогда claims==[]
vs ожидаемые [C1_CREATED]), таблицы was/стало (2). Guard-тесты T7.121 в worktree прежние зелёные
(гейт без ключа там не вызывался — стендовый payload базы не содержал ключ, краснели только
тесты изменённого поведения). BYLO-колонка таблицы сверена побайтово со свежим прогоном гейта
базы по фикстуре (24 = 19+5+0+6 вне гейта — совпадение полное). Незакреплённые копии новых
тестов для прогонов в worktree лежали в `~/dsh1/t7121a-keep/` (в репо не коммитились).

**Изменённые тесты (было → стало, ожидания не ослаблены).**
`tests/scenario/test_curator_period_gate_session_t7121.py`: `_payload()` добавляет
`session_limits.curator_record_gates = True` (иначе гейты в снапшот-сессиях молчали бы и все
периодные ожидания краснели); причины/исходы 15 сценарных тестов — байт-в-байт прежние.
`tests/unit/test_curator_v16_config_v28_t7121.py`: пины v16/v28 перечислены заново; тест
`test_config_v28_differs_from_v27_by_curator_pin_only` → `test_config_v28_is_v27_with_the_curator_pin_and_record_gates_key`
(diff v27→v28 теперь ровно две правки — пин + ключ session_limits). Unit-тесты T7.121
(`test_curator_period_gate_t7121.py` 24 теста) не изменены: гейт как чистая функция вызывается
напрямую и от ключа конфигурации не зависит.

**Ловушка фикстуры.** Записи JSONL разделены ровно переводом строки `\n`, а `normalized_text`
страниц содержит сырой U+2028 (LINE SEPARATOR, нормализация research_proxy) — `splitlines()`
режет строку фикстуры по U+2028 и даёт JSONDecodeError; читается `.split("\n")` (в тесте есть
комментарий).

**Полные прогоны.** Перед коммитом кода: ruff чистый; mypy strict (161 файл) чистый; образ
`noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3085 passed + 12
skipped** (collected 3109; база T7.121 — 3053+12, новых +32: dateline 6, page-year 5, docdate 9,
marker 4, config unit 3, таблица 3, config scenario 2); stage 2 `timing`: 4 passed. Контейнеров
41 → 41; scratch `noezema_mig_*` 40 → 40, шаблонов `noezema_tpl_*` 0 → 0 (диагностический прогон
с принудительной остановкой оставил 1 mig + 1 tpl — удалены до замера, база восстановлена).

## T7.122a (2026-10-18): исправление фактических ошибок README при приёмке T7.122 (е1–е5) и переход на единственную рабочую ветку `main` (решение пользователя 2026-10-18; документация: код, тесты, промпты и конфиги не тронуты)

**Исправленные утверждения (README.md).**
- е1: «config-v26, config-v27, config-v28 выпущены payload'ами и не активированы нигде» и пункт
  «Не сделано» «Активация config-v21…config-v28 нигде не выполнена» — неверно. Факт: конфигурации
  последовательно активировались менеджером на dev-стенде командой `activate-online`; на 2026-10-18
  на стенде активна config-v28 (canonical `8db816ee…`). Решение об активации — за менеджером; в
  репозитории оно кодом не фиксируется.
- е2: консольной команды `noezemactl` нет (в `pyproject.toml` нет `[project.scripts]`). Команды
  запуска в README — только `python -m hostctl.cli …` (на dev-стенде — `.venv/bin/python -m hostctl.cli …`
  из каталога репозитория); имя `noezemactl` осталось одной фразой как историческое. В
  deploy/dev-stand/README.md «например через `noezemactl ask`» заменено на рабочую форму; строка
  «В документах эта команда называется noezemactl claim-dispute» — историческое упоминание, оставлено;
  строка образца вывода `(noezemactl reassessment-tick)` — дословная цитата вывода команды из кода
  (`hostctl/cli.py`), не правится: код этой задачей не меняется.
- е3: контрольный набор прогоняется не `eval-run`, а скриптом `deploy/dev-stand/control-run.sh <метка>`
  (T7.118); отчёт прогона — `python -m hostctl.cli honesty-report --control-tag <метка> [--json OUT]
  [--baseline F]`.
- е4: «Регулярные реальные сессии на dev-стенде с замерами нагрузки … — за оператором» заменено на
  «контрольные прогоны (control set + `honesty-report` на живой модели) выполняются после каждого
  выпуска; их отчёты хранятся вне репозитория» (прогоны 2026-10-16…18 выполнены, метки c6c9a30-base,
  11014ee-v27, a99365f-v28).
- е5: «Старт M4 по плану возможен только после серии реальных MVP-сессий и по решению пользователя»
  убрано (Gate M4 пройден — раздел «Прогресс по вехам»); вместо него открытые дефекты последнего
  контрольного прогона: связи улик у перепроверок (в том числе полученных конверсией дублей) не
  проходят гейты значения/периода — чинится в следующей задаче; куратор может вернуть пустой список
  утверждений при содержательной сводке.

**Часть Б — единственная ветка `main`.** Заменено рабочее упоминание ветки `impl/from-scratch` → `main`:
AGENTS.md (заголовок файла, §4 — рабочая ветка и ритуал пуша, запись §7), README.md (раздел
«Разработка»), deploy/dev-stand/README.md (получение кода стендом: `git clone -b main`),
.github/workflows/ci.yml (триггеры push/pull_request — только `main`), локальный хук
`.git/hooks/pre-push` (не версионируется; разрешённая цель пуша менеджера `refs/heads/main`).
Ветки `impl/from-scratch`, `codex-main-1` и `wip-archive` после пуша удаляет менеджер (две последние —
с архивными тегами). Исторические упоминания прежней ветки (ADR-0001…0038, архив STATUS, отчёты задач)
не тронуты; строка 3 этого файла «Ветка: impl/from-scratch» оставлена как историческая запись эпохи
реализации (задачей разрешена только одна запись в активной истории). Слияние в `main` — отдельный
merge-коммит следующим за этой записью.

**Полные прогоны (§6).** Перед коммитом на `impl/from-scratch`: ruff чистый; mypy strict (161 файл)
чистый; образ `noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3085 passed +
12 skipped**; stage 2 `timing`: **4 passed**. После слияния на `main` — тот же набор зелёный.
Контейнеров 41 → 41; scratch `noezema_mig_*` 40 → 40, шаблонов `noezema_tpl_*` 0 → 0.

## T7.122b (2026-10-18): README переписан под читателя, впервые открывшего репозиторий (решение пользователя 2026-10-18 «проверь тщательно документацию-описание и сделай его более понятным»; только документация: код, тесты, промпты и конфиги не тронуты)

README.md переписан целиком (~330 строк) по 11 замечаниям менеджера к тексту на `1e27f3a`:
- новый порядок: «Как это выглядит» (живой пример с dev-стенда: прогноз Банка России 6,0–7,0% —
  четыре улики одной группы независимости → бейдж «Подтверждено слабо» с подписью
  «Опубликовано производителем (Банк России)», уровень E1; прогноз SberCIB 6,5% от 29 сентября
  2026 года — отдельный производитель; rambler/SuperJob как пример пересказа), «Принципы
  честности», «Статус» коротко, «Быстрый старт», схема сессии mermaid (8 узлов) со списком
  проверок хоста, таблицы бейджей и порогов типов утверждений, раздел об источниках и пересказе,
  честный раздел ограничений, глоссарий;
- жаргон основной части переведён на общепонятный язык (оператор вместо менеджера; «файл-описание»
  вместо payload; внутренние имена — только в разделах «Для разработчиков» и «Глоссарий»);
- бейдж-таблица соответствует apps/web/reliability.py (6 уровней + «Опубликовано производителем»,
  цвета по `_LEVEL_COLOR`), подписи уровней E0–E4 и типов утверждений — из apps/web/labels.py;
- исправлен неверный путь SSE: прежний README писал `GET /api/v1/sessions/{id}/timeline/sse`,
  фактически `GET /api/v1/timeline/sse` с параметром `session_id` (apps/web/api.py);
- быстрый старт сверен по коду: PostgreSQL из infra/compose.dev.yaml (127.0.0.1:5432,
  noezema/noezema_dev = DEFAULT_DATABASE_URL packages/domain/db/engine.py), миграции
  `-m alembic upgrade head` (bootstrap-снапшот конфигурации сеется миграцией 0001 и он sealed —
  packages/domain/config.py), веб `python -m apps.web.main` (bind 127.0.0.1:8321, apps/web/bind.py),
  LLM-шлюз `NOEZEMA_LLM_BASE_URL` (packages/llm_gateway/config.py); честно указано, что fake LLM
  отвечает только записанными тестовыми сценариями (tests/fakes/fake_openai_server.py) и без
  реальной LLM осмысленного ответа не даёт;
- относительные ссылки и якоря проверены скриптом (все цели существуют); сканы файла: CJK/греческий
  пуст, смежная кириллица с латиницей пуста, строк с обратными косыми чертами в добавленном нет.

## T7.123 (2026-10-18): гейты честности ведут каждую новую связь; объявленное в сводке намерение при пустом claims отклоняется одним раундом (репроигрывание стендов 64837cb5 — к1/к2, 33a62678 — к4, b7e93ff6 — к3; код и тесты — коммиты `97b48aa` (часть 2) и `a87e14f` (часть 3); документация — следующий коммит; ADR-0038 §12, ADR-0033 §15)

**Порядок проверок границы (анализ, часть 1) — `_curator_boundary`
(`apps/orchestrator/orchestrator.py:3340`):** структурная разборка → проверка объявленного
намерения при пустых `claims` (часть 3, :3404, только первый вызов) → разрешение якорей
перепроверок (SELECT якоря принёс и формулировку цели: :3435, :3490) → гейт значения на связи
T7.108 (:3529) → разрешение опоры → гейт дублей T7.83/ADR-0033 (обращение операции в
перепроверку, :3600–3712) → гейт периода/даты прогноза/даты документа по связям при ключе
`curator_record_gates` (:3745–3786) → temporal `as_of` T7.84(б) → предпроверка правил с
объединённым фильтром снятых связей (:3786). Почему стенды проходили раньше: к1 — значение 9,6
без единицы измерения не попадало в измерительный профиль утверждения, гейт значения молчал, и
перепроверка оставалась со связью на страницу ключевой ставки; к2 — новая формулировка
finmarket отклонялась верно, но после обращения гейтом дублей операция становилась перепроверкой
и периодный гейт пропускал её по освобождению ADR-0038 §2; к3 — пустые `claims` с пустой опорой
принимались как есть (отличить объявление намерения в сводке было нечем), запасной путь опоры
T7.115 молчал: в финальном ответе исследователя b7e93ff6 ссылок `[c:…]` не было вовсе.

**Часть 2 (коммит `97b48aa`).** `apps/orchestrator/curator_rejections.py:189`
`links_without_claim_value`: параметр `statement_overrides` (перепроверка ведётся формулировкой
ЦЕЛИ — цель главнее собственного пересказа, ADR-0033 §15) и резервный профиль «голых»
десятичных токенов `_claim_bare_values` (тот же профиль pairing-фильтров T7.120,
`apps/research_proxy/value_attribution.py:702`) — включается только когда измерительный
профиль пуст. `apps/orchestrator/curator_period_gate.py`: :888 `PeriodGateDecision`, :932
`claim_period_rejections_by_links` — по каждой оставшейся `supports`-связи `source_assertion`
по её собственным текстам; неподтверждённая связь снимается строкой `evidence_link[<k>]:
evidence <e> does not confirm the period of claim <i>: «…»` (дата документа — прежняя строка
T7.121a); операция снимается только если ни одна связь не подтвердила период — байт-сохранённые
причины §2 с guard-условиями `all_usable`/`all_scanned` (стендовая таблица §11.6 не сдвинулась);
связь без значения утверждения вне периода (юрисдикция гейта значения, тот же предикат
`usable`); освобождение перепроверок снято. Прежняя обёртка :1040 сохранена — пять прежних
unit-файлов не изменены кроме одного (см. журнал ожиданий, пункт а). Оркестратор: снятые
периодным гейтом связи
объединяются с `removed_links` (:3777–3786) и попадают в фильтр предпроверки правил; journal
`rejected_links` активен по обоим наборам снятий. Гейты периода/прогноза/даты документа —
только при `session_limits.curator_record_gates` (config-v28, ключ не менялся); гейт значения
безусловен. Ранее записанные связи не затрагиваются никогда.

**Часть 3 (коммит `a87e14f`).** Новый чистый модуль `apps/orchestrator/curator_declared_claims.py`:
`summary_declares_new_claim` (сводка складывается в одну строку — пробелы, NBSP, U+2028/U+2029 —
и сверяется с закрытым списком оснований: «создаем новый claim», «создаём новый claim»,
«предлагается создать», «фиксируем новое состояние»), `DECLARED_EMPTY_REASON` = «в сводке
объявлено новое утверждение, но список claims пуст», kind `claims_empty_declared_in_summary`.
Граница (:3404): пустые `claims` + пустая опора + объявленное намерение — отказ ЦЕЛИКОМ прежним
событием с аддитивным kind; ровно один раунд исправления (на втором вызове проверка молчит,
:3091); принятое после раунда пустое предложение догоняет опору из ссылок `c:<id>` финального
ответа исследователя (запасной путь T7.115, :3135; аддитивный ключ payload
`relied_from_explorer`, :3290). Нового AuditEventType, порогов и пинов промпта нет;
curator-v17/config-v29 не заведены (ADR-0038 §12.5: причина раунда дословно названа хостом,
правка промпта добавила бы дубликат).

**Репроигрывание стендов (было → стало).** к1 (`HH_CLAIM`, связь на страницу ключевой ставки
SberCIB + связь на обзор HR-Ratings): было — операции приняты, гейт значения молчал; стало —
связь без значения снята именованно (`evidence_link[0]: evidence 0 does not contain the value
of claim 0`), перепроверка остаётся с честной связью, оценка не повышается. Стендовая форма
(обе связи на порочащую страницу — аудит 64837cb5): операция снимается полностью с причиной
`claim[0]: no linked evidence contains the claim value`. к2 (формулировка C1 CREATED + страница
finmarket 6727171): новая формулировка отклонялась и раньше; стало — та же проверка идёт и по
перепроверкам: обращённая/явная перепроверка без периода на связи получает отказ
(«период «октябре 2026» не найден рядом со значением…»), ровно один раунд, исправленная
формулировка с периодом из текста принимается; без ключа гейта — тишина (прежнее поведение
закреплено тестом). к3 (b7e93ff6): было — принято пустое предложение с объявлениями в сводке;
стало — ровно один отказ с именованной причиной и один раунд; после честного пустого ответа —
принятие с опорой из `[c:…]` rationale исследователя (стенд: в реальном rationale ссылок не
было — там хост остаётся честно тихим, объявление видно в журнале по kind отказа). к4
(33a62678): ВЕРДИКТ — отказ честный, гейт не менялся: на странице terbuny/SuperJob период
«II квартал 2026 года» назван только в заголовке (более 1100 знаков от окна значения «+7,5%»),
целевой период «за 2026 год» подтверждён таблицей («∆ за 2026/25»), рядом со значением — «по
итогам мая 2026»; стендовая строка отказа закреплена байт-в-байт (на базе индекс `claim[4]`,
в репроигрывании `claim[0]` — сдвиг индекса задокументирован), исправленная формулировка с
периодом из текста принимается.

**Журнал изменённых ожиданий (было → стало).** (а)
`tests/unit/test_curator_period_gate_t7121.py::test_reverify_is_checked_and_already_dropped_operations_are_skipped`:
было — перепроверка вне гейта (`{}`), стало — проверена (`claim[0]: период «октябре 2026» …`);
тест переименован. (б) `tests/scenario/test_curator_correction_round_t7106.py` (т1): было —
две операции приняты (утверждение «10,3 резюме на одну вакансию» проходило), 2 отказа; стало —
принята одна, три отказанные операции (claims 1/2/3), голое значение гейтится. (в) тот же файл,
гейт 2: было — исправленный ответ переиспользовал короткие страницы; стало — обязан связаться
со страницей, несущей значение («Веер», конкуренция).

**Красность на базе `1fb52f8`** (временный detached worktree, удалён): полные новые файлы —
2 failed (ожидания (а)(б)) + 2 collection errors (отсутствующий модуль/импорты); поведенческие
пробы unit — 3 failed 1 passed (`TypeError: links_without_claim_value() got an unexpected
keyword argument 'statement_overrides'`; молчание пустого профиля закреплено зелёным и на базе);
пробы scenario — к1a/к1b/rev2 красные, якоря к2a/к2b/rev1/k4 зелёные (закрепляют базово-верное
поведение намеренно).

**§6:** baseline базы 3085 passed + 12 skipped (+ timing 4) → после реализации 3107 passed,
12 skipped (`-n auto -m "not timing"`, 273.75 с) + timing 4 passed, 3119 deselected (47.86 с);
добавлено 22 теста: unit 14 (`test_gates_every_link_t7123.py` 10,
`test_curator_declared_empty_claims_t7123.py` 4) + scenario 8
(`test_curator_link_gates_replay_t7123.py` 7, `test_curator_declared_empty_session_t7123.py` 1).
Фикстуры страниц — `tests/fixtures/t7123/` (SberCIB ключевая ставка, HR-Ratings hh-индекс,
обзор SuperJob; побайтовые нормализованные тексты реальных страниц).
## T7.124 (2026-10-19): аудит уже записанных связей — план «значение на связи» без единой записи; честного существующего пути снять записанную связь нет (коммит `33215b7` — части 2 и 3; документация — следующий коммит; ADR-0038 §13)

**Часть 1 (анализ, кода нет) — почему применения нет.** Граница ADR-0038 §12 («ранее записанные
связи гейты не затрагивают никогда») оставлена в силе, но дыра остаётся посчитанной: стенд
`83e7a1c8` держит `E3 supported` 0.75 на двух группах независимости, одна из которых — связи к
странице SberCIB про решение ЦБ по ключевой ставке, где значения 9,6 нет вовсе (десятичные
токены страницы: 11,6 / 5,3 / 6,3 / 4,6 / 0,1). Перебраны все механизмы, которыми хост уже
умеет трогать записанное знание: у таблицы `evidence` нет ни признака «исключена из оценки»,
ни `status` (`migrations/versions/0004_memory.py:74-99`, правок её колонок не было вовсе); смена
`relation` врёт об опровержении; `counterevidence_resolutions` снимает с оценки только `counters`
и только по счётчикам (`migrations/versions/0012_counter_resolutions.py:52-77`,
`packages/memory/rules_engine.py:159-164`); `assessment_evidence.role` — механическая копия
`relation` и правилами не читается (`packages/memory/reassessment.py:567-571`); правила и
группировка берут улики напрямую из `evidence` без фильтра
(`packages/memory/rules_engine.py:145-260`, `packages/memory/source_graph.py:172-192`); сужение
`scope` сам хост называет нечестным понижением через другую дверь
(`packages/memory/reverify.py:40-44`); операторская коррекция графа источников приписала бы
странице ключевой ставки ложное происхождение (ADR-0031); `claim_withdrawals` (ADR-0031 §6,
вариант 3) снимает утверждение целиком вместе с честными связями. **Вывод: снятие уже записанной
связи — отдельное решение пользователя (миграция плюс предикат в одном месте сборки оценки),
минимальный честный вариант описан в ADR-0038 §13.4; задача делает только законную половину —
аудит и план.** Опции `--apply` у новой команды нет намеренно: заглушки, которая всегда
отказывает, AGENTS §4 запрещает.

**Части 2 и 3 (коммит `33215b7`).** (а) Из рабочего гейта записи
`apps/orchestrator/curator_rejections.py::links_without_claim_value` извлечены чистые функции
`link_value_gate` (:203, исход `unchecked` / `present` / `absent`) и `statement_value_profile`
(:227, измеренный профиль, при его пустоте — «голые» десятичные токены T7.120); цикл гейта
вызывает их, строки причин байт-прежние, поведение не изменилось (паритет закреплён тестом).
(б) Новый read-only модуль `apps/orchestrator/link_audit.py` (741 строка):
`audit_recorded_links(db, store, since=…, claim_ids=…)` — охват по головам текущего снимка
правил (`assessment_state == «current»` плюс `config_snapshot_id` снапшота, :484-485 — тот же
охват, что у предпросмотра T7.117), исход каждой записанной `supports`-связи `source_assertion`
тем же `link_value_gate` по целому сохранённому тексту страницы (текст берётся из
`sources.metadata.normalized_sha256`, затем из payload ближайшего `research_fetch_completed`
улики; нет текста → именованный исход «текста нет», и связь НЕ исключается из пересчёта), и
гипотетический пересчёт: сборка улики без проваливших связей (`_assembly_without`) теми же
чистыми функциями группировки (`grouping_arguments`, `group_source_graph`) и теми же правилами
(`evaluate`, окружные группы — из записанного снимка `environment_independence`, переоценка
ведёт все источные улики утверждения). Утверждения под действующей коррекцией оператора
(ADR-0031) пропускаются с названной причиной. Записей нет ни одного вида: ни `audit_events`, ни
staging, ни `apply_source_graph_change` (проверено счётниками девяти таблиц до и после).
(в) `hostctl/cli.py:555-724` — команда `link-audit` (в документах `noezemactl link-audit`):
`--since <ISO>` (утверждения, у которых есть связь, записанная не раньше даты) и `--claims
<uuid,…>`; печатает по каждой связи её исход и профиль значения, участников групп, которые
ушли бы, «группы было (N) → группы стало (N)» и «оценка было: … → стало: …», затем итог и
обязательную строку о том, что запись не выполнена и где лежат варианты (§13 ADR-0038).

**Репроигрывание 83e7a1c8 (было → стало).** Было (записано на стенде): три связи — две на
страницу ключевой ставки chunk-0/chunk-1 и одна на hr-ratings; голова `E3 supported` 0.75, две
группы независимости (SberCIB, HR-Ratings). План T7.124: две связи — «нет значения», третья —
«значение есть»; из участников групп ушёл бы один источник (SberCIB); группы было (2) → стало
(1); оценка было `E3 supported` 0.75 → стало `E1 hypothesis` (правила external_fact:
`min_independence_groups = 2`, `min_support_evidence = 2`; config-v28 не менялся). В БД после
прогона не изменилось ничего: голова осталась `E3 supported`, счётники таблиц идентичны,
повторный прогон даёт побайтово тот же план.

**Проба по всем 53 стендам (т7).** Тот же чистый гейт на фикстуре
`tests/fixtures/t7121a/stand_claims_with_pages.jsonl` называет ровно 6 связей пробы менеджера и
ничего больше (из 80 пар «утверждение → страница»; 2 утверждения без числового профиля — вне
проверки). Разбор закреплён тестом и ADR-0038 §13.6: `7cfbe942` ×3 (rosstat) — ложные связи
(значения 2,2 нет вовсе; «2.2» встречается только приклеенным к датам «31.12.2030» и размерам
«52.22 Кб»); `9266248e` → nbj.ru и `e189c80d` → garant.ru — ограничения проверки (страница
называет 5,59 % округлённо «с 9,5% до 5,6%»; «5,59» только внутри индексной формы «105,59%»);
`4e7ba8c2` → finmarket.ru — диапазон 2,1–2,2 % подтверждается только целиком (T7.113).
Ограничения **не исправлены**: окна значения, формы чисел и пороги — стоп-критерий задачи.
Отдельно зафиксированный дефект вне юрисдикции: нормализованный текст страниц garant.ru и
finmarket сохранён с replacement-символами (распознавание кодировки) — числа читаются, русские
слова нет.

**Красность на базе `9d2a4b1`** (временный detached worktree `/tmp/wt7124`, удалён): оба новых
файла красные на сборе — `ModuleNotFoundError: No module named 'apps.orchestrator.link_audit'`
и `ImportError: cannot import name 'VALUE_GATE_ABSENT' from 'apps.orchestrator.curator_rejections'`.

**§6:** baseline базы 3107 passed + 12 skipped (+ timing 4) → после реализации 3120 passed,
12 skipped (`-n auto -m "not timing"`, 287.57 с) + timing 4 passed, 3132 deselected (48.03 с);
добавлено 13 тестов: unit 3 (`tests/unit/test_stand_fixture_link_verdicts_t7124.py`) + scenario
10 (`tests/scenario/test_link_audit_t7124.py`). Посев «уже записанных» связей в тестах честный:
staging плюс `MemoryService.apply_claim_staging` (тот же commit-boundary код), потому что
границы куратора T7.108/T7.123 такие связи сегодня не пропустили бы. Остаток после прогонов:
контейнеров 41 (норма), БД `noezema_mig_*` 40, `noezema_tpl_*` 0. Ни миграций, ни нового
AuditEventType, ни правок правил/порогов/группировки, ни изменения промптов и конфигов.

## T7.125 (2026-10-19): отзыв уже записанной связи улики — `evidence_link_revocations`, предикат на входе оценки и группировки, `link-audit --apply` и `link-audit-cancel` (миграция 0026; ADR-0039; ADR-0038 §14)

**Решение пользователя получено.** T7.124 закончил половину: план есть, применения нет, потому что
«снятие уже записанной связи — отдельное решение пользователя» (ADR-0038 §13.4 требовал миграции и
этого решения). Постановка T7.125 («отзыв связи улики») и есть это решение; строки T7.125 в
`docs/PLAN_FROM_SCRATCH.md` нет — нумерация продолжается за пределами написанного плана (как
T7.121a…T7.124). Прежний вывод о нечестности подмен (`relation`, `scope`, `roles`) остаётся в силе:
реализован ровно вариант §13.4 — таблица отзывов плюс предикат на входе, а не правка фактов.

**Часть 1 (миграция 0026 и модель).** `migrations/versions/0026_link_revocations.py`:
`evidence_link_revocations` по образцу `counterevidence_resolutions` (миграция 0012) — `evidence_id`
и `claim_id` с `ON DELETE CASCADE`, непустая `reason` (CHECK), машиночитаемый `basis jsonb`,
`actor`, `rules_version`, `created_at`, колонки отмены `cancelled_at`/`cancelled_by`/`cancel_reason`
(CHECK тройки: живут только вместе, `cancel_reason` непустая); действующий отзыв один на улику —
частичный unique `uq_link_revocations_active (evidence_id) WHERE cancelled_at IS NULL`
(:88), индексы по улике и по утверждению. ORM — `ORMLinkRevocation`
(`packages/domain/models/memory.py:353`). Два новых значения `AuditEventType`
(`LINK_REVOCATION_CREATED`, `LINK_REVOCATION_CANCELLED`, `packages/domain/models/enums.py:555-556`):
у `audit_events.type` CHECK в схеме нет, поэтому второй миграции не потребовалось (закреплено
тестом как отсутствие ограничения); подписи витрины — `apps/web/labels.py:1097` («связь улики
отозвана») и `:1103` («отзыв связи улики отменён»). Закрытый CHECK
`claim_assessment_heads_prepared_by_check` расширен новым значением `system:link_revocation` — той
же процедурой, что 0007/0011/0012/**0022** (DROP плюс ADD со всем прежним перечнем).

**Часть 2 (предикат ровно на ВХОДЕ).** `active_link_revocation_exclusion(column)`
(`packages/memory/source_graph.py:62-75`) — `NOT EXISTS` по действующему отзыву; применён в двух
местах сборки оценки: селект улик для переоценки (`packages/memory/reassessment.py:406-416`) и вход
группировщика (`packages/memory/source_graph.py:213` — источные улики утверждения) вместе с
записанными родителями значения (`:101`). Пороги, `claim_type_rules`, шкала оценок, алгоритм
группировки и его пороги склейки, промпты и конфиги не изменены ни на байт; второй копии предиката
нет; строки `evidence` не UPDATE-ятся и не DELETE-ятся, новой колонки у `evidence` нет. Окружной
снимок независимости (`packages/memory/env_independence.py:169-189`) не фильтрован осознанно: он
считает группы по `environment_manifest_id`, а host-связи чтения этого признака не несут (в
`apps/orchestrator/evidence.py` его присваивания нет) — отзывов, которых там можно было бы не
учесть, для связей чтения нет.

**Часть 3 (применение, отмена, команды).** `apps/orchestrator/link_audit.py:823-1419` —
`apply_link_revocations` (:993) и `cancel_link_revocations` (:1317): одна транзакция, аудит в той же
транзакции (§3), перед записью — проверка всех перечисленных улик, любой отказ отменяет весь вызов
(`LinkRevocationError` со списком именованных отказов :835-843: нет улики; нет утверждения; вне
юрисдикции гейта; «значение на странице есть»; «текста нет»; нет измеренного значения; под
коррекцией оператора ADR-0031; нет действующей головы; «не останется ни одной улики» — только с
явным `--allow-empty`). `basis` строит основание тем же чистым гейтом записи
(`link_value_gate`/`statement_value_profile`, `apps/orchestrator/curator_rejections.py:198-234`) по
целой сохранённой странице: вердикт, профиль значения, `relation`, `evidence_kind`, canonical URI,
`page_sha256`, `config_snapshot_id` и его хэш, версия движка правил. Каскад `_revocation_cascade_claim`
(:935-979) — механизм ADR-0029 §22: голова `pending` с `current_assessment_id`/`epistemic_status`
NULL (§3 Lifecycle) с `AND assessment_state = 'current' RETURNING`, ровно одна durable-задача
`reassessment_jobs` (причина `link_revocation_change`), `prepared_by = 'system:link_revocation'`;
доменные ревизии не повышаются, оценку назначает не отзыв, а батч `run_reassessment_batch`
(`packages/memory/reassessment.py:777`), который вызывает вызывающая сторона.
`hostctl/cli.py:555` (`link-audit`) получил `--apply --evidence <uuid,…> --reason "…"` плюс
`--allow-empty`, `hostctl/cli.py:902` — новая команда `link-audit-cancel --revocation <uuid,…>
--reason "…"`; обе заканчиваются закрытием engine (`_closed()`), иначе teardown фикстуры сообщает о
живом соединении scratch-БД. Read-only поведение T7.124 не изменено: без `--apply` по-прежнему
только план и ни одной записи; план теперь показывает уже отозванные связи (`LinkAuditRow.revoked_links`
:212-216, `LinkAuditReport.links_revoked` :267-270) отдельной строкой с причиной и актором, новых
вердикт-строк нет — прежние утверждения T7.124 про план истинны. План по-прежнему не пишет ничего.

**Репроигрывание 83e7a1c8 (было → стало после применения).** Было: `E3 supported` 0.75, две группы
независимости (SberCIB, HR-Ratings). Отзыв двух связей к странице ключевой ставки (`--allow-empty`
не нужен: связь на hr-ratings остаётся) → каскад «снятых голов: 1; задач переоценки заведено: 1» →
рабочий батч: `E1 hypothesis` 0.15, одна группа `{hr-ratings…}`; строки `evidence` побайтно прежние
(`assertion_text`, `scope`, `source_id`, `chunk_id`, `created_at`), `assessment_evidence` прежней
оценки не тронуты, аудит — `link_revocation_created`. Повторный отзыв тех же улик: «уже отозвано
связей: 2», каскад (0, 0), записей нет. Отмена отзывов возвращает `E3 supported` и обе группы;
отмена при уже `pending` голове и живой задаче даёт (0, 0) — второй задачи заводить нечем, батч
после неё всё равно восстанавливает оценку.

**Найденное по дороге (свои же красноты полной проверки).** Первая полная проверка §6 провалилась в
трёх тестах, и оба класса — следствия этого коммита, а не чужие дефекты: (а) новые `AuditEventType`
не получили подписей витрины → `tests/unit/test_web_labels.py::test_every_source_value_has_a_label`
и `::test_audit_event_labels_cover_every_audit_type` («значения без подписи»); (б) первая версия
миграции 0026 перечислила прежние значения CHECK `prepared_by` **без `commit_carryover`** (его
добавила миграция 0022, им пользуется `packages/domain/services/commit.py:320`) → красный
`tests/scenario/test_quiesce_race.py::test_commit_after_flip_carries_claims_to_active_snapshot`
(`CheckViolationError`). Исправлено: полный прежний перечень плюс guard-тест, сравнивающий множества
значений CHECK до и после миграции и пробующий записать голову на каждом прежнем значении.

**Ограничения, закреплённые тестами как факты (не правятся).** Откат `downgrade -1` падает
`CheckViolationError`, если в базе живёт голова, подготовленная отзывом: миграция возвращает прежний
CHECK и Postgres проверяет его по существующим строкам; тест называет отказ и проходит откат дальше
только после снятия голов (тот же предел, что у миграции 0012).

**Красность на базе `b005dbf`** (временный detached worktree `/home/denis/dsh1/.wt7125`, удалён):
поведенческий файл красный на сборе — `ImportError: cannot import name 'REFUSAL_LAST_EVIDENCE' from
'apps.orchestrator.link_audit'`; файл миграции — `5 failed, 1 passed`
(`UndefinedTableError: relation "evidence_link_revocations" does not exist`); тот один зелёный на
базе тест документирует отсутствие CHECK у `audit_events.type`, а не работу новой механики.

**§6:** baseline базы 3120 passed + 12 skipped (+ timing 4 passed, 3132 deselected) → после
реализации 3139 passed, 12 skipped (`-n auto -m "not timing"`, 292.83 с; пересчёт после
документационной правки — 296.71 с) + timing 4 passed, 3151 deselected (48.05 с / 47.90 с); добавлено 19 тестов: 13 scenario
(`tests/scenario/test_link_revocations_t7125.py` — репроигрывание 83e7a1c8, все отказы, повтор,
«последняя улика» с `--allow-empty` и её итог `insufficient_data`, отзыв/отмена на входе
`load_source_graph_assembly`, три CliRunner-теста) и 6 scenario
(`tests/scenario/test_link_revocations_migration_t7125.py` — up/down/up с побайтовой схемой,
расширение CHECK плюс guard множеств и честный отказ отката, частичный unique и повторный отзыв
после отмены, тройка колонок отмены, `ON DELETE CASCADE`, отсутствие CHECK у `audit_events.type`).
Тест T7.124 переименован (`test_cli_link_audit_offers_no_apply_and_rejects_bad_parameters` →
`test_cli_link_audit_is_read_only_by_default_and_rejects_bad_parameters`) с HISTORY-пометкой: прежняя
формулировка «применения нет намеренно» с T7.125 неистинна, assertions (неполное `--apply` → отказ с
названной причиной, записей нет; кривые `--since`/`--claims` → `EXIT_USAGE_ERROR`) те же и зелёные.
Остаток после прогонов: контейнеров 41 (норма), БД `noezema_mig_*` 40, `noezema_tpl_*` 0.

**Остаток (названо, не сделано).** Витрина отдельной строки об отзыве НЕ сообщает: карточка
утверждения перечисляет все его улики как факты журнала и берёт оценку из головы, которая отзыва не
содержит; отдельной строки «связь отозвана, причина …» и счётчика отзывов в API нет — это решение о
представлении, отдельно от решения об отзыве (ADR-0039 §6). **Закрыто отдельно: T7.126 (часть в),
раздел ниже — строка в карточке есть; счётчика отзывов в API по-прежнему нет, и он не требовался.**
Пакетного снятия по вердиктам нет по
причине фактических данных (ADR-0039 §4), ограничения проверки ADR-0038 §13.6 не исправлены,
отзывом снимается только `supports`-связь `source_assertion` (для опровержений есть
`counterevidence_resolutions`). Правил, порогов, промптов и конфигов этот коммит не касается;
миграция 0026 — единственная.

## T7.126 (2026-10-19): три правки по фактическим случаям стенда `.92` — pairing сверяет показатель утверждения, гейт дублей видит подмножество значений и одиночного кандидата, витрина называет отозванную связь (решение пользователя 2026-10-19; ADR-0029 §25, ADR-0033 §16, ADR-0039 §9)

Все три случая — следы проверки менеджера на `.92` (сессия 8181a76a и отзывы T7.125), а не
домыслы: тексты утверждений и страниц взяты SELECT'ом в фикстуры `tests/fixtures/t7126/`
(`rambler_57077146_realnye_zarplaty_2026.normalized.txt` + `.url`, `claims_dup_pairs.jsonl`).
Проверка на живом стенде — действие менеджера; честный локальный эквивалент, сделанный здесь, —
репроигрывание той же формы утверждений и страниц (стендовые `83e7a1c8`, `b262da08`, `5538afc2`,
`841224a7`, `7cfbe942`) на этих фикстурах.

**Было (одна причина в трёх местах): хост сравнивал числа, а не факты.** Разные факты одной страницы
склеивались по совпавшему числу; то же самое наблюдение в двух формулировках не склеивалось; отозванная
связь на карточке выглядела уликой.

### Часть а — pairing «значение + производитель» обязан быть совместим с показателем утверждения (ADR-0029 §25, метод v15 → v16)

- **Было:** `b262da08` «По данным hh.ru, … hh-индекс … составил 8,7.» получил запись «Опубликовано
  производителем (Росстат)» с основанием «По данным Росстата, реальные располагаемые доходы … выросли
  … лишь на 1,5% год к году — против 8,7% за тот же период 2025 года». Проверка pairing сводилась к
  «во фрагменте назван первоисточник» + «во фрагменте есть число утверждения» (T7.109a/T7.120) и не
  различала два разных факта одной страницы: `8,7` (безразмерное) против `8,7%` (процент другой величины).
- **Стало:** после числового фильтра добавлен второй (`_fragment_confirms_indicator`,
  `apps/research_proxy/value_attribution.py:1906`, вызов `:1973`): **(а)** та же размерность единицы —
  совместимо всегда (`_unit_dimension` `:331`, `_unit_dimension_after` `:340`, стволы
  `_UNIT_DIMENSION_STEMS` `:316`, `_text_value_units` `:680`); **(б)** иначе обязано пересечение значимых
  основ показателя утверждения и фрагмента. Словарь основ перенесён в `value_attribution.py:1758-1850` и
  импортируется гейтом дублей (`apps/orchestrator/value_duplicate.py:98-103`): второй копии словаря нет.
  Правило только снимает пары; `attributed` и числовой фильтр не тронуты.
- **Следствие, названное открыто:** снятая ложная пара перестаёт называть первоисточника для
  разночтения pairing, поэтому честный исход этой страницы — «атрибуции нет», а не выдуманный
  производитель (`test_rambler_rosstat_fragment_is_no_longer_the_producer_of_the_hh_value`).
- **Уже записанные строки:** `VALUE_ATTRIBUTION_METHOD_VERSION` v15 → v16 (`:269`); действующий гейт
  пересмотра `record_pairing_gate_passes` (`:1984`) применяет ту же совместимость к собственному
  основанию записанной строки, и ложная запись эпохи v15 его не проходит
  (`test_record_pairing_gate_revokes_the_stand_rosstat_basis`) — снимается тем же путём, что ложная
  запись T7.120a. На стенде это исполнит переатрибуция (действие менеджера), не тест.

### Часть б — гейт почти-дублей: подмножество значений и одиночный необъявленный кандидат (ADR-0033 §16)

- **Было:** `find_value_duplicates` требовал **равенства** множеств значений → пара `b262da08`
  (`{8,7}`) / `5538afc2` (`{8,7; 6,1}`) не склеивалась, и на карточке лежали две строки того же числа
  того же периода; ветка T7.85 (тот же текст, другой тип) не видела пару, отличную скобочной оговоркой
  (`841224a7` «…составил 2,2% по данным Росстата.» `temporal_fact` / `7cfbe942` «…(ILO methodology).»
  `external_fact`). Отдельно: найденный, но не объявленный кураторской опорой одиночный кандидат
  (§7.1) отклонялся — и коммит писал **вторую строку того же числа**.
- **Стало:** значения операции обязаны быть подмножеством значений кандидата
  (`apps/orchestrator/value_duplicate.py:968`; обратное направление — другое наблюдение, молчание);
  причина решения называет оба множества (`:1136-1142`); одиночный строгий кандидат вне
  `relied_claim_ids` склеивается с именованной причиной («ONLY candidate …», `:1120-1129`) — фильтр опоры
  защищал от выбора между несколькими кандидатами; добавлена скобочная ветка (`_without_parentheticals`
  `:706`, `_relabel_after_parentheticals` `:734`), требующая больше, чем побайтовая ветка T7.85:
  равные значения оригиналов, покрытый период без конфликта названных месяцев, совпавший показатель по
  оригиналам, ровно один кандидат, другой тип, те же условия улик. Витринный предикат
  `value_duplicate_pair` (`:1197`) читает то же направление.
- **Не ослаблено:** два строгих кандидата без опоры — прежняя неоднозначность; mutual-кластер T7.121 —
  прежний якорь ранейшей записи; guard названных месяцев, вето показателя, пороги перекрытия основ,
  монотонность склейки (§3) и витрина без условий улик — прежние.

### Часть в — витрина называет отозванную связь (закрыт остаток ADR-0039 §6)

- **Было:** карточка утверждения перечисляла все его улики как факты журнала: отозванная связь
  выглядела подтверждением, «происхождение» включало её хост в независимые группы, отдельной строки об
  отзыве не было (§6 ADR-0039).
- **Стало (ADR-0039 §9):** один SQL-фрагмент на все витрины — `LINK_REVOCATION_FACT_COLUMNS` и
  `link_revocation_join` (`apps/web/knowledge.py:202-230`, `LEFT JOIN LATERAL … cancelled_at IS NULL`,
  тот же признак действующего отзыва, что у `packages/memory/source_graph.py:62`) — в запросе фактов
  улик, питающем подписи списка знаний и карточек (`current_assessment_explanation`, `:352-358`), в
  уликах карточки утверждения (`:612-618`), в «происхождении» (`:808-814`) и в уликах карточки ответа
  (`apps/web/answer.py:1267-1273`). Строку составляет сервер (`revoked_link_note`,
  `revoked_link_notes`, `knowledge.py:252-275`): «связь отозвана оператором: <причина> (отзыв <id>,
  <дата>)»; карточки только печатают (`apps/web/api.py:627`, `:893`, `:919-920`); id и дата — из записей
  журнала отзыва. Отозванная связь не подтверждение: `link_is_revoked` (`knowledge.py:277`) убирает её из
  счётчика ответа (`evidence_kind_counts`, `answer.py:477-493`) и из формулировки «как проверено»
  (`describe_verification` по живым связям, `knowledge.py:1009-1030`), список источников карточки её
  пропускает (`api.py:988`). Журнал при этом не переписывается: улика остаётся в списке помеченной
  `link_revocation`; отменённый отзыв (вторая append-запись T7.125) с карточки уходит, и связь возвращается
  в подтверждение и в группы. Fail-closed: строка без колонок JOIN'а = «отзыва нет», пустая причина
  называется пустой («причина не названа»).
- **Миграций, правил, порогов, промптов и конфигов эта часть не потребовала** (отзыв уже снимал связь с
  входа оценки и группировки — витрина обязана была перестать молчать).

### Тесты (27 новых + правки прежних) и красность на прежнем коде

- `tests/unit/test_value_attribution_indicator_t7126.py` (8) — стендовая пара Росстат/hh-индекс,
  отзыв уже записанного основания v15, «та же единица — совместимо всегда», «голые» значения,
  «то же число, другой показатель» = честный отказ, словарь основ принадлежит `value_attribution`
  (guard против второй копии).
- `tests/unit/test_value_duplicate_subset_reliance_t7126.py` (9) — склейка пары `{8,7} ⊂ {8,7; 6,1}`
  (гейт и витрина), обратное направление молчит, два кандидата — неоднозначность, конфликт месяцев,
  скобочная оговорка с relabel, её требования (значения оригиналов, чужой показатель), молчание
  несвязанной формулировки.
- `tests/unit/test_web_link_revoked_note_t7126.py` (7) — форма строки (причина, id отзыва, дата из
  записей журнала), пустая причина, «колонок нет = отзыва нет», `evidence_kind_counts` не считает
  отозванную связь, строки JS печатают `revoked_links` и пропускают отозванный источник, JOIN читает
  действующий отзыв.
- `tests/scenario/test_web_link_revocation_card_t7126.py` (3) — репроигрывание стендовой формы
  `83e7a1c8`: отзыв двух из трёх связей → карточка `/api/v1/knowledge/claims/<id>` печатает ровно две
  строки отзыва, `evidence[].link_revocation` подписывает улики, оценка в карточке — посчитанная после
  отзыва; «происхождение» даёт те же строки, его независимые группы не содержат ни одного отозванного
  хоста, «как проверено» считает одну живую связь (`источников прочитано: 1`); отмена отзыва возвращает
  связь в подтверждение и в группы, строка с карточки уходит, запись журнала остаётся.
- Правки прежних тестов — это правки решения, а не падения: переименованы и переписаны
  `test_value_duplicate.py::test_perfect_value_match_outside_relied_claim_ids_is_left_as_proposed` →
  `…_is_merged_when_it_is_the_only_candidate`, `test_value_duplicate_t7121.py::
  test_single_not_relied_candidate_keeps_previous_refusal` → `…_is_merged_by_the_t7126_rule`,
  `test_value_duplicate_t7121.py::test_month_conflicting_candidate_leaves_strict_even_when_relied`
  (`kept` → `reverified` оставшегося одиночного кандидата), сценарий
  `test_value_duplicate_gate.py::test_value_duplicate_of_an_unrelied_candidate_is_left_as_proposed` →
  `…_is_merged_as_the_only_candidate` (второй строки того же числа нет: утверждений 3 вместо 4,
  `claim_reverified` с `anchor_kept = true`, оценка кандидата не понижена); добавлены
  `test_value_duplicate.py::test_subset_of_the_candidates_values_is_a_suspect`,
  `test_value_duplicate.py::test_op_with_a_number_the_candidate_does_not_name_stays_silent`,
  `test_value_duplicate_pair_t799.py::test_showcase_has_no_evidence_conditions_unlike_the_gate`;
  сценарий `test_value_duplicate_cluster_t7121.py` перестроен честно (две сид-сессии сегодня принесли бы
  перепроверку вместо второй строки, поэтому пара взаимных дублей записывается ОДНОЙ сессией); 24 файла
  атрибуции и переатрибуции подняли ожидаемую версию метода v15 → v16; изменений поведения в них нет —
  проверено по диффу: правки только ожидаемых строк версии метода (`host-value-attribution-v15` →
  `host-value-attribution-v16`) плюс одна переформулированная строка docstring.
- **Красность на базе `db2451d`** (detached worktree `/tmp/wt7126`, `PYTHONPATH=/tmp/wt7126`, удалён):
  часть а — `5 failed, 3 passed` (прежний метод отдаёт `derivative`/`rosstat` на стендовой паре,
  `record_pairing_gate_passes` пропускает ложное основание: `assert True is False`); часть б —
  `6 failed, 3 passed` (прежний гейт молчит на `{8,7} ⊂ {8,7; 6,1}` и отказывает одиночному
  необъявленному кандидату); часть в — unit-файл красен на сборе
  (`ImportError: cannot import name 'LINK_REVOCATION_FACT_COLUMNS' from 'apps.web.knowledge'`),
  scenario — `3 failed` (`KeyError: 'revoked_links'`). Зелёные на базе тесты в каждой части закрепляют
  границы, которые правки сознательно не снимают (два кандидата, конфликт месяцев, вето показателя,
  «колонок нет = отзыва нет»).

**§6:** baseline базы (после T7.125) 3139 passed + 12 skipped + timing 4 passed / 3151 deselected →
после реализации **3169 passed, 12 skipped** (`-n auto -m "not timing"`) и **timing 4 passed,
3181 deselected**; три прогона подряд дали 271.78 с / 258.93 с / 311.99 с и 53.99 с / 48.10 с / 47.69 с
(длительность зависит от загрузки машины, состав результата во всех трёх одинаков); добавлено 30 тестов (27 в четырёх новых файлах плюс 3 новых в прежних файлах гейта
дублей). `ruff check .` и `mypy packages apps hostctl` — чисто. Остаток после прогонов: контейнеров 41
(норма), БД `noezema_mig_*` 40, `noezema_tpl_*` 0.

**Остаток (названо, не сделано).** Счётчика отзывов в списках вопросов и знаний нет; отменённые отзывы на
карточке не показываются (их история — журнал, не карточка); витрина не пересчитывает группы, роли и
оценки, а читает записанное. Прежние пределы не закрыты: ограничения проверки ADR-0038 §13.6
(округление, индексная форма числа, диапазон), отзывом снимается только `supports`-связь
`source_assertion`, «текста нет» не притворяется «значения нет». Ложные строки происхождения эпохи v15
с стенда снимет переатрибуция (`reattribute`) — на стенде это действие менеджера, локально оно закреплено
тестом гейта пересмотра. Правил, порогов, промптов и конфигов весь T7.126 не касается; миграций тоже
(миграция 0026 была в T7.125). Отдельный полный прогон §6 сделан и на промежуточном дереве —
только части а и б, без части в: `ruff` и `mypy` чисто, **3159 passed, 12 skipped** (293.98 с) и
**timing 4 passed, 3171 deselected** (47.96 с): коммит частей а/б зелёный сам по себе, а не вместе с
витриной.

## T7.126a (2026-10-19): часть «д4» брифа T7.126 — усиленный защитный тест на смешанные слова, доставка кода на стенд git-бандлом вместо `git pull`, три исправленных написания и запрет `stash` для промежуточных прогонов (правила, пороги, промпты, конфиги и поведение кода не тронуты)

**Что закрывает.** В брифе T7.126 был пункт «д4»: усилить защиту от смешанных написаний (кириллица
вплотную к латинице) и убрать из README стенда шаги `git pull`. Он не был выполнен и в журнал не попал;
закрыт здесь. Изменены: один тест (новый), три текста/комментария (исправление написания), README
dev-стенда, этот журнал и AGENTS §7.

**Новый защитный тест** — `tests/unit/test_homoglyph_mixed_word_scan_t7126a.py`, маркер `unit`, 15 проверок.
Правило шире T7.110: находится ЛЮБОЕ слово (максимальный бег букв кириллицы и латиницы), в котором
кириллическая буква стоит вплотную к латинской или наоборот, — а не только смежность 20 букв-двойников
`t7110.ADJACENT`. Экранирования (шестнадцатеричные, `\`+буква) и однобуквенные диапазоны классов вырезаются
той же функцией `t7110._clean_line`, поэтому дефект «за границей слова» (урок T7.110) остаётся видимым.
Область: прежние корни `apps/`, `packages/`, `hostctl/` плюс промпты и документация, которую читает
человек: `prompts/**/*.md`, `README.md`, `AGENTS.md`, `docs/adr/`, `deploy/` (суффиксы `.py`, `.md`, `.txt`,
`.sh`, `.json`, `.yaml`, `.yml`, `.service`, `.timer`, `.target`; кеши переводчиков отсечены). Вне скана, с
причиной на каждый путь (`OUT_OF_SCOPE`): `tests/fixtures/**`, `docs/STATUS-archive.md`, `docs/eval/**`,
`docs/STATUS.md`, `ARCHITECTURE.md`, `docs/PLAN_FROM_SCRATCH.md`.

Что закреплено отдельными проверками:
- `test_scan_scope_is_not_empty` — находка про область T7.110: его `SCAN_FILES = tuple(sorted((REPO_ROOT /
  "prompts").glob("*.md")))` не матчит ни одного файла этого репозитория (все 30 промптов лежат в
  `prompts/<роль>/`), то есть проверка промптов у T7.110 была пустой. Новый тест требует, чтобы каждый
  корень дал файлы, а `prompts` — не меньше 20 (исчезнувший или опустевший корень красен);
- `test_exclusions_are_justified` и `test_exclusions_are_live` — у каждого исключения обязано быть
  обоснование не короче 30 знаков, и мёртвое исключение (его в репозитории уже нет) красное: закрытый
  список не может превратиться в склад отговорок;
- `test_scan_is_not_weaker_than_t7110` — прежние корни и прежние исключения сохранены как надмножество,
  прежний тест обязан быть зелёным, и любую строку, которую красит узкий шаблон T7.110, красит и широкий
  шаблон нового теста (ослабить прежнюю защиту молча нельзя);
- `test_web_label_test_still_has_no_allowed_hybrids` — список законных гибридов T7.103 (`apps/web/labels.py`
  и видимый HTML страниц) по-прежнему пуст;
- `test_broad_rule_is_wider_than_the_narrow_one` и параметризованный позитивный контроль (8 образцов) —
  доказано, что смежность не-двойников (латинская `n`) прежним сканом не виделась, что исправленные
  написания чистые и что законные гибриды не считаются находками; образцы дефектов собраны через `chr()`,
  чтобы сам файл теста смешанного слова не содержал.

**Находки широкого скана по `b6e1602`** — 31 вхождение, 14 уникальных написаний. Исправлены три явные
опечатки, только заменой буквы (каждое слово встречалось в репозитории ровно один раз, правка не меняет
ни строки логики):
- `packages/domain/models/memory.py:374`: «верifiable basis отзыва» → «проверяемое основание отзыва»
  (комментарий поля `basis` таблицы отзывов об улике; именно из-за этого написания новый тест краснеет на
  `b6e1602`);
- `packages/memory/dispute.py:24`: «materialизован» → «материализован» (docstring стоп-критерия; в
  репозитории принято «материализованный» — ARCHITECTURE, ADR-0006, ADR-0031);
- `deploy/dev-stand/README.md:557`: «monotонно» → «монотонно» (латинская `n` внутри русского слова;
  ADR-0033 пишет «монотонность»).
Остальные 11 написаний занесены в закрытый список `EXCLUSIONS` с причиной на каждое: «датless» (14
вхождений), «СберCIB», «сберcib» (литерал фильтра команды скана AGENTS §7), «артефактfail» (пропуск
пробела — AGENTS §7 относит пропуски пробела не к гомоглифам), «propagateция», «deriviруется», «fetchом»,
а также «группa», «pин», «сweep», «fоновая» — дословные цитаты примеров дефектного написания из раздела
о гомоглифах AGENTS §7 (замена буквы уничтожила бы пример).

Вне разрешённой области задачи (названы, не правятся): `docs/STATUS.md` — строки-ссылки с якорями
markdown к замороженным заголовкам архива («claimа», «claimов», «snapshotу»), дословные цитаты разобранных
дефектов и опечатка самой цитируемой строки; `docs/eval/**` — подлинные записи прогонов модели;
`tests/fixtures/**` — дословные цитаты страниц источника, в них подлинные опечатки самих сайтов;
`docs/PLAN_FROM_SCRATCH.md` и `ARCHITECTURE.md` — их правка требует решения пользователя (AGENTS §4),
задачей не разрешена.

**Доставка кода на стенд: одна подсекция вместо 12 копий.** В `deploy/dev-stand/README.md` добавлена
подсекция «Обновление кода на стенде (бандл, а не `git pull`)»: на машине разработки
`git fetch origin`, затем `git bundle create /tmp/noezema.bundle refs/remotes/origin/main` и перенос файла
`scp`'ом; на стенде `cd ~/noezema`, `git fetch /tmp/noezema.bundle
refs/remotes/origin/main:refs/remotes/bundle/main`, `git merge --ff-only bundle/main`. Все 12 мест, где
раньше было `git pull` (активации config-v15, v16, v17, v18, v19, v20, v27, v28, ператрибуция T7.85,
миграция 0026 и план аудита связей T7.124 — 11 блоков команд и одна строка прозы), теперь ссылаются на
эту подсекцию; повторения команд удалены. Проверено локально (вне репозитория, на клон-копии — удалена):
полный бандл `refs/remotes/origin/main` этого репозитория — 7 444 163 байта, `git bundle verify` называет
доставляемый коммит; fetch из файла + `merge --ff-only` переводит копию стенда на `b6e1602`; при
расхождении истории (`merge --ff-only` на разошедшейся ветке) — отказ с кодом 128 и нетронутом рабочим
деревом, что и записано в README как то, что решает менеджер, а не мердж. Исторический раздел «Что
остаётся непроверенным до реального деплоя» (там `git pull` не было) не тронут; тест
`tests/unit/test_dev_stand_scripts.py` проверяет, что обязательные строки README после правки на месте.

**Красность доказана на прежнем коде.** `git worktree add --detach /tmp/wt7126a b6e1602`, копия нового
теста в него: `1 failed, 14 passed` — падает `test_repo_text_has_no_mixed_cyrillic_latin_words` со списком
находок «monotонно» (README стенда), «верifiable» (memory.py), «materialизован» (dispute.py); прежние
14 проверок зелёные. Worktree удалён, копия теста из него удалена.

**Дисциплина промежуточных прогонов (новое правило AGENTS §7).** `git stash` для получения
промежуточного дерева больше не используется никогда: он снимает незакоммиченное с рабочего дерева, то
есть прячет ровно то, что должно оставаться видимым (в T7.126 промежуточное дерево получали именно так).
Красность на прежнем коммите доказывается временным detached worktree (`git worktree add --detach
/tmp/wt<номер> <sha>` → копии новых тестов → прогон → `git worktree remove`), сверка незакоммиченной
правки — копией файлов вне репозитория; «сверить и вернуть» через `git checkout -- <файл>` не применяется.

**Полные прогоны (§6).** Перед коммитом: `ruff check .` чисто; `mypy packages apps hostctl` — Success,
163 файла; образ `noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3184 passed +
12 skipped** (было 3169 + 12 — новый тест даёт ровно 15 проверок); stage 2 `timing`: **4 passed,
3196 deselected**. Контейнеров 41 → 41; scratch `noezema_mig_*` 40 → 40, шаблонов `noezema_tpl_*` 0 → 0.

**Остаток (названо, не сделано).** `docs/STATUS.md` остаётся вне автоматического скана и под ручным
сканом AGENTS §7: его строки-ссылки содержат якоря к замороженным заголовкам архива и дословные цитаты
разобранных дефектов, замена буквы в них невозможна. Список исключений закрыт по состоянию на этот
коммит: следующее смешанное написание обязано быть либо исправлено заменой буквы, либо внесено в список
с причиной. `ARCHITECTURE.md`, `docs/PLAN_FROM_SCRATCH.md`, `docs/eval/**` и `tests/fixtures/**` этой
задачей не правились и не правятся. Сам git-бандл нигде не лежит: он создаётся на машине разработки в
момент доставки, и стенд его не скачивает.

## T7.126b (2026-10-19): отзыв связи применён во ВСЕХ сборках входа оценки — предпросмотр независимости, коммит сессии, теневые головы кандидата правил и пересчёт плана (дефект разбора стенда `.92`, утверждение `83e7a1c8`; один коммит: код, тесты и документация; ADR-0039 §10)

**Что было найдено.** На стенде `.92` (коммит доставки `b6e1602`, миграция 0026, config-v28,
rules-v2) `noezemactl independence-preview` по утверждению «По данным HR-Ratings, hh-индекс в России
в 2026 году составил 9,6» показал: группы не изменились (участники те же), но «оценка было: E1
hypothesis (0.15) → стало: E3 supported (0.75)», «итог: утверждений с изменением групп: 0; с
изменением оценки: 1; проверено утверждений: 73». Срез — `tests/fixtures/t7126b/stand-83e7a1c8.json`
(формулировка, три связи, два действующих отзыва с вердиктом гейта «нет значения», записанная голова
E1 hypothesis от `reassessment_worker`, участники снимка). `--apply` записал бы этот подъём, то есть
команда, призванная проверять группы, сама предлагала бы ложное «Проверено».

**Почему.** ADR-0039 §2 утверждал, что предикат «нет действующего отзыва связи» применён «ровно НА
ВХОДЕ». Это было неверно по факту: сборок, из которых оценка утверждения получает свои улики, четыре,
а предикат стоял в двух (рабочий переоценки и вход группировщика). Отозванная связь исключена из
групп, но попадала в `evaluate`, не имела записи соответствия источника группе и получала резервную
группу `UNTRACKED_GROUP` — ровно одну лишнюю «независимую» группу; правилу `external_fact`
(`min_independence_groups = 2`) этого хватает, чтобы признать утверждение подтверждённым. Тот же
механизм давал подъём и на живом пути: коммит сессии, повторно утверждающей утверждение с отозванной
связью, записывал бы E3 в действующую голову и в `assessment_evidence`.

**Что сделано (одна общая точка, только фильтр входа).** `claim_evidence_for_assessment`
(`packages/memory/source_graph.py:79`) — улики утверждения плюс `active_link_revocation_exclusion`,
порядок по `evidence.id`. Её вызывают все четыре входа: `independence_preview.py:287` (оценка
«стало»), `service.py:1041` (коммит: `_assess`, `assessment_evidence`, `evidence_set_hash`, сводка
атрибуции), `reassessment.py:406` (поведение не изменено — делегирование), `hostctl/offline_rules.py:233`
(теневые головы кандидата правил: вход `evaluate`, `merged_scope` и `evidence_set_hash`). Узкий дефект
плана аудита: `apps/orchestrator/link_audit.py:653, :702` — уже отозванные связи возвращались в
пересчёт «стало» и в «уходящие из групп» источники (проявляется, когда у отозванной связи исход не
«нет значения»); теперь они исключены из пересчёта, но в отчёте плана названы по-прежнему. Пороги,
`claim_type_rules`, алгоритм группировки, `evaluate`, промпты, конфиги и миграции не тронуты; второй
копии предиката не заведено.

**Что осталось нефильтрованным, с обоснованием (полная таблица — ADR-0039 §10.1).** Дедуп улики по
`identity_hash` (`service.py:930-946`) — фильтр там создал бы вторую строку `evidence` без отзыва, то
есть обошёл бы решение оператора; снимок независимости окружения (улики с `environment_manifest_id`,
отзыв к ним не применяется); наборы каскада §11.3 (надмножество: лишняя инвалидация головы при
детерминированной переоценке — сужение могло бы пропустить утверждение); предикаты базы коррекций
(`dispute.py`) и разрешений спорного (`resolutions.py`) — ведут только к отказам; план аудита в части
показа и охранных проверок («останется ли хоть одна улика» считается по всем отзывам); витрина,
счётцы честности, метрики оценки качества и GC; предпроверка предложения куратора (улик из журнала там
нет). Отчёт честности подтверждает по `assessment_evidence` действующей оценки — дефект был на стороне
её записи и исправлен общей точкой; строки участия, записанные до правки, снимает рабочий батч.

**Поведение повторного предложения отозванной связи (закреплено тестом).** Новая сессия, предлагающая
ту же связь того же утверждения (тот же `identity_hash`), получает дедуп T7.78: строка `evidence` не
создаётся, действующий отзыв остаётся на ней, `evidence_deduped` растёт, оценка пересчитывается без
этой связи и не меняется (E1 hypothesis 0.15, одна группа). До правки эта же запись возвращалась в
`evaluate` и поднимала оценку до E3 supported 0.75 — переутверждение обходило бы решение оператора.
Улики, записанные этой же транзакцией, отзыва не имеют и остаются во входе (выборка идёт после
autoflush); отменённый отзыв не исключает ничего.

**Тесты (14 новых).** `tests/scenario/test_revocation_input_t7126b.py` (6) — воспроизведение стендового
случая целиком: `research.fetch` с подменённой сетью по фикстурам `tests/fixtures/t7126b/`
(`sbercib-key-rate`, `hr-ratings-hh-index` — копии страниц T7.124 байт в байт, новая вымышленная
страница `tarven-hh-index-2026`, называющая 9,6), хостовый указатель `sbercib → cbr` через
`write_derivative_pointer`, staging + `apply_claim_staging`, отзыв двух связей гейтом и рабочий батч:
т1 — предпросмотр не выдаёт ни одной строки (красен на `7092dfc` ровно стендовой строкой `persisted
E1 0.15 → new E3 0.75`); т2 — отменённый отзыв возвращает связь в группы, в оценку и в записанное
участие (зелён на `7092dfc`: путь отмены дефектным не был; guard обратной стороны правила); т3 —
коммит сессии: отозванная связь вне групп, вне `evaluate` и вне `assessment_evidence` (2 строки вместо
4), новая улика даёт вторую группу и E3 (красен: `4 != 2`); т4 — повторное предложение тех же связей не
обходит отзыв (красен: `('E3','supported') != ('E1','hypothesis')`); т5 — `evidence_set_hash` теневой
головы кандидата правил равен множеству живых улик и не равен хешу со всеми строками `evidence`
(красен); т6 — план аудита: отозванные связи названы, пересчёт = записанное, `excluded_source_uris` пуст
(красен: в списке был `sbercib.ru`). `tests/unit/test_assessment_input_single_point_t7126b.py` (8) —
guard одной точки: четыре входа вызывают общую функцию ровно один раз и сами к таблице отзывов не
обращаются; единственное определение предиката в продакшен-коде — `packages/memory/source_graph.py`,
единственная написанная вручную копия вне него — витринный показ (`apps/web/knowledge.py`); движок
правил и модуль группировки о отзывах не знают; тело общей функции фильтрует только действующие
отзывы этого утверждения и упорядочено по `evidence.id`; дедуп-выборка по `identity_hash`
нефильтрована. Красны на `7092dfc` 5 проверок из 8 (четыре параметризованных входа и тело общей
функции); зелёные 3 закрепляют инварианты, которые соблюдались и раньше.

**Красность доказана на прежнем коде.** `git worktree add --detach /tmp/wt7126b 7092dfc`, копии новых
тестов и `tests/fixtures/t7126b/` в него: **10 failed, 4 passed**; worktree удалён. История не
переписывалась, `git stash` не использовался.

**Прежние тесты не ослаблены.** `tests/scenario/test_link_revocations_t7125.py` (в том числе т6 «план
называет уже отозванные связи без пересчёта»), `tests/scenario/test_link_revocations_migration_t7125.py`,
`tests/scenario/test_link_audit_t7124.py`, `tests/scenario/test_independence_preview_t7117.py`,
`tests/scenario/test_reassessment.py`, `tests/scenario/test_research_evidence.py`, стражи гомоглифов
T7.110 и T7.126a — зелёные; количество тестов выросло (14 новых проверок), ни один прежний тест не
изменён и не удалён.

**Полные прогоны (§6).** Перед коммитом: `ruff check .` чисто; `mypy packages apps hostctl` — Success,
163 файла; образ `noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3198 passed
+ 12 skipped** (было 3184 + 12 — ровно 14 новых проверок); stage 2 `timing` отдельным последовательным
прогоном: **4 passed, 3210 deselected**.

**Остаток (названо, не сделано).** Строки участия оценки, записанные до этой правки, могут нести
отозванную связь — они снимаются ближайшим рабочим переоценкой батчем, массовой правки журнала не
было и не планируется (история не правится). Настенная проверка именно этого исправления (прогон
`independence-preview` на `.92`) — действие менеджера: агент к стенду не обращается. Ограничения
гейта значения (округление, индексная форма числа, диапазон) и `supports`-юрисдикция отзыва — прежний
остаток ADR-0039 §6 / ADR-0038 §13.6, этой задачей не закрывался.

## T7.127 (2026-10-20): отказ по лимиту поиска узла стал понятен модели; отказы учтены в отчёте честности отдельной метрикой (payload config-v29 подготовлен)

**Замер перед кодом (read-only, стенд не тронут).** Прогон менеджера на `.92` 2026-10-20 (база `accd7e2`,
активен config-v28): журнал `research_upstream_request` и `research_fetch_rejected` — 22 успешных
upstream-запроса и 7 отказов лимита за 5 сессий. Поисков на сессию — 7, 8, 10, 4 и 0 (минимум 0, медиана 7,
максимум 10, всего 29 вызова `web.search`); по часам — 10:xx = 7 запросов, 11:xx = 22 запроса при потолке
`research_proxy.rate_limit_max = 20` и окне `rate_limit_window_seconds = 3600`. Первый upstream-запрос —
10:48:42, двадцатый успешный — 11:28:42, первый отказ — 11:32:40, последний отказ — 11:45:54, последний
успешный — 11:52:46: окно скользящее, к 11:32 в нём накопилось больше двадцати запросов, и дальше отказы
чередовались с успехами по мере выхода старых запросов из окна. После отказа модель не останавливалась:
в сессии c3 за первым отказом идут ещё 6 шагов, из них 4 поиска (`web.search, research.fetch, web.search,
research.fetch, web.search, web.search`), в c4 — 7 шагов, из них 3 поиска. Бюджет шагов (20 для вопросов
вида «официальные + независимые», T7.119) к этому моменту не исчерпан, то есть лимит узла упирался в себя
раньше бюджета и сжигал шаги на заведомо отклоняемые вызовы. Отсюда предложение: потолок 60 (примерно три
пиковых часа; при окне 3600 с ограничение не снимается, а поднимается до уровня, которого этот прогон не
достигал). **60 — ПРЕДЛОЖЕНИЕ по замеру, а не вывод из правила**: дефолт `ModePolicy.rate_limit_max` в коде
остаётся 20, новых правил и порогов не заводилось.

**Payload config-v29 (только подготовлен, не активирован).** `docs/eval/config-v29-payload.json` = config-v28
ровно с одной правкой — `research_proxy.rate_limit_max` 20 -> 60; окно 3600 с не менялось, новых ключей в
секции нет, разницей файлов является ровно одна строка
(`    "rate_limit_max": 20,` -> `    "rate_limit_max": 60,`). Canonical v29 — `e0844bafb2b1ed3ce1fb2dec0da8dfbaa91d1b09db843014f89eef3e3d990ecc`,
хеш payload-файла — другое число (`ea6e042a4c809dbc432805fa70fe3a566742a153352303da4ecae3162c909321`);
config-v28 не тронут (canonical `8db816ee…`, хеш файла `82ffe53a…`). **Активация config-v29 — решение
менеджера**: агент payload не активировал, к стенду и его базе не обращался; откатом v29 служит payload v28.

**Отказ, который понимает исследователь.** Машинный код прежний — `ResearchProxyError(..., "rate_limited")`
(HTTP 429 в прокси), журнальная строка прежняя — `research_fetch_rejected{reason:"upstream_rate_limit_exceeded"}`;
в payload строки добавлен только ключ `window_seconds` (содержание payload: новых AuditEventType, новых
колонок и миграций нет). Текст наблюдения строит чистая функция `apps/research_proxy/search.py::rate_limit_notice`
(её вызывает `apps/research_proxy/service.py::_search_upstream`), дословно:

```
внешний поиск отклонён: лимит поиска узла исчерпан — {requests} из {limit} {запрос/запроса/запросов} за окно
{window_seconds} с ({window_minutes} мин); ближайшее освобождение — примерно через {minutes} мин; до этого
времени дальнейшие поиски тоже будут отклонены — используй уже найденные адреса (research.fetch) или
заканчивай с тем, что найдено. Код отказа: rate_limited.
```

Минуты считает чистая функция `minutes_until_release` = `ceil((oldest + window_seconds - now) / 60)` не меньше
нуля, где `oldest` — время ранейшего запроса в окне, `now` — часы БД (окно читают одним запросом
`upstream_window_state`: число запросов в окне, `min(occurred_at)`, `now()`; `count_upstream_requests`
сохранён как тонкая обёртка, публичный контракт модуля не сломан). При пустом окне срок не выдумывается —
текст говорит, что предсказать нечего по чему. Текст держится в 500 знаках: оркестратор режет строку действия
как `str(exc)[:500]`, и более длинный текст потерял бы конец с выходом. Русские окончания чисел считает
`_plural` (1 запрос / 22 запроса / 1000 запросов).

**Отчёт честности (T7.118, только чтение).** Аддитивная метрика `searches_rate_limited` в `SessionSteps`,
в `_compute_totals` и в markdown: поиски, отклонённые лимитом узла, считаются отдельно. `searches_total`,
`searches_official`, `searches_other` остаются числом вызовов `web.search` (не меняются), а `wasted_searches`
больше не считает отказ лимита «пустым поиском»; доля пустых считается по поискам, дошедшим до движка:
`wasted_searches / (searches_total - searches_rate_limited)`. `SCHEMA_VERSION` отчёта намеренно НЕ поднят
(`t7118-honesty-report-v1`), поэтому baseline прогона, снятого до T7.127, грузится и показывает по новой
метрике «нет данных» — не 0 и не ошибку. В markdown отказы названы отдельной строкой и в сессии
(«отклонено лимитом поиска узла: N»), и в итоге прогона. Привязка отказа к сессии: журнальную строку пишет
прокси, `session_id` у неё NULL, времена строк одной фазы совпадают (AGENTS §7), поэтому отказ сопоставляется
с отклонённым `web.search` по интервалу жизни сессии из строки `sessions` и точному совпадению запроса
(`q` из адреса отказа разбирает `search_query_from_upstream_url`), с однократным расходом строки журнала
(`attribute_rate_limited_searches`). Числа контрольного прогона (фикстуры `tests/fixtures/t7127/`):
c3 — 10 поисков, 5 отказов лимита, `wasted_searches` 5 -> 2; c4 — 4 поиска, 2 отказа, 3 -> 1; c2 — 8 поисков,
0 отказов, 4 -> 4 (прежние числа не изменились); итог трёх сессий `searches_total` 22,
`searches_rate_limited` 7, `wasted_searches` 12 -> 7, доля пустых `12/22 = 0.5455` (прежнее правило на этих же
сессиях) -> `7/(22-7) = 0.4667`.

**Тесты (39 новых проверок, ни один прежний тест не удалён).**
`tests/unit/test_config_v29_payload_t7127.py` (6) — v29 = v28 с ровно одной правкой: поэлементное сравнение
секций, ровно одна отличающаяся строка текста, закреплённые canonical- и файл-хеши v29 и неприкосновенность
v28, `ModePolicy.from_section(v29["research_proxy"])` даёт 60 при окне 3600, дефолт без ключа остаётся 20,
скрипты dev-стенда на config-v29 не переведены (дефолт bootstrap — прежний payload).
`tests/unit/test_search_rate_limit_notice_t7127.py` (18) — математика минут: пустое окно -> `None`, ровно
граница окна -> 0, запрос старше окна -> 0, свежий запрос -> 60; параметризованные остатки 60/61/3599/1/120/121
секунд, наивная метка времени, окно 900 с; текст называет «60 из 60 запросов», «3600 с», «через 45 мин»,
сохраняет `rate_limited`, `research.fetch` и слово «заканчивай»; пустое окно не обещает освобождения; длина
текста ≤ 500 знаков и фиксированное начало; числа в тексте равны числам переданного окна (23 из 25);
склонение числительных 1 / 22 / 1000.
`tests/unit/test_rate_limit_search_attribution_t7127.py` (9) — адрес отказа round-trip-ом сопоставляется с
`upstream_request_url`, проценты-кодированный запрос фикстуры читается, адрес без `q` и пустой адрес -> `None`;
привязка одна-к-одной и однократный расход журнальной строки; лишняя журнальная строка остаётся неучтённой;
пустой запрос действия никогда не сопоставляется; guard того, что причина отказа в коде прокси записана ровно
этим литералом.
`tests/scenario/test_rate_limit_refusal_notice_t7127.py` (1) — живой путь `web.search` на подставном SearXNG
с потолком 1: второй вызов даёт `status == "rate_limited"` и текст с «лимит поиска узла исчерпан»,
«1 из 1 запрос», «3600 с», «мин», `research.fetch`; в журнале ровно одна строка отказа с полными payload-ключами
(`upstream_rate_limit_exceeded`, режим, `window_seconds`), в журнале запросов — только первый запрос,
подставный источник тронут ровно один раз.
`tests/scenario/test_honesty_report_rate_limit_t7127.py` (5) — прогон отчёта по фикстурам c3/c4/c2: 5 и 2 отказа
названы отдельно, в c3 `wasted_searches` 2 вместо 5, c4 1 вместо 3, c2 без изменений; `searches_total`,
`searches_official`, `searches_other` прежние и сходятся; итог и доля пустых по новой формуле; markdown содержит
строки отказа по сессиям и в итоге; baseline без новой метрики грузится и даёт «нет данных» (сравнение прочерком,
прежние метрики сравниваются как раньше); контрольный посев «отказы вне окна сессии» -> у c3 0 отказов и
`wasted_searches` снова 5, итог 2 (привязка действительно работает по окну, а не по тексту).

**Красность доказана на прежнем коде.** `git worktree add --detach /tmp/wt7127 accd7e2`, в него — копии новых
тестов и `tests/fixtures/t7127/`, прогон с `--continue-on-collection-errors`: **11 failed, 5 passed, 2 errors**
(отсутствующий payload v29 -> `FileNotFoundError`; импорт `UpstreamWindow` и
`RATE_LIMIT_REJECTION_REASON` -> ошибки сборки двух тестовых файлов; сценарий отказа —
`assert 'лимит поиска узла исчерпан' in 'upstream rate limit exceeded'`; все 5 тестов отчёта —
`AttributeError: 'SessionSteps' object has no attribute 'searches_rate_limited'`, `KeyError`, отсутствие строк
в markdown; отказ `web.search` — прежний английский текст). Зелены на `accd7e2` ожидаемо: дефолтный потолок 20,
страж «стенд не переведён на v29» и три прежние проверки `tests/scenario/test_web_search_tool.py`. Worktree
удалён (`git worktree list` — в списке только основной чек-аут); история не переписывалась, `git stash` не использовался.

**Прежние тесты не ослаблены.** Изменён один прежний файл — `tests/scenario/test_web_search_tool.py`: две
проверки отказа лимита переведены на новый текст (старый текст зафиксирован в комментарии рядом, «было ->
стало»), добавлены проверки окна и лимита; число проверок файла не уменьшено. `tests/scenario/test_research_modes.py`
(отказ лимита: только код и счётчики) не тронут и зелен; стражи гомоглифов T7.110 и T7.126a зелёные; тестов
стало больше на 39.

**Полные прогоны (§6).** Перед коммитом кода: `ruff check .` чисто; `mypy packages apps hostctl` — Success;
образ `noezema-sandbox:test` на месте; stage 1 `-n auto -q -m "not timing"`: **3237 passed + 12 skipped**
(было 3198 + 12 — ровно 39 новых проверок); stage 2 `timing` отдельным последовательным прогоном:
**4 passed**. Перед документационным коммитом прогон повторён целиком: те же числа — 3237 passed + 12 skipped
и 4 passed; контейнеров (41) и тестовых баз (`noezema_mig_*` 40, `noezema_tpl_*` 0) после обоих прогонов ровно
столько же, сколько до них.

**Остаток (названо, не сделано).** Привязка отказа к сессии остаётся эвристикой по окну жизни сессии и
совпадению запроса: при перекрывающихся окнах сессий с одинаковыми запросами она между сессиями неоднозначна
(сумма по узлу при этом не удваивается, строка журнала расходуется один раз); настоящая привязка потребовала бы
`session_id` в журнальной строке прокси — это новая колонка и миграция, чем задача не занималась. Потолок
остаётся узловым, а не посессионным (прежнее решение ADR-0028), окно остаётся 3600 с. Активация config-v29 и
замер нового потолка на `.92` — действие менеджера; агент стенд не трогал. Текст отказа не обучает модель новой
тактике в промпте (explorer-v11 не менялся): объяснение живёт только в наблюдении.
