# ADR-0009: Quiesce-барьер online-активации — committed admission-запись сессии + carry-over на commit (T7.20)

Статус: accepted

Дата: 2026-09-19

## Контекст

Протокол online-активации конфига (§8.7.2, реализован в T4.5) требует
quiesce: перед flip'ом активация проверяет «нет активных сессий и нет
незакрытых commit_attempts» (шаг 3, `acquire_activation`,
packages/memory/activation.py). Проверка корректна, ТОЛЬКО если активная
сессия ВИДИМА проверяющему.

Сессия NOEZEMA живёт в одной долгой phase-1-транзакции
(`run_session`, apps/orchestrator/orchestrator.py: INSERT строки
`sessions` и весь жизненный цикл до COMMITTING — одна tx, коммитится
только когда сессия достигает COMMITTING). До этого момента строка
сессии (и её lease) **некоммичена и невидима** для других транзакций:
проверка `SELECT count(*) FROM sessions WHERE state IN (…)` при flip'е
её не видит.

### Гонка, найденная в EVAL-3d (docs/eval/EVAL-3-freeze.md §10.5)

Прогон EVAL-3d (run `faa3cded-2eec-4087-bc1e-60c242728b90`, БД
`noezema-eval3d`, ADR-0008):

- 15:47:21.8Z — сессия `6f45deea` открыла phase-1 tx (строка сессии —
  невидима);
- 15:47:32.9Z — flip v2→v3 прошёл quiesce-проверку (активных сессий
  «не найдено» — сессия была в полёте) и зафиксировал cohort БЕЗ
  будущего claim сессии;
- 15:50:59.8Z — phase-1 tx скоммитился, сессия дошла до fenced commit:
  revision-fence §5.2.2 НЕ сработал (prepare прочитал ревизию
  ПОСЛЕ flip'а, она совпала), commit создал claim `8bbbb06a`
  (temporal_fact, Sputnik-1) с head только на **superseded** snapshot
  v2 (`974d4158`).

Последствие после T7.19: гейты и слепая выборка считают ровно один head
на claim — head АКТИВНОГО snapshot по указателю `runtime_config_heads`
(§14.1, §8.7.2) — и такой claim **тихо выпадает** из текущего знания и
из всех гейтов. Нарушен инвариант: «никакой claim не должен остаться
с head только на superseded snapshot, пока активный указатель на новом
snapshot».

Почему существующие механизмы не ловили:

- **writer gate** (§5.9.1): сессия его НЕ держит по дизайну
  (packages/memory/writer_gate.py) — держала бы весь сеанс, и worker
  стоял бы за каждой сессией;
- **commit intent** (`sessions.commit_intent_at`): пишется только в
  состоянии `committing` — слишком поздно для quiesce-момента;
- **revision-fence** (§5.2.2): prepare читает ревизию в phase 2 —
  ПОСЛЕ flip'а, поэтому совпадает с текущей;
- **fenced финальная tx**: блокирует session→knowledge→attempt, но
  указатель `runtime_config_heads` не читает и не сравнивает с
  `sessions.config_snapshot_id` (сессия закреплена на своём snapshot'е
  в phase 1 — `plan.config_snapshot_id`).

## Решение

Два слоя. Первый закрывает окно в нормальной эксплуатации; второй —
fail-closed-гарантия «никакое переплетение», включая деградированные
сценарии (потерянная/истёкшая admission-запись).

### Слой 1 — committed admission-запись (барьер)

Миграция `0022_session_admissions`: таблица `session_admissions`
(`session_id` PK, host-generated; `node_owner`; `config_snapshot_id` —
информационный; `lease_expires_at`) + **DB-trigger на `sessions`**:
любой переход в терминальное состояние
(`succeeded/succeeded_partial/failed/cancelled`) в той же транзакции
удаляет запись сессии. Trigger выбран вместо вызова из каждого
терминального кода (finalize/abort/reconciler) осознанно: механизм
уровня схемы не может быть пропущен ни одним путём — ни существующим,
ни будущим; прецедент — sealed-interval trigger (T4.5).

Жизненный цикл (apps/orchestrator/orchestrator.py, `run_session`):

1. **До phase-1 tx** — короткий tx: проверка single-session (M1) +
   `ConfigService.get_effective` + INSERT `session_admissions`
   (`packages/memory/session_admission.register_session_admission`).
   Session UUID генерирует хост ДО этой tx (AGENTS.md §3) и phase 1
   создаёт строку сессии с этим же id.
2. Phase 1/2/3 — без изменений; строка сессии остаётся невидимой до
   COMMITTING, как и раньше.
3. **Терминальная tx** (любой путь: success/fail/abort/reconcile) —
   trigger удаляет запись атомарно со сменой состояния.
4. **Крах сессии** — phase-1 tx откатывается (строки сессии не
   существует), запись живёт до истечения lease. Lease =
   `created_at + phase_deadline + 600s` (не обновляется heartbeat'ом —
   сессия физически не может жить дольше phase deadline: watchdog
   отказывает в renew, поэтому lease покрывает всю возможную жизнь
   сессии, включая prepare+final окно).
5. **Quiesce-проверка** (`acquire_activation`): в той же tx, что и
   существующие проверки — sweep истёкших записей (погибшая сессия не
   может коммитить знание: её собственный более короткий lease мёртв) +
   count живых; живые записи считаются активными сессиями:
   `active sessions present: N`. Число swept фиксируется в audit
   `activation_acquired/takeover` (`swept_admissions`).

Корректность: во время активации (указатель `activating` стоит) новые
сессии не допускаются (admission-гейт T4.4), поэтому в полёте на
момент quiesce может быть только сессия, допущенная ДО активации — у
неё живая admission-запись (если она жива) → flip заблокирован.
Сессия, чья запись истекла, не может коммитить (её lease мёртв) →
sweep безопасен.

### Слой 2 — carry-over на fenced commit (fail-closed backstop)

Окно, которое барьер закрывает только при живом lease: запись
потеряна/истекла, но сессия всё ещё может коммитнуть (например,
деградация watchdog'а). Backstop — в fenced финальной tx
(`finalize`, packages/domain/services/commit.py, шаг 3a'):

- после apply_memory tx читает указатель
  `runtime_config_heads.active_config_snapshot_id` **без блокировки
  строки head** (финальная tx уже держит lock строки сессии; порядок
  head→session дал бы deadlock с acquire активации, который локирует
  head→sessions; корректность без lock — по индукции: следующий flip
  не может начать до commit'а этой tx, т.к. её quiesce блокирует живая
  admission-запись, и его cohort закроет перенесённые claims);
- если `sessions.config_snapshot_id ≠ указатель` — каждый claim,
  СОЗДАННЫЙ этим коммитом (`claims.created_in_session = session.id`),
  переносится на активный snapshot: **pending head**
  (`current_assessment_id=NULL, epistemic_status=NULL,
  prepared_by='commit_carryover'` — lifecycle §3: pending ⇒ NULL/NULL,
  pending никогда не подаётся как current) + **durable reassessment job**
  (`reason='commit_carryover'`, queued; worker пересчитает claim по
  правилам активного snapshot: current или invalid) — ровно механизм
  cohort'а §8.7.2, применённый к claims, которые cohort пропустил;
- audit `commit_snapshot_drift` (session_snapshot, active_snapshot,
  carried_claims) в той же tx.

Выбран carry-over, а не отказ от commit'а (fencing_conflict → failed):
отказ выбросил бы валидное знание из-за смены конфига оператором, а
протокол §8.7.2 отвечает на «claim существует под старым конфигом»
именно pending head + durable job; commit остаётся атомарным
(перенос — в той же fenced tx, откат — откат всего).

## Почему не другие варианты

- **Сессия держит writer gate весь сеанс**: worker (приоритет 0) ждал
  бы gate каждую сессию — деградация параллелизма §5.9.1, который
  writer_gate.py сознательно избегает (интент + fenced check — «last
  line of defence»).
- **Fence на commit: `plan.config_snapshot_id ≠ указатель →
  fencing_conflict`** (отказ): инвариант держит, но знание сессии
  теряется без пересчёта; это бы стал нормальный путь при любом
  flip'е, перешедшем во время сессии, а не safety-net.
- **Укоротить phase-1 до видимой строки сессии** (INSERT строки
  отдельным tx, остальное phase 1): меняет recovery-семантику —
  упавшая сессия оставляла бы закоммиченную non-terminal строку
  (`created`), которую пришлось бы sweep'ить, а single-session проверка
  видела бы мёртвые сессии до sweep'а; больший радиус поражения.
- **Лочить head-строку в финальной tx**: deadlock head↔session с
  acquire активации (см. слой 2) — неопределённая жертва deadlock'а в
  критическом commit-пути.

## Следствия

- Quiesce §8.7.2 стал sound'ным: flip не может пройти, пока живая
  сессия в полёте (admission-запись видна). Гонка EVAL-3d в
  детерминированном виде закрыта тестами
  `tests/scenario/test_quiesce_race.py` (включая backstop-инвариант).
- Погибшая сессия блокирует активацию максимум `phase_deadline + 600s`
  (истёкшая запись свипается следующей активацией) — деградация, а не
  dead-end.
- `rules_version`/`rules_hash`/пороги §22.2 не меняются; замороженные
  конфиги и корпуса не тронуты.
- Инвариант «нет claim с head только на superseded snapshot» теперь
  гарантирован при ЛЮБОМ переплетении: барьер закрывает живые сессии,
  backstop — деградированные; проверка инварианта — в тесте
  `test_commit_after_flip_carries_claims_to_active_snapshot`
  (SELECT: нет claim'а с head на superseded и без head на активном).
- Данные EVAL-3d (`noezema-eval3d`) не переписаны: claim `8bbbb06a`
  остаётся как улика (ADR-0008, freeze §10.5); на следующих прогонах
  сессии, перешедшие flip, переносятся carry-over'ом автоматически.
- Слой 2 срабатывает только при нарушении слоя 1 (потеря/истечение
  записи) — в нормальной эксплуатации событие
  `commit_snapshot_drift` не происходит.
