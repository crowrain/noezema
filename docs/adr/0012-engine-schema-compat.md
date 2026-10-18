# ADR-0012: T7.23 — совместимость структурированного вывода с движками, не принимающими часть ключевых слов JSON Schema (halogen: format/pattern), fail-closed audit

- Статус: принято
- Дата: 2026-09-20
- Контекст: смоук-тест SMOKE-HALOGEN (3 вопроса корпуса, модель
  halogen-flash-next (Qwen3.8 Flash Next, `.hgn`) на
  http://192.168.1.48:8080/v1, БД `noezema-smoke-halogen` — улика,
  только SELECT). Исследование работало (8 источников, 11 чтений), но
  claims = 0, evidence = 0, фаза consolidating не запускалась ни разу.
  Audit: `curator_error` = HTTP 400 «the engine refused this request:
  json_schema not supported: unsupported keyword `format`». В схеме
  `CuratorProposal` (`packages/domain/schemas/staging.py`) `format`
  встречается дважды (`claim_id` → uuid, `as_of` → date-time); схема
  explorer'а (action-envelope) `format` не содержит — поэтому
  exploring работал, а куратор падал. Хост отработал корректно
  («куратор недоступен, claim не предложены»), но в журнале отказ
  движка был неотличим от «модель недоступна».

## 1. Данные: таблица «ключевое слово → принимает ли halogen»

Метод: прямые HTTP-запросы к `/v1/chat/completions` с моделью
halogen-flash-next, одно ключевое слово на запрос (минимальная схема,
`strict: true`), 2026-09-20. Движок сообщает только ПЕРВОЕ
неподдерживаемое ключевое слово, поэтому каждое слово, используемое
нашими схемами, проверено отдельным запросом. HTTP 200 = принято
(генерация запускается), 400 = отвергнуто.

| Ключевое слово | halogen | Примечание |
| --- | --- | --- |
| type / properties / required (базовый object) | 200 | baseline |
| additionalProperties (false) | 200 | |
| enum | 200 | |
| minLength / maxLength | 200 | |
| minItems / maxItems | 200 | |
| minimum / maximum | 200 | |
| `$defs` / `$ref` | 200 | |
| anyOf (включая ветку `{"type": "null"}`) | 200 | |
| default | 200 | |
| title | 200 | |
| description | 200 | |
| `strict: true` / `strict: false` | 200 / 200 | |
| **format (uuid)** | **400** | `unsupported keyword `format`` |
| **format (date-time)** | **400** | `unsupported keyword `format`` |
| **pattern** | **400** | `unsupported keyword `pattern`` |

Снимается ровно то, что отвергнуто: **{format, pattern}**. Всё
остальное движок принимает и в схеме не трогается.

## 2. Что снимается и что НЕ ослабляется

- Снятие (transform `strip_schema_keywords`,
  `packages/llm_gateway/schema_compat.py`) применяется **только к
  схеме, которая уходит движку**: ключевые слова вырезаются на всех
  уровнях (top, `$defs`, `properties`, ветки `anyOf`, `items`), чистая
  функция, идемпотентна, вход не мутирует, порядок остальных ключей и
  все остальные значения не меняются.
- **Валидация ответа на стороне хоста НЕ ослабляется** (явно в
  докстринге `LLMMiddleware.chat`): ответ по-прежнему парсится и
  валидируется **целой** pydantic-моделью (uuid, date-time, pattern,
  все length/budget-ограничения). Движок перестаёт быть валидатором
  формата — валидатором остаётся хост (§3.7).
- Следствие: модель теперь МОЖЕТ вернуть не-UUID в `claim_id` (для
  движка это просто строка) — хост отклонит (LLMSchemaError, ретраи
  схемы). Живая проверка (§5) зафиксировала именно этот сценарий:
  движок принял ответ с `claim_id: "c:..."`, host pydantic отклонил
  (`uuid_parsing`).

## 3. Переключатель: профиль возможностей в env, НЕ в config snapshot

Выбран вариант: поле `LLMGatewayConfig.schema_profile` (env
`NOEZEMA_LLM_SCHEMA_PROFILE`), значения:

- `"none"` — **по умолчанию = текущее поведение**: схема уходит
  байт в байт как её произвёл pydantic (transform не применяется).
  Для qwen36-35b-a3b-q6-mtp запрос остаётся байт в байт прежним →
  сопоставимость с EVAL-3d на этой модели не тронута;
- `"halogen"` — снимает {format, pattern} (то, что отвергает
  движок).

Почему env, а не ключ в config snapshot (`model.*`):

1. Параметры развёртывания гейтвея (base_url, model, api_key,
   max_output_tokens) уже живут в env; «какие ключевые слова
   принимает движок» — свойство ДЕПЛОЯ (какой движок обслуживает
   модель), а не per-session правила/бюджеты, которые по инварианту
   effective config живут в snapshot.
2. Новый ключ в payload менял бы canonical hash payload'а → пришлось
   бы трогать замороженные `config-v2/v3-payload.json` (sha-зафиксированы,
   EVAL-3-freeze §2.1) — запрещено.
3. Значение по умолчанию `"none"` даёт прежнее поведение на
  замороженных конфигах без единого изменения в них.

Неизвестное имя профиля — fail-fast: `ValidationError` при сборке
`LLMGatewayConfig` (запуск не стартует с неверным профилем).

## 4. Fail-closed: отказ движка отличим от «модель недоступна»

- Новый класс `LLMRequestRejectedError(LLMError)` (
  `packages/llm_gateway/client.py`): не-транзитный HTTP 4xx — движок
  НА МЕСТЕ, но отвергает именно этот запрос (схема/параметры/модель/
  auth). Не ретраится (повторный запрос отклонят так же), подкласс
  LLMError — остальные обработчики фаз не меняются.
- `Orchestrator._curator`: отдельный `except LLMRequestRejectedError`
  → audit `session_state_changed` с payload
  `{"curator_error": ..., "curator_error_kind": "request_rejected"}` и
  public_summary «curator request refused by engine (schema/params);
  no claims proposed — check schema_profile for this engine». Ветка
  «модель недоступна» (транзитные ошибки, исчерпанные ретраи) помечена
  симметрично `curator_error_kind = "unavailable"`.
- Новый `AuditEventType` не вводится: `session_state_changed` —
  корректное фазовое событие (консолидация завершилась без
  claim'ов), а `curator_error_kind` + отдельный public_summary —
  отдельный признак, объяснимый по журналу одной строкой:
  `SELECT ... WHERE payload->>'curator_error_kind'='request_rejected'`.
- **Мягкий отказ, а не валить сессию** — обоснование: отказ движка в
  схеме — это расхождение хоста/деплоя (движок не поддерживает
  ключевые слова), а не познавательная ошибка сессии. Исследование уже
  отработало (evidence собрано); жёсткий провал на консолидации
  выбросил бы эту работу и не добавил бы знания. Fail-closed
  соблюдён в том смысле, что он обязателен: claim'ы НЕ создаются,
  сессия не притворяется знающей, отказ ЯВНЫЙ в audit (отдельный тип/
  признак + отдельная public_summary) и объясним по журналу —
  оператор видит «движок отверг схему, проверьте schema_profile», а не
  «модель недоступна». Поведение согласовано с §6.5 (curator
  unavailable → host failure report, no claims proposed).

## 5. Живая проверка (прямые HTTP-запросы, без eval-run и сессий)

Реальная схема `CuratorProposal.model_json_schema()`, прогнанная
через `strip_schema_keywords(..., HALOGEN_UNSUPPORTED_KEYWORDS)`
(2×`format` → 0, всё остальное ключ в ключ сохранено), отправлена
движку как есть (name=CuratorProposal, strict=true) с реалистичным
промптом куратора:

- Запрос: `POST /v1/chat/completions`, model=halogen-flash-next,
  max_tokens=8192, response_format.json_schema = реальная схема без
  format/pattern (сокращённо: `{"type":"object","properties":
  {"summary":{...},"claims":{"items":{"$ref":"#/$defs/ClaimProposal"},
  "type":"array"},"evidence_links":{...},"new_questions":{...}},
  "required":["summary"],"additionalProperties":false,"$defs":{...
  claim_id: {"title":"Claim Id","type":"string"} — format снят;
  as_of: anyOf[{"type":"string"}, {"type":"null"}] — format снят
  ...}}`).
- Ответ: **HTTP 200** (145.7 с), `finish_reason: stop`,
  completion 7115 токенов (из них 6787 reasoning), содержимое —
  корректный JSON: claim «Ключевая ставка ЦБ РФ составляет 14,00% по
  состоянию на 27.07.2026» (as_of `2026-07-27`, dependency на UUID
  `3f2c6f4d-...`), 2 evidence_links supports, 1 new_question.
- `CuratorProposal.model_validate(ответ)`: **OK** — uuid распарсен в
  UUID, date-time в datetime, enum/budgets валидны.
- Контрольный запуск (UUID в контексте не давался): модель выдала
  `claim_id: "c:3f2c6f4d-..."` — движок принял (format не видит),
  host pydantic отклонил (`uuid_parsing`) — доказательство, что
  валидация хоста не ослаблена.

## 6. Что это значит для сопоставимости

- **Смена модели (halogen вместо qwen36-35b-a3b-q6-mtp) ломает
  сопоставимость с baseline EVAL-3d сама по себе** — независимо от
  schema_profile: другая модель (другие веса/бэкенд/поведение),
  включая и то, что curating на halogen требует profile "halogen",
  тогда как на qwen36 schema уходит байт в байт. Это третья
  независимая причина после T7.21 (§11 freeze) и T7.22 (§12 freeze).
- Профиль сам по себе сопоставимость НЕ ломает: на qwen36
  (default "none") запрос байт в байт прежний (зафиксировано тестом
  `test_default_profile_sends_schema_byte_identical`).
- Решение о модели и о прогоне — за пользователем (freeze §13).
  БД `noezema-smoke-halogen` — улика, SELECT только.

> **Дополнение (2026-09-24, T7.36).** Второй профиль по тому же механизму:
> `llamacpp-rocmfpx` снимает `minLength`/`maxLength` для сборки llama.cpp
> ROCmFPX-k2 (модель K2 Horizon MoVA 36B A4B ROCmFP4 FAST на .48): она
> отвергает полные схемы с HTTP 400 `failed to parse grammar`, а
> `format`/`pattern` принимает. Хост по-прежнему валидирует ответ полной
> pydantic-моделью. Замер и обоснование — STATUS.md, раздел T7.36. Текст
> выше не переписан.

