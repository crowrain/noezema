# ADR-0020: NUL-байт в тексте хоста заменяется видимым маркером на трёх границах (T7.46a, §3.3, §15.3)

Статус: accepted

Дата: 2026-09-25

Контекст: SMOKE-V12-K2, `docs/eval/SMOKE-V12-K2-report.md` §3.

## Контекст

Postgres не хранит U+0000 в JSONB: asyncpg кодирует NUL в строковом
параметре как JSON-эскейп `\u0000`, а серверный парсер JSONB его
отклоняет (`UntranslatableCharacterError: unsupported Unicode escape
sequence`). NUL-байт в stdout `python.execute` (выживший
`decode("utf-8", "replace")`, `apps/orchestrator/executor.py:148`) →
evidence-payload `stdout[:2000]` (`apps/orchestrator/evidence.py:104`)
→ report-payload (`orchestrator.py:794`) → INSERT audit-события —
свалил ВСЮ phase-1-транзакцию сессии: 8 шагов работы потеряны (0 строк
в sessions/audit_events/model_runs/session_staging/actions/checkpoints,
факт SELECT'ом). Ранние audit-INSERT'ы той же сессии выжили только из-за
1000-символьного капа `_cap_args` (NUL лежал на позиции 1200+).

Две свойства дефекта определяют решение:

1. **амплификация** — один NUL в любом поле payload'а роняет транзакцию
   вызывающего, а не запись;
2. **исчезновение без следа** — любой «тихий» фильтр (выброс NUL)
   ломал бы наблюдаемость: модель и аудит не узнали бы, что вывод
   инструмента содержал управляющий байт (улика SMOKE-V12-K2 —
   собственный `complete_reason` модели: «нечитаемый результат»).

## Решение

NUL в тексте хоста **заменяется видимым маркером** — четырьмя ASCII-
символами `\x00` (backslash, `x`, `0`, `0`) — и маскируется на трёх
границах (направление SMOKE-V12-K2-report.md §3.4):

- **маркер = `\x00`, НЕ U+FFFD.** U+FFFD уже является продуктом
  `decode("utf-8", "replace")` для НЕВЕРНЫХ байтовых последовательностей:
  U+FFFD смешал бы «в выводе был NUL» с «был невалидный UTF-8» — а это
  разные диагнозы (NUL = бинарный/контрольный вывод, невалидный UTF-8 =
  повреждённая кодировка). Маркер — чистый ASCII (безопасен в любом
  сериализаторе) и не содержит NUL, поэтому маскирование **идемпотентно**
  (повторное применение не меняет значение). Принятая оговорка: инструмент,
  напечатавший буквально 4 символа `\x00`, неотличим от замаскированного
  NUL — свойство любого видимого маркера; цель — наблюдаемость, не
  обратимость.
- **граница 1 — ИСТОЧНИК (захват вывода), `packages/domain/sanitization.py`
  (новый модуль: `mask_nul` / `mask_nul_deep`) + `apps/orchestrator/
  executor.py` (`_python_execute` stdout/stderr, `_workspace_read` content,
  маска ПЕРЕД капом — маркер тратит бюджет капа) + M2-путь
  (`packages/broker/broker.py`: `_sandbox_exec` stdout/stderr,
  `workspace.read` content — тот же принцип для sandboxed-исполнителя,
  §3.4-1).** Одна точка маскирования на захват гарантирует инвариант
  «в наблюдениях хоста нет \x00».
- **граница 2 — EVIDENCE (`apps/orchestrator/evidence.py`,
  `observation_to_evidence`).** Все входные строки маскируются ДО капов
  (mask-then-slice): `identity_hash` и payload считаются от ОДНОГО
  замаскированного значения — сохранённое значение и вход хэша не могут
  расстроиться; доверенный хост пересчитывает durable identity из
  payload на commit-границе (`memory/service.py:_identity_for`) — тот же
  замаскированный вход. Итоговый payload получает глубокую маску
  (`_finalize`) — защита от будущих полей.
- **граница 3 — AUDIT (`packages/domain/services/audit.py`,
  `AuditService.record`).** Рекурсивная маска `payload` + маска
  `public_summary` (TEXT-столбец тоже не хранит NUL) до кода записи.
  Это ПОСЛЕДНЯЯ линия: исключает амплификацию от ЛЮБОГО будущего
  источника NUL (текст модели, новое поле payload'а) — событие пишется
  с маркером, транзакция вызывающего не роняется.

**Диапазон:** маскируется только `\x00`. Другие C0-контрольные байты
легальны в JSONB и в наблюдаемых данных; их тихое удаление изменило бы
наблюдаемое — не сделано (зафиксировано как открытый вопрос в
отчёте T7.46).

**Контракты не меняются:** NUL появляется в наблюдениях только как
маркер — модель и аудит видят маркер (наблюдаемость сохранена);
схемы, `rules_hash`, замороженные payload'ы config-v2…v11 и промпты,
пины, пороги, корпуса, `ARCHITECTURE.md` не тронуты.

## Идентичности (identity)

- identity `python.execute` / `workspace.read` / `workspace.list`
  считается от замаскированных значений (mask-then-slice) — для ЧИСТЫХ
  выводов хэш побайтово совпадает с формулой до T7.46a (тест
  `tests/unit/test_evidence_nul.py::test_python_execute_evidence_clean_output_identity_unchanged`);
- durable identity пересчитывается доверенным хостом из payload на
  commit-границе — вход = тот же замаскированный payload → нет расхождения
  между значением в БД и входом хэша (тест
  `tests/scenario/test_session_nul_commit.py` сверяет `evidence.identity_hash`
  с `computation_identity(значение из БД, sha256 артефакта)`);
- identity `research.fetch` — hex-хэши (NUL-невязкие), маскирование
  провенанса — защитное (hex/UUID не содержат NUL).

## Тесты

красный→зелёный на обоих уровнях (красный — до фикса:
`UntranslatableCharacterError` на INSERT audit-события, воспроизведён
дословно):

- `tests/unit/test_sanitization.py` (4) — маркер, идемпотентность,
  вложенные структуры, ключи dict'а;
- `tests/unit/test_stub_executor.py` (+2) — NUL в stdout/stderr/
  workspace.read маскируется на захвате;
- `tests/unit/test_evidence_nul.py` (3) — маска + identity от
  замаскированного значения; чистый вывод — identity без изменений;
- `tests/scenario/test_audit_sanitization.py` (1) — NUL в top-level/
  nested dict/list + `public_summary` пишется в JSONB/TEXT с маркером
  (audit + outbox-близнец);
- `tests/scenario/test_session_nul_commit.py` (1) — целая сессия:
  `python.execute` печатает NUL на позиции 1200 (за капом `_cap_args`,
  в капе evidence — точная позиция SMOKE-V12-K2); ДО фикса — сессия падала
  на report-событии (весь commit откатился), ПОСЛЕ — КОММИТИТСЯ: state
  succeeded, report-событие записано, evidence несёт маркер, durable
  identity == пересчёту доверенного хоста.

## Не изменено

Схемы БД (миграций нет), `rules_hash`, замороженные payload'ы
config-v2…v11 и промпты (хэши не тронуты), пины, пороги, корпуса,
`ARCHITECTURE.md`, данные прошлых прогонов (SELECT only).

Открытые вопросы (отчёт T7.46, п.5): NUL в ТЕКСТЕ МОДЕЛИ (claim
statement → `session_staging` JSONB) вне трёх границ T7.46a — кандидат
на отдельное усиление → **закрыт дополнением T7.47a ниже**; прочие
C0-байты не маскируются (см. выше) → **закрыт фактом в T7.47a** (тест
`test_postgres_rejects_only_nul_among_c0_bytes`).

## Дополнение T7.47a: граница модельного текста (2026-09-25)

Текст, который генерирует МОДЕЛЬ (ответ LLM), — четвёртый канал NUL,
внешний трём границам T7.46a (наблюдения инструментов, evidence,
audit). Он входит в хост в ОДНОЙ точке — `LLMMiddleware.chat`
(`packages/llm_gateway/client.py`: `json.loads(content)` →
`model_validate`) — и расходится в столбцы, не тронутые T7.46a:

| Путь модельного текста | Столбец | JSONB/TEXT | До T7.47a |
|---|---|---|---|
| claim: statement/scope/search_statements/existing_claim_id | `session_staging.payload` | JSONB | **не покрыт, ФАТАЛЬНО** (откат phase-1) |
| question: text/origin/rationale | `session_staging.payload` | JSONB | **не покрыт, ФАТАЛЬНО** |
| evidence link: note | `session_staging.payload` | JSONB | **не покрыт, ФАТАЛЬНО** |
| план (steps/stopping_criteria/...) | `sessions.plan` | JSONB | **не покрыт, ФАТАЛЬНО** |
| отчёт верификатора (checks/gaps) | `sessions.verification` | JSONB | **не покрыт, ФАТАЛЬНО** |
| chunks извлечения (note; quote — вербатим из замаскированного док.) | `sessions.extraction` | JSONB | **не покрыт, ФАТАЛЬНО** |
| complete reason | `sessions.termination_reason` | TEXT | **не покрыт, ФАТАЛЬНО** (TEXT тоже отклоняет NUL — факт ниже) |
| любые поля (rationale, summary, claims…) | `audit_events.payload/public_summary`, outbox | JSONB/TEXT | покрыт (граница 3) |
| claim/evidence/question на commit | `claims.*`, `evidence.*`, `questions.*` | TEXT/JSONB | производные от staging (покрываются маской staging) |
| `model_runs` (токены/фингерпринт/finish_reason — мета API, сырой ответ в БД не пишется), `actions.arguments_hash` (только хэш), `checkpoints`, `commit_attempts` (хэши/UUID), `sources` (final URL хоста), `workspace_entries.path` (реальные файлы — NUL в пути невозможен) | — | — | N/A |

Решение — тот же принцип, те же примитивы (`mask_nul_deep`, маркер
`\x00`), две новые границы:

- **граница 4 — ВХОД МОДЕЛЬНОГО ТЕКСТА (`LLMMiddleware.chat`),
  маска СРАЗУ ПОСЛЕ `json.loads`, ПЕРЕД `model_validate`.** Все пять
  ролей (explorer/curator/planner/verifier/extractor) проходят через
  эту единственную точку — проверено: других парсингов ответа модели
  в хосте нет. Маска до валидации/капов/хэшей → сохранённое значение и
  вход любого хэша — ОДНО замаскированное значение (mask-then-hash),
  как в границе 2. На чистом входе маска — тождество: значения,
  хэши, дедуп-ключи побайтово как до T7.47a (тест
  `test_clean_response_passes_byte_identical` + существующие тесты
  identity/дедуп зелёны без изменений).
- **граница 5 — STAGING (защитная линия, `StagingService.record`),
  маска payload ДО `payload_hash`.** `session_staging.payload` —
  JSONB-канал знания; линия идемпотентна (граница 4 уже замаскировала)
  и страхует любой будущий не-gateway-источник; `payload_hash` и
  производный от него `commit_attempts.staging_hash` считаются от
  СОХРАНЁННОГО (замаскированного) значения — дедуп по statement
  (T7.9) работает по замаскированному тексту (тест
  `test_nul_in_claim_statement_commits_and_dedupes`: вторая сессия с
  тем же NUL-высказыванием дедупится, claim не дублируется).

**Факт по C0 (заменяет оценку «только `\u0000`» на измерение).**
Замер на реальной БД (UTF8, путь приложения SQLAlchemy+asyncpg; тест
`test_postgres_rejects_only_nul_among_c0_bytes`): среди C0-байтов
(0x00–0x1F) Postgres отклоняет **только NUL** — в JSONB
(`UntranslatableCharacterError` на `\u0000`) и в TEXT/VARCHAR
(`CharacterNotInRepertoireError: invalid byte sequence for encoding
"UTF8": 0x00`) — все остальные (0x01–0x1F) сохраняются. Диапазон
маскирования НЕ расширяется (\x00-only, как в T7.46a).
Побочное наблюдение SMOKE-V12-K2 §3.1 «NUL в бинарном text-параметре
Postgres принимает» **опровергнуто**: на пути приложения NUL в TEXT
отклоняется (сам ADR-0020 уже это учитывал: «TEXT-столбец тоже не
хранит NUL» — граница 3 маскирует `public_summary` по этой причине).

**Семантика повреждённого reason-токена.** `decision.reason` — токен
словаря хоста (CompleteReason или расширение), не свободный текст.
NUL-повреждённый «goal_reached\x00» после маски
(«goal_reached`\\x00`») не равен `GOAL_REACHED` → сессия получает
ТОТ ЖЕ статус, что любой неизвестный/повреждённый reason:
`SUCCEEDED_PARTIAL` (работа сохраняется, исход объясним по
сохранённому маркеру; хост не гадает намерение). До T7.47a этот путь
был фатальным: NUL писался в `sessions.termination_reason` в финальной
fenced-транзакции и ронял весь commit (TEXT отклоняет NUL — факт выше).

**Тесты T7.47a** (красный→зелёный; красный до фикса —
`UntranslatableCharacterError`/`CharacterNotInRepertoireError` с
откатом транзакции сессии):

- `tests/unit/test_gateway_nul.py` (3) — NUL в claim statement/scope,
  в complete reason/rationale маскируется на входе; чистый ответ
  проходит побайтово;
- `tests/scenario/test_model_text_nul.py` (7) — NUL в claim statement
  (commit + дедуп + payload_hash от замаскированного), в question text,
  в complete reason (PARTIAL + маркер + claim выжил), в LLM-плане, в
  отчёте верификатора, в chunks извлечения (сохранённый sha от
  замаскированного док.), эмпирический C0-провер (NUL — единственный
  отклоняемый байт).

**Не изменено (T7.47a):** схемы БД (без миграций), `rules_hash`,
замороженные payload'ы config-v2…v11 и промпты (хэши не тронуты),
пины, пороги, корпуса, `ARCHITECTURE.md`, данные прошлых прогонов.
