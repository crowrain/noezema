# ADR-0027: Единый wiring research-сервиса для всех точек входа сессий; явная семантика egress в режиме curated (T7.66 → T7.68+, §5.12, §11.2)

Статус: proposed

Дата: 2026-10-06 (анализ — T7.66, `docs/web-access-design.md`; харнесс-реализация ещё
не началась; пункты 2–4 требуют явного решения пользователя)

Контекст: живой случай на dev-стенде `.92` («Какая инфляция в России за 2025 год?»,
сессия `099ddccb…`, config-v13, executor sandbox, запуск wake_now из web UI):
`policy_evaluated decision=allow profile_version=curated-v1`, затем `action_failed`
«research proxy is not configured for this host». Контекст wiring: ADR-0023 (`build_orchestrator`
как сборщик сессий), M6 (T6.1–T6.4, `docs/STATUS-archive.md:97–103`), зазор wiring найден и
закрыт для tick/eval ещё при подготовке EVAL-3 (`docs/STATUS-archive.md:1948–1955`,
`tests/unit/test_research_wiring.py`).

## Контекст (факты анализа T7.66)

1. Ошибка исходит из `apps/orchestrator/orchestrator.py:1067–1072`: оркестратор вызывается без
   `research_service`. Единственный сборщик, который его передаёт — `build_orchestrator`
   (`apps/orchestrator/main.py:54–62`); web-фабрика `apps/web/api.py::build_standalone_app`
   (`:2004–2036`) строит `Orchestrator(...)` напрямую и research-сервис не передаёт. Web-юнит
   стенда запускает именно эту фабрику (`deploy/dev-stand/systemd/noezema-dev-web.service:30`).
   Тестов на wiring web-пути нет (закреплён только `build_orchestrator`).
2. Профиль `curated-v1` = `{name}-{version}` YAML-потолка (`packages/policy/profiles.py:44–46`,
   `sandbox/policy/curated.yaml`) — политика fetch разрешает (сетевой режим не `none`,
   `packages/policy/engine.py:158–164`). Слово «sealed» в описаниях стенда — следы промпта
   explorer-v5 («режим Sealed: сети нет», `prompts/explorer/explorer-v5.md:5–10`), bootstrap-дефолтов
   (`packages/domain/config.py:119–120,185–197`) и устаревшей строки
   `docs/ui-simplification-design.md:113`; к активному снимку config-v13 отношения не имеют.
3. семантика режима на сегодня: доменный allowlist при fetch проверяется только в open_lab
   (`apps/research_proxy/service.py:102–107`); `allowed_domains` применяется только открытому
   списку open_lab (`apps/research_proxy/modes.py:125`). Значит **curated + пустой
   `allowed_domains` = fetch к любому публичному http(s)-хосту** под SSRF-guard'ом и лимитами —
   а не «запрет всего». Rate-limit (`20/3600`) считается только по upstream-поиску
   (`service.py:368–392`), прямые `research.fetch` количественно не ограничены. Инструмента
   поиска у модели нет (реестр `packages/policy/tools.py`; `docs/eval/EVAL-3-freeze.md:83`).
4. Сетевой выход при этом — только процесс оркестратора на хосте: контейнер сессии без сети при
   любом профиле (`apps/orchestrator/tool_executors.py:102–107`, ADR-0023). Для dev-стенда `.92`
   исходящие 80/443 сегодня обеспечены временными UFW-правилами оператора («TEMP package install»),
   SearXNG на стенде нет (есть только на dev-машине: `noezema-searxng`, `127.0.0.1:8888`).

## Решение

### 1. Единый конструктор research-сервиса (код, T7.68)

Все точки входа, собирающие оркестратор для `run_session` (wake-tick, eval-run/smoke, ручной
вход, web standalone/wake_now), получают `research_service` из одного чистого построителя
(AGENTS §4: чистая функция в общем модуле, фабрики только вызывают):
`ResearchProxyService(factory, FilesystemArtifactStore(<data root>/artifacts))` — артефактный
корень уже выведен env'ом (`apps/orchestrator/scheduler.py:122–131`). Обязательные тесты:
существующий `tests/unit/test_research_wiring.py` (расширить) + scenario через реальный
`build_standalone_app()` с env (FakeLLM, monkeypatched FetchClient, без реальной сети):
оркестратор web-приложения имеет research-сервис; путь wake_now с allowed `research.fetch`
не даёт ошибки «not configured». Контракт `run_session`, пины промптов и payload'ы не меняются.

### 2. Явная политика доменного egress (решение пользователя, T7.69)

Фиксируется один из вариантов (пока не выбран):
- **A.** curated остаётся «открытым fetchом» (любой публичный хост + SSRF-guard); закрытый список
  доменов — только осознанный open_lab; UFW-egress `.92` делается постоянным решением оператора;
- **B.** переход стенда на closed-list конфигурацию (open_lab с `allowed_domains=[…]`, новая
  config-версия; SearXNG в этом режиме недоступен — `service.py:319–320`) либо новую семантику
  «curated + список» (изменение поведения режима — новая харнесс-задача с тестами всех трёх режимов).

Доменный контроль возможен только в коде (UFW фильтрует по портам/IP); firewall-решения на `.92`
— зона ответственности оператора, не харнесса.

### 3. Ограничения прямых fetch (решение пользователя, отложено)

Прямые `research.fetch` не имеют количественного лимита (только повтор-гейт T7.12 и
bytes/redirect/time на запрос). Если оператор хочет per-session/per-window cap — отдельная задача
(T7.72) по образцу upstream rate-limit (audit-based, fail-closed).

### 4. Поиск модели и SearXNG на стенде (отложено до решения)

`research.search` в реестр не вводится без явного решения: новый инструмент меняет
tool_schema_hash прогонов, словари подписей, пины промптов (нужна новая config-версия), а
upstream-поиск раскрывает тему запроса внешним движкам (§5.12.1). Контроль раскрытия — log +
rate limit уже есть на уровне сервиса; SearXNG для него не «нейтрален».

## Следствия

- web-запуск после T7.68 физически сможет производить внешние факты; наблюдаемая ошибка
  «not configured» становится недостижимой ни на одном входе (это закрепляется тестом).
- Пункт 2 выбирает реальный риск-профиль стенда: без постоянного UFW-egress web-access случая
  невозможен; с curated-открытостью качество источников и поверхность инъекции (§11.2, fence'ы
  уже есть) остаются широкими.
- Противоречие промпта explorer-v5 («Sealed: сети нет») при активном curated сохраняется до
  отдельного решения о новой версии промпта (пины, config-версия) — анализ зафиксирован в
  `docs/web-access-design.md` G8.
