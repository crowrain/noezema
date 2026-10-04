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
2. Прочитать `docs/STATUS.md` целиком (теперь компактный: вехи, матрицы §22.1/§22.2, активная
   история) и раздел текущей вехи в `PLAN_FROM_SCRATCH.md`; `docs/STATUS-archive.md` — ТОЛЬКО
   если расследуешь конкретную более старую задачу/веху, упомянутую по имени (T-номер, PR, ADR),
   которой нет в текущем STATUS.md.
3. Сверить todo-список с `STATUS.md` и `git log`; при расхождении исправить todo, не код.
4. Прогнать проверки (§6) до начала изменений — чтобы знать исходный baseline.

`docs/STATUS-archive.md` — второй по приоритету источник (ниже основного `docs/STATUS.md`;
порядок приоритетов §1 не меняется) для расследования старых решений и закрытых вех: содержит
перенесённые из STATUS.md разделы побайтово (дословно), оглавление с якорями — в начале файла.

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
- Не добавляй заглушки импортов и не меняй публичный контракт модуля ради тестируемости.
  Нужна отдельно тестируемая логика — выноси чистую функцию в отдельный модуль и тестируй её,
  а рабочий модуль пусть её вызывает.

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
  одноразовые БД через fixture `migrated_db` — с T7.55 scratch (`noezema_mig_<hex>`) клонируется из
  session-шаблона `noezema_tpl_<pid>_<hex>` (`CREATE DATABASE ... TEMPLATE`; шаблон запечатан
  ALLOW_CONNECTIONS false), `alembic upgrade head` — один раз на воркер, не на тест; admin-БД
  `noezema` таблиц приложения не содержит.
- Образ sandbox: `noezema-sandbox:test`.

Полная проверка (запускать перед каждым коммитом):

```bash
cd /home/denis/dsh1/noezema-src && export UV_CACHE_DIR="$PWD/.uv-cache" \
  && export DOCKER_CONFIG="${NOEZEMA_DOCKER_CONFIG:-$PWD/.docker-config}" \
  && .venv/bin/ruff check . \
  && .venv/bin/mypy packages apps hostctl \
  && { docker image inspect noezema-sandbox:test >/dev/null 2>&1 \
       || docker build -f sandbox/Containerfile -t noezema-sandbox:test sandbox/; } \
  && NOEZEMA_TEST_DATABASE_URL="postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:54329/noezema" \
     .venv/bin/pytest -n auto -q -m "not timing" \
  && NOEZEMA_TEST_DATABASE_URL="postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:54329/noezema" \
     .venv/bin/pytest -q -m timing
```

pytest — параллельно через pytest-xdist `-n auto` (T7.41: на `.87` это 6 воркеров).
Тесты с маркером `timing` из этого пула ИСКЛЮЧЕНЫ и догоняются вторым ПОСЛЕДОВАТЕЛЬНЫМ прогоном
без xdist (T7.56 — lease/heartbeat-тесты, чья корректность зависит от wall-clock часов БД; при
конкуренции нескольких воркеров за общий Postgres их продление аренды опаздывает за запас).
Оба прогона обязательны: суммарно они дают полный набор тестов.
Образ sandbox собирается ОДИН РАЗ до запуска pytest: под xdist каждый воркер — отдельный
процесс со своим session-scope, параллельный `docker build` одного тега — гонка, поэтому
фикстура `sandbox_image` только проверяет наличие образа (иначе — понятная ошибка).
`DOCKER_CONFIG` должен указывать на writable-каталог (sandbox агента запрещает запись
в `~/.config`; по умолчанию — `.docker-config` в репо, та же логика, что в фикстуре).

Маркеры pytest: `unit`, `scenario` (postgres + fake LLM), `security`, `compat`, `timing` (T7.56).

Приём вопроса оператором (T7.59, ADR-0024): `noezemactl ask "<текст>" [--priority N]` или
`POST /api/v1/questions` (заголовок `X-Admin-Token` — тот же токен, что у команд); вид очереди —
открытый `GET /api/v1/questions`. Правила держит один сервис `packages/domain/services/question_intake.py`:
origin всегда существующий `message` (нового enum-значения и миграции нет), дедуп по точному тексту
(повтор возвращает тот же id, приоритет повтором не меняется), text ≤ 2000, priority — int в [-100, 100]
и поднимает вопрос в начало FIFO (`priority DESC, created_at ASC`) без правки селектора. Аудит-типа
приёма нет: закрытый `AuditEventType` не расширяем, долговременная запись — сама строка `questions`.

Исполнитель инструментов сессий (wake tick, eval-run, смоуки) выбирается env
`NOEZEMA_TOOL_EXECUTOR`: не задан или `stub` — dev-подставка `StubToolExecutor` (ДЕФОЛТ, поведение
прежнее); `sandbox` — одноразовый контейнер на сессию через `SandboxToolBroker` (T7.58, ADR-0023; образ и
движок проверяются до старта сессии, отсутствие = отказ exit 78, тихого отката на stub нет). Для реальных
смоуков после контрольного прогона рекомендован `sandbox` (`EnvironmentFile` unit'а + пин образа
`NOEZEMA_SANDBOX_IMAGE`, пока не включён по умолчанию — решение пользователя).

Dev-стенд (T7.59(б), `deploy/dev-stand/`, целевая ВМ 192.168.1.92 — её разворачивает менеджер по ssh,
агент к `.92` не обращается и пакет только готовит): идемпотентный `bootstrap.sh` (`--dry-run`,
`--no-docker-install`, `--no-units`, `--stub-executor`, `--force`, `--web-host/--web-port/--user`) ставит
пакеты, venv+prod-зависимости **только через uv**, образ с одной закреплённой меткой
`noezema-sandbox:dev-stand` → `NOEZEMA_SANDBOX_IMAGE` (дефолт `:dev` и тестовый `:test` не трогаем),
Postgres 15 в docker **только на 127.0.0.1** с томом и healthcheck, базу `noezema-dev`, миграции, активацию
config-v13 (T7.59(в): = config-v12, но `model.context_window`/`backend_context_limit` = 131072 под окно EXL3;
пропуск, если head уже не bootstrap), env-файл `/etc/noezema/dev.env` (0600, секреты не
печатаются и в `--dry-run` маскируются) и dev-юниты `deploy/dev-stand/systemd/*` — **не копии**
`infra/systemd/*`: `User=` = пользователь стенда, данные `/var/lib/noezema-dev`, группа
`noezema-dev.target` (в загрузку не ставится), tick 60 с (`TimeoutStartSec=3600`), maint 60 с
(reassessment+reconcile), unit-state 5 с. Bind веб-сервиса: `NOEZEMA_WEB_HOST`/`NOEZEMA_WEB_PORT` (дефолт
`127.0.0.1:8321`, поведение прежнее); не-loopback bind при пустом `NOEZEMA_ADMIN_TOKEN` — отказ запуска с
кодом 78 (`apps/web/bind.py`). Скрипты отказываются работать с базой вне `noezema-dev*` (в том числе
`*eval*`/`*smoke*`) и с путями `/var/lib/noezema`, `/run/noezema`. Состояние — `status.sh`, сброс dev-базы —
`reset-db.sh` (подтверждение вписыванием имени базы). Разбор и риски развёртывания: STATUS.md T7.59(б).

## 7. Известные ловушки

- Docker 29.8: `docker kill -s KILL` (не `-9`); `docker cp` не видит tmpfs — использовать
  bind-mount workspace хоста.
- `created_at` строк, рождённых в долгой phase-1-транзакции (model_runs, audit и т.п.),
  = `now()` = старт транзакции: внутри одной сессии все значения совпадают, хронологию
  строить только по `audit_events.sequence`.
- Локальные reasoning-модели: `reasoning_content` расходует `max_output_tokens`; длина
  reasoning дрейфует между запусками — бюджет держать ≥ P99 (для qwen36-35b-a3b-q6-mtp
  минимум 8192; при 4096 сессии падали `finish_reason=length` с пустым content).
- Движок halogen-flash-next (192.168.1.48:8080): отклоняет JSON Schema-ключевые слова
  `format` и `pattern` (HTTP 400 «unsupported keyword»), и сообщает только ПЕРВОЕ такое
  слово — остальные прятаться могут (замер T7.23, ADR-0012). Для него —
  `NOEZEMA_LLM_SCHEMA_PROFILE=halogen`; ответ движка без format валидирует хост полной
  pydantic-моделью (модель может выдать не-UUID — хост отклонит).
- PostgreSQL FTS: конфиг `russian` (не `simple` — падежи не матчатся); `plainto_tsquery`
  (не `to_tsquery` — `*` ломает разбор); `ts_rank(to_tsvector(...), tsquery)` — вектор первым.
- ORM: атрибут `metadata` занят `DeclarativeBase` → `meta = mapped_column("metadata", ...)`.
- asyncpg возвращает свой UUID-тип: `isinstance`-guard перед `uuid.UUID(...)`.
- SQLAlchemy `text()`: nullable bind-параметр (`:p IS NULL` при p=None) НЕ
  конвертируется в позиционный (asyncpg не может вывести тип из None), и
  `:p::text` тоже не конвертируется — литеральный `:` уходит в ПГ
  (PostgresSyntaxError). Паттерн: динамическое WHERE-условие — параметр
  присутствует в SQL и в params только когда не None.
- Инструмент редактирования: после любой внешней мутации файла (`ruff --fix`, `sed`, heredoc)
  перечитать файл перед edit.
- mypy strict на `packages apps hostctl`; `tests/` исключены.
- Тайминг-тесты аренды (T7.51, `test_orchestrator.py::test_slow_llm_does_not_lose_commit_lease`):
  запас на одно опоздавшее продление = `ttl − ttl/3`; при TTL 1 с (0,67 с) heartbeat успевает
  опоздать НЕ только из-за CPU, но и из-за конкуренции за PostgreSQL — 6 одновременных
  `alembic upgrade head` scratch-БД (профиль `-n auto`) воспроизводят flake сами по себе, а
  12 busy CPU-процессов — нет. Абсолютные времена таких тестов держать в масштабе, дающем запас
  ≥ нескольких интервалов продления (сейчас TTL 3 с); продукт при этом менять нельзя.
- Дополнение к предыдущему пункту (T7.55): «6 одновременных alembic upgrade head» — профиль нагрузки
  СТАРОЙ фикстуры; теперь scratch-БД клонируются из session-шаблонов и параллельных миграций по
  одному Postgres нет, а сборки шаблонов сериализованы flock'ом. При внешнем load на машине lease-тесты
  могут флакать на любом пути фикстур — разбор в STATUS.md (T7.55 часть 3).
- Дополнение к двум предыдущим пунктам (T7.56): unit guard-тесты `tests/unit/test_lease.py` масштабированы
  с TTL 0,6 до 3,0 с (кратности сохранены; запас на опоздавшее продление 2,0 с) — на старом масштабе flake
  воспроизводился 1/20 под DDL-штормом Postgres. Стенные lease-тесты помечены маркером `timing` и в полной
  проверке (§6, ci.yml) идут ОТДЕЛЬНЫМ последовательным прогоном после `-n auto -m "not timing"`: источник
  flake — конкуренция нескольких xdist-воркеров за общий PostgreSQL, а не CPU; новые wall-clock тесты в
  параллельный пул не добавлять. Замеры и разбор: STATUS.md T7.56.
- Проект русскоязычный: RUF001–RUF003 отключены намеренно.
- Eval/wake/smoke исполняют инструменты через `StubToolExecutor` (dev-подставка, хост .87 без изоляции —
  риск python.execute осознанный DEV ONLY); sandboxed ToolBroker — только в тестах. Из списка инструментов
  снапшота при этом живут и неисполнимые в stub: shell.execute → Observation `tool_not_supported:` (T7.57(b),
  раньше был безликий `unreachable`), artifact.create → `unknown tool`. Фильтровать offered-список под исполнителя
  нельзя молча: это меняет `tool_schema_hash` шагов и ломает байт-сопоставимость прогонов (STATUS T7.57, разбор А/Б/В).
- Дополнение к предыдущему пункту (T7.58, ADR-0023): в режиме `sandbox` контейнер открывается и уничтожается
  самим `run_session` (имена `noezema-sb-<session_id[:12]>`, host-overlay `NOEZEMA_SANDBOX_WORK_ROOT/<session_id>`);
  сеть контейнера ВСЕГДА `none` — даже у curated/open_lab (`research_proxy` из снапшота egress контейнеру не даёт:
  research.fetch, memory.search и staging-инструменты остаются host-side); пакетов проекта и хостовой ФС в
  контейнере нет, лимиты команды берутся из `sandbox/policy/<profile>.yaml` (sealed 60 с/512 MiB/32 PID), а не из
  stub-константы 15 с. Утечка контейнера возможна только если отказал `docker rm -f` — тогда в логе процесса
  строка `LEAKED container`; тихого отката на stub при недоступном движке/образе нет (отказ до старта сессии).
- CliRunner-тесты хостовых команд (T7.59, `tests/scenario/test_cli_ask.py`): команда CLI сама поднимает
  свой event loop, поэтому в синхронном тесте нельзя брать `AsyncEngine` из фикстуры `migrated_db` —
  второй `asyncio.run` на нём даёт «Event loop is closed» (+ ошибка на teardown). Паттерн: каждый
  read/write теста — свой engine из scratch-URL с `dispose()`; саму фикстуру использовать для URL.
- Standalone-веб раньше строил stub-исполнитель в хардкодном `/var/lib/noezema/workspace` (`build_standalone_app`):
  при запуске от непрод-пользователя (dev-стенд, `User=<user>`) импорт `apps.web.main` падал на `PermissionError`,
  и юнит уходил в restart-loop. С T7.59(б) путь = `<NOEZEMA_DATA_ROOT>/workspace` через
  `apps/web/bind.resolve_standalone_workspace(data_root_from_env())` — тот же env и дефолт, что у wake tick;
  в режиме `sandbox` этот путь не используется (host-overlay берётся из `NOEZEMA_SANDBOX_WORK_ROOT`).
- Ручной вход `python -m apps.orchestrator` до T7.59(в) хардкодил `/var/lib/noezema/workspace` так же; теперь
  корень берётся из env (`apps/orchestrator/scheduler.workspace_root_from_env()`, постоянная
  `WORKSPACE_SUBDIR` — одна на веб-бинд, wake tick и manual entry, литералы «workspace» сведены в неё).
  Артефакты остаются sibling'ом workspace ⇒ `<NOEZEMA_DATA_ROOT>/artifacts`; env не задан → прежний путь.
- «wake now» на standalone-входе (T7.59(в)): `build_standalone_app` не может построить оркестратор до создания
  приложения (оркестратор живёт на его factory), поэтому присваивает `app.state.orchestrator` ПОСЛЕ `create_app`.
  Командный обработчик обязан читать `app.state.orchestrator` в момент команды, а не замыкаться на аргументе
  `create_app(orchestrator=…)` — иначе на реальном стенде wake отвечает `rejected: orchestrator not attached`.
  Тесты, передающие оркестратор аргументом, этого пути не видят: нужен отдельный тест через
  `build_standalone_app()` с env (`NOEZEMA_DATABASE_URL`, `NOEZEMA_DATA_ROOT`, `NOEZEMA_HOST_LIB`/
  `NOEZEMA_UNIT_STATE` + свежий `publish_unit_state`, иначе Command API отвечает 423, `NOEZEMA_LLM_BASE_URL` =
  FakeLLM) и явным `async with app.router.lifespan_context(app)` — `httpx.ASGITransport` lifespan не запускает.
- Состояние узла (`system_constants.node_state`) — источник истины в БД: читать при каждой команде и при каждом
  `GET /api/v1/status` (T7.59(в)). Копировать его в память на время процесса нельзя — веб, стартовавший рядом с
  внешним wake tick, навсегда остаётся с неверным `session_running` (wake отвергается, статус врёт). В памяти
  держится только то, чем владеет сам процесс (`node.session_task`); остаточный маркер разрешает чистая
  `apps/web.api.effective_node_state`, статус показывает сырое значение БД + булев `node_state_stale_marker`.
  Гонка тика (маркер пишется до строки сессии) осознанна и этой правкой не закрыта.
- Стенд (и любой узел, где существует каталог `NOEZEMA_HOST_LIB`) воспринимается `HostStatusAdapter` как узел с
  активным host-протоколом: без свежего снимка юнитов (TTL 15 с) Command API и `POST /api/v1/questions` отвечают
  423, GET работают. Поэтому у `deploy/dev-stand` есть `noezema-dev-unit-state.timer` (5 с); «healthy by default»
  достигается только если ни каталог `NOEZEMA_HOST_LIB`, ни файл `NOEZEMA_UNIT_STATE` не существуют — так делать
  не надо: это выключает fail-closed защиту.
- Юниты стенда с `NOEZEMA_TOOL_EXECUTOR=sandbox` требуют от пользователя `User=` доступа к `/var/run/docker.sock`
  (владелец root:docker, 0660): bootstrap добавляет пользователя в группу `docker`; юниту группа видна сразу,
  интерактивной сессии нужен перелогин. Без группы preflight sandbox отказывает до старта сессии (exit 78).
- Пустая очередь вопросов на стенде — не ошибка: admitted wake доходит до выбора, кандидата нет, сессия
  `FAILED termination_reason="no_question"` (`apps/orchestrator/orchestrator.py`), дальше backoff
  (wake_schedule в config-v12 = config-v13: 60/120/240, cap 86400) и пауза узла после
  `max_consecutive_failures=3`; дальнейшие тики дают `skip`.
  Снимать паузу нужно явно (`hostctl resume-runtime` или команда `resume`). Проверено тестом
  `tests/scenario/test_dev_stand_flow.py`.
- CI (GitHub Actions) ставит `.[dev]` БЕЗ закрепления версий и в job `test` требует отдельной
  сборки sandbox-образа до pytest (шаг §6; с T7.41 фикстура образ не собирает) — расхождение с
  локальной средой даёт красные прогоны (T7.53b разбор: ruff 0.16.10 на раннере против 0.16.7 в
  `.venv`; отсутствие образа → 9 ошибок sandbox-тестов). С T7.54 закреплены: `ruff==0.16.10` в
  dev-зависимостях и шаг сборки образа в ci.yml; mypy/pytest в CI по-прежнему не закреплены
  (потенциальный источник расхождений — сверять версии CI и `.venv`). С T7.55 ci.yml закреплён:
  `ubuntu-24.04` во всех jobs, `checkout@v5`/`setup-python@v6` (Node 24), pytest `-n auto` в job
  `test`; эффект — по оценке 5–7 мин, подтверждать реальным прогоном (`gh run watch`).

## 8. Гигиена длинных сессий

- После каждого закоммиченного PR: обновить STATUS.md, отметить todo, и только потом начинать
  следующий PR — это естественная точка для сжатия контекста.
- Решение, принятое по ходу работы и не очевидное из кода (почему выбран вариант X), записывать
  в STATUS.md (раздел вехи) или ADR, а не держать только в диалоге.
- Найденный инвариант — закреплять тестом; найденную ловушку окружения — дописывать в §7.
- Отвечать пользователю по-русски; выполнять план без лишних вопросов, но пункты из §4
  «требует решения пользователя» — только после явного подтверждения.
- Новый номер конфигурации = НОВЫЙ файл в `docs/eval/` (config-v13), прежние payload'ы не переписываются
  никогда. Два разных хеша, их путают: хеш ФАЙЛА (байты JSON) и canonical-хеш payload'а
  (`canonical_sha256(json.loads(...))`) — в БД в `config_snapshots.payload_sha256` попадает **canonical**
  (`packages/memory/activation.py:374`), поэтому проверить активный снапшот по хешу файла нельзя;
  `input_budget = min(context_window, backend_context_limit) − max_output_tokens − safety_margin_tokens`
  (связь `context_window == backend_context_limit` нигде не проверяется — менять надо оба).
