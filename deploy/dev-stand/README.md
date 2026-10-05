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
| `reset-db.sh` | пересоздание dev-базы с явным подтверждением + миграции + повторная активация config-v13 |
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
`--force`, `--rotate-secrets`, `--with-tick-timer`, `--web-host 0.0.0.0`, `--web-port 8321`,
`--user <name>`.
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
| конфигурация | активация `docs/eval/config-v13-payload.json` через `hostctl activate-online` (окно EXL3 131072; пропускается, если снапшот уже активен; переход v12→v13 — STATUS.md T7.59(в)) |
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
   активирует config-v13, поднимает таймеры. С защитой по состоянию: если в базе есть незавершённая
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

- В `config-v13` стоит `wake_schedule.gpu_required = false`, поэтому на VM без GPU admission
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
пользователю юнитов после `usermod` без перелогина; скорость и объемы первой настоящей сессии.
Подробный список — в `docs/STATUS.md`, раздел T7.59(б) и T7.59(в).
