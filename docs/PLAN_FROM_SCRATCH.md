# NOEZEMA — План реализации с нуля

> Ветка: `impl/from-scratch` (база: `main` @ `e190420`)
> Документ: план v1.0
> Источники: `ARCHITECTURE.md` (draft v0.25), `README.md`, существующий код MVP v0.1 на `main`
> Статус: к исполнению

---

## 1. Цель и рамка

Полная реализация NOEZEMA «с нуля» в отдельной ветке разработки `impl/from-scratch` строго по
`ARCHITECTURE.md` v0.25. Существующий код MVP v0.1 на `main` **не является базой для переделки**:
ветка стартует с чистого дерева (документы + лицензия + конфиги) и строит систему заново по этапам
§19. Код `main` используется только как **reference** для переноса отдельных проверенных фрагментов
(промпты, паттерны LLM gateway, часть тестов) — каждый перенесённый фрагмент переписывается под
новую структуру и проходит те же тесты.

**MVP** (целевой горизонт ветки до merge в `main`) = этапы 1 + 2 + 3a + минимальный web slice
(этап 6) по §19. Этапы 3b, 4, 5, 6-full, 7 выполняются в той же ветке следующими вехами; каждый
блок имеет самостоятельный gate и может быть зафиксирован отдельным tag/merge.

**Не-цели** (не обсуждаются в этом плане): см. §2.2 ARCHITECTURE — доказательства сознания,
неограниченный доступ к хосту, произвольные действия от имени владельца, multi-agent ради
сложности, дообучение LLM, раскрытие скрытой CoT.

---

## 2. Анализ текущего состояния (`main`)

### 2.1. Что уже есть в MVP v0.1

| Компонент | Состояние на `main` |
|---|---|
| ORM / миграции | 16 таблиц, 2 Alembic-миграции |
| Orchestrator | DB-backed explorer loop: LLM предлагает → tool исполняется → строки пишутся **напрямую в доменные таблицы** |
| Curator | LLM-генерация claims + fallback; rules engine |
| Rules engine | min_grade/min_evidence по типам; grade по числу distinct hash'ей и видов evidence |
| Tool Broker | bash, python, web_fetch, search — реальное исполнение, без capability-проверок |
| Sandbox | только path-traversal защита в каталоге (не контейнер) |
| Web | FastAPI, 8 эндпоинтов, SPA на `/ui`; без аутентификации/CSRF/SSE-конвенции |
| Инфраструктура | Docker Compose (postgres, redis, web, worker, orchestrator, alembic), GitHub Actions CI |
| Тесты | 57 тестов (unit + 1 scenario + 1 security) |

### 2.2. Критические разрывы между `main` и спецификацией

| # | Разрыв | Пункт спеки | Влияние |
|---|---|---|---|
| G1 | Нет `session_staging`: claims/evidence пишутся напрямую в доменные таблицы | §3.3, §5.2.2, §5.9 | Работа активной сессии видна как «долговременная память»; атомарный commit невозможен |
| G2 | Нет fenced commit / reconciliation: один `db.commit()` без `commit_attempts` | §5.2.2, §14.2 | Потерянный ответ COMMIT не разрешим; риск mixed state |
| G3 | Нет lease/heartbeat/watchdog, writer intent, revision vector, канонического lock order | §5.2.3, §5.2.2, §14.4 | Нет fencing, нет защиты от параллельных писателей |
| G4 | 16 таблиц против ~40 в §14; нет `config_snapshots`, `runtime_config_heads`, `claim_assessments`, `claim_assessment_heads`, `artifacts`, `sources`, independence snapshots, `checkpoints`, `backup_manifests`, `outbox_events`, `operator_commands`, `messages`-lifecycle | §14 | Версионированные правила и assessment heads — основа всей модели доказательства |
| G5 | Rules engine без independence groups, scope, AND/OR-комбинаций, versioned rules в config snapshot | §3.7, §6.4, §8.7 | Grade не соответствует модели доказательств; E3 вычисляется «по количеству хешей», что ложно |
| G6 | Нет Policy Engine и capability-профилей (`packages/policy` отсутствует) | §5.6, §11.2 | Модель может предложить действие, которое не будет авторизовано; нет `PolicyEvaluated` |
| G7 | Sandbox — файловая, не контейнерная; web_fetch/search исполняются вне изоляции | §5.8, §5.7 | Модель нарушителя (§11.1) не закрыта |
| G8 | Нет content-addressed Artifact Store | §5.11, §14 | Провенанс, workspace manifests, evidence identity невозможны |
| G9 | Нет Context Builder с токеновыми бюджетами и метками pending/invalid | §5.4 | Нет контроля входа модели, pending-claims подаются как знание |
| G10 | Web без разделения Query/Command API, auth, CSRF, idempotency inbox, degraded mode | §13 | Команды оператора не типизированы; нет наблюдаемости при остановленном runtime |
| G11 | Нет systemd-контура: admission, offline rules change, host-transition journal, resume, unit-state publisher, `noezemactl` | §8.7.1, §13 | Смена правил и восстановление хоста не определены исполнимо |
| G12 | Нет bootstrap-миграции с hash-pinned config snapshot | §14.1 | Нет effective config, нет версионируемых правил |
| G13 | Redis/RQ-очередь — вне стека v1 спеки | §17, §13 | Лишняя зависимость; fоновые работы должны быть durable в PostgreSQL |
| G14 | Failpoint/invariant-тесты отсутствуют | §15.4 | Гарантии восстановления непроверены |

### 2.3. Что переносится из `main` (reference)

- Промпты explorer/curator (переподгонить под decision envelope §7 и token budgets).
- Паттерн OpenAI-compatible клиента с Pydantic-валидацией ответа (в `llm_gateway`).
- Часть unit-тестов на enums/rules (переписать под новые правила).
- CI-скелет (postgres service, uv) и Dockerfile-паттерны.
- `.env.example`, `LICENSE`, `README`, `ARCHITECTURE.md` — в ветку попадают как есть.

Всё остальное пишется заново.

---

## 3. Стратегия ветки и организация работ

### 3.1. Дерево ветки

```
impl/from-scratch
├── M0  — каркас репозитория и инфраструктура разработки
├── M1  — контракты и локальная LLM              (этап 1 спеки)
├── M2  — изоляция и атомарная сессия            (этап 2 спеки)
├── M3  — память и доказательства + web slice    (этап 3a + срез 6)  ← MVP-граница
├── M4  — зависимости и переоценка               (этап 3b)
├── M5  — расширенный познавательный цикл        (этап 4)
├── M6  — Research Proxy                          (этап 5)
├── M7  — полный веб + эксплуатация               (этапы 6-full + 7)
```

### 3.2. Дисциплина

1. Ветка `impl/from-scratch` создаётся от `main` один раз. Внутри — **milestone-коммиты**:
   каждый PR = один логический блок задач (1–3 дня работы), merge в ветку только при зелёном CI.
2. `main` не трогается до MVP-gate (конец M3). Merge MVP в `main` — отдельное решение с
   полным прогоном §22.1 `[MVP]`.
3. Теги: `noezema-m0 … noezema-m7` после каждого пройденного gate.
4. Каждый PR обязан: (a) проходить CI; (b) добавлять/обновлять тесты; (c) обновлять
   `docs/STATUS.md` (таблица готовности по пунктам §22.1/§22.2).
5. Открывшиеся архитектурные решения фиксируются ADR в `docs/adr/` (перечень в §10).
6. Запрещено в ветке: прямые UPDATE доменных таблиц извне сервисов, обход staging,
   `print`-отладка, комментарии-«TODO без номера задачи».

### 3.3. Definition of Done (общий)

- ruff + mypy (strict на новых пакетах) зелёные;
- pytest: unit + scenario (+ security, если задеты failpoint-области) зелёные;
- миграции проигрываются с нуля (`alembic upgrade head` на чистой БД) и обратимо, где заявлено;
- `docker compose -f infra/compose.dev.yaml up` поднимает dev-стек без ошибок;
- `docs/STATUS.md` обновлён; gate-критерии вехи отмечены с ссылками на тесты.

---

## 4. Целевая структура (по §18)

```
noezema/
├── apps/
│   ├── orchestrator/          # main.py, state_machine.py, commit_boundary.py,
│   │                          # reconciliation.py, budgets.py, scheduler.py
│   ├── web/                   # app.py, query_api.py, command_api.py, sse.py,
│   │                          # host_status_adapter.py, templates/ (HTMX)
│   └── research_proxy/        # (M6) fetch.py, search.py, ssrf_guard.py, provenance.py
├── packages/
│   ├── domain/
│   │   ├── models/            # enums.py, orm.py (все ~40 таблиц §14)
│   │   ├── repositories/      # по агрегату: sessions, questions, claims, evidence,
│   │   │                      #   assessments, configs, attempts, outbox, audit
│   │   ├── services/          # memory_service.py, session_service.py,
│   │   │                      #   context_builder.py, staging.py,
│   │   │                      #   independence.py, freshness.py
│   │   ├── db/                # engine.py, uow.py (unit of work + lock order helper)
│   │   └── schemas/           # Pydantic: decision envelope, staging ops, events
│   ├── llm_gateway/           # client.py, roles.py, fingerprint.py, retries.py,
│   │                          # compat_suite.py
│   ├── cognition/             # question_selector.py (M1: FIFO),
│   │                          # curiosity.py (M5), planner.py (M5)
│   ├── memory/                # rules_engine/ (registry.py, evaluator.py, scopes.py),
│   │                          # assessment.py, reverify.py
│   ├── policy/                # profiles.py, engine.py, arg_validation.py,
│   │                          # normalization.py
│   ├── tool_broker/           # broker.py, tools/ (workspace, memory, shell, python,
│   │                          #   web), sandbox_runtime.py, idempotency.py
│   └── observability/         # metrics.py, audit.py, outbox.py, alerts.py
├── hostctl/                   # noezemactl: admission, resume, offline-rules,
│                              #   host-recovery-policy, unit-state publisher
├── sandbox/
│   ├── Containerfile
│   └── policy/                # sealed.yaml, curated.yaml, open_lab.yaml
├── prompts/
│   ├── identity.md
│   ├── explorer.md            # M1
│   ├── curator.md             # M1
│   └── verifier.md            # M5
├── docs/
│   ├── adr/                   # ADR-0001… (см. §10)
│   ├── STATUS.md              # матрица готовности §22
│   └── PLAN_FROM_SCRATCH.md   # этот документ
├── migrations/                # Alembic: 0001_bootstrap … (план в §6)
├── infra/
│   ├── compose.dev.yaml       # postgres + fake-llm (dev/CI)
│   └── systemd/               # (M3) target, admission, offline-rules, resume,
│                              #   timers, unit-state, web, host-recovery.defaults.toml
├── tests/
│   ├── fakes/                 # fake_openai_server.py (детерминированный), fixtures
│   ├── unit/
│   ├── scenario/
│   ├── security/              # invariant + failpoint (§15.4)
│   └── model_compatibility/
├── pyproject.toml
└── README.md
```

---

## 5. Вехи: задачи, артефакты, gate

Обозначение задач: `T<веха>.<n>`. В скобках — пункт(ы) спеки.

### M0. Каркас и инфраструктура разработки

Цель: пустой, но полностью «живой» репозиторий — CI, dev-стек, fake LLM, тестовый каркас.

| # | Задача |
|---|---|
| T0.1 | Чистое дерево: из `main` переносятся только `README.md`, `ARCHITECTURE.md`, `LICENSE`, `.env.example`, `.gitignore`, `.dockerignore`; остальное удалено |
| T0.2 | `pyproject.toml` (Python 3.11+, hatchling), ruff (line-length 120, target py311), mypy strict для `packages/`, pytest-конфиг (`asyncio_mode=auto`) |
| T0.3 | Пакетный скелет по §4: пустые `__init__`, `py.typed`, структура каталогов; `packages/domain/db/engine.py` + settings (`pydantic-settings`), `DATABASE_URL` |
| T0.4 | CI (`ci.yml`): jobs `lint` (ruff), `types` (mypy), `test` (pytest + postgres:15 service), `docker` (build); `develop`/ветка добавлены в триггеры |
| T0.5 | Dev-стек: `infra/compose.dev.yaml` — postgres:15 + `tests/fakes/fake_openai_server.py` (детерминированный OpenAI-compatible сервер: сценарные последовательности decision-ответов по seed; json_schema structured output) |
| T0.6 | Тестовый каркас: fixtures `db` (чистая БД на тест), `fake_llm` (HTTP-клиент к fake-серверу), `workspace`; маркеры `unit/scenario/security/compat` |
| T0.7 | `docs/STATUS.md` — матрица §22.1/§22.2 с колонкой «тест-ссылка» |
| T0.8 | ADR-0001: «Реализация с нуля в `impl/from-scratch`; `main` — reference» (обоснование по §2.2) |
| T0.9 | ADR-0002: выбор Artifact Store (по умолчанию: filesystem content-addressed `~/.noezema/artifacts/`; S3-совместимый — отложить) |
| T0.10 | ADR-0003: отказ от Redis/RQ в v1; фоновые работы — durable-таблицы PostgreSQL |

**Gate M0:** CI зелёный на пустом дереве; `docker compose -f infra/compose.dev.yaml up` даёт
working postgres + fake LLM (проверка `GET /v1/models`); все ADR приняты.

---

### M1. Контракты и локальная LLM (этап 1)

Цель: полный минимальный познавательный путь в памяти БД, без изоляции (sandbox — stub) и без
fenced commit (commit — «наивный», fencing появится в M2), но со всеми контрактами.

**K1. Контракты и модели данных**

| # | Задача |
|---|---|
| T1.1 | Полный набор enums (§6, §5.7, §14.3, §8.2, §13.6): session states с `is_terminal/allows_stop/allows_abort`; decision kinds; evidence kinds/relations; grades E0–E4; epistemic statuses; assessment states; tool idempotency classes; question states (§9); message states; command enum (§13.2) |
| T1.2 | Decision envelope §7: Pydantic-схемы `ModelResponse`, `Decision` (kind=tool/complete), strict-валидация; host-generated `turn_id`/`model_run_id` (не из ответа LLM) |
| T1.3 | Миграция `0001_bootstrap` (§14.1): `config_snapshots` (bootstrap, hash-pinned payload literal + пересчёт sha256 в миграции), `runtime_config_heads(scope='global')` — в одной транзакции; фиксация UUIDv5 namespace `c0e3d3b6-dd7b-557d-a4d8-6e41049f8468` |
| T1.4 | Миграция `0002_core`: `questions`, `sessions` (все поля §14), `model_runs`, `actions` (UNIQUE по §14.2), `audit_events` (UNIQUE(session_id, sequence)), `outbox_events`, `messages`, `operator_commands` |
| T1.5 | ORM + repositories по агрегатам; UoW-хелпер (транзакция, flush/commit discipline) |
| T1.6 | Audit/Outbox service: запись в той же транзакции; `visibility`; outbox-projector (idempotent) |

**K2. LLM Gateway**

| # | Задача |
|---|---|
| T1.7 | Клиент OpenAI-compatible (httpx, async): chat + structured output (json_schema); профили backends (llama.cpp/Ollama/vLLM) через config snapshot |
| T1.8 | Fingerprint вызова: model artifact/tokenizer/template hash, backend version, prompt version, tool schema hash, policy version; сохранение в `model_runs` |
| T1.9 | Retries только транзиентные; учёт токенов/latency/finish_reason; `output_schema_valid` |
| T1.10 | Роли gateway (explorer/curator) с разными prompt snapshot-ами; набор ролей — из config snapshot, не из кода |
| T1.11 | Compatibility suite (первая версия): тесты schema-reliability, tool choice, устойчивости к длине контекста — прогон против fake LLM в CI, против реальной модели — ручная команда `noezemactl compat-run` |

**K3. Когниция и оркестратор**

| # | Задача |
|---|---|
| T1.12 | FIFO Question Selector: eligible filter (бюджет + ≥1 проверяемый путь); seeded/message questions; состояния вопроса §9 |
| T1.13 | Промпты `explorer.md`, `curator.md` (перенос+переработка из `main`): один action за ответ, запрет нескольких tool calls, JSON envelope, публичная мотивировка + expected_information |
| T1.14 | State machine сессии (§6): created→…→committing→терминалы; transitions как таблица; `stop_requested_at`/`abort_requested_at` |
| T1.15 | Explorer loop: LLM → envelope → (M2: policy) → tool (M1: in-proc stub executor) → typed observation → контекст следующего шага; лимит шагов |
| T1.16 | Curator protocol: proposal claims/evidence через staging-схемы (M1 — в памяти, таблица `session_staging` появится в M2); fallback при недоступности LLM — host-generated failure report |
| T1.17 | Minimal Query/Command API: `GET /api/v1/status`, `GET /api/v1/timeline` (audit), `POST /api/v1/messages` (inbox, TTL), `POST /api/v1/commands` (enum §13.2, idempotency key) |
| T1.18 | Adapter typed observations → evidence proposals (host-side, не моделью) |

**Gate M1** (§19, этап 1): валидный decision envelope; LLM не влияет на idempotency/IDs;
одна Sealed-сессия (fake LLM) проходит `question → action → evidence → assessment → commit`
(simple commit); CI: ≥40 unit-тестов контрактов.

---

### M2. Изоляция и атомарная сессия (этап 2)

Цель: модель нарушителя закрыта; commit сессии — fenced-транзакция с reconciliation.

**K1. Sandbox и policy**

| # | Задача |
|---|---|
| T2.1 | `sandbox/Containerfile`: rootless, non-root user, read-only rootfs, `network: none`, cap-drop ALL, no-new-privileges, CPU/RAM/PID лимиты, timeout; образы для dev (Docker) и prod (Podman) |
| T2.2 | Security profiles `sandbox/policy/*.yaml`: sealed (M2 default), curated/open_lab — заготовки |
| T2.3 | Sandbox runtime: одноразовый контейнер на сессию, mount COW overlay workspace, уничтожение после сессии; health-check контейнера |
| T2.4 | Capability profiles (`packages/policy/profiles.py`): инструменты, пути, сетевой профиль, лимиты, запрет секретов/сокетов/metadata |
| T2.5 | Policy Engine: allow/deny/require_operator; JSON Schema-валидация аргументов; повторная нормализация path/URL после первого парсинга; `PolicyEvaluated` в audit; сигнал «сходство аргументов с внешним текстом» → require_operator (§11.2) |
| T2.6 | Инструменты, недоступные профилю, отсутствуют в схеме модели; прямая попытка — deny |

**K2. Tool Broker**

| # | Задача |
|---|---|
| T2.7 | Tool contract: классы `pure/observation/idempotent(key)/non_idempotent`; таблица инструментов §5.7 с доступностью по профилям |
| T2.8 | Host-generated `action_id` + `idempotency_key`; связывание с model_run + tool + canonical arguments hash; UNIQUE-ограничения §14.2; повтор key c другим hash — security incident (alert + audit) |
| T2.9 | Retry-политика по классам; `ActionOutcomeUnknown` при crash после start; запрет вслепую-ретраи observation/non_idempotent |
| T2.10 | Реальные инструменты в sandbox: `shell.execute`, `python.execute`; workspace.read/list/write (только overlay); `memory.search` (M3), `question.create`, `message.reply` (staging) |

**K3. Artifact store и staging**

| # | Задача |
|---|---|
| T2.11 | Content-addressed Artifact Store (filesystem по ADR-0002): `artifacts`, `artifact_chunks` (origin, transform chain, trust class); put/get по SHA-256; dedup |
| T2.12 | COW workspace overlay + freeze → immutable `workspace_manifests`/`workspace_entries` (path, size, sha256) |
| T2.13 | `session_staging` — единственный механизм изоляции (§5.2.2): staging ops (claim/evidence/question/identity), `payload_hash`, schema_version; staging-артефакты |
| T2.14 | Host reserve: расчёт из лимитов + p95 rules engine; `max_claims_assessed_per_session` и др. проверяются **до** записи staging-операции; `staging_budget_exceeded` |

**K4. Commit boundary и reconciliation**

| # | Задача |
|---|---|
| T2.15 | `domain_revisions` (scopes: knowledge, dependency_graph); canonical **partial** lock order §5.2.2 с хелпером UoW (проверка подпоследовательности) |
| T2.16 | Lease + heartbeat + progress watchdog (§5.2.3): условные UPDATE, `last_progress_at`, `phase_deadline`, отказ от продления при просрочке |
| T2.17 | `knowledge_write_gate` + writer intent (`commit_intent_at`); worker/activation — M4 (контракт готов) |
| T2.18 | `commit_attempts` (prepared→reconciling→committed/aborted); durable prepared-запись **до** финальной транзакции |
| T2.19 | Финальная fenced-транзакция: locks по порядку, fencing-предикат §5.2.2 (lease, owner, обе ревизии, attempt=prepared), применение staging, pointer workspace, checkpoint, terminal state, audit+outbox — одной транзакцией |
| T2.20 | Reconciliation worker: fenced row-lock protocol (§5.2.2, диаграмма); `database_unavailable`/`finalizer_in_progress` → retry с backoff; `records_inconsistent` → critical alert, запрет wake/GC |
| T2.21 | Bюджеты: soft exhaustion → `succeeded_partial`; hard/unknown action → `failed`; stop_gracefully/abort_session по §6.7 (safe boundary, unknown tool outcome → failed) |

**K5. Тесты**

| # | Задача |
|---|---|
| T2.22 | Failpoint-тесты (первый блок §15.4): kill до COMMIT / после server commit / во время reconciliation; reconciler при открытой final transaction (row-lock wait ≠ ложный aborted); stale finalizer после fencing; kill между action start/result |
| T2.23 | Security-тесты sandbox: path traversal, escape overlay, сеть из контейнера (должна быть отключена), cap/privilege; injection через tool-аргументы |

**Gate M2** (§19, этап 2): неизвестный ответ COMMIT reconciled (test-сценарий); нет mixed state
(старый либо полный checkpoint); partial success только на safe boundary; живой finalizer не
принят за rollback.

---

### M3. Память и доказательства + web slice (этап 3a + срез 6) — **MVP-граница**

Цель: версионируемые правила, assessment heads, наблюдаемый веб, offline-смена правил,
host recovery. После этой вехи — merge MVP в `main`.

**K1. Модель знания**

| # | Задача |
|---|---|
| T3.1 | Миграции `0003_memory`: `claims`, `claim_dependencies` (заготовка), `evidence` (UNIQUE(claim_id, kind, identity_hash); per-kind CHECK §14.3), `claim_revisions`, `claim_assessments`, `claim_assessment_heads` (UNIQUE(claim_id, config_snapshot_id); lifecycle-инварианты §14.1), `assessment_evidence` (role enum), `sources`, `source_independence_snapshots/members`, `environment_manifests` (заготовка M4), `checkpoints`, `backup_manifests` |
| T3.2 | Evidence identity: canonical hash доверенным контуром (per-kind состав §14.3); дедупликация; «перевёрнутый» повтор content — не новый evidence |
| T3.3 | Rules engine v1 (`packages/memory/rules_engine/`): **executable** claim type rules — допустимые kinds, мин. число evidence, independence groups, AND/OR-комбинации, scope-predicate, max_grade без обязательных полей, volatility/reverify_after; правила живут в `config_snapshots.claim_type_rules`; `rules_hash` |
| T3.4 | Assessment: grade/confidence считает только rules engine (единственный producer); `epistemic_status`; counterevidence → disputed; operator attestation не повышает grade |
| T3.5 | Conservативные source independence groups для локального корпуса: PSL, URI-normalization, текст-overlap; snapshot (алгоритм/версия/пороги) в `claim_assessments`; неизвестная lineage — одна группа (не ложная независимость) |
| T3.6 | Lifecycle: `current/pending/invalid` (§8.2, §14.1); staging-операция, меняющая evidence set → синхронное обновление head effective snapshot в пределах session limits; pending/invalid — не current evidence, не dependency |
| T3.7 | Freshness: reverify_after из claim type/volatility/as_of; истечение меняет только freshness_status; `valid_to=NULL` = открытый конец |

**K2. Context Builder**

| # | Задача |
|---|---|
| T3.8 | Context pack §5.4: секции с абсолютными токен-лимитами из config snapshot; hard-секции (протокол) резервируются первыми; ранжирование/усечение остальных; `ContextPacked` (token counts, chunk IDs, причины исключений, tokenizer fingerprint) |
| T3.9 | Retrieval: полнотекст + значимость/свежесть/связь (pgvector — опционально, ADR при подключении); pending/invalid — отдельный лимит и явная метка в той же строке (§5.4.2) |

**K3. Offline-смена правил и host recovery (§8.7.1, §13)**

| # | Задача |
|---|---|
| T3.10 | `hostctl`-скелет: CLI (click/typer), чтение/валидация `host-recovery.defaults.toml` (schema v1, jitter=0), canonical JCS hash |
| T3.11 | Host-transition journal: fsync-safe (tmp→fsync→rename→fsync(dir)) current record + immutable events; `host-transition-head.json` (единственный active head); boot reconciliation (0/1/≥2 unresolved) |
| T3.12 | `noezema-offline-rules.service`: systemd scope, `RuntimeDirectory`, flock (≤2s, backoff), marker, остановка runtime target, проверка `ConsistsOf`/`PartOf`, advisory lock, candidate upsert по `(base_snapshot_id, payload_sha256)`, cohort freeze + manifest, shadow-head батчи, verification seal (JCS digest, counts), atomic publish (pointer + UUIDv5 invalid-вопросы) одной транзакцией, cleanup |
| T3.13 | `noezema-runtime.target` + `noezema-runtime-admission.service` (fail-closed: head tuple, bootstrap seed hash, host records, marker, policy head); `Requires/After` на всех members |
| T3.14 | Resume: `noezema-runtime-resume.service` + retry timer (`AccuracySec=1s`, `RandomizedDelaySec=0`), классификация (transient→`retry_wait` exit 0 / permanent→`resume_blocked` exit 78 / unclassified burst→`resume_degraded`), `noezema-resume-failure@.service`, idempotent audit replay по `(attempt_id, event_seq)` |
| T3.15 | `install-host-recovery-policy` / `resolve-host-policy` (§8.7.1.1): policy-change head, event stream, terminal `effective_hash`, `accept_current`/`install_replacement` |
| T3.16 | Unit-state publisher: `noezema-unit-state.service/.timer` → `/run/noezema/unit-state.json` (boot_id, inventory, TTL 15s) |
| T3.17 | Unit-файлы + `systemd-analyze verify` в CI; baseline `host-recovery.defaults.toml` — hash-pinned в CI |

**K4. Web slice (срез MVP этапа 6)**

| # | Задача |
|---|---|
| T3.18 | Разделение Query/Command API (§13.1/§13.2): read-only credentials для query; Command — auth (локальный администратор), CSRF, rate limit, durable inbox с idempotency key |
| T3.19 | SSE timeline: committed outbox events + host-transition/policy notifications (без представления host record как DB audit до replay) |
| T3.20 | Главная страница (§13.3): статус узла/сессии, фаза, fingerprint, ресурсы, следующее пробуждение, предупреждения (stale, outcome unknown, outbox lag, backup age), recovery-state banner (retry_wait/resume_degraded/resume_blocked) |
| T3.21 | Страница сессии (§13.4): вопрос, план, действия/policy/результаты, chunks/артефакты, claims до/после, итог, fingerprint, failure report |
| T3.22 | Messages: lifecycle §13.6, доставка между действиями/при пробуждении, TTL→expired; `message.reply` — только через tool |
| T3.23 | Controls: `wake_now/pause/resume/stop_gracefully/abort_session` (§6.7, §13.7) с подтверждением для опасных, reason, audit actor |
| T3.24 | Host Status Adapter + degraded mode (§13): read-only journal, fail-closed Command API при любом unresolved host/policy state, stale snapshot, DB outage |

**K5. Тесты**

| # | Задача |
|---|---|
| T3.25 | Failpoint: kill в offline-процессе (до/после candidate upsert, между shadow-head батчами, после seal, до/после atomic publish); повторный run = тот же candidate, нет дублей вопросов; parallel maintenance start; runtime start при marker |
| T3.26 | Invariant: duplicate evidence не повышает grade; counterevidence меняет head в том же commit; offline publish — старый либо полный новый pointer+вопросы; pending/invalid не подаётся как current; grade только rules engine |
| T3.27 | Host resume: outage DB любой длительности — `retry_wait` (без расхода start limit); unclassified crash-loop — `resume_degraded`; permanent — `resume_blocked`; timer lateness; orphan/multiple-unresolved; retained history ≠ active |
| T3.28 | Scenario: полный Sealed-день (N сессий подряд) — FIFO, staging, assessment, commit, timeline в web |

**K6. Wake scheduling (MVP-дополнение, пункт 1 §22.1; пропуск плана — задача добавлена ретроспективно)**

| # | Задача |
|---|---|
| T3.29 | Пробуждение по расписанию + wake admission + backoff/pause (§5.2.1): секция `wake_schedule` в config snapshot (периодический интервал = MVP-случай cron, мин. интервал между сессиями, backoff base/multiplier/max, лимит disk quota, GPU) + `wake_scheduler_state` (миграция `0005`); tick — `noezemactl wake-tick` (systemd `noezema-wake.timer`, 5 мин): расписание читается из effective snapshot, не из таймера; wake admission (paused / не-терминальная сессия / unresolved commit attempt / активный activation slot / disk quota / GPU fail-closed) — пропуск с точной причиной в audit `wake_skipped` (никогда не в очередь); экспоненциальный backoff после failed-сессии; авто-pause после N последовательных неудач (sticky до операторного resume, resume сбрасывает failure-бухгалтерию); `wake_now` обходит расписание (интервал/gap/backoff), но не admission — тот же gate в web Command API (REJECTED с reason); состояние wake (backoff/pause) в /status; fail-closed на битый `wake_schedule` |
| T3.30 | Фоновый heartbeat lease во время долгих операций + честное время в lease (§5.2.3, дефект из первой реальной MVP-сессии): (а) lease-таймстампы пишутся через `clock_timestamp()`, не `now()` — в долгой phase-1-транзакции `now()` = старт транзакции, и `lease_expires_at` был «зацементирован» на `txn_start + ttl`: любая сессия длиннее TTL проигрывала fenced commit независимо от heartbeat (первая реальная сессия, qwen36-35b-a3b-q6-mtp, LLM-вызов 47 с против TTL 30 с → `commit_lease_lost`); (б) `LeaseHeartbeatGuard` — background-task продление lease (интервал = ttl/3, §5.2.3 «TTL равен нескольким heartbeat intervals») вокруг explorer/curator LLM-вызовов; продление в транзакции вызывающего (отдельное соединение блокировалось бы на row-lock сессии), `progress=False` — watchdog прогресса не искажается; отказ продления (phase deadline) → `LeaseLost` на выходе guard, abort + reconciler (никогда не угаданный rollback); оркестратор: `Orchestrator(lease_ttl=...)` — инъекция TTL для тестов (регрессия: LLM-вызовы 2.5 с при TTL 1 с → SUCCEEDED) |

**Gate M3** = gate этапа 3a (§19) + MVP-критерии §22.1 `[MVP]` (пункты 1–11, 13, 15, 19, 20, 21,
23, 28, 30–34) — каждый со ссылкой на тест. После gate: tag `noezema-mvp`, merge в `main` (отдельное решение).

**Серия реальных MVP-сессий** (замер нагрузки до M4, §M4): 1-я сессия 2026-09-14 (qwen36-35b-a3b-q6-mtp,
llama.cpp на 192.168.1.48, «Сколько будет 6*7?»): sandbox `python.execute` 56 мс, ответ модели 2 шага,
fenced commit отклонён по истёкшему lease → T3.30; куратор: `reasoning_content` съедает бюджет
`max_output_tokens` (2048 → пустой content, `finish_reason=length`, 3 ретрая ~84 с) → операционное
решение: `max_output_tokens ≥ 4096` для реальной сессии. 2-я сессия (после T3.30, 2026-09-14) —
**SUCCEEDED**: explorer 68.9 с + 143.4 с (4.8×TTL, guard), куратор 24.4 с (out 1841, schema_valid),
fenced commit 46 мс, claim «6*7=42» → E2/supported/0.55, вопрос → verified.

Результат серии (2026-09-14, БД `noezema_mvp`, накопление знания между сессиями):
- **Проход 1 (бюджет 4096): 4/4 failed** — `LLMSchemaError` на первом explorer-вызове:
  `reasoning_content` съел весь бюджет (проб минимального промпта: 15 000+ знаков reasoning,
  `finish_reason=length`, content пуст; 3 ретрая ≈ 200–286 с/сессию). Поведение модели
  дрейфует: длина reasoning на тривиальном вопросе выросла до 4100–4250 токенов (ср. 545–1841
  во 2-й сессии). Авто-pause wake-планировщика сработал (4 последовательных неудач → sticky
  `paused`); operator resume (RESUME: node_state→idle + сброс failure-бухгалтерии) восстановил
  работу. Отказавшие сессии чисто откатились: в таблицах лишь wake-ledger + хост-лог.
- **Проход 2 (бюджет 8192): 3 succeeded + 1 succeeded_partial** (wall 72–184 с):
  | сессия | итог | LLM-вызовы (out/ток, с) | sandbox | commit |
  |---|---|---|---|---|
  | 42:6 | succeeded_partial (4 шага) | 702/10.8, 4222/65.9, 541/8.4, 2448/33.0, 4252/65.9 | ~0–1 мс ×2 | 50 мс |
  | 12·13 | succeeded (2 шага) | 857/13.1, 1288/19.3, 3132/39.7 | ~0 мс | 22 мс |
  | сумма 1..10 | succeeded (2 шага) | 4233/60.6, 4189/63.8, 2944/38.3 | ~0 мс | 21 мс |
  | 7! | succeeded (3 шага) | 4226/63.5, 4184/64.4, 994/14.9, 2373/30.8 | ~0 мс | 21 мс |
- **Замеры нагрузки (проход 2, 15 LLM-вызовов)**: throughput ≈ 68 ток/с (40 585 out-токенов за
  592 с); LLM-задержка 8–66 с (линейно от длины вывода); wall сессии = LLM-время + 5–10 с
  оверхеда; fenced commit 21–50 мс; sandbox `python.execute` ≤1 мс на тривиальном коде;
  in-токены 600–1110/вызов (контекст-пак с накопленным знанием).
- **Отклонения протокола модели** (защиты отработали): чужой инструмент `message.reply` с
  неверными аргументами → policy DENIED (action_failed); free-text complete-reason вне closed
  enum → `succeeded_partial` + вопрос `partially_answered` (claim всё же закоммичен, E2).
- **Операционное решение**: `max_output_tokens = 8192` — обязательный минимум для
  qwen36-35b-a3b-q6-mtp (бюджет ≥ P99(reasoning) + payload, с запасом); `reset_failure_state()`
  планировщика — только failure-бухгалтерия, node_state снимает web RESUME (полная процедура).
- Итог: 5 закоммиченных сессий, 5 claims (все computed_result, E2/supported/0.55),
  вопросы: 4 verified + 1 partially_answered. Материал для порогов M4 (очередь, батч, SLO) —
  собран; старт M4 — решение пользователя.

---

### M4. Зависимости и переоценка (этап 3b)

Начинается **после** серии реальных MVP-сессий: пороги очереди, размер батча, SLO выводятся из
измеренной нагрузки.

**Пороги M4 (выведены из замеров серии 2026-09-14, см. блок «Серия реальных MVP-сессий»):**
LLM-хост (qwen36-35b-a3b-q6-mtp, MTP/ROCm) — throughput ≈ 68 ток/с, задержка вызова 8–66 с
(линейно от длины вывода; P99 серии ≈ 66 с), in-токены 600–1110/вызов. SLO сессии: wall
72–184 с (серии) → целевой P95 ≤ 200 с при бюджете `max_output_tokens=8192` (худший вызов
≈ 121 с при измеренном throughput — в пределах gateway timeout 300 с). Fenced commit 21–50 мс,
sandbox ≤1 мс на тривиальном коде. Знание: 1–2 claim/сессию, степень зависимости ≤3.
Из этого: **размер батча cascade invalidation = 32 claim** (короткая транзакция остаётся в
десятках мс по замеру commit; closure локального корпуса мал); **очередь**: одна активная
сессия (single node) — worker-батчи выполняются между сессиями, окно между сессиями ≥
измеренного wall (72–184 с); runnable-job-возраст для эскалации `T_escalate` и admission-порог
`T_worker_admission` задаются дефолтами в config snapshot при T4.3/T4.4 из этих чисел
(база: не отставать на >2 интервалов wake при wall ≤ 200 с).

| # | Задача |
|---|---|
| T4.1 ✅ | `claim_dependencies`: направление `from depends on to`; DAG-цикл check при commit (циклическое evidential-ребро отклоняется с audit, claim коммитится); graph revision только при изменении evidential edges (fencing по base из prepared-строки). Claim ID в контекст-паке `[c:<uuid>]`, `dependencies` в staging-схеме (evidential/research), migration 0006 (kind по §8.6), curator-v2. Тесты: test_claim_dependencies.py, test_staging_schema.py, test_orchestrator.py |
| T4.2 ✅ | Cascade invalidation: closure вне блокировки, immutable closure manifest (root, graph rev, упорядоченные IDs, rank, count, sha256); barrier `discovering/active/closing/resolved/blocked`, durable cursor, идемпотентные батчи; final closure scan перед `resolved`. Реализовано: `packages/memory/cascade.py` (start_cascade + process_barrier + protected_claim_ids), migration 0007, retrieval ancestor check, test_cascade.py (8) |
| T4.3 ✅ | `reassessment_jobs`: durable очередь, lease/retry/blocked, unique active job, runnable-предикат (effective pointer, пустой activating slot); worker `system:reassessment` (без LLM, без сети, без evidence). Реализовано: `packages/memory/reassessment.py` (run_reassessment_batch + recover_expired_leases + worker_admission_metrics), audit `reassessment_job_*`, test_reassessment.py (13) |
| T4.4 ✅ | Writer admission: NOWAIT gate, уступление session intent, `T_escalate`, admission gates `T_worker_admission`/`T_repair_admission` в scheduler. Реализовано: `packages/memory/writer_gate.py` (table gate §14.1 CAS NOWAIT + session intent rules 1/4/5), migration 0008 (gate-форма §14.1 + `reassessment_admission` секция: t_escalate=7200, t_worker_admission=7200, queue_slo=172800), worker — deferral с jitter при конфликте + уступка intent (admission и mid-batch), scheduler — `ReassessmentAdmission` + skip `reassessment_backlog`, `hostctl reassessment-tick`; T_repair_admission — T4.5. Тесты: test_writer_admission.py (14) |
| T4.5 | Online-активация §8.7.2: fenced lease + activating pointer + fence, quiesce, shadow heads, seal, atomic flip `publishing→post_publish`, post-publish manifest, terminal-cleanup, repair runner (post-cleanup CAS, superseded-закрытие) |
| T4.6 | Environment manifests (полные поля §14) + environment independence: repeatability/reproducibility/independent replication (§8.7.3) |
| T4.7 | Полный source graph: dependency edges, merge/correction (`source_graph_corrections`), cascade reassessment при слиянии групп |
| T4.8 | Counterevidence resolutions (§8.7.4): XOR-инварианты, uniqueness, valid basis, cascade при инвалидации basis |
| T4.9 | Failpoint: crash в каждом activation state (pointer tuple recovery); stale activator после takeover; repair vs следующий flip; barrier crash между батчами; group merge |

**Gate M4** (§19, этап 3b): invalid ancestor блокирует downstream; worker без starvation (оба
направления); group merge запускает корректный пересчёт.

---

### M5. Расширенный познавательный цикл (этап 4)

| # | Задача |
|---|---|
| T5.1 | Curiosity ranking (§5.3.1): score-формула, eligibility filter, нормализация, ε-разнообразие; веса/пороги/embedding fingerprint — в config snapshot |
| T5.2 | Многошаговое planning: план как наблюдаемый артефакт (наблюдения, критерии остановки, assessment methods) |
| T5.3 | Специализированные роли explorer/verifier/curator: verifier организует детерминированные проверки, **не назначает grade** (§5.5, §3.7) |
| T5.4 | Защита от семантических повторов (§9): similarity целей, перефразирование, циклы планов, стратегии при цикле |
| T5.5 | Untrusted extraction profile: модель без инструментов извлекает chunks с provenance из высокорисковых документов |
| T5.6 | Long-run сценарные тесты (мульти-сессионные, с повторами и противоречиями) |

**Gate M5** (§19, этап 4): host-owned evidence/assessment boundary сохранена; verifier не
назначает grade/confidence; новый метод проверки отличим от перефразирования.

---

### M6. Research Proxy (этап 5)

| # | Задача |
|---|---|
| T6.1 | `apps/research_proxy`: единственный egress; read-only; SSRF-guard (private/loopback/link-local/metadata), редиректы/размер/время; удаление активного содержимого |
| T6.2 | Режимы: Sealed (локальный индекс), Curated (SearXNG через прокси, upstream-лог, rate limits), Open Lab (разрешённые домены, отдельный профиль) |
| T6.3 | Provenance: original + normalized text + hash; `sources`/`artifact_chunks` с origin; маркировка «недоверенный внешний контент» в контексте (data boundaries §11.2) |
| T6.4 | Injection/poisoning-тесты: страница с инъекцией → capabilities не меняются; similarity-сигнал → require_operator; poisoning артефактов прошлых сессий |

**Gate M6** (§19, этап 5): внешний текст не меняет capabilities; group merge запускает cascade
reassessment.

---

### M7. Полный веб + эксплуатация (этапы 6-full + 7)

| # | Задача |
|---|---|
| T7.1 | Веб: knowledge graph (claims/assessments/dependencies, provenance navigation), diagnostics (reconciliation, invalidation, blocked jobs/barriers, retry views) |
| T7.2 | Backup/PITR: `backup_manifests` (DB recovery point + artifact inventory), restore drill (случайная точка retention, проверка всех referenced hashes, boot reconciliation) |
| T7.3 | GC: полный root set §15.3; запрет GC при `reconciling_commit`; retention-политики |
| T7.4 | Security regression: полный прогон security-тестов как gate-джоб; отчёты по метрикам §16 |
| T7.5 | Evaluation run §22.2: 50–100 eligible sessions, замороженные model/config/rules, gates с исходами passed/failed/insufficient_sample, слепая выборка |
| T7.6 | ADR по результатам evaluation |

**Gate M7** (§19, этап 7): §22.1 полный + §22.2 пройден (ни один gate не `failed` и не
`insufficient_sample`).

---

## 6. План миграций (сводка)

| Миграция | Веха | Таблицы / содержание |
|---|---|---|
| `0001_bootstrap` | M1 | `config_snapshots` (bootstrap, hash-pinned), `runtime_config_heads`; UUIDv5 namespace literal |
| `0002_core` | M1 | `questions`, `sessions`, `model_runs`, `actions`, `audit_events`, `outbox_events`, `messages`, `operator_commands` |
| `0003_commit` | M2 | `domain_revisions`, `knowledge_write_gate`, `commit_attempts`, `session_staging`, `staging_artifacts`, `artifacts`, `artifact_chunks`, `workspace_manifests`, `workspace_entries`, `checkpoints`, `backup_manifests` |
| `0004_memory` | M3 | `claims`, `claim_dependencies`, `evidence`, `claim_revisions`, `claim_assessments`, `claim_assessment_heads`, `assessment_evidence`, `operator_attestations`, `sources`, `source_independence_snapshots`, `source_independence_members`, `source_dependency_edges`, `source_graph_corrections`, `environment_manifests`, `environment_independence_snapshots`, `environment_independence_members` |
| `0005_reassessment` | M4 | `reassessment_jobs`, `dependency_invalidation_barriers` |
| `0006_evaluation` | M7 | `evaluation_runs` (зафиксированные пороги, исходы) |

Правила: каждая миграция — отдельный PR; обратимость — только если не влияет на
неизменяемость (audit/outbox — без down); partial unique indexes и deferred CHECK-триггеры
описаны явно с тестами (§14.3); bootstrap — atomарна и fail-closed при hash mismatch.

---

## 7. Безопасность: послойный план

Слои по §11.2, реализуемые вехами:

1. **Изоляция** (M2): одноразовый rootless-контейнер, read-only rootfs, no network, cap-drop ALL,
   no-new-privileges, лимиты, без docker socket/SSH/секретов.
2. **Capability security** (M2): профиль — доверенный контур; содержание контекста не расширяет
   права; повторная нормализация аргументов; недоступный инструмент не в схеме модели.
3. **Provenance** (M2/M6): chunk-уровневый origin, hash, transform chain, trust class; data
   boundaries в контексте; extraction profile (M5).
4. **Evidence rules** (M3): grade детерминированно, только rules engine; independence
   snapshots; scope.
5. **Наблюдаемость** (M3/M7): operational timeline из audit, не из нарратива; метрики §16.3.
6. **Операторское подтверждение** (M3): require_operator для внешних эффектов; повторное
   подтверждение опасных команд; actor/reason/IP в audit.

Недоверенные стороны (строго по §11.1): внешние страницы/документы/datasets, сообщения человека,
LLM, код в sandbox, артефакты прошлых сессий. Каждая из них — отдельный класс
security-тестов (M2: injection в tool-аргументы; M5: отложенная инъекция из артефактов; M6:
страницы).

---

## 8. Тестирование

### 8.1. Слои

| Слой | Где | Когда |
|---|---|---|
| unit | `tests/unit/` | каждая веха; контракты, rules engine, policy, normalization, hashing |
| scenario | `tests/scenario/` | M1: одна Sealed-сессия (fake LLM); M3: «Sealed-день»; M4: cascade-сценарии; M5: long-run |
| security | `tests/security/` | invariant-тесты (property-style, DB-проверки инвариантов) + failpoint-тесты (kill-сигналы, блокировки, time travel) |
| model_compatibility | `tests/model_compatibility/` | CI: fake LLM; manual: реальная модель перед выбором профиля (ADR-0010) |

### 8.2. Failpoint-матрица (по §15.4, распределённая по вехам)

| Веха | Failpoints |
|---|---|
| M2 | kill до COMMIT / после server commit / во время reconciliation; reconciler при открытой final transaction; stale finalizer после fencing; kill между action start/result; writer intent; conflict каждой компоненты revision vector |
| M3 | offline: parallel maintenance, start при marker, kill в каждой фазе прогона, reboot; resume: outage/unclassified/permanent, timer lateness, orphan/multiple-unresolved, policy change crash-точки |
| M4 | каждый activation state; stale activator; repair vs flip; worker gate до acquisition; barrier crash-точки; cycle; basis invalidation; group merge |
| M7 | полный regression-прогон |

### 8.3. Инварианты (автопроверка в security-слое)

Минимальный набор на MVP (полный — §15.4): старый либо полный checkpoint; unknown COMMIT ≠
ложный failure; staging limit не обнаружен впервые после commit boundary; partial success без
unknown action; GC не удаляет root-reachable; effective claim только через effective runtime
snapshot; offline snapshot всегда `active`; незавершённый host transition — ровно один active
head.

### 8.4. Fake LLM

`tests/fakes/fake_openai_server.py`: детерминированный сценарный сервер (последовательности
decision-ответов по seed), поддержка json_schema, ошибки по расписанию (для retry-тестов),
инъекционные сценарии (M6). Это позволяет весь CI работать без GPU.

---

## 9. CI/CD

### 9.1. Jobs (по вехам)

| Job | M0 | M1 | M2 | M3 | M4+ |
|---|---|---|---|---|---|
| lint (ruff) | ✅ | ✅ | ✅ | ✅ | ✅ |
| types (mypy) | ✅ | ✅ | ✅ | ✅ | ✅ |
| unit+scenario (postgres:15) | ✅ | ✅ | ✅ | ✅ | ✅ |
| security (failpoint+invariant) | | | ✅ | ✅ | ✅ |
| docker build (app + sandbox) | ✅ | | ✅ | ✅ | ✅ |
| systemd verify + unit consistency (ConsistsOf/PartOf) | | | | ✅ | ✅ |
| nightly: Sealed-день сценарий + metrics report | | | ✅ | ✅ | ✅ |
| nightly: compat suite против реальной модели (опц., self-hosted runner) | | | | ✅ | ✅ |

### 9.2. Release

- Tags `noezema-m*`; после MVP-gate — `v0.2.0-mvp`.
- Docker-образы: `noezema-app`, `noezema-sandbox` (multistage, rootless-совместимые).
- Packaging host-контура (systemd units + `hostctl`) — отдельный пакет/скрипт установки
  (M3), версионируется с кодом; hash baseline-файлов проверяется CI.

---

## 10. Открытые вопросы → ADR (по §21)

| ADR | Вопрос | Закрыть до |
|---|---|---|
| ADR-0001 | Реализация с нуля в отдельной ветке | M0 ✅ (создаётся с этим планом) |
| ADR-0002 | Artifact Store: filesystem vs S3-совместимый | M0 ✅ |
| ADR-0003 | Отказ от Redis/RQ в v1 | M0 ✅ |
| ADR-0004 | Identity: предлагает revision или меняет документ | M3 |
| ADR-0005 | Open Lab в продукте | M6 |
| ADR-0006 | Подписанный local package mirror | M3 |
| ADR-0007 | Калибровка confidence по claim types | M3 (стартовая), M7 (после evaluation) |
| ADR-0008 | Executable rules/thresholds v1 | M3 |
| ADR-0009 | Visibility classes вне LAN | M3 (web slice) |
| ADR-0010 | Выбор model profile (compat suite) | M3 (до реальных сессий) |
| ADR-0011 | Retention periods (restore/diagnostic) | M7 |
| ADR-0012 | Объём ручной выборки | M7 |
| ADR-0013 | Wall-clock SLO reassessment | M4 (из измеренной нагрузки) |
| ADR-0014 | Multi-thinker tenancy (schema v2) | не v1 |
| ADR-0015 | Weights curiosity-ранжирования | M5 |

---

## 11. Риски и меры

| Риск | Вероятность | Мера |
|---|---|---|
| Спецификация 2600+ строк: drift при реализации | высокая | каждый PR ссылается на пункты §; `docs/STATUS.md` — матрица; ADR на отклонения |
| Сложность host-контура (M3) задержит MVP | высокая | M3 делится на под-PR: journal → offline rules → resume → web; web slice не ждёт 3b |
| Локальная LLM нестабильна в schema | высокая | fake LLM в CI; compat suite до выбора модели (ADR-0010); retries транзиентные; host-generated failure report при недоступности |
| Fenced commit/reconciliation — самый сложный код | средняя | отдельный модуль + максимальное покрытие failpoint (M2); код-ревью по диаграмме §5.2.2 |
| Корпус знаний растёт быстрее, чем batc-лимиты | средняя | host reserve и staging limits проверяются до записи (M2); лимиты в config snapshot |
| systemd-контур не тестируется в CI (ubuntu-latest) | средняя | `systemd-analyze verify` + unit-consistency-тесты; полный прогон — self-hosted runner (M3, ночной job) |
| «Театр верификации» (риск §20.2) | средняя | rules engine — единственный producer grade; blind-выборка в M7 |

---

## 12. Оценка объёма

| Веха | Задач | Оценка (чел.-дни, 1 dev) |
|---|---|---|
| M0 | 10 | 4–5 |
| M1 | 18 | 15–20 |
| M2 | 23 | 25–35 (commit boundary + failpoints) |
| M3 | 29 | 30–40 (host-контур — 40%) |
| M4 | 9 | 30–40 (activation + worker + barrier) |
| M5 | 6 | 20–25 |
| M6 | 4 | 10–15 |
| M7 | 6 | 20–30 (evaluation run — wall-clock) |

MVP (M0–M3) ≈ **70–100 чел.-дней**. При одном разработнике — реалистичный срок MVP ~4–5 месяцев;
при двух (host-контур + когнитивный контур параллельно) — ~2.5–3 месяца.

---

## 13. Первые 10 PR (порядок запуска)

1. `chore: clean tree + pyproject/ruff/mypy/pytest skeleton` (T0.1–T0.3)
2. `ci: lint+types+test jobs, postgres service, fake-llm server` (T0.4–T0.6)
3. `docs: STATUS.md + ADR-0001/0002/0003` (T0.7–T0.10)
4. `domain: enums + decision envelope schemas + tests` (T1.1–T1.2)
5. `migrations: 0001_bootstrap (hash-pinned config + runtime head)` (T1.3)
6. `migrations: 0002_core (sessions/questions/model_runs/actions/audit/outbox/messages/commands)` + ORM + repositories (T1.4–T1.6)
7. `llm_gateway: client + fingerprint + retries + compat suite v1` (T1.7–T1.11)
8. `cognition: FIFO selector + prompts explorer/curator` (T1.12–T1.13)
9. `orchestrator: state machine + explorer loop (stub executor) + curator protocol` (T1.14–T1.16, T1.18)
10. `web: minimal Query/Command API (status/timeline/messages/commands)` (T1.17)

После PR #10 — **M1 gate-прогон** и tag `noezema-m1`.

---

## 14. Критерии приёмки MVP (чек-лист из §22.1 `[MVP]`)

Каждый пункт — с ссылкой на тест в `docs/STATUS.md`:

- [x] 1. Пробуждение по расписанию, pause/backoff (T3.29)
- [ ] 2. Локальная LLM с fingerprint
- [ ] 3. Causal/idempotency ID только в trusted host
- [ ] 4. Typed actions в sandbox
- [ ] 5. Claim только с согласованным assessment lifecycle, provenance, scope
- [ ] 6. Один fenced commit attempt (knowledge + dependency_graph revisions)
- [ ] 7. Lost COMMIT → fenced reconciliation; живой finalizer = wait/retry
- [ ] 8. После failpoints — старый либо полный checkpoint
- [ ] 9. Status, operational timeline, commit attempts, assessment states; authenticated messages/controls
- [ ] 10. Раздельные messages/stop/abort/controls
- [ ] 11. Нет вслепую-ретраев unknown/observation/non_idempotent
- [ ] 13. Partial success только на safe boundary
- [ ] 15. Pending/invalid — не current evidence, в контексте только с меткой
- [ ] 19. Unresolved commit attempt блокирует wake и GC
- [ ] 20. FIFO-вопрос проходит полный минимальный путь
- [ ] 21. Sync-обновление head при смене evidence set; offline publish — полный shadow cohort одним flip
- [ ] 23. Session limits: новые/существующие claims+evidence, отказ до записи staging, host reserve
- [ ] 28. Offline-смена правил: повторный выбор candidate, verification seal, pointer+вопросы одной транзакцией
- [ ] 30. Bootstrap-миграция: hash-pinned snapshot + global head; fail-closed startup/restore
- [ ] 31. Target quiesce через PartOf; admission fail-closed на любой start
- [ ] 32. Host transition: один active head, orphan/≥2 reconciliation, retry_wait/degraded/blocked, replay перед удалением head
- [ ] 33. Recovery policy: root-owned, hash-pinned, jitter=0, AccuracySec=1s, effective_hash terminal
- [ ] 34. Web вне runtime target, fail-closed Command API, unit-state snapshot TTL

---

*Конец плана. Исполнение начинается с PR #1 из §13.*
