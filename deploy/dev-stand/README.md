# Dev-стенд NOEZEMA (T7.59(б))

Пакет, чтобы один человек «потрогал» MVP: открыл страницу, задал вопрос, нажал wake now,
посмотрел ленту событий, остановил, сбросил. Целевая машина — **192.168.1.92** (Ubuntu 24.04,
один пользователь, без GPU). Подготовка и проверка пакета — только на dev-машине (.87); саму VM
поднимает менеджер по ssh. Никакой другой контур этот пакет не трогает.

## Чем стенд не является

- это **не** прод-контур: юниты в `deploy/dev-stand/systemd/` написаны заново и **не являются
  копиями** `infra/systemd/*`; отдельные пути (`/var/lib/noezema-dev`, `/var/lib/noezema-dev/host`)
  вместо `/var/lib/noezema` и `/run/noezema`; отдельный env-файл; `User=` = пользователь стенда;
- это **не** eval/smoke-контуры: база называется `noezema-dev*`, а скрипты отказываются работать с
  любым другим именем (проверка в коде, не по договорённости); eval/smoke-базы доступны только на
  чтение;
- стенд сам не поднимается при загрузке машины: он стартует командой `systemctl start noezema-dev.target`.

## Состав пакета

| файл | что делает |
|---|---|
| `bootstrap.sh` | идемпотентная установка: пакеты → venv/зависимости → env-файл с секретами → каталоги + sandbox-образ → Postgres 15 в docker (127.0.0.1) → база + миграции + активация конфигурации → юниты → поиск SearXNG (только по `--with-searxng`) |
| `systemd/*` | 8 файлов стенда: target, web, tick+timer, maint+timer, unit-state+timer |
| `searxng/settings.yml` | ШАБЛОН настроек поиска (formats html+json, limiter off); `secret_key` здесь — плейсхолдер, настоящего секрета в репозитории нет |
| `searxng-settings.sh` | рендер настроек из шаблона: генерирует `secret_key` (не печатает), повторный запуск ключ не ротирует |
| `status.sh` | состояние: юниты, docker, очередь вопросов, последняя сессия, доступность LLM, версия кода, режим исполнителя инструментов, поиск (SearXNG + режим research_proxy снапшота) |
| `reset-db.sh` | пересоздание dev-базы с явным подтверждением + миграции + повторная активация config-v18 |
| `README.md` | этот файл |

## Деплой (делает менеджер)

```bash
ssh <user>@192.168.1.92
git clone -b impl/from-scratch <repo> ~/noezema && cd ~/noezema
./deploy/dev-stand/bootstrap.sh --dry-run     # план: что будет сделано, ничего не меняется
sudo ./deploy/dev-stand/bootstrap.sh          # фактическая установка (скрипт сам добавит sudo где нужно)
systemctl start noezema-dev.target            # web + unit-state и maint (тик-таймер по умолчанию выключен)
./deploy/dev-stand/status.sh                  # проверка глазом
```

Флаги `bootstrap.sh`: `--dry-run`, `--no-docker-install`, `--no-units`, `--stub-executor`,
`--force`, `--rotate-secrets`, `--with-tick-timer`, `--with-searxng`, `--recreate-searxng`,
`--web-host 0.0.0.0`, `--web-port 8321`, `--user <name>`.
Идемпотентность: повторный запуск не пересоздаёт venv, базу, контейнер Postgres и sandbox-образ.
`--force` пересоздаёт venv (`uv venv --clear`) и юниты, заново пишет env-файл **теми же секретами**:
пока существуют контейнер или том Postgres, ни пароль БД, ни admin-токен не ротируются — пароль живёт
в томе, его «ротация» рассинхронизировала бы кластер с env-файлом. Ротация — только явный
`--rotate-secrets`, и она ротирует admin-токен; пароль БД на месте не меняется никогда — скрипт прямо
пишет об этом («пароль БД НЕ ротирован: он закреплён в томе»). Новый пароль возможен только после
осознанного удаления контейнера и тома (`docker rm -f noezema-dev-db && docker volume rm noezema-dev-pgdata`).

## Что где лежит и какие порты

| | |
|---|---|
| приложение | репозиторий как есть (`$APP_DIR`), venv в `$APP_DIR/.venv`, зависимости **только через uv** и только prod-extras (AGENTS §6) |
| база | docker-контейнер `noezema-dev-db` (образ `postgres:15`), том `noezema-dev-pgdata`, публикация **только на 127.0.0.1**, база `noezema-dev`, пользователь `noezema`. Порт выбирается до записи env-файла: 5432, а если на хосте он уже занят (нативный `postgresql.service`) — первый свободный из 5433..5440; выбранный порт пишется в env-файл (`NOEZEMA_DEV_DB_PORT`), повторный запуск переиспользует порт существующего контейнера. `NOEZEMA_DEV_DB_PORT=<порт>` задаёт явно: занят → понятная ошибка, а не молчаливый переезд |
| миграции | `alembic upgrade head` из venv (URL из env-файла) |
| конфигурация | активация `docs/eval/config-v18-payload.json` через `hostctl activate-online` (config-v18 = config-v17 ровно с одной правкой: `model.reasoning_by_phase` — куратору, экстрактору и верификатору ответ запрашивается БЕЗ рассуждения движка, исследователю и планировщику оставляется; `session_limits.max_explorer_steps = 16`, пины explorer-v9 и curator-v8, пороги, бюджеты, окно EXL3 131072 и `model.max_output_tokens = 8192` — байт в байт config-v17; пропускается, если снапшот уже активен; переход v17→v18 — STATUS.md T7.80) |
| данные сессий | `/var/lib/noezema-dev` (+ `sandbox` — work_root контейнерного исполнителя) |
| host-контур стенда | `/var/lib/noezema-dev/host`, снимок юнитов: `/var/lib/noezema-dev/host/unit-state.json` |
| секреты/настройки | `/etc/noezema/dev.env`, режим **0600**, владелец — пользователь стенда; в отчёты и чат не попадают (AGENTS §5) |
| поиск (опционально) | контейнер `noezema-searxng` (`searxng/searxng:latest`), публикация **только на 127.0.0.1:8888** (внутри 8080); настройки `/etc/noezema/searxng/settings.yml` — `secret_key` генерируется на ВМ и в репозиторий не попадает; ставится только по `--with-searxng` (раздел «Поиск (SearXNG)») |
| web UI/API | `127.0.0.1:8321` по умолчанию (`NOEZEMA_WEB_HOST`/`NOEZEMA_WEB_PORT`) |
| sandbox-образ | `noezema-sandbox:dev-stand` (собран из `sandbox/Containerfile`, закреплён одной меткой в `NOEZEMA_SANDBOX_IMAGE`; dev/test метки не затрагиваются) |
| LLM | `http://192.168.1.42:8080/v1`, модель `qwen38-exl3-3bpw-128k`, `NOEZEMA_LLM_SCHEMA_PROFILE=none`, `NOEZEMA_LLM_REASONING_PROFILE=none` (раздел «Профиль рассуждения движка»), `MAX_OUTPUT_TOKENS=8192`, `TIMEOUT_SECONDS=600` (переопределяется `NOEZEMA_DEV_LLM_*`) |

## Пользование: цикл MVP

1. **Открыть UI:** `http://127.0.0.1:8321`. Если стенд поднят на LAN (`--web-host 0.0.0.0`), то же
   самое читается с другой машины как `http://<VM>:8321` — GET-эндпоинты открытые (§13.1).

   **С T7.65 главная страница — «Вопросы и ответы»** (простой режим): одной фразой состояние узла,
   форма вопроса с выбором срочности («Обычный» / «Срочно (вперёд очереди)» / «Потом»), кнопки
   «Запустить обработку», «Возобновить», «Пауза» и таблица «Мои вопросы» со ссылкой «Открыть ответ»
   на карточку `/answer/<id>` (вывод с бейджем надёжности, «Как это получено», честные замечания).
   Всё инженерное — прежнее содержимое главной — теперь по адресу **`/engineer`** (там же формы с
   числовым приоритетом, карточка «Узел» с кнопкой «wake now», таблица очереди с id и кодами,
   ссылки `/session/<id>`, `/knowledge`, `/claim/<id>`, `/diagnostics`, `/metrics`, `/evaluation`).
   Пункты 2–7 ниже описывают именно её; в простом режиме те же действия делаются теми же API.
2. **Задать вопрос:** карточка «Задать вопрос» (текст ≤ 2000, приоритет, поле admin-токена; токен
   запоминается в `sessionStorage` браузера). Токен = `NOEZEMA_ADMIN_TOKEN` из env-файла:
   `sudo grep NOEZEMA_ADMIN_TOKEN /etc/noezema/dev.env`. Тот же самый текст второй раз **не
   создаёт** вторую строку очереди — вернётся существующий id (`replayed: true`).
3. **ВПЕРЁД очереди:** приоритет выше обычного (например 9). FIFO выбирает кандидата по
   `priority DESC, created_at ASC` (§5.3.2), то есть вопрос оператора с приоритетом обгоняет
   остальные кандидаты, а среди равных приоритетов решает возраст. Эквивалент из консоли:
   `.venv/bin/python -m hostctl.cli ask "почему падает X?" --priority 9`.
4. **wake now — это кнопка/команда Command API, а не systemctl.** Кнопка «wake now» в карточке «Узел»
   (это `POST /api/v1/commands` с `{"type":"wake_now", "idempotency_key": ...}`) — **единственный**
   способ запустить сессию прямо сейчас: она обходит только тайминг расписания (интервал, минимальный
   интервал, backoff), шлюзы admission (§5.2.1) остаются и могут ответить «rejected» с причиной — это
   нормальный ответ планировщика. `sudo systemctl start noezema-dev-tick.service` и
   `.venv/bin/python -m hostctl.cli wake-tick` сессию **не форсируют**: это тот же тик, что делает
   таймер, он подчиняется снапшоту конфигурации. После завершившейся сессии действует
   `wake_schedule.interval_seconds = 3600`, поэтому ручной старт тика напечатает в журнал
   `wake-tick: wait (interval_not_elapsed)` и выйдет с кодом 0 — сессии не будет (проверка:
   `journalctl -u noezema-dev-tick.service -n 20 --no-pager`). У `wake-tick` нет флага «сейчас»;
   форсированный wake есть только в Command API.
5. **Лента событий:** ссылка из таблицы очереди ведёт на `/session/<id>` (состояние, события,
   коммит). Общий поток: `GET /api/v1/timeline`, поток SSE: `GET /api/v1/timeline/sse`, метрики: `/metrics`.
6. **Очередь:** карточка «Очередь вопросов» = `GET /api/v1/questions` (открытый запрос): id, текст,
   состояние, приоритет, origin (`message` — принятый оператором), привязанная сессия, позиция.
7. **Состояние стенда:** `./deploy/dev-stand/status.sh` — юниты и их периоды, docker (состояние
   Postgres, наличие образа, число сессионных контейнеров), очередь и последняя сессия из БД,
   wake-книга (`consecutive_failures`, пауза), доступность LLM через `/v1/models`, версия кода,
   режим исполнителя. Флаги `--no-llm`, `--no-web` — чтобы не ходить вовне при проверке вне VM.
8. **Стоп:** `sudo systemctl stop noezema-dev.target` (снимает web и все три таймера; контейнер
   Postgres остаётся: `docker stop noezema-dev-db`).
9. **Сброс:** `./deploy/dev-stand/reset-db.sh` — просит вписать имя базы (защита от «а вдруг это
   прод-база»), останавливает таймеры, пересоздаёт `noezema-dev`, накатывает миграции, заново
   активирует config-v18, поднимает таймеры. С защитой по состоянию: если в базе есть незавершённая
   сессия, reset откажется — сначала дождаться её терминации (или остановить tick-таймер).

## Юниты стенда (8 файлов)

| юнит | что делает |
|---|---|
| `noezema-dev.target` | группа стенда; сам не включается в загрузку (никто не хочет его в `multi-user.target`) |
| `noezema-dev-web.service` | web UI/API, `Type=simple`, `Restart=always`, bind из env-файла |
| `noezema-dev-tick.service` + `.timer` | один `hostctl wake-tick` на срабатывание; `Type=oneshot`, `TimeoutStartSec=3600` (сессия имеет право отработать свой бюджет), таймер 60 с, `OnBootSec=10m`. **В загрузку не ставится без `--with-tick-timer`** (T7.61(б)): единственный юнит стенда, который сам запускает сессию |
| `noezema-dev-maint.service` + `.timer` | два шага подряд: `reassessment-tick` затем `reconcile-tick`, таймер 60 с |
| `noezema-dev-unit-state.service` + `.timer` | публикация снимка юнитов каждые 5 с — без него Command API и приём вопроса на стенде отвечают 423 |

Все сервисы — `PartOf=noezema-dev.target`, все таймеры — `WantedBy=noezema-dev.target`: остановка
target останавливает wakes, публикацию и web разом. Проверка синтаксиса на dev-машине:
`systemd-analyze verify --man=no` на отрендеренных копиях (тот же критерий, что у
`scripts/verify_systemd_units.sh`).

## Как запускать сессии

**По умолчанию сессию на стенде запускает только оператор.** Кнопка «wake now» на странице или
`POST /api/v1/commands` с `{"type":"wake_now"}` (нужен `X-Admin-Token`) — они обходят интервал из снапшота,
но не обходят admission (§5.2.1): узел в паузе или с незавершённой сессией не будет разбужен и командой.

**Плановые сессии — отдельное решение оператора.** `noezema-dev-tick.timer` (60 с; фактический интервал
берёт `wake_schedule` из снапшота) включается только явным флагом:

```bash
sudo ./deploy/dev-stand/bootstrap.sh --with-tick-timer    # поставить/оставить плановый тик
sudo systemctl disable --now noezema-dev-tick.timer       # выключить plan sessions вручную
sudo systemctl enable  --now noezema-dev-tick.timer       # включить их вручную, без bootstrap
```

Повторный `bootstrap.sh` без флага уже включённый таймер НЕ отключает: он печатает текущее состояние и
команду выключения (самостоятельные сессии оператора — только его решение). Текущее состояние всегда видно в `./status.sh` — строка «тик-таймер: выключен (сессии —
только wake now)» или «включён (state=…, next=…)».

Почему по умолчанию выключено: узел, который сам себя будит без участия оператора, на пустой очереди
доходит до `max_consecutive_failures=3` и уходит в автопаузу (ниже раздел про пустую очередь), а «wake now»
при включённом таймере конкурирует с плановым тиком (и проигрывает ему по времени запуска). Конкуренция закрыта узловым замком (T7.61(а), ADR-0025):
второй вход получает skip `session_in_progress` и второй сессии не создаёт — но это защита от ошибки, а не
причина держать таймер включённым.

Одну сессию «в обход расписания» можно запустить и из CLI: `sudo -u <stand user> .venv/bin/python -m
hostctl.cli wake-tick` — при занятой сессии он так же напечатает `wake-tick: skip (session_in_progress)` и
выйдет 0.

## VM без GPU и пустая очередь — это не ошибка

- В `config-v18` (как и в `config-v17`, `config-v16`, `config-v15`, `config-v14` и `config-v13`) стоит `wake_schedule.gpu_required = false`, поэтому на VM без GPU admission
  проходит и сессии запускаются. Если бы значение было `true`, тик напечатал бы `skip (gpu)` и вышел
  с кодом 0: шлюз работает, а не ломается (§5.2.1).
- Расписание — `interval_seconds=3600`, `min_session_interval_seconds=600`; таймер тикает каждые
  60 с, поэтому большинство тиков печатают `wait`. Эффективное время определяет снапшот
  конфигурации, а не период таймера.
- **Пустая очередь:** admitted-сессия доходит до выбора вопроса, кандидата не находит и завершается
  `FAILED` с `termination_reason = no_question`. Планировщик считает это неудачей: backoff 60/120/240
  с (потолок из снапшота), а после `max_consecutive_failures = 3` узел уходит в `paused`, и дальше
  тики дают `skip`. Это ожидаемое поведение стенда, где спрашивать пока не о чем.
  Что делать: задать вопрос и снять паузу — `.venv/bin/python -m hostctl.cli resume-runtime --reason "…"`
  или `POST /api/v1/commands {"type":"resume","idempotency_key":"…"}` с admin-токеном. Текущее
  состояние видно в `status.sh` (строка `wake-состояние`) и в `/api/v1/status`.

## Где смотреть логи

| что | где |
|---|---|
| web: запросы, ошибки, отказ от запуска (пустой токен при публичном bind) | `journalctl -u noezema-dev-web.service -f` |
| решения планировщика wake: `wake` / `wait` / `skip` и причина (backoff, paused, gpu_required) | `journalctl -u noezema-dev-tick.service -n 60 --no-pager` |
| reassessment + reconcile | `journalctl -u noezema-dev-maint.service -n 60 --no-pager` |
| публикация снимка юнитов (если она встала — web деградирует до 423) | `journalctl -u noezema-dev-unit-state.service -n 30 --no-pager` |
| Postgres | `docker logs noezema-dev-db` |
| сессионные контейнеры sandbox | `docker ps -a --filter name=noezema-sb-`; строка `LEAKED container` в логе tick-сервиса, если `docker rm -f` отказал |
| host-журнал стенда (fsync-safe JSONL + head) | `/var/lib/noezema-dev/host/host-transitions/`, `…/host-transition-events/`, `…/host-transition-head.json`; активная смена политики — `…/host-policy-change-head.json` |
| снимок юнитов | `/var/lib/noezema-dev/host/unit-state.json` |
| оверлеи инструментов сессий | `/var/lib/noezema-dev/sandbox/<session_id>` (одноразовый на сессию) |
| события сессий (аудит) | не в файле: таблица `audit_events` в базе и наружу — `GET /api/v1/timeline`, SSE `GET /api/v1/timeline/sse`, страница `/session/<id>` |

## ВМ с deny-by-default UFW (192.168.1.92)

`bootstrap.sh` **не меняет фаервол** — он только печатает нужные правила, если `ufw status` активен
(`sudo -n ufw status`, без интерактивного пароля; если состояние прочитать не удалось — просто напоминает).
Правила на 192.168.1.92 (выполняет менеджер до/после деплоя, по необходимости):

```bash
sudo ufw allow out on docker0 to 172.17.0.0/16                       #docker-мост: пулл образа и трафик контейнера
sudo ufw allow out to any port 8080 proto tcp                        # LLM http://192.168.1.42:8080/v1
sudo ufw allow in from 192.168.1.0/24 to any port 8321 proto tcp     # web UI/API из LAN (если bind не loopback)
sudo ufw allow out to any port 53 proto udp                          # DNS
sudo ufw allow out to any port 123 proto udp                         # NTP (таймеры и метки времени)
sudo ufw allow out to any port 80,443 proto tcp                      # apt и пулл postgres:15
```

Симптом закрытого фаервола — не «подключиться не удалось», а вот что видно по шагам: `docker run`
проходит, healthcheck контейнера зелёный, но проверка эндпоинта (`TCP + SELECT 1` на
`127.0.0.1:<порт>`) висит и bootstrap падает с диагностикой (состояние контейнера, слушатели порта,
лог). Именно эта проверка, а не внутренний healthcheck, — то же самое, что делает приложение.

### Внешний доступ сессий: web «wake now» после T7.68

После T7.68 (ADR-0027 §1) веб-команда wake_now и плановый тик собирают сессию одним построителем
(`apps/orchestrator/session_assembly.py`): research-инструмент `research.fetch` доступен и веб-запуску —
в том же режиме, что предписывает активный снапшот (сейчас на стенде curated; в sealed его нет, и wiring
это не обходит). Выход в сеть делает процесс оркестратора на хосте; контейнер сессии по-прежнему всегда
`network=none`. Артефакты и нормализованный текст удачных fetch пишутся в `<NOEZEMA_DATA_ROOT>/artifacts` —
тот же каталог, что у тика (для этого стенда — `/var/lib/noezema-dev/artifacts`). Операторское условие
реального внешнего факта — постоянные исходящие 80/443 (правила выше; сейчас они помечены временными —
харнесс фаервол не меняет). Проверка без новых инструментов: задать вопрос, отвечаемый только внешним
фактом, и посмотреть шаги карточки ответа (`research.fetch → completed`); режим активного снапшота виден
в строке «конфиг» `status.sh` (имя payload-файла). Подробный разбор: `docs/web-access-design.md` §6–7 и
STATUS §T7.68.

## Поиск (SearXNG, T7.70)

Поисковый пакет ставится **только по явному флагу** и только там, где это разрешено режимом снапшота:

```bash
./bootstrap.sh --with-searxng          # настройки + контейнер noezema-searxng на 127.0.0.1:8888
./bootstrap.sh --with-searxng --recreate-searxng   # пересобрать контейнер (иначе существующий НЕ трогается)
```

Что делает шаг и чего он не делает:

- настройки пишутся в `/etc/noezema/searxng/settings.yml` из шаблона
  `deploy/dev-stand/searxng/settings.yml`; `secret_key` **генерируется на ВМ**, не печатается и не
  хранится в репозитории (в шаблоне — плейсхолдер). Повторный запуск существующий ключ НЕ ротирует
  (ротация — отдельный осознанный шаг: `deploy/dev-stand/searxng-settings.sh --out … --rotate-secret`);
- контейнер публикуется **только на 127.0.0.1:8888** (внутри SearXNG слушает 8080), restart-policy
  `unless-stopped`, настройки примонтированы read-only;
- готовности ждут тем же способом, каким узел будет пользоваться поиском: HTTP 200 + JSON на
  `http://127.0.0.1:8888/search?q=test&format=json` (≤60 с). Пробный запрос настоящий — он уходит к
  поисковым движкам; если исходящие закрыты, шаг падает с диагностикой (состояние контейнера, лог,
  подсказка про 80/443 и DNS), а не молча оставляет полу-установку;
- **`bootstrap.sh` не меняет UFW и iptables**: он печатает нужные правила. Исходящие 80/443 tcp и
  DNS 53 применяет оператор (раздел «ВМ с deny-by-default UFW» выше); на этой ВМ трафик контейнера
  уже выпускает цепочка `DOCKER-FORWARD`, а список `DOCKER-USER` пуст — их пакет тоже не трогает;
- без флага `--with-searxng` шаг ничего не создаёт и не удаляет: если контейнер уже есть, сообщается
  его состояние и что пересоздавать его никто не будет.

Удаление (откат) — осознанная команда оператора:

```bash
sudo docker rm -v -f noezema-searxng      # настройки остаются в /etc/noezema/searxng/ (их удаляют вручную)
```

Как это связано с узлом: `research_proxy` (единственный egress, §5.12) в режиме **curated** отправляет
поисковый запрос на `research_proxy.searxng_url` — в config-v13…v15 это `http://127.0.0.1:8888`, он же
в `private_allowlist`. Лимит расхода держит узел, а не контейнер: `rate_limit_max`/`rate_limit_window_seconds`
из активного снапшота (сейчас 20 запросов на 3600 с), каждый upstream-записанный запрос виден в ленте
(`research_upstream_request`, при отказе — `research_fetch_rejected` с причиной
`upstream_rate_limit_exceeded`). Встроенный limiter SearXNG выключен специально: он отвечал бы 429 на
JSON-запросы автоматического клиента, а ограничивает расход узел.

Состояние — одной строкой в `status.sh` (раздел «поиск (SearXNG)»): контейнер есть/нет, отвечает/нет,
режим `research_proxy` активного снапшота (`mode`, `searxng_url`, лимит, число разрешённых доменов) и
то, выдан ли модели инструмент `web.search` в этом снапшоте. Проверку ответа можно пропустить:
`./status.sh --no-search` (off-VM запуск).

Границы, которые пакет не меняет: в режиме **sealed** поиска у модели нет вовсе (локальный индекс —
`memory.search`), в open_lab upstream-поиска нет; найденные заголовки и фрагменты — недоверенные данные
и не становятся доказательством: факт появляется только после `research.fetch` выбранной страницы.
Решение и разбор: `docs/web-access-design.md` §5/§7.2, ADR-0027 §4 и ADR-0028, STATUS §T7.70/T7.71.

### Поиск для модели: активация снапшота и откат (делает менеджер)

Контейнер сам по себе модели ничего не даёт — инструмент `web.search` появляется только вместе с активным
снапшотом. Он есть и в текущем дефолте (`config-v18`, как и в `config-v17`, `config-v16` и `config-v15`), и в прежнем (`config-v14`); исторически переход
`v13 → v14` был нужен именно ради поиска — `bootstrap.sh` тогда активировал `config-v14`, а узлы, уже
работавшие на `config-v13`, переводились одной явной онлайн-активацией (шлюзы admission и drain не
обходятся):

```bash
cd ~/noezema
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v14-payload.json \
  --reason "T7.71: поиск web.search + промпт explorer-v6" --drain-wait-seconds 120
./deploy/dev-stand/status.sh | sed -n '/поиск (SearXNG)/,/^$/p'   # «web.search доступен модели в этом снапшоте: да»
```

Откат того шага — прежний снапшот (файл `config-v13-payload.json` не переписывался, canonical payload
`0260fcd2f79035e634d49fe8304e44a3784b63dc0a81566687cbe52aa7f94ce0`):

```bash
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v13-payload.json --reason "откат T7.71" --drain-wait-seconds 120
```

Постфактум переход видно по `model_runs`: шаги v14 идут с `prompt_version = explorer-v6` и
`tool_schema_hash = a7adc948…` (offered-список v14), шаги v13 — с `explorer-v5` и `f7628473…`; никакого
«тихого» переключения нет ни в UI, ни в БД.

### Перепроверка без самовыведения даты: активация config-v15 (T7.73)

`config-v15` = `config-v14` ровно с двумя пинами — `prompts.curator` → **curator-v8** (перепроверка
сохраняет опорную дату и scope якоря; каждое использованное наблюдение обязано быть привязано) и
`prompts.explorer` → **explorer-v7** (первоисточник и независимое исследование, расхождение показывать,
а не выбирать молча). Пороги, `claim_type_rules`, окна и бюджеты не изменились, поэтому сравнение с
прежними прогонами сохраняется. Canonical payload
`b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73` (в `config_snapshots.payload_sha256`
попадает canonical, не хеш файла).

Узел, уже работавший на v14, переводится одной онлайн-активацией:

```bash
cd ~/noezema
git pull                                   # пакет с prompts/curator/curator-v8.md и prompts/explorer/explorer-v7.md
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v15-payload.json \
  --reason "T7.73: curator-v8 (перепроверка хранит якорную дату) + explorer-v7 (первоисточник vs пересказ)" \
  --drain-wait-seconds 120
```

Откат — активация payload'а v14 (он закоммичен и не менялся, canonical
`22903be78602cf7897f0de57b99514b66c58eca83960fa05458fd341e0104df4`):

```bash
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v14-payload.json --reason "откат T7.73" --drain-wait-seconds 120
```

Верификация после активации (вопрос задаёт оператор, например через `noezemactl ask`): перепроверка уже
подтверждённого временного факта **без даты в формулировке** — «Перепроверь по независимому источнику
<https://…>: <тот же текст утверждения>?». Ожидаемый исход: та же голова и та же оценка (или выше), в
журнале `claim_reverified` с `anchor_kept`, карточка не показала понижения; если причина оценки всё-таки
появилась — она подписана прямо на карточке (`grade_reasons`), а не исчезла молча. Шаги новых сессий видно
по `model_runs.prompt_version`: `curator-v8` и `explorer-v7`.


Если SearXNG не поднят (или закрыт egress 80/443), активный curated продолжает работать как раньше для
`research.fetch`, а `web.search` даёт **failed-действие**: наблюдение с текстом ошибки (≤500 знаков, fence'а
в нём нет) и запись `research_upstream_request` со `status: "failed"` и причиной; в простом режиме это шага
«поискал в интернете» не добавит, а «Как это получено» честно останется без внешних источников. Поискового
результата «из ниоткуда» харнесс не выдаёт никогда.

### Двусторонний поиск и лимит шагов: активация config-v16 (T7.76)

`config-v16` = `config-v15` ровно с двумя правками: `prompts.explorer` → **explorer-v8** (правило 10:
спорное число проверяется двумя сторонами поиска — официальный первоисточник и отдельный запрос именно
про независимую оценку, например «независимая оценка инфляции 2025» / «альтернативные оценки инфляции» /
«inflation estimate independent Russia 2025»; найденное сравнивается, расхождение называется открыто;
если независимых оценок нет — так и говорится) и `session_limits.max_explorer_steps` **10 → 16**
(перепроверка на подставке израсходовала 8 из 10 шагов на официальную сторону и до независимой не дожила;
16 шагов ≈ 912 с при замеренных ~57 с на шаг плюс куратор ≤180 с — внутри `session_timeout_seconds` и
`phase_deadline_seconds` 1800). Пороги, `claim_type_rules`, token-бюджеты (Σ=26624), пин curator-v8, окна
модели и список инструментов не изменились: сравнение с прежними прогонами сохраняется. Canonical payload
`740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3` (в `config_snapshots.payload_sha256`
попадает canonical, не хеш файла).

Узел, уже работавший на v15, переводится одной онлайн-активацией:

```bash
cd ~/noezema
git pull                                   # пакет с prompts/explorer/explorer-v8.md
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v16-payload.json \
  --reason "T7.76: explorer-v8 (двусторонний поиск) + max_explorer_steps 16" \
  --drain-wait-seconds 120
```

Откат — активация payload'а v15 (он закоммичен и не менялся, canonical
`b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73`):

```bash
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v15-payload.json --reason "откат T7.76" --drain-wait-seconds 120
```

Что видно после активации: шаги новых сессий идут с `model_runs.prompt_version = explorer-v8`; карточка
вопроса про спорную величину показывает прочитанными оба источника и, если оценки различаются, их
расхождение в шагах работы; если независимая оценка не нашлась — вывод остаётся честным `E1` с причиной
«недостаточно независимых источников», а не молчаливым «Проверено» по одному первоисточнику. Сам лимит
шагов стал виден модели: контекст каждого шага содержит раздел «Бюджет шага» (шаг N из M, сколько
осталось) и точные схемы аргументов выданных инструментов — прежде модель знала только имена инструментов.

### Прогноз — не измерение состоявшегося: активация config-v17 (T7.77)

`config-v17` = `config-v16` ровно с одной правкой: `prompts.explorer` → **explorer-v9**. Правила 1–11
перенесены из explorer-v8 дословно (закреплено тестом сравнения блоков правил), добавлено ровно правило
12: *прогноз до события — не независимая оценка реализованного значения*. Прогноз сопоставим только с
ожиданиями (другие прогнозы, консенсус, оценки регулятора «что будет»); измерением состоявшегося называются
наблюдаемая и воспринимаемая инфляция по опросам домохозяйств, альтернативные индексы цен, независимые
академические исследования; пересказ официальной цифры со ссылкой на первоисточник вторым наблюдением не
считается (правила 9 и 10). Лимит шагов (16), пороги, `claim_type_rules`, token-бюджеты (Σ=26624), пин
curator-v8, окна модели, `research_proxy` и список инструментов — байт в байт config-v16: правка живёт в
инструкциях модели, движок независимости не тронут. Canonical payload
`5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717` (в `config_snapshots.payload_sha256`
попадает canonical, не хеш файла).

Узел, уже работавший на v16, переводится одной онлайн-активацией:

```bash
cd ~/noezema
git pull                                   # пакет с prompts/explorer/explorer-v9.md
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v17-payload.json \
  --reason "T7.77: explorer-v9 (прогноз — не оценка состоявшегося)" \
  --drain-wait-seconds 120
```

Откат — активация payload'а v16 (он закоммичен и не менялся, canonical
`740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3`):

```bash
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v16-payload.json --reason "откат T7.77" --drain-wait-seconds 120
```

Что видно после активации: шаги новых сессий идут с `model_runs.prompt_version = explorer-v9`; в карточке
вопроса про спорную величину предсобытийный прогноз не создаёт видимости независимого подтверждения —
если независимого измерения состоявшегося нет, вывод остаётся честным `E1` с причиной «недостаточно
независимых источников». Хостовая половина той же ловушки (пересказ официальной цифры со ссылкой на
первоисточник) закрыта в T7.77 детектором производности v2 и командой переатрибуции
`python -m hostctl.cli research-reattribute --since <дата>` —
STATUS §T7.77.

### Профиль рассуждения движка и обрезанный ответ: активация config-v18 (T7.80)

`config-v18` = `config-v17` ровно с одной правкой: в `model` добавлен `reasoning_by_phase` —
политика «рассуждать / не рассуждать» для каждой фазы **вызова** (не путать с состоянием сессии):

| фаза вызова | режим | почему |
|---|---|---|
| `consolidation` (куратор) | `off` | ответ куратора — самый длинный JSON в системе; замер 2026-10-08 (сессия 1d0886fa, halogen-flash-next): три ответа ровно по 8192 completion tokens с `finish_reason=length`, JSON оборван внутри строки → «failed schema after 3 attempts» → ноль утверждений |
| `extraction`, `verification` | `off` | у этих ответов тоже структурированный документ под тем же потолком; при выключенном рассуждении валидный schema-JSON приходит за доли секунды |
| `exploration`, `planning` | `on` | выбор инструмента и план — то, где рассуждение полезно; ответы короткие, срез под 8192 для них не измерялся |

Важно: **в config-v17 режимы `extraction.mode=off`, `verification.mode=off` и `planning.mode=template`**,
то есть на стенде сегодня существует только два реальных вызова — исследовательский и кураторский.
Политика для остальных фаз задекларирована заранее, чтобы не менять payload в момент включения режима.
`model.max_output_tokens` остаётся **8192**: у halogen комната ответа фиксирована (рассуждение закрывается
«by answer_room» примерно за ~1000 токенов до потолка), поэтому raising потолок не убирает обрезку длинного
JSON — она лишь переезжает. Проверено тестом активации (`tests/scenario/test_config_v18_activation.py`).

Самая правка payload ничего не меняет в запросе, пока узлу не сказано, **как** его движок умеет
отключать рассуждение. Это возможность движка, поэтому она в окружении (`ADR-0030`):

| движок | значение | что добавляется к запросу при режиме `off` |
|---|---|---|
| не измерен | `none` (дефолт стенда) | ничего: запрос байт в байт прежний, сравнимые прогоны не ломаются |
| halogen-* | `halogen` | `"reasoning_effort": "none"` (замер: `reasoning_tokens` = 0, валидный JSON за доли секунды; `low` и `thinking_budget` движок игнорирует) |
| llama.cpp c ChatML-шаблоном Qwen3.x | `chat-template` | `"chat_template_kwargs": {"enable_thinking": false}` (замер на 192.168.1.141: `reasoning_tokens` = 0) |

Неизвестное имя профиля — **отказ при старте** (`unknown reasoning_profile ...; known profiles: …`),
а не тихий дефолт: молча послать движку параметр, который он может отклонить, хуже чем ничего не послать
(та же логика, что у `NOEZEMA_LLM_SCHEMA_PROFILE`, ADR-0012). Профиль `none` при этом честно означает,
что обрезанный ответ становится немедленной ошибкой «ответ обрезан по лимиту» — повторять тот же запрос
при температуре 0 бессмысленно.

Активация (делает менеджер; узел, уже работавший на v17):

```bash
cd ~/noezema
git pull                                    # пакет с docs/eval/config-v18-payload.json
# одна строка в /etc/noezema/dev.env (0600): значение — по фактическому движку стенда.
# если строка уже есть с другим значением — править её вручную, не добавлять вторую
grep -q '^NOEZEMA_LLM_REASONING_PROFILE=' /etc/noezema/dev.env \
  || sudo sh -c 'printf "NOEZEMA_LLM_REASONING_PROFILE=halogen\n" >> /etc/noezema/dev.env'
sudo systemctl restart noezema-dev-web.service noezema-dev-tick.service noezema-dev-maint.service
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v18-payload.json \
  --reason "T7.80: model.reasoning_by_phase (curator/extractor/verifier without engine reasoning)" \
  --drain-wait-seconds 120
```

Либо с самого начала: `NOEZEMA_DEV_LLM_REASONING_PROFILE=halogen ./deploy/dev-stand/bootstrap.sh` —
скрипт положит строку в `/etc/noezema/dev.env` сам и напечатает `reasoning_profile=…` в сводке запуска.

Что видно после активации: `./status.sh` показывает снапшот по canonical-префиксу `b5605e4e`
(полный canonical config-v18 — `b5605e4eb04d610ca387f732bfa35f4571750fd4370013f2f2a0e8e7e45f9dec`;
в `config_snapshots.payload_sha256` попадает canonical, не хеш файла); кураторские строки
`model_runs` при обрезке получают `finish_reason='length'` и `output_tokens`, равный потолку, а аудит —
`curator_error_kind: "truncated_output"` вместо прежнего «unavailable». Проверочный вопрос — любой, где
ответ куратора длинный (например «ключевая ставка ЦБ РФ на последнем заседании»): при профиле `halogen`
второй кураторский запрос содержит `reasoning_effort="none"`, и утверждение появляется.

Откат — активация payload'а v17 (он закоммичен и не менялся, canonical
`5c402f4d75ae000c61d824ba2a4407bbfe8d94e0946311ff9ef90b06db721717`) плюс удаление строки профиля из
`/etc/noezema/dev.env` и рестарт юнитов: без политики `reasoning_by_phase` ни одна фаза режима не получает,
и запросы шлюза снова байт в байт прежние.

```bash
.venv/bin/python -m hostctl.cli activate-online \
  --payload docs/eval/config-v17-payload.json \
  --reason "T7.80 rollback: config-v17 (no phase reasoning policy)" \
  --drain-wait-seconds 120
```

### Дополнительный корневой сертификат для research-proxy (T7.77)

В certifi (базовое доверие httpcore, из которого `research.fetch` строит TLS-контекст) нет российского
корневого УЦ — на голом стенде `https://rosstat.gov.ru` даёт `CERTIFICATE_VERIFY_FAILED` (STATUS §T7.77).
`SSL_CERT_FILE` при этом не помогает: httpcore читает контекст из certifi и переменные окружения OpenSSL
не спрашивает. Оператор может добавить корень явно — только для egress research-proxy:

1. сертификат скачивает **оператор на своей машине** со страниц оператора доверенной инфраструктуры:
   `guft.mincifry.gov.ru` (УЦ Минцифры) / `gosuslugi.ru` — и уже свой PEM переносит на ВМ. Пакет стенда
   сертификат не скачивает и в репозитории корневых сертификатов нет;
2. на ВМ файл кладётся в `/etc/noezema/research-extra-ca.pem`, режим `0644` (например
   `sudo install -m 0644 research-extra-ca.pem /etc/noezema/research-extra-ca.pem`);
3. в `/etc/noezema/dev.env` добавляется строка
   `NOEZEMA_RESEARCH_EXTRA_CA_FILE=/etc/noezema/research-extra-ca.pem`;
4. веб перезапускается (`sudo systemctl restart noezema-dev-web.service`, сессии живут в нём) — переменная
   читается при сборке research-proxy, поэтому без перезапуска она не применится;
5. проверка глазом: новый вопрос про инфляцию, в ленте `research.fetch` доходит до `rosstat.gov.ru` без
   ошибки сертификата; или повторный тот же вопрос — отсутствие `CERTIFICATE_VERIFY_FAILED` в журнале.

Границы (закреплено тестами `tests/unit/test_research_tls_extra_ca.py`): PEM **добавляется** к стандартному
набору доверия, ничего не заменяет; `check_hostname` и обязательная верификация сохраняются — сертификат,
выпущенный не для того адреса, по которому идёт соединение, отвергается и с добавленным корнем; без
переменной поведение побайтно прежнее. Битая настройка (файла нет / файл не PEM) — research-proxy не
поднимается вовсе с ошибкой, называющей переменную и путь: узел не работает с половинной конфигурацией.

## Как оспорить утверждение оператору (T7.81)

Инструмент для случая, когда витрина показывает «Проверено», а оператор знает: вторая «независимая» оценка —
пересказ того же первоисточника (стендовый пример — `c970bc08…`, пресс-выпуск + страница банка). Оператор
**не назначает оценку и ничего не удаляет**: он утверждает факт о происхождении источника («эта страница —
пересказ той»), дальше работают существующие механизмы — коррекция графа источников (§11.3), каскад снятия
оценок, рабочий переоценки и правила. Разбор, пределы и честный отказ от ручного «снятия» утверждения —
STATUS.md T7.81 и ADR-0031.

### Шаг 1. Найти утверждение и адреса его источников

```bash
curl -s http://127.0.0.1:8321/api/v1/knowledge/claims |
  python3 -c 'import json,sys
for c in json.load(sys.stdin)["claims"]:
    print(c["id"], (c.get("reliability") or {}).get("level"), c["statement"][:70])'

curl -s http://127.0.0.1:8321/api/v1/knowledge/claims/<uuid>/provenance |
  python3 -c 'import json,sys
p = json.load(sys.stdin)
for e in p["evidence"]:
    s = e.get("source") or {}
    print(e["relation"], s.get("canonical_uri"))
print("операторские споры:", [(c["state_label"], c.get("reason")) for c in p["operator_corrections"]])'
```

Нужен **полный** uuid утверждения: лента и страница `/claim/<uuid>` дают его целиком. Спорить разрешено только
про адрес, который уже лежит среди улик этого утверждения (чужой адрес — отказ, и это правильно: спор обязан
опираться на прочитанное хостом).

### Шаг 2. Оспорить из хостовой консоли

```bash
cd ~/noezema                                                   # $APP_DIR, куда развернут репозиторий
set -a; . /etc/noezema/dev.env; set +a                        # креды из env-файла, не из командной строки
.venv/bin/python -m hostctl.cli claim-dispute \
  --claim <полный uuid> \
  --primary https://rosstat.gov.ru/press/<…> \
  --retelling https://www.sbercib.ru/economy/<…> \
  --reason "страница дословно повторяет абзацы пресс-выпуска и его таблицу"
```

В документах эта команда называется `noezemactl claim-dispute`. Причина обязательна (3…500 знаков): она
остаётся в журнале и показывается людям. Адрес вставляют как видно — с `www`, query‑строкой, хвостовым слэшем
или без схемы; он нормируется тем же правилом, что группировка источников, поэтому http и https одной страницы
— один и тот же источник. Вывод при успехе (код выхода `0`):

```
claim-dispute: оспорено
  утверждение   <uuid>
  коррекция     <uuid> (merge: <пересказ> → <первоисточник>)
  актор         operator:hostctl
  причина       страница дословно повторяет абзацы пресс-выпуска и его таблицу
  перепроверка  снятых оценок: 1, затронутых утверждений: 1, заведённых задач пересчёта: 1
  вопрос        <uuid> (новый, позиция N)
  дальше        оценку пересчитают правила на ближайшем maint-тике (noezemactl reassessment-tick);
                бейдж меняется только вместе с ней
```

Отказ — код выхода `2` и одна строка вместо трейса:
`claim-dispute: отказ (dispute_source_not_found) retelling: https://…`; в базе при этом не появляется ни
коррекции, ни события журнала (откат whole‑way). Повтор той же команды тем же оператором назван идемпотентным
повтором: второй коррекции, второго вопроса и второй задачи пересчёта нет.

### Шаг 3. То же из браузера

Открыть `/claim/<uuid>`: блок «Отношение оператора к этому утверждению» показывает действующие споры (пересказ
← первоисточник, причина, кто и когда) и прежние снятые; ниже — форма «Оспорить (фактом о происхождении
источника)»: два раскрывающихся списка адресов (они собраны из улик этого утверждения), поле «причина своими
словами», поле `admin token` (`sudo grep NOEZEMA_ADMIN_TOKEN /etc/noezema/dev.env`) и кнопки «Оспорить» /
«Снять спор». Это те же вызовы Command API, что и CLI: без токена — **401**, при нездоровом снимке юнитов —
**423** (за свежий снимок отвечает `noezema-dev-unit-state.timer`, 5 с).

### Шаг 4. Дождаться пересчёта и проверить глазами

Оценку пересчитывает `noezema-dev-maint.service` (таймер 60 с: reassessment + reconcile) — либо дождаться
тиками, либо запустить их явно (`… python -m hostctl.cli reassessment-tick`, затем `reconcile-tick`). После:

```bash
curl -s http://127.0.0.1:8321/api/v1/knowledge/claims |
  python3 -c 'import json,sys
for c in json.load(sys.stdin)["claims"]:
    print(c["id"], (c.get("reliability") or {}).get("level"), c.get("epistemic_status"))'

docker exec noezema-dev-db psql -U noezema -d noezema-dev -c \
  "SELECT actor, kind, valid, rules_version FROM source_graph_corrections ORDER BY created_at DESC"
```

Ожидаемое по спорному утверждению: сразу после спора голова `pending` — бейджа «Проверено» нет, потому что
pending никогда не подаётся как действующий ответ; после рабочего переоценки — `hypothesis` и E1 с подписанной
причиной понижения («недостаточно независимых источников»), а в provenance две группы независимости становятся
одной с основанием «склейка оператора». В очереди вопросов появляется «Перепроверить утверждение (операторский
спор): <текст утверждения>» — обычный вопрос, его можно задать узлу как обычно. Grade и статус по-прежнему
назначают правила: оператор их не выставил и не может выставить ни из консоли, ни из формы.

### Шаг 5. Откат спора

```bash
.venv/bin/python -m hostctl.cli claim-dispute-cancel --claim <полный uuid> \
  --reason "первоисточник всё-таки отдельный: другие таблицы и даты"
```

Строка коррекции не удаляется — она становится `valid = false`, каскад проходит по той же паре ещё раз, и
прежняя оценка возвращается сама (`supported`/E3), если независимости снова достаточно. На карточке видно и
исходный спор (актор, время, причина), и снятие (кем, когда, почему). Знание, улики и все прежде сделанные
оценки остаются в базе полностью (§14): удаления знания этим механизмом нет. Миграций, перезапуска юнитов и
смены снапшота правил ни для спора, ни для отмены не требуется — достаточно `status.sh`, чтобы увидеть
состояние узла.

### Что при этом важно понимать

- Каскад снимает оценки у тех утверждений, **чьи улики используют эти источники**. Утверждение, связанное со
  спорным только через зависимость (`claim_dependencies`), остаётся действующим — это семантика существующего
  `apply_source_graph_change`, она не расширена (тест `test_every_claim_that_used_those_sources_is_requeued`).
- Второй оператор не может наложить вторую склейку на ту же пару: он получит отказ с подписанной причиной
  («источники уже объединены»). Если спор снят, та же строка оживает при новом споре — история не затирается.
- Спор опирается на артефакт прочтения пересказывающей страницы (`basis_artifact_id`): если хост её не читал,
  оспорить пару нельзя — спор возможен только про то, что уже стало уликой.
- Ручного перевода утверждения в «недействующее» на стенде **нет**: это остановлено по стоп-критерию задачи,
  варианты и последствия — ADR-0031 §6 (решение за пользователем).

## Безопасность и границы

- UI на стенде **читается из LAN**, если его поднять на `0.0.0.0`: статус, очередь вопросов,
  сообщения, лента событий и сессии доступны без токена (§13.1). Наружу (WAN) стенд не выставлять.
- Команды и приём вопроса — Command-контур: `X-Admin-Token`, иначе **401** (T3.18). При bind на
  не-loopback адрес **без** `NOEZEMA_ADMIN_TOKEN` web отказывается стартовать с понятной ошибкой и
  кодом 78 (`apps/web/bind.py`) — открытого «на весь сегмент» узла без управления не получается.
- Host-гейт (T3.24): устаревший снимок юнитов, незавершённый host-переход или активная смена
  политики → POST-эндпоинты отвечают **423** с `recovery_state`, GET продолжают работать. Поэтому на
  стенде и есть таймер публикации юнитов (TTL снимка — 15 с, период 5 с).
- Секреты: генерируются в env-файл (0600), никогда не печатаются; `--dry-run` маскирует креды в
  планируемых командах.
- Postgres опубликован только на `127.0.0.1`. Sandbox-исполнитель (`NOEZEMA_TOOL_EXECUTOR=sandbox`)
  требует наличия образа и доступа пользователя юнитов к `/var/run/docker.sock` (bootstrap добавляет
  его в группу `docker`). Если это неудобно — `--stub-executor`: инструменты исполняются на хосте без
  изоляции, допустимо только для dev-стенда (AGENTS §7).

## Что остаётся непроверенным до реального деплоя

Реальный LLM на 192.168.1.42 и профиль схемы `none` на нём; настоящее поведение сессий на VM без GPU
(длины, тайминги, диск); работа юнитов под живым systemd (дома проверен только синтаксис); реальный
пакетный путь установки docker и `uv` на конкретной сборке 24.04 (фолбэкер astral-установщика под sudo
реализован, но на живой ВМ ещё не исполнялся); поведение автовыбора порта Postgres и diagnostics при
занятом 5432 — проверено только тестами с **подставными** командами (`tests/unit/test_dev_stand_scripts.py`,
`--dry-run`), не на .92; реальный ufw этой ВМ и требуемые ему правила; доступность docker-сокета
пользователю юнитов после `usermod` без перелогина; скорость и объемы первой настоящей сессии; реальная выдача SearXNG на `.92` (какие движки отвечают и что именно они находят) и поведение `web.search` при живом поиске — харнесс проверял это только тестами с фейковым SearXNG на `127.0.0.1` (`tests/scenario/test_web_search_tool.py`, `tests/security/test_search_injection.py`), ни одного внешнего запроса не делал.
Подробный список — в `docs/STATUS.md`, раздел T7.59(б) и T7.59(в).
