# ADR-0023: Переключатель инструментарного исполнителя сессии — `NOEZEMA_TOOL_EXECUTOR` (stub | sandbox), жизненный цикл одноразового контейнера в `run_session` (T7.58, §5.7, §11.2)

Статус: accepted

Дата: 2026-10-04

Контекст: рекомендация **Б** раздела T7.57 `docs/STATUS.md` («перевести
eval-run/смоуки на реальный SandboxToolBroker, как отдельная задача с замером
wall-clock стоимости»); `apps/orchestrator/main.py::build_orchestrator`
(единственный сборщик сессий для wake tick, eval run, ручного входа и web app),
`packages/broker/broker.py::SandboxToolBroker` (исполнял инструменты в
контейнере только в тестах), `apps/orchestrator/orchestrator.py`
(`run_session`, freeze workspace), `docs/eval/SMOKE-V14B-EXL3-report.md` §…
(shell.execute ×1 → FAILED «без последствий»).

## Контекст

До T7.58 eval-run, wake tick и смоуки исполняли инструменты через
`StubToolExecutor` — M1-подставку: python на хосте (`sys.executable -I`),
workspace — общая директория `root/"workspace"`, изоляции нет (DEV ONLY по
docstring модуля), `shell.execute` не реализован → `tool_not_supported:`
(T7.57(b)). `SandboxToolBroker` + одноразовый контейнер
(`packages/sandbox/runtime.py`: network none, cap-drop ALL, non-root uid 10001,
read-only rootfs, no-new-privileges, лимиты CPU/mem/PID/timeout из
`sandbox/policy/*.yaml`) существовали и были зелёными **только в тестах** —
в продукте их никто не собирал: контейнер требует lifecycle (start → exec →
collect/freeze → destroy), а `build_orchestrator` вызывается ДО того, как
сессия и её effective capability profile существуют.

Спека при этом уже описывает sandbox-семантику: §5.7 — «workspace.write …
только session overlay», классы повторяемости и retry-policy Tool Broker
(продуктово применялись только на stub-пути, где transient/ретраи не живут);
§11.2 + инвариант §3 (AGENTS) — «инструмент, недоступный профилю, отсутствует
в схеме модели», «одноразовый контейнер: network none, cap-drop ALL».

Нельзя было сделать и по-другому: фильтровать offered-список инструментов под
исполнителя запрещено (меняется `tool_schema_hash` каждого шага → ломается
байт-сопоставимость замороженных eval-серий; разбор А в T7.57).

## Решение

**Один env-переключатель на все host-входы сессий** — `NOEZEMA_TOOL_EXECUTOR`:

| значение | исполнитель |
|---|---|
| *(не задан)* / пусто | `stub` — DEFAULT, поведенчески побайтово прежний путь (тот же `StubToolExecutor`, та же общая workspace-директория) |
| `stub` | то же самое явно |
| `sandbox` | `SandboxSessionExecutor` = `SandboxToolBroker` поверх одноразового контейнера на сессию |
| иное | **отказ** (`ToolExecutorConfigError`, exit 78 в CLI): «unknown tool executor mode … no silent fallback to the dev stub» |

Развязка сделана **чистой функцией в отдельном модуле** (AGENTS §4):
`apps/orchestrator/tool_executors.py::resolve_tool_executor_mode(raw)`;
рабочий модуль (`build_tool_executor`, тот же вызов в CLI) лишь дергает её.

**Продолжение контракта `run_session` — optional lifecycle hook, а не новый
параметр.** Модуль объявляет `SessionScopedExecutor` (Protocol):
`open_session(session_id, cap_profile)` / `close_session()`. Оркестратор зовёт
их через `getattr` (`_open_tool_sandbox` / `_close_tool_sandbox`), поэтому:

- у `StubToolExecutor` и у тестовых doubles хуков нет → путь исполнителя
  дословно прежний (число шагов, транзакции, аудит — без изменений);
- сигнатура `run_session`, `CommitPlan`, `SessionOutcome`, аудит-события,
  staging, fenced commit — не изменены.

**Точка открытия:** внутри phase-1 транзакции `_run_to_committing`, сразу после
строки сессии и audit `SESSION_STARTED` (т.е. уже известны durable `session.id`
— контейнер называется по нему: `noezema-sb-<session_id[:12]>` — и effective
capability profile; ещё ничего не исполнено). Точка закрытия — `finally`
вокруг phase-1 блока `run_session`: нормальное завершение, ранний терминальный
`_finish`, поднявшийся `LeaseLost`, любой infra-исклучение. `close_session()`
не бросает исключений и не подменяет исход сессии; утечку он называет в логе
процесса (`LEAKED container …`) и убирает одноразовый host-overlay
(`work_root/<session_id>`).

**Fail-closed предпроверка.** В sandbox-режиме `build_tool_executor` до любой
работы проверяет движок (`which(engine)`) и образ
(`<engine> image inspect <image>`) → `ToolExecutorUnavailableError` с текстом,
из которого видна команда сборки. wake tick: сообщение + `exit 78` ДО записи
`node_state=session_running` (не клинит ноду); eval-run: то же, до `_finish`
(серия не считается выполненной, гейты не пишутся). Тихого отката на stub нет
ни в одном из путей; `execute()` без открытого контейнера тоже отказывает, а не
«выполняет на хосте».

**Профиль контейнера = YAML-потолок access-профиля снапшота**, сеть — всегда
`none`: `sandbox_runtime_profile(cap_profile)` грузит
`sandbox/policy/<cap.name>.yaml` (ресурсы, ro rootfs, no-new-privileges) и
переводит `network: research_proxy` (curated/open_lab) в `none`, потому что
egress в NOEZEMA хост-side и в обоих режимах исполнителя (`research.fetch`
перехватывается оркестратором и идёт через Research Proxy, §11.2/M6). Неизвестное
имя профиля или инструменты вне потолка → отказ (снапшот может сужать, не
расширять — как `effective_profile`).

**Модель-facing список инструментов не тронут ни в одном режиме** (нифильтрации
по исполнителю): offered-список и `tool_schema_hash` считаются из снапшота →
хэши шагов сопоставимы между stub- и sandbox-прогонами; одинакова и диагностика
недоступного: `artifact.create` → `unknown tool` в обоих режимах (нет в реестре
`packages/policy/tools.py`), `question.create`/`message.reply` — deferred в
staging и применяемые host-side (детерминированный эффект идентичен).

**Логи — только процесс, не аудит.** Никаких новых полей в audit-событиях,
`model_fingerprint`, `CommitPlan`: режим виден из метки процесса
(`wake-tick: tool executor=sandbox …`, logging-строка
`tool executor=sandbox opened session=… container=… profile=… network=none`) и
из имени контейнера. Это сознательное ограничение: любые новые durable-поля
поломали бы сравнимость замороженных артефактов прошлых прогонов.

## Изменения

- `apps/orchestrator/tool_executors.py` (новый): resolverswitch'а,
  `sandbox_runtime_profile`, `ensure_sandbox_available` (preflight),
  `SessionScopedExecutor` (Protocol), `SandboxSessionExecutor`
  (`ToolExecutor` + lifecycle: `workspace_dir` — overlay сессии, setter
  запрещён; `snapshot_id` — свойство с делегированием broker'у (T7.7-пиннинг
  memory.search)), `build_tool_executor` (единственная точка сборки).
- `apps/orchestrator/main.py`: `executor=StubToolExecutor(...)` →
  `executor=build_tool_executor(workspace_root)`; docstring дополнен
  (research proxy остаётся host-side в обоих режимах).
- `apps/orchestrator/orchestrator.py` (+~45 строк, поведение stub-пути не
  изменено): хуки `_open_tool_sandbox` (после создания строки сессии) и
  `_close_tool_sandbox` (в `finally` phase-1); комментарий в конструкторе о
  необязательном lifecycle-контракте.
- `hostctl/cli.py`: wake tick — сборка orchestrator до записи
  `node_state=session_running`, явный отказ (`exit 78`) + метка режима; eval-run
  — метка режима на сессию и те же классы ошибок в fail-closed ветке.
- `apps/web/api.py::build_standalone_app` — тот же `build_tool_executor`
  (иначе web-unit молча остался бы на stub при общем `EnvironmentFile`).
- `tests/unit/test_tool_executor_switch.py` (новый, 25 тестов),
  `tests/scenario/test_orchestrator_sandbox_executor.py` (новый, 5 docker-тестов).
- `docs/STATUS.md`: раздел T7.58 (дизайн-анализ (а)–(е), замеры стоимости,
  различия python-среды, остатки); матрица §22.1 п.4 — ссылка на новый тест;
  `AGENTS.md` §6/§7 — как включить sandbox-режим и ловушки.

## Тесты

- unit (без docker/БД): парсинг (`stub` по умолчанию/пусто, `sandbox`,
  регистр/пробелы, 8 неизвестных значений → отказ с именем переменной и
  списком известных); assembly (default → `StubToolExecutor`, хуков нет;
  неизвестный режим → отказ без побочных эффектов; sandbox без движка → отказ,
  workspace не создан); профиль (sealed → `network none` + лимиты sealed.yaml;
  curated `research_proxy` → контейнеру всё равно `none`; неизвестный профиль и
  инструменты вне потолка → отказ); контракт (`isinstance SessionScopedExecutor`,
  placeholder `workspace_dir` несуществующий, repoint запрещён, execute без
  контейнера → отказ, open с отсутствующим образом → отказ + идемпотентный close).
- unit (паритет stub): через тот же switch — `shell.execute` →
  `tool_not_supported:` (T7.57(b)), python.print ok, `artifact.create` →
  `unknown tool`.
- scenario (postgres + fake LLM + docker, образ `noezema-sandbox:test`, без
  маркера `timing` — wall-clock ассертов нет): полная сессия оркестратора на
  реальном контейнере (`python.execute`/`shell.execute` реально в контейнере,
  `workspace.write` → `workspace_entries`+`committed_workspace_manifest_id`,
  host-workspace не использован, имя контейнера = id сессии, после сессии — ни
  контейнера, ни overlay-каталога); отсутствие egress (DNS `gaierror`,
  `/proc/net/dev` = только `lo`) в sealed И curated; отказ при отсутствии образа
  (сессии нет в БД, каталогов нет, контейнеров нет); исключение внутри сессии →
  контейнер уничтожен (`finally`), phase-1 rollback (строк `sessions` нет);
  stub-режим через тот же switch → общая host-workspace + отказ shell.execute с
  прежней диагностикой.
- Красный→зелёный по построению: до правок тесты switch'а не существовали;
  существующие 1064 тестов (1064 passed baseline) прошли без изменений — ни один
  ассерт не ослаблен, `tests/scenario/test_orchestrator.py` не тронут.

## Не изменено

`ARCHITECTURE.md`; tool-списки снапшотов и offered-строки промптов;
`tool_schema_hash`, `rules_hash`, payload'ы config-v*…v12 и их хэши, промпты и
пины, model schemas (JSON Schema, уходящая в LLM), корпуса, пороги §22.2;
миграций нет; AuditEventType/audit-payload'ы, `model_fingerprint`, `CommitPlan`
не расширены; semantics `run_session` (сигнатура, транзакции, fenced commit,
admission) не изменены; поведение stub-режима по умолчанию не изменено.
eval-run/смоуки/NOEZEMA-сессии с реальным LLM не запускались (это T7.59 — по
решению пользователя); к `noezema-eval*`/`noezema-smoke*` обращений не было.

## Что остаётся непроверенным (T7.59)

Режим проверен fake-LLM-сессиями и реальными контейнерами, но не реальным
смоуком: effect на wall-clock 7 сессий (замер — +10,8…+11,7 с), частота
transient/refusals у реальной модели (модель может писать код, которому не хватает
библиотек или сети — в контейнере это наблюдение с ошибкой, а не ошибка хоста),
дрейф образа между прогонами (`noezema-sandbox:dev` по умолчанию vs пин тестов
`noezema-sandbox:test`), поведение systemd-unit'ов `.87` с `EnvironmentFile`.
Рекомендация по включению sandbox-режима по умолчанию — в STATUS.md T7.58:
пока явный env для eval/смоук-серий после контрольного смоука (T7.59), дефолт
остаётся `stub`.
