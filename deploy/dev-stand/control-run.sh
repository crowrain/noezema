#!/bin/bash
# T7.118 (часть 3): контрольный набор вопросов для отчёта честности `hostctl honesty-report`.
# T7.131 (часть 2): ожидание законченной сессии ИМЕННО ЭТОГО вопроса и повторный wake_now.
#
# ЗАПУСКАЕТ ТОЛЬКО ОПЕРАТОР на ВМ стенда (192.168.1.92), после развертывания/выпуска.
# Агент этот скрипт не запускает и сессий на стенде не поднимает.
#
# Использование:  ./control-run.sh <метка>
#   <метка> — метка контрольного прогона: буквы/цифры/`.`/`_`/`-` (то же правило, что у
#   `hostctl honesty-report --control-tag`). Тексты вопросов читаются из
#   docs/eval/control-set-v1.jsonl — того же файла, по которому сверяют ожидания (CONTROL-SET.md);
#   вопрос отправляется текстом `… (контроль <метка>)`, чтобы отчёт отобрал ровно эти вопросы.
#
# Что делает: для каждого из пяти вопросов ждёт idle-узла (потолок 10 минут, дальше честный
# SKIP), принимает вопрос приоритетом 9, будит узел wake_now и ждёт, пока У ЭТОГО ВОПРОСА
# появится сессия в терминальном состоянии. Признак — открытая карточка
# `GET /api/v1/questions/<id>/answer` (состояние вопроса и его сессии), а НЕ состояние узла:
# `system_constants.node_state` описывает УЗЕЛ, а не этот вопрос, и строка сессии вместе с
# `questions.state` невидимы до фиксации фазы 1 (T7.61(а)) — между wake и стартом сессии узел
# бывает `idle` законно (отказанное пробуждение, свернувшаяся работа, сессия другого процесса).
# Если вопрос всё ещё `candidate`, а узел `idle` дольше 60 с, скрипт повторяет wake_now
# (не больше 3 раз, каждый повтор — строка WAKE в журнале). Потолок 45 минут на вопрос прежний;
# строки ASKED/DONE/TIMEOUT прежние по формату, в DONE дописаны id сессии и её состояние.
# Секреты не печатаются. В конце печатает команду отчёта — её оператор выполняет сам.
set -u
ENV_FILE="${NOEZEMA_DEV_ENV_FILE:-/etc/noezema/dev.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "не найден env-файл стенда: $ENV_FILE (переменная NOEZEMA_DEV_ENV_FILE)" >&2
  exit 2
fi
set -a; . "$ENV_FILE"; set +a

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONTROL_SET="${CONTROL_RUN_SET:-$REPO_ROOT/docs/eval/control-set-v1.jsonl}"
WEB_HOST="${NOEZEMA_WEB_HOST:-127.0.0.1}"
WEB_PORT="${NOEZEMA_WEB_PORT:-8321}"
API="${NOEZEMA_DEV_API:-http://${WEB_HOST}:${WEB_PORT}/api/v1}"

# Интервалы ожидания, секунды. Наружу вынесены (вместе с CONTROL_RUN_SET — набором вопросов)
# только затем, чтобы тест прогнал этот скрипт против подставного API быстро; на стенде работают
# дефолты (30 / 20 / 600 / 2700 / 60 / 3).
POLL="${CONTROL_RUN_POLL_SECONDS:-30}"
PRE_POLL="${CONTROL_RUN_PRE_POLL_SECONDS:-20}"
PRE_LIMIT="${CONTROL_RUN_PRE_IDLE_LIMIT_SECONDS:-600}"
MAX_WAIT="${CONTROL_RUN_MAX_WAIT_SECONDS:-2700}"
RETRY_AFTER="${CONTROL_RUN_CANDIDATE_RETRY_SECONDS:-60}"
MAX_WAKE="${CONTROL_RUN_WAKE_RETRIES:-3}"

TAG="${1:-}"
if ! [[ "$TAG" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,40}$ ]]; then
  echo "использование: $0 <метка контрольного прогона (буквы, цифры, . _ -)>" >&2
  exit 2
fi
if [ ! -f "$CONTROL_SET" ]; then
  echo "не найден контрольный набор: $CONTROL_SET" >&2
  exit 2
fi
if [ -z "${NOEZEMA_ADMIN_TOKEN:-}" ]; then
  echo "в $ENV_FILE нет NOEZEMA_ADMIN_TOKEN: вопрос и команду wake_now не принять" >&2
  exit 2
fi

node() {
  curl -s -m 8 "$API/status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('node_state'))"
}
ask() {
  curl -s -m 10 -X POST "$API/questions" -H "Content-Type: application/json" \
    -H "X-Admin-Token: $NOEZEMA_ADMIN_TOKEN" \
    -d "$(python3 -c "import json,sys; print(json.dumps({'text': sys.argv[1], 'priority': 9}, ensure_ascii=False))" "$1")" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"
}
wake() {
  curl -s -m 10 -X POST "$API/commands" -H "Content-Type: application/json" \
    -H "X-Admin-Token: $NOEZEMA_ADMIN_TOKEN" \
    -d "{\"type\":\"wake_now\",\"idempotency_key\":\"t7118-$TAG-$(date +%s%N)\"}" >/dev/null
}
# Карточка вопроса (открытый GET, токен не нужен): состояние вопроса плюс терминальная сессия,
# начатая НЕ РАНЬШЕ, чем мы задали вопрос. Оговорка про время нужна дедупу текста (T7.59):
# повтор прогона той же меткой получает тот же id, а его прежняя сессия — не ответ этого прогона.
# Строка вывода: "<состояние вопроса>|<id сессии>|<состояние сессии>"; незаполненные поля пусты.
card() {
  CARD_JSON="$(curl -s -m 8 "$API/questions/$1/answer")" CARD_NOT_BEFORE="$2" python3 -c '
import datetime
import json
import os

TERMINAL = ("succeeded", "succeeded_partial", "failed", "cancelled")
try:
    not_before = float(os.environ.get("CARD_NOT_BEFORE") or 0)
except ValueError:
    not_before = 0.0
try:
    card = json.loads(os.environ.get("CARD_JSON") or "")
except Exception:
    print("||")
    raise SystemExit
question = card.get("question") or {}
qstate = str(question.get("state") or "")
for session in (card.get("sessions") or []):
    if str(session.get("state")) not in TERMINAL:
        continue
    try:
        stamp = datetime.datetime.fromisoformat(str(session.get("started_at") or "")).timestamp()
    except ValueError:
        stamp = not_before
    if stamp + 2 >= not_before:
        print("%s|%s|%s" % (qstate, session.get("id") or "", session.get("state")))
        break
else:
    print("%s||" % qstate)
'
}

while IFS=$'\t' read -r qid qtext; do
  waited=0
  while [ "$(node)" != "idle" ] && [ "$waited" -lt "$PRE_LIMIT" ]; do
    sleep "$PRE_POLL"
    waited=$((waited + PRE_POLL))
  done
  state="$(node)"
  if [ "$state" != "idle" ]; then
    echo "SKIP  $qid $(date -u +%FT%TZ) node=$state (за ${PRE_LIMIT} с узел не вернулся в idle;" \
         "возможно, узел на паузе — hostctl resume-runtime)"
    continue
  fi
  asked_epoch="$(date +%s)"
  id="$(ask "$qtext (контроль $TAG)")"
  if [ -z "$id" ]; then
    echo "SKIP  $qid $(date -u +%FT%TZ) node=$state (вопрос не принят: API не вернул id)"
    continue
  fi
  echo "ASKED $qid $id $(date -u +%FT%TZ) :: ${qtext:0:60}"
  wakes=1
  wake "$wakes"
  finished=false
  paused=false
  left_candidate=false
  candidate_idle=0
  qstate=""
  session_id=""
  session_state=""
  note=""
  waited=0
  while [ "$waited" -lt "$MAX_WAIT" ]; do
    IFS='|' read -r qstate session_id session_state <<CARD_LINE
$(card "$id" "$asked_epoch")
CARD_LINE
    if [ -n "$session_state" ]; then
      finished=true
      break
    fi
    case "$qstate" in
      selected|researching) left_candidate=true ;;
    esac
    state="$(node)"
    if [ "$state" = "session_running" ]; then
      candidate_idle=0
    elif [ "$state" = "paused" ]; then
      paused=true
      break
    elif [ "$state" = "idle" ] && [ "$qstate" = "candidate" ]; then
      # Пробуждение принято, а сессия этого вопроса так и не началась: узел снова idle, вопрос
      # всё ещё в очереди. Повторный wake_now — не чаще одного раза в RETRY_AFTER секунд.
      candidate_idle=$((candidate_idle + POLL))
      if [ "$candidate_idle" -ge "$RETRY_AFTER" ] && [ "$wakes" -le "$MAX_WAKE" ]; then
        wakes=$((wakes + 1))
        wake "$wakes"
        echo "WAKE  $qid $id $(date -u +%FT%TZ) node=$state вопрос ещё candidate," \
             "повторный wake_now $wakes/$((MAX_WAKE + 1))"
        candidate_idle=0
      fi
    elif [ "$state" = "idle" ] && [ "$left_candidate" = true ] && [ -n "$qstate" ] \
        && [ "$qstate" != "candidate" ] && [ "$qstate" != "selected" ] && [ "$qstate" != "researching" ]; then
      # Вопрос вышел из выбора (работа свернулась, не зафиксировав строку сессии), узел idle.
      # Выход обязан быть ВИДЕН в этом прогоне: у вопроса прежнего прогона (дедуп текста)
      # состояние изменилось до нашего вызова, и это не конец работы текущего прогона.
      finished=true
      note="(сессия этого вопроса не записана; вопрос=$qstate)"
      break
    fi
    sleep "$POLL"
    waited=$((waited + POLL))
  done
  state="$(node)"
  if [ "$paused" = true ]; then
    echo "PAUSED $qid $id $(date -u +%FT%TZ) node=paused (сессии не идут — hostctl resume-runtime)"
    continue
  fi
  if [ "$finished" = true ]; then
    echo "DONE  $qid $id $(date -u +%FT%TZ) node=$state session=${session_id:-—} state=${session_state:-—}${note:+ $note}"
  else
    echo "TIMEOUT $qid $id $(date -u +%FT%TZ) node=$state (потолок ${MAX_WAIT} с прошли," \
         "сессия этого вопроса не завершилась; вопрос=${qstate:-?} wake_now вызовов=$wakes)"
  fi
done < <(python3 -c '
import json
import sys
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    q = json.loads(line)
    print(q["id"] + "\t" + q["text"])
' "$CONTROL_SET")

echo "CONTROL_DONE tag=$TAG"
# команда в запускаемой на стенде форме: её печатают и выполняют оператор из каталога репозитория
echo "отчёт: .venv/bin/python -m hostctl.cli honesty-report --control-tag $TAG --json \"control-$TAG-honesty.json\" (из каталога ~/noezema)"
