# NOEZEMA — Статус реализации

Ветка: `impl/from-scratch`. План: [PLAN_FROM_SCRATCH.md](PLAN_FROM_SCRATCH.md).
Каждый пункт §22.1/§22.2 ARCHITECTURE.md получает ссылку на тест при закрытии.

## Прогресс по вехам

| Веха | Статус | Tag | Примечание |
|---|---|---|---|
| M0 каркас | ⬜ не начата | — | |
| M1 контракты + LLM | ⬜ не начата | — | |
| M2 изоляция + commit | ⬜ не начата | — | |
| M3 память + web slice (MVP) | ⬜ не начата | — | |
| M4 зависимости + переоценка | ⬜ не начата | — | |
| M5 расширенный цикл | ⬜ не начата | — | |
| M6 Research Proxy | ⬜ не начата | — | |
| M7 полный веб + эксплуатация | ⬜ не начата | — | |

## Матрица §22.1 (техническая приёмка)

| # | Критерий | Класс | Статус | Тест |
|---|---|---|---|---|
| 1 | пробуждение по расписанию, pause/backoff | MVP | ⬜ | — |
| 2 | локальная LLM с fingerprint | MVP | ⬜ | — |
| 3 | causal/idempotency ID в trusted host | MVP | ⬜ | — |
| 4 | typed actions в sandbox | MVP | ⬜ | — |
| 5 | claim только с согласованным lifecycle | MVP | ⬜ | — |
| 6 | один fenced commit attempt | MVP | ⬜ | — |
| 7 | lost COMMIT → reconciliation | MVP | ⬜ | — |
| 8 | failpoints → старый/полный checkpoint | MVP | ⬜ | — |
| 9 | status/timeline/attempts/assessments + auth messages/controls | MVP (dependencies — v1) | ⬜ | — |
| 10 | раздельные messages/stop/abort/controls | MVP | ⬜ | — |
| 11 | нет вслепую-ретраев | MVP | ⬜ | — |
| 12 | random backup point + root set | v1 | ⬜ | — |
| 13 | partial success на safe boundary | MVP | ⬜ | — |
| 14 | каскадная инвалидация | v1 | ⬜ | — |
| 15 | pending/invalid не current | MVP | ⬜ | — |
| 16 | worker: priority, retry, no starvation | v1 | ⬜ | — |
| 17 | repeatability/reproducibility/replication | v1 | ⬜ | — |
| 18 | counterevidence resolutions | v1 | ⬜ | — |
| 19 | unresolved attempt блокирует wake/GC | MVP | ⬜ | — |
| 20 | FIFO полный минимальный путь | MVP | ⬜ | — |
| 21 | sync head update + offline flip | MVP | ⬜ | — |
| 22 | barrier crash-resume | v1 | ⬜ | — |
| 23 | session limits + host reserve | MVP | ⬜ | — |
| 24 | online activation | v1 | ⬜ | — |
| 25 | activating slot / terminal-cleanup | v1 | ⬜ | — |
| 26 | quiesce через writer gate | v1 | ⬜ | — |
| 27 | recovery по pointer tuple | v1 | ⬜ | — |
| 28 | offline rules change | MVP | ⬜ | — |
| 29 | repair runner CAS | v1 | ⬜ | — |
| 30 | bootstrap migration fail-closed | MVP | ⬜ | — |
| 31 | target quiesce + admission | MVP | ⬜ | — |
| 32 | host transition protocol | MVP | ⬜ | — |
| 33 | recovery policy protocol | MVP | ⬜ | — |
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
