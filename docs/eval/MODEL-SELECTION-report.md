# MODEL-SELECTION (T7.69b): разбор серии MODELSEL T7.69a — подбор модели NOEZEMA

Дата разбора: 2026-10-06. Задача: **только анализ и документы**. Ни кода, ни тестов, ни payload'ов,
ни промптов, ни deploy в этом разборе не менялось; новых сессий, eval-run'ов и обращений к моделям
(`.42`, `.48`) и к стенду `.92` не было — все числа ниже получены SELECT'ами по БД серии
и чтением логов/артефактов, которые оставила серия T7.69a.

Серия (T7.69a) и её продолжение (T7.69c) были операционными: коммитов не делали, материалы лежат
в `/home/denis/dsh1/modelsel` (только для чтения): `SERIES.log`, `driver.log`, `DONE`,
`<slug>/{summary.json,preflight.log,preflight_probe.json,logs/run.log,logs/worker.log,data/…}`.

## 0. Что измерялось и на каких условиях

| Параметр | Значение серии |
|---|---|
| код | `22ea68d` (`impl/from-scratch`), дерево чистое — одно и то же для всех прогонов (`driver.log`) |
| payload правил | config-v13: файл `fe931c15a34fe71e…`, canonical `0260fcd2f79035e6…` (в БД — canonical) |
| корпус | `~/dsh1/smoke-v8/question-set-smoke.jsonl`, sha256 `b3e05ad5d206…`, 7 вопросов, seed 20260924, blind-size 10, slo 3600 |
| исполнитель инструментов | **stub** (`NOEZEMA_TOOL_EXECUTOR` не задан — `run.sh` его снимает явно), как в SMOKE-V13/V14/V14B |
| движок | `http://192.168.1.48:8080/v1` (llama-swap на `.48`), смена модели между прогонами |
| профиль схемы | подбирался предстартом: `none → llamacpp-rocmfpx → halogen`, первый профиль с HTTP 200 на все 5 response-схем |
| research_proxy | режим `curated`, `allowed_domains = []`, searxng_url `http://127.0.0.1:8888` (контейнер `noezema-searxng` был запущен, **не использовался**: инструмента поиска у модели нет), лимит 20/3600 считается только по upstream-поиску |
| БД прогона | свежая `noezema-smoke-msel-<slug>` на каждый прогон |

**Ограничение выбора профиля — не свойство харнесса:** у K2 профиль `none` дал 5×HTTP 400
(«Failed to initialize samplers: failed to parse grammar»); после stripping `maxLength`/`minLength`
(`llamacpp-rocmfpx`) — 5/5 HTTP 200 (`k2/preflight_probe.json`). Это единственный кандидат, которому
профиль нужен; остальные пять приняли `none`.

**Главная оговорка:** N=7 сессий × один прогон на модель. Это направление, а не статистика; ни один
вывод ниже нельзя читать как измеренное преимущество модели. Гейты §22.2 во всех прогонах дали
`insufficient_sample` (единственный `passed` — `high_severity_incidents = 0`).

## 1. Сводка по моделям

| slug / модель | профиль | окно из `GET /running` | cold load, с | elapsed прогона, с | сессии | termination_reason | commit_attempts | claims | ответы верны (из 7) | deny/failed действий | model_runs (expl/cons), макс output | `finish_reason=length` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `k2` · k2-horizon-mova-36b-a4b-rocmfp4-fast | `llamacpp-rocmfpx` | 262144 | 0.35 | 943 (wall сессий 919) | 7/7 succeeded | `goal_reached` ×7 | committed ×7 | 6: temporal_fact ×4 **E3**, local_observation ×2 **E2**; все head `current`, `supported` | **7/7** | deny 3, failed всего 4 из 23 | 30 / 7 · max 1246 (expl), 4153 (cons) | 0 |
| `q36-a3b-q4` · qwen36-35b-a3b-q4-mtp | `none` | 131072 | 9.23 | 1329 (1318) | 7/7 succeeded | `goal_reached` ×7 | committed ×7 | 5: E3 ×4, E2 ×1; все supported | **7/7** | deny 0, failed 0 из 15 | 22 / 7 · max 7195 (expl), 3348 (cons) | 0 |
| `q36-a3b-q6` · qwen36-35b-a3b-q6-mtp | `none` | 524288 | 12.96 | 1448 (1444) | 7/7 succeeded | `goal_reached` ×7 | committed ×7 | 6: E3 ×5, E2 ×1; все supported (Python-claim **дублирован**) | **7/7** | deny 1, failed 2 из 20 | 27 / 7 · max 7696 (expl), 4151 (cons) | 0 |
| `q38-27b-q5xl` · qwen38-27b-q5xl | `none` | 262144 | 9.39 | **4144** (4134) | 7/7 succeeded | `goal_reached` ×7 | committed ×7 | 7: E3 ×4 supported, E2 ×1 supported, computed_result ×2 **hypothesis E1** | **7/7** (Q4 частично) | deny 0, failed 1 из 21 (`shell.execute`) | 28 / 7 · max 4416 (expl), 4719 (cons) | 0 |
| `q38fn-iq4xs` · qwen38-flash-next-iq4xs (прогон 1) | `none` | 196608 | 26.85 | 1755, **partial** | 2/7 commits | `goal_reached` ×2 | committed ×2 | 2: E3 ООН, E2 plan (supported) | проверяемы только 2 из 7 | deny 0, failed 0 из 4 | 6 / 2 · max 1706 | 0 |
| `q38fn-iq4xs-r2` · qwen38-flash-next-iq4xs (прогон 2) | `none` | 196608 | 30.03 | 2834 (2829) | 7/7 succeeded | `goal_reached` ×7 | committed ×7 | 7: E3 ×3 supported, E2 ×2 supported (**дублированный** план), **disputed E1** Python, **hypothesis E1** | 7/7 по значениям; формулировки хуже | deny 4, failed 4 из 19 | 26 / 7 · max 4440 (expl), 4820 (cons) | 0 |

Прогоны `q36-*`, `q38-27b-q5xl`, `q38fn-iq4xs-r2` шли уже после закрепления DPM high на `.48`
(`dpm_level_note` в `summary.json`: служба `amdgpu-dpm-high`, FCLK 2000 МГц — справка менеджера,
с `.87` значение не измерялось). Прогон `k2` и аварийный прогон flash-next шли **до** закрепления.
Скоростные сравнения `k2` с остальными поэтому неравноусловны.

Пропущенные кандидаты: `gufo-27b` (gufo-qwen38-27b-q4-dflash2) — схема не принята ни одним
профилем: 5×HTTP 400 «strict schemas require every property» во всех трёх профилях, в том числе
`halogen` (`gufo-27b/preflight.log`, `preflight_probe.json`; отказ отличается от halogen-«keyword»
проблемы из AGENTS §7). Движок **halogen-flash-next**, а также `deepseek-v4-flash-iq3xxs`, `ornith`
и `ling` в серию не входили: они вне `CANDIDATES` драйвера (строка 40 `driver.sh`: «обращений к
моделям вне списка … нет»), а вотчдог серии считает их появление в `GET /running` событием
`foreign_model` с остановом всей серии. Никаких замеров по ним в этой серии нет.

## 2. Корректность ответов: матрица по 7 вопросам корпуса

Эталон построен **по артефактам, которые реально скачала сама серия** (файлы
`modelsel/<slug>/data/artifacts/<sha>` + таблица `sources`), а не по памяти: у артефактов разные
sha между прогами (страницы `.org` менялись), поэтому эталон = «что харнесс видел в момент прогона».

| # | Вопрос | Эталон по корпусу (где именно) | k2 | q36-a3b-q4 | q36-a3b-q6 | q38-27b-q5xl | flash-next r1 | flash-next r2 |
|---|---|---|---|---|---|---|---|---|
| Q1 (100) | государств-членов ООН на 15.04.2026 | `un.org/en/about-us` → «193 Member States»; ru.wiki «Список государств — членов ООН» | 193 ✓ | 193 ✓ | 193 ✓ | 193 ✓ | 193 ✓ | 193 ✓ |
| Q2 (90) | последняя стабильная Python | `python.org/downloads/` → `Python 3.14.8` (стабильные), `…/release/python-3148/` → «Release date: Sept. 30, 2026»; chocolatey `python314` → `3.14.8` | ✓ (+ дата релиза 30.09 grounding) | ✓ | ✓ | ✓ | сессия не выполнена | ✓ по значению; в формулировке «на **2026-06-15**» при `as_of=2026-10-06` |
| Q3 (80) | сверить Python с ранее зафиксированным | прежний claim + те же источники | ✓ переиспользован тот же claim | ✓ переиспользован | ✓ по значению, но создан **дублирующий** claim вместо переиспользования | ✓ переиспользован | — | ✗ bookkeeping: прежний E3-claim понижен до **disputed E1** (новая evidence помечена `counters`) + отдельный hypothesis E1 |
| Q4 (90) | `notes/plan.md`, три пункта (дата, повестка, ответственные) + зафиксировать число | файл на диске (`data/workspace/notes/plan.md`) | ✓ 3 строки, в каждой все три поля (в первой строке слиплись слова: «ОбсуждениеProgressReports») | ✓ 3 пронумерованных пункта со всеми полями | △ 3 строки, но это **один** пункт, разрезанный на дата/повестка/ответственные («2024-06-10 / Q2 Planning Session / Management Team»); claim «ровно 3 пункта (строки)» считает строки | △ то же: строки = поля одного пункта («Дата: …», «Повестка: …», «Ответственные: …») | ✓ 3 строки со всеми полями, но все три даты одинаковые (2026-06-16) | ✓ 3 строки (tab-разделители), разные даты, все три поля |
| Q5 (80) | прочитать `plan.md` прошлой сессии, подтвердить число | файл + прежний claim | ✓ подтверждено, тот же claim | ✓ подтверждено, тот же claim | ✓ подтверждено против прежнего claim | ✓ подтверждено против прежнего claim | — | △ подтверждено, но записан **новый** claim вместо переиспользования |
| Q6 (0) | последняя стабильная Go | `go.dev/dl/` → Stable: `go1.27.0`, `go1.27.1`; ru.wiki Go: infobox `1.27.1` («1 сентября 2026») | ✓ 1.27.1 (источник назван точно) | ✓ 1.27.1 | ✓ 1.27.1 | ✓ 1.27.1 | сессия не выполнена | ✓ 1.27.1, но «на 2026-06-15» в формулировке |
| Q7 (0) | дата запуска «Спутник-1» | ru.wiki «Спутник_1» → «4 октября 1957 года в 19:28:34 по Гринвичу»; `nasa.gov/history/dawn-of-the-space-age` → «October 4, 1957» (без времени) | ✓ дата + 19:28:34 UTC (это есть только в Википедии — один источник на эту деталь) | ✓ дата верна, **но `as_of=2024-05-20`** (внесource дата) | ✓ дата + время, `as_of=1957-10-04` корректно | ✓ дата; time не заявлен | сессия не выполнена | ✓ дата |

Нельзя проверить без сети (и не проверялось):
- какие значения являются «последними стабильными» на **настоящий** момент вне скачанных артефактов;
- расхождение Go 1.27.1 (эта серия) против 1.25.4 (SMOKE-V14B) — дрейф источника между прогонами или
  иная выборка страницы: без сети не разрешается;
- содержимое снапшота правил на живом стенде `.92` (в json есть только `config_snapshot_id
  7bf94512…`; косвенно совпадает с config-v13 по `input_budget = 120832`, но проверять — это обращение к `.92`).

Выдуманных чисел без evidence в серии не найдено: во всех claim-утверждениях значения повторяются в
тексте скачанных страниц; единственные внесource детали — `as_of` у Q4-модели (Спутник, 2024-05-20) и
датированные якоря «на 2026-06-15» у flash-next r2. Сканы БД (189 текстовых колонок) на NUL/C0 и
длинные повторы: находок нет во всех шести БД; скан артефактов: файлов с NUL — 0, с VT — 0,
«long repeat» 3–5 файла на прогон (повторяющаяся разметка HTML-страниц, не модельный дефект).

## 3. Отказы инструментов и схем (exact reasons из `audit_events`)

Предлагается модели инструменты **списком снапшота**, а не реестром: `base_tools =
sorted(cap_profile.tools)` (`apps/orchestrator/orchestrator.py:1395`), плюс шапка «Доступные
инструменты» (`:1294`). Проверка по факту: пин всех прогонов серии (и V13/V14B) —
`tool_schema_hash = f76284735e255086`, что ровно `canonical_sha256` от списка **config-v13 минус
`message.reply`** (пустой inbox, T7.13): 9 имён, **включая `artifact.create`**. Реестр же
(`packages/policy/tools.py::all_tools()`) содержит 9 инструментов и `artifact.create` в нём
отсутствует ⇒ любой вызов `artifact.create` заранее обречён: `get_tool()` → None → deny
«unknown tool: artifact.create» (`packages/policy/engine.py:115–118`). Это известный конфликт
снапшота и реестра (AGENTS §7, примечание T7.64 про подпись в `apps/web/labels.py`), а не выдумка модели.

| модель | deny (политика) | failed (исполнитель) | что это значит |
|---|---|---|---|
| k2 | `artifact.create` ×1 («unknown tool»); `research.fetch` ×1 («argument ('language',): Extra inputs are not permitted»); `question.create` ×1 («Field required 'text'» + extra `'question'`, `'assertion_text'`, `'search_statements'`, `'dependencies'`) | `workspace.read` ×1 (`not found`) | аргументы tools задают pydantic-модели реестра с `extra="forbid"`; модель переносила поля curator-конверта в вызов инструмента |
| q36-a3b-q4 | — | — | единственный прогон без единого отказа действия |
| q36-a3b-q6 | `artifact.create` ×1 | `workspace.write` ×1: «path escapes workspace: '/workspace/claim_latest_python_version.txt'» (повтор тем же инструментом с относительным путём — успех) | единственная модель, которая писала отдельный файл-результат; путь исправлен сама |
| q38-27b-q5xl | — | `shell.execute` ×1: «tool_not_supported … dev/eval stub; use python.execute or workspace.*» → сразу заменён `python.execute` (exit 0) | ожидаемое поведение stub'а (AGENTS §7) |
| flash-next r1 | — | — | лишь 4 действия до обрыва |
| flash-next r2 | `artifact.create` ×3; `memory.search` ×1 («argument ('top_k',): Extra inputs are not permitted») | — | попытка обращения к памяти единственная за серию и отклонена схемой ⇒ переиспользования не случилось (`significant_claim_reuse 0/5`) |

**Память между сессиями.** `memory.search` как инструмент не вызывался нигде, кроме q38-27b-q5xl
(×2: один вернул прежний claim «…Python — 3.14.8 (supported, E3, p=0.75)», второй пустой) и одной
отклонённой попытки flash-next r2. Переиспользование claim'ов шло через сопоставление зависимостей
при extraction: `significant_claim_reuse` = k2 2/6, q4 2/5, q6 1/6, 27b 2/5, r2 0/5 (все —
`insufficient_sample`). Дублирование вместо переиспользования: q6 (Python) и r2 (план).

**Признаки петелек:** повторы одинаковых вызовов внутри одной сессии — максимум ×2
(`research.fetch` одного URL, `workspace.read` plan.md), ни одного deny по repeat-гейту
(`TOOL_REPEAT_DENY_LIMIT = 2`, отказов «repeat» в аудит-событиях — 0 во всех БД). Межсессонные
одинаковые fetch'и (un.org/python.org/go.dev) — общее обращение к корпусу, не петля.

**model_runs.** Обрывов генерации нет ни у одной модели: `finish_reason` = `stop` во всех 176 записях
серии по шести БД (`length` — 0), вызовов с `output_tokens ≥ 8000` (потолок `max_output_tokens=8192`) — 0,
невалидной схемы ответа — 0. Максимум output по фазе: exploring 761–7696 (k2 1246, q4-mtp 7195,
q6-mtp 7696, 27b 4416, flash-next r1 761 / r2 4440), consolidating 1706–4820. Максимальный input
вызова в серии — 29267 токена (flash-next-r2) при бюджете 120832. Пины
промптов одинаковы во всех прогонах: curator-v7 `19d6c6e8…` (по 7) и explorer-v5 `3b1fd49d…`.

Скорость генерации (токены output / сумма latency по фазе, только как порядок величин):

| модель | exploring tok/s | consolidating tok/s | холодная загрузка |
|---|---|---|---|
| k2 | ≈ 23 | ≈ 32 | 0.35 с (модель уже была в RAM) |
| q36-a3b-q4-mtp | ≈ 38 | ≈ 73 | 9.23 с |
| q36-a3b-q6-mtp | ≈ 60 | ≈ 72 | 12.96 с |
| q38-27b-q5xl | ≈ 14 | ≈ 17 | 9.39 с |
| flash-next (r2) | ≈ 19 | ≈ 25 | 30.03 с |

Суммарное же wall-clock время прогона у K2 наименьшее (919 с против 1318/1444/4134/2829), потому что
K2 тратит заметно меньше токенов на шаг (exploring avg 464 против 1487–2484).

## 4. Живой случай на стенде `.92` (K2): `artifact.create` и «Extra inputs»

Файлы: `/home/denis/dsh1/stand-k2-f39258bc/{stand-k2-session.json,stand-k2-answer.json}` — сессия
`f39258bc…`, вопрос «Чему равна сумма квадратов натуральных чисел от 1 до 50? Проверь вычислением в
sandbox», итог `succeeded`, карточка ответа: **42925**, уровень надёжности «verified», замечание
«часть шагов не удалась: 2». Лента: seq 9–11 — `python.execute` с `sum(i*i …)` и сверкой по формуле
`n(n+1)(2n+1)//6` → stdout `42925 42925 True` (exit 0); seq 12–13 — `artifact.create` → deny «unknown
tool»; seq 14–15 — `question.create` → deny «argument ('dependencies',): Extra inputs are not
permitted» + «argument ('search_statements',): Extra inputs are not permitted»; seq 16 —
`complete_reason = goal_reached`.

Разбор по механизму:
1. **Такого инструмента нет в реестре.** `all_tools()` даёт ровно девять: workspace.read/list/write,
   python.execute, shell.execute, memory.search, question.create, research.fetch, message.reply.
   При этом `artifact.create` **есть** в возможностях config-v13 (`policy.capabilities.tools`) и в
   YAML-потолке `sandbox/policy/curated.yaml`, а offered-список строится из снапшота → модель его
   видит (пин `f76284735e255086` это подтверждает). Отказ даёт реестровый lookup, до профиля.
   То есть это **конфигурационный пробел харнесса**, воспроизводимый на любой модели этого payload'а;
   корректный путь для такой задачи — `workspace.write`.
2. **Схема аргументов `question.create`** = `QuestionCreateArgs(text: 1..2000, origin: …)` c
   `extra="forbid"` (`packages/policy/tools.py`). Поля `dependencies`, `search_statements`,
   `assertion_text` принадлежат **выходной схеме curator'а** (consolidating-этап,
   `prompts/curator/curator-v7.md`), а не инструменту: K2 перенёс лексику curator-конверта в вызов
   инструмента. Отказ корректен и fail-closed; он же объясняет «Extra inputs are not permitted» для
   `research.fetch ({'language'})` в прогоне серии.
3. **Характерно ли это K2?** Да, но не уникально: в серии `noezema-smoke-msel-k2` — ровно те же два
   класса отказов (`artifact.create` «unknown tool», `research.fetch ('language')` extra inputs,
   `question.create` с curator-полями и отсутствующим `text`); в SMOKE-V13 (K2) по БД —
   `artifact.create` ×**2** + deny «path '/' outside profile read roots» ×1 + два 404 от cbr.ru
   (в отчёте V13 указано «deny 3»; по факту аудит-событий их 5 — отчёт занижал); у q36-a3b-q6 и
   flash-next-r2 `artifact.create` тоже отвергается (×1 и ×3). Отличие K2 в том, что он при этом
   **доводит все сессии до `goal_reached`** и не портит знание: ни один claim из-за этих отказов не
   пострадал. В случае `.92` отказ не повлиял на ответ: вычисление выполнено, значение подтверждено
   дважды (прямое и формулой), карточка «verified» опирается на `local_observation`/computed evidence.
4. **Что стоит исправить (по задачам, а не здесь):** либо убрать `artifact.create` из возможностей
   config-версии (новая payload — никогда не переписывая прежние), либо ввести его в реестр; и то и
   другое меняет `tool_schema_hash` прогонов, поэтому требует новой config/промпт-пинов — см. план
   T7.68/T7.71 и ADR-0027 «Следствия».

## 5. Сравнение с базовыми прогонами

| Признак | SMOKE-V13 (K2, `4d5549c`, config-v11) | SMOKE-V14 (EXL3 + explorer-v5, `3b7c577`, config-v12) | SMOKE-V14B (EXL3 + explorer-v4, продукт `3b7c577`, код `fde7640`, config-v11) | Серия MODELSEL (`22ea68d`, config-v13, `.48`) |
|---|---|---|---|---|
| движок/модель | `.48` K2 (profile `llamacpp-rocmfpx`) | `.42:8080` qwen38-exl3-3bpw-128k (`none`) | `.42:8080` EXL3 (`none`) | `.48`, шесть моделей |
| исходы | 2 succeeded + 5 **succeeded_partial** (termination_reason — свободный текст модели; дефект терминала §6, устранён explorer-v5) | 6 succeeded + 1 succeeded_partial (`budget_exhausted`, Go) | **7 succeeded, `goal_reached` ×7** | все записанные сессии всех прогонов: **30 из 30 `goal_reached`** (в прерванном прогоне flash-next записаны только 2 сессии из 7) |
| claims | 5 current: temporal_fact ×4 **E3** supported + local_observation ×1 E2 (7 assessments, 2 superseded) | 4 current: temporal_fact ×2 E3 + external_fact ×1 E3 + local_observation ×1 E2 | 6 current: temporal_fact ×4 E3 + local_observation ×2 E2 (1 superseded assessment) | см. §1–2 (E3 доминируют; спорный claim только у flash-next r2) |
| отказы действий | deny/failed 5 (artifact ×2, path-deny, 404 ×2) | research.fetch-404 | `shell.execute → unreachable` + Go-404 (`unreachable` — прежняя подпись stub'а) | artifact.create-deny у трёх моделей; `shell.execute → tool_not_supported:` (T7.57(b)) у 27b |
| truncation | 0 `length` (max output 3646) | 0 (max 4481) | 0 (max 4691) | 0 во всех шести БД |
| время | 17.4 мин (по отчёту SMOKE-V13) | 16.6 мин (по отчёту SMOKE-V14) | 18 мин 21 с, сумма сессий 690 с (по отчёту SMOKE-V14B) | k2 15.7 мин; q4 22.2; q6 24.1; 27b 69.1; flash-next r2 47.2 (elapsed прогонов серии) |
| переиспользование знания | `significant_claim_reuse` 2/5 (остальные гейты — числители 5/5 и 4/4 при `insufficient_sample`) | 2/4 | **1/6** | k2 2/6 · q4 2/5 · q6 1/6 · 27b 2/5 · flash-next r2 0/5 |
| пин explorer-промпта | explorer-v4 `5829a55c…` (curator-v7 `19d6c6e8…`) | explorer-v5 `3b1fd49d…` | explorer-v4 `5829a55c…` | explorer-v5 `3b1fd49d…` у всех шести прогонов серии |

Отличия условий, которые нельзя игнорировать при сравнении: (а) код (`4d5549c` / `3b7c577` против
`22ea68d` — между ними T7.64–T7.66); (б) payload config-v11/v12 против config-v13: в v11/v12
`context_window = backend_context_limit = 262144` ⇒ `input_budget = 251904` (подтверждено аудит-событием
seq 7 в обеих БД), в config-v13 — 131072 ⇒ **120832** (тоже подтверждено seq 7); то есть базовые прогоны
имели вдвое больный бюджет контекста, чем серия (при observed max input 29267 в серии это не было
ограничивающим); `max_output_tokens = 8192` и `safety_margin = 2048` — одинаковы везде; (в) движок `.42`
против `.48` и разное окно моделей (реальные окна: K2 262144, q4-mtp 131072, q6-mtp 524288, 27b 262144,
flash-next 196608 — при одном и том же бюджете харнесса); (г) исполнитель **stub во всех перечисленных
прогонах** (стенд `.92` работает с `sandbox`: случай в §4 прошёл в контейнере, а `research.fetch`/
`memory.search` остаются host-side — ADR-0023); (д) на `.87` поднят SearXNG (`noezema-searxng`,
127.0.0.1:8888), но ни один прогон его не вызывал — инструмента поиска нет; (е) режим прокси
`curated + allowed_domains=[]` = фактический открытый fetch (см. ADR-0027 §3 и решение пользователя ниже);
(ж) пин explorer-промпта в базовых V13/V14B — v4, в V14 и во всей серии — v5; `tool_schema_hash`
одинаковый (`f76284735e255086`) во всех перечисленных прогонах.

## 6. Устойчивость `.48` — только записи серии

Из `SERIES.log` и `driver.log`, без собственных аппаратных выводов:
- `q38fn-iq4xs` (прогон 1): статус `crashed_host48_reboot_x2`, терминальных сессий 2/7, причина в
  записи серии — «data fabric sync flood x2: 08:34:25Z и 08:45:04Z; остановлено менеджером ~09:02Z»;
  драйвер тогда вотчдога не имел, серия была остановлена вручную. Первая сессия этого прогона шла
  1411 с (3 шага) — единственный случай аномальной длительности в серии.
- После этого в драйвер добавлен вотчдог (`WD_POLL=15`, `WD_DOWN_AFTER=45`, подтверждений 2;
  классы `host48_down` / `model_gone` / `foreign_model`) и `RUN_TIMEOUT=6000`. Все дальнейшие прогоны
  прошли без срабатываний; перед каждой моделью и после неё — проверка `/health` и `GET /running`
  (`driver.log`).
- С 12:43 MSK на `.48` закреплён DPM high (служба `amdgpu-dpm-high`, FCLK 2000 МГц) — справка
  менеджера; с `.87` значение не читается, поэтому в отчёте это условие, а не измерение. Прогоны
  `k2` и `q38fn-iq4xs` идут **до** закрепления, остальные — после.
- Холодная загрузка: K2 0.35 с; q4/q6/27b 9.2–13.0 с; flash-next 26.9 с (первый прогон) и 30.0 с
  (второй). Это единственная пара чисел, объясняющая риск ожидания на старте для Flash-Next класса.

## 7. Рекомендация (и обязательная оговорка)

**Оговорка:** N=1 прогон по 7 сессий на модель. Ниже — выбор направления по заданному порядку
критериев «корректность > честность > отсутствие отказов схем > скорость», а не измеренный вывод;
окончательное решение за оператором после повторных прогонов.

**Основная модель для стенда `.92`: `k2-horizon-mova-36b-a4b-rocmfp4-fast` (профиль `llamacpp-rocmfpx`).**
- корректность: 7/7 ответов соответствуют корпусу; fulfillment вопроса Q4 — единственный полный наряду
  с q4-mtp и flash-next-r2 (в трёх полях на пункт);
- честность: ни одного внесource якоря или выдуманного числа; при недостатке подтверждения модель сама
  метила это в derived-question (например «точное время запуска приведено только в Википедии»), а
  knowledge-итог остался `supported/E3` с корректным числом независимых источников;
- отказы схем: 3 deny за прогон (`artifact.create`, лишние аргументы) — это самое слабое место K2 и
  ровно то, что видно на живом стенде; ни один из них не сломал сессию и не испортил знание;
- скорость: кратчайший прогон серии (919 с wall) при скромной скорости генерации.

**Запасная модель: `qwen36-35b-a3b-q4-mtp` (профиль `none`).** Единственный прогон **без единого
отказа действия**, 7/7 `goal_reached`, claims все supported; минус — выдуманный `as_of` (2024-05-20)
у вечного факта и отсутствие MTP-запаса: её окно ровно 131072, то есть равно нынешнему бюджету
config-v13. Если приоритет «отсутствие отказов схем» поднимается выше честности формулировок —
менять выбор на неё.

**Не рекомендовать к стенду сейчас:** `qwen38-flash-next-iq4xs` — класс модели, который в этой серии
дважды ронял `.48` (прогон 1) и который во втором прогоне показал наихудшее обращение со знанием:
3× «unknown tool», отказ `memory.search` по схеме, нулевое переиспользование, внесource якорь
«на 2026-06-15» в двух claims и понижение previously-supported E3-claim до disputed из-за
собственной mis-classified `counters` evidence. Риск назвать прямо: если на стенд ставится Flash-Next
класс, то риск недоступности узла из-за перезагрузок движка уже подтверждён записями серии, а
холодная загрузка 27–30 с добавляется к каждой паузе. `qwen38-27b-q5xl` честен (единственный реально
воспользовался `memory.search`, честно отметил computed_result как hypothesis) и безопасен по схемам,
но 69 минут на смоук — слишком медленно для стенда; `qwen36-35b-a3b-q6-mtp` самый быстрый по
токенам, но дублирует claims вместо переиспользования и частично выполняет Q4.

**Точные значения env стенда** (файл `/etc/noezema/dev.env`, генерируемый `deploy/dev-stand/bootstrap.sh`;
менять может только менеджер — этот разбор код не меняет):

```
NOEZEMA_LLM_BASE_URL=http://192.168.1.48:8080/v1
NOEZEMA_LLM_MODEL=k2-horizon-mova-36b-a4b-rocmfp4-fast
NOEZEMA_LLM_SCHEMA_PROFILE=llamacpp-rocmfpx
NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192
NOEZEMA_LLM_TIMEOUT_SECONDS=600
```

(переопределения bootstrap'а: `NOEZEMA_DEV_LLM_BASE_URL`, `NOEZEMA_DEV_LLM_MODEL`,
`NOEZEMA_DEV_LLM_SCHEMA_PROFILE`; дефолты пакета сейчас указывают на `.42` EXL3 и профиль `none`).
Для запасной модели — `NOEZEMA_LLM_MODEL=qwen36-35b-a3b-q4-mtp` и
`NOEZEMA_LLM_SCHEMA_PROFILE=none`.

**Нужен ли config-v14.** Нет, не как следствие выбора модели: реальное окно K2 (262144) **больше**
нынешнего `context_window = backend_context_limit = 131072` config-v13, то есть config-v13 работает
консервативно и бюджет `input_budget = min(131072, 131072) − 8192 − 2048 = 120832` соблюдается;
наибольший observed input в серии — 29203 токена. config-v14 нужен **по другой причине и уже
запланирован**: введение `research.search` (T7.71) меняет offered-список, а значит
`tool_schema_hash`, словари подписей и пины промптов; тогда же (и только тогда) имеет смысл
поднимать окно под модель с окном 524288, если выбор изменится на q6-mtp. Отдельно: если будет
принято решение чинить `artifact.create` через payload (новая config-версия вместо правки прежней),
это тоже повод для config-v14, а не для редактирования `config-v13-payload.json`. Ни один payload в
этом разборе не создавался и не менялся.

## 8. Открытые вопросы (не решались: потребовали бы запуска или сети)

1. Повторные прогоны тех же шести моделей при закреплённом DPM high — без них нельзя утверждать, что
   K2 быстрее q4/q6 системно, а не в этом одном прогоне.
2. Go 1.27.1 против 1.25.4 (V14B): дрейф источника или иная выборка страницы — проверка требует сети.
3. Живой стенд `.92`: какой именно снапшот там активен (`7bf94512…`) и в каком режиме research_proxy
   он работает — обращение к `.92` запрещено рамками задачи.
4. `artifact.create`: убирать из payload или вводить в реестр (T7.71-ряд) — требует решения оператора
   и новой config-версии.
5. Поиск (`research.search`, T7.71) теперь решён «да» (см. ADR-0027, решения пользователя
   2026-10-06), но конкретная семантика раскрытия запроса внешним движкам и лимиты прямых fetch
   (T7.72) — всё ещё открыты.
