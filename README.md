# NOEZEMA

**Local-first autonomous thinker** — система, которая периодически пробуждается, выбирает неизвестный ей вопрос, исследует его, проверяет выводы с доказательствами и сохраняет знания для следующей сессии и человека-наблюдателя.

| Параметр | Значение |
|----------|----------|
| Статус | Архитектурный draft v0.23 |
| Язык | Python |
| Бэкенд | Локальная LLM (OpenAI-compatible API) |
| База данных | PostgreSQL 15+ |
| Сэндбокс | Rootless Podman / Docker |
| Веб-интерфейс | FastAPI + HTMX + SSE |

---

## Идея

NOEZEMA — автономный локальный мыслитель. Каждая сессия:

1. **Пробуждение** — система определяет, что условия выполнены (GPU, диск, нет активной сессии)
2. **Выбор вопроса** — Curiosity Engine ранжирует кандидатов по новизне, проверяемости, связи с интересами (в MVP — FIFO Question Selector, ранжирование появляется на этапе 4)
3. **Исследование** — LLM выполняет типизированные действия внутри изолированного sandbox
4. **Верификация** — правила доказательств (E0–E4) оценивают каждый claim по набору evidence
5. **Консолидация** — Curator предлагает изменения; Memory Service валидирует staging
6. **Атомарный commit** — результат фиксируется одной транзакцией; при сбое — reconciliation

Мыслитель свободен выбирать темы, развивать идентичность и организовывать рабочее пространство. Границы безопасности, планировщик и журнал событий находятся вне его контроля.

## Ключевые отличия

Относительно ранних экспериментов с автономным AI на сервере с персистентной памятью:

- ✅ Управляющий контур отделён от среды мыслителя
- ✅ Модель не исполняет команды непосредственно на хосте
- ✅ Действия передаются через типизированный протокол с capability-профилями
- ✅ Знания отделены от субъективных воспоминаний
- ✅ Каждое утверждение связано с доказательствами и статусом проверки
- ✅ История событий неизменяема
- ✅ Локальная LLM — штатный режим, а не дополнительный
- ✅ Наблюдаемость через веб-интерфейс, включая read-only host recovery при остановленном runtime

### Гарантии восстановления

Отдельно от концептуальных отличий — свойства, которые обычно всплывают уже в эксплуатации:

- Потерянный ответ на `COMMIT` разрешается fenced row-lock reconciliation, а не догадкой по сетевому ответу
- Durable worker и barrier recovery: ни один poison job, ни падение между батчами не теряют прогресс и не парализуют систему
- Транзиентная недоступность БД восстанавливается сама; человек нужен только для несогласованных записей
- Knowledge и dependency graph защищены revision vector и единым частичным lock order
- В MVP rules/config меняется offline внутри systemd-owned maintenance scope: target membership проверяется через `PartOf/ConsistsOf`, current state и immutable host events журналируются fsync-safe, а pointer+questions публикуются одной транзакцией; fenced online activation появляется в 3b
- Классифицированный DB outage остаётся в durable `retry_wait` и не расходует systemd crash budget; неклассифицированный crash-loop виден как auto-recovering `resume_degraded`, permanent inconsistency — как operator-required `resume_blocked`
- Единственный fsync-safe active head отделяет незавершённый host transition от retained history; boot reconciliation восстанавливает один orphan и блокирует runtime при множественных unresolved records
- Recovery policy берётся только из versioned root-owned host files, hash-ируется и материализуется в transition; DB snapshot не может неявно изменить backoff
- Retry и unit-state timers имеют явные `AccuracySec=1s`, отключённый systemd jitter и измеряемую lateness

## Архитектура

```
┌─────────────────────────────────────────────────┐
│           Доверенный контур (Linux VM)           │
│                                                  │
│  Supervisor → Orchestrator → Curiosity Engine    │
│                  → Context Builder               │
│                  → LLM Gateway → Локальная LLM   │
│                  → Policy Engine                 │
│                  → Tool Broker → Sandbox         │
│                  → Memory Service                │
│                                                  │
│  PostgreSQL (domain + audit + outbox)            │
│  Content-addressed Artifact Store                │
│  Research Proxy (контролируемый egress)          │
│  Query API / Command API → Веб-модуль            │
└─────────────────────────────────────────────────┘
         ↕
┌─────────────────────────────────────────────────┐
│       Sandbox (одноразовый, COW overlay)         │
│  /workspace (base snapshot + overlay)            │
│  Ограниченный shell / Python                     │
└─────────────────────────────────────────────────┘
```

Подробнее: [ARCHITECTURE.md](ARCHITECTURE.md)

## Модель доказательств

Каждое значимое утверждение проходит оценку по уровням:

| Grade | Статус | Требование |
|-------|--------|------------|
| E0 | `unverified` | Нет проверки |
| E1 | `integrity_checked` | Проверка целостности цитирования |
| E2 | `single_method_supported` | Один метод в рамках scope |
| E3 | `independently_corroborated` | Независимые источники или репликация |
| E4 | `formally_verified` | Формальная верификация или многократная репликация |

Grade вычисляет версионируемый rules engine — не LLM, не verifier. Claim assessment строится по evidence set, claim type rules, independence groups и scope.

### Типы утверждений (v1)

| Тип | Минимальный grade для `supported` |
|-----|----------------------------------|
| `local_observation` | E2 |
| `computed_result` | E2 |
| `formal_theorem` | E4 |
| `empirical_conjecture` | E3 (≥2 независимых экспериментов) |
| `procedural` | E3 (≥2 успешных репликаций) |
| `external_fact` | E3 (≥2 независимых источника) |
| `temporal_fact` | E3 + временной scope |
| `self_model` | E2 |

## Безопасность

**Модель нарушителя:** внешняя страница, сообщение человека, LLM, код в sandbox и артефакты прошлых сессий считаются недоверенными.

Защита слоями:

- **Изоляция** — одноразовый rootless-контейнер, read-only rootfs, no network, cap-drop ALL
- **Capability security** — права задаются профилем, содержание контекста их не расширяет
- **Provenance** — каждый chunk имеет origin, hash, transform chain и trust class
- **Evidence rules** — grade вычисляется детерминированно, не моделью
- **Наблюдаемость** — operational timeline из audit events, не из нарратива модели
- **Операторское подтверждение** — для действий с внешним эффектом

### Режимы доступа к интернету

| Режим | Поиск | Egress |
|-------|-------|--------|
| **Sealed** | Локальный индекс | Нет |
| **Curated** | SearXNG через Research Proxy | Контролируемый |
| **Open Lab** | Внешний API | Разрешённые домены |

## Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.11+ |
| Веб-фреймворк | FastAPI + Pydantic |
| База данных | PostgreSQL 15+ (опционально pgvector) |
| ORM / миграции | SQLAlchemy + Alembic |
| Веб-UI | HTMX + Server-Sent Events |
| Сэндбокс | Rootless Podman или Docker |
| Оркестрация | systemd 249+ |
| LLM-бэкенд | llama.cpp / Ollama / vLLM |
| Тесты | pytest (unit, scenario, security, model_compatibility) |

### Требования к железу (ориентир)

Модель класса **30B в Q4** — ~24 GB VRAM, 64 GB RAM.

## Структура репозитория

```
noezema/
├── apps/
│   ├── orchestrator/        # Машина состояний сессий
│   ├── web/                 # FastAPI: Query API + Command API + UI
│   └── research_proxy/      # Контролируемый egress
├── packages/
│   ├── domain/              # Доменные модели, схемы, constraints
│   ├── llm_gateway/         # OpenAI-compatible API, fingerprint
│   ├── cognition/           # Curiosity Engine, Context Builder
│   ├── memory/              # Claims, evidence, assessments, rules
│   ├── policy/              # Capability checks, profiles
│   ├── tool_broker/         # Типизированные инструменты, retry
│   └── observability/       # Метрики, audit, outbox
├── sandbox/
│   ├── Containerfile        # Базовый образ sandbox
│   └── policy/              # Security profiles
├── prompts/
│   ├── identity.md          # Документ идентичности
│   ├── explorer.md          # Промпт исследователя (MVP)
│   ├── curator.md           # Промпт куратора (MVP)
│   └── verifier.md          # Промпт верификатора (этап 4)
├── docs/adr/                # Архитектурные решения
├── migrations/              # Alembic миграции
├── infra/
│   ├── compose.yaml         # Локальная разработка
│   └── systemd/             # Production supervisor + host-recovery.defaults.toml
└── tests/
    ├── scenarios/           # Сценарные тесты сессий
    ├── security/            # Invariant и failpoint-тесты
    └── model_compatibility/  # Compatibility suite для LLM
```

## Этапы реализации

| Этап | Что | Gate |
|------|-----|------|
| **1. Контракты и LLM** | Enums, schemas, host-generated IDs, LLM Gateway, FIFO Selector, minimal explorer/curator | Sealed-путь question → evidence → assessment → commit |
| **2. Изоляция и commit** | Sandbox, capability policy, Tool Broker, COW/staging, revision vector, fenced reconciliation | Unknown COMMIT reconciled, живой finalizer не принят за rollback |
| **3a. Память и доказательства** | Claims/evidence, versioned assessment heads, systemd-owned offline rules change с verification seal, single active head + current/event journal, root-owned recovery policy и точные durable retry timers, conservative source grouping | Старый либо полный новый pointer+questions; один unresolved transition, DB outage — `retry_wait`, crash-loop — `resume_degraded`, inconsistency/policy error — `resume_blocked`, ни один start не обходит admission |
| **3b. Зависимости и переоценка** | Closure manifests, resumable barriers, reassessment worker, fenced online rules activation, full grouping, resolutions | Invalid ancestor блокирует downstream; stale activator/repair не меняет effective config |
| **4. Расширенный познавательный цикл** | Curiosity ranking, planning, specialized verifier/curator, защита от повторений | Verifier не назначает grade |
| **5. Research Proxy** | SSRF-safe fetch/search, provenance, injection tests | Внешний текст не меняет capabilities |
| **6. Веб-модуль** | MVP: status/timeline/messages/controls и read-only degraded host status; затем knowledge graph и diagnostics | Без system-bus access; stale snapshot или любой unresolved host transition fail-closed закрывает Command API |
| **7. Эксплуатация** | Backup/PITR, GC, security regression, 50–100 сессий | §22.1 → frozen evaluation → §22.2 full acceptance |

MVP — этапы 1, 2, 3a + минимальный web slice: status, SSE timeline, messages, controls и degraded host recovery view. Он уже выполняет полный минимальный познавательный путь с локальной LLM. Этап 3b начинается после серии реальных сессий: пороги очереди и SLO выводятся из измеренной нагрузки.

## Не-цели первой версии

- Доказательство сознания или субъективного опыта
- Неограниченный доступ к хостовой системе
- Выполнение произвольных действий от имени владельца
- Multi-agent orchestration ради сложности
- Дообучение основной LLM во время работы
- Публичное раскрытие скрытой chain of thought

## Статус

📝 Архитектурный draft v0.23 — документация без кода.

[Полная спецификация](ARCHITECTURE.md) описывает 22 раздела: архитектурные принципы, компоненты, машину состояний сессий, модель памяти, evidence grading, безопасность, воспроизводимость, веб-модуль, модель данных (40 таблиц), надёжность и восстановление, наблюдаемость, стек, структуру, этапы, риски, открытые вопросы и критерии успеха.

## License

[Apache 2.0](LICENSE)
