# ADR-0022: Терминальный статус сессии — из ЯВНОГО токена CompleteReason в начале reason, а не из точного равенства строки (T7.49, T7.37b, §6, §7)

Статус: accepted

Дата: 2026-09-26

Контекст: T7.37b (разбор SMOKE-V8-K2, `docs/STATUS.md` раздел T7.37 п.3),
повторяющийся в каждом K2-смоуке; `apps/orchestrator/orchestrator.py`
(вывод статуса: точное равенство `ctx.complete_reason ==
"goal_reached"`); `packages/domain/schemas/decision.py`
(`Decision.normalized_reason` — точное `CompleteReason(reason)`).

## Контекст

Модель K2 завершает сессию решением
`{"kind": "complete", "reason": "..."}`, где `reason` — строка, которую
хост до T7.49 сверял ТОЧНЫМ равенством с токеном `goal_reached`. Модель
часто вместо чистого токена пишет токен с суффиксом
(«goal_reached — утверждение: …», «goal_reached: …») или длинный
свободный текст («Вопрос отвечен и подтверждён двумя источниками…») —
хотя работа выполнена (claims/evidence закоммичены, вопрос отвечен).
Точное равенство → сессия `succeeded_partial`, вопрос
`PARTIALLY_ANSWERED` — статистика успеха занижается, сигнал в смоуках
портится.

**Измерение (T7.49, шаг 1)** — все терминальные сессии 6 БД прошлых
смоуков (SELECT only): `noezema-smoke-v8-k2`, `-v10-k2`, `-v10b-k2`,
`-v11-halogen`, `-v12-k2`, `-v13-k2` — 36 сессий (в v12 — 6 строк + 1
потерянная на NUL; в v10-k2 — 2 строки прогона):

| класс | определение | число | доля partial (20) |
|---|---|---|---|
| A | точный токен | 18 | — (2 partial: `budget_exhausted` — легитимный partial, не лечится нормализацией) |
| B | токен в начале + разделитель + суффикс | 3 | **3/20 (15%)** — все три `goal_reached` + суффикс (v8 `c79140b5`, v11 `1bcf6776`, v13 `0d0c1b3f`) |
| C | токен внутри/в конце, не в начале | 0 | — |
| D | чистый свободный текст без токена | 15 | **15/20 (75%)** — не лечится host-нормализацией |
| E | NULL / `unknown_action_outcome` | 0 | — |

Вывод: host-нормализация безопасна только для класса B (3 из 20
partial); **основная доля (класс D, 75%) лечится другим слоем**
(промпт/схема) — см. «Варианты для класса D» ниже.

## Решение

Правило безопасности: **хост НЕ имеет права выводить УСПЕХ из
свободного текста по смыслу или по ключевым словам.** Ложный `succeeded`
хуже ложного `partial`: `partial` — безопасное направление (как
`unknown_action_outcome` → `failed`). Никаких словарей русских фраз,
никаких эвристик «отвечен/подтверждён», никакого LLM. Нормализуется
только то, где модель **явно назвала токен закрытого enum**
`CompleteReason` **в начале** строки.

Единая чистая функция
`packages/domain/schemas/decision.py::normalize_complete_reason(
reason: str | None) -> CompleteReason | None` (единственный источник
правил; `Decision.normalized_reason` делегирует ей — логика не
дублируется). Правила:

- trim; регистр не важен;
- допустимы обрамляющие кавычки/бэктики/точка;
- токен обязан стоять **в начале** строки и заканчиваться на границе
  слова: за токеном — конец строки или разделитель
  (пробел, «—», «–», «-», «:», «;», «,», «.», «(»);
- «goal_reachedX», «not goal_reached», текст, где токен не в начале —
  НЕ распознаются (None);
- если в начале токен X, а в суффиксе упомянут другой токен — побеждает
  первый (X);
- не-строка/пустая → None.

Оркестратор (`apps/orchestrator/orchestrator.py`):

- вывод статуса: `succeeded` ТОЛЬКО когда
  `normalize_complete_reason(ctx.complete_reason) is GOAL_REACHED` И нет
  unknown actions (T2.21-правило не тронуто); при нераспознанной
  причине — прежнее поведение (`succeeded_partial`);
- `sessions.termination_reason`: канонический токен для распознанных,
  сырая строка для нераспознанных (как раньше); failed —
  `unknown_action_outcome` (как раньше);
- audit-событие complete: ОБА поля — `complete_reason` (сырой, как
  раньше) + `normalized_reason` (канонический токен или null). Сырой
  текст модели нигде не теряется.

`ARCHITECTURE.md` не требует точного равенства: §7 («Нормальное
завершение», строка 1042) показывает канонический пример
`"reason": "goal_reached"`, но не специфицирует семантику сравнения;
§6.6/§6.7 фиксируют только `succeeded_partial` для soft exhaustion и
operator stop; §14 — колонку `sessions.termination_reason`. Правка
уточняет реализацию в рамках молчания спеки, контракт не меняет
(схема `ModelResponse`/`Decision` для модели не тронута —
tool/schema hash не меняется).

## Изменения

- `packages/domain/schemas/decision.py`: `normalize_complete_reason`
  (новая чистая функция + константы правил); `Decision.normalized_reason`
  — делегирование (было: точное `CompleteReason(reason)`).
- `apps/orchestrator/orchestrator.py`: статус (нормализованное
  сравнение), `CommitPlan.termination_reason` (канонический токен /
  сырая строка), audit complete (payload += `normalized_reason`).
- `tests/unit/test_complete_reason_normalization.py` (новый): таблица
  нормализации — 20 позитивных + 13 негативных (все 8 обязательных из
  постановки + free-form из замеров + «   » + не-строки/пустые) +
  приоритет первого токена + не-строки.
- `tests/scenario/test_complete_reason_normalization.py` (новый,
  postgres + fake LLM): «goal_reached — <текст>» → succeeded +
  VERIFIED + `termination_reason="goal_reached"` + audit несёт и сырую,
  и нормализованную; свободный текст → succeeded_partial (прежнее
  поведение) + `normalized_reason=null`; unknown_actions +
  «goal_reached — …» → failed (`unknown_action_outcome`).
- `tests/unit/test_decision_envelope.py`: +тест делегирования свойства
  (существующие ассерты не ослаблены).
- `docs/STATUS.md`: раздел T7.49 (измерение, изменения, матрица §22.1
  п.13).

## Тесты

Красный→зелёный: сценарный «goal_reached — <текст> → succeeded»
красный на старом коде (`succeeded_partial` — дословно дефект T7.37b),
зелёный после правки; «свободный текст → partial» — статусный ассерт
зелёный и до, и после (страхование прежнего поведения), ассерт
`normalized_reason` — красный до (поля нет) / зелёный после;
unknown_actions — зелёный и до, и после (страж T2.21). Существующие
тесты точного равенства (`test_orchestrator.py`, `test_failpoints.py`,
`test_evaluation_gates.py` — 39 тестов) не ослаблялись и не
обновлялись: все ассерты прошли без изменений.

## Варианты для класса D (не реализовано — описание)

- **Вариант A: промпт explorer-v5.** Точная правка: «`reason` — РОВНО
  один токен `goal_reached | budget_exhausted | no_progress | blocked`
  без пояснений; пояснение — в `public_rationale`». Плюсы: лечит
  источник (75% partial), объяснение модели не теряется
  (`public_rationale` уже уходит в audit); не трогает контракт схемы.
  Минусы: новый payload config-v12 + пин (паттерн T7.38–T7.43); эффект
  модельно-зависим — K2 не следует инструкциям жёстко (halogen даёт
  точный токен 6/7 против 4/7 у K2 — модельная особенность, T7.44);
  требует контрольного смоука для замера.
- **Вариант B: enum-ограничение схемы** (JSON-schema enum у поля
  `Decision.reason`). Плюсы: жёсткая гарантия на уровне
  in-protocol-валидации — свободный текст физически невозможен.
  Минусы: **меняет схему, уходящую в модель** → меняется tool/schema
  hash и fingerprint — запрещено в рамках этой задачи и рискованно для
  K2/halogen (локальные движки чувствительны к схемам — ADR-0012;
  отбрасывание ответа моделью с enum-нарушением = потеря шага);
  нарушает декларацию поля «or a host-defined extension»
  (`decision.py`) — host-расширения (напр. `operator_stop`) ломаются.

Рекомендация (для отдельной задачи по решению пользователя):
**вариант A** — он же был предложением (3) разбора SMOKE-V8-K2 и
рекомендацией SMOKE-V13-K2-report.md §8; вариант B — только при
переходе на движок, гарантированно соблюдающий enum в
structured output.

## Не изменено

`ARCHITECTURE.md` (не требует правки — spec молчит о семантике
сравнения, см. «Решение»), схема `ModelResponse`/`Decision` для модели
(JSON-schema, уходящая в LLM — tool/schema hash не меняется), промпты
(explorer-v4/curator-v7 и пины), payload'ы config-v2…v11 и их хэши,
схемы БД (миграций нет), `rules_hash`, пороги, корпуса, данные прошлых
прогонов (SELECT only), существующие тесты.

## Дополнение (T7.50)

Вариант A реализован в T7.50: `prompts/explorer/explorer-v5.md`
(diff v4→v5 = строка версии + точечные правки ТОЛЬКО про завершение:
`reason` — ровно один токен из `goal_reached | budget_exhausted |
no_progress | blocked`, пояснение — в `public_rationale`; правило 5 —
сопоставление условий с токенами + «goal_reached только на
подтверждённый результатами инструментов ответ»; пара примеров
правильный/неправильный — паттерн безопасности примеров T7.39/T7.43:
тема вне всех корпусов, без реальных id),
`docs/eval/config-v12-payload.json` (= config-v11 с ЕДИНСТВЕННЫМ
изменением `prompts.explorer` → explorer-v5) и пин BOOTSTRAP_PAYLOAD
(explorer-v4 → explorer-v5, `BOOTSTRAP_SNAPSHOT_ID` не меняется).
Стражи: токены промпта = `CompleteReason` минус `operator_stop` (токен
хоста, его не выбирает модель — `test_explorer_prompt_completion.py`),
правильный пример → `GOAL_REACHED`, неправильный → `None` (связь с
T7.49); страж примеров `test_prompt_example_no_real_data.py`
распространён на explorer. Проверка эффекта на модели (класс D, 15/20
partial) — отдельный контрольный смоук (V14); решение о запуске и
разрешение на 192.168.1.48 — за пользователем. Вариант B не
реализован (схема модели не изменена).
