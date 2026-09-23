# EVAL-4d: нулевое переиспользование значимых claims — разбор (T7.33, без изменения поведения)

- Дата: 2026-09-23.
- БД: `noezema-eval4d` (контейнер `noezema-test-db`, порт 54329,
 `noezema/noezema_dev`) — **улика, только SELECT**; строки ранов и
 сохранённые итоги не тронуты. `noezema-eval2`/`noezema-eval3d` не
 понадобились: данные ADR-0006/0008 (0/16 replay, 1/28) взяты из текстов.
- Контекст: гейт 5 `significant_claim_reuse` (§22.2,
 `ARCHITECTURE.md:2608`) провален во всех сериях: EVAL-1 1/29, EVAL-2
 1/28 (ADR-0006), EVAL-4d **0/29** (сохранённый результат
 `evaluation_runs.gates`, `ca5933c4-54e1-…`). ADR-0006 назвал корнем
 кросс-язычный промах `memory.search` (0/16 непустых replay), уточнением
 2026-09-16 переклассифицировал его как дефект механизма. В
 `retrieval.py` FTS теперь двуязычный (`ts_rank` по `russian` И
 `english`), а R стало ХУЖЕ (1/29 → 0/29). Этот документ находит
 фактическую причину по сохранённым данным.
- **В этой задаче поведение кода не меняется**: `packages/`, `apps/`,
 `hostctl/`, `tests/` не тронуты; пороги §22.2, `claim_type_rules`,
 `rules_hash`, замороженные payload'ы config-v2…v5 и корпуса v1–v4,
 `ARCHITECTURE.md`, строки ранов не тронуты. Это разбор и таблица
 вариантов — решение за пользователем.

## 1. Механизм переиспользования по спецификации и коду

### 1.1 Что меряет гейт 5

Спека (`ARCHITECTURE.md:2608`): «≥25% значимых claims
переиспользуются/перепроверяются в 20 сессиях». Реализация
(`packages/evaluation/gates.py:408-454`, `_gate_reuse`):

- знаменатель — значимые claims: head `current` в активном snapshot
 (`runtime_config_heads.active_config_snapshot_id`), `epistemic_status
 IN ('supported','disputed','refuted')`, `effective_grade` E2+
 (`gates.py:410-421`);
- «затронут» — claim входит в ≥2 РАЗЛИЧНЫЕ сессии через UNION четырёх
 веток (`gates.py:422-438`):
  1. `evidence.claim_id` + `evidence.created_in_session`;
  2. `claim_revisions.claim_id` + `claim_revisions.session_id`;
  3. `claim_dependencies.from_claim_id` + `created_in_session`;
  4. `claim_dependencies.to_claim_id` + `created_in_session`;
- числитель — `count(DISTINCT created_in_session) >= 2` на claim
 (`gates.py:439-444`). Порог 0.25, направление `at_least`, N<20 →
 `insufficient_sample` (`gates.py:447-454`, §22.2).

В EVAL-4d: знаменатель = 29 (19 temporal_fact E3, 6 external_fact E3,
3 local_observation E2, 1 computed_result E2; ещё 2 disputed E1 —
`f7138356`, `2fa5a1a9` — вне знаменателя), сохранённый результат
`0/29 failed, ratio 0.0, ci95 [0.0, 0.117]`.

### 1.2 Как claim'ы сшиваются (коммит)

- Дедуп на коммите — **побайтовое равенство `statement` И `claim_type`**
 (`packages/memory/service.py:348`: «1. claims (exact statement+type
 dedup against the corpus)»; запрос дедупа `service.py:396-414`). При
 совпадении новый claim НЕ создаётся — evidence новой сессии
 прикрепляется к существующей строке claim'а
 (`evidence.created_in_session` = сессия-фоллов-ап) — это единственный
 механизм-путь, по которому evidence-ветка гейта получает вторую
 сессию.
- Уточнение T7.9 (STATUS.md, раздел T7.9): дедуп переиспользует только
 claim'а, у которого есть head в snapshot сессии (headless-legacy — нет).
- Тип назначается при создании и «меняется только revision с
 обоснованием и новым assessment» (`ARCHITECTURE.md:1199`, §8.7) —
 таблица `claim_revisions` (`ARCHITECTURE.md:1909-1913`) — см. §2.2.
- Dependency-рёбра: модель ссылается на существующие claims по полному
 UUID из строк `[c:<uuid>]` раздела «Знание» контекст-пака
 (`prompts/curator.md:24-32`); хост проверяет UUID, target, цикл
 (`service.py:486-579`). Протокол требует ссылку явно
 (`apps/orchestrator/orchestrator.py`, `_protocol_text`): «Переиспользование
 знания: … связь с известным знанием обязательно фиксируй dependency'ем».

### 1.3 Identity: у evidence есть, у claim'а нет

- Evidence: `identity_hash` считается хостом из канонического
 content/provenance; `UNIQUE(evidence.claim_id, evidence.evidence_kind,
 evidence.identity_hash)` (§14.3, `ARCHITECTURE.md:2128`).
- Claim: в таблице `claims` (§14, `ARCHITECTURE.md:1900-1903`:
 `id, statement, claim_type, freshness_status, …`) identity-поля нет; в
 §8.2 (`ARCHITECTURE.md:1068-1088`) claim представлен `statement` +
 `claim_type` и lifecycle-полями. Естественный ключ claim'а в коде —
 точная пара (statement, claim_type). Спецификация identity claim'а
 **не определяет вовсе** (ни §8.2, ни §14, ни §14.3) — см. §4.5.

### 1.4 Поиск

- Контекст-пак и `memory.search` — один retrieval
 (`packages/cognition/retrieval.py`): гибридный FTS + significance +
 freshness. FTS двуязычный: `GREATEST(ts_rank(to_tsvector('russian',
 statement), plainto_tsquery('russian', q)), ts_rank(to_tsvector('english',
 search_statements), plainto_tsquery('english', q)))`
 (`retrieval.py:154-201`). `search_statements` — 1–2 английских
 варианта, которые модель заполняет для каждого предложенного claim'а
 (`_protocol_text`, orchestrator).
- Embeddings выключены: «Embeddings are off in the bootstrap config
 (`embeddings.enabled=False`); pgvector would be an ADR-gated extension,
 not part of v1» (`retrieval.py:9-10`).
- **Измеренный дефект документации (улика §3.4)**: docstring
 `retrieval.py:154-156, 216-220` утверждает, что частичные AND-совпадения
 ранжируются «~1e-20 = no match by design». На фактическом PostgreSQL
 15.17 (контейнер `noezema-test-db`) это НЕ так: для AND-запроса
 `ts_rank(vector, query)` возвращает значение уровня полного совпадения
 (0.06–0.1) при ≥2 общих лексемах и ~1e-20 при ≤1 (контрольные тесты —
 §3.4). SQL-фильтр `> 0` (`retrieval.py:189-197`) и порог
 `MIN_RELEVANCE = 1e-9` (`retrieval.py:216-220`) частичные совпадения с
 ≥2 лексемами НЕ отсекают — фактическая семантика recall'а: «claim
 делит с запросом ≥2 содержательных лексема». Отсюда ложноположительные
 попадания (улика: запрос о населении Земли вернул claim про ООН —
 §3.2, q12) и более широкий recall, чем задокументирован.

## 2. Три пути гейта 5 — срабатывания в EVAL-4d

Все три пути — **0** (подтверждено SQL, §5; сохранённый результат
`0/29`).

### 2.1 Путь «evidence»

Что должно произойти: follow-up-сессия коммитит evidence, привязанный к
существующему значимому claim'у. Единственный механизм-способ — точный
dedup statement+type: новая сессия формулирует statement побайтово как
якорный claim (и того же `claim_type`) → хост прикрепляет её evidence к
якорной строке → `evidence.created_in_session` даёт вторую сессию.

Срабатывания: **0**. В EVAL-4d 29 значимых claim'ов несут 54 evidence-
строки (25 claim'ов × 2 + 4 × 1), но каждая строка — из СОБСТВЕННОЙ
сессии создания claim'а; ни у одного значимого claim'а нет evidence из
≥2 сессий (SQL §5.1). Ни одна follow-up-сессия не сформулировала
statement побайтово.

### 2.2 Путь «revisions» (`claim_revisions`)

Что должно произойти: сессия вносит revision значимого claim'а (смена
`claim_type`/значения с обоснованием — `ARCHITECTURE.md:1199`),
`claim_revisions.session_id` даёт вторую сессию.

Срабатывания: **0 — по построению**: таблица `claim_revisions` ПУСТА во
всём ране (0 строк), и в кодовой базе **нет ни одного писателя** —
таблицу читают только два запроса гейтов (`gates.py:365, 427`), ORM-
модель определена (`packages/domain/models/memory.py:87-99`), но ни
`packages/`, ни `apps/`, ни `hostctl/` её не заполняют. Путь мёртв в v1:
даже при идеальном поведении модели он не сработает.

### 2.3 Путь «dependencies» (`claim_dependencies`)

Что должно произойти: follow-up-сессия объявляет dependency-ребро на
якорный claim, который значим (E2+ supported/disputed/refuted) — ребро
несёт `created_in_session` = сессия follow-up'а → якорь получает вторую
сессию (ребро может сработать и в обратную сторону — ветка `to_claim_id`).

Срабатывания: **0**. За весь ран рождено ровно ОДНО ребро:
`70ff7a38… → 387115c9…`, kind `research`, сессия `62586be8…`
(Python-якорь, 21:58). Разбор: from-сторона `70ff7a38` — значимый
(E3), но её `created_in_session` = `62586be8` = сессия, создавшая само
ребро → сессий = 1, вторая не добавлена; to-сторона `387115c9`
(«В предоставленном снимке страницы …/python314 указан пакет Python 3.14
версии 3.14.7», hypothesis E1) — **не значима** (E1) → гейт её не
считает. Единственное зафиксированное за весь ран «поиск → нашёл →
ссылка» (ADR-0006-механика) упёрлось в E1-claim.

Вывод §2: гейт 5 не провалился на «мало данных» — все 69 сессий
достигли успешного терминала (65 succeeded + 4 succeeded_partial,
0 failed), 54 claim'а, 29 значимых; провал — в том, что ни одна
значимая строка не получила второго касания, а из трёх путей один
(revisions) мёртв по построению, и для двух живых модель за весь ран
не произвела ни одного подходящего события.

## 3. Улика

### 3.1 Пользовалась ли модель поиском (аудит `action_started`)

Вызовов `memory.search` за серию: **17** — 14 `completed` + 3
`failed (policy:deny)`: модель передавала аргумент `limit`, отсутствующий
в схеме инструмента («argument ('limit',): Extra inputs are not
permitted») — сессии `edb22b39`, `e863aa40`, `904931f5`. Результаты
вызваны из `action_completed` в аудите (поле `data` — то, что модель
реально получила в момент поиска):

| # | сессия (роль) | запрос (язык) | результат В МОМЕНТ | replay финальный snapshot |
|---|---|---|---|---|
| q01 | ca2ac4b5 (Python old-as_of) | `source_assertion 167f483323c0 Download Python \| Python.org 3.14.7` (en) | пусто | **3** (70ff7a38, 387115c9, d270b601) |
| q02 | 62586be8 (Python-якорь) | `Download Python 3.14.7 latest stable Python.org Chocolatey python314` (en) | 1 (387115c9 E1) | **3** |
| q03 | 904931f5 (todo FU) | `notes/todo.md выполненные задачи` (ru) | пусто | **1** (cf3dbff0 — создан позже) |
| q04 | 904931f5 (todo FU) | `todo.md` (файл) | пусто | 0 |
| q05 | 904931f5 (todo FU) | `todo.md Задача 1` (ru) | пусто | 0 |
| q06 | 7add23b6 (Python FU) | `Python 3.14.7` (наим.) | **3** (70ff7a38 E3 + 2) | 3 |
| q07 | c7e1a7ac (население FU) | `население Земли оценка 8.3 миллиарда world population estimate 2026` (ru+en) | пусто | **2** (d6f1c286, 7f27582d — позже) |
| q08 | edb22b39 (метро FU) | `Рублёво-Архангельская линия первый участок открыто станций` (ru) | пусто | **1** (6cc8a0d8 — та же сессия) |
| q09 | edb22b39 (метро FU) | `Рублево-Архангельская линия метро` (ru) | пусто | 1 |
| q10 | 98836b42 (glossary FU) | `notes/glossary.md терминов определено glossary terms count` (ru+en) | пусто | 0 |
| q11 | 98836b42 (glossary FU) | `glossary.md 2 два термина первый термин гипотеза` (ru) | пусто | 0 |
| q12 | 3f0f3489 (население FU) | `актуальное число населения Земли 8.3 миллиарда 2026 current world population 8.3 billion` (ru+en) | 1 — **07d655cf (ООН 193, чужая тема)** | **4** |
| q13 | 4f26ac38 (Бразилия) | `Президент Бразилии` (ru) | пусто | 0 |
| q14 | e9595a91 (АТЭС) | `АТЭС 21 экономика APEC 21 economies` (ru+en) | пусто | 0 |

Языки запросов: 6 ru (q03, q05, q08, q09, q11, q13), 4 ru+en
(q07, q10, q12, q14), 2 en (q01, q02), 1 filename (q04), 1
product-name (q06).

Итог: **14 завершённых вызовов из 10 сессий**; 3/14 непустых в момент
поиска, **8/14 непустых в replay против финального snapshot**
(EVAL-2: 0/16 — ADR-0006). Кросс-язычный корень, названный ADR-0006,
**закрыт**: английские запросы (q02, q06) и смешанные (q07, q12, q14)
матчат русские statement'ы через `english`-вектор `search_statements` и
через `russian`-вектор (латиница проходит парсер). Из 11 пустых в
момент поиска 8 были «пусты по праву»: матчащий claim ещё не существовал
(q01 — claim рождается в той же сессии; q03/q07/q08/q09 — якорные
сессии не создали claim'ов, §3.3; q10/q11 — в glossary-паке claim'ов не
было вовсе). Поиск перестал быть корнем; причина ниже по течению.

### 3.2 Что делали follow-up'ы: коммитное поведение

Из 69 сессий **28 (40%) не записали ни одной staging-операции**
(`staging_recorded` = 0): 13 — куратор предложил НУЛЬ операций
(чистое поведение модели), 13 — предложения **отклонены rules engine**
(`session_state_changed` → `curator_rejected_by_rules`, T7.9 pre-check),
1 — `curator_error` (схема). Разбор по 33 сессиям 11 паков — **22 из 33
(64%)**:

- **13 follow-up'ов предложили ноль операций** (4d510a51, 848106dd,
 4dd12dfa, 7add23b6, 46bec75c, 4d67e817, 7871250d, de3f809a, 70235790,
 5cc41d0a, c3537eb3, 3b2a4a7d, 8c370d7f) — при том что у части из них
 якорный claim БЫЛ виден в контекст-паке (таблица §3.5) и/или найден
 `memory.search` (7add23b6 — q06, 3 хита, включая E3-якорь 70ff7a38).
- **8 сессий отклонены rules engine** — систематическая пара
 «claim type ↔ evidence kind» у halogen:
 `computed_result ← local_observation` (×6: 511ebff0, 4a6589a8,
 5a6cfc44, 98836b42, 6d599d64, 904931f5), `temporal_fact ← computation`
 (3c595fec), `computed_result ← source_assertion` (c7e1a7ac). Правила
 заморожены (`claim_type_rules` config-v4: computed_result требует
 `computation`; local_observation — `local_observation`; temporal_fact —
 `source_assertion|quote_integrity`), модель предлагает «файл содержит N
 строк» как computed_result с evidence-наблюдением — и всё предложение
 отбрасывается целиком (T7.9: pre-commit отбой, staging пуст).
- **1 сессия — curator_error** (ebc99390, метро-якорь): модель ссылалась
 на существующий claim **усечённым 16-символьным UUID**
 (`6e70379ee1f52284`) — схема CuratorProposal отвергала 3 раза,
 предложение потеряно целиком.

Ни в одной из 22 сессий гейт не мог сработать: нет ни claim'а, ни
evidence, ни ребра — все три пути пусты по входу.

### 3.3 Таблица 11 паков «якорь + follow-up»

Корпус v3: 11 паков = 11 якорей (приоритет 90) + 22 follow-up'а
(приоритет 80), порядок FIFO подтверждён датами сессий (каждый якорь
ранее обоих своих follow-up'ов). «Класс промаха» по постановке T7.33:
(а) перефраз того же факта, (б) другой claim_type, (в) follow-up не
родил claim вовсе, (г) claim не значимый, (д) иное.

| # | Пак | Якорь (сессия → claim, type, grade, statement дословно) | Follow-up'ы (сессия → claim) | Класс |
|---|---|---|---|---|
| 1 | ООН | d8feee43 → 07d655cf, temporal_fact E3: «По состоянию на 2026-06-15 число государств-членов ООН составляло 193.» | e3c2e95c → bd0cbc5b, **external_fact** E3: «По состоянию на 2026 год число государств-членов ООН составляет 193.»; 4dd12dfa → (0 операций) | **(а)+(б)** — перефраз того же факта И другой claim_type; dedup невозможен в принципе; обязательный dependency (протокол) не объявлен |
| 2 | ЕС | ed316e0b → 80c1908c, temporal_fact E3: «По состоянию на 15 июня 2026 года Европейский союз состоит из 27 стран-членов.» | 46bec75c → (0); 8c370d7f → (0) | **(в)** — ни один follow-up не родил claim (оба — 0 staging) |
| 3 | Python | 62586be8 → 70ff7a38, temporal_fact E3: «По состоянию на 2026-08-05T00:00:00Z последней стабильной (bugfix, не pre-release) версией Python является 3.14.7.» | 7add23b6 → (0; нашёл якорь поиском, q06); 7871250d → (0) | **(в)** — follow-up'ы не родили claim, хотя якорь был виден (поиск + контекст) |
| 4 | PostgreSQL | 506db3a4 → c701f374, temporal_fact E3: «По состоянию на 2026-08-13 последней стабильной версией PostgreSQL является 18.6.» | 4d67e817 → (0); de3f809a → (0) | **(в)** |
| 5 | Ключ. ставка ЦБ | 0db5b4b0 → 426fe04a, temporal_fact E3: «Ключевая ставка Банка России на 18 сентября 2026 года составляет 14,00% годовых.» | 70235790 → (0); 5cc41d0a → (0) | **(в)** |
| 6 | Население Земли | 3c595fec → (claim'ов нет; отклонено rules: temporal_fact ← computation) | c7e1a7ac → (0; отклонено rules: computed_result ← source_assertion); 3f0f3489 → d6f1c286 external_fact E3: «По данным Worldometer и Wikipedia, население Земли в 2026 году составляет примерно 8.3 млрд человек.» + 7f27582d temporal_fact E1 | **(д)** — якорь не родил claim (rules); follow-up-claim значимый, но затронут 1 сессией |
| 7 | Рублёво-Арх. | ebc99390 → (claim'ов нет; curator_error: UUID 16 симв. в dependency, схема ×3) | edb22b39 → 6cc8a0d8 temporal_fact E3: «По состоянию на 5 сентября 2026 года на первом участке Рублёво-Архангельской линии метро открыто пять станций: …»; 3b2a4a7d → (0) | **(д)** — якорь не родил claim (ошибка схемы) |
| 8 | plan.md | 511ebff0 → (claim'ов нет; отклонено rules: computed_result ← local_observation) | 9fb04cb8 → 5a37ce68 local_observation E2: «В файле notes/plan.md записано 3 пункта.»; c3537eb3 → (0) | **(д)** — якорь не родил claim (rules) |
| 9 | reading.md | 739c434e → 412234b4 computed_result E2: «Количество непустых строк в файле /home/denis/dsh1/eval4d-data/workspace/notes/reading.md равно 3.» + d3475eca local_observation E2: «Файл notes/reading.md содержит ровно три непустые строки: '1984', 'Война и мир', 'Преступление и наказание'.» | 4d510a51 → (0); 848106dd → (0) | **(в)** |
| 10 | todo.md | 4a6589a8 → (claim'ов нет; отклонено rules: computed_result ← local_observation) | 904931f5 → (0; отклонено rules: computed_result ← local_observation); 09281a15 → cf3dbff0 local_observation E2: «В файле notes/todo.md одна задача отмечена выполненной ([x]) и две задачи не выполнены ([ ]), всего три задачи.» | **(д)** — якорь не родил claim (rules); из 2 FU: 1 отклонён rules, 1 родил claim (якорь-контрагент отсутствует) |
| 11 | glossary.md | 5a6cfc44 → (claim'ов нет; 2 отклонения rules: computed_result ← local_observation, local_observation ← computation) | 98836b42 → (0; отклонено rules); 6d599d64 → (0; отклонено rules) | **(д)** — все 3 сессии отклонены rules: 0 claim'ов в паке |

Агрегат: (в) — 5 паков (2, 3, 4, 5, 9); (а)+(б) — 1 пак (1); (д) — 5
паков (6, 7, 8, 10, 11); (г) — 0 (все рождённые follow-up-claim'ы
значимы E2/E3, кроме 7f27582d E1, у которого контрагент-якорь отсутствует
вовсе). Побайтового совпадения statement в любом паке нет: единственная
пара «якорь + follow-up-claim» (пак 1) различается и формулировкой
(«…число государств-членов ООН составляло 193.» vs «…число
государств-членов ООН составляет 193.» + сдвиг даты «2026-06-15» →
«2026 год»), и типом (temporal_fact → external_fact).

Тот же факт, три строки (пак 1, включая old-as_of-предшественника):
d87fb075 (1c647376, 21:34): «По состоянию на 15 апреля 2026 года в ООН
входило 193 государства-члена.» (temporal E3); 07d655cf (d8feee43):
«По состоянию на 2026-06-15 число государств-членов ООН составляло
193.» (temporal E3); bd0cbc5b (e3c2e95c): «По состоянию на 2026 год
число государств-членов ООН составляет 193.» (external E3). Все три —
разные statement'ы → три отдельные строки → каждая затронута 1 сессией.
Аналогично ЕС: f2d5561d (4332a0eb): «По состоянию на 1 января 2026 года
Европейский союз состоял из 27 стран-членов.» + 80c1908c (ed316e0b) —
две строки, две сессии, ни одна не «переиспользована». Корпус сработал
по дизайну (один и тот же факт переспрашивается); механизм не сработал:
сшить строки он умеет только побайтово.

### 3.4 Replay + измеренная семантика ts_rank

Replay (SQL §5.2, тот же запрос, что в `retrieval.py`, финальный
snapshot — верхняя граница: в момент поиска corpus был меньше): **8/14
непустых** против 0/16 в EVAL-2 (ADR-0006). Кросс-язычный промах закрыт.

Измерено на PostgreSQL 15.17 (контейнер `noezema-test-db`), контроль
`simple`/`russian` конфигами:

| запрос vs вектор | общих лексем | `@@` | `ts_rank` |
|---|---|---|---|
| `python & 3.14.7` vs chocolatey-claim | 2/2 | true | 0.09735848 |
| `python & download` | 1/2 | false | 1e-20 |
| `python & download & 3.14.7` | 2/3 | **false** | **0.09735848** |
| `кот & жираф & слон` vs «кот ест мышь» | 1/3 | false | 1e-20 |
| `кот & мышь & жираф` | 2/3 | **false** | **0.098500855** |
| q12 (население) vs «…государств-членов ООН…193» | 2+ | **false** | **0.09735848** |

Вывод: порог «нет совпадения» в этом PG — **≤1 общий лексем**; при ≥2
`ts_rank` даёт значение уровня полного совпадения, и ни SQL-фильтр `> 0`,
ни порог 1e-9 его не отсекают. Последствия: (1) документация
`retrieval.py:154-156, 216-220` («partial AND matches rank ~1e-20 = no
match by design») ложна для задеплоенного PG — ловушку зафиксировать
(AGENTS.md §7); (2) recall шире заявленного: ложноположительные
попадания по общим лексемам (q12 → ООН-claim в ответе модели); (3) на
гейт 5 это влияет косвенно — поиск не связывающее ограничение (§4).

### 3.5 Якорь был ВИДЕН follow-up'ам (контекст-пак)

Реплей retrieval против корпуса на момент старта каждой follow-up-сессии
(SQL §5.3; значимые claim'ы, рождённые ранее, `relevance ≥ 1e-9`):

Точные совпадения (top-claim'ы, `rel = ts_rank`):

| сессия (пак) | видимые claim'ы (rel) |
|---|---|
| 7add23b6 (Python FU) | **6**: d270b601 (0.786), c0b987e9 (0.464), 387115c9 (0.424), **якорь 70ff7a38 (0.407)**, c701f374 (0.268), f7138356 (0.097) |
| 4d67e817 (PG FU) | **6**: **якорь c701f374 (0.464)**, c0b987e9 (0.268), 70ff7a38 (0.239), d270b601, f7138356, 387115c9 |
| 7871250d (Python FU) | **6**: d270b601 (0.868), **якорь 70ff7a38 (0.573)**, c0b987e9 (0.464), c701f374 (0.461), 387115c9 (0.254), f7138356 |
| de3f809a (PG FU) | **5**: **якорь c701f374 (0.644)**, 70ff7a38 (0.391), c0b987e9, d270b601, f7138356 |
| 46bec75c (ЕС FU) | **3**: **якорь 80c1908c (0.772)**, f2d5561d (0.772), 07d655cf (0.097) |
| 8c370d7f (ЕС FU) | **4**: **якорь 80c1908c (0.268)**, f2d5561d (0.268), 07d655cf (0.097), bd0cbc5b (0.097) |
| 4dd12dfa, e3c2e95c (ООН FU) | по **2**: **якорь 07d655cf (0.644)**, d87fb075 (0.259) |
| 70235790, 5cc41d0a (CBR FU) | по **1**: **якорь 426fe04a (0.464)** |
| 4d510a51, 848106dd (reading FU) | по **1**: якорный d3475eca (0.099) |
| c3537eb3 (plan FU) | **1**: 5a37ce68 (0.264) — claim ПЕРВОГО follow-up'а того же пака (якоря нет) |
| 3b2a4a7d (метро FU) | **1**: 6cc8a0d8 (0.933) — claim ПЕРВОГО follow-up'а того же пака (якоря нет) |
| 904931f5, c7e1a7ac, edb22b39, 9fb04cb8, 98836b42, 6d599d64, 09281a15, 3f0f3489 | **0** — в корпусе не было claim'а, с которым можно было бы сойтись (якорь не создал/ещё не создал) |

Информация для переиспользования была у модели (якорь в разделе
«Знание» с `[c:<uuid>]`, протокол требует dependency), механизм поиска
работал — и модель 13 раз не предложила ничего, 1 раз — с обрезанным
UUID, 8 раз — несовместимую пару type/evidence. Два FU→FU случая
(c3537eb3, 3b2a4a7d) — самое сильное подтверждение слоя 1: целевой
claim (claim первого follow-up'а того же пака) был виден с высоким
рангом (0.264/0.933) — и сессия снова не закоммитила ничего.

## 4. Связывающее ограничение и варианты

### 4.1 Связывающее ограничение — что именно

> **Уточнено §7 (2026-09-23, после коммита T7.33).** «Follow-up'ы не
> коммитят знание» верно как факт, но не как атрибуция: 13 сессий
> «предложил ноль» — переиспользование, которое протокол не может
> записать (нет операции «перепроверил существующий claim»). Текст
> ниже сохранён как есть.

Два слоя, оба подтверждены данными:

- **Слой 1 (доминирующий) — follow-up'ы не коммитят знание.** 22 из 33
 паковых сессий (13 «предложил ноль» + 8 «отклонено rules» + 1
 «curator_error») не создали ни одного события любого из трёх путей
 гейта. Для этих сессий НИКАКОЕ улучшение сшивания/поиска не помогает —
 сшивать нечего. 8 из 11 пустых поисков в момент поиска были пусты по
 праву (якорный claim не существовал — его не создала якорная сессия).
- **Слой 2 (шов) — сшивка на коммите только побайтовым statement+type
 или явным dependency на значимый claim.** Подтверждено на единственном
 связываемом случае (пак 1, ООН): follow-up-claim — перефраз + другой
 claim_type → dedup невозможен; обязательный dependency не объявлен
 (хотя якорь был виден в контексте и находился поиском). Единственное
 объявленное за ран ребро (62586be8 → 387115c9) указало на E1-claim и
 гейтом не посчитано.

**Гипотеза «exact statement dedup — связывающее ограничение»:
подтверждена на уровне шва, недостаточна как полная картина.** Пересчёт
«что было бы» на сохранённых строках (идеальный fact-identity,
без переписывания итогов): максимум связывания — пак 1 (ООН: 3 строки /
3 сессии) и ЕС (2 строки / 2 сессии) — общие evidence identity
`84abcf4730…` (ООН, 3 claim'а) и `da578f37…`/`ccd036f7…` (ЕС, 2
claim'а) показывают, что хостовая identity этого факта УЖЕ существует на
уровне evidence (§14.3). Любое другое связывание невозможно: в остальных
паках строка-контрагент отсутствует (якорь/фоллов-ап не коммитили).
Итог идеального identity на сохранённых данных: числитель 2,
знаменатель 26 → **2/26 = 0.0769 → failed** (с claim_type в ключе:
2/27 = 0.0741; с датой as_of: 0/29). То есть даже совершенное
identity-сшивание не спасает сохранённый ран — потому что слой 1.

### 4.2 Есть ли у claim'а identity — и пробел ли это

У claim'а identity **нет ничего, кроме точного (statement, claim_type)**:
`claims` (§14, `ARCHITECTURE.md:1900-1903`) — `id, statement, claim_type,
…` без identity-поля; дедуп-ключ — `service.py:348, 396-414`; спека
identity claim'а не определяет нигде. У evidence identity есть
(`identity_hash`, `UNIQUE(claim_id, evidence_kind, identity_hash)`,
§14.3, `ARCHITECTURE.md:2128`). Это **дизайн-пробел, а не нарушение
спеки**: спека молчит, и молчание — последствие позиции «statement —
утверждение модели, хост его не переписывает» (та же позиция, что
у ADR-0016, «известные ограничения»: хост не переписывает
free-form-вход модели). По классу это тот же дефект, что закрыли
ADR-0007 (scope: вход гейта производила модель) и ADR-0016 (as_of):
вход, определяющий измерение гейта 5 (идентичность claim'а),
производится модельным free-text, а хост умеет сравнивать его только
побайтово. Для гейта 5 это означает: «переиспользование» меряется
строковым совпадением, которое свободная формулировка модели не
гарантирует — при свободной формулировке переиспользование через
dedup-путь невозможно в принципе (класс (а) таблицы §3.3).

### 4.3 Варианты решения (решение за пользователем; ничего не реализовывать)

Пересчёты — «что было бы» на строках `noezema-eval4d` (SELECT);
сохранённый итог `0/29` не пересчитан и не переписан.

**(i) Ничего не менять; гейт 5 признать неизмеряемым на этой модели и
зафиксировать.**
- Что меняется: только документ (этот разбор + STATUS.md + пометка в
 ADR-0006, как сделано в T7.33); код, пороги, payload'ы — не тронуты.
- Числа: сохранённый g5 = 0/29 failed (остаётся); следующий ран:
 предрегистрация EVAL-5 (EVAL-5-freeze §3.2) — failed при наблюдаемом
 R = 0, но арифметически проходим (R ≤ 10, пул 28–34 ≤ 4R = 40;
 10/28…10/34 = 0.294–0.357; проходит при ~8/10 совпадений).
- Риски: g5 остаётся конгломератом «модель коммитит» × «хост сшивает»;
 без разложения (слои 1/2) провал следующего рана будет повторно
 атрибутирован поиску (как в ADR-0006). Замер «способности
 переиспользовать» на halogen не состоится до тех пор, пока модель не
 коммитит follow-up'ы.

**(ii) Identity для claim'а (нормализация statement / ключ факта /
host-деривация — паттерн ADR-0007 для scope, ADR-0016 для as_of: вход
гейта производит модель, хост делает его каноническим).**
- Что меняется в коде: поле-identity или производный ключ на claim
 (миграция), дедуп-запрос `service.py:396-414` (ключ = identity вместо
 statement+type), retrieval/тесты; `claim_type_rules` и `rules_hash` —
 НЕ трогает; замороженные payload'ы — не трогает; **ARCHITECTURE.md —
 трогает** (таблица `claims` §14 + §14.1 — требуется решение
 пользователя, AGENTS.md §4). Сопоставимость: новый baseline (как
 T7.30/ADR-0016); сохранённые прогоны не переоцениваются.
- Числа на сохранённых данных (идеальный ключ): факт-ключ без
 claim_type и as_of → **2/26 = 0.0769 → failed**; с claim_type →
 2/27 = 0.0741 → failed; с датой → 0/29 → failed. Потолок = 2
 (паки 1 и 2): в остальных паках нет строки-контрагента.
- Риски: host-ключ факта по free-form statement — NLP-задача (класс,
 который ADR-0007/0016 обошли, привязав хостовый вход к ВОПРОСУ, а не к
 тексту модели); неверный ключ сшивает разные факты; при сшивании
 теряется история as_of (частично компенсируется: reverify_after
 пере-деривируется на каждом коммите — ADR-0016/0017).

**(iii) Сшивка через evidence identity (§14.3) или dependency-рёбра
вместо statement.**
- (a) Evidence identity: на коммите новый claim, чей support-evidence
 имеет identity_hash, совпадающий с evidence существующего значимого
 claim'а (тот же source+диапазон+kind — тот же фрагмент источника),
 считается переиспользованием. Код: дедуп-ветка `service.py` + индекс по
 identity_hash; rules_hash — не трогает; payload'ы — не трогает;
 ARCHITECTURE.md — добавка в §14.1/§14.3 (решение пользователя).
 Числа: ровно тот же потолок, что (ii), — общие identity
 `84abcf4730…` (ООН: 3 сессии) и `da578f37…`/`ccd036f7…` (ЕС: 2 сессии)
 → **2/26 = 0.0769 → failed** на сохранённых данных. Риски: гранулярность
 identity = фрагмент источника, а не факт — два разных факта из одного
 фрагмента склеятся; работает только когда follow-up цитирует тот же
 фрагмент (в корпусе v3/v4 это гарантировано: 2 источника на пак).
- (b) Dependency-рёбра: механизм СУЩЕСТВУЕТ и уже считается гейтом
 (путь 3, §2.3); протокол требует его явно; модель дала 1 ребро (на E1).
 Код: ничего (или усиление промпта — новый payload → protocol_hash,
 сопоставимость нового рана с EVAL-4d ломается). Числа на сохранённых
 данных: 0 (новых рёбер нет). Следующий ран: потолок = соблюдение
 протокола моделью — самый дешёвый зонд слоя 1.

**(iv) Embeddings / pgvector.**
- Честная оценка: **отдельная веха, не v1** — `retrieval.py:9-10`
 («pgvector would be an ADR-gated extension, not part of v1»); в стеке
 нет embedding-модели (llama-swap), pgvector не установлен (тестовый
 контейнер), нужен ADR + infra + новый payload
 (`embeddings.enabled`). Числа на сохранённых данных: **0/29 без
 изменений** — контекст-пак и так подавал якорь follow-up'ам (§3.5:
 6/6/6/5/4/3/2/2 совпадения), а 7 follow-up'ов с нулём совпадений в
 основном не имели чего искать (якорь не создал claim'а) — embeddings не
 воскрешают отсутствующую строку. Эффект возможен только в будущих
 ранах и только на recall-хвосте (q01-типа «кухонные» запросы,
 ложноположительные q12-типа).

**(v) Корпусное — формулировки follow-up. СТРОГО о подгонке.**
- Follow-up'ы v3 и так просят переиспользовать: «В прошлой сессии было
 установлено … Перепроверь это утверждение строго по тем же двум
 источникам». Движок провала — «коммитит ноль», а не «не понимает, что
 просит вопрос»: ни формулировка, ни поиск этого не исправят.
- Линия подгонки: запросить «перепроверь прежний claim» = легитимный
 сценарий переиспользования; довести вопрос до «повтори claim дословно,
 слово в слово» = **подгонка под механизм** (замер превращается в
 «следование инструкции копирования строки», а не в переиспользование
 знания) — гейт 5 меряет «переиспользуется/перепроверяется в 2
 сессиях» (§22.2), и строковое сравнение — его текущая реализация, но не
 его смысл. Корпус v1–v4 — заморожены, не трогать; изменение — только
 новый корпус под новым решением пользователя.
- Числа: на сохранённых данных — 0/29 без изменений; в будущем рани
 потолок тот же, что у (vi.1) (модель должна начать коммитить).

**(vi) Иное.**
- (vi.1) Промпт: пара «claim type ↔ evidence kind» (curator-v4: «счёт
 строк/содержимое файла → local_observation-claim с local_observation-
 evidence; computed_result требует computation-evidence; local_observation
 не подтверждается source_assertion; temporal_fact не подтверждается
 computation»). Меняется: только промпт → новый payload → protocol_hash
 нового рана меняется (сопоставимость с EVAL-4d — новая baseline),
 rules_hash/пороги/ARCHITECTURE.md — нет. Числа: сохранённые данные —
 0/29 без изменений; следующий ран — разблокирует 8 отклонённых
 паковых сессий (якоря: plan, todo, население, glossary; FU: todo-a,
 glossary-a/b, население-a) → якоря рождают claim'ы → у follow-up'ов
 появляется, что переиспользовать → g5 становится измеримым. Это прямой
 удар по слою 1.
- (vi.2) Host-префикс-разрешение усечённых UUID (16-символьный
 hex-префикс → единственный claim): `service.py:507-512` (dependency
 resolution); rules_hash — нет; ARCHITECTURE.md — нет (деталь
 поведения). Числа: сохранённые — 0/29 (строки уже записаны); следующий
 ран — метро-якорь (ebc99390) коммитится, пак 7 становится связываемым.
- (vi.3) Host-путь «перепроверки»: follow-up-вопрос, ссылающийся на
 прежний claim, хост связывает автоматически (question→claim resolution
 без участия модели). Большое изменение: семантика §14.1 (кто создаёт
 dependency-рёбра), вход гейта, оркестратор — не v1, отдельная задача.

### 4.4 Итоговая таблица вариантов

| # | Вариант | rules_hash | payload'ы | ARCHITECTURE.md | сопоставимость | g5 на данных EVAL-4d (what-if) | риск |
|---|---|---|---|---|---|---|---|
| i | ничего | нет | нет | нет | полная | 0/29 (сохранённый) | повторная ложная атрибуция провала поиску |
| ii | identity claim'а | нет | нет | **да** (§14/§14.1) | новая baseline | ≤ 2/26 = 0.0769 → failed | NLP-ключ по free-text; склейка разных фактов; потеря as_of-истории |
| iii.a | evidence identity | нет | нет | **да** (§14.1/14.3) | новая baseline | ≤ 2/26 = 0.0769 → failed | гранулярность = фрагмент источника, не факт |
| iii.b | dependency (усиление промпта) | нет | **да** (новый) | нет | новая baseline | 0 (новых рёбер нет) | потолок = соблюдение моделью |
| iv | embeddings/pgvector | нет | **да** | **да** | новая baseline + веха | 0/29 | отдельная веха (infra, ADR, модель эмбеддингов) |
| v | корпус (дословность) | нет | нет | нет | **подгонка** при «дословно» | 0/29 | меряет копирование, а не переиспользование |
| vi.1 | промпт type↔evidence | нет | **да** (новый) | нет | новая baseline | 0/29 (но слой 1 разблокирован для будущего) | требует нового рана для проверки |
| vi.2 | UUID-префиксы | нет | нет | нет | полная (деталь) | 0/29 | мало; +1 связываемый пак в будущем |
| vi.3 | host-перепроверка | нет | — | **да** (§14.1) | новая baseline | 0/29 | большой дизайн-сдвиг, не v1 |

## 5. Вспомогательные SQL (все — SELECT по `noezema-eval4d`)

### 5.1 Три пути гейта 5 (пересчёт сохранённого 0/29)

```sql
WITH cur AS (
  SELECT h.claim_id, a.effective_grade
  FROM claim_assessment_heads h
  JOIN claim_assessments a ON a.id = h.current_assessment_id
  WHERE h.assessment_state = 'current'
    AND h.epistemic_status IN ('supported','disputed','refuted')
    AND h.config_snapshot_id = (SELECT active_config_snapshot_id
         FROM runtime_config_heads WHERE scope='global')
),
significant AS (
  SELECT claim_id FROM cur
  WHERE (CASE COALESCE(effective_grade,'E0')
         WHEN 'E0' THEN 0 WHEN 'E1' THEN 1 WHEN 'E2' THEN 2
         WHEN 'E3' THEN 3 WHEN 'E4' THEN 4 ELSE 0 END) >= 2
),
refs AS (
  SELECT claim_id, created_in_session, 'evidence' AS path FROM evidence
  WHERE claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
  UNION SELECT claim_id, session_id, 'revisions' FROM claim_revisions
  WHERE claim_id IN (SELECT claim_id FROM significant) AND session_id IS NOT NULL
  UNION SELECT from_claim_id, created_in_session, 'dep_from' FROM claim_dependencies
  WHERE from_claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
  UNION SELECT to_claim_id, created_in_session, 'dep_to' FROM claim_dependencies
  WHERE to_claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
)
SELECT path, count(*) AS ref_pairs, count(DISTINCT created_in_session) AS sessions
FROM refs GROUP BY path;
-- evidence: 29 пар (25 claim'ов × 2 строки + 4 × 1), 28 сессий; dep_from: 1 (70ff7a38, 62586be8);
-- revisions: пусто; dep_to: пусто (387115c9 — E1, не значим).
-- Ни один значимый claim не имеет ≥2 сессий:
SELECT e.claim_id, count(DISTINCT e.created_in_session)
FROM evidence e WHERE e.claim_id IN (SELECT claim_id FROM significant)
AND e.created_in_session IS NOT NULL
GROUP BY e.claim_id HAVING count(DISTINCT e.created_in_session) >= 2; -- 0 строк
```

Знаменатель: `SELECT count(*) FROM significant;` → 29 (19 temporal E3 +
6 external E3 + 3 local E2 + 1 computed E2). Сохранённый результат:
`SELECT gates->'significant_claim_reuse' FROM evaluation_runs;` →
`{"numerator": 0, "denominator": 29, "ratio": 0.0, "outcome":
"failed", …}`.

### 5.2 Вызовы memory.search (аудит + действия)

```sql
SELECT a.id, a.session_id, a.started_at, a.state, a.error_code,
  (SELECT (payload->>'arguments')::jsonb->>'query' FROM audit_events x
   WHERE x.type='action_started' AND x.payload->>'action_id'=a.id::text LIMIT 1) AS query,
  (SELECT payload->>'data' FROM audit_events x
   WHERE x.type='action_completed' AND x.payload->>'action_id'=a.id::text LIMIT 1) AS result
FROM actions a WHERE a.tool='memory.search' ORDER BY a.started_at NULLS LAST;
-- 14 completed + 3 policy:deny («argument ('limit',): Extra inputs are not permitted»).
```

Replay (финальный snapshot; «непустой» = ≥1 строки с effective
relevance ≥ 1e-9, порог `MIN_RELEVANCE` из `retrieval.py`):

```sql
WITH queries AS (SELECT * FROM (VALUES
  ('q01','source_assertion 167f483323c0 Download Python | Python.org 3.14.7'),
  ('q02','Download Python 3.14.7 latest stable Python.org Chocolatey python314'),
  ('q03','notes/todo.md выполненные задачи'), ('q04','todo.md'),
  ('q05','todo.md Задача 1'), ('q06','Python 3.14.7'),
  ('q07','население Земли оценка 8.3 миллиарда world population estimate 2026'),
  ('q08','Рублёво-Архангельская линия первый участок открыто станций'),
  ('q09','Рублево-Архангельская линия метро'),
  ('q10','notes/glossary.md терминов определено glossary terms count'),
  ('q11','glossary.md 2 два термина первый термин гипотеза'),
  ('q12','актуальное число населения Земли 8.3 миллиарда 2026 current world population 8.3 billion'),
  ('q13','Президент Бразилии'),
  ('q14','АТЭС 21 экономика APEC 21 economies')
) v(tag,q))
SELECT q.tag, count(*) FILTER (WHERE r.rel >= 1e-9) AS effective_hits
FROM queries q
CROSS JOIN LATERAL (
  SELECT GREATEST(
    ts_rank(to_tsvector('russian', c.statement), plainto_tsquery('russian', q.q)),
    ts_rank(to_tsvector('english',
      coalesce((SELECT string_agg(s,' ') FROM jsonb_array_elements_text(
        coalesce(c.search_statements,'[]'::jsonb)) s),'')),
      plainto_tsquery('english', q.q))
  ) AS rel
  FROM claims c
  JOIN claim_assessment_heads h ON h.claim_id=c.id
    AND h.config_snapshot_id=(SELECT active_config_snapshot_id
         FROM runtime_config_heads WHERE scope='global')
) r
GROUP BY q.tag; -- непустые: q01,q02,q03,q06,q07,q08,q09,q12 → 8/14
```

Контроль семантики `ts_rank` (PG 15.17, контейнер `noezema-test-db`):

```sql
SELECT ts_rank(to_tsvector('russian','кот ест мышь'),
               to_tsquery('russian','кот & жираф'))          AS r_1of2,  -- 1e-20
       ts_rank(to_tsvector('russian','кот ест мышь'),
               to_tsquery('russian','кот & мышь & жираф'))  AS r_2of3;  -- 0.0985
-- при ≥2 общих лексемах — ранг уровня полного совпадения, @@ = false
```

### 5.3 Коммитное поведение паковых сессий

```sql
-- 28 из 69 сессий без staging; 13 — curator_rejected_by_rules; 1 — curator_error
SELECT left(s.id::text,8) AS sess,
  (SELECT count(*) FROM audit_events a WHERE a.session_id=s.id
     AND a.type='staging_recorded') AS staged,
  (SELECT count(*) FROM audit_events a WHERE a.session_id=s.id
     AND a.type='session_state_changed'
     AND a.payload::text ILIKE '%curator_rejected_by_rules%') AS rejected_rules,
  (SELECT count(*) FROM audit_events a WHERE a.session_id=s.id
     AND a.type='session_state_changed'
     AND a.payload::text ILIKE '%curator_error%') AS curator_err
FROM sessions s ORDER BY s.created_at;

-- counters коммита (claims_reused/created, evidence_added, dependencies_added)
SELECT a.type, a.payload->'memory'
FROM audit_events a JOIN sessions s ON s.id=a.session_id
WHERE a.type='commit_attempt_committed' AND left(s.id::text,8) IN (…33 паковых…);
```

Видимость якоря в контекст-паке follow-up'а (retrieval против корпуса на
момент старта сессии):

```sql
WITH fu AS (SELECT s.id AS sess, s.created_at AS start, q.text AS qtext
  FROM sessions s JOIN questions q ON q.id=s.question_id
  WHERE left(s.id::text,8) IN (…22 follow-up'а…))
SELECT f.s8,
 (SELECT count(*) FROM claims c
  JOIN claim_assessment_heads h ON h.claim_id=c.id
    AND h.config_snapshot_id=(SELECT active_config_snapshot_id
         FROM runtime_config_heads WHERE scope='global')
  JOIN sessions cs ON cs.id=c.created_in_session
  WHERE cs.created_at < f.start
    AND GREATEST(
      ts_rank(to_tsvector('russian', c.statement), plainto_tsquery('russian', f.qtext)),
      ts_rank(to_tsvector('english',
        coalesce((SELECT string_agg(x,' ') FROM jsonb_array_elements_text(
          coalesce(c.search_statements,'[]'::jsonb)) x),'')),
        plainto_tsquery('english', f.qtext))) >= 1e-9) AS visible_matches
FROM fu f ORDER BY f.start;
-- 7add23b6:6, 4d67e817:6, 7871250d:6, de3f809a:5, 8c370d7f:4, 46bec75c:3,
-- 4dd12dfa:2, e3c2e95c:2, 70235790:1, 5cc41d0a:1, 3b2a4a7d:1, c3537eb3:1,
-- 4d510a51:1, 848106dd:1; остальные 8 — 0 (в корпусе не было строки-контрагента)
```

### 5.4 Evidence identity общих фактов (улика слоя 2)

```sql
SELECT e.identity_hash, count(DISTINCT e.claim_id) AS claims,
       count(DISTINCT e.created_in_session) AS sessions
FROM evidence e
WHERE e.identity_hash IN (
  SELECT identity_hash FROM evidence
  WHERE claim_id IN (
    SELECT id FROM claims WHERE left(id::text,8) IN
      ('07d655cf','bd0cbc5b','d87fb075','f2d5561d','80c1908c')))
GROUP BY e.identity_hash HAVING count(DISTINCT e.claim_id) >= 2;
-- 84abcf4730… (src 945f3705, chunk-0): 3 claim'а / 3 сессии (ООН-факт)
-- da578f37… и ccd036f7… (src 557bafba, 37a1e078): 2 claim'а / 2 сессии (ЕС-факт)
```

## 6. Не тронуто

`packages/`, `apps/`, `hostctl/`, `tests/` — без изменений (проверка —
в STATUS.md, раздел T7.33); пороги §22.2, `claim_type_rules` и
`rules_hash`; замороженные payload'ы config-v2…v5 и корпуса v1–v4
(хэши — EVAL-4-freeze §2 / EVAL-5-freeze §2); миграции; строки ранов и
сохранённые итоги `noezema-eval4d` (SELECT only, гейты не
пересчитаны — peresчёт «что было бы» в §4.3 — гипотетический, над
скопированными в запрос значениями); `ARCHITECTURE.md`;
`docs/adr/0006-*.md` — исторический текст не переписан (только
пометка-ссылка). Прогон не запускался: eval-run, сессии NOEZEMA и
обращения к LLM (192.168.1.48) — отсутствуют; фоновых процессов нет.

## 7. Проверка: 13 «предложил ноль» — переиспользование, которое протокол не может записать (дополнение 2026-09-23)

Дополнение после коммита T7.33 (`27a9d85`). §4.1 относит слой 1 к
поведению модели («follow-up'ы не коммитят знание»). Здесь проверена
альтернатива: модель переиспользовала знание, а протокол не дал ей это
записать. Код, пороги, сохранённые итоги — не тронуты (SELECT only).

### 7.1 Код: операции «перепроверил существующий claim» нет

`CuratorProposal` (`packages/domain/schemas/staging.py`) —
единственное, что модель может записать: `summary`, `claims:
list[ClaimProposal]`, `evidence_links: list[EvidenceLink]`,
`new_questions`.

- `EvidenceLink.claim_index: int` — индекс в списке `claims` **этого же**
  предложения, не id. Evidence прикрепляется как
  `claims[claim_index]` (§1.2).
- На существующий claim по id можно сослаться только через
  `ClaimProposal.dependencies: list[ClaimDependencyProposal]`
  (`claim_id: UUID`) — то есть **изнутри нового** claim'а.
- Старый claim попадает в `claims` сессии только через dedup — побайтовое
  равенство `(statement, claim_type)` (§1.2, T7.9).

Следствие: чтобы привязать свежее evidence к существующему claim'у,
модель обязана предложить claim — побайтово тот же либо новый с
зависимостью. Решение «факт уже установлен, нового утверждать нечего» не
имеет допустимой операции: сессия завершается без записи, и ни один из
трёх путей гейта 5 (§2) не срабатывает.

### 7.2 Данные: что модель сказала в 13 сессиях

Сырые ответы модели в EVAL-4d **не сохранялись**:
`model_runs.raw_response_artifact_id` пуст у всех 374 записей (не
очистка по сроку — `raw_retention_until` тоже пуст). Рассуждение модели
сохранилось только в аудите: `session_state_changed` с ключами
`rationale` / `complete_reason` (завершение фазы exploring).

13 «предложил ноль» (§4.1) выделены так: пустая паковая сессия (0
claim, 0 evidence) без `curator_rejected_by_rules` и без
`curator_error` в `session_state_changed`. Claim — якорь пака,
видимость которого в контекст-паке follow-up'а подтверждена §3.5.

| Сессия | Пак | Claim | `complete_reason` | Ссылка на claim | Фрагмент `rationale` дословно |
|---|---|---|---|---|---|
| `4d510a51` | reading.md | `d3475eca` | `goal_reached` | **нет** | «Файл notes/reading.md уже прочитан; в нём ровно три непустые строки, каждая из которых является названием книги. Это подтверждает количество книг в списке.» |
| `848106dd` | reading.md | `d3475eca` | `goal_reached` | по id | «Существующий claim c:d3475eca-814d-4b27-a285-eafa0491ff19 также фиксирует ровно три книги.» |
| `4dd12dfa` | ООН | `07d655cf` | `goal_reached` | без id | «Ранее зафиксированное число 193 совпадает с обоими источниками, поэтому дальнейший сбор данных не требуется.» |
| `7add23b6` | Python | `70ff7a38` | `goal_reached` | без id | «Это согласуется с существующим поддерживаемым claim о последней стабильной версии Python 3.14.7.» |
| `46bec75c` | ЕС | `80c1908c` | `goal_reached` | без id | «Оба названных источника уже загружены: официальный источник ЕС указывает 27 стран-членов, а ранее зафиксированные claims также подтверждают число 27.» |
| `4d67e817` | PostgreSQL | `c701f374` | `goal_reached` | без id | «Это подтверждает существующее утверждение о том, что последней стабильной версией PostgreSQL является 18.6.» |
| `7871250d` | Python | `70ff7a38` | `goal_reached` | по id | «Оба названных источника уже загружены: снимок Chocolatey явно указывает версию пакета Python 3.14 как 3.14.7, а ранее зафиксированное поддерживаемое утверждение c:70ff…» |
| `de3f809a` | PostgreSQL | `c701f374` | `goal_reached` | по id | «Это совпадает с ранее зафиксированным claim c:c701f374-a115-4607-b628-261fbf6679b3 о том, что последней стабильной версие» |
| `70235790` | ставка ЦБ | `426fe04a` | `goal_reached` | без id | «Для даты 18.09.2026 оба источника согласуются с существующим claim о значении 14,00% годовых; расхождений, требующих дополнительного исследования, не выявлено.» |
| `5cc41d0a` | ставка ЦБ | `426fe04a` | `goal_reached` | по id | «Существующий claim c:426fe04a-9580-4e9d-bf9c-0a28ddbfab64 фиксирует 14,00% на 18.09.2026, что совпадает с текущим значением по источникам.» |
| `c3537eb3` | plan.md | `5a37ce68` | `goal_reached` | по id | «Содержимое notes/plan.md показывает три строки-пункта плана встречи, что совпадает с ранее зафиксированным утверждением c:5a37ce68-4940-469e-b8a0-210fbad8dfb9 о 3 пунк…» |
| `3b2a4a7d` | Рублёво-Арх. | `6cc8a0d8` | `goal_reached` | без id | «Это согласуется с существующим claim.» |
| `8c370d7f` | ЕС | `80c1908c` | `goal_reached` | без id | «В текущем контексте уже есть загрузка обоих хостов: Википедия явно указывает «Membership: 27 members» и перечисляет 27 государств, а официальная страница ЕС загружена;…» |

Итог: **13 из 13 — follow-up'ы** (якорей среди них нет), **13 из 13 —
`goal_reached`**. Ссылка на существующий claim — в **12 из 13**:
5 по id, 7 без id («согласуется с существующим claim»,
«Ранее зафиксированное число 193 совпадает…»); 1 без ссылки
(`4d510a51` подтверждает число книг, но с прежним claim'ом не сверяет).
В каждой сессии модель загрузила оба названных источника и сверила с
ними значение.

Это и есть переиспользование, которое меряет гейт 5: вторая сессия
перепроверяет установленный факт по источникам. Оно произошло — и не
оставило записи.

### 7.3 Пересчёт по SQL гейта

Собственный SQL `_gate_reuse` (`packages/evaluation/gates.py:408`) —
CTE `cur` / `significant` / `refs` / `per_claim` без изменений — плюс к
`refs` гипотетические пары «claim ← перепроверившая его сессия» (§7.6).
Базовая строка воспроизводит сохранённый итог `0/29` точно — пересчёт
верен.

| Что засчитано | Пар | Claim'ов | g5 | Исход (порог ≥ 0.25) |
|---|---|---|---|---|
| как в EVAL-4d (сохранённый итог) | 0 | 0 | 0/29 = 0.000 | failed |
| строго: только claim, названный моделью по id | 5 | 5 | 5/29 = 0.172 | failed |
| полно: + якорь пака там, где ссылка без id | 13 | 8 | **8/29 = 0.276** | **passed** |

`4d510a51` (без ссылки) указывает на `d3475eca`, уже засчитанный через
`848106dd` по id — на оба числа не влияет.

### 7.4 Оговорки

- Это what-if, **не переоценка**: сохранённый итог `0/29 failed`,
  строки ранов и данные не тронуты.
- Истина — между строгим (0.172) и полным (0.276) вариантом. В полном
  ссылка без id привязана к якорю пака по видимости (§3.5), id модель
  там не назвала.
- Засчитываются ссылки, как и в самом гейте; корректность каждой
  перепроверки (§7.2 — модель пишет, что загрузила оба источника) здесь
  не проверялась.
- 9 сессий слоя 1 с отказом (8 rules + 1 `curator_error`) этим не
  покрыты — их закрывают варианты vi.1 / vi.2 (§4.3).

### 7.5 Что меняется в выводах §4

- **Слой 1 делится надвое.** 13 из 22 пустых сессий — не «модель не
  коммитит», а **переиспользование, которое протокол не может
  записать**; 9 — модель + rules (8 пар type↔evidence, 1 обрезанный
  UUID).
- **Атрибуция рекомендации (i) неверна.** «g5 на halogen неизмерим —
  модель не коммитит follow-up'ы» винит модель; по §7.2 модель
  переиспользует знание корректно. Провал гейта 5 в EVAL-4d — в
  значительной части артефакт протокола.
- **vi.1 / vi.2 не трогают самую большую группу**: они лечат 9 сессий, не
  эти 13.
- **Вариант (vii), новый — не реализован, решение за пользователем:**
  `EvidenceLink` может ссылаться на существующий claim по id как
  альтернатива `claim_index`. Прецедент в схеме уже есть —
  `ClaimDependencyProposal.claim_id`; хост проверяет то же условие, что
  dedup T7.9 (у claim'а есть head в snapshot'е сессии); инвариант
  «id генерирует хост» не нарушается — модель ссылается на выданный
  хостом id, а не порождает его. Пара к vi.2 (UUID-префиксы): модель уже
  обрезала UUID (`curator_error`, пак 7). Затрагивает схему
  `CuratorProposal` (уходит движку — профиль схемы, ADR-0012), промпт
  куратора (замороженный snapshot → новый payload) и путь коммита
  `service.py`; семантика staging, вероятно, требует ADR и правки
  `ARCHITECTURE.md` (AGENTS.md §4). `rules_hash` и `claim_type_rules` —
  не затрагиваются.

### 7.6 SQL пересчёта (SELECT only, `noezema-eval4d`)

```sql
-- $1 uuid[] — claim_id, $2 uuid[] — перепроверившая сессия (пары §7.2)
WITH cur AS (
  SELECT h.claim_id, a.effective_grade
  FROM claim_assessment_heads h
  JOIN claim_assessments a ON a.id = h.current_assessment_id
  WHERE h.assessment_state = 'current'
    AND h.epistemic_status IN ('supported', 'disputed', 'refuted')
    AND h.config_snapshot_id = <EFFECTIVE_SNAPSHOT_SQL>
),
significant AS (SELECT claim_id FROM cur WHERE <effective_grade >= E2>),
extra(claim_id, s) AS (SELECT * FROM unnest($1::uuid[], $2::uuid[])),
refs AS (  -- три пути гейта 5 без изменений + гипотетические пары
  SELECT claim_id, created_in_session s FROM evidence
    WHERE claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
  UNION SELECT claim_id, session_id FROM claim_revisions
    WHERE claim_id IN (SELECT claim_id FROM significant) AND session_id IS NOT NULL
  UNION SELECT from_claim_id, created_in_session FROM claim_dependencies
    WHERE from_claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
  UNION SELECT to_claim_id, created_in_session FROM claim_dependencies
    WHERE to_claim_id IN (SELECT claim_id FROM significant) AND created_in_session IS NOT NULL
  UNION SELECT claim_id, s FROM extra WHERE claim_id IN (SELECT claim_id FROM significant)
),
per_claim AS (SELECT claim_id, count(DISTINCT s) n FROM refs GROUP BY claim_id)
SELECT (SELECT count(*) FROM significant), (SELECT count(*) FROM per_claim WHERE n >= 2);
```

`<EFFECTIVE_SNAPSHOT_SQL>` и `<effective_grade >= E2>` — те же
выражения, что в `gates.py` (`EFFECTIVE_SNAPSHOT_SQL`,
`_grade_at_least(..., 'E2')`); при пересчёте импортированы из модуля,
не скопированы.
