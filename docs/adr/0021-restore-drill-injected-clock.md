# ADR-0021: Restore drill работает на инъекционном часовом поясе операции, а не на `now()` БД (T7.46b, §15.3, §22.1 item 12)

Статус: accepted

Дата: 2026-09-25

Контекст: обнаружение T7.45b (latent time bomb), `docs/STATUS.md`
раздел T7.45 п.6; `packages/backup/service.py:164,230`
(`create_backup(now=…)` → `retention_until = now + retention_days`),
`packages/backup/restore.py:75–103` (до T7.46b).

## Контекст

Restore drill (`run_restore_drill`) принимает параметр `now`, но до
T7.46b использовал его только для штампа `verified_at`
(`restore.py:336`), а выбор точки бэкапа сравнивал host-заштампованную
`retention_until` с **реальным** `now()` БД (`restore.py:84`):

```sql
WHERE (retention_until IS NULL OR retention_until > now())
```

Последствия:

1. **одна операция, два часовых пояса** — выбор точки и штамп
   `verified_at` шли от разных часов;
2. **time bomb** — `retention_until` штампует host-час (тот же
   инъекционный `now` из `create_backup`), а истекает по wall-clock'у
   БД: тот же манифест был «retained» в момент бэкапа и «expired» в
   момент drill'а 10/20 дней позже. Тесты
   (`tests/scenario/test_backup_pitr.py`, фиксированный
   `NOW = 2026-09-15T12:00Z`) упали 2026-09-25 12:00Z (10d-окна) и
   упали бы 2026-10-05 (20d-окна) — suite, зелёный на T7.44b
   (~10:39Z того же дня), стал падать каждый день без единой
   изменения кода (T7.45b п.6).

Вопрос постановки T7.46b: (i) bug в коде drill'а (инъекционный
`now` игнорируется) или (ii) drill по замыслу работает на реальном
времени и тест обязан строить `NOW` от текущего времени.

## Решение

**Вариант (i): bug в коде drill'а.** Drill работает на инъекционном
часовом поясе операции: `run_restore_drill(…, now=…)` — один `now`
(или host-час `datetime.now(UTC)` при `None`) на ВСЮ операцию: выбор
точки, штамп `verified_at`, аудит.

Аргументы из фактов кода:

1. **конвенция модуля — инъекционный host-час.** `create_backup`
   принимает `now` (`service.py:164`: `moment = now or _now()`) и
   штампует `retention_until = moment + retention_days` (`service.py:230`);
   `run_restore_drill` принимает тот же параметр `now` и штампует
   `verified_at` из него (`restore.py:336`). Drill-запрос с `now()` БД —
   единственный расходчик времени в модуле, игнорирующий инъекцию;
2. **`retention_until` — host-заштампованное значение.** Истекает оно
   должно от того же host-часа, что и штамповано: сравнивать host-
   значение с часом хранилища — сравнивать часы двух систем (на
   desync-хосте drift накапливался бы в обе стороны);
3. **production-семантика сохранена.** CLI `noezemactl restore-drill`
   (`hostctl/cli.py:657–700`) вызывает `run_restore_drill` БЕЗ `now` →
   после фикса `moment = datetime.now(UTC)` — drill по-прежнему
   выбирает точку «на текущий момент» по host-часу, как до фикса;
   инъекция — механизм для детерминированных прогонов (тесты),
   не для смены семантики;
4. **`now()` БД оправдана там, где часы не инжектятся.** GC-сweep
   (`packages/gc/service.py:338`) — операционная реальная очистка без
   инъекционного часа — продолжает использовать `now()`: там
   «текущий момент» и есть смысл операции. Drill — операция с
   декларированным параметром `now`, и параметр обязан работать.

## Изменения

- `packages/backup/restore.py`: `_pick_retained_backup(db, rng, now)` —
  `retention_until > :drill_now` (бинд параметра, non-nullable
  datetime — ловушка §7 о nullable-биндах не затрагивается);
  `run_restore_drill` вычисляет `moment = now or datetime.now(UTC)`
  и использует его и для выбора, и для `verified_at` (раньше — два
  независимых `now or …`).
- `tests/scenario/test_backup_pitr.py`: фиксированный `NOW` остаётся
  опорной датой (НЕ реальное время); drill-тесты получают фикстуру
  `drill_now` с параметрами **+0/+1/+5 лет** (сдвиг через
  параметр, не через wall-clock) — доказательство не-зависимости от
  даты запуска: retention-окна, прибитые к сдвинутому now, открыты для
  drill'а на ЛЮБОЙ дате запуска; expired-window семантика осталась
  тестируемой (`test_restore_drill_no_retained_backup` — единственный
  бэкап вне окна ЧАСА DRILL'а → `RestoreDrillError`); добавлен
  `test_restore_drill_selects_on_injected_clock_not_wall_clock` —
  регрессионный страж: бэкап, чьё окно ЗАКРЫТО на wall-clock'е
  (2026-09-25 12:00Z < дата запуска) но ОТКРЫТО на инъекционном
  `now=NOW`, обязан выбираться — при возврате к `now()` БД тест падает
  на каждой дате запуска после 2026-09-25.
- Аудит/аудит-payload drill'а не менялись (payload не несёт
  `verified_at`; stamp — только в манифест).

## Тесты

`tests/scenario/test_backup_pitr.py` — 22 тест-кейса (10 уникальных:
3 create_backup + 6 drill ×3 сдвига + 1 injected-clock страж;
прежние 5 drill-тестов — 5 ×3 = 15 + страж + create-only; все
существующие ассерты сохранены, не ослаблены): до T7.46b — 5 падений
каждый день с 2026-09-25 12:00Z (`RestoreDrillError: no backup point
inside the retention window`), после — зелёные на дате запуска
2026-09-25 (wall-clock уже ПЕРЕЧЕРКНУЛ 10d-окна фиксированного
`NOW` — время бомбы реально взорвалось до фикса).

## Не изменено

`create_backup` (интерфейс и штамп `retention_until` — не тронуты),
GC-сweep (`now()` БД — по смыслу), CLI `restore-drill` (не передаёт
`now` — host-час, production-семантика), схемы, `ARCHITECTURE.md`
(§15.3 «drill выбирает случайную точку retention window» — часы не
специфицированы, решение уточняет, не переопределяет), замороженные
payload'ы/промпты/корпуса.
