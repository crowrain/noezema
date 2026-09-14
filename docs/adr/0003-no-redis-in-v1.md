# ADR-0003: Отказ от Redis/RQ в v1

- Статус: принято
- Дата: 2026-09-14
- Контекст: `docs/PLAN_FROM_SCRATCH.md` §2.2 (G13), ARCHITECTURE §13, §17

## Контекст

MVP v0.1 на `main` использует Redis + RQ для оркестрации сессий. В
спецификации v0.25 Redis отсутствует в целевом стеке: §13 явно говорит
«Отдельная SPA, WebSocket и Redis первой версии не требуются», фоновые
работы описаны как durable-сущности PostgreSQL (§5.9.1 `reassessment_jobs`,
`outbox_events`, commit attempts).

## Решение

1. Redis и RQ **не используются** в ветке `impl/from-scratch`.
2. Фоновые работы (reassessment worker, reconciliation, barrier processor,
   outbox projector, GC) — процессы доверенного контура с durable
   очередями в PostgreSQL (lease + `next_attempt_at` + backoff).
3. Dev-стек (`infra/compose.dev.yaml`) содержит только postgres + fake LLM.

## Последствия

- Плюсы: на одну инфраструктурную зависимость меньше; fоновая работа
  становится crash-safe по определению (запись в БД атомарна с доменным
  состоянием); совпадает со стеком спеки.
- Минусы: нет «бесплатной» очереди как в RQ — каждый worker реализует
  lease/retry сам (M4); при очень высокой частоте событий outbox-проекция
  потребует внимания к нагрузке (не ожидается в v1: одна сессия за раз).
