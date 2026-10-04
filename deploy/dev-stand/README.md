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
| `bootstrap.sh` | идемпотентная установка: пакеты → venv/зависимости → env-файл с секретами → каталоги + sandbox-образ → Postgres 15 в docker (127.0.0.1) → база + миграции + активация конфигурации → юниты |
| `systemd/*` | 8 файлов стенда: target, web, tick+timer, maint+timer, unit-state+timer |
| `status.sh` | состояние: юниты, docker, очередь вопросов, последняя сессия, доступность LLM, версия кода, режим исполнителя инструментов |
| `reset-db.sh` | пересоздание dev-базы с явным подтверждением + миграции + повторная активация config-v12 |
| `README.md` | этот файл |

## Деплой (делает менеджер)

```bash
ssh <user>@192.168.1.92
git clone -b impl/from-scratch <repo> ~/noezema && cd ~/noezema
./deploy/dev-stand/bootstrap.sh --dry-run     # план: что будет сделано, ничего не меняется
sudo ./deploy/dev-stand/bootstrap.sh          # фактическая установка (скрипт сам добавит sudo где нужно)
systemctl start noezema-dev.target            # web + три таймера
./deploy/dev-stand/status.sh                  # проверка глазом
```

Флаги `bootstrap.sh`: `--dry-run`, `--no-docker-install`, `--no-units`, `--stub-executor`,
`--force`, `--web-host 0.0.0.0`, `--web-port 8321`, `--user <name>`. Идемпотентность: повторный запуск
не пересоздаёт venv, базу, контейнер Postgres, sandbox-образ и admin-токен; существующие секреты и
данные сохраняются без `--force` (перезапись — только по явному флагу).

## Что где лежит и какие порты

| | |
|---|---|
| приложение | репозиторий как есть (`$APP_DIR`), venv в `$APP_DIR/.venv`, зависимости **только через uv** и только prod-extras (AGENTS §6) |
| база | docker-контейнер `noezema-dev-db` (образ `postgres:15`), том `noezema-dev-pgdata`, публикация **только `127.0.0.1:5432`**, база `noezema-dev`, пользователь `noezema` |
| миграции | `alembic upgrade head` из venv (URL из env-файла) |
| конфигурация | активация `docs/eval/config-v12-payload.json` через `hostctl activate-online` (пропускается, если снапшот уже активен) |
| данные сессий | `/var/lib/noezema-dev` (+ `sandbox` — work_root контейнерного исполнителя) |
| host-контур стенда | `/var/lib/noezema-dev/host`, снимок юнитов: `/var/lib/noezema-dev/host/unit-state.json` |
| секреты/настройки | `/etc/noezema/dev.env`, режим **0600**, владелец — пользователь стенда; в отчёты и чат не попадают (AGENTS §5) |
| web UI/API | `127.0.0.1:8321` по умолчанию (`NOEZEMA_WEB_HOST`/`NOEZEMA_WEB_PORT`) |
| sandbox-образ | `noezema-sandbox:dev-stand` (собран из `sandbox/Containerfile`, закреплён одной меткой в `NOEZEMA_SANDBOX_IMAGE`; dev/test метки не затрагиваются) |
| LLM | `http://192.168.1.42:8080/v1`, модель `qwen38-exl3-3bpw-128k`, `NOEZEMA_LLM_SCHEMA_PROFILE=none`, `MAX_OUTPUT_TOKENS=8192`, `TIMEOUT_SECONDS=600` (переопределяется `NOEZEMA_DEV_LLM_*`) |

## Пользование: цикл MVP

1. **Открыть UI:** `http://127.0.0.1:8321`. Если стенд поднят на LAN (`--web-host 0.0.0.0`), то же
   самое читается с другой машины как `http://<VM>:8321` — GET-эндпоинты открытые (§13.1).
2. **Задать вопрос:** карточка «Задать вопрос» (текст ≤ 2000, приоритет, поле admin-токена; токен
   запоминается в `sessionStorage` браузера). Токен = `NOEZEMA_ADMIN_TOKEN` из env-файла:
   `sudo grep NOEZEMA_ADMIN_TOKEN /etc/noezema/dev.env`. Тот же самый текст второй раз **не
   создаёт** вторую строку очереди — вернётся существующий id (`replayed: true`).
3. **ВПЕРЁД очереди:** приоритет выше обычного (например 9). FIFO выбирает кандидата по
   `priority DESC, created_at ASC` (§5.3.2), то есть вопрос оператора с приоритетом обгоняет
   остальные кандидаты, а среди равных приоритетов решает возраст. Эквивалент из консоли:
   `.venv/bin/python -m hostctl.cli ask "почему падает X?" --priority 9`.
4. **wake now:** кнопка «wake now» в карточке «Узел» (это `POST /api/v1/commands` с
   `{"type":"wake_now", "idempotency_key": ...}`) либо `.venv/bin/python -m hostctl.cli wake-tick`,
   либо `sudo systemctl start noezema-dev-tick.service`. Команда обходит только тайминг расписания;
   шлюзы admission (§5.2.1) остаются и могут ответить «skip» — это нормальный ответ планировщика.
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
   активирует config-v12, поднимает таймеры. С защитой по состоянию: если в базе есть незавершённая
   сессия, reset откажется — сначала дождаться её терминации (или остановить tick-таймер).

## Юниты стенда (8 файлов)

| юнит | что делает |
|---|---|
| `noezema-dev.target` | группа стенда; сам не включается в загрузку (никто не хочет его в `multi-user.target`) |
| `noezema-dev-web.service` | web UI/API, `Type=simple`, `Restart=always`, bind из env-файла |
| `noezema-dev-tick.service` + `.timer` | один `hostctl wake-tick` на срабатывание; `Type=oneshot`, `TimeoutStartSec=3600` (сессия имеет право отработать свой бюджет), таймер 60 с, `OnBootSec=10m` |
| `noezema-dev-maint.service` + `.timer` | два шага подряд: `reassessment-tick` затем `reconcile-tick`, таймер 60 с |
| `noezema-dev-unit-state.service` + `.timer` | публикация снимка юнитов каждые 5 с — без него Command API и приём вопроса на стенде отвечают 423 |

Все сервисы — `PartOf=noezema-dev.target`, все таймеры — `WantedBy=noezema-dev.target`: остановка
target останавливает wakes, публикацию и web разом. Проверка синтаксиса на dev-машине:
`systemd-analyze verify --man=no` на отрендеренных копиях (тот же критерий, что у
`scripts/verify_systemd_units.sh`).

## VM без GPU и пустая очередь — это не ошибка

- В `config-v12` стоит `wake_schedule.gpu_required = false`, поэтому на VM без GPU admission
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
(длины, тайминги, диск); работа юнитов под живым systemd (дома проверен только синтаксис); путь
установки docker/uv через apt на конкретной сборке 24.04; доступность docker-сокета пользователю
юнитов после `usermod` без перелогина; скорость и объемы первой настоящей сессии. Подробный список —
в `docs/STATUS.md`, раздел T7.59(б).
