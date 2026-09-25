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
| M7 полный веб + эксплуатация | ✅ Gate M7 пройден (T7.1–T7.6: knowledge graph + provenance, backup/PITR §15.3, GC root set, security gate + §16 metrics, evaluation run §22.2, ADR-0004; noezema-m7); после gate — дефектные серии EVAL-1/2/3/3b/4/4d: T7.7–T7.28 закрыты (ADR-0005–0015), активная фаза T7.29–T7.43 — в разделе ниже; [дословно](STATUS-archive.md#m7-полный-веб--эксплуатация) | noezema-m7 (на `2e1631c`) | см. раздел M7 в архиве | 942 тест |

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
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | ✅ | test_web_api.py + test_web_mvp.py (status+host/timeline+SSE/messages/commands; admin-token auth на Command, queries open; assessment view — M3 memory) + dependencies (v1-часть): test_web_knowledge.py (T7.1: claims/heads по effective snapshot, зависимости в обе стороны §8.6, provenance-навигация source→parent/artifact/группы) |
| 10 | раздельные messages/stop/abort/controls | MVP | ✅ | test_web_api.py (раздельные endpoints; closed enum; idempotency key; stop/abort флаги сессии) |
| 11 | нет вслепую-ретраев | MVP | ✅ | test_tool_broker.py (§5.7 retry-классы: pure=2, idempotent=1, non_idempotent/observation=0 без вслепую-ретраев; idempotency key + different hash=incident/alert) + test_llm_gateway.py |
| 12 | random backup point + root set | v1 | ✅ T7.2 + T7.3 | backup/PITR-сторона: test_backup_pitr.py (9: create_backup — recovery point `pg_current_wal_lsn()` + content-addressed artifact inventory + host-contour state с явным `host_ops_absent`-evidence, DB CHECK shape/consistency, audit `backup_created` той же tx; restore drill — случайная точка retention window (expired не выбирается), re-hash inventory + всех referenced objects + registry drift check, policy files, boot reconciliation + admission ДО старта runtime (записанный в манифест active head = ожидаемое degraded состояние, сюрприз-хед → failed), `verified_at` только при pass + audit `backup_restore_drill` (outcome/problems), corruption → failed без штампа; CLI `noezemactl backup`/`restore-drill`; diagnostics `backups`-блок). Root set (GC-сторона §15.3): test_gc.py (4: полный root set §15.3 — все классы корней выживают при apply-sweep, expired orphan + unpinned удаляются, checkpointed manifest живёт, expired backup REPORTED (не удаляется), terminal committed attempt + его manifest удаляются; запрет GC при reconciling_commit (rows сессии skipped, terminal attempt сессии не кандидат); dry-run ничего не удаляет + audit `gc_sweep` apply=false; expired backup + terminal config attempt в отчёте). CLI `noezemactl gc [--apply]` |
| 13 | partial success на safe boundary | MVP | ✅ | test_orchestrator.py::test_budget_exhausted_partial (succeeded_partial) |
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

## Активная история (T7.29–T7.43)

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
