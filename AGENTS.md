# AGENTS.md — NOEZEMA (`impl/from-scratch`)

Этот файл — долговременная память для агента. Он перечитывается с диска и переживает сжатие
контекста. Всё, что здесь написано, важнее пересказа в checkpoint-сводке: при расхождении
верь этому файлу, `docs/STATUS.md` и коду, а не сводке.

## 1. Источники истины (по убыванию приоритета)

1. `ARCHITECTURE.md` v0.25 (корень репо) — спецификация. Ссылки вида `§5.2.2` ведут сюда.
2. `docs/PLAN_FROM_SCRATCH.md` — план: вехи M0–M7, задачи `T<веха>.<n>`, gate каждой вехи.
3. `docs/STATUS.md` — фактическое состояние: вехи, матрица §22.1/§22.2 со ссылками на тесты.
4. `docs/adr/` — принятые отклонения и решения. Новое архитектурное решение = новый ADR.
5. Код и тесты. Checkpoint-сводка контекста — последнее по приоритету.

## 2. Ритуал начала работы (и после каждого сжатия контекста)

1. `git status && git log --oneline -15` — где мы, нет ли незакоммиченного.
2. Прочитать `docs/STATUS.md` целиком и раздел текущей вехи в `PLAN_FROM_SCRATCH.md`.
3. Сверить todo-список с `STATUS.md` и `git log`; при расхождении исправить todo, не код.
4. Прогнать проверки (§6) до начала изменений — чтобы знать исходный baseline.

## 3. Неприкосновенные инварианты

Нарушение любого пункта — блокер, даже если тесты зелёные.

- **Staging.** Модель меняет знание ТОЛЬКО через `session_staging`-операции
  (claim / evidence / question / identity). Никаких прямых UPDATE доменных таблиц извне сервисов.
- **Fenced commit.** prepared-строка `commit_attempts` пишется отдельной транзакцией ДО финальной.
  Финальная транзакция: locks в каноническом порядке → fencing-предикат (lease, owner, revisions,
  attempt=prepared) → apply_memory → staging → pointer/ревизия → terminal + audit/outbox — одной tx.
- **Reconciliation.** Неизвестный исход COMMIT не превращается в ложный failure; живой finalizer
  (`finalizer_in_progress`) ≠ rollback; unresolved attempt блокирует wake и GC.
- **ID и ретраи.** `turn_id` / `action_id` / `idempotency_key` генерирует хост, не LLM.
  Ретраи: pure=2, idempotent=1, observation/non_idempotent/unknown=0. Тот же key с другим hash —
  security incident (alert + audit).
- **Оценка знания.** grade/confidence производит ТОЛЬКО rules engine. Verifier, оператор, LLM
  grade не назначают. Duplicate evidence не повышает grade; counterevidence → disputed (≤E1).
- **Lifecycle.** head `current` ⇔ `current_assessment_id` и `epistemic_status` NOT NULL;
  pending/invalid ⇒ оба NULL. Pending/invalid никогда не подаются как current: в контексте —
  отдельный лимит и метка в ТОЙ ЖЕ строке; не хватает бюджета на метку — строка исключается целиком.
- **Effective config.** Только через `runtime_config_heads` → `config_snapshots`, fail-closed
  (`ConfigService.get_effective`). Правила и бюджеты живут в snapshot, не в коде.
- **Sandbox.** Одноразовый контейнер: network none, cap-drop ALL, non-root, ro rootfs,
  no-new-privileges. Инструмент, недоступный профилю, отсутствует в схеме модели.
- **Audit + outbox** пишутся в той же транзакции, что и изменение.
- **Host-контур.** Admission и Command API — fail-closed при любом unresolved host/policy state.
  Журнал переходов fsync-safe (tmp→fsync→rename→fsync(dir)); ровно один active head.

## 4. Дисциплина PR и веток

- Ветка `impl/from-scratch`. Один PR = один логический блок задач плана (`T…`), в сообщении
  коммита — номера задач и пункты § спеки.
- Каждый PR: зелёные ruff + mypy strict + pytest; новые/обновлённые тесты; обновлённый
  `docs/STATUS.md` (строка вехи + строки матрицы со ссылками на тесты).
- Gate вехи отмечается в STATUS.md ТОЛЬКО если каждый критерий имеет ссылку на тест.
  Строка матрицы со статусом ⬜/🔄 означает, что gate не пройден — не писать «все закрыты».
- Tag после gate: `noezema-m0…m7`, MVP — `noezema-mvp`.
- **Требует явного решения пользователя, не делать самостоятельно:**
  - merge в `main`;
  - старт M4 (по плану — только после серии реальных MVP-сессий и замеров нагрузки);
  - удаление/переписывание истории, force-push;
  - изменение `ARCHITECTURE.md`.
- Запрещено: обход staging, `print`-отладка, `TODO` без номера задачи.

## 5. Секреты

- Никогда не вставлять токены/пароли в команды, сообщения, коммиты, STATUS.md, файлы репо.
  В частности, не использовать `https://x-access-token:<token>@github.com/...` в `git push`.
- Push — через git credential helper или переменную окружения (`GH_TOKEN`), настроенные
  пользователем. Если доступа нет — остановиться и сообщить, не просить токен в чат.
- В итоговых сводках и отчётах секреты не упоминать даже частично.

## 6. Окружение и команды

- Репо: `/home/denis/dsh1/noezema-src`; sandbox агента — workspace-write под `/home/denis/dsh1`.
- git identity: `Hermes Agent <hermes@local>`.
- uv: `/home/denis/.local/bin/uv`; venv без pip; `UV_CACHE_DIR="$PWD/.uv-cache"`
  (запись в `~/.cache` запрещена sandbox'ом).
- Тестовая БД: контейнер `noezema-test-db`, порт 54329, `noezema/noezema_dev`. Тесты создают
  одноразовые БД через fixture `migrated_db`; admin-БД `noezema` таблиц приложения не содержит.
- Образ sandbox: `noezema-sandbox:test`.

Полная проверка (запускать перед каждым коммитом):

```bash
cd /home/denis/dsh1/noezema-src && export UV_CACHE_DIR="$PWD/.uv-cache" \
  && .venv/bin/ruff check . \
  && .venv/bin/mypy packages apps hostctl \
  && NOEZEMA_TEST_DATABASE_URL="postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:54329/noezema" \
     .venv/bin/pytest -q
```

Маркеры pytest: `unit`, `scenario` (postgres + fake LLM), `security`, `compat`.

## 7. Известные ловушки

- Docker 29.8: `docker kill -s KILL` (не `-9`); `docker cp` не видит tmpfs — использовать
  bind-mount workspace хоста.
- PostgreSQL FTS: конфиг `russian` (не `simple` — падежи не матчатся); `plainto_tsquery`
  (не `to_tsquery` — `*` ломает разбор); `ts_rank(to_tsvector(...), tsquery)` — вектор первым.
- ORM: атрибут `metadata` занят `DeclarativeBase` → `meta = mapped_column("metadata", ...)`.
- asyncpg возвращает свой UUID-тип: `isinstance`-guard перед `uuid.UUID(...)`.
- Инструмент редактирования: после любой внешней мутации файла (`ruff --fix`, `sed`, heredoc)
  перечитать файл перед edit.
- mypy strict на `packages apps hostctl`; `tests/` исключены.
- Проект русскоязычный: RUF001–RUF003 отключены намеренно.

## 8. Гигиена длинных сессий

- После каждого закоммиченного PR: обновить STATUS.md, отметить todo, и только потом начинать
  следующий PR — это естественная точка для сжатия контекста.
- Решение, принятое по ходу работы и не очевидное из кода (почему выбран вариант X), записывать
  в STATUS.md (раздел вехи) или ADR, а не держать только в диалоге.
- Найденный инвариант — закреплять тестом; найденную ловушку окружения — дописывать в §7.
- Отвечать пользователю по-русски; выполнять план без лишних вопросов, но пункты из §4
  «требует решения пользователя» — только после явного подтверждения.
