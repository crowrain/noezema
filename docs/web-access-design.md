# T7.66 — внешний доступ NOEZEMA: как это устроено сейчас, чего не хватает и что решать оператору

Дата: 2026-10-06. Тип задачи: анализ и проект (без харнесса, без кода). Ветвь
`impl/from-scratch`, HEAD `1c62e03`. Ничего не запускалось на стенде
`.92`; сеть не использовалась; к БД — только SELECT-счётчики служебных баз тестового
кластера.

Разбирается живой случай: вопрос «Какая инфляция в России за 2025 год?» (стенд
`192.168.1.92`, сессия `099ddccb…`, конфиг `config-v13`, исполнитель `sandbox`,
запуск кнопкой «Запустить обработку» / wake_now). В ленте сессии:
`policy_evaluated decision=allow profile_version=curated-v1`, затем `action_failed`
с ошибкой хоста **«research proxy is not configured for this host»**; разведчик ушёл в
`blocked`, 0 клеймов, итог `succeeded_partial/blocked`.

Гипотеза из брифа — **подтверждена полностью**: точка входа web standalone строит
оркестратор без research-сервиса (пункт 1 ниже). Ошибка не в сети стенда и не в политике:
политика действие разрешила, а исполнять его было нечем.

---

## 0. Краткий ответ одной фразой

Инструмент `research.fetch` на стенде разрешён политикой (профиль `curated-v1`), но
ни один web-запуск («Запустить обработку» / wake_now) не имеет исполнителя этого
инструмента: единственный сборщик research-сервиса — `build_orchestrator`, а web-приложение
собирает оркестратор самостоятельно и research-сервис не передаёт. Интернет-выход узла
для таких fetch — сам хост `.92` (не контейнер сессии), и он сегодня держится на
временных правилах UFW «TEMP package install».

---

## 1. Шаг 1. Что есть сейчас: точки входа сессий и ResearchProxyService

### 1.1 Инструмент `research.fetch` и его host-обработчик

- Инструмент объявлен в реестре: `packages/policy/tools.py:98–103`
  (`research.fetch`, аргумент `url ≤ 2000`, категория `NON_IDEMPOTENT`). В реестре
  **нет** инструмента поиска (`research.search` отсутствует во всём `tools.py`;
  зафиксировано и в `docs/eval/EVAL-3-freeze.md:83`: «research.search модель не вызывает
  (инструмента нет в реестре)». Модель умеет только скачать URL, который сама выбрала.
- Разрешение политики: `packages/policy/engine.py:158–164` — аргумент с URL
  запрещён **только** если сетевой режим профиля `none`. Для профиля
  `research_proxy` (curated) действие разрешается → отсюда `decision=allow` в
  наблюдаемой ленте; `profile_version` формируется в `packages/policy/profiles.py:44–46`
  как `{name}-{version}` от YAML-профиля (`sandbox/policy/curated.yaml`:
  `name: curated`, `version: v1`) → **`curated-v1`**.
- Обработка на хосте (не в контейнере): `apps/orchestrator/orchestrator.py:1667–1678` —
  оркестратор перехватывает `research.fetch` до брокера и вызывает
  `self._research_fetch(...)`; счётчик повторов одного инструмента с одними аргументами
  (`tool_call_counts`, строка 1665, правило T7.12) учитывает и такие попытки.
- `_research_fetch`: `apps/orchestrator/orchestrator.py:1044–1173`. Первые строки:

  ```python
  if self.research_service is None:
      return ToolObservation(error="research proxy is not configured for this host")
  ```

  (`orchestrator.py:1067–1072`). Это ровно та ошибка из ленты сессии `099ddccb…`.
  Оркестратор затем фиксирует `action_failed` c текстом ошибки в audit
  (`orchestrator.py:1723–1735`) и в observations-ленту разведчика.

### 1.2 Как сервис подключается (и где нет)

Конструктор оркестратора принимает `research_service: object | None = None`
(`apps/orchestrator/orchestrator.py:234`, присваивание `:248`). Реально его передаёт
только один сборщик:

- **A. `apps/orchestrator/main.py::build_orchestrator`** (`:28–64`): создаёт
  `ResearchProxyService(session_factory, FilesystemArtifactStore(workspace_root.parent / "artifacts"))`
  (`main.py:54–56`; артефактный корень = сосед workspace-каталога, `apps/orchestrator/scheduler.py:122–131`,
  `ARTIFACTS_SUBDIR = "artifacts"`) и передаёт его в оркестратор (`main.py:62`). Из него сессии
  собираются для:
  - **wake-tick** (юнит `noezema-dev-tick.service` → `hostctl/cli.py`, импорт
    `build_orchestrator` на `hostctl/cli.py:225`, вызов на `:303`);
  - **eval-run / смоуков** (`hostctl/cli.py:1131` импорт, `:1334` вызов);
  - **ручного входа** `python -m apps.orchestrator` (`main.py:76`).
- **B. Web standalone-app**: `apps/web/api.py::build_standalone_app` (`:2004–2036`)
  собирает gateway, tool-executor (`:2033`) и вызывает конструктор `Orchestrator(...)`
  напрямую (`:2023–2034`) — **без аргумента `research_service`**. Кнопка
  «Запустить обработку» (operator command wake_now) исполняет `run_session()` именно этого
  оркестратора: `apps/web/api.py:1444–1508` (admission `WakeScheduler` `:1456`, захват
  полосы `NodeSessionGuard` `:1470–1481` — T7.61(а), запуск задачи `:1506`). Web-юнит на
  стенде запускает это приложение: `deploy/dev-stand/systemd/noezema-dev-web.service:30`
  (`apps.web.main` → `app = build_standalone_app()`, `apps/web/main.py:36`).

Следствие (гипотеза подтверждена): **любой fetch, начатый web-кнопкой или wake_now, даёт
«research proxy is not configured for this host» независимо от конфига, UFW и SearXNG**.
Прямой tick (`noezema-dev-tick.service`), если бы он на `.92` работал, упал бы **другой**
ошибкой (сетевой транспорт fetch к публичному URL — при закрытом UFW без 80/443), а не этой.

Проверка тестами: wiring закреплён только для `build_orchestrator`
(`tests/unit/test_research_wiring.py`, docstring: до EVAL-3 «research proxy was not wired»);
web-фабрика тестами не покрыта (`tests/unit/test_web_api.py` — ни одного упоминания
research; `tests/scenario/test_web_standalone_wake.py` гоняет wake_now без research-шагов).

### 1.3 Что такое ResearchProxyService и от чего зависит

In-process библиотека внутри процесса sessions (не сетевой сервис): читает effective
config (`ConfigService.get_effective`, `apps/research_proxy/service.py:92–95` — fail-closed
без активного снимка), применяет режим (`modes.py`), SSRF-guard, fetch-limit; кладёт
оригинал+нормализованный текст в content-addressed store (артефактный корень <data root>/artifacts,
`apps/orchestrator/scheduler.py:105–131`) и пишет journal/audit. HTTP-обёртка для
standalone-режима существует (`apps/research_proxy/main.py:21–48`, `build_standalone_app()`
вызывается при импорте модуля, порт по умолчанию 127.0.0.1:8322; эндпоинты fetch/search/healthz —
`apps/research_proxy/api.py`) — **на dev-стенде никем не запускается**: в
`deploy/dev-stand/systemd/` нет юнита прокси, и research_proxy нигде в пакете dev-stand
не упомянут.

### 1.4 Где живёт SearXNG и кто его зовёт

- Реальная эксплуатация — только `.87`: контейнер `noezema-searxng`
  (`searxng/searxng:latest`, версия 2026.9.15), порт `127.0.0.1:8888 → 8080`, restart policy
  **`no`**, конфиг bind-mount `/home/denis/dsh1/searxng-config/settings.yml`
  (`use_default_settings: true; limiter: false; formats: html+json`) — проверено read-only
  `docker inspect`. В репозитории нет ни install-скрипта, ни systemd-юнита SearXNG.
- Вызывает его **только** `ResearchProxyService.search()`: upstream-запрос исполняется при
  режиме `curated` и наличии `searxng_url` (`service.py:312–362`, решение — `:332–334`;
  в sealed/open_lab upstream нет вовсе — open_lab выход = allowlisted fetch, не поиск,
  `service.py:319–320`). URL строится как `{base}/search?format=json&q=…`
  (`apps/research_proxy/search.py:116–121`); сам запрос идёт через тот же SSRF-guard
  FetchClient (`service.py:394–397`) и потому требует `private_allowlist` с точной записью
  `127.0.0.1:8888` — в config-v13 она есть (`docs/eval/config-v13-payload.json`).
- До модели поиск сегодня **не доведён**: инструмента поиска в реестре нет (п. 1.1), поэтому
  SearXNG жив, но модели не нужен: все реальные прогоны на `.87` делали прямые
  `research.fetch` названных URL (`docs/eval/SMOKE-V13-K2-report.md:102` — research.fetch ×15;
  `docs/eval/SMOKE-V14-EXL3-report.md:120–131,318–328` — ×16 allow / ×15 completed, «searxng за
  прогон не понадобился»). Перед прогонами preflight проверял его HTTP-200
  (`docs/STATUS.md:1473–1476`: `docker exec noezema-searxng curl http://127.0.0.1:8080/`).
- На `.92` SearXNG нет вообще (по данным оператора, там только `noezema-dev-db`) — и для
  самого `research.fetch` он не нужен; нужен только если решать вводить поиск (раздел 5, вариант D).

---

## 2. Шаг 2. Профиль, режимы и что значит «curated при пустом allowed_domains»

### 2.1 Режимы (ADR / M6-спека)

`docs/STATUS-archive.md:97–103` (гейт M6, T6.1–T6.4): **sealed** — сети нет, только локальный
индекс; **curated** — SearXNG через прокси с журналированием upstream и rate limit; **open_lab** —
fetch строго по закрытому списку доменов (`docs/STATUS-archive.md:1226`: «open_lab — непустой
закрытый список»). Реализация: `apps/research_proxy/modes.py:27–30` (enum),
fail-closed сборка `ModePolicy`:

- sealed **игнорирует** остатки backend-настроек (`modes.py:105–109`; тест
  `tests/unit/test_research_modes.py::test_sealed_ignores_backend_options`);
- curated **требует** `searxng_url` — пустой/без схемы → ConfigError (`modes.py:87–96,111–113`;
  тесты `::test_curated_requires_searxng_url`, `::test_bad_searxng_url_fail_closed`), egress включён;
- open_lab **требует** непустой закрытый список `allowed_domains` (`modes.py:98–103,115–117`;
  тест `::test_open_lab_requires_allowed_domains`);
- backend-параметры применяются только «своему» режиму: `searxng_url`/rate-limit — curated
  (`modes.py:123–124`), `allowed_domains` — **только open_lab** (`modes.py:125`).

### 2.2 Что означает пустой allowed_domains в curated (важно)

Проверка домена при fetch исполняется **исключительно для open_lab**:
`service.py:102–107` (`if mode_policy.mode is ResearchMode.OPEN_LAB and not
mode_policy.domain_allowed(url)` → reject). Значит **curated + `allowed_domains: []` = «нет
закрытого списка»**, а не «запрет всего»: любой публичный http(s)-URL проходит, если
разрешён SSRF-guard'ом и лимитами. Это поведение закреплено тестом open_lab-закрытия
(`tests/scenario/test_research_modes.py::test_open_lab_fetch_is_domain_closed` — `:278`) при
том, что ни один тест не ограничивает curated по домену. То есть config-v13 сегодня означает:
«модель может скачать любой публичный сайт; поиск через SearXNG доступен сервису (не модели);
приватные адреса запрещены, кроме `127.0.0.1:8888`».

### 2.3 Почему на стенде написано «curated-v1», а в описании — «sealed»

- `curated-v1` = policy_version effective-профиля снимка config-v13
  (`profiles.py:44–46`; `sandbox/policy/curated.yaml` name/version; ceiling-сужение snapshot'ом —
  `profiles.py:69–102`). Профиль в активном снимке действительно curated (bootstrap активирует
  config-v13: `deploy/dev-stand/bootstrap.sh:78–85,558–562` → activate-online payload
  `docs/eval/config-v13-payload.json`; там `research_proxy.mode="curated"`).
- Слово «sealed» в описаниях — следы трёх других мест, не связанных с активным конфигом:
  1. **промпт разведчика говорит неправду**: `prompts/explorer/explorer-v5.md:5–10` — «Ты
     работаешь в режиме Sealed: сети нет» (и нигде не называет `research.fetch`; список
     инструментов модель получает из контекста хостом: orchestrator.py:1294
     «# Доступные инструменты» из cap_profile.tools, т.е. research.fetch в списке был);
  2. **bootstrap-дефолты** конфига (до активации / при skip активации): sealed access_profile и
     sealed mode — `packages/domain/config.py:119–120,185–197`;
  3. **устаревшие формулировки документов**: `docs/ui-simplification-design.md:113` «Профиль
     `sealed` (нет сети) на стенде» (строка писалась до curated-конфигов).
- Разграничение для оператора: лента сессии `099ddccb…` доказывает только `access_profile=curated`
  (policy_evaluated). `research_proxy.mode` активного снимка из этой ошибки **не** проверяется:
  `_research_fetch` упал до обращения к сервису (`orchestrator.py:1067–1072`), поэтому «sealed mode»
  vs «curated mode» на `.92` данным наблюдением не различимы; косвенно — payload v13. Это можно
  снять только SELECT'ом по БД стенда (вне этой задачи) или после починки wiring.

### 2.4 SSRF-guard, лимиты и sandbox-сеть (граница безопасности fetch)

- Guard (`apps/research_proxy/ssrf_guard.py`): только http/https, без credentials
  в URL (`:93–113`); адрес блокируется, если он private/loopback/link-local (метаданные
  169.254.169.254 выделены явно — `:35`)/reserved/multicast/unspecified, обе стеки; IPv4-mapped
  Special-case (`:116–141`). Исключение — **exact**-запись `host` или `host:port` из
  `private_allowlist` (`:85–90`); hostname-мишени проверяются по всем DNS-ответам
  (`check_host`, `:144–154`).
- PINING и redirect'ы: транспорт резолвит имя один раз, валидирует **каждый** полученный
  адрес и пинит первый валидный (`apps/research_proxy/backend.py:37–83`), TLS SNI по исходному
  имени; unix-sockets отказаны (`:85–91`). FetchClient пере-валидирует целевой адрес на каждом
  hop'е, лимит redirect'ов и размер ответа, общий timeout (`fetch.py:75–150`; лимиты config-v13:
  4 MiB, ≤3 redirect'а, 10 с).
- Лимит запросов: cluster-wide rate-limit `20/3600 с` считается **только по upstream-поиску**
  (audit `research_upstream_request`, `search.py:124–139`; проверка перед запросом —
  `service.py:368–392`, отказ с записью в audit). **Прямые `research.fetch` нигде не
  лимитированы по количеству** — только повтор-гейт оркестратора на одинаковые аргументы
  внутри одной сессии (`orchestrator.py:1665`, T7.12).
- Контейнер сессии при curated всё равно без сети: `_NETWORK_FOR_CONTAINER` = `none` для обоих
  сетевых режимов (`apps/orchestrator/tool_executors.py:102–107`; ADR-0023, §3 AGENTS.md).
  **Единственный сетевой выход — процесс оркестратора на хосте.** Для `.92` это означает: выход в
  интернет = исходящие TCP процесса noezema-user.

---

## 3. Шаг 3. Как fetch становится фактом (цепочка до карточки ответа)

1. Fetch возвращает **envelope без текста** (`service.py:290–304`): sha originals/normalized,
   media type, `trust_class="external"`, предупреждение о fence (`UNTRUSTED_EXTERNAL`, `:62`).
2. Оркестратор читает нормализованный текст из content store (`orchestrator.py:1081–1100`) — если
   формат не нормализуется (нормализуются только html/plain/markdown/json,
   `apps/research_proxy/normalization.py:58–85`), fetch завершается ошибкой «no normalized text
   available for this content» (`orchestrator.py:1095–1100`). Практически: PDF/XLSX-страницы
   регуляторов (типично для cbr.ru/rosstat) — этот случай; текст нужен HTML-страница.
3. Текст кладётся в контекст модели **только за fence'ом** с бюджетом 40 000 символов
   (`RESEARCH_CONTEXT_BUDGET`, `orchestrator.py:132,1101–1127`) и сопровождается host-derived provenance
   (sha источника, id артефакта, «недоверенный внешний контент»); сам факт чтения пишется в audit
   `research_content_read` (`:1128–1140`). Инструмент остаётся NON_IDEMPOTENT, но повторная
   загрузка идемпотентно переиспользует source/chunk (тесты
   `tests/scenario/test_research_proxy.py::test_refetch_is_idempotent_reuses_source_and_chunk`).
4. Evidence: observation → `source_assertion` с идентичностью из sha оригинала + chunk-id
   (`apps/orchestrator/evidence.py:174`; текст-бюджет фрагмента 2×2000 символов — `:62–77`, T7.22),
   источник заводится как external source (canonical_uri = fetch URL). Независимость групп — по
   registrable domain (`packages/memory/independence.py:109,163`): два документа с cbr.ru =
   одна группа (тест `tests/scenario/test_research_evidence.py::test_same_registrable_domain_is_one_group`).
5. Гейт grading'а external/temporal факта в config-v13 (`claim_type_rules`): E3 требует **≥2
   групп источников и ≥2 evidence**; с одним доменом клейм останется ≤E1/hypothesis (confidence
   penalty `min(1, groups/2)`). Для «инфляции 2025» минимальная рабочая пара — два разных
   регуляторных домена (например cbr.ru + rosstat.gov.ru).
6. Host-гейт покрытия источников (ADR-0010) гейтит только URL, **названные в тексте вопроса**
   (`apps/orchestrator/source_coverage.py:49–52` → `extract_question_urls`,
   `packages/memory/scope.py:237`). Вопрос «Какая инфляция в России за 2025 год?» без URL →
   трекер не создаётся (`orchestrator.py:664–666`) и гейт раскрыт по умолчанию;
   fetch с ошибкой тоже «покрывает» названный источник (`source_coverage.py:116–127`,
   `orchestrator.py:1695–1700`). Отказанный политикой fetch не помечает ничего.
7. Freshness: срок перепроверки (`reverify_after`) существует **только у relative-анкоры**
   (ADR-0017; `packages/memory/rules_engine.py:263–279`, closed-списки дат/relative-форм —
   `packages/memory/scope.py:124–129,149–158`). «за 2025 год» не входит ни в закрытые формы дат
   (`_DATE_FORMS`: день-месяц-год), ни в relative-паттерны («на текущую дату», «сейчас») →
   анкоры нет, as_of = модельное (или пустой), claim **evergreen** без срока. Для прошлого периода
   это согласуется с ADR-0017 («утверждение о фиксированной точке не перепроверяется»), но означает:
   уточнения статистики после публикаций не поднимут переоценку сами — см. вопрос 6 раздела 8.

На UI сегодня: шаги ответа собираются только из **выполненных** действий (research.fetch с
доменом-подписью) — `apps/web/answer.py:165–206,227–235`; отказанные действия попадают в счётчик
«есть неуспешные шаги» без причины; честные замечания различают «сети нет» (snapshot network=none)
и «внешние источники не использовались» (`answer.py:324–341`, `apps/web/labels.py:1400,1405`).
Провенанс источников (canonical_uri) доступен в knowledge/claim-API (`apps/web/knowledge.py:231,389,539`);
карточка ответа источника пока не показывает.

---

## 4. Шаг 4 (продолжение): список пробелов G1–G10

- **G1 (код, корень наблюдаемой ошибки).** Web-фабрика `build_standalone_app`
  (`apps/web/api.py:2004–2036`) не передаёт `research_service`; единственный сборщик —
  `build_orchestrator` (`apps/orchestrator/main.py:54–62`). Тестов на wiring web-пути нет.
  Стенд «web wake» сегодня физически не способен на внешний факт.
  → **Закрыт T7.68 (коммит `cbac9e2`)**: сборщик сессии — общий чистый модуль
  `apps/orchestrator/session_assembly.py`, web-фабрика вызывает только его; тесты Wiring и
  end-to-end wake_now зафиксированы (красны на старом коде) — см. п. 6 плана ниже и STATUS §T7.68.
- **G2 (семантика curated).** При пустом `allowed_domains` curated открывает fetch к любому
  публичному хосту (п. 2.2): доменный allowlist как принудительный гейт реализован только у
  open_lab. Это либо осознанная политика, либо дыра в ожиданиях оператора. Требует явного
  решения (ADR-0027).
- **G3 (нет SearXNG на .92).** Контейнера нет; он не нужен для `research.fetch`, но блокирует
  любой будущий search-путь. Образ/настройка есть только как практика `.87` (вне репозитория).
- **G4 (сеть .92 = UFW, временная).** Исходящие 80/443 — подписанные «TEMP package install»,
  снимаемые в любой момент; DNS 192.168.1.1:53 разрешён. Без постоянных правил fetch к cbr.ru
  просто не соединится (другая ошибка, транспортная). UFW фильтрует по портам/IP, домены фильтровать
  не умеет → реальный доменный контроль — только в коде (`allowed_domains` / закрытый список),
  поэтому G2 и G4 решать вместе.
- **G5 (нет лимита прямых fetch).** Rate-limit только у upstream-поиска (п. 2.4). Прямые загрузки
  не ограничены по количеству за сессию/окно; защита от зацикливания — повтор-гейт T7.12 на
  идентичные аргументы. Volume-политика для открытого fetch не определена.
- **G6 («за 2025 год» → evergreen).** см. п. 3.7: анкор none, `reverify_after` NULL; вопрос без
  relative-формулировки никогда не станет «о настоящем». Если нужна перепроверка уточнений —
  менять closed-списки форм дат в `scope.py` (правила, не веб) → отдельная задача + решение.
- **G7 (E3 требует двух доменов).** Правило config-v13 + группировка по registrable domain
  (п. 3.5). Для одной темы модель должна найти два независимых источника — в промпте это только
  правило 7 про названные вопросом URL (`prompts/explorer/explorer-v5.md:55–59`), подсказки про
  «два первичных источника» и про research.fetch нет.
- **G8 (промпт врёт про режим).** explorer-v5 объявляет «Sealed: сети нет» при активном curated —
  модель может не попытаться использовать `research.fetch`, а попытки объяснять «случаем». Любая
  правка = новый файл `explorer-v6.md` + смена pins в payload (config-vNN) — трогает замороженные
  rules/prompts (решение пользователя, см. AGENTS §4/§8).
- **G9 (поиска нет у модели).** Поиск есть на уровне сервиса и HTTP-API, но не как инструмент
  модели; введение `research.search` = изменение реестра (`tools.py`) → tool_schema_hash, словари
  подписей (`labels.py` completeness), промпт-пины, новый payload — отдельный пакет (п. 5 вариант D).
- **G10 (UI отказа).** Причина отказа fetch не видна человеку («есть неуспешные шаги: N»), источники
  выполненных fetch на карточке ответа не показаны (данные в API есть — canonical_uri; display —
  этап UI-3, теперь T7.67).

---

## 5. Варианты включения внешнего доступа и рекомендация

Все варианты предполагают **сначала** починить G1 (web-wiring) — без него web-запуск никогда не
будет иметь выхода; дальше выбирается только политика/config/инфраструктура.

- **A. «curated как есть»** (config-v13 без изменений по хешам): wire web → fetch работает через
  процесс оркестратора на `.92`; SearXNG не нужен вовсе (модель сама называет URL — фактические
  смоуки делали именно прямые fetch'и названных моделью источников, V13/V14). Цена: постоянные
  UFW allow-out 80/443 (или разрешение по списку IP,
  что для CDN нереалистично). Домен-политика: любой публичный сайт. Минимальная дистанция до
  работающего случая «инфляция 2025».
- **B. open_lab с закрытым списком**: `mode=open_lab`, `allowed_domains=[cbr.ru, rosstat.gov.ru, …]`
  (+ профиль open_lab — шире ресурсы, `sandbox/policy/open_lab.yaml`). Доменный контроль в коде;
  SearXNG в этом режиме **не работает** (поиск без upstream — `service.py:319–320`), значит вариант B
  = «fetch по списку». Цена: новая config-версия + решение о профиле (open_lab vs curated+список —
  кода для последнего нет: принудительный доменный список реализован только у open_lab).
- **C. Гибрид A→B**: старт на A (никаких новых payload'ов, только код G1), наблюдение за реальным
  качеством источников на живом стенде; затем по решению оператора переезд на B/new config-vNN со
  списком доменов или curated+домен-гейтом (тогда харнесс-задача на изменение semantics в
  `modes.py/service.py` с тестами — поведение всех трёх режимов закреплено тестами, менять аккуратно).
- **D. Поиск модели (research.search + SearXNG на .92)**: надстройка над A/B; добавляет риск утечки
  темы запроса наружу (§5.12.1 upstream log), новый инструмент и новую config-версию.
  **Решение принято пользователем 2026-10-06: D принят поверх A** (см. ADR-0027, «Решения пользователя
  2026-10-06», пункты a–c).

**Рекомендация:** C (сначала G1-фикс без изменения конфига/пинов; политика egress на время наблюдения —
A со списком UFW-решений оператора), параллельно записать в ADR-0027, что «curated+пустой список =
открытый fetch» есть осознанное поведение или признанная дыра. D и G6/G8 — только по отдельным решениям.

> Статус рекомендации на 2026-10-06 (T7.69b): часть решений вынесена пользователем и зафиксирована в
> ADR-0027 — egress `.92` делается постоянным (вариант A по доменам: пустой `allowed_domains` = любой
> публичный http(s)-хост под SSRF-guard'ом), поиск принят (D поверх A). Аналитика вариантов ниже не
> переписывается: она остаётся основанием, по которому эти решения были выбраны. G6/G8 (переформулировка
> «за 2025 год» и ложь промпта о режиме) — всё ещё открыты и закрываются вместе с explorer-v6/config-v14.

---

## 6. План задач харнесса (после этой аналитики)

Нумерация (уточнена в T7.69b): этап UI-3 перенесён в **T7.67**; web-access-задачи начинаются с T7.68.
Номер **T7.69 занят** серией подбора модели (T7.69a/c — прогоны MODELSEL, T7.69b — разбор
`docs/eval/MODEL-SELECTION-report.md`), поэтому позиция «запись решения по G2» из нумерации выведена:
она **закрыта решением пользователя 2026-10-06 (a)** и задачей харнесса не является. Дальнейшие позиции
сохраняют номера, но меняют статус: **T7.70** (SearXNG на стенде) и **T7.71** (`research.search`)
приняты решением (c), **T7.72** (лимиты прямых fetch) остаётся отложенным.

- **T7.68 — единый сборщик sessions: research_service во всех точках входа.** → **реализовано
  2026-10-06 (коммит `cbac9e2`, разбор в STATUS §T7.68); G1 закрыт.**
  Извлечение чистого конструирования (например helper `research_service_for(factory, workspace_root)`,
  общий для `apps/orchestrator/main.py` и `build_standalone_app`; AGENTS §4: чистая функция в общем
  модуле, фабрики лишь собирают). Файлы: `apps/web/api.py`, `apps/orchestrator/*` (helper), тесты —
  расширить `tests/unit/test_research_wiring.py` и scenario-тест поверх `build_standalone_app`
  (wake_now + fake LLM + monkeypatched FetchClient; без реальной сети). Acceptance: оркестратор,
  собранный web-фабрикой, имеет ResearchProxyService с артефактным корнем `<data root>/artifacts`;
  ошибка «not configured» недостижима ни на одном входе. Stop-criteria: не менять контракт
  `run_session`, пины, payload'ы; тесты только локальные (fake origins как в test_research_proxy.py).
  Фактическая реализация: чистый модуль `apps/orchestrator/session_assembly.py` с полным построителем
  сессии (`build_session_orchestrator` = gateway+profile+executor+orchestrator; внутри —
  `research_service_for` и `artifacts_root_for`, корень артефактов = sibling workspace'а точки входа,
  прежняя формула `build_orchestrator`; веб-workspace берётся из `resolve_standalone_workspace(data_root_from_env())`)
  — оба фабрики теперь только вызывают его. Вариант «helper только на research_service_for» отклонён: он
  оставил бы в api.py дублирующую сборку gateway/profile/executor, из которой точки входа и могли бы
  снова разойтись. Тесты: +2 unit (wiring веб-фабрики; tick/web эквивалентность) в
  `tests/unit/test_research_wiring.py` и scenario `tests/scenario/test_web_standalone_research.py`
  (wake_now через реальную фабрику, FakeLLM + loopback fake origin без сети); краснота на старом коде
  зафиксирована в STATUS §T7.68. Stop-criteria соблюдены: контракт `run_session`, payload'ы, пины и
  политика не изменены.
- ~~**T7.69 — запись решения по G2 и (при необходимости) config-v14**~~ → **закрыто решением
  пользователя 2026-10-06 (a)+(b)** (ADR-0027, раздел «Решения пользователя 2026-10-06»): выбрана
  открытая семантика A (пустой `allowed_domains` = любой публичный http(s)-хост под SSRF-guard'ом),
  новая config-версия для egress-политики не вводится; постоянный исходящий 80/443 на `.92` — действие
  оператора, не харнесса. Если позже потребуется closed-list (вариант B), он вернётся как отдельная
  задача с новым номером и новым payload-файлом (`config-vNN`), прежние payload'ы не переписываются.
- **T7.70 — dev-stand пакет для SearXNG (принято решением (c)).** Юнит/скрипт в `deploy/dev-stand`
  (docker run с портом `127.0.0.1:8888`, settings.yml в пакете), preflight-галочка в статусе. Stop-criteria:
  не трогать UFW; образ не пуллить без разрешения оператора (пуллинг = сетевой выход).
- **T7.71 — пакет «поиск для модели» (research.search) (принято решением (c)).** Реестр инструмента, schema-hash тесты,
  labels completeness, промпт-обновление (explorer-v6), config-vNN с pins; e2e через fake SearXNG по
  образцу `tests/scenario/test_research_modes.py`. Stop-criteria: не включать без решения о риске утечки
  темы (§5.12.1) и о пересмотре замороженных corpus/eval-воспроизводимости — то есть само решение «поиск
  нужен» получено, а решение о раскрытии темы внешним движкам и о пересмотре корпусов всё ещё нужно.
- **T7.72 — лимиты прямых fetch (G5).** Rate/объём-политика fetch'ей за сессию/окно в research_proxy по
  образцу upstream rate-limit (audit-based, fail-closed). Acceptance: лимит конфигурируем из payload;
  тест отказa c audit-записью. Stop-criteria: не ломать существующие smoke-арифметики без решения.

Мелкие UI-хвосты G10 (показать источники fetch и причину отказа на карточке ответа) — в T7.67 (этап
UI-3), вне этой линейки.

---

## 7. Оператор: точные действия на `.92` (и rollback). Я их не исполняю

Диагностика (read-only):

```
sudo ufw status verbose
docker ps -a --format '{{.Names}} {{.Status}}' | grep -i searxng
ss -ltn | grep 8888            # слушает ли что-то на loopback:8888
```

7.1 Постоянный egress (решение оператора; без него web-access случая невозможен):

```
# сделать временные правила постоянными (пере-комментировать):
sudo ufw status numbered      # найти две TEMP-правила 80/tcp,443/tcp out
sudo ufw delete <номер>       # удалить старое правило (или оба)
sudo ufw allow out 80/tcp  comment 'noezema research proxy'
sudo ufw allow out 443/tcp comment 'noezema research proxy'
```

Rollback: `sudo ufw delete allow out 80/tcp` и аналогично 443 (или удаление по номеру из
`ufw status numbered`).
Альтернатива оператора: оставить правила как есть (риск снятия) или закрыть egress совсем — тогда
вариант ответа на случай только вычислительный/локальный, web-факты невозможны.

7.2 SearXNG на `.92` — **только** если принято решение D (search модели):

```
mkdir -p ~/searxng-config
# скопировать settings.yml образца .87 (limiter: false; formats html+json)
docker run -d --name noezema-searxng --restart unless-stopped \
  -p 127.0.0.1:8888:8080 \
  -v ~/searxng-config/settings.yml:/etc/searxng/settings.yml \
  searxng/searxng:latest
# проверка (та же, что preflight .87): docker exec noezema-searxng curl -s http://127.0.0.1:8080/
```

(Пулл образа требует разрешённого 443-out.) Rollback: `docker rm -v -f noezema-searxng` (контейнер и
его безымянный cache-volume; конфиг-файл остаётся). Конфиг research_proxy при этом уже содержит
`searxng_url=http://127.0.0.1:8888` и allowlist `127.0.0.1:8888` — менять payload ради самого
контейнера не нужно.

7.3 Применение кодового фикса T7.68 на стенде (после мержа ветви): redeploy bundle +

```
sudo systemctl restart noezema-dev-web.service   # оркестратор web-процесса пересобирается со сервисом
```

Rollback — откат пакета на прежний bundle и повторный restart. Смена конфига (после T7.69b она нужна
только вместе с T7.71 — config-v14 из-за нового offered-списка; отдельной задачи «смена egress-конфига»
больше нет, решение (a) оставило curated как есть) — через
activate-online по образцу `deploy/dev-stand/bootstrap.sh:558–562`:

```
# NOEZEMA_DATABASE_URL — то же значение, что в /etc/noezema/dev.env (его читают юниты);
# сам URL с паролем в документы не выписываем.
sudo -u <пользователь стенда> env NOEZEMA_DATABASE_URL="<из /etc/noezema/dev.env>" \
  <repo>/.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-vNN-payload.json --reason "T7.71: …"
```

Rollback активации — осознанное решение оператора: активация прежнего payload той же командой
(правило «новый номер = новый файл», AGENTS §8; процедуры quiesce-бара и пере-оценки голов —
ADR-0009 / разбор T7.19–T7.20 в архиве STATUS).

7.4 Контрольный живой прогон (после T7.68; только с согласия оператора): wake_now того же вопроса на
`.92`; ожидаемый здоровый след: `policy_evaluated allow` → `action_completed research.fetch` ×≥2 (два
домена) → `research_content_read` → claim external_fact ≥E1…E3. При transport-ошибке — смотреть
UFW/DNS (п. 7.1); при «no normalized text» — формат источника (PDF/XLSX).

---

## 8. Вопросы оператору (решения вне харнесса)

Статус на 2026-10-06: вопросы 1–3 **закрыты решениями пользователя** (a)/(b)/(c), записанными в
ADR-0027 («Решения пользователя 2026-10-06»); их формулировки оставлены без изменения, потому что
остаются описанием вариантов. Вопросы 4–7 остаются открытыми.

1. **Доменная политика curated:** оставляем «любой публичный сайт» (A) или переходим к закрытому
   списку доменов (B; какой список: cbr.ru, rosstat.gov.ru, …)? Если B — через open_lab-профиль или
   новую семантику «curated+список» (кодовая задача).
   → **Закрыто (a): выбран A**, куруемый список с пустым `allowed_domains` = любой публичный
   http(s)-хост под SSRF-guard'ом.
2. **UFW .92:** делаем 80/443-out постоянными (какие комментарии) или убираем их вообще? От этого
   зависит сам вопрос «возможны ли внешние факты на этом стенде».
   → **Закрыто (b): 80/443-out становятся постоянными**; правила меняет оператор-менеджер.
3. **SearXNG и search модели (D):** вводить или жить без поиска? Принять риск раскрытия темы запроса
   upstream-движкам (§5.12.1, upstream log).
   → **Закрыто (c): поиск нужен** — SearXNG на стенде (T7.70) и `research.search` модели (T7.71).
   Риск раскрытия темы при этом отдельно не снят: он обязан быть учтён в T7.71 (upstream log + лимит).
4. **Лимиты прямых fetch (G5):** нужен ли rate/объём-кап и какой (per session/per сутки)? — открыто
   (задача T7.72 в плане, решения по значению нет).
5. **Промпт explorer-v6 (G8):** согласны исправить «Sealed: сети нет» и добавить инструкции про
   первичные источники — это новые пины и новая config-версия? — открыто (выйдет вместе с T7.71).
6. **Freshness («за 2025 год», G6):** оставляем evergreen или добавляем закрытую форму «за <год> год»
   в формы дат (меняет classification правил)?
7. **Разрешаете ли controlled run на .92** после T7.68 (один wake_now того же вопроса) для проверки
   end-to-end? Что делать с временными TEMP-правилами до прогона? — открыто; решение (b) снимает
   половину вопроса (TEMP-правила заменяются постоянными), согласие на прогон по-прежнему нужно явно.

---

## 9. Зафиксированные наблюдаемые факты и границы анализа

- Кодовая гипотеза подтверждена: web-фабрика без `research_service` (`apps/web/api.py:2023–2034`),
  ошибка ровно из `orchestrator.py:1067–1072`.
- Все выводы о `.92` — из формулировки брифа (данные менеджера); стенд не трогался. UFW-факты,
  отсутствие SearXNG и TEMP-правила приняты как входные данные.
- Сеть (кроме docker/локального тестового кластера) не использовалась; запросы к cbr.ru/searxng
  не выполнялись; контейнеры не менялись (только read-only `docker inspect` на dev-машине).
- Что нельзя утверждать без SELECT по БД `.92`: фактическое значение `research_proxy.mode` активного
  снимка стенда (ошибка «not configured» наступает раньше обращения к сервису — см. п. 2.3).
