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
`--no-docker-install`, `--no-units`, `--stub-executor`, `--force`, `--rotate-secrets`, `--with-tick-timer`,
`--web-host/--web-port/--user`) ставит
пакеты (в 24.04 apt-пакета `uv` нет → официальный установщик от root: `sudo env
UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh <скачанный файл>`, затем проверка `uv --version`),
venv+prod-зависимости **только через uv** (`--force` пересоздаёт venv: `uv venv --clear`), образ с одной
закреплённой меткой `noezema-sandbox:dev-stand` → `NOEZEMA_SANDBOX_IMAGE` (дефолт `:dev` и тестовый
`:test` не трогаем), Postgres 15 в docker **только на 127.0.0.1** с томом и healthcheck
`pg_isready -U noezema -d postgres` (без `-d` — спам «database does not exist»), порт выбирается ДО
записи env-файла и пишется в него (`NOEZEMA_DEV_DB_PORT`; 5432, занят → первый свободный 5433..5440;
повторный запуск переиспользует порт существующего контейнера), готовность ждётся по эндпоинту приложения
(TCP + `SELECT 1` на `127.0.0.1:<порт>`, ≤120 с), базу `noezema-dev`, миграции, активацию
config-v20 (с T7.83(б) дефолт стенда; = config-v19 ровно с одной правкой — пин `prompts.curator` → curator-v10:
абзац к правилу 7 — то же значение того же показателя за тот же период перепроверяется по `existing_claim_id`
независимо от формулировки; хостовый гейт дублей по значению работает при любой конфигурации, ADR-0033),
а config-v19 (T7.82(б)) = config-v18 ровно с одной правкой — пин `prompts.curator` → curator-v9:
факультативное поле ответа `relied_claim_ids` — честный перечень уже записанных утверждений из контекст-пака,
на которые опирается вывод, без перепроверки и без новой оценки (ADR-0032)), а config-v18 (T7.80) = config-v17
ровно с одной правкой — новый ключ `model.reasoning_by_phase`: куратору, экстрактору и верификатору ответ
запрашивается без рассуждения движка, исследователю и планировщику оно оставляется; `max_output_tokens` не поднят,
см. ADR-0030), а config-v17 (T7.77) = config-v16 ровно с одной
правкой — пин `prompts.explorer` → explorer-v9
(правило 12: прогноз до события — не независимая оценка реализованного значения; правила 1–11 байт-в-байт
explorer-v8), а config-v16 = config-v15 ровно с двумя правками — пин → explorer-v8
(двусторонний поиск: первоисточник + обязательный отдельный запрос про независимую оценку, расхождение
называется открыто) и `session_limits.max_explorer_steps` 10 → 16). В остальном байт в байт
config-v15/config-v14: пин `prompts.curator` → curator-v8 (перепроверка сохраняет якорную дату и scope,
связывает каждое использованное наблюдение), `policy.capabilities.tools` `+web.search`/`−artifact.create`,
explorer-пины v6…v7 сохранены в истории, окна EXL3 из
config-v13 сохранены: `model.context_window`/`backend_context_limit` = 131072; пороги, `claim_type_rules` и
бюджеты токенов не менялись. Прежний payload остаётся откатом:
`NOEZEMA_DEV_CONFIG_PAYLOAD=$REPO_ROOT/docs/eval/config-v19-payload.json` (canonical `4d76c000…`; canonical
config-v20 — `2b065470…`, хеш файла — другое число; откатом config-v19 был config-v18, canonical `b5605e4e…`,
а для того — config-v17, canonical `5c402f4d…`, а для того — config-v16, canonical `740ae9a1…`).
Профиль рассуждения движка — отдельная переменная окружения стенда `NOEZEMA_DEV_LLM_REASONING_PROFILE`
(дефолт `none`: без замера конкретного движка в запрос не добавляется ничего; `halogen` —
`reasoning_effort=none`; `chat-template` — `chat_template_kwargs.enable_thinking=false`; неизвестное имя —
отказ при старте), она пишется в env-файл и печатается в сводке запуска. Пропуск, если head уже не bootstrap), env-файл `/etc/noezema/dev.env` (0600, секреты не
печатаются и в `--dry-run` маскируются) и dev-юниты `deploy/dev-stand/systemd/*` — **не копии**
`infra/systemd/*`: `User=` = пользователь стенда, данные `/var/lib/noezema-dev`, группа
`noezema-dev.target` (в загрузку не ставится), tick 60 с (`TimeoutStartSec=3600`), maint 60 с
(reassessment+reconcile), unit-state 5 с. Bind веб-сервиса: `NOEZEMA_WEB_HOST`/`NOEZEMA_WEB_PORT` (дефолт
`127.0.0.1:8321`, поведение прежнее); не-loopback bind при пустом `NOEZEMA_ADMIN_TOKEN` — отказ запуска с
кодом 78 (`apps/web/bind.py`). Скрипты отказываются работать с базой вне `noezema-dev*` (в том числе
`*eval*`/`*smoke*`) и с путями `/var/lib/noezema`, `/run/noezema`. Состояние — `status.sh`, сброс dev-базы —
`reset-db.sh` (подтверждение вписыванием имени базы). Разбор и риски развёртывания: STATUS.md T7.59(б).

**Сессии на стенде запускает оператор (T7.61(б)):** `noezema-dev-tick.timer` — единственный юнит, который
сам будит узел, и `bootstrap.sh` включает его только по `--with-tick-timer`. Уже включённый таймер скрипт
ни при первом, ни при повторном запуске не отключает (только сообщает состояние + команду). Состояние видно в
`status.sh` («тик-таймер: выключен (сессии — только wake now)» / «включён»), раздел README — «Как запускать
сессии». Причина дефолта: плановый тик на пустой очереди доходит до `no_question`→FAILED и после трёх
повторов авто-паузит узел, после чего блокируется и сам тик, и «wake now» (admission), а снять паузу нужно
явно.

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
- Поиск для модели (T7.71, ADR-0028): `web.search` — только `{"query": ≤500}`, класс OBSERVATION; потолки
  curated/open_lab, sealed не выдан (`memory.search` уже даёт тот же локальный индекс). Хиты поиска никогда не
  становятся знанием: у `observation_to_evidence` нет ветки для `web.search`, поэтому evidence/sources/artifact_chunks
  не создаются — знание даёт только `research.fetch` выбранной страницы. Оформление наблюдения — чистый модуль
  `apps/orchestrator/search_view.py` (fence только вокруг внешней части, резка целыми хитами по бюджету 8000,
  литералы fence'а в чужих данных нейтрализуются; незакрытый fence = сломанная граница). Профиль open_lab даёт
  инструмент, но upstream не вызывает (`modes.py` при open_lab принудительно `searxng_url=None`) — наблюдение должно
  честно это говорить, а не молчать. Снапшот `research_proxy.searxng_url` — **корень origin** (`http://127.0.0.1:8888`),
  не `/search`: путь добавляет `apps/research_proxy/search.py::upstream_request_url`. В журнале
  `research_upstream_request` поле — `upstream_host` (только хост, без порта); лимит upstream считается по строкам
  этого же журнала (fail-closed), при исчерпании — `research_fetch_rejected{reason:"upstream_rate_limit_exceeded"}`.
  Тесты поиска не делают ни одного внешнего запроса и не трогают эталонный SearXNG (`noezema-searxng` на `.87`).
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
  Артефакты остаются sibling'ом workspace ⇒ `<NOEZEMA_DATA_ROOT>/artifacts` (постоянные `WORKSPACE_SUBDIR` и
  `ARTIFACTS_SUBDIR` + `workspace_root_from_env()` / `artifacts_root_from_env()`); env не задан → прежний путь.
  Standalone-прокси (`apps/research_proxy/main.py`) до T7.61(б) хардкодил `/var/lib/noezema/artifacts` и на
  стенде писал бы артефакты в чужой контур (и получил бы PermissionError) — теперь тот же env, что у всех.
  Внимание при тестировании: импорт `apps.research_proxy.main` БИЛДИТ приложение (`app = build_standalone_app()`),
  а `FilesystemArtifactStore.__init__` создаёт каталог — такой импорт делать только после установки
  `NOEZEMA_DATA_ROOT` на тестовый путь.
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
- Развёртывание стенда на Ubuntu 24.04 (T7.59(в), `deploy/dev-stand/bootstrap.sh`): apt-пакета `uv` там нет,
  поэтому установщик исполняется от root (`UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1`) и результат
  проверяется `uv --version`. Порт Postgres смотрим по `ss -ltn` ДО записи env-файла: занят 5432 (нативный
  PG) → первый свободный 5433..5440, он же пишется в env как `NOEZEMA_DEV_DB_PORT` и его читают status.sh и
  reset-db.sh; повторный запуск берёт порт существующего контейнера. `--force` пересоздаёт venv только через
  `uv venv --clear` и НЕ ротирует секреты, пока контейнер/том есть (ротация — `--rotate-secrets`). Готовность БД
  ждём `SELECT 1` на опубликованном `127.0.0.1:<порт>` (внутренний healthcheck обязан иметь `-d postgres`,
  иначе спам «database does not exist»), ufw только диагностируется — правил скрипт не добавляет.
  Проверка скриптов без реальной ВМ: `bash -n`, `shellcheck -S warning` и
  `tests/unit/test_dev_stand_scripts.py` (настоящие скрипты с подставными docker/ss/uv/sudo, только `--dry-run`); `shellcheck` в агентской среде `.87` не установлен — если его нет и на ВМ, из трёх проверок выполняются две (`bash -n` + pytest) и это указывается в отчёте отдельной строкой, а не «проверено shellcheck».
- Пустая очередь вопросов на стенде — не ошибка: admitted wake доходит до выбора, кандидата нет, сессия
  `FAILED termination_reason="no_question"` (`apps/orchestrator/orchestrator.py`), дальше backoff
  (wake_schedule в config-v12 = config-v13: 60/120/240, cap 86400) и пауза узла после
  `max_consecutive_failures=3`; дальнейшие тики дают `skip`.
  Снимать паузу нужно явно (`hostctl resume-runtime` или команда `resume`). Проверено тестом
  `tests/scenario/test_dev_stand_flow.py`. Внимание (T7.61(б)): пауза блокирует и плановый тик, и «wake now»
  (`_admission` первым условием даёт `REASON_PAUSED`), так что оператор, только что добавивший вопрос через
  `ask`, разбудить узел не сможет. Разбор вариантов и рекомендация (не считать `no_question` отказом узла в
  `record_session_result`) — STATUS.md T7.61(б) п.7; кода пока нет.
- CI (GitHub Actions) ставит `.[dev]` БЕЗ закрепления версий и в job `test` требует отдельной
  сборки sandbox-образа до pytest (шаг §6; с T7.41 фикстура образ не собирает) — расхождение с
  локальной средой даёт красные прогоны (T7.53b разбор: ruff 0.16.10 на раннере против 0.16.7 в
  `.venv`; отсутствие образа → 9 ошибок sandbox-тестов). С T7.54 закреплены: `ruff==0.16.10` в
  dev-зависимостях и шаг сборки образа в ci.yml; mypy/pytest в CI по-прежнему не закреплены
  (потенциальный источник расхождений — сверять версии CI и `.venv`). С T7.55 ci.yml закреплён:
  `ubuntu-24.04` во всех jobs, `checkout@v5`/`setup-python@v6` (Node 24), pytest `-n auto` в job
  `test`; эффект — по оценке 5–7 мин, подтверждать реальным прогоном (`gh run watch`).
- Гонка двух точек входа (T7.61(а), найдено на стенде .92): admission `decide` видит только **committed**
  строки `sessions`, а phase 1 держит строку запущенной сессии открытой до COMMITTING ⇒ «wake now» (веб) и
  запланированный `noezema-dev-tick.service` (отдельный процесс) получают wake одновременно. Маркер
  `node_state='session_running'` в admission не участвует и не должен (остаточный маркер от убитого юнита
  клинил бы узел). Взаимное исключение даёт `apps/orchestrator/node_guard.py`: session-level advisory lock
  `pg_try_advisory_lock(hashtext(node_session_lock_name(owner)))` на **отдельном** соединении (`NullPool` +
  AUTOCOMMIT), удерживаемое всю сессию; при гибели соединения замок снимает PostgreSQL — убитый юнит узел не
  клинит. Ключ считает Postgres (`hashtext`): Python `hash()` солится PYTHONHASHSEED ⇒ каждый юнит взял бы свой
  замок, и ничего не исключалось бы. Замок не участвует в каноническом порядке блокировок §5.2.2 (живёт вне
  транзакций), не lease и не fencing (§8.7.2, §5.2.3) и не заменяет список admission §5.2.1 — только окно
  decide→start. Не работает за transaction-mode pooler (pgbouncer); прямого pooler в проекте нет — при его
  появлении guard менять на долговременную строку с lease.
- **Правило:** любая новая точка входа, запускающая `run_session` (wake-tick, веб «wake now», eval-run, ручной
  `python -m apps.orchestrator`, будущие команды и юниты), обязана взять `NodeSessionGuard` ДО записи маркера
  `session_running` и отдать его в `finally` на всех выходах: нормальный конец, исключение внутри сессии,
  отмена задачи, fail-closed отказ (снятие идемпотентно). Порядок: admission → замок → маркер → сессия → учёт
  исхода → снятие. Занятый лейн — skip с `REASON_SESSION_IN_PROGRESS` (`wake-tick: skip (session_in_progress)`,
  exit 0; веб-ответ «a session is already running» + `wake_reason`), не ошибка и не failure-учёт. Тесты:
  `tests/scenario/test_node_session_exclusion.py` (репродюсер сквозь реальные точки входа) и
  `tests/scenario/test_node_session_guard.py` (release на всех выходах, `pg_terminate_backend`, разные
  `node_owner`).
- Тесты статуса: при опросе `/api/v1/status` не требовать условие «для каждого снимка» — синхронизироваться
  барьерами (T7.62, CI run 37290125308). Хвостовое окно между завершением задачи сессии (`node.session_task`)
  и сбросом маркера `_record_session_outcome` (done-callback заводит её ОТДЕЛЬНОЙ задачей в `apps/web/api.py`)
  законно даёт `session_running` + `node_state_stale_marker=True` при отсутствии незавершённой строки `sessions`
  — это не дефект: команды решает `effective_node_state` (→ `idle`). Снимок «пока веб владеет сессией» получают
  удержанием сессии (event-гейт в `StubToolExecutor.execute` — тот же event loop), видимость COMMITTING-строки
  пинят гейтом вокруг `commit_prepare` (окно = phase-1 commit … терминальный commit). Временные ожидания —
  только потолки против зависания. Тесты: `tests/scenario/test_web_node_state_db_truth.py` (5, incl. тест
  хвостового окна и тест «shutdown не бросает учёт исхода»), `tests/scenario/test_dev_stand_flow.py`.

- Teardown scratch-БД и асинхронное закрытие соединения (T7.63): `DROP DATABASE` чинящей тест фикстуры падает с
  «database … is being accessed by other users», если в базе осталось **открытое соединение незавершённой
  async-задачи**. Две стороны: (1) `AsyncEngine.dispose()` закрывает только **idle**-соединения пула — сессию,
  оставленную живой задачей, он не закрывает; поэтому shutdown обязан дождаться свои незавершённые задачи учёта
  (`apps/web/api.py::_await_with_ceiling`: сначала дождаться отменённой `session_task` — её done-callback и
  ЗАВОДИТ задачу `_record_session_outcome`, — затем `node._resets`, и лишь потом снятие гарда и `dispose`;
  потолок 20 с + warning с именем незавершённой задачи, а не тихий бросок). (2) Фикстурный DROP идёт по схеме
  plain → короткое ожидание → `DROP DATABASE … WITH (FORCE)` (PostgreSQL 15), и каждый форс **называет**
  оставшиеся бэкэнды (pid/state/последний запрос) в warning: форс не должен прятать реальную протечку.
  Признак по подписи соединения: сессия sessionmaker — последний запрос `COMMIT;` при непустом `xact_start`;
  NullPool/AUTOCOMMIT-соединение гарда — `pg_try_advisory_lock/unlock` при пустом `xact_start`.
- `pg_locks` (как и `pg_stat_activity` в части advisory-замков) — **кластерное** представление: advisory-замок с
  тем же `hashtext`-ключом, взятый в другой базе (у каждого xdist-воркера своя scratch-БД), виден из текущей
  базы (строки несут `database`, `classid=4294967295`, `objid=hashtext(name)`). Любой тестовый вопрос «сколько
  соединений держат наш замок» обязан фильтровать `database = (SELECT oid FROM pg_database WHERE datname =
  current_database())`; без фильтра утверждение флакает под `-n auto` и тот же список pid можно отправить в
  `pg_terminate_backend`, убив соединение чужого воркера (T7.63, `tests/scenario/test_node_session_guard.py::_lock_holders`).
- Подписи интерфейса (T7.64, ADR-0026): каждое значение пользовательского enum — а также строка отказа
  Command API, причина пропуска запуска и тип события ленты — обязано иметь подпись в `apps/web/labels.py`.
  Тест полноты `tests/unit/test_web_labels.py` берёт значения из самого кода (перечисления домена,
  `REASON_*`/`WAIT_*` планировщика, константы `apps/web/host_status.py`, `api.NODE_STATES`, строки отказа,
  вынутые регуляркой из `apps/web/api.py`, итоги фиксации из `FinalizeOutcome`) — новое значение без подписи
  краснит тест, молча добавить enum нельзя. Тексты проверяются тем же тестом: ≤40/≤160/≤120 знаков, непустые,
  без `§`, номеров задач плана (`T7.xx`), кодов snake_case и uuid/sha-подобных строк; неизвестное значение
  уходит на запасной путь (label = сам код, пустые hint/action) — выдумывать подпись запрещено. Бейдж
  надёжности — `apps/web/reliability.py`: это перевод уже вычисленной оценки rules engine, пороги берутся из
  effective-снапшота (`claim_type_rules`), запасная таблица модуля сверяется с `docs/eval/config-v13-payload.json`
  тестом; presentation-слой grade и epistemic_status не назначает и не повышает (API-поля только добавляются).
- Простые страницы простого режима (T7.65): весь человеческий текст — из серверных подписей. В разметке
  `/` и `/answer/<id>` и в строковых литералах их JS запрещено писать код перечисления (`session_running`,
  `goal_reached`…), `§`, номер задачи плана, uuid/hex; тест `tests/scenario/test_web_answer_pages.py`
  сканирует статику вне `<script>` плюс quoted-литералы JS и краснеет на любом из этого (своей проверкой
  красноты он же доказывает, что сканер не пустой). Имена свойств JSON (`state_label`) — ключи ответа API, а
  не надпись на экране: в скан они не входят, экранировать их не надо. Прежняя главная закреплена тестами
  (`test_web_questions.py::test_main_page_carries_the_ask_form_and_the_queue_table`, `test_web_mvp.py`,
  `test_web_knowledge.py`) и живёт на `/engineer` без изменений: менять закрепленные строки можно только с
  обоснованием в STATUS.md, а не подгоняя тест под новую страницу.
- Слово «проверено» (T7.65) имеет один источник — бейдж надёжности при `reliability.level == verified` и
  заголовок строки подтверждения `verification_lead` («как проверено» ровно при том же уровне, иначе
  «чем подтверждено»). Страница не имеет права формулировать это сама (в её исходниках этого слова нет), а
  тест карточки требует, чтобы во всей карте ни одна строка не утверждала проверку там, где нет головы оценки
  `current`. Отрицание «Не проверено» утверждением не считается: проверка делает это негативным lookbehind
  (`(?<!не )\bпроверен`) — без него тест краснел бы на честном бейдже pending-оценки.
- Шаги работы в карточке ответа (T7.65) собираются из `audit_events` **по `sequence`** и только из
  реально выполненных действий: `action_started` без парного `action_completed{ok:true}` (обрыв, отмена,
  фаза 1 без COMMIT) и `action_failed` в шаги не пишутся — они дают замечание «часть шагов не удалась: N».
  Подпись действия берётся из категории `action_tool`, значения которой тестируются из реестра
  `packages/policy/tools.all_tools()`: новый инструмент без подписи краснит полноту словаря. Словарь
  обязан покрывать объединение реестра и возможностей снапшота правил (`test_action_names_come_from_the_tool_registry_and_snapshots`):
  в config-v13 есть `artifact.create`, которого в реестре нет и который исполнитель не выполняет («unknown
  tool»), — подпись нужна, чтобы лента не осталась без подписи. Действию, подписи которого в словаре нет,
  имя не выдумывается: используется нейтральное «действие без названия» (запасной путь `describe` вернуть
  сам код сюда не попадает — `answer.known_label` отдаёт пустую строку). Утверждение «сеть закрыта»
  допускается только если это подтверждает снимок правил этой работы (`policy.capabilities.network == "none"`);
  без сессии работает нейтральная формулировка.

- Перепроверка существующего claim'а и гейт staging (T7.73, уточнение ADR-0018): `validate_against`
  (`apps/orchestrator/orchestrator.py:2180`) выполняется ДО подмены типа якоря (:2201–2259), поэтому требование
  `as_of` для `temporal_fact` проверялось по типу из предложения модели, а оценка потом считалась по типу якоря —
  на датless-перепроверке это гарантированно давало `as_of_missing` и падение E3/0.75 → E1/0.30. Теперь гейт не
  требует даты у операции с `existing_claim_id`; дату, `date_anchor` и assessed_scope якоря сохраняет хост
  (`packages/memory/reverify.py`: `resolve_reverify_reference`, `merge_reverify_scope`), а непустая дата предложения
  при датless вопросе молча не подставляется — это revision (в аудит `claim_reverified.as_of_conflict`). Вторая
  половина той же ловушки: `existing_claim_id` отвергается fail-closed, если якорь не попал в контекст-пак
  (`curator_rejected: "claim[0]: reverify reference <id> is not visible to the session"`,
  `curator_reject_kind: "reverify_unresolved"`), а пакет собирается лексикой FTS — формулировка вопроса
  перепроверки обязана цитировать statement якоря, иначе сессия проходит без anchor'а и без доказательств
  (первые версии репродюсера `tests/scenario/test_reverify_temporal_scenario.py` краснели именно на этом).

- Витрина «вопрос → утверждение» (T7.74): перепроверка и дедуп-повтор НЕ заводят строку `claims`
  (`packages/memory/service.py`: ветки `existing_claim_id` и reuse берут существующий claim, его
  `created_in_session` — сессия **прежнего** вопроса), поэтому выборка утверждений только по
  `claims.created_in_session` слепа к ответу нового вопроса (на стенде это читалось как «Ответ не
  записан»). Долговременная связь «эта сессия оценила этот claim» — `claim_assessments.claim_id +
  created_in_session` (тот же источник, что `packages/evaluation/gates.py::_gate_reuse`; worker- и
  активационные оценки имеют `created_in_session IS NULL` и в карточку не попадают). Отдельного
  события дедуп-повтора в закрытом `AuditEventType` нет: отношение `reverified`/`reused` различается
  наличием или отсутствием `claim_reverified` для пары (claim, session) при уже доказанном факте
  оценки этой сессией; текстовые эвристики (statement, публичные summary) запрещены. Окно связи
  общее для карточки и списка (`apps/web/answer.py::touched_claims_cte`, использует и
  `apps/web/questions_view.py`): иначе «Мои вопросы» и карточка показывают разное; бюджет списка
  (≤4 SELECT, без N+1) закреплён тестом.

- `registrable_domain` для `.gov.ru` (T7.75): в PSL-списке `packages/memory/independence.py`
  (`MULTI_PART_SUFFIXES`) нет записи `gov.ru`, поэтому
  `registrable_domain("https://rosstat.gov.ru/") == registrable_domain("https://minfin.gov.ru/") == "gov.ru"` —
  все ведомства с одним `.gov.ru` сворачиваются в одну группу. Всё, что решает «это страница самого
  первоисточника?», обязано сравнивать **метку хоста**, а не registrable domain: в словаре и в
  разрешении родителя (`apps/research_proxy/source_attribution.py::is_home_host`,
  `apps/research_proxy/service.py::_resolve_primary_source`) так и сделано. Тот же подводный камень
  валит тесты независимости: хосты `interfax.example.ru / expert.example.ru / ria.example.ru` дают
  ОДИН registrable domain `example.ru` (PSL не знает `example.ru`), и группы сливаются сами по себе
  — фикстуры обязаны брать заведомо разные домены (`interfax.example / expert.example`) и это
  проверять. Менять PSL-список нельзя молча: это порог группировки, влияющий на прежние прогоны.
- `split` не отменяет parent-склеивание (T7.75): коррекция `source_graph_corrections{split}` снимает
  только прямые relation-основания (edge/correction), а `parent_source_id`, общий домен, общий hash и
  совпадение текста — алгоритмические факты (`packages/memory/independence.py:26–33`, :340–356).
  Ошибочное склеивание производного источника лечится только изменением данных (обнулить
  `parent_source_id`) — это вариант C ADR-0029 и он не реализован. Значит детектор производности
  обязан оставаться консервативным: ложный пропуск безопаснее ложной склейки, а «исправим потом
  коррекцией» — не план.

- Модель не видела ни схем инструментов, ни своего бюджета (T7.76). `model_tools_schema` (полные схемы
  аргументов из реестра) до этой задачи **использовали только тесты**: в контекст шага исследоватора уходил
  один список имён инструментов, а хостовый текст протокола перечислял поля кураторского конверта
  (`claim_type`, `as_of`, `scope`, `dependencies`, `search_statements`, `evidence_links`) так, что читались
  как аргументы инструмента. Отсюда стендовые отказы: `question.create` с `search_statements`
  («argument ('search_statements',): Extra inputs are not permitted»), раньше — `memory.search` с лишним
  `limit` и три отказа `artifact.create`; **отклонённое действие тоже стоит один шаг**, и на 10 шагах такие
  повторы съедали половину бюджета, а лимит `session_limits.max_explorer_steps` модели вообще не
  сообщался. Правка — аддитивная: `apps/orchestrator/tool_context.py` рендерит схемы из реестра
  (`get_tool(name).args_model.model_json_schema()`, тот же источник истины, что шлюз; копировать схемы в
  текст нельзя), блок бюджета шага и отметка повторного чтения адреса. Ловушки самой правки: потолок
  (`TOOL_SCHEMAS_CHAR_LIMIT`) применяется к склеенным строкам инструментов и снимает **целые строки** —
  обрезанная посередине схема уже не схема; надбавку обязано сопровождать увеличение
  `EXPLORER_CONTEXT_BUDGET` (= `RESEARCH_CONTEXT_BUDGET + 8_000 + TOOL_CONTEXT_ADDENDA_CHARS`), потому что
  контекст-пак режется по свежести (T7.10) и без роста тихо выбрасывал бы наблюдения поиска. Видимость —
  не ослабление: `extra="forbid"`, `policy_engine.evaluate` и `tool_schema_hash(allowed_tools)` не тронуты
  (текст контекста в хеш схем не входит), новый «охранник» поверх реестра не добавлялся.
- Тестовые ловушки двустороннего поиска (T7.76): (1) фикстура «официальная сторона» обязана ставить
  первоисточник на **home host словаря атрибуции** (`rosstat.gov.ru`), иначе пересказ склеивается с заново
  созданным якорем и вторая группа независимости появляется сама — тест был бы зелёным при дефекте
  (`rosstat.example` именно так и вела себя); (2) отметка повтора адреса попадает в контекст **следующего**
  шага: проверять её нужно на `prompts[3]`, а отсутствие — на `prompts[:3]`; (3) имя инструмента живёт в
  payload'е `action_started`, а `action_completed` несёт только `{action_id, ok, error, data}` — считать
  выполненные действия надо связкой по `action_id`; (4) бейдж надёжности — у строки утверждения
  (`card["claims"][i]["reliability"]`), а не в `card["result"]`.
- Семантика повторного вызова закреплена тестами и «по вкусу» не правится (T7.76): гейт
  `TOOL_REPEAT_DENY_LIMIT = 2` считает **буквально** совпадающие пары (tool, аргументы) — повтор с другой
  формулировкой аргументов ему не повтор; поведение второго `research.fetch` того же URL закрепляет
  `tests/scenario/test_research_provenance.py::test_repeated_tool_call_is_denied_after_limit`. Поэтому вторая
  сторона двусторонней проверки — ДРУГОЙ адрес и ДРУГОЙ запрос, а не второй заход на ту же страницу: хост это
  объясняет отметкой, а не новым отказом. Канонический ключ адреса (`fetch_url_key`) используется только
  отметкой; сделать его жёстким гейтом — отдельная задача (кандидат вместе с лимитами fetch, T7.72).

- Типографическая слепота детектора и ловушка фикстур (T7.77): живые русские страницы набраны неразрывными
  пробелами (NBSP U+00A0 — сберсибовское «По\xa0данным Росстата», узкий U+202F), софт-гифеном U+00AD и
  ZW-символами; шаблоны `ATTRIBUTION_TEMPLATES` требовали ровно одного ASCII-пробела — вся страница
  пересказа осталась `no_value_attribution`, а утверждение получило E3 с ложной формулировкой. С v2
  (`host-source-attribution-v2`) типографика нормализуется над **копией** текста на входе детектора:
  хранимые артефакты и их хеши не меняются, `MAX_SCAN_CHARS` считается по копии, `basis_fragment` — из
  копии (невидимые символы видны оператору как пробелы). Одинарный `\n` теперь мягкая граница (фрагменты
  шире) — окно 120, вето `OWN_ASSESSMENT` и отказ при двух первоисточниках не ослаблены. Ловушка тестов:
  фикстура дефекта обязана содержать Unicode прямо в литерале (`"По\xa0данным …"`); если редактор/копипаст
  заменит NBSP на обычный пробел, фикстура станет зелёной при сломанном детекторе. Переатрибуция прошлых
  решений — команда `research-reattribute` (`python -m hostctl.cli research-reattribute --since`; её идемпотентность закреплена
  `tests/scenario/test_research_reattribute.py`): окно — журнал `research_fetch_completed` с
  `payload->>'attribution_method'='host-source-attribution-v1'`, помечаются только непомеченные строки,
  пересчёт — каскад `apply_source_graph_change` + рабочий переоценки (прямых UPDATE нет).
- Производность уровня значения и её носитель (T7.78, ADR-0029): решение «чей пересказ читает ЭТО
  утверждение» живёт на улике — `evidence.scope.value_attribution`. Ключ в конверт staging-операции `evidence`
  кладёт хост (`apps/orchestrator/orchestrator.py::_value_attribution` :2127, вызов из `_curator` :2411–2438),
  в `evidence.scope` его переносит commit boundary (`packages/memory/service.py:917–944`). Ловушки правки:
  (1) перескопирование scope по T7.17 (`service.py:998`) обязано нести этот ключ, иначе следующий коммит того
  же утверждения тихо сотрёт решение — носитель `packages/memory/scope.py::carry_value_attribution`, факт
  закрепления закреплён тестом повторного коммита; (2) дедуп-повтор той же улики (`identity_hash` решения не
  включает) дописывает решение к существующей строке, а не плодит вторую; (3) загрузчик снимка меняет только
  ВХОДЫ `group_source_graph` (`packages/memory/source_graph.py:57`, `:148`) — эффективный родитель добавляется
  туда, где страничный указатель отказался; страничный родитель старшего, а у одной улики одного утверждения
  два разных первоисточника — родителей нет вовсе; алгоритм группировки, `TEXT_OVERLAP_THRESHOLD` и PSL-список
  трогать нельзя: это смена шкалы независимости (вариант D); (4) число обязано считаться значением только
  рядом с единицей измерения (`_UNIT_TAIL_CHARS = 8`; «1,2 п.\u00a0п.» — тоже значение), иначе годы 2025/2024
  сойдут за значения и склеят несвязанное; (5) ловушка фикстуры: неоднозначная страница обязана быть длинной
  (навигация + две атрибуции разных первоисточников) и набранной NBSP прямо в литерале, а прочитанный
  первоисточник — на home host словаря (`rosstat.gov.ru`), иначе «родитель» в тесте — заново заведённый якорь
  и тест зелёный при дефекте; независимые сайты — заведомо разные registrable domains (AGENTS §7, T7.75).
- Причины оценок рабочего переоценки (T7.77): reassessment-worker НЕ пишет `CLAIM_ASSESSED` (это язык
  staging-пути, `packages/memory/service.py`); у таблицы `claim_assessments` колонки причин нет вовсе.
  Grade/status/причины рабочей оценки — в payload события `reassessment_job_completed`; тестам читать
  последнее по нему (`ORDER BY occurred_at DESC LIMIT 1`). Хронология по `audit_events.sequence` действует
  только внутри одной сессии (sequence per-session) — кросс-сессийные ORDER BY sequence невалидны.
- TLS research-proxy и инертный `SSL_CERT_FILE` (T7.77): httpcore строит контекст сам
  (`create_default_context()` + certifi, `httpcore/_ssl.py`) и переменные окружения OpenSSL не читает —
  подстановка `SSL_CERT_FILE` на стенде не помогла (`rosstat.gov.ru` → CERTIFICATE_VERIFY_FAILED). Единственный
  штатный способ дополнить доверие egress — `NOEZEMA_RESEARCH_EXTRA_CA_FILE` (модуль `apps/research_proxy/tls.py`):
  PEM **добавляется** к certifi-ядру, `check_hostname`/`CERT_REQUIRED` не ослабляются ни в каком режиме; без
  переменной FetchClient строит пул веткой без `ssl_context` (прежнее поведение); битая переменная —
  `ResearchTlsError` при сборке FetchClient и `ResearchProxyService` (fail-closed, узел не поднимается).
  TLS-тесты (`tests/unit/test_research_tls_extra_ca.py`) генерируют сертификаты через `openssl` CLI
  (trustme/cryptography в зависимостях нет) и без него скипуются — скипы сообщаются отдельной строкой.

- Точный якорь окна доказательства (T7.79, ADR-0011 доп.): поиск ведётся по **свёрнутому** тексту
  (пробелы, NBSP U+00A0/U+202F, софт-гифен U+00AD, ZW, BOM), но окно вырезается из **исходного**
  нормализованного текста: позиции возвращает карта `_fold_with_map`. Потеря карты = тихая порча
  фрагмента (из источника исчезает типографика, меняется normalized-вид), а тест на NBSP зелён при такой
  ошибке, если сверённый текст сравнивается сам с собой — фикстура обязана проверять NBSP **внутри
  вырезанного окна**. Год-одиночка якорем не считается (дата есть в навигации каждой страницы), число не
  матчится внутри большего числа («5,6» не цепляет «15,6%»). Порядок сигналов второго окна зафиксирован:
  терминальное окно T7.16 → цитаты исследователя → значения вопроса → общий value-якорь T7.22;
  перестановка (например цифры раньше цитат) возвращает стендовый промах — ранний числовой блок
  перетягивает окно (замеры STATUS.md T7.79 §1).
- Текст модели в окне доказательства — **только поисковый сигнал**: `researcher_quote_terms` принимает
  фразу, лишь если она дословно есть в этом же источнике (1..3 вхождения, потолок 8 цитат, кавычки или
  придаточное от 60 знаков). Репродюсер обязан проверять обе ветки (цитата есть → фраза появляется во
  фрагменте; цитаты нет → фрагмент не меняется и текста модели в нём нет), иначе тест зелёный при дефекте,
  а «дотягивание» выдуманного не заметить. Payload улики и идентичность §14.3 от выбора окна не зависят
  (`test_assertion_text_budget_is_explicit_and_identity_ignores_text`) — новый payload-ключ не требовался.
- Ноль утверждений ≠ «ответ проверен» (T7.79): terminal вопроса `verified` требует claims>0 в
  оркестраторе **и** `applied_claims > 0` на границе финальной транзакции (`packages/domain/services/commit.py`,
  fail-closed по staging-операциям: перепроверка и дедуп-повтор считаются применёнными — T7.74). Проверить
  итог по БД можно только по новому аддитивному ключу `question_state` в payload'е
  `commit_attempt_committed`: в доменных таблицах терминал виден лишь строкой `questions`. Тесты, ждавшие
  `verified` при нуле применённых утверждений (например plan-fallback, где хост отклоняет конверт куратора
  `evidence_link[0]: index 0 out of range`), закрепляли сам дефект — менять такое ожидание можно
  только с обоснованием в STATUS.md и с проверкой, что ветка «есть утверждение → verified» осталась зелёной.
- Тест на упрощённом вопросе скрывал дефект окна (T7.79a): короткий вопрос («публикуется ли точное значение
  5,59%?») меняет плотность терминов так, что терминальное окно само накрывает фразу со значением — промах
  исчезает, и репродюсер зелёный при дефекте. Воспроизведение обязано подавать **полный текст вопроса из
  стенда** и **дословный нормализованный текст страницы** (content-addressed фикстура
  `tests/fixtures/artifacts/21/218e9a1ea…`, её SHA входит в `test_artifact_integrity`). Тот же класс маскировки
  на синтетике: термино-плотный блок, стоящий рядом с ключевой фразой, тоже накрывает её окном — нужен
  нейтральный разрыв (замер: ≥20 предложений), а для HTML-страницы он другой, чем для plain-text, потому что
  `normalize_content` сдвигает offsets.
- Складка `_fold` склеивает ряды цифр (T7.79a): неразрывные пробелы и переводы строк выбрасываются, поэтому
  колонки таблицы «8,0 \n 7,7 \n 6,6 \n 5,6» в свёрнутом тексте выглядят одним непрерывным рядом цифр. Граница
  числа проверяется по соседним символам **исходного** текста на raw-границах из `_fold_with_map`
  (`_numeric_boundary_ok`): собственная ячейка «5,6» принимается, «15,6», «5,64» и «5,60» — нет (слева или справа
  стоит цифра). Проверять границу по свёрнутому тексту — тихий отказ значения, набранного таблицей.
- У точного якоря обязана быть ветка вытеснения (T7.79a): сигнал, найденный в источнике, чьё единственное окно
  пересекается с терминальным, фиксирует своё окно, а освобождённый слот занимает общий value-якорь T7.22;
  прежний отказ по пересечению выбрасывал такой якорь целиком, и слот доставался хронологии или навигации.
  Одновременно держать: значение уже покрыто терминальным окном → вытеснения нет
  (`test_value_already_inside_the_term_window_is_not_duplicated`); сигналов нет → прежнее поведение T7.22 без
  изменений (`test_overlap_case_without_signals_loses_the_value`); окон ≤ 2 и каждое ≤ бюджета
  (`test_real_cbr_page_displacement_keeps_the_window_count_and_budget`). Классы точных сигналов: цитата →
  десятичные значения (специфичное раньше: «5,59» до «5,6») → даты д.м.гггг; даты опущены ниже значений по той
  же причине, по которой год-одиночка значением не считается (дата есть в навигации почти каждой страницы).
  `question_value_terms` сохраняет порядок упоминания: сортировка — свойство выбора окна, а не данных наблюдения.
- Дополнение к пункту про локальные reasoning-модели (T7.80, ADR-0030): **у halogen-flash-next комната ответа
  фиксирована** — рассуждение закрывается признаком `reasoning_closed_by="answer_room"`
  (`usage.completion_tokens_details.reasoning_closed_by`) примерно за ~1000 токенов до потолка, поэтому
  подъём потолка ответа (`model.max_output_tokens`) обрезку длинного структурированного JSON не лечит:
  срез лишь переезжает. Замер (сессия `1d0886fa`, .92): три ответа куратора
  ровно по 8192 completion tokens, «Unterminated string … char 3350». Лечит другое: `reasoning_effort: "none"`
  (halogen) и `chat_template_kwargs {"enable_thinking": false}` (llama.cpp Qwen3.x) дают `reasoning_tokens = 0`
  и валидный JSON за доли секунды; `reasoning_effort: "low"` и `thinking_budget` движок **игнорирует** — проверять
  ключ надо одним запросом до активации профиля, а не подбором в проде.
- Повтор идентичного запроса при температуре 0 бессмыслен (T7.80, ADR-0030): сервер отвечает идентично, поэтому
  прежний цикл «три попытки одной схемы» при обрезке тратил ~450 с и три слота движка на три копии одного
  оборванного JSON. Повтор обязан менять сам запрос (выключенное рассуждение), иначе это не ретрай, а повторение
  отказа; менять температуру «чтобы получилась другая длина» нельзя — ломает сравнивость замеров ADR-0011 §7.
  Обрезка (`finish_reason=length` либо упор в `max_output_tokens`) — отдельный исход `LLMTruncatedResponseError`,
  а не схемная осечка: обрезанный документ отказывается, даже если он чудом оказался синтаксически целым, и каждая unusable-попытка
  обязана оставить строку `model_runs` (`output_schema_valid=false`) — иначе «почему куратор молчит» читается
  только из дампа HTTP.

- Улики `source_assertion` реселектируются после исследования (T7.82(а), ADR-0011 §12): окно выбрано при
  fetch по rationale НА ТОТ МОМЕНТ, а перед VERIFYING хост заново выбирает окна тем же модулем assertion_window
  по ФИНАЛЬНОЙ rationale — тесты, asserting `payload["assertion_text"]`, обязаны закладывать сигнал
  «исследователь уже прочитал ключевой факт» (цитата/значение), иначе зелёный тест фиксирует старое
  промахнувшееся окно; без единого точного якоря фрагмент остаётся оконным как при fetch. Правка одна —
  `payload["assertion_text"]`; identity/дедуп улики (§14.3) не тронуты, на staging-операции реселекция не влияет.
- Опора ответа на прежнее знание (T7.82(б), ADR-0032): `relied_claim_ids` разрешаются хостом ТОЛЬКО по строкам
  контекст-пака `[c:<uuid>]`; неразрешимый id — честный отказ в payload того же `claim_created`
  (`relied_claims_rejected`) и НЕ валит предложение (в отличие от fail-closed гейта перепроверки T7.73), поэтому
  выдуманный id куратора — не провал сессии, а записанная причина. Никакого нового AuditEventType/migration:
  ключи payload'а появляются только непустыми — payload старых сессий неизменен байт в байт. Карточка берёт ids
  из payload (витрина не переизобретает разрешатель); `touched_claims_cte` никогда не выдаёт отношение `relied` —
  ранги created/reverified/reused прежние, полноту новой подписи проверяет test_web_labels.py.

- Дубль по значению и гейт кураторского предложения (T7.83, ADR-0033): byte-exact дедуп T7.9 слеп к
  перефразе — новый claim с тем же `claim_type`, равным НЕПУСТЫМ множеством десятичных значений и
  покрытым периодом он не ловится. Значение = maximal-последование `5,59`-подобных токенов; границы числа
  зеркалят `_numeric_boundary_ok` (ADR-0011): `5,6` ≠ `5,59`, «105,59» — одно число и никогда вхождение
  «5,59», целые без разделителя и годы значениями не считаются; неразбираемый токен — молчание про всю
  формулировку. Период: годы операции (текст + `as_of`) непустые и ⊆ годов кандидата (текст + `as_of`).
  Гейт в `_curator` после T7.34-гейта перепроверки и разрешения relied-id, до стейджинга; кандидаты — id
  контекст-пака ∩ головы оценки снапшота сессии; операции с явным `existing_claim_id` не трогает никогда.
  Ровно один строгий кандидат и все связи `supports` → staged-копия получает `existing_claim_id` (оценка
  монотонна, якорь хранит reverify.py); кандидатов ≥2 / есть counters / нет supports / кандидатов нет —
  операция остаётся как предложена. Новых enum/подписей/миграций нет: решение — ключ `value_duplicates` в
  payload уже существующего `claim_created`, только при непустом списке (тест равенства наборов ключей);
  массив `claims` аудит-события НЕ переписывается (`existing_claim_id: null` на месте, подменённый id живёт
  только в staging-операции). Уже записанные пары задним числом не склеиваются: знание не удаляется, а
  фиктивный `claim-dispute` ради косметики подделал бы оценку (STATUS.md T7.83 п.7). Тесты сначала красные:
  unit — отсутствующий импорт чистой функции, сценарий — второй claim с тем же значением на карточке.
- **Значение ≠ показатель (T7.83a, ADR-0033 §7):** равные `claim_type` + множество значений + период НЕ
  делают два утверждения одним фактом — «13,7%» инфляционных ожиданий гейт T7.83 склеил с «13,7%» средней
  ключевой ставки, то есть пристегнул улику чужого показателя к чужому утверждению (и поднял бы его оценку).
  Склейка теперь требует обоих условий: кандидат объявлен куратором в `relied_claim_ids` (хост не решает за
  модель, какой из видимых claim'ов она имела в виду; счёт «ровно один кандидат» берётся ПОСЛЕ этого фильтра —
  иначе неопорный совпаденец создавал бы ложную неоднозначность), и это тот же показатель. Метки показателя
  (`SCOPE_METRIC_KEYS` = metric/indicator/показатель/объект) приоритетнее формулировок: есть у обеих сторон →
  обязаны совпасть после нормализации; у кандидата метка читается из `assessed_scope` текущей головы снапшота —
  колонки `scope` на таблице `claims` нет вовсе (`ORMClaim`), а host-scope-v1 (`packages/memory/scope.py`) метки
  показателя не несёт, поэтому на свежих стендовых claims решает словарь слов. Ловушки словаря: `_STOP_WORDS`
  сравниваются **только точно** (префикс «и» убил бы «инфляцию», «в» — «вклады»), префиксные основы
  `_STOP_STEMS` сравниваются префиксом по цельным корням служебных слов (кратчайшая — «год», она же
  снимает и «город…»: город — не показатель),
  месяцы отсекаются как маркеры периода (`_MONTH_PREFIXES`); основа слова — **префикс** фиксированной длины
  `INDICATOR_STEM_LEN = 5` (суффикс в русском меняется сильнее начала, а длиннее 5 — разные показатели сливаются);
  отношение пересечения к объединению сравнивается точной дробью (`fractions.Fraction`, без float), порог
  `INDICATOR_OVERLAP_MIN = Fraction(3, 5)` — нижняя граница реальной пары фикстуры .92 (3/5) над ближайшим
  ложным кандидатом («ставка по вкладам» ↔ «ключевая ставка», 1/2). Территория — часть имени показателя:
  `_TERRITORY_GROUPS` замкнут (12 групп), конфликт засчитывается только если территории названы **обеими**
  сторонами, поэтому неполнота словаря не может создать ложную склейку; без словаря территорий «наблюдаемая
  инфляция России» ↔ «…Беларуси» дают ровно 3/5 — одного порога не хватило бы. Типографика та же, что у
  атрибуции T7.77: NBSP U+00A0 и узкий U+202F → обычный пробел, софт-гифен U+00AD убирается, NFKC, ё→е; без
  этого `«декабрь\xa02025»` даёт выброшенное слово и перекрытия не хватает. В любом сомнении — `kept`: лишний
  дубль на карточке дешевле чужой улики у утверждения.

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
- Операторский спор утверждения (T7.81, ADR-0031): единственный санкционированный спекой lever оператора над
  независимостью — строка `source_graph_corrections` + существующий каскад `apply_source_graph_change`, а дальше
  оценку считает rules engine. Три подводных камня: (1) **голова самоуничтожается** — прямой перевод головы в
  `pending`/`invalid` без изменения фактов worker вернёт в `current` с прежней оценкой на ближайшем maint-тике
  (60 с на стенде): «снятое руками» знание держится один тик; (2) идемпотентность по естественному ключу
  `(actor, from_source_id, to_source_id, kind, rules_version)` означает, что повтор **тем же актором** — это
  replay (200, `replayed=true`), а отказ «источники уже объединены» возможен только для другого актора: тест
  «второй оператор не может…» обязан звать сервис с другим `actor`, иначе получит зелёный повтор; (3) сравнение
  адреса оператора с `sources.canonical_uri` — **без схемы** (`normalize_uri` приводёт и хост, и порт, и query):
  со схемой в сравнении «тот же источник» превращается в «адреса нет среди источников», то есть честный отказ
  подменяется ложным. Бейдж надёжности при проверке витрины читать у строки списка
  (`/api/v1/knowledge/claims` → `reliability`), а не у голов `claim_detail`: у heads его нет (T7.76).
