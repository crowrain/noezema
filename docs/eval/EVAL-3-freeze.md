# EVAL-3 — замороженная конфигурация (готово, НЕ запущено)

Статус: **подготовка завершена; первый запуск (2026-09-16) сорвался — дефект конфига, пойманный fail-fast до первого вызова модели; пере-заморожено (§2.6); перезапуск — только после явного решения пользователя.**
Все пороги §22.2 неизменны; исключение типов через ADR не делается (решение от 16.09.2026).

## 1. Что изменилось относительно EVAL-1 / EVAL-2

| Компонент | EVAL-1/2 | EVAL-3 |
|---|---|---|
| Access profile | sealed (нет egress) | **curated**: `research.fetch` через research proxy (SearXNG 127.0.0.1:8888 + прямой fetch публичных URL) |
| Корпус | v1: 50 базовых вопросов, без URL | **v2**: 50 вопросов — 24 URL-факта (2 пары источников на факт, 2 разных registrable domain), 11 паков (3×), 12 workspace-вопросов |
| Активация | — | **mid-run `activate-online` v2→v3** (flip volatility `configurable`→`temporal`): запускает re-evaluation всех external/temporal head-ов → 3 old-as_of claim становятся `due`, остальные `fresh` (live-проверено ранее на отдельной БД) |
| `phase_deadline_seconds` | 300 (LeaseLost 7/50 = 14%) | **1800** (решение пользователя: цель ≤2–3%) |
| Приоритет в корпусе | не поддерживался | **`priority` в JSONL** (новый фича seed'а `eval-run`): детерминированный FIFO-порядок — без неё порядок случаен (seed = одна транзакция ⇒ у всех строк `created_at = now()` = старт транзакции, тайбрейк по случайному uuid; ловушка из AGENTS.md §7) |
| Wiring research | `build_orchestrator` без `research_service` → `research.fetch` fail-closed «not configured» на любом хост-входе | **ResearchProxyService подключён** в `build_orchestrator` (wake-tick + eval-run) |

## 2. Замороженные артефакты

### 2.1 Конфигурации (`docs/eval/`)

- **v2** `config-v2-payload.json`, canonical sha256 `ffc98c9e54b5ba0b3621d5f3fb37616fabbb6b159a4a9d5d46710adee75a6e2d` (пере-заморожен 2026-09-16, §2.6)
  - `access_profile: curated`, tools: +`research.fetch`;
  - `research_proxy`: mode curated, searxng `http://127.0.0.1:8888`, private_allowlist `["127.0.0.1:8888"]`, max_response 4 MiB, timeout 10 c, rate 20/3600 c;
  - `model`: context 40960, `backend_context_limit` 262144 (фактический предел слота бэкенда, §2.6), max_output 8192, thinker-local; input_budget = min(40960, 262144) − 8192 − 2048 = **30720** ≥ Σ секций 26624 (запас 4096);
  - `session_limits`: phase_deadline 1800, session_timeout 1800, max_explorer_steps 10;
  - `curiosity.selector: fifo`; repetition enabled (no_progress 2, rephrase 0.6);
  - `claim_type_rules`: external_fact/temporal_fact — allowed `source_assertion|quote_integrity`, min_grade_for_supported E3, min_independence_groups 2, min_support_evidence 2, requires_scope (+`requires_as_of` для temporal), volatility `configurable`; local_observation E2/1/1; computed_result E2/1/1.
- **v3** `config-v3-payload.json`, canonical sha256 `2e93889ce4b4f943f3c4e03d9a87ce9740c2012f2781a65637cd0ce831501d92` (пере-заморожен 2026-09-16, §2.6)
  - **diff v2→v3 = ровно 2 строки**: volatility `external_fact` и `temporal_fact` → `temporal`. Поведенчески нейтрально (оба 30 дней, `VOLATILITY_REVERIFY_DAYS`), но активация v3 меняет snapshot head'ов → enqueued re-evaluation всех external/temporal claim (механизм due/stale).

### 2.2 Корпус v2 (`docs/eval/question-set-v2.jsonl`)

sha256 (файл, как его хеширует eval-run): `93c1a93a4d6cbb4b29d31a30dedecb8488e5cf2135345a42d04bedbee11d31f4`

Структура (порядок потребления = priority DESC → created_at ASC → id):

| Приоритет | Кол-во | Сессии | Состав |
|---|---|---|---|
| 100 | 3 | 1–3 | old-as_of вопросы (as_of 15.04.2026 / 01.01.2026) — коммитятся ПЕРВЫМИ, чтобы к моменту v3-активации их reverify_after (as_of+30d) уже был в прошлом → worker пометит `due` |
| 90 | 11 | 4–14 | якоря паков: 7 URL-фактов + 4 workspace-файла (notes/plan.md, reading.md, todo.md, glossary.md) |
| 80 | 22 | 15–36 | follow-up паков: «В прошлой сессии было установлено… перепроверь по тем же двум источникам / файлу» — якорный claim уже в knowledge-контексте (FTS), модель подтверждает → evidence/dependency из 2-й сессии → `significant_claim_reuse` |
| 0 | 14 | 37–50 | одиночные URL-вопросы (4 external_fact + 10 temporal_fact), порядок внутри — случайный (uuid) |

**24 URL-факта** (20 temporal + 4 external); у каждого вопроса — явная формулировка факта и ровно 2 источника на 2 разных registrable domain (lift-правило: ≥2 groups + ≥2 source_assertion ⇒ E3; ≥4 groups ⇒ E4 ⇒ падение E3-gate, поэтому в вопросах — «строго по этим двум источникам»). Все 24 пары проверены фактом в нормализованном тексте ≤40 KiB на 16.09.2026:

| # | Факт (тип) | Источник 1 | Источник 2 |
|---|---|---|---|
| T1 | 193 государства-члена ООН (temporal) | un.org/en/about-us | ru.wikipedia.org (Список_государств_—_членов_ООН) |
| T2 | Гутерриш — 9-й ГС ООН (temporal) | un.org/en/about-us | ru.wikipedia.org (Антониу_Гутерриш) |
| T3 | ключевая ставка 14,00% с 27.07.2026 (temporal) | cbr.ru | consultant.ru/legalnews/32063 |
| T4 | Python 3.14.7 — последняя стабильная (temporal) | python.org/downloads | community.chocolatey.org/packages/python314 |
| T5 | PostgreSQL 18.6 — последний релиз (temporal) | postgresql.org | habr.com/ru/news/1080592 |
| T6 | PostgreSQL 14 EOL 12.11.2026 (temporal) | postgresql.org | habr.com/ru/articles/1081568 |
| T7 | 27 стран в ЕС (temporal) | en.wikipedia.org (European_Union) | european-union.europa.eu (facts-and-figures) |
| T8 | 24 официальных языка ЕС (temporal) | europa.eu/european-union/index_en | en.wikipedia.org (Languages_of_the_European_Union) |
| T9 | население Земли ≈8,3 млрд (temporal) | worldometers.info/world-population | en.wikipedia.org (World_population) |
| T10 | 9 ядерных государств (temporal) | fas.org/nuke/guide | icanw.org (faq_ru) |
| T11 | 118 химических элементов (temporal) | ru.wikipedia.org (Периодическая_система) | jinr.ru (posts/periodicheskaya-tablitsa-cherez-150-let) |
| T12 | Рублёво-Архангельская: 5 станций, 8,7 км, 05.09.2026 (temporal) | stroi.mos.ru (proiekt-rublievo-arkhanghiel-skoi-linii-mietro) | msk.kp.ru/daily/277813.5/5299000 |
| T13 | Конституция РФ: 12.12.1993, изменения с 01.07.2020 (temporal) | ru.wikipedia.org (Конституция_Российской_Федерации) | duma.gov.ru (legislative/documents/constitution) |
| T14 | инфляция 6,33% (авг. 2026) (temporal) | cbr.ru | kommersant.ru/doc/8953020 |
| T15 | USD ≈84,24 ₽ (16.09.2026) (temporal) | cbr.ru | banki.ru/products/currency |
| T16 | JPY 54,47 ₽/100 (temporal) | cbr.ru/eng/currency_base/daily | banki.ru/products/currency/cb |
| T17 | 33 буквы русского алфавита (temporal) | ru.wikipedia.org (Русский_алфавит) | dkyaspol.ru |
| T18 | 193 гос. в ООН @15.04.2026 (temporal, old as_of) | как T1 | как T1 |
| T19 | Python 3.14.4 @15.04.2026 (temporal, old as_of) | как T4 (таблицы релизов) | как T4 |
| T20 | 27 стран в ЕС @01.01.2026 (temporal, old as_of) | как T7 | как T7 |
| X1 | ISO 4217 код иены = JPY (external) | ru.wikipedia.org (Иена) | cbr.ru/eng/currency_base/daily |
| X2 | HTTP 404 = Not Found (external) | en.wikipedia.org (HTTP_404) | httpstatuses.com/404 |
| X3 | «Спутник-1» запущен 04.10.1957 (external) | ru.wikipedia.org (Спутник_1) | nasa.gov/history/dawn-of-the-space-age |
| X4 | 8 планет в Солнечной системе (external) | en.wikipedia.org (Solar_System) | science.nasa.gov/solar-system |

Механика reuse (проверено по коду `packages/evaluation/gates.py`): якорный claim считается reused, если на него есть ≥2 distinct-сессии через `evidence.created_in_session` / `claim_revisions.session_id` / `claim_dependencies`. Follow-up-сессия даёт это либо точным dedup'ом statement (host связывает новые evidence с существующим claim), либо dependency-edge (curator объявляет `dependencies` на `[c:<uuid>]` из knowledge-контекста — ADR-0006 cross-lingual FTS поднимает якорный claim).

### 2.3 Код (коммит этого блока, ветка `impl/from-scratch`)

1. `apps/orchestrator/main.py` — `build_orchestrator` передаёт `research_service=ResearchProxyService(session_factory, FilesystemArtifactStore(workspace_root.parent / "artifacts"))`. Ранее wake-tick и eval-run собирали оркестратор без сервиса → `research.fetch` всегда fail-closed. В sealed-профиле wiring инертен (tool profile-gated + proxy fail-closed).
2. `hostctl/cli.py` — `eval-run --questions` принимает опциональное целое `priority` (default 0), fail-closed парсинг с номером строки; seed записывает `priority` в `questions.priority`.
3. Тесты: `tests/unit/test_research_wiring.py` (wiring + путь artifact-хранилища), `tests/unit/test_corpus_parse.py` (парсинг + **закрепление формы замороженного корпуса**: 50/3/11/22/14, уникальность).

### 2.4 Инфраструктура

- SearXNG: контейнер `noezema-searxng`, 127.0.0.1:8888, конфиг `/home/denis/dsh1/searxng-config/settings.yml` (formats html+json, limiter off). **В самом ране `research.search` модель не вызывает** (инструмента нет в реестре) — search-путь проэ2е-проверен вне рана; in-run fetch идёт напрямую по URL из вопросов. Контейнер держать запущенным (e2e-проверка proxy search по запросу).
- Свежая БД `noezema-eval3` (тестовый контейнер `noezema-test-db`, порт 54329). EVAL-БД noezema-eval/noezema-eval2 заморожены, не трогать.
- Модель: `qwen36-35b-a3b-q6-mtp` (Qwen3.6 35B A3B Q6 MTP) @ `http://192.168.1.48:8080/v1` (llama-swap; слот модели: `-c 524288 --parallel 2` — отсюда `backend_context_limit` 262144). Env: `NOEZEMA_LLM_BASE_URL` + `NOEZEMA_LLM_MODEL`, `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192` (reasoning-бюджет ≥P99, AGENTS.md §7), `NOEZEMA_LLM_TIMEOUT_SECONDS=600`. Модель грузится llama-swap'ом по первому infer-запросу — перед серией прогреть малым запросом и убедиться в `status=loaded`.

### 2.5 Допуски к запуску (правки по решению пользователя, после заморозки)

1. **Отчётность (не гейты):** во все ratio-гейты добавлен 95% доверительный интервал (Wilson) — ключ `ci95: {low, high}` в результате гейта и в печатной таблице `eval-run` (`packages/evaluation/gates.py: wilson_ci95`, `_gate`). Пороги, знаменатели и исходы не меняются (закреплено тестами: `tests/unit/test_wilson_ci.py`, `tests/scenario/test_evaluation_gates.py`).
2. **Выгрузка слепой выборки для ручной проверки:** `hostctl blind-sample --run <id|label> [--out file] [--fragment-chars N]` — тот же seeded/стратифицированный отбор, что меряют blind-гейты (общий код отбора: `packages/evaluation/blind.py: blind_sample_claim_ids`, используется и гейтами, и выгрузкой). Формат — §5 «Ограничение метода». Тест: `tests/scenario/test_blind_sample_dump.py` (совпадение выборки с измеренной + поля claim/evidence + фрагмент из content-addressed хранилища).
3. **Документ:** новый раздел §5 «Ограничение метода» (blind-гейты = структурная проверка; в отчёте рана вынесены в отдельный блок), пересчёт reuse-ожидания в §4, оговорка о хэшах в §8.

### 2.6 Пере-заморозка (2026-09-16): первый запуск сорвался — дефект конфига

Первый запуск (тот же день) сорвался по технической причине: **все 50 сессий упали мгновенно** (0 steps, 0 с, 0 вызовов модели) с ошибкой `token budgets invalid: section limits sum 26624 > input_budget 22528` — fail-closed валидация §5.4.1 сработала до первого model-call, данных в БД нет.

**Причина:** при заморозке `max_output_tokens` был поднят с 4096 (EVAL-2) до 8192 без пересчёта суммы секций. В EVAL-2 бюджет сходился впритык: 32768 − 4096 − 2048 = **26624** = Σ секций; после подъёма input_budget = 32768 − 8192 − 2048 = 22528 < 26624. Payload при заморозке live-проверялся только на активацию (freeze-когорта), полная сессия под v2 не исполнялась, а `activate-online` валидировал канонический хэш, но не бюджеты токенов — дефект дожил до запуска.

**Что изменено — ровно 2 строки в секции `model` каждого payload, ничего больше:**
`context_window: 32768 → 40960` и добавлено `backend_context_limit: 262144` (фактический предел слота бэкенда: модель на 192.168.1.48 запущена с `-c 524288 --parallel 2`, конфиг llama-swap проверен). Итог: input_budget = min(40960, 262144) − 8192 − 2048 = **30720** при Σ секций **26624** (запас 4096).

| payload | canonical ДО | canonical ПОСЛЕ |
|---|---|---|
| v2 | `73b5f14e6630f086d2465a42308effe7d10e517ef39fe8b1ce2f563c417c1aaf` | `ffc98c9e54b5ba0b3621d5f3fb37616fabbb6b159a4a9d5d46710adee75a6e2d` |
| v3 | `dbfd11b1a6fc83cf8695b44753448fce1049ce7614e2a1d40bad9a2ba92d1f62` | `2e93889ce4b4f943f3c4e03d9a87ce9740c2012f2781a65637cd0ce831501d92` |

**Почему подъём окна, а не урезание секций:** секции бюджета остаются байт в байт как в EVAL-2 и bootstrap (Σ = 26624) — урезание contradictions/protocol/recent_errors изменило бы то, что видит модель, и сломало бы A/B-сопоставимость с baseline EVAL-2, ради которой прогон и делается. Diff v2→v3 после правки не изменился по смыслу: ровно 2 строки volatility. Пороги §22.2, rules engine и корпус (`question-set-v2.jsonl`, sha256 `93c1a93a4d6cbb4b…` — **не изменился**) не тронуты.

**Доказательства срыва (сохранены, не удалять):** БД `noezema-eval3` — run `83366765-e795-44d6-a5d0-b2027270fa54` (`outcome=insufficient_sample`, 0 строк сессий / 0 claims / 0 model_runs), БД оставлена как есть и НЕ переиспользуется; логи: `/home/denis/dsh1/eval3-logs/` — `eval3-run.log` (все 50 мгновенных падений), `eval3-activate-v2.log`, `eval3-watchdog.log` (watchdog штатно зафиксировал завершение рана без v3-активации, `et=0 < 20`), `eval3-worker.log`.

**Регрессия-защита (код, тот же коммит-блок):**
1. `activate-online` — **fail-closed на невалидных бюджетах**: `packages/memory/activation.py: _validate_payload_budgets` проверяет `TokenBudgets.from_snapshot(model, token_budgets).validate()` (зеркалируя `ContextBuilder`: §5.4.2-секция `pending_claims` исключена из суммы) **ДО** записи candidate-строки; конфиг, на котором не стартует ни одна сессия, не публикуется. Тест: `tests/scenario/test_online_activation.py::test_online_rejects_unstartable_token_budgets` (ошибка до записи, candidate не создан, head не тронут).
2. Замороженные payload'ы закреплены тестом: `tests/unit/test_freeze_payloads.py` — грузит оба файла из репо, требует `validate() == []`, закрепляет секции за EVAL-2-значениями (Σ 26624) и идентичность `model`/`token_budgets` между v2 и v3.

## 3. План запуска (после утверждения)

```
0. docker start noezema-searxng   (если остановлен); прогрев модели (малый infer-запрос,
   status=loaded); тестовый контейнер БД жив
1. CREATE DATABASE noezema-eval3b;  alembic upgrade head
   (NOEZEMA_DATABASE_URL=postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:54329/noezema-eval3b)
   (noezema-eval3 — доказательства сорванного первого запуска, §2.6; не трогать, не переиспользовать)
2. env рана: NOEZEMA_DATABASE_URL=...noezema-eval3b, NOEZEMA_DATA_ROOT=<свежая директория>,
   NOEZEMA_NODE_OWNER=eval3, NOEZEMA_LLM_BASE_URL=http://192.168.1.48:8080/v1,
   NOEZEMA_LLM_MODEL=qwen36-35b-a3b-q6-mtp, NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192,
   NOEZEMA_LLM_TIMEOUT_SECONDS=600
3. hostctl activate-online --payload docs/eval/config-v2-payload.json --reason "EVAL-3: curated research profile"
4. фоновый цикл worker (ДО активации v3 и на всё время рана):
   while :; do hostctl reassessment-tick --batch-size 50 --lease-seconds 120; sleep 15; done
5. hostctl eval-run --label EVAL-3 --questions docs/eval/question-set-v2.jsonl \
     --count 50 --slo-seconds 3600 --seed 20260915 --blind-size 50   (фон, лог в файл)
6. watchdog: как только в БД ≥20 claim типов external_fact|temporal_fact с head-ом
   (3 old-as_of уже среди них — сессии 1–3) →
   hostctl activate-online --payload docs/eval/config-v3-payload.json --reason "EVAL-3: mid-run v2->v3 (volatility temporal)"
   (driver eval-run сам мостит activation_slot_busy: retry 10 c ≤ 600 c)
7. фоновый цикл worker доводит activation jobs до конца (re-evaluation: old as_of → due,
   текущие → fresh); eval-run считает 11 гейтов сразу после цикла 50 сессий —
   к этому моменту очередь активации давно пуста (активация ~сессия 15–25)
8. пост-ран: SELECT count(*) FROM claim_assessment_heads WHERE assessment_state='pending'
   (ожидается 0), остановить фоновый цикл, отчёт по гейтам
```

Повторный расчёт гейтов без пересессий: `eval-run --skip-sessions` на той же БД.

## 4. Арифметика гейтов (MIN_SAMPLE = 20 у всех ratio-гейтов; overall = passed только если ни один гейт не failed и не insufficient_sample)

Все ratio-гейты в отчёте рана несут **95% доверительный интервал (Wilson)** — правка отчётности по §22.2 («95% доверительный интервал публикуется»): пороги, знаменатели и исходы гейтов интервалом не меняются. Blind-гейты в отчёте помечены **отдельно** (структурная проверка, см. §5).

| Гейт | Порог | Ожидание | Почему |
|---|---|---|---|
| new_supported_refuted_e2 | ≥0.80 | ~1.00 (N≈24–40) | supported ⇒ grade ≥ min_grade_for_supported (E2/E3/E4) — структурно |
| external_temporal_e3 | =1.00 | 1.00 (N≈24–28) | ровно 2 домена/claim + ≥2 source_assertion ⇒ E3 (E4 требует ≥4 groups — вопросы запрещают лишние источники) |
| eligible_sessions_with_outcome | ≥0.60 | ≥0.94 (N=50) | каждая сессия коммитит claim/evidence; LeaseLost ≤2–3% при phase_deadline 1800 |
| near_duplicate_questions | ≤0.15 | 0/50 | повтор-гард срабатывает внутри сессии; каждый вопрос задаётся один раз |
| significant_claim_reuse | ≥0.25 | **проходит только при ≥9 якорях из 11, исход практически бимодальный** | см. расчёт ниже и риск 1 в §6 |
| due_stale_time_sensitive | ≤0.20 | 0.15 (3/20) | 20 temporal-claim с current head; 3 old-as_of → due после v3-активации; запас: +1 due ещё пройдёт (0.20), +2 — нет |
| reassessment_slo | =1.00 | 1.00 (N≥20) | watchdog активирует v3 только при ≥20 claim ⇒ ≥20 activation jobs; фоновый цикл доводит за секунды (SLO 3600 c) |
| current_pending_invalid_ancestor | 0 | 0 | evidential-зависимости на non-current цели host отклоняет при коммите; pending-окно активации короткое, очередь доведена до нуля до гейтов |
| high_severity_incidents | 0 | 0 | idempotency-конфликтов нет; reconciliation-записей не ожидается |
| blind_provenance_path | ≥0.90 | ~1.0 (sample 50) — **структурная проверка, не пройденный гейт §22.2** | external/temporal evidence → source (proxy), local/computed → artifact; оговорка §5 |
| blind_scope | ≥0.80 | ~1.0 — **структурная проверка, не пройденный гейт §22.2** | requires_scope=true для всех коммитимых типов; supported ⇒ ≥1 evidence со scope; оговорка §5 |

Расчёт reuse (честная арифметика, проверка формулы пользователя): знаменатель гейта — **все** значимые claim'ы рана (supported/refuted, E0+). Если follow-up разрешён точным dedup'ом (statement+claim_type совпадают с якорным claim — формулировки корпуса написаны под этот путь), новый claim-строки нет и знаменатель не растёт; тогда при k переиспользованных якорях из 11 доля = k/(50−2k), и k/(50−2k) ≥ 0.25 ⇔ **k ≥ 9** (k=9: 9/32 = 0.281 ✓; k=8: 8/34 = 0.235 ✗). Общее условие при k_d dedup-разрешённых follow-up: 4k + k_d ≥ 50. Две оговорки: (а) если reuse идёт через dependency-edge (модель перефразировала ⇒ новый claim + ребро), follow-up-claim **входит в знаменатель**: чистый dependency-путь требует k ≥ 12.5 — невозможно, максимум 11/50 = 0.22 < 0.25, гейт математически не проходит; (б) сессии, дающие >1 или 0 claim, сдвигают знаменатель на ±. Отсюда и бимодальность: модель либо держит якорные формулировки (≈9–11/11 → passed), либо перефразирует (≤8/11 → failed).

## 5. Ограничение метода (blind-гейты и CI)

**Слепая выборка по §22.2 — РУЧНАЯ процедура (в §22.2/ARCHITECTURE v0.3 она определена именно как ручная, с публикацией 95% ДИ).** Автоматический ран может измерить только структурную проекцию: у каждого claim из выборки есть linked evidence, evidence разрешается в source/artifact, scope заявлен. Поэтому:

- исходы `blind_provenance_path` / `blind_scope` в отчёте рана — **структурная проверка, а не пройденные гейты §22.2**; в отчёте они вынесены в отдельный блок с явной пометкой и не приравниваются к остальным гейтам;
- **полная приёмка** требует, чтобы человек независимо проверил каждый claim выборки (следует ли statement из процитированных фрагментов) и чтобы 95% ДИ был опубликован (в отчёте — по всем ratio-гейтам, Wilson);
- для ручной проверки: `hostctl blind-sample --run <id|label> --out <file>` — выгружает **тот же seeded/стратифицированный отбор**, что меряют гейты (выборка совпадает с измеренной, детерминирована seed'ом), по каждому claim: id, тип, statement, epistemic_status, grade, assessed_scope; по каждому связанному evidence: kind, relation, scope, URL источника или id артефакта и процитированный фрагмент (нормализованный текст источника из content-addressed хранилища / observation-артефакт).

## 6. Риски (честный список)

1. **reuse < 0.25 — топ-риск, и после пересчёта (§4) это бимодальный риск**: passed только при ≥9 якорях из 11, причём через dedup-путь (точное совпадение statement+claim_type). Митигация: детерминированный порядок паков (priority), формулировки «В прошлой сессии было установлено <якорная формулировка>… перепроверь» (максимально близки к якорному statement), cross-lingual FTS (ADR-0006). Если модель перефразирует — гейт провалится математически, это честный результат, повторный ран только по решению пользователя.
2. **Модель неверно типизирует** (temporal→external): due_stale-знаменатель упадёт ниже 20 → insufficient_sample. Митигация: «на текущую дату» во всех 20 temporal-вопросах; 4 external — явно стабильные факты.
3. **Один источник вместо двух** (fetch 404/timeout): claim не supported → выпадает из E3-знаменателя; при полном срыве всех 24 → insufficient_sample. Митигация: пары проверены e2e 16.09.2026; страницы мелкие, факт в лиду (≤40 KiB budget).
4. **E4 over-fetch** (модель дофетчит 3–4 домена) → E3-gate failed. Митигация: «строго по этим двум источникам».
5. **Drift/404 страницы в момент рана** (проверка 16.09.2026, ран — в тот же день).
6. **Активация v3 не произойдёт**, если claim-ов не наберётся 20 (все fetch-и сломаны) — тогда reassessment_slo = insufficient_sample; watchdog логирует причину.
7. **LeaseLost** — цель ≤2–3% (phase_deadline 1800); EVAL-2: 14% при 300 c.
8. **Pending heads в момент гейтов** — исключено фоновым циклом (проверка п. 8).
9. **SearXNG down** — ран не блокирует (search не вызывается in-run), но e2e-проверка search-пути невозможна.
10. **Context overflow** — max_output 8192 (reasoning), context 40960 (input_budget 30720, запас над Σ секций 4096, §2.6), страницы мелкие.

## 7. Baseline EVAL-2 (сравнение после рана)

50 attempts / 43 terminal / 7 LeaseLost (14%); significant_claim_reuse 1/28 — failed (единственный failed-гейт); near_duplicate 0/16; external_temporal_e3 и due_stale — insufficient_sample (sealed-профиль, нет source-backed claim); SLO не фиксировался в ране.

## 8. Что НЕ меняется

Пороги §22.2 (immutable), rules engine, SQL гейтов, ADR-0005/0006, staging/fenced-commit инварианты, EVAL-БД (закрыты), `ARCHITECTURE.md`. **Замороженные артефакты** (`config-v2/v3-payload.json`, `question-set-v2.jsonl`) — хэши, приведённые в §2: после допусков к запуску (2026-09-16, правки только отчётности: ci95, blind-выгрузка) пересчитаны и **не изменились**; после пере-заморозки (§2.6) изменились v2/v3 (ровно 2 строки model-секции, таблица ДО/ПОСЛЕ там же), хэш `question-set-v2.jsonl` **не изменился** ни в один из двух раз.

## 9. Перезапуск EVAL-3c (2026-09-19)

Повод: после EVAL-3b (остановлен, 7 сессий, 0 external/temporal claims;
разбор — `docs/eval/EVAL-3b-postmortem.md`) закрыты T7.8–T7.18
(T7.8–T7.17 — merge в `main` `4b49f00`; T7.18 — `83d0ea9`, принят и
запушен в `impl/from-scratch`). Условие перезапуска — E2E на черновой
БД на реальной модели — выполнено: шаг 1, прогон `EVAL3c-STEP1-T718`
на `noezema-eval-draft3` (2026-09-18, §9.3). Решение пользователя
(2026-09-19): **запускаем EVAL-3c как есть**, с известным
ограничением (дробление факта куратором — ставка ЦБ, §9.3) — в этот
прогон не чиним.

### 9.1 Код прогона

- Коммит кода прогона: **`83d0ea9`** (ветка `impl/from-scratch`, T7.18) —
  HEAD на момент запуска, кодовых изменений после него нет. Коммит
  этого раздела (только документ) — отдельный.
- Правила оценки: **rules-v2 вместо rules-v1** (ADR-0007, T7.17, с
  уточнением T7.18 от 2026-09-18: относительная опорная дата «на
  текущую дату» = дата сессии по часам хоста выводится хостом):
  `RULES_ENGINE_VERSION = "rules-v2"` (`packages/memory/evidence.py`),
  версия фиксируется в каждой assessment; payload-правила
  (`claim_type_rules`), пороги §22.2, `requires_scope` и правило E3
  (≥2 независимых groups, ≥2 source_assertion) не менялись.
- rules_hash (тот же `rules_hash(dict(claim_type_rules))`, который
  eval-run фиксирует в строке рана):
  - v2 payload (активен на старте): `a0b78e2d246f645641965c0946424db4defde3a4b06c779d78310f6bc0c467cd`
  - v3 payload (после mid-run флипа): `f96eeffc527cbeb2ca3a0c3ffa207d2fff3a62582483ede1fa14c2b16b866d71`
    (разница v2/v3 — ровно 2 строки volatility external/temporal →
    `temporal`, §2.1).

### 9.2 Что изменилось после EVAL-3b (T7.8–T7.18, по строке на задачу)

| Задача | Коммит | Что |
|---|---|---|
| T7.8 | `8079c0f` | текст утверждения в payload `source_assertion` (EVAL-3b P.1, §6.4) |
| T7.9 | `460680f` | claim без head не коммитится (EVAL-3b P.2, §14.1) |
| T7.10 | `160c5bf` | newest-first обрезка explorer-контекста (EVAL-3b P.3, §5.4) |
| T7.11 | `1b5fc7a` | идемпотентный refetch — существующий source+chunk, не 500 (EVAL-3b P.4, §6.4) |
| T7.12 | `212f61b` | N одинаковых (tool, args_hash) → deny с наблюдением (EVAL-3b P.5, §5.4) |
| T7.13 | `3ba4cc5` | `message.reply` не предлагать при пустом inbox + самоочевидная схема (EVAL-3b P.6) |
| T7.14 | `6163045` | правила в промптах: explorer ≤2 повтора после ошибки; curator — вопрос без текста утверждения (EVAL-3b P.2/P.4) |
| T7.15 | `5c2be5d` | E2E-валидация полного пути на черновой БД — E3 из 2 независимых источников (условие перезапуска EVAL-3) |
| T7.16 | `ccdc385` | вопрос-зависимый выбор фрагмента assertion_text (assertion-window, EVAL-3b, §6.4) |
| T7.17 | `779d1fd` | устойчивый scope — оценка по хост-деривации (rules-v2, ADR-0007; фикстуры assertion-window в репозитории; §3.7, §8.7, §11.2) |
| T7.18 | `83d0ea9` | относительная опорная дата «на текущую дату» выводится хостом — дата сессии (уточнение ADR-0007, §3.7, §8.7) |

Поведенческие последствия для рана: куратор видит текст утверждения в
строках evidence (P.1) → способен сформулировать external/temporal_fact;
последний фетч не отрезается (P.3); повторные fetch не падают 500
(T7.11) и не жгут шаги (T7.12); scope не зависит от совпадения ключей,
придуманных моделью (T7.17), а для вопросов «на текущую дату» опорную
дату не сдвигает и модельный `as_of` (T7.18) — E3 открыт при двух
названных в вопросе источниках.

### 9.3 Допуск: шаг 1 на `noezema-eval-draft3`, прогон `EVAL3c-STEP1-T718` (2026-09-18)

Прогон `7f136a3f-3fa6-4cae-a353-11ac0bbb3f98`, БД `noezema-eval-draft3`
(оставлена как улика, не трогать), config v2 активирован (canonical
`ffc98c9e…`), модель `qwen36-35b-a3b-q6-mtp` @ 192.168.1.48 (loaded).
Три вопроса скопированы дословно из `question-set-v2.jsonl`, без
подсказок про scope: ставка ЦБ (cbr.ru + consultant.ru), страны ЕС
(en.wikipedia + european-union.europa.eu), HTTP 404 (en.wikipedia +
httpstatuses.com). 3/3 сессий terminal (succeeded_partial), 3 коммита;
все оценки — rules-v2, rules_hash `a0b78e2d…`,
`assessed_scope.scope_schema = host-scope-v1`. overall
`insufficient_sample` (N=3 < 20 — ожидаемо для 3-вопросного smoke).

| Вопрос | Итог по БД |
|---|---|
| Страны ЕС | «Количество стран-членов ЕС составляет 27.» — `external_fact`, **supported, E3**, confidence 0.75; 2 независимые groups (wikipedia.org + europa.eu), 2 source_assertion; `assessed_scope.as_of = 2026-09-18` = дата сессии (T7.18 работает) |
| Ставка ЦБ | **E1/hypothesis** на обоих claim'ах — известное ограничение, см. ниже |
| HTTP 404 | `succeeded_partial`, question_answered, claim не закоммичен (модель ответила без утверждения) |

**Допуск пройден.**

Известное ограничение (решение пользователя: в этот прогон НЕ чиним):
куратор разбил факт ставки ЦБ на **два claim по одному источнику** —
«Ключевая ставка Банка России установлена на уровне 14,00%…»
(1 source_assertion: cbr.ru) и «Совет директоров Банка России принял
решение сохранить…» (1 source_assertion: consultant.ru). Каждый claim
несёт одну independence-group (< 2) → E1. Известное поведение модели;
в прогоне EVAL-3c такой факт (T3) выпадет из числителя E3-гейта, если
повторится.

### 9.4 Перепроверка замороженных артефактов (2026-09-19, до запуска)

Файловые sha256 — **не изменились**:

| Файл | sha256 (файл) |
|---|---|
| `docs/eval/config-v2-payload.json` | `2b22b417f2422b693854e3338505e9ba1540d2ddda89a1bcf49342de0668f8c3` |
| `docs/eval/config-v3-payload.json` | `87278c77e2919ed0a0019c5c3f2b2e7de937b940869967b016978b8978be5525` |
| `docs/eval/question-set-v2.jsonl` | `93c1a93a4d6cbb4b29d31a30dedecb8488e5cf2135345a42d04bedbee11d31f4` |

Канонические хэши (canonical JSON, `packages/domain/canonical.py`;
пересчитаны 2026-09-19 — **совпадают с §2.1/§2.6, не изменились**):

| payload | canonical sha256 |
|---|---|
| v2 | `ffc98c9e54b5ba0b3621d5f3fb37616fabbb6b159a4a9d5d46710adee75a6e2d` |
| v3 | `2e93889ce4b4f943f3c4e03d9a87ce9740c2012f2781a65637cd0ce831501d92` |

Пороги §22.2, корпус (`question-set-v2.jsonl`) и payload-файлы — не
трогались.

### 9.5 Инфраструктура прогона

- БД: **новая `noezema-eval3c`** (CREATE DATABASE + `alembic upgrade
  head`, контейнер `noezema-test-db`, порт 54329).
- data-root: **`/home/denis/dsh1/eval3c-data`** (свежая).
- Логи: **`/home/denis/dsh1/eval3c-logs/`** — `eval3c-run.log`,
  `eval3c-activate-v2.log`, `eval3c-worker.log`, `eval3c-watchdog.log`.
- Не трогать: БД noezema-eval / noezema-eval2 / noezema-eval3 /
  noezema-eval3b / noezema-eval-draft2 / noezema-eval-draft3, их логи и
  data-директории — улики прошлых прогонов.
- План запуска — §3 с заменой имён: label **`EVAL-3c`**, БД
  `noezema-eval3c`, data-root и логи выше; замороженные параметры без
  изменений: `--count 50 --slo-seconds 3600 --seed 20260915 --blind-size 50`.
- Перед eval-run — явная проверка: активный head (
  `runtime_config_heads.active_config_snapshot_id` →
  `config_snapshots.payload_sha256`) = canonical v2 `ffc98c9e…`; не
  совпадает — останов и доклад.

### 9.6 Известный риск: опорная дата и полуночь UTC (T7.18)

Для относительных форм («на текущую дату») опорная дата = дата сессии
(`sessions.created_at`, часы хоста, UTC). **Переиспользованный claim
получает опорную дату сессии переиспользования**: если прогон
пересечёт 00:00 UTC, то для сессий, начавшихся после полуночи,
evidence предыдущего дня окажется «ранее» нового якоря и «текущие»
temporal claim'ы могут опуститься до E1 по неизменённому предикату
`all(...)` (известное следствие T7.18, ADR-0007 «Известное
последствие»). Митигация (решение пользователя): прогон стартует
**утром UTC**, чтобы уложиться до полуночи; время старта фиксируется в
отчёте о запуске. Если прогон всё же пересечёт полночь — это **отмечается
в отчёте по прогону, но прогон НЕ останавливается и НЕ переигрывается**:
провал гейта — результат.
