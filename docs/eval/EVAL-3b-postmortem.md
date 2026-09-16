# EVAL-3b — постмортем остановленного прогона (разбор по данным, без новых прогонов)

Дата разбора: 16.09.2026. Прогон остановлен по решению пользователя после 4 сессий; на момент
остановки в БД `noezema-eval3b` завершено **7 сессий**, 8-я остановлена mid-exploration.
DB `noezema-eval3b` и логи `/home/denis/dsh1/eval3-logs/` сохранены как улики, не изменялись
в ходе разбора. Сводные цифры: sessions=7, claims=7 (0 external_fact/temporal_fact),
heads=5, evidence=16 (8 source_assertion), sources=14, model_runs=54, actions=43,
messages=0, et=0 на всём протяжении (watchdog).

Сессии (id8 / вопрос / исход):

| id8 | вопрос | исход |
|---|---|---|
| a4c2de66 | ООН (15.04.2026) | succeeded_partial |
| 46849cec | Python (15.04.2026) | succeeded_partial, budget_exhausted |
| 75651adc | ЕС (01.01.2026) | succeeded_partial, budget_exhausted |
| f19c114f | todo.md | succeeded (goal_reached) |
| 0cba31ca | glossary.md | succeeded (goal_reached) |
| 8ccb0b7f | reading.md | succeeded_partial |
| b091cfe2 | ЕС (текущая дата) | succeeded_partial, budget_exhausted |

Сессия 8 («ключевая ставка», priority 90) стартовала после 13:48:03 (commit сессии 7) и была
остановлена mid-exploration: её фаза-1 транзакция откатилась (нет строки в `sessions`, нет
actions/model_runs), но следы proxy в его собственных транзакциях остались: `cbr.ru` (13:49:14)
и `consultant.ru` (13:49:44) — research_fetch_completed, 2 строки в `sources`, 2 artifact'а.

---

## П.1 Почему curator выдал `local_observation` «В evidence присутствует ссылка…» вместо `external_fact`

**Классификация: дефект системы (корневой) + поведение модели (вторичное).**

Уточнение фактуры: мета-claims («В evidence присутствует ссылка на пакет python314 на
Chocolatey» — `e098b6d3`; «В evidence присутствует ссылка на официальную страницу загрузок
Python» — `c005b58a`) созданы **сессией 2 (Python-вопрос)** — chocolatey и python.org это
источники именно этого вопроса. Сессия 1 (ООН) claims не создала вовсе; сессии 3 и 7 тоже
не создали (у 7 — только procedural-claim о процессе). Все 8 source_assertion в БД принадлежат
сессии 2 (7×chocolatey + 1×python.org).

Цепочка причины (код + данные):

1. Вход куратора (consolidating, `apps/orchestrator/orchestrator.py:1569-1605`):
   вопрос + знание (секции `claims_evidence`/`pending_claims`/`contradictions` — на момент
   сессии 2 все пустые: claims_evidence=0 токенов в CONTEXT_PACKED, seq=7) + строки evidence
   вида `[{i}] {kind} {hash16} {payload ≤1000}` + (пустый) верификационный отчёт.
2. Payload evidence вида `source_assertion` строится в `apps/orchestrator/evidence.py:81-97`:
   `{"url", "url_arg", "original_sha256", "normalized_sha256", "chunk_id"}`. **Ни текста
   страницы, ни фрагмента, ни цитаты — только URL и хеши.**
3. Explorer видел контент (fenced normalized text до 40 KiB, `orchestrator.py:773-833`,
   `RESEARCH_CONTEXT_BUDGET = 40_000`) — но только в собственном цикле. Контент никуда не
   переносится: ни в payload evidence, ни в staging, ни в context pack. Единственный
   персистентный доступ к тексту — через artifact store по source_id+chunk_id (им пользуются
   только blind-sample dump и верификация цитат, не куратор).
4. Следовательно, из всех 8 строк evidence куратор мог извлечь только факты вида «присутствует
   ссылка на URL X». Мета-claim — честный вывод из его входа; `local_observation` при этом
   — корректный тип для того, что модель реально утверждала (наблюдение о состоянии
   evidence). Модель ошиблась не в типе, а в том, что создала claim вместо вопроса
   (правило 5 промпта: «Если evidence недостаточно для какого-либо claim — не создавай
   claim; создай вопрос»).
5. Подтверждение по токенам: consolidating-ран сессии 2 — in=2289 (реконструкция:
   вопрос ~60 + «(пусто)» + 8 строк × ~50 + system curator.md ~700) — у входа физически
   не было места для фактов.
6. Расхождение со спекой: §6.4 ARCHITECTURE.md — «`source_assertion` — утверждение из точного
   source chunk». Утверждение (текст) должно быть частью evidence; реализация его не несёт.
7. Правила claim-типов (allowed_kinds и т.д.) куратору **не подаются** — он видит только
   список имён типов в system-промпте (`prompts/curator.md`); правила применяются rules
   engine на assessment (host). Модель заранее не знает, что local_observation не может
   иметь source_assertion evidence (это и привело к паре, отклонённой на П.2).

Что менять:

- **A1 (система, корневое)**: payload `source_assertion` обязан нести текст утверждения
  из chunk (выдержка/цитата — например, фрагмент до N символов из normalized chunk),
  иначе куратор не способен сформулировать external_fact/temporal_fact.
- **A2 (альтернатива/дополнение A1)**: в вход куратора передавать fenced-тексты источников
  сессии (обрезанные по бюджету) либо explorer предлагает формулировки claims в финальном
  отчёте, а куратор только ссылается/валидирует.
- **A3 (промпт, дёшево)**: до A1/A2 — явное правило в curator.md: «строки source_assertion
  содержат только ссылку на источник; если текста утверждения в строке нет — не создавай
  claim о факте, создай вопрос». Убирает генерацию мета-claims.

---

## П.2 Два claim без строки в `claim_assessment_heads`

**Классификация: дефект системы.**

Факты:

- Head'ов нет ровно у двух мета-claims: `e098b6d3` (7 source_assertion, chocolatey) и
  `c005b58a` (1 source_assertion, python.org). Остальные 5 claims имеют head
  (4 workspace local_observation — current/supported E2; 1 procedural — current/hypothesis E1).
- Прямое доказательство в audit (сессия 46849cec, event `commit_attempt_committed`, seq=69):
  `memory.problems = ["assessment rejected for e098b6d3…: support evidence kind
  'source_assertion' not allowed for local_observation", "assessment rejected for
  c005b58a…: …"]`, `assessments=0`, `claims_created=2`, `evidence_added=8`. Commit
  **применён** — claims и evidence закоммичены, head'ов нет.
- Механизм: `packages/memory/service.py` apply-поток — step 1 создаёт claims, step 3
  (`service.py:516-533`) делает per-claim `_assess`; `RuleValidationError`
  (`packages/memory/rules_engine.py:172-176`: kind support-evidence не входит в
  `allowed_kinds`) ловится → `problems.append` → commit продолжается. Frozen-правило
  `local_observation.allowed_kinds = ["local_observation"]` — source_assertion не в списке.
- Допустимость по §14.1: `claim_assessment_heads` — источник истины lifecycle для пары
  claim/config-snapshot; §14.1 формулирует инварианты строк head (current ⇔ оба поля
  NOT NULL и т.д.), но «каждый claim обязан иметь head» явно не прописано — однако:
  (а) docstring `_assess`: «One deterministic assessment + head upsert (same transaction)» —
  намерение дизайна: каждый коммиченный claim получает assessment;
  (б) `claim_view` (`service.py:830-831`): нет head → `None` — claim **невидим** retrieval,
  context pack, memory.search, гейтам и blind-sample;
  (в) claim + 8 evidence = вечный dead weight + «яд для dedup»: будущая сессия с тем же
  statement «переиспользует» claim и снова получит отклонённый assessment.
- Тесты: отклонение на уровне rules engine покрыто (`tests/unit/test_rules_engine.py:138`,
  `test_disallowed_support_kind_is_rejected`); поведение apply-потока «commit несмотря на
  отклонение» тестом не зафиксировано — латентный пробел.

Задача + тест + фикс:

- **B1**: host-валидация предложения куратора (validate_against, `orchestrator.py:1657`):
  evidence_link, связывающий evidence kind, не допущенный правилом типа заявленного claim,
  → отклонить предложение на стадии consolidating (до commit).
- **B2**: инвариант commit — отклонённый assessment не применяется: либо весь commit, либо
  минимум claim с его evidence не коммичатся (claim без head в `claims` не появляется).
- **B3 (тесты)**: scenario-тест «claim с недопущённым видом evidence не коммичится без head»;
  invariant-тест «для каждого committed claim существует head активного snapshot».
- (Ремонт данных этой БД — опционально, только с явного разрешения: удалить два claim и их
  evidence либо оставить как улику.)

---

## П.3 «Сессия 1 семь раз скачала chocolatey из другого вопроса»

**Корректировка предпосылки (по данным): чужих URL в прогоне не было. Классификация цикла:
дефекты системы ×2 + поведение модели.**

Точные действия (audit `action_started`, verbatim URL):

- Сессия 1 (ООН): `un.org/en/about-us` (200) → `ru.wikipedia.org/wiki/Список_государств—_членов_ООН`
  (**404 — опечатка модели**, см. П.5) → `ru.wikipedia.org/wiki/Список_государств_—_членов_ООН`
  (200). Chocolatey здесь нет.
- Сессия 2 (Python): `python.org/downloads` (200) → `community.chocolatey.org/packages/python314`
  **×7 (все 200, один и тот же URL)** → `python.org/downloads` (500 UniqueViolation).
  Chocolatey — **собственный второй источник вопроса** (текст вопроса: «Ответь строго по этим
  двум источникам: python.org/downloads и community.chocolatey.org/packages/python314»).
- cbr.ru (13:49:14) и consultant.ru (13:49:44) — **не сессия 7**: сессия 7 завершилась commit'ом
  в 13:48:03. Это фетчи **сессии 8** («ключевая ставка», priority 90 — её источники в корпусе
  ровно cbr.ru + consultant.ru/legalnews/32063), остановленной mid-exploration. Proxy пишет
  events в своей транзакции без session_id, поэтому атрибуция по времени.

Истинные причины цикла повторений:

1. **Трuncation explorer-контекста (система, главная).** Prompt шага собирается в
   `_explorer_context` (`orchestrator.py:1489-1532`): pack + инструменты +
   `observations[-15:]` + evidence, затем **целое обрезается `[:24_000]` символов**.
   Fenced-контент research.fetch (до 40 000 байт) дописывается в observations в конец —
   значит, **результат последнего фетча всегда отрезан**: модель не видит результат
   предыдущего fetch и повторяет тот же. Подтверждение: input_tokens константен на всю
   петлю (сессия 2: 9895 ×6, сессия 3: 8521 ×9); CONTEXT_PACKED минимальный (317–330
   токенов, protocol=215, question_plan=62–75, всё остальное 0) — размер сообщения
   задаёт одна и та же 24k-обрезка.
2. **Неидемпотентный refetch в research proxy (система).** Повторный fetch статичного
   контента: `INSERT INTO artifact_chunks (… , 'chunk-0', …)` с новым id при существующем
   artifact (по sha) → `UniqueViolationError (artifact_id, chunk_id)` → 500
   (`apps/research_proxy/service.py:220-230`). Комментарий кода обещает dedup по
   original content hash — он есть только для identity evidence, не для store. Данные:
   сессия 3 — 8 повторных fetches europa.eu → 8×500; сессия 7 — её же 2 URL (artifact'ы
   созданы сессией 3) → сразу 2×500; сессия 2 — повторный python.org → 500. Семь повторных
   chocolatey не дали 500, потому что страница динамическая (разные sha → новые
   artifact'ы) — в итоге 7 дублирующих source-строк одной страницы.
3. **Нет intra-session repeat-guard (система).** `packages/cognition/repetition.py`
   защищает от циклов вопросов между сессиями; идентичные tool-вызовы внутри сессии
   (один и тот же arguments_hash) не охраняются.
4. **Поведение модели**: после 500 модель повторяет тот же вызов 7–8 раз без смены
   стратегии; после 404 — самоисправилась (хорошее поведение).

Что менять:

- **C1 (система, приоритет)**: последнее наблюдение обязано быть в prompt: обрезка должна
  отбрасывать самые старые observations, а не последние (собственный бюджет на
  observations, newest-first), либо уменьшить RESEARCH_CONTEXT_BUDGET до значения,
  гарантированно влезающего.
- **C2 (система)**: idempotency research.fetch — при существующем artifact по sha
  вернуть existing source/chunk (dedup), а не 500.
- **C3 (система)**: guard — N идентичных (tool, arguments_hash) в сессии → deny с
  объясняющей observation («URL уже загружен, результат в наблюдениях»).
- **C4 (промпт)**: правило explorer'а на ошибку инструмента (не более 2 ретраев, затем
  смена стратегии или завершение с gap).

---

## П.4 `message.reply` отклоняется валидацией аргументов

**Классификация: дефект системы (дизайн) + поведение модели (следование схеме).**

Точные данные (audit `policy_evaluated`, error_code `policy:deny`):

- Сессия 1, seq=20 и seq=22: модель слала поле `message`:
  reasons = `argument ('message_id',): Field required; argument ('body',): Field required;
  argument ('message',): Extra inputs are not permitted`.
- Сессия 4 (f19c114f), seq=15: модель слала поле `text` — та же тройка ошибок с `('text',)`.
- Итого 3 потерянных шага (2 в сессии 1, 1 в сессии 4) из бюджета max_explorer_steps=10.

Причины:

1. Инструмент показан модели при **пустом inbox**: схема строится из статического профиля
   (`packages/policy/tools.py:123-138`), `messages` в прогоне = 0 строк, секция messages
   context pack = 0 токенов. Описание «Ответить на сообщение оператора (staging)» не
   говорит, что ответ возможен только на увиденное сообщение с ID.
2. `message_id` непрозрачен для модели: его нельзя сконструировать — только ответить на
   сообщение, ID которого модель видела. Без сообщений инструмент структурно неcallable,
   но остаётся в схеме; модель (qwen36-35b) использует его как канал «финального ответа»
   (сессия начинается с вопроса — рефлекс «ответить»).
3. Фидбэк после deny — сырой pydantic-текст (`orchestrator.py:1263-1265`): модель не
   разобралась — ретрай сессии 1 (seq=22) повторил то же неверное поле.

Что менять:

- **D1 (система)**: inbox-gating — `message.reply` в схеме модели только при наличии
  непрочитанных сообщений оператора (динамический профиль), либо переписать описание и
  возвращать на deny объяснение «сообщений для ответа нет; финальный ответ — отчёт сессии».
- **D2 (система)**: при ошибке схемы аргументов — observation на языке модели
  (имена полей, пример вызова), а не сырой pydantic.

---

## П.5 «Дефект корпуса: URL списка стран ООН отдаёт 404»

**Классификация: корпус НЕ виноват; поведение модели (опечатка URL) + вывод о методе проверки.**

Факты:

1. Корпус-файл `docs/eval/question-set-v2.jsonl` (frozen, sha256 `93c1a93a4d6c…`, не тронут)
   содержит байт-корректный URL: `…/wiki/Список_государств_—_членов_ООН`
   (кодопоинты вокруг тире: 0x5f 0x2014 0x5f). Fetch: **200** (проверено повторно 16.09).
2. Закоммиченный в DB вопрос (seed) содержит тот же корректный URL.
3. Percent-encoded source-строка успешного фетча декодируется в корректный URL
   (`%D0%A1%D0%BF…` → `Список_государств_—_членов_ООН`).
4. 404 — **опечатка модели в рантайме** (audit `action_started`, сессия a4c2de66, seq=14):
   `https://ru.wikipedia.org/wiki/Список_государств—_членов_ООН` — пропущено подчёркивание
   **перед** тире. Следующий шаг (seq=17) модель сама исправилась и загрузила корректный
   URL (200), ответ (193) построен по корректным источникам; в финальном тексте цитата
   повторяет битый вариант — косметический артефакт формулировки.
5. Полная повторная проверка всего корпуса **тем же способом, что при заморозке**
   (httpx GET, follow_redirects, browser UA → `normalize_content` research proxy → grep
   факта в первых 40 KiB normalized text; метод — `/tmp/factgrep.py`, 16.09.2026):
   **37 уникальных URL — все 200**; все 24 пары фактов присутствуют. Единственный
   формальный «miss» — consultant.ru: там «равен 14%», а не «14,00%» (факт «ставка 14%
   с 27.07.2026» на странице есть — pattern в моей проверке был строже, чем в корпусе).

Почему «проверка пропустила»: **не пропустила — в корпусе нечего было пропускать.**
Замораживающая проверка фетчила точные (корректные) URL из файла. Слепое пятно — в
рантайме: модель перебивает длинный Unicode-URL (эм-тире + подчёркивания) и может
уронить символ; системы защиты от «URL, близкого, но не тождественного источнику
вопроса» нет.

Что менять:

- **E1 (система, низкий приоритет)**: guard на research.fetch — URL, отличающийся от
  источника вопроса на 1–2 символа (edit distance / нормализация) → observation
  «URL не совпадает с источником вопроса (проверьте подчёркивание/тире)».
- **E2 (гигиена freeze)**: проверка корпуса — воспроизводимый артефакт (команда
  corpus-check со списком 24 пар и ожидаемых fact-pattern'ов), а не разовые скрипты в /tmp.

---

## Итог: что менять (по приоритету)

| # | Задача | Устраняет |
|---|---|---|
| 1 | C1 — последние наблюдения в prompt explorer'а (newest-first eviction) | цикл повторных fetch (главная трата step-бюджета: 7–8 из 10 шагов) |
| 2 | A1/A3 — текст утверждения в payload source_assertion / правило куратора | корень et=0: куратор физически не может сформулировать external_fact |
| 3 | B1+B2+B3 — claim без head не коммичится + тесты | dead claims, невидимость, яд для dedup |
| 4 | C2 — idempotent refetch (dedup по artifact sha) | 500 UniqueViolation на повторных fetch, каскад ретраев |
| 5 | D1 — inbox-gating message.reply | потерянные шаги на пустом inbox |
| 6 | C3+C4+E1 — repeat-guard, правила на ошибки, typo-guard URL | вторичные утечки шагов |
| 7 | A2, E2 — вход куратора, воспроизводимый corpus-check | системная устойчивость |

Новый прогон не запланирован, корпус не перезамораживается, `noezema-eval3`/`eval3-*.log`
(первый прогон) и `noezema-eval3b`/`eval3b-*.log` (этот) не изменялись. Ждём решение.

## Строка прогона в `evaluation_runs`

Строка ран-а EVAL-3b (`noezema-eval3b`, run `34e06d35-e8c5-4e9f-aa70-1ba39b53c358`,
label `EVAL-3`, started 13:10:30 UTC) **оставлена незакрытой**: `outcome='running'`,
`finished_at=NULL`, `gates={}`, eligible/completed=0. Штатного способа закрыть ран
«как прерванный» в механизме нет: closed-set `outcome` =
`('running','passed','failed','insufficient_sample')` (миграция `0020_evaluation`),
единственная функция закрытия — `finish_evaluation_run`
(`packages/evaluation/service.py`), которая вычисляет итог по гейтам: для убитого ран-а
она поставила бы `insufficient_sample` (7 сессий, et=0) и записала бы `finished_at`
во время постфактумного закрытия, перезаписав честную запись о прерванном оператором
ран-е. `outcome='running' + finished_at=NULL` — точная улика остановки; настоящий
документ и есть её отчёт. DB и логи не изменялись.
