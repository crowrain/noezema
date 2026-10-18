# SMOKE-V14-EXL3 — разбор смоук-прогона (T7.52b)

Разбор-анализ (только SELECT и чтение; код, тесты, payload'ы, промпты,
корпуса, ARCHITECTURE.md не тронуты). Прогон — первый бой связки
**EXL3 + explorer-v5 / config-v12**, ожидавший T7.50 («проверка эффекта
нового промпта на модели — отдельный контрольный смоук V14»). Запуск —
T7.52a (переключение env на EXL3 на 192.168.1.42:8080 и старт).

> Указатель (T7.53): конфаунд этого отчёта (модель + explorer-v5 сразу)
> разобран контрольным прогоном **SMOKE-V14B-EXL3** (EXL3 + explorer-v4 /
> config-v11, `docs/eval/SMOKE-V14B-EXL3-report.md`): эффект explorer-v5 на
> EXL3 данными не подтверждается; исторический текст этого отчёта не переписан.

## 0. Конфаунд: два фактора вместо одного

Относительно SMOKE-V13-K2 в этом прогоне изменены **две переменные
одновременно**:

1. **модель**: `k2-horizon-mova-36b-a4b-rocmfp4-fast` (192.168.1.48,
   профиль `llamacpp-rocmfpx`) → `qwen38-exl3-3bpw-128k` (EXL3
   Qwen3-8B 3bpw, окно 131072; 192.168.1.42:8080, профиль `none`);
2. **промпт/payload**: config-v11 (curator-v7 + **explorer-v4**) →
   config-v12 (curator-v7 — без изменений + **explorer-v5**, T7.50).

Не менялись: код (`3b7c577`, поведение продукта то же, что в V13;
T7.51 — только тесты), корпус (`b3e05ad5…`), правила (rules-v2,
`f96eeffc527c…`), пороги гейтов, seed 20260924, session_limits и
payload-бюджеты. Поэтому **атрибуция «что дал EXL3, а что —
explorer-v5» по одному прогону невозможна**; допустимы только
безусловные утверждения о паре «explorer-v5 на EXL3» как об одном
экспериментальном целом (§7). Контрольные прогоны для разнесения — §7.

## 1. Остановка воркера и состояние 7/7

Остановка — **после** SELECT-проверок (как T7.48b):

- `sessions`: 7/7 терминальные — 6 `succeeded` + 1 `succeeded_partial`,
  нетерминальных 0;
- `commit_attempts`: 7/7 `committed`, незакрытых попыток 0; потерь сессий
  нет (eligible 7 = completed 7);
- `systemctl --user stop smoke-v14-exl3-worker` → `inactive` (rc=4, unit
  не запущен); все юниты серии `smoke-*` (14 шт.) inactive; процессов
  `worker.sh` / `hostctl` не осталось.

Условия прогона:

| Параметр | Значение |
|---|---|
| код | `3b7c577` (`impl/from-scratch`, чисто, = origin) |
| модель | `qwen38-exl3-3bpw-128k` (EXL3 Qwen3-8B 3bpw, окно 131072; llama-swap/llama.cpp на 192.168.1.42:8080) |
| профиль схемы | `none` (полная pydantic-схема action-envelope/v1 уходит движку как есть) |
| payload | `docs/eval/config-v12-payload.json`: file `493970d8…`, canonical `c23005bd…`; пины curator-v7 `19d6c6e8…`, **explorer-v5 `3b1fd49d…`** (активирован запуском; snapshot `11c2f8c7-…`) |
| корпус | `question-set-smoke.jsonl` (7 вопросов, sha `b3e05ad5…`) — побайтово тот же, что v8…v13 |
| правила | rules-v2, `f96eeffc527c…` (не менялся) |
| БД | `noezema-smoke-v14-exl3` (создана запуском) |
| run | id `b99f8ef5-1111-4028-9824-d91f69bdfb3e`, seed 20260924, slo 3600 s, blind-size 10 |
| время | 2026-10-04 02:05:03Z → 02:21:41Z (**16,6 мин** — самый быстрый из серии), EXIT=0, outcome `insufficient_sample` (N=7) |
| воркер | `reassessment-tick --batch-size 50 --lease-seconds 120` + `reconcile-tick`, цикл 15 s (101+101 тик-блока) |

Хронология сессий (по audit-sequence; длительность от session-границ):

| # | Сессия | Вопрос | Шаги | Итог |
|---|---|---|---|---|
| 1 | `c4dcac54` | ООН (anchor) | 3 | succeeded, 50 с |
| 2 | `bcdcae2c` | Python (anchor) | 5 | succeeded, 304 с (один curator-вызов 180 с) |
| 3 | `3b4014d1` | plan.md (anchor) | 3 | succeeded, 41 с |
| 4 | `b2c73bf8` | Python FU | 3 | succeeded, 97 с |
| 5 | `640ae74e` | plan FU | 2 | succeeded, 31 с |
| 6 | `2ac7c041` | Sputnik | 3 | succeeded, 56 с |
| 7 | `05303f68` | Go (anchor) | 10 (+curator) | **succeeded_partial**, 415 с, бюджет исчерпан |

## 2. EXL3 как модель: метрики, C0/NUL/repeats, verifier, retry/deny, качество по вопросам, fingerprint

### 2.1 Структурные метрики (36 model_runs)

| Метрика | exploring (29) | consolidating (7) |
|---|---|---|
| finish_reason | stop 29/29 | stop 7/7 |
| output_schema_valid (полной моделью хоста) | 29/29 | 7/7 |
| output tokens med / p95 / max | 510 / 3409 / **3784** | 1905 / 3879 / **4481** |
| input tokens med / max | 10754 / **30617** | 5408 / 10852 |
| latency med / max | 12,5 с / 131,1 с | 29,6 с / **180,1 с** |

- Все outputs ≤ `max_tokens=8192` (env gateway) → ни одного
  `finish_reason=length`; AGENTS-ловушка «reasoning съедает бюджет» в
  этом прогоне не сработала (ни одной пустой content-сессии).
- Скорость генерации (output tokens / суммарная latency): exploring
  ≈ **47,1 tok/s**, consolidating ≈ **45,2 tok/s**; медианная скорость
  отдельного вызова — 61,0 и 74,9 tok/s. Для сравнения V13-K2: 24,4 /
  20,7 tok/s → EXL3 на .42 примерно в **2 раза быстрее** K2-FAST на .48
  при заметно меньшем числе параметров (безусловное наблюдение пары).
- planner/verifier вызовов — **0**; curator = ровно 7 (по одному на
  сессию).

### 2.2 C0/NUL/repeats

- PostgreSQL, коды 1–31 по 14 колонкам (payload'ы staging/audit, jsonb
  sessions, claims, evidence.scope, sources.metadata, questions.text):
  **0 вхождений** — ни NUL, ни вертикального табуляции, ни прочих
  управляющих кодов. Прямых литер `\x00`-строк тоже 0.
- Диск `~/dsh1/smoke-v14/data` (19 файлов артефактов): контрольных байт
  **нет**. «Повторы» встречаются только как цепочки пробелов (hex 20,
  длина ≥16) в нормализованном тексте скачанных страниц и в payload'ах
  action_completed — это содержимое fetched-HTML в фенсе UNTRUSTED DATA,
  а не продуктовые данные: claims/staging/questions/evidence/sessions/
  termination — чистые.
- **`python.execute` за прогон не вызывался ни разу** → фиксы NUL-каналов
  (T7.46a/T7.47a) снова **не подтверждены «в бою»** — прогон доказывает
  только отсутствие регрессии (как V13). Маркерного случая не было.

### 2.3 Verifier

Вызовов verifier-роли нет; в config-v12 `verification.mode = "off"` —
фаза выключена конфигом (как во всех прошлых смоуках). Исследовательская
проба VerifierReport/loop на `u000b` (2026-10-03, T7.52a) в продукт не
попала и в прогоне не участвовала.

### 2.4 Retry / deny

- policy_evaluated: **allow 20** (`research.fetch` ×16, `workspace.read`
  ×2, `workspace.write` ×1, `workspace.list` ×1), **deny 0**;
- отказ не policy, а repetition-guard: `tool_call_repeated` — 1
  (Go-сессия, seq 28: третий идентичный fetch после `executed_count=2`,
  `repetition.enabled=true` в снапшоте прогона);
- action_failed ×2 (обе Go): failed `research.fetch` с ошибкой хоста
  «normalized artifact unreadable» (action=raw-вариант wiki, 38 B) и
  сам отказ guard'а;
- **отклонения complete хостом — 3** (Go, coverage gate, §4.3 этого не
  гейта); `unknown_actions` = 0; событий retry/transport-ошибок gateway
  в audit нет → попыток host-retry не потребовалось;
- search-инструмент (`memory.search`) и searxng за прогон **не
  использовались** (ответы строились прямыми fetch'ами названных
  источников); `question.create` — 0: модель новых вопросов не создала
  (в V13 был один).

### 2.5 Качество по 7 вопросам против K2-V13

Проверка groundedness — по артефактам на диске (raw/normalized sha
совпадают с БД):

| Вопрос | V14-результат | Grounding в скачанных источниках | V13-K2 для сравнения |
|---|---|---|---|
| ООН | claim `e7e09270` E3 supported «15 апреля 2026 … 193 государства-члена», as_of **explicit** 2026-04-15, conf .75 | un.org/about-us: «193 Member States»; ru.wiki список ✓ | тот же ответ (формулировка богаче: +193-е государство в 2011) |
| Python anchor | `7758de6f` E3 supported «…стабильная версия Python — **3.14.8**», as_of host-derived 2026-10-04 (anchor relative), reverify_after 2026-11-03 | python.org normalized: «Download Python 3.14.8» ×5; chocolatey: пакет «Python 3.14 **3.14.8**» (+список 3.14.0…3.14.8) ✓ | 3.14.7 (as_of 2026-09-23). Контент chocolatey между датами изменился (`fe05677a`→`21d87864`) — новый патч-релиз; python.org raw `f6c11887…` идентичен обеим прогонам |
| plan.md anchor | `d68694fb` E2 supported «ровно 3 непустых пункта: дату, повестку и ответственных», local_observation/evergreen, conf .55 | workspace.write → workspace.read (evidence), содержимое в workspace-манифесте ✓ | тот же результат |
| Python FU | reuse `7758de6f` + **новая assessment** (сессия `b2c73bf8`) + **новая evidence**-строка (пересвежённый chocolatey, sha `612e0d2f…`) | повторный fetch тех же двух источников ✓ | reuse через **existing_claim_id=полный UUID** (правило 7) + claim_reverified ×2 |
| plan FU | reuse `d68694fb` + новая assessment; evidence-дедуп (read побайтово идентичен → новых строк 0) — ровно случай ADR-0018 «перепроверка на той же evidence» | ✓ | аналогично (UUID-путь) |
| Sputnik | `dd6abab7` E3 supported «запущен **4 октября 1957**», external_fact/evergreen, anchor none | ru.wiki ×4 «4 октября 1957»; NASA «October 4, 1957» ✓ | temporal_fact с точным временем (19:28:34 UTC) — V14-формулировка уже по детали, но корректна; **иная классификация типа** claim'а (external_fact vs temporal_fact) — наблюдение rules-v2 на новых формулировках |
| Go | **claim'а нет**: `succeeded_partial`, budget_exhausted (§3/§4.3) | go.dev/dl скачан (raw `a533670a…`, в контенте go1.27.1 как последний стабильный ✓); ru.wiki Go полный URL скачан (`63d63ada…`) — данные были в контексте, но coverage gate закрыться не мог (§4.3) | Go **1.27.1** E3 supported (`dec02bf7`) — K2 закрыл гейт, буквально скачав обрезанный URL (404 → `errored`) |

Итог по качеству: 6/7 вопросов answers и grounded не хуже V13; единственный
проигрыш — Go — вызван не «неванием» модели (факт был в скачанном
go.dev), а механизмом coverage-гейта плюс различием поведения моделей
(§4.3) — **атрибуция фактора невозможна** без контрольного прогона.

### 2.6 fingerprint и context_window — что реально управляло бюджетом

Во всех 36 model_runs `model_fingerprint.model` = `context_window:
32768, max_output_tokens: 4096` — это **декларативные MVP-дефолты**
dataclass `ModelProfile` (`packages/llm_gateway/config.py:24–25`);
оркестратор строит профиль как `ModelProfile(model_alias=…,
backend_name="local")` без env-переопределений (`apps/orchestrator/main.py:36`),
поэтому fingerprint одинаков у EXL3, K2 и halogen (в V13 те же 32768/4096)
— метаданные реального окна модели в fingerprint **не отражают** и на
прогон не влияют.

Реальные бюджеты:

- packing контекста: `TokenBudgets.from_snapshot`
  (`packages/cognition/tokenizer.py`) берёт модель из **config-снапшота**,
  а config-v12 задаёт `context_window/backend_context_limit = 262144`,
  `max_output_tokens = 8192`, `safety_margin = 2048` →
  `input_budget = 251904` (записан в audit `context_packed` ×7 — во всех
  сессиях); сумма секций token_budgets проходу не препятствовала;
- HTTP-запрос: `max_tokens` gateway'а из env
  `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192` (`packages/llm_gateway/client.py:129`).

Наблюдение (оценка, не правка): packing-бюджет 251904 оценочных токенов
(word+symbol эвристика) **больше физического окна EXL3 (131072)** — при
длинных сессиях packing может собрать контекст, который движок обрежет
или отклонит; в этом прогоне максимальный input был 30617 оценочных токенов
(≈48 % окна), опасности не было. Обратная сторона «дефолтного
fingerprint» — риск ложной тревоги при чтении audit (числа 32768/4096 в
нём не означают лимит прогона). Открытых дефектов не создавал ни в этом,
ни в прошлых прогонах.

## 3. Статусы, complete_reason (классы A/B/C/D), честность budget_exhausted

| Сессия | state | termination_reason (raw) | normalized | класс (ADR-0022) |
|---|---|---|---|---|
| c4dcac54 … 2ac7c041 (6 сессий) | succeeded | `goal_reached` | `goal_reached` (тот же токен) | **A — 6/7** |
| 05303f68 (Go) | succeeded_partial | `budget_exhausted` | — (токен поставил хост по исчерпанию шагов, audit seq 42 «report: budget_exhausted after 10 steps») | терминал хоста, не модели |

- explorer-v5 пишет пояснение в **отдельное поле `public_rationale`**
  complete-решения, а `reason` — ровно токен; проверено по payload'ам
  (пример: seq 25 Python-anchor с развёрнутым rationale + 
  `complete_reason="goal_reached"`, `normalized_reason="goal_reached"`).
  Host-нормализация T7.49 **ни разу не понадобилась** (raw==token) —
  классы B и D в этом прогоне не встретились вообще: у explorer-v5+EXL3
  свободный текст в `reason` не наблюдался (безусловное наблюдение пары;
  отдельный вклад промпта vs модели — см. §0/§7).
- **Честность partial**: Go-сессия partial **по делу** — claim'ов ноль,
  coverage не закрыт, бюджет 10 шагов (`max_explorer_steps=10`) исчерпан
  реально. Это прямо противоположно V13, где 5/7 partial были
  формальными (работа полная, терминал — из-за free-form `reason`), и
  снимает асимметрию сравнения: в V14 partial = настоящий проигрыш одного
  вопроса, в V13 partial = артефакт формата.
- Классы C («токен не в начале») и «неполные» вариации — 0.

## 4. Гейт 5 (significant_claim_reuse) = 2/4: состав и почему только 4 claims

### 4.1 Хранёное значение против пересчёта

Хост-гейт в `evaluation_runs.gates`: numerator **2**, denominator **4**,
ci95 [0.15; 0.85], outcome `insufficient_sample` (threshold 0.25).
Пересчёт кодом репозитория (`~/dsh1/smoke-v14/analyze-gate5-v14.py`,
SELECT-only) — **MATCH: 2/4**.

### 4.2 Состав числителя и знаменателя

Знаменатель (head current, supported|disputed|refuted, grade ≥ E2): все
4 claims прогона. Числитель — claims с ≥2 различными сессиями в UNION
пяти веток гейта:

- **Python `7758de6f`** — 2 сессии (`bcdcae2c`, `b2c73bf8`): и ветка [1]
  evidence (FU добавила новую evidence-строку со свежего chocolatey), и
  ветка [5] assessments. **Глубокая перепроверка**: заново скачанный
  источник + новая оценка;
- **plan `d68694fb`** — 2 сессии (`3b4014d1`, `640ae74e`) **только по
  ветке [5]**: read вернул побайтово идентичное содержимое → evidence
  дедуплицирована (новых строк 0), но хост создал assessment в чужой
  сессии. Формальная перепроверка на той же evidence — случай, который
  ADR-0018 прямо предусматривает («записью перепроверки является оценка,
  а не evidence»);
- UN `e7e09270` и Sputnik `dd6abab7` — по 1 сессии (FU-вопросов к ним не
  было) → только знаменатель.

Механизм reuse в V14 — **host-dedup по точному совпадению
statement+claim_type** с head-в-снапшоте (`packages/memory/service.py`,
T7.9), а **не правило 7**: в FU-staging `existing_claim_id = null` ×2,
событий `claim_reverified` нет (0 против 2 в V13). Числительно гейт даёт
тот же результат (50 %), но путь другой; «улучшение механизма перепроверки
V13→V14» утверждать нельзя — изменился лишь способ, каким модель и хост
договорились о reuse.

### 4.3 Почему claims всего 4 (против 5 в V13)

Не хватило ровно Go-claim'а, и корень — дефект извлечения named-URL из
вопроса: `extract_question_urls` обрезает URL по первой `)` — в вопросе
Go это `…/wiki/Go_(язык_программирования` **без закрывающей скобки**
(defect есть и в V13; здесь он впервые стал фатальным для whole-вопроса):

- coverage-трекер требует fetched|errored по каждой named-строке; ключ —
  домен+path с процент-декодом, поэтому канонические скачивания полного
  URL **не матчатся** обрезанной строке (скобка в ключом пути);
- EXL3 трижды получал отказ complete (`source_coverage_incomplete`,
  uncovered = [обрезанный URL]), скачивая правильные варианты: полный
  https, action=raw (упал на хостовой нормализации — не «errored» по
  правилам трекера, т.к. это не egress-отказ), http-вариант; повторный
  идентичный fetch отрезал repetition-guard;
- V13-K2 закрыл тот же гейт **случайно успешно**: после первого отказа он
  сходил буквально по обрезанному битому URL → HTTP 404 → named source
  помечен `errored` → complete принят, Go-claim (1.27.1) родился;
- EXL3 битый литерал не пробовал, вместо этого повторял канонические
  скачивания → дошёл до лимита шагов → честный partial без claim'а.

Различие поведения («дословно исполнять названное» vs «скачать правильное
и объяснить») — правдоподобная гипотеза и промпта (explorer-v5), и модели,
**проверенная быть не может** (§0); для гейтов это наблюдение пары.

### 4.4 Остальные гейты (хранёные)

| Гейт | Значение | Комментарий |
|---|---|---|
| e2_provenance | 4/4 (ci .51–1) | все claims с evidence ≥ E2 |
| external_temporal_e3 | 3/3 | UN, Python, Sputnik ✓ |
| eligible sessions | 7/7 | потерь нет |
| near_duplicate_questions | 0/7 | новых model-вопросов не создавалось вовсе |
| due_stale_time_sensitive | 0/1 | «fresh» ровно один claim (Python), срок 2026-11-03 ещё не наступил; tick'и его корректно не трогали |
| reassessment_slo | 0/0 | переназначений не было — гейт на smoke-выборке неисполним |
| pending_invalid_ancestor | 0/4 | |
| high_severity_incidents | passed (0) | |
| blind provenance / scope | 4/4 и 4/4, пороги .9/.8 | структурная сверка; outcome `insufficient_sample` (n=4 < blind-size 10), ручная смысловая слепая проверка не проводилась — как в прошлых прогонах |

## 5. Пиновки, схемы, даты, blind, NUL/lease-ошибки, traceback-классификация

- **Пиновки**: prompt_sha256 ненулевой 36/36; consolidating ×7 =
  curator-v7 `19d6c6e8…` (тот же, что в V11–V13 — промпт куратора не
  менялся ✓); exploring ×29 = explorer-v5 `3b1fd49d…` (ровно хэш T7.50 ✓);
  config-снапшот прогона пинит те же хэши; rules-v2 `f96eeffc527c…`.
- **Схемы**: tool_schema_hash одинаков во всех 29 exploring-вызовах
  (`f76284735e…`) **и побайтово равен v13-шному** — схема инструментов/
  ModelResponse/Decision не менялась; профиль `none` — движок EXL3 не
  отверг ни одной полной схемы (HTTP-400 «unsupported keyword» маркеров
  нет); output_schema_valid 36/36.
- **Даты (ADR-0016/0017)**: anchor explicit → UN (as_of 2026-04-15 из
  вопроса); relative → Python (host-clock as_of 2026-10-04, единственный
  claim с reverify_after = момент последней оценки +30 сут =
  2026-11-03T02:13:18Z); none → plan, Sputnik (evergreen без срока).
  Модельный as_of в формулировке Python («на 2026-09-30») — дата релиза из
  источника; claim.as_of вывел хост ✓.
- **Lease**: LeaseLost/PendingRollback/heartbeat-отказ — 0.
- **NUL/C0 в продуктивных таблицах** — 0 (§2.2); unknown_actions 0.
- **worker.log (6262 строки)**: ровно один класс traceback'ов — известный
  teardown-дефект `hostctl/cli.py`: на каждом тике цепочка «Exception
  closing connection … RuntimeError: Task <… AsyncEngine.dispose() …
  attached to a different loop» + «RuntimeError: Event loop is closed»
  (**201 chain-блок** + 1 хвостовой; «Event loop is closed» ×404 = по две
  строки на блок). Иных исключений нет (ни LeaseLost, ни rollback, ни
  grammar/schema ошибок). Содержательная нагрузка тиков нулевая
  (`reassessment-tick: … deferred=True` ×101 — переназначать нечего;
  `reconcile-tick: nothing to do` ×101); шум маскировал пустоту, но сам
  дефект (dispose после закрытого цикла) живёт с V13 и остаётся в
  рекомендациях.

## 6. Окружение: restart-политики контейнеров и реально использованные инструменты

- `noezema-searxng`: к моменту запуска T7.52a был **Exited ~42 часа**
  (host перезагружался), restart policy = **`no`**; launch.sh поднял его
  (`docker start`), preflight проверил :8888 → HTTP 200. Во время самого
  прогона searxng **не понадобился** (search-инструмент не вызывался —
  §2.4).
- `noezema-test-db`: Up, restart policy тоже **`no`**.
- **Предложение (НЕ применено)**: `docker update --restart unless-stopped
  noezema-searxng noezema-test-db` — снимает риск «прогон упал из-за
  перезагрузки хоста» (ровно сценарий V14-launch, где searxng пришлось
  поднимать вручную). Команда не исполнялась: анализ не меняет окружение.
- Реально использованные инструменты прогона: `research.fetch` ×15
  completed + 1 failed (+1 отказ guard'а), `workspace.write` ×1,
  `workspace.read` ×2, `workspace.list` ×1; `python.execute`,
  `shell.execute`, `artifact.create`, `memory.search`, `question.create`,
  `message.reply` — ноль. Артефакты на диске: 19 файлов (raw+normalized),
  хэши совпадают с sources/artifacts в БД.
- Gateway env ровно T7.45a-профиля: BASE_URL .42, MAX_OUTPUT_TOKENS 8192,
  профиль схемы `none`; к LLM на 192.168.1.48 обращений не было;
  llama-swap и конфиги .42 не тронуты.

## 7. Сводная таблица v8→v14, вердикт, контрольные прогоны

| Прогон | Модель | Пиновки (cur/expl) | Время | Claims | Статусы succ+partial | Гейт 5 | Ключевое |
|---|---|---|---|---|---|---|---|
| V8-K2 (09-24) | K2 | v4 / v4 | 23,9 мин | 7 | 2+5 | 0/5 | baseline; partial из free-form reason |
| V10B-K2 | K2 | v6 / v4 | 17,9 | 6 | 4+3 | 1/5 | E0-якорь plan (позже закрыто v7) |
| V11-HALOGEN | halogen | v7 / v4 | 57,5 | 6 | 6+1 | 1/6 | schema-profile эпизод; медленный движок |
| V12-K2 | K2 | v7 / v4 | 17,0 | 6 | 2+4 (1 потеря) | 2/5 | NUL-дефект коммита → исправлен T7.46/47 |
| V13-K2 | K2 | v7 / **v4** | 17,4 | 5 | 2+5 | 2/5 | чистое повторение после фиксов; класс D=4, B=1 |
| **V14-EXL3** | **EXL3** | **v7 / v5** | **16,6** | **4** | **6+1** | **2/4** | Go без claim'а (coverage-bracket); класс A 6/7; speed ×2 |

**Что прогон устанавливает безусловно (пара explorer-v5 + EXL3):**

1. Завершение сессий — чистый токен `goal_reached` у всех 6 выполненных
   сессий (класс B/D не встретился ни разу; host-нормализация не
   понадобилась); partial единственная и настоящая.
2. 36/36 schema-valid ответов при профиле `none`, 0=length-truncations,
   finish stop 36/36; скорость ~2× K2-FAST; потерь нет (7/7 committed).
3. Ни одного C0/NUL/repeat-аномалии в продуктивных данных и артефактах;
   lease — 0 ошибок.
4. Host-dedup reuse (statement+type) + assessment-запись перепроверки
   работают без участия правила 7; гейт 2/4 сошлся с пересчётом.

**Что утверждать нельзя:**

1. Что чистоту токенов дал explorer-v5 **или** EXL3 — две переменные
   менялись вместе (§0); правдоподобно, что промпт (его diff ровно про
   завершение), но это гипотеза.
2. Что гейт 5 «улучшился»/«ухудшился»: знаменатели разные (4 vs 5), а
   расхождение целиком объясняется потерей Go-claim'а из-за дефекта
   обрезки `)` в `extract_question_urls` + различия поведения моделей.
3. Что EXL3 «качественнее/слабее» K2 по ответам: на этой temperature-выборке
   N=7 ответы совпадают везде, где вопрос дошёл до коммита; единственный
   проигрыш — механизм, не знание (данные Go были в контексте).
4. Что NUL-фиксы подтверждены «в бою» (python.execute не было) или что
   дефект coverage-extractor исправлен (не тронут — только задокументирован
   здесь как фатального для Go-вопроса).

**Рекомендация (контрольные прогоны, по одному на гипотезу):**

1. **Разнести факторы**: EXL3 + config-v11 (explorer-v4) либо K2 +
   config-v12 — чистый A/B одного фактора; это единственный способ
   измерить «вклад промпта» и «вклад модели».
2. **Отдельная маленькая задача (код)**: парсер `extract_question_urls`
   должен извлекать URL с balanced-parenthesis tail — сейчас любой
   wiki-URL вида `…_(…)` делает свой question недоказуемым; после фикса
   Go-пара вернётся в основную линию. Анализ этот код не менял.
3. **Teardown-шум** `hostctl/cli.py` (dispose вне цикла) — чинить в
   отдельной правке: 201 traceback-блок за 16-минутный прогон.
4. docker restart-policy (команда §6) — применить решению оператора.

## 8. Проверки, коммит, не тронуто

Перед коммитом — полная проверка §6 на этом же дереве: `ruff check .`
(чисто), `mypy packages apps hostctl` strict (129 файлов, чисто), образ
`noezema-sandbox:test` присутствует, `pytest -n auto -q` c
`NOEZEMA_TEST_DATABASE_URL` — **1048 passed, 12 skipped in 279.70 s**
(базовая линия T7.51 не изменилась).

Коммит — docs-only (3 файла: новый отчёт, `docs/STATUS.md`, пометка-указатель
в `SMOKE-V13-K2-report.md`), без push; sha этого коммита — в `git log` /
STATUS T7.52 (самоссылки в файле нет — конвенция прошлых отчётов).

Не тронуто (инварианты): код, тесты, payload'ы (v2…v12), промпты/пины,
корпуса, ARCHITECTURE.md, схемы БД (миграций нет), пороги; БД
`noezema-smoke-v14-exl3` и все прошлые смоук/eval — **только SELECT**;
прогоны не запускались; к 192.168.1.48 не обращались; llama-swap/серверы
.env не тронуты; docker-политики не менялись (команда только предложена);
фоновых процессов не осталось (воркер остановлен, юниты inactive, job'ы сессии закрыты).
