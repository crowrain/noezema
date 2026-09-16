# EVAL-3 — замороженная конфигурация (готово, НЕ запущено)

Статус: **подготовка завершена, запуск — только после явного решения пользователя.**
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

- **v2** `config-v2-payload.json`, canonical sha256 `73b5f14e6630f086d2465a42308effe7d10e517ef39fe8b1ce2f563c417c1aaf`
  - `access_profile: curated`, tools: +`research.fetch`;
  - `research_proxy`: mode curated, searxng `http://127.0.0.1:8888`, private_allowlist `["127.0.0.1:8888"]`, max_response 4 MiB, timeout 10 c, rate 20/3600 c;
  - `model`: context 32768, max_output 8192, thinker-local;
  - `session_limits`: phase_deadline 1800, session_timeout 1800, max_explorer_steps 10;
  - `curiosity.selector: fifo`; repetition enabled (no_progress 2, rephrase 0.6);
  - `claim_type_rules`: external_fact/temporal_fact — allowed `source_assertion|quote_integrity`, min_grade_for_supported E3, min_independence_groups 2, min_support_evidence 2, requires_scope (+`requires_as_of` для temporal), volatility `configurable`; local_observation E2/1/1; computed_result E2/1/1.
- **v3** `config-v3-payload.json`, canonical sha256 `dbfd11b1a6fc83cf8695b44753448fce1049ce7614e2a1d40bad9a2ba92d1f62`
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
- Модель тёплая: thinker-local, `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192` (reasoning-бюджет ≥P99, AGENTS.md §7), `NOEZEMA_LLM_TIMEOUT_SECONDS=600`.

### 2.5 Допуски к запуску (правки по решению пользователя, после заморозки)

1. **Отчётность (не гейты):** во все ratio-гейты добавлен 95% доверительный интервал (Wilson) — ключ `ci95: {low, high}` в результате гейта и в печатной таблице `eval-run` (`packages/evaluation/gates.py: wilson_ci95`, `_gate`). Пороги, знаменатели и исходы не меняются (закреплено тестами: `tests/unit/test_wilson_ci.py`, `tests/scenario/test_evaluation_gates.py`).
2. **Выгрузка слепой выборки для ручной проверки:** `hostctl blind-sample --run <id|label> [--out file] [--fragment-chars N]` — тот же seeded/стратифицированный отбор, что меряют blind-гейты (общий код отбора: `packages/evaluation/blind.py: blind_sample_claim_ids`, используется и гейтами, и выгрузкой). Формат — §5 «Ограничение метода». Тест: `tests/scenario/test_blind_sample_dump.py` (совпадение выборки с измеренной + поля claim/evidence + фрагмент из content-addressed хранилища).
3. **Документ:** новый раздел §5 «Ограничение метода» (blind-гейты = структурная проверка; в отчёте рана вынесены в отдельный блок), пересчёт reuse-ожидания в §4, оговорка о хэшах в §8.

## 3. План запуска (после утверждения)

```
0. docker start noezema-searxng   (если остановлен); модель тёплая; тестовый контейнер БД жив
1. CREATE DATABASE noezema-eval3;  alembic upgrade head
   (NOEZEMA_DATABASE_URL=postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:54329/noezema-eval3)
2. env рана: NOEZEMA_DATABASE_URL=...noezema-eval3, NOEZEMA_DATA_ROOT=<свежая директория>,
   NOEZEMA_NODE_OWNER=eval3, NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192, NOEZEMA_LLM_TIMEOUT_SECONDS=600
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
10. **Context overflow** — max_output 8192 (reasoning), context 32768, страницы мелкие.

## 7. Baseline EVAL-2 (сравнение после рана)

50 attempts / 43 terminal / 7 LeaseLost (14%); significant_claim_reuse 1/28 — failed (единственный failed-гейт); near_duplicate 0/16; external_temporal_e3 и due_stale — insufficient_sample (sealed-профиль, нет source-backed claim); SLO не фиксировался в ране.

## 8. Что НЕ меняется

Пороги §22.2 (immutable), rules engine, SQL гейтов, ADR-0005/0006, staging/fenced-commit инварианты, EVAL-БД (закрыты), `ARCHITECTURE.md`. **Замороженные артефакты** (`config-v2/v3-payload.json`, `question-set-v2.jsonl`) — хэши, приведённые в §2, пересчитаны до и после допусков к запуску и **не изменились** (правки касались только отчётности: ci95 в гейт-таблице, blind-выгрузка, этот документ).
