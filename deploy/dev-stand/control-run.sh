#!/bin/bash
# T7.118 (часть 3): контрольный набор вопросов для отчёта честности `hostctl honesty-report`.
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
# SKIP), принимает вопрос приоритетом 9, будит узел wake_now, ждёт окончания сессии (потолок
# 45 минут), печатает журнал ASKED/DONE c id, временем и состоянием узла. Секреты не печатаются.
# В конце печатает команду отчёта — её оператор выполняет сам.
set -u
set -a; . /etc/noezema/dev.env; set +a

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONTROL_SET="$REPO_ROOT/docs/eval/control-set-v1.jsonl"
API=http://127.0.0.1:8321/api/v1

TAG="${1:-}"
if ! [[ "$TAG" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,40}$ ]]; then
  echo "использование: $0 <метка контрольного прогона (буквы, цифры, . _ -)>" >&2
  exit 2
fi
if [ ! -f "$CONTROL_SET" ]; then
  echo "не найден контрольный набор: $CONTROL_SET" >&2
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

while IFS=$'\t' read -r qid qtext; do
  waited=0
  while [ "$(node)" != "idle" ] && [ "$waited" -lt 600 ]; do
    sleep 20
    waited=$((waited + 20))
  done
  state="$(node)"
  if [ "$state" != "idle" ]; then
    echo "SKIP  $qid $(date -u +%FT%TZ) node=$state (за 10 минут узел не вернулся в idle;" \
         "возможно, узел на паузе — hostctl resume-runtime)"
    continue
  fi
  id="$(ask "$qtext (контроль $TAG)")"
  echo "ASKED $qid $id $(date -u +%FT%TZ) :: ${qtext:0:60}"
  wake
  sleep 30
  end=$(( $(date +%s) + 2700 ))
  while [ "$(node)" = "session_running" ] && [ "$(date +%s)" -lt "$end" ]; do
    sleep 30
  done
  state="$(node)"
  if [ "$state" = "session_running" ]; then
    echo "TIMEOUT $qid $id $(date -u +%FT%TZ) node=$state (45 минут прошли, сессия ещё идёт)"
  else
    echo "DONE  $qid $id $(date -u +%FT%TZ) node=$state"
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
# команда в запускаемой на стенде форме: её печатают и выполняет оператор из каталога репозитория
echo "отчёт: .venv/bin/python -m hostctl.cli honesty-report --control-tag $TAG --json \"control-$TAG-honesty.json\" (из каталога ~/noezema)"
