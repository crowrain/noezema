# ADR-0005: Первый фактический evaluation run (§22.2) — заморозка, прогон 50 сессий, исходы

- Статус: принято
- Дата: 2026-09-15
- Контекст: `docs/PLAN_FROM_SCRATCH.md` T7.7, `ARCHITECTURE.md` §19
  этап 7, §22.2; ADR-0004 (механизм)

## Контекст

ADR-0004 закрыл механизм (frozen config + 11 gates + three outcomes +
blind sample + overall outcome). §22.2 требует фактический прогон:
«После выполнения §22.1 и до объявления full v1 acceptance запускается
50–100 eligible sessions с замороженными model/config/rules».

Статус механизма без прогона — «платформа работает, гипотеза не
подтверждена». Этот ADR фиксирует конфигурацию серии (до запуска) и
фактические исходы.

## Заморозка (до запуска серии)

| Параметр | Значение |
| --- | --- |
| модель | `qwen36-35b-a3b-q6-mtp` @ `http://192.168.1.48:8080/v1` (llama-swap) |
| LLM env | `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192` (reasoning-бюджет ≥ P99, AGENTS §7), `NOEZEMA_LLM_TIMEOUT_SECONDS=600` |
| config snapshot | bootstrap (activation_mode='bootstrap', active head) — id в run |
| rules | `rules-v1` + `rules_hash(snapshot.claim_type_rules)` — в run |
| SLO reassessment | 3600 с (зафиксировано до серии) |
| blind seed | 20260915 |
| blind size | 50 |
| сессий | 50 |
| corpus | `docs/eval/question-set-v1.jsonl` — 50 вопросов (12 вычислительных, 10 фактических, 10 временных, 6 multi-step, 5 терминов, 4 workspace-наблюдения, 3 self-model); sha256 записан в `model_fingerprint` run |
| БД | `noezema-eval` @ 127.0.0.1:54329 (alembic head, созданная под серию) |
| node owner / data root | `eval-node` / `/home/denis/dsh1/noezema-eval-data` |

Выбор модели: reasoning-модель целевого класса в llama-swap-пуле;
`max_output_tokens=8192` — минимум из ловушки AGENTS §7 (при 4096
сессии падали `finish_reason=length`).

## Проведение

`noezemactl eval-run` (hostctl): freeze run ДО серии → seed corpus
(origin='seeded', dedup by text) → 50 сессий через стандартный wake
admission + orchestrator pipeline (admission fail-closed, operator
resume при sticky pause) → `compute_gates`
(`packages/evaluation/gates.py`) → `finish_evaluation_run`.

Smoke-прогон (1 сессия, отдельный run, до серии) подтвердил pipeline
end-to-end: freeze → seed → succeeded (9 steps, ~8.7 мин, включая
первую загрузку модели) → gates → finish.

Run: `29667a42-0f6f-44b3-9df7-7175a1f25882`, окно 2026-09-15
16:03:13 → 19:42:09 UTC (~3 ч 39 мин, 239 model runs).

### Поправка данных (до пересчёта gates, config НЕ менялась)

Оркестратор не заполнял `sessions.started_at` (баг: window-якорь §22.2
не записывался) — все 48 строк сессий имели `started_at = NULL`, и
window-CTE gates исключал их (denominator 0). Исправление:

1. **Платформа** (T7.7): оркестратор теперь записывает
   `started_at` при создании сессии (apps/orchestrator/orchestrator.py,
   тест в test_orchestrator.py).
2. **Данные этого run**: 48 строк backfill'нулись из audit trail
   (`started_at = MIN(audit_events.occurred_at)` по сессии —
   SESSION_STARTED — первое событие сессии).

Зафиксированная конфигурация (модель, snapshot, rules, SLO, пороги,
seed, blind size) не изменялась: поправлен баг расчёта, а не
параметры (§22.2 «SLO и пороги меняются только до нового run» —
соблюдено).

## Фактические результаты

**Сессии**: 50 попыток; 48 зарегистрированных terminal сессий
(26 succeeded + 22 succeeded_partial, 0 failed). 2 технических
срыва на уровне lease (LeaseLost после >600 с, сессии 32 и 38):
строки сессий не созданы (срыв до/на границе lease), из eligible-
окна исключены; зафиксированы в логе драйвера. eligible = 48,
completed (succeeded/partial) = 48.

**Знание**: 34 claims (27 computed_result, 5 local_observation,
1 external_fact, 1 temporal_fact); 29 current (все supported, E2+);
5 pending (без head — включая оба external/temporal fact). 26 вопросов
verified, 22 partially_answered, 0 near-duplicate циклов, 0
high-severity инцидентов, 0 reassessment jobs (не было триггеров),
1 claim dependency.

| # | gate | порог | исход | числ/знам |
| --- | --- | --- | --- | --- |
| 1 | new_supported_refuted_e2 | ≥80% E2+ | **passed** | 29/29 (1.0) |
| 2 | external_temporal_e3 | 100% E3 | insufficient_sample | 0/0 (both pending) |
| 3 | eligible_sessions_with_outcome | ≥60% | **passed** | 48/48 (1.0) |
| 4 | near_duplicate_questions | ≤15% | **passed** | 0/48 (0.0) |
| 5 | significant_claim_reuse | ≥25% | **failed** | 1/29 (0.0345) |
| 6 | due_stale_time_sensitive | <20% | insufficient_sample | 0/0 (нет temporal current) |
| 7 | reassessment_slo (3600 с) | 100% | insufficient_sample | 0/0 (нет jobs) |
| 8 | current_pending_invalid_ancestor | 0 | **passed** | 0 (29 current) |
| 9 | high_severity_incidents | 0 | **passed** | 0 |
| 10 | blind_provenance_path | ≥90% | **passed** | 29/29 (1.0) |
| 11 | blind_scope | ≥80% | **passed** | 29/29 (1.0) |

Blind sample: 29 current claims < запрошенных 50 → выборка = все
(«минимум 50 либо все», §22.2), seed 20260915, 100% покрываются
provenance path и не выходят за scope.

**Overall outcome: `failed`** (1 failed gate; 3 insufficient_sample).

## Вывод

По §22.2: «Технический успех без quality gates означает, что
платформа работает, а исследовательская гипотеза не подтверждена».
Итог первого run — именно этот статус, зафиксированный измерением:

- **Платформа работает**: 100% eligible сессий с результатом, 100%
  новых claims E2+ supported, 0 pending/invalid ancestor, 0
  high-severity инцидентов, 100% blind-выборки с полным provenance
  path и в scope, 0 дублей.
- **Гипотеза не подтверждена**: `significant_claim_reuse` failed
  (3.4% < 25%) — на разнородном корпусе каждая сессия отвечала на
  новый вопрос и почти не ссылалась на накопленное знание (1
  dependency из 29 значимых claims; 0 claim_revisions). Механизм
  переиспользования (memory.search → evidence/revision) существует,
  но модель к нему не прибегает.
- **3 gate'а insufficient_sample** (external/temporal E3, due/stale,
  reassessment SLO): измерение не состоялось — ни одного
  supported/refuted external/temporal claim, ни одного reassessment
  job в окне. По §22.2 реакция: продлить run (корпус/модель,
  стимулирующие тип) либо заранее до freeze следующего run
  исключить тип отдельным ADR; закрывать как отказ нельзя.

Реакция на `failed` — работа над системой (механизм/промпты
переиспользования знания между сессиями). Full v1 acceptance не
объявляется: §22.2 требует «ни один gate не failed и ни один не
insufficient_sample». Следующий run — с новой config version
(изменённые пороги/SLO только до него).
