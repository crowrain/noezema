# NOEZEMA — Статус реализации

Ветка: `impl/from-scratch`. План: [PLAN_FROM_SCRATCH.md](PLAN_FROM_SCRATCH.md).
Каждый пункт §22.1/§22.2 ARCHITECTURE.md получает ссылку на тест при закрытии.

## Прогресс по вехам

| Веха | Статус | Tag | Примечание |
|---|---|---|---|
| M0 каркас | ✅ выполнена | — | чистое дерево, скелет, CI, fake LLM, ADR-0001/0002/0003 |
| M1 контракты + LLM | ✅ выполнена | noezema-m1 | PR #4–#10; gate пройден: 112 тестов (86 unit ≥ 40), Sealed-сессия question→action→evidence→commit на fake LLM |
| M2 изоляция + commit | ✅ выполнена | noezema-m2 | PR #11–#16: sandbox+runtime, policy engine, tool broker, artifact store+staging+freeze, commit boundary (prepared→fenced final tx) + reconciliation; gate пройден: 206 тестов, failpoints (kill до/после COMMIT, open final tx, stale finalizer, kill mid-action), security (сеть off, cap-drop, injection, ro rootfs) |
| M3 память + web slice (MVP) | 🔄 в работе | — | PR #17: память — модель (0004), evidence identity (§14.3), rules engine v1, independence (PSL+overlap), lifecycle heads (§14.1), apply в fenced tx. PR #18: context pack §5.4 + retrieval (fulltext russian, pending/invalid — отдельный лимит и метка в той же строке §5.4.2). PR #19: host recovery — noezemactl CLI, recovery policy schema v1 (jitter=0, JCS-хэш), fsync-safe transition journal + head + boot reconcile, offline rules (advisory lock, cohort+seal, atomic publish с UUIDv5 invalid-вопросами), fail-closed admission, resume-классификация (transient→retry_wait/0, permanent→resume_blocked/78, unclassified→degraded) + idempotent audit replay, policy change head + event stream, unit-state publisher, systemd units + CI-verify; 305 тест |
| M4 зависимости + переоценка | ⬜ не начата | — | |
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
| 1 | пробуждение по расписанию, pause/backoff | MVP | ⬜ | — |
| 2 | локальная LLM с fingerprint | MVP | 🔄 | test_llm_gateway.py, test_compat_and_roles.py (gateway+fingerprint; local model profile — PR #10) |
| 3 | causal/idempotency ID в trusted host | MVP | ✅ | test_orchestrator.py (turn_id/action_id/idempotency_key генерирует хост) |
| 4 | typed actions в sandbox | MVP | ✅ | test_sandbox_runtime.py + test_tool_broker_sandbox.py (одноразовый контейнер, cap-drop/network/ro-rootfs, shell/python в sandbox, overlay) + test_sandbox_security.py |
| 5 | claim только с согласованным lifecycle | MVP | ✅ | test_memory_service.py (head current ⇔ assessment+epistemic_status NOT NULL, CHECK §14.1; dedup claim+evidence; supersede) + test_orchestrator.py (apply в fenced tx: claim→evidence→assessment→head одной транзакцией) |
| 6 | один fenced commit attempt | MVP | ✅ | test_orchestrator.py (prepared-строка до финального tx; fencing predicate: lease+revision+attempt=prepared; partial unique §14.2) + test_reconciler.py |
| 7 | lost COMMIT → reconciliation | MVP | ✅ | test_reconciler.py (kill before COMMIT→aborted; after commit→accepted; open final tx→finalizer_in_progress; stale finalizer→fenced) + reconcile_with_retries (backoff+jitter, fresh conn) |
| 8 | failpoints → старый/полный checkpoint | MVP | ✅ | test_failpoints.py (kill mid-action → outcome_unknown → session failed, staging не применён, ревизия не поднимается = полный старый checkpoint) + test_reconciler.py |
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | 🔄 | test_web_api.py (status/timeline/messages/commands; attempts — M2, assessments — M3, auth — M7) |
| 10 | раздельные messages/stop/abort/controls | MVP | ✅ | test_web_api.py (раздельные endpoints; closed enum; idempotency key; stop/abort флаги сессии) |
| 11 | нет вслепую-ретраев | MVP | 🔄 | test_tool_broker.py (§5.7 retry-классы: observation/non_idempotent без ретраев; idempotency key + conflict=incident) + test_llm_gateway.py |
| 12 | random backup point + root set | v1 | ⬜ | — |
| 13 | partial success на safe boundary | MVP | ✅ | test_orchestrator.py::test_budget_exhausted_partial (succeeded_partial) |
| 14 | каскадная инвалидация | v1 | ⬜ | — |
| 15 | pending/invalid не current | MVP | 🔄 | test_memory_service.py (lifecycle CHECK: pending/invalid ⇒ assessment/status NULL) + test_context_builder.py/test_retrieval.py (§5.4.2: отдельный лимит pending, метка в той же строке, исключение целиком если не хватает на метку) + test_offline_rules.py (переход в pending/invalid при offline смене правил: deferred→pending, removed-type→invalid) |
| 16 | worker: priority, retry, no starvation | v1 | ⬜ | — |
| 17 | repeatability/reproducibility/replication | v1 | ⬜ | — |
| 18 | counterevidence resolutions | v1 | ⬜ | — |
| 19 | unresolved attempt блокирует wake/GC | MVP | 🔄 | test_reconciler.py (unresolved prepared → reconciled_abort; wake/GC-блокировка — вместе с M3 durable knowledge) |
| 20 | FIFO полный минимальный путь | MVP | ✅ | test_web_api.py::test_wake_now_runs_full_session + test_orchestrator.py::test_full_sealed_session + test_question_selector.py (durable knowledge — M3) |
| 21 | sync head update + offline flip | MVP | ✅ | test_orchestrator.py + test_memory_service.py (sync head update в fenced tx: новый assessment + head→current, old superseded) + test_offline_rules.py (offline flip: atomic publish pointer + UUIDv5 invalid-вопросы одной tx; deferred→pending, removed-type→invalid) |
| 22 | barrier crash-resume | v1 | ⬜ | — |
| 23 | session limits + host reserve | MVP | 🔄 | test_orchestrator.py (max_explorer_steps из config) + test_staging_reserve.py (host reserve: staging_budget_exceeded ДО записи) |
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
| 34 | web degraded observer | MVP | ⬜ | — |

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
