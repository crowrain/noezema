# EVAL-3 — замороженная конфигурация (готово, НЕ запущено)

Статус: **подготовка завершена; первый запуск (2026-09-16) сорвался — дефект конфига, пойманный fail-fast до первого вызова модели; пере-заморожено (§2.6); перезапуск — только после явного решения пользователя. 2026-09-19: EVAL-3c сорван технически (§10.1), перезапуск EVAL-3d доведён до конца 50 сессий, но гейты упали на дефекте учёта head-ов после mid-run активации — T7.19 (§10.2–10.4); досчёт EVAL-3d — после проверки T7.19.**
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

## 10. EVAL-3c сорван → EVAL-3d; дефект гейтов T7.19 (2026-09-19)

### 10.1 Хронология

**EVAL-3c** — прогон `bd21973c-164a-44e0-94de-4cbfe4ce8ed0`, БД
`noezema-eval3c` (оставлена как улика, данные НЕ трогать), логи
`/home/denis/dsh1/eval3c-logs/`:

| Время (UTC) | Событие |
|---|---|
| 08:25:25 | старт: `eval-run` заморожен (snapshot `6f0f711e` = canonical v2 `ffc98c9e…`, rules-v2, rules_hash `a0b78e2d…`, SLO 3600 c, seed 20260915, blind 50), 50 вопросов засиданы |
| 08:25:26–08:33 | 2 сессии из 50 завершены (succeeded, succeeded_partial) |
| 08:36:26 | последняя запись watchdog (`et=2 < 20`) |
| ~08:36:51 | **сорван по технической причине**: `eval-run`, worker и watchdog были запущены через `setsid nohup` из headless-сессии dsh и убиты при завершении хода сессии. Утверждение в отчёте о запуске, что процессы «переживут завершение хода», было непроверенным |

**EVAL-3d** — прогон `faa3cded-2eec-4087-bc1e-60c242728b90`, БД
`noezema-eval3d` (данные НЕ трогать), логи `/home/denis/dsh1/eval3d-logs/`;
та же замороженная конфигурация (§2, §9) и тот же код (`83d0ea9`),
изменён только документ заморозки (§9):

| Время (UTC) | Событие |
|---|---|
| 13:05:54 | старт через `systemd-run --user` (переживает завершение хода сессии) |
| 13:05:55–16:33:26 | **все 50 сессий завершены**: 24 succeeded, 26 succeeded_partial, 0 failed, 0 LeaseLost (40 сессий под v2, 10 под v3) |
| 15:47:32 | watchdog (et=20) активировал v3: `activate-online` fence 2, audit `activation_published` 15:47:32.977Z, snapshot `db622010` = canonical v3 `2e93889c…`; v2 → superseded, указатель `runtime_config_heads` → v3 |
| 15:47:33–16:33 | 20 reassessment-задач активации выполнены (completed=20, очередь пуста, pending heads = 0) |
| 16:33:26 | session 50/50 завершена; **eval-run упал** в `_finish → compute_gates → _provenance_complete` (`packages/evaluation/gates.py:500`): `sqlalchemy.exc.MultipleResultsFound`. Строка прогона осталась `outcome=running`. Worker и watchdog остановлены пользователем |

### 10.2 Дефект гейтов (T7.19)

После mid-run активации у claim'а есть head на **каждый** config
snapshot (shadow heads, `UNIQUE(claim_id, config_snapshot_id)`, §8.7.2)
— в `noezema-eval3d`: **25 current head на v2 (superseded) + 34 на v3
(active); у 24 claim'ов current head на обоих** (10 claim'ов — только
v3, созданы после флипа; 1 — только v2, см. §10.4), 35 claim'ов всего.
Ни один SQL гейтов (`packages/evaluation/gates.py`, строки ~217/247/324/
371/440/504/590) и blind-выгрузка (`packages/evaluation/blind.py`) не
фильтровал head по snapshot:

- гейты считали **строки head'ов**, а не claim'ы — знаменатели
  раздуты (59 «current head» при 35 claim'ах), доли искажены;
- per-claim запросы blind-гейтов (`scalar_one_or_none` / `.first()`
  по мульти-результату) падают `MultipleResultsFound` — именно это
  убило EVAL-3d в `_finish`;
- watchdog тоже считал с дублями (et=51 к концу — сумма current-строк
  external/temporal по обоим snapshot'ам).

Дефект латентный с T7.5 (механизм оценки §22.2): ни один прошлый прогон
не доходил до mid-run активации (EVAL-1/2 — активаций нет, EVAL-3b —
остановлен ранее, первый запуск EVAL-3 — сорвался до сессий, шаги на
черновых БД — 3 сессии без флипа).

### 10.3 Правило выбора head (T7.19)

**Текущее знание claim'а = head `(claim_id, active_config_snapshot_id)`**,
где `active_config_snapshot_id` — указатель
`runtime_config_heads(scope='global')` (равенство указателя, НЕ
`config_snapshots.activation_state='active'`):

- §14.1: «current lifecycle разрешается только через
  `runtime_config_heads.active_config_snapshot_id`. Pointer equality, а
  не `config_snapshots.activation_state='active'`, определяет effective
  config»;
- §8.7.2: «Query/Memory Service сначала разрешает effective snapshot
  через runtime pointer и только затем читает соответствующий head»;
- то же разрешение делает query path: `MemoryService.claim_view`
  возвращает `None` для claim'а без head на активном snapshot — такой
  claim не является текущим знанием и не подаётся в контекст.

Следствия, закреплённые тестами (`tests/scenario/
test_evaluation_gates_activation.py`):

1. каждый claim учитывается гейтами **ровно один раз** — по head'у
   активного snapshot (на нём и grade, и epistemic_status, и
   provenance/scope для blind-гейтов);
2. claim **без head на активном snapshot** не имеет current lifecycle
   по effective config (§14.1) и не входит ни в знаменатели, ни в
   числители head-гейтов, ни в слепую выборку. Это не
   «неправомерное выбрасывание»: протокол активации (§8.7.2)
   гарантирует shadow head на новом snapshot для **каждого** claim'а,
   существовавшего на момент флипа (cohort = все claims; publish
   запрещён без полного seal), — head'а на новом snapshot может не
   быть только у claim'а, созданного в окне race (см. §10.4);
3. claim, чей head на активном snapshot `pending | invalid`, — не
   «current» (lifecycle §14.1) и выпадает по фильтру
   `assessment_state = 'current'` — семантика гейта, не дефект;
4. указатель разрешается в момент `compute_gates` (гейты меряют
   финальное состояние знания по effective config — ради этого в
   эксперименте и mid-run-флип), а **не** snapshot из строки рана
   (строка заморожена на v2; гейты от неё не зависят).

### 10.4 Почему это не меняет метод

- **Пороги §22.2** — неизменны (immutable; зафиксированы до серии в
  строке рана).
- **Определения гейтов по смыслу** — неизменны: «current head
  claim'а» теперь разрешается по §14.1; знаменатели по смыслу — как и
  задумано, по **одному** head на claim (до любой активации так и
  было; прогон без mid-run активации даёт те же числители/знаменатели —
  зафиксировано тестом).
- **Замороженные артефакты** — не трогались. Явная сверка (2026-09-19,
  T7.19) — хэши **не изменились** и совпадают с §2.1/§2.6/§9.4:

  | Файл | sha256 (файл) |
  |---|---|
  | `docs/eval/config-v2-payload.json` | `2b22b417f2422b693854e3338505e9ba1540d2ddda89a1bcf49342de0668f8c3` |
  | `docs/eval/config-v3-payload.json` | `87278c77e2919ed0a0019c5c3f2b2e7de937b940869967b016978b8978be5525` |
  | `docs/eval/question-set-v2.jsonl` | `93c1a93a4d6cbb4b29d31a30dedecb8488e5cf2135345a42d04bedbee11d31f4` |

  Канонические (canonical JSON): v2 `ffc98c9e54b5ba0b3621d5f3fb37616fabbb6b159a4a9d5d46710adee75a6e2d`,
  v3 `2e93889ce4b4f943f3c4e03d9a87ce9740c2012f2781a65637cd0ce831501d92`.
  Сверка по `noezema-eval3d`: `payload_sha256` активного snapshot'а
  (`db622010`) = `2e93889c…` = canonical v3; `rules_hash` строки рана =
  `a0b78e2d…` (v2, §9.1) — всё как при заморозке.

- Изменён **только учёт** head'ов после активации (`packages/evaluation/
  gates.py`: 7 запросов; `packages/evaluation/blind.py`: 2 запроса;
  общее правило — константа `EFFECTIVE_SNAPSHOT_SQL` в blind.py).
  Это правка учёта дублей, а не метода.

### 10.5 Данные EVAL-3d и оценка гейтов при досчёте (read-only)

Состояние `noezema-eval3d` (SELECT, 2026-09-19): 35 claims; v2 — 25
current (hypothesis/E1 ×11, supported/E2 ×4, supported/E3 ×10); v3 — 34
current (hypothesis/E1 ×16, supported/E2 ×4, supported/E3 ×14);
pending/invalid — 0; reassessment_jobs completed=20; sessions: 50
terminal (40 под v2, 10 под v3).

**Claim только на superseded v2** — `8bbbb06a` (temporal_fact,
current/hypothesis/E1, `prepared_by=session`, сессия `6f45deea`,
committed 15:50:59Z). Источник — **quiesce-race активации**: сессия
`6f45deea` (40-я, под v2) открыла длинную phase-1-транзакцию в
15:47:21.8Z — **до** флипа (15:47:32.9Z); её строка сессии и создаваемый
claim были незакоммичены (невидимы) на момент cohort freeze
(15:47:32.88Z), поэтому проверка «нет активных сессий»
(`packages/memory/activation.py:463–483` считает только
закоммиченные `ACTIVE_SESSION_STATES`) увидела 0 активных, а cohort
(`SELECT id FROM claims`) не включила in-flight claim; коммит в
15:50:59.8Z — после флипа — записал head под замороженным v2 snapshot
сессии (`plan.config_snapshot_id`, `apps/orchestrator/orchestrator.
py:296`). **Отдельный латентный дефект активации** (quiesce не закрывает
окно невидимой in-flight сессии) — вне T7.19; гейты обязаны
детерминированно обрабатывать получившееся состояние данных (правило
§10.3, п.2 — зафиксировано регрессионным тестом).

Гейты EVAL-3d **после** T7.19 (выполнено read-only `compute_gates` на
`noezema-eval3d`, запись НЕ делалась; строка рана остаётся
`outcome=running` до проверки T7.19):

| Гейт | num/den | Исход |
|---|---|---|
| new_supported_refuted_e2 | 18/18 | insufficient_sample (N<20) |
| external_temporal_e3 | 14/14 | insufficient_sample (N<20) |
| eligible_sessions_with_outcome | 50/50 | passed |
| near_duplicate_questions | 0/50 | passed |
| significant_claim_reuse | 5/18 (0.278) | insufficient_sample (N<20) |
| due_stale_time_sensitive | 8/17 (0.471) | insufficient_sample (N<20) |
| reassessment_slo | 20/20 (SLO 3600 c) | passed |
| current_pending_invalid_ancestor | 0/34 | passed |
| high_severity_incidents | 0 | passed |
| blind_provenance_path | 34/34 | passed — **структурная проверка, §5** |
| blind_scope | 34/34 | passed — **структурная проверка, §5** |

overall (правило finish: нет failed, есть insufficient_sample) →
**insufficient_sample**. Для сравнения — что посчитал бы **старый**
код, не упади он: g1 32/32, g2 24/24, g5 10/32, g6 16/30, g8 0/59
(знаменатели раздуты дублями), blind-гейты — падение
`MultipleResultsFound`. Слепая выборка после T7.19: 34 claim'а
(уникальных), seed 20260915, size 50.

### 10.6 Судьба строк прогонов

- **EVAL-3d `faa3cded…`**: НЕ досчитана и НЕ закрыта (решение
  пользователя после проверки T7.19). Данные БД не менялись. Варианты
  досчёта: штатный `compute_gates + finish_evaluation_run` по строке
  (аналог `close-draft2-runs.py`) либо повторный расчёт через
  `eval-run --skip-sessions` (новая строка; гейты от snapshot'а строки
  не зависят — §10.3, п.4).
- **EVAL-3c `bd21973c…`**: закрыта штатно как прерванная
  (`compute_gates + finish_evaluation_run`, аналог draft2) **после**
  правки T7.19 — в `noezema-eval3c` только один snapshot с head'ами
  (v2), дублей нет, расчёт корректен; 2 сессии из 50, гейты по малым
  N → insufficient_sample, overall insufficient_sample. БД
  `noezema-eval3c` остаётся уликой, данные прогона не менялись
  (кроме штатного закрытия строки рана).

## 11. T7.21 (2026-09-20) — следующий прогон требует НОВОЙ заморозки

T7.21 (ADR-0010) изменил поведение сессии и промпт explorer —
сопоставимость ЛЮБОГО следующего прогона с baseline EVAL-3d
(ADR-0008) сломана:

- `prompts/explorer.md` v3 → **v4** (правило 7: скачивать каждый
  названный вопросом источник до завершения; подстраховка — основной
  механизм хост-гейт, §3.7/§5.4/§11.2);
- хост-гейт на `complete` (`source_coverage_incomplete`, audit
  `complete_rejected`) — сессии, где модель пыталась завершить без
  скачивания второго источника, теперь на 1–2 шага длиннее;
- хост-пол бюджета `max_steps = max(configured, len(named)+3)`;
- новые audit-события: `named_sources`, `source_coverage` в report.

Следующий прогон (повторный корпус `question-set-v2.jsonl` или
EVAL-3e) обязан быть заморожен заново: новый код-коммит, prompt-
версии, строка freeze с перепроверкой хэшей (§2.1/§2.6). Замороженные
артефакты v2/v3-payload и корпус v2 T7.21 НЕ трогал — повторный прогон
ставится на те же вопросы, но по новому freeze. Решение о прогоне —
за пользователем (ADR-0008 §5; оценка ожидаемого исхода — ADR-0010 §6:
только T7.21 даёт 15–17 supported по гейту 2, проходимость N≥20 —
при добавлении T7.22-исправления окна фрагмента, 20–25).

## 12. T7.22 (2026-09-20) — окно assertion-фрагмента: вторая причина сломать сопоставимость

T7.22 (ADR-0011) изменил payload `source_assertion` и, с ним,
контекст куратора — это **вторая, независимая** причина (помимо §11)
не сопоставимости любого следующего прогона с baseline EVAL-3d:

- `assertion_text` в payload теперь несёт **до двух непересекающихся
  2000-символьных окон** (терминальное T7.16 + value-окно T7.22,
  разделитель «[…]»), а не одно: фрагмент до 4005 символов (до — 2000);
- фрагменты на страницах группы A (лид/инфобокс/виджет) — это ДРУГОЙ
  текст, чем в EVAL-3d (навигация/оглавление): куратор, который в
  EVAL-3d по правилу 5 НЕ привязывал второй источник (факта в
  фрагменте не было), в следующем прогоне увидит факт и привяжет —
  состав evidence и grade external/temporal claim'ов изменится
  (оценка: +6…7 E3 из группы A, ADR-0011 §6);
- промпты ролей (explorer v4, curator) — без изменений в T7.22;
  изменился только хост-контент промпта куратора (`# Evidence`).

Следующий прогон обязан быть заморожен заново (новый код-коммит,
строка freeze, перепроверка хэшей §2.1/§2.6). Замороженные
`config-v2`/`v3-payload.json` и `question-set-v2.jsonl` T7.22 НЕ
трогал — повторный прогон ставится на те же вопросы по новому
freeze. Идентичность evidence (§14.3) от фрагмента не зависит —
dedupe-семантика реретчей не изменилась (тест
`test_assertion_text_budget_is_explicit_and_identity_ignores_text`).
Решение о прогоне — за пользователем (ADR-0008 §5; оценка ожидаемого
исхода — ADR-0011 §6: T7.21+T7.22 → 22–24 supported по гейту 2,
проходимость N≥20 достигнута).

## 13. T7.23 (2026-09-20) — совместимость структурированного вывода с движком (halogen): третья причина сломать сопоставимость

T7.23 (ADR-0012) добавил `LLMGatewayConfig.schema_profile` (env
`NOEZEMA_LLM_SCHEMA_PROFILE`): `"none"` (по умолчанию — схема уходит
движку **байт в байт** как её произвёл pydantic, текущее поведение) и
`"halogen"` (снимает ровно то, что движок halogen отвергает —
`format`, `pattern` — из схемы, **уходящей движку**; валидация ответа
хостом — полной pydantic-моделью, uuid/date-time/все ограничения — НЕ
ослабляется). Замороженные `config-v2`/`v3-payload.json` и корпус v2
НЕ трогал: профиль живёт в env, а не в payload, и значение по
умолчанию даёт прежнее поведение на замороженных конфигах.

Смена модели — **самостоятельная** причина несопоставимости:

- SMOKE-HALOGEN гонялся на **halogen-flash-next** (Qwen3.8 Flash Next,
  `.hgn`, http://192.168.1.48:8080/v1), а не на qwen36-35b-a3b-q6-mtp.
  Любая смена модели ломает сопоставимость ЛЮБОГО следующего прогона с
  baseline EVAL-3d **сама по себе** (другие веса/бэкенд/поведение
  модели), независимо от schema_profile. Это **третья, независимая**
  причина (помимо §11 — T7.21, и §12 — T7.22).
- Профиль сам сопоставимость НЕ ломает: на qwen36-35b-a3b-q6-mtp с
  profile `"none"` (default) запрос байт в байт идентичен EVAL-3d
  (тест `test_default_profile_sends_schema_byte_identical`).
- На halogen curating требует profile `"halogen"` (иначе движок
  отвергает схему HTTP 400 — SMOKE-HALOGEN: claims=0, consolidating не
  запускался ни разу). Живая проверка (ADR-0012 §5): реальная схема
  `CuratorProposal` через transform → движок принял (HTTP 200) и
  вернул валидный JSON, `CuratorProposal.model_validate` OK.
- Отказ движка в схеме теперь отличим от «модель недоступна» в
  audit: `curator_error_kind = "request_rejected"` (мягкий отказ,
  сессия без знания, исследование не выбрасывается).

Следующий прогон обязан быть заморожен заново (новый код-коммит,
строка freeze, перепроверка хэшей §2.1/§2.6) **и** зафиксирована
модель + профиль движка. БД `noezema-smoke-halogen` — улика, SELECT
только. Решение о модели и о прогоне — за пользователем.
