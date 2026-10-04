# ADR-0024: Приём вопроса оператором — `origin='message'`, идемпотентность по точному тексту, командная авторизация (T7.59, §5.3, §5.3.2, §13.1–§13.6)

Статус: accepted

Дата: 2026-10-04

Контекст: задача T7.59 («чтобы живой человек мог потрогать MVP»: задать вопрос
из браузера и увидеть, как он уходит в сессию). Точки входа: CLI `noezemactl
ask`, `POST /api/v1/questions`, форма на главной странице web UI. Касается
`packages/domain/services/question_intake.py` (новый сервис),
`apps/web/api.py`, `hostctl/cli.py::ask`.

## Контекст

Вопросы в реестре (`questions`, миграция 0002) до T7.59 создавали только два
продюсера:

1. сидинг корпусом — `hostctl/cli.py::eval-run` (origin `'seeded'`, `INSERT`
   с точной проверкой `SELECT 1 FROM questions WHERE text = :t`);
2. модель через staging — `packages/domain/services/staging.py::apply` по
   `new_questions` куратора (origin из схемы proposals, по умолчанию
   `'model_proposal'`, `parent_id = session.question_id`).

Спека при этом уже отводила место человеку: §5.3 в источниках кандидатов
перечисляет «сообщения человека», §5.3.2 — «seeded/message question → FIFO
selection», §14 перечисляет поле `origin`. Значение `QuestionOrigin.MESSAGE`
(`packages/domain/models/enums.py`) существовало, пропускалось CHECK-констрейнтом
миграции 0002 (список CHECK строится из enum) и входило в whitelist
`FIFOQuestionSelector.is_eligible` — но ни один продюсер его не создавал.

Существовавший `POST /api/v1/messages` (§13.6, жизненный цикл
`created→queued→delivered→acknowledged→answered|expired`) вопроса не создаёт:
`apps/orchestrator/orchestrator.py` доставляет сообщения в `ctx.messages` как
недоверенный текст контекста и предлагает модели `message.reply`; очередь FIFO
при этом не меняется. То есть «оператор спросил» и «NOEZEMA выбрал вопрос» были
разными контурами, и второго входа для человека не было.

## Решение

1. **Origin — существующее `message`.** Никакого нового значения enum, никакой
   миграции, никакого изменения CHECK/pins/snapshot: provenance оператора —
   это то значение, которое спека уже обещает (§5.3/§5.3.2). Альтернатива
   «новый origin `operator`» отклонена: закрытый enum → миграция + пересчёт
   hash/payload-конфигов + правка eligibility-whitelist, без выигрыша в
   смысле.

2. **Один сервис, три входа.** Правила приёма живут в
   `packages/domain/services/question_intake.py`: `validate_operator_question`
   (чистая функция: strip, непустой текст, ≤ `MAX_QUESTION_TEXT_CHARS = 2000`,
   priority — настоящий int в `[-100, 100]`, bool отвергается),
   `put_operator_question` (дедуп + вставка), `queue_position` и
   `question_queue` (вид очереди). CLI, API и форма — тонкие обёртки (AGENTS
   §4): правила нельзя развести по входам.

3. **Идемпотентность = точный текст после strip, по всему реестру.** Повтор
   возвращает существующую строку (`created=False` / HTTP 200 +
   `replayed=true`) и **не** меняет её priority. Область проверки: сопоставляется
   с любой строкой `questions`, включая сидинг корпусом и вопросы модели —
   повторная формулировка не должна плодить второй кандидат. Прецедент — тот же
   точный текстовый тест в сидинге `eval-run`. Альтернативы отклонены:
   - `UNIQUE (text)` в схеме — миграция, и она запретила бы осознанные дубли
     других продюсеров (вопросы модели truncate'ятся до 2000 символов →
     схлопывание разных формулировок);
   - дедуп по близким формулировкам на приёме — семантика §9
     (`packages/cognition/repetition.py`, Jaccard `rephrase_threshold` из
     снапшота, `no_progress_limit`) относится к выбору («стоит ли ещё одна
     сессия»), а не к приёму («оператор уже присылал этот текст?»). Разные
     вопросы — разные механизмы.

4. **Авторизация = семейство Command API (T3.18 + T3.24).** `POST
   /api/v1/questions` требует `X-Admin-Token` (тот же токен, что и команды; 401
   при отсутствии/несовпадении) и так же отказывает на деградированном хосте
   (423 с телом `host_not_healthy`, идентичным `/api/v1/commands`). `GET
   /api/v1/questions` — открытый query-эндпоинт (§13.1, наблюдательный режим).
   Свободный текст вопроса не парсится как команда: closed-enum
   `operator_command` в этом пути не участвует (инвариант §13.2 сохранён).

5. **Priority — единственный рычаг «ВПЕРЁД очереди».** Операторское значение
   пишетcя в `questions.priority` и участвует в упорядочивании FIFO
   (`priority DESC, created_at ASC, id`) без изменения селектора; при равных
   приоритетах действует обычный FIFO по возрасту — привилегии «потому что
   оператор» нет. `queue_position` считает позицию по тому же запросу
   `QuestionRepository.list_candidates`, который читает селектор (в окне
   `QUEUE_WINDOW = 500`; глубже — `None`).

6. **Аудит: нового типа нет.** Закрытый реестр `AuditEventType` (§14.4) не
   содержит типа приёма вопроса; `question_selected` — событие сессии,
   `MESSAGE_CREATED` и `OPERATOR_COMMAND_RECEIVED` существуют в enum, но не
   производятся ни одним продюсером (проверено grep'ом по `packages/ apps/
   hostctl/`). Изобретать тип или записывать вопрос под чужим типом запрещено
   задачей и AGENTS §3. Долговременной записью приёма остаётся сама строка
   `questions` (`origin='message'`, `created_at`) — её же читает GET очереди.
   Решение «поднять unused-типы аудита для messages/commands» — отдельная
   задача, не эта.

7. **Staging-инвариант не затронут.** Модель по-прежнему меняет знание только
   через `session_staging` (`VALID_OPS`, apply в commit-транзакции). Приём
   оператора — host-контурная запись строки реестра вопросов через domain
   сервис, того же класса, что сидинг `eval-run`; прямой записи от модели не появляется.

## Изменения

- `packages/domain/services/question_intake.py` (новый) — константы bounds,
  чистая валидация, `find_question_by_text`, `put_operator_question`,
  `queue_position`, `question_queue`, `intake_error_payload`.
- `apps/web/api.py` — `QuestionIn` (bounds из сервиса), `GET
  /api/v1/questions` (view очереди: id, текст, origin, state, priority,
  created_at, позиция, последняя сессия), `POST /api/v1/questions` (201 при
  создании, 200 при реплее, 400 при отказе валидации, 401/423 по правилам
  команд); главная страница получила форму «Задать вопрос» (текст, priority,
  поле токена) и таблицу очереди; токен хранится в `sessionStorage` браузера
  (в UI нет серверальной сессии и нет нового JS-пакета — тот же vanilla
  `fetch`, что на остальных страницах).
- `hostctl/cli.py::ask TEXT [--priority N]` —печат id, origin/state/priority и
  позицию в очереди; exit 2 при отказе валидации или без
  `NOEZEMA_DATABASE_URL`.

## Тесты

- `tests/unit/test_question_intake.py` — bounds (ровно `MAX`, ±1), strip,
  whitespace-only, не-строка, bool-priority, вне диапазона; отдельно —
  «operator question FIFO-eligible without touching the selector» (guard, что
  origin `message` остаётся в whitelist eligibility).
- `tests/scenario/test_operator_question_intake.py` — PostgreSQL: candidate c
  origin `message`, реплей по точному тексту (в т.ч. с другими обрамляющими пробелами и с
  другим priority → приоритет НЕ меняется), реплей против засеянного
  корпуса, позиция FIFO и «priority ставит вперёд», ties по возрасту, вид
  очереди с аннотацией сессии.
- `tests/scenario/test_web_questions.py` — 401 без/с неверным токеном (и что
  ничего не добавилось), 201 + очередь, идемпотентный реплей (тот же id, тот
  же count), открытость GET на деградированном хосте + 423 на POST с телом
  `host_not_healthy`, 400 (whitespace) и 422 (priority вне диапазона,
  неизвестное поле `origin`), маркеры формы/таблицы в HTML главной страницы.
- `tests/scenario/test_cli_ask.py` — CliRunner: id+позиция в выводе, строка в
  БД с origin/state/priority, реплей тем же id, «ВПЕРЁД FIFO», exit 2 на
  пустом тексте/вне диапазона и без `NOEZEMA_DATABASE_URL`, отсутствие опции
  `--origin` (provenance не выбирается оператором).

## Не изменено

`ARCHITECTURE.md`, модели/схемы, `config-v*` payload'ы, промпты и пины,
`tool_schema_hash`, корпусы, пороги, миграции (ни одной новой), enum'ы
(`QuestionOrigin`, `QuestionState`, `AuditEventType`), поведение селектора,
`POST /api/v1/messages` и жизненный цикл сообщений (§13.6), остальные CLI
команды, default web bind (`127.0.0.1:8321`) и default executor (stub).
Ни один существующий тест не изменён и не ослаблен.

## Что остаётся непроверенным

- Приём questions на живой ВМ стенда (`.92`) и их прохождение через wake tick с
  реальным LLM — проверено только на scratch-БД с FakeLLM (T7.59 часть б,
  `docs/STATUS.md`).
- Дедуп по точному тексту не защищает от близких формулировок: два вопроса «6×7»
  и «шесть умножить на семь» — два кандидата; дальше работает §9-guard, а не
  приём.
- Позиция вне `QUEUE_WINDOW` сообщается как `None`: очередь глубже 500
  кандидатов в MVP не просматривается (селектор читает голову списка).
