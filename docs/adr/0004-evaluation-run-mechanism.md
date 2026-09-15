# ADR-0004: Механизм evaluation run (§22.2) — frozen config + gates + blind sample

- Статус: принято
- Дата: 2026-09-15
- Контекст: `docs/PLAN_FROM_SCRATCH.md` T7.5, `ARCHITECTURE.md` §16.3, §19 этап 7, §22.2

## Контекст

«evaluation 50–100 sessions» (этап 7) и «прохождение §22.2 завершает
full v1 acceptance» (Gate M7) требуют механизма, который:

1. фиксирует конфигурацию (model + config snapshot + rules) ДО серии —
   «SLO и пороги меняются только до нового evaluation run с новой
   config version» (§22.2);
2. фиксирует пороги (§16.3 «Evaluation thresholds фиксируются до
   серии»);
3. измеряет каждый gate §22.2 с одним из трёх исходов: `passed` /
   `failed` / `insufficient_sample` (denominator < 20 = «измерение не
   произошло», не pass и не fail);
4. строит слепую выборку (seed + size, стратификация по type/status,
   95% CI);
5. записывает overall outcome (passed/failed/insufficient_sample) как
   durable-запись, которую gate M7 читает.

## Решение

`packages/evaluation/service.py` + миграция `0020_evaluation`
(`evaluation_runs`):

- **frozen config**: `config_snapshot_id` (FK → config_snapshots) +
  `model_fingerprint` (jsonb) + `rules_version` (text) + `rules_hash`
  (text, 64-hex). Изменение любого из этих полей = новый run.
- **thresholds** (jsonb): 11 gates §22.2 с порогами. Дефолт:
  `new_supported_refuted_e2: 0.80`, `external_temporal_e3: 1.00`,
  `eligible_sessions_with_outcome: 0.60`,
  `near_duplicate_questions: 0.15`,
  `significant_claim_reuse: 0.25`, `due_stale_time_sensitive: 0.20`,
  `reassessment_slo_seconds: null` (зафиксировано до run оператором),
  `current_pending_invalid_ancestor: 0`,
  `high_severity_incidents: 0`, `blind_provenance_path: 0.90`,
  `blind_scope: 0.80`.
- **gates** (jsonb): `{gate_name: {outcome, numerator, denominator, ...}}`.
  Каждый gate имеет один из трёх исходов §22.2: `passed` (порог
  достигнут на достаточной выборке), `failed` (порог не достигнут на
  достаточной выборке), `insufficient_sample` (denominator < 20 —
  «измерение не произошло»). `insufficient_sample` — не fail и не
  pass: реакция = продлить серию или (до freeze) исключить тип
  claims отдельным ADR.
- **blind sample**: `blind_sample_seed` (bigint, фиксирован до серии) +
  `blind_sample_size` (integer, дефолт 50). Стратификация по
  type/status, 95% CI публикуется с результатами.
- **overall outcome** (CHECK: `running | passed | failed |
  insufficient_sample`): `failed` если ≥1 gate failed; иначе
  `insufficient_sample` если ≥1 gate insufficient_sample (и нет
  failed); иначе `passed`. Full v1 acceptance (Gate M7) требует, чтобы
  ни один gate не был `failed` и ни один не остался
  `insufficient_sample`.
- **eligible/completed sessions**: `eligible_sessions` (запланированные
  + wake_now; operator abort исключён, technical failure входит) и
  `completed_sessions` (с результатом: evidence / закрыт-вопрос /
  пересмотр-claim, §22.2).

Web: `GET /api/v1/evaluation` (list, newest first) +
`GET /api/v1/evaluation/{run_id}` (detail: frozen config + thresholds +
gates + blind sample) + `/evaluation` HTML (read-only: runs table с
color-coded outcome, auto-refresh 5s).

Audit: `EVALUATION_RUN_STARTED` / `EVALUATION_RUN_FINISHED` (closed
registry расширен).

## Обоснование

- **frozen config** — §22.2: «SLO и пороги меняются только до нового
  evaluation run с новой config version». Run не может быть изменён
  задним числом: `finished_at` + `outcome` + `gates` — terminal.
- **three outcomes** — §22.2: «при N<20 gate получает
  insufficient_sample». `insufficient_sample` не смешивается с
  `failed`: «технический успех без quality gates означает, что
  платформа работает, а исследовательская гипотеза не
  подтверждена» (§22.2) — measurement gap ≠ quality failure.
- **blind sample** — §22.2: «слепая выборка … seed, стратификация по
  type/status, 95% CI». Seed фиксируется до серии (не
  deterministically derivable из данных — иначе выборка не слепая).
- **overall outcome** — Gate M7: «прохождение §22.2 завершает full v1
  acceptance». Overall `passed` ⇔ все gates passed; `failed` ⇔ ≥1
  failed; `insufficient_sample` ⇔ нет failed, но ≥1
  insufficient_sample. Это позволяет gate M7 читать один статус.

## Альтернативы

- **Изменение порога в том же run** — отклонено: §22.2 запрещает
  («SLO и пороги меняются только до нового evaluation run»).
- **Two outcomes (pass/fail)** — отклонено: §22.2 требует
  `insufficient_sample` (measurement gap ≠ quality failure).
- **Non-frozen config (config может меняться во время run)** —
  отклонено: §22.2 требует frozen config на всю серию.
- **Blind sample без seed** — отклонено: §22.2 требует фиксированный
  seed (воспроизводимость).

## Тесты

- `tests/scenario/test_evaluation.py` (4): lifecycle (create →
  finish: frozen config + gates + blind sample captured; overall
  passed при всех passed gates); failed gate → overall `failed`;
  insufficient_sample gate (denominator < 20) → overall
  `insufficient_sample`; list + detail (newest first, 404 для
  unknown).
- `tests/scenario/test_web_evaluation.py` (2): list + detail routes
  (frozen config + gates + blind sample); HTML page read-only.

## Ссылки

- `ARCHITECTURE.md` §16.3 (thresholds фиксируются до серии), §19
  этап 7 (evaluation 50–100 sessions; ADR по результатам; Gate:
  §22.1 → §22.2), §22.2 (11 gates, three outcomes, blind sample,
  frozen config).
- `docs/PLAN_FROM_SCRATCH.md` T7.5 (evaluation run §22.2), T7.6
  (ADR по результатам evaluation).
