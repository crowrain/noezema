# NOEZEMA — Статус реализации

Ветка: `impl/from-scratch`. План: [PLAN_FROM_SCRATCH.md](PLAN_FROM_SCRATCH.md).
Каждый пункт §22.1/§22.2 ARCHITECTURE.md получает ссылку на тест при закрытии.
Архив закрытых разделов вех (M0–M6, M7 T7.1–T7.28, исходные ячейки таблицы): [STATUS-archive.md](STATUS-archive.md).

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
| M7 полный веб + эксплуатация | ✅ Gate M7 пройден (T7.1–T7.6: knowledge graph + provenance, backup/PITR §15.3, GC root set, security gate + §16 metrics, evaluation run §22.2, ADR-0004; noezema-m7); после gate — дефектные серии EVAL-1/2/3/3b/4/4d: T7.7–T7.28 закрыты (ADR-0005–0015), активная фаза продолжается после gate: T7.52–T7.71 (ADR-0023…ADR-0028) — разделы ниже, в том числе T7.68 (единый сборщик оркестратора), T7.70 (SearXNG на dev-стенде) и T7.71 (`web.search` для модели, config-v14, explorer-v6); [дословно](STATUS-archive.md#m7-полный-веб--эксплуатация) | noezema-m7 (на `2e1631c`) | см. раздел M7 в архиве | 942 тест |

## Матрица §22.1 (техническая приёмка)

| # | Критерий | Класс | Статус | Тест |
|---|---|---|---|---|
| 1 | пробуждение по расписанию, pause/backoff | MVP | ✅ | T3.29 (задача добавлена ретроспективно — пропуск плана): `apps/orchestrator/scheduler.py` + миграция 0005 (`wake_schedule` в snapshot, `wake_scheduler_state`), `noezemactl wake-tick` + `noezema-wake.timer`. Тесты: test_wake_schedule.py (unit: fail-closed-валидация `wake_schedule`, timing: первый tick/интервал/мин. gap/backoff-окно, экспоненциальный backoff + cap) + test_wake_scheduler.py (scenario: каждый admission gate — paused/nonterminal/unresolved commit/activation slot/disk quota/GPU fail-closed → skip с точной причиной + audit `wake_skipped`; backoff после failed; авто-pause после 3 неудач (sticky); success/cancel/partial сбрасывают; operator pause не сбивается; битый `wake_schedule` → fail-closed) + test_web_api.py (wake_now обходит timing но не admission — REJECTED с reason; resume сбрасывает failure-бухгалтерию; /status.wake) |
| 2 | локальная LLM с fingerprint | MVP | ✅ | test_llm_gateway.py (OpenAI-compatible gateway для локальных бэкендов llama.cpp/Ollama/vLLM, retries только транзиентные, token/latency/finish_reason, test_fingerprint_is_deterministic_and_versioned) + test_compat_and_roles.py (compat suite T1.11, versioned prompts, tool schema hash). Fingerprint (профиль модели + prompt version + tool schema hash + policy version → canonical sha256) пишется в `model_runs.model_fingerprint` (NOT NULL) на каждом explorer/curator-вызове (T1.7–T1.8). Оговорка MVP: artifact/tokenizer-хэши опциональны (None до пиннинга артефакта); ModelProfile собирается в коде из gateway-settings — секция `model` снапшота не подключена (T1.7 «через config snapshot» — частично) |
| 3 | causal/idempotency ID в trusted host | MVP | ✅ | test_orchestrator.py (turn_id/action_id/idempotency_key генерирует хост) |
| 4 | typed actions в sandbox | MVP | ✅ | test_sandbox_runtime.py + test_tool_broker_sandbox.py (одноразовый контейнер, cap-drop/network/ro-rootfs, shell/python в sandbox, overlay) + test_sandbox_security.py + test_orchestrator_sandbox_executor.py (T7.58: продуктивный путь `NOEZEMA_TOOL_EXECUTOR=sandbox`, контейнер на сессию в `run_session`) |
| 5 | claim только с согласованным lifecycle | MVP | ✅ | test_memory_service.py (head current ⇔ assessment+epistemic_status NOT NULL, CHECK §14.1; dedup claim+evidence; supersede) + test_orchestrator.py (apply в fenced tx: claim→evidence→assessment→head одной транзакцией) |
| 6 | один fenced commit attempt | MVP | ✅ | test_orchestrator.py (prepared-строка до финального tx; fencing predicate: lease+revision+attempt=prepared; partial unique §14.2) + test_reconciler.py |
| 7 | lost COMMIT → reconciliation | MVP | ✅ | test_reconciler.py (kill before COMMIT→aborted; after commit→accepted; open final tx→finalizer_in_progress; stale finalizer→fenced) + reconcile_with_retries (backoff+jitter, fresh conn) |
| 8 | failpoints → старый/полный checkpoint | MVP | ✅ | test_failpoints.py (kill mid-action → outcome_unknown → session failed, staging не применён, ревизия не поднимается = полный старый checkpoint) + test_reconciler.py |
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | ✅ | test_web_api.py + test_web_mvp.py (status+host/timeline+SSE/messages/commands; admin-token auth на Command, queries open; assessment view — M3 memory) + dependencies (v1-часть): test_web_knowledge.py (T7.1: claims/heads по effective snapshot, зависимости в обе стороны §8.6, provenance-навигация source→parent/artifact/группы) |
| 10 | раздельные messages/stop/abort/controls | MVP | ✅ | test_web_api.py (раздельные endpoints; closed enum; idempotency key; stop/abort флаги сессии) |
| 11 | нет вслепую-ретраев | MVP | ✅ | test_tool_broker.py (§5.7 retry-классы: pure=2, idempotent=1, non_idempotent/observation=0 без вслепую-ретраев; idempotency key + different hash=incident/alert) + test_llm_gateway.py + test_search_tool_contract.py (T7.71: новый `web.search` объявлен в реестре классом OBSERVATION → 0 ретраев, повтор идентичного вызова отсекает guard T7.12) |
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
| due/stale time-sensitive | <20% | mechanism: ADR-0004 + test_evaluation.py (thresholds jsonb); расчёт: gates.py + test_evaluation_gates.py + test_freshness.py (T7.27/ADR-0014: правило §8.6/T3.7 по reverify_after на момент расчёта гейта, не по сохранённому полю — без зависимости от переоценки/флипа; retrieval — то же правило при чтении); T7.28/ADR-0015: строгое направление `below` (`ratio < threshold`) по спеке «<20%» — на границе 6/30 = 0.200 → failed, 6/31 → passed (до T7.28 — `at_most`, расхождение на границе); граничные тесты всех долевых гейтов — test_evaluation_gates.py (+18); actual run — T7.7 (EVAL-1/2); EVAL-3d (2026-09-19, mid-run v2→v3) — ADR-0008; EVAL-4d — passed 0/28 по сохранённому полю (артефакт), по правилу failed 22/28 — ADR-0014; сохранённых результатов g6 ровно на пороге 0.20 в прошлых прогонах нет (SELECT по noezema-eval*); T7.30/ADR-0016: опорная дата as_of строки claim — хост-деривация (явная дата вопроса > относительная форма → дата сессии по часам хоста > модельный as_of при бездатовом вопросе; модельная дата — только аудит: staging + claim_created `as_of`/`assessed_as_of`), формула reverify_after не изменена — test_scope.py (+6), test_as_of_commit.py (7: коммит relative/явная/бездат/полночь-UTC, переоценка без возврата модельной даты, re-деривация as_of при переиспользовании, форма EVAL-4d 19 relative + 6 явных старых + 3 бездатных = 28 → g6 8/28 = 0.2857 → failed — пересчёт T7.29); T7.31 (2026-09-23): заморозка EVAL-5 — корпус v4 (55 вопросов: 34 v2 + 13 v3 дословно + 8 новых; бездатных URL-вопросов 0 — все с явной датой/относительной формой; K = 2 заложенных due: ООН @ 2026-04-15, ЕС @ 2026-01-01; sha256 `b08009ff…`); предрегистрация g6 — **passed** (2/22.3 = 0.090; маржа 2: проходит при ≤ 4 due, падает при 5; N < 20 → insufficient_sample — гейт не оценён, не failed); предрегистрация g5 — failed при наблюдаемом R = 0 (EVAL-4d), но арифметически проходим в отличие от v3 (пул 28–34 ≤ 4R = 40: 10/28 = 0.357 … 10/34 = 0.294 ≥ 0.25; незакрытый в v3 конфликт g5/g6 — 11/47 = 0.234 — снят); EVAL-5-freeze.md, раздел T7.31; T7.32/ADR-0017 (2026-09-23): срок перепроверки существует ТОЛЬКО у утверждения о настоящем (якорь `relative` → now + окно volatility; якоря `explicit`/`none` — срока нет, NULL) — класс «просрочен навсегда» (улика `de9855eb`, Спутник 1957) исчезает по построению; статус `evergreen` (NULL = «срока нет по построению»; retrieval-вес 1.0 = fresh — неизменный факт не опускается как unknown); знаменатель гейта 6 = только `temporal_fact` с `reverify_after IS NOT NULL` (способные просрочиться; фиксированные моменты не проходят гейт композицией); порог 0.20 и направление `below` не тронуты; миграция 0024; предрегистрация PЕРЕСЧИСЛЕНА: K = 0, знаменатель relative-only 20.3–22.6, ожидаемо **passed** 0/20.3 … 0/22.6 (проходит при ≤ 4 due — 4/20.3 = 0.197 < 0.20, падает при 5 — 0.246/0.221; N < 20 → insufficient_sample); тесты: test_scope.py (+3), test_freshness.py (+1), test_rules_engine.py (+1), test_as_of_commit.py (+1), test_evaluation_gates.py (+1), test_retrieval.py/test_memory_service.py/test_reassessment.py (ожидания по правилу ADR-0017) — ADR-0017, пометка в ADR-0014, EVAL-5-freeze.md §3.1, раздел T7.32 |
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

Разделы ниже перенесены побайтово в [STATUS-archive.md](STATUS-archive.md) (T7.42, 2026-09-25):
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

## Активная история (T7.29–T7.53)

 ### T7.29 — разбор поля `as_of` у `temporal_fact` (EVAL-4d): определение по спеке, улика по 22 claim'ам, варианты решения — без изменения поведения

 **Задача — разбор, не исправление** (поведение кода не меняется;
 БД `noezema-eval4d`/`noezema-eval3d` — только SELECT; отчёт T7.27 §4).
 Документ: `docs/eval/EVAL-4d-as-of-analysis.md`; в `docs/eval/
 EVAL-4-freeze.md` заголовок/статус обновлён (EVAL-4c остановлен,
 EVAL-4d пройден) + «Постскриптум» в §9 (g6 по правилу failed 22/28,
 ссылка на разбор).

 **Семантика по спеке** (одной фразой): `as_of` — типизированное поле
 claim'а, базовая дата due/stale-механики (§8.2:1080, §8.6:1121,
 §8.7:1173, §14:1900 — определения значения в ARCHITECTURE.md нет;
 действие — ADR-0007:44–52, 163–175, 200–201): его надлежащее значение
 = **опорная дата вопроса** (явная дата; «на текущую дату» — дата
 сессии по часам хоста, T7.18; вопрос без даты — модельный `as_of`
 как fallback). «Дата наблюдения источником»/«дата события» в спеке
 не предусмотрены. Источник «по состоянию на 15.06.2026» + вопрос
 «на текущую дату» (21.09) → правильный `as_of` = 2026-09-21; due у
 такого claim'а — честная работа механизма, но корень — **модельный
 артефакт**, а не сигнал «данные источника старые» (при надлежащем
 as_of claim был бы fresh — механизм freshness возраста контента
 источника в рамках конвенции корпуса ADR-0007:64–71 не отслеживает).

 **Путь значения** (корень артефакта): модель пишет `as_of`
 (`staging.py:56`; валидация — только наличие, `staging.py:96–98`);
 промпт говорит лишь «момент времени (ISO-8601)» (`curator.md:23,46`)
 и **текущую дату модель не видит** (context builder даты не
 вставляет); на коммите `as_of` идёт в строку claim как есть
 (`service.py:349,400`) → `reverify_after = as_of + 30d`
 (`service.py:954`, `rules_engine.py:263–267`, volatility
 `configurable` = 30d), а хост-опорная дата (T7.18) ушла только в
 scope (`service.py:387,407`, `scope.py:200–242`) — **сверки
 модельного `as_of` с опорной датой нет нигде**. Вход гейта 6
 производит модель — тот же класс, что ADR-0007 устранил для
 scope/grade.

 **Улика (22 = 16 просроченных A + 6 непросроченных с as_of раньше
 даты сессии)**, классификация (a) дата-наблюдение из источника /
 (b) дата события / (c) без видимого основания: **(a) = 2** (оба
 непросроченные: «проверенная 7 сентября 2026», «Last updated on:
 21.09.2026»), **(b) = 8** (Спутник 1957, замкнутие 7-го периода
 2016, релизы Blender/Python/PostgreSQL, открытие линии, датированные
 записи), **(c) = 12** (11 просроченных + 1: даты в evidence нет
 вовсе; кластеры 2026-06-15 ×5 и 2026-08-13 ×4 по разным источникам —
 модель штампует день.месяц из страницы + текущий год; statement
 дублирует ту же дату). Сверка с EVAL-3d (ADR-0008 §3.5): тот же
 паттерн — 3×(c) + 1×(b) (b56208f3 — релиз 2026-08-13).

 **Варианты (решение за пользователем)** — пересчёт due на данных
 EVAL-4d (gate_now 2026-09-22 08:59:42 UTC, правило T7.27,
 направление T7.28): (i) ничего не менять — **22/28 = 0.7857 →
 failed** (как есть); (ii) уточнить промпт/описание — данные
 EVAL-4d не переоцениваются (без ре-рана **22/28**), гипотетический
 новый прогон при полном соблюдении **6/28 = 0.2143 → failed** (и
 без инъекции текущей даты в контекст не закрывает «на текущую
 дату»); (iii) хост выводит `as_of` по опорной дате (+ отдельное
 audit-поле даты источника) — пересчёт **8/28 = 0.2857 → failed**;
 (iv) то же через ADR-0016 + правку ARCHITECTURE.md (требует явного
 решения пользователя, AGENTS.md §4) — **8/28**; (v) reporting-only
 разбивка due в отчёте (без изменения гейта) + решение о корпусе v4:
 N_temporal ≥ 31 — **6/31 = 0.1935 → passed** (граница T7.28).
 **Структурный вывод**: на данных EVAL-4d g6 провален при ЛЮБОМ
 варианте — даже идеальная модель оставляет 6 заложенных просрочек
 (6/28 ≥ 0.20); as_of-артефакты вторичны (16/22). **Рекомендация**:
 сейчас (i)+(v1); к новому прогону — (iii)+(iv) (хост-выводимая
 базовая дата reverify_after, как T7.18 для scope) + N_temporal ≥ 31
 в корпусе v4.

 **Не тронуто**: `packages/`/`apps/`/`hostctl/`/`tests/` (ruff +
 mypy strict + 877 pytest — без изменений), пороги §22.2,
 `claim_type_rules`/`rules_hash`, замороженные payload'ы
 config-v2…v5 и корпуса v2/v3, `ARCHITECTURE.md`, строки ранов и
 сохранённые итоги (SELECT only).

 ### T7.30 — базовая дата свежести `as_of` выводится хостом (решение пользователя 2026-09-22: варианты (iii)+(iv) T7.29) — ADR-0016

 **Определение (ADR-0016)**: `as_of` := **хост-выводимая опорная
 дата** — базовая дата `reverify_after` (§8.6/§22.2) выводится
 доверенным хостом; **модельная дата — аудиторская** (не входит ни в
 строку claim, ни в расчёт срока — расширение ADR-0007:172–175 с
 scope на freshness). Одна функция, один источник истины —
 `derive_claim_as_of` (`packages/memory/scope.py`), тот же
 приоритет, что T7.17/T7.18 для scope (`derive_claim_scope` теперь
 вызывает её — копия логики нет):
 - вопрос с **явной датой** → `claims.as_of` = эта дата (полночь UTC);
 - вопрос с **относительной формой** («на текущую дату» и т.п.) →
   `claims.as_of` = **дата сессии по часам хоста (UTC)** — якорь
   **начало сессии** (`sessions.created_at`), не момент commit
   (ADR-0007 п.2; сессия, пересекающая полночь, не переякоряет);
 - вопрос **без опорной даты** → прежний fallback: модельный
   `as_of` как есть (Спутник 1957, релизы — дата события
   остаётся).

 **Код** (`packages/memory/` — сторона хоста):
 - `scope.py`: новая `derive_claim_as_of` (datetime, UTC);
   `derive_claim_scope` рефакторена через неё (поведение scope —
   без изменений, все 33 теста test_scope.py без правок);
 - `service.py` (коммит — единственный путь записи `claims.as_of`):
   `as_of` новой строки claim и строки при **переиспользовании**
   (dedup) = хост-деривация из вопроса ТЕКУЩЕЙ сессии (ре-деривация
   на каждом коммите — как у scope: claim утверждается для даты,
   которую анкрит этот вопрос); `reverify_after` на коммите
   (`_assess`) и в переоценке (`reassessment.py:577`) — от
   сохранённого `claims.as_of` (формула `rules_engine.py:263–267`
   **не тронута**); модельная дата в audit `claim_created`
   (`as_of` = модель, `assessed_as_of` = хост) + staging payload
   (durable) — **новая колонка/миграция не нужны** (значение не
   теряется);
 - retrieval/gates/web/blind-выгрузка/hostctl — читают сохранённые
   `claims.as_of`/`reverify_after` (freshness по §8.6/T3.7 на момент
   чтения, T7.27) — код не меняется; переоценка модельную дату не
   возвращает (её в строке claim нет).

 **Не меняется** (ADR-0016): схема `CuratorProposal` (поле `as_of`
 модели остаётся её предложением; валидация `temporal_fact requires
 as_of` `staging.py:96–98` — как есть; схема уходит движку halogen —
 ADR-0012), промпт куратора (замороженный snapshot), формула
 `reverify_after()`, rules engine/`claim_type_rules`,
 `rules_version` (rules-v2) и `rules_hash` (хэш payload'а — не
 тронут; изменилась хост-деривация значения, не правила — как T7.18),
 пороги §22.2, замороженные payload'ы config-v2…v5 и корпуса v2/v3
 (хэши до/после совпадают — см. отчёт), сохранённые данные прошлых
 прогонов (noezema-eval* — SELECT only, не переоценивались).
 Известное ограничение: statement может содержать дату источника
 («По состоянию на 2026-06-15 …»), отличную от as_of — statement
 хост не переписывает.

 **Тесты** (877 → **890**, +13):
 - unit `tests/unit/test_scope.py` (+6): приоритет
   `derive_claim_as_of` (явная > относительная → дата сессии >
   модельный fallback; относительная без даты сессии — fail-closed
   fallback; бездатовый вопрос — модельный as_of, naive→UTC; дата
   scope = дата-часть `derive_claim_as_of` — одна функция для всех
   трёх приоритетов);
 - scenario `tests/scenario/test_as_of_commit.py` (7, новый файл —
   реальный коммит, не прямые вставки в БД): (1) относительный
   вопрос + модельный as_of в прошлом → `claims.as_of` = начало
   сессии, `reverify_after` = +30d, fresh, модельная дата в
   staging/audit (`as_of`/`assessed_as_of`); (2) явная старая дата →
   as_of = явная дата (модельная «будущая» не сдвигает), due по
   дизайну корпуса; (3) бездатовый → модельный as_of как есть
   (Спутник 1957 → due); (4) сессия, пересекающая полночь UTC → дата
   начала сессии (якорь T7.18); (5) переоценка (worker, pending →
   re-evaluation) не возвращает модельную дату; (6) переиспользование
   (dedup, день N → день N+1): as_of re-деривируется из вопроса
   текущей сессии; (7) **форма EVAL-4d в фикстуре** (19 relative —
   14 с модельными артефактами + 5 уже в дедлайне, 6 явных старых,
   3 бездатных = 28; 14+6+2 — ровно due-множество T7.29) → гейт 6
   **8/28 = 0.2857 → failed** (совпадает с пересчётом T7.29;
   19 relative fresh, due = 6 заложенных + 2 бездатных).

 **Документы**: ADR-0016 (новый); ADR-0007 — пометка-уточнение у
 строк 199–201 (исторический текст не переписан);
 `ARCHITECTURE.md` — только определение as_of/базы reverify_after:
 §8.6 (строка о Reverify deadline) и §8.7 (после таблицы базовых
 типов) — минимальные формулировки со ссылкой на ADR-0016 (явное
 решение пользователя, AGENTS.md §4); `docs/eval/
 EVAL-4d-as-of-analysis.md` §4 — пометка «решение принято →
 T7.30/ADR-0016». Корпус v4 и новую заморозку НЕ делать — следующая
 задача (N_temporal ≥ 31, предрегистрация g6: 6/31 = 0.1935 →
 passed, граница T7.28).

 ### T7.31 — корпус v4 + заморозка EVAL-5 (2026-09-23; прогон НЕ выполнялся — решение о запуске за пользователем)

 **Замечание к постановке T7.30**: «N_temporal ≥ 31» при K = 6
 (3 заложенных v2 + 3 v3 T-old) недостижимо — при измеренном
 yield EVAL-4d (0.4058, Wilson 95% ≈ [0.29, 0.53]) и марже ≥ 2
 неожиданных due требовалось Q ≥ 99 (v3 = 69). Решение (моё,
 обоснование в EVAL-5-freeze §3.1): редукция числителя, а не
 раздувание корпуса — **K = 2** (2 old-as_of + 0 бездатных;
 relative-вопросы под T7.30 fresh до конца рана, бездатные
 исключены) → N > 5·(K+2) = 20 → Q ≥ 50; **v4 = 55**.

 **Честный пересчёт g6 (ШАГ 1, до сборки)** — due-классы по
 построению, проверены в коде (`derive_claim_as_of`,
 `reverify_after = as_of + 30d`) и данных EVAL-4d (SELECT по
 noezema-eval*):

 | Класс | as_of (T7.30) | due | v2 | v3 | v4 |
 |---|---|---|---|---|---|
 | A. явная старая дата в вопросе | приоритет 1 = явная дата | ВСЕГДА | 3 | 6 | **2** (ООН @ 2026-04-15, ЕС @ 2026-01-01) |
 | B. бездатные о прошедших событиях (Спутник 1957, PG14-EOL, Конституция 1993, Win10-EOL) | приоритет 3 = модельное = дата события (улика: `de9855eb` as_of 1957-10-04 due) | ВСЕГДА | 3 | +3 (T-date v3) | **0** |
 | C. бездатные re-verify («Повтори и подтверди») | модельное = дата исходного claim'а (улики: population-reconfirm due @ 2026-07-01; Rublyovo-reconfirm @ 2026-09-05 — fresh, катится) | часто | 7 | 7 | **0** |
 | relative «на текущую дату» | приоритет 2 = начало сессии | fresh (ран < 30d) | 15 | 35 | 41 |

 Измеренные yield'ы (EVAL-4d, 28/69 = 0.4058): explicit-old
 6/6 = 1.0; relative URL 19/35 = 0.543 (якоря 5/7,
 rel-follow-up'ы 2/7, standalone 12/21); dateless URL
 3/16 = 0.1875. Требуемый Q при марже 2 (N > 5(K+2),
 N ≈ 0.4058·Q): K=8 → Q ≥ 124; K=6 → Q ≥ 99; K=4 → Q ≥ 74;
 K=3 → Q ≥ 62; **K=2 → Q ≥ 50** (выбрано; v3 = 69 — «стена»
 ADR-0008 §5 A при K ≥ 6). v4 = 55: N точечный = 22.3,
 по-классовый = 24.6, нижний край ДИ = 16.0 (< 20 →
 insufficient_sample — честно предрегистрировано).

 **Предрегистрация (до любого рана, EVAL-5-freeze §3):**
 - **g6 — ожидаемо passed**: 2/22.3 = 0.090 (по-классовый
   2/24.6 = 0.081); маржа 2 сохраняется — проходит при ≤ 4 due
   (4/22.3 = 0.179), падает при 5 (0.224); при N < 20 —
   insufficient_sample (гейт не оценён, не failed).
 - **g5 — ожидаемо failed** (наблюдаемое поведение): в EVAL-4d
   R = 0 (halogen не делал кросс-сессионного dedup на
   follow-up'ах) → 0/≈31 < 0.25. Арифметически v4 **проходима в
   отличие от v3**: R ≤ 10 (6 URL + 4 ws пака), пул значимых
   28–34 (EVAL-4d 31 при 69; −4 исключённых, +8 новых) ≤ 4R =
   40 → идеал 10/28 = 0.357 … 10/34 = 0.294 ≥ 0.25. **Конфликт
   g5-vs-g6** (g6 — большой пул distinct temporal, g5 — малый
   пул, затронутый ≥2 сессиями) в v3 был арифметически
   незакрыт (11/47 = 0.234 < 0.25 при любом поведении модели);
   в v4 снят: 8 одиночных новых (не 19) + исключение
   набухавших пул бездатных/old вопросов → оба гейта
   проходимы в одном прогоне; остаток g5 — только поведение
   модели (наблюдение, не противоречие дизайна). Цена: N на
   нижнем крае (22–25).

 **Корпус v4** (`docs/eval/question-set-v4.jsonl`, sha256
 `b08009ff38728c8c6cd70ebf8797acd0a77dd46296f8be230848b6284516e0c6`):
 55 уникальных = 34 строки v2 + 13 строк v3 ДОСЛОВНО (кураторский
 подвыбор, каждая байт-в-байт) + 8 новых (все «на текущую
 дату»: Go 1.27.1, FreeBSD 15.1, Nginx 1.31.6, Франция/Швеция/
 Испания — главы, .NET 10 EOL 14 Nov 2028, PHP 8.5 active EOL
 31 Dec 2027; шаблоны 3/3/2 ≤ 4). Приоритеты 2×100 / 10×90 /
 14×80 / 29×0 (FIFO: old-as_of первыми, follow-up'ы после всех
 якорей). Жёсткие требования выполнены и закреплены тестом:
 - Jaccard (`curiosity.word_set`/`jaccard`) < 0.6 на всех
   срезах: new-new max 0.5714, new-vs-v2 max 0.5200,
   new-vs-v3 max 0.5926;
 - 2 источника на вопрос — разные регистрируемые домены;
 - значение дословно в нормализованном тексте ОБОИХ страниц —
   live GET 2026-09-23: HTTP 200 по всем 43 URL-вопросам (86
   запросов-страниц), значения в нужных строках таблиц
   (`/home/denis/dsh1/corpus-v4-liveverify.json`, 43/43 ok;
   противоречий между источниками нет — зафиксирован дрейф
   «текущих» значений: Python 3.14.7, PG 18.6, ЦБ 14,00% c
   27.07.2026, население ~8.2 млрд, ФРС 3.50–3.75%, метро 278);
 - бездатных URL-вопросов 0 (проверено тестом
   `parse_question_date`/`question_uses_relative_date`);
 - исключено: 3 v3 T-old + 1 v2 old-as_of (заложенный due,
   сверх K = 2), 3 v3 T-date + 7 v2 re-verify + 6 v2
   бездатных (модельное as_of — непредсказуемый due), пакет
   Рублёво-Архангельской (v2 10+28) — источники противоречат с
   2026-09-05 (5 открытых станций msk.kp.ru vs 14 по проекту
   stroi.mos.ru) — дефект дизайна, не сценарий. Честный
   недобор 8 новых вместо 12+ (стены источников: org-count
   403/JS, госсайты должностных лиц 403/404/DNS/TLS, пары
   ставок центробанков противоречат) — подгонка исключена.

 **Конфигурации**: новые payload'ы не создаются — T7.30
 изменение стороны хоста, не поле конфигурации; старт =
 canonical v4 (`7ef0f579…`), mid-run флип = v5 (`f9c23ba9…`),
 как в EVAL-4. Payload'ы v2…v5 и корпуса v1/v2/v3 НЕ тронуты —
 файловые хэши до/после T7.31 совпадают (таблица —
 EVAL-5-freeze §6).

 **Тесты** (890 → **891**, +1): `test_corpus_parse.py::
 test_question_set_v4_corpus_shape` — 55 уникальных;
 приоритеты 2/10/14/29; наследованные строки байт-в-байт =
 строки v2/v3; 2 URL на разных регистрируемых доменах на
 вопрос; у каждого URL-вопроса явная дата или относительная
 форма; у 2 old-as_of — явные прошедшие даты; Jaccard < 0.6 на
 трёх срезах (new-new, new-vs-v2, new-vs-v3). Тесты v2/v3 не
 ослаблены.

 **Документы**: `docs/eval/EVAL-5-freeze.md` (новый, по образцу
 EVAL-4-freeze: §1 изменения; §2.1 reuse payload'ов v4/v5 с
 обоснованием; §2.2 корпус v4 + исключения + дрейф; §2.3 код =
 `11168e2` (T7.30) + T7.31 (docs/tests, без кода); §3
 предрегистрация g5/g6 + масштабирование g1–g4 + конфликт
 g5-vs-g6; §4 риски; §5 чек-лист (БД `noezema-eval5`,
 `--count 55`, утренний старт UTC); §6 не меняется). Строка g6
 матрицы §22.2 обновлена (замест «корпус v4, N_temporal ≥ 31»).
 Прогон не выполнялся, LLM-вызовов нет, фоновых процессов нет;
 проверки noezema-eval* — SELECT только.

 ### T7.32 — срок перепроверки только у утверждения о настоящем (решение пользователя 2026-09-23; ADR-0017)

 **Правило** (корень провалов гейта 6 — класс «просрочен
 навсегда»: «Спутник-1 запущен 4 октября 1957» → срок отсчитан от
 as_of 1957 → due всегда; улики: claim `de9855eb` EVAL-4d, 16
 модельных as_of-артефактов T7.29/T7.30, 6 zаложенных due v2/v3):
 срок `reverify_after` существует ТОЛЬКО у утверждения о
 настоящем — вопрос с относительной формой. Якорь (закрытый
 набор) уже определяет `derive_claim_as_of` (ADR-0016):

 | Якорь | Вопрос | Срок |
 |---|---|---|
 | `explicit` | явная дата | НЕТ (NULL) |
 | `relative` | «на текущую дату», «сейчас» (T7.18) | **момент проверки (now) + окно по volatility** |
 | `none` | без даты (модельный as_of); относительная без `session_date` (fail-closed); сессия без вопроса | НЕТ (NULL) |

 Срок НЕ отсчитывается от `as_of` никогда. `as_of` остаётся опорной
 датой утверждения (ADR-0016 не отменяется: приоритет, якорь начала
 сессии, re-деривация, модельная дата — staging/audit).

 **Код** (неприкосновенные не тронуты: `claim_type_rules`,
 `rules_hash`, правила §22.2, направления гейтов, payload'ы
 v2…v5, корпуса v1–v4, сохранённые прогоны):

 - `packages/domain/models/enums.py`: `FreshnessStatus.EVERGREEN =
   "evergreen"` (NULL-срок ≠ unknown: «срока нет по построению,
   вечно валидно»); `ClaimDateAnchor` {explicit, relative, none}.
 - `packages/memory/scope.py`: `derive_claim_as_of` возвращает
   `ClaimAsOf(as_of, anchor)` — якорь ИЗ ТОЙ ЖЕ функции (ветка,
   давшая значение; повторного разбора вопроса на месте вызова
   нет — один источник истины, как ADR-0016); якорь
   персистируется в host-scope-v1 как `date_anchor`;
   `anchor_from_scope` — сохранённый якорь, legacy/мусор →
   `relative` (консервативно: срок держится, а не «вечно валидно»
   на отсутствующих данных).
 - `packages/memory/rules_engine.py`: `reverify_after(result,
   anchor, now)` = `now + окно volatility` при `relative`, иначе
   `None`. Окна НЕ меняются (static 90 / configurable 30 /
   temporal 30): якорь — существование срока, volatility — его
   длина. Класс volatility «не истекает» НЕ НУЖЕН (фиксированный
   момент любого типа — срока нет вовсе; `formal_theorem`
   «устаревающий через 90 дней» исчез).
 - `packages/memory/freshness.py`: NULL → `evergreen` (было
   `unknown`); fresh/due — без изменений. Для любого claim'а с
   head NULL всегда = «нет срока по построению» (оба писателя —
   правило выше) — чистая функция (reverify_after, now) сохранена.
 - `packages/memory/service.py`: коммит пишет срок по якорю из
   `derive_claim_as_of` (тот же вызов, что дал as_of/scope);
   audit `claim_assessed` + `date_anchor`.
 - `packages/memory/reassessment.py`: worker — по сохранённому
   `date_anchor` (`_latest_assessed_scope`): relative → срок
   обновляется к моменту проверки; фиксированный момент → NULL
   остаётся NULL (переоценка НЕ возвращает срок); legacy →
   relative.
 - `packages/cognition/retrieval.py`: `evergreen` = вес **1.0**
   (= fresh) — неизменный факт НЕ опускается как unknown (0.7).
 - `packages/evaluation/gates.py`: гейт 6 — знаменатель =
   `temporal_fact` current с `reverify_after IS NOT NULL`
   (способные просрочиться; фиксированные моменты вне
   знаменателя — иначе гейт проходил бы композицией). Числитель,
   порог 0.20, направление `below`, MIN_SAMPLE = 20, Wilson —
   не тронуты.
 - `migrations/versions/0024_freshness_evergreen.py`: CHECK
   `claims.freshness_status` + `evergreen` (единственная миграция;
   новых колонок нет).

 **Решения** (обоснования — ADR-0017 «почему не другие
 варианты»): (а) якорь возвращается из `derive_claim_as_of`
 dataclass'ом, а не перепроверкой вопроса на месте вызова; (б)
 NULL → `evergreen`, а не переиспользование `unknown` (иначе
 retrieval 0.7 опускал бы «Спутник» ниже свежих); (в) знаменатель
 g6 — только со сроком (старая предрегистрация 2/22.3
 построена на zаложенных due, которых под ADR-0017 нет); (г)
 legacy (без `date_anchor`) → `relative`, fail-closed в сторону
 перепроверки.

 **Корпус (последствие, НЕ переделано)**: из 22 вопросов,
 выброшенных T7.31 из v3, причина «due всегда / непредсказуемый
 due» УПРАЗДНЕНА — 20 возвращаются в следующий корпус (v5):
 16 бездатных (модельный as_of → якорь `none` → срока нет;
 Спутник 1957, PG14-EOL, Конституция 1993, Win10-EOL,
 re-verify'ы) + 4 old-as_of (явные прошедшие даты → якорь
 `explicit` → срока нет; 3 v3 T-old @ 2026-06-01 + 1 v2 Python
 @ 2026-04-15). Пакет Рублёво-Архангельской (2) остаётся
 исключённым по СОБСТВЕННОЙ причине (источники противоречат с
 2026-09-05: 5 станций msk.kp.ru vs 14 stroi.mos.ru — дефект
 дизайна, не сценарий). Корпус v4 и файлы v1/v2/v3 НЕ тронуты;
 переделка — задача следующего прогона.

 **EVAL-5 (предрегистрация пересчитана, EVAL-5-freeze §3.1)**:
 K 2 → **0** (2 явных old-as_of не due по построению; бездатных
 в v4 нет; relative — fresh до конца рана). Знаменатель
 relative-only: точечный 0.4058×55 − 2 = 20.3, по-классовый
 22.6, нижний край ДИ 0.29×55 − 2 = 14.0. Ожидаемо **passed**
 0/20.3 … 0/22.6; проходит при ≤ 4 due (4/20.3 = 0.197 < 0.20),
 падает при 5 (0.246/0.221); N < 20 → insufficient_sample.
 Старое «2/22.3 = 0.090 passed» НЕДЕЙСТВИТЕЛЬНО (заменено
 честно, не переписано). Порог/направление — не тронуты.

 **Тесты** (891 → **899**, +8): новые — `test_scope.py`
 `test_derive_claim_scope_carries_the_date_anchor`,
 `test_anchor_from_scope_reads_stored_anchor`,
 `test_anchor_from_scope_legacy_scope_defaults_to_relative`
 (+3); `test_freshness.py` `test_evergreen_does_not_flip_with_time`
 (+1); `test_rules_engine.py`
 `test_reverify_after_fixed_point_has_no_deadline` (+1);
 `test_as_of_commit.py`
 `test_reassessment_does_not_return_deadline_to_fixed_point_claim`
 (+1); `test_evaluation_gates.py`
 `test_gate6_due_stale_mixed_deadline_and_fixed_point_claims` (+1;
 5 due / 20 со сроком + 10 фиксированных → 5/20 failed, старое
 знаменательство 5/30 passed — разбавление устранено).
 Исправлены ожидания по правилу (тесты проверяли просрочку по
 СТАРОМУ правилу — смысл сохранён, не ослаблены):
 `test_freshness.py` (NULL: unknown → evergreen),
 `test_rules_engine.py` (сигнатура + база now вместо as_of;
 relative-ветка та же), `test_enums.py` (4 → 5 статусов;
 +`ClaimDateAnchor`), `test_scope.py` (6 derive-тестов —
 as_of-значения без изменений, добавлены anchor-утверждения),
 `test_retrieval.py` (no-deadline: unknown 0.7 → evergreen 1.0,
 не опускается ниже fresh), `test_memory_service.py` (коммит:
 прошлый as_of → due теперь NULL/evergreen; fresh-ветка —
 на сессии с относительным вопросом; lifecycle-тест без
 вопроса → evergreen), `test_as_of_commit.py` (relative: срок =
 commit + 30d — диапазонные утверждения; явная/бездатный:
 due навсегда → NULL/evergreen; форма EVAL-4d: 8/28 failed →
 0/20 passed — due-хвост исчез по построению),
 `test_evaluation_gates.py` (NULL-claim вне знаменателя: 3/22 →
 3/21), `test_reassessment.py` (legacy-scope: due по старому
 правилу → срок обновлён к now + окно, «keeps due» больше
 невозможно ни для какого якоря). Не тронуты и зелёны: 33
 scope-теста (as_of), T7.18 (полночь), гейты EVAL-4d 22/28 (все
 со сроком), пороговые 6/30–6/31, g6 3/20 и (3,9) activation,
 worker now+90d.

 **Документы**: ADR-0017 (новый); ADR-0014 — пометка-уточнение
 (смысл NULL, история не переписана); ARCHITECTURE.md — ТОЛЬКО
 определение срока (разрешено пользователем 2026-09-23): §8.6
 (reverify deadline), §8.2 (статусы + evergreen), §8.7
 (упоминание расчёта); EVAL-5-freeze.md (§1, §2.2-примечание,
 §2.3, §3.1, §3.2, §4.5, §5.11, §6); STATUS.md (этот раздел,
 строка g6, счётчик M7). Прогон не выполнялся, LLM-вызовов
 нет (192.168.1.48 не тронут), фоновых процессов нет;
 noezema-eval* — SELECT только.

### T7.33 — разбор нулевого переиспользования гейта 5 (EVAL-4d): связывающее ограничение + варианты — без изменения поведения

**Задача — разбор, не исправление** (поведение кода не меняется;
`noezema-eval4d` — только SELECT; сохранённый итог g5 `0/29 failed` не
пересчитан). Документ: `docs/eval/EVAL-4d-reuse-analysis.md`; пометка-
ссылка — `docs/adr/0006-eval-2-reuse-results.md` (исторический текст не
переписан).

1. **Три пути гейта 5** (`gates.py:408-454`) — срабатывания в EVAL-4d:
 (a) `evidence` (claim затронут ≥2 сессиями через
 `evidence.created_in_session`) — 0: все 54 evidence-строки 29
 значимых claim'ов — из их собственной сессии создания (никакого
 cross-session dedup); (b) `claim_revisions` — 0 **по построению**:
 таблица пуста во всём ране, и в коде нет НИ ОДНОГО писателя (читают
 только два запроса гейтов; ORM-модель есть, писателя нет) — путь мёртв
 в v1; (c) `claim_dependencies` — 0: за весь ран ровно одно ребро
 (70ff7a38 E3 → 387115c9 **E1**, kind research, сессия-якорь 62586be8) —
 from-сторона получает только собственную сессию, to-сторона не
 значима (E1) → гейт не считает. Сохранённый результат 0/29
 подтверждён пересчётом (SQL — документ §5.1).
2. **Модель пользовалась поиском**: 17 вызовов `memory.search`
 (14 completed + 3 `policy:deny` — модель передавала аргумент `limit`,
 которого нет в схеме). Язык: 6 ru, 4 ru+en смешанных, 2 en, 1
 filename, 1 product-name. Результаты — из `action_completed` аудита
 (то, что модель реально получила): 3/14 непустых В МОМЕНТ; **replay
 против финального snapshot: 8/14 непустых** (EVAL-2: 0/16 — ADR-0006).
 Кросс-языковый корень ADR-0006 **закрыт** (английские/смешанные
 запросы матчат русские statement'ы). 8 из 11 пустых в момент поиска —
 «пусто по праву»: матчащий claim ещё не существовал (якорные сессии
 его не создали).
3. **Таблица 11 паков** (документ §3.3, statement'ы дословно):
 (в) «follow-up не родил claim вовсе» — 5 паков (ЕС, Python,
 PostgreSQL, ЦБ, reading.md); (а)+(б) «перефраз + другой claim_type» —
 1 пак (ООН: якорь 07d655cf temporal E3 «…составляло 193.», follow-up
 bd0cbc5b external E3 «…составляет 193.» — побайтового совпадения нет,
 обязательный dependency не объявлен); (д) «якорь не родил claim» — 5
 паков (население, Рублёво, plan.md, todo.md, glossary.md); (г) — 0.
4. **Связывающее ограничение — два слоя** (оба по данным):
 **Слой 1 (доминирующий)**: 22 из 33 паковых сессий (64%) не
 закоммитили НИ ОДНОГО знания — 13 follow-up'ов предложили ноль
 операций (при видимом в контексте якоре: 7add23b6 — 6 совпадений,
 4d67e817 — 6, …), 8 сессий — предложение отклонено rules engine
 (систематическая пара type↔evidence у halogen: computed_result ←
 local_observation ×6, temporal_fact ← computation, computed_result ←
 source_assertion; T7.9 pre-commit отбой), 1 — curator_error (усечённый
 16-символьный UUID в dependency → схема ×3, предложение потеряно). В
 таких сессиях ни один из трёх путей гейта не может сработать —
 улучшение поиска/сшивания не помогает. Самое сильное подтверждение:
 два FU→FU случая (c3537eb3, 3b2a4a7d) видели claim ПЕРВОГО
 follow-up'а того же пака в контексте с рангом 0.264/0.933 — и всё
 равно не закоммитили ничего. **Слой 2 (шов)**: сшивка на
 коммите — только побайтовый `statement+claim_type`
 (`service.py:348, 396-414`) или явный dependency на значимый claim;
 единственная связываемая пара (ООН) упёрлась в оба условия
 (перефраз + другой тип + нет ребра). **Гипотеза «exact statement
 dedup — связывающее ограничение» подтверждена на шве, недостаточна
 целиком**: идеальное fact-identity дало бы на сохранённых данных
 максимум 2/26 = 0.0769 (ООН: 3 строки/3 сессии, ЕС: 2/2 — общие
 evidence identity `84abcf4730…`/`da578f37…`/`ccd036f7…`), всё равно
 failed.
5. **Identity**: у claim'а — ничего, кроме точного (statement,
 claim_type) (`claims` §14 `ARCHITECTURE.md:1900-1903`, дедуп
 `service.py:348`); у evidence — `identity_hash` +
 `UNIQUE(claim_id, evidence_kind, identity_hash)` (§14.3,
 `ARCHITECTURE.md:2128`). Спека identity claim'а не определяет вовсе —
 **дизайн-пробел** (не нарушение): вход, определяющий гейт 5,
 производится модельным free-text, хост сравнивает побайтово — тот же
 класс, что закрыли ADR-0007 (scope) и ADR-0016 (as_of).
6. **Измеренный дефект документации retrieval** (улика §3.4): на
 PostgreSQL 15.17 (контейнер `noezema-test-db`) `ts_rank(vector, query)`
 для AND-запроса возвращает ранг уровня полного совпадения (0.06–0.1)
 при ≥2 общих лексемах и ~1e-20 при ≤1 (контрольные тесты в документе) —
 docstring `retrieval.py:154-156, 216-220` («partial AND matches rank
 ~1e-20 = no match by design») ложен; SQL-фильтр `> 0` и порог 1e-9
 частичные совпадения НЕ отсекают → фактическая семантика recall'а —
 «≥2 общих лексема» (отсюда ложноположительное попадание q12: запрос о
 населении Земли → claim про ООН).
7. **Варианты** (решение за пользователем, НЕ реализовано; what-if
 пересчёт на строках noezema-eval4d, сохранённый итог не тронут —
 документ §4): (i) ничего — зафиксировать неизмеряемость на модели
 (g5 0/29; EVAL-5 предрегистрация failed при R=0, арифметически
 проходим: пул 28–34 ≤ 4R=40); (ii) identity для claim'а (паттерн
 ADR-0007/0016) — трогает **ARCHITECTURE.md** (§14/§14.1, решение
 пользователя), rules_hash/payload'ы — нет, what-if ≤2/26 → failed,
 риск — NLP-ключ по free-text; (iii.a) сшивка через evidence identity
 §14.3 — трогает ARCHITECTURE.md, what-if ≤2/26, риск — склейка разных
 фактов из одного фрагмента; (iii.b) dependency-усиление промптом —
 новый payload (protocol_hash), what-if 0, потолок = соблюдение
 моделью; (iv) embeddings/pgvector — **отдельная веха, не v1**
 (`retrieval.py:9-10`), what-if 0/29; (v) корпусное — «дословно» =
 **подгонка под механизм** (строковое сравнение vs способность
 переиспользовать), строго помечено; (vi.1) промпт type↔evidence
 (новый payload) — разблокирует 8 отклонённых сессий, прямой удар по
 слою 1; (vi.2) UUID-префиксы (деталь, ничего больше) — +1 связываемый
 пак; (vi.3) host-перепроверка — большой дизайн, не v1.
8. **Что не тронуто**: код, тесты, пороги §22.2, rules_hash,
 claim_type_rules, замороженные payload'ы, корпуса v1–v4,
 ARCHITECTURE.md, сохранённые итоги прогонов (noezema-eval* — SELECT
 only); прогон НЕ запускался (eval-run, сессии NOEZEMA, LLM на
 192.168.1.48 — отсутствуют), фоновых процессов нет.

**Тесты**: код не менялся — полная проверка как подтверждение
отсутствия регрессии: ruff + mypy strict + pytest
(NOEZEMA_TEST_DATABASE_URL) — **899 passed без изменений**.

**Дополнение (2026-09-23, после коммита)** — проверка гипотезы о
протоколе, `EVAL-4d-reuse-analysis.md` §7. Схема `CuratorProposal` не
имеет операции «перепроверил существующий claim»: `EvidenceLink`
адресует claim по индексу в своём предложении, существующий id доступен
только в `dependencies` нового claim'а. Все 13 сессий «предложил ноль» —
follow-up'ы с `goal_reached`; в 12 из 13 модель в `rationale` сверяет
факт с существующим claim'ом (5 — по id). Пересчёт собственным SQL
гейта с записью этих перепроверок: строго 5/29 = 0.172 (failed),
полно **8/29 = 0.276 (passed)**; базовая строка воспроизводит
сохранённые 0/29. Вывод §4.1 уточнён: слой 1 = 13 (протокол) + 9 (модель
+ rules); рекомендация (i) винит модель ошибочно; новый вариант (vii) —
`EvidenceLink` по id существующего claim'а — не реализован, решение за
пользователем. Код, пороги, сохранённые итоги не тронуты.

### T7.34 — перепроверка существующего claim'а записывается (решение пользователя 2026-09-23: направление (vii) из EVAL-4d §7.5, включая минимальную правку ARCHITECTURE.md; ADR-0018)

**Задача**: «перепроверка существующего claim'а должна записываться» —
протокол не дал записать 13 follow-up-перепроверок EVAL-4d (§7.2
разбора: 5 — прямо по id), поэтому гейт 5 не видел ни одной сессии
переиспользования (0/29). Реализован вариант (vii) в уточнённой форме
(см. ADR-0018: запись — НЕ EvidenceLink, а сессионная assessment-
строка; EvidenceLink-by-id сам по себе ненадёжен — ловушка identity).

1. **Операция модели — существующий вид staging** (AGENTS.md §3
  не тронут): `claim`-операция `session_staging` с новым опциональным
  полем `ClaimProposal.existing_claim_id` (`packages/domain/schemas/
  staging.py`). Идентификаторы выдаёт хост, модель только
  ССЫЛАЕТСЯ (инвариант «id генерирует хост»): полное UUID из строки
  `[c:<uuid>]` контекст-пака или hex-префикс 8–31 символ. Чистая
  функция `resolve_claim_reference` (`packages/memory/service.py`):
  полное UUID — только если видимо; префикс — только если
  ОДНОЗНАЧЕН среди видимых (модель уже обрезала UUID — curator_error
  пака 7 EVAL-4d); иначе — отказ с причиной. Поле — строка (8–36),
  НЕ `uuid.UUID`: профиль halogen снимает `format` (ADR-0012), и
  усечённое UUID должно дойти до хоста; валидация ответа хостом —
  полной pydantic-моделью (схема нового поля — anyOf string|null,
  без format/pattern — проверено).
2. **Две границы, fail-closed** (условие T7.9 — «у claim'а есть head в
  `config_snapshot_id` сессии»): (a) граница куратора
  (`apps/orchestrator/orchestrator.py`, `_curator`): ссылка резолвится
  по claim'ам контекст-пака (строки `[c:<uuid>]`); неразрешима —
  отказ ВСЕГО предложения до записи staging, audit
  `session_state_changed` с `curator_reject_kind: reverify_unresolved`
  и явной причиной (не молчаливый пропуск); rules pre-check (T7.9)
  для reverify-claim'а — под типом ЯКОРЯ (модельная повторная
  формулировка — audit-only). (b) граница коммита
  (`MemoryService.apply_claim_staging`): повторная проверка по
  «head в snapshot'е сессии» (набор шире пака — строгее, fail-closed);
  неразрешимо — problem в аудите коммита + отказ операции и её
  evidence-ссылок (индексы `claim_index` выровнены, плейсхолдер
  `None`), молчаливого fallback'а на создание НЕТ.
3. **Запись перепроверки** — новая строка `claim_assessments`
  (`claim_id` + `created_in_session` = сессия-перепроверка), создаётся
  тем же путём `_assess`, что и оценка нового/переиспользованного
  claim'а; строки reassessment worker / активации имеют
  `created_in_session IS NULL` и не считаются. Запись существует
  НЕЗАВИСИМО от нового evidence — подтверждено на данных (ловушка
  identity, ADR-0018): из 20 evidence-identities якорей follow-up'ы
  воспроизвели побайтово 12 (схлопывание в строку якоря —
  `UNIQUE(claim_id, evidence_kind, identity_hash)` переиспользует
  строку, `created_in_session` остаётся якорной), 8 — дрейф
  содержимого (динамические страницы); пара ЕС (46bec75c, 8c370d7f →
  `80c1908c`) — полное схлопывание обоих источников у обоих
  follow-up'ов: наивный evidence-путь видел бы 1 сессию.
4. **Свежесть (ADR-0017)**: подтверждённая перепроверка — новый
  момент проверки → единое правило `reverify_after(result, anchor,
  now)` в `_assess` сдвигает срок relative-якоря к моменту проверки
  (now + окно volatility); якорь re-деривируется из вопроса
  сессии-перепроверки той же `derive_claim_as_of` (T7.30/ADR-0016);
  evergreen (якорь explicit/none, NULL) не трогается. Копий логики
  нет.
5. **Оценка** (AGENTS.md §3): тот же evidence — схлопывается по
  identity (дубликат НЕ повышает grade), grade не меняется, запись
  существует; новое evidence — обычный rules engine; контр-evidence —
  disputed ≤ E1. grade/confidence — только rules engine.
6. **Гейт 5** (`packages/evaluation/gates.py`, `_gate_reuse`): пятая
  UNION-ветка `refs` — `claim_assessments.claim_id +
  created_in_session IS NOT NULL` (только значимые claims, только
  сессионные строки). Порог 0.25 и направление `at_least` не тронуты;
  прочие гейты и прочие ветки — без изменений.
7. **Промпт и конфиг**: `prompts/curator.md` → **curator-v4** (поле
  `existing_claim_id` + правило 7 «Перепроверка»: факт уже установлен
  и сверен — не новый claim, а reverify; ноль операций — нет).
  Замороженные payload'ы config-v2…v5 НЕ переписаны; новый
  `docs/eval/config-v6-payload.json` (паттерн EVAL-4-freeze §2.1:
  diff v5→v6 — ровно 1 поле `prompts.curator.version`; canonical
  sha256 `e6de7fe3c7e308f8…`, file sha256 `3832e077d76dfc91…`;
  `TokenBudgets.validate() == []`). Pin `BOOTSTRAP_PAYLOAD` —
  curator-v4 (паттерн T7.14); миграции хэш пересчитывают из кода —
  не тронуты.
8. **ARCHITECTURE.md** — минимальная правка строго в пределах
  определения записи перепроверки: один абзац в §14.1 (после
  `claim_assessment_heads.prepared_by`) — определение записи
  (сессионная assessment-строка, момент проверки §8.6/ADR-0017,
  независимость от evidence, worker-строки не входят, ссылка по
  host-issued id, отличие от дедупа и ревизии, путь гейта 5) со
  ссылкой на ADR-0018. §22.2 и прочее не тронуты.
9. **Что не тронуто**: rules_hash, claim_type_rules, пороги §22.2 и
  направления гейтов, виды staging-операций, дедуп T7.9,
  `UNIQUE(claim_id, evidence_kind, identity_hash)`,
  `claim_revisions` (писателя по-прежнему нет — ревизия значения ≠
  перепроверка), dependency-резолюция (префиксами НЕ расширена —
  ограничение ADR-0018), замороженные payload'ы v2–v5, корпуса v1–v4,
  сохранённые данные прогонов (noezema-eval* — SELECT only, не
  переоценивать).

**Тесты** (существующие не ослаблены): `tests/unit/test_claim_reference.py`
(12: полное UUID видимо/не видно/регистр/32-hex, префикс
однозначный/неоднозначный/нет-матча/мин-длина 8/дефисы/мусор/пустой
набор/пробелы); `tests/unit/test_staging_schema.py` (+5: поле по
умолчанию None, UUID и префикс принимаются, <8 и >36 — схема
отклоняет, halogen-профиль: в схеме после strip нет format/pattern,
фрагмент поля = anyOf string(8..36)|null); `tests/unit/test_memory_service.py`
(+8, сценарии через MemoryService, не прямые вставки: якорь создаёт
claim → follow-up перепроверяет тот же id с ТЕМ ЖЕ evidence — evidence
не дублируется (1 строка, created_in_session = якорь), grade E2 не
меняется, запись = вторая assessment-строка (created_in_session =
follow-up), audit `claim_reverified`; то же с НОВЫМ evidence —
evidence_added=1, grade по правилам; контр-evidence — disputed E1;
headless-якорь — fail-closed (2 problems: «reverify reference … not
visible» + «evidence staging rejected: claim op 0 was rejected»,
claim НЕ создаётся); несуществующий id — fail-closed; уникальный
16-hex-префикс — принят, audit фиксирует reference и resolved;
relative-якорь (back-dated сессия) — срок сдвинут к моменту
перепроверки (now+90d ± 1ч); evergreen — NULL остаётся NULL);
`tests/scenario/test_reverify_curator.py` (2: куратор-граница —
неизвестный id отказывает ВСЁ предложение (0 staging, 1 claim,
audit `reverify_unresolved` + «not visible», терминал succeeded,
следующая сессия допущена); E2E happy path — follow-up ссылается по
id из пака, коммит: 1 claim, 2 evidence, 2 assessment (вторая =
follow-up), audit `claim_reverified`); `tests/scenario/test_evaluation_gates.py`
(+3: форма EVAL-4d §7.2 в фикстуре — 29 значимых claim'ов (19
temporal E3 + 6 external E3 + 3 local_obs E2 + 1 computed E2) + 13
перепроверок по 8 claim'ам ТОЛЬКО assessment-строками (без evidence):
полная форма 8/29 = 0.276 → **passed**; строгая (5 по id) 5/29 =
0.172 → **failed**; worker-строки (created_in_session NULL) на всех
остальных claim'ах — числитель остаётся 8). Регрессии: T7.9
(headless не переиспользуется — test_memory_service.py, существующий +
новый headless-reverify), T7.30/T7.32 (as_of-деривация и срок —
существующие тесты test_as_of_commit.py / test_freshness.py не
изменены и зелёные), существующие тесты гейта 5 (14/51 rich, 5/20
граница passed, 4/20 failed) — без изменений и зелёные.

**Полная проверка**: ruff + mypy strict + pytest
(NOEZEMA_TEST_DATABASE_URL) — **929 passed** (899 baseline + 30 новых),
без изменений существующих ожиданий. Прогон НЕ запускался (eval-run,
сессии NOEZEMA, LLM на 192.168.1.48 — отсутствуют), фоновых процессов
нет.

### T7.35 — промпт привязан к snapshot'у по СОДЕРЖИМОМУ (ADR-0019, §8.7.1, §12, §14)

Задача (решение пользователя 2026-09-23): приведение реализации к
спеке — `payload_sha256` хэширует неизменяемые prompts (ARCHITECTURE.md 1215/2069), fingerprint включает prompt version (1719), model_runs
хранит prompt_version (1886). Находка приёмки T7.34: payload хранил
ярлык версии (protocol_hash хэшировал ярлык), путь из payload'а
игнорировался (имя файла зашито), сверки версии/содержимого не было,
`model_runs.prompt_version` пуст у всех 620 вызовов EVAL-3d/EVAL-4d
(ни одна из 8 точек `ORMModelRun(...)` поле не заполняла).

**Криминалистика (git + SELECT, строки ранов не тронуты) — что РЕАЛЬНО
шло:**

| Прогон (БД) | Объявлено | Фактически | Совпало |
|---|---|---|---|
| EVAL-1/2/3/3b (noezema-eval, -eval2, -eval3, -eval3b) | curator-v2/explorer-v2 | curator-v2/explorer-v2 | ✅ (EVAL-1/2: HEAD не устанавливается однозначно — ран перекрывает коммиты, но файлы промптов в окнах не менялись) |
| EVAL-3c (noezema-eval3c, код 83d0ea9) | curator-v2/explorer-v2 | **curator-v3/explorer-v3** (T7.14) | ❌ обе роли |
| EVAL-3d (noezema-eval3d, код 83d0ea9) | curator-v2/explorer-v2 (v2→v3 payload, оба объявляют v2/v2) | **curator-v3/explorer-v3** | ❌ обе роли |
| EVAL-4/4b/4c/4d (код ff59dbf/defff9a/375f759) | curator-v2/explorer-v2 (payload v4/v5) | **curator-v3/explorer-v4** (T7.14/T7.21) | ❌ обе роли |

planner/verifier/extractor v1 — совпадают во всех прогонах (файлы не
менялись с 09-15). Независимая улика: bootstrap-snapshot в БД
пересеивается миграцией 0001 из кода — в eval3c/3d он несёт v3/v3, в
eval4* — v3/v4. Полная таблица со sha256 текстов — ADR-0019.

**Реализация:**
1. `resolve_prompts` (packages/llm_gateway/roles.py): payload пинит для
   каждой роли `path` (относительно корня репо) + `version` + `sha256`
   сырых байтов файла; loader резолвит ПО ССЫЛКЕ ИЗ SNAPSHOT'а (зашитого
   имени больше нет), сверяет sha256 И заголовок версии; любое
   расхождение / непин / неизвестная роль / path traversal →
   `PromptPinError` → fail-closed: сессия не стартует, audit
   `prompt_pin_mismatch` (out-of-session, session_id NULL),
   admission не регистрируется. Защита от mid-run flip: phase 1 сверяет
   prompts-секцию своего snapshot'а с той, что резолвлена на
   admission (не совпало — сессия не стартует).
2. model_runs: все 8 точек записи заполняют `prompt_version`,
   `prompt_sha256` (новая колонка, миграция 0025) и
   `tool_schema_hash`; fingerprint вызова включает prompt_version И
   prompt_sha256. Исторические строки не переписаны
   (prompt_sha256 NULL — до пинов).
3. Раскладка: `prompts/<role>/<version>.md` — 11 файлов
   (curator-v1…v4, explorer-v1…v4, planner/verifier/extractor-v1),
   байт-в-байт из git (sha256 зафиксированы тестом); плоские
   `prompts/<role>.md` удалены — **единый источник истины =
   версионированные файлы** (старые пути живут в git-истории).
4. **Решение по v2…v5 (и v6) — (б) fail-closed**: непинованный
   payload исполнять отказывается. Обоснование: (а) вернул бы
   «исполнимость» payload'у без пина содержимого — гарантия
   воспроизводимости была бы выдуманной; при (а) v4/v5 давали бы
   v2/v2 — НЕ то, что реально шло в EVAL-4d (см. таблицу); v6 под (а)
   дал бы explorer-v2 вместо explorer-v4, на котором шли все прогоны
   с T7.21. Закоммиченные файлы v2…v6 не переписаны (артефакты),
   исполняемым с T7.35 является только пинованный payload (v7+).
5. **config-v7-payload.json** = v6 + sha256 всех промптов (curator-v4
   `6e129ded…`, explorer-v2 `38e9b688…`, planner/verifier/extractor-v1
   `6aeb22bc…`/`34fe8069…`/`af5dba62…`); всё кроме `prompts` — байт в
   байт v6; `TokenBudgets.validate() == []`; canonical sha256
   `b1c572bdad7d413983412c1ec867db27f869f34f74d117a488dba2a29ebe8207`.
   payload для ближайшего смоук-прогона. ВНИМАНИЕ: v7 пинит
   explorer-v2 (объявление v6); если смоук нужен на explorer-v4 —
   отдельный payload (решение о содержимом, вне T7.35).
6. BOOTSTRAP_PAYLOAD (паттерн T7.14, теперь по содержимому): пины
   explorer-v4/curator-v4/planner-v1/verifier-v1/extractor-v1, пути —
   версионированные файлы; миграция 0001 пересчитывает hash из кода
   (fail-closed) — новые БД получают пинованный bootstrap;
   `BOOTSTRAP_SNAPSHOT_ID` (UUIDv5) не меняется — admission существующих
   БД не ломается. `protocol_hash` (формула не тронута) теперь
   транзитивно включает пины содержимого.
7. **EVAL-5:** заморозка НЕДЕЙСТВИТЕЛЬНА (стоит на v4/v5: непинованы +
   при любом поведении загрузчика дают текст, несовпадающий с
   реальным EVAL-4d) — заметная пометка в начале EVAL-5-freeze.md;
   перезаморозка НЕ выполнена в T7.35 (впереди корпус v5), нужна перед
   запуском на payload'е с пином содержимого (v7+).
8. Пометки «фактически шло <версия> — см. ADR-0019» добавлены в
   EVAL-3-freeze.md (§9.1), EVAL-4-freeze.md (§2.1), ADR-0008 —
   исторический текст не переписан.
9. ARCHITECTURE.md — НЕ тронут: спека уже требует неизменяемые промпты
   в payload'е, prompt version в fingerprint'е и prompt_version в
   model_runs — правка спеки не потребовалась.

**Не меняется**: rules_hash, claim_type_rules, пороги §22.2 и
направления гейтов, закоммиченные payload'ы v2…v6, корпуса v1–v4,
сохранённые данные noezema-eval* (SELECT only), ТЕКСТ промптов
(curator-v4 остаётся curator-v4).

**Тесты** (существующие не ослаблены; старый
test_real_prompts_are_versioned заменён УСИЛЕННЫМ — пин по
содержимому, не только ярлык): `tests/unit/test_prompt_pinning.py`
(12: главный регресс — изменение файла на диске НЕ меняет
загружаемый пинованный текст (sha-mismatch → fail-closed, никогда
молчаливая подмена); расхождение заголовка версии → fail-closed;
непинованный legacy-payload → fail-closed (решение (б)); loader
использует ссылку из snapshot'а, а не зашитое имя; нет файла /
path traversal / неизвестная/отсутствующая роль / нет секции —
fail-closed; заголовок версии; BOOTSTRAP_PAYLOAD-пины == файлы репо
(path+version+sha256); 11 восстановленных файлов == git-блобы по
sha256); `tests/scenario/test_prompt_pinning.py` (2: через оркестратор
на fake LLM — 4 model_runs, у каждой prompt_version + prompt_sha256,
sha == пин роли из snapshot'а, fingerprint согласован; испорченный
пин bootstrap'а → PromptPinError, сессий 0, audit
prompt_pin_mismatch с причиной); `tests/unit/test_compat_and_roles.py`
(2 адаптированы под новый API с сохранением интента: извлечение
версии+хэша, unversioned → fail-closed по заголовку).

**Полная проверка**: ruff + mypy strict + pytest
(NOEZEMA_TEST_DATABASE_URL) — **942 passed** (929 baseline + 13 новых,
−1 вынесенный/усиленный), без изменений существующих ожиданий. Прогон
НЕ запускался (eval-run, сессии NOEZEMA, LLM на 192.168.1.48 —
отсутствуют), фоновых процессов нет.

### T7.35 (follow-up) — config-v8: пин `explorer-v4` для смоук-прогона (ADR-0019)

`config-v7` унаследовал от v6 устаревший ярлык `explorer-v2`, тогда как
все прогоны с T7.21 фактически шли на `explorer-v4` (криминалистика
ADR-0019). После T7.35 пин исполняется буквально, поэтому устаревший
ярлык стал бы реальным поведением: explorer без правила повторов T7.14 и
без подстраховки покрытия источников T7.21. `docs/eval/config-v8-payload.json`
= v7 с единственным изменением `prompts.explorer` → `explorer-v4`
(`prompts/explorer/explorer-v4.md`); все пять пинов разрешаются,
`TokenBudgets.validate() == []`. canonical sha256
`9f1fc79ab1f1f1e1743278ebf5494480fd862e87d765afef9d114cc44c587e64`, file
sha256 `135ebe09fa35c4b999d5fbceddd705802ac56d9474bde8e5eb534acbc1f5bf4e`.
v7 не переписан. Тест —
`tests/unit/test_freeze_payloads.py::test_config_v8_pins_explorer_v4_and_differs_from_v7_only_there`.
Назначение — смоук-прогон новых T7.30/T7.32/T7.34/T7.35 на живых сессиях.

### T7.36 — профиль схемы `llamacpp-rocmfpx` для K2 Horizon MoVA на .48 (ADR-0012)

Решение пользователя 2026-09-24: NOEZEMA переключается на модель
`k2-horizon-mova-36b-a4b-rocmfp4-fast` (K2 Horizon MoVA 36B A4B ROCmFP4
FAST; llama-swap на 192.168.1.48, сборка llama.cpp ROCmFPX-k2,
`--ctx-size 262144` = `context_window` payload'а, `--parallel 1`).
Payload модель по имени не пинит (`model_alias: thinker-local`) — модель
задаёт `NOEZEMA_LLM_MODEL`, новый payload не нужен.

Замер прямыми HTTP к движку: ни `none`, ни `halogen` не работают — все
пять схем ответа, которые шлёт оркестратор (CuratorProposal,
ExtractionReport, ModelResponse, PlanResponse, VerifierReport), дают
HTTP 400 `Failed to initialize samplers: failed to parse grammar`
(перевод JSON Schema -> GBNF). Каждый используемый keyword по отдельности
принимается (`format` uuid/date-time, `maxLength`, anyOf, $defs/$ref);
снятие ровно `minLength`/`maxLength` делает все пять схем компилируемыми,
`format`/`pattern` сохраняются. Реальный ответ куратора через этот профиль
(HTTP 200, finish=stop) прошёл полную валидацию хоста pydantic-моделью.
Модель рассуждающая: на тривиальное предложение — 2308 выходных токенов и
~8,7 тыс. символов reasoning, 58 с (риск `finish_reason=length` при
`max_output_tokens` 8192 на сложных шагах — AGENTS.md §7).

`packages/llm_gateway/schema_compat.py`: `LLAMACPP_ROCMFPX_UNSUPPORTED_KEYWORDS`
+ запись `llamacpp-rocmfpx` в `SCHEMA_PROFILES`; валидация ответа хостом
не ослаблена. Тесты — `tests/unit/test_schema_compat.py`
(`test_profiles_registry`, `test_unknown_schema_profile_fails_fast`,
`test_curator_proposal_schema_round_trip_llamacpp_rocmfpx`). rules_hash,
claim_type_rules, пороги, payload'ы, корпуса, ARCHITECTURE.md не тронуты.


### T7.37 — SMOKE-V8-K2: смоук-прогон новых T7.30/T7.32/T7.34/T7.35/T7.36 на K2 и его разбор (2026-09-24)

**T7.37a — переключение на K2 и запуск.** Ревью `smoke-v8/launch.sh`
против эталона eval4b и кода — ошибок не найдено, правок не потребовалось
(хэши config-v8 `135ebe09…`/корпуса `b3e05ad5…`/canonical v8 `9f1fc79a…`,
пины 5 ролей, живой HTTP-чек схемы куратора через профиль
`llamacpp-rocmfpx` (HTTP 200), searxng, БД `noezema-smoke-v8-k2` (0025),
активация v8). Env: ровно 2 ключа — `NOEZEMA_LLM_MODEL` →
`k2-horizon-mova-36b-a4b-rocmfp4-fast`, `NOEZEMA_LLM_SCHEMA_PROFILE` →
`llamacpp-rocmfpx` (бэкап `noezema-llm.env.bak.2026-09-24T121733Z`); env
оставлен как есть — K2 остаётся моделью NOEZEMA. Прогон
(run `a0a84ce9-a327-4531-aede-6f488a1d3d2a`, snapshot `da1b0165…`,
корпус 7 q, seed 20260924, slo 3600 s): 2026-09-24 12:18:12Z →
12:42:04Z, EXIT=0; 7 сессий — 2 succeeded (4/7, 5/7), 5
succeeded_partial (1, 2, 3, 6, 7); `outcome=insufficient_sample`
(N=7<20 — не приёмка). Отчёт T7.37a: `task27-smoke-launch.log`.

**T7.37b — остановка воркера и разбор (только анализ; код/тесты/
payload'ы/корпуса/ARCHITECTURE.md не тронуты, БД — SELECT only, LLM
.48 не тронут).**

1. **Воркер остановлен.** До остановки: 7/7 сессий терминальны,
   7/7 commit_attempts = `committed`; `systemctl --user stop
   smoke-v8-k2-worker`; оба юнита inactive (transient-юниты —
   `LoadState=not-found` после stop), процессов hostctl/worker.sh нет.
   Воркер за 24 мин не сделал работы (103 × reassessment `deferred=True`
   — сессии в полёте, 103 × reconcile «nothing to do»).
2. **Гейт 5 = 0/5 (разбор — `docs/eval/SMOKE-V8-K2-report.md` §3).**
   Знаменатель 5 = значимые claims (E2+ supported: ООН E3, Python
   `35d5b7ae` E3, plan.md E2, Спутник E3, Go E3). Числитель 0 по всем
   пяти путям: evidence — 11/11 строк из сессии-создателя;
   claim_revisions — 0 (писателя нет); claim_dependencies — 0 (единственное
   ребро отклонено); **claim_assessments — ни одной строки с
   created_in_session = сессия-перепроверка**. По пакам:
   - **Python** (якорь S2 → FU S4): якорь дал значимый claim
     (`35d5b7ae` E3/supported); FU видела его в контекст-паке полным
     UUID (`[c:35d5b7ae…] … (supported, E3, p=0.75)`, `retrieval.py:95`)
     и цитировала в rationale — но **`existing_claim_id` НЕ
     использован**: куратор предложил НОВЫЙ claim
     `local_observation`+source_assertion → rules pre-check (T7.9)
     отклонил ВСЁ предложение (`claim 0 (local_observation): support
     evidence kind 'source_assertion' not allowed for local_
     observation`) → 0 staging. Аудит `reverify_unresolved` — 0
     (путь не входился). Классификация: **модель не воспользовалась
     операцией** + неверный claim_type → отказ всего (класс (ви.1)
     T7.33 type↔evidence).
   - **plan.md** (якорь S3 → FU S5): **якорь не создал claim** — своё
     предложение отклонено rules engine (`claim 1 (computed_result):
     support evidence kind 'local_observation' not allowed for computed_
     result` — та же пара, что 6× в EVAL-4d) → FU видела в паке 0
     claim'ов и создала НОВЫЙ (local_observation E2, `existing_claim_id:
     null`). Классификация: **якорь не дал значимого claim'а**.
   - **Вывод:** 0/5 — не дефект механизма T7.34 (обе fail-closed
     границы и пятый путь гейта на месте и сверены с кодом), а модельное
     поведение K2: 0/7 сессий заполнили `existing_claim_id`; оба пака
     упали до reverify-пути на модельных отказах. Матрица type↔evidence
     в промпте куратора отсутствует (T7.33 (ви.1)).
3. **Выдуманные id: 1 за весь ран** — S1 (ООН) dependency
   `c0000000-0000-0000-0000-000000000000` → host-отказ
   `dependency_edge_rejected` «target missing» (claim закоммичен без
   ребра). S1 была первая — id в контексте не было (claims_evidence=0) →
   нулевой UUID-плейсхолдер (класс галлюцинации T7.33).
   `existing_claim_id` ≠ null — **0 строк** (поле не использовалось).
   Причины 5 succeeded_partial (терминал = точное равенство
   `complete_reason == "goal_reached"`, `orchestrator.py:826`): S1/S3/S6
   — свободный текст вместо кода; S2 — `goal_reached: <текст>` (код +
   суффикс, равенство не срабатывает); S7 — budget_exhausted (10 шагов).
   Уточнение T7.37a: partial S1 не из-за dependency-отказа (на терминал
   он не влияет). Плюс 9 policy deny — ошибки tool-схем K2
   (неизвестный `artifact.create` ×2, `memory.search` с чужими
   аргументами ×4 и т.д.) — хост fail-closed корректен.
4. **Новинки — числами** (отчёт §5): **T7.35** — 46 model_runs,
   46/46 несут prompt_version+prompt_sha256, **0 расхождений** с пинами
   config-v8 (curator-v4 ×7, explorer-v4 ×39); consolidating
   tool_schema_hash NULL по замыслу (куратор без инструментов).
   **T7.36** — ошибок 400 «failed to parse grammar» **0**;
   finish_reason **46/46 stop** (0 length); выходные токены: мин 107,
   медиана 582,5, **макс 4902** « 8192; reasoning не учитывается
   отдельно нигде (клиент читает только usage+finish_reason; сырые
   ответы не ретейнятся) — входит в output_tokens, усечений нет.
   **T7.30/T7.32** — 7/7 claim'ов без отклонений: ООН explicit →
   NULL/evergreen; Спутник none → NULL/evergreen; Python/Go relative →
   as_of = дата сессии, reverify_after = МОМЕНТ ПРОВЕРКИ + 30 д (окно
   volatility), fresh; якорь — из сохранённого `assessed_scope.
   date_anchor`.
5. **«Event loop is closed» (worker.log, 206 traceback'ов на 206
   тиков)** — teardown-дефект `hostctl/cli.py`: после
   `asyncio.run(_run())` (цикл A, на нём пул/asyncpg-коннекты) —
   `asyncio.run(engine.dispose())` на НОВОМ цикле B → «Future attached
   to a different loop» + `call_soon` по закрытому циклу A. Класс:
   **безвредный шум** (тик завершается до traceback'а, exit 0, данных не
   трогает; в ране воркер не работал), но реальный дефект — маскирует
   ошибки ~6 КБ шума на такт. Паттерн во ~10 командах CLI. Не исправлено
   (отдельная задача).
6. **Итоговые гейты** (N=7, не приёмка): new_supported_refuted_e2
   insufficient_sample 5/5 ci95=[0.5655, 1.0]; external_temporal_e3
   insufficient_sample 4/4; eligible_sessions_with_outcome insufficient_
   sample 7/7; near_duplicate_questions insufficient_sample 0/7;
   **significant_claim_reuse insufficient_sample 0/5 ci95=[0.0,
   0.4345]**; due_stale_time_sensitive insufficient_sample 0/2;
   reassessment_slo insufficient_sample 0/0; current_pending_invalid_
   ancestor insufficient_sample 0/7; high_severity_incidents **passed**
   0/None; blind-гейты (структурные) insufficient_sample 7/7.
7. **Предложения (по приоритету; все — отдельные задачи, решение за
   пользователем):** (1, высокий) матрица type↔evidence в curator-v4 →
   новый payload (2/7 сессий отклонены — корень гибели обоих паков);
   (2, высокий) усилить правило 7 «Перепроверка» примером (0/7
   использовали `existing_claim_id`); (3, средний) нормализация
   `complete_reason` (4/7 сессий деградировали в partial из-за
   формулировки); (4, средний) dispose движка в том же `asyncio.run`
   (hostctl CLI); (5, низкий) «dependencies: [] если нет зависимостей»;
   (6, низкий) следить за слабостью K2 к tool-схемам (9 deny).

Полная проверка (подтверждение отсутствия регрессии — код не менялся):
ruff + mypy strict + pytest (NOEZEMA_TEST_DATABASE_URL) — 944 passed.
Фоновых процессов нет; коммит — только отчёт и STATUS.md.

### T7.38 — curator-v5 и config-v9: матрица type↔evidence, пример перепроверки, dependencies (решение пользователя 2026-09-24: предложения (1), (2), (5) разбора SMOKE-V8-K2 — одна правка промпта, один новый payload)

Основание (`docs/eval/SMOKE-V8-K2-report.md`, `docs/eval/EVAL-4d-reuse-analysis.md` §3.2/§3.3/§7): гейт 5 = 0 и в EVAL-4d (0/29), и в смоуке (0/5), а механизм T7.34 исправен — K2 заполнил `existing_claim_id` в 0/7 claim-операций, и оба пака смоука погибли на парах type↔evidence, которые rules pre-check (T7.9) отбивает ВСЁ предложение: `computed_result ← local_observation` (якорь S3 plan.md; ×6 из 8 отказов EVAL-4d) и `local_observation ← source_assertion` (FU S4 Python — якорный claim `35d5b7ae` виден в контексте полным UUID, цитирован в rationale, операция не использована). В curator-v4 матрицы допустимых пар не было вовсе; правило 7 «Перепроверка» — только словами, без примера. Семантика отказа (отказ по операции вместо всего предложения) и нормализация `complete_reason` (предложения (3)/(4)) — НЕ в этой задаче.

1. **`prompts/curator/curator-v5.md`** — НОВЫЙ файл (curator-v4 не тронут: на него пинятся прошлые payload'ы, ADR-0019), заголовок `version: curator-v5`. Три правки относительно v4, остальное — без изменений:
   - (а) матрица «тип claim'а → допустимые виды evidence» — ровно `allowed_kinds` из `claim_type_rules` payload'а (computed_result←computation; local_observation←local_observation; self_model←local_observation; external_fact и temporal_fact←source_assertion, quote_integrity; procedural←experiment_run, computation; empirical_conjecture←experiment_run; formal_theorem←formal_check) + правило выбора ОТ evidence к типу (сначала — какие виды evidence есть, потом — только тип, который их допускает) + короткие примеры ровно на наблюдённые ошибки: содержимое файла прочитано → `local_observation` (не `computed_result` — только при evidence `computation`); факт из веб-источника → `temporal_fact` (если про «сейчас»/дату) или `external_fact` (не `local_observation` — только наблюдение в workspace);
   - (б) правило 7 «Перепроверка» с КОНКРЕТНЫМ примером: в контексте `[c:<uuid>] … (supported, E3, …)`, вопрос сессии про тот же факт, сверка по свежим источникам совпала → операция claim с `existing_claim_id` (полностью, как в контексте), `claim_type` = тип существующего claim'а, `evidence_links` на загруженные сейчас источники + короткий фрагмент JSON; явно: НЕ заводить новый claim на уже установленный факт — использовать перепроверку;
   - (в) зависимости: если зависимостей нет — `"dependencies": []`; никогда не подставлять заглушки (вроде `c0000000-0000-…`) и не выдумывать id — только id, видимые в контексте (S1 смоука: `dependency_edge_rejected` «target missing»).
2. **Единый источник истины матрицы** — `tests/unit/test_curator_prompt_matrix.py`: тест разбирает матрицу из curator-v5.md (markdown-таблица; матчатся только строки с backtick-токенами, header/separator пропускаются) и сравнивает МНОЖЕСТВО пар (claim_type, evidence kind) с `claim_type_rules.<type>.allowed_kinds` config-v9; тип, отсутствующий в правилах, — ошибка. При будущей правке правил промпт не может молча разойтись.
3. **`docs/eval/config-v9-payload.json`** = config-v8 с ЕДИНСТВЕННЫМ изменением `prompts.curator` → curator-v5 (path `prompts/curator/curator-v5.md`, sha256 содержимого). Формат v8 воспроизведён побайтово (`indent=2`, sorted keys, trailing newline — сверено); v8 не переписан; все пины разрешаются `resolve_prompts`, `TokenBudgets.validate() == []`.
4. **`BOOTSTRAP_PAYLOAD`** — пин curator поднят до curator-v5 (паттерн T7.14/T7.35: пин следует за текущим промптом; миграция 0001 пересчитывает hash из кода, fail-closed, `BOOTSTRAP_SNAPSHOT_ID` не меняется); `tests/scenario/test_prompt_pinning.py` — ассерт curator `curator-v4` → `curator-v5` (пины bootstrap).
5. Тест `tests/unit/test_freeze_payloads.py::test_config_v9_pins_curator_v5_and_differs_from_v8_only_there` — по образцу v8-теста: v9 отличается от v8 только `prompts.curator`, версия curator-v5, explorer остаётся explorer-v4.

Хэши: curator-v5.md sha256 `7978f73a36506c26a751bda9561f779cb7c11f8b561ec192862f85f61dd2b0dd`; config-v9 — file sha256 `030f7fd1b1489d246120b0dd476de2fd1f6a980f64f5861de51689dcc11bebf8`, canonical `ef1bf8d7d3bff4ae9cb0c7a0378c7944bccce14e4c049b5a8d89ff4e5e129e7b`.

Не меняется: код поведения (кроме bootstrap-пина), схема CuratorProposal, rules_hash, claim_type_rules, пороги, payload'ы v2…v8, корпуса, ARCHITECTURE.md, данные прогонов (SELECT only). Прогон НЕ запускался, к LLM на 192.168.1.48 обращений не было, фоновых процессов нет — проверка нового промпта будет повторным смоуком отдельной задачей (на config-v9).

Тесты было/стало: 944 → 946 (+2: матрица единого источника, pин v9; ассерт сценария обновлён, не ослаблен).

**Пометка (2026-09-24, T7.39):** пример перепроверки из п.1(б) (настоящий id claim'а SMOKE-V8-K2 `35d5b7ae-…` + реальный факт и `as_of` смоука) заменён в curator-v6 на свободный от данных пример — раздел T7.39. Исторический текст не переписан; curator-v5 заморожен (на него пинится config-v9).

### T7.39 — curator-v6 и config-v10: безопасный пример перепроверки (решение пользователя 2026-09-24)

Основание (найдено при приёмке T7.38): пример в правиле 7 curator-v5 взят из реальных данных — id `35d5b7ae-…` (полный UUID — в примере curator-v5.md и в `docs/eval/SMOKE-V8-K2-report.md`) — настоящий id claim'а из SMOKE-V8-K2, и тот же факт про последнюю стабильную версию Python, что в паке Python корпуса смоука. Следующий шаг — повторный смоук на свежей БД, где у claim'а Python будет ДРУГОЙ id: если модель, узнав в своём вопросе пример, скопирует id из примера, хост его не разрешит, а на границе куратора неразрешённая ссылка отбивает ВСЁ предложение (T7.34) — гейт 5 снова 0, и не отличить «модель не пользуется перепроверкой» от «модель скопировала пример». Второе: в примере был конкретный `as_of` — для вопросов без опорной даты модельный as_of — запасное значение (ADR-0016), его тоже могли скопировать.

1. **`prompts/curator/curator-v6.md`** — НОВЫЙ файл (curator-v5 не тронут — на него пинится config-v9), заголовок `version: curator-v6`. Diff v5→v6 = строка версии + блок примера в правиле 7, остальное побайтово как в v5. Новый пример: тема, которой нет ни в одном корпусе (v1–v4 и корпус смоука) — external_fact «Столица Франции — Париж» («Париж» не встречается ни в одном корпусе; в v4 есть «глава государства Франции» — иной факт); `claim_type = external_fact` → `as_of = null` — для external_fact as_of НЕ обязателен: `ClaimProposal.as_of: datetime | None = None`, `validate_against` требует as_of только для `temporal_fact` (staging.py:114), и в reverify-пути `derive_claim_as_of` external_fact без даты — обычная ветка «none» (ADR-0016/0017); `existing_claim_id` — свежий uuid4 `118b76b3-…` (полный UUID — только в примере curator-v6.md; по конвенции в docs пишется усечённо — иначе нарушался бы собственный страж п.3), проверен grep'ом (case-insensitive, по префиксу) по всему репо, ПОЛНОЙ git-истории и `~/dsh1` — нигде нет; строка контекста в примере — с тем же id. Пример по-прежнему валидный JSON той же формы (проверено парсингом).
2. **Аудит остальных конкретных значений curator-v6 (п.2 задачи):**
   - `c0000000-0000-…` в правиле про dependencies — оставлено ДОСЛОВНО. Обоснование: это конкретный пример ЗАПРЕЩЁнного паттерна заглушек — ровно то, что выдумал K2 в смоуке S1 (`dependency_edge_rejected` «target missing»); дословная форма привязывает запрет к реальному наблюдённому паттерну, а описание словами «подсказки» не уменьшает, но теряет якорь. Риск копирования ≈ 0: это не полный UUID (обрезано «…»), разрешение T7.34 fail-closed — неразрешимая ссылка отклоняется в любом случае; вероятность, что случайный id claim'а начинается с `c0000000-`, = 16⁻⁸ ≈ 2·10⁻¹⁰ на claim.
   - `(supported, E3, p=0.75)` — оставлено: канонические значения rules engine (`GRADE_CONFIDENCE_BASE[E3] = 0.75`, rules_engine.py:32-38) и грамматика строк раздела «Знание» — не артефакт конкретного прогона.
   - Остальное в промпте — общее лексическое наполнение (имена claim_type/evidence-kind, префикс «от 8 символов», индексы 0/0, «N пунктов/строк»): конкретных id, дат и чисел прогонов в v6 не осталось (id `35d5b7ae-…`, «3.14.7» и `as_of` 2026-09-24T12:00:00Z удалены примером).
3. **Тест-страж** — `tests/unit/test_prompt_example_no_real_data.py` (новый): (а) полные UUID в тексте ВСЕХ кураторских промптов (v1…v6) не пересекаются с UUID из `docs/` (отчёты/разборы содержат реальные id прогонов — 57 уникальных); единственное исключение — замороженный curator-v5, зафиксированное ровно парой (uuid, doc) (`35d5b7ae-…` ↔ `docs/eval/SMOKE-V8-K2-report.md` — и есть найденная при приёмке T7.38 утечка; файл не переписывается — на него пинится config-v9): любое расхождение (новый uuid, другой doc) — падение теста; (б) statement примера перепроверки КАЖДОЙ версии (парсится из ```json-блока правила 7) не встречается ни в одном корпусе — docs/eval/question-set-v1…v4.jsonl и смоук-корпус (лежит вне репо, `REPO_ROOT.parent/smoke-v8/question-set-smoke.jsonl` — проверяется при наличии). Общее правило: будущие версии промптов покрываются автоматически.
4. **`docs/eval/config-v10-payload.json`** = config-v9 с ЕДИНСТВЕННЫМ изменением `prompts.curator` → curator-v6 (path `prompts/curator/curator-v6.md`, sha256 содержимого). Формат v9 воспроизведён побайтово (`indent=2`, sorted keys, trailing newline — сверено); v9 не переписан; все пины разрешаются `resolve_prompts`, `TokenBudgets.validate() == []`.
5. **`BOOTSTRAP_PAYLOAD`** — пин curator поднят curator-v5 → curator-v6 (паттерн T7.14/T7.35/T7.38: пин следует за текущим промптом; hash пересчитывается из кода, fail-closed, `BOOTSTRAP_SNAPSHOT_ID` не меняется); `tests/scenario/test_prompt_pinning.py` — ассерт curator `curator-v5` → `curator-v6` (пины bootstrap).
6. Тесты: `tests/unit/test_freeze_payloads.py::test_config_v10_pins_curator_v6_and_differs_from_v9_only_there` — по образцу v9-теста: отличие v10 от v9 только `prompts.curator`; `tests/unit/test_curator_prompt_matrix.py` — распространён на curator-v6/config-v10 (матрица в v6 не меняется, parametrize: v5+v9 и v6+v10).

Хэши: curator-v6.md sha256 `a3dbccdcc6c911fc58c8e7339478fd8938cf8291f2d5bd5a9aff02340e046635`; config-v10 — file sha256 `9ea966b286665136ec7e01ea336598c99824474184df361503fc414df356a9ca`, canonical `de24dbf06ca5f4e3446489016072d6756647a642d337b2c20d89d482b7865a37`.

Не меняется: код поведения (кроме bootstrap-пина), схема CuratorProposal, rules_hash, claim_type_rules, пороги, payload'ы v2…v9, корпуса, ARCHITECTURE.md, curator-v5 (заморожен, пин config-v9), данные прогонов (SELECT only). Прогон НЕ запускался, к LLM на 192.168.1.48 обращений не было, фоновых процессов нет — проверка нового промпта будет повторным смоуком отдельной задачей (на config-v10).

Тесты было/стало: 946 → 956 passed, 4 skipped (+10 passed: страж 2×6 parametrization — 8 passed/4 skipped (старые версии без ```json-примера), пин v10, матрица v6; ассерт сценария обновлён, не ослаблен).

### T7.40 — повторный смоук SMOKE-V10B-K2: закрытие прерванного прогона, перезапуск и разбор (решение пользователя 2026-09-24)

**T7.40a — закрытие SMOKE-V10-K2 и полный перезапуск с нуля (SMOKE-V10B-K2).** Прерванный SMOKE-V10-K2 (2026-09-24 15:14Z, остановлен ~15:35Z — пользователь гнал свои тесты на 192.168.1.48) оставил строку `evaluation_runs` 474e99b6 в `running`. Закрыт ШТАТНЫМ путём (`~/dsh1/close-smoke-v10-run.py`, те же доменные сервисы, что в `_finish` eval-run-драйвера: `compute_gates` + `finish_evaluation_run`; прецедент T7.25/EVAL-3-freeze §10.6, close-eval4c-run.py): `outcome=insufficient_sample`, `finished_at=2026-09-24 18:58:11Z`, eligible=completed=2 (обе сессии — succeeded_partial, commit_attempts 2/2 committed), гейты 10×insufficient_sample + `high_severity_incidents=passed` (N=2 — ожидаемо). Корпус в прерванной БД частично потреблён, поэтому повторный прогон — С НУЛЯ на свежей БД (честное сравнение с SMOKE-V8-K2). `launch.sh` перезапуска отличается от прерванного ровно строками имён (БД `noezema-smoke-v10b-k2`, base `…/smoke-v10b`, node-owner/banner/label/юниты `smoke-v10b-k2-*`, reason) — остальное побайтово (тот же корпус sha b3e05ad5…, payload config-v10, модель, seed 20260924, slo 3600, blind-size 10). Предстартовая проверка зелёная (хэши/пины ok, схема куратора компилируется движком HTTP 200, searxng ok, миграции до 0025, активный снапшот = canonical v10). Run id `213b035c-da7c-474c-bcc1-9357dabc2791`, snapshot `fcc9c355-…`. Прогон 18:59:33Z → 19:17:31Z (18.0 мин), eval-run EXIT=0. БД `noezema-smoke-v10-k2` сохранена как улика.

**T7.40b — остановка воркера и разбор (только анализ: код/тесты/payload'ы/корпуса/ARCHITECTURE.md не тронуты).** Воркер остановлен штатно: до остановки SELECT (7/7 сессий терминальны, 7/7 commit_attempts committed, 0 нетерминальных), `systemctl --user stop smoke-v10b-k2-worker`, юнит inactive, все юниты smoke-v8-k2-*/smoke-v10-k2-*/smoke-v10b-k2-run inactive, процессов нет, orphan-лог-тейлеры убраны. Документ: `docs/eval/SMOKE-V10B-K2-report.md`. Ключевое:
- **ГЛАВНОЕ — перепроверка сработала ВПЕРВЫЕ.** FU Python `6ced7d7e` указал `existing_claim_id` = полный UUID якоря `e856cd0c-…` (не префикс, не заглушка примера `118b76b3-…` из curator-v6); полный путь подтверждён SELECT'ами: `[c:…]` в паке (61 т.) → предложение куратора → резолюция на границе куратора (reference==resolved) → rules pre-check под типом якоря → 4 staging-операции (applied) → fenced commit (19:10:20.607→.644) → аудит `claim_reverified` (seq 36) + `claim_assessed` (seq 37) → новая строка `claim_assessments` `b2d843da-…` (created_in_session = FU), head переехал; grade/confidence не изменились (E3/supported/0.75 → E3/supported/0.75, `requirements_met`); новое source_assertion — иной identity_hash (chocolatey-дрейф content-hash `fa3eb6b8…`→`fd8dc92c…`), перепрос python.org схлопнулся в строку якоря (та же identity, §14.3); `reverify_after` сдвинут к МОМЕНТУ ПРОВЕРКИ: 2026-10-24 19:10:20.618 = коммит перепроверки + 30 д (ADR-0017/T7.32). **Гейт 5 = 1/5 (против 0/5 в v8, тот же знаменатель 5)** — воспроизведён собственным SELECT по формуле `_gate_reuse`.
- **Второй пак (plan.md) НЕ перепроверил — гипотеза «слабые claim'ы не попадают в пак» ОПРОВЕРГНУТА.** Якорь `c6aa88ea-…` БЫЛ в паке FU (53 т., `included_chunks.claims_evidence`); фильтра по grade/статусу в пак-пути нет (retrieval.py — только FTS+лимиты, §5.4.2 — только pending/invalid). Корень: якорь создал claim с `evidence_links: []` (наблюдение `workspace.read` было linkable) + 3 выдуманных dependency-edge (`efd81621-…`, отклонены) → rules `no_evidence` → E0/hypothesis (правило local_observation min_support=1/min_groups=1/min_grade=E2 — невыполнимо при 0 evidence). FU написал в summary «предлагаю операцию перепроверки», но `existing_claim_id: null` → новый дубль-claim `64b1761f-…` (E2/supported). Дефекты модельно-промптовые (хост — по замыслу во всех точках).
- **Регрессия blind_provenance 7/7→5/6** — не кодовая: не прошёл ровно якорный `c6aa88ea-…` (у current assessment 0 связанного evidence → `if not evs: return False`); следствие состава claim'ов (матрица T7.38 сделала local_observation-claim якоря ПРИНИМАЕМЫМ — в v8 всё предложение отклонялось и claim'а не было). 6 vs 7 claim'ов: −2 (модель объединила двойные факты Python «3.14.7+3.15 pre» и Go «1.27.1+дата» в одно statement), +1 (якорь plan.md принят) → знаменатель current_pending_invalid_ancestor 0/6.
- **Матрица type↔evidence (правка (а) T7.38): 0 отказов rules engine во всех 7 сессиях** (v8 — 2, оба убили паки). Типы: 6/6 формально корректны, 5/6 бесспорно по существу, 1 спорен (Спутник temporal_fact для фиксированного исторического факта — natural type external_fact; поведенческого вреда нет, якорь none → evergreen).
- **T7.35**: 38/38 model_runs (7 consolidating + 31 exploring) с prompt_version+prompt_sha256 = пины config-v10, 0 расхождений, 0 NULL. **T7.36**: 0 ошибок грамматики, 38/38 finish=stop, токены мин 168/медиана 521/макс 3109 « 8192. **T7.30/T7.32**: 0/6 отклонений (ООН explicit + Спутник none → NULL/evergreen; Python/Go relative → as_of=дата сессии + 30 д; у Python срок — от момента перепроверки).
- **Статусы: 4 succeeded / 3 partial** (v8 — 2/5). Все 3 partial — free-form `complete_reason` (вывод T7.37b подтверждён дословно); все 4 succeeded — ровно `goal_reached`. Policy deny 9→3 (tool-схемы K2 слабее). Worker «Event loop is closed» — 160 traceback-блоков (teardown-дефект hostctl НЕ исправлен в 5db8ca3 — задача не стояла).
- **Предложения (по приоритету)**: (1, Высокий) промпт — обязательное связывание evidence с claim + различение evidence_links/dependencies (устранит E0-якорь, дубли, blind 5/6, выдуманные id); (2, Высокий) промпт — правило 7: негативный пример («перепроверка без existing_claim_id — это новый claim») + случай слабого (hypothesis/E0–E1) claim'а; (3, Средний) нормализация complete_reason (host, T7.37b п.3); (4, Средний) hostctl dispose в том же asyncio.run (T7.37b п.4); (5, Низкий) следить за tool-схемами K2; (6, Низкий) гайдлайн «исторический факт → external_fact». Все — отдельной задачей по решению пользователя.

БД (noezema-smoke-v10b-k2, noezema-smoke-v10-k2, noezema-smoke-v8-k2, noezema-eval*) — только SELECT. Прогоны не запускались, к LLM на 192.168.1.48 обращений не было, фоновых процессов нет.

### T7.41 — полная проверка параллельно: pytest-xdist `-n auto` (решение пользователя 2026-09-25: «займёмся оптимизацией»)

Основание: полная проверка (`ruff + mypy + pytest`) была однопоточной — baseline 2026-09-25 (583d1e1): pytest 489.0 с (8:08) на 960 тестах при 6 свободных ядрах на `.87` (load <1) — время уходило в последовательное выполнение тестов (scenario-тесты: scratch-БД + `alembic upgrade head` subprocess на тест). Задача — параллельный pytest через pytest-xdist с честным замером до/после и двумя подряд параллельными прогонями (ловля нестабильности от параллелизма).

1. **Разбор ДО правки — источники общего состояния под параллелью (перепроверено по коду, не по разведке):**
   - **Риск #1 (БД) — подтверждён БЕЗОПАСНЫМ.** `migrated_db` (tests/conftest.py): scratch-БД `noezema_mig_<token_hex(4)>` (2^32 вариантов — коллизия между воркерами и с остатками пренебрежимо мала), `CREATE DATABASE` → `alembic upgrade head` отдельным subprocess → DROP в teardown; каждый тест берёт СВОЮ БД — взаимно независимы. Единственный общий ресурс — соединения сервера (max_connections=100, занято 6 до прогона); под 6 воркерами пик замерен 13 из 100 (мониторинг `pg_stat_activity` в ходе обоих параллельных прогонов).
   - **Риск #2 (docker-образ) — подтверждён, ПОЧИЩЕН.** `sandbox_image` (session-scope, tests/conftest.py) строила `noezema-sandbox:test` при отсутствии (`docker build -t` с фиксированным тегом); под xdist каждый воркер — отдельный процесс со своим session-scope → до N одновременных `docker build` одного тега (гонка + N лишних сборок). Решение — вариант «собрать ОДИН РАЗ до pytest»: в команду полной проверки (AGENTS.md §6) добавлен шаг `docker image inspect … || docker build …` ДО запуска pytest (с тем же `DOCKER_CONFIG`, что и фикстура), а фикстура `sandbox_image` теперь ТОЛЬКО проверяет наличие образа и при отсутствии падает `pytest.fail` с понятной инструкцией (сборка команды не ослаблена — тесты не тронуты; в CI без docker-движка sandbox-тесты и так skip через `docker_engine`).
   - **Другие источники общего состояния — найдено, все безопасны по замыслу, чинить нечего:**
     - `docker_engine` (session-scope) пишет `os.environ["DOCKER_CONFIG"]` — одно и то же значение во всех воркерах (идемпотентно);
     - `fake_llm` — собственный subprocess на эфемерный порт (`bind :0`) на тест; состояние `_state` сервера — внутри его процесса;
     - sandbox-контейнеры: имена `noezema-sb-<session_id[:12]>` — фиксированные session_id в `test_sandbox_runtime.py` РАЗНЫЕ на тест (11111111-2222 / aaaaaaaa-bbbb / ffffffff-0000 — каждый тест собирается один раз), в остальных файлах — `uuid4`; `work_root` = `tmp_path` (уникален на тест) → коллизий контейнеров нет;
     - `test_security_gate` — ВЛОЖЕННЫЙ `pytest -m security` subprocess (внутренний — последовательный, свой scratch-БД, наследует `NOEZEMA_TEST_DATABASE_URL`); добавляет нагрузку на соединения, общего состояния нет;
     - `127.0.0.1:8888` (searxng_url) и `127.0.0.1:1` — конфигурационные строки фейков/заведомо мёртвых адресов, не биндуются (подтверждено разведке);
     - в тестах нет autouse-фикстур, `global`/atexit/signal, module-level MUTABLE-состояния (только константы/regex/immutable UUID), monkeypatch — function-scope с автосбросом, файловых записей вне `tmp_path` нет; в app-коде нет lru_cache/синглтонов;
     - pytest-randomly не установлен → порядок сбора детерминирован; порядок-зависимых тестов нет (каждый тест создаёт свою БД/tmp/сервер) → `xdist_group` и `--dist=loadscope` НЕ потребовались, распределение — default `load` (по тесту — лучшее выравнивание при неравномерных длительностях).
   - **Побочный наблюдательный вывод (не дефект этой задачи):** в `noezema-test-db` накопились 72 старые scratch-БД (53 `noezema_mig_*` + 19 `noezema_dbg_*`; 14–24.09 — остатки прерванных/старых прогонов) + улики `noezema-eval*/noezema-smoke*`. Замером подтверждено, что ни baseline, ни параллельные прогоны scratch-БД НЕ утекают (созданные прогонками дропнуты в teardown: счётчик 72→72, контрольный одиночный прогон — 4 созданные/4 дропнутые); периодические «касания» старых БД (запись `pg_class`/`pg_statistic`) — фоновый autovacuum, паттерн есть и в прошлые дни (13/12/30 БД в сутки 22/23/24.09). Старые БД не чистились (не в задаче; `noezema-eval*/noezema-smoke*` — улики).

2. **Зависимости:** `pytest-xdist>=3.8` в `[project.optional-dependencies].dev` (uv.lock в проекте не ведётся — по существующему паттерну: CI ставит `uv pip install --system ".[dev]"`; локально `uv pip install --python .venv/bin/python` → pytest-xdist 3.8.0 + execnet 2.1.2).

3. **Число воркеров: `-n auto`** (на `.87` = 6): 6 свободных ядер при load <1; 960 тестов = 367 unit (CPU-быстрые) + 280 scenario (subprocess/БД-bound — здесь и весь выигрыш: ~2–5 с на тест от scratch-БД+alembic) + 16 security (включая вложенный gate-прогон) — default `load` выравнивает длинные хвосты по воркерам; единственный cost cap — соединения БД (100, запас 94) — при 6 воркерах пик 13, подтверждён замером; docker-сборка больше не масштабируется с числом воркеров (собирается один раз ДО pytest — п.1 риск #2). Явное число (напр. `-n 4`) дало бы меньше параллелизма scenario-хвоста без выигрыша в надёжности.

4. **AGENTS.md §6** — единственная правка AGENTS.md: команда полной проверки получила шаг сборки образа ДО pytest (`DOCKER_CONFIG` writable — sandbox агента запрещает `~/.config`; по умолчанию репо-локальный `.docker-config`, логика фикстуры) и `pytest -n auto -q` + пояснение. Вне §6 — не тронуто.

5. **Замер (честный, полный `ruff + mypy + pytest`, `NOEZEMA_TEST_DATABASE_URL`, 583d1e1):**
   - baseline последовательный (до правки): 956 passed + 4 skipped, pytest 489.0 с, полный 490.8 с;
   - параллельный #1 (`-n auto`, 6 воркеров): pytest 234.0 с (3:54), полный 234.7 с — 956 passed + 4 skipped, 0 failed, 0 error (×2.09 к baseline);
   - параллельный #2 (повтор подряд, ловля флаков): pytest 239.2 с (3:59), полный 239.8 с — 956 passed + 4 skipped, 0 failed, 0 error (×2.04 к baseline);
   - число тестов во всех трёх прогонах совпадает (956 passed + 4 skipped, 0 failed, 0 error); флаков от параллелизма: НЕТ (два параллельных прогона подряд — одинаковый состав и результат);
   - `pg_stat_activity` (сэмпл каждые 5 с) пик под параллелью: 13 из 100 (idle-фон 6) — запас большой, 6 воркеров безопасны; после прогонов: висячих контейнеров нет, scratch-БД, созданные прогонками, все дропнуты (счётчик legacy `noezema_mig_*/noezema_dbg*` 72→72).

Не меняется: rules_hash, claim_type_rules, пороги §22.2, payload'ы, промпты, корпуса, ARCHITECTURE.md, содержимое тестов (кроме фикстуры `sandbox_image` — сборка вынесена из неё в команду §6, ассерты/логика тестов не тронуты; xdist_group-метки не понадобились). Прогон NOEZEMA не запускался, к LLM 192.168.1.48 обращений не было, фоновых процессов нет (docker-контейнеры sandbox убираются в teardown — проверено `docker ps`).

### T7.43 — curator-v7 и config-v11: обязательное связывание evidence + правило 7 — негативный пример и случай слабого claim'а (решение пользователя 2026-09-25: два дефекта SMOKE-V10B-K2 — предложения (1) и (2) высокого приоритета)

Основание (`docs/eval/SMOKE-V10B-K2-report.md` §5/§6/§12, curator-v6): якорь plan.md (сессия `de32a56e`) создал claim с `evidence_links: []`, хотя наблюдение `workspace.read` (содержимое созданного им же файла) было linkable-evidence и цитировалось в rationale, а вместо связи evidence модель предложила 3 выдуманных dependency-id (`efd81621-…`, отклонены fail-closed на коммите) → rules engine `no_evidence` → **E0/hypothesis** → цепная поломка: этот же E0-claim провалил гейт blind_provenance_path (0 evidence — нечего обходить провенансом, 7/7 → 5/6). Follow-up этого claim'а (`9ec3807f`) написал в summary «предлагаю операцию перепроверки», но оставил `existing_claim_id: null` → создал дубль-claim вместо перепроверки. Дефекты модельно-промптовые (хост — по замыслу во всех точках). Проверка менеджера по `claim_type_rules` ВСЕХ payload'ов v2…v10: `min_support_evidence >= 1` для ВСЕХ 8 claim_type — 0 evidence нелегально ВСЕГДА, это не крайний случай, а универсальное правило. (Уточнение: `min_support_evidence = 2` — у ЧЕТЫРЁХ типов: external_fact, temporal_fact, empirical_conjecture, procedural; у остальных шести — 1; в постановке задачи «остальные — 1» не учитывало empirical_conjecture/procedural.)

1. **`prompts/curator/curator-v7.md`** — НОВЫЙ файл (curator-v6 не тронут: на него пинится config-v10), заголовок `version: curator-v7`. Diff v6→v7 = строка версии + две правки, остальное побайтово:
   - (а) **Обязательное связывание evidence + различение evidence_links/dependencies** (описание `evidence_links` в протоколе + правило 1, обратное направление): **КАЖДЫЙ claim обязан иметь минимум одну запись `evidence_links`** (верхний уровень CuratorProposal, `evidence_index` → `claim_index`) — claim с 0 evidence никогда не проходит rules engine (минимальная поддержка — минимум одно evidence допустимого для типа вида; минимум два, где требуется) — нет evidence, нет и claim: создай вопрос; явно противопоставлены: `evidence_links` — ЗАГРУЖЕННЫЕ СЕЙЧАС источники/наблюдения, подкрепляющие claim; `dependencies` (внутри `claims`) — ссылки на ДРУГИЕ claim'ы (граф зависимостей/переоценка), id только видимые в контексте, никогда не выдумывать; короткий пример на наблюдённую ошибку: наблюдение прочитано — файл `notes/plan.md` в списке evidence → claim с записью `evidence_links` на этот `evidence_index` и БЕЗ `dependencies` (если не ссылаешься на другой claim);
   - (б) **Правило 7 «Перепроверка»** — добавлены: случай СЛАБОГО claim'а (перепроверка работает независимо от текущей оценки существующего claim'а — E0/E1/E2/E3: действие пересчитывает оценку заново через rules engine по новому набору evidence; существующий claim слабый (hypothesis, E0–E1), а свежий evidence есть → перепроверка с `supports` поднимет оценку; новый claim на уже установленный факт только потому, что старый слабый, — не нужен) + **слова — не операция**: текст `summary` («уже установлено», «предлагаю операцию перепроверки») механизмом не читается — операция определяется ТОЛЬКО полем `existing_claim_id`; если решил перепроверить — обязан заполнить `existing_claim_id`, слов в summary недостаточно; и НЕГАТИВНЫЙ пример ровно на наблюдённую ошибку (§5, `9ec3807f` seq 17): в контексте `[c:26170444-…] Столица Испании — Мадрид (hypothesis, E0, p=0.05)`, вопрос — про тот же факт, сверка совпала, но в summary «Факт уже установлен — предлагаю операцию перепроверки», а `existing_claim_id: null` → JSON-пример → это новый claim (дубль), а не перепроверка; правильно — то же действие с `existing_claim_id` = id claim'а (как в контексте).
2. **Безопасность нового примера (паттерн T7.39):** id `26170444-…` (полный UUID — только в примере curator-v7.md; по конвенции в docs пишется усечённо — иначе нарушался бы собственный страж п.4) — свежий uuid4; grep (case-insensitive, по префиксу) по всему репо, ПОЛНОЙ git-истории и `~/dsh1` — нигде нет; не совпадает с `118b76b3-…` (пример curator-v6, заморожен). Тема негативного примера — «Столица Испании — Мадрид»: вне всех корпусов (v1–v4 + смоук, проверено), ДРУГОЙ факт, чем у позитивного примера («Столица Франции — Париж»): два claim'а с одинаковым statement+claim_type схлопываются дедупом T7.9 и не могут существовать вместе — позитивный/негативный примеры с одним фактом были бы внутренне противоречивы. `p=0.05` — каноническое значение ветки `no_evidence` rules engine (rules_engine.py:190), не артефакт прогона. Оба JSON-примера — валидный JSON (проверено парсингом).
3. **Единый источник истины (числа о evidence):** в промпте НЕТ цифр по типам — формулировки словами («минимум одну запись», «минимум одно evidence допустимого для типа вида; минимум два, где требуется»), список типов, где требуется два, НЕ приводится (иначе разошлось бы с payload: `min_support_evidence = 2` у четырёх типов, не двух — см. уточнение в основании). Единственное числовое утверждение промпта — универсальный нижний предел «claim с 0 evidence никогда не проходит rules engine» — закреплено тестом-сверкой с payload: `tests/unit/test_curator_prompt_matrix.py::test_min_support_evidence_floor_matches_prompt_claim` (`min_support_evidence >= 1` у всех 8 типов config-v11 + наличие утверждения в тексте промпта). Матричный тест расширен парой curator-v7 + config-v11 (матрица в v7 не меняется — множество пар идентично).
4. **Тест-страж T7.39** — `tests/unit/test_prompt_example_no_real_data.py` распространён на curator-v7 (glob подхватывает автоматически): регулярка `EXAMPLE_JSON` теперь применяется через `findall` — находит ОБА JSON-примера правила 7, а не только первый; `test_reverify_example_statement_not_in_any_corpus` — statements всех claims ВСЕХ примеров не встречаются ни в одном корпусе (v1–v4 + смоук); НОВЫЙ `test_rule7_example_ids_do_not_intersect_docs` — id ОБОИХ примеров (у позитивного — полный UUID внутри JSON, у негативного — id строки контекста `[c:…]`) не пересекаются с UUID из `docs/` (замороженное исключение curator-v5 сохранено; v1–v4 — skip: правило 7 без id).
5. **`docs/eval/config-v11-payload.json`** = config-v10 с ЕДИНСТВЕННЫМ изменением `prompts.curator` → curator-v7 (path `prompts/curator/curator-v7.md`, sha256 содержимого). Формат v10 воспроизведён побайтово (`indent=2`, sorted keys, trailing newline — сверено); v10 не переписан (file sha256 `9ea966b2…` не изменился); все пять пинов разрешаются `resolve_prompts`, `TokenBudgets.validate() == []`. Тест `tests/unit/test_freeze_payloads.py::test_config_v11_pins_curator_v7_and_differs_from_v10_only_there` (по образцу v10-теста: отличие v11 от v10 — только `prompts.curator`).
6. **`BOOTSTRAP_PAYLOAD`** — пин curator поднят curator-v6 → curator-v7 (паттерн T7.14/T7.35/T7.38/T7.39: пин следует за текущим промптом; hash пересчитывается из кода, fail-closed, `BOOTSTRAP_SNAPSHOT_ID` не меняется); `tests/scenario/test_prompt_pinning.py` — ассерт curator `curator-v6` → `curator-v7` (пины bootstrap).
7. **Пометки-ссылки** — в `docs/eval/SMOKE-V10B-K2-report.md` у предложений (1) и (2) добавлено «— **сделано в T7.43** (curator-v7, config-v11)»; исторический текст не переписан. (В постановке задачи предложения названы «§9»; в файле раздел «Предложения (по приоритету)» — §12, §9 — статусы сессий — пометки проставлены в §12, где предложения реально стоят.)

Хэши: curator-v7.md sha256 `19d6c6e8e2d890ae4cf5c42e5e0ec6bd864a2c6ef12fdb2766e01f977bc89a3a`; config-v11 — file sha256 `67468a2e3f8fe1b7025ab2e4946224f9488495c7fe42eba901ac4c3d58353de8`, canonical `a407ce8346dedb7b219572a5a9e95c13a704f35d3479e0c0e42266e9c4105600` (canonical v10 `de24dbf0…` не изменился — сверено).

Не меняется: код поведения (кроме bootstrap-пина), схема CuratorProposal/EvidenceLink/ClaimDependencyProposal, rules_hash, claim_type_rules, пороги, payload'ы v2…v10, корпуса, ARCHITECTURE.md, curator-v6 (заморожен, пин config-v10), данные прогонов (SELECT only). Прогон НЕ запускался, к LLM на 192.168.1.48 обращений не было, фоновых процессов нет — проверка нового промпта будет повторным смоуком отдельной задачей (на config-v11; разрешение на обращение к .48 — за пользователем).

Тесты было/стало: 956 → **964** passed, 4 → **8** skipped (+8 passed: pин v11, floor-сверка min_support_evidence, матрица v7, страж ×3 на v7 (uuid-пересечение, statements обоих примеров, id обоих примеров) + страж id на v5/v6; +4 skipped: новый id-страж на v1–v4, у которых правило 7 без id).

Проверка: ruff + mypy strict + pytest -n auto (NOEZEMA_TEST_DATABASE_URL) — 964 passed, 8 skipped. Пометка: на 2-м из 3 подряд параллельных прогонов один раз упал timing-тест `test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease` (PendingRollbackError); 1-й и 3-й прогоны — зелёные, одиночный прогон — зелёный; к изменениям (промпт/payload/docs) отношения не имеет — существующий флок под `-n auto`.

### T7.44 — SMOKE-V11-HALOGEN: повторный смоук на config-v11 (curator-v7) + halogen и его разбор (a: переключение на halogen и запуск; b: остановка воркера и разбор в сравнении с SMOKE-V10B-K2)

**Ограничение (главное):** в этом прогоне изменились ДВЕ переменные разом — промпт curator-v6 → curator-v7 И модель K2 → halogen-flash-next; это не чистый эксперимент (эффект правки промпта и эффект смены модели не разделены). Документ: `docs/eval/SMOKE-V11-HALOGEN-report.md`.

**a. Переключение на halogen и запуск (T7.44a, отчёт `~/dsh1/task37-smoke-v11-halogen.log`):** env — ровно 2 ключа (`NOEZEMA_LLM_MODEL` → `halogen-flash-next`, `NOEZEMA_LLM_SCHEMA_PROFILE` → `halogen`, существующий профиль ADR-0012; base URL/лимиты не тронуты), бэкап `noezema-llm.env.bak.2026-09-25T091316Z`; `smoke-v11/launch.sh` (отличие от v10b: DB `noezema-smoke-v11-halogen`, модель/профиль, config-v11, owner, юниты `smoke-v11-halogen-*`, + предстартовый чек юнитов/процессов); preflight зелёный (хэши config-v11 file `67468a2e…`/canonical `a407ce83…` пересчитаны и совпали с T7.43; корпус `b3e05ad5…` побайтово тот же; пины 5/5; схема куратора под профилем halogen — HTTP 200); run id `9d256ed9-…`, seed 20260924 (общий для серии), 09:16:41Z.

**b. Разбор (T7.44b), итоги (подробности — в отчёте):**

1. **Воркер остановлен** (SELECT-контроль до: 7/7 терминальны, 7/7 commit_attempts `committed`): `smoke-v11-halogen-worker` → inactive, run — unload, все 6 юнитов v8/v10/v10b — inactive, процессов нет, orphan-тейлер task37 убран.
2. **Правка (а) обязательного связывания evidence — СРАБОТАЛА:** план-якорь `de62dddd` (сессия 3) теперь `evidence_links`=1 → **E2/supported** (в v10b `c6aa88ea` — 0 evidence → E0); Python-якорь `5208f25c` — 2 evidence, E3; E0-claims нет, выдуманных id нет (0, против 3 в v10b), blind_provenance **6/6** (было 5/6).
3. **Правка (б) правила 7 (негативный пример + «слова не операция») — НЕ сработала на halogen:** **0/7** claim-операций с непустым `existing_claim_id` (в v10b K2 — 1/7), **0** `claim_reverified`; оба FU написали «предлагаю перепроверку» в summary и оставили поле null (улики: аудит `75295140` seq 24, `b52c29bb` seq 17).
4. **Гейт 5 = 1/6** (пересчёт по `_gate_reuse` совпал с сохранённым, ci95 [0.0301, 0.5635]): знаменатель **6** (против 5 в v10b) — ровно на 1 больше, потому что план-якорь теперь значим (E2, следствие п.2); числитель 1 — Python-якорь `5208f25c` тронут 2 сессиями. **Механизм записи Python-пака другой, чем в v10b:** не `existing_claim_id`, а **dedup T7.9** (statement FU побайтово = якорю, md5 `6eecaa4b…`; коммит: `claims_reused: 1, claims_created: 0, evidence_deduped: 1`) → assessment-строка `5cd864cb` (created_in_session = FU). План-пак дедупом НЕ спасён (statement перефразирован: «содержится» vs «записано») → **дубль-claim `62611669`** (тот же класс Д2 v10b), перепроверки нет.
5. **Последняя сессия (7, 1422 с, succeeded_partial) — это Go, не Спутник** (Спутник — сессия 6, 581 с, succeeded): цикл `artifact.create`-deny → ранний complete отклонён (`source_coverage_incomplete`) → fetch 404 (URL с обрезанной скобкой `Go_(язык_программирования` — класс v10b) → refetch → `goal_reached: <текст>` (не точное равенство) → partial; +1 вопрос `444efb44` (противоречие дат Go в Википедии — легитимен).
6. **Сравнение с v10b:** типы 6/6 формально (0 отказов rules), 5/6 по существу (Спутник temporal_fact — то же спорное наблюдение); T7.35: 38/38 model_runs с пином (curator-v7 `19d6c6e8…` ×7, explorer-v4 ×31), 0 расхождений; T7.36-halogen: 0 ошибок схемы (все паттерны), 38/38 stop, токены макс 7419 (лимит 8192; K2 v10b макс 3109); T7.30/T7.32: 0/6 отклонений (explicit/none → NULL/evergreen, relative → as_of=дата сессии + срок=момент проверки+30д); статусы **6 succeeded / 1 partial** (halogen даёт точный `goal_reached` 6/7 против 4/7 у K2 v10b — модельная особенность, host-фикс T7.37b п.3 не сделан); длительности: halogen медленнее на 5 из 6 первых сессий (итог 57,5 мин против 18,0; исключение Python-FU 226 с < 288 с); policy deny 5 (все `artifact.create`) + 1 fetch-404; worker «Event loop is closed» — 466 блоков (teardown-дефект hostctl, не исправлен, не в задаче).
7. **Разделение промпт/модель:** к (а) — E0-якорь исчез, blind 6/6, знаменатель 5→6 (направленный эффект правки виден, но «промпт vs модель» — только при контроле K2); к halogen — скорость, точность `goal_reached`, `artifact.create`-deny, токены, цитирование statement побайтово (dedup vs reverify); **не разделено однозначно** — причина не-использования `existing_claim_id` (дефект правки (б) vs особенность модели) и выдуманных-id=0.
8. **Вывод и следующий шаг (подтверждено):** правка (а) сработала, (б) — нет на halogen; подтверждение НЕ чистое (две переменные). **Нужен смоук curator-v7 + K2** — разделит: (а) K2 под v7 свяжет evidence + заполнит `existing_claim_id` → обе правки промптовые; (б) не заполнит → правка (б) недостаточна; (в) не свяжет evidence → эффект (а) модельный. Медлительность halogen — отдельный модельный факт.

Не меняется: код, тесты, payload'ы v2…v11, промпты, корпуса, ARCHITECTURE.md, rules_hash, пороги; данные прошлых прогонов (noezema-eval*/noezema-smoke* — SELECT only). Прогон запускался (T7.44a, разрешение пользователя), LLM-обращений от этой задачи (T7.44b) не было; фоновых процессов нет.

Проверка (T7.44b, подтверждение отсутствия регрессии, AGENTS.md §6, параллельно разбору): ruff + mypy strict + pytest -n auto (NOEZEMA_TEST_DATABASE_URL) — 964 passed, 8 skipped (без изменений против baseline `4442f48`; код не тронут).

### T7.45 — SMOKE-V12-K2: чистый смоук curator-v7 + K2 и его разбор (a: переключение обратно на K2 и запуск; b: остановка воркера, разбор в сравнении с SMOKE-V10B-K2 + документирование NUL-байт дефекта как открытого пункта)

**T7.45a — переключение обратно на K2 и запуск SMOKE-V12-K2** (отчёт `~/dsh1/task39-smoke-v12-k2.log`): env — ровно 2 ключа (`NOEZEMA_LLM_MODEL` → `k2-horizon-mova-36b-a4b-rocmfp4-fast`, `NOEZEMA_LLM_SCHEMA_PROFILE` → `llamacpp-rocmfpx` — обратное переключение после T7.44a; base URL/лимиты побайтово не тронуты, diff подтверждён), бэкап `noezema-llm.env.bak.2026-09-25T105945Z` (конвенция имён как у двух прошлых). `smoke-v12/launch.sh` = v10b + строки имён (БД `noezema-smoke-v12-k2`, base `…/smoke-v12`, label/node-owner/юниты `smoke-v12-k2-*`, reason) + payload config-v10→config-v11 (хэши пересчитаны, не взяты из STATUS/отчётов: file `67468a2e…`/canonical `a407ce83…` — совпали с T7.43) + предстартовые проверки неактивности юнитов прошлых смоуков (v8/v10/v10b-k2, v11-halogen) и отсутствия других прогонов (перенесено из v11 — общая, не halogen-специфичная часть; halogen-проверки профиля НЕ перенесены — профиль llamacpp-rocmfpx, как в v10b). Preflight зелёный (корпус `b3e05ad5…` побайтово тот же, что во всех прошлых смоуках; пины 5/5; схема куратора под llamacpp-rocmfpx — HTTP 200; searxng ok; миграции до 0025). Run id `3b53a8c2-8fcb-408c-a647-03d2124750a5`, snapshot `820a9165-…`, seed 20260924 (общий для серии), slo 3600, blind-size 10. Прогон 11:05:24Z → 11:22:27Z (17,0 мин), eval-run EXIT=0. **Единственная переменная относительно SMOKE-V10B-K2 — промпт curator-v6→v7** (модель K2 в обоих, профиль/корпус/остальное payload те же) — чистый контроль, запрошенный SMOKE-V11-HALOGEN-report.md §7–8.

**T7.45b — остановка воркера и разбор (только анализ: код/тесты/payload'ы/промпты/корпуса/ARCHITECTURE.md не тронуты; БД — только SELECT; к LLM на .48 обращений не было).** Воркер остановлен штатно: до остановки SELECT (6/6 сессий терминальны, 6/6 commit_attempts = committed, 0 нетерминальных, **0 строк упавшей сессии `19b3f7a7…` в `sessions`** — п.1), `systemctl --user stop smoke-v12-k2-worker` → inactive; все 10 юнитов `smoke-v8-k2-*`/`smoke-v10-k2-*`/`smoke-v10b-k2-*`/`smoke-v11-halogen-*`/`smoke-v12-k2-*` — inactive (transient-unload, rc=4 — паттерн v8/v10b/v11); процессов hostctl/worker.sh/tick'ов нет. Документ: `docs/eval/SMOKE-V12-K2-report.md`. Ключевое:
1. **Дефект NUL-байта (документирован, НЕ исправлен — отдельная задача; отчёт §3).** Python-якорь `19b3f7a7…` (8 шагов: fetched python.org/downloads + chocolatey.org/packages/python314, evidence собрано, claim про Python 3.14.7 сформирован) упал на seq 42 — запись **финального отчётного audit-события** (report, `orchestrator.py:792–808`, JSONB payload): `asyncpg.exceptions.UntranslatableCharacterError: unsupported Unicode escape sequence, DETAIL \u0000 cannot be converted to text` (ошибка серверного парсера JSONB — NUL в строке внутри payload). **Откатилась ВСЯ phase-1-транзакция сессии: 0 строк в sessions/audit_events/model_runs/session_staging/actions/checkpoints (факт SELECT'ом)** — 8 шагов работы полностью потеряны (выжили только `sources`-строки — proxy-транзакция — и артефакты на диске). **Источник (исключение фактами):** сырые артефакты обеих страниц чисты (`a8951810…` python.org / `a1bb09ed…` chocolatey — 0 NUL-байтов, sha256 = `content_hash`; у python.org тот же content-hash, что в v10b), нормализованные тексты чисты (пересчёт `normalize_content`: 0 NUL; retry-коммит с теми же текстами в БД без ошибки), единственный файл workspace чист (`notes/plan.md` — 0 NUL → workspace.read исключён), memory.search исключён (результат — только строки retrieval по закоммиченным plan-claim'ам, их statement'ы в БД чисты) → **NUL-байт в stdout `python.execute`**: исполнитель `decode("utf-8","replace")` сохраняет NUL (`apps/orchestrator/executor.py:148`) → computation evidence-payload `stdout[:2000]` (`apps/orchestrator/evidence.py:104`) → report-payload (`orchestrator.py:794`) → JSONB INSERT; ранние audit-INSERT'ы выжили из-за 1000-символьного капа `_cap_args`. Улика: собственный `complete_reason` модели в событии — «нечитаемый результат («2 None»), — это неAuthoritative evidence». **Где нужна санитизация (направление, не сделано):** (1) корень — захват вывода (`executor.py`, `_python_execute`/`workspace.read`); (2) граница evidence — `observation_to_evidence`; (3) последняя линия — `AuditService.record`/`AuditEventRepository.create` (исключает амплификацию: один NUL → смерть всей сессии). **Не связан с curator-v7 — подтверждено** (дефект конвейера данных; curator-промпт не входит в report-payload; проявился бы при любой модели/промпте). Побочный эффект: вопрос остался `candidate` → драйвер выбрал его заново в слоте 4 (retry `8feaee93`, закоммичен), Go (`7968bfb3…`) в 7 слотов не влез — не выполнялся (остался `candidate`).
2. **Гейт 5 = 2/5 — воспроизведён кодом репозитория (`_gate_reuse`, SELECT only): stored == recomputed, MATCH.** Знаменатель 5 (6 закоммиченных claim'ов; chocolatey E1/hypothesis не значим; потерянная сессия своего не добавила — откат). Числитель 2 — **оба через НАСТОЯЩУЮ перепроверку (`existing_claim_id` полным UUID), не dedup**: Python `aa132f9d…` (anchor-retry + FU — ветки evidence и assessments) и plan `035e8a14…` (якорь + FU — ветка assessments; evidence FU схлопнулось в строку якоря, §14.3). **plan.md-FU `63bccf6c` использовал `existing_claim_id` — ПОЛНЫЙ UUID якоря `035e8a14…`** (36 символов, не префикс/заглушка; `reference == resolved`; событие `claim_reverified` seq 26; строка `claim_assessments` с `created_in_session = FU`) — **первый на K2+curator-v7 случай настоящей перепроверки и первый в серии для plan.md-пары** (v10b/v11 — дубль). Python-FU `ce17933b` — аналогично (полный UUID `aa132f9d…`, `claim_reverified`, +1 evidence/2 deduped). В аудите 2 `claim_reverified` (v10b — 1, v11 — 0), 2/2 FU заполнили поле (v10b — 1/7, v11 — 0/7).
3. **Вердикт по правкам (чистое сравнение v10b→v12, единственная переменная — промпт):** **(б) сработала на K2 под curator-v7** — план-пара, дававшая при v6 дубль (`existing_claim_id: null` + «предлагаю операцию перепроверки» — дословный негативный пример правила 7 из v7), при v7 заполнила поле полным UUID и перепроверила якорь → эффект промптовый; неиспользование halogen'ом (v11) — модельная особенность, правка достаточна. **(а) подтверждена модельно-независимо** (K2+v6 нарушение: E0-якорь/3 fabricated id; halogen+v7 и K2+v7 — якорь с evidence, E2, blind 6/6, 0 fabricated).
4. **Статусы: 2 succeeded + 4 succeeded_partial (из 6) + 1 потеряна** (в постановке «4 succeeded + 2 partial» — перестановка; факт БД/run.log: succeeded = оба FU). Причины partial по аудиту: 3 × free-form `complete_reason` (ожидаемый класс T7.37b п.3) + 1 × **`budget_exhausted`** (НОВЫЙ класс: retry-якорь сжёг 10-шаговый бюджет: 7 попыток fetch (5 ok, включая повтор того же URL; 404 `python.org/api/v2/downloads/`; repeat-guard), deny `memory.search` (лишний аргумент `limit`), `shell.execute`→unreachable; claim при этом закоммичен E3). Tool-deny: 2 (v8: 9 → v10b: 3 → v11: 5 → **v12: 2**).
5. **Числа:** T7.35 — 33 model_runs (6 consolidating + 27 exploring), **33/33 пины = config-v11** (curator-v7 `19d6c6e8…`×6, explorer-v4 `5829a55c…`×27), 0 NULL, 0 расхождений; `tool_schema_hash` 27/27 exploring `f76284735e…`, 6 consolidating NULL (по замыслу). T7.36-K2 — **оба паттерна 0** (grammar 0, keyword/400 0, PromptPinError 0, `output_schema_valid=false` 0, `request_rejected`/`curator_error` 0); 33/33 finish=stop; токены 105/460/2956 « 8192. T7.30/T7.32 — 6 claim'ов, **0/6 расхождений** (explicit→NULL/evergreen; none→NULL/evergreen ×3; relative×2 → as_of 2026-09-25 + 30 д от коммита якоря (11:17:35.710Z) и **от момента перепроверки** (11:19:31.972Z — ADR-0017)). Типы: 6/6 формально (0 отклонений rules, 0 fabricated id); по существу — Спутник выбрал **external_fact** («естественный» тип для фиксированного исторического факта — наблюдение v10b §7 само разрешилось).
6. **Итог по серии T7.38–T7.45 (отчёт §10):** (а) — промптовый эффект, модельно-независим — **доказано** (обе модели соблюдают v7, K2+v6 нарушал); (б) — **достаточен на K2** (2/2 FU, полные UUID, 2 `claim_reverified`, гейт 5 = 2/5 впервые >50%), halogen — модельная особенность. **Для вопроса про эффект промпта серия закрыта на данном уровне уверенности — новый прогон НЕ нужен**; **нужен один прогон ПОСЛЕ починки NUL-байт дефекта** — восстановить полную статистику 7/7 (Go-пара в v12 не измерялась; слот занят retry-повтором) и закрыть инфраструктурный пункт. Python-пара статистически не потеряна (перепроверку записал retry-якорь + FU).

Не меняется: код, тесты, payload'ы v2…v11, промпты, корпуса, ARCHITECTURE.md, rules_hash, пороги; данные прошлых прогонов (noezema-eval*/noezema-smoke* — SELECT only; noezema-smoke-v12-k2 — только SELECT; артефакты/`normalize_content` — чтение с диска). Прогон запускался (T7.45a, разрешение пользователя); LLM-обращений от этой задачи (T7.45b) не было; фоновых процессов нет (воркер остановлен, все юниты inactive).

Проверка (T7.45b, подтверждение отсутствия регрессии, AGENTS.md §6, параллельно разбору): ruff + mypy strict — зелёные; pytest -n auto (NOEZEMA_TEST_DATABASE_URL) — **958 passed, 6 failed, 8 skipped** (215 с). Изменения задачи — только `docs/` (отчёт + STATUS + пометки в v10b/v11), код/тесты не тронуты (git diff = 4 файла docs). 6 падений — НЕ регрессия этой задачи:
- **5 × `tests/scenario/test_backup_pitr.py::test_restore_drill_*` — обнаружен LATENT TIME-BOMB в тесте (отдельная задача).** Тесты шьют бэкапы с фиксированным `NOW = 2026-09-15T12:00Z` (`test_backup_pitr.py:41`) и `retention_days` 1/10/20, т.е. `retention_until = NOW + retention_days` (`packages/backup/service.py:230`), а restore-drill выбирает точку запросом с **реальным** `now()` (`packages/backup/restore.py:85–92`: `retention_until > now()`). 10-дневное retention (до 2026-09-25T12:00Z) **истекло сегодня в 12:00Z**: тесты, жившие одним 10d-бэкапом, получают `RestoreDrillError: no backup point inside the retention window`; `test_restore_drill_random_retained_point_and_verification` — `assert len(set(seen)) >= 2` (выбор «случайный» из одной оставшейся точки). Доказательство не-связи с задачей: suite был зелёным (964+8) на проверке T7.44b сегодня в ~10:39Z (до 12:00Z), падение воспроизводится одиночными после 12:00Z, diff задачи — только docs. Направление: `NOW` относительно реального времени (или инъекция `now` в drill-запрос); тесты трогать в этой задаче запрещено (анализ).
- **1 × `tests/scenario/test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease`** — задокументированный флок T7.43 (PendingRollbackError на параллельном xdist; зелёный в одиночном повторе, как и в T7.43).

Итог проверки: **регрессии по изменениям задачи нет** (docs-only + зелёные ruff/mypy + одиночные повторы; 6 падений — чужие, описаны выше).

### T7.46 — починка двух дефектов SMOKE-V12-K2 (a: NUL-байт роняет commit сессии; b: time bomb в restore-drill) — ADR-0020, ADR-0021

**T7.46a — NUL-байт из вывода инструмента больше не роняет commit сессии** (коммит `39cf85c`; SMOKE-V12-K2-report.md §3, направление §3.4; **ADR-0020**).

Дефект: `\x00` в stdout `python.execute` выживал `decode("utf-8","replace")` (`executor.py:148`) → evidence-payload `stdout[:2000]` (`evidence.py:104`) → report-payload (`orchestrator.py:794`) → JSONB: asyncpg кодирует NUL как `\u0000`, серверный парсер JSONB отклоняет (`UntranslatableCharacterError`) → откатывалась **вся** phase-1-транзакция (все шаги сессии).

Решение (три границы §3.4, новое чистое ядро `packages/domain/sanitization.py`: `mask_nul`/`mask_nul_deep`):
1. **маркер = 4 ASCII-символа `\x00`, НЕ U+FFFD** (U+FFFD уже есть как продукт `decode("utf-8","replace")` для невалидных байтов — смешать «был NUL» с «был невалидный UTF-8» нельзя). Маркер чистый ASCII и не содержит NUL → маскирование идемпотентно. NUL **не исчезает молча** — модель и аудит видят маркер.
2. **ИСТОЧНИК (захват вывода)** — `executor.py` `_python_execute` stdout/stderr + `_workspace_read` content (маска ПЕРЕД капом); M2-путь — `broker.py` `_sandbox_exec` stdout/stderr + `workspace.read` content (тот же принцип для sandboxed-исполнителя). Одна точка на захват → инвариант «в наблюдениях хоста нет \x00».
3. **EVIDENCE** (`observation_to_evidence`) — входные строки маскируются **ДО капов** (mask-then-slice): `identity_hash` и payload — от ОДНОГО замаскированного значения (сохранённое значение и вход хэша не расстроятся; durable identity доверенный хост пересчитывает из payload на commit-границе — тот же вход). Итоговый payload — глубокая маска `_finalize` (защита от будущих полей).
4. **AUDIT** (`AuditService.record`) — рекурсивная маска `payload` + `public_summary` (TEXT тоже не хранит NUL) до кода записи — **последняя линия**, исключает амплификацию от любого будущего источника NUL.

Диапазон: маскируется только `\x00` (прочие C0-байты легальны в JSONB, тихое удаление изменило бы наблюдаемое — не сделано). Контракты не меняются: для ЧИСТЫХ выводов identity побайтово как до фикса (тест `test_python_execute_evidence_clean_output_identity_unchanged`).

Тесты (красный→зелёный, красный = дословное воспроизведение `UntranslatableCharacterError`): `tests/unit/test_sanitization.py` (4, новый), `tests/unit/test_stub_executor.py` (+2), `tests/unit/test_evidence_nul.py` (3, новый), `tests/scenario/test_audit_sanitization.py` (1, новый: NUL в top-level/nested dict/list + `public_summary` → JSONB/TEXT с маркером, audit + outbox-близнец), `tests/scenario/test_session_nul_commit.py` (1, новый: **целая сессия** — `python.execute` печатает NUL на позиции 1200 (за капом `_cap_args`, в капе evidence — точная позиция SMOKE-V12-K2); ДО — сессия падала на report-событии (весь commit откатился), ПОСЛЕ — КОММИТИТСЯ: state succeeded, report-событие записано, evidence несёт маркер, durable identity == пересчёту доверенного хоста из значения в БД + sha256 артефакта).

**T7.46b — time bomb в restore-drill: drill работает на инъекционном часе, не на `now()` БД** (ADR-0021; обнаружено T7.45b п.6).

Вывод по шагу 1 (bug в коде vs в тесте): **bug в коде drill'а**. `run_restore_drill` принимает `now`, но использовал его только для `verified_at` (`restore.py:336`), а выбор точки сравнивал host-заштампованную `retention_until` с реальным `now()` БД (`restore.py:84`) — **одна операция, два часовых пояса**. Аргументы: (1) конвенция модуля — инъекционный host-час (`create_backup` принимает `now` и штампует `retention_until` из него, `service.py:164,230`; `run_restore_drill` принимает тот же `now` для `verified_at`) — drill-запрос с `now()` единственный расходчик, игнорирующий инъекцию; (2) `retention_until` — host-заштампованное значение, истекает должно от того же host-часа (сравнивать host-значение с часом хранилища = drift на desync-хосте); (3) production-семантика сохранена — CLI `restore-drill` не передаёт `now` → после фикса `datetime.now(UTC)` (host-час), как и до; (4) `now()` БД оправдана там, где часы не инжектятся (GC-сweep `gc/service.py:338` — реальная операционная очистка, не тронут).

Изменения: `restore.py` — `_pick_retained_backup(db, rng, now)` → `retention_until > :drill_now` (бинд, non-nullable datetime — ловушка §7 о nullable-биндах не затронута); `run_restore_drill` — `moment = now or datetime.now(UTC)` на всю операцию (выбор + `verified_at` + аудит). Тесты `test_backup_pitr.py`: фиксированный `NOW = 2026-09-15T12:00Z` — опорная дата (НЕ реальное время); drill-тесты — фикстура `drill_now` с параметрами **+0/+1/+5 лет** (сдвиг через параметр, не wall-clock) — доказательство не-зависимости от даты запуска (retention-окна, прибитые к сдвинутому now, открыты на ЛЮБОЙ дате запуска); expired-window семантика тестируема (`test_restore_drill_no_retained_backup` — бэкап вне окна ЧАСА DRILL'а → `RestoreDrillError`); новый страж `test_restore_drill_selects_on_injected_clock_not_wall_clock` — бэкап, чьё окно ЗАКРЫТО на wall-clock'е (2026-09-25 12:00Z) но ОТКРЫТО на `now=NOW`, обязан выбираться (при возврате к `now()` БД — падение на каждой дате после 2026-09-25).

Шаг 3 (иные хардкод-даты в тестах): grep `2026-`/`datetime(20` — 131 match в 14 файлах. **Опасного паттерна** (host-заштампованное значение сравнивается с реальным `now()`/`now()` БД) **больше нигде нет** — везде либо инъекция `now=` в функцию, либо wall-clock-относительные штампы `now() ± interval` (self-consistent). Перечень «ближних к реальному времени» (2026-09-xx), **безопасные** (инъекция/чистые функции, не-зависимы от даты запуска): `test_retrieval.py` (2026-09-22 ×2, `retrieve(now=…)`), `test_freshness.py` (NOW 2026-09-22, чистая `freshness_status`), `test_wake_schedule.py` (T0 2026-09-14, чистая), `test_wake_scheduler.py` (NOW 2026-09-14, `decide(now=…)`), `test_evaluation_gates.py` (2026-09-22 ×5, `compute_gates(now=…)`), `test_rules_engine.py` (2026-09-xx, `reverify_after(…, now=…)`), `test_scope.py` (2026-09-xx, `derive_claim_as_of(…)`), `test_staging_schema.py` (2026-09-14, фикстурное поле), `test_as_of_commit.py` (2026-09-xx, данные + `date_trunc(now())` self-consistent). Тронут только `test_backup_pitr.py` (тот же паттерн, тот же файл).

Проверка §6 (перед каждым коммитом):
- **T7.46a** (`39cf85c`): ruff + mypy strict — зелёные; pytest -n auto (NOEZEMA_TEST_DATABASE_URL) — **970 passed, 8 skipped, 5 failed** — 5 = ТОЛЬКО задокументированная time bomb T7.46b (каждый день с 2026-09-25 12:00Z, фикс — следующий коммит, заказанный порядок T7.46a→T7.46b), других падений нет, флок `test_slow_llm` не проявился.
- **T7.46b** (этот коммит): ruff + mypy strict — зелёные; pytest -n auto — **987 passed, 8 skipped, 1 failed** — 1 = `test_slow_llm_does_not_lose_commit_lease` (задокументированный флок T7.43, PendingRollbackError на параллельном xdist; **одиночный повтор — 1 passed**, как и в T7.43). Все 5 time-bomb тестов — зелёные (дата запуска 2026-09-25, wall-clock уже перечёркнул 10d-окна фиксированного `NOW` — бомба реально взорвалась до фикса, после — зелёная).

Не меняется: схемы (миграций нет), `rules_hash`, замороженные payload'ы config-v2…v11 и промпты (хэши не тронуты), пины, пороги, корпуса, `ARCHITECTURE.md`, curator-v7/config-v11, `create_backup`/GC-сweep/CLI `restore-drill` (интерфейс и production-час не тронуты), данные прошлых прогонов (SELECT only). Открытые вопросы (ADR-0020, отчёт): NUL в ТЕКСТЕ МОДЕЛИ (claim statement → `session_staging` JSONB) вне трёх границ T7.46a — кандидат на отдельное усиление → **закрыто T7.47a**; прочие C0-байты не маскируются → **закрыто фактом T7.47a**.

### T7.47a — NUL в тексте МОДЕЛИ (четвёртый канал, вне трёх границ T7.46a): граница на входе ответа + защитная линия staging — ADR-0020 (дополнение T7.47a)

**Карта путей модельного текста → БД** (полная таблица — в дополнении ADR-0020). Модельный текст входит в хост в ЕДИНСТВЕННОЙ точке — `LLMMiddleware.chat` (grep: других парсингов ответа модели нет; `model_runs` сырой ответ в БД не пишет — `raw_response_artifact_id` ни разу не заполняется, `actions` хранит только `arguments_hash`, `checkpoints`/`commit_attempts` — UUID/хэши, `sources` — final URL хоста, `workspace_entries.path` — реальные файлы). До T7.47a НЕ ПОКРЫТЫ:

1. `session_staging.payload` (JSONB) — claim statement/scope/search_statements/existing_claim_id, question text/origin/rationale, evidence note — **ФАТАЛЬНО**: откат всей phase-1-транзакции (режим SMOKE-V12-K2, теперь достижимый из текста модели);
2. `sessions.plan` (JSONB) — LLM-план (planning=llm) — фатально;
3. `sessions.verification` (JSONB) — отчёт верификатора — фатально;
4. `sessions.extraction` (JSONB) — chunks извлечения (note; quote — вербатим из ЗАМАСКИРОВАННОГО хостом док., NUL в quote невозможен) — фатально;
5. `sessions.termination_reason` (TEXT) — complete reason — **тоже фатально** (факт ниже: TEXT отклоняет NUL; пишет финальная fenced-транзакция → роняет весь commit).

Покрыто T7.46a: audit/outbox (граница 3). Производные от staging (покрываются маской staging): `claims.*`, `questions.text`, evidence-строки на commit.

**Решение** (тот же принцип ADR-0020, тот же `mask_nul_deep`/маркер `\x00`, новых механизмов нет): **граница 4 — вход модельного текста** (`packages/llm_gateway/client.py`, маска СРАЗУ ПОСЛЕ `json.loads(content)`, ПЕРЕД `model_validate` — до схем/капов/хэшей: сохранённое значение и вход любого хэша — одно замаскированное значение; все 5 ролей проходят через эту точку) + **граница 5 — staging** (`packages/domain/services/staging.py`, `record()`: маска payload ДО `payload_hash` — mask-then-hash; идемпотентна к границе 4, страхует будущие не-gateway-источники; `commit_attempts.staging_hash` — производный от сохранённого payload_hash → согласован).

**Факт по C0-байтам (замер на реальной БД, UTF8, путь приложения SQLAlchemy+asyncpg; тест `test_postgres_rejects_only_nul_among_c0_bytes`):** среди C0 (0x00–0x1F) Postgres отклоняет **только NUL** — JSONB: `UntranslatableCharacterError` (на `\u0000`), TEXT и VARCHAR: `CharacterNotInRepertoireError` (invalid byte sequence for encoding "UTF8": 0x00); остальные 0x01–0x1F сохраняются во всех трёх типах. Диапазон маскирования НЕ расширяется (\x00-only). **Опровергнуто** побочное наблюдение SMOKE-V12-K2 §3.1 «NUL в бинарном text-параметре Postgres принимает» — на пути приложения TEXT NUL отклоняет (сам ADR-0020 уже учитывал: «TEXT-столбец тоже не хранит NUL»).

**Семантика повреждённого reason-токена:** `decision.reason` — токен словаря хоста, не свободный текст; NUL-повреждённый «goal_reached\x00» после маски не равен `GOAL_REACHED` → `SUCCEEDED_PARTIAL` (тот же статус, что любой неизвестный/повреждённый reason; работа сохраняется, исход объясним по сохранённому маркеру; хост не гадает намерение). До T7.47a путь был фатальным (NUL в `termination_reason` ронял финальную fenced-транзакцию).

**Идентичности/дедуп/правила не сломаны** (требование задачи): маска — тождество на чистом входе (тест `test_clean_response_passes_byte_identical`; существующие identity/дедуп-тесты зелёны без изменений: test_evidence_nul, test_session_nul_commit, test_memory_service, test_orchestrator::test_memory_search_and_claim_reuse — 64 теста прогнаны); дедуп T7.9 по ЗАМАСКИРОВАННОМУ statement — вторая сессия с тем же NUL-высказыванием дедупится, claim не дублируется (тест ниже).

**Тесты** (красный→зелёный; красный до фикса — `UntranslatableCharacterError`/`CharacterNotInRepertoireError`, откат транзакции сессии): `tests/unit/test_gateway_nul.py` (3, новый: NUL в claim statement/scope + в complete reason/rationale маскируется на входе; чистый ответ — побайтово), `tests/scenario/test_model_text_nul.py` (7, новый: NUL в claim statement — КОММИТ + дедуп + `payload_hash == canonical_sha256(сохранённый payload)`; NUL в question text — question с маркером; NUL в complete reason — PARTIAL + маркер в `termination_reason` + claim выжил; NUL в LLM-плане / отчёте верификатора / chunks извлечения — JSONB с маркером + сохранённый sha от замаскированного док.; C0-провер).

**Проверка** (AGENTS.md §6): ruff + mypy strict — зелёные; pytest -n auto — см. итог T7.47 (оба коммита).

Не меняется: схемы БД (без миграций), `rules_hash`, замороженные payload'ы config-v2…v11 и промпты (хэши не тронуты), пины, пороги, корпуса, `ARCHITECTURE.md`, curator-v7/config-v11, данные прошлых прогонов (SELECT only). Фоновых процессов нет.

### T7.47b — flake `test_slow_llm_does_not_lose_commit_lease` под xdist: НЕ голодание — реальная гонка в `LeaseHeartbeatGuard` (cancel посреди execute отравляет общую сессию)

**Гипотеза и замеры (before).** Гипотеза задачи: CPU-контенция под xdist (фейк-LLM 2.5 c vs TTL 1 c) → голодание event-loop → истечение лиза. Замеры: (a) 20 одиночных прогонов теста под нагрузкой 6 CPU-процессов (loadavg ≈ 6, 100 % от 6 CPU): **0/20 падений** — одиночный профиль flake НЕ воспроизводит; (b) 3 полных прогона `-n auto`: **3/3 зелёные** (998 passed, 8 skipped) — flake в этой сессии не воспроизведён (история: T7.43 1/3, T7.45 1/3, T7.46 1/1 параллельных прогонов — вероятностное окно ~1 из 3–4, в 3 прогонах он мог не выпасть).

**Корневая причина (эксперимент, детерминированно).** Отмена задачи ПОСЕРЕДИНЕ её `session.execute` на ОБЩЕЙ `AsyncSession` отравляет сессию: **100 % воспроизведение** `PendingRollbackError: Can't reconnect until invalid transaction is rolled back` при следующем обращении. Механизм в продукте: `LeaseHeartbeatGuard.__aexit__` безоговорочно канцелит guard-задачу, а renewal выполняется прямо на общей сессии/транзакции вызывающего (осознанно — отдельное соединение блокировалось бы на row-lock вызывающего). Если cancel попадает в `db.execute` heartbeat (окно ~мс; под CPU-контенцией окно шире — медленнее и цикл, и execute), in-flight execute рвётся, сессия остаётся в состоянии «нужен rollback», который guard сделать НЕ МОЖЕТ (транзакция phase-1 вызывающего в полёте); следующие heartbeat'ы loop глотает (`except Exception: continue`). Основной поток натыкается сразу после LLM-вызова → phase-1-транзакция умирает. Чистое голодание дало бы ДРУГУЮ симптоматичную картину (LeaseLost → падение ассерта), а не PendingRollbackError — контенция лишь УШИРЯЕТ окно; класс ошибки flake совпадает с экспериментом точь-в-точь. Вывод: реальная кодовая гонка renewal/cancel → фикс продуктового кода.

**Фикс** (`packages/domain/services/lease.py`, `LeaseHeartbeatGuard`): renewal выполняется в CHILD-задаче (`_heartbeat_once`), loop ждёт её через `asyncio.shield`; `__aexit__` канцелит только loop-задачу (она может лишь спать или ждать shield — cancel в sleep чистый) и ДРЕЙНИРУЕТ в-flight renewal до завершения, прежде чем вернуть управление. Семантика без изменений: тот же интервал (ttl/3), тот же `LeaseLost` на выходе, fenced commit остаётся финальным гейтом; на выходе в-flight renewal может дожить (продление лиза на ~мс позже — ровно то, что сделал бы следующий по расписанию renewal). Ассерты flake-теста и lease-lost control-теста (`test_guard_raises_when_renewal_refused`) — НЕ тронуты.

**Красный→зелёный (детерминированный регрессионный тест).** `tests/unit/test_lease.py::test_guard_exit_does_not_poison_shared_session` (новый, 1): heartbeat патчится — после сигнала выполняет РЕАЛЬНОЕ медленное statement (`pg_sleep(0.5)`) и только затем renewal; guard закрывается ~0.1 c после сигнала — cancel детерминированно попадает в середину реального execute (запас 0.4 c). До фикса: **FAILED — `PendingRollbackError`** (симптоматика flake точь-в-точь); после: **PASSED** (общая сессия остаётся usable, лиз продлён).

**Замеры (after).** (a) 20 одиночных прогонов под нагрузкой 6 CPU-процессов: **0/20**; (b) 3 полных прогона `-n auto`: **3/3 зелёные** (999 passed, 8 skipped — включая новый тест). Фоновые процессы (CPU-burn) после замеров убиты и проверены (pgrep пусто).

**Before/after** (падения lease-теста; полный набор тестов зелёный в обоих):

| профиль | before | after |
|---|---|---|
| 20 одиночных прогонов под нагрузкой (6 burn) | 0/20 | 0/20 |
| 3 полных прогона `-n auto` | 0/3 (998/8) | 0/3 (999/8) |
| детерминированный регрессионный тест | FAILED (PendingRollbackError) | PASSED |

В этой сессии flake не воспроизвёлся в before-замерах (вероятностный; история ~1 из 3–4 параллельных прогонов) — эффект фикса доказан красным→зелёным тестом и УСТРАНЕНИЕМ механизма (cancel больше не может попасть в execute: renewal в child-задаче, drain на выходе).

Не меняется: схемы БД (без миграций), `rules_hash`, замороженные payload'ы config-v2…v11 и промпты (хэши не тронуты), пины, пороги, корпуса, `ARCHITECTURE.md`, curator-v7/config-v11, данные прошлых прогонов (SELECT only), ассерты flake-теста и lease-lost control-теста. Фоновых процессов нет.

### T7.48 — SMOKE-V13-K2: чистое повторение после починки NUL/lease (запуск T7.48a, анализ T7.48b)

**T7.48a (запуск, `~/dsh1/task43-smoke-v13-launch.log`).** Чистое повторение SMOKE-V12-K2: единственная переменная — код (`239db71` → `4d5549c`: T7.46a/b NUL в выводе инструментов + time bomb restore-drill, T7.47a NUL в тексте модели, T7.47b гонка LeaseHeartbeatGuard; поведение промптов не менялось). Модель K2 + профиль llamacpp-rocmfpx (env не тронут, T7.45a), payload config-v11 (curator-v7 `19d6c6e8…` + explorer-v4 `5829a55c…`, canonical `a407ce83…`), корпус `b3e05ad5…` (7 вопросов, побайтово тот же). Preflight зелёный (10 юнитов прошлых смоуков inactive; searxng ok; миграции до 0025). Run `d0b66d37-1aa9-4bd6-9aa1-466ca8600e71` (snapshot `64b3d4b0…`, seed 20260924, slo 3600, blind-size 10), 2026-09-26 07:08:54Z → 07:26:18Z (17,4 мин), EXIT=0. Воркер `smoke-v13-k2-worker` (reassessment-tick --batch-size 50 --lease-seconds 120 + reconcile-tick, цикл 15 s) запущен.

**T7.48b (анализ, `docs/eval/SMOKE-V13-K2-report.md`).** Воркер остановлен: до остановки SELECT (7/7 терминальных — 2 succeeded + 5 succeeded_partial, commit_attempts 7/7 committed, 0 нетерминальных), stop → `inactive` (transient-unload rc=4); все 12 юнитов `smoke-v8/v10/v10b-k2-*`, `smoke-v11-halogen-*`, `smoke-v12-k2-*`, `smoke-v13-k2-*` — inactive; процессов hostctl/worker.sh нет.

Результат (подробности в отчёте):

- **7/7 сессий, потерь нет** (в v12: 6/7 + 1 потеряна на NUL): 2 succeeded (Python-якорь `1a7dc49c` + Python-FU `5c4183e6`, оба `goal_reached` точным равенством) + 5 succeeded_partial; Go-пара **выполнена** (`0d0c1b3f`, 7 шагов, 353 с; в v12 не выполнялась). 5 claim'ов (1 на тему; 0 дублей, 0 E1 — в v12: 6), 10 evidence, 7 assessments, 8 вопросов (7 корпуса + 1 модельный).
- **Гейт 5 = 2/5** (ci95 [0.1176, 0.7693], insufficient_sample — ожидаемо при N=7): пересчёт кодом репозитория `_gate_reuse` (SELECT only) — stored == recomputed, **MATCH True**. Знаменатель 5 = все 5 значимых claim'ов (E2+); числитель 2 — Python `64e6a300…` (ветка evidence: якорь + FU-identity; ветка claim_assessments: якорь + строка-перепроверка) и plan `6c3aa2c6…` (только ветка claim_assessments — evidence FU схлопнулись дедупом). **Обе пары — настоящая перепроверка**: `existing_claim_id` = полный UUID (не префикс), `claim_reverified` reference==resolved==UUID, строки `claim_assessments` с чужой сессией — не dedup. Правило 7 (curator-v7) сработало на K2 второй раз подряд (v12: 2/2, v13: 2/2; v10b v6: 1/2 — дубль).
- **Живая проверка фиксов: маркеров `\x00` 0 и NUL 0 во всех каналах** (audit/staging/sessions/артефакты на диске/workspace) — при этом **в прогоне не было ни одного `python.execute`** (единственный бинарный канал; источники NUL v12) и NUL модель не выдавала. Честный вывод: **прогон НЕ доказывает фиксы «в бою» — ситуация не воспроизводилась**; подтверждено отсутствие регрессии (7/7, 7/7 committed) + валидность пайплайна на `4d5549c`. `UntranslatableCharacterError`/`CharacterNotInRepertoireError` 0; `LeaseLost`/`PendingRollbackError`/lease-ошибки 0 (174 блока тиков + 7 fenced commit'ов); worker.log — 174 traceback-блока «Event loop is closed» (known teardown-дефект `hostctl/cli.py`, не NUL/lease).
- **Статусы 2+5**: 5/5 partial — из-за `complete_reason` (4 free-form, класс T7.37b + 1 «`goal_reached — <текст>`», класс v8-S2/v11-S7; `budget_exhausted` 0 против 1 в v12). T7.47a маскирует только NUL (чистый текст побайтово) → состав классов — вариативность модели, не влияние фиксов.
- Числа: 38 model_runs (curator-v7 7 + explorer-v4 31), **38/38 с пинами config-v11**, 0 расхождений/NULL; 0 ошибок схем (оба паттерна T7.36); 5/5 дат/сроков согласованы (due_stale 0/2; 2 fresh-срока = commit +30 д); типы: temporal_fact ×4 + local_observation ×1; max output 3646 ≤ 8192; finish_reason stop 38/38; blind 5/5 + 5/5 (структурно); deny 3 (новый класс: workspace.list «path '/' outside profile read roots» + 2 × artifact.create «unknown tool»); 2 × fetch 404.
- **Итог серии T7.38–T7.47: закрыта на данном уровне** — (1) эффект промпта curator-v7 (правки (а)/(б)) подтверждён в v12 и воспроизведён в v13 (2/5 через робастный путь в двух независимых выборках K2); (2) NUL/lease-дефекты закрыты фиксами + чистым повторением 7/7. **Рекомендация: повторный прогон НЕ нужен** (гейт стабилен, полнота подтверждена, фиксы покрыты тестами; ещё один прогон на тех же условиях — только новая выборка temperature). Дальше — другая работа: host-нормализация `complete_reason` (T7.37b — главная доля partial 5/7) и teardown «Event loop is closed» в `hostctl/cli.py`; «в бою» проверку NUL-фикса ждать в естественном потоке (следующий реальный прогон с `python.execute`).
- Не тронуто: код, тесты, payload'ы (v2…v11), промпты, корпуса, `ARCHITECTURE.md`; БД (все смоук- и eval-БД) — только SELECT; прогоны не запускались; LLM на 192.168.1.48 не опрашивался; фоновых процессов нет.

### T7.49 — host-нормализация `complete_reason` (решение по рекомендации T7.37b/SMOKE-V13-K2-report §8; ADR-0022)

Рекуррентный дефект с T7.37b (каждый K2-смоук): модель завершает сессию `{"kind":"complete","reason":"…"}`, а хост до T7.49 выводил статус ТОЧНЫМ равенством `ctx.complete_reason == "goal_reached"` (`orchestrator.py`) → «goal_reached — <текст>» (и любой свободный текст) → `succeeded_partial` + `PARTIALLY_ANSWERED` при выполненной работе.

**Шаг 1 — измерение (SELECT only, до изменений).** Все терминальные сессии 6 БД прошлых смоуков — `noezema-smoke-v8-k2`, `-v10-k2`, `-v10b-k2`, `-v11-halogen`, `-v12-k2`, `-v13-k2` — 36 строк (v10-k2 — 2 строки прогона; v12-k2 — 6 строк + 1 сессия, потерянная на NUL-дефект до T7.46a). Классификация `sessions.termination_reason` (сырой text `reason` модели; audit `payload->>'complete_reason'` совпадает с колонкой во всех 3 сессиях класса B):

| DB | A точный токен | B токен + разделитель + суффикс | C токен внутри/в конце | D free-form | E NULL/unknown |
|---|---|---|---|---|---|
| v8-k2 | 3 (2 goal_reached succeeded, 1 budget_exhausted partial) | 1 (`c79140b5` «goal_reached: …», partial) | 0 | 3 (partials) | 0 |
| v10-k2 | 0 | 0 | 0 | 2 (partials) | 0 |
| v10b-k2 | 4 (4 goal_reached succeeded) | 0 | 0 | 3 (partials) | 0 |
| v11-halogen | 6 (6 goal_reached succeeded) | 1 (`1bcf6776` «goal_reached: …», partial) | 0 | 0 | 0 |
| v12-k2 | 3 (2 goal_reached succeeded, 1 budget_exhausted partial) | 0 | 0 | 3 (partials) | 0 |
| v13-k2 | 2 (2 goal_reached succeeded) | 1 (`0d0c1b3f` «goal_reached — утверждение: …», partial) | 0 | 4 (partials) | 0 |
| **Итого** | **18** | **3** | **0** | **15** | **0** |

Partial всего 20 (класс B — 3; класс D — 15; 2 `budget_exhausted` в A — легитимный partial, нормализацией не лечатся). Нормализация host'ом лечит ровно 3/20 (15%) — класс B, все `goal_reached`-токен → стали бы succeeded/VERIFIED. **Класс D (15/20, 75%) — большинство: host-нормализацией не лечится** (правило безопасности, ниже), лечится другим слоем — варианты описаны в ADR-0022 «Варианты для класса D» (A: промпт explorer-v5 — точный токен, пояснение в `public_rationale`; B: enum-ограничение схемы — меняет контракт модели; НЕ реализовано).

**Шаг 2 — `ARCHITECTURE.md`.** Спека НЕ требует точного равенства и не описывает вывод статуса по free text: §7 «Нормальное завершение» (строка 1042) — канонический пример `"reason": "goal_reached"` (пример, не семантика сравнения); §6.6–§6.7 (строки 991, 1013) — `succeeded_partial` для soft exhaustion и operator stop; строка 564/897 — терминальные состояния; §14 (строка 1852) — колонка `sessions.termination_reason` без правил сравнения. Стопа не потребовалось — реализовано.

**Изменения (1 функция + 3 точки оркестратора, схема модели не тронута):**

- `packages/domain/schemas/decision.py`: новая чистая `normalize_complete_reason(reason: str | None) -> CompleteReason | None` (единственный источник правил; ADR-0022): trim, регистр не важен, обрамляющие кавычки/бэктики/точка; токен **в начале** строки, заканчивается на границе слова (конец строки или разделитель: пробел, «—», «–», «-», «:», «;», «,», «.», «(»); `goal_reachedX`/«not goal_reached»/токен не в начале → None; первый токен побеждает; не-строка/пусто → None. `Decision.normalized_reason` — делегирование (было: точное `CompleteReason(reason)`).
- `apps/orchestrator/orchestrator.py`: (а) вывод статуса — `succeeded` ТОЛЬКО при `normalize_complete_reason(ctx.complete_reason) is GOAL_REACHED` и без unknown actions (T2.21 не тронуто); нераспознанная причина → `succeeded_partial` (прежнее поведение); (б) `CommitPlan.termination_reason` — канонический токен для распознанных, сырая строка для нераспознанных (как раньше), failed — `unknown_action_outcome` (как раньше); (в) audit complete-события — payload несёт ОБА поля: `complete_reason` (сырой, как раньше) + `normalized_reason` (токен или null) — сырой текст модели нигде не теряется. Правило безопасности (ADR-0022): хост НЕ выводит успех из free text по смыслу/ключевым словам — ложный succeeded хуже ложного partial; словари русских фраз / эвристики / LLM — нет.
- Не меняются: JSON-schema `ModelResponse`/`Decision` для LLM (tool/schema hash), промпты и пины, payload'ы config-v2…v11, схемы БД, `rules_hash`, пороги, корпуса.

**Тесты (красный→зелёный + стражи):**

- `tests/unit/test_complete_reason_normalization.py` (новый): 20 позитивных (все обязательные: «goal_reached», «GOAL_REACHED», « goal_reached », «`goal_reached`», «goal_reached — утверждение: …», «goal_reached: …», «goal_reached. Вопрос отвечен», «goal_reached (двумя источниками)», «budget_exhausted», «no_progress — …», «blocked: нет доступа» + кавычки/регистр/точка) + 13 негативных (все 8 обязательных: «goal_reachedX», «not goal_reached», «Вопрос отвечен и подтверждён (goal_reached)», «Вопрос отвечен и подтверждён двумя источниками», «», None, «goal reached», «goal_reached_partially» + free-form SMOKE + «   »/«goal_reached!») + приоритет первого токена («blocked — goal_reached не достигнут» → BLOCKED) + не-строки.
- `tests/scenario/test_complete_reason_normalization.py` (новый, postgres + fake LLM, 3 сценария): (а) «goal_reached — <текст>» → `succeeded`, вопрос `VERIFIED`, `sessions.termination_reason = "goal_reached"` (канонический токен), audit complete-событие несёт `complete_reason` (сырой) + `normalized_reason` ("goal_reached"); (б) free-form без токена → `succeeded_partial` + `PARTIALLY_ANSWERED` + сырая `termination_reason` + `normalized_reason = null` (прежнее поведение, страж); (в) unknown action + «goal_reached — …» → `failed` + `unknown_action_outcome` (T2.21, страж). Красный→зелёный: (а) красный на старом оркестраторе (дословно дефект T7.37b: `succeeded_partial` вместо `succeeded`), (б) ассерт `normalized_reason` красный до (поля нет) — статусный ассерт зелёный и до, и после, (в) зелёный и до, и после.
- `tests/unit/test_decision_envelope.py`: +тест делегирования `Decision.normalized_reason` (существующие ассерты точного равенства/неизвестного токена не ослаблены и не обновлялись). Существующие сценарные тесты (`test_orchestrator.py` — вкл. `test_budget_exhausted_partial`, `test_failpoints.py`, `test_evaluation_gates.py`; 39 тестов) — без изменений, зелёные.
- Матрица §22.1: строка 13 (partial success на safe boundary) дополнена ссылками на T7.49-тесты.

Не тронуто: `ARCHITECTURE.md`, промпты/пины, payload'ы/хэши, схемы БД, пороги, корпуса, данные прошлых прогонов (SELECT only); eval-run/smoke не запускались; LLM на 192.168.1.48 не опрашивался; фоновых процессов нет.

### T7.50 — explorer-v5 + config-v12: класс D (free-form `complete_reason`) лечится у источника — промптом (решение пользователя 2026-09-26: вариант A ADR-0022)

Основание (ADR-0022 «Варианты для класса D», замер T7.49): в 15 из 20 partial-сессий смоуков V8–V13 explorer писал в `decision.reason` свободный текст («Вопрос отвечен и подтверждён двумя источниками…») вместо токена завершения; host-нормализация T7.49 лечит только 3/20 (класс B — токен в начале + суффикс), хост НЕ имеет права выводить успех из свободного текста (правило безопасности ADR-0022) → лечить у источника — промпт.

1. **`prompts/explorer/explorer-v5.md`** — НОВЫЙ файл (explorer-v4 не тронут: на него пинится config-v9…v11 и история), заголовок `version: explorer-v5`. Diff v4→v5 = строка версии + ТОЧЕЧНЫЕ правки только про завершение, остальное побайтово:
   - (а) «Протокол ответа»: для `{"kind": "complete", "reason": ...}` — `reason` — РОВНО ОДИН токен из `goal_reached | budget_exhausted | no_progress | blocked`, в нижнем регистре, БЕЗ пояснений, кавычек, точек и любого другого текста; всё пояснение (что установлено, на каких источниках, почему прекращаешь) — в `public_rationale` этого же ответа;
   - (б) правило 5 — сопоставление условий с токеном: вопрос отвечен и подтверждён → `goal_reached`; бюджет исчерпан → `budget_exhausted`; прогресса нет два хода подряд → `no_progress`; продолжать нельзя из-за внешней блокировки → `blocked`; + токен отражает фактическое состояние — `goal_reached` только если ответ подтверждён результатами инструментов (правило 3 остаётся в силе), не выбирать ради «успеха»;
   - (в) новый раздел «Примеры завершения» — один короткий ПРАВИЛЬНЫЙ JSON-пример (токен в `reason`, пояснение в `public_rationale`) и один НЕПРАВИЛЬНЫЙ (free-text в `reason`, пометка «так делать нельзя» + пояснение — в `public_rationale`).
2. **Безопасность примеров (паттерн T7.39/T7.43):** тема примера «столица Новой Зеландии — Веллингтон» — вне всех корпусов (v1–v4 + смоук: grep «Зеланд»/«Веллингтон» = 0 совпадений), без UUID и реальных данных из docs/; оба JSON-примера валидны (проверено парсингом и `ModelResponse.model_validate`); единый источник истины: в промпте нет цифр и правил, которых нет в коде — список токенов — сам enum `CompleteReason` минус `operator_stop` (токен хоста, его не ставит модель — закреплён стражем), формулировка пометки не описывает нормализацию хоста.
3. **`docs/eval/config-v12-payload.json`** = config-v11 с ЕДИНСТВЕННЫМ изменением `prompts.explorer` → explorer-v5 (path + sha256 + version); формат v11 воспроизведён побайтово (indent=2, sorted keys, trailing newline), v11 не переписан; все пины разрешаются `resolve_prompts`, `TokenBudgets.validate()==[]`. Хэши (пересчитаны самим, не взяты из отчётов/STATUS): explorer-v5 sha256 `3b1fd49d…`; config-v12 file `493970d8…` / canonical `c23005bd…`; сверка: canonical config-v11 `a407ce83…` — не изменился.
4. **BOOTSTRAP_PAYLOAD (`packages/domain/config.py`)** — пин explorer explorer-v4 → explorer-v5 (паттерн T7.14/T7.35/T7.38/T7.39/T7.43: пин следует за текущим промптом; hash из кода, fail-closed); `BOOTSTRAP_SNAPSHOT_ID` не меняется; комментарий в коде — история пина.

**Тесты:**

- `tests/unit/test_explorer_prompt_completion.py` (новый): (а) prompt↔код — набор токенов `CompleteReason`, присутствующих в промпте, ровно = enum минус `operator_stop` (тот ставит хост при operator stop — `apps/orchestrator/orchestrator.py`, не модель — зафиксировано в тесте); (б) оба JSON-примера — валидные `ModelResponse`-окутки (парсинг + `model_validate`); (в) связь с T7.49 — `reason` правильного примера проходит `normalize_complete_reason` как `GOAL_REACHED`, неправильного (free-text) — как `None`.
- `tests/unit/test_freeze_payloads.py` — новый `test_config_v12_pins_explorer_v5_and_differs_from_v11_only_there` (config-v12 отличается от v11 ТОЛЬКО пином explorer; пины разрешаются; бюджеты валидны). Тесты config-v8…v11, которые справедливо пинят explorer-v4, — не тронуты.
- `tests/unit/test_prompt_example_no_real_data.py` (страж примеров T7.39/T7.43 распространён на explorer): (а) `test_prompt_uuids_do_not_intersect_docs` теперь покрывает ВСЕ промпты (curator + explorer); (б) новый `test_explorer_example_topics_not_in_any_corpus` — free-text поля примеров explorer (`public_rationale`, `expected_information`, free-text `decision.reason`) и закреплённая тема («столица Новой Зеландии — Веллингтон») не встречаются ни в одном корпусе (v1–v4 + смоук).
- `tests/scenario/test_prompt_pinning.py` — ассерт ТЕКУЩЕГО пина `explorer-v4` → `explorer-v5` (bootstrap) с комментарием паттерна.

**Документы:** ADR-0022 — короткое дополнение «Дополнение (T7.50)» (вариант A реализован: explorer-v5/config-v12 + пин; проверка — отдельный смоук; вариант B не реализован); `SMOKE-V13-K2-report.md` §8 — пометки-ссылки «сделано в T7.49/T7.50» в двух местах (открытый пункт 2 и рекомендация (1)) — исторический текст не переписан; матрица §22.1 строка 13 дополнена ссылками на T7.50-тесты.

Не тронуто: код поведения (кроме bootstrap-пина), схема `ModelResponse`/`Decision` (tool/schema hash), `normalize_complete_reason`, `rules_hash`, curator-v7 и его пины, explorer-v1…v4 (заморожены), payload'ы config-v2…v11 и их хэши, корпуса, `ARCHITECTURE.md`, пороги, данные прошлых прогонов (SELECT only); eval-run/smoke не запускались, к LLM на 192.168.1.48 обращений не было, в noezema-eval*/noezema-smoke* не писали; фоновых процессов нет. Проверка эффекта нового промпта на модели (класс D) — отдельный контрольный смоук (V14) — решение о нём и разрешение на .48 за пользователем.

### T7.51 — flake `test_slow_llm_does_not_lose_commit_lease` под `-n auto`: ВТОРОЙ режим отказа — голодание продления аренды (не гонка T7.47b); лечение — масштаб времени теста, TTL 1 с → 3 с

**Основание.** T7.47b (`4d5549c`) закрыл ОДИН режим отказа этого теста — гонку `PendingRollbackError` (cancel renewal посреди `execute` на общей сессии), закреплённый детерминированным тестом `tests/unit/test_lease.py::test_guard_exit_does_not_poison_shared_session`. В приёмке T7.50 (2 падения в 3 полных прогонах) и в этой сессии (1 падение в 3 прогонов до правки) тест падает ДРУГИМ классом: `packages.domain.services.lease.LeaseLost: lease lost during long operation for session …` из `LeaseHeartbeatGuard.__aexit__` (`packages/domain/services/lease.py:232`) при `exc_type=None`, т.е. `_lost` выставлен ОТКАЗОМ продления, а не исключением в вызове модели; поднимается в `_curator` (`apps/orchestrator/orchestrator.py:2025`) на длинном curator-вызове. Класс ошибки и место подъёма — другие, чем у T7.47b.

**Механизм (по коду + замерам).** `LeaseHeartbeatGuard._renew_loop` спит `ttl/3` и делает renewal; `LeaseService.heartbeat` — условный UPDATE с предикатом `lease_expires_at > clock_timestamp()`. Продление, чей UPDATE реально исполнился ПОСЛЕ истечения TTL, ОТКЛОНЯЕТСЯ → `LeaseLost` на выходе guard'а → сессия честно аварийно завершается, commit закрыт fencing'ом (§5.2.3) — продуктовое поведение корректно. Запас на один «опоздавший» renewal = `ttl − ttl/3 = 2·ttl/3`: при TTL 1 с это **0,67 с** wall-clock на цепочку « overshoot `asyncio.sleep` + очередь/исполнение UPDATE на нагруженном PostgreSQL»; при TTL 3 с (интервал 1,0 с) — **2,0 с**. Этот же предикат отказывает и прямому `lease.heartbeat(progress=True)` на границе шага (`orchestrator.py:1387`) — запас тот же. Тест был завязан на wall-clock именно с этим запасом.

**Замеры ДО правки (воспроизведение).**
- (a) цикл 20 одиночных прогонов теста + 6 busy CPU-процессов (`python -c "while True: pass"`, loadavg до 6,5): **0/20**; тот же цикл с 12 busy-процессами (loadavg 12,4–12,7 ≈ 2× oversubscription): **0/20** — чистая CPU-контенция flake НЕ воспроизводит (тот же результат, что в before-замерах T7.47b).
- (b) цикл 20 одиночных прогонов теста под DDL-штормом PostgreSQL: 6 фоновых воркеров непрерывно создают scratch-БД → `alembic upgrade head` → DROP (тот же профиль нагрузки, который даёт xdist: 6 одновременных миграций на одном сервере; CPU-burn не запускались): **3/20 падений** (прогоны 7, 8, 18) — два раза `LeaseLost: lease lost during long operation` (`lease.py:232`, подъём guard'а) и один раз `LeaseLost: heartbeat refused for session …` (`lease.py:111` ← прямой `lease.heartbeat(progress=True)` на границе шага, `orchestrator.py:1387`); ОТКАЗЫВАЮЩИЙ ПРЕДИКАТ ОДИН И ТОТ ЖЕ (`lease_expires_at > clock_timestamp()`), т.е. один дефект — запас 2·ttl/3; длительность теста под штормом 13–26 с (против 12,1 с в покое), loadavg ≈ 7.
- (c) полные проверки §6 подряд ДО правки: **1 падение в 3 прогонах** — run 2: `1 failed, 1047 passed, 12 skipped in 249.29 s` (тот же тест, traceback выше), времена прогонов 290 с / 250 с / 254 с — совпадает с наблюдением приёмки T7.50 (2/3).
- Вывод по замерам: источник — конкуренция за PostgreSQL (миграции scratch-БД + запросы соседних воркеров), а не только CPU: шторм воспроизводит flake **без единого другого pytest-воркера**. Продуктовый код причиной НЕ является (сценарий «вызов длиннее TTL → продление опоздало больше чем на 2·ttl/3 → аренда потеряна → честный abort» исполняется по §5.2.3) → правка только тестовая.

**Выбранное лечение — вариант (а): масштаб времени теста при сохранении всех отношений.** В `tests/scenario/test_orchestrator.py` рядом с тестом константы `LEASE_TTL_SECONDS = 3.0` и `MODEL_DELAY_SECONDS = 2.5 * LEASE_TTL_SECONDS` (= 7,5 с): интервал продления `ttl/3` = 1,0 с (было 0,333), задержка модели 2,5×TTL (было 2,5 с при TTL 1 с) — кратность та же, model call по-прежнему > 2×TTL и покрывается ≈7 продлениями за вызов; меняется только абсолютный запас: **0,67 с → 2,0 с**. Ассерты теста (`SUCCEEDED`, `termination_reason`, цепочка model_runs/actions/audit/outbox/session_staging/workspace_manifests/commit_attempts/domain_revisions/claims/evidence/heads) и сам сценарий «вызов модели длиннее TTL» — не тронуты; код продукта (`lease.py`, `orchestrator.py`) не тронут.

**Почему НЕ (б) `pytest.mark.xdist_group`.** Под `-n auto` с дефолтным `--dist=load` метка группы только приковывает тесты одной группы к одному воркеру; flake-тест и так единственный в своём воркере, а нагрузка приходит ОТ других воркеров (их миграции и запросы к общему серверу). Замер (b) воспроизводит flake вообще без других pytest-воркеров → синхронизацией расписания это не устраняется; остаётся снизить чувствительность теста к wall-clock. Цена варианта (а): длительность теста 12,1 с → 31,7 с в покое (под штормом 40–64 с); влияние на полный прогон — в замерах после.

**Замеры ПОСЛЕ правки.**
- (a) тот же цикл 20 прогонов под DDL-штормом (те же N=20 и K=6 одновременных миграций): **0/20**; длительность теста под штормом 40–64 с.
- (b) тот же цикл 20 прогонов под 12 busy CPU-процессами (loadavg до 12,5): **0/20**; длительность ≈33–34 с.
- (c) полные проверки §6 подряд после правки: **8 прогонов подряд — все зелёные** (7 + контрольная проверка на финальном дереве перед коммитом), в каждом `1048 passed, 12 skipped` (тот же состав, что и до правки), падений `test_slow_llm_does_not_lose_commit_lease` — **0/8**, других падений нет. Времена полных проверок (ruff + mypy strict + docker-образ + pytest `-n auto`): 276 / 255 / 223 / 276 / 224 / 279 / 253 / 249 с (pytest-часть: 4:35 / 4:14 / 3:42 / 4:35 / 3:43 / 4:38 / 4:12 / 4:07). Для сравнения ДО правки: 290 / 250 / 254 с и **1 падение этого теста в 3** (run 2) — удлинённый тест не удлинил полный прогон (он не на критическом пути 6 воркеров), а окна flake за 8 прогонов не выпало ни разу.

**Почему новый тест не добавлен (+0):** механизм «продление опоздало → LeaseLost» уже закреплён детерминированно (`tests/unit/test_lease.py::test_guard_raises_when_renewal_refused` — отказ продления; `test_guard_exit_does_not_poison_shared_session` — режим T7.47b). Тест на синтетическую задержку renewal добавил бы ещё один wall-clock-зависимый тест ровно в том классе хрупкости, который эта задача устраняет.

**Не тронуто:** код продукта (`packages/domain/services/lease.py`, `apps/orchestrator/orchestrator.py`), схемы (миграций нет), payload'ы/промпты/пины и их хэши, корпуса, `ARCHITECTURE.md`, пороги, данные прошлых прогонов (SELECT only), детерминированный тест T7.47b в `tests/unit/test_lease.py`, ассерты lease-теста; число тестов не изменилось (+0). eval-run/smoke/NOEZEMA-сессии не запускались, к LLM на 192.168.1.48 обращений не было. Измерительные busy-процессы убиты (`pgrep` пуст), измерительные БД `noezema_storm_%` удалены (0 в `pg_database`); фоновых процессов нет.

### T7.52 — SMOKE-V14-EXL3: первый бой EXL3 + explorer-v5/config-v12 (a: переключение модели и запуск; b: остановка воркера и разбор) — КОНФАУНД: две переменные сразу

**(a) Запуск (T7.52a).** Env T7.45a-профиля переключён с K2 (.48) на EXL3: `qwen38-exl3-3bpw-128k` (EXL3 Qwen3-8B 3bpw, физическое окно 131072; llama-swap на 192.168.1.42:8080), профиль схемы `none`, `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192`; `.48` не опрашивалась, llama-swap не тронут. БД `noezema-smoke-v14-exl3`, payload **config-v12** (file `493970d8…`/canonical `c23005bd…`; активация snapshot `11c2f8c7-…`), корпус `b3e05ad5…`, seed 20260924. Preflight: searxng оказался Exited ~42 ч (restart policy `no` после перезагрузки хоста) → поднят launch-скриптом (`docker start`, :8888 HTTP 200); пины 5/5 зелёные; юниты прошлых смоуков inactive. Run `b99f8ef5-1111-4028-9824-d91f69bdfb3e`: 2026-10-04 02:05:03Z → 02:21:41Z (16,6 мин), EXIT=0, outcome `insufficient_sample`.

**(b) Разбор (T7.52b).** Остановка — после SELECT: 7/7 терминальных (6 succeeded + 1 succeeded_partial), commit_attempts 7/7 committed, потерь нет; юнит inactive; все smoke-*inactive. Отчёт: `docs/eval/SMOKE-V14-EXL3-report.md` (+ указатель в SMOKE-V13-K2-report.md). Ключевое:
- **complete_reason**: 6/7 — класс A (`goal_reached` ровно токеном, пояснение explorer-v5 пишет в отдельное `public_rationale`; T7.49-нормализация не понадобилась ни разу; классов B/C/D ноль); Go-сессия — единственная partial, **честная**: host-терминал `budget_exhausted` после 10 шагов без принятого complete (в V13 5/7 partial были формальными).
- **EXL3 структурно**: 36 model_runs (29+7; planner/verifier — 0, `verification.mode=off` в config-v12), finish stop 36/36, output_schema_valid 36/36 при профиле `none`, outputs max 4481 ≤ 8192 (length-трuncations нет), скорость aggregate ≈47/45 tok/s vs 24/20 у V13-K2 (~×2); fingerprint `context_window 32768/max_output_tokens 4096` — **инертные MVP-дефолты** `ModelProfile` (одинаковы во всех прогонах, включая V13); packing-бюджет берётся из model-секции config-v12: `input_budget = 262144−8192−2048 = 251904` оценочных токенов — **больше физического окна EXL3 (131072)**, риска в этом прогоне не было (max input 30617), наблюдение на будущие длинные сессии.
- **C0/NUL/repeats**: нули по 14 JSON/text-колонкам БД и байтам 19 artifact-файлов; `python.execute` за прогон не вызывался → NUL-фиксы «в бою» по-прежнему не подтверждены (только отсутствие регрессии). Lease-ошибок 0. worker.log: единственный класс traceback'ов — 201 блок teardown `AsyncEngine.dispose … attached to a different loop` + `Event loop is closed` (`hostctl/cli.py`, тот же дефект с V13) при нулевой содержательной нагрузке тиков.
- **Дeny/retry**: policy allow 20/deny 0; один отказ repetition-guard (`tool_call_repeated`, Go); unknown_actions 0; search/searxng во время прогона не использовались; новых вопросов модель не создавала (near_dup 0/7).
- **Гейт 5 = 2/4** (хранёное = пересчёт кодом репозитория, `~/dsh1/smoke-v14/analyze-gate5-v14.py`, MATCH): Python-пара — глубокая перепроверка (ветки [1]+[5], FU добавила новую evidence-строку свежего chocolatey); plan-пара — только ветка [5] (read побайтово идентичен → evidence дедуплицирована; случай ADR-0018 «запись перепроверки — оценка, не evidence»). **Правило 7 не использовалось**: `existing_claim_id=null` ×2, reuse через host-dedup точное statement+type с head-в-снапшоте (T7.9); `claim_reverified` — 0 (в V13 — 2). UN/Sputnik — только знаменатель.
- **Go без claim'а (знаменатель 4 вместо 5)**: `extract_question_urls` обрезает URL по первой `)` → named-источник `…/wiki/Go_(язык_программирования` без закрывающей скобки не может быть покрыт каноническими скачиваниями (coverage-ключ матчит с ней); EXL3 скачивал правильные варианты (полный https/raw/http), 3× отклонение complete, отказ guard'а → бюджет исчерпан; V13-K2 закрыл тот же дефект случайно: literal-fetch битого URL → 404 → `errored` → Go-claim родился. Дефект экстрактора **не лечился** (задача — анализ); выделен в рекомендации отдельной кодовой правкой.
- **Пиновки/схемы**: prompt_sha256 36/36 ненулевые: curator-v7 `19d6c6e8…` ×7 (identичен V11–V13), explorer-v5 `3b1fd49d…` ×29 (= хэш T7.50); tool_schema_hash exploring побайтово равен v13-шному (схемы не менялись); rules-v2 `f96eeffc…`; даты ADR-0016/0017 корректны (explicit UN 2026-04-15; relative Python — host-clock as_of 2026-10-04, единственный reverify_after 2026-11-03 (+30 сут от FU-оценки); none plan/Sputnik); blind structural provenance/scope 4/4 и 4/4 (n=4 < blind-size → insufficient_sample; ручная слепая проверка не проводилась, как в прошлых прогонах).
- **Окружение**: restart policy `no` у `noezema-searxng` и `noezema-test-db`; **предложено (не применено)**: `docker update --restart unless-stopped noezema-searxng noezema-test-db`.

**Конфаунд и границы выводов.** Относительно V13 изменены ДВЕ переменные сразу (модель K2→EXL3 и explorer-v4/config-v11 → explorer-v5/config-v12): безусловны только утверждения о паре «explorer-v5 на EXL3» (чистые токены завершения, 36/36 schema-valid, ×2 скорость, честный partial, отсутствие NUL/C0/lease-аномалий, host-dedup reuse работает без правила 7); атрибуция вклада модели vs промпта по одному прогону невозможна; «гейт 5 изменился» не утверждается (знаменатели 4 vs 5 объясняются потерей Go через coverage-дефект + различием поведения). Рекомендации: контрольные прогоны по одному фактору (EXL3+config-v11 или K2+config-v12); отдельная правка парсера `extract_question_urls` (balanced-parenthesis tail); починка teardown-шума `hostctl/cli.py`; решение о restart-policy — за оператором.

**Не тронуто:** код, тесты (проверка §6 на этом же дереве: ruff/mypy strict 129 файлов/pytest `-n auto` — 1048 passed, 12 skipped), payload'ы v2…v12, промпты/пины и хэши, корпуса, `ARCHITECTURE.md`, схемы БД (миграций нет), пороги; БД `noezema-smoke-v14-exl3` и все прошлые смоук/eval — SELECT only; прогоны не запускались; к 192.168.1.48 обращений не было; docker-политики не менялись (команда лишь предложена); фоновых процессов нет (smoke-v14-exl3-worker остановлен, все smoke-* inactive).

### T7.53 — SMOKE-V14B-EXL3: контрольный прогон EXL3 + explorer-v4/config-v11 (a: запуск; b: остановка воркера и разбор) — конфаунд T7.52 разведён по одному фактору

**(a) Запуск (T7.53a).** Та же модель, что в T7.52 (`qwen38-exl3-3bpw-128k`, llama-swap 192.168.1.42:8080, профиль `none`, max_output 8192), но payload **config-v11** (file `67468a2e…`/canonical `a407ce83…`; активация snapshot `d566c9b6-…`) — т.е. **explorer-v4** `5829a55c…`, побайтово пин V13-K2; корпус `b3e05ad5…`, seed 20260924, правила rules-v2 `f96eeffc…`. Код `fde7640` (продукт = `3b7c577`, идентичен V14; нормализация T7.49 уже активна). Preflight зелёный (`~/dsh1/task51-t753a-smoke-v14b-launch.log`: searxng Up :8888, пины 5/5). Run `b5e5ec33-8af4-4d46-a5d0-a29a1fc343d2`, БД `noezema-smoke-v14b-exl3`: 2026-10-04 03:10:09Z → 03:28:31Z (18,4 мин), EXIT=0, outcome `insufficient_sample`.

**(b) Разбор (T7.53b).** Остановка — после SELECT: **7/7 `succeeded`** (termination_reason goal_reached ×7 канонический), commit_attempts 7/7 committed, потерь нет; юнит остановлен (transient unloaded), процессов worker.sh/hostctl нет. Отчёт: `docs/eval/SMOKE-V14B-EXL3-report.md` (+ указатель в SMOKE-V14-EXL3-report.md). Ключевое:
- **complete_reason**: **7/7 класс A** — сырое значение ровно `goal_reached` (len 12), normalized == raw ×7; B/C/D нули. **Спасённых T7.49-нормализацией статусов — 0 из 7**: страховка не понадобилась ни разу. EXL3 ставит чистый токен в `reason` и при explorer-v4 (перечисление токенов там есть, требования «ровно токен» нет) — весь нарратив в `public_rationale`. Чистота reason — **стиль модели, не строгость промпта** (K2+v4 в V13: D=4, B=1).
- **Go закрыт случайной же цепью**: coverage-дефект тот же (`extract_question_urls` обрезает по первой `)`): 2× completion rejected с `uncovered: ["…язык_программирования"]`; модель скачала литерально битый URL → 404 → `errored` → гейт закрыт → **Go claim E3 «1.27.1»** (артефакты go.dev «go1.27.1»×103 + полная вики с карточкой). Diff v4→v5 касался только завершения (coverage-правила равны) → различие V14/V14B на битом URL = **сэмплинг-variance той же модели**, не эффект промпта; экстрактор не лечился.
- **Claims 6 vs 4**: +Go-claim и +дубль plan-claim: FU-curator написал statement с опечаткой («архитектор **иследователь**» против «и исследователь») → host-dedup (побайтовый statement+type) не слил → отдельный claim E2 (identity-hash наблюдения совпадает с анкерным `4126a0f7…` — дедуп evidence сработал, раздвоился claim). **Гейт 5 = 1/6** (хранёное = пересчёт `_gate_reuse`, MATCH; `~/dsh1/smoke-v14b/analyze-gate5-v14.py`): числитель только Python-пара `[1]+[5]` (FU добавила новую evidence-строку chocolatey `c35b33db…` + assessment чужой сессии); правило 7 не использовалось (`existing_claim_id` null ×7, reuse через host-dedup). Разница 2/4(V14) vs 1/6(V14B) — арифметика разных наборов claim'ов (опечатка + случайно появившийся Go), **не** улучшение/ухудшение механизма.
- **EXL3 структурно**: 38 model_runs (explorer-v4 ×31 + curator-v7 ×7): stop 38/38, output_schema_valid 38/38 при профиле `none`, grammar/HTTP400 ноль; outputs explorer med/p95/max 679/3291/**4691**, curator 2032/3005/**3058** (≤8192); скорость aggregate exploring **45.9** / consolidating **58.3** tok/s (V14: 47.1/45.2; V13-K2: 20.7/24.4); latency max 180 с — один explorer-вызов; шаги 31 (= V13, состав иной). Пиновки 38/38 exact (explorer-v4 `5829a55c…` = пин config-v11); tool_schema_hash exploring побайтово равен v13-/v14-ному. Даты ADR-0016/0017 корректны (UN explicit 2026-04-15 evergreen; Python/Go as_of 2026-10-04 host-clock, reverify_after +30 сут; plan ×2/Спутник none). Blind structural 6/6 + 6/6 (n<blind-size; ручная — не проводилась). Fingerprint cw/mout 32768/4096 — инертны как всегда; packing input_budget 251904 > окна EXL3 131072 (max input 28780).
- **Инструменты (впервые богаче)**: research.fetch ×13, **python.execute ×2** (первый вызов в серии: ISO-дата и дата+время UTC; exit 0, чистый stdout → маска T7.46a прошла «в бою», но маскирование под РЕАЛЬНЫМИ NUL по-прежнему не доказано), workspace list/read/write ×5, memory.search ×1 (пусто до коммитов), **shell.execute ×1 → FAILED `unreachable`**: eval-профиль оркестратора использует `StubToolExecutor` без ветки shell.execute (политика и схема инструмента разрешают — apps/orchestrator/main.py:45, executor.py:71) — разрыв конфига и исполнителя, выделен в открытые пункты. Deny 0, unknown_actions 0, repetition-guard отказов 0 (в V14 был 1), completion rejected ×2 (только Go).
- **C0/NUL/загрязнение**: сплошной скан всех text/jsonb-колонок БД (**189 колонок × 3040 строк**): сырых управляющих байтов, литерального `\x00`-маркера маски, литерального `\u000b`, повторов в модельных полях — **ноль**; попадания регекспa повторов ×7 (audit+outbox) классифицированы: пробелы ×16–26 во внешнем контенте payload'ов. Байты 0x00/0x0B/0x0C в 18 artifact-файлах — ноль. Lease/T7.51 маркеров (LeaseLost/PendingRollbackError/UntranslatableCharacter) — **ноль во всех логах**. Traceback'и: ровно два класса одной teardown-цепи (`Event loop is closed` + asyncio Future-loop, `hostctl/cli.py`, дефект с V13) ×2294 каждый — число раздуто простоем воркера ~4 ч 45 мин после конца eval-run.
- **Выводы (раздельно)**: **(а) эффект модели** K2→EXL3 (промпт/корпус побайтово равны): класс A 2/7→7/7, статусы 2+5→7+0, скорость ×~2–3 — направление надежно; гейт-арифметика (2/5→1/6) — нет. **(б) эффект explorer-v5** на EXL3 **данными не подтверждается**: v4 даёт тот же результат (A 7/7); hypothesis полезности v5 (лечение класса D) остаётся непроверенной ровно там, где D жил (K2-класс). **(в)** при N=7×1 нельзя утверждать значимость различий гейтов (1/6 vs 2/4 vs 2/5), воспроизводимость Go-закрытия и бесполезность v5 на других моделях. **(г) рекомендация**: **оставить explorer-v5 в пине** BOOTSTRAP/config (откатывать нечем — выигрыша v4 в данных нет; D-проблема живёт в K2-классе), серию T7.38…T7.53 закрыть, дополнительный смоук для этого решения не нужен; приоритет открытых пунктов: кодовая правка `extract_question_urls` (balanced-parenthesis tail) → разрыв shell.execute в eval-профиле → увязка packing-бюджета с окном модели → restart-policy контейнеров (предложение T7.52 не применено: searxng Up с 02:04Z запуском V14, в прогоне не использовался; политика `no` у searxng и test-db) → teardown-шум `hostctl/cli.py` → NUL «в бою» (случай появится — фиксировать, спец-смоук не делать) → fingerprint ≠ физическое окно.

**Не тронуто:** код, тесты (проверка §6 на этом же дереве: ruff/mypy strict 129 файлов/pytest `-n auto` — 1048 passed, 12 skipped), payload'ы v2…v12, промпты/пины и хэши, корпуса, `ARCHITECTURE.md`, схемы БД (миграций нет), пороги; БД `noezema-smoke-v14b-exl3` и все прошлые смоук/eval — SELECT only (пересчёт гейта — кодом репозитория); прогоны не запускались; к LLM на 192.168.1.42/.48 обращений не было; llama-swap не тронут; docker-политики не менялись (команда лишь предложена); фоновых процессов нет (smoke-v14b-exl3-worker остановлен, transient unloaded, все smoke-* inactive).

### T7.54 — CI (GitHub Actions) красная на каждом пуше с ≥2026-09-25: две независимые причины — незакреплённая версия ruff и отсутствующая сборка sandbox-образа в ci.yml

**Основание.** Workflow «CI» на `impl/from-scratch` падает на заданиях `lint` и `test` (`types`, `docker` зелёные); пользователь получает письма «[crowrain/noezema] Run failed: CI». Разбор по логам прогона run 37169515170 (коммит `3b7c577`):
1. **lint:** `ruff check .` → 4×SIM117 в `tests/unit/test_lease.py` (строки 59, 65, 105, 131 — вложенные `async with factory() as db: … async with db.begin():`; файл правился в T7.47b). CI ставит dev-зависимости без закрепления (`ruff>=0.8` в pyproject.toml → job `lint`: `uv pip install --system ".[dev]"`) и получает ruff 0.16.10; в `.venv` на `.87` стоял 0.16.7, который на этот синтаксис не реагировал — локальная полная проверка §6 была зелёной. Поведение правила SIM117 различается между этими версиями (точную версию изменения правила не устанавливал — для лечения вывода не требуется); корень расхождения — незакреплённая версия: CI и локально проверяли код РАЗНЫМИ ruff.
2. **test:** итог `1039 passed, 12 skipped, 9 errors`; все 9 ошибок — отказ фикстуры `Failed: docker image noezema-sandbox:test is not built. Run the full check from AGENTS.md §6 …`: tests/scenario/test_sandbox_runtime.py ×3, test_sandbox_security.py ×4, test_tool_broker_sandbox.py ×1, test_failpoints.py::test_container_kill_mid_execution_is_outcome_unknown ×1. Причина: T7.41 (747f10b) убрал сборку образа из фикстуры `sandbox_image` (под xdist каждый воркер — отдельный процесс, параллельный `docker build` одного тега — гонка) и перенёс её в предварительный шаг §6, а ci.yml (последнее изменение — T0.4–T0.6) этого шага не получил; на GitHub-раннере docker доступен (`docker_engine` не скипает) → фикстура падает fail-closed.

**Что исправлено (один коммит, код продукта не тронут).**
1. `.github/workflows/ci.yml`, job `test`: шаг «Build sandbox image» ПЕРЕД «Run tests» — ровно команда §6: `docker image inspect noezema-sandbox:test >/dev/null 2>&1 || docker build -f sandbox/Containerfile -t noezema-sandbox:test sandbox/`. Остальное в ci.yml не изменено (триггеры, service postgres, остальные jobs, версии actions; pytest-шаг оставлен без `-n auto`).
2. `tests/unit/test_lease.py`: 4 нарушения SIM117 устранены объединением вложенных `with` в один в запятой форме (`async with factory() as db, db.begin():`) — та же форма, что уже применяется в этом файле (строки 40/50 и др.) и ровно то, что предлагает autofix ruff. Поведение тестов не изменилось: `with A() as a, B():` эквивалентен вложенным `with` по спецификации языка (тот же порядок входа, выход в обратном порядке, та же propagateция исключений) — эмпирически сверено на синтетических контекст-менеджерах с логами enter/exit (последовательности идентичны); ассерты и порядок контекстов сохранены; все 8 тестов файла зелёные.
3. `pyproject.toml`: dev-зависимость `ruff>=0.8` → `ruff==0.16.10` — CI и локальная проверка теперь одним закрепленным ruff; `.venv` на `.87` обновлен способом репозитория (uv, не pip): 0.16.7 → 0.16.10.
4. `AGENTS.md` §7: добавлена строка-ловушка о незакреплённых версиях в CI и требовании предварительной сборки образа (исторический текст не переписан).

**Проверено локально.**
- Воспроизведение CI: ruff 0.16.10 установлен в ОТДЕЛЬНОЕ окружение вне репозитория (`/home/denis/dsh1/.ruff-ci`, UV_CACHE_DIR как в §6); `ruff check .` на ДО-правочном дереве даёт ровно 4 SIM117 (59/65/105/131) — совпадает с логом CI. После правок: «All checks passed!» версиями 0.16.10 и обновлённым ruff из `.venv`. НОВЫХ нарушений (помимо этих 4) у 0.16.10 по всему репозиторию нет.
- Симуляция разрешения зависимостей CI (`uv pip install --python <чистый py3.11 venv> ".[dev]"` с закреплённым pyproject): ruff==0.16.10 ✓; mypy 2.4.0 — РАСХОДИТСЯ с `.venv` (2.3.1); pytest 9.1.1 — совпадает. Расхождение mypy перечислено, не закреплено (по условию задачи).
- 9 тестов, падавших в CI, и весь test_lease.py прогнаны локально с заранее собранным образом: зелено (песочная группа — passed, lease — 8 passed).
- Полная проверка §6 на финальном дереве: ruff 0.16.10 чисто + mypy strict (129 файлов) + sandbox-образ + pytest `-n auto` — **1048 passed, 12 skipped** (тот же состав, что baseline до правок; сходится с CI-арифметикой: 1039 passed + 9 errors = те же 1048).

**Проверяемо ТОЛЬКО реальным прогоном GitHub Actions (gh/пуша здесь не было):** пройдёт ли шаг сборки образа на hosted-раннере ubuntu-latest (docker там доступен по умолчанию, отдельный docker job в этом workflow уже собирается — ожидаемо, но именно этот шаг ранее не выполнялся); поведение 9 sandbox-тестов на GH-раннере с собранным образом: network=none, cap-drop ALL, non-root, read-only rootfs, tmpfs/bind-mounts могут вести себя иначе в docker/контейнерной среде GitHub, чем на `.87` (тесты test_sandbox_security/test_failpoints проверяют ровно эти опции); установка закреплённого ruff==0.16.10 через `pip install --upgrade uv` + `uv pip install --system ".[dev]"` на чистом раннере; длительность job `test` с учётом сборки образа на чистом кэше (порядка десятков секунд).

**Предложения (не внедрялись, решение за менеджером).**
- `-n auto` в pytest-шаге ci.yml (job `test`): ubuntu-latest — 4 ядра → 4 воркера xdist; по замерам T7.41 параллелизация дала ×2.0–2.1 при 6 ядрах, ожидаемо сокращение pytest-части ~8–9 мин → ~4–5 мин и всего job `test` с ~11.5 мин до ~7–8 мин (с поправкой на сборку образа). Риски низкие: тайминг-тесты масштабированы T7.51, пик подключений к service-PG при xdist замерен 13 из 100.
- mypy/pytest в CI остаются незакреплёнными (`>=`): если очередной релиз mypy сломает зелёный прежде `types`-job — закрепить аналогично ruff; `cache: pip` в setup-python при установке через uv фактически не используется (кандидат на будущую оптимизацию).

**Не тронуто:** код продукта (`packages/`, `apps/`, `hostctl/`), схемы БД (миграций нет), payload'ы/промпты/пины и их хэши, корпуса, `ARCHITECTURE.md`, пороги, данные прогонов; тесты — только `tests/unit/test_lease.py` (стиль `with`) и число тестов не изменилось (+0); к БД noezema-eval*/noezema-smoke* обращений не было вообще (задача — SELECT only, SELECT не выполнялись); eval-run/смоук/NOEZEMA-сессии не запускались; к LLM на 192.168.1.42/.48 обращений не было; llama-swap не тронут; пуша не было, история не переписывалась; фоновых процессов нет (одноразовые вспомогательные venv вне репозитория — `.ruff-ci`/`.ci-sim*` — в git не входят).

### T7.55 (часть 1, коммит с фикстурой) — migrated_db: клон scratch-БД из шаблона вместо `alembic upgrade head` на каждый тест

**Основание.** Замер менеджера (2026-10-04): `alembic upgrade head` в фикстуре `migrated_db`
(отдельный Python-процесс на каждый тест; фикстура запрашивается 369 тестами по collect-only) =
0,8–1,2 с; `CREATE DATABASE ... TEMPLATE` готового шаблона = 0,08–0,19 с. Побочно: 6 xdist-воркеров
мигрировали по одному Postgres одновременно — конкуренция, воспроизводившая flake T7.51.

**Замеры ДО** (чистое дерево `1e4d691`, idle-старт, §6 ×3 подряд, pytest c `--durations=0`):
pytest-часть 263,73 / 227,17 / 281,82 с (медиана 263,73); сумма setup фикстуры migrated_db по
отчёту durations: 988,5 / 793,9 / 947,9 с (медиана 947,9; средний setup на тест под xdist
2,15–2,68 с — выше одиночного замера менеджера из-за конкуренции воркеров). Остатки scratch-БД после
каждого прогона: baseline без изменений.

**Эмпирическая проверка семантики перед реализацией** (одноразовый бенч вне репо, порты/БД только
свои `noezema_bench_*`, удалены): CREATE шаблона → alembic head 0,71–0,72 с → подключений к шаблону
0 → `ALTER DATABASE ... WITH ALLOW_CONNECTIONS false` (запечатанный шаблон отказывает подключениями
даже суперпользователю — проверено); клон `CREATE DATABASE ... TEMPLATE` даёт полную схему (46
таблиц, alembic_version=0025_prompt_content_pin), флаг коннектабельности клонами НЕ
наследуется; клон 0,084–0,102 с против старого пути 0,75–0,79 с (idle) ≈ ×9; 4 параллельных клона
одного шаблона сериализуются ACCESS EXCLUSIVE (~0,15 с каждый) — гонок нет.

**Реализация (tests/conftest.py):** session-scope фикстура `migrated_db_template` — по одному
шаблону `noezema_tpl_<pid>_<hex>` на воркер (и на одиночный запуск): CREATE → `alembic upgrade head`
подпроцессом (как старый путь) → ожидание нуля подключений (engine.dispose + pg_stat_activity, до
20 с) → запечатывание ALLOW_CONNECTIONS false → yield имени; DROP шаблона в finalizer'е сессии
(try/finally; при неудаче — явный UserWarning с именем БД). Session-фикстура читает
`NOEZEMA_TEST_DATABASE_URL` напрямую (function-scope `test_db_url` session-фикстуре недоступен —
ScopeMismatch), без env — skip как раньше. `migrated_db`: контракт не изменён — те же yields
(scratch_url, engine), имена `noezema_mig_<hex>`, teardown dispose+DROP; создание заменено на клон
из шаблона (lock_timeout 20 с + до 3 попыток против transient ACCESS EXCLUSIVE). Отката на старый
путь нет: сломанный/отсутствующий шаблон роняет все зависимые тесты явной ошибкой фикстуры
(fail-closed). Тестов, которые мигрируют сами или полагаются на «пустую БД до миграции», в tests/
не обнаружено (grep по CREATE DATABASE/alembic: единственный создатель БД — conftest.py).

**Новый тест:** `tests/unit/test_fixture_template_clone.py` (+2 теста, unit-маркер): (1) клон
идентичен свежей миграции — совпадение alembic version_num, списков таблиц, колонок (тип/nullable),
pg_indexes (indexdef), pg_constraint с клоном шаблона и с БД, мигрированной напрямую `alembic
upgrade head` (тест воспроизводит старый путь фикстуры, свои scratch удаляет в teardown); (2) два
клона одного шаблона изолированы — таблица+строка, созданные в clone A, отсутствуют в clone B.

**Замеры ПОСЛЕ** (то же дерево кода, §6 ×3 подряд): pytest-часть 105,33 / 154,59 / 103,00 с
(медиана 105,33 — против 263,73 ⇒ −60,0%, ×2,5); сумма setup migrated_db: 146,9 / 260,2 / 102,0 с
(медиана 146,9 против 947,9 ⇒ −84,5%; средний setup 0,28–0,70 с). Итог прогонов: **1050 passed, 12
skipped** (1048 + 2 новых теста; ни один тест не ослаблен/не удалён). Остатки БД в noezema-test-db
до и после обоих блоков identical: 53 × `noezema_mig_*`, 19 dbg-family (18 × `noezema_dbg_*` + 1
`noezema_dbg8*`), 2 × `noezema_clismoke_*`, новых нет, `noezema_tpl_*` = 0 после прогонов.
Легаси-мусор не удалял (фактические числа: mig=53, менеджер называл 54; dbg-family=19 ✓). Команда
очистки по решению менеджера: `docker exec noezema-test-db psql -U noezema -d noezema -c "DROP DATABASE <имя>"` для каждого имени из `SELECT datname FROM pg_database WHERE datname LIKE 'noezema_mig_%' OR datname LIKE 'noezema_dbg%' OR datname LIKE 'noezema_clismoke_%';` (`noezema-smoke-*/noezema-eval*` — не трогать никогда).

**Эксперимент fsync (только замер, проект не изменён):** одноразовый контейнер
`noezema-fsync-bench` (postgres:15-alpine, порт 54330, данные на tmpfs, `-c fsync=off -c
synchronous_commit=off -c full_page_writes=off`, SHOW подтвердил off/off/off) — одна полная
pytest-проверка (`-n auto`) против него: **58,51 с, 1050 passed, 12 skipped** (setup migrated_db в
сумме 49,0 с). Сравнение: на обычном noezema-test-db тот же код — медиана 105,33 с ⇒ цена
fsync/журналирования на этой ВМ ≈ 45–50% pytest-времени даже после перехода на клоны. Контейнер
удалён (`docker rm -f`); noezema-test-db (54329) не перенастраивался и не трогался.

**Не тронуто (коммит 1):** код продукта (`packages/`, `apps/`, `hostctl/`), миграции/схемы,
payload'ы/промпты/пины, `ARCHITECTURE.md`, пороги, данные прогонов; из tests/ изменены только
`conftest.py` и новый тест; eval-run/смоук/NOEZEMA-сессии не запускались; к
noezema-eval*/noezema-smoke* обращений не было (SELECT тоже); к LLM .42/.48 обращений не было; пуша
не было; фоновых процессов после работы не остаётся.

**T7.55 (часть 2, коммит CI) — укрепление `.github/workflows/ci.yml`.**
- `runs-on: ubuntu-latest` → `ubuntu-24.04` во всех 4 jobs: метка `ubuntu-latest` переводится на
  Ubuntu 26 roll-out'ом 19.10–19.11.2026 (GitHub changelog от 17.09.2026, runner-images #14748) —
  пин исключает непрошеную смену ОС раннера посреди активной вехи; откат — одна строка обратно.
- `actions/checkout@v4` → `@v5`, `actions/setup-python@v5` → `@v6`: существование и Node-24-runtime
  подтверждены по сети (checkout v5 — major «Updated to the node24 runtime», требует runner ≥ v2.327.1
  — hosted-раннеры автообновлены; setup-python v6 — breaking-changelog «Upgraded action from node20 to
  node24»). Шаг `with:` (python-version "3.11", cache: pip) и шаги uv не изменены. Более свежие мажоры
  (у них есть и более свежие) сознательно не брались: задача предписывала эти кандидаты,
  лишняя поверхность риска не нужна.
- pytest-шаг job `test`: добавлен `-n auto` (hosted-раннер ubuntu-24.04 = 4 vCPU). sandbox-образ
  собирается предыдущим шагом (T7.54), фикстура под xdist только проверяет тег (T7.41).
- Ожидаемый выигрыс job `test` (было 10,5 мин по CI-прогону менеджера): старый путь на последовательном
  раннере
  тратил почти всё время pytest на ~370 подпроцессов `alembic upgrade head` (замер: 0,8–1,2 с каждый);
  шаблонная фикстура оставляет ~1 миграцию на воркер + клоны (локально сумма setup 948→147 с). С учётом
  -n auto (×2–2,5 по замерам T7.41 на меньшем числе ядер) ожидаем pytest-часть ~8–9 мин → ~3–4 мин и
  job `test` ~5–7 мин суммарно. Оценка, не замер: GitHub Actions здесь не запускался.
- YAML проверен парсером (PyYAML safe_load + assertions: мажоры actions, набор runs-on, pytest-шаг,
  неизменность `with:`); actionlint локально не гонялся. Реальная проверка пунктов 6–8 — только после
  пуша (`gh run watch`, делает менеджер). Самый рискованный пункт — `-n auto` (4 ядра + service-PG
  против тайминг-тестов T7.51); откат — удалить `-n auto` (или reverts коммита целиком), остальное
  независимо.

**T7.55 (часть 3) — расследование flake `test_slow_llm_does_not_lose_commit_lease` (семейство T7.51).**
После перехода на шаблонную фикстуру полный §6-прогон стал краснеть ~в трети запусков (4 красных из 12:
оркестраторный lease-тест, в одном случае плюс `test_guard_keeps_lease_alive_across_long_operation`), всегда
на «медленных» окнах (154–156 с против 100–125 с). Attribution: тот же тест упал и на СТАРОМ conftest
(прогоны в отдельном git-worktree на дереве `1e4d691`, та же машина: 1 красных из 9–10, время прогона
285 с) — значит flake средовой (машина сегодня нагружена извне, load average 4–5), а не привнесённый
шаблонной фикстурой. Механика та же, что описана T7.51: heartbeat-обновление аренды опаздывает > TTL при
проседании event loop/Postgres. Принято решение в tests/conftest.py: сборки шаблонов сериализованы глобальным
flock (`$TMPDIR/noezema_tpl_build.lock`) — устраняет синхронный шторм 6 одновременных `alembic upgrade head`
на старте сессии (источник flake по T7.51) и nested-запусков в середине suites; клоны остаются свободными,
deadlock невозможен (лок держится только ~1–2 секунды сборки и отпускается до любых межпроцессных ожиданий).
Замер M1 (×5 полных прогонов): 3 зелёных (103.8/125.7/139.1 с) / 2 красных (те же lease-тесты на тех же
нагруженных окнах) — статистически значимого снижения частоты в условиях внешнего load не показано, но
защита от шторма построена и стоит дёшево (+единицы секунд на прогрегон). Частотный итог дня: старый путь
1/9–10 красных, новый без M1 4/12, новый с M1 2/5. Рекомендация менеджеру (НЕ выполнено — вне рамок задачи):
перенастроить абсолютные времена lease-тестов по методике T7.51 под новый профиль фикстуры или добавить
повторную попытку только для lease-семейства; при внешнем load на машине flake может повторяться на обоих
путях — это не блокирует CI (на GH-раннере своих соседей нет) и не является дефектом шаблонов.

**Не тронуто (коммит 2):** `.github/workflows/ci.yml`, `tests/conftest.py` (только сериализация сборок
шаблонов, часть 3), этот STATUS (части 2–3) и одна дополняющая строка AGENTS §7; продукт, миграции/схемы,
прочие тесты, пороги, пины, ARCHITECTURE.md, данные прогонов — нетронуты; eval-run/смоук/NOEZEMA-сессии не
запускались; к noezema-eval*/noezema-smoke* обращений не было (SELECT тоже); к LLM .42/.48 обращений не
было; легаси-БД не удалялись; временный git-worktree для замера старого пути удалён; пуша не было.

### T7.56 — flake lease-семейства под `-n auto`: лечение абсолютных времён unit-guard тестов (TTL 0,6→3,0 с) + последовательный stage `timing` в полной проверке и CI; код продукта не тронут

**Основание.** Два известных падających теста: `tests/scenario/test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease`
(история: T7.50 приёмка, T7.51 before TTL 1 с — 3/20 под DDL-штормом; после масштабирования T7.51 до TTL 3 с красным ещё
4 из 12 полных §6-прогонов в «медленных» окнах T7.55) и `tests/unit/test_lease.py::test_guard_keeps_lease_alive_across_long_operation`
(TTL 0,6 с — не масштабировался в T7.51; падал у менеджера на WSL2 `-n 12`; упоминался в красных прогонах T7.55). Механика одна:
предикат продления `lease_expires_at > clock_timestamp()` на часах Postgres; запас на одно опоздавшее продление (и на цепочку
проверок после guard'а) = 2·ttl/3 wall-clock.

**Инвентаризация времязависимых тестов (запас = slack до отказа предиката/дедлайна).**
- `test_guard_keeps_lease_alive_across_long_operation`: TTL 0,6; interval 0,2; op 1,5 (2,5×TTL); запас 0,4 с. ПАДАЛ.
- `test_guard_renewal_is_not_progress`: TTL 0,6; interval 0,2; op 0,9 (1,5×TTL); запас 0,4 с на продление, 0,7 с до первого продления. Не наблюдался.
- `test_guard_raises_when_renewal_refused`: TTL 0,6; interval 0,2; op 0,5; обратный режим — первый renewal должен случиться за 0,5 с (slack 0,3 с), иначе LeaseLost не поднят. Не наблюдался.
- `test_guard_exit_does_not_poison_shared_session` (T7.47b): TTL 1,5; interval 0,5 + pg_sleep 0,5 ⇒ slack 0,5 с; детерминированный регрессионный — по плану задачи не трогался. Не наблюдался.
- `test_slow_llm_does_not_lose_commit_lease`: TTL 3,0 (T7.51); interval 1,0; 4 вызова по 2,5×TTL=7,5 с; запас 2,0 с на продление/границу шага. ПАДАЛ (см. выше).
- Прочие lease: `test_session_nul_commit.py` TTL 10 мин; все orchestrator/scenario тесты — дефолтный TTL 30 с при вызовах без delay ⇒ запас ≥20 с. Не тронуты.
- Вне lease: failpoints `test_container_kill_mid_execution_is_outcome_unknown` (kill контейнера через 1,5 с от начала команды `sleep 30`; хвост 28,5 с); `test_web_api.py::test_wake_now_runs_full_session` (полл-дедлайн ~10–13 с на сессию ~3–6 с); activation_drain (поллы 15/30 с); research_proxy `/slow` (ожидание таймаута — нагрузка не мешает); conftest-ready-wait 20 с. Запасы большие, падений нет.
- Полностью детерминированные и потому НЕ считались: wake_schedule (чистые функции), lease acquire/heartbeat/expired/deadline (истечение форсируется SQL), unit-математика backoff.

**Замеры ДО (N=20 одиночных прогонов каждого подозрительного теста; машина .87, 6 CPU; профили: STORM — 6 воркеров
CREATE noezema_storm_* → alembic upgrade head → DROP [методика T7.51]; CLONE-шторм — новый профиль фикстуры T7.55:
6× CREATE DATABASE ... TEMPLATE запечатанного storm-шаблона + DROP, без alembic; BURN — 12 busy python).**
- STORM: `test_guard_keeps_lease_alive` **1/20** (run 11): продления внутри op прошли (`st1.expires_at > st0.expires_at` зелёный),
  но к финальному `is_live` аренда уже протухла — цепочка «state read + COMMIT + новое подключение + проверка» превысила запас
  0,4 с при loadavg ≈7. Остальные STORM-тесты 0/20, включая slow_llm (dur 45–52 с).
- CLONE: все четыре lease-теста 0/20 — новый профиль фикстуры сам по себе flake не воспроизводит.
- BURN(12): все семь измеренных тестов 0/20 — чистая CPU-контенция flake НЕ воспроизводит (тот же вывод, что T7.47b/T7.51).
- failpoints kill и web_api poll: 0/20 в обоих профилях → лечения не потребовали.

**Выбранное лечение.**
(a) *Масштаб unit-guard тестов* (лечит воспроизведённый flake): константы `GUARD_TTL_SECONDS = 3.0`,
    op-кратности сохранены побайтово отношеними: 2,5×TTL (`LONG_OP_SECONDS`), 1,5×TTL (`NOT_PROGRESS_OP_SECONDS`),
    2,5 интервала (`REFUSED_RENEWAL_OP_SECONDS`) — было 1,5/0,9/0,5 при TTL 0,6. Запас на опоздавшее продление и на
    post-guard цепочку: 0,4 → 2,0 с (×5), интервал 0,2 → 1,0 с. Тот же масштаб, что T7.51 выбрал для scenario-теста.
(b) *Маркер `timing`* (зарегистрирован в pyproject markers) на четырёх wall-clock lease-тестах (три unit-а выше + slow_llm):
    полная проверка §6 = `pytest -n auto -q -m "not timing"` затем `pytest -q -m timing` (без xdist); ci.yml job `test` —
    два шага так же. Обоснование по замерам: изолированно slow_llm стабилен на существующем TTL 3 во всех трёх профилях
    (0/20), его падение — многоворкерная конкуренция за общий Postgres (красные окна полных прогонов T7.51/T7.55);
    последовательный stage структурно убирает соседей-воркеров, не меняя тест. Unit-тесты помечены согласованно: любой
    wall-clock lease-тест вне параллельного пула (§7). Абсолютные значения slow_llm (3/7,5 с) НЕ изменены — масштаб T7.51
    подтверждён замерами; TTL 6 не вводился: избыточен по данным (STORM clean на 3), удвоил бы цену stage-2.
(c) *Отвергнуто*: подмена Python-времени/sleep — предикаты живут на часах БД, подмена не устраняет wall-clock зависимость;
    шов в продукте (параметр интервала guard'а) не вносился — вне рамок задачи (требует решения пользователя). Assert'ы,
    сценарий «вызов дольше TTL», retry-механизмы — не тронуты/не добавлялись.

**Замеры ПОСЛЕ.**
- Одиночные циклы N=20 леченных unit-тестов × те же три профиля: 9/9 циклов **0 падений** (STORM totals 382/317/242 с,
  CLONE 183/142/83 с, BURN 226/181/129 с; длительность прогонов выросла вместе с op — цена масштабирования).
- Полная проверка §6 (новая двухстейджовая команда) ×8 подряд на простаивающей машине: **8/8 зелёные**
  (stage1 1046 passed + 12 skipped, 103,07–143,56 с; stage2 4 passed, 47,21–53,84 с; wall с ruff/mypy/образом 151–197 с).
- §6 при фоновой CPU-нагрузке (6 busy python всё время прогона) ×4: **4/4 зелёные** (stage1 142,18–154,06 с;
  stage2 48,49–52,15 с; wall 195–204 с). Итого 12 полных проверок — 0 падений.
- Цена по времени vs T7.55 (wall ≈104–139 с, pytest -n auto 109,66 с): stage-2 добавляет ~47–54 с последовательного
  времени; параллельный stage частично компактнее (lease-тесты из него исключены); net ≈ +45…+60 с на полную проверку.

**Что не проверено до реального CI-прогона:** поведение двух pytest-шагов ci.yml на hosted GitHub-раннере (marker
фильтрация, service-PG при `-m timing`, длительность job test ~ оценка 6–8 мин) — локально YAML провалидирован
PyYAML safe_load с assertions (оба pytest-шага, порядок «parallel→serial», шаг сборки образа неизменен, runs-on/actions
не изменены); actionlint не запускался; реальный прогон — за менеджером (`gh run watch`).

**Не тронуто:** код продукта (`packages/`, `apps/`, `hostctl/`), миграции/схемы, payload'ы/промпты/пины, корпуса,
`ARCHITECTURE.md`, пороги, данные прогонов; из tests/ — только `tests/unit/test_lease.py` (3 guard-теста: TTL/op-масштаб +
маркер), `tests/scenario/test_orchestrator.py` (маркер + комментарий; значения T7.51 не менялись), маркер в pyproject;
детерминированный тест T7.47b и ассерты lease-тестов — без изменений; число тестов не изменилось (+0); conftest не тронут.
eval-run/смоук/NOEZEMA-сессии не запускались; к noezema-eval*/noezema-smoke* обращений не было (SELECT тоже); к LLM
.42/.48 обращений не было. Измерительные процессы (storm/busy) убиты (pgrep пуст), измерительные БД `noezema_storm_*`
удалены; легаси-счётчики БД вернулись к исходным: mig=53 / dbg-family=19 / clismoke=2 / tpl=0 (замерено в начале и в
конце задачи); пуша не было, история не переписывалась.

---

### T7.57 — два независимых дефекта eval: extract_question_urls и скобки URL (SMOKE-V14 §4.3) + диагностика shell.execute в Stub-исполнителе

Две независимые правки, каждый — отдельным коммитом (a)/(b), ревертируемыми поодиночке.
Ни один замороженный артефакт (корпуса/пины/payload'ы/промпты/schemas) не тронут.

#### Часть (a): URL со сбалансированными скобками извлекаются целиком

**Дефект.** `_URL_RE = r"https?://[^\s\"'<>)\]]+"` в `packages/memory/scope.py` исключал «)» из
тела URL: вопрос Go (`…/wiki/Go_(язык_программирования)`, corpus question-set-smoke.jsonl,
строка 6; также v4 строки Go_/Rust_/R_, v3 Rust_/R_) давал обрезанный named-source без закрывающей
скобки. Гейт покрытия (ADR-0010) требовал скачать именно битый адрес: канонические загрузки
полного URL не совпадали с усечённым `coverage_key` → `complete_rejected`; в SMOKE-V14 Go-сессия
умерла budget-exhausted без claim (гейт 5: denominator 4), в V13-K2/V14B Go закрылся только случайно
— модель скачала литеральный обрезанный URL → 404 → `errored` (SMOKE-V14B §3, E3 через errored).

**Замер до правки** (измерительный скрипт вне репо; корпусные строки `#`-комментарии пропускаются,
как в hostctl eval-run): v1 50 строк — скобко-содержащих URL 0; v2 50 — 6 строк с обёрнутыми парами
URL «(url1 и url2),» (извлекались корректно, обязаны были остаться идентичными); v3 69 — 8 строк, из
них 2 усечённых (`…/Rust_(язык_программирования`, `…/R_(язык_программирования`); v4 55 — 8, из них 3
усечённых (Go_/Rust_/R_); smoke 7 — 1 усечённая (Go_).

**Замороженные хэши — затронуты ли (проверено до правки):** НЕТ. Байты корпусов не менялись
(sha corpus/pin `b3e05ad5…` сверен); payload'ы config-v*, промпты, схемы инструментов URL вопросов
не содержат (grep по docs/eval/*.json — ноль вхождений wikipedia/go/rust-адресов); экстрактор
вызывается только на записи (`derive_claim_scope` в write-путях commit, `named_source_urls` на старте
сессии) и никогда на чтении — гейты/переоценки читают сохранённые scopes; строки audit past-run БД
(noezema-smoke-v13-k2 / v14-exl3 / v14b-exl3, SELECT) содержат усечённые написания как неизменяемые
данные прошлых прогонов — они не пересчитываются. Влияния на записанные артефакты нет → правка допустима.

**Правка.** `extract_question_urls`: `_URL_START_RE` находит схему, `_scan_url_token` сканирует тело
счётчиком баланса скобок (произвольная вложенность): «(» входит в URL; «)» входит, только закрывает
«(» внутри токена, иначе — конец URL и остаётся СНАРУЖИ (обёртка фразы, markdown-скобка).
Терминаторы whitespace/кавычки/`<>`/`]` не изменились (NBSP терминирует, как прежний `\s`); хвостовые
`.,;:!?` срезаются как раньше; дедупликация и порядок — те же. Единственное поведенческое отличие,
помимо скобок: `https://.` больше не даёт пустой «URL» (защита; корпусных случаев нет).

**Последствия для покрытия (проверено тестами).** `coverage_key` нормализует весь путь через
percent-decode+lowercase (`wikipedia.org/wiki/go_(язык_программирования)`), поэтому каноническая
загрузка с percent-encoded Cyrillic и литеральными скобками (то, что реально качает браузер/прокси)
и полностью закодированный вариант `%28…%29` дают ОДИН ключ — названный источник закрывается
канонической загрузкой. Несоответствие извлечения и сопоставления исключено: тот же путь в том же
ключе; тесты фиксируют оба написания.

**Код/тесты:** `packages/memory/scope.py` (экстрактор); `tests/unit/test_scope.py` (+1 parametrized:
11 случаев — корпусный Go-вопрос, URL в скобках предложения, `(см. url)`, markdown `[текст](url)`,
точка/запятая после закрывающей скобки, две wiki-URL в одном тексте, query+fragment, вложенные скобки,
v2-регрессия обёрнутой пары байт-в-байт, пустой случай); `tests/unit/test_source_coverage.py` (+3:
named-набор Go-вопроса содержит ПОЛНЫЙ адрес; нормализация ключа Cyrillic/скобок/`%28`; трекер
закрывается каноническими загрузками); `tests/scenario/test_source_coverage.py` (+1 полный сценарий:
вопрос называет wiki-URL со скобками (example-домены), модель качает canon (percent-encoded) →
первый же `complete` принят, ноль `complete_rejected`, claim E3 supported от 2 групп, coverage-отчёт
несёт полные написания). ADR-0010 §3 п.1 — уточнение T7.57 (без переписывания истории).

Существующие ассерты test_scope.py/test_source_coverage.py не ослаблены и прошли без изменений.

#### Часть (b): shell.execute в eval — дыра конфигурации и «unreachable» без диагностики

**Факты анализа (до правки, код HEAD ca5fc9a):**
(i) Исполнитель во всех прогонах — `StubToolExecutor`: `build_orchestrator`
(apps/orchestrator/main.py:45) используется и wake-тиком (hostctl/cli.py:273), и
`eval-run`/смоуками (тот же builder в цикле серии внутри `eval_run`, hostctl/cli.py:1193, def с :979);
Web API — тоже stub (apps/web/api.py). `SandboxToolBroker` (packages/broker/broker.py)
инстанцируется только в тестах (`tests/scenario/test_tool_broker_sandbox.py`,
test_sandbox_security*). Реального исполнения shell.execute в eval нет: ветки в stub
не было, был финальный `Observation(ok=False, error="unreachable")` с `# pragma: no cover`
(executor.py:71) при существовании ToolSpec shell.execute (packages/policy/tools.py:91,
NON_IDEMPOTENT, ShellExecuteArgs command≤10000) и его присутствии в списке инструментов
всех замороженных payload'ов config-v2…v12 (и BOOTSTRAP). Тот же разрыв касается
artifact.create — того нет даже в реестре (`unknown tool`).
(ii) `_python_execute` stub: `asyncio.create_subprocess_exec(sys.executable, "-I", "-c", code,
cwd=workspace)` — код модели исполняется НА ХОСТЕ (.87) от имени пользователя сервиса;
`-I` (isolated: без env/cwd-импортов) и 15 с timeout c kill прямого ребёнка, вывод ≤10 кБ
с маскированием NUL. Сети/ФС-изоляции НЕТ: честный уровень риска — полный доступ к хосту
на правах пользователя сервиса; допустимо только потому, что модели локальные/полудоверенные
и прогоны закрытые (docstring модуля: DEV ONLY, «never run in production with an untrusted model»).
(iii) Модель видит инструменты из per-step `filter_offered_tools` (orchestrator.py:151,
секция «# Доступные инструменты … Только этот список существует») над capabilities снапшота —
shell.execute и artifact.create там есть; хэш `tool_schema_hash(allowed_tools)` пишется в
model_runs каждого шага (orchestrator.py:1448, plan-этап :851/:887) и входит в fingerprint
(`toolchain_hash`). Любое изменение offered-списка меняет байт-хэши и ломает сопоставимость
с прошлыми прогонами V13–V14B (сверка идёт по этим значениям).
(iv) `Observation(ok=False, error="unreachable")` для оркестратора — обычный НЕудачный шаг:
action FAILED + ACTION_FAILED audit + observation-строка модели (orchestrator.py:1702–1741),
evidence не создаётся (`observation_to_evidence`: not ok → None, apps/orchestrator/evidence.py:95);
transient учитывается только ретраями ToolBroker (broker.py RETRY_POLICY) — на stub-пути не
используется; result_unknown=False → ветка T2.21 не включается; статус сессии не меняется
(потому в V14B shell.execute ×1 дал FAILED «без последствий»). Повтор-гард считает и неудачные
исполненные вызовы (инкремент до исполнения, orchestrator.py:1644): третий идентичный — deny.

**Правка (минимальная безопасная).** Финальный fallback stub'а вместо `unreachable`/no-cover:
`tool_not_supported: <tool> is not available in this executor (dev/eval stub); use python.execute
or workspace.* instead`, transient=False. shell.execute в stub НЕ реализован. Списки инструментов,
payload'ы, схемы, tool_schema_hash — не тронуты (диагностика видна модели только как текст неудачного
наблюдения; повтор-гард и лимиты прежние).

**Тесты:** unit `test_policy_tool_without_stub_impl_gives_explicit_diagnosis`
(tests/unit/test_stub_executor.py: shell.execute → tool_not_supported/не transient/не unknown-tool;
research.fetch на прямом вызове; artifact.create остаётся unknown tool) + guard
`test_stub_python_execute_documented_host_risk_stays_explicit` (DEV ONLY/-I/timeout видимы в модуле);
scenario `test_stub_unsupported_tool_is_a_normal_step_failure` (tests/scenario/test_orchestrator.py:
полная сессия с shell.execute → action failed c диагностикой, action_failed audit ×1, evidence 0,
сессия SUCCEEDED).

**Предложения (не реализованы):**
- **А. Фильтровать список инструментов модели по возможностям исполнителя.** Плюсы: модель
  перестанет дергать неисполнимые инструменты; минус: меняются `tool_schema_hash` каждого шага и
  offered-строка промпта → ломается байт-сопоставимость eval-прогонов V13–V14B и будущих замороженных
  серий (сравнение по toolchain_hash/model_runs); кроме того фильтровать надо по снапшоту, а не по
  коду — иначе нарушен §Effective config. Цена/риск выше пользы на текущем этапе.
- **Б. Перевести eval-run/смоуки на реальный SandboxToolBroker (docker на .87 есть).** Плюсы: shell/python
  исполняются в одноразовом контейнере (network none, cap-drop ALL) — закрывает главный дырявый участок
  (ii); соответствие §Sandbox инварианту; Model-facing список не меняется → хэши сопоставимы внутри
  новых заморозок. Оценка: умеренная — в `build_orchestrator` добавить жизненный цикл контейнера на
  сессию (runtime+handle уже готовы из тестов), mount workspace, таймауты/ретраи broker'а (§5.7) включатся
  фактически; риски: +секунды на старт контейнера за шаг (среднее влияние на wall-clock смоуков), drift
  образа при пересборке между прогонами (пин `noezema-sandbox:test` — тот же, что в тестах), ошибки
  инфраструктуры станут transient-ретраями (изменит статистику шагов). Это рекомендуемое направление.
- **В. Реализовать shell.execute в Stub.** Не рекомендуется: `/bin/sh -c` прямо на хосте усилит (ii)
  вместо изоляции; stub по замыслу — временная подставка M1.

**Рекомендация:** Б (eval/smoke на реальном sandbox-исполнителе), как отдельная задача с замером
wall-clock стоимости; А не делать; В не делать. До решения пользователя ничего из этого не внедрено.

**Не тронуто (b):** payload'ы/промпты/schemas/пины/корпуса/pins, tool-списки снапшотов, повтор-гард,
`tool_schema_hash`, прошлые прогоны; shell.execute в stub не реализован. eval-run/смоуки не запускались;
LLM .42/.48 не трогались; к noezema-eval*/noezema-smoke* только SELECT (в части a).

## T7.58 — инструментарный исполнитель сессий: переключатель stub | sandbox, одноразовый контейнер в жизненном цикле `run_session` (реализация рекомендации «Б» T7.57; ADR-0023)

**Основание.** Задача менеджера: подготовить и проверить режим реального
инструментарного исполнителя для eval/смоуков, НЕ меняя дефолтное поведение
(в T7.57 это была рекомендация «Б» с явной просьбой замерить wall-clock стоимость).
Красных тестов не было — задача дизайнерско-инженерная; критерий остановки (согласован с
менеджером): если интеграция требует менять контракт `run_session`/семантику сессии сильнее
небольшого хука, менять схемы/хэши/промпты/пины/model schemas или ARCHITECTURE.md — остановиться
на документационном разборе. **Критерий не сработал** (дельта orchestrator.py — +52 строки, из них
исполняемого кода ~20: два хука по 6 строк и два вызова в phase 1; схемы/промпты/
пины/ARCHITECTURE.md не тронуты).

### 1. Дизайнерский разбор (шаг 1)

**(а) Где создавать и гарантированно уничтожать контейнер.** `build_orchestrator`
(`apps/orchestrator/main.py:53`) вызывается **на сессию** во всех host-путях: wake tick — один orchestrator
на тик = одна сессия (`hostctl/cli.py::_tick` → `orchestrator.run_session(question_id)`), eval-run —
per-session цикл `for i in range(args.sessions)` со своей сборкой и своим scratch-URL
(`hostctl/cli.py::_run_sessions`), web app — один orchestrator в standalone-app. Профиль
(`cap_profile`) и `session.id` известны только **внутри** phase 1, поэтому lifecycle вынесен в
`Orchestrator.run_session`, а не в CLI:

- открытие — `_run_to_committing`, сразу после audit `SESSION_STARTED` (`apps/orchestrator/orchestrator.py:606`):
  durable `session.id` (имя контейнера детерминировано: `noezema-sb-<session_id[:12]>`) и эффективный
  `cap_profile` уже есть; до этого ни один инструмент не исполнялся, отказа модели ещё нет;
- закрытие — `finally` вокруг phase-1 транзакции (`orchestrator.py:378–384`): покрывает нормальное
  завершение, ранний терминальный `_finish` (budget/admission/stop), поднявшийся `LeaseLost`,
  infra-исключение и rollback phase 1; `close_session()` не бросает и не меняет исход сессии.

Контракт `run_session` (сигнатура, `CommitPlan`, `SessionOutcome`, транзакции, fenced commit, аудит)
не изменён: хуки опциональны и объявлены отдельным Protocol `SessionScopedExecutor`
(`apps/orchestrator/tool_executors.py`), оркестратор зовёт их через `getattr`
(`_open_tool_sandbox`/`_close_tool_sandbox`, orchestrator.py:2380–2404). У `StubToolExecutor` и у всех
тестовых doubles хуков нет → stub-путь исполняется дословно как раньше (в unit-тесте закреплено
`not hasattr(executor, "open_session")`). Деструктор/контекстный менеджер не подошли: `run_session` —
не async-CM, а failure-обработка вызывающего CLI должна была остаться прежней.

**(б) Consistency workspace.** Stub держит общую директорию `root/"workspace"` (`StubToolExecutor.workspace_dir`,
одна на orchestrator; оркестратор делает «freeze» в снапшот-манифест, схватывая `executor.workspace_dir`
— orchestrator.py:863–870) и использует её как staging-dir artifact store. Sandbox берёт per-session overlay
`work_root/<session_id>/work`, смонтированный в контейнер (`packages/sandbox/runtime.py::start`), — это ровно
«session overlay» спеки §5.7. Freeze работает в обоих режимах без правок: в sandbox он видит overlay (в
scenario-тесте записано `notes.md` → `workspace_entries` + `committed_workspace_manifest_id`), после
`close_session()` каталог удалён, а манифест остаётся durable-артефактом. Развязка обязательна: общий
host-workspace на несколько сессий = перенос состояния между сессиями (в eval это скрытый канал).

**(в) Чем среда контейнера отличается от хоста и что это меняет в смоуках.** См. таблицу п. 4 ниже:
`python:3.11-slim` (замер образа: Python 3.11.16, `/usr/local/bin/python`), из пакетов — только
pip/setuptools/wheel/packaging базы, сторонних нет; network none; на хосте python 3.11.15 из `.venv`, тот же
интерпретатор, что у оркестратора (хотя и с `-I`), есть пакетный набор проекта, есть сеть, есть вся
файловая система пользователя. Следствие для смоуков: `python.execute` в контейнере не сможет
`import httpx/numpy/bs4` (в stub мог) и не сможет достать URL из sandbox (`requests`); это сужает
то, что модель реально делает, — но делает это предсказуемым и изолированным.

**(г) Пути `research.fetch`, `memory.search`, `question.create`, `artifact.create`.** Проверено по коду:
`research.fetch` перехватывается оркестратором host-side в обоих режимах (orchestrator.py:1667–1676 →
`self._research_fetch`, §M6 proxy; в broker он вообще unreachable) — режим его не касается; `memory.search`
в broker идёт через runtime-хранилище с host-generated scope, session-scoped storage подключается оркестратором
(`session_storage.attach`, snapshot_id pinned); `question.create`/`message.reply` deferred (staging writer) и
применяются host-side (`_apply_host_side`) — детерминированный эффект идентичен. Отличается одно: в stub эти
вызовы исполняет `StubToolExecutor`, а не broker, поэтому retry-policy Tool Broker (§5.7) продуктивно не жил;
в sandbox он включается. Набор инструментов модели **не изменён ни в одном режиме**: offered-список и
`tool_schema_hash` считаются из снапшота (`sorted(cap_profile.tools)`), `artifact.create` отсутствует в реестре
и даёт `unknown tool` одинаково в обоих режимах (unit-тест паритета).

**(д) Переходные отказы broker и повтор-гард.** Broker ретраит только transient infra по классам §5.7
(`RETRY_POLICY`: pure 2, idempotent 1, observation/non_idempotent 0 — python/shell не ретраятся); `SandboxError`
→ `transient=True`, «контейнер исчез» → `result_unknown=True` → ветка T2.21 (outcome unknown). Повтор-гард
оркестратора считает **исполнение шага**, а не попытки broker: счётчик увеличивается до вызова
(orchestrator.py:1665) и независим от исполнителя → transient-ретраи broker не раздувают счётчик. Session status
от этого не меняется (неудачное наблюдение = обычный failed-step, как показал разбор T7.57); риск новый — рост
доли failed-шагов при проблемах docker, что в eval видно по `actions.state`.

**(е) Нужен ли docker eval-run процессу на `.87`.** Да: пользователь `denis` состоит в группе `docker`
(проверено `id`), docker 29.x доступен, образ `noezema-sandbox:test` собран §6-шагом; unit'ы systemd могут
задавать `NOEZEMA_TOOL_EXECUTOR=sandbox` и `NOEZEMA_SANDBOX_IMAGE=<pin>` через EnvironmentFile. Никаких
новых привилегий не требуется (root-less контейнер, bind-mount только на свой work_root в /tmp).

### 2. Что сделано

- env-switch `NOEZEMA_TOOL_EXECUTOR` (`stub` по умолчанию | `sandbox`), разбор — чистая функция
  `resolve_tool_executor_mode(raw)` в новом модуле `apps/orchestrator/tool_executors.py`; неизвестное значение →
  `ToolExecutorConfigError` с именем переменной, значением и списком известных режимов (тихого отката на stub нет;
  тот же fail-closed паттерн, что у `NOEZEMA_LLM_SCHEMA_PROFILE`).
- `build_tool_executor(workspace_root)` — единственная точка сборки; её используют все входы:
  `apps/orchestrator/main.py:53`, `apps/web/api.py:1186` (иначе web-slice молча остался бы на stub при общем
  `EnvironmentFile`). CLI (`hostctl/cli.py`) передаёт режим в journal и отказывается ДО записи
  `node_state='session_running'`: wake tick → `exit 78`, eval-run → fail-closed без `_finish` (серия не считается).
- sandbox-режим: preflight движка и образа (`ensure_sandbox_available`, понятная ошибка с командой сборки),
  контейнер на сессию, профиль = YAML-потолок access-профиля снапшота с network `none`
  (`sandbox_runtime_profile`: curated/open_lab `research_proxy` контейнеру не выдаётся никогда — egress host-side;
  инструменты вне потолка и неизвестный профиль → отказ), гарантированное уничтожение + удаление overlay,
  идемпотентный close (утечка называется в логе процесса).
- Новые тесты: `tests/unit/test_tool_executor_switch.py` (25) + `tests/scenario/test_orchestrator_sandbox_executor.py`
  (5, docker; без маркера `timing` — wall-clock ассертов нет, чтобы не нагружать lease-тесты). Существующие тесты
  и фикстуры не изменены; ни один ассерт не ослаблен.
- Логи: только процесс (`logger = noezema.tool_executor`, метки CLI), audit/`model_fingerprint`/CommitPlan — без новых полей.

### 3. Замеры стоимости (шаг 4; скрипт вне репо `../t758_bench.py`, fake-движка нет)

| операция | N | median | p95 |
|---|---|---|---|
| `runtime.start` + `destroy` (контейнер на сессию) | 10 | **1,524 мс** | 2 058 мс |
| preflight `docker image inspect` | 10 | 22 мс | 28 мс |
| `python.execute print(1)` в контейнере | 20 | **48,6 мс** | 51,0 мс |
| `shell.execute echo 1` в контейнере | 20 | 38,1 мс | 40,9 мс |
| `python.execute print(1)` через Stub (хост) | 20 | 8,9 мс | 11,5 мс |
| `python execute sum(range(1e5))` контейнер / stub | 5 | 51,3 мс / 9,7 мс | — |

Дельта одного вызова ≈ +40 мс (запуск интерпретатора внутри контейнера против хостового `-I`).
Экстраполяция на типовой смоук (7 сессий × ~8 шагов): **+10,8 с** при 0 python-вызовах на сессию,
**+11,1 с** при 1, **+11,7 с** при 3 — т.е. цена почти целиком в старте контейнера (≈1,5 с × 7), а не в
инструментах; это ≈1 % wall-clock смоука V14B-EXL3 (eval-run 18,4 мин —
`docs/eval/SMOKE-V14B-EXL3-report.md`, сводная таблица прогонов). `docker ps -a` после прогона: ноль
контейнеров с проектым префиксом.

### 4. Python-среда: stub vs контейнер и влияние на смоуки

| | Stub (default) | sandbox-контейнер |
|---|---|---|
| интерпретатор | `sys.executable` = python проекта (3.11.15) с `-I`, та же среда, что у оркестратора | CPython 3.11.16 из `python:3.11-slim` (`/usr/local/bin/python`), только stdlib |
| pip-пакеты | доступны пакеты `.venv` (httpx, sqlalchemy, pydantic, click…; нет numpy/pandas/bs4/lxml/PIL) | нет сторонних (`pip list`: pip 24.0, setuptools, wheel, packaging); сам pip в образе есть, но поставить ничего не может: network none + read-only rootfs |
| сеть | есть (хостовая) — stub может достать любой URL | `--network none`: ни DNS, ни интерфейсов (проверено тестом: `gaierror`, `/proc/net/dev` = только `lo`) |
| ФС | вся ФС хоста + общий `root/"workspace"` | read-only rootfs, запись только в overlay сессии (+tmpfs /tmp 64M), UID 10001, cap-drop ALL |
| лимиты команды | жёстко 15 с (`TOOL_TIMEOUT_SECONDS`), cap 10 000 байт | из профиля снапшота: sealed 60 с/512 MiB/32 PID/CPU 1.0; curated 120 с (timeout, pids, cpu — из YAML) |
| `shell.execute` | не реализован → `tool_not_supported:` (T7.57(b)) | работает (`/bin/sh -c`) и в sealed-профиле разрешён |

Практический эффект для смоуков: в sandbox модель потеряет сетевые «заходы» через python (в stub они
работали — дыра (ii) из разбора T7.57) и потеряет хостовые пакеты; взамен получает рабочий `shell.execute`
и одинаковый с eval-инвариантом изолирующий контур. Ожидать стоит роста шагов `web.search`/`research.fetch`
(единственные легальные источники) и появления failed-наблюдений вида «ModuleNotFoundError» — это сигнал модели,
а не ошибка хоста.

### 5. Не сделано / остаётся для T7.59

- Реальный смоук в sandbox-режиме (с живой моделью) не запускался — по условию задачи это отдельная задача
  T7.59 с решением пользователя; проверено только fake-LLM + реальные контейнеры.
- Не сделано сознательно: фильтрация offered-списка инструментов по возможностям исполнителя
  (меняется `tool_schema_hash` → ломает сопоставимость серий), shell.execute в stub, миграции/аудит-поля режима,
  docker-in-docker и per-step контейнеры.
- Дрейф образа: прод-дефолт `SandboxSettings.image = noezema-sandbox:dev`, тесты и §6 пинят
  `noezema-sandbox:test`; для замороженной eval-серии тег надо пинить явно (как пины LLM) — решение за пользователем.
- Рекомендация по включению дефолта: **пока нет** — оставить `stub` как значение по умолчанию (существующие
  прогоны и тесты остаются байтово сопоставимыми), включить `NOEZEMA_TOOL_EXECUTOR=sandbox` в EnvironmentFile
  eval/смоук-юнитов `.87` после одного контрольного смоука T7.59 (замер wall-clock ≈+1 %, риск — новая доля
  failed-шагов из-за отсутствия пакетов/сети и зависимость от доступности docker на хосте eval'а).

**Остатки.** `docker ps -a`: контейнеров с префиксом `noezema-sb-` — 0 (до и после); всего контейнеров 41, как
до задачи (новых не создано: ни один контейнер не пережил teardown тестов). Scratch-БД: `noezema_mig_%`=53,
`noezema_dbg%`=19, `noezema_clismoke_%`=2, `noezema_tpl_%`=0, storm=0 — идентично baseline до задачи.
К `noezema-eval*`/`noezema-smoke-*`/`noezema_mvp` обращений не было; eval-run/смоуки не запускались; LLM .42/.48
не трогались; фоновых процессов не осталось. Полная проверка §6 (ruff + mypy strict 130 файлов + образ +
pytest `-n auto -m "not timing"` **1094 passed, 12 skipped** (baseline 1064 + 30 новых) за 132 с; затем
`-m timing` **4 passed** за 47 с) — зелёная.

## T7.59(а) — приём вопроса оператором: CLI `ask`, `POST/GET /api/v1/questions`, форма на главной странице (ADR-0024)

**Основание.** Задача менеджера: дать живому человеку возможность «потрогать MVP» —
задать вопрос из браузера и увидеть его в очереди и в сессии. Критерий остановки (предварительный
анализ до правок): если приём требует миграции схемы, нового значения закрытого `QuestionOrigin`
с правкой hash/payload или изменения семантики выборки сильнее priority — остановиться на
документационном разборе. **Критерий не сработал** (обоснование в п. 1), реализация сделана.

### 1. Анализ исходного пути (что было в коде до задачи)

**(а) Продюсеров вопроса было два, оператора среди них не было.** `questions` заполняли:
сидинг корпусом (`hostctl/cli.py::eval-run`, прямой `INSERT … origin='seeded'` с проверкой
`SELECT 1 FROM questions WHERE text = :t`) и модель через staging
(`packages/domain/services/staging.py::apply_staging` по `new_questions` куратора, origin из
схемы proposals, default `'model_proposal'`, `parent_id=session.question_id`). Ни CLI, ни web
вопрос не создавали.

**(б) Сообщения человека в очередь не превращались.** `POST /api/v1/messages` пишет `ORMMessage`
(state `created`), оркестратор доставляет недоставленные сообщения в `ctx.messages` на orienting
(недоверенный текст контекста, плюс offered-инструмент `message.reply`) — очередь FIFO при этом
не меняется: §13.6 (жизненный цикл сообщений) и priority «влияет только на порядок доставки».
Так что путь «сообщение → вопрос» в коде отсутствовал физически.

**(c) Но спека под него место уже отвела, и оно свободно.** `QuestionOrigin.MESSAGE` есть в enum
(`packages/domain/models/enums.py`), а CHECK `questions.origin` построен из
enum (`migrations/versions/0002_core.py`, `_in_check(QuestionOrigin)`) — т.е. значение допущено
схемой; §5.3 в источниках кандидатов перечисляет «сообщения человека», §5.3.2 — «seeded/message
question → FIFO selection»; `FIFOQuestionSelector.is_eligible` уже пропускает origin `message`.
Продюсера у значения не было ни одного. **Вывод: приём оператора = первый продюсер существующего
origin**, а не новый вид строки: миграций нет, enum не расширяется, payload'ы и пины не тронуты,
селектор не меняется. Именно поэтому критерий остановки не сработал; он бы сработал на любом из
трёх вариантов: новая CHECK/колонка, новое значение `QuestionOrigin` (→ пересчёт снапшотов) или
иная семантика отбора (например «операторские вопросы всегда раньше» вне priority).

**(d) Состояния и приоритеты.** `QuestionState`: candidate → selected → researching →
partially_answered / verified / rejected / deferred; приём создаёт `candidate` (то, что читает
FIFO), дальше состояниями владеет цикл сессии. `questions.priority` — integer NOT NULL DEFAULT 0,
CHECK на него нет (это вход ранжирования, §5.3.1), поэтому bounds держит сервис приёма.
UNIQUE только по `id` → дедуп уровня приложения, осознанно (иначе model-вопросы, truncate'ящиеся
до 2000 символов, схлопывались бы с чужими формулировками).

**(e) dedup vs близкие дубликаты (T5.4/T7.9).** На приёме — точное совпадение текста после strip,
по всему реестру (любой origin/state), прецедент — сидинг `eval-run`. Семантические близкие
формулировки на приёме НЕ сливаются: за этим §9 (`packages/cognition/repetition.py`: Jaccard по
host-словесному множеству, `rephrase_threshold` и `no_progress_limit` из снапшота — в config-v12
0.6 и 2; `_INVESTIGATED_STATES` — только уже исследованные состояния) и работает на отборе: такой
кандидат получает стратегию §9 и исключение через `exclude_ids` в `list_candidates`. Два разных
вопроса («Сколько будет 6*7?» и «шесть умножить на семь») — два кандидата; решит их цикл, а не приём.

**(f) Как FIFO использует priority.** `QuestionRepository.list_candidates` =
`ORDER BY priority DESC, created_at ASC, id` среди `state='candidate'`; `FIFOQuestionSelector.select`
берёт голову списка и проверяет eligibility (§6.2 «первый eligible question»). Операторский priority
пишется в то же поле → «ВПЕРЁД очереди» = большее число без правки селектора; при равных
приоритетах действует обычный FIFO по возрасту (привилегии «потому что оператор» нет). Позиция для
оператора считается тем же запросом, который читает селектор (в окне `QUEUE_WINDOW = 500`), поэтому
показанное число не может разойтись с фактическим порядком обслуживания.

### 2. Реализация

- `packages/domain/services/question_intake.py` (новый сервис): `validate_operator_question`
  (чистая функция: strip, непустой текст, ≤ 2000 = ceiling оператора того же `MessageIn.body`,
  priority — настоящий int в [-100, 100], bool отвергается), `find_question_by_text`,
  `put_operator_question` (дедуп → `(question, created)`, реплей не меняет priority; origin фиксирован
  `message`, state — `candidate`), `queue_position`, `question_queue` (вид очереди: кандидаты в порядке
  FIFO с позициями, затем уже проработанные, к каждой — последняя сессия), `intake_error_payload`.
- `apps/web/api.py`: `QuestionIn` (bounds из сервиса); **GET `/api/v1/questions?limit=`** — открытый
  query: `{id, text, origin, state, priority, created_at, position, session:{id,state}|null}` + `count`;
  **POST `/api/v1/questions`** — Command-API семейство (тот же `X-Admin-Token`, что у команд):
  201 при создании (`position` в ответе), 200 + `replayed=true` при повторе (тот же id, дубликата нет),
  400 `{error:"invalid_question", detail}` при отказе валидации, 401 без/с неверным токеном,
  423 с идентичным командам телом `host_not_healthy` на деградированном хосте. Свободный текст не
  парсится как команда: closed-enum `operator_command` в пути не участвует (§13.2).
- Главная страница (`_MAIN_HTML`): карточка «Задать вопрос» (textarea, number priority, поле admin
  токена) + таблица очереди с автообновлением; токен после первой отправки хранится в
  `sessionStorage` браузера (в UI нет серверальной сессии). Никаких новых JS-зависимостей: тот же
  vanilla `fetch`, что на страницах knowledge/metrics/evaluation.
- `hostctl/cli.py::ask TEXT [--priority N]`: печатает id, origin/state/priority и позицию в очереди;
  exit 2 при отказе валидации и без `NOEZEMA_DATABASE_URL`; опций `--origin/--state` нет (provenance
  не выбирается оператором, §3.4). Сессий не запускает: вопрос берёт следующий wake tick (или
  `wake-tick`/web wake_now).
- Аудит: нового `AuditEventType` не появилось (закрытый реестр; типы `MESSAGE_CREATED` и
  `OPERATOR_COMMAND_RECEIVED` в enum есть, но не производятся ни одним продюсером — поднимать их
  отдельная задача). Долговременная запись приёма — сама строка `questions`.

### 3. Тесты (новые)

- `tests/unit/test_question_intake.py` (unit) — bounds точно (`MAX`, ±1), strip/whitespace, не-строка,
  bool-priority, вне диапазона; guard «operator question FIFO-eligible без правки селектора».
- `tests/scenario/test_operator_question_intake.py` (scenario) — candidate с origin `message`; реплей
  по точному тексту (другие обрамляющие пробелы и другой priority → id тот же, priority не изменился,
  строка одна); реплей против засеянного корпуса; позиция FIFO и «priority ставит вперёд»; ties по
  возрасту; вид очереди с аннотацией сессии.
- `tests/scenario/test_web_questions.py` (scenario) — 401 без/с неверным токеном (и что очередь не
  изменилась), 201+очередь, реплей (тот же id, `count` тот же), GET открыт на деградированном хосте +
  POST 423 с телом `host_not_healthy`, 400 (whitespace-only) и 422 (priority вне диапазона, неизвестное
  поле `origin`), маркеры формы/таблицы в HTML главной страницы.
- `tests/scenario/test_cli_ask.py` (scenario) — CliRunner: id+позиция в выводе, строка БД с
  origin/state/priority и FIFO-головой, реплей тем же id, «ВПЕРЁД FIFO» относительно засеянного
  кандидата, exit 2 на пустом тексте/вне диапазона/без URL, отсутствие `--origin`.

Ни один существующий тест не изменён и не ослаблен; новых wall-clock тестов нет (в `timing`-пул
ничего не добавлялось, AGENTS §7). Полная проверка §6 перед этим коммитом: ruff чисто, mypy strict —
**131 файл** (новое покрытие сервисом), pytest `-n auto -m "not timing"` **1137 passed, 12 skipped**
(baseline 1094 + 43 новых) и затем `-m timing` **4 passed**.

### 4. Не изменено / остаётся

`ARCHITECTURE.md`, enum'ы (`QuestionOrigin`/`QuestionState`/`AuditEventType`), миграции (ни одной),
`config-v*` payload'ы, промпты и пины, `tool_schema_hash`, пороги, дефолтный web bind
(`127.0.0.1:8321`) и default executor (stub), staging-путь модели, lifecycle сообщений (§13.6),
остальные CLI-команды. Матрицу §22.1 новая возможность не меняет (новый пункт в список критериев
спеки не добавляем); покрытие пункта 20 «FIFO полный минимальный путь» получило продюсера кандидата
от оператора — `test_cli_ask.py` + `test_web_questions.py`.

Остаётся непроверенным после этой части: приём на живой ВМ стенда и прохождение вопроса через
wake tick с реальным LLM (часть (б) — ниже), близкие формулировки (см. п. 1(e)), позиция глубже
`QUEUE_WINDOW` сообщается как `None`.

## T7.59(б) — пакет dev-стенда `deploy/dev-stand/`: bootstrap, dev-юниты, status/reset, web bind из env и fail-closed

### 1. Что собрано

| файл | назначение |
|---|---|
| `deploy/dev-stand/bootstrap.sh` | идемпотентная установка стенда (7 шагов), флаги `--dry-run`, `--no-docker-install`, `--no-units`, `--stub-executor`, `--force`, `--web-host/--web-port/--user` |
| `deploy/dev-stand/systemd/*` (8 файлов) | `noezema-dev.target`, `noezema-dev-web.service`, `noezema-dev-tick.service/.timer`, `noezema-dev-maint.service/.timer`, `noezema-dev-unit-state.service/.timer` — **свои dev-юниты, не копии `infra/systemd/*`** |
| `deploy/dev-stand/status.sh` | юниты и периоды, docker (Postgres, образ, число сессионных контейнеров), очередь вопросов и последняя сессия из БД, wake-книга, доступность LLM через `/v1/models`, версия кода, режим исполнителя; флаги `--no-llm`, `--no-web` |
| `deploy/dev-stand/reset-db.sh` | пересоздание dev-базы с подтверждением вписыванием имени базы (или `--yes`), миграции, повторная активация config-v12; отказ при незавершённой сессии |
| `deploy/dev-stand/README.md` | деплой, цикл пользования (UI → вопрос → wake now → лента → стоп → reset), пути и порты, логи, безопасность, границы |

Целевая ВМ — 192.168.1.92 (Ubuntu 24.04, один пользователь, без GPU): разворачивает менеджер по ssh;
агент пакет готовил и проверял только локально, к `.92` (как и к `.42`/`.48`) не обращался.

### 2. bootstrap.sh: порядок шагов и почему именно такой

1. пакеты: python ≥ 3.11 (probe `python3.12/3.11/3`), docker при отсутствии (`apt-get install -y docker.io`
   + `systemctl enable --now docker`; `--no-docker-install` — не ставить), uv (`apt-get install -y uv`,
   fallback — официальный установщик в `/usr/local/bin`); при `NOEZEMA_TOOL_EXECUTOR=sandbox` пользователь
   стенда добавляется в группу `docker`.
2. venv + зависимости **только через uv** (`uv venv` затем `uv pip install --python .venv/bin/python -e .`,
   prod-extras без dev) — как AGENTS §6; существующий venv не пересоздаётся без `--force`.
3. env-файл `/etc/noezema/dev.env` (0600, владелец — пользователь стенда): секреты генерируются **до**
   Postgres, иначе повторный запуск не совпал бы с паролем уже созданного контейнера; существующий
   `NOEZEMA_ADMIN_TOKEN` сохраняется (ротация — только `--force`). Ключи: `DATABASE_URL`, `DB_PASSWORD`,
   `DATA_ROOT`, `HOST_LIB`, `UNIT_STATE`, `NODE_OWNER`, `ADMIN_TOKEN`, `LLM_BASE_URL/MODEL/SCHEMA_PROFILE/
   MAX_OUTPUT_TOKENS/TIMEOUT_SECONDS` (EXL3 192.168.1.42:8080, модель `qwen38-exl3-3bpw-128k`, профиль
   `none`, 8192/600), `TOOL_EXECUTOR=sandbox`, `SANDBOX_IMAGE/ENGINE/WORK_ROOT`, `WEB_HOST/PORT`.
4. каталоги данных `/var/lib/noezema-dev` (+ `sandbox`, `host`, владелец — пользователь стенда) и сборка
   образа из `sandbox/Containerfile` с **одной закреплённой меткой** `noezema-sandbox:dev-stand` →
   `NOEZEMA_SANDBOX_IMAGE` (дефолт `SandboxSettings.image = noezema-sandbox:dev` и тестовый `:test` не
   тронуты; поля проверены по коду: `engine=docker`, `work_root=$DATA_ROOT/sandbox`).
5. Postgres 15 в docker: том `noezema-dev-pgdata`, healthcheck `pg_isready`, публикация **только
   `127.0.0.1:5432`**; существующий контейнер не пересоздаётся.
6. база `noezema-dev` (`createdb` если её нет) → `alembic upgrade head` → активация
   `docs/eval/config-v12-payload.json` через `hostctl activate-online --drain-wait-seconds 120`, **если**
   активный head — bootstrap/none (иначе шаг пропускается).
7. юниты: шаблоны рендерятся плейсхолдерами `@REPO@ @USER@ @GROUP@ @ENVFILE@ @DATA@ @UNITSTATE@` в
   `/etc/systemd/system`, `daemon-reload`, `enable` трёх таймеров и web-сервиса; запуск — вручную
   (`systemctl start noezema-dev.target`).

Границы, проверенные кодом скриптов, а не договорённостью: имя базы вне `noezema-dev*` (и любое
`*eval*`/`*smoke*`) — отказ; путь `/var/lib/noezema`, `/run/noezema` или их подкаталог — отказ. Секреты
не печатаются, а в `--dry-run` планируемые команды маскируются (`POSTGRES_PASSWORD=<masked>`,
`asyncpg://<creds>@…`).

### 3. Юниты стенда и поведение wake на ВМ без GPU / при пустой очереди

tick — `Type=oneshot`, `TimeoutStartSec=3600` (сессия имеет право отработать бюджет снапшота; преждевременный
kill дал бы ровно ту неопределённость коммит-границы, которую разбирает reconciliation), таймер 60 с —
заметно чаще расписания (`interval_seconds=3600`, `min_session_interval_seconds=600`), поэтому большинство
тиков печатает `wait`: эффективное время решает снапшот, а не период (§5.2.1). `maint` = два ExecStart
подряд: `reassessment-tick` затем `reconcile-tick`. Все сервисы `PartOf=noezema-dev.target`, таймеры
`WantedBy=noezema-dev.target`, target в загрузку не ставится (после reboot стенда нет, пока не запустили).

- **ВМ без GPU:** в config-v12 `wake_schedule.gpu_required = false` ⇒ шлюз GPU не срабатывает и сессии
  идут. Если значение станет `true`, тик даст `skip (gpu)` и код 0 — штатная работа шлюза, не ошибка
  (`hostctl` возвращает 78 только на ConfigError/admission-ошибках).
- **Пустая очередь:** admitted-сессия доходит до выбора вопроса, кандидата не находит и завершается
  `FAILED` с `termination_reason="no_question"` (`apps/orchestrator/orchestrator.py`); `record_session_result`
  считает это неудачей → backoff 60/120/240 (потолок 86400) → после `max_consecutive_failures=3` узел в
  `paused`, и дальнейшие тики дают `skip`. Выход — задать вопрос и снять паузу (`hostctl resume-runtime`
  или `POST /api/v1/commands {"type":"resume", …}`). Поведение закреплено тестом
  `test_dev_stand_flow.py::test_wake_with_an_empty_queue_is_a_recorded_no_question_not_an_error`: именно так
  стенд ведёт себя до первого вопроса, и это не ошибка конфигурации.
- **`noezema-dev-unit-state.timer` (5 с) обязателен:** на стенде `/var/lib/noezema-dev/host` существует,
  значит `HostStatusAdapter` считает host-протокол активным и требует свежий снимок юнитов (TTL 15 с);
  без публикации Command API и приём вопроса отвечают 423 (`unit_state_stale`), GET продолжают работать
  (T3.24/§13.1). Альтернатива — указать несуществующие `NOEZEMA_HOST_LIB`/`NOEZEMA_UNIT_STATE`
  (healthy-by-default), но тогда на стенде не действует fail-closed защита.

### 4. Web: NOEZEMA_WEB_HOST/PORT, fail-closed и находка про data root

- `apps/web/bind.py` — чистый резолвер (AGENTS §4): хост/порт из env, дефолты исторические
  (`127.0.0.1:8321`, поведение не изменилось), отказ при нецелом/вне-диапазона порте и при **не-loopback
  bind с пустым `NOEZEMA_ADMIN_TOKEN`** (текст ошибки называет оба охраняемых POST-эндпоинта). Exit — 78,
  как у конфигурационных отказов hostctl. Отказ происходит на импорте `apps/web/main.py`, поэтому покрыт
  и запуск `python -m apps.web.main`, и `uvicorn apps.web.main:app`.
- Находка локальной проверки: `build_standalone_app` строил stub-исполнитель в **хардкодном**
  `/var/lib/noezema/workspace`; под `User=<пользователь стенда>` импорт падал на `PermissionError` —
  юнит уходил в restart-loop. Исправлено тем же порядком, что у wake tick: workspace =
  `resolve_standalone_workspace(data_root_from_env())`, то есть `<NOEZEMA_DATA_ROOT>/workspace`, дефолт
  прежний; в режиме sandbox путь не используется (host-overlay берётся из `NOEZEMA_SANDBOX_WORK_ROOT`).
- Граница доступа зафиксирована в README: на стенде GET (`/api/v1/status`, `/api/v1/questions`,
  `/api/v1/messages`, `/api/v1/timeline`, страницы сессий) открыты (§13.1 — читать могут все в LAN),
  команды и `POST /api/v1/questions` требуют admin-токен (401, T3.18); наружу (WAN) стенд не выставляется.

### 5. Локальная проверка (`.87`, без реального LLM, без `.92`)

- `bash -n` — чисто; `shellcheck 0.11.0` (`-S warning`) — 0 замечаний по трём скриптам; при `-S info` был
  один SC2012 (`ls` → `find` в подсчёте юнитов), исправлено. shellcheck ставился в `.venv` через uv только
  для проверки и удалён (в `pyproject.toml` не добавлялся).
- `bootstrap.sh --dry-run` — exit 0, печатает весь план (7 шагов + резюме по-русски); в выводе нет ни
  пароля, ни токена (`POSTGRES_PASSWORD=<masked>`, `postgresql+asyncpg://<creds>@127.0.0.1:5432/noezema-dev`);
  после прогона `docker ps -a` не изменился, `/etc/noezema` не создан.
- 8 юнитов проверены `systemd-analyze verify --man=no` (systemd 255) на отрендеренных копиях с реальными
  путями/пользователем — ни одной строки вывода, то есть ни parse-ошибок, ни неизвестных ключей; проверка
  плейсхолдеров (`grep @…@`) чистая.
- Startup формы стенда: `NOEZEMA_DATA_ROOT=<одноразовый каталог> NOEZEMA_TOOL_EXECUTOR=stub` импорт
  `apps.web.main` завершился успешно от обычного пользователя, создал `<data root>/workspace` и показал
  bind `127.0.0.1:8321`; временный каталог удалён.
- Fail-closed руками: `NOEZEMA_WEB_HOST=0.0.0.0 NOEZEMA_ADMIN_TOKEN=` → exit 78 + текст про открытые GET и
  охраняемые POST; `NOEZEMA_WEB_HOST=192.168.1.50` без токена → exit 78; `NOEZEMA_WEB_PORT=70000` →
  exit 78 (`is outside 1..65535`). Ничего не слушало 8321, процессов не осталось.
- Сквозной прогон на scratch-БД (фикстура `migrated_db`, FakeLLM вместо модели, stub-исполнитель):
  `tests/scenario/test_dev_stand_flow.py` — **2 passed**: вопрос через `POST /api/v1/questions` (priority 9,
  позиция 1) → `GET /api/v1/questions` → команда `wake_now` (202 completed) → сессия наблюдалась в
  `/api/v1/status` (`session` ≠ None до возврата узла в idle), `counts.sessions == 1`, в ленте
  `session_started` и `session_committed`, вопрос после коммита `state=verified`, позиция снята, к строке
  очереди привязана сессия; второй тест — пустая очередь ⇒ `termination_reason="no_question"`,
  `consecutive_failures ≥ 1`, приём вопроса после этого работает (позиция 1). Одноразовые БД удалены
  фикстурой, контейнеров и процессов не осталось.

### 6. Что НЕ проверено до реального развёртывания (список рисков)

1. apt-путь на конкретной сборке 24.04: `docker.io`, наличие пакета `uv` в репозитории (fallback —
   официальный установщик — локально не проверялся), поведение при отсутствии `curl`.
2. Юниты под живым systemd: проверен только синтаксис; фактический порядок запуска (после `docker.service`,
   пока Postgres-контейнер не healthy), `Restart=always` при отказе БД, остановка target'ом — нет.
3. Доступ `User=` юнита к `/var/run/docker.sock` после `usermod -aG docker` без перелогина (юниту группа
   видна, интерактивной сессии — нет): проверится только на ВМ.
4. Реальный LLM (`192.168.1.42`, профиль `none`, 8192 выходных токенов, 600 с) и длина reasoning у этой
   модели — локально весь путь шёл через FakeLLM; обращение к `.42` запрещалось задачей.
5. Sandbox-сессии под пользователем юнита: overlay в `NOEZEMA_SANDBOX_WORK_ROOT`, лимиты
   `sandbox/policy/<profile>.yaml`, отказ preflight при отсутствующем образе (exit 78) — на ВМ.
6. Время и объём первой настоящей сессии, disk_quota 1024 MiB из снапшота, поведение при нехватке места.
7. Первый запуск Postgres: скачивание образа `postgres:15` (таймаут ожидания healthy — 90 с в скрипте).
8. `activate-online` на живой базе с ненулевым drain-окном (локально шаг наблюдался только в сухом прогоне;
   активный head стенда был bootstrap ⇒ активация обязательна).
9. Перезагрузка ВМ: target в загрузку не ставится — после reboot стенда нет, это осознанное решение, но оно
   означает «никто не поднимает стенд сам».
10. admin-токен в `sessionStorage` браузера и открытые GET: LAN-чтение принято как компромисс стенда;
    выход стенда в WAN документом запрещён.

### 7. Не изменено / остаётся

`infra/systemd/*` (прод-юниты не тронуты и не копировались), дефолтный web bind `127.0.0.1:8321`,
дефолтный исполнитель (stub), существующие CLI-команды, enum'ы, миграции (ни одной), `config-v*`
payload'ы, промпты и пины, `tool_schema_hash`, пороги, корпуса, staging-путь модели. Матрица §22.1 не
расширяется; тесты части (б) — дополнительные продюсеры пункта 20 («FIFO полный минимальный путь»):
`test_dev_stand_flow.py` кладёт кандидата оператором и ведёт его через командный wake до коммита.

Проверка §6 перед этим коммитом: ruff чисто, mypy strict — **132 файла**, `-n auto -m "not timing"` и
`-m timing` — см. числа в отчёте (новые тесты: `tests/unit/test_web_bind.py` 20, `tests/scenario/test_dev_stand_flow.py` 2).

## T7.59(в) — дефекты, найденные при развёртывании dev-стенда на 192.168.1.92

Три независимых дефекта, найденных менеджером на живой ВМ; исправлены тремя отдельными коммитами
(каждый revert-able независимо). Тесты: локально, fake-LLM и подставные команды; `.92`/`.168` и
`.42`/`.48` не трогались, реальных сессий с живым LLM не запускалось.

### Коммит 1 — web: «wake now» на standalone-входе, node_state из БД, workspace ручного входа

**Дефект 1 (web, `apps/web/api.py`).** `build_standalone_app()` — вход, под которым живёт и стенд
(`python -m apps.web.main`), и прод-веб: сначала создаётся приложение (оно владеет engine и
session factory), затем на этой factory строится оркестратор и присваивается
`app.state.orchestrator`. Обработчик `POST /api/v1/commands` читал оркестратор из **замыкания**
аргумента `create_app(orchestrator=…)`, который на этом пути `None` ⇒ каждая команда `wake_now`
отвечала `rejected: {"reason": "orchestrator not attached"}`, сессия не начиналась. Тесты этого не
видели, потому что все они передавали оркестратор аргументом в `create_app`.
Фикс: команда берёт оркестратор из `app.state.orchestrator` **в момент выполнения**
(`_command_orchestrator()`); семантика `create_app(orchestrator=…)` не изменена — она по-прежнему
заполняет `app.state.orchestrator`, просто присвоение, сделанное после `create_app`, теперь тоже
виден). Текст отказа
`orchestrator not attached` сохранён (его проверяет существующий тест).

**Дефект 2 (web, sticky node state).** Веб читал состояние узла из `system_constants.node_state`
один раз в lifespan и держал его в памяти. Если веб стартовал, пока внешний `hostctl wake-tick`
держит сессию, копия навсегда оставалась `session_running`: «wake now» отвечала «a session is already
running», а `/api/v1/status` показывал состояние, которого в БД давно нет.
Фикс: источник истины — БД. Состояние читается из `system_constants` на каждую команду
(`PAUSE`/`RESUME`/`WAKE_NOW`) и на каждый `GET /api/v1/status`; в памяти остаётся только то, чем
владеет сам веб (`node.session_task`). Чистая часть вынесена в `effective_node_state(raw,
web_owns_session, db_has_nonterminal_session)` (AGENTS §4): `idle`/`paused` берутся как есть;
`session_running` сохраняется, если за ним реально стоит сессия — незавершённая строка `sessions`
или задача этого веб-процесса; остаточный маркер (ни того, ни другого) разрешается в `idle`, чтобы
сдохший тик не клинил узел навсегда. Инвариант сохранён и закреплён тестом: пока внешний тик держит
сессию (`session_running` + незавершённая сессия), `wake_now` отвергается; после её терминации —
принимается тем же процессом без перезапуска. `/api/v1/status` честен: поле `node_state` — сырое
значение из БД, плюс новый булев `node_state_stale_marker` (маркер есть, сессии за ним нет).
Остаточная гонка осознанна и не изменена: тик пишет маркер до создания своей строки сессии
(`hostctl/cli.py`), поэтому в узком окне между этими двумя записями wake возможен — ровно та же
экспозиция, что была до T7.59(в); полная защита — единая транзакция «маркер + строка сессии» на
стороне тика, это отдельная задача.

**Дефект 3 (orchestrator, `apps/orchestrator/main.py::_run`).** Ручной вход `python -m
apps.orchestrator` хардкодил `/var/lib/noezema/workspace`, а artifact store — рядом с ним; на стенде
(`User=<user>`, `NOEZEMA_DATA_ROOT=/var/lib/noezema-dev`) это PermissionError до начала сессии.
Фикс: корень берётся из env тем же способом, что и у wake tick — новый общий хелпер
`apps/orchestrator/scheduler.workspace_root_from_env()` = `<NOEZEMA_DATA_ROOT>/workspace`
(постоянная `WORKSPACE_SUBDIR`; `apps/web/bind.resolve_standalone_workspace` теперь использует ту же
постоянную, `hostctl wake-tick`/`run-sessions` — тоже, литералы «workspace» в трёх местах сведены к
одному). Артефакты остаются sibling'ом workspace ⇒ `<NOEZEMA_DATA_ROOT>/artifacts`. Env не задан →
прежний `/var/lib/noezema/workspace`, поведение прежнее.

**Новые тесты.** `tests/scenario/test_web_standalone_wake.py` (1) — путь самого стенда: env →
`build_standalone_app()` → lifespan → `POST /api/v1/commands {"type":"wake_now"}` против scratch-БД с
FakeLLM и stub-исполнителем; требует «не orchestrator not attached», прохождение admission, полный
сессионный цикл и то, что workspace вырос из `NOEZEMA_DATA_ROOT`.
`tests/unit/test_web_node_state.py` (4) — чистая `effective_node_state` на всех четырёх комбинациях.
`tests/scenario/test_web_node_state_db_truth.py` (3) — состояние пишет **второе** подключение (как
другой процесс): (а+б) веб стартует при внешнем `session_running` + незавершённой сессии → wake
отвергнут; после терминации и `idle` в БД → принят тем же приложением; (в) `/api/v1/status` отражает
БД (`paused`, затем `idle`) и отказ по состояния из БД; остаточный маркер показан честно
(`node_state_stale_marker: true`) и не клинит узел.
`tests/unit/test_orchestrator_entry_workspace.py` (5) — workspace/артефакты из env, дефолт не
изменён, `_run` реально вызывает хелпер (env задан/не задан), артефакты в том же data root.
Существующие тесты (`test_web_api.py`, `test_dev_stand_flow.py`, `test_wake_scheduler.py`,
`test_web_bind.py`) не ослаблены и не изменены.

### Коммит 2 — config-v13: окно EXL3 131072 вместо выдуманного 262144; стенд активирует v13

**Зачем.** Стенд говорит с `qwen38-exl3-3bpw-128k` (EXL3, физическое окно 131072), а `config-v12`
объявляет `model.context_window = model.backend_context_limit = 262144`. Планировщик контекста берёт
бюджет из снапшота (`packages/cognition/tokenizer.py`: `input_budget = min(context_window,
backend_context_limit) − max_output_tokens − safety_margin_tokens`), то есть узел планирует упаковку
до 251904 оценочных токенов — вдвое больше окна движка. Это уже наблюдалось: SMOKE-V14B (замер в
STATUS выше) зафиксировал `packing-бюджет 251904 > физического окна EXL3 (131072)`; в том прогоне
риска не было (max input 30617), но на стенде с реальными длинными сессиями это источник отказов
`finish_reason=length`/перерасхода. Новый снапшот — `docs/eval/config-v13-payload.json`.

**Что изменено.** Ровно два поля: `model.context_window` и его зеркало `model.backend_context_limit`
→ 131072. `max_output_tokens` остаётся 8192 (это верхняя граница вывода шлюза, к окну отношения не
имеет), `safety_margin_tokens` — 2048. Всё остальное побайтово равно v12: промпты и их пины
(explorer-v5/curator-v7), sampling, structured_output, `token_budgets` (Σ = 26624, те же восемь
секций), wake_schedule, policy, session/activation limits, claim_type_rules, verification, extraction,
embeddings, curiosity, repetition, research_proxy, schema_version. Формат файла — тот же, что у всех
замороженных payload'ов (`indent=2`, сортировка ключей, ensure_ascii=false, перевод строки в конце);
v12 не переписан.

**Хеши (посчитаны, не переписаны из отчёта) и бюджеты.**
- v12: file `493970d8ac845e3a2ca559180314592b7becdfbac404d1d530bfa85b6364057e`,
  canonical `c23005bdec51bd9182c9000cd2d59a1a33bdbd311229cc52df58d608f3447db0` (не изменились);
- v13: file `fe931c15a34fe71e670c29a2aacc6e63ba3defd54b5db88923201f6342e1a0ed`,
  canonical `0260fcd2f79035e634d49fe8304e44a3784b63dc0a81566687cbe52aa7f94ce0`;
- `input_budget(v13) = min(131072, 131072) − 8192 − 2048 = 120832` против `Σ token_budgets = 26624`
  (запас ×4,5); `TokenBudgets.from_snapshot(v13.model, v13.token_budgets).validate() == []`; та же
  проверка, что делает активация fail-closed (`_validate_payload_budgets`, §5.4.1); промпты
  резолвятся (`resolve_prompts(v13["prompts"], REPO_ROOT)` → explorer-v5/curator-v7).
  Правило «`context_window` обязан равняться `backend_context_limit`» в коде отсутствует: отдельной
  связи нет, считается минимум двух значений (поэтому зеркало изменено тоже — иначе смысл окна
  сохранился бы в большем из двух).

**Тесты.** `tests/unit/test_freeze_payloads.py`: `test_config_v13_differs_from_v12_only_in_the_context_window_pair`
(отличие ровно в этих двух полях, всё остальное секция-за-секцией равно v12, бюджеты пересчитаны и
валидны), `test_config_v13_file_is_byte_stable_and_pinned` (форма файла + закреплённые file/canonical
хеши), `test_config_v12_bytes_and_hashes_are_untouched` (v12 не переписан: байты стабильны при той же
сериализации, хеши совпадают с записанными в отчётах смоук-серии).

**`BOOTSTRAP_PAYLOAD` не тронут — и не должен быть.** Это не «ещё один config-файл», а код, из
которого migration 0001 (`migrations/versions/0001_bootstrap.py:39-40`) собирает bootstrap-снапшот и
сверяет его канонический хеш с закреплённым `BOOTSTRAP_PAYLOAD_SHA256`; миграции 0005/0009/0014/0016/0017
додписывают к нему секции. Любая правка `model` там изменила бы идентичность bootstrap-снапшота во
всех freshly migrated базах (включая тестовые шаблоны и eval/smoke-базы) и ломает инвариант §3
«effective config»/пин хеша. К тому же задача про окно стенда — это задача активного снапшота, а не
начального: bootstrap-снапшот держит `context_window 32768 / max_output_tokens 4096`, его input_budget
(26624) ровно равен Σ секций, и он предназначен для «узла без активаций», а не для боёв с EXL3.

**Онлайн или оффлайн (что именно активирует v13 на живом стенде).** Смена `model` — часть payload'а
(`config_snapshots.model`), поэтому она идёт тем же путём, что и смена правил: **онлайн-активация
§8.7.2** — `.venv/bin/python -m hostctl.cli activate-online --payload docs/eval/config-v13-payload.json
--reason "…" --drain-wait-seconds N`. Оффлайн-контур (§8.7.1, `hostctl offline-rules`) требуется только
там, где runtime останавливают: он предполагает « competing knowledge writers нет» и работает через
maintenance unit с host-transition journal. На стенде reassessment/maint-таймеры работают (60 с), то
есть конкуренция за heads есть — ровно та причина, по которой §8.7.2 существует; потому bootstrap.sh и
использует activate-online. drain (§8.7.2, T7.26/ADR-0013) публикует намерение и ждёт окно между
сессиями (ноль активных сессий/admission records/unresolved attempts), затем freeze cohort → prepare
heads → seal → publish (pointer + questions в одной транзакции) → post_publish; слот активации
кворует wake на всё время, при таймауте drain намерение снимается, candidate остаётся `draft`, повтор
команды продолжает с того же состояния. Ожидаемый вывод успешной команды:
`activate-online: state=active published=True resumed=False pending=… questions=…` (exit 0); exit 1 —
drain-таймаут или pre-publish отказ.

**Процедура перехода v12 → v13 на стенде (именно так, по шагам).**
1. `cd ~/noezema && git pull` (файл `docs/eval/config-v13-payload.json` должен быть в рабочем дереве).
2. Что активно сейчас — читаем head (имя снапшота в БД = canonical-хеш payload'а, колонка
   `payload_sha256`; хеш файла в репо от него отличается, см. выше):
   ```bash
   docker exec noezema-dev-db psql -U noezema -d noezema-dev -Atc \
     "SELECT s.activation_mode, s.payload_sha256, s.model->>'context_window'
        FROM runtime_config_heads h JOIN config_snapshots s ON s.id = h.active_config_snapshot_id
       WHERE h.scope = 'global'"
   ```
   (для v12 третья колонка — `262144`). `status.sh` показывает это же одной строкой (пункт 14, коммит 3).
3. Смена на живом узле (web и tick не останавливаются):
   ```bash
   set -a; . /etc/noezema/dev.env; set +a          # креды в env, не в командной строке и не в отчёт
   .venv/bin/python -m hostctl.cli activate-online \
     --payload docs/eval/config-v13-payload.json \
     --reason "T7.59(в): EXL3 context window 131072" \
     --drain-wait-seconds 120
   ```
   Если в этот момент идёт сессия, команда ждёт окно (до 120 с) и может выйти с `activation failed: …`
   и кодом 1 — это не повреждённое состояние: повторить команду (она resume-идемпотентна) или увеличить
   `--drain-wait-seconds`. Альтернатива с остановкой runtime: `sudo systemctl stop noezema-dev.target`,
   затем `sudo env NOEZEMA_DATABASE_URL=… .venv/bin/python -m hostctl.cli offline-rules --payload
   docs/eval/config-v13-payload.json --reason "…" --host-lib /var/lib/noezema-dev/host`, затем старт.
4. Проверка: тот же SELECT из шага 2 должен дать `online | 0260fcd2… | 131072` — в колонке
   `payload_sha256` лежит canonical-хеш payload'а (`canonical_sha256(requested_payload)`,
   `packages/memory/activation.py:374`), а не хеш файла (`fe931c15…`); `GET /api/v1/status`
   (`config.snapshot_id`) и карточка «Узел» показывают новый снапшот; начатая до активации сессия
   дорабатывает на своём снапшоте (effective config фиксируется на старте сессии), новая возьмёт v13.
5. `reset-db.sh` пересоздаёт базу и активирует v13 по умолчанию (как и bootstrap).

**Стенд по умолчанию теперь на v13:** `CONFIG_PAYLOAD` в `bootstrap.sh` и `reset-db.sh` указывает на
`docs/eval/config-v13-payload.json` (`NOEZEMA_DEV_CONFIG_PAYLOAD` переопределяет), README обновлён.
`config-v12` остаётся в репо и не переписывается: на нём идут смоук-серии SMOKE-V14/V14B, и его хеш
упомянут в их отчётах.

### Коммит 3 — дефекты `deploy/dev-stand` на Ubuntu 24.04 (пункты 9–14)

**9. `uv`.** В 24.04 нет apt-пакета `uv`, а прежний фолбэкер выполнял
`curl … | sh -c 'env UV_INSTALL_DIR=/usr/local/bin …'` от имени пользователя стенда: запись в
`/usr/local/bin` отвергалась, и шаги 2+ валились с невнятной ошибкой. Теперь порядок такой: попытка
`apt-get install -y uv` (для дистрибутивов, где uv есть) → скачивание установщика в файл
(`curl -fsSL https://astral.sh/uv/install.sh -o /tmp/noezema-uv-installer.sh`) → запуск **от root**:
`sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh <файл>`; при отказе root-установки —
пользовательский `~/.local/bin` с временным добавлением в PATH. После этого наличие uv **проверяется**
(`uv --version`, `hash -r`), а не предполагается; при неудаче — понятная смерть с предложением поставить
uv вручную.

**10. Порт Postgres.** Хост может уже держать `127.0.0.1:5432` (на .92 — нативный PG15); раньше это
всплывало только как `docker run: port is already allocated` после того, как секреты были записаны.
Добавлен `resolve_db_port`, который вызывается **до** записи env-файла (URL содержит порт), порядок
приоритетов: (а) опубликованный порт существующего контейнера `noezema-dev-db` — переиспользуется,
повторный запуск не «съезжает»; (б) явный `NOEZEMA_DEV_DB_PORT` — если занят, понятная ошибка с
подсказкой `NOEZEMA_DEV_DB_PORT=5433 ./bootstrap.sh`, молчаливого переезда нет; (в) порт, записанный
ранее в env-файле (`NOEZEMA_DEV_DB_PORT`, bootstrap пишет его всегда); (г) 5432, если свободен по
`ss -ltn`; (д) первый свободный из `5433…5440` — выбранный порт печатается и попадает в env-файл.
Проверка занятия — `ss -ltn` (если `ss` нет, считаем свободным: ошибку даст docker, она явна).

**11. `--force` и секреты.** (а) `uv venv` отказывается работать по существующему каталогу без
`--clear` — теперь при `--force` вызывается `uv venv "$VENV" --python … --clear`. (б) **Решение:**
пока существуют контейнер или том Postgres, ни `--force`, ни повторный запуск **не ротируют** пароль БД
и admin-токен: пароль живёт в томе `noezema-dev-pgdata`, его «ротация» рассинхронизировала бы кластер с
`/etc/noezema/dev.env`, а ротация токена молча инвалидировала бы уже открытые UI-сессии. Явная ротация —
новый флаг `--rotate-secrets` (ротирует admin-токен; пароль БД на месте не меняется никогда, о чём
скрипт прямо пишет). Отдельный вариант «честно с нуля» — осознанное удаление кластера самим оператором
(`docker rm -f noezema-dev-db && docker volume rm noezema-dev-pgdata`); скрипт этого за `--force` не делает.
Случай «кластер есть, а пароля в env-файле нет» больше не генерирует новый пароль (он не подошёл бы
кластеру): понятный отказ с инструкцией — восстановить env-файл либо удалить том осознанно.

**12. Готовность Postgres.** Healthcheck контейнера исправлен на `pg_isready -U noezema -d postgres`
(без `-d` pg_isready спрашивает базу `noezema`, которой нет, и health-статус спамит «database … does not
exist» каждые 5 с). Ожидание переведено на **тот эндпоинт, которым пользуется приложение**: цикл
`db_endpoint_ok` поднимает TCP-соединение к опубликованному `127.0.0.1:<порт>` и выполняет `SELECT 1`
(asyncpg из venv стенда), таймаут — `NOEZEMA_DEV_DB_READY_TIMEOUT`, по умолчанию 120 с. При неудаче
печатается диагностика: состояние/health контейнера, слушатели порта (`ss -ltn`), хвост `docker logs`
(через маску секретов) и подсказка про deny-by-default ufw / чужой процесс на порту.

**13. Фаервол.** Скрипт **не меняет фаервол** — добавлена PRE-START диагностика (`firewall_diagnostics`
перед созданием контейнера, печатается и в `--dry-run`): если `ufw status` активен (читается через
`sudo -n`, чтобы не висеть на запросе пароля), выводятся требуемые правила — исходящие на docker0 в
`172.17.0.0/16`, исходящие к хосту:порту LLM, входящие на `WEB_PORT` из LAN, DNS/NTP/80-443. После
запуска контейнера тот же путь (TCP + `SELECT 1`) служит проверкой «работает ли связка», а виснет —
печатается подсказка про ufw. Команды для .92 зафиксированы в README, раздел «ВМ с deny-by-default UFW».

**14. Прочее.** `status.sh`: активный снапшот (`activation_mode/state`, `snapshot sha`, canonical
`payload_sha256` + **имя файла из `docs/eval`**, подобранное по canonical-хешу — не по хешу файла, см.
AGENTS §7), `context_window`/`max_output_tokens` из снапшота, порт БД из env-файла рядом с фактически
опубликованным, `node_state` из `system_constants` (источник истины) плюс счётчик незавершённых сессий,
и честное описание «wake now» (кнопка/команда; `systemctl start noezema-dev-tick.service` сессию не
запускает — тик выдаст `wait (interval_not_elapsed)`). Тот же текст — в итоге bootstrap (раньше там
стоял неверный «или systemctl start …»). В `reset-db.sh`: `DB_VOLUME` больше не используется неопределённой,
резолвинг порта такой же (env → env-файл → контейнер), и проверка незавершённых сессий исправлена на
`state NOT IN ('succeeded','succeeded_partial','failed','cancelled')` — прежний список
`('selected','running','committing','reconciling_commit')` не совпадал ни с одним значением enum, поэтому
guard всегда говорил «0» и никогда не останавливал опасный сброс.

**Проверки коммита 3.** `bash -n bootstrap.sh status.sh reset-db.sh` — чисто. `shellcheck -S warning`
(0.11.0, установлен временно в отдельный venv `.shellcheck-venv`, `pyproject.toml` не тронут; после
проверки каталог удалён) — 0 замечаний. Реальный `bootstrap.sh --dry-run` на .87: exit 0,
`/var/lib/noezema-dev` не создан, env-файл не тронут, креды замаскированы (`POSTGRES_PASSWORD=<masked>`,
`NOEZEMA_DATABASE_URL=postgresql+asyncpg://<creds>@…`), напечатаны выбранный порт (5432 свободен) и
напоминание про ufw. Новые тесты `tests/unit/test_dev_stand_scripts.py` (12) гоняют **настоящие скрипты**
с подставными `docker`/`ss`/`sudo`/`apt-get`/`systemctl`/`ufw`/`uv` и покрывают: dry-run ничего не меняет
и не печатает секреты; автовыбор 5433 при занятом 5432; понятная смерть при явном занятом порту;
переиспользование порта существующего контейнера (5439); отказ генерировать пароль рядом с чужим
кластером; `--force` не ротирует секреты; `--rotate-secrets` ротирует токен и честно сообщает про пароль;
plan uv-установки от root в `/usr/local/bin`; pins на healthcheck `-d postgres`, эндпоинт-проверку,
«диагностика без правок фаервола» и текст про wake now. Реальный bootstrap/reset на .87 не запускались.


## T7.61 — узел не запускает две сессии одновременно

### T7.61(а) — гонка «wake now» + запланированного тика: разбор (до правки кода)

**Симптом на стенде 192.168.1.92 (2026-10-05).** Оператор нажал «wake now»: сессия `7f6b28a7` началась в
05:26:52 и шла ~40 с. Через 36 с планировщик (`noezema-dev-tick.service`, отдельный процесс) получил
admission и запустил **вторую** сессию `fa5448d5` на том же узле и (в итоге) на том же вопросе. Инвариант
§5.2.1 «одна сессия на узел» нарушен; обе сессии прошли полный цикл до succeeded, то есть защита не
сработала ни на одном из этапов.

**Механизм гонки (по коду, в порядке исполнения).** Admission `decide` → `_admission` проверяет живую
сессию как `SELECT count(*) FROM sessions WHERE state NOT IN (<terminal>)`
(`apps/orchestrator/scheduler.py:479`, причина `REASON_NONTERMINAL_SESSION` там же:483). Строка же
запускаемой сессии создаётся внутри phase 1 (`apps/orchestrator/orchestrator.py:365-368`,
`_run_to_committing`) в транзакции, которая остаётся открытой до COMMITTING (T7.20, ADR-0009), и видима
другим соединениям только после финальной транзакции. Следовательно, в момент admission тика строка
web-сессии для этого соединения **не существует** — счётчик даёт 0, причина `nonterminal_session` не
выносится, решение = `wake`. Собственная внутренняя проверка оркестратора
(`orchestrator.py:554`, «single session at a time (M1)») читает те же невидимые rows и тоже пропускает.
Маркер `system_constants.node_state='session_running'` в admission вообще не участвует (`_admission`
реагирует только на `paused`; T5.2/T7.59(в) осознанно оставили остаточный маркер неблокирующим, иначе
убитый тик-юнит клинит узел), поэтому и как взаимное исключение он не работает — при обоих порядках
записи (веб пишет маркер до старта задачи, тик — до старта сессии) вторая точка входа проходит дальше.

**Инвентарь точек входа, запускающих `run_session` (все обязаны стоять в одном лейне).**

| Точка входа | Где | Как стартует сессию | Отношение к гонке до правки |
|---|---|---|---|
| планировщик хоста | `hostctl/cli.py::wake_tick` (`ExecStart=… -m hostctl.cli wake-tick`, `deploy/dev-stand/systemd/noezema-dev-tick.service`) | `decide(scheduled)` → маркер session_running (было:cli 286-293) → `run_session()` | участник гонки (вторая сессия) |
| «wake now» веб | `apps/web/api.py` `_apply_command`, ветка WAKE_NOW (маркер было:977, `create_task` было:987) | admission на отдельной сессии → маркер → фоновая задача | участник гонки (первая сессия) |
| ручной вход | `apps/orchestrator/main.py::_run` | `build_orchestrator` → `run_session()` без admission и без маркера | потенциальный третий участник: обходил и admit, и маркер |
| серия eval | `hostctl/cli.py::eval_run._run_sessions` (маркер было:1305) | цикл admission+retry → `run_session()` | потенциальный участник рядом с тиком на той же БД |
| тесты/смоуки поверх этих же функций | `tests/scenario/*`, eval-серии | те же пути | — |

Вне списка (sessions не запускают, правки не требуют): `hostctl` maintenance/reconcile/ask/preflight,
`apps/research_proxy`, offline rules.

**Стоп-критерий проверён до правки — он НЕ сработал.** ARCHITECTURE §5.2.1 предписывает для admission и
создания сессии блокировку строк (`runtime_config_heads → sessions`, строки 451-452), то есть node lease
таблица в смысле «отдельная сущность с TTL» здесь не требуется; §8.7.2 (строка 1211) уже описывает
нужный механизм — «DB ownership — session-level PostgreSQL advisory lock на scope; это не lease и не
fencing protocol», §12 (≈1406): «advisory lock держит не транзакция, а соединение… потеря connection
немедленно завершает скрипт». §5.9.1 п.3 уже закрепляет, что admission-гейт живёт вне короткой доменной
транзакции. Правка не затрагивает модель данных, миграции, payload config-v*, промпты, пины и
`tool_schema_hash`; новых enum/Reason в доменной модели нет (новый REASON — только хостная константа).
Значит, вариант с node lease table (потребовал бы таблицы + TTL + reconciler-политики) оставлен как
отклонённый, а реализация возможна без миграции → коммит 2 делать можно.

**Варианты и почему выбран пятый.**
- А. «Honour `node_state='session_running'` in admission». Отклонён: ломает T5.2/T7.59(в) (остаточный
  маркер от убитого юнита клинит узел до ручной чистки) и не закрывает гонку при одновременном старте
  (оба видят `idle`). Кроме того меняет поведение существующих тестов T7.59(в).
- Б. «Поднимать маркер в момент session creation внутри phase 1». Отклонён: маркер — строка
  `system_constants`, её COMMIT невозможен до конца phase 1 (та же транзакция), а отдельная запись из
  phase 1 была бы незащищённым «staging-обходом» state-machine.
- В. `SELECT … FOR UPDATE` на строке узла/вопроса через всю сессию. Отклонён: держит row lock минуты
  (§5.2.2 канонический порядок блокировок + §12 предупреждение о висящих транзакциях), конкурирует с
  финальной транзакцией и не даёт crash-safety.
- Г. Отдельная таблица node-lease с heartbeat. Отклонён: требует миграцию и reconciler-политику, а §8.7.2
  явно говорит, что для этой задачи нужен именно advisory lock, не lease (уже реализовано в offline rules).
- Д **(выбран)**. Session-level advisory lock узла на **отдельном** соединении, удерживаемый всю
  `run_session`. Ключ — `hashtext('noezema:node_session:<node_owner>')`, имя считает PostgreSQL (одинаково
  во всех процессах узла; Python `hash()` запрещён — PYTHONHASHSEED дал бы каждому юниту свой замок).
  Механизм уже.house-style: тот же паттерн у writer gate (`packages/memory/cascade.py`) и offline rules
  (`hostctl/offline_rules.py`). Замок не участвует в каноническом порядке блокировок (§5.2.2), потому что
  живёт вне транзакций (AUTOCOMMIT + NullPool). Порядок в каждой точке входа: admission → замок →
  маркер session_running → `run_session` → учёт исхода → снятие замка. Снятие идемпотентно и выполняется на
  всех выходах (нормальный конец, исключение внутри сессии, отмена задачи, fail-closed отказ) плюс
  автоматически PostgreSQL при гибели соединения — убитый тик не клинит узел.

**Что сделано (коммит «T7.61(а)»).** Новый модуль `apps/orchestrator/node_guard.py` (`NodeSessionGuard`:
`acquire()` — `pg_try_advisory_lock(hashtext(:n))` без ожидания, `release()` идемпотентный и не падает на
мёртвом бэкенде, `name`, `held`; `node_session_guard()` async-CM с исключением
`NodeSessionInProgress`; `NodeSessionGuardError` — fail-closed, если исключить невозможно). В
`scheduler.py` добавлены `REASON_SESSION_IN_PROGRESS`, `NODE_SESSION_LOCK_PREFIX`,
`node_session_lock_name()` и `WakeScheduler.record_guard_skip()` (§5.2.1 «пропуск с записью точной
причины»: аудит `wake_skipped` в той же форме, что и у admission-пропуска). Точки входа: wake-tick берёт
замок после `decide` и до маркера, занятый лейн = `wake-tick: skip (session_in_progress)` + exit 0; веб
«wake now» берёт замок до `_save_node_state`, занятый = reject `{"reason": "a session is already running",
"wake_reason": "session_in_progress"}` (та же формулировка, что и для локального случая), снятие — в
`finally` обёртки задачи сессии (покрывает исключение и отмену) и как страховка в lifespan; ручной вход
при занятом лейне печатает skip и возвращает 0 (сессию не создаёт); eval-run берёт/снимает замок на каждую
сессию серии (поведение серии, backoff и пауза — без изменений). `decide()` и список admission не изменены:
замок — дополнительная защита окна decide→start, а не замена admission.

**Тесты.** `tests/scenario/test_node_session_exclusion.py` — репродюсер гонки **сквозь реальные точки
входа**: веб-wake через `build_standalone_app()` (окно держит медленный scripted-ответ модели), тик —
реальным подпроцессом `python -m hostctl.cli wake-tick` с теми же env. До правки он красный:
`tick.stdout = "wake-tick: session -> succeeded (node_state=idle)"`, в БД 2 сессии при ожидаемой 1; после
правки — `wake-tick: skip (session_in_progress)`, exit 0, ровно одна сессия, а «wake now» afterwards
проходит. Тест отдельно фиксирует сам механизм: в момент admission тика `nonterminal == 0` (строка фазы 1
невидима). `tests/scenario/test_node_session_guard.py` (9): взаимное исключение двух движков к одной БД и
идемпотентность acquire/release; разные `node_owner` не блокируют друг друга; после
`pg_terminate_backend(pid)` держателя лейн свободен, а `release()` мёртвого guard не падает (именно это
даёт crash-safety убитого юнита); снятие при исключении внутри сессии и при отмене задачи; веб-wake
отвергается, пока замок занят у другого процесса (и не создаёт сессию); отменённая веб-сессия возвращает
лейн; wake-tick exit 0 + точная строка скипа + аудит `wake_skipped reason=session_in_progress,
source=scheduled`; ручной вход при занятом лейне → 0 без строки sessions. Стенных ассертов нет, маркером
`timing` ничего не добавлено. Существующие тесты не изменены, кроме `tests/unit/test_orchestrator_entry_workspace.py`,
где collaborator-заглушка дополнена фейком guard'а (эти тесты намеренно не подключаются к БД).

**Остаточные свойства и ограничения.** Session-level advisory lock требует одного DB-соединения на клиента:
за transaction-mode pooler (pgbouncer) он не даёт взаимного исключения — в проекте прямо соединение с
PostgreSQL везде, включая стенд (`deploy/dev-stand`), но при появлении pooler guard придётся заменить
долговременной строкой с lease. Замок не заменяет lease сессии (§5.2.3) и не является fencing токеном
(§8.7.2): он решает только «кто может **начать** сессию». `node_state` остаётся маркером для оператора,
admission-список §5.2.1 — прежний. Не проверено на реальном стенде (только локально, fake LLM): поведение
unit'а после `systemctl kill` посреди сессии (ожидаем: замок освобождён Postgres'ом), совместимость guard
с transaction pooler, и «wake now» при реально занятом GPU/диске.

**Проверки коммита T7.61(а).** `ruff check .` — чисто; `mypy packages apps hostctl` — 133 файла, ошибок нет;
образ `noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1198 passed, 12 skipped**
(до коммита было 1188: добавлены 10 новых тестов); `pytest -q -m timing` — **4 passed**. Репродюсер до
правки красный (`wake-tick: session -> succeeded`, sessions=2), после — зелёный; wall-clock тестов не
добавлено, пул `timing` не пополнен. Новые файлы: `apps/orchestrator/node_guard.py`,
`docs/adr/0025-node-session-advisory-lock.md`, `tests/scenario/test_node_session_exclusion.py`,
`tests/scenario/test_node_session_guard.py`. Миграций, изменений модели/промптов/пинов/config-v* нет.

### T7.61(б) — стенд: сессию запускает оператор; артефакты research_proxy в data root узла

**5. `apps/research_proxy/main.py` больше не хардкодит `/var/lib/noezema/artifacts`.** Единственная
строка правки была такой: `store = FilesystemArtifactStore(Path("/var/lib/noezema/artifacts"))`. Это
 production-контурный путь, а standalone-прокси на dev-стенде работает от пользователя стенда над
`/var/lib/noezema-dev`: (а) это PermissionError при старте процесса, (б) артефакты узла уезжали в чужой
контур — ровно та находка, из-за которой T7.59(в) переводил workspace на env. Теперь корень берётся из тех
же правил, что и у остальных точек входа: `apps/orchestrator/scheduler.py` получил `ARTIFACTS_SUBDIR` +
`artifacts_root_from_env()` (= `data_root_from_env() / ARTIFACTS_SUBDIR`, то есть ровно
`<NOEZEMA_DATA_ROOT>/artifacts` — sibling того же `<data root>/workspace`, из которого работают wake tick,
веб-бинд и ручной вход). Env не задан → прежний `/var/lib/noezema/artifacts`, поведение прежнее. Проверено
`tests/unit/test_research_proxy_entry_artifacts.py` (5): дефолт не изменился (`/var/lib/noezema/artifacts`);
корень следует за `NOEZEMA_DATA_ROOT`; `build_standalone_app()` (тот же путь, что `python -m
apps.research_proxy.main`) строит store именно в `<data root>/artifacts`; артефакты остаются sibling'ом
workspace и не пишутся в production-путь. Отдельно
закреплено, что сам entry-модуль импортируется только после установки data root: импорт билдит приложение,
а `FilesystemArtifactStore.__init__` создаёт каталог.

**6. Плановый тик на стенде — только по явному флагу (`--with-tick-timer`).** Дефолт изменён осознанно и в
пределах dev-стенда: раньше `step_units` включал `noezema-dev-tick.timer` всегда, то есть ВМ сама
разбуживала узел каждые 60 с (фактически — по `wake_schedule.interval` снапшота) без участия оператора.
Сейчас `bootstrap.sh`:
- включает `noezema-dev-unit-state.timer`, `noezema-dev-maint.timer` и `noezema-dev-web.service` как раньше
  (без unit-state Command API отвечает 423, без maint нечего примирять), а тик-таймер — только при
  `--with-tick-timer`;
- **никогда не отключает уже включённый таймер**: read-only `tick_timer_state()` (`systemctl is-enabled`) —
  если таймер включён, скрипт печатает состояние и команду `sudo systemctl disable --now
  noezema-dev-tick.timer` для оператора; повторный запуск без флага тоже ничего не меняет. Отключение
  plan sessions — решение оператора, не следствие переустановки;
- `status.sh` печатает вердикт: «тик-таймер: выключен (сессии — только wake now)» или
  «включён (state=…, …)» плюс строки `next=` для всех трёх таймеров (как раньше);
- README получил раздел «Как запускать сессии»: «wake now» (кнопка/`POST /api/v1/commands`) — обходит
  интервал, но не admission; плановые сессии — `bootstrap.sh --with-tick-timer` или вручную
  `systemctl enable|disable --now noezema-dev-tick.timer`; разовый запуск из CLI (`hostctl.cli wake-tick`),
  и что при занятой сессии он напечатает skip. Строка таблицы юнитов помечена: тик — единственный юнит,
  который сам запускает сессию, в загрузку без флага не ставится.

Тесты (`tests/unit/test_dev_stand_scripts.py`, +6): по умолчанию в dry-run `systemctl enable` НЕ содержит
`noezema-dev-tick.timer` (но содержит unit-state/maint/web); флаг добавляет его; уже включённый таймер
репортится и в плане нет ни одного `disable`; флаг описан в `--help`; `status.sh` (реальный скрипт со
stub'ным `systemctl`) печатает нужный вердикт в обе стороны; README-пин. Хarness дополнен `_run_status()` и
stub'ом `systemctl`, отвечающим на `is-enabled` (`STUB_TICK_TIMER_STATE`). `bash -n bootstrap.sh status.sh
reset-db.sh` — чисто; `shellcheck -S warning` (0.11.0, временно в `.shellcheck-venv`, `pyproject.toml` не
тронут; каталог удалён после проверки) — 0 замечаний. Реальный `bootstrap.sh` на .87 и тем более на .92 не
запускался: проверены только dry-run со stub'ами.

**7. Пауза после трёх «пустых» тиков: разбор (код не изменён).**

Как это получается сейчас: admitted wake доходит до выбора вопроса, кандидата нет →
`_finish(SessionState.FAILED, termination_reason="no_question")` (`apps/orchestrator/orchestrator.py`,
ветка после `_select_question_with_guard`) → `WakeScheduler.record_session_result(final_state="failed")`
увеличивает `consecutive_failures`, ставит backoff и при
`consecutive_failures >= wake_schedule.max_consecutive_failures` (config-v13: 3; backoff 60/120/240, cap
86400) пишет `node_state='paused'`, `paused_reason='consecutive_failures'`. Дальше блокируется и плановый
тик, и «wake now»: `_admission` первым условием возвращает `REASON_PAUSED` (§5.2.1: wake_now обходит
расписание, но не admission). То есть на пустом стенде после трёх тиков оператор, только что добавивший
вопрос через `ask`, получает отказ разбудить узел, пока явно не сделает `resume`.

Варианты. (А) «Пустая очередь = условие admission»: выводить `skip (empty_queue)` до создания сессии.
Отклонено: §5.2.1 — закрытый список условий *о состоянии узла и контуре* (пауза, живая сессия, unresolved
commit, слот активации, liveness, квота диска, GPU); «кандидатов нет» — условие о *содержимом знания*, а
eligibility кандидата считает селектор сессии по `curiosity`-секции снапшота (режим, score, похожие
вопросы). Хост, решающий это отдельно, получил бы вторую копию правил отбора и расхождение с тем, что
реально выберет сессия. (Б) «Считать `no_question` отсутствием работы, а не отказом»: в
`record_session_result` различать `termination_reason` — при `no_question` не увеличивать счётчик и не
ставить backoff, но оставить FAILED-сессию в ленте и аудите. (В) Новый терминальный статус/исход («нет
работы») вместо FAILED — требует изменения enum сессий и миграции; вне рамок задачи. (Г) Ослабить правило
конфигом (`max_consecutive_failures` или отдельный счётчик пустых очередей в `wake_schedule`) — это новая
версия снапшота (config-v14), а не кодовая правка: решение пользователя, к тому же лечит симптом.

Рекомендация — (Б): отказ узла и отсутствие работы — разные события, а пауза по §5.2.1 призвана защищать от
исправляемого отказа (движок, гейты, коммит), а не от того, что спрашивать пока нечего. Правка маленькая:
`record_session_result` принимает `termination_reason` и на `no_question` оставляет счётчик/backoff нетронутыми
(FAILED в лентах, аудитах и метриках сохраняется — видимость не теряется); реальные отказы по-прежнему ведут
к паузе после трёх. Что нужно проверить при реализации: что тест `tests/scenario/test_dev_stand_flow.py`
пересобирается на новое ожидание (сейчас он пинит именно автопаузу), что пауза по-прежнему достижима за реальными
отказами, и что оператор видит пустую очередь в `status.sh`/`GET /api/v1/questions` — иначе «не отказывается,
но и ничего не делает» станет тише, чем сейчас. Снимать автопаузу по-прежнему нужно явно:
`hostctl resume-runtime` или команда `resume`. Связь с пунктом 6: выключенный по умолчанию тик-таймер сам по
себе уменьшает число «пустых» прогонов, но не меняет семантику — при включённом таймере разбор остаётся
актуальным.

**Сопутствующая правка теста (найдено на полной проверке этого коммита).** Первый полный прогон
`-n auto` упал на `tests/scenario/test_web_node_state_db_truth.py::test_leftover_session_running_marker_does_not_wedge_the_node`
(`assert during_run is not None`, строка 230) и воспроизвёлось стабильно (2/2 полных профиля), тогда как
одиночный прогон файла (3×) и целенаправленный `-n 6` по трём файлам статусных тестов были зелёные. Причина
не в продукте: тест пытался *поймать одним снимком* состояние «маркер `session_running` + видна незавершённая
строка `sessions`». Phase 1 держит строку `sessions` невидимой для других connections до COMMITTING
(`apps/orchestrator/orchestrator.py`, комментарий T7.20 перед `_run_to_committing`), поэтому окно = от
COMMITTING до терминального коммита, а снимали его poll'ом раз в 0,1 с; когда профиль стал на 12 тестов длиннее,
окно начало попадать между снимками. Правка тестовая и **усиливает** пин: проверяется каждый снимок статуса,
снятый пока этот веб владеет сессией, — ни на одном маркер не должен выглядеть остаточным
(`node_state_stale_marker is False`); модельные задержки `delay_seconds = 0.8` держат сессию в phase 1, чтобы
наблюдение не зависело от длины финального окна. Проверено негативным контролем: если в `apps/web/api.py`
заменить `node.session_task = asyncio.create_task(_run_locked_session())` на `pass` (маркер есть, сессии нет),
два из трёх тестов файла краснеют — пин живой. Пул `timing` не пополнен: абсолютных длительностей тест по-прежнему не
утверждает.

**Проверки коммита T7.61(б).** `ruff check .` — чисто; `mypy packages apps hostctl` — Success, 133 файла;
образ `noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1210 passed, 12 skipped**
(после T7.61(а) было 1198: добавлены 5 тестов research_proxy и 7 тестов dev-стенда); `pytest -q -m timing` —
**4 passed**. Отдельно: `bash -n bootstrap.sh status.sh reset-db.sh` — чисто, `shellcheck -S warning` —
0 замечаний (проверка временным venv `.shellcheck-venv`, `pyproject.toml` не тронут; каталог удалён после
проверки), `tests/unit/test_dev_stand_scripts.py + test_research_proxy_entry_artifacts.py +
test_orchestrator_entry_workspace.py` — 29 passed. Пул `timing` не пополнен; wall-clock утверждений не
добавлено. Миграций, изменений модели/промптов/пинов/config-v* нет; ARCHITECTURE.md не изменён. Реальные
`.92`/`.87`, LLM-хосты `.42`/`.48`, eval/smoke-базы не трогались (только SELECT по счётчикам).

## T7.62 — флаки статусных тестов: барьеры вместо «условия для каждого снимка» (тесты+доки; продукт не тронут)

**Контекст.** CI (run 37290125308) упал на
`tests/scenario/test_web_node_state_db_truth.py::test_leftover_session_running_marker_does_not_wedge_the_node`
(`assert all(s["node_state_stale_marker"] is False for s in while_running)`; 1209 passed). На том же HEAD локальный
полный профиль `pytest -n auto -q -m "not timing"` воспроизвёл красный в **3 прогонах из 4**: run1 и run3 — та же
цель (163.5 с / 156.9 с), run2 — другой тест того же семейства гонок:
`tests/scenario/test_dev_stand_flow.py::test_asked_question_runs_a_full_session_and_leaves_the_queue`
(`assert saw_session`, «сессия должна была быть видна в /api/v1/status»), run4 зелёный (1210 passed).

**Диагноз (продукт прав; изменений продукта нет).** В `apps/web/api.py` конец сессии двухшаговый: завершается
`node.session_task` (done-callback `_reset` снимает guard и заводит **отдельную** задачу `_record_session_outcome`,
которая дописывает маркер в `idle` своей транзакцией через `WakeScheduler.record_session_result`). В окне между
завершением задачи и этой записью статус легально показывает сырой `session_running` + нет незавершённой строки
`sessions` + живая задача процесса уже не жива ⇒ `node_state_stale_marker=True`. Прежняя формулировка теста
(«every such sample») предполагала, что любой снимок цикла снят «пока веб владеет сессией» — неверно для хвостового
окна: оно миллисекундное, тихий локальный прогон в него не попадает, нагруженный раннер — да. Для команд это не
дефект: `effective_node_state` ровно эту комбинацию приводит к `idle` (`tests/unit/test_web_node_state.py`).
Второй флейк — та же семья, обратный квантор: строка `sessions` видна сторонним соединениям только в окне [commit
фазы 1 с `_transition(COMMITTING)` … терминальный коммит финальной транзакции], а `saw_session` ловился poll'ом раз
в 0,1 с — под `-n auto` снимки не попали в окно.

**Детерминированное воспроизведение (до правки).** Тест-side monkeypatch `WakeScheduler.record_session_result`
(задержка 1 с перед реальной записью итога) на неизменном коде: **2/2 красных**; оригинальный poll-цикл поймал
**18 снимков** `node_state=session_running, session=None, stale_marker=True` — ∀-утверждение падает
детерминированно. Скретч сохранён вне репозитория (`t762_scratch_repro_saved.py`), в suites не входит.

**Что изменено (только тесты).** `test_leftover_…wedge_the_node` переписан на барьерах: `_GateExecutor`
(подкласс `StubToolExecutor`) паркует сессию event-гейтом на tool-шаге внутри фазы 1 — единственный call-site
`self.executor.execute(...)` в `apps/orchestrator/orchestrator.py`, тот же event loop; все снимки «пока веб владеет
сессией» берутся в удержанном состоянии (stale=False гарантирован через `_owns_live_session`), там же проверяется
отказ повторного wake_now («a session is already running»). Завершение ждёт событий, а не poll'а: обёртка инстанса
`orchestrator.run_session` (event окончания сессии) и обёртка `WakeScheduler.record_session_result` (event закрытия
хвостового окна). Абсолютные времена — только потолки против зависания (120 с); `delay_seconds=0.8` убраны: барьер
держит фазу 1 дольше и надёжнее временной задержки. Хвостовое окно проверяется **отдельным новым тестом**
`test_tail_window_after_session_end_is_stale_but_harmless`: запись исхода удерживается гейтом, снимок обязан показать
stale_marker=True, безвредность доказывается прямым `effective_node_state(…) == "idle"`; после снятия — idle /
stale=False. В основном тесте ни один снимок в хвостовое окно не попадает (обоснование в docstring).
`test_dev_stand_flow.py`: гейт вокруг `commit_prepare` (seam между committed фазой 1 и шагом prepared-attempt) —
видимость `session.state=committing` + stale=False проверяется в удержанном окне; `_wait_idle` остался только как
потолок против зависания. Семантические пины сохранены полностью: остаточный маркер назван и не блокирует wake,
во время своей сессии маркер не stale, после — idle/`session is None`/counts прежние. Тестов стало +1 (1210→1211).

**Классификация остальных poll-паттернов (grep по tests/, не изменено).** Все перечисленные — ожидания перехода
состояния или гарантии от зависания, без ∀-утверждений по снимкам и без утверждений об окне видимости:
`test_web_standalone_wake.py` (poll до idle), `test_web_api.py` (poll до idle), `test_node_session_exclusion.py`
(ожидание model-запроса + poll до idle; проверка «строки sessions не видно» в начале фазы 1 структурно надёжна —
фаза 1 держит строку невидимой), `test_node_session_guard.py::_free_lane` (poll acquire замка),
`test_activation_drain.py::_wait_slot` (poll с monotonic-потолком); `for range(3)` в `test_wake_scheduler.py` —
итерация отказов, не опрос статуса.

**Найдённый сопутствующий flake (НЕ этого класса; не исправлял).** В двух полных прогонах (A4 покой и B3 под
нагрузкой) stage1 дал «1211 passed, 12 skipped, **1 error**»: ERROR at teardown
`tests/scenario/test_node_session_guard.py::test_web_session_returns_the_lane_when_its_task_is_cancelled` —
`DROP DATABASE noezema_mig_…`: «database is being accessed by other users» (гонка DROP фикстуры с дозавершением
отменённой веб-сессии/её соединений при нагрузке). Это не падение теста и не цель T7.62; цель в этих прогонах
зелёная. Именно такие unfixed-DROP остатки объясняют исторический прирост `noezema_mig_*` (см. «Остатки»).
Молча не перезапускался: зафиксирован как есть, имя/ошибка/нагрузка выше.

**Доказательство (после правки).** Полная двухстейджевая §6 **6 раз подряд** (09:54–10:13): `ruff check .` и
`mypy packages apps hostctl` чистые в каждой итерации; stage1 — 1211 passed, 12 skipped во всех шести (131,8–163,0 с;
в A4 дополнительно тот самый teardown-ERROR), **падений целевого теста 0/6**; stage2 — 4 passed ×6 (47,3–51,4 с).
**3 прогона под нагрузкой** (6 busy `python -c "while True: pass"`, убиты после; pgrep/ps — ноль): B1/B2 полностью
зелёные (158,9 / 133,4 с), B3 — зелёный по тестам с тем же teardown-ERROR (192,6 с); **падений цели 0/3**.
**Целевой файл отдельно ×20**: 20/20 зелёных (по ~4,7 с). Итого цель проверена без единого падения в 9 полных
параллельных профилях и 20 одиночных прогонах. Пул `timing` не пополнен, wall-clock утверждений не добавлено.

**Остатки тестовых БД.** До proof: mig=56, tpl=0, dbg-семейство=19, clismoke=2. После 9 полных + 20 одиночных
прогонов: mig=58 — ровно два новых scratch, оба остатки teardown-ERROR (A4: `noezema_mig_2c8b5091`, B3:
`noezema_mig_325fb4bf`). Свои измерительные базы удалены (DROP при нуле активных сессий), после удаления — снова
**56/0/19/2**, идентично «до». Scratch-базы создаёт только `tests/conftest.py` (+ cleanup в
`test_fixture_template_clone.py`) и роняет их в `finally`; зелёные прогоны (4 воспроизводящих, скретчи, A1–A3,
A5–A6, B1–B2) не оставили ничего — утечки mig накапливались только от teardown-гонок и убитых процессов прежних
сессий; этим же объясняется +2 к числам T7.60. Легаси `noezema_dbg*`/`noezema_clismoke*` текущими тестами не
создаются — не тронуты.

**Инварианты.** Код продукта (apps/, packages/, hostctl/), схемы, payload'ы, промпты, пины, ARCHITECTURE.md и
пороги не тронуты; eval/smoke — SELECT-счётчики; `.42`/`.48`/`.92` не использовались, реальных сессий/смоуков нет;
контейнеры: `docker ps -a` до/после идентичны (41). Ловушка закреплена в AGENTS.md §7 («тесты статуса: …»).

**Проверки коммита T7.62.** `ruff check .` — чисто; `mypy packages apps hostctl` — Success, 133 файла; образ
`noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1211 passed, 12 skipped** (132,4 с; было
1210: +1 новый тест хвостового окна); `pytest -q -m timing` — **4 passed** (47,5 с). Финальный прогон зелёный, без
teardown-ERROR. Счётчики БД после всех прогонов и удаления своих остатков: 56/0/19/2; busy-процессов нет;
`docker ps -a` — те же 41 контейнер. Миграций, изменений модели/промптов/пинов/config-v*, ARCHITECTURE.md — нет;
пул `timing` не пополнен. Реальные `.92`/`.87`, LLM-хосты `.42`/`.48`, eval/smoke-базы не трогались (SELECT only).

## T7.63 — teardown scratch-БД: «database is being accessed by other users» и утечка `noezema_mig_*`

**Симптом (унаследован от прогонов T7.62).** В полных двухстейджевых проверках под нагрузкой stage1 давал
«…passed, 12 skipped, **1 error**»: ERROR at teardown
`tests/scenario/test_node_session_guard.py::test_web_session_returns_the_lane_when_its_task_is_cancelled` —
`DROP DATABASE "noezema_mig_…"` → `database … is being accessed by other users`. Тест зелёный, красная именно
фикстура; scratch-база при этом оставалась в кластере.

### 1. Диагностика (до любой правки)

Репродукция — два независимых профиля, целевые тесты под external load (busy-процессы `while True`,
убиты по завершении batch; leftovers проверялись `pgrep -af t763_busy`). Временный плагин
`t763_dbg.py` (`PYTHONPATH=… pytest -p t763_dbg`, в репо НЕ попал) обёртывал `tests.conftest._admin_exec`:
перед DROP снимал `pg_stat_activity` этого scratch (pid, application_name, backend_type, state, xact_start,
query_start, backend_start, client_addr, client_port, wait_event, query), при отказе — таймлайн
+0/+50/+150/+400/+1000/+2000 мс и повторный plain `DROP`. Логи: `/home/denis/dsh1/t763_diag.jsonl`.

| Профиль (до правки) | busy | прогонов | красных | teardown-ERROR | утечек mig |
|---|---|---|---|---|---|
| A_target_load: целевой тест ×32 | 8 | 32 | **1** (3 %) | 1 | `noezema_mig_6364e3eb` |
| B_file_load: весь `test_node_session_guard.py` последовательно ×24 | 10 | 24 | 0 | 0 | — |
| C_group: 5 scenario-файлов `-n 6` ×10 | 8 | 10 | **5** (50 %) | 5 | 06083e53, 3f4250d7, 762f2f33, bb20f1b4, db536c19 |

**(а) «backend ещё умирает» — НЕТ.** Запись отказа A (полная): перед DROP на scratch висел
`pid 548390`, `application_name ''`, `backend_type 'client backend'`, state **active** с последним запросом
`COMMIT;` (xact_start и query_start заполнены), `client_addr 172.17.0.1:47024`. Таймлайн после отказа:
+4,29…+8,77 с — по-прежнему **1 строка**, state **idle**, запрос `COMMIT;`; повторный plain DROP через 8 с
тот же `ObjectInUseError`. Вывод: backend не умирал —  это открытое соединение с уже завершённой транзакцией.

**Кто держит соединение.** Признак по последнему запросу: `COMMIT;` при непустом `xact_start` = сессия из
sessionmaker-движка (веб-движок приложения), а не NullPool/AUTOCOMMIT-соединение гарда (у того последним
был бы `pg_try_advisory_lock/unlock`). Владелец по сокету на хосте не опознаваем: 54329 публикуется через
docker-proxy, внутренний socket принадлежит root-процессу и из `/proc/*/fd` непривилегированному наблюдателю
невиден (`ss -tnp` процесс не показал) — поэтому атрибуция делалась по SQL-подписи соединения.

Временный зонд (`zz_t763_probe.py`, 10 прогонов ×2 варианта, удалён): **сразу после**
`await gather(session_task)` в `node._resets` остаётся **1 незавершённая задача** учёта исхода (10/10);
после выхода из lifespan над scratch висят бэкэнды с `COMMIT;`; вариант, где тест сам дожидается
`node._resets`, — ноль незавершённых задач. То есть гонка «teardown раньше учёта» реальна и регулярна.

**(в) отмена внутри `NodeSessionGuard.release()` — проверена отдельно и НЕ подтверждена как протечка.**
`release()` сначала обнуляет `_conn/_engine`, а обработчики её `try` ловят `Exception`, не `BaseException`:
cancel посреди round trip пропускает `_close_quietly`, и повторный `release()` уже no-op. Замер (одноразовая
БД + pg_stat_activity/pg_locks, три варианта задержки cancel: 0 / 1 мс / 10 мс): после отмены — **ноль
бэкэндов** на базе, **ноль держателей** замка, `pg_try_advisory_lock` соперника сразу берёт лейн. SQLAlchemy/
asyncpg закрывают брошенное соединение сами, PostgreSQL снимает session-level замок с соединением. Код гарда
не изменён; факт закреплен тестом `test_lane_is_free_when_the_cancel_lands_inside_the_release`.

**Второй, независимый источник того же симптома (тестовый дефект).** В C_group красным падал и сам тест
`test_lane_is_free_after_the_holders_backend_is_terminated`:
`AssertionError: замок должен держать ровно одно соединение: [551376, 551385, 551357]` (в остальных прогонах — по 2 pid).
Причина: `pg_locks` — **кластерное** представление, в нём видны advisory-замки с тем же `hashtext`-ключом,
которые параллельные xdist-воркеры держат в СВОИХ scratch-БД. Проверено напрямую (psql): замок из
`noezema_t763_lockprobe` видим из базы `noezema` (`datname=noezema_t763_lockprobe`), с фильтром
`d.datname = current_database()` — 0 строк. Следствие: утверждение флакало под `-n auto`, и тот же список pid
мог уйти в `pg_terminate_backend`, убив соединение чужого воркера; а отказ утверждения оставлял гард
неосвобождённым → держал и лейн, и scratch-БД → teardown-ERROR + утечка (в C_group именно так: state idle,
последний запрос `SELECT pg_try_advisory_lock(hashtext($1))`, xact_start NULL).

**Классификация:** **(б)** — genuinely open connection, в двух формах: (1) брошенная на shutdown задача
`_record_session_outcome` (продуктовый порядок «…сессия → учёт исхода → снятие» не выполнялся при остановке);
(2) тестовый дефект чтения `pg_locks` без фильтра по базе. Причина (а) в замерах не встретилась ни разу.

Детерминированный репроз (1) закреплен новым тестом до правки продукта:
`test_shutdown_waits_for_the_session_outcome_record` → `FAILED … assert marker_reset.is_set()` за ~12 с, и
тот самый симптом в warnings: `UserWarning: scratch DB noezema_mig_c9025ef5 still had a connection at teardown
(pid 555920 state=idle query='COMMIT;')` — подпись соединения идентична случайной отказной записи A.

### 2. Лечение по результату диагностики

**(б-1) Продукт: остановка веб-процесса обязана дождаться собственного «учёта исхода»**
(`apps/web/api.py`, lifespan shutdown + хелпер `_await_with_ceiling`). Порядок §5.2.1
«admission → замок → маркер → сессия → учёт исхода → снятие» теперь выполняется и при shutdown:

- cancel `node.session_task` стал **дожидаемым** (`_await_with_ceiling(node.session_task)`): именно
  done-callback отменённой задачи ЗАВОДИТ отдельную задачу `_record_session_outcome`; без этого ожидания
  drained-набор был бы пуст и учёт всё равно бросался бы незавершённым;
- затем `await _await_with_ceiling(*list(node._resets))` — раньше снятия замка гарда и раньше `engine.dispose()`
  (учёт пишет через ЭТОТ движок);
- потолок `_SHUTDOWN_DRAIN_CEILING_SECONDS = 20.0` — гарантия против зависания на клиненном Postgres, а не
  ожидаемая длительность; незавершённая задача отменяется и **называется в логе**
  (`logger.warning(… "its accounting is incomplete")`) — потеря учёта видна, а не молчалива;
- `release()` гарда и `owns_engine → engine.dispose()` остались в прежнем относительном порядке, уже после учёта; идемпотентность release, порядок Admission→замок→маркер, пороги, lease/fencing — не затронуты.

Минимальность: правка только в lifespan-shutdown ветке; путь команды `wake_now`, запись маркера, admission и
учёт внутри сессии не менялись. Тест-репродуктор (красный до правки, зелёный после):
`tests/scenario/test_web_node_state_db_truth.py::test_shutdown_waits_for_the_session_outcome_record`.

**(б-1, тестовая страховка) Фикстура более не краснеет из-за чужого соединения**
(`tests/conftest.py::_drop_scratch_database`, + синхронная обёртка `_drop_scratch_database_sync`,
+ диагностический `_database_backends`). Порядок: plain `DROP DATABASE IF EXISTS` → короткое
ограниченное ожидание (≤2 с: умирающему backend хватает миллисекунд) → только потом `DROP … WITH (FORCE)`
(PostgreSQL 15; CI — `postgres:15-alpine`, локальный контейнер 15.17), ≤3 попыток, иначе RuntimeError.
Каждый форс сопровождается warning с **именем базы и перечнем оставшихся бэкэндов (pid, state, последний
запрос)** — реальная протечка остаётся названной, а не убранной молча; ни один функциональный тест от этого не
становится зелёным (все утверждения теста выполняются до teardown). Замеченные в диагностике «настоящие»
случаи теперь выглядят так: `scratch DB noezema_mig_… still had a connection at teardown (pid N state=idle
query='COMMIT;'); forcing the drop`. В proof-прогонах счётчик таких warning'ов — **0** (см. п. 6): лечится
причина, а не симптом.

**(б-2) Тест: `pg_locks` — кластерное представление, вопрос про одну базу требует фильтра по базе**
(`tests/scenario/test_node_session_guard.py::_lock_holders`): добавлено
`AND database = (SELECT oid FROM pg_database WHERE datname = current_database())`. Утверждение
«замок держит ровно одно соединение» перестало флакать под xdist и больше не может отдать
`pg_terminate_backend` pid из чужой scratch-БД. Это усиление, не ослабление (тот же `!= []` в
`test_web_wake_now_rejects_while_another_process_holds_the_lane` раньше мог случайно удовлетвориться чужим
замком).

**Гигиена освобождения гарда в тестах.** В `test_lane_is_exclusive_across_engines_and_reusable_after_release`,
`test_different_node_owners_do_not_block_each_other` и `test_lane_is_free_after_the_holders_backend_is_terminated`
held-гарды освобождаются в `finally`: падение утверждения больше не оставляет соединение, которое держит и
лейн, и scratch-БД (именно этот каскад давал «FAILED + ERROR at teardown + утечка» в C_group). Утверждения и
их порядок сохранены дословно, идемпотентный двойной `release()` остался.

**(в) Правки не потребовалось** — см. замер выше; вместо правки инвариант закреплен новым тестом
`test_lane_is_free_when_the_cancel_lands_inside_the_release` (зелёный до и после: он закрепляет факт, а не
чинит падение).

### 3. Проческа других мест DROP/teardown

Все места создания/удаления scratch-БД в тестах (grep `DROP DATABASE|CREATE DATABASE` по `tests/`):

| место | что было | статус |
|---|---|---|
| `tests/conftest.py::migrated_db` teardown | `DROP DATABASE "<dbname>"` без IF EXISTS/FORCE | исправлено (`_drop_scratch_database`) |
| `tests/conftest.py::migrated_db_template` finalizer | `DROP DATABASE IF EXISTS` (уже с UserWarning) | исправлено (тот же хелпер, sync-обёртка) |
| `tests/unit/test_fixture_template_clone.py` ×2 (`direct_name`, `name_b`) | `DROP DATABASE IF EXISTS` своими руками | исправлено (импортирован тот же хелпер) |
| `tests/scenario/test_research_proxy.py:140`, `tests/unit/test_staging_reserve.py:32` | только комментарии о том, что пул нельзя оставлять открытым | изменять нечего |

Создание движков без гарантированного `dispose()`: AST-обход всех `tests/**.py` — 22 функции-строителя
(`_make_app`, `_make_orchestrator`, `_web_app`, `_make_stand`, …) создают `create_async_engine` и возвращают
его тесту; повторный обход по всем тестам, которые этих строителей вызывают, дал **0** функций без `dispose()`.
Отдельных правок нет. Важная оговорка (зафиксирована в AGENTS §7): `AsyncEngine.dispose()` закрывает только
**idle**-соединения пула — соединение, оставленное незавершённой async-задачей, он не закрывает; этот класс
закрыт на источнике (п. 2, «б-1») и подстрахован teardown'ом (п. 2, страховка). Шаблонный finalizer уже
до T7.55 дожидается `pg_stat_activity` (≤20 с) перед запечатыванием — оставлено как есть.

### 4. Утечки scratch-БД: что найдено и что предложено

Отправная точка этого разбора (замерена до первой правки): `noezema_mig_* = 56`, `noezema_tpl_* = 0`,
`noezema_dbg* = 19`, `noezema_clismoke* = 2` — ровно то состояние, с которым закончил T7.62.

**Пара «54 → 56» из отчёта T7.62 (`noezema_mig_2c8b5091`, `noezema_mig_325fb4bf`) в кластере отсутствует**
(`SELECT datname FROM pg_database WHERE datname IN (…)` — пусто): их удалил сам T7.62 («Свои измерительные базы
удалены … снова 56/0/19/2»). То есть к началу T7.63 новых unidentified-остатков не было; причина исторического
роста ровно та, что лечится здесь: teardown-ERROR ронял DROP, база оставалась.

**Свои измерительные базы этой диагностики (удалены мною вручную, см. п. 6).** Созданы моими batch-прогонами
до правки; в PostgreSQL 15 время создания БД каталогами не хранится — возраст указан по mtime каталога данных
(`base/<oid>`, это нижняя граница: autovacuum тоже может его обновить):

| база | oid | mtime каталога (UTC) | из какого замера |
|---|---|---|---|
| `noezema_mig_6364e3eb` | 68382960 | 2026-10-05T11:08:44 | A_target_load (тот самый teardown-ERROR) |
| `noezema_mig_3f4250d7` | 68439486 | 2026-10-05T11:15:33 | C_group |
| `noezema_mig_762f2f33` | 68442186 | 2026-10-05T11:16:09 | C_group |
| `noezema_mig_db536c19` | 68466404 | 2026-10-05T11:18:55 | C_group |
| `noezema_mig_bb20f1b4` | 68471789 | 2026-10-05T11:19:39 | C_group |
| `noezema_mig_06083e53` | 68488838 | 2026-10-05T11:21:58 | C_group |

Удалены явно, поимённо, при нуле активных соединений (`DROP DATABASE IF EXISTS "<name>"`). Легаси-остатки
прежних сессий (всего в кластере 62 = 56 + свои 6) **не тронуты**; eval/smoke-базы не трогались (только SELECT).

**Предложенная санация легаси (НЕ выполнена).** Шаг 1 — безопасно посмотреть (SELECT-only):

```bash
docker exec noezema-test-db psql -U noezema -d noezema -Atc "SELECT d.datname FROM pg_database d \
  WHERE d.datname LIKE 'noezema_mig_%' AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname) \
  ORDER BY d.oid"
```

Шаг 2 — одна команда на удаление: только `noezema_mig_*`, у которых нет ни одного соединения в
`pg_stat_activity` и чей каталог данных старше 6 часов; `WITH (FORCE)` потому что соединение может появиться
между проверкой и DROP (окно осознанно, оно не шире самой гонки):

```bash
docker exec noezema-test-db sh -lc 'psql -U noezema -d noezema -Atc "SELECT d.oid, d.datname FROM pg_database d \
  WHERE d.datname LIKE '"'"'noezema_mig_%'"'"' AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)" \
  | while IFS="|" read -r oid db; do d=/var/lib/postgresql/data/base/$oid; \
      if [ -d "$d" ] && [ -z "$(find "$d" -newermt "-6 hours" 2>/dev/null)" ]; then \
        printf "DROP DATABASE IF EXISTS \"%s\" WITH (FORCE);\n" "$db"; fi; done'
```

Синтаксис конвейера проверен dry-run'ом (он печатает SQL-текст, сам ничего не удаляет: 62 кандидата на момент
проверки). `noezema_dbg*` (19), `noezema_clismoke*` (2) и всё, что содержит `eval`/`smoke`, в эту выборку не
попадают: фильтр — строго префикс `noezema_mig_`.

### 5. Доказательство после правки (те же профили, что и в замерах)

Повторы целевого теста и групп — тем же harness'ом (`t763_proof.sh`), busy-процессы `t763_busy.py` запускались
перед прогонами и убивались после (`pgrep -af t763_busy` → 0).

| профиль | прогонов | красных | teardown ERROR | forced drops | утечек mig |
|---|---|---|---|---|---|
| T_target_load — `test_web_session_returns_the_lane_when_its_task_is_cancelled`, busy=8 | 32 | **0** | **0** | 0 | 0 |
| T_guard_file_load — весь `test_node_session_guard.py` последовательно, busy=10 | 12 | 0 | 0 | 0 | 0 |
| T_group_load — 5 scenario-файлов `-n 6`, busy=8 (было 5/10 красных) | 12 | **1** | **0** | 0 | **0** |

Единственный красный в T_group_load, назван как есть:
`tests/scenario/test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease` —
`packages.domain.services.lease.LeaseLost: heartbeat refused for session …` (test_orchestrator.py:446).
Тест помечен `@pytest.mark.timing`: в §6 и CI он специально вынесен из параллельного пула именно потому, что
так флакает (AGENTS §7, замеры T7.51/T7.55/T7.56). Repro-профиль C намеренно затянул его в `-n 6` под 8
busy-процессов — это известный класс wall-clock flake под external load, а не следствие правок T7.63: в тех же
12 прогонов teardown ERROR нет ни одного (было 5), утечек scratch нет ни одной (было 5). Молча не перепрогонялся.

**Полная двухстадийная §6-проверка — 9 прогонов подряд** (`t763_full.sh`: ruff → mypy → наличие образа →
`pytest -n auto -q -m "not timing"` → `pytest -q -m timing`), 6 в покое и 3 под нагрузкой (busy=8):

| # | профиль | ruff / mypy / образ | stage1 | stage2 | teardown ERROR | forced drops | mig до/после | tpl |
|---|---|---|---|---|---|---|---|---|
| F_quiet_1 | покой | OK / OK / OK | 1213 passed, 12 skipped (135,5 с) | 4 passed (47,4 с) | 0 | 0 | 56 / 56 | 0 |
| F_quiet_2 | покой | OK / OK / OK | 1213 passed, 12 skipped (129,6 с) | 4 passed (47,4 с) | 0 | 0 | 56 / 56 | 0 |
| F_quiet_3 | покой | OK / OK / OK | 1213 passed, 12 skipped (164,2 с) | 4 passed (47,5 с) | 0 | 0 | 56 / 56 | 0 |
| F_quiet_4 | покой | OK / OK / OK | 1213 passed, 12 skipped (136,2 с) | 4 passed (47,4 с) | 0 | 0 | 56 / 56 | 0 |
| F_quiet_5 | покой | OK / OK / OK | 1213 passed, 12 skipped (135,8 с) | 4 passed (47,7 с) | 0 | 0 | 56 / 56 | 0 |
| F_quiet_6 | покой | OK / OK / OK | 1213 passed, 12 skipped (160,0 с) | 4 passed (47,4 с) | 0 | 0 | 56 / 56 | 0 |
| F_load_1 | busy=8 | OK / OK / OK | 1213 passed, 12 skipped (130,4 с) | 4 passed (47,4 с) | 0 | 0 | 56 / 56 | 0 |
| F_load_2 | busy=8 | OK / OK / OK | 1213 passed, 12 skipped (161,3 с) | 4 passed (47,3 с) | 0 | 0 | 56 / 56 | 0 |
| F_load_3 | busy=8 | OK / OK / OK | 1213 passed, 12 skipped (116,4 с) | 4 passed (49,7 с) | 0 | 0 | 56 / 56 | 0 |

Ни одного «1 error» при teardown; ни одного блока `warnings summary` в stage1 — то есть **путь
`WITH (FORCE)` не понадобился ни в одном прогоне**: страховка осталась, но лечена причина. Счётчик
`noezema_mig_*` идентичен до и после каждого из девяти прогонов (56), `noezema_tpl_* = 0`. Stage1 стал на два
теста длиннее прежнего (1211 → 1213) — добавлены ровно два новых теста, удалённых нет.

Целевые файлы отдельно, последовательно: `test_web_node_state_db_truth.py` + `test_node_session_guard.py` →
15 passed, счётчик scratch без изменений. Контейнеры: `docker ps -a` до начала работы и после всех прогонов —
один и тот же набор (41), busy-процессов после завершения нет.

### 6. Инварианты и риски

**Инварианты.** Правка продукта — только ветка остановки lifespan в `apps/web/api.py` (+39 строк: хелпер
`_await_with_ceiling`, логгер, два дожидания). Порядок §5.2.1 «admission → замок → маркер → сессия → учёт
исхода → снятие» сохранён и усилен (учёт теперь до снятия и до `dispose`); идемпотентность `release()`,
структура гарда, lease/fencing, пороги, admission — не тронуты. `ARCHITECTURE.md`, схемы моделей, payload'ы
config-v*, промпты, пины зависимостей, `tool_schema_hash`, миграции — без изменений (миграций не добавлено).
Пул `timing` не пополнен; новых wall-clock утверждений нет: в тесте только event-ожидания и один потолок
против зависания. Ограничение на `-n auto` для wall-clock тестов (AGENTS §7) соблюдено.

**Риски и что осталось.**
1. Потолок 20 с означает: при клиненном PostgreSQL учёт исхода всё-таки теряется — но не молча, а с warning'ом,
   называющим задачу. Долговременное решение (учёт вне event loop процесса / persistent-задача) — отдельная
   задача, здесь сознательно не делалось: правка должна была остаться минимальной.
2. `WITH (FORCE)` в фикстуре — страховка teardown'а. Она рвёт соединение принудительно, и незавершённая
   транзакция откатывается; для scratch-базы это безопасно (она всё равно выбрасывается), но warning с pid/state
   запроса обязан оставаться — иначе страховка начнёт прятать реальные протечки продукта. В зелёных профилях она
   не сработала ни разу.
3. Общая форма гонки «учёт исхода пишется отдельной задачей после отмены сессии» закрыта для shutdown
   веб-процесса. Точка входа wake tick (`apps/orchestrator/scheduler.py`) ведёт учёт синхронно в своём процессе
   и этой гонки не имеет; новый продукт-код на неё не влияет.
4. Оставшийся шлейф варианта (в): если cancel приходит внутрь `NodeSessionGuard.release()`, соединение закрывает
   не наш код, а SQLAlchemy/asyncpg по пути сборки мусора — в одноразовых замерах это давало stderr-шум
   «Task exception was never retrieved». Логично было бы закрыть явным `BaseException`-путём в гарде, но замер
   показал отсутствие утечки и незакрытых лейнов; вместо правки инвариант закреплен тестом
   `test_lane_is_free_when_the_cancel_lands_inside_the_release`.
5. Wall-clock lease-тесты под external load могут флакать (C_group run 9) — известный и задокументированный
   класс, относится к профилю прогонов, не к продукту.

**Проверки коммита T7.63.** `ruff check .` — чисто; `mypy packages apps hostctl` — Success, 133 файла; образ
`noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1213 passed, 12 skipped** (110,3 с),
**0 ERROR при teardown**, ни одного блока `warnings summary` (то есть `WITH (FORCE)` не понадобился);
`pytest -q -m timing` — **4 passed** (47,4 с). Счётчики баз после всех прогонов и удаления своих остатков:
mig 56 / tpl 0 / dbg-семейство 19 / clismoke 2 (до правок было 56; замеры подняли до 62 — свои 6 удалены).
`docker ps -a` — те же 41 контейнер; busy-процессов нет. В репо не попали временный диагностический плагин,
batch-скрипты, замерные логи и временный probe-тест (создан был один `tests/scenario/zz_t763_probe.py` — удалён
до коммита). Хосты `.42`/`.48` и стенд `.92` не использовались; eval-run, смоуки и сессии с реальным LLM не
запускались; `noezema-eval*`/`noezema-smoke*` — только SELECT.

## T7.64 — единый серверный словарь подписей и шкала надёжности; аддитивные `label`/`hint` в JSON API (M7, этап 1 из 3)

Закрыто: проект `docs/ui-simplification-design.md` раздел 5.1 («Словарь подписей») — только фундамент.
Страницы (T7.65 «главная + страница ответа», T7.66 «справка + страницы для инженера») на этом этапе
НЕ переделывались: HTML и JS в `apps/web/api.py` не тронуты. ADR-0026.

Файлы: `apps/web/labels.py` (новый, 1390 строк), `apps/web/reliability.py` (новый),
`apps/web/knowledge.py`, `apps/web/api.py` (только добавления), новый открытый `GET /api/v1/glossary`;
тесты `tests/unit/test_web_labels.py`, `tests/unit/test_web_reliability.py`,
`tests/scenario/test_web_labels_api.py`.

### 1. Анализ до правок (инвентарь того, что видно пользователю)

Словарь: **25 категорий, 230 подписей** (label ≤40 знаков, hint ≤160, action ≤120; все три поля
заполнены). Категории и значения берутся из рабочего кода, а не переписаны в тесте:

| Категория | Значений | Источник значений |
|---|---|---|
| `question_state` | 7 | `QuestionState` |
| `question_origin` | 9 | `QuestionOrigin` |
| `session_state` | 17 | `SessionState` (+ закрытая пятиступенчатая шкала `session_stage`) |
| `node_state` | 4 | веб-тройка `api.NODE_STATES` (`idle/paused/session_running`) + доменный `NodeState` (`sleeping`) |
| `epistemic_status`, `freshness_status` | 5 + 5 | `EpistemicStatus`, `FreshnessStatus` |
| `claim_type`, `claim_head_state`, `evidence_grade` | 8 + 4 + 5 | `ClaimType`, `AssessmentState` + синтезируемый `none`, `EffectiveGrade` |
| `command_type`, `command_state` | 8 + 6 | `OperatorCommandType`, `OperatorCommandState` |
| `wake_reason` | 12 | константы `apps/orchestrator/scheduler.py` (`REASON_*`, `WAIT_*`): `activation_slot_busy`, `backoff_active`, `disk_quota_exceeded`, `gpu_unavailable`, `interval_not_elapsed`, `min_interval_not_elapsed`, `nonterminal_session`, `paused`, `reassessment_backlog`, `repair_backlog`, `session_in_progress`, `unresolved_commit_attempt` |
| `command_refusal` | 9 | строки отказа Command API: `session_already_running`, `node_paused`, `orchestrator_not_attached`, `no_active_session`, `node_not_paused`, `pause_while_session_running`, `not_available_in_m1`, `host_not_healthy`, `session_lane_unavailable` (для динамической `session lane unavailable: …` — подпись по префиксу) |
| `termination_reason` | 13 | `CompleteReason` (5) + итоги фиксации как `commit_<итог>` по `FinalizeOutcome` (`committed`, `fencing_conflict`, `lease_lost`, `attempt_missing`) + закрытые литералы оркестратора/сверителя (`no_question`, `operator_abort`, `unknown_action_outcome`, `commit_boundary_error`) |
| `audit_event_type` | 78 | весь `AuditEventType` (типы ленты сессий и служебных процессов) |
| `evidence_kind`, `evidence_relation`, `assessment_evidence_role`, `dependency_kind` | 6 + 2 + 4 + 2 | соответствующие перечисления §8.7 (`role: support/counter/scope_witness/context`) |
| `barrier_status`, `reassessment_job_status`, `commit_attempt_status`, `message_state` | 5 + 5 + 4 + 6 | «Диагностика»: барьеры инвалидации, джобы переоценки, попытки фиксации, сообщения |
| `recovery_state`, `paused_reason` | 4 + 2 | константы `apps/web/host_status.py` (`RECOVERY_NONE/RETRY_WAIT/RESUME_DEGRADED/RESUME_BLOCKED`) + `{operator, consecutive_failures}` |

Критерий остановки по DB/схемам не сработал: pydantic-модели ответов в веб-слоя нет (`extra="forbid"`
только у входных `MessageIn`/`QuestionIn`/`CommandIn`, GET-эндпоинты отдают `JsonDict` без
`response_model`) — значит подписи добавляются без миграций, без изменений payload'ов и без хешей.

### 2. Расхождения дизайн ↔ ARCHITECTURE/код и чем разрешены

1. **`QuestionOrigin.invalid_assessment`** в дизайн-документе (раздел 3) пропущен. Значение закрытого
   enum существует (§8.7, происхождение вопроса из потерявшей силу оценки) — подпись придумана по смыслу
   кода: «оценка потеряла силу». Полнота проверена тестом.
2. **Состояния узла.** Дизайн говорит о тройке `idle/session_running/paused`, доменный `NodeState`
   содержит ещё `sleeping`. Подписаны оба набора; `sleeping` помечен как служебное, на экран узла не
   выводится (веб показывает `api.NODE_STATES`).
3. **`head_state = none`** — не значение `AssessmentState`, а результат COALESCE в запросе знания;
   добавлен в категорию `claim_head_state` отдельным ключом.
4. **Пятиступенчатая шкала.** В дизайне без этапа остались `created`, `stopping`, `aborting`. Принято:
   `created` → этап 1 «подготовка»; `failed/cancelled/stopping/aborting` — вне линейной шкалы
   (`index = null`) с человеческим названием исхода; успешная линия покрывает ровно 5 этапов (тест
   `test_every_session_state_maps_to_a_human_stage`).
5. **«weak = гипотеза E1–E2» против rules-v2.** Правила дают предположению максимум E1 (`supported`
   получает E2+). Комбинация `hypothesis + E3/E4` product-кодом не производится; если такая запись
   пришла, бейдж честен: «не проверено» + текст про несогласованность записи, а не «подтверждено слабо»
   (`test_weak_only_for_hypothesis_with_partial_support`).
6. **`QuestionState.rejected`** в текущем коде никем не выставляется (закрытое перечисление описывает
   жизненный цикл по §13.6). Подпись дана по формулировке спецификации; фактической надобности нет.
7. **`termination_reason` — свободное текстовое поле** (`sessions.termination_reason` пишет и сырые
   строки модели, и формулировки сверителя вроде «attempt already aborted»). Подписаны закрытые
   литералы оркестратора/сверителя и `CompleteReason`; неизвестная строка остаётся на запасном пути
   (label = сам код). Полный перевод требует сделать поле закрытым enum — это решение про модель
   данных, не про интерфейс, и оно не принято.
8. **Тексты ошибок валидации приёма вопроса** (`{"error": "invalid_question", "detail": …}`) не
   подписаны: их формулирует `packages/domain/services/question_intake.py`, а относятся они к форме на
   главной странице (T7.65). Осознанный лимит этапа.
9. **«вычислено в изолированной среде» из дизайна (раздел 3) не используется.** Профиль `sealed`
   действительно без сети, но это свойство запуска, а не поле доказательства: `describe_verification`
   говорит «выполнено вычисление (+ артефакт результата сохранён)» и никогда не утверждает
   независимость или изоляцию сверх зафиксированных в данных групп (§6 риск дизайн-документа закрыт
   честной формулировкой; тесты `test_one_source_versus_two_sources_and_independence_claims`,
   `test_quote_integrity_is_not_a_check_of_the_source_itself`).

### 3. Шкала надёжности и пороги типов (ничего не пересчитывается)

`reliability(claim_type, epistemic_status, effective_grade, *, head_state, min_grade_for_supported)`
→ `{level, label, hint, color}`. Уровень выводится ТОЛЬКО из уже посчитанной оценки (§3 «Оценка знания»,
ADR-0026); статус и grade функция не меняет.

| Тип утверждения | Порог `supported` | Что требует подсказка weak |
|---|---|---|
| `local_observation`, `computed_result`, `self_model` | E2 | наблюдение/вычисление в точной области действия |
| `external_fact`, `procedural`, `temporal_fact` | E3 | минимум два независимых источника; `temporal_fact` — ещё и as_of |
| `empirical_conjecture` | E3 | два опыта независимо полученными методами (independent_replication) |
| `formal_theorem` | E4 | формальная проверка вывода в точной области действия |

Пороги взяты из `docs/eval/config-v13-payload.json → claim_type_rules` (действующие правила, §8.7);
запасная таблица модуля `TYPE_MIN_GRADE` сверяется с payload'ом тестом
`test_fallback_thresholds_match_the_rules_payload`, а при запросе знания порог читается из
effective-снапшота (`runtime_config_heads → config_snapshots.claim_type_rules`) и передаётся функцией —
то есть экран не «помнит» свои пороги (тест `test_threshold_from_snapshot_wins_over_the_fallback` +
сценарный `test_weak_claim_names_the_threshold_of_its_type_from_the_snapshot`). Расхождений между
дизайн-документом, README («Модель доказательств»), ARCHITECTURE §8.7 и payload'ом правил не найдено:
уровни E2/E2/E2/E3/E3/E3/E3/E4 названы одинаково во всех четырёх источниках.

Сопоставление статусов: `supported → verified (green)`; `disputed → disputed (orange)`,
`refuted → refuted (red)`, `deferred → deferred (gray)` — уровня не зависят; гипотеза E1–E2 →
`weak (yellow)`; гипотеза E0/без оценки и head `pending/invalid/none` → `unverified (gray)`
(подсказка называет причину: «новой оценки ещё нет», «прежняя оценка потеряла силу»,
«оценки нет»). Строка «как проверено» (`describe_verification`) строится только из полей
доказательств и при отсутствии данных говорит «подробности проверки недоступны».

### 4. Эндпоинт → добавленные поля (существующие ключи и значения не изменены)

| Эндпоинт | Добавлено | Категория словаря |
|---|---|---|
| `GET /api/v1/status` | `node_state_label`, `node_state_hint`; в `host`: `recovery_state_label/_hint/_action` | `node_state`, `recovery_state` |
| `GET /api/v1/questions` и ответ `POST /api/v1/questions` (и replay) | `state_label/_hint`, `origin_label/_hint`, у вложенной сессии `session.state_label` | `question_state`, `question_origin`, `session_state` |
| `POST /api/v1/questions` при 423 | `reason_label/_hint/_action`, `recovery_state_label/_hint/_action` | `command_refusal` (`host_not_healthy`), `recovery_state` |
| `GET /api/v1/knowledge/claims` (строка) | `type_label`, `head_label`, `freshness_label`, `grade_label` (None, если оценки нет), `reliability{level,label,hint,color}` | `claim_type`, `claim_head_state`, `freshness_status`, `evidence_grade` |
| `GET /api/v1/knowledge/claims/{id}` | то же + `head_state` (действующая head) + `verification: [фразы]` | + `reliability.describe_verification` |
| `GET /api/v1/knowledge/claims/{id}/provenance` | `verification` с зафиксированными группами независимости | `evidence_kind`, группы источников/условий |
| `GET /api/v1/sessions/{id}` | `state_label`, `stage{index,name,of,hint}`, у каждого события `type_label` | `session_state`, `audit_event_type` |
| `GET /api/v1/timeline` | у каждого события `type_label` | `audit_event_type` |
| `POST /api/v1/commands` (и replay), 423 | `type_label`, `state_label`, в `result`: `reason_label/_hint/_action`, `wake_reason_*`, `paused_reason_*`, `recovery_state_*` | `command_type`, `command_state`, `command_refusal`, `wake_reason`, `paused_reason`, `recovery_state` |
| `GET /api/v1/glossary` (новый, открытый) | `categories{категория → значение → {label,hint,action}}`, `category_count`, `entry_count`, `stage_count` | весь словарь |

### 5. Тесты и краснота проверки полноты

- `tests/unit/test_web_labels.py` (19): полнота по всем категориям (значения из enum'ов, констант
  планировщика/host_status, `api.NODE_STATES`, строк отказа, вынутых регуляркой из исходника
  `apps/web/api.py`, итогов фиксации из `FinalizeOutcome`); тексты (длины 40/160/120, непустота, запрет
  `§`, `T7.xx`, snake_case, uuid/sha-подобных строк); запасной путь; копии словаря; пятиступенчатая
  шкала по всем `SessionState`; нормализация строк отказа и кодов допуска.
- `tests/unit/test_web_reliability.py` (18): матрица тип × E0..E4 × статус + инварианты
  (`verified ⇔ supported`; disputed/refuted/deferred от уровня не зависят; weak только hypothesis E1–E2,
  иначе unverified); подсказка weak называет отсутствие по порогу типа; цвета шкалы; равенство
  `TYPE_MIN_GRADE` payload'у правил; приоритет порога из снапшота; честность «нет данных → нет фразы».
- `tests/scenario/test_web_labels_api.py` (10): прежний контракт эндпоинтов цел (наборы ключей), новые
  поля совпадают со словарём, `verification` в детали утверждения, порог из снапшота в подсказке,
  подписи отказов команд и отказа приёма вопроса на деградировавшем узле, полнота `GET /api/v1/glossary`.
- Краснота проверки полноты доказана временными пробнами (обе отменены до коммита): добавленное в
  `QuestionOrigin` значение `probe_unlabeled` → красный
  `test_every_source_value_has_a_label`; заменённая в `apps/web/api.py` строка отказа «node is not
  paused» → новая фраза → красный `test_command_refusal_literals_from_api_code_are_labeled`; удлинённая
  подпись → красный тест текстовых лимитов. Диагностический probe-файл обрыва соединения удалён до
  коммита (утечки не нашлось: предупреждения teardown оказались следствием красных тестов, а не продукта).

### 6. Что осталось за этапом

- T7.65: главная страница «Вопросы и ответы» (одна фраза-статус вместо баннера, форма с выбором
  «Обычный/Срочно/Потом», кнопка «Запустить обработку» с объяснением), страница ответа
  `/answer/<id вопроса>` с бейджем надёжности и блоком «как получено» из 4–6 шагов, человеческие
  тексты отказов команд в разметке. Словарь и шкала для этого уже готовы и доступны через API.
- T7.66: `/help` (обзорная часть + словарь по алфавиту из `GET /api/v1/glossary` + частые вопросы),
  tooltip-подсказки, переименования в диагностике/метриках/evaluation, свёрнутые «подробно»,
  переключатель режимов.
- Не подписаны (осознанный лимит): тексты ошибок валидации приёма вопроса (сервис intake) и свободные
  строки `termination_reason` вне закрытого набора — обе вещи требуют решения про модель данных или
  про форму на главной, не про словарь.

**Проверки коммита T7.64.** `ruff check .` — чисто; `mypy packages apps hostctl` — Success, 135 файлов;
образ `noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1260 passed, 12 skipped**
(163,4 с), без единого блока `warnings summary` (то есть форсированный DROP scratch-БД не понадобился);
`pytest -q -m timing` — **4 passed** (47,4 с). Счётчиков новых wall-clock тестов нет. Новые тесты:
19 unit + 18 unit + 10 scenario = 47 (было 1213 → стало 1260, существующие тесты не изменены и не
удалены). Счётчики баз после прогонов: mig 56 / tpl 0 / dbg-семейство 19 / clismoke 2 (как до работы);
`docker ps -a` — те же 41 контейнер; leftover-процессов нет. Хосты `.42`/`.48` и стенд `.92` не
использовались, eval-run и смоуки не запускались, `noezema-eval*`/`noezema-smoke*` — только SELECT.

## T7.65 — простая главная «Вопросы и ответы», карточка ответа и её JSON-API (M7, этап 2 из 3)

Дизайн: `docs/ui-simplification-design.md` §2–4 (этап 2), подписи и бейдж — ADR-0026
(`apps/web/labels.py`, `apps/web/reliability.py`), тесты `tests/unit/test_web_answer.py`,
`tests/scenario/test_web_answer_api.py`, `tests/scenario/test_web_answer_pages.py`.

**Останова по критерию «нужны изменения модели/миграций/payload'ов» НЕ сработала:** прочитано
из существующих таблиц, изменены только `apps/web/*` и тесты. Модель БД, миграции, payload'ы
`config-v*`, промпты, rules engine, оркестратор, hostctl — не тронуты (проверено диффом).

### 1. Анализ до правок: что уже есть в API для карточки ответа и чего нет

Цепочка связей вопроса к знанию (всё уже существует, миграции не нужны):

- `sessions.question_id → questions.id` — какие сессии работали по вопросу; у сессий без
  выбранного вопроса (`termination_reason="no_question"`) `question_id IS NULL`, они в карточку
  не попадают;
- `claims.created_in_session → sessions.id` — какие выводы породила именно эта работа (в
  отличие от `evidence_links.claim_id`, который связывает вывод с доказательствами, и
  `questions.source_claim_id`, который связывает новый вопрос с родителем);
- `claim_assessment_heads (claim_id, config_snapshot_id)` — действующая ли оценка (`current`
  ⇔ `current_assessment_id` и `epistemic_status` NOT NULL, §Lifecycle) на **действующем**
  снимке правил; запрос карточки использует тот же публичный алиас
  `knowledge.EFFECTIVE_SNAPSHOT_SQL`, что и лента знания (в модуле `answer.py` ранее не было
  своей копии этого SQL — новой головы оценки никто не обходит);
- `audit_events (session_id, sequence, type, payload)` — хронология работы; внутри phase-1
  все `created_at` равны старту транзакции (§7), поэтому порядок шагов берётся **только из
  `sequence`**;
- `sessions.termination_reason` — почему работа закончилась («цель достигнута», «не удалось»).

Чего в API не было: ни одного объекта, который связывал бы вопрос с его выводами, шагами и
итогом («ответ» как таковой). Поэтому добавлен один открытый GET (по образцу
`/api/v1/questions/{id}/knowledge`):

`GET /api/v1/questions/{question_id}/answer` → `{question{…,state_label,state_hint,origin_label},
sessions[…] (свежие первыми, максимум 25), result{kind,label,hint,action,active}, claims[…],
other_claims[…], steps[{n,text}], honesty[…], work{session_id,state_label,started_at}}`;
несуществующий вопрос → 404 `question not found`. Существующие эндпоинты и поля не изменены.

- **Ответом считаются** выводы, порождённые сессиями этого вопроса (`created_in_session`) и
  имеющие голову `current` на действующем снимке. Выводы без принятой оценки (`pending`,
  `invalid`, головы нет) показываются отдельным блоком «другие выводы» и недействующими
  строками, но **не** как ответ и без зелёного бейджа (правило §Lifecycle на уровне интерфейса).
- Известное ограничение (осознанно, долговременной связи «вопрос → переиспользованный вывод»
  в схеме нет): вывод, который сессия присоединила к уже существующему (`claims.meta
  .existing_claim_id`), не приписывается этому вопросу. Это не меняет ни базы, ни ленты знания:
  карточка отвечает только за то, что породила работа по данному вопросу.

### 2. Как собираются «шаги работы» (4–6 фраз) и почему они честные

1. лента сессии, ответившей на вопрос, читается `ORDER BY sequence ASC` (не по времени);
2. действие попадает в шаги только если у него есть `action_completed` с исходом (`ok`):
   начатое без исхода (обрыв, отмена, фаза 1 без COMMIT) и `action_failed` в шаги не пишутся;
   такие действия учитываются отдельно и дают замечание «часть шагов не удалась: N»;
3. группы: `question_selected` → «взял ваш вопрос из очереди», `context_packed` →
   «подготовил память и правила для работы», выполненные инструменты (одна строка словаря
   `action_tool` на каждый инструмент реестра) с свёрткой повторов в одну фразу со счётчиком
   («выполнил вычисление по коду — 3 раза»), `claim_assessed` → «сформулировал вывод и
   подтверждения», `session_committed` → «записал итог в базу знаний одним шагом»;
4. если групп больше 6 — действия сводятся по смыслу в одну фразу
   «выполнил ещё несколько шагов работы», счёт шагов остаётся (максимум 6);
5. незнакомый инструмент никогда не называется по имени (подпись «действие без названия»),
   потому что подписи берутся из словаря, а словарь полон относительно реестра
   (`test_action_names_come_from_the_tool_registry_and_snapshots` краснеет, если в реестр
   добавили инструмент без подписи).

Вывод: ни одна фраза шагов не появляется без соответствующего записанного события. Пустая
лента (фаза 1 без COMMIT) → пустой список и текст «Рассказать пока нечего…», а не выдуманный
рассказ.

### 3. Честные замечания и слово о проверке

- «внешние источники не использовались» — если в ленте нет ни одного успешно выполненного
  `research.fetch`; конкретнее («источников извне не было: сеть закрыта») — только если это
  подтверждает снимок правил работы (`policy.capabilities.network == "none"`); без сессии
  утверждать про сеть нельзя, поэтому используется первый, более слабый вариант;
- «вывод получен вычислением» — если доказательства ответа только вычислительные
  (`evidence.evidence_type ∈ {computation, artifact}`) и связей `supports`;
- «подтверждений нет…» — строка `reliability.describe_verification([])`, а не новая фраза;
- «часть шагов не удалась: N» — по числу действий с плохим исходом.
- **Слово «проверено» имеет один источник:** бейдж надёжности (`reliability.label`, уровень
  `verified` = вывод принят правилами оценки) и заголовок строки подтверждения
  `verification_lead` («как проверено» — только при том же уровне, иначе «чем подтверждено»).
  В разметке и JS обеих новых страниц этого слова нет вовсе (тест
  `test_answer_page_never_invents_a_verification_word_of_its_own`), поэтому страница не может
  назвать проверенным то, что оценкой не принято.
- Формулировки про исполнителя (изолированная среда/подставка) не используются — та же
  договорённость, что в T7.64 п.9: карточка описывает записанное знание, а не среду.

### 4. Что делает интерфейс и где остались закрепленные тестами строки

- `/` — новая главная «Вопросы и ответы»: одной фразой состояние узла
  (`node_state_label`/`_hint`) + служебный контур (`host.recovery_state_label/_action`),
  форма вопроса с выбором срочности («Обычный» 0 / «Срочно (вперёд очереди)» 9 / «Потом» −5,
  в раскрытом «дополнительно» — числовое `ask-priority`, как раньше), пароль оператора с тем
  же хранением в `sessionStorage`, «Запустить обработку» (`wake_now`), «Возобновить»/«Пауза»,
  таблица «Мои вопросы» (вопрос · состояние · когда · «Открыть ответ») с прежней сортировкой
  очереди и прежними интервалами обновления (3000 мс статус, 5000 мс очередь).
- `/engineer` — **бывшая главная страница**, перенесена без изменений содержимого
  (`_MAIN_HTML` → `_ENGINEER_HTML`) плюс ссылка «← простой режим». Закреплённые тестами
  маркеры (`test_web_questions.py::test_main_page_carries_the_ask_form_and_the_queue_table`,
  `test_web_mvp.py::test_main_and_session_pages_render`, `test_web_knowledge.py` про
  `/knowledge` и `/diagnostics`) не ослаблены и не переписаны: у новой главной те же id
  `ask-form`/`ask-text`/`ask-priority`/`ask-token`, надзаголовок «Задать вопрос», строка
  «Очередь вопросов: …» (в форме подсказки сортировки), кнопка с id `wake-now` и reason
  `'web: wake now'`, ссылки `/knowledge` и `/diagnostics`; `/metrics` и `/evaluation` остались
  только на инженерной странице — там они и закреплены (`href="/metrics"`/`"Метрики (§16)"`).
  Это единственные изменения закреплённых тестами строк, и они нужны были ровно для одного: чтобы прежние
  проверки продолжали работать на обеих страницах, а не были удалены.
- `/answer/<id>` — карточка ответа поверх того же JSON-API (тёмная тема тех же страниц):
  заголовок-баннер с человеческим итогом (`result.label`), блок «Ответ» (выводы с бейджем,
  цвет — из `reliability.color`, строка подтверждения + `verification_lead`), «Как это
  получено» (4–6 шагов; раскрытый «подробно (для инженера)» → ссылка `/session/<id>` и лента
  событий через существующий API), «Честно об этом ответе», «Что можно сделать дальше»
  («Задать уточняющий вопрос» → `/?ask=<текст>`, «Показать все знания», «Для инженера»).
  Пока `result.active` — страницу обновляет `setTimeout(load, 2500)`; в баннере «Идёт: <этап>
  (n из 5)» (`session_stage().name/index/of`). Никаких кодов состояния, номеров задач плана,
  `§`, uuid и хешей в видимом тексте простых страниц: это проверяет
  `tests/scenario/test_web_answer_pages.py`.

### 5. Тесты (46 новых) и доказательство их красноты

- `tests/unit/test_web_answer.py` (28): порядок только по `sequence`, свёртка повторов,
  максимум 6 шагов, отброшенные/незавершённые действия → `([], 1)`, пустая лента → пустые
  шаги и ни одного выдуманного слова, незнакомый инструмент без имени, условия честных
  замечаний (в том числе запрет фразы про сеть без снимка), доменные коды никогда не
  возвращаются функциями сборки карточки (`known_label`);
- `tests/scenario/test_web_answer_api.py` (8): 404; очередь (ни сессий, ни выводов, «Ждёт
  обработки»); работа идёт (этап + `active` для автообновления); **сквозной прогон** как в
  `test_dev_stand_flow` (`POST /api/v1/questions` → `wake_now` с FakeLLM: `python.execute` →
  curator supports → commit) → «Проверено», строка подтверждения и шаги; `pending`/`invalid`
  — только в `other_claims`, без зелёного бейджа и без слова о проверке; неполные записи
  (нет ленты, нет головы оценки, нет доказательств) → 200 и честные пустые блоки.
  Краснота правила «проверено» доказывается парой: `test_current_assessment_is_the_only_source…`
  (та же карточка с принятой оценкой обязана сказать «Проверено», `verification_lead` =
  «как проверено») против `test_pending_and_invalid_conclusions_are_not_the_answer`
  (без принятой оценки — ни одной утверждающей формы); краснота полноты словаря инструментов —
  `test_new_tool_without_a_label_reddens_the_completeness_check` (в реестр добавлен инструмент
  без подписи → тест полноты выдаёт ровно его имя).
- `tests/scenario/test_web_answer_pages.py` (7): главная — человеческие элементы и ссылки; `/engineer` —
  прежние маркеры старой главной (включая `§16`/`§22.2`) и «← простой режим»; карточка ответа —
  обязательные блоки, подстановка id ровно один раз, текст при отсутствии вопроса; чистота
  текста обеих простых страниц (ни `§`, ни номеров задач, ни uuid/hex, ни кодов перечислений,
  кроме служебного значения команды `wake_now` в теле запроса) и самопроверка красноты этой
  чистоты (`test_purity_check_is_red_when_a_code_or_a_spec_mark_leaks`).
- `tests/unit/test_web_labels.py` (3 новых + расширенная полнота): новые категории словаря
  `action_tool`, `answer_step`, `answer_result`, `honesty_note`, `verification_lead`; значения
  `action_tool` берутся из `packages/policy/tools.all_tools()`, тексты — под теми же лимитами
  40/160/120 и запретом `§`/номеров задач/кодов/uuid.

Всего: `pytest -n auto -q -m "not timing"` → 1306 passed, 12 skipped (было 1260 + 12);
`pytest -q -m timing` → 4 passed. ruff и mypy strict (136 файлов) чистые.
Добавлено тестов: 43 в трёх новых файлах (`28 + 8 + 7`) и 3 в `tests/unit/test_web_labels.py`
(полнота словаря по реестру инструментов, краснота полноты при новом инструменте без подписи,
подписи всех ключей конструктора карточки). Существующие тесты не изменены и не удалены; количество
тестов выросло с 1276 до 1322. Окружение до и после работы одинаково: `docker ps -a` — 41 контейнер
(из них noezema-именованных два — `noezema-test-db` и `noezema-searxng`), scratch-БД в тестовом
кластере — mig 56 / tpl 0 / dbg-семейство 19 / clismoke 2; leftover-процессов pytest/alembic нет.
Хосты `.42`/`.48` и стенд `.92` не использовались, eval-run и смоуки не запускались, базы
`noezema-eval*`/`noezema-smoke*` не тронуты (не читались). Временем ничего не проверялось:
ожидания в новых тестах — только потолки против зависания, синхронизация по событиям узла.

### 6. Снимки дословных текстов и JSON

JSON карточки (реальные прогоны, зонд убран после снятия):

- очередь: `{"result":{"kind":"waiting","label":"Ждёт обработки",
  "hint":"Вопрос записан в очередь и ещё не взят в работу.",
  "action":"Нажмите «Запустить обработку» или дождитесь планового запуска узла.","active":false},
  "sessions":[],"claims":[],"other_claims":[],"steps":[],
  "honesty":["внешние источники не использовались"],"work":{"session_id":null,…}}`
- ответ вычислением: `result.label` «Ответ есть», `claim` `6*7 равно 42`,
  `reliability {"level":"verified","label":"Проверено","color":"green"}`,
  `verification ["выполнено вычисление: 1, артефакт результата сохранён"]`,
  `verification_lead "как проверено"`, шаги «взял ваш вопрос из очереди»,
  «подготовил память и правила для работы», «выполнил вычисление по коду»,
  «сформулировал вывод и подтверждения», «записал итог в базу знаний одним шагом»,
  honesty «источников извне не было: сеть закрыта», «вывод получен вычислением».
- работа была, ответа нет: `result {"kind":"no_answer","label":"Ответ не записан",
  "hint":"Работа по вопросу была, но действующего утверждения от неё не осталось.
  Последняя сессия: готово · цель достигнута."}`, `claims []`, вывод в `other_claims`
  (`head_state "pending"`, `reliability.label "Не проверено"`, `color gray`,
  `verification_lead "чем подтверждено"`), шаги только реально выполненные.

Видимый статический текст главной: «NOEZEMA — вопросы и ответы · Все знания · Диагностика ·
Для инженера · Узел сейчас: … · Задать вопрос · Насколько срочно (Обычный / Срочно (вперёд
очереди) / Потом) · Пароль оператора · Отправить · дополнительно · Число в очереди (обычно не
нужно) · Обработка очереди · Запустить обработку · Возобновить · Пауза · Один запуск обрабатывает
один вопрос из очереди, обычно 30–60 секунд. · Пауза останавливает плановые запуски узла,
возобновление её снимает. · Мои вопросы · Очередь вопросов: сначала срочные, затем по времени».
Карточка ответа: «NOEZEMA — ответ на вопрос · Все вопросы · Все знания · Для инженера · Ответ ·
Как это получено · подробно (для инженера) · Честно об этом ответе · Что можно сделать дальше»;
сообщения JS и запасные строки («Идёт: <этап> (n из 5)», «Рассказать пока нечего: записанных
шагов работы по этому вопросу нет.», «Замечаний нет.», «Есть и другие выводы по этому вопросу,
но они сейчас не действуют:», «такого вопроса нет») перечислены в тесте чистоты страниц.

### 6a. Ловушки этапа записаны в AGENTS.md §7

Записи (отдельным коммитом `010c374` после зелёного кодового коммита `e98094b`, с поправкой формулировки
про `artifact.create`): простые страницы говорят только серверными подписями и закреплённые маркеры прежней
главной нельзя подгонять под новую; слово о проверке имеет один источник (бейдж + `verification_lead`);
шаги — только выполненные действия по `sequence`. Сверка с кодом показала: словарь `action_tool` покрывает
объединение реестра инструментов и возможностей снапшота правил (в config-v13 есть `artifact.create`,
которого в реестре нет), поэтому нейтральная подпись «действие без названия» применяется к действию, подписи
которого в словаре нет, а не к инструменту вне реестра.

### 7. Риски и что осталось этапу 3 (T7.66)

- Карточка ответа отвечает только за выводы, порождённые работой по этому вопросу; переиспользованный
  вывод (`meta.existing_claim_id`) не приписывается — при необходимости придётся добавлять связь в
  схему (тогда это отдельная задача с миграцией).
- На новой главной пароль оператора обязателен для отправки вопроса (как и раньше через API): без
  него человек получает «нужен пароль оператора», а не ошибку формы; если потребуется режим чтения
  без пароля — это решение пользователя.
- Автообновление карточки ответа (2,5 с) работает, пока `result.active`; узел в паузе остаётся в
  состоянии «Ждёт обработки» — подсказка про «Запустить обработку» это учитывает, но самого
  объяснения паузы (`pause_until`) страница не показывает.
- Этап 3 (T7.66): инженерные `/session/<id>`, `/knowledge`, `/claim/<id>`, `/diagnostics` —
  подписи словаря вместо кодов и сырых payload'ов, лента знаний как «что известно», карточка
  утверждения с зависимостями; `GET /api/v1/questions/{id}/knowledge` остаётся источником для
  карточки ответа. (Пометка T7.66: этот этап перенесён в **T7.67**, номер T7.66 занят анализом
  внешнего доступа ниже.)

## T7.66 — внешний доступ (research proxy): анализ живой ошибки «not configured» и проект включения (M7; без кода)

Полный разбор: `docs/web-access-design.md`; проектное решение (Proposed): ADR-0027
(`docs/adr/0027-research-proxy-single-wiring-and-curated-egress-semantics.md`). Ничего не
запускалось на стенде `.92`, сеть не использовалась, код/тесты/конфиги не изменены (только
документы).

**Гипотеза случая подтверждена полностью.** Web-фабрика `apps/web/api.py::build_standalone_app`
(`:2004–2036`) вызывает конструктор `Orchestrator(...)` (`:2023–2034`) без аргумента
`research_service`, поэтому wake_now («Запустить обработку») исполняет
`_research_fetch` с `self.research_service is None` и даёт ровно наблюдаемую ошибку
«research proxy is not configured for this host» (`apps/orchestrator/orchestrator.py:1067–1072`) —
независимо от UFW и конфига. Единственный сборщик с research-сервисом — `build_orchestrator`
(`apps/orchestrator/main.py:54–62`; wiring закреплён тестом только для него,
`tests/unit/test_research_wiring.py`; web-путь тестами не покрыт — закрыто T7.68, см. раздел ниже). Wake-tick/eval-run
(`hostctl/cli.py:303` и `:1334`) сервис имеют — там случай упал бы другой ошибкой (транспорт).

Ключевые уточнения анализа: `curated-v1` = `{name}-{version}` YAML-потолка
(`packages/policy/profiles.py:44–46` + `sandbox/policy/curated.yaml`), fetch разрешён политикой,
т.к. сетевой режим профиля ≠ none (`packages/policy/engine.py:158–164`); **curated при пустом
`allowed_domains` = любой публичный http(s)-хост** (доменный гейт — только у open_lab:
`apps/research_proxy/service.py:102–107`, `modes.py:125`), а не «запрет всего»; rate-limit
`20/3600` считается только по upstream-поиску (`service.py:368–392`), прямые fetch количественно
не лимитированы; SearXNG нужен лишь поиску `search()` curated-режима (`service.py:312–362`) и
модели сегодня недоступен (в реестре нет инструмента поиска, EVAL-3-freeze.md:83); «sealed» в
описаниях — следы промпта explorer-v5 (`prompts/explorer/explorer-v5.md:5–10`), bootstrap-дефолтов
(`packages/domain/config.py:119–120,185–197`) и устаревшей строки
`docs/ui-simplification-design.md:113`, к активному снимку config-v13 отношения не имеют. Выход в
сеть при curated — процесс оркестратора на хосте (контейнер сессии всегда `network=none`,
`apps/orchestrator/tool_executors.py:102–107`).

Пробелы G1–G10, варианты A/B/C/D и рекомендация (сначала кодовый фикс wiring без изменения
payload'ов, решение по egress-политике — за оператором), план задач **T7.68** (единый wiring +
тест через `build_standalone_app`), ~~T7.69 (config-решение)~~ → закрыт решением пользователя
2026-10-06 (a) и перенумерован: номер T7.69 занят серией подбора модели (T7.69a/c/b, см. ниже),
**T7.70** (SearXNG dev-stand — принят решением (c)), **T7.71** (поиск модели `research.search` —
принят решением (c)), **T7.72** (лимиты прямых fetch — отложен) — в `docs/web-access-design.md` §4–6;
операторские команды по `.92` с rollback и вопросы пользователя — §7–8 того же документа.
Этап UI-3 перенесён из T7.66 в **T7.67** (пометка добавлена выше и в
`docs/ui-simplification-design.md`).

Проверки commit'а: ruff — OK; mypy strict — OK (136 файлов); образ `noezema-sandbox:test` — на месте;
pytest два прогона: **1306 passed + 12 skipped** (`-n auto -m "not timing"`) и **4 passed** (`-m timing`)
— совпадает с прежним эталоном. Изменены только `docs/web-access-design.md`, ADR-0027, этот раздел
STATUS.md и пометка в `docs/ui-simplification-design.md`.

## T7.68 — единый сборщик сессионного оркестратора: web «wake now» получает research proxy (M7; ADR-0027 §1, G1 закрыт). 2026-10-06

**Контекст.** Живой случай стенда `.92` (T7.66: вопрос про инфляцию, веб-запуск): политика разрешает
`research.fetch`, но каждая попытка умирала с «research proxy is not configured for this host»
(fail-closed `apps/orchestrator/orchestrator.py:1067–1072`). Причина G1: web-фабрика строила
`Orchestrator(...)` сама и не передавала `research_service`; единственный сборщик был
`build_orchestrator`. Задача = пункт 1 ADR-0027.

**Что сделано.** Новый чистый модуль `apps/orchestrator/session_assembly.py` (AGENTS §4):
`artifacts_root_for(workspace_root)` = sibling workspace'а (`parent/ARTIFACTS_SUBDIR`),
`research_service_for(factory, workspace_root)` = `ResearchProxyService(factory,
FilesystemArtifactStore(artifacts_root_for(...)))`, `build_session_orchestrator(factory, workspace_root)` —
полная сборка (gateway/profile/executor через `build_tool_executor` + Orchestrator) с возвратом
`(orchestrator, gateway)`: ownership gateway остался у вызывающего. `build_orchestrator` теперь тонкий
вызов с прежней публичной сигнатурой (wake-tick, eval-run, manual не тронуты; semantics T7.7/T7.58 в
докстринге сохранены), `build_standalone_app` (`apps/web/api.py`) больше не импортирует LLMMiddleware/
LLMGatewayConfig/ModelProfile/build_tool_executor и не конструирует Orchestrator — зовёт построитель на
своей фабрике и своём workspace (`resolve_standalone_workspace(data_root_from_env())`). Других
конструкторов `Orchestrator(` в продакшн-коде не осталось (только тесты). Поведение gateway прежнее:
manual/tick закрывают в finally, веб-процесс — нет (живёт с приложением; закреплено докстрингом).

**Точки входа (до → после).**

| Вход | Место сборки | research_service до | после | каталог артефактов |
|---|---|---|---|---|
| wake tick | `hostctl/cli.py` → `build_orchestrator(factory, <использованный root>/workspace)` | есть | есть (через построитель) | `<использованный root>/artifacts` (формула выведена из аргумента — override `--data-root` сохранён) |
| eval-run / смоуки | `hostctl/cli.py` → тот же `build_orchestrator` | есть | есть | тот же |
| manual `python -m apps.orchestrator` | `apps/orchestrator/main.py`, `workspace_root_from_env()` | есть | есть | `<NOEZEMA_DATA_ROOT>/artifacts` |
| web standalone («wake now», юнит `noezema-dev-web`) | `apps/web/api.py::build_standalone_app` — раньше прямой `Orchestrator(...)` | **нет (G1): каждый research.fetch fail-closed, артефактного хранилища в веб-процессе не было вовсе** | есть (через построитель) | `<NOEZEMA_DATA_ROOT>/artifacts` (sibling веб-workspace = тот же каталог, что у тика при общем data root) |
| standalone-прокси `apps/research_proxy/main.py` | свой `ResearchProxyService`, `artifacts_root_from_env()` | есть | не тронут | тот же каталог |

Хостовый контур (hostctl backup/restore-drill/blind-sample читают `NOEZEMA_ARTIFACTS_ROOT`, дефолт —
data root) не менялся: с T7.61(б) он уже согласен с тиковым default-каталогом.

**Выбор формы.** План допускал «helper вида research_service_for». Выбран полный построитель: веб
дублировал не только research-строку, но и сборку gateway/profile/executor — точечный helper оставил бы
в `api.py` дублирующую assembly, из которой входы снова могли бы разойтись; цель задачи — отсутствие
второй точки конструирования вообще. Компромисс сознательный: веб-фабрика получила зависимость от
всего модуля сборки сессий (это и есть единомыслие входов).

**Тесты.** `tests/unit/test_research_wiring.py` (+2): (а) wiring веб-фабрики — `research_service` есть,
store root = `<data root>/artifacts`, executor stub; (б) parity — wake-tick формула вызова и веб-фабрика
на одном `NOEZEMA_DATA_ROOT` дают один каталог артефактов, одинаковые типы и независимые экземпляры
сервиса; `artifacts_root_from_env()` согласован. Новый `tests/scenario/test_web_standalone_research.py`:
активированный curated-снапшот (loopback `private_allowlist`), wake_now через **реальный**
`build_standalone_app()` с env + lifespan + unit-state, explorer вызывает research.fetch на локальный
fake-origin: action `completed` без error, в audit нет ни одной строки «not configured», есть
`research_content_read` (mode=curated), `sources`/`artifact_chunks` (`origin_kind=research_proxy`),
evidence `source_assertion`, файлы артефактов под `<tmp data root>/artifacts`, fence'd текст вошёл в
контекст модели, сессия `succeeded`. Сети нет: только loopback HTTP. Краснота на старом коде (api.py
фабрика временно возвращена к HEAD, без коммита): все три новых теста красные, scenario воспроизводит
живой текст — `state=failed error=research proxy is not configured for this host`; файл восстановлен.

**Не изменено (стоп-критерии не понадобились).** Контракт `run_session` и `_research_fetch`,
`apps/research_proxy/*`, политика/профили, payload config-v13, пины промптов, реестр инструментов,
миграции, JSON API; executor default stub; sealed остаётся fail-closed (wiring инертен там, где egress
запрещён профилем).

**Эффект на стенде после деплоя.** После redeploy bundle'а web «wake now» собирает сессию идентично
тику: research.fetch работает по режиму активного снапшота (на `.92` — curated). Операторское условие
реального внешнего факта прежнее — постоянные исходящие 80/443 UFW на `.92` (нынешние помечены
временными; SearXNG для fetch не нужен). Проверка без новых инструментов: шаги карточки ответа
(`research.fetch → completed`) и файлы в `<NOEZEMA_DATA_ROOT>/artifacts` — веб и тик теперь пишут в один
каталог. Строку «research proxy» в `status.sh` не добавляли: режим виден из payload-файла активного
снапшота (строка «конфиг» печатает его имя); явная строка запланирована в preflight T7.70.

**Хвосты.** (1) Расхождение config-v13 (в снапшоте есть `artifact.create`, его нет в реестре/исполнителе —
«unknown tool») — отдельная проблема из разбора T7.69b, эта задача её не касалась. (2) Формула артефактов
намеренно выведена из АРГУМЕНТА workspace, а не перечитывает env — так сохранён семантика override
`--data-root` у wake-tick; эквивалентность каталогов гарантирована при общем data root (пин тестом (б)).
(3) Веб-процесс по-прежнему не закрывает свой gateway (статус-кво T7.59 — до T7.68 было так же); при
будущем refactor shutdown веб разбирать отдельно.

Проверки commit'а `cbac9e2` (код+тесты): ruff — OK; mypy strict — OK (137 файлов); образ
`noezema-sandbox:test` — на месте; pytest два прогона: **1309 passed + 12 skipped** (`-n auto -m "not timing"`,
173.3 с) и **4 passed** (`-m timing`) — baseline 1306+12 ровно + эти три теста. Повторная полная проверка
перед документным коммитом (этот раздел + отметки в ADR-0027, `docs/web-access-design.md` и README стенда):
те же результаты — **1309 passed + 12 skipped** и **4 passed**.

## T7.69 — серия подбора модели NOEZEMA (a/c — прогоны, b — разбор). 2026-10-06

**T7.69a и T7.69c были операционными: собственных коммитов не делали** — их материалы лежат вне репо,
в `/home/denis/dsh1/modelsel` (только для чтения): `SERIES.log`, `driver.log`, `DONE`,
`<slug>/{summary.json,preflight.log,preflight_probe.json,logs/…,data/…}`. Серия прогнала смоук-корпус
(7 вопросов, sha256 `b3e05ad5d206…`, seed 20260924) на шести моделях движка `.48` одним и тем же кодом
(`22ea68d`) и config-v13 (`0260fcd2f79035e6…`), исполнитель — stub, профиль схемы подбирался предстартом
(`none → llamacpp-rocmfpx → halogen`, первый профиль с HTTP 200 на все 5 response-схем). Прогоны:
`k2-horizon-mova-36b-a4b-rocmfp4-fast` (943 с, `goal_reached` ×7), `qwen36-35b-a3b-q4-mtp` (1329 с),
`qwen36-35b-a3b-q6-mtp` (1448 с), `qwen38-27b-q5xl` (4144 с), `qwen38-flash-next-iq4xs` — первый прогон
прерван двумя перезагрузками `.48` (запись серии: «data fabric sync flood x2», 2/7 сессий), повторный
`q38fn-iq4xs-r2` доведён до `goal_reached` ×7 (2834 с). `gufo-qwen38-27b-q4-dflash2` отсеян предстартом:
схема не принята ни одним профилем (5×HTTP 400 «strict schemas require every property»,
`gufo-27b/preflight_probe.json`). Движок halogen-flash-next, `deepseek-v4-flash-iq3xxs`, `ornith` и `ling`
в серию не входили — они вне списка кандидатов драйвера (`modelsel/driver.sh:40`), замеров по ним нет.
С 12:43 MSK на `.48` закреплён DPM high (справка менеджера в `summary.json`); прогоны `k2` и первого
flash-next это не касается, поэтому их скорость с остальными неравноусловна.

**T7.69b — разбор (этот commit):** `docs/eval/MODEL-SELECTION-report.md`. Источник чисел — только
SELECT по БД прогонов (`noezema-smoke-msel-<slug>`) и чтение логов/артефактов серии; новых сессий,
eval-run'ов, обращений к `.42`/`.48`/`.92` и к внешней сети не было. Ключевое: во всех шести БД
**ни одного `finish_reason=length`** (163 model_runs, все `stop`), вызовов с output ≥ 8000 нет,
отказов невалидной схемы нет; находок сканов текстовых колонок на NUL/C0 — нет. Отказы действий:
`artifact.create → «unknown tool»` (K2 ×1 в серии, ×2 в V13, q6 ×1, flash-next-r2 ×3) и отказы по
лишним аргументам (`research.fetch {language}`, `question.create {dependencies, search_statements}`,
`memory.search {top_k}`) — с `extra="forbid"` pydantic-моделей реестра. Причина `artifact.create`
разобрана точно: offered-список строится из возможностей снапшота (`orchestrator.py:1395`, шапка
«Доступные инструменты» `:1294`), пин всех прогонов `f76284735e255086` = hash списка config-v13 без
`message.reply` (пустой inbox, T7.13), а в реестре (`packages/policy/tools.py::all_tools()`) этого
инструмента нет → отказ предопределён конфигурацией, а не «выдумкой» модели (ловушка известна: AGENTS §7
и примечание T7.64 про подпись в `apps/web/labels.py`). Тот же разбор применён к живому случаю на `.92`
(`artifact.create` + `question.create` extra inputs при верном ответе 42925, вычисленном в sandbox):
характерно для K2 (повторяется в серии и в V13), но не уникально и на корректность ответа не влияет.
Память между сессиями: `memory.search` реально вызывался только у `q38-27b-q5xl` (×2, один вернул прежний
E3-claim); у flash-next-r2 единственная попытка отклонена схемой → `significant_claim_reuse 0/5`;
дублирование вместо переиспользования — q6 (Python) и flash-next-r2 (план).

Рекомендация (с обязательной оговоркой **N=1 прогон по 7 сессий на модель**, направление а не статистика;
гейты §22.2 везде `insufficient_sample`, кроме `high_severity_incidents = 0`): для `.92` —
`k2-horizon-mova-36b-a4b-rocmfp4-fast` + `NOEZEMA_LLM_SCHEMA_PROFILE=llamacpp-rocmfpx`
(корректность 7/7, ни одного внесource якоря, кратчайший прогон; слабое место — 3 отказа схем за прогон),
запасная — `qwen36-35b-a3b-q4-mtp` + профиль `none` (ноль отказов действий, но выдуманный `as_of` и окно
ровно 131072 = нынешний бюджет). config-v14 выбранной моделью **не требуется** (окно K2 262144 больше
`context_window` 131072, observed input ≤ 29203); он нужен из-за T7.71 (новый offered-список →
`tool_schema_hash`, пины, labels). Риск назван прямо: класс Flash-Next дважды ронял `.48` в этой серии и
показал наихудшее обращение со знанием — на стенд его не ставить.

**Решения пользователя 2026-10-06** зафиксированы в ADR-0027 («Решения пользователя 2026-10-06»):
(a) доменная политика — открытый список (A: пустой `allowed_domains` = любой публичный http(s)-хост под
SSRF-guard'ом); (b) исходящие 80/443 на `.92` становятся постоянными (правила UFW меняет оператор, не
агент и не пакет `deploy/dev-stand`); (c) поиск нужен → SearXNG на стенде (**T7.70**) и инструмент
`research.search` модели (**T7.71**, с explorer-v6 и config-v14). ADR-0027 остаётся **proposed**
(пункт 1 — код T7.68 — не выполнен); в нём перенумерованы пункты 2–4 и закрыт прежний «T7.69 — решение
по доменам». В `docs/web-access-design.md` обновлены только статусы (вариант D принят поверх A, §8
вопросы 1–3 закрыты) и нумерация плана задач; анализ G1–G10 не переписывался.

Проверки commit'а (повторный полный прогон уже после правок документов): ruff — OK; mypy strict — OK
(136 файлов); образ `noezema-sandbox:test` — на месте; pytest два прогона: **1306 passed + 12 skipped**
(`-n auto -m "not timing"`, 161.9 с) и **4 passed** (`-m timing`, 47.4 с) — совпадает с эталоном;
контейнеры до/после — 41, БД на тестовом кластере: миграционных scratch 56, шаблонов 0, admin `noezema` 1,
смоук-БД 16 (из них 6 серии MODELSEL), всего 111.
Изменены ровно четыре файла: новый `docs/eval/MODEL-SELECTION-report.md`, ADR-0027,
`docs/web-access-design.md` и этот раздел STATUS.md; код, тесты, payload'ы, промпты и deploy не менялись.

## T7.70/T7.71 — поиск на dev-стенде (SearXNG) и инструмент поиска для модели: анализ до кода (M7; G3, G8/G9)

Основание: решения пользователя 2026-10-06 (ADR-0027 «Решения пользователя», пункты (a)–(c)) и
`docs/web-access-design.md` §5 (вариант D принят поверх A), §6 (план T7.70 → T7.71 → T7.72),
§7.2 (команды SearXNG), G8/G9. Этот раздел — шаг 1: контракт, гейты и последствия записаны **до**
любой правки кода; код — в следующих коммитах этой же ветки (`T7.70:` package, `T7.71:` инструмент).

### 1. Имя инструмента: `web.search` (по ARCHITECTURE), а не `research.search`

Спецификация называет его двумя местами: таблица §5.7 (`ARCHITECTURE.md:754`) — «web.search …
observation … sealed: локальный индекс; curated: SearxNG; open_lab: внешний API», и пример
протокола §7 (`ARCHITECTURE.md:1027`) — `{"tool": "web.search", "arguments": {"query": …}}`.
ADR-0027 §4 и web-access-design писали имя неформально (`research.search`). Решение: **`web.search`**
(следую архитектуре).

Попутно зафиксирован уже существующий дрейф имён, который эта задача НЕ чинит: в реестре
(`packages/policy/tools.py`) fetch называется `research.fetch`, а таблица §5.7 — `web.fetch`.
Переименование невозможно без переписывания замороженных payload'ов config-v1…v13 (`policy.capabilities.tools`
пинится байтово и входит в canonical-хеш снапшота). Следствие: в одном offered-списке живут
`web.search` и `research.fetch`; расхождение — отдельное решение (ADR-0028 «Решения», п. 1).

Второй дрейф, тоже не чинится: §5.7 объявляет fetch как observation, а реестр помечает
`research.fetch` классом `IdempotencyClass.NON_IDEMPOTENT`. Класс действия определяет ретраи
(`packages/broker/broker.py::RETRY_POLICY`, инвариант AGENTS §3 «observation/non_idempotent = 0») —
правка существующего класса меняет семантику повторов уже записанных сессий. Новый инструмент берётся
по таблице: **OBSERVATION** (ретраев 0, повтор идентичного вызова отсекается guard'ом T7.12).

### 2. Контракт (аргументы, лимиты, что видит модель)

- `ToolSpec(name="web.search", idempotency_class=OBSERVATION, args_model=WebSearchArgs, path_args=())`
  — аргументов-путей нет, перечитывания контейнерных путей нет.
- `WebSearchArgs.query: str = Field(min_length=1, max_length=500)`, модель наследует `_Args`
  (`extra="forbid"`), как у всех остальных инструментов: лишние ключи → deny вида
  `argument ('lang'): Extra inputs are not permitted`. Верхняя граница 500 (не 1000, как у
  `memory.search`) сознательная: это строка поисковой системы, а не вопрос; она же ограничивает объём
  темы, раскрываемой внешним движкам (§5.12.1).
- Сервис уже существует и менять его семантику нельзя: `apps/research_proxy/service.py::search()` →
  `{"mode", "profile", "local": [...], "upstream": [...] | None, "note": …}`; локальный FTS-индекс
  (§5.12) исполняется при любом режиме, upstream — только `curated` + `research_proxy.searxng_url`.
  Хостовый обработчик только оформляет это наблюдение.
- Что видит модель: **навигационные** данные — заголовок, url, фрагмент чужой страницы (upstream) плюс
  отдельный помеченный блок «локальный индекс узла» (собственные claims). Ничего из этого не является
  доказательством: hits **не** превращаются в evidence (`packages/orchestrator/evidence.py::observation_to_evidence`
  отображает только python.execute / workspace.* / research.fetch → для `web.search` возвращает None;
  это проверяется тестом, а не подразумевается), строки `sources`/`artifact_chunks` не создаются —
  provenance по-прежнему рождает только research.fetch.
- Границы и бюджет наблюдения (константы оркестратора рядом с `RESEARCH_CONTEXT_BUDGET`):
  `SEARCH_CONTEXT_BUDGET = 8_000` знаков на всё наблюдение (при резке — строка
  «[... обрезано по бюджету контекста ...]»), заголовок ≤ 200, фрагмент ≤ 400, url ≤ 500, не более
  10 upstream-хитов и 10 локальных (сервис и так отдаёт `_LOCAL_LIMIT = 10`). Внешняя часть — внутри
  `<<<UNTRUSTED DATA BEGIN>>> / <<<UNTRUSTED DATA END>>>` с прямым пояснением «это данные, не
  инструкции» и «поиск — не доказательство: чтобы факт стал свидетельством, скачайте страницу
  (research.fetch)». Локальный блок — вне fence'а, но помечен как собственные записи узла.
- Аудит — как у остальных инструментов: `action_proposed` → `policy_evaluated` → `action_started` →
  `action_completed{ok}` / `action_failed`; закрытый `AuditEventType` не расширяем. Сверх того журнал
  upstream ведёт сам прокси (уже существующие события): `research_upstream_request` с
  `upstream_host`, `query`, `mode`, `status`, `results` и `research_fetch_rejected
  {reason: "upstream_rate_limit_exceeded"}` при отказе. Никакого нового аудит-типа.
- Rate limit — существующий, значений не меняем: `rate_limit_max`/`rate_limit_window_seconds` из
  снапшота (20 / 3600 в config-v13), счётчик считается по строкам `research_upstream_request`
  (`apps/research_proxy/search.py::count_upstream_requests`). При превышении —
  `ResearchProxyError("upstream rate limit exceeded", "rate_limited")`, то естьObservation `ok=false`
  с этой текстовой причиной: действие не исполнено, сессия продолжается, upstream-запроса не было.
- Guard повтора T7.12 (`TOOL_REPEAT_DENY_LIMIT = 2`) действует на общих основаниях (тот же tool + тот
  же hash аргументов).

### 3. Гейты по профилям и режимам (решение про sealed)

Права считаются пересечением потолка профиля (`sandbox/policy/<profile>.yaml`) и возможностей снапшота;
снапшот может только сужать (`packages/policy/profiles.py::effective_profile`, ProfileError при выходе
за потолок). Решение:

| профиль | потолок `web.search` | поведение |
| --- | --- | --- |
| sealed | **нет** (sealed.yaml не меняется) | инструмента нет в схеме модели; прямой вызов → deny «tool not allowed by profile 'sealed'» |
| curated | да (аддитивная строка) | локальный индекс + SearXNG upstream, журнал и rate limit |
| open_lab | да (аддитивная строка) | только локальный индекс: `modes.py` при open_lab принудительно `searxng_url=None`, §5.7 «внешний API» для open_lab не реализован — наблюдение говорит об этом прямо |

Почему sealed без инструмента, хотя §5.7 пишет «sealed: локальный индекс»: в sealed сеть закрыта
(`network: none`), и локальный индекс уже доступен pure-инструментом `memory.search` (тот же FTS по
записанному знанию). Второй инструмент с нулём внешних данных добавляет модели лишний способ
утверждать «я поискал» там, где искать негде, — то есть ровно ту путаницу, которую закрывает G8.
Если позже понадобится индексный поиск именно под именем `web.search`, это одна аддитивная строка в
`sealed.yaml` плюс payload, который его запрашивает; существующие sealed-снапшоты (в том числе
BOOTSTRAP_PAYLOAD) его не запрашивают, значит их поведение не меняется ни на байт.

Существующие гейты остаются и проверяются отдельно: при `network: none` URL в строковом аргументе
запрещён (`packages/policy/engine.py::_check_paths_and_network`); backstop §11.2 — запрос, дословно
скопированный из недоверенного текста (окно 48 знаков), даёт `require_operator`, а такое действие не
исполняется. Для поиска это отдельный тест: инъекция из fetched-страницы не может заставить узел
отправить ей же придуманный запрос внешним движкам.

### 4. Влияние на offered-список, `tool_schema_hash` и пины

Offered-список строится из возможностей снапшота (`base_tools = sorted(cap_profile.tools)`, затем
`filter_offered_tools(..., has_message=…)`), молча ничего не вырезается (ловушка AGENTS §7). Существующие
снапшоты `web.search` не запрашивают → их `tool_schema_hash` и лента шагов не меняются; новый инструмент
виден только там, где его явно granted.

- config-v13: caps 10 инструментов; pин offered-списка (inbox пуст, `message.reply` снят по T7.13) =
  `f76284735e255086…` (замер MODELSEL), полный список 10 → `c64d98d40e631755…`.
- config-v14 = v13 ровно с двумя изменениями: `policy.capabilities.tools` — **+web.search, −artifact.create**
  (список из 10 имён остаётся списком из 10), и пин `prompts.explorer` → `explorer-v6`.
  Полный список 10 → `0cff269d74562bce…`, offered-список (без `message.reply`) → **`a7adc9487f6536dde5889ece4fbf0bea5a5b61e6475079e9a7c6fcff81a97cbe`**.
- `artifact.create` убирается из снапшота, а не из реестра (его в реестре и нет — отсюда «unknown tool»
  в MODELSEL-серии и на `.92`). Подпись `artifact.create` в `apps/web/labels.py` остаётся: тест полноты
  берёт объединение реестра и возможностей **BOOTSTRAP_PAYLOAD** (AGENTS §7), где этот инструмент есть.
- Словарь подписей обязан получить `web.search`: значения категории `action_tool` тестируются из
  `all_tools()`, новый инструмент без подписи краснит полноту.
- Слово о внешности ответа не меняется: честные замечания (`build_honesty_notes`) по-прежнему
  опираются на research.fetch (они про «прочитал внешнюю страницу»), шаг «как получено» для поиска
  получает форму «поискал в интернете: <запрос>» с truncation и без выдумки.

### 5. Промпт explorer-v6 (G8) и почему v5 не правится

`prompts/explorer/explorer-v5.md` заморожен пинами (sha256 `3b1fd49d…` в payload'ах, ADR-0019):
править байты нельзя. Новый файл `explorer-v6.md` — ровно то, что обещано T7.69b/web-access-design §8
вопрос 5: (1) убрать ложь «работаешь в режиме Sealed: сети нет» (режим задаёт снапшот, и на `.92`
активен curated); (2) описать поток «поиск → выбор страницы → research.fetch», где фрагменты поиска
не доказательство; (3) правило «для внешнего факта — минимум два независимых источника (разные
registrable-домены)»; (4) напоминание, что поля конверта куратора (`dependencies`,
`search_statements`, `question`/`assertion_text`) — НЕ аргументы `question.create` (живой отказ
«Extra inputs are not permitted» на `.92` и в серии MODELSEL). Остальные правила 1–7 и оба JSON-примера
переносятся дословно: тест `tests/unit/test_prompt_example_no_real_data.py` сканирует бесплатный текст
примеров всех `explorer-v*.md` по корпусам репо, значит новый пример вводить нельзя.

### 6. Нужен ли новый ADR

Да: **ADR-0028 (Proposed)** — «Инструмент поиска для модели: имя по §5.7, гейта по профилям,
ненаблюдаемое в доказательство; снапшот config-v14 без artifact.create». Он фиксирует решения, которых
в ADR-0027 нет: разрешение дрейфа имён (web.search vs research.search vs research.fetch), отказ
давать `web.search` потолку sealed при существующем `memory.search`, правило «hits не становятся
evidence и не создают sources», снятие `artifact.create` из возможностей снапшота как способ убрать
предопределённые отказы шагов. ADR-0027 **остаётся Proposed**: его пункт 3 (что ограничивает расход и
раскрытие темы в curated/open_lab, T7.72) решения не получил; пункты 1 (T7.68) и 4 (поиск нужен)
реализованы этими двумя задачами, пункт 2 решён ранее.

### 7. Критерий остановки: не срабатывает

Проверено по списку: миграций БД нет (ни одной таблицы не касаемся), `ARCHITECTURE.md` не правится
(имя берётся из §5.7), семантика режимов (`apps/research_proxy/modes.py`, `service.py`, `search.py`,
api.py) не меняется — хост только оформляет уже существующий сервис; правила оценки знания
(rules engine, evidence/claims, пороги типов, `claim_type_rules`), thresholds и gate-матрицы не
трогаются; `_research_fetch` и default executor (stub) не изменяются. Из «правиловых» файлов
добавляются ровно две строки потолков (`curated.yaml`, `open_lab.yaml`) — capability ceiling нового
инструмента, аддитивно и не меняющий ни один существующий гейт.

### 8. Тестовая стратегия (что именно краснеет на старом коде)

- пакет SearXNG: `bash -n` + содержательные пины скриптов (`tests/unit/test_dev_stand_scripts.py`,
  субститы docker/ss/sudo/ufw, только `--dry-run`): флаг `--with-searxng` documented в `--help`,
  существующий контейнер НЕ пересоздаётся без `--recreate-searxng`, secret_key генерируется в файл
  настроек и не печатается, правила сети только печатаются (`ufw allow` вне note-строк отсутствует),
  README содержит раздел «Поиск (SearXNG)». Краснота: сегодня ни флага, ни контейнера, ни раздела нет.
- контракт/реестр: `web.search` в `all_tools()`, OBSERVATION, extra=forbid, границы длины, отсутствие в
  sealed-схеме модели (`model_tools_schema`) и присутствие в curated/open_lab. Краснеет на текущем
  реестре (9 инструментов).
- гейты PolicyEngine: allow в curated, deny «tool not allowed by profile 'sealed'», deny по длине/лишним
  ключам, require_operator при дословном копировании запроса из недоверенного текста.
- оркестратор (FakeLLM + loopback fake SearXNG, сети нет): наблюдение с fence'ом, хиты **внутри** рамки,
  evidence пусто (0 строк), `research_upstream_request` записан; дальше research.fetch выбранного url →
  evidence есть. Краснеет: сегодня `web.search` → «unknown tool».
- безопасность (по образцу `tests/security/test_research_injection.py`): title/snippet с instruction-
  текстом не исполняется, fence цел, никаких action_failed из-за содержимого хита.
- rate limit и upstream-журнал через fake SearXNG: после исчерпания лимита — отказ без обращения к
  движку и запись `research_fetch_rejected{reason:"upstream_rate_limit_exceeded"}`; при битом upstream —
  `status="failed"` в журнале и Observation с текстом ошибки.
- labels/steps: полнота словаря (новый инструмент требует подпись), шаг «поискал в интернете: …»,
  отсутствие шага для не выполненного до конца действия, лимиты ≤40/≤160/≤120 и запрет кодов сохраняются.
- explorer-v6: пин v6 читается и совпадает (resolve_prompts), v5-пины прежних payload'ов не тронуты.
- config-v14: загрузка+валидация (TokenBudgets.validate() == [], resolve_prompts, пины, canonical-хеш),
  diff против v13 = ровно два ключа; активация в scratch-БД по образцу v13-теста.
- сценарий «как на стенде» (по образцу `tests/scenario/test_web_standalone_research.py`): standalone web
  + wake_now + curated-v14 + fake SearXNG + fake origin → search, затем fetch двух разных доменов, две
  внешние страницы в fence'е, session succeeded.

### 9. Результаты (реализация, 2026-10-06)

**T7.70 — пакет SearXNG для dev-стенда: коммит `6f892c5`.** Файлы: `deploy/dev-stand/searxng/settings.yml`
(шаблон с заглушкой `@SECRET@`, `limiter: false`, formats html+json), `deploy/dev-stand/searxng-settings.sh`
(`--out/--template/--rotate-secret/--check`; secret_key = `openssl rand -hex 32`, подстановка через env —
значение в файл, никогда не в вывод), шаг 8 `bootstrap.sh` (`--with-searxng`, `--recreate-searxng`:
bind-mount настроек read-only, `-p 127.0.0.1:8888:8080`, ожидание готовности JSON-пробой `/search?format=json`
≤60 с), раздел «поиск (SearXNG)» в `status.sh` (+ флаг `--no-search`), README «Поиск (SearXNG, T7.70)».
Сетевые правила (`ufw allow … to 8888/tcp`, либо closed-list egress) напечатаны как **заметка оператору**:
в скриптах нет ни одной вызванной команды firewall — stop-criteria «не трогать UFW» соблюдён; pull образа
`searxng/searxng:latest` делает менеджер (скрипт при недоступном образе объясняет команду, а не тянет её).
Тесты: `tests/unit/test_dev_stand_scripts.py` (+17 — 19 тестов стало 36; настоящие скрипты с подставными docker/ss/sudo/ufw и
только `--dry-run`, плюс `bash -n`) — пинят флаг в `--help`, неизменение существующего контейнера без
`--recreate-searxng`, отсутствие firewall-вызовов, секрет в файле и не в stdout, раздел README. Краснота на
старом коде: ни флага, ни скрипта настроек, ни раздела статуса/README не существовало. `shellcheck` в этой
среде не установлен — из трёх рекомендуемых AGENTS §7 проверок выполнены две (`bash -n` + pytest); отмечено
отдельно в отчёте, а не «проверено shellcheck».

**Errata к числам в уже сделанных коммитах** (историю AGENTS §4 переписывать запрещает, поэтому исправление
живёт здесь): сообщение `6f892c5` сообщает «+12 в tests/unit/test_dev_stand_scripts.py» — по замеру файла это
+17 (было 19 тест-функций, стало 36); сообщение `f32a30b` сообщает «40 из 65 красные» — по замеру 55 из 63.
Сами тесты и их результаты от этого не меняются: полная проверка §6 зелёная, цифры прогонов приведены ниже.

**ADR-0028 (`proposed`)** закрыл расхождения формы: имя `web.search` вместо планового `research.search`, отказ профилю sealed при обещанном §5.7 «локальном индексе», дрейф `web.fetch`/`research.fetch` и класс `NON_IDEMPOTENT` у fetch — всё это решения, которые ADR-0027 не определял.

**T7.71 — инструмент поиска для модели (G8/G9).** Реестр: `WebSearchArgs.query` (1…500, `extra="forbid"`) и
`ToolSpec("web.search", OBSERVATION, path_args=())` в `packages/policy/tools.py` (реестр теперь 10 инструментов).
Потолки: аддитивные строки `web.search: true` в `sandbox/policy/curated.yaml` и `open_lab.yaml`;
`sealed.yaml` не изменён (обоснование — п. 3 выше, ADR-0028). Оркестратор: host-side `_web_search` рядом с
`_research_fetch` поверх уже существующего `ResearchProxyService.search` (без fail-open: при отсутствии
research-сервиса — Observation `ok=false` «research proxy is not configured for this host», тот же текст, что у
fetch), оформление — новый чистый модуль `apps/orchestrator/search_view.py` (константы 8000/200/400/500/10;
fence только для внешней части; резка целыми хитами с меткой бюджета; нейтрализация литералов fence'а внутри
чужих данных; блок «память узла» вне fence'а; честный заголовок, когда upstream не выполнялся или дал пусто).
`observation_to_evidence` для `web.search` остаётся None → находки не становятся ни evidence, ни sources, ни
artifact_chunks (проверяется тестом, включая декоративный хит с полями `url`/`normalized_text`/`source_id`).
Новых аудит-типов нет: общий путь `action_*` + уже существующие `research_upstream_request` и
`research_fetch_rejected{reason:"upstream_rate_limit_exceeded"}`. Интерфейс: подпись категории `action_tool`
(«поискал в интернете») и шаг «поискал в интернете: <запрос>» (запрос берётся из arguments действия, mask NUL,
одна строка, ≤80 знаков, не показывается, если содержит код/§/номер задачи — иначе только общий ярлык);
честные замечания карточки ответа не изменились («внешние источники не использовались» остаётся верным при
поиске без чтения страницы). Промпт: новый `prompts/explorer/explorer-v6.md` (v5 байт в байт прежний),
правило 4 «сначала навигация, потом чтение», запрет полей чужих схем в аргументах, честный режим;
JSON-примеры перенесены дословно (тест `test_prompt_example_no_real_data.py` сканирует все `explorer-v*.md`).

**Хеши и идентичность.** explorer-v5 (неизменен) `3b1fd49d687a39ab88809ac208cc9dfc4f0390b0da3a9ea848f888cf22a69c3c`;
explorer-v6 `5e4cffd85e2c038d92ef7bf3264183a4eda2ca09896707dcdabebaabbad09952` (он же пин в config-v14).
config-v13: файл `fe931c15a34fe71e670c29a2aacc6e63ba3defd54b5db88923201f6342e1a0ed`, canonical
`0260fcd2f79035e634d49fe8304e44a3784b63dc0a81566687cbe52aa7f94ce0`. config-v14: файл
`6d361dae4454a56472de58df4c4c34577d6b1ff4cc870d9b418d72943256ed89`, **canonical `22903be78602cf7897f0de57b99514b66c58eca83960fa05458fd341e0104df4`**
(именно canonical попадает в `config_snapshots.payload_sha256`, AGENTS §8). Diff против v13 — ровно два ключа:
`policy.capabilities.tools` (`+web.search`, `−artifact.create`) и `prompts.explorer`. `tool_schema_hash`:
v13 полный список 10 → `c64d98d40e6317556134d544c100a1bfb40c47935d6108bffeb56f3291e3ffef`, offered (без
`message.reply`) → `f76284735e255086…` (замер MODELSEL); v14 полный список 10 → `0cff269d74562bce2e7fc8827ce9de263244c565e61f96f640880aa69b16b1e5`,
offered → `a7adc9487f6536dde5889ece4fbf0bea5a5b61e6475079e9a7c6fcff81a97cbe`. Offered-список нигде молча не
фильтруется (ловушка AGENTS §7) — в тесте v14 он собирается тем же способом, что и на стенде.

**Тесты T7.71 (файлы → что закрепляют).** Все — только loopback/фейки: реальных запросов наружу нет, к
эталонному SearXNG `.87` (`noezema-searxng`) обращения нет ни в одном тесте.

| файл | тестов | что ловит | краснел ли на старом коде |
| --- | --- | --- | --- |
| `tests/unit/test_search_tool_contract.py` | 14 | реестр (OBSERVATION, путей нет, `extra=forbid`, границы длины), потолки трёх профилей, отсутствие в схеме модели sealed и наличие в curated/open_lab, `ProfileError` при выходе снапшота за потолок, матрица allow/deny PolicyEngine (в т. ч. URL-в-аргументе при `network: none`), evidence None для search | да: 10 из 14 красные на HEAD (4 закрепляют уже существующее) |
| `tests/unit/test_search_view.py` | 10 | fence только вокруг внешней части и ровно одна пара маркеров при poisoned-хите, нейтральный текст вместо fence-маркера в данных, честные формулировки «поиска не было» / «совпадений нет», резка целыми хитами ≤ бюджета, потолок 10 хитов, потолки полей, метки локальных записей, NUL | да: модуль отсутствует → collect error (все 10) |
| `tests/scenario/test_web_search_tool.py` | 4 | полный путь на fake SearXNG: fence+audit без evidence; search→fetch → ровно один `source_assertion` и `sources=[url]`; rate limit → `research_fetch_rejected{upstream_rate_limit_exceeded}` + единственное failed-действие; снапшот без права → `policy:deny`, 0 upstream-строк, 0 evidence | да: 3 из 4 (`web.search` → «unknown tool»); тест отказа без права зелёный и на старом коде (отказывался и прежний unknown tool) |
| `tests/scenario/test_web_standalone_search_v14.py` | 2 | активация **немодифицированного** config-v14 (`payload_sha256 == canonical == пин`, профиль curated, пин explorer-v6, tools) и путь «как на стенде»: standalone-веб + `wake_now` + search+fetch → 1 completed web.search, ровно 1 `research_upstream_request` с запросом модели status ok, все explorer-шаги на prompt_version/sha из пина, `tool_schema_hash` == hash offered-списка v14, evidence `{source_assertion: 1}` | да: оба красные (payload-файла нет; wake не даёт ни actions, ни model_runs) |
| `tests/scenario/test_search_local_only.py` | 1 | open_lab: инструмент выдан, но upstream не вызывается вообще (fake-движок зафиксировал 0 обращений, журнал пуст), модель получает честный текст без fence'а; evidence/sources 0 | да: collect error (нет `search_view`) |
| `tests/security/test_search_injection.py` | 4 | отравленная выдача не меняет возможности узла (профиль и реестр те же, неизвестный инструмент → deny); дословно скопированный из snippet запрос → `require_operator` (backstop §11.2); poisoned-заголовки остаются внутри одной пары fence'ов, injection-текст не создаёт действий, exfil-URL никуда не отправлен; навязанный выдачей `shell.execute` → deny (`policy:*`) | да: collect error (нет `search_view`) |
| `tests/unit/test_web_answer_search_steps.py` | 18 | подпись `action_tool` и её границы, форма шага «поискал в интернете: <запрос>», truncation 80, сворачивание NUL/переносов, отказ показывать запрос с кодом/§/номером задачи, отсутствие шага для не выполненного до конца действия, повтор → один шаг со счётчиком, honesty-примечания | да: 15 из 18 красные (3 закрепляют прежние honesty-замечания) |
| `tests/unit/test_explorer_prompt_search_rules.py` | 8 | v6 — новый файл, v5 байт в байт (пин), пин config-v14 совпадает и **fail-closed** при подменённом sha (`PromptPinError`), ложь про режим удалена и закреплена, правило «поиск ≠ доказательство», аргументы поиска = только `query`, чужие поля схем отвергаются реестром, протокол завершения ADR-0022 сохранён в v6 | да: все 8 красные (файла v6 нет) |
| `tests/unit/test_freeze_payloads.py` | +2 | diff v14 vs v13 = ровно два ключа (`capabilities.tools` и `prompts.explorer`; остальные sections байт в байт, бюджеты Σ=26624 ≤ input_budget 120832) и byte-форма файла + пин хешей v14 | да: оба новых красные (11 прежних зелёные — v13 и ранние не тронуты) |

Замеры красноты сделаны отдельным прогоном этих же файлов во временном `git worktree` на `6f892c5` (затем удалён):
**красными оказались 55 из 63** тестов в девяти новых файлах (`test_search_view.py`, `test_search_local_only.py` и
`test_search_injection.py` — collect error: модуля `apps/orchestrator/search_view.py` ещё нет; contract 10/14,
answer-steps 15/18, prompt-rules 8/8, freeze +2/2, `test_web_search_tool.py` 3/4, `test_web_standalone_search_v14.py`
2/2), восемь тестов зелёные и на старом коде, потому что закрепляют уже существующее поведение (например отказ
неизвестного инструмента). Ещё 2 новых случая добавились параметризацией `test_prompt_example_no_real_data.py`
(скан всех `explorer-v*.md`) — итого collected-дельта 65. **Замечание о собственной ошибке:** в сообщении коммита `f32a30b`
эта сумма записана как «40 из 65» — цифра неверна, правильный замер приведён выше; сообщение не переписывается.

Итого новых тестов: **65** (полная проверка §6: до T7.71 — 1326 passed / 12 skipped в параллельном пуле и
4 timing; после — **1391 passed / 12 skipped** и **4 passed** timing; отказов нет). Известный flake
`tests/conftest.py` («fake LLM server exited during startup») на этих прогонах не воспроизводился.

**Что изменено в deploy (не в конфигурации узлов).** Дефолт dev-стенда переведён на `config-v14`:
`CONFIG_PAYLOAD`/`CONFIG_REASON` в `bootstrap.sh`, `reset-db.sh`, соответствующие строки README и комментариев
systemd-юнитов. Откат — одна переменная: `NOEZEMA_DEV_CONFIG_PAYLOAD=docs/eval/config-v13-payload.json`
(canonical `0260fcd2…`). Прежние payload'ы не переписаны.

**Что осталось за T7.71 (не закрыто).** (1) Решение оператора о раскрытии темы запроса внешним движкам:
сейчас в upstream-запросе уходит текст `query` (≤500 знаков) — контроль = существующий журнал
`research_upstream_request` + rate limit снапшота; (2) качество выдачи SearXNG и состав движков (`settings.yml`
пакета включает лишь limiter/formats, список движков — решение оператора); (3) **T7.72** — лимиты прямых
`research.fetch` (ADR-0027 пункт 3, по-прежнему без решения; открытость curated после T7.71 стала заметнее);
(4) дрейф имён `web.search`/`research.fetch` и класс `NON_IDEMPOTENT` у fetch — зафиксированы в ADR-0028 как
отдельные решения; (5) §5.7 обещает open_lab «внешний API» поиска — не реализовано, открыто говорит об этом
в наблюдении и в таблице п. 3.

## T7.67a — «Мои вопросы»: нумерация, порядок «последние сверху», краткий ответ;
исправление нечестной свалки действий стенда (M7; упрощение UI, этап 3). 2026-10-06

Пользовательское требование: «Нужна нумерация и сортировка заданных вопросов и
полученных ответов: самые последние должны быть сверху».

### 1. Анализ до кода: кто потребляет `GET /api/v1/questions`

| Потребитель | Запрос | Что читает | Effect |
| --- | --- | --- | --- |
| JS `_HOME_HTML` | `?limit=50` → теперь `?limit=50&order=recent` | строки таблицы в серверном порядке | новые аддитивные поля |
| JS `_ENGINEER_HTML` | `?limit=25`, порядок по умолчанию | прежние ключи | не затронут (запрос и разметка не менялись) |
| `deploy/dev-stand/status.sh` (~л. 237) | `?limit=5` | id/position/state/priority/origin/session/text | аддитивные поля не читает, скрипт не менялся |
| `tests/scenario/test_web_questions.py` | дефолт | строки и позиции [1,2] (пины) | пины зелёные без правок |
| `tests/scenario/test_web_labels_api.py` | дефолт | проверки «ключ есть в строке» | аддитивность безопасна |
| `packages/domain/services/question_intake.question_queue` | прямой импорт (не HTTP) | точные dict очереди | сервис **не тронут** (его пины — прямые импорты с точными словарями) |

Размещение вьюхи: презентационный слой `apps/web/questions_view.py` (тот же приём,
что `apps/web/knowledge.py`, `apps/web/answer.py`). Эквивалентность дефолтной выдачи
старому сервису закреплена тестом `test_default_view_matches_the_intake_queue_service`
(поключевое сравнение строк и сессионных ссылок).

### 2. Схема нумерации (решение без миграции)

- **(а) колонка № в БД через миграцию** — отвергнуто: номера не участвуют в доменной
  логике, миграция трогала бы схемы, запрещённые на этом этапе, и не даёт ничего,
  чего нельзя достичь вариантом (б).
- **(б) окно `row_number() OVER (ORDER BY created_at ASC, id ASC)` по всей таблице**,
  вычисляемое в подзапросе до внешнего ORDER BY/LIMIT — принято. №1 — первый вопрос
  системы; номер не зависит от `limit`, выбранного порядка и состояния вопроса.
- Стабильность: `questions.created_at` пишется один раз при приёме и больше не
  меняется; переходы состояния, приоритет и работа сессий на номер не влияют (окно
  не смотрит ни на `state`, ни на `priority`); новые вопросы всегда получают больший
  номер. Ничего не «перенумеровывается» задним числом.
- Неразрешимость ничьих: строки, принятые в одной транзакции, делят `now()` (§7) —
  tie-break `id ASC` детерминирован; карточка ответа и список используют **то же
  выражение**, поэтому «Вопрос №N» на карточке не может разойтись со списком
  (тесты `test_tie_created_at_gets_distinct_deterministic_numbers`,
  `test_answer_card_number_matches_the_queue_number`).
- Оговорка честности: номер — «место в хронологии создания», а не хранимый
  неизменяемый атрибут; строка, закоммиченная вне порядка меток времени, займёт
  своё хронологическое место детерминированно. Для схемы записи системы (внешние
  записи только через intake) это несущественно — потому и выбран вариант без
  миграции, разрешённый пользователем при гарантируемости.

### 3. Порядок выдачи и контракт API (только добавления)

- `order=queue` — **значение по умолчанию и порядок прежние**: кандидаты в порядке
  селектора (`priority DESC, created_at ASC, id ASC`), затем разобранные вопросы;
  ключей/порядка JSON это не касается (пины `test_web_questions.py`).
- `order=recent` — новые сверху: `created_at DESC, id DESC`. Значение ограничено
  паттерном FastAPI `^(queue|recent)$`; неизвестное — 422, молча не подставляется.
- Новые ключи строки (аддитивно): `number` (int; `null` лишь если строка исчезла во
  время запроса), `answer` `{kind,label,statement,reliability}`, `queue_place`
  (каким по счёту узел возьмёт вопрос — **только у ожидающих обработки**; решение
  серверное, JS не сравнивает коды состояний), `created_by_operator` (bool).
- `position` сохранён как прежде (ранг селекторной очереди) и при `order=recent`
  тоже возвращается; тест сверяет позиции обоих порядков.

### 4. Краткий ответ в колонке «Ответ»: честность

- Род решения — `answer.build_answer_result`, тот же, что у карточки вопроса:
  `waiting / in_progress / answered / no_answer / failed`; подписи — из существующей
  категории словаря `answer_result` (новых ключей словаря не заводилось).
- Утверждения отбираются пакетным запросом с семантикой карточки: голова оценки
  `assessment_state='current'` на действующем снимке (`EFFECTIVE_SNAPSHOT_SQL`).
  pending/invalid головы отсекаются на SQL и **не могут** стать «ответом» — тест
  сеет claims с pending- и invalid-головами оценки (итог `no_answer`,
  statement/reliability = null).
- Показывается первое утверждение `(created_at, id)` = карточный `claims[0]`;
  длина колонки — `SUMMARY_STATEMENT_CHARS=160` со сворачиванием переводов строк,
  маскировкой NUL и многоточием **внутри** потолка. Обрезка относится только к
  списку: карточка печатает утверждение целиком (проверено тестом).
- Бейдж — `knowledge.assessment_view(head_state="current")` → `reliability`: level/
  label/hint/color равны карточному бейджу (тест сверяет равенство). Нет действующего
  утверждения — нет ни statement, ни бейджа: только серверная подпись состояния.

### 5. Исправление бага стенда (находка сессии 66c7901a)

`executed_actions` группировал шаги по одному лишь инструменту: detail хранился в
единственном слоте на инструмент, и два fetch **разных** доменов рендерились как
«прочитал внешнюю страницу: cbr.ru — 2 раза» — ложь о числе разных источников.
Ключ группировки теперь `(инструмент, показанный detail)`; для безымянных инструментов
detail в ключ не входит (скрытые url-подобные детали не рвут шаг на части). Точные
повторы сваливаются с настоящим числом; разные действия — отдельные шаги в пределах
MAX_STEPS=6; правило переполнения (`merged_actions: N` = сумма настоящих количеств)
и честные потребители (`tool == ...`) не изменились. То же для двух разных
`web.search`-запросов.
Обоснование правки закреплённого теста (AGENTS §7 допускает менять закреплённые
строки с обоснованием): `test_repeated_searches_collapse_into_one_step_with_one_real_query`
закреплял ровно баг (разные запросы → «1 запрос — 2 раза»). Заменён четырьмя: разные
запросы → два шага; идентичные запросы → один шаг «… — 2 раза»; две разные страницы →
два шага («cbr.ru» и «expert.ru», без «2 раза»); идентичные страницы (разные URL одного
домена) → один шаг «cbr.ru — 2 раза». Остальные тесты файла (свалка `python.execute ×3`,
потолок склейки, исключение failed/aborted) не тронуты и зелёные.

### 6. Тексты страниц (дословно)

- Абзац под заголовком главной: «Очередь вопросов: последние заданные — сверху. № —
  общий номер по порядку задания, «в очереди» — каким по счёту узел возьмёт вопрос.
  Вопросы, предложенные системой, помечены.»
- Шапка таблицы: `№` · `Вопрос` · `Ответ` · `Когда` · (пустая колонка под ссылку).
- Клетка «Ответ»: действующее утверждение → бейдж (`reliability.label`) + краткое
  утверждение; иначе серверные подписи и позиция очереди: «Ждёт обработки ·
  в очереди: 2-й», «Работа ещё идёт», «Ответ не записан», «Не получилось». Статусные
  слова — только из словаря `answer_result`; фразовая рамка «в очереди: N-й» —
  статическая страница-фраза (как уже были «сессия: …»), кодов состояний в JS нет.
- Пометка системного вопроса под текстом: `origin_label` словаря («предложен самой
  системой»); системные вопросы не скрываются.
- Пустое состояние и автообновление (5 с) прежние: «Очередь пуста. Задайте вопрос выше.»
- Карточка ответа: строка над текстом вопроса — «Вопрос №N» (JS собирает из
  `question.number` API; исчезнувший вопрос → пустая строка, номер не выдумывается).

### 7. Пример выдачи `GET /api/v1/questions?limit=2&order=recent`

```json
{"questions": [
  {"id": "0b7a…", "text": "Сколько стоит владение сервером три года?", "origin": "message",
   "state": "candidate", "priority": 0, "created_at": "2026-10-06T12:03:11+00:00",
   "position": 2, "session": null,
   "number": 4, "queue_place": 2, "created_by_operator": true,
   "answer": {"kind": "waiting", "label": "Ждёт обработки", "statement": null, "reliability": null},
   "state_label": "ждёт очереди", "state_hint": "Вопрос записан в очередь и ещё не взят в работу: узел дойдёт до него по порядку.",
   "origin_label": "от вас", "origin_hint": "Вы задали этот вопрос через форму, команду приёма или сообщение."},
  {"id": "6c4f…", "text": "Сколько будет 6 на 7?", "origin": "message",
   "state": "verified", "priority": 0, "created_at": "2026-10-06T12:01:02+00:00",
   "position": null, "session": {"id": "9e2d…", "state": "succeeded", "state_label": "готово"},
   "number": 1, "queue_place": null, "created_by_operator": true,
   "answer": {"kind": "answered", "label": "Ответ есть", "statement": "6 на 7 равно 42",
              "reliability": {"level": "verified", "label": "Проверено",
                              "hint": "Вывод принят правилами оценки: уровень подтверждения E2.",
                              "color": "green"}},
   "state_label": "ответ получен и проверен", "state_hint": "Сессия завершилась успешно, вывод записан и прошёл оценку правил.",
   "origin_label": "от вас", "origin_hint": "Вы задали этот вопрос через форму, команду приёма или сообщение."}
], "count": 2}
```

(Иллюстрация формы ответа; ключи и значения подписей соответствуют тестированному
выводу — номера 4 и 1: «последний заданный» сверху, № считается по всей таблице.)

### 8. Стоимость страницы (нет N+1)

Фиксированные ≤4 SELECT независимо от числа вопросов: (1) оконный запрос вопросов
(`number` + `queue_rank` в одном подзапросе); (2) сессии страниц по
`question_id IN (:ids)`; (3) claims тем же списком id; (4) `effective_claim_rules`
один раз. Пустой список — 1 запрос (ранний возврат до IN-запросов). Закреплено
тестом `test_page_costs_a_fixed_number_of_queries` (счётчик `before_cursor_execute`;
одинаковое число запросов при limit=5 и limit=10; пустая страница дешевле полной).

### 9. Тесты

Новые файлы: `tests/scenario/test_web_questions_view_api.py` (9 тестов: номер по всей
таблице и устойчивость к смене состояний/приоритета; ничьи created_at — детерминированные
номера; recent новые сверху + дефолтный порядок не изменился + 422 на неизвестный порядок;
эквивалентность `question_queue`; queue_place только у ожидающих; честность итога:
pending/invalid не показываются, обрезка 160 внутри потолка, равенство бейджа карточке,
карточный claims[0] не обрезан; операторный vs системный вопрос; номер карточки = номер
списка; стоимость запросов) и `tests/unit/test_web_questions_view.py` (7 тестов чистой
части). В `tests/unit/test_web_answer_search_steps.py` вместо одного закреплявшего баг
теста — четыре (+3 нетто). В `tests/scenario/test_web_answer_pages.py` +2: колонка `№`
и `order=recent` без `.sort(` в JS главной; «Вопрос №» на карточке. Краснота на HEAD
проверена временным worktree (без коммита): два теста свалки действий красные, unit-вьюхи
— ошибка сбора (модуля ещё нет). Suite: 1412 passed, 12 skipped; timing: 4 passed.
Проверка чистоты страниц (`_assert_pure`) проходит для обеих изменённых страниц.

### 10. Что не тронуто и что остаётся T7.67

Не тронуты: миграции и схемы, конфиг-снапшоты и payload'ы, промпты и пины, реестр
инструментов, политика, оркестратор, инженерная страница (её JS-запрос), `status.sh`,
`question_intake`. Дальше в T7.67 (следующие части упрощения «вопросов») следует
наследовать этот контракт: общий номер по creation-хронологии и порядок recent уже
заданы и не должны дублироваться клиентскими сортировками.

## T7.73 — честность понижения оценки при перепроверке (стенд .92: кейс 66c7901a → da2abfc1). Анализ до кода

### 1. Что произошло на стенде (данные сняты read-only, `/home/denis/dsh1/stand-case-inflation-reverify/`)

Claim `9266248e-2433-4a12-a3f7-b51ff10ad803` — «Годовая инфляция в России по итогам 2025 года
… составила 5,59% — официальный показатель Росстата …».

| | сессия 1 (66c7901a) | сессия 2 (da2abfc1, перепроверка) |
|---|---|---|
| evidence | cbr.ru + expert.ru (`source_assertion`, `supports`) | + interfax.ru; cbr.ru перечитан → дедуп |
| assessment | E3 / supported / p=0.75, reasons `["requirements_met"]` | **E1 / hypothesis / p=0.30, reasons `["as_of_missing"]`** |
| claim.as_of | `2026-01-21T00:00:00+03:00` | **NULL** |
| counters | — | `evidence_added 1`, `deduped 1` |

Карточка ответа после сессии 2: бейдж «Подтверждено слабо» вместо «Подтверждено». Доказательная
база при этом **выросла**: three sources, три независимые группы (`basis: "single"` — cbr.ru,
expert.ru, interfax.ru), ни одного опровергающего evidence.

### 2. Механика падения (цепочка, файл:строка)

1. Куратор в сессии 2 предложил перепроверку `existing_claim_id=9266248e…` с
   `claim_type: external_fact`, `as_of: null`, `scope: {}` (`staging_validated`, payload
   `claim_created`). Модель поступила ровно так, как её учат prompt и ADR: правило 7 curator-v7
   требует на перепроверке `existing_claim_id` и тип существующего claim'а, пример в самом
   правиле содержит `"as_of": null`; тот же пример — в ADR-0018 (`docs/adr/0018-reverify-existing-claim.md:98–102`).
2. Ветка перепроверки коммита: `packages/memory/service.py:479–491`. Строка **486** —
   **безусловное** `target_claim.as_of = host_ref.as_of`, где `host_ref` —
   `derive_claim_as_of(question=…, as_of=model_as_of, session_date=…)`. Вопрос перепроверки
   даты не содержал, модельный `as_of` — null ⇒ `packages/memory/scope.py` возвращает
   `(None, ClaimDateAnchor.NONE)` (ветка «нет ничего») ⇒ **уже установленная опорная дата claim'а
   затирается в NULL**. Туда же: `claim_scopes[…] = derive_claim_scope(… as_of=None …)` →
   assessed_scope `{"as_of": null, "date_anchor": "none", "source_domains": []}`. Аудит
   `claim_reverified` зафиксировал это честно: `"as_of": null, "assessed_as_of": null,
   "date_anchor": "none"`.
3. Оценка: `packages/memory/service.py:1132` передаёт в rules engine `has_as_of=claim.as_of is not None`
   ⇒ `False`. `packages/memory/rules_engine.py:182` `as_of_ok = (not rule.requires_as_of) or has_as_of`
   ⇒ `False` для `temporal_fact` (`requires_as_of: true` в config-v14); `requirements_met`
   (:213) не выполняется; ветка «не выполняет» (:230–246) даёт статус `hypothesis`,
   пониженный grade и **единственную причину `("as_of_missing",)`** (:246).
4. Уверенность: `rules_engine.py:248` `GRADE_CONFIDENCE_BASE[E1] = 0.30`, множитель
   `min(1, groups/min_independence_groups) = min(1, 3/2) = 1` ⇒ ровно **0.30**.
5. Витрина: `apps/web/knowledge.py:78–103 assessment_view` + `apps/web/reliability.py`
   (уровень E1 → «Подтверждено слабо») — витрина лишь переводит вывод rules engine,
   ничего не придумывая. **Правила не сломаны: сломан вход в них.**

Причина падения — не contradiction, не устаревание и не отзыв источника: единственная причина,
записанная rules engine, — «у claim'а нет опорной даты», а claim'ом она была и была утрачена
на шаге 2 по вине хоста. Оценка упала из-за **пустого поля предложения**, то есть ровно так,
как это запрещено формулировкой задачи.

### 3. Почему `evidence_added 1` / `deduped 1` и почему привязалось только одно evidence

Куратор предложил две ссылки: на interfax.ru (новый источник → новый ряд evidence) и на
перечитанный cbr.ru. Второй fetch дал байт-в-байт тот же артефакт, поэтому идентичность
`source_assertion_identity` (URL канонический + content hash + chunk) совпала с рядом якоря:
`packages/memory/service.py:809–824` находит существующий ряд по
`UNIQUE(claim_id, evidence_kind, identity_hash)` и увеличивает `deduped`, **не** дублируя
доказательство (инвариант T7.9 «duplicate evidence не повышает grade»). Итого 1 новый ряд +
1 повторное использование. Отдельно: ria.ru был прочитан, но куратор сам его не связал
(«не использован фрагмент RIA»), rosstat ×2 отвалился по таймауту, rbc вернул 401 —
связывать было нечего. Пересмотр при этом уже идёт по **объединённому** набору:
`service.py:883–914` поднимает все rows claim'а (`all_evidence`) в одну оценку — так что
«union» менять не нужно, нужно его закреплять тестами.

### 4. Это дефект реализации или пробел решения? И то и другое

- **Реализация:** `service.py:486` затирает as_of без условия «если выведены новые данные».
  Для нового claim это предписано ADR-0016 §4 (as_of re-deriviруется на каждом коммите), для
  **перепроверки** — нет nowhere not stated. Дедуп-ветка (`service.py:572–590`) делает то же
  (`existing_claim.as_of = host_ref.as_of` безусловно): dateless reuse так же стирает дату.
- **Пробел решения:** ADR-0016 §4 «re-derive on every commit» + ADR-0018 (тип/значение —
  якорные) не оговаривают случай «перепроверка без даты»: при буквальном следовании им
  уже установленная опорная дата исчезает, а вместе с ней — выполнение `requires_as_of`.
  Требуется уточнение ADR-0018 (и запись следствия в ADR-0016), а не только патч кода.
- **Ловушка prompts/хоста:** `CuratorProposal.validate_against` (`packages/domain/schemas/
  staging.py:104–115`) требует `as_of` для `temporal_fact`, а подмену типа на тип якоря
  оркестратор делает **позже** (`apps/orchestrator/orchestrator.py:2180` против
  :2258–2259). Честная перепроверка temporal-якоря с `as_of: null` отклоняет ВСЁ предложение
  («claim[0]: temporal_fact requires as_of») — поэтому модель на стенде и выбрала
  `external_fact`. Это ловушка выбора «солгать о типе или потерять дату», её надо закрыть.

### 5. Как независимость считает «независимые» источники (для ADR-0029, реализация вне T7.73)

`packages/memory/independence.py` + `packages/memory/source_graph.py:60–190`: группировка
строится по registrable domain канонического URL и по идентичному content hash; слияния по
`parent_source_id`, валидным рёбрам графа и поправкам графа **не имеют производите-ля** (строки
`sources` пишет только `apps/research_proxy/service.py:219–235`, ни parent, ни рёбер, ни
populated `SourceGraphInput.sample_text`) ⇒ текстовое перекрытие (`TEXT_OVERLAP_THRESHOLD = 0.8`)
в реальных сессиях инертно. Практика стенда: Rosstat → «Интерфакс»/«Expert» — три домена,
три группы, гейт `min_independence_groups: 2` выполнен, хотя первоисточник один. Разделение
первоисточника и производной публикации — предмет **ADR-0029 (Proposed)**, не кода T7.73.
Указатель происхождения (вариант B) реализован позже — в T7.75, см. её раздел §10–§12.

### 6. Принимаемые решения T7.73 (реализация)

1. **Перепроверка/reuse хранят якорную дату.** Новая чистая функция `packages/memory/reverify.py`
   (`resolve_reverify_reference` + `merge_reverify_scope`), вызываемая из обеих веток
   (`service.py:479–491`, :572–590): если вопрос сессии **сам** принёс якорь (explicit-дата или
   relative-форма) — поведение прежнее, предписанное ADR-0016/0017/0018 (в т.ч. сдвиг
   `reverify_after` у relative-якоря); если ничего не принесено, а у якоря дата есть —
   сохраняется **она** вместе с её `date_anchor` из assessed_scope текущего head (relative не
   превращается в evergreen); если предложение несёт **другую непустую** дату при датеless
   вопросе — она **не подставляется**: остаётся якорная, а расхождение пишется в аудит
   `claim_reverified` (`as_of_conflict`) — по ADR-0018 смена значения это revision/контр-evidence,
   не молчаливая замена. Scope перепроверки = хостовый из вопроса, но с сохранённой датой/якорем
   и `source_domains` = **объединение** старых и новых доменов (иначе старые evidence сами
   провалят `_canonical_covers` и downgrade воспроизведётся другим путём).
2. **Ловушка curate-гейта закрывается:** `validate_against` не требует `as_of` от операции,
   у которой задан `existing_claim_id` (дату держит хост). Тип якоря по-прежнему подменяется
   оркестратором, требования к **новым** temporal-claim'ам не ослабляются.
3. **Причина понижения доходит до карточки.** Reasons rules engine сейчас живут только в аудите
   `claim_assessed` (`service.py:1221`) и никуда дальше; карточка их не показывает
   (`apps/web/answer.py`). Добавляемое поле карточки (без миграции): причины текущего head
   подтягиваются из `audit_events` по `payload->>'assessment_id'` и подписываются новым
   словарём `labels` (категория `assessment_reason`) — «нет опорной даты», «мало доказательств»,
   «независимость не набрана» и т.д. Оценка по-прежнему производится **только** rules engine.
4. **Промпты (commit 2):** curator-v8 (на перепроверке — либо перенести `as_of`/`scope`
   существующего claim'а, либо задать новые с обоснованием; связывать каждое использованное
   наблюдение) и explorer-v7 (находить первоисточник **и** независимое исследование, отличать
   первоисточник от пересказа, расхождение показывать, а не выбирать молча). curator-v7 и
   explorer-v6 не трогаются; новый payload `config-v15` = v14 + ровно два пина.
5. **Критерий остановки:** миграции, ARCHITECTURE.md, пороги и шкала grading, правила
   независимости в rules engine — не трогаются; способ отделения первоисточника описывается в
   ADR-0029 как вариант, не реализуется.

### Результаты T7.73 (реализация)

Три коммита: код+тесты, промпты+снапшот, документы. Полная проверка §6 перед каждым:
ruff чисто, mypy `packages apps hostctl` — «Success: no issues found in 140 source files»,
`pytest -n auto -m "not timing"`: **1412 → 1438** (коммит 1) → **1470 passed, 12 skipped**
(коммит 2); `pytest -m timing`: 4 passed оба раза. Откатов и ослабленных проверок нет.

**Коммит 1 — механика перепроверки (`packages/memory/reverify.py`, новый модуль).**

Причина дефекта была не в rules engine: он честно доложил `as_of_missing` (кейс .92: голова
`9266248e-…` упала с E3/0.75 supported до E1/0.30 hypothesis, `claim_assessed.reasons =
["as_of_missing"]`). Пустую дату создал ветка записи перепроверки: у вопроса без даты
`derive_claim_as_of` даёт `(None, NONE)`, и прежний код присваивал `claims.as_of = None` и
перезаписывал assessed_scope (`{"as_of": null, "date_anchor": "none", "source_domains": []}`),
то есть *хост сам снял опорную дату с claim'а, который он же перепроверяет*.

Что теперь (чистая функция + две ветки записи):

| случай | было | стало |
|---|---|---|
| вопрос без даты, `as_of: null` в предложении | дата якоря стиралась → `as_of_missing` → E1 | дата и `date_anchor` якоря **сохраняются** (`carried_existing`) |
| вопрос без даты, предложение с **другой** датой | дата предложения подставлялась молча | остаётся якорная; расхождение в аудит `claim_reverified.as_of_conflict` (ADR-0018: смена значения — revision, не подмена) |
| вопрос называет дату явно или требует «на сегодня» | перенос привязки | без изменений (ADR-0016/0017), в т.ч. сдвиг `reverify_after` у relative-якоря; смена UTC-дня пишется в аудит `anchor_date_changed` |
| scope перепроверки | хостовый scope вопроса, пустые `source_domains` | хостовый + сохранённая дата/якорь + `source_domains` = **объединение** прежних и новых доменов (иначе собственные старые evidence не проходят `_canonical_covers` и downgrade воспроизводится другим путём) |
| curate-гейт | `validate_against` требовал `as_of` у операции с `existing_claim_id`, хотя тип якоря подменяется оркестратором **после** гейта (`apps/orchestrator/orchestrator.py:2180` против :2201–2259) | требования к дате нет только для операции с `existing_claim_id`; **новым** temporal-claim'ам дата по-прежнему обязательна (`tests/unit/test_staging_schema.py::test_temporal_fact_requires_as_of` зелёный) |
| причина оценки на карточке | жила только в аудите `claim_assessed` | поле `grade_reasons` карточки: причины текущего head подтягиваются из `audit_events` по `payload->>'assessment_id'` и подписываются словарём `labels` (новая категория `assessment_reason`, 12 кодов rules engine). Миграции нет; оценку по-прежнему производит только rules engine |

Тесты commit 1 (красные на прежнем коде — проверка с временным откатом файлов, без коммита:
«13 failed, 49 passed»; ключевые сообщения «reverify erased the anchor's reference date»,
`assert (datetime.datetime(1995, 1, 1…) is not None and 1995 != 1995)`, «epistemic status
lowered: hypothesis», `['claim[0]: temporal_fact requires as_of'] == []`, «нет категории
подписей: assessment_reason», `KeyError: 'grade_reasons'`):
`tests/unit/test_reverify_reference.py` (13), `tests/unit/test_reverify_temporal_anchor.py` (5),
`tests/scenario/test_reverify_temporal_scenario.py` (1, полный двухсессийный репродюсер на
FakeLLM: одна голова, E3 supported, дата якоря сохранена, три источника в assessed_scope,
`reasons = ["requirements_met"]`, `anchor_kept = "true"`), плюс +3 в `test_staging_schema.py`,
+2 в `test_web_labels.py` (полнота словаря проверяет коды прямо из `rules_engine`), +3 карточки
в `tests/scenario/test_web_answer_api.py`. Новый тип аудита не добавлялся: закрытый
`AuditEventType` не расширяем, новая информация — ключи payload'а существующего события
`claim_reverified`.

**Коммит 2 — промпты и снапшот.**

- `prompts/curator/curator-v8.md` (sha256 `c14603eed9119c474f85c2848b68ab5a1e661398f930f0effe5dbb50caadde71`):
  правило 8 — «`as_of: null` при `existing_claim_id` означает „сохранить дату якоря“… пустое поле
  предложения не является основанием понизить оценку… значение, просто отличное от якорного, без
  обоснования молча не применяется… понижение оценки возможно по делу (противоречие,
  устаревание, отзыв источника)»; правило 9 — «Каждый ИСПОЛЬЗОВАННЫЙ источник обязан иметь запись
  в `evidence_links`… иначе независимость считается по меньшему числу источников». Тестом
  закреплено, что правила 1–7 переехали дословно и добавлены ровно 8 и 9.
- `prompts/explorer/explorer-v7.md` (sha256 `41d3f7a22f5704f2b9302057fc944ab8cd069c8cfb3063fc5973df28f8ea40fa`):
  правило 9 — «Независимость источника — не количество адресов… Ищи ПЕРВОИСТОЧНИК — того, кто
  данные получил или посчитал… И НЕЗАВИСИМОЕ исследование… Публикация, пересказывающая релиз
  первоисточника („по данным ведомства“), — это продолжение того же источника… Числа расходятся —
  покажи расхождение открыто в `public_rationale`… никогда не выбирай одно число молча». Правила
  1–8 сохранены дословно.
- `docs/eval/config-v15-payload.json` = config-v14 **ровно с двумя правками** (`prompts.curator`,
  `prompts.explorer`; тест сверяет изменённые ключи `{path, sha256, version}` и равенство всех
  остальных разделов). Хеш файла `c65b69db5a1f50d44e6dc9e9c4b6399381c191b1df62506a9ef275a6d3b5f722`,
  **canonical** `b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73` — последний
  попадает в `config_snapshots.payload_sha256`. Пороги, `claim_type_rules` (в т.ч.
  `requires_as_of: true` для `temporal_fact`), окна, `research_proxy`, `policy` — байт в байт v14;
  payload v14 не тронут и остаётся откатом.
- Тесты commit 2: `tests/unit/test_curator_prompt_reverify.py` (12),
  `tests/unit/test_explorer_prompt_source_independence.py` (9, включая повторную проверку
  протокола завершения ADR-0022 на v7), `tests/scenario/test_config_v15_activation.py` (2: снимок
  опознаётся по canonical-хешу, пины читаются из разделов снимка и резолвятся по путям payload'а;
  откат активацией payload'а v14 возвращает head на `22903be7…` с curator-v7/explorer-v6),
  +1 параметризованная пара в `test_curator_prompt_matrix.py` (curator-v8 ↔ config-v15) и +3 в
  `test_freeze_payloads.py`. Ещё +5 дало прежнее тестовое множество: `test_prompt_example_no_real_data.py`
  параметризуется найденными в `prompts/` файлами, поэтому примеры v8/v7 проверяются им автоматически.

**Как включить v15 на стенде .92 (делает менеджер; агент к .92 не обращается).** После deploy
пакета из этой ветки — одна онлайн-активация:

```bash
hostctl activate-online --payload docs/eval/config-v15-payload.json --drain-wait-seconds 120
```

Проверка после активации (агент её не выполняет, это команды менеджера): head → canonical
`b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73`; `bootstrap.sh`/`reset-db.sh`
дефолтом уже указывают на v15. Откат — активация payload'а v14 (он закоммичен и не менялся) либо
явный выбор при развёртывании: `NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v14-payload.json`.
Верификационный вопрос на стенде — перепроверка уже подтверждённого временного факта без даты в
вопросе: ожидаемый исход — та же голова, оценка не ниже прежней, `claim_reverified` с
`anchor_kept`, а если причина всё же появилась — она видна на карточке в `grade_reasons`.

**Что осталось за границей задачи.** Независимость «три адреса = три группы» (Rosstat →
«Интерфакс»/«Expert») не исправлена: разделённые механизмы (`parent_source_id`,
`source_dependency_edges`, `source_graph_corrections`) существуют в модели и движке, но продуцента
не имеют. Варианты и рекомендация — **ADR-0029 (Proposed)**; реализация требует отдельной задачи и,
для вариантов C и D, явного решения пользователя. (Справка: вариант B выполнен в T7.75, C и D — нет.) ARCHITECTURE.md, миграции, пороги и шкала
grading не менялись.

## T7.74 — карточка нового вопроса видит то, что он перепроверил (стенд .92: вопрос №14 `7734e5b9`). Анализ до кода

### 1. Что видно на стенде (данные сняты оператором; агент к .92 не обращается)

`claim 9266248e…` («Годовая инфляция … 5,59% …») после сессии `a7b060fe` снова стоит в
«Проверено»: лента этой сессии содержит `claim_reverified` (по T7.73 — anchor_kept) и
свежий `claim_assessed` E3 / supported / p=0.75. Карточка же вопроса №14
(«Перепроверь утверждение: „…5,59%…“») говорит «есть частичный ответ», в баннере —
**«Ответ не записан»**, и в списке «Мои вопросы» строка №14 — тоже «Ответ не записан».
То же для системного вопроса перепроверки №12 (сессия `da2abfc1`, кейс T7.73): работа есть,
ответ есть, карточка его не показывает. Долговременный пункт бэклога витрины —
«reused existing claim не связан с карточкой нового вопроса».

### 2. Почему карточка слепая (цепочка, файл:строка)

1. `apps/web/answer.py:561` — утверждения карточки выбираются единственным условием
   `WHERE c.created_in_session IN (:sessions этого вопроса)`.
2. Утверждение, **перепроверенное** сессией этого вопроса, строкой `claims` не создавалось:
   ветка перепроверки коммита не заводит новый claim, а берёт существующий
   (`packages/memory/service.py:546 claims.append(target_claim)`), его
   `created_in_session` — сессия **прежнего** вопроса. Тот же механизм у дедуп-повтора
   (`:628 claims.append(existing_claim)`, T7.9).
3. `apps/web/questions_view.py` (`_SQL_QUEUE`/`_SQL_RECENT`, выборка утверждений через
   `JOIN sessions s ON s.id = c.created_in_session`) — та же связка, поэтому список
   «Мои вопросы» слепой идентично.
4. Итог: `answer_claim_count == 0` → `build_answer_result` (`apps/web/answer.py:246–300`)
   по состоянию вопроса «partially_answered» выдаёт `no_answer` → подпись
   «Ответ не записан» (`apps/web/labels.py`, категория `answer_result`). Подпись честная
   относительно **записанных этим вопросом** утверждений — и ложная относительно того,
   что вопрос реально сделал с знанием.

### 3. Где долговременно живёт связь «сессия → затронутое утверждение»

Критерий останова задачи был: если без миграции связь получается только через payload
аудита (и никак иначе) — остановиться и описать варианты. Проверено по коду: **связь
получается из долговременных колонок, критерий не срабатывает.**

| как сессия затронула claim | где записано долговременно | где в ленте |
|---|---|---|
| создала | `claims.created_in_session` (`packages/memory/service.py:676`) | `claim_created` (`:689`) |
| перепроверяла (`existing_claim_id`) | **новая строка `claim_assessments` с `created_in_session = session.id`** (`:1227–1240`; шаг 3 apply проходит по всем claims этого коммита, включая перезатронутые, `:941–1004`) | `claim_reverified` c `session_id=…` и `payload.claim_id` (`:547–569`) |
| приняла повторно (дедуп по statement+type, T7.9) | та же новая строка `claim_assessments.created_in_session` (тот же шаг 3) | **отдельного события нет** |

Три следствия, важных для витрины:

- `claim_assessments.claim_id + created_in_session` — долговременная запись о том, что
  именно ЭТА сессия оценила именно ЭТОТ claim. Её уже использует другой контур как
  источник истины: `packages/evaluation/gates.py:408–470 _gate_reuse` перечисляет ровно эти
  пути «сессия затронула утверждение» и отдельно оговаривает, что считаются только
  сессионные оценки (`created_in_session IS NULL` у worker-оценок
  `packages/memory/reassessment.py:549` и активационных — в выборку не попадают).
  Миграция не нужна: колонки и строки уже есть.
- Различение «перепроверено» / «принято повторно» возможно без текстовых эвристик:
  по паре (claim, session) событие `claim_reverified` либо есть, либо его нет
  (`AuditEventType` закрыт и не расширяется; события дедуп-повтора в нём нет — это
  **единственный пробел**, он закрывается различием «есть/нет события перепроверки» при
  уже доказанном факте «оценена этой сессией»). Ни совпадения строк, ни разбор statement,
  ни разбор публичных summary в расчёт не берутся.
- Оценки worker'а и активации не могут просочиться в карточку: `created_in_session IS NULL`.

### 4. Решение (что именно меняется)

Связь «вопрос → утверждение» расширяется с «создал» на «затронул», отношение подписывается:
`created | reverified | reused`, подписи — новая категория `claim_relation` в
`apps/web/labels.py`; итог вопроса получает честные варианты кроме «Ответ не записан»
(новые значения категории `answer_result`). Ни одно действующее утверждение (head `current`) не перестаёт быть
ответом; pending/invalid по-прежнему не становятся ответом (правило жизненного цикла §3):
отношение показывается у строки, но в список ответа она попадает только при head `current`.
`packages/memory/*`, миграции, перечисления домена, пороги и правила — не тронуты; API —
только добавление полей.

### 5. Что сделано (реализация)

- **Общий источник связи «вопрос → утверждение».** В `apps/web/answer.py` строитель
  `touched_claims_cte()` собирает долговременные связи в одно окно (`sess → touches → ranked`):
  `created` — из `claims.created_in_session`; `reverified`/`reused` — из
  `claim_assessments.claim_id + created_in_session` с различением по существованию события
  `claim_reverified` этой же сессии (`LEFT JOIN LATERAL … e.type = 'claim_reverified' AND
  e.payload->>'claim_id' = a.claim_id::text`). Утверждения, созданные этой же сессией, из
  второй ветки отсекаются (`LEFT JOIN claims c_own … WHERE c_own.id IS NULL`), и по каждой паре
  (вопрос, утверждение) остаётся сильнейшее отношение: `created` выше `reverified`, `reverified`
  выше `reused` (`DISTINCT ON … CASE`). Числовые приоритеты — `RELATION_PRECEDENCE`, они же
  используются в чистой функции `relation_kind` (по ней же строится итог вопроса).
- **Карточка и список читают одно и то же окно.** `question_answer` выбирает утверждения как
  `FROM ranked r JOIN claims c …` и добавляет у каждого `relation`, `relation_label`,
  `relation_hint`; `list_question_rows` (`apps/web/questions_view.py`) строит `answer` по той же
  схеме (окно с `question_id` из самой выборки) и добавляет в `answer` `relation` +
  `relation_label`. Отдельного запроса на утверждения в списке нет — бюджет страницы прежний,
  что закреплено тестом `test_the_queue_page_does_not_add_queries_for_reverified_answers`
  (число SELECT'ов с перепроверками равно числу без них и не больше четырёх). Итог строки
  строится тем же `build_answer_result`, что и карточка, поэтому они не могут разойтись
  (проверено тестами паритета в обоих файлах).
- **«Было → стало».** `_reverify_assessment_pairs` одним запросом берёт свежую оценку этой сессии
  и предыдущую оценку того же утверждения (`DISTINCT ON … p.created_at <= f.created_at`, строка
  прежней оценки остаётся в таблице после `superseded` — она и есть источник «было»).
  `reverify_history_view` пишет запись только когда изменилась grade или эпистемический статус:
  перепроверка с той же оценкой ответ подтверждает, но «стало» не выдумывает (тест
  `test_reverify_that_changed_nothing_does_not_invent_a_history`). Строка истории:
  `было: E3 · независимое подтверждение (подтверждено) → стало: E1 · сверена целостность
  (предположение)`; подписи оценок берутся из словаря, причины обеих сторон — из ленты
  `claim_assessed` по `assessment_id` (T7.73), неизвестный код не показывается.
- **Честный итог вопроса.** При наличии ответного утверждения `build_answer_result` различает:
  связи нет → прежний `answered`; `reverified` → `reverified`, а если работа закончилась
  частично (состояние вопроса `partially_answered` либо терминальная сессия `succeeded_partial`)
  → `partially_reverified`; `reused` → `reused`. Прежние пять значений `answer_result` и их
  приоритет не изменены; новых значений состояний вопроса или сессий нет. Действующее
  утверждение (head `current`) ответом быть не перестало; pending/invalid по-прежнему ответом не
  становится: утверждение остаётся в `other_claims`, итог — «Ответ не записан» (тест
  `test_pending_head_after_a_reverify_is_still_not_an_answer`).
- **Шаги берутся из правильной работы.** Рабочая сессия карточки выбирается из
  `touched_by_claim` — из той сессии, что действительно прикоснулась к ответу; лента чужой
  сессии больше не рассказывается как работа этого вопроса (в end-to-end тесте:
  `work.session_id` равен сессии перепроверки).
- **Страница ответа** (`apps/web/api.py`): под строкой утверждения печатаются подпись отношения и
  строки истории; новых цитируемых литералов и кодов в JS нет, снимок текстов страниц зелёный.
  Страница «для инженера» не менялась (её закреплённые строки те же).

### 6. Как закрыт пробел «у дедуп-повтора нет события»

Долговременных записей три: `claims.created_in_session` (создала), `claim_assessments` с
`created_in_session` этой сессии (оценила) и `claim_reverified` с `session_id` этой сессии и
`payload.claim_id` (перепроверяла). События дедуп-повтора в закрытом `AuditEventType` нет,
поэтому правило такое: **если сессия этого вопроса записала оценку утверждения, которое она же не
создавала, то это «перепроверено», когда о паре (утверждение, сессия) есть событие перепроверки,
и «принято повторно», когда его нет.** Проверка существования события делается внутри того же окна
(`LEFT JOIN LATERAL … LIMIT 1`), отдельными запросами на строку — нет. Текстовых эвристик, разбора
statement и чтения публичных summary нет вообще: только колонки и закрытый тип события. Оценки
worker'а и активационные (`created_in_session IS NULL`) в карточку попасть не могут — ветка
требует совпадения с сессиями этого вопроса; это тот же критерий, что уже использует долговременный
контур оценки `packages/evaluation/gates.py::_gate_reuse`. Дедуп-ветка проверена не подделкой
строк, а реальным коммитом (`MemoryService.apply_claim_staging`, `claims_created == 0`,
`claims_reused == 1`) в тесте `test_dedup_reuse_is_an_answer_of_the_new_question`.

### 7. Подписи (дословно, `apps/web/labels.py`)

Новая категория `claim_relation` (значения — `RELATION_KEYS` из `apps/web/answer.py`, полнота
проверяется тестом словаря):

| код | label | hint | action |
|---|---|---|---|
| `created` | создано этим вопросом | Утверждение записала сессия этого вопроса: до него его в знаниях не было. | Посмотрите подтверждения и оценку утверждения в его карточке. |
| `reverified` | перепроверено этим вопросом | Сессия этого вопроса подтвердила уже записанное утверждение: строка осталась прежней, оценка обновилась. | Посмотрите, какая оценка была до этой сессии и какой стала после. |
| `reused` | уже было в знаниях, принято повторно | Эта сессия не заводила нового утверждения: то же самое уже было записано и получило новую оценку. | Посмотрите, чем подтверждено это утверждение, в его карточке. |

Новые значения категории `answer_result`:

| код | label | hint | action |
|---|---|---|---|
| `reverified` | Ответ есть: подтверждено заново | Сессия этого вопроса подтвердила уже записанное утверждение — ответ тот же, оценка свежая. | Посмотрите в строке вывода, какая оценка была и какой стала. |
| `partially_reverified` | Ответ есть частично: подтверждено | Сессия этого вопроса подтвердила уже записанное утверждение, но работа по вопросу закончилась частично. | Уточните вопрос или повторите перепроверку, чтобы довести её до конца. |
| `reused` | Ответ есть: уже было, подтверждено | Этот вопрос не добавил нового утверждения: найденное уже было записано и получило новую оценку. | Посмотрите, чем подтверждено это утверждение, в его карточке. |

Слово «проверено» по-прежнему звучит только из бейджа надёжности (`reliability.level == verified`
и `verification_lead`): подписи отношения используют форму «перепроверено этим вопросом» (это
название того, что сделал вопрос, а не утверждение о проверенности знания), а тексты итога и
истории этого корня не содержат вовсе — закреплено тестами
`test_verified_word_never_appears_in_answer_building_blocks` и
`test_relation_and_history_texts_carry_no_codes_or_verified_word`.

### 8. Пример карточки вопроса перепроверки (реальные поля витрины, тот же кейс, что №14)

Ключевые части ответа API; многоточием отмечены прежние поля, правкой не затронутые.

```json
{
  "result": {
    "kind": "reverified",
    "label": "Ответ есть: подтверждено заново",
    "hint": "Сессия этого вопроса подтвердила уже записанное утверждение — ответ тот же, оценка свежая. Последняя сессия: готово · цель достигнута.",
    "action": "Посмотрите в строке вывода, какая оценка была и какой стала."
  },
  "claims": [
    {
      "statement": "Годовая инфляция по итогам года составила 5,59 процентного пункта",
      "claim_type": "temporal_fact",
      "type_label": "факт на определённое время",
      "head_state": "current",
      "grade_label": "E1 · сверена целостность",
      "reliability": { "level": "weak", "label": "Подтверждено слабо" },
      "relation": "reverified",
      "relation_label": "перепроверено этим вопросом",
      "relation_hint": "Сессия этого вопроса подтвердила уже записанное утверждение: строка осталась прежней, оценка обновилась.",
      "grade_reasons": [
        { "code": "as_of_missing", "label": "у утверждения нет опорной даты" }
      ],
      "reverify_history": [
        {
          "from": { "grade": "E3", "grade_label": "E3 · независимое подтверждение",
                    "epistemic_status": "supported", "status_label": "подтверждено" },
          "to": { "grade": "E1", "grade_label": "E1 · сверена целостность",
                  "epistemic_status": "hypothesis", "status_label": "предположение" },
          "from_reasons": [ { "code": "requirements_met", "label": "требования правил выполнены" } ],
          "reasons": [ { "code": "as_of_missing", "label": "у утверждения нет опорной даты" } ],
          "text": "было: E3 · независимое подтверждение (подтверждено) → стало: E1 · сверена целостность (предположение)"
        }
      ],
      "…": "прежние поля утверждения без изменений"
    }
  ],
  "other_claims": [],
  "work": { "session_id": "сессия перепроверки, а не сессия-якорь" }
}
```

Строка «Моих вопросов» (`GET /api/v1/questions`) для того же вопроса:

```json
{
  "number": 2,
  "text": "Перепроверь по независимому источнику: Годовая инфляция …?",
  "answer": {
    "kind": "reverified",
    "label": "Ответ есть: подтверждено заново",
    "statement": "Годовая инфляция по итогам года составила 5,59 процентного пункта",
    "reliability": { "level": "weak", "label": "Подтверждено слабо" },
    "relation": "reverified",
    "relation_label": "перепроверено этим вопросом"
  }
}
```

Долговременный пункт бэклога витрины «reused existing claim не связан с карточкой нового
вопроса» — **закрыт этой правкой**: и перепроверка, и дедуп-повтор показываются как ответ вопроса
с подписанной причиной.

### 9. Тесты и их краснота на прежнем коде

Новые тесты (`tests/unit/test_web_answer.py`, `tests/scenario/test_web_answer_reverify_card.py`)
и добавленные проверки в существующих (`tests/unit/test_web_labels.py` — полнота словаря по
`RELATION_KEYS`; `tests/scenario/test_reverify_temporal_scenario.py` — пункты (з)–(и)) краснеют на
коде до этой правки. Краснота проверена двумя временными откатами `apps/web` (`git stash`, без
коммита; после проверки изменения возвращены, `git stash list` пуст):

- откат 1: `18 failed, 47 passed` — все шесть сценарных тестов нового файла (карточка
  перепроверки, отсутствие истории при неизменной оценке, частичность, pending head, дедуп-повтор,
  бюджет списка), восемь unit-тестов отношения/истории и четыре теста полноты словаря подписей;
- откат 2 (end-to-end, реальные две сессии с fake LLM): `1 failed` —
  `test_dateless_reverify_keeps_the_E3_of_a_temporal_fact::…` падает на
  `assert [] == ['reverified']`, то есть ровно на симптоме подставки: карточка вопроса
  перепроверки не видит утверждений, хотя сессия отработала и поставила E3/supported.

Ни один прежний тест не ослаблен и не удалён; количество тестов полной проверки выросло
(см. §10).

### 10. Полная проверка §6 перед коммитом

| шаг | результат |
|---|---|
| `ruff check .` | All checks passed |
| `mypy packages apps hostctl` | Success: no issues found in 140 source files |
| образ sandbox | `noezema-sandbox:test` present (пересборка не требовалась) |
| `pytest -n auto -q -m "not timing"` | **1484 passed, 12 skipped** за 2 мин 59 с (до правки — 1470 passed / 12 skipped; +14 тестов: 8 unit-тестов отношения и истории в `tests/unit/test_web_answer.py` и 6 сценарных в `tests/scenario/test_web_answer_reverify_card.py`) |
| `pytest -q -m timing` (отдельный последовательный прогон) | **4 passed**, 1496 deselected |

Отказов и предупреждений в финальном прогоне нет; известная флакающего места (`fake LLM server
exited during startup`) в этом прогоне не было. Остатки окружения до работы и после неё одинаковы:
контейнеров 41 (из них noezema-именованных 2, контейнеров `noezema_mig_*` — 0), в тестовом
Postgres одноразовых баз `noezema_mig_%` 56 и служебных `noezema_(tpl|dbg|clismoke)` 21 — новые
тесты их не добавили и ничего не оставили. Стенд `.92` не трогали; внешних запросов, eval/smoke-
или реальных LLM-сессий (в том числе к `.42`/`.48`) эта задача не делала; миграций и изменений
схемы нет; `AuditEventType`, правила, пороги, конфиг-v*, промпты и пины, реестр инструментов,
политика и оркестратор не изменены; `/engineer` не менялся.

## T7.75 — производные источники: пересказ релиза не даёт второй группы независимости (ADR-0029, вариант B)

Задача закрывается двумя коммитами: код+тесты и этот раздел (анализ был написан до правок кода).
Цель по принципу
пользователя 2026-10-07: исследование вопроса не может опираться только на официальные источники;
пересказ релиза (РИА/Интерфакс/Эксперт, пересказывающие один релиз Росстата) — не независимое
подтверждение, а самостоятельное исследование, которое цитирует официальные данные ради спора с
ними, — независимо и склеиваться с первоисточником не должно.

### 1. Где рождается строка источника и где физически можно записать указатель происхождения

Строка `sources` в продакшн-коде создаётся ровно в одном месте: `ResearchProxyService.fetch`
(`apps/research_proxy/service.py:195–241`) — проверка идемпотентности по `content_hash`
(:195–209) и `INSERT INTO sources … ('external_url', canonical_uri, now(), content_hash, metadata)`
(:215–241), в той же транзакции журнала, что и `artifact_chunks` и аудит
`research_fetch_completed` (:266–286). Orchestrator (`_research_fetch`,
`apps/orchestrator/orchestrator.py:1045–1225`) только читает нормализованный текст обратно из
хранилища, отдаёт его модели fenced и журналирует `research_content_read`; доказательные строки
`evidence` появляются позже — на commit'е, из связей куратора через `StagingService`
(`packages/memory/staging.py`), и лишь связывают уже существующий `sources.id`.

Отсюда выбор точки записи: **fetch-транзакция proxy**. Аргументы:

1. там же, где строка источника появляется, лежит и нормализованный текст страницы
   (`normalized.text`) — единственный вход детектора;
2. производность источника — свойство provenance, а не знание модели: писать её через
   `session_staging` означало бы, что хост меняет знание мимо staging-контура (AGENTS §3), и при
   этом источник, прочитанный без последующего коммита (таких на стенде большинство), указателя бы
   никогда не получил;
3. отметка ставится **только новой** строке (`if not idempotent`): повторный fetch существующего
   содержимого ничего не переоценивает — это и есть «ничего не меняется задним числом».

### 2. Что уже умеют `group_sources` / `source_graph` (и чего в них нет)

`packages/memory/independence.py`: группы строит по registrable domain (PSL-список), общему
`content_hash`, совпадению текста (`TEXT_OVERLAP_THRESHOLD = 0.8`) и — в v2 — по
`parent_source_id`: склейка родителя с ребёнком выполняется, только если родитель **узел этого
графа** (:336–338), а основание пишется как `parent:<short id>` с высшим приоритетом записи
(:384–389). `source_dependency_edges` склеивают только пары, оба конца которых уже узлы
(:354–356), поэтому «указать первоисточник» без строки-родителя ничего не меняет — это прямо
сказано в ADR-0029.

Ключевое свойство загрузчика `build_source_independence_snapshot`
(`packages/memory/source_graph.py:87–105`): прямые родители множества источников добавляются в
**узлы графа**, но не в члены снимка. Значит два пересказа одного релиза склеиваются через общего
родителя, даже если сам первоисточник не является доказательством этого утверждения (и даже если
он вообще никогда не читался) — ровно то, что требуется для случая «первоисточник не прочитан».

`sample_text` в запросе загрузчика — NULL (`:64–66`): ветвь текстового совпадения на реальном пути
inert (замер T7.73 §5), поэтому сегодня три ленты одного релиза дают три группы только потому, что
склейка по parent/edge никогда не заполнялась.

### 3. Как указатель доходит до оценки (механика не меняется, меняется только вход)

`_evaluate_claim_locked` (`packages/memory/service.py:1153–1224`) строит снимок независимости
источников утверждения и отдаёт число групп rules engine; `ClaimTypeRule` читает пороги из
снапшота правил (`claim_type_rules`), а не из кода. Для `external_fact`/`temporal_fact`
`min_independence_groups: 2`, `min_support_evidence: 2`: при трёх свидетельствах в **одной**
группе rules engine выдаёт E1/`hypothesis` с причиной `insufficient_independence`
(`packages/memory/rules_engine.py:208–246`; подпись — «независимых групп меньше нужного»), а не
E3/`supported`. Пороги, шкала, правила и сама логика группировки в этой задаче не тронуты:
изменяется единственное входное число — `len(groups)`.

### 4. Критерий остановки

Не сработал: миграция не нужна (`sources.parent_source_id` уже в модели —
`packages/domain/models/memory.py:173–186`, `migrations/versions/0004_memory.py:205–220`), новый
`AuditEventType` не нужен (используется существующее `research_fetch_completed`, payload'у
добавлены поля), пороги/шкала/rules engine/logика `group_source_graph`/`group_sources` не
изменены, `ARCHITECTURE.md` не менялся, staging-операций и закрытых словарей не добавлено.

Что осталось **за** рамками задачи и здесь только описано: (а) бэкфилл существующих строк
`sources` — требует отдельного решения и вне T7.75; (б) исправление ошибочного склеивания
оператором (вариант C ADR-0029) — потребовало бы либо правки данных (`parent_source_id`), либо
новой staging-операции с новым типом аудита, то есть миграции/расширения закрытого набора;
(в) индекс для поиска строки первоисточника по домену (выраженный индекс по
registrable domain) — это миграция.

### 5. Что делается (решение)

1. **Чистый модуль** `apps/research_proxy/source_attribution.py` (по образцу
   `apps/orchestrator/search_view.py`): ни БД, ни сети, ни сессии; вход — `canonical_uri`
   прочитанной страницы и её нормализованный текст; выход — решение со статусом, ключом/именем
   первоисточника, его домашним URI и фрагментом-основанием. Версия метода
   `host-source-attribution-v1`.
2. **Запись** в той же fetch-транзакции (`apps/research_proxy/service.py`): новой строке `sources`
   проставляется `parent_source_id`, а в `metadata` добавляется объект `derivative_of` (ключ, имя,
   URI первоисточника, версия метода, маскированный фрагмент-основание). Ни одна существующая
   колонка/API-поле не изменены — только добавления.
3. **Аудит существующим событием**: payload `research_fetch_completed` получает поля
   `attribution_status`, `attribution_method`, а при доказанной производности — `derivative_of`
   (имя и URI первоисточника, id родителя). Тип события не меняется.
4. **Витрина без новых подписей**: инженерный провенанс уже выбирает родителя
   (`apps/web/knowledge.py:390–398`, `:545–549`) и уже рисует «← <uri первоисточника>»
   (`apps/web/api.py:824`). Карточка ответа получает одну честную строку в
   `describe_verification` (`apps/web/reliability.py`, литералы человеческих фраз живут там же,
   категориями `labels.py` они не являются): «часть прочитанного — пересказ первоисточника: N».

### 6. Словарь первоисточников и шаблоны атрибуции (дословно)

Словарь — минимальный и расширяемый: новая запись = один элемент кортежа
`PRIMARY_SOURCES` (`key`, `name`, `home_uri`, `home_hosts`, `alias_patterns`); тесты
`tests/unit/test_source_attribution.py::test_registry_is_wellformed` (целостность: домашний URI
принадлежит домашнему хосту, алиасы матают имя) и `::test_registry_extends_by_one_entry`
(расширение словаря одной записью не требует правки детектора). Алиасы — толерантные к падежам
основы (`федеральн[а-яё]* служб[а-яё]* …` ловит «Федеральной налоговой службы») плюс доменные
формы (`rosstat\.gov\.ru`): страница может назвать первоисточник доменом. Самостраница
исключается по **метке хоста**, а не по registrable domain: у `rosstat.gov.ru` и `minfin.gov.ru`
он общий (`gov.ru`; в PSL-списке `MULTI_PART_SUFFIXES` записи `gov.ru` нет) — см. §10 и AGENTS §7.

```python
PRIMARY_SOURCES: Final[tuple[PrimarySource, ...]] = (
    PrimarySource(
        key="rosstat",
        name="Росстат",
        home_uri="https://rosstat.gov.ru/",
        home_hosts=("rosstat.gov.ru",),
        alias_patterns=(
            r"росстат[а-яё]*",
            r"федеральн[а-яё]* служб[а-яё]* государственн[а-яё]* статистик[а-яё]*",
            r"\brosstat\b",
            r"federal state statistics service",
            r"rosstat\.gov\.ru",
        ),
    ),
    PrimarySource(
        key="cbr",
        name="Банк России",
        home_uri="https://cbr.ru/",
        home_hosts=("cbr.ru",),
        alias_patterns=(
            r"банк[а-яё]* росс[а-яё]*",
            r"центр[а-яё]* банк[а-яё]* росс[а-яё]*",
            r"\bцб\s*рф\b",
            r"центробанк[а-яё]*",
            r"bank of russia",
            r"central bank of the russian federation",
            r"cbr\.ru",
        ),
    ),
    PrimarySource(
        key="minfin",
        name="Минфин России",
        home_uri="https://minfin.gov.ru/",
        home_hosts=("minfin.gov.ru",),
        alias_patterns=(
            r"минфин[а-яё]*",
            r"министерств[а-яё]* финансов[а-яё]* росс[а-яё]*",
            r"министерств[а-яё]* финансов[а-яё]* рф",
            r"ministry of finance of the russian federation",
            r"minfin\.gov\.ru",
        ),
    ),
    PrimarySource(
        key="fns",
        name="ФНС России",
        home_uri="https://www.nalog.gov.ru/",
        home_hosts=("nalog.gov.ru",),
        alias_patterns=(
            r"\bфнс[а-яё]*",
            r"федеральн[а-яё]* налог[а-яё]* служб[а-яё]*",
            r"federal tax service",
            r"nalog\.gov\.ru",
        ),
    ),
)
```

Шаблоны указания на первоисточник (алиас должен стоять в пределах
`ATTRIBUTION_WINDOW_CHARS = 120` знаков от шаблона, внутри одного фрагмента — фрагмент это
предложение, режется по `.`, `!`, `?`, `…` и переводу строки):

```python
ATTRIBUTION_TEMPLATES: Final[tuple[str, ...]] = (
    r"по данным",
    r"по информации",
    r"по сообщени[а-яё]*",
    r"сообща[а-яё]*",
    r"как сообщил[а-яё]*",
    r"как сообщает",
    r"согласно",
    r"со ссылкой на",
    r"со слов",
    r"цитиру[а-яё]*",
    r"ссылка[а-яё]* на",
    r"по оценк[а-яё]*",
    r"релиз[а-яё]*",
    r"according to",
    r"as reported by",
    r"per data from",
    r"data from",
    r"citing",
    r"cites",
    r"statement from",
)

MEASUREMENT_MARKERS: Final[tuple[str, ...]] = (
    r"%",
    r"\bпроцент[а-яё]*",
    r"\bп\.?п\.",
    r"\bмлрд\b",
    r"\bмлн\b",
    r"\bтыс[а-яё]*",
    r"\bрубл[а-яё]*",
    r"\bдоллар[а-яё]*",
    r"\bевро\b",
    r"\bpercent\w*",
    r"\brose\b",
    r"\bfell\b",
    r"\bincreas\w*",
    r"\bdecreas\w*",
    r"\bstood at",
    r"\breached",
    r"\bamounted to",
    r"\bcame to",
    r"\brecorded",
    r"\bсоставл[а-яё]*",
    r"\bвырос[а-яё]*",
    r"\bувелич[а-яё]*",
    r"\bсниз[а-яё]*",
    r"\bповыс[а-яё]*",
    r"\bопустил[а-яё]*",
    r"\bподнял[а-яё]*",
    r"\bупал[а-яё]*",
    r"\bсократ[а-яё]*",
    r"\bпревысил[а-яё]*",
    r"\bдостиг[а-яё]*",
)

OWN_ASSESSMENT_MARKERS: Final[tuple[str, ...]] = (
    r"по нашим расч[а-яё]*",
    r"по нашей оценк[а-яё]*",
    r"наш[а-яё]* расч[а-яё]*",
    r"наш[а-яё]* оценк[а-яё]*",
    r"наш[а-яё]* методик[а-яё]*",
    r"мы оцениваем",
    r"независим[а-яё]* оценк[а-яё]*",
    r"независим[а-яё]* расч[а-яё]*",
    r"самостоятельн[а-яё]* оценк[а-яё]*",
    r"оценка редакции",
    r"в отличие от",
    r"расхождени[а-яё]*",
    r"не совпада[а-яё]*",
    r"противореч[а-яё]*",
    r"our own estimate",
    r"by our estimate",
    r"we estimate",
    r"independent estimate",
    r"in contrast to",
    r"contradict\w*",
    r"disagree\w*",
    r"differs from",
)

VALUE_NUMBER_PATTERN: Final = re.compile(r"\d[\d.,\u00a0\s]{0,15}\d|\d+")
```

Значение (третий обязательный член пары «значение + первоисточник») — числовой токен
`VALUE_NUMBER_PATTERN` (`\d[\d.,\u00a0\s]{0,15}\d|\d+`) **плюс** хотя бы один маркер измерения из
`MEASUREMENT_MARKERS` в том же фрагменте: голое число (дата года, номер статьи, «в 2025 году по
данным Росстата») значением не считается.

### 7. Правила «не производный» (пять отрицаний, все закреплены тестами)

1. **Только пара.** Отметка ставится, если в одном фрагменте собрались алиас первоисточника,
   шаблон атрибуции и значение. Голые упоминания организации («Росстат опубликует релиз 16
   января», «ведомство подчиняет…») — не производная (`no_value_attribution`).
2. **Своя оценка побеждает.** Если в тексте встречи любой маркер `OWN_ASSESSMENT_MARKERS`
   («по нашим расчётам», «независимая оценка», «наша методика», «в отличие от», EN analogs) —
   страница считается самостоятельным исследованием и не склеивается никогда
   (`own_assessment`), даже если рядом упоминается первоисточник и другое число. Вето
   намеренно широкое: ложный пропуск безопаснее ложной склейки.
3. **Сомнение = отказ.** Если в тексте доказанно атрибутированы значения **двум** разным
   первоисточникам из словаря, отметка не ставится (`ambiguous_primaries`).
4. **Никогда не от себя.** Страница не помечается производной от первоисточника, которым она сама
   является: если хост страницы — домашний хост кандидата (`is_home_host`: равен либо поддомен),
   этот кандидат исключается (rosstat.gov.ru не может быть пересказом Росстата). Сравнение — по
   метке хоста, а не по registrable domain: `registrable_domain("https://rosstat.gov.ru/") ==
   registrable_domain("https://minfin.gov.ru/") == "gov.ru"`, и по registrable domain страница
   одного гос-ведомства была бы «своей» для другого. При этом
   первичная публикация **может** быть признана пересказом другого первоисточника: строгий запрет
   «страница первоисточника никогда не помечается» оставил бы cbr.ru отдельной группой и сохранил
   бы E3 ровно в том случае, из-за которого поставлен принцип пользователя. Остаточный риск этой
   формулировки (две по-настоящему разные первичные публикации склеятся, если одна приводит
   значение другой) закрыт правилом 3 — два разных первоисточника в одном тексте дают отказ — и
   описан в §10 как ограничение.
5. **Детектор решает только новые прочтения.** Повторный fetch уже известного содержимого
   идемпотенен и ничего не дописывает; существующие claims/оценки задним числом не меняются.

### 8. Первоисточник не прочитан: якорь-узел (как склеиваются пересказы между собой)

Склейка детей одного родителя требует строки-родителя в графе (§2). Если строки первоисточника
нет, `apps/research_proxy/service.py` создаёт **декларированный якорь**: `sources` с
`source_type='external_url'`, `canonical_uri = <home_uri из словаря>`, `content_hash IS NULL`,
`retrieved_at IS NULL` и `metadata.declared_primary_anchor = true`. Это возможно без миграции:
CHECK на `source_type` закрытый, но удовлетворён; единственные ограничения `sources` — PK и FK на
`parent_source_id` (`migrations/versions/0004_memory.py:205–220`), `content_hash` nullable. Якорь
не является доказательством (в `evidence` он не появляется, в члены снимка независимости
загрузчиком не включается) и честно помечен как **не прочитанный узлом**: у него нет ни контента,
ни времени получения.

Порядок разрешения родителя детерминирован (`_resolve_primary_source`): сначала уже прочитанная
строка первоисточника того же домашнего хоста и **без своего родителя** (последняя по
`retrieved_at DESC NULLS LAST`, просмотр не более `PRIMARY_LOOKUP_SCAN_LIMIT = 500` строк), затем
существующий якорь этого словарного ключа, затем новый якорь. Ограничение «родитель сам
первоисточник, а не пересказ» — новое правило проволочки: без него цепочка новость → ЦБ → Росстат
транзитивно склеила бы два разных первоисточника в одну группу (`parent_source_id NOT NULL`
родителем быть не может). Если первоисточник потом действительно прочитают, его строка попадёт в
тот же registrable domain и сливается с якорем алгоритмически (основание `domain:<d>`), то есть
пересказ + первоисточник оказываются в одной группе — как и должно быть.

Обратимость честно ограничена: коррекция `split` в `source_graph_corrections` отменяет только
прямые relation-основания (edge/correction), но не алгоритмические факты, к которым относится
parent (`packages/memory/independence.py:26–33`, :340–356). Отменить ошибочную склейку можно лишь
изменив данные (обнулить `parent_source_id`) — это вариант C ADR-0029 и он остаётся остатком.
Именно поэтому детектор консервативен (§7), а не «широкий».

### 9. Ожидаемый эффект на стенде и что видит человек

Ничего из уже оцененного не меняется: утверждение `9266248e` («инфляция 2025 = 5,59%») и его
оценка E3 остаются как есть — отметки ставятся только новым прочтениям. Бэкфилл (разметка
существующих строк `sources`) вне рамок T7.75: он требует отдельного решения о допустимости
заднего изменения provenance и, по сути, это тот же вариант C.

Новая перепроверка того же вопроса **теми же источниками** (cbr.ru + expert.ru + interfax.ru +
ria.ru, каждый пересказывает релиз Росстата) после этой правки даёт одну группу независимости с
основанием `parent:<id якоря>` вместо четырёх `single` — при условии, что каждая из страниц сама
называет первоисточник вместе со значением (иначе она остаётся отдельной группой: детектор не
додумывает, он читает текст). Rules engine при одной группе вместо двух честно выдаёт
E1/`hypothesis` с причиной `insufficient_independence` («независимых групп меньше нужного»,
«Проверьте утверждение по источнику из другой независимой группы») — E2 этой шкалой при одной
группе не получается: для `external_fact`/`temporal_fact` `min_grade_for_supported = E3`, а
невыполненные требования дают E1/hypothesis. Снижение оценки ожидаемо и соответствует принципу
пользователя: три-четыре пересказа одного релиза не являются независимым подтверждением. Если к
ним добавятся реально независимые работы (свои расчёты, другая методика, другое число), групп
станет две, и оценка вернётся к E3 — уже за настоящую независимость.

Что видно человеку: на инженерном провенансе у такой строки источника появляется ссылка родителя
(«← https://rosstat.gov.ru/») — уже существующий UI; в `verification` карточки ответа — строка
«часть прочитанного — пересказ первоисточника: N»; в журнале узла — `research_fetch_completed` с
`derivative_of.name = "Росстат"` и статусом атрибуции. Никаких новых подписей `labels.py`, новых
enum-значений и новых категорий полноты не появилось.

### 10. Реализовано (коммит `0621334`): где именно записывается производность

Чистый модуль `apps/research_proxy/source_attribution.py` (366 строк, без LLM и без сети; статусы
`derivative`, `own_assessment`, `ambiguous_primaries`, `no_value_attribution`, `self_primary`;
потолки `MAX_SCAN_CHARS = 40_000`, `BASIS_FRAGMENT_CHARS = 300`, `ATTRIBUTION_WINDOW_CHARS = 120`,
версия метода `ATTRIBUTION_METHOD_VERSION = "host-source-attribution-v1"`).

Запись — в fetch-транзакции proxy (`apps/research_proxy/service.py`):

- `:166–168` — решение детектора по нормализованному тексту и `final_url` (до записи строк);
- `:291–293` — только новой строке источника и только при доказанной производности:
  `_mark_derivative` (`:445–…`) делает `UPDATE sources SET parent_source_id = :parent,
  metadata = metadata || {"derivative_of": {key, name, uri, method, basis_fragment}}`
  (`:457–470`); ни одной новой колонки и миграции нет;
- `_resolve_primary_source` (`:372–444`) — порядок родителя: прочитанная строка того же домашнего
  хоста без своего родителя (окно просмотра `PRIMARY_LOOKUP_SCAN_LIMIT = 500`, `:76`, `:393`) →
  существующий якорь → новый якорь (`:425–440`: `content_hash IS NULL`, `retrieved_at IS NULL`,
  `metadata.declared_primary_anchor = true`);
- аудит существующим событием `research_fetch_completed`: `attribution_status`,
  `attribution_method`, и при производности `derivativity_written` (различает «узнали пересказ» и
  «проставили указатель»: повтор идемпотентен и указатель не дописывает) плюс `derivative_of` с id
  родителя (`:309–330`); конверт `research.fetch` — `attribution_status` и `derivative_of`
  (`:362–370`, только добавления); `public_summary` дописывает « · пересказывает: Росстат»
  (`:341`).

Что происходит, когда первоисточник не прочитан: создаётся декларированный якорь — единственный
общий родитель всех пересказов. Загрузчик снимка (`packages/memory/source_graph.py:87–105`) включает
прямых родителей в узлы графа, но не в члены снимка, поэтому пересказы склеиваются через якорь, не
становясь доказательством и не получая веса; при последующем реальном прочтении первоисточника его
строка попадает в тот же домашний хост и сливается с якорем алгоритмически (основание `domain:<d>`).

Витрина: карточка ответа выбирает `s.parent_source_id` и `ps.canonical_uri AS parent_uri`
(`apps/web/answer.py:828`, `:831`), `describe_verification` формулирует по этим данным
«часть прочитанного — пересказ первоисточника: N» (`apps/web/reliability.py:240–246`, форма строки
— через `_parent_ref`, `:283–299`: вложенный `parent` инженерного провенанса или плоские колонки
карточки). Инженерный провенанс («← https://rosstat.gov.ru/») показывал parent и раньше
(`apps/web/knowledge.py:390–400`, `:540–552`). Новых enum-значений, подписей `labels.py` и
аудит-типов нет: причина `insufficient_independence` подписана там уже (`apps/web/labels.py:1523`).

### 11. Тесты (29 новых), их краснота на прежнем коде и замер до/после

| тест | что закрепляет |
|---|---|
| `tests/unit/test_source_attribution.py` (24) | RU- и EN-пересказы → `derivative`; своя оценка с другим числом, «в отличие от», `independent estimate` → `own_assessment`; голое упоминание организации и даты/счётчики без значения → `no_value_attribution`; два первоисточника в одном тексте → `ambiguous_primaries`; страница первоисточника от себя → `self_primary`; cbr.ru со ссылкой на Росстат → `derivative_of = rosstat`; граница сканирования (атрибуция за `MAX_SCAN_CHARS` не находится) и детерминизм `basis_fragment` (300 знаков, NUL маскирован, литерал fence нейтрализован); словарь самосогласован и расширяется одной записью; `is_home_host` ≠ registrable domain |
| `tests/scenario/test_derivative_attribution.py` (4) | настоящий `research.fetch` + staging + rules engine при подменённом `FetchClient` (`FakeFetchClient`, внешних запросов нет): три пересказа одного релиза на трёх регистрируемых доменах → один снимок из трёх источников, одна группа с основанием `parent:<id якоря>`, оценка E1/`hypothesis` с причиной `insufficient_independence`; якорь ровно один и честный (`content_hash IS NULL`, `retrieved_at IS NULL`); `derivative_of` в provenance и в журнале; независимая оценка со своим числом и спором с Росстатом → отдельная группа `single`, склейки нет; повторный fetch идемпотенен (`derivativity_written = false`, один якорь, две строки `sources`); карточка называет пересказ серверной фразой, а без указателя фразы нет |
| `tests/unit/test_web_reliability.py::test_retelling_is_named_only_when_the_host_recorded_the_pointer` | фраза появляется только при наличии указателя (плоская и вложенная формы), presentation не додумывает происхождение |

Краснота доказана временным откатом кода (`git stash push -u` по четырём изменённым
файлам, без коммита): на прежнем коде `tests/scenario/test_derivative_attribution.py` — 4 failed,
новый тест надёжности — 1 failed, `tests/unit/test_source_attribution.py` — ошибка сборки (нет
модуля), при этом 18 прежних тестов модуля надёжности остались зелёными (ничего не ослаблено).

Замер «до/после» на одном и том же сценарии (временный скрипт, в историю не попал): три пересказа
релиза + staging + rules engine. **До правки:** 3 группы независимости с основанием `single` ×3 и
оценка `E3 / supported` — ровно стендовый дефект `9266248e`. **После:** 1 группа с основанием
`parent:<id якоря>` и оценка `E1 / hypothesis`, причина `insufficient_independence`.

Отдельная находка про тестовые данные: первая версия fixture использовала хосты
`interfax.example.ru / expert.example.ru / ria.example.ru`, и все три свернулись в один
registrable domain `example.ru` (PSL-список не знает `example.ru`) — группы сливались и без новой
логики, дефект был бы незаметен. fixture переписан на реально различные домены (`interfax.example /
expert.example / ria.example`); проверка «домены действительно разные» обязательна для любых
тестов независимости.

Регрессия: подмножество research/independence/web/security (19 файлов) — 193 passed. Полная
проверка §6: ruff без замечаний, mypy strict — Success (141 файл), `-n auto -m "not timing"` —
**1513 passed, 12 skipped** (было 1484 + 12), затем последовательный `-m timing` — **4 passed**.

### 12. Остаток, пределы и риски (что осталось честной недоработкой)

1. **Бэкфилла нет.** Утверждение `9266248e` и его E3 не меняются задним числом: отметки ставятся
   только новым прочтениям. Разметка прежних строк `sources` — отдельное решение о допустимости
   изменения provenance (по сути вариант C).
2. **Ошибочную склейку нельзя отменить существующими средствами.** `source_graph_corrections{split}`
   снимает только прямые relation-основания; parent — алгоритмический факт
   (`packages/memory/independence.py:26–33`, :340–356). Средство исправления — вариант C ADR-0029,
   он требует новой staging-операции и (вероятно) нового `AuditEventType` → вне рамок T7.75.
   Поэтому детектор намеренно консервативен: ложный пропуск безопаснее ложной склейки.
3. **грубость registrable domain для `.gov.ru` (найдено до правки, усугублено parent-связью).**
   `registrable_domain("https://rosstat.gov.ru/") == registrable_domain("https://minfin.gov.ru/") == "gov.ru"`
   (`MULTI_PART_SUFFIXES` не содержит `gov.ru`), поэтому якорь Росстата может транзитивно соединить
   с пересказами и строки других `.gov.ru`-ведомств. Это уже существующая черта группировки, а не
   новая: правка PSL-списка меняет группы всех прежних прогонов (порог группировки) — отдельное
   решение пользователя. Внутри детектора эта грубость обойдена сравнением меток хоста.
4. **Потолок сканирования.** `MAX_SCAN_CHARS = 40_000`: указание на первоисточник в конце очень
   длинной страницы не будет замечено (детектор детерминирован, но это пропуск, а не ошибка).
5. **Разрез по точкам.** Фрагмент — предложение (`[.!?
…]+`), десятичные значения («5.59 %») могут
   быть рассечены; пара обычно выживает, потому что глагол/единица остаются в том же фрагменте, но
   гарантией это не является (русский стандарт «5,59%» безопаснее).
6. **Смешанные страницы.** Страница, которая приводит чужую оценку и официальное число, при наличии
   любого маркера собственной оценки получает вето на уровне всего документа → не склеивается
   (возможный пропуск производности). И наоборот: страница, честно пересказывающая первоисточник и
   добавляющая свои рассуждения без маркеров из списка, будет склеена.
7. **Словарь мал.** Четыре записи (Росстат, Банк России, Минфин России, ФНС России); многословные
   падежные формы вне основ алиасов и первоисточники вне словаря не опознаются — расширение одной
   записью кортежа не требует правки детектора (тест на расширение есть), но это ручная работа.
8. **Цепочка в один шаг.** Родителем может быть только строка без своего родителя: новость → ЦБ →
   Росстат не склеивает новость с Росстатом транзитивно (иначе два разных первоисточника оказались
   бы в одной группе). Для трёх-звенных цепочек нужна отдельная механика.

Стоп-критерий задачи не сработал: миграций, изменения порогов/шкалы, правок правил rules engine и
`ARCHITECTURE.md`, новых `AuditEventType` не потребовалось.

## T7.76 — двусторонний поиск: почему узел ищет только официальную сторону и не видит ни своего бюджета, ни схем инструментов. Анализ до правок

Требование пользователя (2026-10-07): «при исследовании вопроса опираться не только на официальные
источники, которые могут подтасовывать расчёты, но и на независимые исследования». После T7.75
хост склеивает пересказы одного первоисточника в **одну** группу независимости
(`apps/research_proxy/source_attribution.py`, ADR-0029 вариант B), поэтому спорное число без
независимой оценки больше не может получить `E3`: оно честно падает до `E1 / insufficient_independence`
(замер T7.75 §11). Значит «Проверено» для спорных величин теперь **требует** второй стороны поиска.
Задача выясняет, почему узел её не ищет, и чинит это средствами контекста и промпта.

### 1. Что реально сделано на подставке .92 (read-only дампы `/home/denis/dsh1/stand-case-inflation-reverify/`)

Вопрос — годовая инфляция РФ за 2025 год (`claim 9266248e…`, «5,59% — официальный показатель Росстата»).
Действия исследователя по `audit_events` (по `sequence`):

| сессия | шаги (`action_started`) | чем кончилось |
|---|---|---|
| `66c7901a` (первичная проверка) | 3: `web.search`(«Банк России инфляция декабрь 2025 года годовой уровень официальные данные») → `research.fetch` cbr.ru/analytics/dkp/dinamic/CPD_2025-12/ → `research.fetch` expert.ru/news/godovaya-inflyatsiya-… | E3 / supported / 0.75, группы `single` ×2 (cbr.ru, expert.ru) |
| `da2abfc1` (перепроверка) | 8 из 10: `web.search`(«Росстат_consumer price index декабрь 2025 инфляция 5,59% 2025 год») → `research.fetch` cbr.ru (тот же адрес, что в прошлой сессии) → `web.search`(«Росстат пресс-релиз декабрь 2025 цены выросли 5,59% rosstat.gov.ru») → `research.fetch` rosstat.gov.ru/storage/mediabank/3_16-01-2026.html (**отказ**, timeout) → `research.fetch` **тот же URL второй раз** (отказ) → `research.fetch` ria.ru/20260116/rosstat-2068393962.html → `research.fetch` rbc.ru/rbcfreenews/… (**отказ**, 401) → `research.fetch` interfax.ru/business/1068021 | E1 / hypothesis / 0.30 (`as_of_missing` — разбор T7.73); после T7.75 та же работа дала бы и вторую причину: все четыре адреса — орбита одного релиза Росстата |

Ни в одной из двух сессий узел не задал ни одного вопроса о **независимой** оценке: оба
`web.search` были «где официальный релиз», все прочитанные страницы — сам регулятор или пересказ
его релиза. То есть отсутствовала не механика оценки, а **вторая половина процедуры исследования**.

### 2. (а) Сколько шагов сжигает официальная сторона и сколько нужно независимой

- Официальная сторона на живом стенде: **3 шага** при удачном стечении (`66c7901a`) и **8 из 10**
  при перепроверке (`da2abfc1`), причём 4 из этих 8 — впустую: повторное чтение адреса, уже
  прочитанного в прошлой сессии (cbr.ru → дедуп на фиксации), два захода на один и тот же
  `rosstat.gov.ru` с таймаутом, и `rbc.ru` с 401. На бюджете `max_explorer_steps = 10`
  (config-v15 `session_limits`) этого хватило, чтобы **не дожить** до независимой стороны: после
  прочтения `interfax.ru` у сессии осталось 2 шага — ровно на complete и предложение куратора.
- Минимум для честной двусторонней проверки по плану задачи: **5 шагов** — поиск официального
  первоисточника, его чтение, отдельный поиск независимой оценки, её чтение, завершение.
  Реалистично с отказами сетей (таймауты и 401 — обычное дело на стенде) это **8–12 шагов**: по одному
  поиску+чтению на каждую сторону плюс 2–4 шага на сбойные адреса и повторные формулировки запроса.
- **Модель не видит ни лимита, ни остатка.** `max_explorer_steps` читается хостом
  (`apps/orchestrator/orchestrator.py:736`, нижняя граница при именованных источниках вопроса — :742)
  и используется только как верхняя граница цикла `for step in range(1, max_explorer_steps + 1)` (:1454).
  В контекст шага (`_explorer_context`, :1949–2025) не попадает ни номер шага, ни остаток: там есть
  только список инструментов, пакет, наблюдения с префиксами `[N]` (индексы наблюдений/evidence, не
  шаги) и строка «Предложи ровно одно следующее действие». grep по `max_steps`/«осталось» по `apps/`
  находит только циклы — нигде эти числа не рендерятся. Следствие: у модели нет обратной связи о
  расходе, она тратит шаги до хостового терминала `budget_exhausted` (SMOKE-V13-K2 Go-сессия —
  «7 шагов, 353 с» на бюджете 10; SMOKE-V14B `b2ab6bf9` — 9 шагов, 509 с). Отказ действия тоже
  стоит шаг: отклонённый complete из-за непокрытых именованных источников (:1540–1561) и отказ
  политики (:1589–1660) не «дешевле» успешного вызова.
- **Гейт повторности есть, но не по адресу.** `TOOL_REPEAT_DENY_LIMIT = 2` (:149), ключ — хеш
  пары (инструмент, аргументы) (`arguments_hash`, :1586), отказ при `rep_count >= 2` (:1645) с
  наблюдением «результат уже есть». Почему он не помог на стенде:
  1. лимит **разрешает два исполнения** идентичного вызова — второй заход на `rosstat.gov.ru`
     состоялся и стоил шаг (и upstream-квоту), отказал бы только третий;
  2. формулировки `web.search` различались («инфляция декабрь 2025» vs «пресс-релиз … rosstat.gov.ru») →
     разные хеши → гейт их не видит, а upstream-запрос и шаг уплачены дважды;
  3. счётчик живёт внутри `_explorer_loop` (локальный `tool_call_counts`, :1453), то есть перечитывание
     адреса, прочитанного **в прошлой сессии** (cbr.ru), вообще не учитывается.
   Семантику этого гейта закрепляет существующий тест `tests/scenario/test_research_provenance.py:405–485`
   (identical fetch исполняется ровно `TOOL_REPEAT_DENY_LIMIT` раз, отказ один) — сужать его молча
   нельзя: легитимный повторный fetch нужен для проверки свежести содержимого (SMOKE-V14B §«повторный
   fetch с новым content hash»).

### 3. (б) Почему модель несёт `search_statements` в аргументы `question.create`

Что модель **видит** об этом инструменте (ровно это, ничего больше):

1. В контексте шага — только имя. `_explorer_context` (:1955–1958) выдаёт
   `# Доступные инструменты\n<имена через запятую>\nТолько этот список существует; другие инструменты вызывать нельзя.`
   Описаний и схем аргументов там нет. Реестр их содержит — `model_tools_schema(profile)`
   (`packages/policy/tools.py:139`) возвращает `{name, description, arguments: args_model.model_json_schema()}` —
   но оркестратор эту функцию **никогда не вызывает**: её импортируют только
   `tests/unit/test_policy_tools.py` и `tests/unit/test_search_tool_contract.py`. При этом
   `ARCHITECTURE.md §5.4.1` (строка 673) резервирует под это бюджет: «протокол, **схемы инструментов**,
   правила — 4096». То есть зарезервированная секция просто не заполняется.
2. В структурном конверте — пустая объектная клетка. Шлюз отдаёт движку
   `response_schema.model_json_schema()` с `strict: True` (`packages/llm_gateway/client.py:123–137`),
   а `Decision.arguments` — `dict[str, Any]` без свойств (`packages/domain/schemas/decision.py:74`).
   Ни одна схема аргументов конкретного инструмента в генерацию не передаётся; контроль постфактум —
   `_Args(extra="forbid")` (`packages/policy/tools.py`, `QuestionCreateArgs` :59) и
   `policy_engine.evaluate(tool, args)` (:1589), откуда и стендовый отказ вида
   `argument ('search_statements',): Extra inputs are not permitted`. Отказ **не бесплатен**: он
   записывает `ACTION_FAILED` и сжигает шаг цикла.
3. Откуда берутся поля чужого конверта — из хостового же текста. Хостовая секция «Протокол действий»
   (`_protocol_text`, :2027–2055) попадает в контекст **исследователя** (:1981, рендер пакета) и прямо
   велит: «укажи их в поле `dependencies` предложенного claim (список claim id)» (:2040–2042) и
   «Для каждого предложенного claim заполни `search_statements` — 1–2 англоязычных варианта
   формулировки (только для поиска)» (:2043–2046). Но исследователю нечем предложить claim: эти поля
   живут только в кураторском конверте (`packages/domain/schemas/staging.py:66` `search_statements`,
   `prompts/curator/curator-v8.md:60–80` `dependencies`), а куратор данный протокол **не читает** —
   `_curator` (:2091–2100) собирает свой user-контекст из знания, evidence и верификации, без секции
   `protocol`. Модель делает единственное доступное ей действие: пишет знакомое имя в аргументы того
   инструмента, который ей реально выдан (`question.create`). Та же ловушка уже давала
   `memory.search` с лишним `limit` (разбор SMOKE-V12, §5 настоящего файла) и три отказа `artifact.create`
   у K2 (`docs/eval/MODEL-SELECTION-report.md` §7).
4. Промпт уже запрещает, но не поясняет. explorer-v7 правило 2 запрещает перенос полей чужих схем
   (`prompts/explorer/explorer-v7.md`; тот же дефект описан в `tests/unit/test_explorer_prompt_search_rules.py`),
   однако ни промпт, ни контекст не говорят, какие аргументы `question.create` **разрешает**. Итог —
   запрет без знания: модель угадывает и ошибается, теряя шаг на каждую догадку.

**Минимальная правка (не ослабляющая `extra="forbid"`):** показать в контексте шага точные схемы
аргументов именно выданных инструментов — аддитивно, из того же реестра (`model_tools_schema`), с
фильтром по `allowed_tools`, чтобы не противоречить пошаговой фильтрации списка (T7.13) и не менять
`tool_schema_hash` шага (он считается от `tool_schema_hash(allowed_tools)`, :1483 — текст контекста на
него не влияет). Дополнительно — назвать владельца полей (`dependencies`/`search_statements`/`as_of` —
предложение куратора, а не аргумент инструмента) в хостовом протоколе и в промпте explorer-v8. Контроль
и `extra="forbid"` остаются как были: меняется только то, что модель знает до генерации.

### 4. (в) Как должна звучать двусторонность в explorer-v8

Процедура для спорных чисел (экономика, статистика, политика) — три шага вместо одного:

1. **Официальная сторона:** один `web.search` по первоисточнику (ведомство, релиз, первичная публикация)
   и одно `research.fetch` выбранной страницы. Хватает одной пары «поиск + чтение»: повторное чтение
   того же адреса новых знаний не добавляет (правило T7.9 про дедуп evidence), а группа независимости от него
   не растёт (T7.75).
2. **Обязательная независимая сторона:** отдельный `web.search`, сформулированный именно про
   независимую/альтернативную оценку — например «независимая оценка инфляции 2025»,
   «альтернативные оценки инфляции», «наблюдаемая инфляция», «inflation estimate independent Russia 2025» —
   и чтение найденного. Поиск «того же релиза ещё раз» вторым шагом не считается.
3. **Сравнение:** назвать обе величины и расхождение (или честное «расхождений не найдено»). Если
   независимых оценок найти не удалось — сказать «независимых оценок не найдено»: это результат, и он
   объясняет `E1 insufficient_independence` в карточке лучше, чем молчание.

Отдельные правила, которые должны стать тестуемыми: не перечитывать тот же URL; беречь шаги — знать
свой остаток бюджета и не делать второй идентичный вызов (гейт и так отказывает на третьем); спорное
число без второй стороны — не «ответ», а половина проверки.

### 5. Стоп-критерий

Правка удерживается в рамках: новый промпт `explorer-v8` (новый файл, пины v1…v7 не тронуты),
аддитивный хостовый текст в контексте шага (схемы аргументов из существующего реестра + остаток шагов +
отметка повторного чтения адреса), новый payload `config-v16` и дефолт dev-стенда. Не потребовались:
правка `ARCHITECTURE.md`, миграция, новое значение `AuditEventType`, новый инструмент, изменение
правил/порогов rules engine и шкалы grade. Критерий «описать, не делать» не сработал.

### 6. Реализация (коммиты `bf73349` — анализ, `30fe681` — код, промпт, payload и тесты)

**(а) Новый чистый модуль `apps/orchestrator/tool_context.py` (146 строк: ни БД, ни сети, ни состояний).**

- `render_tool_argument_schemas(allowed_tools)` — блок «# Схемы аргументов выданных инструментов»:
  `- web.search(query: str) — <описание из реестра>`. Схема берётся из единственного источника истины —
  `get_tool(name).args_model.model_json_schema()`, то есть ровно из того же механизма `model_tools_schema`,
  который до этой задачи **использовали только тесты** (ловушка записана в AGENTS §7). Необязательное поле с
  дефолтом подписано «необязательно (по умолчанию …)», обязательное — без пометки. Неизвестный инструмент не
  даёт строки вовсе: хост не описывает то, чего нет. Ограничение `TOOL_SCHEMAS_CHAR_LIMIT = 4000` снимает
  **целые строки**, поэтому схема никогда не обрывается на середине (схема посреди строки — это уже не схема).
- `render_step_budget(step, step_limit)` — «# Бюджет шага»: «Шаг 3 из 16; осталось действий: 13» плюс цена
  отказа: «Отклонённое действие (лишние аргументы, повтор того же вызова, отвергнутый complete) тоже стоит
  один шаг». Если лимита нет — строки нет: хост не выдумывает бюджет.
- `fetch_url_key` + `render_repeat_fetch_note` — канонический ключ адреса (схема, фрагмент и хвостовой слэш
  выровнены, NUL маскируется) и отметка **следующего** шага: «[N] повтор того же адреса: первая попытка была
  на шаге M. Новых знаний этот заход не обещает, второй группы независимости от него нет. Для второй стороны
  проверки нужен другой адрес.»

**(б) Оркестратор (`apps/orchestrator/orchestrator.py`).** В контекст шага исследоватора добавлены три
хостовых блока: схемы аргументов — сразу после списка инструментов («Только этот список существует; другие
инструменты вызывать нельзя»), бюджет шага — следом, отметка повтора — в хвост наблюдений (`observations`).
`_explorer_loop` ведёт `fetched_urls: dict[str, int]` (адрес → шаг первой попытки) и ставит отметку, когда
`research.fetch` повторяет уже прочитанный адрес. Бюджет контекста поднят ровно на надбавку:
`EXPLORER_CONTEXT_BUDGET = RESEARCH_CONTEXT_BUDGET + 8_000 + TOOL_CONTEXT_ADDENDA_CHARS` = 40000 + 8000 + 4400
= **52400** (было 48000). `_protocol_text` переписан: поля кураторского конверта (`claim_type`, `as_of`,
`scope`, `dependencies`, `search_statements`, `evidence_links`) названы **аргументами инструмента не
являются**. Порядок секций, усечение по свежести (T7.10) и гейт `HARD_SECTIONS = ("protocol",)` не тронуты;
`tool_schema_hash(allowed_tools)` не тронут — текст контекста в него не входит.

**Ничего не запрещено заново и ничего не ослаблено.** `extra="forbid"` в `_Args` реестра,
`policy_engine.evaluate`, гейт повтора `TOOL_REPEAT_DENY_LIMIT = 2` и форма отказа
(«argument ('search_statements',): Extra inputs are not permitted») — как были. Повторный fetch того же
адреса получает **видимую отметку**, а не новый отказ: семантика повтора закреплена
`tests/scenario/test_research_provenance.py`, менять её в этой задаче было бы правкой прецедента. Схема,
отправляемая шлюзу (`model_tools_schema`), и раньше была строгой — дефект был в тексте, который читала
модель, а не в валидации.

**(в) explorer-v8 (`prompts/explorer/explorer-v8.md`, sha256 `eff45071…fb2e`).** Правила 1–9 совпадают с v7
байт в байт (закреплено тестом, включая трёхпробельные продолжения строк — без них сравнение было бы
ложным). Добавлены два правила:

- **правило 10 — двусторонний поиск:** «Спорное число (экономика, статистика, политика) проверяется ДВУМЯ
  сторонами поиска, а не одной. Официальная сторона: один `web.search` по первоисточнику … и одно
  `research.fetch` выбранной страницы — этой пары достаточно, перечитывать тот же адрес второй раз смысла
  нет. Затем ОБЯЗАТЕЛЬНЫЙ отдельный `web.search`, сформулированный именно про независимую оценку:
  „независимая оценка инфляции 2025“, „альтернативные оценки инфляции“, „наблюдаемая инфляция“, „inflation
  estimate independent Russia 2025“ — и чтение найденного. Поиск „того же релиза ещё раз“ второй стороной не
  считается: это тот же источник (правило 9). Найденное сравни: назови обе величины и расхождение, либо
  честно „расхождений не найдено“. Если независимых оценок найти не удалось — так и скажи в
  `public_rationale`: „независимых оценок не найдено“. Это результат, а не провал: он объясняет пониженную
  надёжность ответа лучше молчания.»
- **правило 11 — конечность шагов:** остаток бюджета написан в контексте (раздел «Бюджет шага»), планировать
  расход надо так, чтобы вторая сторона дожила до своего поиска и чтения; не тратить шаги на повторное
  чтение прочитанного, вызовы с заведомо лишними аргументами и перефразирование одного и того же запроса —
  «отклонённое действие стоит столько же, сколько успешное».

Правило про первоисточник (9) и запрет подставлять правительственный пресс-релиз вторым источником (в составе
9) сохранены без изменений — v8 не отменяет v7, а дополняет его.

**(г) config-v16 (`docs/eval/config-v16-payload.json`).** Отличия от v15 — ровно два: пин
`prompts.explorer` → `{path: "prompts/explorer/explorer-v8.md", sha256: "eff45071…", version: "explorer-v8"}`
и `session_limits.max_explorer_steps`: 10 → **16**. Всё остальное совпадает побайтово: кураторский пин
curator-v8 (`c14603ee…`), `research_proxy` (curated, SearXNG `127.0.0.1:8888`, `private_allowlist`,
rate limit 20/3600 с), `policy.capabilities` (те же 10 инструментов), `claim_type_rules` (внешний факт: 2
подтверждения, **2 группы независимости**, минимум E3), TokenBudgets, schedule, модель. Canonical-хеш payload
(тот, что ложится в `config_snapshots.payload_sha256`) — `740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3`;
хеш файла — `79d63b2d380775621495ad2445ce3610484c8ce5fd3c5f857f65f26a7817d131`; это разные числа и путать их
нельзя (AGENTS §8). Прежние payload'ы config-v1…v15 не переписаны.

**Дефолт dev-стенда:** `bootstrap.sh` и `reset-db.sh` активируют config-v16; откат —
`NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v15-payload.json` (canonical `b3801812…`).

### 7. Бюджетная арифметика: почему 16 шагов, а не 10 и не 24

- цена шага измерена, не предполагана: сессии SMOKE-V14B — 3/7/3/2/4/9/3 шага за 48/305/28/21/116/509/74 с
  (≈27–57 с на шаг); подставка .92: `da2abfc1` — 8 шагов из 10, ≈57 с на шаг; V13-K2: 5 шагов = 262 с,
  7 шагов = 353 с. Задержка `exploring` медианно 16,4 с, максимум 180,1 с (замер T7.75).
- 16 шагов × ≈57 с ≈ **912 с** исследования + куратор ≤180 с < `phase_deadline_seconds = 1800` и
  `session_timeout_seconds = 1800` (оба не изменены). 24 шага (~1370 с) уже упёрлись бы в дедлайн с учётом
  куратора и записи.
- лимит поиска не превышается: максимум 16 upstream-запросов против `rate_limit_max = 20` на 3600 с (запас 4);
  журнал запросов (`research_upstream_request`) считали раньше — он же и ограничивает.
- надбавка к контексту шага измерена на реальном payload'е: блок схем для 10 curated-инструментов —
  **1750 знаков / 2684 UTF-8 байта** (при потолке 4000) ≈ **394 токена**; строка бюджета — 155 знаков при
  потолке 400. Текст протокола после переписывания — 1179 знаков ≈ 231 токен при бюджете секции 4096.
  `Σ token_budgets = 26624` не изменился; `input_budget = 131072 − 8192 − 2048 = 120832` — надбавка в него
  входит с запасом.

### 8. Тесты (44 новых) и их краснота на прежнем коде

| тест | что закрепляет |
|---|---|
| `tests/unit/test_explorer_step_context.py` (17) | блок схем строится из реестра (`get_tool(...).model_json_schema()`), описание реестра попало в строку, optional с дефолтом назван необязательным, лишнего поля (`search_statements`) в схеме `question.create` нет; неизвестный инструмент не получает строки; блок укладывается в потолок и **обрывается целыми строками**; бюджет шага («Шаг 3 из 16; осталось действий: 13») и пустой budget при отсутствии лимита; канонический ключ адреса (`fetch_url_key`: схема/фрагмент/слэш/NUL) и формулировка отметки повтора |
| `tests/scenario/test_two_sided_search_session.py` (4) | сквозной прогон FakeLLM + подменённый `FetchClient` (ни одного сокета): официальная сторона + независимая оценка с **другим числом** → 2 upstream-запроса записаны, **2 группы независимости**, оценка E3/`supported`, причины `insufficient_independence` нет, обе страницы стоят подтверждениями, карточка говорит «прочитано: 2» и несёт бейдж «Проверено»; односторонний прогон → 1 группа, `E1` с причиной `insufficient_independence` (то есть узел честен, а не «почти проверено»); разделы «Схемы аргументов выданных инструментов» и «Бюджет шага» доходят до модели (проверено по журналу запросов FakeLLM: текст был в `last_user`); повторный fetch того же адреса виден модели на следующем шаге («первая попытка была на шаге 2») при двух реально выполненных чтениях |
| `tests/unit/test_explorer_prompt_two_sided_search.py` (11) | правила 1–9 v8 совпадают с v7 байт в байт; нумерация 1…11; обязательные слова правила 10 (двусторонность, «независимая оценка инфляции 2025», «расхождений не найдено», «независимых оценок не найдено») и правила 11 («Бюджет шага», «отклонённое действие»); JSON-примеры v8 разбираются как полный конверт; пины v7 и v8 разрешаются `resolve_prompts`; подменённый sha256 пинов → `PromptPinError` (fail-closed) |
| `tests/scenario/test_config_v16_activation.py` (2) | активация v16: снимок с canonical-хешем `740ae9a1…`, `max_explorer_steps = 16`, пины curator-v8/explorer-v8, тексты промптов достаются по пинам снапшота, всё кроме `prompts` и `session_limits` совпадает с v15, порог `min_independence_groups = 2` на месте; **откат**: повторная активация config-v15 возвращает canonical `b3801812…`, `max_explorer_steps = 10` и пин explorer-v7 |
| `tests/unit/test_freeze_payloads.py` (+4) | v16 отличается от v15 ровно двумя разделами и ровно двумя ключами (пин explorer: path/sha256/version; лимит: 10 → 16); лимит совместим с дедлайном сессии и с потолком поиска; файл payload'а побайтово стабилен (canonical `740ae9a1…`, file `79d63b2d…`); прежние payload'ы v1…v15 не тронуты |
| `tests/unit/test_dev_stand_scripts.py` (+4) | дефолт `bootstrap.sh` и `reset-db.sh` — config-v16, откат назван явно (config-v15 + его canonical-хеш), README стенда описывает активацию и откат с canonical-хешами; файлы прежних payload'ов на месте |

Краснота доказана временным откатом **без коммита** (`git stash push -u -- apps/orchestrator`, плюс временное
удаление `explorer-v8.md` и `config-v16-payload.json`, потом `git stash pop`; hashes файлов сверены после
восстановления): на прежнем коде `tests/unit/test_explorer_step_context.py` не собирается
(`ModuleNotFoundError: apps.orchestrator.tool_context`); `tests/scenario/test_two_sided_search_session.py` —
**2 failed / 2 passed**: видимые схемы («# Схемы аргументов выданных инструментов» отсутствует в тексте,
дошедшем до модели) и отметка повтора красные, а проверки групп независимости и оценок зелёные — их обеспечил
T7.75, и это ровно то, почему дефект был невиден: математика была правильной, модель просто не искала вторую
сторону. `tests/unit/test_explorer_prompt_two_sided_search.py` — 11 failed (нет файла), `test_freeze_payloads.py`
— 3 failed из 4 новых при отсутствующем payload'е (четвёртый — «прежние payload'ы не тронуты» — зелёный, как и
должно быть), прежние 17 тестов этого файла зелёные. Прежние скрипты стенда (config-v15 как дефолт) красят обе
проверки дефолта.

Отдельная находка про учёт: сборов стало **1529 → 1573** (+44), хотя новых тестовых функций 42 — два теста в
`tests/unit/test_prompt_example_no_real_data.py` параметризуются списком файлов промптов и документов и потому
сами покрыли `explorer-v8.md` и `config-v16-payload.json` (проверка «в промптах и доках нет реальных данных»
распространилась автоматически). Полный прогон §6: ruff без замечаний, mypy strict — Success (142 файла),
`-n auto -m "not timing"` — **1557 passed, 12 skipped** (было 1513 + 12), затем последовательный `-m timing` —
**4 passed**. Ни один прежний тест не удалён и не ослаблен.

### 9. Активация на подставке .92 (делает менеджер; агент к .92 не подходил)

```bash
hostctl activate-online --payload docs/eval/config-v16-payload.json \
  --reason "T7.76: explorer-v8 (двусторонний поиск) + max_explorer_steps 16" \
  --drain-wait-seconds 120
```

Что должно быть видно после (проверено сценарным прогоном на scratch-БД, не на .92): активный снимок с
`payload_sha256 = 740ae9a1…`, `session_limits.max_explorer_steps = 16`, пины `curator-v8` + `explorer-v8`;
в новых сессиях `model_runs.prompt_version = 'explorer-v8'`. Прежние сессии и их оценки не меняются: снапшот
сессии заморожен.

Откат (та же команда, другой payload; закреплён тестом
`tests/scenario/test_config_v16_activation.py::test_config_v15_remains_available_as_the_rollback`):

```bash
hostctl activate-online --payload docs/eval/config-v15-payload.json \
  --reason "откат T7.76: explorer-v7 и max_explorer_steps 10" --drain-wait-seconds 120
```

После отката ожидаемый canonical — `b3801812…`, лимит снова 10, пин `explorer-v7`. Для dev-стенда тот же откат
одной переменной: `NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v15-payload.json ./bootstrap.sh`.

### 10. Остаток, пределы и риски (что осталось честной недоработкой)

1. **Правило 10 — инструкция, а не гарантия.** Хост по-прежнему не знает, *является ли* найденная страница
   независимым исследованием: он считает группы независимости по данным (домен, хеш, текст, parent), а смысл
   «независимая оценка» остаётся на модели. Значит возможны два честных провала: модель не сделала вторую
   сторону (тогда `insufficient_independence` и `E1` — карточка это называет) и модель сделала поиск, но
   вторая сторона склеилась с первой атрибуцией T7.75 (тогда группа одна снова). Второе — предел ADR-0029:
   словарь первоисточников намеренно консервативен, и независимая работа, дословно пересказывающая официальный
   текст, правильно получает одну группу.
2. **Раскрытие темы поиска.** Двусторонность увеличивает число запросов к SearXNG (до 16 за сессию вместо
   ~5), а значит и число внешних запросов к поисковым системам: формулировки темы («независимая оценка
   инфляции 2025») видны вовне. Это уже было верным для официальной стороны (T7.71), но теперь масштаб другой;
   запросы модели журналируются (`research_upstream_request`), и их список — часть аудита.
3. **Сессии стали длиннее.** 16 шагов ≈ 912 с исследования против ≈570 с при 10; таймер wakeup
   (`TimeoutStartSec=3600`) и дедлайны фаз это покрывают, но «ответ через минуту» теперь ответ через
   15–20 минут в худшем случае. Сокращать лимит — отдельное решение с замером, не правка по вкусу.
4. **Видимость схем не заменяет строгой проверки.** Модель может прислать лишнее поле и получить отказ —
   отказ стал понятнее (форма та же), шаг всё равно сожжён. Реальной экономии шагов без замера на живой модели
   утверждать нельзя: заметку о бюджете и схемы модель увидит, но сэкономит ли шаги — покажет следующий прогон
   на .92 (замер вёл не агент).
5. **Отметка повтора адреса не запрещает повтор.** Она появляется на следующем шаге и формулирует цену;
   жёсткий гейт (`TOOL_REPEAT_DENY_LIMIT = 2`) остался прежним, потому что его семантика закреплена другим
   сценарием и менять её здесь значило бы править прецедент. Более умный запрет (канонический URL вместо
   буквального) — кандидат на отдельную задачу, как и лимиты fetch: **следующая задача T7.72**.
6. Поля кураторского конверта (`search_statements` и другие) по-прежнему существуют в staging и в промптах
   куратора — правка касалась только того, что модель читает как **аргументы инструмента**. Отдельного
   «охранника», который отказывал бы `question.create` с полями конверта до реестра, не добавляли: это была бы
   вторая валидация поверх `extra="forbid"`.

## T7.77 — типографика детектора производности, переоценка затронутых утверждений и российский корневой сертификат. Анализ до правок

Решения пользователя от 2026-10-07: «1. Да, запускай» (исправить детектор и точечно переоценить
затронутые утверждения) и «2. Да, добавь российский корневой сертификат». Факты подставки
(noezema-ddev, стенд T7.76), на которых построен этот анализ:

- **СберCIB** (`sbercib.ru`) опубликовал пересказ релиза Росстата; нормализованный текст страницы
  содержит фразу `По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59%
  (после 9,52% в\xa02024 году)` — между словами стоит NBSP U+00A0. Детектор хоста классифицировал
  страницу как `no_value_attribution`, указатель производности не проставлен; утверждение
  `c970bc08…` получило E3 «Проверено» с текстом «независимые оценки Сбера/СберCIB совпали», хотя
  СберCIB пересказывает Росстат, а не измеряет сам.
- **nbj.ru** начал фрагмент со слов `Согласно опубликованным данным…`, имени первоисточника в окне
  нет — классификация `no_value_attribution` здесь честная: «опубликованные данные» без имени —
  не атрибуция. Этот случай фиксируем тестом как эталон осторожности.
- **rosstat.gov.ru** не открылся: `SSL: CERTIFICATE_VERIFY_FAILED … unable to get local issuer
  certificate` — цепочка выпущена «Russian Trusted Sub CA → Russian Trusted Root CA» (Минцифры),
  которой нет в certifi; подстановка сертификата через `SSL_CERT_FILE` не помогла.

### 1. Где сравнение текста чувствительно к типографике

Детектор (`apps/research_proxy/source_attribution.py`) сопоставляет нормализованный текст страницы
с регулярными шаблонами. Регистр шаблонов закрыт `re.IGNORECASE` (`_compiled`, :225) — к регистру
сравнение нечувствительно. Чувствительность к типографике в трёх местах:

1. **Литеральные пробелы внутри шаблонов.** Темплаты `по данным`, `сообща[а-яё]* …`,
   `как сообщил…` (:133–154) и многословные алиасы — `банк[а-яё]* росс[а-яё]*`,
   `федеральн[а-яё]* служб[а-яё]* … государственн[а-яё]* статистик[а-яё]*` (:84, :96–103) —
   требуют ровно один ASCII-пробел между словами. В живом русском веб-тексте словесные промежутки
   набраны неразрывными пробелами: NBSP U+00A0 (сберсибовское `По\xa0данным`), узкий NBSP U+202F,
   тонкие пробелы U+2009…U+200A. Шаблон `по данным` к `По\xa0данным` не прикладывается → вся
   страница Сбербанка осталась без атрибуции. Асимметрия заметна даже внутри файла:
   `VALUE_NUMBER_PATTERN` (:218) уже терпит `\u00a0` внутри числа («5,59 %»), а шаблоны фраз — нет.
2. **Мягкий перенос внутри слова.** Софт-гифен U+00AD (перенос «Росста­та» в вёрстке) и ZW-символы
   U+200B..U+200D, word joiner U+2060 разрывают алиасные шаблоны по буквам: `росстат[а-яё]*` к
   `Росста\u00adта` не прикладывается.
3. **Граница фрагмента.** `_FRAGMENT_SPLIT` (:222) режет текст по `[.!?\n…]`: перевод строки —
   жёсткая граница, хотя нормализованный HTML склеивает текстовые блоки одинарным `\n`
   (`packages/retrieval/normalization.py`). Фраза, набранная через перенос («По\xa0данным\nРосстата,
   …»), попадает в два фрагмента и пара «шаблон + алиас» не собирается.

Кавычки и тире к совпадению шаблонов отношения не имеют: ни «ёлочки», ни U+2013/2014 не входят в
регулярки (алиасы — словесные корни), они влияют только на читаемость фрагмента-основания. Это
отдельно проверяется тестом: страница с NBSP и «ёлочками» вокруг имени первоисточника должна быть
опознана как пересказ.

Детектор при этом остаётся консервативным инструментом: ложная склейка дороже ложного пропуска,
поэтому правка обязана расширять распознавание, не ослабляя стоп-условия (вето собственной оценки,
окно шаблона 120 символов, отказ при двух первоисточниках).

### 2. План исправления детектора

Нормализация типографики — один раз на входе детектора, над копией текста: хранимые артефакты и
их хеши не меняются, меняется только то, по чему сканирует детектор. Нормализуемое отображено в
`ATTRIBUTION_METHOD_VERSION` → `host-source-attribution-v2`: по этому полю в `sources.metadata` и
в журнале решения v2 отличаются от решений битого v1 (переоценка, §3, опирается на это).

- пробельные варианты Unicode (U+00A0, U+2000..U+200A, U+202F, U+205F) → обычный пробел;
  идущие подряд пробелы схлопываются в один — шаблоны с одиночным промежутком снова прикладываются;
- невидимая типографика удаляется: софт-гифен U+00AD, ZWSP/ZWNJ/ZWJ U+200B..U+200D, word joiner
  U+2060, BOM U+FEFF;
- границей фрагмента остаются окончания предложения `[.!?…]`; одиночные переводы строк (типографический
  перенос, склейка HTML-блоков) больше не рвут фразу. Цена решения фиксируется честно: число
  с маркером измерения обязано находиться в том же фрагменте, а фрагмент стал шире на
  «мягкие» переводы строк — окно «шаблон↔алиас» в 120 символов и вето собственной оценки от этого
  не ослабевают;
- фрагмент-основание показывается по нормализованной копии: это согласовано с v1, где `_basis` (:285)
  уже схлопывал пробелы и переводил переводы строк в обычные — оператор увидит ровно тот текст, по
  которому детектор принял решение (невидимые символы становятся видимыми пробелами); маскировка
  NUL и fence-маркеров не меняется;
- потолок сканирования MAX_SCAN_CHARS применяется к нормализованному тексту.

Тесты фиксируют красноту на старом коде до правки: сберсибовская цитата сохранилась в задаче
дословно, с literal `\xa0`.

### 3. Как переатрибутировать решённое битым детектором и что реально пересчитывает оценки

Окно кандидатов — журнал существующего типа `research_fetch_completed`: события с
`payload->>'attribution_method' = 'host-source-attribution-v1'`, начиная с деплоя T7.75
(2026-10-07T14:30Z). Для каждого прочитанного источника: нормализованный текст поднимается из
хранилища артефактов по `normalized_sha256`, детектор v2 запускается против канонического URL и
текста — решение принимается заново, без догадок. Указатель проставляется только строкам, которые
ещё не размечены (`parent_source_id IS NULL` и нет `metadata.derivative_of`) — повторный запуск
команды ничего не меняет (идемпотентность). Функции проставления указателя общие с путем fetch:
`_resolve_primary_source` (пересказанная страница первоисточника уже прочитана → ссылка на неё;
не прочитана → заявленный якорь `metadata.declared_primary_anchor`) и `_mark_derivative`; для
переатрибуции они вынесены в общий модуль `apps/research_proxy/derivative_pointer.py`, путь fetch
делегирует им — одна реализация, два места применения.

Что реально пересчитывает оценки при изменении графа источников (найдено в коде, не изобретено):
`packages/memory/source_graph.py::apply_source_graph_change` (:257) — доверенный каскад хоста §11.3:
он находит утверждения, чьи улики ссылаются на изменённые источники, снимает их текущие оценки с
статуса `current` и ставит в очередь `reassessment_jobs` (reason `source_graph_change`) с записью
существующего события `AuditEventType.SOURCE_GRAPH_CHANGED`. Далее рабочий переоценки
`packages/memory/reassessment.py::run_reassessment_batch` (:763) — ровно тот же механизм, которым
пользуется `hostctl reassessment-tick`, — пересобирает снимок независимости
(`build_source_independence_snapshot`) и переоценивает утверждение правилами; две страницы-пересказа
с общим предком склеиваются `group_source_graph` в одну группу (основание `parent:<id>`), E3
сменяется честной более низкой оценкой с причиной `insufficient_independence`. Новый AuditEventType
не нужен — `SOURCE_GRAPH_CHANGED` существует; миграция БД не нужна; движок правил, пороги и
ARCHITECTURE.md не трогаются.

Интерфейс: `hostctl research-reattribute --since <ISO> [--dry-run]` — тонкая обёртка CLI над
доверенным модулем `apps/research_proxy/reattribution.py`. Таблица вывода на каждый кандидат:
canonical URI → чем было (статус v1 из журнала) → чем стало (решение v2 и имя первоисточника) →
проставленный предок/«указатель уже есть»/«изменений нет». `--dry-run` ничего не пишет.

Триггеры остановки, при которых эту часть делать нельзя было бы (не наступили — описываем): если бы
идемпотентная запись указателей потребовала миграции или нового типа события журнала; если бы
переоценка требовала правки `group_source_graph`/порогов правил. Нулевое обнуление
`parent_source_id` для ошибочных склеек (вариант C) не делаем: ложных склеек v1 не оставлял —
детектор молчал, а не склеивал вслепую.

### 4. Прогноз до события — не оценка состоявшегося (уровень промпта)

Стендовая ошибка карточки `9266248e…`: прогнозы ЦБ («ожидает инфляцию 6,5–7%») и МЭР (6,8%),
высказанные до конца года, куратор зачитал как «независимые оценки», подтвердившие факт Росстата.
Это дефицит инструкции модели, не кода правил: движок оценивает группы источников, а какие тексты
модель принесла уликами — выбор модели. Исправление — новый промпт explorer-v9 с правилом «прогноз до
события не является независимой оценкой реализованного значения» (правила 1–11 v8 сохраняются
дословно), и конфигурация `config-v17`: байт-в-байт равна v16, кроме пина промпта explorer.
Пороги правил не меняются — при честном одногрупповом наборе уликами результат даёт каскад §3.

### 5. Дополнительный корневой сертификат research proxy

httpcore строит TLS-контекст по умолчанию как `ssl.create_default_context()` + загрузка certifi
(`.venv/.../httpcore/_ssl.py`); `FetchClient` подменяет пул транспорта без аргумента `ssl_context`
(`apps/research_proxy/fetch.py`:59–62) — доверенная цепочка research proxy сейчас ровно certifi, и
эмпирический факт (г) подтверждает: `SSL_CERT_FILE` на стенде не помог. Решение: переменная
`NOEZEMA_RESEARCH_EXTRA_CA_FILE`. Если она установлена, research proxy строит SSL-контекст как
сейчас по умолчанию плюс загрузка указанного PEM (`ctx.load_verify_locations`), и передаёт его в
`httpcore.AsyncConnectionPool(ssl_context=…)` — параметр существует (httpcore 1.0.9). Переменная не
установлена → поведение побайтово прежнее (контекст не подменяется). Файл отсутствует или не читается
как PEM → понятная ошибка на старте, fail closed: тихой подмены цепочки нет. ПроверкаHostname'а и
`CERT_REQUIRED` остаются как в контексте по умолчанию — TLS не ослабляется ни в каком режиме;
переменная относится только к исходящим запросам research proxy (fetch и upstream-поиск), LLM-шлюз
её не читает. Операционная установка сертификата Минцифры на стенде — инструкция в README: PEM
кладёт оператор, скачивая его с официальных страниц Госуслуг/Минцифры и сверяя отпечаток; узел сам
ничего не качает. ТестTLS поднимает локальный сервер на 127.0.0.1 с тестовым CA (openssl CLI —
альтернатива trustme/cryptography, которых нет в зависимостях): без переменной fetch обязан падать с
`CERTIFICATE_VERIFY_FAILED`, с переменной — успешен, а сертификат того же CA но с другим hostname —
отвергаться даже при установленной переменной.

### 6. Что осталось бы незакрытым после этих правок

- Переатрибуция охватывает окно журнала после деплоя T7.75; более старые чтения (до T7.75) окном не
  заданы — при необходимости окно расширяется параметром `--since`, механизм тот же.
- Прогноз-vs-оценка остаётся инструкцией модели: формального запрета положить прогноз уликой в
  staging нет, и ввод такого гейта означал бы правку правил/схем — это остановка по критериям задачи.
- Кураторский промпт (curator-v8) не менялся: карточки «независимые оценки…» строятся из выводов
  модели по уликам; если стенд снова покажет формулировку от прогнозов — кандидат отдельной
  задачи на curator-v9.

### 7. Реализация (коммиты `2cd07ef` — анализ, `d91dc12` — детектор v2 и переатрибуция, `aa51d62` — explorer-v9/config-v17, `c5f801c` — дополнительный CA)

**(а) Детектор v2 (`apps/research_proxy/source_attribution.py`).** Типографика нормализуется ровно
один раз на входе детектора и только над **копией** текста: хранимый артефакт, его байты и все хеши
не меняются; `MAX_SCAN_CHARS` применяется к копии. `_SCAN_TRANSLATE`: U+00A0, U+2000..U+200A, U+202F,
U+205F → обычный пробел (последовательные пробелы схлопываются `_CONSECUTIVE_SPACES`); U+00AD (софт-гифен),
U+200B..U+200D (ZWSP/ZWNJ/ZWJ), U+2060 (word joiner), U+FEFF (BOM) удаляются. Границы фрагментов —
`_fragments()`: окончания предложения `[.!?\u2026]+`, пустая строка (`\n{2,}`) и **одинарный `\n` как
мягкий перенос, склеивающий fragments** (п. 1 анализа). Цена зафиксирована честно: фрагмент стал шире
на типографические переводы строк, но окно «шаблон↔алиас» 120 символов, вето собственной оценки
`OWN_ASSESSMENT`, отказ при двух первоисточниках и `MEASUREMENT_MARKERS` не ослаблены; сами шаблоны
`ATTRIBUTION_TEMPLATES` и алиасы не переписаны ни на символ. `ATTRIBUTION_METHOD_VERSION` →
`host-source-attribution-v2`; `basis_fragment` показывается по нормализованной копии — согласовано с v1,
где `_basis` уже схлопывал пробелы (оператор видит невидимые символы обычными пробелами).

**(б) Общий модуль проставления указателя (`apps/research_proxy/derivative_pointer.py`).** `resolve_primary_source`
(пересказанная страница первоисточника уже прочитана → ссылка на неё по **метке хоста**, не registrable
domain — ловушка T7.75; иначе якорь `declared_primary_anchor`) и `write_derivative_pointer` вынесены из
`service.py`; fetch-путь делегирует им — реализация одна, применений два (fetch и переатрибуция).

**(в) Переатрибуция (`apps/research_proxy/reattribution.py` + команда `research-reattribute`:
`.venv/bin/python -m hostctl.cli research-reattribute --since <ISO> [--dry-run]`).**
Кандидаты окна — журнал существующего типа `research_fetch_completed` с
`payload->>'attribution_method' = 'host-source-attribution-v1'` (`DISTINCT ON` по источнику, последнее
решение), начиная с `--since` (для подставки — деплой T7.75 `2026-10-07T14:30Z`). По каждому: нормализованный
текст поднимается из хранилища артефактов по `normalized_sha256`, детектор v2 решает заново против
канонического URL. Размечаются только непомеченные строки (`parent_source_id IS NULL` и нет
`metadata.derivative_of`) под блокировкой `FOR UPDATE`; уже корректно склеенные строки стенда (пара
ria↔interfax) не трогаются. В `derivative_of` пишется метод v2 и маркер `written_by: "research-reattribute"`.
Пересчёт — только существующий каскад §11.3 (`apply_source_graph_change`, аудит `SOURCE_GRAPH_CHANGED`)
и один `run_reassessment_batch()` после записи; остаток очереди dren'ит maint-таймер идемпотентно. Миграции,
нового AuditEventType, правки правил/порогов нет — стоп-критерии не наступили. Формат вывода — вывод кода
`hostctl/cli.py:473–489`; поведение команды (нулевой dry-run, идемпотентность, exit 2 на ошибках) закреплено
тестами:

```
$ .venv/bin/python -m hostctl.cli research-reattribute --since 2026-10-07T14:30:00Z --dry-run
research-reattribute: окно с 2026-10-07 14:30:00+00:00 (только план)
https://sbercib.ru/…
    было: no_value_attribution  стало: derivative → Росстат  [будет проставлен указатель]
https://vedomosti.example/…
    было: no_value_attribution  стало: derivative → Росстат  [будет проставлен указатель]
изменено источников: 0; затронуто утверждений: 0; снятых голов: 0; задач переоценки создано: 0
```

(в dry-run строка итога нулевая — план показан построчно, ничего не пишется и заявок не заводится). Реальный прогон добавляет
строку `рабочий переоценки: processed=… completed=…`. Ошибки — exit 2 с текстом (нет `NOEZEMA_DATABASE_URL`,
кривой `--since`, недоступный каталог артефактов). Идемпотентность закреплена тестом: второй запуск —
«изменено источников: 0», действия «указатель уже проставлен», голова оценки та же.

СберCIB-случай на тестах (строки страницы перенесены с подставки дословно, NBSP в литералах): **было** —
`no_value_attribution`, указателя нет, утверждение `c970bc08…` E3 «Проверено» («независимые оценки
Сбера/СберCIB совпали») при двух страницах-пересказах = две группы; **стало** — обе страницы `derivative →
Росстат` с общим предком, склейка `parent:<id якоря>` даёт **одну** группу, голова пересчитана честно:
`E1/hypothesis` с причиной `insufficient_independence`. Честная деталь, записанная в AGENTS §7: причины
рабочей переоценки живут только в payload события `reassessment_job_completed` (у `claim_assessments`
колонки причин нет; `CLAIM_ASSESSED` — язык staging-пути), тест читает именно его. nbj.ru
(«Согласно опубликованным данным…» без имени) остался `no_value_attribution` — эталон осторожности закреплён
тестом отдельно.

**(г) explorer-v9 и config-v17.** Новый файл `prompts/explorer/explorer-v9.md` (sha256 файла
`bf5169a0…d766af`). Правила 1–11 совпадают с v8 байт в байт (тест сравнивает нумерованные блоки вместе
с трёхпробельными продолжениями и сверяет всё вне раздела «Правила»), добавлено правило 12:

> 12. Прогноз до события — не независимая оценка реализованного значения: это утверждение об ожиданиях,
> и сопоставимо оно только с тем, что ожидалось — другими прогнозами, консенсусом предсказаний, оценками
> регулятора «что будет». Независимая оценка реализованного значения — альтернативный расчёт или измерение
> того, что уже произошло: наблюдаемая и воспринимаемая инфляция по опросам домохозяйств, альтернативные
> индексы цен, независимые академические исследования с другой методикой и другими авторами. Прогноз уместен
> в ответе как контекст («чего ожидали тогда»), но никогда — как независимое подтверждение уже состоявшейся
> величины; публикация банка или СМИ, пересказывающая официальную цифру со ссылкой на первоисточник, вторым
> независимым наблюдением тоже не является (правила 9 и 10).

`config-v17` (`docs/eval/config-v17-payload.json`) отличается от v16 **ровно одним** разделом — пином
`prompts.explorer` (path/sha256/version): `session_limits` (включая `max_explorer_steps = 16`), кураторский
пин curator-v8, `research_proxy`, `claim_type_rules` (2 подтверждения, 2 группы независимости), TokenBudgets,
schedule и модель побайтово равны v16 (проверено тестом построчно). Canonical-хеш payload —
`5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717`, хеш файла — `3d51cefc…04a86`
(разные числа; AGENTS §8). Дефолт dev-стенда (`bootstrap.sh`, `reset-db.sh`) — config-v17; откат —
`NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v16-payload.json` (canonical `740ae9a1…`), команда
активации/отката и хеши — в README стенда. Движок правил не тронут: «прогноз ≠ измерение» остаётся
инструкцией модели (п. 6 незакрытого).

**(д) Дополнительный корневой сертификат (`apps/research_proxy/tls.py`).** Переменная
`NOEZEMA_RESEARCH_EXTRA_CA_FILE`. Если она установлена, контекст = `create_default_context()` + certifi
(то же ядро доверия, что ставит httpcore по умолчанию) + `load_verify_locations` операторского PEM; явно
восстанавливаются `check_hostname=True` и `CERT_REQUIRED`. Переменная не задана или пуста → `None`,
`FetchClient` строит пул веткой **без** `ssl_context` — поведение прежнее побайтно. Файла нет / файл не PEM →
`ResearchTlsError` с именем переменной и пути: бросается и при сборке `FetchClient`, и в
`ResearchProxyService.__init__` (все входы research-proxy — standalone `main.py` и `session_assembly` —
строят сервис, узел не поднимается с половинной настройкой). Обе исходящие ветки (fetch и upstream-поиск)
идут через `FetchClient`; LLM-контур переменную не читает. `SSL_CERT_FILE` по-прежнему инертен (httpcore
его не спрашивает). README стенда: PEM кладёт оператор (страницы `guft.mincifry.gov.ru` / `gosuslugi.ru`
названы; узел ничего не скачивает), `/etc/noezema/research-extra-ca.pem` 0644, env-строка в
`/etc/noezema/dev.env`, перезапуск веб.

### 8. Тесты (+34 к набору) и их краснота на прежнем коде

Набор до задачи: 1573 собранных тестов; после T7.77: **1607** (удалённых нет; два теста dev-стенда
переименованы под v17). Полный зелёный прогон §6 перед каждым коммитом: stage1 `1591 passed, 12 skipped`
+ timing `4 passed` (после C3b); у C2 — `1569+12`/`4`, у C3a — `1584+12`/`4`.

| партия | тесты | краснота на прежнем коде |
|---|---|---|
| C2: `tests/unit/test_source_attribution.py` (+8), `tests/scenario/test_research_reattribute.py` (4) | NBSP/U+202F/софт-гифен/ZW/BOM-случаи, склейка через одинарный `\n`, вето и окно не ослаблены, эталон nbj.ru; метод v2 в решении; переатрибуция окна (было 2 группы/E3 → стало 1 группа/E1 `insufficient_independence`), идемпотентность, dry-run ноль, CLI-вывод и exit 2 | детекторные тесты на коде до правки: **7 failed, 25 passed в 0.24 с** (сберсибовская цитата сохранена литералами `\xa0`); scenario-тесты на прежнем коде красны отсутствием модуля `reattribution` и команды |
| C3a: `tests/unit/test_explorer_prompt_forecast_rule.py` (8), `test_freeze_payloads.py` (+3), `tests/scenario/test_config_v17_activation.py` (2); авто-расширение параметризации `test_prompt_example_no_real_data` на explorer-v9.md (+2) | правила 1–11 v9 побайтово равны v8; нумерация 1…12; слова правила 12; вне разделов «Правила»/версии файл идентичен v8; v17 пинует v9, v16 остаётся с v8; подменённый sha пина → `PromptPinError`; v17 = v16 ровно по одному разделу; активация/откат на БД | те же файлы на дереве C2: **8 failed в 0.20 с** (нет explorer-v9.md) и **6 failed, 57 passed в 1.70 с** (freeze+dev-stand: нет payload v17, дефолты скриптов ещё v16) |
| C3b: `tests/unit/test_research_tls_extra_ca.py` (7) | без переменной self-signed → `CERTIFICATE_VERIFY_FAILED`; с переменной — успех; сертификат, выпущенный не для 127.0.0.1, отвергается **и с добавленным корнем** («Hostname mismatch»); контекст сохраняет `check_hostname`/`CERT_REQUIRED` и **дополняет** certifi (по числу CA), а не заменяет; битый путь/не-PEM → `ResearchTlsError` на сборке FetchClient и сервиса | модуля `tls.py` на прежнем коде не существовало (тесты не исполняются); эмпирическая краснота зафиксирована стендом: `rosstat.gov.ru` → CERTIFICATE_VERIFY_FAILED, SSL_CERT_FILE не помог (п. 5 анализа) |

TLS-тестам нужен бинарник `openssl` (генерация тестовых сертификатов; trustme/cryptography в зависимостях
нет) — без него тесты помечены skipif и сообщаются отдельной строкой отчёта, как с shellcheck. Внешних
запросов ни один тест не делает: TLS терминируется локальным сервером `127.0.0.1`, ключи живут в tmp_path.

### 9. Что делает менеджер на подставке .92 (агент к .92 не обращался)

1. **Переатрибуция.** Сначала план: `.venv/bin/python -m hostctl.cli research-reattribute
   --since 2026-10-07T14:30:00Z --dry-run`
   от пользователя узла с env стенда (`NOEZEMA_DATABASE_URL`, артефакты `<NOEZEMA_DATA_ROOT>/artifacts`).
   Ожидается строка sbercib (и, если читалось, пересказ ведомостей) «было: no_value_attribution → стало:
   derivative → Росстат». Затем та же команда без `--dry-run`; в итоге — nonzero «изменено источников» и
   строка `рабочий переоценки: … completed=1…` (остаток очереди добирает maint-таймер). Проверка результата
   read-only: у утверждения `c970bc08…` голова — `E1/hypothesis`, в payload ближайшего
   `reassessment_job_completed` причина `insufficient_independence`; бейдж «Проверено» с карточки уходит.
   Повторный запуск команды обязан дать «изменено источников: 0».
2. **Откат переатрибуции** как операции нет: проставленный указатель — факт графа, созданный тем же
   механизмом, что и при обычном fetch (удаление `parent_source_id` = вариант C ADR-0029, не реализован).
   Ошибочных склеек v1 не оставлял (детектор молчал), поэтому откат здесь — не ожидаемый сценарий.
3. **Конфигурация.** Активация: `.venv/bin/python -m hostctl.cli activate-online --payload docs/eval/config-v17-payload.json
   --reason "T7.77: explorer-v9 (прогноз — не оценка состоявшегося)" --drain-wait-seconds 120`
   (canonical `5c402f4d…` виден в `config_snapshots.payload_sha256`). Откат: тот же путь с
   `docs/eval/config-v16-payload.json` (canonical `740ae9a1…`) — пин вернётся на explorer-v8.
4. **Корневой сертификат.** Шаги README («Дополнительный корневой сертификат для research-proxy»):
   PEM от оператора в `/etc/noezema/research-extra-ca.pem` (0644), env-строка, `systemctl restart
   noezema-dev-web.service`. Проверка: fetch `rosstat.gov.ru` без ошибки сертификата. Откат — удалить
   env-строку и перезапустить веб (доверие вернётся к certifi). Битой настройкой узел не поднимется:
   ошибка назовёт переменную и путь.

### 10. Остаток, пределы и риски

- Окно переатрибуции задано `--since`; чтения до деплоя T7.75 окном не охвачены (бэкфилл ADR-0029
  остаётся «не сделано» — расширяется тем же параметром при отдельном решении).
- Фрагменты стали шире на одинаковые переводы строк: число с маркером измерения обязано остаться в том
  же фрагменте, окно 120 и вето собственной оценки не ослаблены (зафиксировано тестами); ложная склейка
  по-прежнему дороже ложного пропуска.
- Переатрибуция поднимает якоря, созданные прежним методом (`declared_primary_anchor` v1), без их
  перепроверки методологией v2 — якорь это адрес заявленного первоисточника, а не решение детектора.
- «Прогноз ≠ измерение» — инструкция модели; гейт в staging означал бы правку правил (стоп-критерий).
  Если стенд снова соберёт карточку из прогнозов — кандидат curator-v9.
- Переоценка после переатрибуции выполняется одним `run_reassessment_batch()` без
  `recover_expired_leases`: осиротевшие аренды задач dren'ит maint-цикл (идемпотентно), это штатный путь.

## T7.78 — производность не страницы, а конкретного значения: анализ до правок

Решение пользователя от 2026-10-07: «запускай T7.78». Факты подставки (.92), на которых построен
анализ:

- детектор v2 (T7.77) на странице СберCIB теперь видит атрибуции, но их **две**: `По\xa0данным
  Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59%` и `По оценкам Банка России,
  инфляция в январе замедлилась до 10,7%…`, `…в апреле… 6,2%`. Две пары «значение + первоисточник»
  → статус `ambiguous_primaries` → указателя нет → `research-reattribute --since 2026-10-07T14:30:00Z
  --dry-run` даёт «изменено источников: 0»;
- несправедливость осталась: пересказ засчитан независимым наблюдением для утверждения `9266248e…`
  («инфляция 2025 = 5,59%», E4) и для `c970bc08…` («независимые оценки Сбера/СберCIB совпали с
  официальной цифрой, расхождений нет», E3 «Проверено»).

### 1. Что решается поуровнево, а что остаётся страничным

Страничное решение T7.75/T7.77 (`sources.parent_source_id` + `metadata.derivative_of`) остаётся и
не меняется: оно честно, когда всей странице присвоен один первоисточник, и отказывается решать,
когда первоисточников несколько. Поуровневое решение добавляется **для конкретного доказательства**:
«этот фрагмент, на который опирается это утверждение, пересказывает вот этот первоисточник». Для
фрагмента `По\xa0данным Росстата … 5,59%` источник — пересказ Росстата; для фрагмента про январские
10,7% тот же источник — пересказ Банка России; сама страница как строка остаётся непомеченной.

Осторожность T7.75 сохраняется на уровне фрагмента: своя оценка/альтернативный расчёт во фрагменте →
не производный; число, отличное от числа утверждения («другое число») → не производный для этого
утверждения; голое упоминание первоисточника без значения → не производный; страница самого
первоисточника (`is_home_host`) → не производный сама от себя; две пары «значение + первоисточник»
для одного и того же значения утверждения → отказ `ambiguous_primaries`. Ложный пропуск (остались
две группы) безопаснее ложной склейки — направление консерватизма то же, что в ADR-0029.

### 2. Где хранить решение: `evidence.scope` (миграции нет)

Перебранные варианты.

1. **Отдельная таблица связи доказательства с первоисточником** — отклонено: любая новая таблица =
   миграция = стоп-критерий задачи.
2. **Карта «утверждение → первоисточник» в `sources.metadata`** — отклонено: строка источника одна
   на все утверждения, решение стало бы зависеть от перечисления утверждений и смешалось бы с
   `derivative_of` (это факт о странице, а не об улике); идемпотентность переатрибуции T7.77
   (`NOT (metadata ? 'derivative_of')`, `apps/research_proxy/reattribution.py:279–281`) перестала бы
   означать «страница не размечена».
3. **Хостовый JSONB доказательства — `evidence.scope`** ← выбрано. Колонка существует
   (`packages/domain/models/memory.py:62–84`, NOT NULL, default `{}`), её пишет только хост, и она
   уже несёт версии/происхождение строки. Ключ `value_attribution` добавляется к каноническому
   `host-scope-v1` словарю (`packages/memory/scope.py:398–416`) как **дополнительный** ключ; форма
   решения (с маркером метода `host-value-attribution-v1`) разбирается на чтение строго и
   fail-closed: некорректная форма не даёт склейки.

Проверено, что запись в `evidence.scope` не меняет семантику дедупликации и идентичности знания:

- идентичность улики — `source_assertion_identity(kind, source content hash, normalized range)`
  (`packages/memory/evidence.py:142–151`), вызов — `apps/orchestrator/evidence.py:174`; текст
  фрагмента и скоуп в неё не входят;
- дедуп-запись поиска существующей улики — `(claim_id, evidence_kind, identity_hash)`
  (`packages/memory/service.py:883–898`, ограничение `migrations/versions/0004_memory.py:89`);
- множество улик оценки — `_evidence_set_hash` = `kind:relation:identity_hash`
  (`packages/memory/service.py:1427–1430`), скоуп не входит;
- предикат покрытия — `rules_engine.py:179–181` → `scope_covers` (`packages/memory/scope.py:419–466`):
  каноническая ветвь читает только `as_of` и `source_domain`, легаси-ветвь перебирает ключи
  **утверждения**, лишний ключ доказательства никогда не запрашивается. То есть новый ключ влияет на
  оценку ровно одним каналом — числом групп независимости, и больше ничем.

**Найденная ловушка (идёт в AGENTS §7).** T7.17 пересчитывает скоуп каждой улики утверждения из его
провенанса и **перезаписывает** строку (`packages/memory/service.py:966–980`). Решение, записанное в
`scope`, исчезло бы при следующем коммите любой сессии, коснувшейся этого утверждения. Поэтому
запись сопровождается правилом переноса: хостовы ключи атрибуции переносятся в пересчитанный скоуп
чистой функцией (`packages/memory/scope.py`), и это закреплено тестом отдельно от сценария
переатрибуции.

### 3. Кто принимает решение (слои) и где именно

Чистая функция — `apps/research_proxy/value_attribution.py`: на вход текст фрагмента (то, что узел
реально прочитал как основание улики) и формулировка утверждения, на выход — первоисточник или отказ
с причиной. Словарь первоисточников, нормализация типографики, шаблоны атрибуции, вето собственной
оценки и разбиение на фрагменты переиспользуются из `apps/research_proxy/source_attribution.py`
(второй детектор на том же словаре: один словарь — одна истина).

Слои обязывают выбрать точку записи: `packages/` не импортирует `apps/` (проверено grep — ноль
вхождений), значит детектор нельзя вызвать из загрузчика графа. Решение **пишут** только актёры
apps-слоя, а packages читают его как данные (id родителя и имя). Второй вынуждающий факт: в момент
`observation_to_evidence` (`apps/orchestrator/evidence.py:158–218`) утверждения ещё нет — формулировка
claim появляется позже, в кураторском предложении. Место, где пара «утверждение ↔ улика» существует
вместе с обоими текстами и с соединением БД, — запись staging-операции `evidence`
(`apps/orchestrator/orchestrator.py:2358–2370`): payload операции остаётся хостовым
(модель задаёт только индексы и отношение), туда и ложится решение; `MemoryService.apply_claim_staging`
переносит его в `evidence.scope` при создании строки улики. Для уже существующих улик решение
дописывает команда переатрибуции (apps-слой, тот же детектор).

### 4. Как независимость учитывает «эффективного родителя этого утверждения»

Единственная правка — **вход** группировщика в загрузчике снимка:
`packages/memory/source_graph.py:136–144`. К `SourceGraphInput` строки источника применяется
эффективный parent, записанный в уликах этого утверждения; строки родителей-первоисточников
добавляются как **узлы** (не члены снимка) тем же механизмом, что прямые родители страниц
(`:87–105`). `group_source_graph` (`packages/memory/independence.py:275–435`) не меняется вообще:
склейка происходит существующим основанием `parent:<short12>` (`:336–338`, запись основания
`:384–389`). Никаких новых оснований, порогов и правил.

Пример (стенд, утверждение «инфляция 2025 = 5,59%», улики: reliz Росстата прочитан либо объявлен
якорем + страница СберCIB + ещё один пересказ):

| | до T7.78 | после T7.78 |
|---|---|---|
| строка sbercib.ru в снимке | `parent_source_id IS NULL` → отдельная группа | эффективный parent = строка/якорь Росстата → `parent:<id>` |
| групп независимости | 2 (или 3) | 1 |
| вывод rules engine | E3/E4 «подтверждено независимо» | `insufficient_independence`, E1/hypothesis |

Конфликт: если улики одного утверждения опираются на один и тот же источник, но разные фрагменты
приписывают разные первоисточники, эффективный parent не применяется вовсе (остаются отдельные
группы) — направление консерватизма то же, что у отказа `ambiguous_primaries`.

### 5. Если первоисточник никогда не читался

Используется уже существующее разрешение родителя (`apps/research_proxy/derivative_pointer.py:42–103`):
прочитанная оригинальная публикация того же домашнего хоста, иначе существующий якорь
(`declared_primary_anchor`, `content_hash IS NULL`, `retrieved_at IS NULL`), иначе новый якорь.
Родителем не может быть уже помеченный пересказ — цепочка «новость → ЦБ, цитирующий Росстат» не
склеивается транзитивно. Якорь остаётся узлом графа и никогда не становится членом снимка ни при
каком уровне решения.

### 6. Соотношение с страничной разметкой

Страничная отметка не ослабляется и не удаляется: для страниц с одним первоисточником она по-прежнему
решает всё (и дешевле). Поуровневое решение дополняет её там, где страница неоднозначна, и там, где
страница пересказывает **одного** первоисточника по одному значению и сохраняет независимость по
другому. Знак «как проверено» считает union: указатель строки источника либо запись атрибуции улики
(`apps/web/reliability.py:237–246`) — фраза «часть прочитанного — пересказ первоисточника: N» не
утверждает ничего сверх фактов в переданных строках.

### 7. Что пересчитывается

- новые сессии: строка улики создаётся (`service.py:916–937`) раньше, чем считается снимок независимости
  того же коммита (`_assess` → `build_source_independence_snapshot`, :1158) — решение попадает в оценку
  сразу, отдельного каскада нет;
- существующее знание: команда переатрибуции дописывает решения по уже созданным уликам и запускает
  **существующий** каскад §11.3 `apply_source_graph_change` (тип журнала
  `AuditEventType.SOURCE_GRAPH_CHANGED`) + рабочий переоценки (`packages/memory/source_graph.py:257–350`).
  Каскад выбирает утверждения по `evidence.source_id`, то есть пересчитываются все утверждения, чьи
  улики опираются на затронутый источник (консервативно шире, чем «только получившие решение»;
  оценка может не измениться — это идемпотентно);
- аудит: новых типов событий нет. В payload `claim_assessed` добавляется поле со списком поуровневых
  первоисточников этой оценки; в журнале каскада — счётчики затронутых утверждений и задач (как в
  T7.77).

### 8. Стоп-критерий этой задачи (описано, не сделано)

Не делаются: миграции БД и новые таблицы; новые `AuditEventType`; изменение порогов группировки и
шкалы оценок; правка логики `group_sources`/`group_source_graph` и правил rules engine; правка
`ARCHITECTURE.md`; удаление уже проставленных страничных указателей (вариант C ADR-0029).


### 9. Реализация (коммит `50c8009` — детектор значения, запись решения, независимость и тесты;
этот раздел — отдельным коммитом документов)

Чистая функция (словари не скопированы, а переиспользованы): `apps/research_proxy/value_attribution.py`
(`VALUE_ATTRIBUTION_METHOD_VERSION = "host-value-attribution-v1"` :59, `attribute_value_in_fragment` :146).
Она берёт из `source_attribution.py` нормализацию типографики, разбиение на фрагменты, окно атрибуции,
шаблоны атрибуции/маркеры собственной оценки и `is_home_host`; добавляет только сопоставление **числа
утверждения** с числом фрагмента (`claim_value_numbers`, `_measured_numbers`: значение считается
значением только рядом с единицей измерения — `%`, `п. п.`, `процент`, `млрд`, `рубль`…, поэтому годы
«2025/2024» в число утверждения не попадают; хвост проверки длины `_UNIT_TAIL_CHARS = 8`).

Статусы решения (`derivative` или явный отказ): `own_assessment`, `ambiguous_primaries`,
`no_value_attribution`, `self_primary`, `value_not_attributed` — отказа без записанного статуса нет,
и ни один отказ ничего не пишет.

Кто решает и где freeze: `apps/orchestrator/orchestrator.py::_value_attribution` (:2127) — только для
`source_assertion`, только по тексту основания улики (`payload["assertion_text"]`) и формулировке
утверждения; вызывается из staging-цикла `_curator` (:2411–2438) и добавляет решение к конверту
операции `evidence`. Модель к решению не допускается, отдельного типа staging-операций нет — ключ
живёт внутри прежнего конверта.

Носитель решения: `packages/memory/scope.py` (`EVIDENCE_VALUE_ATTRIBUTION_KEY = "value_attribution"`,
`build_value_attribution` :467, `parse_value_attribution` :511, `carry_value_attribution` :529).
Commit boundary переносит конверт в `evidence.scope`: новая строка улики — `packages/memory/service.py:938–944`,
дедуп-повтор той же улики дописывает решение к существующей строке, если его ещё нет (:917–923), а
перескопирование scope по T7.17 сохраняет ключ (:998–1005). Миграции нет: `scope` — jsonb, identity
(`source_assertion_identity`, `evidence_set_hash`) и UNIQUE-индекс `0004_memory.py:89` решения не
содержат, поэтому дедуп и повторный fetch не ломаются (проверено тестом идемпотентного повтора).

Независимость: `packages/memory/source_graph.py::_value_attribution_parents` (:57) читает записанные
решения и `effective_parent` (:148) добавляет их в **входные данные** снимка; алгоритм
`group_source_graph`, пороги (`TEXT_OVERLAP_THRESHOLD`), PSL-список и правила rules engine не тронуты.
Страничный указатель старшего: если у строки источника есть `parent_source_id`, он и используется;
поуровневое решение заполняет только то место, где страница отказалась. Конфликт (у улики одного
источника в рамках одного утверждения два разных первоисточника) — эффективного родителя нет вовсе.

Знак «как проверено»: `apps/web/reliability.py:247–250` считает union страничных указателей и записей
атрибуции улики (`_value_attribution_ref` :306); карточки уже передавали `scope`
(`apps/web/knowledge.py`, `apps/web/answer.py`), поэтому API-поля не менялись. В payload
`claim_assessed` добавлено аддитивное поле `value_attributions` (`service.py:1325`) — перечень
поуровневых первоисточников этой оценки; новых `AuditEventType` нет.

### 10. Тесты (+30 к набору) и их краснота на прежнем коде

- `tests/unit/test_value_attribution.py` (20): живой текст СберCIB с двумя первоисточниками на странице
  и литеральными NBSP; страничный детектор на нём честно отказывает (`ambiguous_primaries`), уровень
  значения решает производность для 5,59% (Росстат) и отдельно для 10,7% (Банк России); собственная
  оценка редакции и «в отличие от Росстата» — `own_assessment`; голое упоминание ведомства без числа —
  `no_value_attribution`; число утверждения не присвоено ни одному первоисточнику страницы —
  `value_not_attributed`; страница самого Росстата — `self_primary` (первоисточник не пересказ самого
  себя); minfin.gov.ru со значением ЦБ — derivative ЦБ; типография (`\xa0`, `\u202f`, `\u00ad`) и «1,2 п. п.»
  обработаны; каждое число обязано иметь единицу (годы не считаются значениями).
- `tests/scenario/test_value_attribution_levels.py` (9): настоящие staging/rules без LLM, сеть —
  FakeFetchClient. Главный случай: неоднозначная страница + пересказ того же релиза → **2 группы и E3**
  до переатрибуции, после — **1 группа** с основанием `parent:<id>` и честный `insufficient_independence`
  (E1/hypothesis в событии `reassessment_job_completed`, не в догадке теста); противоположные случаи —
  «другое число» остаётся двухгруппным E3, собственная оценка редакции не приклеивается к первоисточнику;
  карточка считает пересказ по записи на улике (контроль: без колонки `scope` фразы нет); dry-run ничего
  не пишет и не создаёт задач переоценки; повторный прогон меняет ноль строк и не трогает голову оценки;
  CLI `research-reattribute --evidence-level` печатает «было/стало» по каждому доказательству.
- `tests/scenario/test_value_attribution_session.py` (1): настоящая сессия curated (FakeLLM + подменный
  HTTP-слой, фикстура T7.76) читает rosstat.gov.ru и неоднозначную страницу-пересказ: страничный уровень
  не разметил пересказ (`ambiguous_primaries`, `parent_source_id IS NULL`, нет `derivative_of`), а улика
  получила решение **от `_curator` при коммите**, и её родитель — уже прочитанная страница Росстата, а не
  новый якорь; итог — одна группа и E1 с причиной `insufficient_independence`.

Краснота доказана временным откатом (без коммита): (1) все правки исходного кода откачены — оба новых
файла тестов падают на коллекции (`No module named 'apps.research_proxy.value_attribution'`,
`cannot import name 'reattribute_evidence_window'`); (2) откачен только `packages/memory/source_graph.py`
— 2 провала с утверждением «2 группы вместо 1» (`basis: single` там, где должен быть `parent:`), остальные
8 зелёные; (3) откачена только карточка (`apps/web/reliability.py`) — провал по фразе «часть прочитанного
— пересказ первоисточника»; (4) откачен только вызов из `_curator` — сессионный тест краснеет: решения на
уликах нет вовсе. После каждого отката рабочее дерево восстановлено побайтово (`git diff --stat` совпал).

### 11. Переатрибуция существующего знания: `research-reattribute --evidence-level`

`apps/research_proxy/reattribution.py::reattribute_evidence_window` (:765 — номер строки в дереве после T7.85; до этого дополнения функция была на :594) идёт по журналу
`research_fetch_completed` окна (любой `attribution_method`, последний снимок на источник) и перечитывает
страницу из artifact-хранилища по `normalized_sha256`; для каждой улики этих источников, где записи о
происхождении значения ещё нет, решение принимается той же функцией; запись — единственный UPDATE по
`scope || jsonb` с предзащитой `FOR UPDATE` и условием «ключа ещё нет» (идемпотентно), дальше работают
только существующие механизмы: каскад `apply_source_graph_change` (журнал `source_graph_changed`) и рабочий
переоценки. Прямых правок оценок нет. Уже решённые улики не молчат: они перечислены с действием
«изменений нет: решение уже записано ранее».

Оговорка, которую нужно знать перед прогоном: текст основания улики долговременно не хранится
(`assertion_text` живёт в наблюдении сессии), поэтому переатрибуция решает по **сохранённому
нормализованному тексту страницы** и формулировке утверждения. Это ровно тот вход, на котором v2 уже
решает бэкфилл страниц (T7.77); вето собственной оценки действует по всему тексту unit'а целиком —
консервативнее, чем решение по одному фрагменту нового знания.

Пример `--dry-run` (вывод теста `test_cli_evidence_level_flag_reports_was_and_became`, те же данные, что
в главном сценарии):

```text
research-reattribute: окно с 2000-01-01T00:00:00+00:00 (только план)
утверждение 7a70aa67 · https://vedomosti.example/inflyaciya-2025-goda
    утверждение: Инфляция в России по итогам 2025 года составила 5,59%.
    было: записи о происхождении значения нет  стало: derivative → Росстат  [будет записано происхождение значения улики]
утверждение 7a70aa67 · https://sbercib.example/inflyaciya-2025
    утверждение: Инфляция в России по итогам 2025 года составила 5,59%.
    было: записи о происхождении значения нет  стало: derivative → Росстат  [будет записано происхождение значения улики]
доказательств с новым решением: 0; изменено источников: 0; затронуто утверждений: 1; снятых голов: 0; задач переоценки создано: 0
```

Уточнение T7.85 (написанное выше не отменяет, только расширяет режим): с `host-value-attribution-v2`
этот же прогон пересматривает и **уже записанные** улики — те, чья запись помечена прежней версией метода
(`payload->'value_attribution'->>'method'`), а не только улики без записи; в плане рядом с «было» печатается
номер прежнего метода. Источники и улики под коррекцией оператора (ADR-0031) пропускаются в обоих режимах,
прежняя запись сохраняется, если свежий метод отказался решать, а существующий якорь переиспользуется, пока
первоисточник тот же. Поле `pairing` в записи говорит, каким режимом получено решение
(`value_and_primary_in_fragment` / `publication_window`). Разбор и ожидания по стенду — раздел T7.85.

### 12. Что делает менеджер на подставке .92 (агент к .92 не обращался)

1. Деплой ветки с этой правкой (миграций нет — достаточно перезапуска сервисов).
2. `python -m hostctl.cli research-reattribute --since <дата последнего fetch> --evidence-level --dry-run`
   (для окна T7.75 можно сначала прогнать прежний режим без флага — он чинит указатели страниц).
3. Показать пользователю список «утверждение → было/стало» и итоговую строку: сколько доказательств
   получит решение, сколько утверждений зацепит каскад, какие головы снимаются. Ожидание по стендовому
   пропуску: `9266248e…` («инфляция 2025 = 5,59%») и `c970bc08…` теряют вторую группу независимости и
   падают до E1/hypothesis с причиной `insufficient_independence`; карточка вместо «подтверждено
   независимо» покажет «часть прочитанного — пересказ первоисточника: N».
4. Боевой прогон — **только после явного «да» пользователя**: та же команда без `--dry-run`. Откат
   записанного решения отдельной операции нет (вариант C ADR-0029 не реализован); последствия — понижение
   оценки, никогда повышение.

### 13. Остаток, пределы и риски

- Скругление: утверждение «около 5,6%» и фрагмент «5,59%» — разные канонические числа, склейки не будет
  (ложный пропуск безопаснее ложной склейки). Числа сопоставляются канонизацией (`5,59` → `5.59`),
  диапазоны и проценты без единицы измерения не сопоставляются.
- Родитель-якорь — домашний хост словаря: добавление узла `rosstat.gov.ru` в граф может склеить с ним
  другие `.gov.ru`-источники по основанию `domain:gov.ru` (прежняя грубость PSL, AGENTS §7 T7.75); при
  сомнении лечится только вариантом C.
- Каскад выбирает утверждения по `evidence.source_id`, поэтому пересчитываются и те, где оценка не
  изменится; это идемпотентно, но окно переатрибуции стоит держать узким (флаг `--since`).
- Собственная оценка вето по всему переданному тексту: для нового знания это фрагмент основания, для
  переатрибуции — вся страница; единый консервативный смысл, но на странице с маркером собственной оценки
  поуровневое решение не будет принято ни для какого значения (это видно в dry-run как `own_assessment`).
- Вариант C ADR-0029 (обнуление ошибочных `parent_source_id` и откат уже записанного решения) по-прежнему
  не реализован и требует явного решения пользователя; вариант D (своя шкала независимости) не вводился:
  пороги, `group_source_graph` и payload'ы config-v1…config-v17 не изменены.

## T7.79 — окно по значению вопроса и честный итог вопроса без утверждений: анализ до правок

Решение пользователя от 2026-10-07: «запускай T7.79», вариант (а) — фрагмент-доказательство по
значениям и опоре исследователя, (б) — не «ответ получен и проверен» при нуле утверждений. Факты
подставки (.92), на которых построен анализ:

- сессия `96c710ff`: прочитаны `cbr.ru/press/event/?id=28251`, `cbr.ru/analytics/dkp/dinamic/CPD_2025-12/`
  (в тексте страницы есть «Годовая инфляция … 5,59%») и `cbr.ru/press/reginfl/?id=64837`; публичная
  rationale исследователя цитирует эту фразу, но **фрагменты-доказательства**, переданные куратору,
  содержали только отдельные показатели («видны отдельные показатели (9,3%, 5,2%, 2,6%, диапазон 4–6%)»)
  → куратор предложил 0 утверждений и 1 новый вопрос;
- сессия `succeeded` / `goal_reached` при claims=0 ⇒ `question.state = verified`, то есть подпись
  «ответ получен и проверен», а карточка того же вопроса показывает «Ответ не записан». Тот же дефект
  зафиксирован 2026-10-05 (сессия `1b70d54a`).

### 1. Почему фраза со значением не попала в окно (разбор по коду)

Фрагмент выбирается одной строкой — `apps/orchestrator/evidence.py:193–200`:
`select_assertion_windows(normalized_text, f"{question}\n{plan}", SOURCE_ASSERTION_TEXT_BUDGET,
max_windows=SOURCE_ASSERTION_MAX_WINDOWS)`. Единственный сигнал хоста — текст вопроса и план
исследователя: они подложены в данные наблюдения (`apps/orchestrator/orchestrator.py:1186–1188`,
назначение — комментарий `:1748`). Публичная rationale исследователя и уже прочитанные им фразы в
выборе окна не участвовали вовсе. Номера строк в этом разделе — коду **до правки** (`fa8e235`);
в коде после правки те же места сдвинулись: `extract_terms` — `assertion_window.py:190` (фильтр `:205`),
`select_assertion_window` — `:219`, `_significant_numbers` — `:402`, `_fact_region_candidates` — `:440`
(формула счёта `:470`), `_FACT_ZONE_DECAY` — `:349`; подкладка контекста в наблюдение —
`orchestrator.py:1203–1205` и её комментарий `:1773`.

- `extract_terms` (`assertion_window.py:183`) отбирает токены по фильтру `:198`: длина ≥ 4 и **не**
  только из цифр. «5,59» даёт токены «5» и «59» — оба отбрасываются. Десятичное значение вопроса не
  даёт ни одного термина, то есть в терминальной плотности первого окна оно не участвует;
- первое окно (`select_assertion_window`, `:212`) = плотность различных основ терминов в окне
  `_MATCH_SPAN = 400` + числовой бонус ≤ 0,5 (`_NUMBER_BONUS_MAX`, `:176`) + близость к началу
  (`_START_BONUS_DECAY = 8 000`); на странице ЦБ плотней всего термины вопроса оказываются в
  навигации разделов и в заголовках блоков («Материалы…», «Отобразить/Скрыть подраздел»), поэтому окно
  стартует с нуля;
- второе окно (`_fact_region_candidates`, `:433–465`): якорь — ЛЮБОЙ запуск цифр раньше
  `_FACT_ZONE_DECAY = 16 000`, а `score = (t + _VALUE_WEIGHT·sig) · (1 − pos/16 000)` (`:463`). Ключевое
  предложение на ~14-м килобайте получает множитель ≈ 0,11 против ≈ 0,9 у блока значений в начале
  страницы; `_significant_numbers` (`:395`) из «5,59%» считает только часть «59» (части «5» — один знак
  и за ней запятая), то есть вклад именно спрашиваемого значения — 1 единица против десятков в таблице
  показателей.

Итог разбора: ranking не знает ни **какое именно значение** спрашивают, ни **какую фразу источника
исследователь уже прочитал**. Репродукция синтетической страницей той же структуры (навигация с датами,
блок значений в начале, длинный комментарий, ключевое предложение на offset 14261) даёт сегодня два окна
(`start=0` — навигация, `start=4624` — середина комментария) и «5,59%» вне обоих; закреплена тестом
(§9 ниже).

### 2. Какие честные сигналы есть у хоста (и что из них можно брать)

1. **Точные значения формулировки вопроса** — числа с запятой/точкой, проценты, даты д.м.гггг. Текст
   вопроса — доверенный операторский вход (§1, T7.59/ADR-0024), и он уже сейчас доходит до
   `select_assertion_windows`; отдельного класса якорей для него нет только потому, что `extract_terms`
   чисел не выпускает (`:198`). Год-одиночка сигналом не считается по той же причине, по которой
   `_significant_numbers` исключает 1900–2099: дата есть в навигации каждой страницы.
2. **Опора исследователя** — `public_rationale` шага (хост видит её: `orchestrator.py:1600`) и
   накопленные наблюдения (`ctx.observations`, дословные fenced-фрагменты прочитанных страниц). Граница
   честности: модельный текст имеет право быть **только поисковым сигналом**. Содержимое окна — дословный
   фрагмент нормализованного текста источника; цитата, которой в этом источнике нет, отбрасывается и НИЧЕГО
   не «дотягивает» (в `assertion_text` текст модели не попадает, новый payload-ключ не появляется:
   идентичность §14.3 и без того не зависит от текста фрагмента — `tests/scenario/test_research_evidence.py`).
3. **Типографика живых страниц** (AGENTS §7, T7.77): вопрос пишет «5,59%», страница набирает
   «5,59\xa0%» (NBSP U+00A0, узкий U+202F, софт-гифен U+00AD). Поиск обязан быть нечувствительным к
   пробелам и невидимым символам, но **позиции обязаны сохраняться**: окно вырезается из исходного текста,
   хранимый артефакт и его хеши не меняются.

### 3. Бюджет куратора: окна остаются двумя (расчёт, не «увеличение вслепую»)

`SOURCE_ASSERTION_TEXT_BUDGET = 2_000`, `SOURCE_ASSERTION_MAX_WINDOWS = 2` (`evidence.py:67`, `:77`);
потолок секции `claims_evidence` для двух улик худшей пары закреплён
`tests/unit/test_assertion_window.py::test_two_evidence_fragments_fit_the_claims_evidence_budget`.
Новый сигнал меняет только **выбор якоря** второго окна (общие термины/любые цифры → точное значение
вопроса и дословная цитата исследователя), а не число окон и не их длину — расчёт ADR-0011 §5 остаётся
верным без пересмотра. Третье окно потребовало бы заново считать долю секции `claims_evidence` (и,
следовательно, бюджет кураторского контекста) — это отдельная задача, здесь не делается.

### 4. Где рождается терминальное состояние вопроса и что разрешает машина состояний

Записей состояния `questions` ровно четыре: intake `candidate` (`question_intake.py:137`),
`researching` (`orchestrator.py:661`), `deferred` (`:1940`) и терминал — единственный
`UPDATE questions SET state` внутри финальной fenced-транзакции (`commit.py:399–402`); значение приходит из
`CommitPlan.question_terminal`, выбранного в `orchestrator.py:900–902`:

> `QuestionState.VERIFIED if final is SessionState.SUCCEEDED else QuestionState.PARTIALLY_ANSWERED`

то есть «сессия успешна» само по себе уже является единственным основанием назвать ответ проверенным —
неважно, записано ли утверждение. Таблицы допустимых переходов у `QuestionState` нет
(`packages/domain/models/enums.py:341–352`, только `is_resolved`), поэтому `partially_answered` уже
легально пишется этим же механизмом (для неуспешных сессий): новая ветка не требует ни нового enum-значения,
ни миграции. `SELECTED` существует лишь как аудит-событие и подписная метка рабочего состояния
(`orchestrator.py:673`, `answer.py:112`) — эта правка его не касается.

Витрина строит итог одним функцией (`apps/web/answer.py::build_answer_result`, карточка `:969`/`:980`,
список — `apps/web/questions_view.py::build_answer_summary`), поэтому «карточка vs список» согласованы
структурно. При claims=0 и терминальной сессии итог уже сейчас честный: `no_answer` → «Ответ не записан»
(`answer.py:386–396`); врёт рядом стоящая подпись состояния в той же карте (`answer.py:990`).

Источник истины «действующее утверждение вопроса» — отношения T7.74
(`apps/web/answer.py::touched_claims_cte`): `created` по `claims.created_in_session`, `reverified` /
`reused` по `claim_assessments.claim_id + created_in_session`. В момент коммита тем же фактом располагает
финализатор: `applied_claims` — число применённых claim-операций staging (`commit.py:366`,
`staging.apply_recorded`), а каждая перепроверка и каждый дедуп-повтор — это claim-операция staging,
записанная кураторским путём (`orchestrator.py:2400–2411`); отказ модели или правил даёт ровно ноль
(`return 0, 0` в `:2244`, `:2256`, `:2293`, `:2329`, `:2362`, `:2397`).

### 5. Честный итог сессии без единого утверждения (план правки)

Правило: `VERIFIED` даётся только если этот коммит применил ≥ 1 claim-операцию вопроса; иначе —
существующее состояние `PARTIALLY_ANSWERED`. Направление fail-closed: «проверено» не может возникнуть из
пустоты, а частичный итог честнее выдуманного полного. В витрине после правки: `kind = no_answer`
(«Ответ не записан») плюс подпись состояния без утверждения, что ответ **есть** (правится текст подписи в
presentation-слое; enum, API-поля и матрица §22.1/§22.2 не меняются).

Что от этой правки НЕ меняется: право вопроса на повторную работу (`_INVESTIGATED_STATES` уже включает
`partially_answered`, `packages/cognition/repetition.py:58–69`), отбор кандидатов (только `candidate`,
`question_selector.py:44`), пороги и шкала оценок, eligibility перепроверки.

Предел, который надо знать: состояние описывает последний терминал, поэтому повторный запуск УЖЕ
отвеченного вопроса, ничего не записавший, переведёт его в `partially_answered`. На стенде путь редкий
(селектор берёт только `candidate`), а карточка продолжает показывать записанный прежде ответ через
отношения T7.74 (claims>0 ⇒ `answered`/`reverified`/`reused`) — расхождения «карточка vs список» не
появляется, поскольку оба строит один builder.

### 6. Стоп-критерий этой задачи (описано, не сделано)

Не делаются: миграции БД и новые значения `QuestionState`/`AuditEventType`; изменение порогов
группировки и шкалы оценок, правил rules engine; правка `group_sources`/`group_source_graph`,
`research_proxy`, реестра инструментов; payload'ы config-v1…config-v17; промпты и их пины;
`ARCHITECTURE.md`; третье окно доказательства (требует пересчёта бюджета секции `claims_evidence`).

**Стоп-критерий проверен до правки — он НЕ сработал.** Правка не потребовала ни миграций, ни нового
enum-значения, ни изменения порогов/правил/промптов:Terminal состояния вопроса выбрал существующее
`QuestionState.PARTIALLY_ANSWERED`, окно доказательства осталось двух окон по 2000 знаков.

### 7. Реализация (коммит `c2e919c` — якоря окна и честный terminal)

**(а) Точные значения вопроса.** `apps/orchestrator/assertion_window.py`:

- `_QUESTION_VALUE_RE` (`:484`) = `\d{1,3}(?:[.,]\d+)+` — десятичное значение с разделителем или
  дата д.д.гггг; один год (три и более знаков без разделителя) в якорь не берётся, как и в
  `_significant_numbers` (`:395`): даты есть в навигации каждой страницы;
- `question_value_terms` (`:547`) — значения в порядке появления в формулировке вопроса (без
  дедупликации по значимости: куратор спрашивает «5,59%» и «21.01.2026» — оба сигнала реальны);
- `_fold_with_map` (`:522`) — поиск по свёрнутому тексту (удаляются все пробелы, NBSP U+00A0,
  узкий U+202F, софт-гифен U+00AD, ZW-символы и BOM) с картой позиций: найденная позиция
  **переводится обратно в смещения исходного текста**, поэтому окно — дословный вырез страницы,
  а артефакт и его хеши (`original_sha256`/`normalized_sha256`) не меняются; свёрнутый текст никуда
  не сохраняется и куратору не показывается;
- `_numeric_boundary_ok` — числовой якорь не цепляется внутрь большего числа («5,6» не находит
  «15,6%») — иначе окно вставало бы на первое попавшееся вхождение внутри таблицы значений.

**(б) Опора исследователя.** `researcher_quote_terms` (`:584`) принимает из текста исследователя
только те куски (кавычки «…»/“…”/"…", а также длинные придаточные от 60 знаков), которые
**дословно присутствуют в этом же источнике** (свёрнутое совпадение, 1..3 вхождения — больше трёх
значит навигационный повтор, а не прочитанная фраза); потолок — 8 цитат. Нет совпадения — сигнал
молча отбрасывается и **ничего не дотягивает**: ни один символ текста модели в `assertion_text` не
попадает (проверено тестом), payload улики (`url`, `original_sha256`, `normalized_sha256`, `chunk_id`,
`assertion_text`, `url_arg`) не изменился, идентичность §14.3 не затронута.

**Порядок сигналов второго окна** (`select_assertion_windows`, блок `:722–731`): терминальное окно
T7.16 (без изменений) → цитаты исследователя → точные значения вопроса → прежний общий value-якорь
T7.22. Точный якорь ставится так же, как value-окно: вырез `budget` со сдвигом назад на `lead`, без
пересечения с основным окном и с тем же вето на оглавление (`_is_table_of_contents`). Если первое
окно — leading-prefix (термины не совпали, это навигация), точное окно его **заменяет** — прежний
фолбэк T7.22 сохранён. Бюджет и число окон не изменились: `SOURCE_ASSERTION_TEXT_BUDGET = 2_000`,
`SOURCE_ASSERTION_MAX_WINDOWS = 2` (`evidence.py:67`, `:77`), разделитель `"\n[…]\n"`.

**Куда хост берёт опору.** `apps/orchestrator/orchestrator.py`: публичные rationale шагов
исследователя собираются в `_explorer_loop` (`rationale_lines`, `:1502`, append `:1579`) и передаются
в `observation_to_evidence(..., explorer_text=…)` (`:1845–1850`) — последние 8 шагов не более 6000
знаков (`RATIONALE_SIGNAL_STEPS`/`RATIONALE_SIGNAL_CHARS`, `:174–175`). В `apps/orchestrator/evidence.py`
это именованный параметр `explorer_text` (`:102`) с явной пометкой в docstring: поисковый сигнал, не
содержимое улики. Данные наблюдения (`observation_data`) не изменились — промпт шага, fence'ы и
`turn_id`/`action_id` прежние.

**(в) Честный итог вопроса.** `apps/orchestrator/orchestrator.py:915–918`: terminal вопроса
`VERIFIED` только если сессия `SUCCEEDED` **и** куратор предложил хотя бы одно утверждение; иначе
`PARTIALLY_ANSWERED`. Страховка на границе финальной транзакции (`packages/domain/services/commit.py:406–415`,
step 5): хост перечитывает `applied_claims` из `staging.apply_recorded` (шаг 3b — созданные,
перепроверенные и принятые повторно claim-операции, T7.74) и, если terminal просит `verified` при
нуле применённых утверждений, пишет `partially_answered`. То есть ответ через **действующее**
утверждение (перепроверка якоря или дедуп-повтор) остаётся `verified` — правомерно. Выбранное
состояние записано в payload аудита фиксации (`COMMIT_ATTEMPT_COMMITTED`, аддитивный ключ
`question_state`, `:457`) — прежнее поведение было непроверяемым из БД.

**(г) Подпись состояния** (`apps/web/labels.py:64–67`): `question_state.partially_answered` —
«ответ неполон» / «Работа по вопросу завершена, но ответа не хватает: часть утверждений не записана
либо их нет вовсе.» (прежде: «есть частичный ответ» / «Ответ получен не полностью…», то есть подпись
утверждала наличие ответа). Итог работы при нуле действующих утверждений карточка и список получают
одним построителем (`apps/web/answer.py::build_answer_result` → `no_answer` = «Ответ не записан»),
поэтому пара «карточка vs список» расходиться не может.

### 8. Тесты (+16 к набору) и их краснота на прежнем коде

Собрано тестов: было **1637** (`fa8e235`), стало **1653**. Удалённых и ослабленных тестов нет.

Unit, `tests/unit/test_assertion_window.py` (файл: 36 тестов, из них 10 новых):
`test_deep_key_sentence_is_outside_windows_without_the_value_anchor`,
`test_question_value_anchor_puts_the_deep_value_into_the_fragment`,
`test_value_anchor_survives_nbsp_and_soft_hyphen_typography`,
`test_number_needle_does_not_match_inside_a_larger_number`,
`test_researcher_quote_anchors_the_window_only_if_verbatim_in_the_source`,
`test_absent_researcher_quote_is_ignored_and_injects_nothing`,
`test_quote_anchor_takes_priority_over_the_question_value`,
`test_anchor_windows_keep_the_t722_budget_and_bounds`,
`test_question_value_terms_ignores_bare_years_and_keeps_first_order`,
`test_researcher_quote_terms_caps_and_filters_chrome`.
Фикстура — страница той же структуры, что стендовая аналитическая страница ЦБ: 17 323 знака,
навигационный хром с датами и повторами формулировки вопроса, плотный числовой блок в середине,
ключевая фраза «Годовая инфляция в декабре 2025 года составила 5,59%» на offset **17212**. Замеры:
без новых сигналов окна `(0, len 2000)` и `(7470, len 2000)` — значения в фрагменте **нет**; с
`value_terms=['5,59', '21.01.2026', '5,6']` — `(0, 2000)` и `(16959, 364)`, значение внутри; с цитатой
— `(0, 2000)` и `(16912, 411)`; при наборе страницы «5,59\xa0%» (NBSP) окно `(16959, 365)` и NBSP
сохранён в фрагменте побайтово. Первый тест закрепляет прежнее поведение (`value_terms=()`) — то есть
дефект зафиксирован как ожидаемое свойство старого пути, а не выдуман.

Сценарии (новая страница + настоящая сессия на FakeLLM и локальном origin):
`tests/scenario/test_assertion_value_anchor_session.py` (+2) —
`test_question_value_pulls_the_deep_sentence_into_the_curator_prompt` (сессионный репродюсер отказа
подставки: кураторский промпт содержит «Годовая инфляция в декабре 2025 года составила 5,59%» —
проверено по запросу FakeLLM с секцией `# Evidence`; якорное окно не содержит навигационного хрома;
`claims_proposed == 1`) и `test_researcher_quote_pulls_the_deep_sentence_of_a_second_page` (первой
прочитана страница с этой формулировкой, вторая — длинная аналитическая; вопрос **без чисел**, глубокая
фраза попадает в фрагмент второй улики только через дословную цитату исследователя; придуманная моделью
фраза «Сверяю эту формулировку…» в фрагменте отсутствует). Улики читаны из БД:
`audit_events.payload->'evidence'` терминальной записи сессии, а не из памяти теста.

Итог без утверждений (`tests/scenario/test_orchestrator.py`, +3):
`test_succeeded_without_claims_does_not_verify_the_question` (успешная сессия `goal_reached` с пустым
конвертом куратора: состояние вопроса ≠ `verified`, равно `partially_answered`, в аудите фиксации
`payload->>'question_state' = "partially_answered"`, строка сессии осталась `succeeded`/`goal_reached`),
`test_curator_failure_keeps_the_question_honest_too` (тот же итог на другом пути нуля — отказ модели),
`test_one_applied_claim_still_verifies_the_question` (страховка от перекоса правки: одно применённое
утверждение — `verified`).

Витрина (`tests/scenario/test_web_questions_view_api.py`, +1):
`test_zero_claim_session_is_shown_as_no_answer_in_card_and_list` — для состояния, которое записывает
хост после такой сессии, список даёт `answer.kind = no_answer` и «Ответ не записан», карточка даёт ровно
тот же `kind`/`label`, ни одного утверждения как ответа не показывает, нигде в тексте итога слова
«проверен» нет, а подпись состояния — «ответ неполон».

**Изменённое ожидание прежнего теста (усиление, не ослабление).**
`tests/scenario/test_planning.py::test_invalid_plan_falls_back_to_template`: единственный шаг этого
репродюсера — `python.execute`, улики не создаются, и хост отклоняет предложение куратора целиком
(`curator_rejected: ["evidence_link[0]: index 0 out of range"]`). Знание не применено ни в коем виде,
а тест требовал `state == "verified"` — то есть закреплял ровно тот дефект, который правка убирает.
Ожидание изменено на `partially_answered` и добавлена проверка `outcome.claims_proposed == 0`.
Основание здесь записано; остальные три терминальные проверки того же файла (`:199`, `:274`, `:312`)
не тронуты и зелёные: там утверждение применяется.

Краснота на прежнем коде (рабочее дерево восстановлено побайтово после каждого отката; состояние
`git status` совпало с исходным):

1. откат только `apps/web/labels.py` к `fa8e235` → тест витрины краснеет:
   `assert 'есть частичный ответ' == 'ответ неполон'`;
2. откат `packages/domain/services/commit.py` + `apps/orchestrator/orchestrator.py` (правило terminal)
   → `test_succeeded_without_claims_does_not_verify_the_question` и
   `test_curator_failure_keeps_the_question_honest_too` краснеют на `'verified' != 'verified'`, а
   `test_invalid_plan_falls_back_to_template` с новым ожиданием — на `'verified' == 'partially_answered'`;
   `test_one_applied_claim_still_verifies_the_question` зелёный и там, и там (он проверяет, что правка
   ничего не обесценивает);
3. полный откат `apps/` + `packages/` → `tests/unit/test_assertion_window.py` падает на коллекции
   (`ImportError: cannot import name 'question_value_terms'`), а оба сценария окна краснеют по сути:
   во фрагментах нет фразы со значением (`assert deep, fragments` → пустой список; в фрагменте второй
   улики — навигация и середина комментария, как на подставке).

### 9. Полный прогон §6 перед коммитом

`ruff check .` — чисто; `mypy packages apps hostctl` — Success, 146 файла; образ
`noezema-sandbox:test` проверен наличием. `NOEZEMA_TEST_DATABASE_URL=… .venv/bin/pytest -n auto -q -m "not timing"`
— **1637 passed / 12 skipped** (2 мин 42 с — финальный прогон уже усиленного набора);
`-q -m timing` отдельным последовательным прогоном — **4 passed** (54 с). Первый прогон этого этапа дал
один провал — указанный выше `test_planning.py` (ожидание прежнего дефекта), что и потребовало
документированной правки ожидания; повторные полные прогоны зелёные.

### 10. Что делает менеджер на подставке .92 (агент к .92 не обращался)

Изменения на подставке — **только деплой кода и перезапуск сервисов**: миграций нет (`git diff
fa8e235..c2e919c -- alembic/versions` пуст), config-v17 и её пины (explorer-v9, canonical `5c402f4d…`)
не тронуты — повторная активация конфига не нужна, база не пересоздаётся.

1. Деплой ветки `impl/from-scratch` до `c2e919c` и перезапуск юнитов, которые исполняют сессии и
   показывают витрину (tick/wake-юнит и веб-юнит); `noezema-dev-unit-state.timer` трогать не нужно.
2. Проверка (а) без правки данных: `noezemactl ask` вопросом со значением из формулировки оператора
   (например «…точное значение 5,59% … или только округлённое 5,6%?»), затем разбудить узел (веб-команда
   «wake now» либо `systemctl start noezema-dev-tick.service`) и посмотреть
   карточку ответа: в тексте фрагмента улики должно читаться полное предложение источника со значением
   (прежний отказ — только «видны отдельные показатели (9,3%, 5,2%, 2,6%, диапазон 4–6%)»).
3. Проверка (б) по уже случившимся сессиям: `SELECT payload->>'question_state' FROM audit_events
   WHERE type='commit_attempt_committed' ORDER BY sequence DESC LIMIT 5` — у новых записей ключ есть;
   у сессий, где куратор ничего не предложил, состояние `partially_answered`, карточка вопроса показывает
   «Ответ не записан» и подпись «ответ неполон». Прежние строки `questions` правкой НЕ меняются:
   история состояний остаётся прежней, новый смысл применяется только к новым терминалам.
4. Смысл (2) и (3) — наблюдение, а не переоценка: знания и оценки этих сессий правка не трогает;
   переатрибуция/переоценка по T7.77/T7.78 здесь не требуются.
5. Следующий EVAL-прогон: результаты **не сопоставимы** с прежними заморозками, потому что текст
   фрагментов-доказательств меняется (ADR-0011 §7: нужен новый freeze и новая config version; payload
   config-v1…config-v17 не изменены). Боевой прогон — только по отдельному решению пользователя.

### 11. Остаток, пределы и риски

- Точный якорь помогает там, где значение или цитата **есть** в прочитанном источнике. Если страница
  даёт только округлённое значение (5 claim'ов ADR-0011 §4), окно честно покажет округлённое — куратор
  по своему промпту не привяжет его к точному утверждению: это не регресс, а то же честное поведение.
- Пересказ своими словами цитатой не считается (совпадения нет → сигнал молчит), поэтому выигрывает
  исследователь, который дословно переписывает прочитанное; промпт explorer-v9 об этом не спрашивается
  (правка промптов вне рамок задачи).
- Даты д.д.гггг из формулировки вопроса — реальный якорь, но дата может встречаться в списке дат
  навигации: отбор защищает окно вето на оглавление и запрет пересечения с основным окном; полностью
  риск не закрыт (окно может встать на список обновлений вместо текста).
- Значения берутся из формулировки **вопроса**; значение, которое модель назвала сама (не спрашивалось),
  попадает в окно только как цитата — при её отсутствии действует прежний ranking T7.22.
- `partially_answered` описывает последний терминал: повторная работа над уже отвеченным вопросом,
  ничего не записавшая, переведёт его в это состояние (отбор кандидатов берёт только `candidate`, так
  что путь редкий), при этом карточка по-прежнему показывает записанный прежде ответ через отношения
  T7.74. Различить «нет ни одного утверждения» и «записана часть» на уровне enum нельзя; различие
  видно по итогу витрины (`no_answer` против `answered`/`reverified`/`reused`) и по новому ключу аудита.
- Третье окно доказательства (например отдельное окно под цитату) не делалось: оно требует пересчёта
  бюджета секции `claims_evidence` кураторского контекста (`tests/unit/test_assertion_window.py::
  test_two_evidence_fragments_fit_the_claims_evidence_budget`) — кандидат отдельной задачи.
- Отдельная назревшая задача (здесь не решается): жёсткий гейт повторного `research.fetch` одного
  канонического адреса и лимиты fetch (T7.72/T7.76).

## T7.79a — точный сигнал фиксирует окно вместо выброса: разбор стендового промаха 2026-10-08

Решение пользователя от 2026-10-08 («Да, запускай»). Факты подставки (.92) взяты из дампов БД, полученных
ранее в этой же задаче; агент к .92 не обращался:

- сессия `9c4d3a24` (halogen), вопрос `47b25c88…`: «Публикует ли Банк России точное значение годовой
  инфляции за 2025 год — 5,59% — или во всех материалах только округлённое 5,6%? Укажи, в каких именно
  материалах ЦБ какое значение приводится»; прочитаны `cbr.ru/analytics/dkp/dinamic/CPD_2025-12/`,
  `cbr.ru/press/event/?id=28251`, `cbr.ru/press/reginfl/?id=64837`;
- публичная rationale исследователя цитировала фразу источника «Годовая инфляция в декабре уменьшилась до
  5,59% (в ноябре – 6,64%)»; дамп фрагментов-доказательств этой сессии: окна со стартов **3782** и **140**,
  ни в одном из них нет «5,59» → куратор работал без спрашиваемого значения.

### 1. Воспроизведение реальным текстом (код до правки)

Текст страницы скопирован в фикстуру `tests/fixtures/artifacts/21/218e9a1ea998f5483b128725c7582a850695f07672976308cfdf6488feaff4ea`
(content-addressed: sha256 файла = имя, 6283 знака) и подан ровно тем же сигналом, что даёт хост: полный
вопрос + публичная цитата. Номера строк ниже — коду **до** правки (`25b9d5d`): `select_assertion_windows` —
`assertion_window.py:654`, `_exact_anchor_starts` — `:612`, `_numeric_boundary_ok` — `:536`.

Термины вопроса после фильтра основ: `росси` (19 вхождений), `банк` (15), `инфляц` (13), `точно` (3),
`годово`, `значен`, `публик` (по 1); всего 53 совпадения, порог cap = 26 — cap на этой странице ничего не
снимает. Кандидаты терминального окна (оценка = различных основ в интервале ±800 знаков + числовой бонус
+ близость к началу; старт окна = позиция − `_LEAD` = 300):

| позиция плотности | различных основ | числовой бонус | бонус начала | оценка | старт окна |
|---|---|---|---|---|---|
| 4082 | 5 | 0,5 | 0,245 | **5,745** | **3782** |
| 4690 | 5 | 0,5 | 0,207 | 5,707 | 4390 |
| 4730 | 5 | 0,5 | 0,204 | 5,704 | 4430 |
| 4805 | 5 | 0,5 | 0,200 | 5,700 | 4505 |
| 3942 | 4 | 0,5 | 0,254 | 4,754 | 3642 |

Почему выигрывает 3782: плотность считается **числом различных основ**, и разрыв в одно основание (1 балл)
больше суммы обоих бонусов (0,5 + 0,5), поэтому табличный блок с пятью основами бьёт и лид страницы, и
более ранние места с четырьмя основами. Навигационный хром тоже матруется — в шапке и меню стоят
`инфляц` (0, 188, 206), `росси` (11…630) и `банк` (20…624), то есть его лучший кандидат даёт 3 различных
основы: оценка 3 + 0,5 (числа «107016», «8 800 300-30-00») + 0,5 (полный бонус начала) = **4,0**, что ниже
5,745; основ `точно`, `годово`, `значен`, `публик` в навигации нет. Окно стартует с 3782 и занимает
[3782..5782): там «(-5,4%), электротовары… (-5,0%)… обувь (-1,2%)» — столбцы таблицы процентов,
спрашиваемого значения там нет.

Точные сигналы на этом же тексте:

- «5,59%» встречается **ровно один раз** — `уменьшилась до\u00a05,59%` на смещении 3238 (типографика ЦБ
  разделяет числа неразрывным пробелом и переводами строк). Единственное окно вокруг него [2938..4938)
  пересекается с терминальным, и прежний код на этом основании **выбрасывал якорь целиком**;
- процитированная исследователем фраза найдена ровно один раз — 3196..3262 («Годовая инфляция в\u00a0декабре
  уменьшилась до\u00a05,59% (в\u00a0ноябре\u00a0– 6,64%)»), её окно [2896..4896) тоже пересекается с
  терминальным → цитата выбрасывалась по той же причине;
- «5,6»: два вхождения — 3191 («…в 2025 году составила 5,6%») и 4984 (собственная строка таблицы
  `7,7 \n 6,6 \n 5,6 \n Продовольственные`). Первое даёт окно [2891..4891) с пересечением; второе прежняя
  граница числа отвергала: складка склеивает колонки в `…9,58,88,18,07,76,6|5,6п…`, слева стоит цифра, и
  значение таблицы казалось частью чужого числа;
- после выброса всех точных сигналов слот доставался общему value-якорю T7.22: лучший кандидат — позиция
  440, оценка 58,35 (десятки «значимых» чисел навигации при множителе близости к началу), старт **140** =
  ровно тот второй фрагмент, который показал дамп подставки.

Итог воспроизведения прежним кодом: окна `(3782, False)` и `(140, False)` — ни одного с «5,59».

### 2. Что изменено (коммит `075ec0a`)

- **точный сигнал фиксирует окно** (`_exact_anchor_start` возвращает `(start, displaced)`, новые строки
  модуля: `anchor_signals` — `:600`, `_exact_anchor_start` — `:688`, `_best_value_window_start` — `:759`,
  `select_assertion_windows` — `:787`). Сигнал, найденный в источнике, но не имеющий положения без
  пересечения с терминальным окном, занимает второй слот сам; освобождённый слот заполняется общим
  value-якорем T7.22 (тот же цикл и тот же ranking, вынесенные в отдельную функцию без изменения
  семантики). Если неподходящего кандидата нет — остаётся одно окно. Исключение: значение уже целиком
  лежит внутри терминального окна (`primary_start ≤ raw_start` и `raw_end ≤ primary_start + budget`) —
  сигнал закрывается без вытеснения, дубля окна нет (прежнее поведение закреплено тестом);
- **приоритеты точных сигналов**: цитата → десятичные значения → даты д.м.гггг; внутри значений более
  специфичное (большее число цифр) раньше менее специфичного: «5,59» закрепляется раньше «5,6», независимо
  от порядка упоминания в вопросе. Даты опущены ниже десятичных значений по той же причине, по которой
  год-одиночка не считается значением: дата стоит в навигации и подвале почти каждой страницы, и на
  синтетическом тексте с хронологией дат прежнее поведение отдавало окно хронологии (старт 3083) вместо
  предложения со спрашиваемым значением;
- **граница числа проверяется по соседним символам исходного текста** (`_numeric_boundary_ok`), а не
  склеенной складки: перевод строки, пробел и неразрывный пробел — не цифры, поэтому собственная ячейка
  таблицы «5,6» на 4984 теперь принимается. Обманки не матчатся по-прежнему: «5,6» внутри «15,6», «5,64»,
  «5,60», «115,6» отсекается цифрой слева или справа; NBSP и софт-гифен **внутри** иглы учитывает карта
  `_fold_with_map` (raw-границы берутся из неё), процент и пунктуация справа — не цифры.

Стоп-критерий не сработал: число окон (2) и их длина (`SOURCE_ASSERTION_TEXT_BUDGET = 2000`) не изменились,
третье окно не вводилось, identity и dedup улики (§14.3), payload, промпты и их пины, пороги и rules engine
не тронуты; алгоритм первого окна T7.16 не изменён (старт 3782 на этом тексте остаётся тем же).

### 3. Результат на реальном тексте

| сигнал | окна до правки | окна после правки |
|---|---|---|
| вопрос + цитата исследователя | 3782, 140 — «5,59» нет | 140 и **2896** (в окне есть «5,59%», «6,64%» и вся процитированная фраза) |
| только вопрос (без цитаты) | 3782, 140 — «5,59» нет | 140 и **2938** (в окне есть «5,59») |
| цитата, которой нет в источнике | 3782, 140 | идентично строке «только вопрос»: текст модели ничего не дотягивает |

Число окон то же (2), каждое ≤ 2000 знаков, пересечений нет: вытеснённое терминальное окно заменено
запасным value-окном (старт 140 — тот же кандидат T7.22, что и раньше).

### 4. Тесты (+15 к набору) и их краснота на прежнем коде

Unit, `tests/unit/test_assertion_window.py` (файл: 50 тестов, из них 14 новых): терминальное окно на
реальном тексте без значения (`test_real_cbr_page_term_window_alone_misses_the_exact_value` — закрепляет
сам дефект), цитата+значение фиксируют окно с «5,59%», тот же вопрос без цитаты, обратный порядок значений
в вопросе (округлённое названо первым), неизменность числа окон и бюджета при вытеснении, чужая цитата
игнорируется, порядок и специфичность сигналов (`anchor_signals`), дата не вытесняет значение, границы
числа («5,6» не рвёт «115,64»; табличная ячейка через NBSP-склейку принимается), отсутствие дубля когда
значение уже внутри терминального окна, прежний промах без сигналов (поведение T7.22 закреплено как факт)
и вытеснение при наличии сигнала.

Scenario, `tests/scenario/test_assertion_value_anchor_session.py` (3 теста, 1 новый): страница с той же
геометрией (навигация с числами, ключевая фраза выше термино-плотного блока); проверяется, что фрагмент
улики содержит фразу со значением, что окон ≤ 2 и каждое ≤ 2000, что **запрос куратора к модели** (FakeLLM)
содержит эту фразу и что сохранённый claim несёт значение.

Краснота на прежнем коде. Полный откат `apps/orchestrator/assertion_window.py` до `25b9d5d` ломает импорт
новых тестов (`anchor_signals` отсутствует), поэтому поведение проверялось иначе: в рабочий модуль
ставился текст HEAD плюс один вспомогательный блок (перечисление сигналов) без правил вытеснения и без
новой границы числа. Прогон unit- и scenario-файлов: **8 провалов** (7 unit: `test_real_cbr_page_quote_and_value_pin_the_window_that_carries_559`,
`test_real_cbr_page_without_the_quote_anchors_the_exact_value_too`,
`test_more_specific_value_anchors_before_the_rounded_one`, `test_number_anchor_reads_a_table_cell_as_its_own_number`,
`test_overlap_case_exact_signal_displaces_the_term_window`, `test_date_signal_yields_to_the_asked_value`,
`test_real_cbr_page_specific_value_wins_when_question_names_the_rounded_one_first` и 1 сценарий
`test_exact_value_whose_window_overlaps_the_term_window_still_reaches_the_curator`) при **45 зелёных** —
из них 36 прежних unit и 2 прежних сценарных теста (7 новых unit-тестов оказались зелёными и на прежнем
поведении: они закрепляют факты, которые правка не меняет). Ни один прежний тест не переписан и не ослаблен
(diff unit-файла — только добавления; `+322`, удалений нет).

### 5. Полный прогон §6

Перед коммитом кода: `ruff check . packages apps hostctl` — чисто; `mypy packages apps hostctl` — 146 файла
без замечаний; образ `noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1652 passed,
12 skipped** (базовое значение было 1637 passed + 12 skipped: +14 unit и +1 сценарий, скипы те же — TLS-скипы);
`pytest -q -m timing` — **4 passed**. Повторный прогон перед документационным коммитом — те же результаты.

### 6. Остаток, пределы и риски

- Тексты фрагментов нового прогона отличаются от прежних: следующий EVAL-прогон требует новой заморозки и
  config version (ADR-0011 §7); payload'ы config-v1…config-v17, пины промптов и пороги не тронуты. Стенд
  .92 после правки не перезапускался (запрет на прогоны в этой задаче), проверка — на реальном тексте
  страницы из дампа.
- Цена вытеснения измерима: плотностное окно таблицы процентов уходит из фрагмента, его место занимает
  запасной value-якорь. Если куратору нужны оба контекста, нужно третье окно — это пересчёт бюджета секции
  `claims_evidence` (ADR-0011 §5), отдельная задача.
- Вытеснение сознательно не срабатывает, когда значение уже показано терминальным окном: иначе каждое
  попадание значения в плотностное окно ломало бы прежнее поведение T7.16/T7.22 (тест
  `test_value_already_inside_the_term_window_is_not_duplicated`).
- Пересказ цитаты своими словами по-прежнему цитатой не считается; значение, которого нет в прочитанном
  источнике, честно отсутствует и по промпту куратора к точному claim'у не привязывается (ADR-0011 §4).
- Уточнённый риск дат: опускание дат ниже значений снимает частый класс промаха (хронология разделов), но
  вопрос, где дата и есть спрашиваемое значение, теперь выигрывает только если десятичного значения в
  вопросе нет. Отдельного сигнала «вопрос именно про дату» в данных наблюдения нет — это кандидат
  отдельной задачи.

## T7.80 — профиль рассуждения движка по фазам вызова и обрезанный лимитом ответ: анализ до правок

Решение пользователя от 2026-10-08 («Запускай задачу B + C»). Замеры стенда предоставлены
пользователем (подставка .92, модель `halogen-flash-next` на `192.168.1.141`); агент к `.92`, к `.42`,
`.141` и `.48` не обращался, eval-run/смоуков не запускал: ни одного живого запроса к LLM в этой задаче
не было — вся проверка сделана на fake-сервере и тестовой БД.

### 1. Улика: три попытки куратора, которые не были тремя разными попытками

Сессия `1d0886fa`: каждая из трёх попыток куратора вернула **ровно 8192** completion tokens
(`model.max_output_tokens` = 8192) за 143–175 с, JSON оборван внутри строки («Unterminated string …
char 3350») → `LLMSchemaError «failed schema after 3 attempts»` → `curator_error_kind = "unavailable"` →
claims = 0. В `model_runs` ни одной из этих попыток нет: журнал получил только итоговый
синтетический ряд, а значит вопрос «почему куратор молчит» по БД unanswered — он был answerable только
через дамп HTTP-ответов.

Три факта определяют решение:

1. **Комната ответа halogen фиксирована**: рассуждение закрывается «by answer_room» примерно за ~1000
   токенов до потолка (`reasoning_closed_by`, `reasoning_closed_at` в `completion_tokens_details`).
   Поднимать `max_output_tokens` бесполезно для длинного JSON: срез переезжает, но остаётся.
2. **Выключается рассуждение двумя разными ключами**: `reasoning_effort: "none"` (halogen) и
   `chat_template_kwargs: {"enable_thinking": false}` (llama.cpp Qwen3.x) → `reasoning_tokens = 0` и
   валидный schema-JSON за доли секунды. `reasoning_effort: "low"` и `thinking_budget` движок игнорирует.
3. **Повтор прежнего запроса — это тот же запрос**: шлюз слал только `model`, `max_tokens`, `messages`,
   `response_format`; при `HALOGEN_TEMPERATURE = 0` ответ идентичен, то есть ретрай схемной осечки ничего
   не менял и только сжигал Attempt-бюджет и lease-время.

### 2. Что решено (детали — ADR-0030)

| решение | где | почему так |
|---|---|---|
| фаза вызова замкнута: `exploration`, `planning`, `extraction`, `verification`, `consolidation` | `packages/llm_gateway/reasoning_compat.py::REASONING_PHASES` | словарь совпадает с уже существующим `model.sampling.temperature_by_phase`; неизвестная фаза — `ValueError`, а не отсутствие политики |
| возможность движка отключить рассуждение — профиль в **env** (`NOEZEMA_LLM_REASONING_PROFILE`: `none` \| `halogen` \| `chat-template`) | `packages/llm_gateway/config.py` (+ `field_validator`) | это свойство движка и сборки, а не политика узла: ровно та же граница, что у `NOEZEMA_LLM_SCHEMA_PROFILE` (ADR-0012 §3); unknown профиль — отказ при старте |
| режим фазы — в **снапшоте** (`model.reasoning_by_phase`), разрешается на каждом вызове | `apps/orchestrator/orchestrator.py::_reasoning_mode` → `resolve_reasoning_policy(dict(snapshot.model))` | effective config (§3): сессия воспроизводима по снапшоту; кэшировать нельзя — head может смениться |
| профиль `none` не добавляет в запрос **ничего** | `reasoning_body_additions` возвращает `{}` | прежние прогоны (EVAL/EVAL-2/смоуки) остаются байт в байт: без этого сравнивость замеров ADR-0011 §7 теряется |
| обрезка — отдельный исход `LLMTruncatedResponseError`, не «схемная осечка» | `client.py` (`_TRUNCATED`, `completion_was_truncated`) | `finish_reason == "length"` либо упор в `max_output_tokens`; обрезанный документ отказывается, даже если он чудом оказался синтаксически целым |
| ровно один повтор ДРУГИМ запросом (режим `off`), без backoff; иначе немедленная честная ошибка | `client.py` (ветка после основного цикла) | повтор того же запроса при температуре 0 идентичен; движок обязан измениться, иначе смысл теряется |
| unusable-попытка получает строку `model_runs` (`finish_reason`, `output_tokens`, `output_schema_valid=false`) и на отказе, и на успехе после повтора | `orchestrator.py::_record_unusable_attempts` (5 точек успеха + fallback-ветки) | улика обрезки должна читаться из БД без дампа HTTP; recovery не стирает следы |
| маркер куратора `curator_error_kind = "truncated_output"` — значение payload'а, `AuditEventType` не расширяем | `orchestrator.py` (новая ветка `except LLMTruncatedResponseError`) | стоп-критерий: миграции и нового enum-значения не делал; `phase` в `model_runs` остаётся состоянием сессии |
| кривой `model.reasoning_by_phase` отвергается активацией до публикации снапшота | `packages/memory/activation.py::_validate_payload_reasoning` | §5.4.1 fail-closed: узел не должен получить снапшот, из-за которого все его сессии упадут в ValueError |

### 3. Карты фаз и что реально меняет провод

| фаза вызова | место в коде | есть ли вызов при config-v17/v18 | режим v18 |
|---|---|---|---|
| `exploration` | `_explore` (один шаг исследователя) | да (`payload.explorer.mode`) | `on` |
| `planning` | `_propose_plan` | нет (`planning.mode = "template"` → MVP-шаблон) | `on` — задекларировано заранее |
| `extraction` | `_extract` | нет (`extraction.mode = "off"`) | `off` — заранее |
| `verification` | `_verify` | нет (`verification.mode = "off"` → host no-op) | `off` — заранее |
| `consolidation` | `_curator` | да | `off` |

То есть при текущем снапшоте config-v18 меняет провод **только кураторского вызова** (и то лишь при
профиле, который умеет выключать рассуждение). Остальные режимы — предзадекларированы: включать эти
фазы будет другая задача, и её payload не должен менять политику «на ходу».

`model.sampling.temperature_by_phase` в payload'ах есть, но **не реализован**: ни один код его не читает.
Реализовать его в этой задаче означало бы поменять температуру реальных прогонов без замера (при
`temperature = 0` ретраи идентичны — факт 3 выше), поэтому он описан в ADR-0030 §6 и оставлен как есть.

### 4. config-v18 против config-v17

Единственная правка — новый ключ `model.reasoning_by_phase`:
`{"consolidation": "off", "extraction": "off", "exploration": "on", "planning": "on", "verification": "off"}`.
Все прочие разделы, включая `model.max_output_tokens = 8192`, `session_limits.max_explorer_steps = 16`,
пины explorer-v9 и curator-v8, пороги, `claim_type_rules`, бюджеты (Σ=26624) и окна модели — байт в байт
config-v17; ничего не удалено.

| payload | хеш файла | canonical (попадает в `config_snapshots.payload_sha256`) |
|---|---|---|
| config-v18 | `3cbd70840ad6c7454234666daf44d9e2befde9281cc822b554fa71d7f28b2009` | `b5605e4eb04d610ca387f732bfa35f4571750fd4370013f2f2a0e8e7e45f9dec` |
| config-v17 (откат) | `3d51cefc6c2193ad61627325f70bc433156495455c9eef80a60c0f9d38304a86` | `5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717` |

### 5. Тесты (+55) и их краснота на прежнем коде

- `tests/unit/test_reasoning_compat.py` (26): словарь фаз и режимов, профили и их additions (`none` → пусто),
  fail-closed неизвестного профиля и неизвестной фазы, предикат обрезки (`length`, упор в потолок, «stop» ниже
  потолка — не обрезка), проверка политики payload'а.
- `tests/unit/test_llm_truncation.py` (14): fake-сервер с `finish_reason = "length"` и **оборванным** JSON —
  второй запрос содержит `reasoning_effort="none"` (halogen) или `chat_template_kwargs.enable_thinking=false`
  (chat-template), `attempts_detail = [output_schema_valid False, True]`; профиль `none` → один запрос и
  немедленная `LLMTruncatedResponseError`; unknown профиль → `ValidationError` при построении конфигурации шлюза.
- `tests/scenario/test_curator_truncation.py` (5): сценарий куратора до `model_runs` и аудита — маркер
  `truncated_output`, строка с `finish_reason='length'`, `output_tokens=4096` (потолок тестового gateway) и
  `phase='consolidating'`; halogen-recovery применяет утверждение и сохраняет оборванную строку рядом с
  валидной; политика из снапшота попадает на провод до всякой обрезки; профиль `none` игнорирует политику,
  а не угадывает ключ.
- `tests/scenario/test_config_v18_activation.py` (3): активация v18 (canonical в БД, политика читается из
  эффективного снапшота тем же кодом, что и вызовы, `max_output_tokens` не поднят, прочие разделы дословно
  v17); кривая политика → `ActivationError` и head не сдвинут; откат v17 → прежний canonical и `None` у всех фаз.
- unit: `test_freeze_payloads.py` (+4: ровно один новый ключ, byte-stability и оба хеша, та же проверка,
  которой политику проверяет активация, прежние payload'ы не переписаны), `test_dev_stand_scripts.py`
  (дефолт v18 и откат v17 перепривязаны; +3 новых теста: env-строка профиля в bootstrap и сводке, профили в
  README, разделение payload/окружение).

Краснота на прежнем коде. Прежний `apps/orchestrator/orchestrator.py` + `packages/memory/activation.py`
(HEAD до правки) при новых тестах: **4 провала / 1 passing** — «the truncated attempt vanished from
model_runs: ['stop', 'stop', 'stop']», `assert None == 'none'` (на провод не попал ни один параметр
рассуждения: тело запроса было `{'model': …, 'max_tokens': 4096}`), `Failed: DID NOT RAISE ActivationError`.
На уровне шлюза прежний код краснеет самим импортом (`ImportError: cannot import name
'LLMTruncatedResponseError'`) и тестом unknown-профиля (`DID NOT RAISE ValidationError`). Тесты стендовых
скриптов краснеют на прежних файлах по существу: `git show HEAD~2:deploy/dev-stand/bootstrap.sh` не содержит
ни одной строки `LLM_REASONING_PROFILE` и активирует config-v17. Ни один прежний тест не ослаблен: из
изменённых тестовых файлов удалены только две строки ожиданий в `TestDevStandConfigVersion` (дефолт v17 → v18),
остальные его проверки сохранены; правок прежних проверок в сценариях нет.

### 6. Полный прогон §6

Перед коммитом кода (`c75f7dc`): ruff — чисто; mypy — «Success: no issues found in 147 source files»;
образ `noezema-sandbox:test` на месте; `-n auto -q -m "not timing"` — **1697 passed, 12 skipped** (базовое
значение T7.79a было 1652 + 12: +45 новых тестов, скипы те же); `-q -m timing` — **4 passed**. Перед
коммитом конфигурации и стенда (`e3cbc87`): тот же прогон — **1707 passed, 12 skipped** (+10) и **4 passed**.
Перед документным коммитом этого раздела — четыре зелёных прогона того же дерева: параллельная стадия
**1707 passed, 12 skipped** (183.58 с / 149.00 с / 205.69 с / 184.01 с) и последовательная `-q -m timing` —
**4 passed** (53.37 с / 47.47 с / 47.48 с / 47.50 с); ruff чисто во всех, mypy «Success: no issues found in 147
source files». Правки только документные, число тестов не изменилось. Известный flake `fake LLM server exited
during startup` ни в одном из этих прогонов не воспроизводился.

### 7. Шаги менеджера на стенде .92 (агент к .92 не обращается)

1. `cd ~/noezema && git pull` (пакет содержит `docs/eval/config-v18-payload.json`).
2. В `/etc/noezema/dev.env` (0600) одна строка: `NOEZEMA_LLM_REASONING_PROFILE=halogen` — значение по
   фактическому движку узла; для llama.cpp Qwen3.x — `chat-template`; сомнение → оставить `none` и
   проверить одним запросом к `/v1/chat/completions` до активации профиля (тот же порядок, что ADR-0012 §5).
3. `sudo systemctl restart noezema-dev-web.service noezema-dev-tick.service noezema-dev-maint.service`
   (профиль читается при старте процесса).
4. `.venv/bin/python -m hostctl.cli activate-online --payload docs/eval/config-v18-payload.json
   --reason "T7.80: model.reasoning_by_phase (curator without engine reasoning)" --drain-wait-seconds 120`.
5. Проверка: `./deploy/dev-stand/status.sh` — снапшот по canonical-префиксу `b5605e4e`; проверочный вопрос с
   длинным кураторским ответом (например «ключевая ставка ЦБ РФ на последнем заседании»): второй кураторский
   запрос содержит `reasoning_effort="none"`, утверждение появляется, а в `model_runs` видна оборванная строка
   с `finish_reason='length'`.

Откат: активация payload'а `docs/eval/config-v17-payload.json` (canonical `5c402f4d…`) + удалить строку
профиля из `dev.env` + рестарт юнитов. Прежние payload'ы (v13–v17) не переписаны; история коммитов не
переписывалась (`git reflog`: только `commit:`).

### 8. Что осталось непроверенным и что это меняет

- Профиль `halogen` на реальном движке в этой задаче **не проверялся** (LLM недоступен по условию задачи):
  замеры предоставлены пользователем, проверка — fake-сервером. Первый стендовый прогон с v18 обязан
  описать фактическое поведение (ADR-0011 §7: новый EVAL-прогон требует новой заморозки).
- С профилем `none` обрезанный ответ больше не «спасается» флуктуацией длины: прежний цикл из трёх
  идентичных попыток иногда случайно получала более короткий JSON, и сессия выживала. Теперь это честный отказ
  (`truncated_output`) — меньше ложных «unavailable», но и меньше случайных recovery; сравнивать прежние
  прогоны с новыми нужно с учётом этого (ADR-0030 §3).
- Строки `model_runs` для unusable-попыток добавляются **только** там, где attempt не прошёл схему: тесты,
  считающие строки журнала на чистых сценариях (`test_prompt_pinning.py`, `test_planning.py`,
  `test_verification.py`, `test_web_standalone_search_v14.py`), не сдвинулись; продуктовые гейты `model_runs`
  не читают. Метрика «три попытки куратора» в наблюдаемом виде заменена «одна попытка + один повтор либо
  немедленный отказ» — это изменение формы наблюдения, а не порогов (пороги и rules engine не тронуты).
- `temperature_by_phase` по-прежнему не читается кодом; K2 (`llamacpp-rocmfpx`) профиля рассуждения не получил:
  по замеру он почти не рассуждает, поэтому для него остаётся `none`, пока нет отдельного замера.
- Новых классов обрезки (например `finish_reason = "content_filter"`) предикат не считает обрезкой — они
  остаются прежними исходами; замкнутому списку нужен замер, чтобы расширяться.

## T7.81 — инструмент оператора для спорного утверждения (стенд .92, `c970bc08`): анализ до правок

Решение пользователя от 2026-10-08 («давай с п.1»): нужен вход оператора, чтобы (а) оспорить действующее
утверждение — оно перестаёт показываться как действующий ответ с бейджем «Проверено», автоматически ставится
вопрос на перепроверку, причина спора видна людям; и (б) снять утверждение вручную (недействующее состояние +
каскадная инвалидация зависимых). Стендовый пропуск — утверждение `c970bc08…` («независимые оценки совпали:
_rosstat_ и _sbercib_») с головой E3/`supported`, где вторая группа независимости — пересказ того же релиза.
Ни атрибуция (T7.75/T7.78), ни переоценка его не исправляют: склейка по значению не состоялась из-за округления
(«около 5,6%» против «5,59%», T7.78 §13), а группировка по registrable domain оставила два разных источника в
двух группах.

### 1. Факты, проверенные до выбора механизма

| # | факт | где проверено |
|---|---|---|
| 1 | Тип команды оператора — **закрытый enum, материализованный в CHECK** при миграции: `type text NOT NULL CHECK (type IN {_in_check(OperatorCommandType)})` | `migrations/versions/0002_*.py:193–206`; enum — `packages/domain/models/enums.py:379–389` («Free text is never parsed into this»), список спеки — §13.2:1783 |
| 2 | Значение, добавленное в `OperatorCommandType` сегодня, **не пройдёт CHECK** в уже размеченной БД: CHECK строился из enum в момент прогоном миграции 0002, а тестовые scratch-БД (`migrated_db`) строятся с нуля и CHECK получат новое значение. То есть новый тип команды без миграции дал бы зелёные тесты и падение на живом стенде | вывод из 0002:196 + fixture `migrated_db` (AGENTS §6); отдельной правки не требуется — это стоп-критерий |
| 3 | `claim_assessment_heads.prepared_by` — тоже закрытый CHECK, расширен миграциями до `session | rules_activation | reassessment_worker | system:cascade | system:barrier | system:source_graph | system:counter_resolution | commit_carryover`; значения «оператор» там нет | `migrations/versions/0022_session_admissions.py:50–60` (последняя правка списка; 0023–0025 её не меняют), спека §14:2059 |
| 4 | Состояния головы — `current | pending | invalid`; `invalid` пишут только рабочий переоценки («недостаток данных») и offline-rules активация. Head **не существует** без строки оценки: `current` ⇒ `current_assessment_id`/`epistemic_status` NOT NULL (AGENTS §3) | `packages/memory/reassessment.py:276`, `hostctl/offline_rules.py:248`, спека §8.6:1148 |
| 5 | Каскад §8.6 (`start_cascade`) **не имеет продуктового продюсера**: вызывают его только тесты; пересчёт оценок в проде делает `apply_source_graph_change` (T4.7) | grep `start_cascade` → только `tests/scenario/test_cascade.py`, `test_failpoints_m4.py`; продюсеры `apply_source_graph_change` — `apps/research_proxy/reattribution.py:304,672`, `derivative_pointer.py` |
| 6 | Инвалидация головы **не устойчива**: worker пересчитывает «только из существующих evidence» (`§8.6:1148`), поэтому head, снятый без изменения фактов, возвращается в `current` с прежней оценкой на ближайшем тике maint-юнита (60 с на стенде). В тесте это ровно фаза C: split-коррекция → worker → `supported` E3 обратно | `packages/memory/reassessment.py:549–577`, `tests/scenario/test_source_graph.py:384–392` |
| 7 | Спека **разрешает** оператору менять классификацию источников и запрещает менять статус утверждения мнением: «Operator attestation не разделяет группу. Исправление ложного объединения оформляется как отдельная source-graph correction с проверяемой provenance-цепочкой, actor и audit record»; «Если новый источник или correction объединяет ранее разные группы, зависимые claim assessments инвалидируются и пересчитываются» | §11.3:1667–1669; таблицы `source_graph_corrections` (миграция 0011:52–77, `kind CHECK IN ('merge','split')`, UNIQUE `(actor, from_source_id, to_source_id, kind, rules_version)`, колонка `reason_audit_event_id`) |
| 8 | Склеивание групп коррекцией `merge` реально понижает оценку: basis `correction:merge` попадает в снимок независимости, а `temporal_fact` требует `min_independence_groups = 2` для `supported`/E3 → одна группа ⇒ `insufficient_independence` ⇒ **E1/hypothesis** | `packages/memory/independence.py:351–398`, `config-v18-payload.json` (`claim_type_rules.temporal_fact`), `packages/memory/rules_engine.py:208–237` |
| 9 | Вопрос на перепроверку каскад уже создаёт **с цитатой statement'а** утверждения (ловушка T7.73 без этого не возникает) и детерминированным UUIDv5 — повтор его не дублирует | `packages/memory/cascade.py:364–391` |
| 10 | Новый `AuditEventType` не нужен: `CLAIM_INVALIDATED`, `CASCADE_STARTED`, `OPERATOR_COMMAND_RECEIVED/COMPLETED/REJECTED` и `SOURCE_GRAPH_CHANGED` уже в enum; последние три **ни один код не пишет** (продюсеров нет) | `packages/domain/models/enums.py:473,475,491–493,516`; grep `OPERATOR_COMMAND` → только enum |
| 11 | Подписи: тест полноты берёт значения категории `command_type` из самого enum — новое значение без подписи краснит тест | `tests/unit/test_web_labels.py:120`, AGENTS §7 (T7.64) |

### 2. Стоп-критерий этой задачи — применён частично

Не делаются: миграции БД и новые таблицы; расширение CHECK-списков `operator_commands.type` и
`claim_assessment_heads.prepared_by`; новые значения `OperatorCommandType`, `EpistemicStatus`,
`AssessmentState`, `AuditEventType`; изменение порогов группировки, PSL-списка, `group_source_graph`,
правил rules engine и payload'ов config-v1…config-v18; правка `ARCHITECTURE.md`; удаление уже проставленных
указателей производности (вариант C ADR-0029).

Из этого следует развилка, и она решена так:

- **(а) «оспорить» реализуем без миграций** — через механизм, который спека для этого и отведена (§11.3:
  correction + каскад + пересчёт). Операторское возражение оформляется **как факт о происхождении источника**
  («эта страница — пересказ той»), а не как мнение о claim: оно меняет классификацию, правила сами понижают
  оценку, бейдж меняется потому, что независимости стало меньше, а не потому, что так захотел оператор.
- **(б) «снять утверждение» в формулировке «стать `invalid` по воле оператора» — остановлено по критерию.**
  Для него нужен хотя бы один из запрещённых шагов: новое значение enum/CHECK в БД (тип команды или актор
  головы), новая таблица-флаг «снято», либо presentation-слой, который прячет `current`-оценку (запрещено
  ADR-0026 и §13.5: UI не назначает статус и не оформляет действующее знание по-своему). Факт 6 добавляет
  второй, уже технический аргумент: даже если голову перевести в `invalid` прямым UPDATE, рабочий переоценки
  вернёт её в `current` с той же оценкой E3, потому что улики не изменились — то есть «снятие» без нового
  долговременного состояния просто самоуничтожается за один тик. Варианты (1–4) описаны в ADR-0031 §5 и
  оставлены на решение пользователя.

### 3. Что выбрано для (а)

| решение | где | почему так |
|---|---|---|
| Носитель решения — строка `source_graph_corrections` (`kind='merge'`, `actor='operator:<вход>'`, `rules_version=RULES_ENGINE_VERSION`, `basis_artifact_id` = артефакт прочитанной пересказывающей страницы, `reason_audit_event_id` = событие журнала с причиной) | существующая таблица миграции 0011; новых колонок нет | §11.3 требует «проверяемой provenance-цепочки, actor и audit record» — колонки ровно для этого и созданы; UNIQUE-индекс даёт естественную идемпотентность повтора |
| Причина спора живёт в payload события журнала (`operator_reason`), а не в текстовых полях доменных таблиц | `packages/domain/services/audit.py` (существующий `AuditService`) | долговременная запись с актором и временем; никаких текстовых эвристик (запрет T7.74) при чтении UI: причина читается по `reason_audit_event_id` |
| Новый тип команды в `POST /api/v1/commands` — **не добавляем** | тот же путь, что ADR-0024 выбрал для вопросов: своя mutating-точка Command-API-семейства с admin-токеном и fail-closed гейтом 423 | факт 1–2: новый тип = миграция CHECK + правка §13.2; прецедент `POST /api/v1/questions` уже задокументирован как отклонение в ADR |
| Идемпотентность — естественный ключ коррекции, клиентский `idempotency_key` не принимается | UNIQUE `(actor, from_source_id, to_source_id, kind, rules_version)` | §20.10 («Idempotency hijack»): ключ генерирует хост; повтор с другой формулировкой причины возвращает ту же строку, а не вторую коррекцию |
| Пересчёт — только существующий каскад `apply_source_graph_change` + рабочий переоценки | `packages/memory/source_graph.py:327`, `packages/memory/reassessment.py` (тот же порядок, что `reattribution.py:304–311`) | никаких прямых UPDATE оценок и голов; actor каскада = операторский актор, значит журнал называет человека |
| Перепроверка — вопрос, который каскад уже создаёт | `packages/memory/cascade.py:364–391` | цитата statement'а (T7.73) и детерминированный id; отдельный механизм не нужен |
| Отмена — та же коррекция с `valid = false` + повторный каскад | `packages/memory/independence.py:340–356` (невалидная коррекция не участвует), тест-прецедент `test_counter_resolutions.py:463` | знание не удаляется: строка остаётся, пересчёт возвращает E3; прецедент «коррекция → split → E3» — `test_source_graph.py:384–392` |
| UI — честная отметка на странице утверждения («оспорено оператором»: кто, когда, причина, какие источники объединены) + бейдж, который после пересчёта считают правила | `apps/web/knowledge.py::claim_provenance`, `apps/web/labels.py` (новая категория подписей), `apps/web/reliability.py` не трогается | ADR-0026: presentation ничего не назначает; слово «проверено» остаётся только у бейджа (AGENTS §7, T7.65) |

### 4. Что увидит человек (ожидаемое поведение после правки)

1. На странице утверждения — блок «оспорено оператором» с причиной, актором и временем; рядом — две группы
   независимости, объединённые основанием `correction:merge`.
2. Пока идёт пересчёт, голова `pending`: карточка показывает «оценка ещё не принята», бейджа «Проверено» нет
   (правило lifecycle: pending никогда не подаётся как current).
3. После рабочего переоценки — `current` с `insufficient_independence`: **E1 · сверена целостность**,
   статус «гипотеза». Ни в ленте, ни в списке «Мои вопросы» это больше не выглядит подтверждённым ответом.
4. В очереди появляется вопрос «Переоценить утверждение (каскадная инвалидация): <statement>» — та же форма,
   что у существующего каскада; если перепроверка найдёт настоящую независимость, оценка поднимется сама.
5. Отмена возвращает E3 и бейдж — с записью отмены в журнале.

### 5. Тесты до правок (краснота на прежнем коде)

Репродюсер строится на данных стендового пропуска: утверждение `temporal_fact` c двумя источниками на разных
registrable domains, один — пересказ релиза другого; голова E3/`supported`; карточка отдаёт
`reliability.level == verified`. На HEAD до правок новые тесты краснеют по существу: эндпоинта спора нет
(404), CLI-команды нет (click «No such command»), блок «оспорено» на странице утверждения отсутствует.
Ни один прежний тест не ослабляется; число тестов только растёт.

### 6. Реализация (а): что именно сделано

| часть | где | как |
|---|---|---|
| сервис спора | `packages/memory/dispute.py` (новый модуль: чистые функции + рабочий контур) | `dispute_claim(...)`: проверить причину → зафиксировать утверждение (`SELECT … FOR UPDATE`) → взять **источники именно этого claim'а** из его улик → разрешить пару «первоисточник ← пересказ» по адресам → событие `operator_command_received` с причиной в payload → строка `source_graph_corrections{merge}` (создать или воскресить ранее снятую) → `apply_source_graph_change(source_ids = {пересказ, первоисточник})` → вопрос на перепроверку (`put_operator_question`, origin `message`, цитата statement'а) → `operator_command_completed`. `cancel_dispute(...)`: `valid = false` (строка не удаляется) + второй каскад по той же паре. `list_claim_disputes(...)` — чтение для витрины, в том числе «кем снят» |
| Command API | `apps/web/api.py`: `POST /api/v1/knowledge/claims/{claim_id}/dispute`, `POST …/dispute/cancel` | admin-токен и гейт здоровья юнитов (423) — ровно как у команд и приёма вопроса; 201 новый спор / 200 повтор (`replayed: true`) / 200 отмена; отказ → 404 или 409 с подписанной причиной |
| CLI | `hostctl/cli.py`: `noezemactl claim-dispute` и `claim-dispute-cancel` | тот же сервис, актор `operator:hostctl`; идемпотентный повтор назван повтором; отказ → exit 2 и строка «отказ (<код>) <подробность>», без трейсов |
| подписи | `apps/web/labels.py`: категории `graph_correction_kind`, `dispute_state` («оспорено оператором» / «спор снят»), `dispute_actor` (веб-витрина / хостовая консоль) + 8 подписанных отказов спора | полнота проверяется из кода: значения вида коррекции — из CHECK таблицы, акторы и коды отказов — из констант модуля спора (`tests/unit/test_web_labels.py`) |
| витрина | `apps/web/knowledge.py::claim_source_corrections` → ключ `operator_corrections` в provenance; форма спора и лента споров на `/claim/{id}` | presentation ничего не назначает: показывает строку коррекции, актора, время, причину из журнала и состояние («оспорено оператором» / «спор снят»); бейдж по-прежнему считает `reliability.py` по оценке правил |

Формат запроса API (доп. поля запрещены, `extra="forbid"`):

```json
POST /api/v1/knowledge/claims/{claim_id}/dispute      заголовок: X-Admin-Token
{"primary_uri": "rosstat.example/press/inflation-sep-2026",
 "retelling_uri": "https://sbercib.example/economy/pochemu-inflyatsiya-5-6?utm=1",
 "reason": "Страница банка дословно повторяет абзацы пресс-выпуска и его таблицу"}
→ 201 {"claim_id","replayed":false,"correction_id","actor":"operator:web","reason",
       "assessments_requeued":1,"claims_touched":1,"recheck_jobs_created":1,
       "question_id","question_created":true,"operator_corrections":[…]}

POST /api/v1/knowledge/claims/{claim_id}/dispute/cancel
{"reason": "первоисточник всё-таки отдельный: разные таблицы и даты"}   (поле можно опустить)
→ 200 {"cancelled":true,"correction_id", …, "operator_corrections":[…]}   (та же строка, valid=false)

отказ → 409 | {"rejected":true,"reason":"dispute_source_not_found","detail":"retelling: https://…"}
```

Формат CLI (нужен `NOEZEMA_DATABASE_URL`; адрес можно вставить без схемы и с query — он нормируется тем же
`normalize_uri`, что и группировка источников):

```
noezemactl claim-dispute --claim <полный uuid> \
    --primary <адрес первоисточника> --retelling <адрес пересказа> --reason "<причина>"
  claim-dispute: оспорено
    утверждение   <uuid>            коррекция     <uuid> (merge: <пересказ> → <первоисточник>)
    актор         operator:hostctl  причина       <текст оператора>
    перепроверка  снятых оценок: 1, затронутых утверждений: 1, заведённых задач пересчёта: 1
    вопрос        <uuid> (новый, позиция N)
noezemactl claim-dispute-cancel --claim <uuid> [--reason "<почему спор снимается>"]
отказ: exit 2, «claim-dispute: отказ (dispute_source_not_found) retelling: https://…»
```

### 7. Отменимость и пределы механизма — честно

- Спор отменяется (`…/dispute/cancel`, `claim-dispute-cancel`): строка коррекции остаётся с `valid = false`,
  повторный каскад + пересчёт возвращают прежнюю оценку; в provenance видно и исходный спор с причиной, и снятие
  (кем, когда, почему). Знание, улики и все прежде сделанные оценки не удаляются (§14).
- Предел честности: каскад §11.3 инвалидирует те утверждения, **чьи улики затрагивают эти источники**. Утверждение,
  связанное с спорным только через `claim_dependencies`, остаётся `current` — это существующая семантика
  `apply_source_graph_change`, и она не расширена (тест `test_every_claim_that_used_those_sources_is_requeued`).
- Спор работает лишь там, где у хоста есть артефакт прочтения пересказывающей страницы: `basis_artifact_id`
  берётся из улики. Без него коррекция — attestation без provenance‑цепочки; fail-closed требование артефакта
  оставлено отдельной задачей (ADR-0031 §8).
- Ручное «снять утверждение» (б) **не реализовано**: стоп-критерий задачи + самоуничтожение прямого `invalid`
  за один maint-тик. Варианты 1–4 и рекомендация — ADR-0031 §6; решение за пользователем.

### 8. Тесты и прогоны

| тест | что закрепляет |
|---|---|
| `tests/unit/test_claim_dispute.py` (12) | границы причины (3…500), нормирование адреса (`http↔https`, query, хвостовой слэш, порт, www-хост), fail-closed выбор пары среди улик утверждения и отказ «это один и тот же источник», текст вопроса (цитата statement'а, потолок, детерминизм), подписи всех отказов спора, отсутствие новых значений в CHECK `claim_assessment_heads_prepared_by_check` миграции 0022, отмена = `valid = false` без удаления |
| `tests/scenario/test_claim_dispute.py` (10) | реальный HTTP: 201 + коррекция с актором/видом/основанием + пара событий ленты с причиной + head→pending с NULL‑парой + задача пересчёта `source_graph_change` + вопрос в очереди; бейдж `verified` → «нет» **только** после рабочего переоценки (`insufficient_independence`, одна группа с основанием `correction:merge`); повтор = 200 `replayed` без дубликов; затронуты оба утверждения с этими источниками, связанное через зависимость — нет; отказ подписан и не пишет ни строки; второй оператор на той же паре получает отказ (откат целиком); отмена возвращает E3/2 группы и сохраняет прежнюю причину; 401 без токена и 423 при открытом переходе юнитов; provenance называет спор высказыванием оператора |
| `tests/scenario/test_cli_claim_dispute.py` (6) | CLI-контракт: exit 0 с id коррекции/вопроса и актором `operator:hostctl`, повтор назван повтором, cancel → прежняя E3 после пересчёта, отказ = exit 2 с кодом и без трейсов, требуется полный uuid, cancel без спора → отказ |
| `tests/unit/test_web_labels.py` (+1) | краснота полноты по построению: новый актор спора или новый код отказа без подписи падают тот же тест полноты |

Прогоны §6 (после правок): ruff — All checks passed; mypy strict — Success, no issues found in 148 source
files; `pytest -n auto -q -m "not timing"` — **1736 passed, 12 skipped** (до правок было 1707 passed, 12
skipped: добавлено ровно 29 тестов, ни один прежний не ослаблен); `pytest -q -m timing` — **4 passed, 1748
deselected**. Контейнеров и тестовых баз стало ровно столько же, сколько до правок (замеры в отчёте по задаче:
`docker ps -a` = прежнее число, `noezema_mig_*` = прежнее число); ни одного форсированного `DROP DATABASE` в
хождении новых тестов.

### 9. Что делает менеджер на `.92` (агент к `.92` не обращался)

Раздел «Как оспорить утверждение оператору» в `deploy/dev-stand/README.md`: найти id утверждения в ленте
(`GET /api/v1/knowledge/claims` или страница `/claim/…`), проверить адреса источников в provenance, вызвать
`noezemactl claim-dispute …` (или ту же форму на карточке утверждения), дождаться maint‑тика (60 с) и увидеть
исход пересчёта; откат — `claim-dispute-cancel`. Ни миграций, ни перезапуска юнитов, ни смены снапшота правил для
этого не требуется.

## T7.82 — куратор слеп к ключевому факту прочитанной страницы и к собственной опоре на прежнее знание (стенд .92, сессия `f0d7e844`): два дефекта и две правки

Замер подставки `.92` (сессия `f0d7e844`, config-v18, halogen-flash-next; БД читалась только SELECT, агент к
`.92` и к LLM не обращался). Вопрос — годовая инфляция 2025 по официальным данным и независимым оценкам.
Исследователь прочитал страницу Банка России (аналитический материал «Инфляционные ожидания и потребительские
настроения» № 12(108)) и в финальной rationale назвал наблюдение населения 14,5%. Куратор этого значения не
увидел: его фрагмент `source_assertion`-улики был выбран окнами **в момент fetch** — по сигналам, накопленным
до того, как исследователь дочитал страницу до ключевой фразы. Второй дефект: ответ оперся на уже записанное
утверждение `9266248e…` («годовая инфляция 2025 = 5,59%», E4), которое было в контекст-паке и использовано
выводом, но карточка показывала только два новых claims — связь «ответ использовал это знание» нигде не была
видна. Оба дефекта закрыты раздельно: (а) реселекция окон по финальной rationale (`b98c592`), (б) честная опора
на уже записанное знание через новое факультативное поле кураторского ответа и конфигурацию config-v19
(`fff30ba`, ADR-0032).

### 1. Дефект (а): окно выбирается до прочтения факта — анализ смещений

Маркеры живой страницы (артефакт `7e2eb441…`, sha закреплён тестом; текст сохранён с 11 CRLF-последовательностями).
Смещения зависят от представления: в побайтовом артефакте начало фразы «годовая инфляция не изменилась и
составляла 14,5%» — **2410**, служебное слово «подраздел» того же текста — **3960**; в выгрузке журнала стенда
(только LF) те же маркеры — **2452** и **3957**. Разница ровно от представления (переносы строк и экранирование
неразрывных пробелов), смысл измерений тот же; тесты пинят артефактные числа и проверяют сам факт duality.

Прежние окна чтения (терминологическое от значения вопроса + служебное слово) фразу не накрывали ни одним
окном — куратор получал фрагмент без 14,5% (репродюсер `test_real_cbr_page_fetch_windows_miss_the_key_fact`).

**Правка (а)** — после завершения исследования (включая шаг с финальной complete-rationale) хост заново
выбирает окна каждой `source_assertion`-улики тем же модулем `apps/orchestrator/assertion_window.py`, где роль
`explorer_text` играет сигнал исследователя = последние `RATIONALE_SIGNAL_STEPS=8` строк финальной rationale,
обрезанные по `RATIONALE_SIGNAL_CHARS=6000`. Точные якоря — только то, что исследователь **действительно сказал
и что реально есть в этом источнике**: дословные цитаты (принимаются при ровно одном вхождении,
`_MAX_QUOTE_OCCURRENCES=1`) и числовые значения его текста; порядок сигналов: уникальные цитаты → значения
вопроса → значения вывода (свежие раньше) → даты. Нет ни одного точного якоря — окна остаются как при fetch
(`reselect_assertion_fragment` возвращает `None`, улика не трогается). Бюджет `SOURCE_ASSERTION_TEXT_BUDGET=2000`,
число окон (2), дословная природа вырезов, идентичность §14.3 (`source_assertion_identity(original_sha256,
chunk_id, kind)` — assertion_text в идентичность не входит) не изменены; в БД не записывается ничего нового:
исходные тексты страниц держат только в памяти сессии (`SessionContext.evidence_source_texts`), payload улики
меняется в единственной точке — мутация `payload["assertion_text"]` перед VERIFYING (после проверки abort/STOPPING).
Вето границы «оглавление/служебный раздел» — симметричный узкий спан `2×match_span(400)` от сырого старта
совпадения. Разбор и механизм: ADR-0011 §12.

Тесты (а): `tests/unit/test_assertion_window_reselect.py` (11) — репродюсер дефекта, реселекция пинит окно
2154 = raw_start(2454) − lead(300) с фразой внутри, отсутствие якоря → окна как при fetch, цитата при двух
вхождениях отвергается, TOC-вето, fold-совпадение (NBSP/софт-гифен) без изменения дословности;
`tests/scenario/test_window_reselect_session.py` — полная сессия: куратор получает фрагмент с 14,5%.
Краснота на прежнем коде доказана временным откатом.

### 2. Дефект (б): опора на прежнее знание невидна — механизм честной связки

Решение (не перепроверка и не переоценка): кураторский prompt **curator-v9** (`prompts/curator/curator-v9.md`,
sha256 `8897e6c5d7fb…`; тело curator-v8 сохранено байт в байт, добавлены пункт протокола и правило 10 — ровно
3 ханка diff) получает факультативное поле `relied_claim_ids` — перечень id УЖЕ записанных утверждений из
контекст-пака этой сессии, на которые опирается ответ. Хост в `_curator`:

- разрешает каждый id тем же разрешателем, что у перепроверки (`resolve_claim_reference` против
  `_pack_claim_ids(knowledge)`): полный UUID или однозначный hex-префикс; видимые id — строки `[c:<uuid>]`
  контекстного пакета (урок T7.73: пакет собирается лексикой FTS);
- отказ по одному id **не отменяет предложение целиком** (в отличие от fail-closed гейта перепроверки):
  причина пишется в `relied_claims_rejected` payload уже существующего события `claim_created`;
- дедупликация полного id и его же префикса; потолок записи `RELIED_CLAIMS_LIMIT=8`;
- ключи `relied_*` появляются в payload только когда они непустые — payload старых сессий байт в байт прежний.

Никакого нового AuditEventType, никаких миграций: факт живёт в payload события `claim_created`
(`payload->'relied_claim_ids'` — канонические полные uuid-строки). По опертому claim не производится ни одной
staging-операции, не пишется `claim_reverified`, не начисляется оценка, head и строка claim не меняются.

Витрина: карточка вопроса показывает опертые утверждения **отдельным блоком** под записанными выводами —
связь «использовано из знаний» (четвёртый ключ `claim_relation` в словаре подписей; RELATION_KEYS теперь
created/reverified/reused/relied, но touched_claims_cte блок `relied` не выдаёт, поэтому ранги прежних
результатов не изменились). Выборка — из payload событий этой сессии (`jsonb_array_elements_text` + uuid-гард,
`DISTINCT ON (claim)`, потолок `MAX_RELIED_CLAIMS=16`); claims, уже показанные в блоке записанных выводов,
из блока опоры исключаются; бейдж и строки подтверждения — тот же `assessment_view`/`describe_verification`,
пересчёта нет. Проверяющий вопрос стенда: «годовая инфляция 2025 по независимым оценкам» — карточка обязана
показать прежнее утверждение строкой «использовано из знаний».

Конфигурация: `docs/eval/config-v19-payload.json` = config-v18 ровно с одной правкой (пин `prompts.curator` →
curator-v9); canonical payload **`4d76c000a87395b0887eedfcee4a25f58c3c3dde2f390b7dbe2140c189a8ce3d`**, sha256
файла `0845315721…` (дuality canonical/файл — AGENTS §6); payload'ы v1…v18 не переписаны, config-v18
(canonical `b5605e4eb04d…`) — documented-откат. Bootstrap/reset-db dev-стенда по умолчанию активируют v19.

### 3. Наблюдение (не правка): collapse шагов работы в карточке

Карточка ответа вместо перечня шагов показала «выполнил ещё несколько шагов работы: N». Это штатное поведение
с T7.65 (коммит `e98094b`): `build_answer_steps` сворачивает шаги сверх `MAX_STEPS=6` в одну строку-счётчик.
На config-v18 это стало встречаться чаще, потому что лимит шагов исследователя поднят с 10 до 16 (config-v16) —
лента просто упирается в потолок отображения чаще. Правки не требует; раскрывающийся список — отдельный
кандидат, если оператору потребуется полная лента.

### 4. Тесты и прогоны

- после (а): `pytest -n auto -q -m "not timing"` — **1748 passed, 12 skipped** (+12 тестов относительно baseline
  T7.81); `-m timing` — **4 passed**; ruff чистый; mypy strict — no issues in 148 source files;
- после (б): тот же прогон — **1755 passed, 12 skipped** (новые: `tests/scenario/test_relied_claims.py` (3:
  разрешение полного id и префикса с дедупликацией, честные отказы без провала сессии, отсутствие
  claim_reverified/новой оценки/развода staging, блок карточки «использовано из знаний» с прежним бейджем E3;
  предложение curator-v8-формы → payload байт в байт прежний) и +1 тест `tests/unit/test_dev_stand_scripts.py`
  о конфиге v19/canonical/откате); `-m timing` — **4 passed**; mypy strict — no issues in 148 source files;
- краснота (б) на прежнем коде доказана временным откатом `packages apps` при сохранённых тестах: 2 теста
  красные (`extra="forbid"` отвергает новое поле), контрольный зелёный.

### 5. Что делает менеджер на `.92`

`git pull` → `./deploy/dev-stand/bootstrap.sh` (дефолт активации — config-v19; либо одна команда
`activate-online --payload docs/eval/config-v19-payload.json --reason "T7.82(б) …" --drain-wait-seconds 120`,
раздел README «Опора на уже записанное знание»). Проверка: `status.sh` показывает снапшот по префиксу
`4d76c000`; затем вопрос про годовую инфляцию 2025 по независимым оценкам — в ленте у `claim_created` может
появиться `relied_claim_ids`, карточка вопроса показывает опертое утверждение связью «использовано из знаний»,
а фрагменты source_assertion-улик содержат процитированные исследователем значения. Откат — payload v18
(`NOEZEMA_DEV_CONFIG_PAYLOAD=…/config-v18-payload.json`).

## T7.83 — одно и то же значение показателя записывается дважды рядом с собой: хостовый гейт дублей по значению и абзац о перефразе в промпте куратора (стенд .92, сессия `f452a978`)

Дефект воспроизведён на реальных утверждениях стенда, анализ выполнен до кода, краснота тестов на
прежнем коде доказана. Реализация: коммит `8fc9d54` (а), промпт/конфигурация: `d254e18` (б). ADR-0033.

### 1. Дефект

Вопрос оператора: «Сравни официальную годовую инфляцию в России за 2025 год (Росстат) с наблюдаемой
населением инфляцией по опросу инФОМ… пересказы официальных данных не считай независимыми». Куратор
в `summary` объявил перепроверку обоих утверждений контекст-пака, но обе операции принёс с
`existing_claim_id: null`. Рядом с E4/0.95 supported-утверждением

> `9266248e…` (temporal_fact): «Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю
> 2024) составила 5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)»

записался новый claim `e189c80d…` «Официальная годовая инфляция … составила 5,59% (по данным
Росстата)» — на единственном пересказе первоисточника. Карточка показала одно значение одного
показателя за один период дважды, с противоречащими бейджами. Одновременно: новое наблюдение
`391c4388…` («Наблюдаемая населением годовая инфляция … 14,5% (по данным опроса инФОМ для Банка
России)») дубликатом не является — это другой показатель с другими значениями относительно кандидата
`6d9a12ff…` (13,1%/15,6%), и оно обязано остаться новым claim'ом.

### 2. Почему byte-exact дедуп T7.9 тут слеп и что такое «дубль по значению»

Дедуп T7.9 сравнивает канонизированные строки statements; перефраза («Официальная …», порядок
слов, ссылка на первоисточник) для него — другой текст. Детерминированный анализ формулировок
фикстуры (до написания кода) дал определение: дубль = тот же `claim_type`, равное непустое множество
значимых десятичных значений формулировок и покрытый период; границы числа — та же семантика, что у
assertion_window (ADR-0011, `_numeric_boundary_ok`): `5,6` ≠ `5,59`, «105,59» — одно число и никогда
не вхождение «5,59», годы и целые без разделителя значениями не считаются; неразбираемый токен —
молчание про всю формулировку. Пара e189c80d↔9266248e совпадает строго (значения {5,59}; год
операции 2025 ⊆ годы кандидата 2025∪as_of-2026), пара 391c4388↔6d9a12ff — нет ({14,5} ≠ {13,1;15,6},
к тому же тип другой).

### 3. Решение гейта и доказуемость «не понизить»

Гейт живёт в `_curator` после гейта перепроверки T7.34 и разрешения relied-id (T7.82), до стейджинга;
кандидаты — утверждения контекст-пака с головой оценки в снапшоте конфигурации сессии (та же
видимость, что у T7.34/T7.9: «куратор видел в контексте»). Операции с явным `existing_claim_id` не
трогаются вообще. Чистая функция — `apps/orchestrator/value_duplicate.py` (ни БД, ни LLM). Матрица:
ровно один строгий кандидат и все связи `supports` → staged-копия операции получает
`existing_claim_id` кандидата (честная склейка, variant (i)); ≥2 кандидатов, есть `counters`, нет
`supports`, нет строгого кандидата — операция остаётся как предложена (variant (ii)), решение с
человекочитаемой причиной. Склейка не может понизить оценку: union-оценка перепроверки монотонна по
evidence-строкам и группам (текстового канала слияния нет; одна новая страница присоединяется к
группе, но не разъединяет её — ADR-0029), counterevidence в кандидата не протекает (условие
all-supports), `as_of`/`date_anchor`/scope сохраняет `packages/memory/reverify.py` (правило 8
кураторского протокола, при конфликте дат — аудит `as_of_conflict`); для кандидата фикстуры понижение
исключено арифметически: boost E4 требует `grade.level < 4` (`rules_engine.py:221–227`, потолок уже
достигнут), confidence-множитель `min(1, groups/2)` насыщен. Знание не удаляется, оценки не
подделываются (§3 сохранены полностью).

### 4. Запись в аудит без новых типов и миграций

Решение каждой затронутой операции — ключ `value_duplicates` (`claim_index`, `target`, `action`,
`reason`) в payload **уже существующего** события `claim_created`; ключ появляется только при непустом
списке, поэтому payload сессий без дублей байт в байт прежний (закреплено тестом равенства набора
ключей). Исходное кураторское предложение в аудит не переписывается: массив `claims` хранит
`existing_claim_id: null` там, где модель его не принесла; подменённый id живёт только в
staging-операции. Нового AuditEventType нет, миграции нет, API-поля только добавлены.

### 5. Тесты (19 новых), краснота на прежнем коде, прогоны

- `tests/unit/test_value_duplicate.py` (12) — границы значений и периодов, обе пары фикстуры
  дословно, негативы 5,6/105,59/другой год/другой тип, два кандидата и counters → честный kept;
- `tests/scenario/test_value_duplicate_gate.py` (3) — полная сессия на fake LLM по форме кураторского
  вывода f452a978: предсостояние кандидата E4/0.95 supported зафиксировано до предложения; после —
  дубль не создан ( staging claim[1] указывает на id кандидата), кандидат перепроверен со своей
  якорной датой (в аудит `as_of_conflict`, stored_as_of 2026-01-21), оценка осталась ровно E4/0.95
  supported, новых evidence 5, наблюдение 14,5% — новый claim E1/hypothesis, карточка показывает
  «5,59» один раз связью «перепроверено этим вопросом», опертое наблюдение — «использовано из
  знаний»; payload `claim_created` содержит ровно одну запись `value_duplicates`; контроли:
  предложение без дублей → payload байт в байт прежний, явный `existing_claim_id` → путь T7.34 без изменений;
- краснота до правки кода: unit-файл красный отсутствующим импортом модуля; сценарий на HEAD
  воспроизвёл дефект ровно как на стенде — карточка содержит второй claim с тем же значением,
  что поймано assertion'ом `dup_count[0] == 0` в `test_value_duplicate_gate.py:320`
  (`1 failed in 2.85s`, все предварительные проверки состояния зелёные);
- после (а): §6 целиком зелёный — ruff чистый, mypy strict «no issues found in 149 source files»,
  `-n auto -q -m "not timing"` — **1770 passed, 12 skipped** (+15 к baseline T7.82: 12 unit + 3
  сценария), `-m timing` — **4 passed**;
- после (б): **1774 passed, 12 skipped** (+4: пер-файловая параметризация сканеров
  `test_prompt_example_no_real_data.py` подхватила curator-v10 (+3) и +1 тест конфигурационной пары в
  `test_dev_stand_scripts.py`), timing **4 passed**.

### 6. (б): curator-v10 и config-v20

`prompts/curator/curator-v10.md` — тело curator-v9 байт в байт + абзац к правилу 7 (идентичность
факта = показатель+значение+период, не формулировка; перефразу оформляй `existing_claim_id` сам, не
полагаясь на хост), sha256 `8b940bf5…`. `config-v20` = config-v19 ровно с одной правкой — пин
`prompts.curator`; canonical `2b065470677e167dceecb9c91240c94a7c54d91408000e44e51e97feb3c18f9f`,
canonical v19 (`4d76c000…`) не изменился (тест сравнивает payload'ы по ключам). Дефолт стенда —
config-v20, откат — config-v19; README получил раздел активации. Гейт хоста от пина не зависит:
промпт сокращает частоту дефекта на входе, детерминированность гарантирует хост.

### 7. Что делает менеджер на `.92` (агент к `.92` не обращался)

`git pull` → `./deploy/dev-stand/bootstrap.sh` (дефолт — config-v20; либо одна команда
`activate-online --payload docs/eval/config-v20-payload.json --reason "T7.83(б): curator-v10 pin …"
--drain-wait-seconds 120`, раздел README «Дубль по значению»). Проверка: `status.sh` — снапшот по
префиксу `2b065470`; затем тот же вопрос, что выявил дефект (официальная инфляция 2025 против
инФОМ): карточка обязана показать «5,59» один раз связью «перепроверено этим вопросом», новое
наблюдение — отдельной строкой с relation «создано»/«использовано из знаний»; в ленте `claim_created`
может появиться ключ `value_duplicates`. Откат — payload v19. Уже записанная пара `e189c80d…` +
`9266248e…` на карточке f452a978 **не склеивается и не удаляется задним числом**: инструмента слияния
существующих claims нет и заводить его ради косметики нельзя (знание не удаляется, §3);
`hostctl claim-dispute` (ADR-0031) неприменим — реального расхождения значений нет, фиктивная
counterevidence подделала бы оценку. Карточка остаётся как есть; повторение дефекта предотвращено
гейтом.

### 8. Непроверенное и пределы

- поведение живого куратора (не fake) под curator-v10 на стенде не замерено — агент к .92 и LLM .42/.141/.48
  не обращался; хостовый гейт работает независимо от промпта, но снижение частоты ошибок модели —
  гипотеза до первого замера;
- перефразы с дрейфом числа («около 5,6%») гейт не склеивает и не должен: 5,6 — другое значение,
  новый claim законен; текстово-семантическая идентичность метрик в T7.83 не проверялась (осознанно:
  ложный пропуск безопаснее ложной склейки) — этот предел закрыт уточнением T7.83a (п.9 ниже),
  лексически и с сохранением приоритета «ложный пропуск безопаснее ложной склейки»;
- уже записанный стендовый дубль остаётся на карточке (см. п.7) — витрина f452a978 не «починена», и
  это честное состояние, а не забытая правка.

## T7.83a — уточнение гейта дублей по значению по замечанию приёмки: склейка только с опорным
утверждением того же показателя (коммит `ffdb90b`)

Замечание менеджера после приёмки T7.83 (`e17be99`): **значение не является показателем**. Гейт
склеивал перепроверкой пару «Инфляционные ожидания населения в декабре 2025 года составили 13,7%» и
«Средняя ключевая ставка Банка России за 2025 год составила 13,7%» — `claim_type`, множество
десятичных значений ({13,7}) и период совпали идеально. Улика об ожиданиях оказалась бы пристёгнута
к утверждению о ставке: лишняя поддерживающая улика чужому показателю и нечестный бейдж на витрине.

### 1. Что изменено (то же место в `_curator`, тот же механизм)

`apps/orchestrator/value_duplicate.py` (чистый модуль, без БД и LLM): у `ValueDuplicateClaim` и
`ValueDuplicateCandidate` добавлено поле `metric`; склейка требует двух новых условий — **сначала**
структурной законности (опора ответа), **затем** показателя:

1. **relied-фильтр.** Склеивается только кандидат, объявленный куратором в `relied_claim_ids`
   (T7.82/ADR-0032) — то есть тот, к которому модель вправе была бы приписать перепроверку явным
   `existing_claim_id` (T7.34). Хост не решает за модель, какой из видимых claim'ов она имела в виду;
   кандидат вне опоры остаётся нетронутым ни на байт. Счёт «ровно один кандидат» берётся ПОСЛЕ
   фильтра (`strict[0] if relied_strict else None`), иначе неопорный совпаденец создавал бы ложную
   неоднозначность и лишал оператора законной склейки; два и более **опорных** кандидата — как
   прежде, неоднозначность. Отказ: `action "kept"`, `target null`, причина называет
   `relied_claim_ids` (детерминированный текст, как у `claim_link_missing`).
2. **показатель.** `indicator_check` (:249): если метку объявляют обе стороны — точное совпадение после
   нормализации; иначе формулировки обязаны разделять словарь значимых слов
   (`statement_indicator_words`, :200) не слабее `INDICATOR_OVERLAP_MIN = Fraction(3, 5)` по основам
   (`INDICATOR_STEM_LEN = 5`), и при этом не называть непересекающихся территории из замкнутого
   `_TERRITORY_GROUPS`.

Метка показателя кандидата читается там, где хост реально видит scope строки знания: подзапрос
`claim_assessment_heads` → `claim_assessments.assessed_scope` текущей головы снапшота сессии
(`apps/orchestrator/orchestrator.py:2814–2820`, тот же источник, что у перепроверки T7.73). Колонки
`scope` на таблице `claims` нет (`packages/domain/models/memory.py::ORMClaim`), а host-scope-v1
(`packages/memory/scope.py`) метки показателя не несёт — поэтому на свежих стендовых claims решает
словарь формулировок. Ошибочный вариант (сканировать payload'ы `claim_assessment_recorded` по всей
истории) отвергнут: секвенс-скан без индекса по JSONB, плюс история оценок содержит устаревшие метки.

### 2. Правила словаря и порог (калибровка, а не движок правил)

Значимое слово = только буквы (`[^\W\d_]+`), длина ≥3, нижний регистр после нормализации типографики
живых страниц (NBSP U+00A0 и узкий U+202F → обычный пробел, софт-гифен U+00AD убирается, NFKC, ё→е —
та же ловушка, что у атрибуции T7.77). Отсекаются месяцы (`_MONTH_PREFIXES`) как маркеры периода,
служебные слова `_STOP_WORDS` **точным** сравнением (префиксным нельзя: «и» съело бы «инфляцию», «в» —
«вклады») и префиксные основы `_STOP_STEMS` по цельным корням служебных слов (кратчайшая — «год»,
она же снимает и «город…»: город показателем не является; предлоги для этого вынесены в точный
`_STOP_WORDS`): глаголы связки «значение = N» (составил*/составля*/составит*/достиг*/превысил*/
увелич*/снизил*/вырос*/упал*/изменил*/показал*/оценка*), маркеры периода (год*/месяц*/квартал*/
период*/итог*/данн*/датир*/число*/дата*) и отсылка к источнику как таковая (опубликов*/сообща*/
сообщил*/источник/согласно/показател*/значени*). Территории —
часть имени показателя и значимы осознанно. Основа слова = **префикс** фиксированной длины 5: переживает
падеж («инфляция/инфляции/инфляцией», «ставка/ставки») и не сводит разные показатели к одной основе;
суффикс в русском меняется сильнее начала, поэтому префикс. Отношение — |пересечение|/|объединение|
основ, сравнение точной дробью `fractions.Fraction` (никаких float).

Замеры на реальных формулировках фикстуры .92 и отрицательных примерах приёмки (`CALIBRATION` в
тесте, ADR-0033 §7): 3/5 — реальная пара `e189c80d…` ↔ `9266248e…`; 4/5 — перефраз наблюдаемой
инфляции; 1/1 — «ключевая ставка Банка России» против её же перефразы; 0 — ожидания 13,7% против
ставки 13,7%; 1/4 — безработица против ВВП (при общей территории «России»); 1/2 — «ставка по вкладам»
против «ключевая ставка»; 1/4 — инфляционные ожидания против инфляции того же месяца; 1/3 — Россия
против Казахстана и глобальная против российской (там же конфликт территорий); **3/5** — наблюдаемая
инфляция России против наблюдаемой инфляции Беларуси: на одном пороге это выглядело бы одним
показателем, поэтому территории обязательны. Обоснование величины 3/5: минимальное значение,
отделяющее реальные пары фикстуры (минимум 3/5) от ближайшего ложного кандидата (максимум 1/2); между
ними заведомо нет ни одной измеренной формы — дыра в 1/10 оставлена осознанно, и все спорные случаи
решаются отказом («kept»), потому что лишняя улика чужому утверждению дороже лишнего дубля.

### 3. Тесты (краснота на `e17be99` доказана)

Юнит `tests/unit/test_value_duplicate.py`: было 12, стало **29 функций / 39 собираемых проверок**
(+17 функций и +27 проверок за счёт параметризаций:
relied-фильтр во всех четырёх формах — склейка только с опорой, отказ при опоре вне списка (тот самый
13,7%-дефект), неоднозначность после фильтра, сохранение прежних веток `counters`/supports при опоре;
сверка меток scope и словарь формулировок + таблица `CALIBRATION` со всеми замерами выше; территории).
Сквозной `tests/scenario/test_value_duplicate_gate.py`: было 3, стало **5** (+2): идеальный дубль вне
`relied_claim_ids` (тот же тип/число/период, поддерживает первоисточник) — новый claim записан,
кандидат не тронут нигде (оценка, `stored_as_of`, набор улик сверяются построчно), событие
`claim_reverified` отсутствует, `claims_count` не изменился, `value_duplicates[0].action == "kept"` и
причина называет `relied_claim_ids`; и форма дефекта приёмки 13,7% ↔ 13,7% при объявленной опоре —
склейки нет.

Краснота до правки (файлы гейта временно возвращены на `e17be99`, потом восстановлены): два новых
сквозняка — `2 failed, 3 passed` (`assert len(rows) == 2 / assert 1 == 2`: прежний гейт склеил
ожидания со ставкой и оставил один claim); юнит-файл на `e17be99` краснеет отсутствующим импортом
новых имён модуля, а поведение прежнего гейта для тех же форм проверено временным пробником —
`6 failed` на шести формах (пробник удалён после замера): везде `action = reverified` там, где теперь
`kept` (ожидания/ставка при `relied=True` и `relied=False`,
безработица/ВВП, Россия/Казахстан, Россия/Беларусь, идеальный дубль вне опоры).

Ничего прежнего не ослаблено: 12 исходных юнит-проверок (в том числе «несколько кандидатов →
неоднозначность» и тексты причин) не изменены ни словом — в файле правился только его docstring;
формулировка причины неоднозначности при нулевой опоре называет обе половины, поэтому прежние
assert'ы `ambiguous value duplicate` и `relied_claim_ids` остаются зелёными. В сквозном файле
изменён только общий хелпер `_compare_curator`/`_run_compare_session`: добавлен необязательный
`relied_ids`, дефолт которого — прежнее значение списка; тела трёх прежних тестов не тронуты.
Тест байт-идентичности payload без дублей, ветка явного `existing_claim_id`, монотонность
union-оценки, запрет удаления знания — без изменений.

### 4. Проверки и что не тронуто

ruff чистый; mypy strict (`packages apps hostctl`) — «no issues found in 149 source files»;
`pytest -n auto -q -m "not timing"` — **1803 passed, 12 skipped** (+29 к baseline после T7.83(б):
1774 → 1803); `pytest -q -m timing` — **4 passed**. Честно о флэке: один из прогонов `-n auto` дал
единственную красноту вне новых тестов — `tests/scenario/test_relied_claims.py::test_relied_claims_land_in_existing_audit_event_and_on_the_card`;
одиночный прогон файла (3 passed) и два других полных прогона этого цикла — зелёные, повторить отказ
не удалось. Источник такой красноты уже разобран в AGENTS §7 (T7.55/T7.56: конкуренция воркеров за
общий PostgreSQL), к этой правке он отношения не имеет. Резидуум стоек: контейнеров `docker ps -a` 41,
тестовых scratch-БД (`noezema_mig*` / `noezema_tpl*`) 56, шаблонов и миграций среди имён контейнеров
0. Стоп-критерий задачи не задействован: миграций нет (`alembic heads` — единственный head
`0025_prompt_content_pin`, как и до правки), нового
`AuditEventType` нет (список закрыт), порогов/scale/rules engine не добавлено — константы модуля
фиксированы и закреплены тестом. Промпт `curator-v10` (`prompts/curator/curator-v10.md`, sha256
`8b940bf5…`) и payload'ы config-v1…v20 (canonical v20 `2b065470…`, v19 `4d76c000…`) не изменены: абзац
куратора «то же число того же показателя за тот же период … хост заметит дубль по значению и соединит
его честно» после T7.83a остаётся верным (хост соединяет теперь уже более узко — то же утверждение того
же показателя, объявленное опорой), а обязанность заполнить `existing_claim_id` сама по себе никуда не
делась; менять запиненный промпт ради этой формулировки решения нет.

### 5. Непроверенное и пределы T7.83a

- словарь лексилен: перефраз того же показателя другими словами (например «ставка денежного рынка»
  против «ключевая ставка Банка России») может получить отказ — лишний дубль на карточке, но не чужая
  улика; это осознанный выбор в сторону ложного пропуска;
- `_TERRITORY_GROUPS` замкнут (12 групп): страна вне списка даст лишь пропущенный конфликт территорий
  (эффект — возможная лишняя склейка по значению), молчаливой ложной склейки из-за неполноты нет;
  словарь не трогает пороги независимости (`MULTI_PART_SUFFIXES`/PSL, T7.75) — это другой контур;
- метка показателя есть не у каждого утверждения: host-scope-v1 её не несёт, значит на стенде решает
  словарь формулировок; замер живого поведения куратора под curator-v10 и живого же набора scope'ов
  на .92 — за менеджером (агент к .92 не обращался);
- поведение уже записанного дубля `e189c80d…` + `9266248e…` не меняется: задним числом никто ничего не
  переписывает (§3 ADR-0033).

## T7.84 — отказ куратора становится объяснимым, а проверка опорной даты — после определения вида операции (стенд .92, сессия `ed36f4a0`; ADR-0033 §8, ADR-0034)

Коммиты: `015aa4b` (часть а — дайджест отклонённого предложения в журнале), `fdf7d75` (часть б —
разделение гейта staging и порядок проверок). Часть в — только анализ (ADR-0034, **proposed**: решение
о пооперационном отказе за пользователем, кода нет). Документы — отдельным последним коммитом.

### 1. Что произошло на стенде (замер `ed36f4a0`, аналитически: агент к .92 не обращался)

Стенд 192.168.1.92, HEAD `4d2a236`, config-v20 (куратор curator-v10), движок halogen-flash-next
(192.168.1.141). Сессия `ed36f4a0-850c-482b-99a5-41f8d46166fe`, вопрос `65b54abd-7cb1-432b-83a3-74be5952b612`:
«Какова официальная годовая инфляция в России за 2025 год по данным Росстата и насколько она расходится
с наблюдаемой населением инфляцией по опросу инФОМ за декабрь 2025 года? …». Контекст-пак содержал три
отношения `claims_evidence`: `9266248e…` («Годовая инфляция в России по итогам 2025 года (декабрь 2025 к
декабрю 2024) составила 5,59% …», temporal_fact, as_of 2026-01-21, E4, 7 улик, бейдж «Проверено»),
`391c4388…` (наблюдаемая инфляция 14,5%, temporal_fact, as_of 2025-12-31, E1, одна улика) и дубль из
T7.83 `e189c80d…` (то же 5,59%, temporal_fact, as_of 2025-12-31, E1). Исследователь к этому моменту уже
назвал обе цифры в своём итоге (Росстат, публикация 16.01.2026; опрос инФОМ в обзоре ЦБ № 12 (108)).

Журнал сессии зафиксировал ровно одно отказное событие (`session_state_changed`) с payload
`{"curator_rejected": ["claim[0]: temporal_fact requires as_of", "claim[1]: temporal_fact requires as_of"]}`.
Карточка вопроса: пустой список утверждений и пустой список опор, состояние `partially_answered`
(подпись «ответ неполон»). **Сам ответ куратора не записан нигде**: ни statement'ов, ни типов, ни связей,
ни summary — то есть по журналу невозможно понять, что именно модель предлагала и была ли часть
предложения законной. Это дефект (а): отказ назван, но необъясним по содержанию.

### 2. Часть а (`015aa4b`): тот же ключ отказа + снимок отклонённого предложения

Новый чистый модуль `apps/orchestrator/rejected_proposal.py` (143 строки): `rejected_proposal_digest
(proposal)` возвращает читаемый снимок — `summary` (≤ `REJECTED_SUMMARY_CHARS = 500`),
`relied_claim_ids` (до `REJECTED_RELIED_LIMIT = 8`), счётчик `new_questions`, массив `claims`
(`claim_index`, statement ≤ `REJECTED_STATEMENT_CHARS = 300`, `claim_type`, `as_of` ISO или null,
`existing_claim_id`, `scope_keys` — только ключи, значения scope в журнал не кладутся) и массив
`evidence_links` (`claim_index`, `evidence_index`, `relation`). потолок всего ключа —
`REJECTED_PROPOSAL_MAX_CHARS = 4096` минус `_COUNTER_RESERVE = 64` на служебные счётчики; операции
добавляются целиком, при нехватке места запись **не обрезается посередине** (урок T7.76: половина
схемы — не схема), а срезанные позиции называются ключами `omitted_claims` / `omitted_evidence_links`
(появляются только когда они ненулевые). Текст модели перед обрезкой проходит `mask_nul` — NUL-маскировка
границы журнала (`AuditService.record`, `packages/domain/services/audit.py:48`) сохранена и не обойдена.

Ключ добавлен в payload **тех же существующих событий** отказа после получения предложения (нового
`AuditEventType` нет, прежние ключи не изменены): структурный гейт — `apps/orchestrator/orchestrator.py:2686`,
неразрешимая ссылка перепроверки — `2721`, «у якоря нет головы в снапшоте сессии» — `2756`, проверка
опорной даты (появилась в части б) — `2908`, предпроверка движком правил — `2946` (там же, где есть,
ключ `value_duplicates`). Три отказа ДО предложения (`2582` обрезанный лимитом ответ, `2603`
request_rejected, `2629` недоступная/схемно неверная модель) дайджеста не содержат: предложения нет.

### 3. Часть б (`fdf7d75`): порядок проверок до и после

Было (одна функция на всё): `CuratorProposal.validate_against` проверял индексы уликовых связей,
требовал `as_of` у каждой `temporal_fact`, пустые/длинные `search_statements`, дубли `dependencies` и бюджет
новых вопросов — одним обходом, до разрешения перепроверок (`apps/orchestrator/orchestrator.py:2675`
вызывал именно её).

Стало (граница куратора, файл:строки на HEAD после правки):

1. `orchestrator.py:2675` — `proposal.structural_problems(len(ctx.evidence), questions_max=…)`
   (`packages/domain/schemas/staging.py:123`): индексы связей, бюджеты вопросов, `search_statements`,
   дубли `dependencies`. Эти проверки безразличны к виду операции и остаются первыми.
2. `2692–2728` — разрешение `existing_claim_id` (ADR-0018); `2747–2763` — «у якоря нет головы»; там же
   подмена типа операции типом якоря (T7.34).
3. `2775–2795` — разрешение `relied_claim_ids` (ADR-0032); `2806–2886` — гейт дублей по значению
   (ADR-0033/T7.83a), чьи решения подменяют операции в `staged_claims`.
4. `2898–2913` — `proposal.temporal_as_of_problems(reverify_indices)` (`staging.py:134`), где
   `reverify_indices` — индексы операций, которые хост **уже** ведёт как перепроверку
   (`staged_claims[i].existing_claim_id is not None`). Отказ — то же событие с прежним ключом
   `curator_rejected`, плюс `rejected_proposal` и, если гейт что-то решил, `value_duplicates`.
5. `2915–2953` — предпроверка движком правил (`curator_rejected_by_rules`).

`validate_against` сохранён и обязан отвечать ровно те же строки в том же порядке: внутри — общий обход
`_walk(evidence_count, questions_max, *, check_as_of)` (`staging.py:169`), общий предикат
`_needs_reference_date` (`staging.py:152`); порядок причин и их тексты не разъехались — равенство закреплено посимвольно тестом
`tests/unit/test_staging_check_order.py::test_validate_against_messages_and_their_interleaved_order_are_byte_identical`,
а прежние тесты (`tests/unit/test_staging_schema.py`) не изменены ни словом. Семантика T7.83a не тронута:
фильтр по `relied_claim_ids`, сверка показателя, «ровно один строгий кандидат после relied-фильтра»,
all-supports; операции с явным `existing_claim_id` гейт не трогает; `claims_for_validation` (то, что
проверяет движок правил) от переупорядочивания не изменился — конверсия гейта туда не попадает.

Политика отказов не изменена: операция, оставшаяся **новой** `temporal_fact` без даты, по-прежнему
отклоняет всё предложение (`return 0, 0`, staging пуст) с тем же текстом причины — но теперь с дайджестом
и с решениями гейта рядом.

### 4. Поведение гейта дублей при отсутствующей дате (закреплено тестами)

`apps/orchestrator/value_duplicate.py::_period(statement, as_of_year)` (строки 375–381) с `as_of=None`
не падает: период строится только из лет формулировки. Если таких лет нет — период пуст, а пустой период
никогда не «покрывается» кандидатом (`if new_period and new_period <= _period(...)`), поэтому операция
остаётся предложенной с честной причиной («the period [] is not covered»). Кандидат без даты сопоставляется
по годам из своего statement. Ничего не склеивается молча — поведение консервативное, закреплено
`tests/unit/test_value_duplicate_dateless.py` (6 проверок).

### 5. Реальная форма стенда: два варианта опорного списка (сквозные тесты на fake-LLM)

Форма `ed36f4a0` воспроизведена в `tests/scenario/test_dateless_curator_reverify_order.py`: тот же вопрос
(его лексика называет оба утверждения — иначе они не попали бы в пакет, T7.73), вопрос без собственной
даты, два датless-пересказа уже записанных значений (5,59% и 14,5%), связи только `supports`,
`existing_claim_id` моделью не заполнен.

- **опора объявлена на один из двух одинаковых по значению claims** (`relied = {9266248e…}`): строгий
  кандидат после relied-фильтра ровно один, показатель совпадает → склейка перепроверкой; хост сохраняет
  якорную дату (`as_of` якоря 2026-01-21 остаётся), оценка не понижается, `claim_reverified` в журнале
  есть, новой строки claim'а нет. Тот же вывод из T7.83a: **тот жеClaim в пакете, не объявленный опорой,
  неоднозначности не создаёт** — склейка разрешена с объявленной опорой.
- **опора объявлена на оба claims 5,59%** (`relied = {9266248e…, e189c80d…}`): после relied-фильтра
  строгих кандидатов два → хост не выбирает «кто настоящая инфляция» (эвристики нет) → операция остаётся
  новой, даты у неё нет → отказ всего предложения. В журнале: прежняя причина по `claim[0]`, дайджест
  обеих операций и обе записи гейта дублей (неоднозначность для одной операции и законная склейка для
  другой — её отменил отказ, и это видно). Staging пуст, ни нового claim'а, ни перепроверки: карточка
  остаётся «ответ неполон».

Оба варианта закреплены тестами; ничего не «додумано» эвристиками: обе развилки записаны как есть.

### 6. Что видит человек после правки

- Успешная половина: утверждение на карточке одно (значение не дублируется), бейдж и оценка якоря не
  ниже прежних, в ленте — `claim_reverified`, связка «использовано из знаний» (T7.82(б)) на месте.
- Отказ: в ленте сессии вместо двух строк причин теперь тот же отказ **плюс** то, что модель предлагала
  (утверждения целиком в пределах 300 знаков, типы, даты — null если их нет, связи «утверждение ↔ улика»,
  число новых вопросов, summary, объявленные опоры) и то, что уже решил гейт дублей. JSON-API: только
  добавился ключ `rejected_proposal` в payload существующего события; enum, подписи словаря
  (`apps/web/labels.py`) и тест полноты подписей не менялись — новых строк отказа не вводилось.

### 7. Тесты

Новые файлы (29 проверок): `tests/unit/test_curator_rejection_digest.py` (8),
`tests/scenario/test_curator_rejection_journal.py` (3), `tests/unit/test_staging_check_order.py` (7),
`tests/unit/test_value_duplicate_dateless.py` (6), `tests/scenario/test_dateless_curator_reverify_order.py` (5).

Краснота до правок. Все пять файлов прогнаны на дереве `4d2a236` (модуль дайджеста удалён, гейт
возвращён к прежней версии; после замера файлы восстановлены из HEAD, `git diff HEAD -- packages apps tests`
пуст): **13 failed, 8 passed, 1 ошибка сбора** (`ModuleNotFoundError: apps.orchestrator.rejected_proposal`).
Отказались зелёными восемь проверок по замыслу: байт-идентичность строк и порядка `validate_against`,
шесть проверок консервативного поведения гейта при отсутствующей дате (они фиксируют то, что уже было)
и сквозной тест «temporal_fact со своей датой переупорядочивания не заметил» — это замки неизменности,
а не новые требования. Причина красноты по файлам: в журнале — `KeyError: 'rejected_proposal'` (3
сквозняка и 4 сценарных проверки порядка), в юнитах порядка — отсутствующие методы
`structural_problems` / `temporal_as_of_problems`, в сценариях порядка — отказ там, где должна была быть
перепроверка. Отдельно зафиксирована краснота файлов части б на дереве сразу после части а (`015aa4b`):
**9 failed, 9 passed** — то есть часть б действительно краснеет именно из-за порядка проверок, а не из-за
отсутствия дайджеста. Ослаблено ничего: прежние тесты (staging-схема, гейт дублей, commit boundary,
перепроверка, карточка) не изменены ни словом; количество тестов выросло 1803 → 1832.

### 8. Часть в: пооперационный отказ вместо отказа всем предложением — только анализ (ADR-0034)

В `docs/adr/0034-per-operation-vs-whole-proposal-curator-rejection.md` (**proposed**, кода нет):
(1) правило «отклоняется всё предложение» нигде прямо не записано как требование — оно живёт в коде
границы куратора (`return 0, 0` до записи staging), в ADR-0018 (там же названа **другая** дисциплина на
границе коммита: отказ именно этой операции с `None`-плейсхолдерами вместо индексов) и в контр-прецедентах
ADR-0032 §3 и ADR-0033 §2, где отказ пооперационный; (2) разобрано, какие проверки по природе
пооперационные (`as_of`, разрешение `existing_claim_id`, движок правил — цикл по claims, `search_statements`),
а какие обязаны остаться шире предложения (бюджет `new_questions`, диапазоны `claim_index`, связи
зависимостей); (3) разобрано, что происходит с уликами, вопросами и опорой при выбросе операции: улики и
источники появляются до куратора и от выбора не зависят, связь к выброшенной операции граница коммита уже
отвергает названной причиной (`packages/memory/service.py:865–873`), новые вопросы выживают как отдельные
staging-операции, `relied_claim_ids` — payload существующего события, а не операция; расходятся «предложено»
(`orchestrator.py:3029`) и «применено» (T7.79 считает применённые операции); (4) варианты A (оставить как
есть + измерять частоту потерь по `rejected_proposal`), B (пооперационный отказ на границе куратора с
новым ключом вида `rejected_operations`) и C (возврат отказа модели на одну правку — требует curator-v11 и
config-v21, то есть выхода за границы этой задачи); (5) что именно решать пользователю. Рекомендация агента:
A плюс замер частоты; B — только если замер покажет систематические потери законных перепроверок.

### 9. Проверки и что не тронуто

ruff чистый; mypy strict (`packages apps hostctl`) — «no issues found in 150 source files»;
`pytest -n auto -q -m "not timing"` — **1832 passed, 12 skipped**; отдельный прогон `pytest -q -m timing` —
**4 passed**. Перед коммитом части а было 1814 passed / 12 skipped (прирост 11), перед частью б — 1832 / 12
(прирост ещё 18). Стоп-критерии не задействованы: миграций нет (`alembic heads` — единственный head
`0025_prompt_content_pin`, как и до правки), схема БД и `AuditEventType` не менялись, порогов, scale и
правил evaluation engine не добавлено, `ARCHITECTURE.md` не тронут, детектор производности и группировка
источников, research proxy и реестр инструментов не тронуты; промпты и payload'ы config-v1…v20 не изменены
(гейт работает при любой конфигурации), схема `CuratorProposal` не менялась — пины curator-v8/v9/v10
остаются валидными, поэтому остановки из-за пина не потребовалось. API: только аддитивный ключ в payload
существующего события. Знание не удаляется и не переписывается задним числом.

Резидуум окружения после всех прогонов: контейнеров `docker ps -a` — 41 (как до работы), из них с именами
миграций/шаблонов — 0; тестовых scratch-БД и шаблонов (`noezema_mig*` / `noezema_tpl*`) — 56, что совпадает
с значением, зафиксированным в разделе T7.83a. Флэков этой правкой не найдено; известное исключение —
исторический flake `tests/scenario/test_relied_claims.py::test_relied_claims_land_in_existing_audit_event_and_on_the_card`
и фикстура `fake_llm._free_port` (AGENTS §7), в прогонах T7.84 они не всплывали.

### 10. Непроверенное и пределы

- Живое поведение куратора на .92 после активации HEAD с частями а и б — за менеджером: правки
  конфигурационно независимы, менять config-v20 для них не нужно. Ожидание: датless-пересказ значения,
  объявленный опорой, перепроверяется вместо отказа; неоднозначный вариант по-прежнему даёт отказ — но
  теперь разбором объяснимый. Частоту этих двух случаев можно считать только по журналу (ключ
  `rejected_proposal`), то есть после активации.
- Развилка «опора объявлена / не объявлена» для живого curator-v10 не измерена: куратор обязан заполнять
  `existing_claim_id` сам (curator-v10, правило 7), и как часто он этого не делает при законной дубли-форме
  — вопрос замера на стенде.
- Часть в не реализована по решению задачи: это анализ для решения пользователя (ADR-0034, proposed).
  Пока оно не принято, дисциплина границы куратора остаётся прежней — отказ всем предложением.

## T7.85 — пересказ узнаётся по конкретному значению в живых страницах, прежние решения пересматриваются, дубль той же формулировки склеивается честно (стенд .92; ADR-0029 дополнение, ADR-0033 §9)

Задача поставлена так: сначала разбор живых текстов **прежним** детектором, потом правка, потом
переатрибуция как инструмент менеджера. Стенд `.92` (192.168.1.141) не тронут: ни одного вызова стенда,
LLM-узлов и внешних сетей; разбор — по копиям нормализованных артефактов базы `noezema-dev`, которые лежат
в `tests/fixtures/t785/` и проверены по `sha256(normalized)` строк источников.

### 1. Замер до правки: как решал детектор на `4698efc`

Страничный метод был `host-source-attribution-v2`, поуровневый — `host-value-attribution-v1`. Таблица
построена прогоном прежнего кода по тем же фикстурам (отдельный worktree на `4698efc`; значения взяты из
формулировок стендовых утверждений):

| страница (sha префикс) | 5,59 % | 14,5 % | 13,1 / 15,6 % | 6,3 % | страница целиком |
|---|---|---|---|---|---|
| `cbr_CPD_2025-12` `218e9a1e` | no_value_attribution | — | — | — | no_value_attribution |
| `cbr_Infl_exp_25-12` `7e2eb441` | — | value_not_attributed | — | self_primary | self_primary |
| `cbr_reginfl_64837` `a25e3cf8` | value_not_attributed | — | — | — | derivative → Росстат |
| `expert_5-59` `7fc08195` | **value_not_attributed** | — | — | — | derivative → Росстат |
| `forbes_552221` `f98ef136` | — | **no_value_attribution** | no_value_attribution | no_value_attribution | no_value_attribution |
| `garant_1963171` `09d8f784` | no_value_attribution | — | — | — | no_value_attribution |
| `interfax_1068021_a` `adf05eb5` | derivative → Росстат | — | — | — | derivative → Росстат |
| `interfax_1068021_b` `0f96906a` | derivative → Росстат | — | — | — | derivative → Росстат |
| `kommersant_8294535` `20c19e0a` | — | **no_value_attribution** | no_value_attribution | — | no_value_attribution |
| `nbj_71790` `7b5c7f14` | no_value_attribution (своя оценка) | — | — | — | no_value_attribution |
| `ria_2068393962` `414c0a44` | **no_value_attribution** | — | — | — | no_value_attribution |
| `sbercib_2025` `8be76229` | derivative → Росстат | value_not_attributed | — | — | ambiguous_primaries |

Пропуски, которые эта задача закрыла (жирным):

- **forbes.ru и kommersant.ru для 14,5 %** — главная дыра. На forbes первоисточник назван в предыдущем
  предложении того же блока: «…вырос до 13,7%, следует из опроса «инФОМ», опубликованного ЦБ. Оценка
  наблюдаемой населением годовой инфляции в декабре 2025 года составила 14,5%.» (смещение 652 в
  нормализованном тексте). Строгое правило требует шаблон + измеренное число + алиас **в одном фрагменте**,
  поэтому решение было «нет атрибуции», хотя блок явно пересказывает публикацию ЦБ. На kommersant та же
  форма: «составила 6,3%, следует из опубликованных данных опроса, проводимого «инФОМ» по заказу Банка
  России».
- **expert.ru и ria.ru для 5,59 %** — форма придаточного изложения («замедлилась до 5,59%, следует из
  данных Росстата») не была в страничном словаре, а склейка блоков оставляла двойной пробел
  («следует из␠␠данных»), который шаблон с одним ASCII-пробелом не матчил. Страничный указатель на expert.ru
  ставился, на ria.ru — нет (там не сработал даже страничный шаблон), а записей о происхождении **значения**
  не было ни там, ни там: именно из-за этого `391c4388…` держало E3/0.75 как «независимое подтверждение»
  двух пересказов одного и того же опроса.

Ошибочных склеек прежний детектор не делал: все пропуски этого замера — ложные пропуски, а не ложные
родители. Направление правки из этого следует прямо: догонять распознавание, не ослабляя отказ.

### 2. Разбор Банка России и «инФОМ» (решение, зафиксированное тестами)

- **«инФОМ» в реестр первоисточников не добавлен.** Опрос назван в текстах как заказанный и опубликованный
  Банком России: страница ЦБ `Infl_exp_25-12` — «Источники: ООО «инФОМ»… Источник: Банк России»,
  kommersant.ru — «данных опроса, проводимого «инФОМ» по заказу Банка России». Регистрация инФОМ как
  первоисточника разрезала бы одно исследование на две группы независимости: собственная страница ЦБ
  осталась бы `self_primary`, а forbes и kommersant связывались бы с инФОМ — и утверждение про 14,5 %
  получило бы «независимое подтверждение» там, где источник один. Поэтому первоисточник этих значений —
  Банк России, а его пересказы связываются с его якорем. Текст, который называет только «инФОМ» и не
  называет ЦБ, остаётся честным отказом (так и решён kommersant для 13,1/15,6 %).
- **Страница без имени первоисточника в том же фрагменте остаётся честным отказом**, даже если она
  принадлежит ведомству: страница доклада `cbr_CPD_2025-12` («годовая инфляция в декабре снизилась до
  5,59 %») Росстат не называет ни разу — для 5,59 % решения нет, указателя нет.
- **«регулятор» в словарь не добавлен**: у слова нет развёрнутого референта в тексте, любое решение по нему
  было бы догадкой.
- **Своя оценка — veto, и он работает:** `nbj_71790` («по нашим расчётам») и 14,5 % на sbercib.ru (значение
  стоит рядом с собственной оценкой редакции) пересказами не стали; оконный режим поверх отказа собственного
  вида не включается — закреплено тестом
  `test_publication_window_never_overrides_a_refusal_of_its_own_kind`.

### 3. Правка детектора и почему это не ослабление

- Словарь страничных шаблонов: добавлены «следует из», «сообщил*», «сообщени*»
  (`apps/research_proxy/source_attribution.py:192–194`), у алиаса Банка России появился вариант «цб» (:137);
  метод — `host-source-attribution-v3` (:43). Голое упоминание по-прежнему не решение: нужна пара
  «измеренное число + шаблон + алиас в окне 120 знаков в одном фрагменте».
- Склейка блоков нормализует повторные пробелы (:347) — то же правило типографики, что в T7.77.
- Поуровневый метод `host-value-attribution-v2` (`apps/research_proxy/value_attribution.py:71`) получил
  второй режим сопряжения — **окно публикации значения** (:120–140, :209, :224): окно расширяется назад до
  400 знаков (не дальше границы блока) и вперёд на 40; режим разрешён **только после честного строгого
  отказа** (`fallback` :323), никогда поверх `own_assessment` и `self_primary`; требует маркера публикации
  первоисточника (`PUBLICATION_MARKERS`) и ровно одного названного первоисточника реестра в окне.
  Атрибутированное измерение в окне может относиться к другому числу — на forbes это 13,7 % рядом с
  «опубликованного ЦБ»; решение формулируется как пересказ публикации, а не как «цифра совпала».
- Консервативность подтверждается тем, что правка местами **отказалась решать там, где раньше решала**:
  страница `interfax_1068021_a` была `derivative → Росстат`, а стала `ambiguous_primaries` — новый шаблон
  «сообщил» увидел в хронологическом блоке страницы («Росстат сообщил, что инфляция… составила 1,26 %»)
  соседство с «Банк России» из служебного меню, то есть два разных первоисточника в одном склеенном
  фрагменте. По значению 5,59 % решение осталось (`derivative → Росстат`, строгий режим), страничный
  указатель на такую страницу хост больше не ставит. Ложный пропуск безопаснее ложной склейки (AGENTS §7,
  T7.75) — это закреплено в таблице теста, а не сглажено.
- Отрицательные проверки закреплены тестами: страница самого Банка России не бывает пересказом самой себя
  (`self_primary`); «по данным Росстата» у другой цифры (garant.ru, 6,1 % за месяц) — не атрибуция;
  «Росстат уточнил потребительскую корзину» без измеренного числа — отказ.

### 4. Переатрибуция прежнего знания: пересмотр прежних записей

`research-reattribute --evidence-level` (`apps/research_proxy/reattribution.py`) отбирает улики двух видов:
без записи происхождения значения (как прежде) и с записью, помеченной **прежней версией метода**
(`CURRENT_VALUE_METHOD_VERSION` :57). Что закреплено:

- коррекция оператора неприкосновенна в обоих режимах: `_operator_corrected_source_ids` (:143) читает
  действующие строки `source_graph_corrections` с актором `operator:*`, apply-петля дополнительно проверяет
  это под `FOR UPDATE` (:802–830). На стенде это пара nbj `b29f0226…` → sbercib `92c2671e…` (утверждение
  `c970bc08…`, ADR-0031);
- если свежий метод отказался решать, а запись прежней версии есть — она **сохраняется** с явной строкой
  «прежняя запись сохранена: свежий метод отказался решать — проверьте вручную» (:669). Тихой отмены
  записанного решения нет;
- существующий указатель не размножается: `_parent_for_decision` (:735) переиспользует записанного родителя,
  пока первоисточник тот же (на стенде — якорь Росстата `33912509…`, к которому уже привязан
  `cbr_reginfl_64837`);
- второй прогон идемпотентен: «изменений нет: решение уже записано ранее», 0 изменений, 0 задач переоценки;
- каскад прежний: `apply_source_graph_change` + рабочий переоценки, прямых правок оценок нет.

Формат плана для улики (строка «было» несёт номер прежнего метода):

```text
утверждение 391c4388 · https://forbes.example/552221-ozhidaniya
    утверждение: Наблюдаемая населением годовая инфляция … составила 14,5%.
    было: derivative → Росстат (host-value-attribution-v1)  стало: derivative → Банк России  [будет записано другое происхождение значения улики]
```

Для `391c4388…` на стенде «было» — «записи о происхождении значения нет»: улики этого утверждения решения не
имели вообще. Мёртвый хелпер `_undecided_evidence()` удалён: после перехода на пересмотр прежних записей
вызовов не осталось; это чистка кода, знание из журнала и БД не удалялось.

### 5. Ожидаемый план на стенде .92 (вычислен по фикстурам; стенд не тронут)

| источник стенда | что изменится |
|---|---|
| expert.ru `d5f79fa0`, ria.ru, interfax `aeca4320…`/`db0e58ba…` и `477f191e…`/`129d89cd…`, sbercib `92c2671e…` | улика 5,59 % получает `derivative → Росстат` (строгий режим) и родителя — существующий якорь Росстата `33912509…` |
| forbes `d4a936ff…`/`6f601bb5…`, kommersant `386de6c8…` | улика 14,5 % получает `derivative → Банк России` (окно публикации) и общий якорь ЦБ |
| cbr `Infl_exp_25-12` `a54812c2`, cbr `CPD_2025-12` `8114234a` | решения нет: `self_primary` и «Росстат не назван» — самостоятельные страницы |
| garant.ru `2b7500fb…`/`bbcfebd5…`, nbj.ru `7b5c7f14…` | решения нет (нет атрибуции этого значения; своя оценка) — их группы независимости сохраняются |
| cbr `reginfl ?id=64837` `775940b7` | уже привязан к якорю Росстата: указатель сохранён, второй якорь не создаётся |
| nbj `b29f0226…` → sbercib `92c2671e…` и утверждение `c970bc08…` | под коррекцией оператора — «не трогаем» в обоих режимах |

Ожидаемые оценки (арифметика правил из payload'а: `min_grade_for_supported = E3`, повышение на одну ступень
только при ≥ 2 × `min_independence_groups`, confidence умножается на `groups / min_independence_groups` —
`packages/memory/rules_engine.py:210–252`):

- **`391c4388-c0dc-461b-a843-23d4d6bfcd17`** (наблюдаемая инфляция 14,5 %, temporal_fact): было E3/0.75
  supported за две «независимые» группы forbes + kommersant. Обе улики становятся пересказами публикации ЦБ и
  сливаются в одну группу со страницей самого ЦБ (тот же registrable domain `cbr.ru`). **Станет E1/0.15
  hypothesis, причина `insufficient_independence`.** Тот же переход закреплён тестом
  `test_primary_plus_two_retellings_is_not_graded_above_one_group`: «первоисточник плюс два его пересказа» не
  оценивается выше одной независимой группы.
- **`9266248e-2433-4a12-a3f7-b51ff10ad803`** (официальная инфляция 2025 = 5,59 %): было E4/0.95 supported
  (≥ 4 групп). После склейки пересказов с якорем Росстата (к нему уже привязан `cbr_reginfl…`, а страница
  доклада ЦБ попадает в тот же компонент по домену `cbr.ru`) остаются три группы: кластер «Росстат + ЦБ +
  пересказы», garant.ru и nbj.ru. **Станет E3/0.75 supported** (3 ≥ 2 → поддерживается, 3 < 4 → повышения
  нет). Понижение, никогда не повышение.
- `6d9a12ff…` (медианные оценки 13,1/15,6 %) и `e189c80d…` (дубль 5,59 % из T7.83) решением не меняются:
  значения названы в блоках без атрибуции первоисточника — честный отказ остаётся честным отказом.

Точное число групп на стенде подтверждает прогон менеджера: `--dry-run` перечисляет каждую улику с
«было → станет» и итоговую строку (сколько доказательств получит решение, сколько утверждений зацепит
каскад, какие головы снимаются). Направление и арифметика выше закреплены тестами; конкретные номера групп —
из сухого прогона, а не из догадки.

### 6. Дубль той же формулировки при другом типе (`2d010d53…` против `58e2d5d4…`)

На стенде одно и то же предложение записано дважды: `external_fact` «Прогноз аналитиков … по данным
декабрьского макроэкономического опроса Банка России составил 6,3%.»
(`2d010d53-0e98-4a48-bc47-98cd152f6ca5`) и побайтово identical `temporal_fact`
(`58e2d5d4-fed4-443f-aba3-b5835a61f638`, E1/0.15). Побайтовый дедуп T7.9 требует того же типа, числовой гейт
ADR-0033 — тоже; оставаясь «новой» `temporal_fact` без даты, вторая операция падала на предпроверку дат
T7.84 (замер `ed36f4a0`).

Хост решает раньше (`apps/orchestrator/value_duplicate.py::find_identical_statement_duplicates` :429):
операция без `existing_claim_id`, чья формулировка побайтово совпадает ровно с одним утверждением
контекст-пака, но тип другой, — это то же утверждение; она конвертируется в его перепроверку и валидируется
и оценивается **по типу якоря** (`apps/orchestrator/orchestrator.py:2888–2910`), поэтому отсутствующая `as_of`
законна (ADR-0018: дату сохраняет якорь). В существующий ключ `value_duplicates` пишется причина,
начинающаяся с `same statement, type relabelled temporal_fact → external_fact`.

Фильтр «кандидат обязан быть объявлен в `relied_claim_ids`» (T7.83a) применён не был и не нужен: при
побайтовом равенстве формулировки показатель, период и значения совпадают по построению, и спор о том, какое
утверждение имела в виду модель, исчезает — закреплено тестом `test_reliance_is_not_required_for_a_byte_identical_statement`
(в сценарии опора не объявлена вовсе). Границы сохранены: побайтовое совпадение **того же** типа оставлено
дедупу T7.9; два кандидата с одним текстом — честный отказ (`kept`, оба id перечислены в причине);
улика-опровержение не превращается в перепроверку чужого утверждения; если куратор сам указал
`existing_claim_id`, хост не вмешивается.

### 7. Тесты и проверки

Новые тесты (50): `tests/unit/test_attribution_fixtures_t785.py` — закреплённая таблица решений по 12 копиям
страниц стенда, каждая фикстура проверяется своим `sha256`; `tests/unit/test_value_duplicate_relabel.py` (12);
`tests/scenario/test_reattribute_stale_records.py` (4: пересмотр записи эпохи v1 с сохранением якоря,
«первоисточник плюс два пересказа», коррекция оператора, отказ свежего метода сохраняет прежнюю запись);
`tests/scenario/test_value_duplicate_relabel_session.py` (3: релейбел в живой сессии без `as_of` и без
`existing_claim_id`, явный `existing_claim_id` не трогается, та же формулировка как опровержение не
склеивается). Обновлены пины метода в 6 существующих файлах (`tests/unit/test_source_attribution.py`,
`tests/unit/test_value_attribution.py`, `tests/scenario/test_derivative_attribution.py`,
`tests/scenario/test_research_reattribute.py`, `tests/scenario/test_value_attribution_levels.py`,
`tests/scenario/test_value_attribution_session.py`) — изменены только ожидаемые номера версий, ни одна проверка
не ослаблена и ни один тест не удалён.

Краснота новых тестов на `4698efc` доказана временным откатом изменённых файлов (`git stash push -- apps
packages hostctl tests/…`, новые файлы остаются в рабочем дереве): unit-файлы — ImportError
(`PAIRING_PUBLICATION_WINDOW`, `find_identical_statement_duplicates` отсутствуют), сценарий переатрибуции —
4 failed (нет `CURRENT_VALUE_METHOD_VERSION`: режима пересмотра прежних записей не существует), сценарий
релейбела — 2 failed (`staging` не получил `existing_claim_id`, ключ `value_duplicates` пуст). После
восстановления те же 50 тестов зелёные.

Полная проверка перед коммитом кода: ruff чистый; mypy strict (`packages apps hostctl`) — «no issues found in
150 source files»; образ `noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1882 passed,
12 skipped** (baseline 1832 + 50 новых); отдельный прогон `pytest -q -m timing` — **4 passed**. Флэков не
наблюдалось. Стоп-критерии не задействованы: миграций нет (`alembic heads` — единственный head
`0025_prompt_content_pin`, как и до правки), `AuditEventType`, пороги, шкала grade → confidence, evaluation
engine и `group_sources` не менялись; `ARCHITECTURE.md` не тронут; fetch/TLS/лимиты research proxy и реестр
инструментов не тронуты; промпты и payload'ы config-v1…config-v20 не изменены и новых не создавалось (версии
методов живут в коде детекторов — пинать их в конфиг не потребовалось); дедуп T7.9, гейт T7.83a и порядок
проверок T7.84 сохранены. API — только аддитивные поля внутри существующих записей и ключей. Знание не
удаляется: переатрибуция либо добавляет запись, либо сохраняет прежнюю.

Резидуум окружения: контейнеров `docker ps -a` — 41 (как до работы), тестовых scratch-БД и шаблонов — 56
(`noezema_mig*` / `noezema_tpl*`), что совпадает со значениями разделов T7.83a и T7.84.

### 8. Непроверенное и пределы

- Живой прогон на `.92` — за менеджером: ожидания п.5 вычислены по фикстурам и арифметике правил, но
  фактическое число групп подтверждает только сухой прогон (и он обязателен перед боевым).
- Окно публикации — расширение распознавания, полученное ценой риска: оно решает там, где первоисточник назван
  не у самого числа. Защита — три обязательных условия (маркер публикации, ровно один названный
  первоисточник реестра, запрет включаться поверх отказа собственного вида) плюс то, что `pairing` записан в
  улике: человек видит, каким режимом получено решение, и может спорить именно с ним.
- Страничный отказ из-за служебных блоков (interfax-форма) — осознанная цена: хост теряет правильный
  страничный указатель на страницу с хронологическим меню. Лечится не расширением окна, а отдельной задачей о
 разделении контента и обвязки страницы; сейчас выбор в сторону отказа.
- Текст основания улики долговременно не хранится, поэтому переатрибуция решает по нормализованному тексту
  всей страницы: veto собственной оценки действует по всему этому тексту (консервативнее, чем по одному
  фрагменту нового знания). Вариант C ADR-0029 (обнуление ошибочных `parent_source_id`) не реализован и
  требует явного решения пользователя.

## T7.85a — окно публикации не видит смены источника внутри окна: вето «другого источника числа» (замечание приёмки T7.85; ADR-0029 дополнение)

### 1. Дефект, найденный на приёмке

Проверка менеджера после T7.85 (`git checkout` на `792d2c4`, репродюсер
`/home/denis/dsh1/scratch_t785a/repro.py`, вызовы `attribute_value_in_fragment` с
`canonical_uri="https://news.example.ru/a"`): режим «окно публикации» проверял маркер публикации, наличие
атрибуции и число названных первоисточников в окне, но не проверял, не появился ли МЕЖДУ фразой атрибуции и
числом другой источник этого числа. Все три примера на `792d2c4` решались как `derivative`,
`pairing=publication_window`:

1. «По данным опубликованного Росстатом отчёта, инфляция … 5,59%. Аналитики Сбербанка ожидают инфляцию 6,3%
   в 2026 году.» → `derivative → Росстат` (чужой прогноз приписан Росстату);
2. «Согласно опубликованному Банком России опросу, ожидания выросли. Экономисты Райффайзенбанка оценивают
   реальную инфляцию в 14,5%.» → `derivative → Банк России`;
3. «Как следует из опубликованного Банком России опроса … Исследование Ромир показало наблюдаемую инфляцию
   14,5%.» → `derivative → Банк России` (чужое исследование приписано публикации ЦБ).

### 2. Принятое решение: вето только внутри оконного режима

В `_publication_decision` (`apps/research_proxy/value_attribution.py:427`) добавлено вето «другого источника
числа». Для каждого кандидата зона проверки начинается от конца КАЖДОЙ фразы атрибуции, закончившейся до
числа (`_attribution_regions` :359 — те же гейты атрибуции, что у `_attributed_keys`, но с сохранением
координат; зоны строит `_veto_zones` :403), и тянется до конца предложения, где стоит число. Внутри зоны
запрещены признаки (`_other_source_sign` :333): действующие лица-оценщики, глагольные и оборотные формы
оценки/прогноза, названия в кавычках вне словаря первоисточников, «опрос/исследование» с чужим заказчиком.
Перед проверкой зона очищается от алиасов словаря (`_mask_primary_aliases` :309): «Банк России» не считается
действующим лицом-банком (иначе банк-актор глушил бы собственный первоисточник), а «„Росстат“» в кавычках —
названием вне словаря. Если после вето кандидатов нет — честный отказ; если был ровно один и он снят —
отказ («сомнение — не приписывать»).

Порядок, закреплённый тестами: строгий режим остался первым и неизменным (`_strict_decision` не тронут —
guard-тесты с monkeypatch `_publication_decision → None` сверяют каждое строгое решение таблицы);
разночтение первоисточников (`named_primary_keys > 1` → `ambiguous_primaries`) вычисляется ДО вето по всем
найденным attribution-кандидатам, поэтому пример (5) остаётся ambiguous даже если один из кандидатов затронут
вето; вето умеет только снимать кандидатов и не может создать решение. Число названных первоисточников и
маркер публикации — прежние гейты окна (400/120/40 не тронуты). Номер метода поднят:
`host-value-attribution-v3` (:82); маркер схемы `VALUE_ATTRIBUTION_SCHEMA` по-прежнему не поднимается —
пересмотр прежних записей делает переатрибуция по метке метода (закреплено новым тестом
`test_records_tagged_v2_method_are_reconsidered_by_current_version`: записанные улики эпохи v2 пересматриваются,
второй прогон идемпотентен).

### 3. Списки признаков и различение имени показателя от чужой оценки

Списки — константы модуля с комментариями: `OTHER_SOURCE_ACTOR_MARKERS` (:168) — аналит*, экономист*,
эксперт*, специалист*, компани*, агентств*, институт*, центр*, банки без границы слова (ловит «Сбербанка»,
«Райффайзенбанка»; имена словаря уже замазаны); `OTHER_SOURCE_ESTIMATION_MARKERS` (:188) — оценив*/оценил*/
оценит, рассчит*/«по расчёт*», «по оценк*», «по мнени*», считают/полагают и конечные глагольные формы
ожидать (ожидаёт/ожидают/ожидали); `SURVEY_WITH_ANOTHER_CUSTOMER_MARKERS` (:211) — «исследование/опрос X»,
где X отсутствует в словаре (прямой порядок слов, см. пределы); кавычаемые названия `_QUOTED_NAME` (:219) —
«…» или "…", не совпадающие с алиасом словаря.

Существительное «оценка» маркером НЕ является: в kommersant-форме «Оценка текущих темпов роста цен при этом
осталась на уровне 14,5%» и в «Показатель ожидаемой инфляции…» это ИМЯ ПОКАЗАТЕЛЯ, и эти строки остаются
пересказами публикации ЦБ (тесты `test_kommersant_form_indicator_noun_stays_derivative`, строка фикстуры
forbes). Признаком чужой оценки служат только глагольные формы («оценивают», «оценили», «считают»,
«полагают», «ожидают») и обороты «по оценке», «по расчётам», «по мнению».

### 4. Было → стало (примеры приёмки; всё закреплено тестами)

| пример | на `792d2c4` | стало (v3) |
|---|---|---|
| (1) отчёт Росстата + «аналитики Сбербанка ожидают 6,3%» | `derivative → Росстат`, окно публикации | отказ `value_not_attributed`, строгий pairing |
| (2) опрос ЦБ + «экономисты Райффайзенбанка оценивают 14,5%» | `derivative → Банк России`, окно | отказ `no_value_attribution` |
| (3) опрос ЦБ + «исследование Ромир показало 14,5%» | `derivative → Банк России`, окно | отказ `value_not_attributed` |
| (4) «По нашим расчётам … 14,5%» | `own_assessment` | без изменений |
| (5) опрос Росстата + «аналитики ЦБ прогнозируют 6,3%» | `ambiguous_primaries`, окно | без изменений |
| (6) kommersant-форма: «…опроса, проводимого „инФОМ“ по заказу Банка России. Оценка … на уровне 14,5%.» | `derivative → Банк России`, окно | без изменений (имя показателя) |
| (7) «По оценке ВШЭ … 14,5%» | `derivative → Банк России`, окно | отказ `no_value_attribution` |
| (8) «Независимые экономисты считают … 14,5%» | `derivative → Банк России`, окно | отказ `value_not_attributed` |
| (9) «Компания „Ромир“ зафиксировала … 14,5%» | `derivative → Банк России`, окно | отказ `no_value_attribution` |
| (10) «Эксперты ЦМАКП рассчитали … 14,5%» | `derivative → Банк России`, окно | отказ `no_value_attribution` |
| (11) «По мнению аналитиков, точная оценка — 14,5%» после отчёта Росстата | `derivative → Росстат`, окно | отказ `no_value_attribution` |

Дополнительно закреплено, что вето НЕ является ложным: «ожидания выросли до 13,7%» внутри самой фразы
атрибуции — не другой источник (вeto начинается после её конца; тест
`test_expectations_inside_the_attribution_sentence_are_not_a_veto` остаётся `derivative → Банк России`), и
«Банк России сохранил оценку…» после фразы атрибуции — первоисточник, а не банк-актор
(`test_bank_of_russia_alias_is_not_an_actor_bank`).

### 5. Тесты и их краснота на `792d2c4`

- `tests/unit/test_publication_window_veto_t785a.py` (14): при откате одного модуля кода
  (`git checkout 792d2c4 -- apps/research_proxy/value_attribution.py`) — **9 failed, 5 passed** (3 дефекта,
  5 вариантов приёмки, пин версии метода);
- `tests/unit/test_value_attribution_guard_t785a.py` (30): тот же откат — **16 failed, 14 passed**; зелёными на
  прежнем коде остаются ровно те проверки, роль которых «не стало хуже»: неизменность всех строгих решений
  таблицы T7.85 и гейтов окна (400/40/120), сохранность словаря `PRIMARY_SOURCES` (инФОМ вне словаря); краснеют
  проверки новой механики (зоны, признаки, замазывание алиасов) — их на прежнем коде просто нет;
- новый сценарный тест переатрибуции (`tests/scenario/test_reattribute_stale_records.py`,
  `test_records_tagged_v2_method_are_reconsidered_by_current_version`) — **1 failed** (пин v3).
После восстановления модуля все зелёные. Таблица решений по 12 копиям живых страниц
(`tests/unit/test_attribution_fixtures_t785.py`) НЕ изменена: ни один текст не поменял статус или первоисточник;
изменена единственная строка — пин версии метода (:208, v2→v3), это метка, а не ожидание.

### 6. Пересчитанный ожидаемый план на стенде .92 (по фикстурам; стенд не тронут)

Вето не меняет ни одного решения по живым текстам стенда: оконные решения forbes (`d4a936ff…`, `6f601bb5…`) и
kommersant (`386de6c8…`) для 14,5 % проходят вето (проверено unit-строками фикстур), строгие решения 5,59 %
(expert/ria/interfax/sbercib) вообще вне зоны действия вето. Значит план T7.85 §5 сохраняет силу с одной
поправкой: записи эпохи v2 (если переатрибуция успела быть запущена до выката v3) помечены несовпадающей меткой и
пересматриваются — строки плана выглядят «было: derivative → Банк России (host-value-attribution-v2) → стало:
derivative → Банк России, будет записано прежнее происхождение значения улики с методом v3», то есть знание о
происхождении не меняется, меняется только метка. Ожидаемые оценки прежние: `391c4388…` — E3/0.75 → **E1/0.15
hypothesis** (первоисточник ЦБ плюс два его пересказа дают одну группу независимости), `9266248e…` — E4/0.95 →
**E3/0.75 supported**. Риски пересчёта: если на живой странице различение «имя показателя / чужая оценка»
ошибётся в строну вето, окно не приклеит пересказ — группа независимости останется завышенной (уровень не
понизится), и это единственный новый способ ошибки v3; ложных склеек вето создать не может по построению (оно
только снимает кандидатов).

### 7. Проверки этой правки

ruff чистый; mypy strict (`packages apps hostctl`) — «no issues found in 150 source files»; образ
`noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1927 passed, 12 skipped** (baseline
1882 + 45 новых: 14 veto-unit + 30 guard-unit + 1 сценарный); отдельный прогон `pytest -q -m timing` —
**4 passed**. Флэков не наблюдалось. Стоп-критерии не задействованы: миграций нет, `AuditEventType`, пороги,
шкала grade → confidence, PSL-список, `group_sources`, fetch/TLS/лимиты research proxy, реестр инструментов,
промпты и payload'ы config-v1…config-v20 не менялись; строгий режим сопряжения, гейт T7.83a, порядок проверок
T7.84, защита коррекций оператора (ADR-0031) и правило «тот же текст с другим типом» (ADR-0033 §9) сохранены;
`ARCHITECTURE.md` не тронут. API не менялся: новых статусов и pairing-значений нет, запись улики прежней формы.
Резидуум окружения после правки: контейнеров `docker ps -a` — 41 (как до работы), тестовых scratch-БД и
шаблонов — 56 / 0 (`noezema_mig*` / `noezema_tpl*`), что совпадает с разделами T7.83a, T7.84 и T7.85.

### 8. Непроверенное и пределы

- Живой прогон на `.92` — за менеджером: ожидания п.6 вычислены по фикстурам; вето поверх живых текстов вне
  фикстур не проверялось вообще (оно может снять там решение — это безопасная сторона, но подтверждать нужно
  сухим прогоном).
- Кавычаемое название вне словаря — самый острый признак: он сработает и на кавычках, которые источником числа
  не являются (названия индикаторов, термины в «ёлочках» внутри зоны). На фикстурах стенда ложных срабатываний нет,
  на живых страницах это риск отказа там, где приписывание было правильным.
- Признак «опрос/исследование X» видит только прямой порядок слов («исследование Ромир»); обратные формы
  («Ромир: опрос показал…») не распознаются — осознанно: обратный порядок дал бы ложные признаки на именах
  словаря. Крупные названия вне кавычек и вне списков действующих лиц (например «ЦМАКП рассчитал…») ловятся
  только глагольным признаком; если ни действия, ни кавычек нет — вето слепое, решение остаётся.
- Собственная оценка страницы («по нашим расчётам») проверяется по всему тексту основания улики, как и прежде:
  conservative veto собственной оценки сильнее, чем нужно для окна публикации, но это сохранённое поведение T7.85.

## T7.85b — окно публикации не видит сокращённое имя оценщика и глагол фиксации: второе замечание приёмки T7.85a (ADR-0029 дополнение)

### 1. Дефект, найденный на приёмке T7.85a

Замер менеджера после T7.85a, воспроизведённый агентом на `7981f08` вызовом
`attribute_value_in_fragment(canonical_uri="https://news.example.ru/a", …)`:

```text
text    = «Согласно опубликованному Банком России обзору, ожидания стабильны.
           ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.»
claim   = «Наблюдаемая инфляция составила 15,2%.»
было    → status=derivative, primary_key=cbr (Банк России), pairing=publication_window
стало   → status=no_value_attribution, primary_key=None, pairing=value_and_primary_in_fragment
```

Причина: ни один признак вето T7.85a такого имени не видит. Кавычек нет (признак «название в кавычках»
слеп), ВЦИОМ и ФОМ не входят в список действующих лиц (это не «компания», не «агентство», не «институт»
и не «центр»), оборота оценки («по оценке», «считают», «прогноз*») тоже нет — роль другого источника числа
несёт **один глагол** «зафиксировал». Живой репортаж называет оценщика сокращённо и без кавычек именно так,
то есть окно публикации приклеивало чужое измерение к публикации ЦБ.

### 2. Правило FIX (та же зона вето, тот же режим; строгий pairing не тронут)

Признаком «другого источника числа» внутри `_other_source_sign` (`apps/research_proxy/value_attribution.py:458`,
признаки подключены строкой :484) стали также:

- **сокращённое имя организации вне словаря первоисточников** — `OTHER_SOURCE_ABBREVIATION_TOKEN` (:241) и
  `_abbreviation_sign` (:428): токен с заглавной буквы, содержащий не менее ДВУХ заглавных букв; смешанный
  внутренний регистр разрешён («РАНХиГС», «ВШЭ», «ЦМАКП», «НИУ ВШЭ», «SberCIB»). Проверка идёт по зоне после
  `_mask_primary_aliases`, поэтому алиас словаря токеном не становится: «ЦБ», «ЦБ РФ», «РОССТАТ»,
  «Банк России» к этому моменту уже пробелы. Одна заглавная буква признаком не считается — это начало
  предложения или обычное существительное («Инфляция ускорилась…», «Оценка … осталась»).
- **глагол фиксации/измерения как действие другого лица** — `OTHER_SOURCE_FIXATION_MARKERS` (:282) и
  `_fixation_verb_sign` (:443) через связки `_SUBJECT_THEN_VERB` (:312) и `_VERB_THEN_SUBJECT` (:313):
  зафиксир*, показал*/показыва*, насчита*, выясни*, измери*/измеря*, замери*/замеря*, зарегистрир*, подсчита*.
  Глагол сам по себе признаком НЕ является: публикация первоисточника состоит из измерений, и голое вето по
  глаголу убило бы оконный режим («Исследование Росстата показало наблюдаемую инфляцию» — защитный тест
  `tests/unit/test_value_attribution_guard_t785a.py`). Признак требует соседства через один пробел с
  НЕ замазанным именем собственным, слева или справа; имя обязано начинаться с заглавной буквы
  (`_NAME_SLOT`, :296, без IGNORECASE — иначе «исследование показало» стало бы чужим источником), граница
  предложения не перескается.

### 3. Белый список: показатели, инструменты и география — не действующие лица

`INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS` (:250), проверка `_is_indicator_or_geography` (:423):

| группа | значения | почему не актор |
|---|---|---|
| имена показателей и инструментов | ИПЦ, ВВП, НДС, НДФЛ, МРОТ, РЕПО, ОФЗ, CPI, PPI, GDP, PMI | показатель число не публикует и не измеряет — он его НАЗЫВАЕТ («ИПЦ вырос на 5,59%», «ставка РЕПО»); вето по нему снимало бы правильные пересказы строгого режима и окна |
| география и юрисдикция измерения | РФ, СССР, США, ЕС | имя указывает территорию значения («годовая инфляция в РФ», «в США инфляция ускорилась»), то есть другой объект измерения, а не другая публикация того же числа |

Организации в список не входят намеренно: именно они — тот другой источник, ради которого вето существует.
Словарь `PRIMARY_SOURCES` НЕ расширяется: ВЦИОМ, ФОМ, ЦСР, Левада-Центр, РАНХиГС, НИУ ВШЭ, ЦМАКП остаются
вне словаря (их собственная страница первоисточником в этой модели не объявлена), и единственное корректное
решение для их числа — отказ приписывания. Ложную склейку белый список исключить не может: источник склейки
— публикация, а не имя показателя. Инвариант закреплён тестом
`test_whitelist_entries_are_not_primary_sources_and_not_actors`: ни одна запись списка не является алиасом
словаря, записью `PRIMARY_SOURCES` или действующим лицом в связке с глаголом фиксации.

### 4. Отрицательный контроль «сам первоисточник» (не сломано)

Маскирование алиасов замазывает имя первоисточника словаря **целиком**, и на его месте остаются
пробелы длиной с имя: соседства «имя␠глагол» не остаётся → вето молчит. Проверено примерами (решения
совпадают до и после правки, замеры на `7981f08` и на новом коде):

| пример | было на 7981f08 | стало |
|---|---|---|
| «…опубликованных 17 декабря данных опроса, проводимого «инФОМ» по заказу Банка России. Оценка текущих темпов роста цен при этом осталась на уровне 14,5%.» (коммерсант-форма) | derivative → Банк России (publication_window) | то же |
| «По данным опубликованного Росстатом релиза, годовая инфляция замедлилась до 5,59%. Росстат показал наблюдаемую инфляцию 15,2%.» | derivative → Росстат (publication_window) | то же |
| «…Наблюдаемую инфляцию показал Росстат: 15,2%.» (обратный порядок) | derivative → Росстат (publication_window) | то же |
| «…Банк России зафиксировал оценку текущих темпов на уровне 14,5%.» | derivative → Банк России (publication_window) | то же |
| «ЦБ РФ зарегистрировал снижение на 1,2 п.п.» | derivative → Банк России (publication_window) | то же |
| «По данным опубликованного Росстатом релиза, ИПЦ вырос на 5,59%.» (утверждение про 5,59%) | derivative → Росстат (строгий режим) | то же |
| обзор ЦБ: 5,59 % в декабре, «В ноябре она составляла 6,64 %.» (утверждение про 6,64 %) | derivative → Банк России (publication_window) | то же |
| «…ИПЦ зафиксировал замедление: годовая инфляция составила 5,59%.» (белый список + глагол) | derivative → Росстат (строгий режим) | то же |
| «ВВП вырос на 1,9%, а инфляция составила 5,59%.» внутри окна | derivative → Банк России (publication_window) | то же |
| «В США инфляция ускорилась, а в РФ годовая инфляция составила 5,59%.» | derivative → Банк России (publication_window) | то же |
| «Наблюдаемая инфляция зафиксирована на уровне 14,5%.» (пассив без действующего лица) | derivative → Банк России (publication_window) | то же |

### 5. Исправленные примеры и принятые ложные вето

| пример после фразы «Согласно опубликованному Банком России обзору…» | было на 7981f08 | стало |
|---|---|---|
| «ВЦИОМ зафиксировал наблюдаемую инфляцию 15,2%.» (сам дефект приёмки) | derivative → Банк России (publication_window) | no_value_attribution |
| «ФОМ насчитал наблюдаемую инфляцию 14,1%.» | derivative → Банк России (publication_window) | no_value_attribution |
| «По данным РАНХиГС, инфляция составила 12,3%.» (без «по оценке») | derivative → Банк России (publication_window) | no_value_attribution |
| «ЦСР выяснил, что инфляция составила 11,9%.» | derivative → Банк России (publication_window) | no_value_attribution |
| «Замеры НИУ ВШЭ показали инфляцию 12,8%.» | derivative → Банк России (publication_window) | no_value_attribution |
| «Левада-Центр показал наблюдаемую инфляцию 13,4%.» | **уже** no_value_attribution (актёр «центр*») | no_value_attribution — тот же отказ, но теперь и по связке «имя␠глагол», то есть без опоры на слово «центр» |
| «Ромир показал наблюдаемую инфляцию 13,9%.» (имя без кавычек, не аббревиатура) | derivative → Банк России (publication_window) | no_value_attribution — верный отказ: Ромир не первоисточник словаря |
| «Инфляция зафиксировала замедление до 14,5%.» (подлежащее — обычное существительное) | derivative → Банк России (publication_window) | no_value_attribution — **ложное вето**, принятый риск |

Ложное вето («честный пересказ стал непомеченным») направлено в безопасную сторону: лишняя независимость
лечится только переатрибуцией или ручной коррекцией (ADR-0031). Ложная склейка новым признаком создать
невозможна по построению: `_other_source_sign` умеет только снимать кандидатов, разночтение окон
(`ambiguous_primaries`) вычисляется раньше вето и остаётся отказом.

### 6. Таблица 12 реальных текстов (T7.85) — изменений нет

Ни одна строка `tests/unit/test_attribution_fixtures_t785.py` не изменила статус, первоисточник или pairing;
изменена единственная строка — пин версии метода (:208, v3 → v4), это метка, а не ожидание. Основание
проверено не только прогоном: зоны вето построены отдельным замером по всем 17 парам «строка фикстуры /
утверждение» (expert_5-59, interfax_1068021_a/b, ria_2068393962, sbercib_2025 и cbr_CPD_2025-12,
cbr_reginfl_64837, garant_1963171, nbj_71790 для 5,59 %; forbes_552221, kommersant_8294535 и
cbr_Infl_exp_25-12 для 14,5 %, 13,1/15,6 и 6,3 %) — новых признаков ни в одной зоне нет. Токены верхнего
регистра в этих текстах (`SberCIB`, `РЕПО`, `НДС`, `ВВП`, `РФ`) либо не попадают в зону вето (строгие решения
5,59 % вычисляются до оконного режима), либо покрыты белым списком.

### 7. Версия метода, переприписывание и ожидаемый план на стенде .92

Метод значений поднят до `host-value-attribution-v4` (:91); маркер формы записи
`VALUE_ATTRIBUTION_SCHEMA = "host-value-attribution-v1"` (`packages/memory/scope.py:447`) НЕ поднят — прежние
записи читаются, их пересматривает метод. Отбор устаревших в `apps/research_proxy/reattribution.py` не
менялся: `COALESCE(scope->'value_attribution'->>'method','') <> :метод`, поэтому **обе** прежние эпохи —
v2 (окно без вето) и v3 (вето T7.85a) — помечаются несовпадающей меткой и пересматриваются; новый сценарий
`test_records_tagged_v2_and_v3_methods_are_reconsidered_by_current_version` держит обе метки в одной базе
(запись Forbes перемаркирована в v3) и требует, чтобы обе строки попали в план («было: derivative → Банк
России (host-value-attribution-v2 / -v3)»), а после прогона имели метод v4 и «изменений нет». Коррекции
оператора (`source_graph_corrections`, актор `operator:*`) неприкосновенны, как и прежде.

План T7.85 §5 сохраняет силу без изменений: вето не трогает ни одно решение по живым текстам стенда
(оконные решения forbes `d4a936ff…`/`6f601bb5…` и kommersant `386de6c8…` для 14,5 % проходят вето —
зафиксировано п.6; строгие решения 5,59 % expert/ria/interfax/sbercib вообще вне зоны действия вето).
Оценки те же: `391c4388…` (наблюдаемая инфляция 14,5 %) — E3/0.75 supported → **E1/0.15 hypothesis**,
причина `insufficient_independence` (первоисточник ЦБ плюс его пересказы = одна группа); `9266248e…`
(официальная инфляция 2025 = 5,59 %) — E4/0.95 supported → **E3/0.75 supported** (три группы: кластер
«Росстат + ЦБ + пересказы», garant.ru, nbj.ru; понижение, никогда не повышение). Отличие v4 от v3 только в
метке: если переатрибуция уже была запущена до выката v4, её строки плана выглядят «было: derivative → Банк
России (host-value-attribution-v3) → стало: то же происхождение, будет записано прежним с методом v4».
Новый способ ошибки — ложное вето по CAPS-токену или подлежащему-существительному: тогда пересказ не
склеится, и группа независимости останется завышенной (уровень не понизится); ложных склеек v4 создать не
может. Точные номера групп подтверждает сухой прогон менеджера (`--dry-run`), а не этот пересчёт.

### 8. Краснота новых тестов на 7981f08

Модуль временно откатывался к версии `7981f08` (`git checkout -- apps/research_proxy/value_attribution.py`
после копии изменённого файла), прогон восстанавливал прежний модуль. Результаты:

- временный пробник только с примерами (без новых констант) — **7 failed, 1 passed**: пять из шести
  отказов «было → стало» краснеют (ВЦИОМ/ФОМ/РАНХиГС/ЦСР/НИУ ВШЭ), краснеют обе проверки признака-аббревиатуры;
  зелёным остаёт «Левада-Центр показал» — на v3 его уже снимал актёр «центр*»;
- новый файл `tests/unit/test_publication_window_veto_t785b.py` на прежнем модуле даёт ошибку сборки теста:
  `ImportError: cannot import name 'INDICATOR_AND_GEOGRAPHY_ABBREVIATIONS'` (признака и белого списка там нет);
- пины метода (`test_method_version_is_v4`, пин в таблице фикстур, сценарные метки v4) на прежнем модуле
  краснеют по значению метки.

После восстановления модуля — все зелёные; изменённых или ослабленных существующих тестов нет, добавлены
11 новых unit-функций (43 примера) и 1 сценарный тест. Таблица фикстур T7.85 не изменена (п.6).

### 9. Проверки этой правки

ruff чистый; mypy strict (`packages apps hostctl`) — «no issues found in 150 source files»; образ
`noezema-sandbox:test` на месте; `pytest -n auto -q -m "not timing"` — **1971 passed, 12 skipped** (baseline
1927 + 44 новых: 43 unit-примера T7.85b + 1 сценарный); отдельный прогон `pytest -q -m timing` — **4 passed**.
Флэков не наблюдалось, упавших timing-тестов не было. Стоп-критерии не задействованы: миграций нет,
`AuditEventType`, пороги, шкала grade → confidence, PSL-список, `group_sources` и `group_source_graph`,
fetch/TLS/лимиты research proxy, реестр инструментов, промпты и payload'ы config-v1…config-v20 не менялись;
строгий режим сопряжения и его гейты (400 / 40 / 120), словарь `PRIMARY_SOURCES` и алиасы, гейт T7.83a,
порядок проверок T7.84, защита коррекций оператора (ADR-0031) и правило «тот же текст с другим типом»
(ADR-0033 §9) сохранены; `ARCHITECTURE.md` не тронут. API не менялся: новых статусов и pairing-значений нет,
форма записи улики прежняя. Резидуум окружения после правки: контейнеров `docker ps -a` — 41 (как до работы),
тестовых scratch-БД и шаблонов — 56 / 0 (`noezema_mig*` / `noezema_tpl*`), что совпадает с разделами T7.83a,
T7.84, T7.85 и T7.85a.

### 10. Непроверенное и пределы

- Живой прогон на `.92` — за менеджером: ожидания п.7 вычислены по фикстурам; ложные вето по CAPS-токенам,
  торговым маркам («SberCIB» в подписи графика) и подлежащим-существительным («Инфляция зафиксировала
  замедление») на живых страницах не проверялись. Проверено только на синтетических строках: «МОНИТОРИНГ
  ОЖИДАНИЙ показал инфляцию 14,5%» после фразы обзора ЦБ был `derivative → Банк России` на v3 и стал
  `no_value_attribution` на v4 — то есть CAPS-подпись блока или графика действительно даёт отказ там, где
  приписывание было правильным. Направление — отказ вместо приписывания, но подтверждать нужно сухим
  прогоном: именно так выглядит завышенная независимость, которая потом снимается только переатрибуцией или
  коррекцией оператора (ADR-0031).
- Связка «имя␠глагол» обязана быть соседней, и это осознанная граница признака: замерено, что «Ромир: опрос
  показал наблюдаемую инфляцию 13,9%» и «инфляция, по данным релиза, показала замедление до 14,5%» остаются
  `derivative → Банк России (publication_window)` и на v3, и на v4. Иными словами ложная склейка в таких
  конструкциях **сохраняется** — T7.85b её не закрыл (именно она была причиной замечания приёмки в других
  формах). Ослабление соседства дало бы ложные признаки на именах самого словаря и на обычных словах,
  поэтому оно не делалось; следующий шаг здесь — признака «дистанцированный оборот», а не разрыхление
  связки.
- Одна заглавная буква не является признаком: сокращённые имена из одного заглавного символа («ЦБ» вне алиаса
  словаря, однобуквенные марки) вето не видит; при этом «ЦБ», «ЦБ РФ» — алиасы словаря и замазаны, то есть
  для них слепота правильная.
- Белый список — константа кода, а не конфигурация: расширение требует правки модуля и теста; решение о
  новой записи (например «ЕАЭС» или отраслевой инструмент) принимается построчно, потому что пропуск в списке
  означает ложное вето, а лишняя запись — пропущенный чужой источник.
