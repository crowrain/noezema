# ADR-0013: T7.26 — drain-протокол online-активации: durable «намерение активации» + ожидание окна между сессиями (§8.7.2, уточнение ADR-0009)

- Статус: принято
- Дата: 2026-09-21
- Контекст: EVAL-4d (прогон `ca5933c4-54e1-451e-91ff-faa23546e612`, БД
  `noezema-eval4d` — улика, только SELECT; логи
  `/home/denis/dsh1/eval4d-logs/`; код `375f759`, T7.25). Прогон прошёл
  все сессии без failed, но mid-run флип v4→v5 **не случился ни разу**:
  watchdog набрал порог `et=20` в 00:54Z и до конца прогона сделал 401
  попытку `activate-online`, каждая — `activate-online failed: active
  sessions present: 1`. Гейт `reassessment_slo` без флипа задач не
  получал. Детали — `docs/eval/EVAL-4-freeze.md` §9.

## 1. Причина (по коду)

Спека §8.7.2 «Activation acquisition» замышляет quiesce так: шаг 4 —
запись activating pointer (fence/owner/lease), шаг 5 — «новые worker
batches и sessions видят activating pointer и не проходят admission».
То есть «quiesce через pointer» — механизм, который останавливает
сессии, допущенные **после** публикации pointer'а; сессии, допущенные
**до**, обязан отловить шаг 3 — проверка «нет активных сессий».

В реализации (`acquire_activation`, packages/memory/activation.py)
проверка шага 3 и запись pointer'а шага 4 стоят в **одной**
транзакции, и проверка — **перед** записью:

```text
lock head → count активных сессий + sweep/ count admission-записей
  → если > 0: raise (транзакция откатывается, pointer НЕ записан)
  → иначе: записать pointer (fence/owner/lease)
```

Следствие: pointer публикуется только если в момент проверки активных
сессий ноль. А «quiesce через pointer» (scheduler `activation_slot_busy`
+ worker-deferral + отказ admission) начинает действовать только **после**
коммита pointer'а. В серийном eval-run (драйвер `eval-run` запускает
следующую сессию сразу после предыдущей; admission-запись T7.20
коммитится до phase-1 tx) момент с нулём активных сессий —
миллисекунды между терминальной tx предыдущей сессии и
admission-записью следующей; watchdog опрашивал ~каждые 30 с —
вероятность поймать окно ≈ 0. Итог: pointer никогда не публиковался,
драйвер никогда не видел «идёт активация» и просто продолжал серию.

T7.20 (ADR-0009) сделал барьер **честным** (in-flight сессия видна по
committed `session_admissions`), но барьер только делает проверку
sound'ной — проверка всё равно стоит до публикации pointer'а, и
«окно» в серийной серии не возникает. В EVAL-3d флип прошёл только
потому, что проверка провалилась в visibility-пропасть (phase-1 tx
невидима, admission-записи ещё не было) — это была гонка (claim
`8bbbb06a`), которую T7.20 закрыл. То есть T7.20 закрыл гонку, но
mid-run активацию в серийном прогоне сделал невозможной — это дефект
T7.26.

Остаточная гонка, которую закрывает новый дизайн: `decide()` читает
slot без lock и мог выдать `wake` по stale-читу; если drain
опубликован до registration admission, регистрация (без проверки
slot'а) коммитится после quiesce-подсчёта — flip мог бы пройти при
живой сессии. Закрыто: admission регистрирует запись под head-lock с
проверкой slot, а flip повторно проверяет quiesce под тем же head-lock
(см. §3).

## 2. Решение: drain — durable «намерение активации»

`acquire_activation` разбит на три шага (packages/memory/activation.py):

### Шаг A — `publish_activation_drain` (своя tx)

Slot (fenced lease) пишется **без** проверки «нет активных сессий» —
намерение публикуется до того, как окно появится. Candidate остаётся
`draft`: «slot set + candidate draft» = drain-фаза (новых
lifecycle-состояний нет). Audit `activation_drain_published`
(новый тип) или `activation_takeover` (expired lease → fence+1).
С этого момента:

- scheduler отбивает wake (`activation_slot_busy`);
- worker defer'ит батчи (проверка `activating IS NOT NULL`);
- **admission сессии** (orchestrator phase 0): head-lock (канонический
  порядок head→sessions, как у активации) + при занятом slot —
  `ActivationInFlightError` (новый тип; сессия не стартовала: ни строки
  `sessions`, ни admission-записи).

Lease drain'а = `max(lease, drain_wait + 600s)` — он переживает
ожидание.

### Шаг B — `wait_activation_drain` (polling, ограничен
`drain_wait_seconds`, default 2400s)

Каждый poll — короткая tx под head-lock: count активных сессий +
sweep истёкших admission-записей (T7.20) + count живых + count
unresolved commit_attempts. Ноль → quiesce. По таймауту — cancel
(своя tx: head-lock, tuple-check, slot-очистка, candidate остаётся
`draft`, audit `activation_drain_cancelled`) +
`ActivationError: drain timed out after Ns: active sessions present: M`
— понятная ретраиваемая ошибка; следующий запуск публикует drain
заново (candidate-строка переиспользуется).

Почему 2400s: сессия не живёт дольше phase deadline (watchdog не
продлевает за ним), а крахнутая сессия — её admission-запись живёт до
`phase_deadline + 600s` и свипается; 1800s + 600s покрывает оба случая.

### Шаг C — `_confirm_drain`

Writer-gate wait (старый шаг 1 перенесён сюда: на момент freeze
запущенного worker batch нет, а slot defer'ит новые) + повторная
quiesce-проверка под head-lock + candidate `draft → preparing_heads` +
audit `activation_acquired` (с накопленным `swept_admissions` — поле,
которое читают тесты T7.20). Таймаут gate-wait = drain-cancel (slot
очищен, candidate draft), как в старом протоколе, где gate-wait
предшествовал записи slot.

### Шаг D — re-check в flip'е (`publish_online`)

Атомарная flip-транзакция под head-lock дополнительно проверяет
quiesce (sessions + sweep + admissions + attempts). Belt-and-suspenders
(по конструкции §3 сработать не должно); если сработает — pre-publish
failure → terminal cleanup `failed`, как любой pre-publish отказ.

## 3. Почему инвариант T7.20 сохраняется (и усиливается)

Сессия может коммитить знание только с committed admission-записью
(барьер T7.20). Регистрация записи (orchestrator phase 0) и flip
сериализованы head-row-lock'ом:

1. запись зарегистрирована ДО подсчёта flip'а → живая на момент flip'а
   → flip заблокирован (шаг D);
2. запись регистрируется ПОСЛЕ commit flip'а → slot всё ещё занят
   (terminal cleanup позже) → регистрация отклонена (шаг A,
   `ActivationInFlightError`);
3. запись между drain-публикацией и flip'ом → живая → drain-ожидание
   блокирует (шаг B) — это и есть «окно между сессиями»;
4. сессия, допущенная ДО drain'а → живая запись → drain ждёт её конца
   (trigger на terminal снимает запись в той же tx);
5. крах сессии → её lease мёртв → запись истекла → sweep (T7.20);
6. деградация (запись потеряна/истекла, сессия ещё может коммитить) →
   backstop carry-over на fenced commit (ADR-0009, слой 2) — не тронут.

Барьер T7.20 не ослаблен: flip по-прежнему невозможен при живой
сессии; теперь активация **дожидается** quiesce вместо мгновенного
отказа. Тесты T7.20 (`test_quiesce_race.py`) зелёные без правок смысла
(единственная правка — механическая параметризация
`drain_wait_seconds=1` в двух местах, где раньше ожидался мгновенный
отказ: смысл «живая сессия блокирует flip» сохранён).

## 4. Crash во время drain — намерение не держит допуск вечно

Намерение = slot с candidate `draft` и lease. Правило занятого slot
(`activation_slot_busy`, общий предикат для scheduler / admission-gate /
worker):

- candidate **non-draft** → занят независимо от lease (pipeline в
  полёте; recovery — takeover watchdog'а, как раньше);
- candidate **draft** + **живой** lease → занят (drain в полёте);
- candidate **draft** + **истёкший** lease → **не занят** — крахнутый
  drain (мёртвый publisher, renewals некому делать) больше не держит
  допуск: по аналогии со свипом истёкших admission-записей T7.20.
  Wake/worker возобновляются; stale slot остаётся в БД, но ничего не
  блокирует.
- slot set при отсутствующей строке candidate / без lease →
  fail-closed (занят).

Следующий `activate-online`: expired own/foreign lease → takeover
(fence+1) — старое намерение fenced out; либо drain доходит до таймаута
→ cancel → slot чисто. Тест: `test_crashed_drain_does_not_hold_admission`.

## 5. Драйвер и watchdog

- `noezemactl activate-online --drain-wait-seconds N` (default 2400 =
  `DEFAULT_DRAIN_WAIT_SECONDS`): watchdog вызывает
  `activate-online --payload docs/eval/config-v5-payload.json --reason
  "EVAL-4: mid-run v4->v5 (volatility temporal)" --drain-wait-seconds
  2400` и дожидается: drain публикует намерение, ждёт окно между
  сессиями (ограничено) и делает флип; по таймауту — намерение снято,
  понятная ошибка (exit 1), следующая попытка продолжает.
- `eval-run` (`_run_sessions`): skip reason `activation_slot_busy` →
  ждать (цикл 10с, **без** 600с deadline — drain ограничен в БД:
  drain-таймаут или истечение drain-lease), серия НЕ прерывается и это
  НЕ `nonterminal_session`; `ActivationInFlightError` из
  `run_session()` → сессия не стартовала (phase 0 отклонена под
  head-lock) → возвращение в admission-цикл для той же сессии: не
  failure, не в `consecutive_failures`.
- `wake-tick`: `ActivationInFlightError` → exit 0 (skip, не failure —
  иначе drain копил бы backoff и уводил узел в auto-pause).

## 6. Что НЕ меняется

- Барьер T7.20 (миграция 0022: `session_admissions`, trigger, sweep,
  carry-over backstop) — только добавлены слои; barier не ослаблен.
- Пороги §22.2 (immutable), `claim_type_rules` (rules-v2), замороженные
  payload'ы `config-v2…v5`, корпуса v2/v3 — не тронуты (хэши
  подтверждены: см. EVAL-4-freeze §9).
- `ARCHITECTURE.md` не изменён (уточнение протокола зафиксировано этим
  ADR; шаг 0 drain добавлен к «Activation acquisition» §8.7.2 —
  изменение спеки требует отдельного решения пользователя).
- ADR-0009 не переписан: его барьер и backstop действуют без изменений;
  этот ADR уточняет протокол активации вокруг барьера.

## 7. Тесты

`tests/scenario/test_activation_drain.py` (4 сценария):

1. `test_drain_flips_between_sessions` — серия сессий подряд,
   активация запрошена посередине: drain опубликован (slot set,
   candidate draft, scheduler skip, predicate busy), сессия
   терминируется → флип между сессиями (active, pointer, slot
   очищен, audit drain_published/acquired/published, без
   drain_cancelled) → серия продолжается на новом snapshot (сессия B
   допущена), ни одна сессия не потеряна; инвариант: нет claim'а без
   head на активном snapshot.
2. `test_drain_timeout_cancels_intent` — сессия не кончается в
   бюджет drain'а: намерение снято (slot чист, candidate **draft** — не
   terminal), pointer прежний, запись допуска жива, audit
   published+cancelled(reason drain_wait_timeout), без acquired; после
   конца сессии тот же run (та же candidate-строка) успешен.
3. `test_crashed_drain_does_not_hold_admission` — publish drain
   напрямую («крах» mid-wait): живой lease → scheduler skip
   `activation_slot_busy`; истёкший lease → wake (намерение не держит
   допуск вечно); следующий run — takeover (fence+1) → active.
4. `test_session_admission_rejected_while_drain` — orchestrator phase 0
   под head-lock отклоняет admission (`ActivationInFlightError`, ни
   строки `sessions`, ни записи допуска); после очистки slot та же
   сессия стартует штатно (серия продолжается).

Регрессия: `test_quiesce_race.py` (T7.20) и
`test_online_activation.py` (T4.5) зелёные (механическая
параметризация drain-таймаута в двух местах — смысл не изменён).
