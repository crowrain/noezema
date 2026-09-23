# ADR-0018: Перепроверка существующего claim'а — запись, привязанная к сессии (T7.34, §3.7, §14.1, §14.3, §22.2)

Статус: accepted

Дата: 2026-09-23 (решение пользователя от 2026-09-23: направление
(vii) из `EVAL-4d-reuse-analysis.md` §7.5 одобрено, включая правку
`ARCHITECTURE.md` — строго в пределах определения записи
перепроверки)

## Контекст

Гейт 5 §22.2 («≥25% значимых claims переиспользуются/перепроверяются
в 20 сессиях» — `ARCHITECTURE.md:2608`) провален во всех сериях; в
EVAL-4d — 0/29 (сохранённый итог). Разбор T7.33
(`docs/eval/EVAL-4d-reuse-analysis.md`) показал: 13 из 22 пустых паковых
сессий — follow-up'ы с `goal_reached`, которые перепроверили факт по
обоим источникам и в 12 из 13 случаев сверили его с существующим
claim'ом (5 — прямо по id, §7.2). Протокол не дал это записать:
`EvidenceLink` адресует claim только индексом в собственном
предложении, существующий id доступен лишь в `dependencies` НОВОГО
claim'а, а дедуп — побайтовое равенство `statement+claim_type`
(`service.py`), которое свободная формулировка не гарантирует.
Пересчёт собственным SQL гейта с записью этих перепроверок: 5/29 =
0.172 (failed) … 8/29 = 0.276 (passed) против сохранённых 0/29 (§7.3).
Это пробел соответствия спеке, а не новая функция: перепроверку гейт 5
обязан мерить, а записать её нечем.

### Ловушка identity — почему не наивный «EvidenceLink по id»

Наивный вариант (vii из §7.5: `EvidenceLink` ссылается на существующий
claim по id) почти ничего не даёт, и вот почему — подтверждено на
данных EVAL-4d (`noezema-eval4d`, SELECT only, 2026-09-23):

- Уникальность evidence — `UNIQUE(claim_id, evidence_kind,
  identity_hash)` (`ARCHITECTURE.md` §14.3), и при совпадении коммит
  ПЕРЕИСПОЛЬЗУЕТ существующую строку: её `created_in_session`
  остаётся сессией якоря. Identity `source_assertion` = хэш
  содержимого источника + цитируемый фрагмент
  (`source_assertion_identity`, `packages/memory/evidence.py`).
- Проверено по 13 парам «follow-up → якорный claim»: follow-up'ы
  реально перелоадили те же URL'ы, но **содержимое динамических
  страниц дрейфует между днями**. Из 20 evidence-identities якорей
  (10 source-based пар × 2 источника) follow-up'ы воспроизвели побайтово
  ровно **12** (те же content-hash → та же identity → схлопывание в
  строку якоря); **8** — тот же URL, но другой content-hash (un.org,
  chocolatey, consultant.ru, msk.kp.ru, habr, postgresql.org —
  баннеры/таймстампы/новостные заголовки). Полное схлопывание обоих
  источников — у обоих follow-up'ов пака ЕС (46bec75c, 8c370d7f →
  `80c1908c`): наивный путь дал бы гейту ровно ОДНУ сессию.
- Вывод: наивный путь ненадёжен в обе стороны. На стабильных
  источниках (или в ране, где страницы не изменились) evidence
  перепроверки схлопнется целиком, и гейт 5 снова увидит одну сессию;
  в EVAL-4d он «случайно» работал на 8 из 10 source-based пар только
  благодаря дрейфу содержимого. Сигнал, определяющий гейт, не может
  зависеть от случайности content-drift.
- Путь «revisions» тоже не подходит: `claim_revisions` в коде нигде не
  пишется (только читается гейтами), и по спеке ревизия — это
  ИЗМЕНЕНИЕ значения claim'а (`previous_value → new_value`,
  `ARCHITECTURE.md` §8.7); перепроверка «без изменений» туда не
  ложится.

## Решение

**Определение: перепроверка (reverify) — это действие сессии, в
которой модель сверяет УСТАНОВЛЕННЫЙ факт (существующий claim) со
свежими источниками, не изменяя значение claim'а. Запись
перепроверки — отдельная строка `claim_assessments`, привязанная к
сессии: `claim_id` + `created_in_session` = сессия-перепроверка. Она
существует НЕЗАВИСИМО от того, появилось ли новое evidence.**

Почему запись = assessment-строка (а не новая таблица
`claim_reverifications`):

- Assessment по спеке (§14.3) — это «оценка claim'а детерминированным
  rules engine на зафиксированном наборе evidence в МОМЕНТ проверки»;
  перепроверка — ровно это: новый момент проверки. Все поля,
  требуемые определением записи, уже есть: исход
  (`epistemic_status`: supported = подтверждена, disputed ≤ E1 =
  контр-evidence), ссылки на evidence (`evidence_set_hash` +
  `assessment_evidence`), момент (`created_at`), сессия
  (`created_in_session`), audit (`claim_assessed` в той же транзакции).
  Новая таблица дублировала бы assessment строку в прямом смысле.
- Строка создаётся существующим путём (`_assess`) при любом claim-
  опере сессии — и при создании, и при dedup-переиспользовании, и при
  reverify. Dedup-переиспользование (побайтовый statement+type) таким
  образом тоже становится измеримым гейтом 5 — это его смысл.
- Путь гейта 5 — одна UNION-ветка
  (`claim_assessments.claim_id + created_in_session`, только
  сессионные строки: worker/activation-строки имеют
  `created_in_session IS NULL`).

### Операция модели — существующий вид staging-операции

Модель выражает перепроверку claim-операцией с новым опциональным
полем `existing_claim_id` (`ClaimProposal`, `packages/domain/schemas/
staging.py`):

```json
{"statement": "<повторная формулировка>", "claim_type": "...",
 "existing_claim_id": "<uuid из [c:<uuid>] или однозначный префикс>",
 "as_of": null}
```

+ обычные `evidence_links` на этот индекс. **Видов staging-операций
не меняется** (claim / evidence / question / identity, AGENTS.md §3):
операция остаётся `claim`; на коммите хост привязывает её к
существующему claim'у (новый claim НЕ создаётся), statement —
аудиторская повторная формулировка, тип/значение — якорные
(смена значения — это revision, не reverify). Инвариант «id генерирует
хост» не нарушается: модель ССЫЛАЕТСЯ на выданный хостом id (как в
`ClaimDependencyProposal.claim_id`).

### Резолюция ссылки — fail-closed

- Условие — то же, что у dedup T7.9: у claim'а есть head в snapshot'е
  сессии. Невидимый/несуществующий id — отказ.
- На границе куратора (до записи staging): ссылка резолвится по
  claim'ам, ВИДИМЫМ в контекст-паке (строки `[c:<uuid>]`);
  неразрешимая ссылка отклоняет ВСЁ предложение с понятной причиной в
  аудите (`session_state_changed`, `curator_reject_kind:
  reverify_unresolved`) — не молчаливый пропуск. Rules pre-check
  (T7.9) для reverify-claim'а выполняется под ТИПОМ ЯКОРЯ
  (модельная повторная формулировка — audit-only).
- На границе коммита: повторная проверка по набору «head в snapshot'е
  сессии»; неразрешимо → problem в аудите коммита + отказ именно этой
  операции (и её evidence-ссылок) — индексы `claim_index` остаются
  выровнены с предложением (плейсхолдер `None`).
- **Префикс-резолюция — реализована** (модель уже обрезала UUID —
  `curator_error`, пак 7 EVAL-4d, §3.2). Строго fail-closed:
  `existing_claim_id` — строка (8–36 символов; `format` в схеме и так
  снят профилем halogen — ADR-0012, валидация ответа хостом полной
  pydantic-моделью не ослаблена); полное UUID принимается только если
  видно; hex-префикс (8–31 символ, дефисы игнорируются) — только если
  ОДНОЗНАЧЕН среди видимых claim'ов; иначе — отказ с причиной.
  Чистая функция `resolve_claim_reference`
  (`packages/memory/service.py`) — unit-тестируема отдельно.
  Dependency-путь (`ClaimDependencyProposal`) префиксами НЕ расширен —
  вне области T7.34 (известное ограничение ниже).

### Свежесть (ADR-0017)

Подтверждённая перепроверка — новый момент проверки, поэтому
**да, она сдвигает `reverify_after` якоря relative**: единое правило —
та же одна функция `reverify_after(result, anchor, now)` в `_assess`
(ADR-0017: срок = МОМЕНТ ПРОВЕРКИ + окно по volatility; якорь — из
вопроса сессии-перепроверки через `derive_claim_as_of`), НИКАКОЙ копии
логики. Evergreen-claim (якорь explicit/none, `reverify_after IS NULL`)
остается без срока, если вопрос перепроверки не делает его
относительным — по тому же правилу.

### Оценка (AGENTS.md §3)

- Перепроверка с тем же evidence — evidence схлопывается по identity
  (`duplicate evidence не повышает grade` не ослабляется ни на йоту);
  assessment пересчитывается rules engine на том же наборе → grade не
  меняется, запись существует.
- Новое отличающееся evidence — обычный путь rules engine (grade может
  вырасти по правилам, например E3→E4 при доп. независимых группах).
- Контр-evidence — существующие правила: `disputed`, ≤ E1, пока нет
  valid resolution.
- grade/confidence производит ТОЛЬКО rules engine.

### Гейт 5

`_gate_reuse` (`packages/evaluation/gates.py`) — пятая ветка `refs`:
`claim_assessments.claim_id + created_in_session IS NOT NULL` (только
значимые claims). Порог 0.25 и направление `at_least` — без изменений.
Прочие гейты не тронуты.

### Промпт и конфиг

Промпт куратора — `curator-v3 → curator-v4` (поле
`existing_claim_id` + правило 7 «Перепроверка»). Замороженные
payload'ы config-v2…v5 НЕ переписаны; новый payload
`docs/eval/config-v6-payload.json` (diff v5→v6 — ровно 1 поле:
`prompts.curator.version`; canonical sha256 `e6de7fe3c7e308f8…`,
file sha256 `3832e077d76dfc91…`; `TokenBudgets.validate() == []`).
Pin в `BOOTSTRAP_PAYLOAD` поднят до curator-v4 (паттерн T7.14).
Схема `CuratorProposal` уходит движку: новое поле —
`anyOf [{type: string, minLength: 8, maxLength: 36}, {type: null}]` —
ключевых слов `format`/`pattern` не добавляет (проверено; профиль
halogen — ADR-0012).

## Что НЕ меняется

- rules_hash, claim_type_rules, пороги §22.2 и направления гейтов;
- виды staging-операций (claim / evidence / question / identity);
- дедуп T7.9 (побайтовый statement+type + условие «head в snapshot'е»)
  — работает как есть; reverify — дополнительный, id-базированный путь;
- `UNIQUE(claim_id, evidence_kind, identity_hash)` и правило
  «duplicate evidence не повышает grade»;
- `claim_revisions` — по-прежнему без писателя (ревизия значения — не
  reverify);
- замороженные payload'ы config-v2…v5, корпуса v1–v4, сохранённые данные
  прошлых прогонов (noezema-eval* — SELECT only, не переоценивать);
- прочие пути гейта 5 и прочие гейты; dependency-резолюция
  (`ClaimDependencyProposal.claim_id`) — префиксами не расширена.

## Известные ограничения

- Модельная повторная формулировка (statement, claim_type, as_of,
  scope в reverify-операции) — audit-only; если модель назвала тип
  иначе, чем якорь, это не ошибка (класс (б) разбора §3.3: ООН
  temporal→external) — оценка идёт под типом якоря.
- Перепроверка НЕ меняет statement/claim_type якоря: факт изменился —
  это revision (в v1 писателя нет) — модель должна выразить это
  контр-evidence (disputed) и/или новым claim'ом.
- Префикс на границе коммита резолвится по «head в snapshot'е
  сессии» (набор шире контекст-пака — пак ограничен бюджетом):
  префикс, однозначный в паке, но неоднозначный в корпусе, отклоняется
  на коммите (fail-closed; практически недостижимо — pre-check куратора
  уже проверил пак).
- Дрейф содержимого источников не лечится: динамические страницы дают
  новую identity (это корректно — новое содержимое = новое
  evidence); запись перепроверки существует в любом случае.

## Тесты

- `tests/unit/test_claim_reference.py` — `resolve_claim_reference`:
  полное UUID (видимо/не видно), префикс (однозначный/неоднозначный/
  пустой), мусор, 32 hex без дефисов, дефисы в префиксе.
- `tests/unit/test_staging_schema.py` — поле `existing_claim_id`
  (строка, 8–36, extra-forbid не сломан).
- `tests/unit/test_memory_service.py` (scenario через MemoryService,
  не прямые вставки): якорь создаёт claim → follow-up перепроверяет
  тот же id с ТЕМ ЖЕ evidence (evidence не дублируется, grade не
  меняется, запись-перепроверка есть); то же с НОВЫМ evidence
  (added, grade по правилам); контр-evidence → disputed ≤ E1;
  headless/несуществующий id → отказ fail-closed (problem + audit,
  молчаливого пропуска нет); префикс — однозначный принят,
  неоднозначный отказ; reverify evergreen-claim'а не создает срока,
  relative — сдвиг к моменту проверки; регрессия T7.9 (headless-claim
  не переиспользуется дедупом).
- `tests/scenario/test_evaluation_gates.py` — форма EVAL-4d в
  фикстуре (не в БД улик): 29 значимых claim'ов + 13 перепроверок
  (§7.2) → гейт 5 = 8/29 = 0.276 → passed; строгий вариант (5
  перепроверок по id) → 5/29 = 0.172 → failed; реперепроверка через
  MemoryService засчитывается (2 сессии); существующие тесты гейта 5
  (14/51 и граничные) не ослаблены.
