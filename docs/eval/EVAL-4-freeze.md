# EVAL-4 — замороженная конфигурация (подготовлено, НЕ запущено)

Статус: **подготовка завершена 2026-09-20; решение о запуске и об объёме серии — за пользователем.**
Документ НЕ переписывает [EVAL-3-freeze.md](EVAL-3-freeze.md) — всё, что там
зафиксировано (план запуска §3, арифметика гейтов §4, ограничение метода
blind-гейтов §5, baseline EVAL-2 §7, «что НЕ меняется» §8, риск опорной даты
и полуночь UTC §9.6, дефекты T7.19/T7.20 §10), действует для EVAL-4
без изменений, если здесь не сказано иное. Ссылки вида «freeze §N» ниже
ведут в EVAL-3-freeze.md, если не оговорено иное.

Все пороги §22.2 неизменны (immutable); исключение типов через ADR не
делается (решение от 16.09.2026, freeze §2).

## 1. Что изменилось относительно EVAL-3

| Компонент | EVAL-3 (заморожено) | EVAL-4 (этот документ) |
|---|---|---|
| Модель | `qwen36-35b-a3b-q6-mtp` (llama-swap) | **`halogen-flash-next`** (Qwen3.8 Flash Next, родной `.hgn`, halogen-flash-server 0.12.0) @ том же `http://192.168.1.48:8080/v1` (решение пользователя 2026-09-20, env `/home/denis/dsh1/noezema-llm.env`) |
| Профиль схемы | `none` (default, схема байт в байт) | **`halogen`** (env `NOEZEMA_LLM_SCHEMA_PROFILE=halogen`, T7.23/ADR-0012: снимает {format, pattern} из схемы, уходящей движку; валидация ответа хостом полной pydantic-моделью — не ослаблена). **Обязателен**: без него куратор падает HTTP 400 (SMOKE-HALOGEN, 0 claims) |
| Код прогона | `83d0ea9` (T7.18) | **`ff59dbf` (T7.23)** — +T7.19, T7.20, T7.21, T7.22, T7.23 (§2.3) |
| Payload'ы | `config-v2/v3-payload.json` (canonical `ffc98c9e…` / `2e93889c…`) | **`config-v4/v5-payload.json`** (canonical `7ef0f579…` / `f9c23ba9…`, §2.1): diff ровно 1 строка на payload — `context_window` под измеренный предел движка halogen |
| Корпус | `question-set-v2.jsonl` (sha256 `93c1a93a…`) | **тот же, не менять** (§2.2) |
| rules / пороги | rules-v2, rules_hash `a0b78e2d…`/`f96eeffc…` | **те же** (claim_type_rules не тронуты, §2.1); пороги §22.2 immutable |
| Объём серии | 50 сессий | **решение пользователя** — варианты с оценкой N под новую модель: §3 |

Сопоставимость с EVAL-1…EVAL-3d сломана (модель + T7.21 + T7.22 + T7.23 —
§4); EVAL-4 — новый baseline.

## 2. Замороженные артефакты

### 2.1 Конфигурации (`docs/eval/`, создана 2026-09-20)

- **v4** `config-v4-payload.json` (старт, аналог v2):
  - canonical sha256: `7ef0f579caebf4a9797af5c250ff92b5334130721114b540269fb248d3998edc`
  - file sha256: `74da16f94ee6d95bdb9288b91d21a64390b2ef23a135f4fb7739904d1265bece`
- **v5** `config-v5-payload.json` (mid-run флип, аналог v3):
  - canonical sha256: `f9c23ba9f9f065d41eb40a1bae11870c1839383219eea500733ea08180c3e626`
  - file sha256: `87fcdb7c03196d222ad7707b84f97ea28c02543f93f17a631446dac770eb9a52`

**Diff v2→v4 = ровно 1 строка** (`model.context_window: 40960 → 262144`);
**diff v3→v5 = те же 1 строка**; **diff v4→v5 = ровно 2 строки volatility**
(`external_fact`/`temporal_fact` → `temporal`) — тот же по смыслу, что v2→v3
(freeze §2.1). Ничего больше не тронуто.

Почему только `context_window` (а что именно НЕ менялось и почему):

| Поле | Значение | Обоснование |
|---|---|---|
| `model.context_window` | 40960 → **262144** | Измеренный прямыми HTTP (2026-09-20) жёсткий контекст движка halogen: ошибка движка «the context is 262144»; prompt+max_tokens обязаны влезать в 262144, иначе 400 fail-fast (§2.4). Для qwen36 40960 было бюджетным окном llama-swap-слота; для halogen фактический предел выше |
| `model.backend_context_limit` | 262144 (не менялось) | В v2/v3 уже 262144 (фактический слот llama-swap, freeze §2.6); halogen принудительно ограничивает тот же 262144 (§2.4) — значение осталось верным без правки |
| `model.max_output_tokens` | 8192 (не менялось) | Совпадает с env `NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192` (источник истины — `noezema-llm.env`); движок принимает max_tokens ≥ 8192 без ограничений до 32768 (§2.4); фактический максимум завершения на halogen — 7341 (SMOKE-HALOGEN2), 7115 (живая проверка ADR-0012 §5) — бюджет 8192 покрывает |
| `model.safety_margin_tokens` | 2048 (не менялось) | Как во всех прошлых прогонах |
| `model.model_alias` / `model.provider` | `thinker-local` / `openai-compatible` (не менялись) | Логический псевдоним; реальное имя модели живёт в env (`NOEZEMA_LLM_MODEL`), gateway читает его из env, а не из payload (`apps/orchestrator/main.py:36`); halogen-flash-server отдаёт OpenAI-compatible `/v1/chat/completions` |
| `model.sampling` | байт в байт как v2/v3: seed 42, temperature_by_phase {exploration 0.6, planning 0.25, verification 0.15}, top_k 40, top_p 0.95 | **Решение: фиксируем ровно `seed` (42), остальное оставляем движку — и фиксируем это явным образом.** (а) Host sampling движку НЕ шлёт: тело запроса gateway — ровно `{model, max_tokens, messages, response_format}` (`packages/llm_gateway/client.py`); фактическое sampling — дефолты движка halogen (контейнер: temperature 0, top_p 0.95, top_k 20, min_p 0). Значения payload по temperature/top_k/top_p поведенчески инертны. (б) Единственное функциональное поле — `seed` (манифест окружения сессии §8.7.3, `packages/memory/service.py`): оставляем 42 — идентично всем прошлым прогонам серии. (в) Замена значений на «фактические дефолты halogen» изменила бы canonical-хэш без изменения поведения — противоречило бы принципу минимального diff (freeze §2.6). Фактические дефолты движка зафиксированы в этом документе как факт деплоя (§2.4) |
| `token_budgets` (секции) | байт в байт как EVAL-2/bootstrap, Σ = 26624 | A/B-принцип freeze §2.6: секции не трогаем; пересчёт бюджета — ниже |
| `claim_type_rules`, `requires_scope`, правило E3 (≥2 независимые groups + ≥2 source_assertion), пороги §22.2 | байт в байт как v2/v3 | По решению: без изменений |
| Всё остальное (policy, research_proxy, session_limits, prompts, curiosity, repetition, wake_schedule, …) | байт в байт как v2/v3 | Смена модели их не требует |

**Расчёт TokenBudgets (§5.4.1, ловушка EVAL-3 freeze §2.6 пересчитана, а не
наследована):**

```
input_budget = min(context_window, backend_context_limit) − max_output_tokens − safety_margin
             = min(262144, 262144) − 8192 − 2048
             = 262144 − 8192 − 2048 = 251904
Σ секций     = 8192+3072+2048+2048+2048+4096+3072+2048 = 26624   (байт в байт EVAL-2)
запас        = 251904 − 26624 = 225280
TokenBudgets.validate() == []   (выполнено на обоих payload'ах, 2026-09-20)
```

(Для сравнения: v2/v3 — min(40960, 262144) − 8192 − 2048 = 30720, запас 4096.
Секции те же; поднят только потолок окна.)

**Правила оценки:** rules-v2 (`RULES_ENGINE_VERSION = "rules-v2"`,
`packages/memory/evidence.py`), код rules engine не менялся с `83d0ea9`
(T7.18). rules_hash (тот же `rules_hash(dict(claim_type_rules))`, что
eval-run фиксирует в строке рана) — совпадают с v2/v3, т.к.
claim_type_rules не тронуты:

- v4 (активен на старте): `a0b78e2d246f645641965c0946424db4defde3a4b06c779d78310f6bc0c467cd`
- v5 (после mid-run флипа): `f96eeffc527cbeb2ca3a0c3ffa207d2fff3a62582483ede1fa14c2b16b866d71`

### 2.2 Корпус

`docs/eval/question-set-v2.jsonl` — **по умолчанию тот же, 50 вопросов, НЕ
менять**. sha256 (файл): `93c1a93a4d6cbb4b29d31a30dedecb8488e5cf2135345a42d04bedbee11d31f4`
(перепроверен 2026-09-20 — не изменился). Структура и таблица 24 URL-фактов —
freeze §2.2 (не переписаны).

### 2.3 Код прогона

- HEAD на момент заморозки: **`ff59dbf`** (`ff59dbf6a96baac74bc1e763f1a8119949405943`,
  ветка `impl/from-scratch`, T7.23). Кодовых изменений после T7.18 (`83d0ea9`,
  код прогона EVAL-3d) — ровно пять:

| Коммит | Задача | Что |
|---|---|---|
| `61f38a5` | T7.19 | гейты считают ровно один head на claim (head активного snapshot по указателю `runtime_config_heads`, §14.1) — правка учёта дублей после mid-run активации (freeze §10.2–10.4); метод не меняет (freeze §10.4) |
| `51f759a` | T7.20 | quiesce-барьер online-активации: committed admission-запись сессии (миграция 0022 + trigger) блокирует flip при in-flight сессии + carry-over на fenced commit (ADR-0009) — закрывает гонку freeze §10.5 |
| `b59d2b8` | T7.21 | host-гейт покрытия источников, названных вопросом (`complete_rejected`), хост-пол бюджета, explorer prompt v4, audit `named_sources`/`source_coverage` (ADR-0010, freeze §11) |
| `9ed6480` | T7.22 | окно assertion-фрагмента: второе (value) окно группы A (ADR-0011, freeze §12) |
| `ff59dbf` | T7.23 | `LLMGatewayConfig.schema_profile` (env), transform {format, pattern}, `LLMRequestRejectedError` + audit `curator_error_kind` (ADR-0012, freeze §13) |

  (плюс docs-коммиты `868695c`, `705c04d`, `c426b7d` — без кода).

### 2.4 Модель, движок и измеренные пределы

Модель: **halogen-flash-next** (Qwen3.8 Flash Next, родной `.hgn`,
halogen-flash-server 0.12.0) @ `http://192.168.1.48:8080/v1`. Контейнер
поднят с `HALOGEN_KV_POOL_POSITIONS=524288`,
`HALOGEN_MAX_TOKENS_DEFAULT=16384`; дефолты sampling движка (конфиг
контейнера): **temperature 0, top_p 0.95, top_k 20, min_p 0** — host их не
переопределяет (§2.1, sampling).

Env запуска (источник истины — `/home/denis/dsh1/noezema-llm.env`):

```
NOEZEMA_LLM_BASE_URL=http://192.168.1.48:8080/v1
NOEZEMA_LLM_MODEL=halogen-flash-next
NOEZEMA_LLM_SCHEMA_PROFILE=halogen      # обязателен (T7.23/ADR-0012)
NOEZEMA_LLM_MAX_OUTPUT_TOKENS=8192
NOEZEMA_LLM_TIMEOUT_SECONDS=600
```

**Измерено прямыми HTTP-запросами к движку 2026-09-20** (чтение, без
прогонов/сессий; минимальная json_schema, `strict: true`):

| Замер | Результат |
|---|---|
| `GET /v1/models` | `halogen-flash-next` — `status: loaded` |
| `max_tokens` = 16384 / 16385 / 20000 / 32768 | все **HTTP 200** — жёсткого потолка max_tokens на 16384 **нет** (16384 = дефолт, не лимит); finish_reason=stop, 49 completion-токенов на запрос |
| prompt 262144 слов (≈262205 ток.) + max_tokens 8 | **HTTP 400**: «max_tokens 8 does not fit: prompt is 262205 tokens and **the context is 262144**, leaving room for -61» |
| prompt 384k / 500k слов | HTTP 400, то же сообщение (контекст 262144) |
| prompt 262000 слов (262061 ток.) + max_tokens 8 | **HTTP 200** (178 c) — в пределах предела принимается |
| prompt 253000 слов (253061 ток.) + max_tokens 8192 | **HTTP 200** (175 c) — наш реальный режим (8192) влезает |
| prompt 254000 слов (254061 ток.) + max_tokens 8192 | **HTTP 400**: «…leaving room for 8083» |

Вывод: движок **жёстко** ограничивает `prompt_tokens + max_tokens ≤ 262144`
(ошибка явная, fail-fast, не silent-обрезка). `HALOGEN_KV_POOL_POSITIONS=
524288` целиком в контекст не транслируется (фактический предел 262144 —
поэтому и зафиксированы оба поля model-секции в 262144). Запас бюджета
225280 токенов (§2.1) — overflow на реалистичных размерах контекстов
невозможен; в худшем случае движок отклонит запрос с явной ошибкой.
Факт замеров: prefill 253k токенов занимает ~3 мин — на реалистичных
контекстах сессии (максимум в smoke — 15659 prompt-токенов) не актуально.

Подтверждение работоспособности на реальной сессии (улики — обе smoke-БД,
SELECT только):

- **SMOKE-HALOGEN** (до T7.23, БД `noezema-smoke-halogen`): куратор — HTTP 400
  ×3 («unsupported keyword `format``), 0 claims, 0 evidence, 8 источников
  скачано, 17 model_runs (только exploring). Закрыто T7.23 + profile halogen.
- **SMOKE-HALOGEN2** (код `ff59dbf`, профиль halogen, БД
  `noezema-smoke-halogen2`): 3 вопроса корпуса → 2 сессии `succeeded`
  (temporal_fact **E3/supported**, 2 source_assertion каждый, rules-v2,
  rules_hash `a0b78e2d…`, confidence 0.75: ЕС-27 @01.01.2026 и ставка ЦБ
  14.00% @2026-09-20 — те самые факты, что в EVAL-3d давали E1 из-за
  дробления куратором), 1 сессия `succeeded_partial` без claim (обрыв JSON
  куратора — риск 1 §5). 18 model_runs, все `finish_reason=stop`, максимум
  7341 из 8192 completion-токенов.

### 2.5 Инфраструктура (для запуска, по решению пользователя)

- БД: **свежая `noezema-eval4`** (CREATE DATABASE + `alembic upgrade head`,
  контейнер `noezema-test-db`, порт 54329, `noezema/noezema_dev`). Не трогать:
  noezema-eval / noezema-eval2 / noezema-eval3 / noezema-eval3b /
  noezema-eval3c / noezema-eval3d / noezema-eval-draft* / noezema-smoke-halogen
  / noezema-smoke-halogen2 — улики прошлых прогонов, SELECT только.
- data-root: **`/home/denis/dsh1/eval4-data`** (свежая).
- Логи: **`/home/denis/dsh1/eval4-logs/`** (`eval4-run.log`,
  `eval4-activate-v4.log`, `eval4-worker.log`, `eval4-watchdog.log`).
- SearXNG: контейнер `noezema-searxng` держать запущенным (freeze §2.4).
- **Запуск — через `systemd-run --user`** (freeze §10.1: `setsid nohup` из
  headless-сессии dsh убивает процессы при завершении хода — так сорван
  EVAL-3c).
- План запуска — freeze §3 с заменой имён: label `EVAL-4`, БД
  `noezema-eval4`, payload'ы **v4/v5**, data-root/логи выше; параметры
  `--slo-seconds 3600 --seed 20260915 --blind-size 50` заморожены (как в
  серии EVAL-3); `--count` — по решению пользователя (§3).

### 2.6 Замороженные параметры прогона

| Параметр | Значение |
|---|---|
| SLO reassessment | 3600 с (как EVAL-3) |
| seed | 20260915 (как серия EVAL-3) |
| blind size | 50 (как серия EVAL-3; выгрузка ручной проверки — freeze §5) |
| phase_deadline / session_timeout | 1800 / 1800 (payload v4/v5, как v2/v3) |
| mid-run флип v4→v5 | **решение пользователя** (механизм гейтов 6/7; повторить можно только с T7.20 — он в коде `ff59dbf`) |

## 3. Объём серии — варианты (решение пользователя)

Базовые варианты — ADR-0008 §5 (A: 100 сессий/корпус v3; B: 75; C: 50/корпус
v2; D: 50 + устранение корня малого N). Оценки там — линейная экстраполяция
EVAL-3d на qwen36. **Под halogen оценки пересчитаны с оговорками.**

Исходные данные:

- EVAL-3d (qwen36, факт, 50 сессий): 35 claims (0.70/сессию), 18 supported
  (0.36), 14 supported ext/temp E3 (0.28), 17 temporal current (0.34).
  T7.21+T7.22-оценка на qwen36 (ADR-0010 §6 / ADR-0011 §6): гейт 2 → 22–24.
- SMOKE-HALOGEN2 (halogen, **3 сессии — мало, это планировочный диапазон,
  не статистическая оценка**): 2 claims, 2 supported E3 (0.67/сессию). Оба
  smoke-вопроса (ЕС-27, ставка ЦБ) — ровно те факты, что в EVAL-3d упали в
  E1 от дробления куратором; под halogen оба закоммичены одним claim'ом с 2
  source_assertion — дробления в выборке нет (n=2). 1 из 3 сессий потеряла
  claim из-за обрыва JSON куратора (риск 1 §5).

Что меняется при halogen:

1. **+ E3-ставка на URL-claim**: 2/2 (smoke) против 14/31 (EVAL-3d). Если
   halogen не дробит факты (в smoke — не дробит), пул supported ext/temp
   растёт относительно qwen36-оценки 22–24.
2. **− Обрыв JSON у куратора** (новый модель-специфичный риск): 1/3 сессий в
   smoke → сессия без claim (≈1 claim/сессию теряется). 95% ДИ для 1/3 —
   очень широкий (~1%–93%): фактический темп неизвестен.
3. Workspace-якоря и follow-up-механика reuse на halogen не проверялись
   (в smoke только одиночные URL-вопросы) — пересчёт по ним не сделан.

Оценка N (50 сессий, корпус v2, planning range):

| Гейт | qwen36 (EVAL-3d факт / T7.21+22 оценка) | halogen (оценка) | Комментарий |
|---|---|---|---|
| g2 external_temporal_e3 | 14 / 22–24 | **≈24–33** | верх — если дробления нет и обрывы редки; низ — если обрывы держат 1/3; n=3, ДИ огромно |
| g1 new_supported_refuted_e2 | 18 / ~26–28 | **≈28–37** | g2 + ~4 local (workspace), как в EVAL-3d |
| g5 significant_claim_reuse | 18 / ~26–28 | **≈28–37** | тот же пул значимых claim'ов; доля reuse — модель-зависима, не пересчитана |
| g6 due_stale_time_sensitive | 17 / ~17–20 | **≈11–17** | 17 × (1 − темп обрывов); **< 20 при любом темпе** — ограничение корпуса v2 (ADR-0008 §5 D: порог 20 не закрыть без +5–8 temporal-вопросов), модель не лечит |

Вывод для решения: g6 при корпусе v2 остаётся insufficient_sample при ЛЮБОЙ
модели — если цель приёмки §22.2, вариант D (или A с корпусом v3) обязателен;
g1/g2/g5 под halogen, вероятно, проходят N≥20 уже при 50 сессиях (при
темпе обрывов < ~30%). Повтор mid-run флипа v4→v5 — по решению (с T7.20 в
коде это безопасно для invariant'а head'ов).

## 4. Что изменилось после EVAL-3d и почему прогон несопоставим

| Изменение | Причина несопоставимости (одна строка) |
|---|---|
| T7.21 (ADR-0010, `b59d2b8`) | сессия, не скачавшая оба источника, названных вопросом, больше не завершается (host-гейт `complete_rejected`), + explorer prompt v4 и хост-пол бюджета — путь сессии и промпт другие, чем в EVAL-3d (freeze §11) |
| T7.22 (ADR-0011, `9ed6480`) | куратор видит до двух непересекающихся 2000-символьных окон в evidence (группа A — ДРУГОЙ текст, чем в EVAL-3d) → состав evidence и grade external/temporal claim'ов другие (freeze §12) |
| T7.23 (ADR-0012, `ff59dbf`) | на halogen схема, уходящая движку, теряет {format, pattern} (без профиля куратор вообще не работает — SMOKE-HALOGEN); валидация хоста не ослаблена (freeze §13) |
| Смена модели: qwen36-35b-a3b-q6-mtp → halogen-flash-next | другие веса/бэкенд (halogen-flash-server вместо llama-swap)/поведение модели — ломает сопоставимость с baseline EVAL-3d сама по себе, независимо от профиля и кода (freeze §13, ADR-0012 §6) |

Не ломают сопоставимость метода, но код другой: T7.19 (`61f38a5`) — правка
учёта head'ов в гейтах (freeze §10.4: числители/знаменатели без mid-run
активации те же); T7.20 (`51f759a`) — quiesce-барьер активации (flip
блокируется при in-flight сессии + carry-over; ADR-0009) — требование для
повтора mid-run флипа (ADR-0008 §5).

## 5. Риски и известные дефекты, с которыми входим в прогон

1. **Обрыв JSON у куратора на длинных ответах (halogen)** — 1 из 3 сессий в
   SMOKE-HALOGEN2 (вопрос HTTP 404, `succeeded_partial`/`budget_exhausted`,
   0 claims). По данным обеих smoke-БД (SELECT): в `noezema-smoke-halogen2` —
   18 model_runs (16 exploring + 2 consolidating у первых двух сессий), все
   `finish_reason=stop`, максимум 7341/8192 completion-токенов; в
   пострадавшей сессии — 10 exploring + **0 consolidating**, т.к. строка
   `model_runs` пишется только при УСПЕШНОМ вызове (`_curator`,
   `apps/orchestrator/orchestrator.py`): 3 неудавшиеся попытки куратора в
   строках model_runs **не учитываются** и видны только в audit — одно событие
   `session_state_changed`: `curator_error = "model output failed schema
   after 3 attempts: Unterminated string starting at: line 106 column 7
   (char 3954)"`, `curator_error_kind = "unavailable"`. Обрывался именно JSON
   ответа куратора (строка не завершилась на ~4k символах), а НЕ
   max_tokens-обрезка: все записанные вызовы — `finish_reason=stop`, далеко
   до 8192. Исследование сессии не выбрасывается; сессия честно
   `succeeded_partial` без знания. В `noezema-smoke-halogen` (до T7.23) —
   другой режим отказа: HTTP 400 движка в схеме, 17 model_runs (только
   exploring), 0 claims — закрыт T7.23. **Не чинится кодом** (ретраи
   gateway = 3, четвёртой не существует); эффект — минус claim'ы → минус N у
   гейтов 1/2/5/6 (§3). Мониторинг в ране: `curator_error_kind` в audit;
   если темп обрывов > ~1/10 сессий — останов и доклад (решение
   пользователя).
2. **Quiesce-гонка активации** — **закрыта T7.20** (`51f759a`, миграция
   0022 + trigger + carry-over; `test_quiesce_race.py`, ADR-0009): входим в
   прогон с фиксом; инвариант «нет claim'а с head только на superseded»
   закреплён тестом.
3. **Claim без значения в тексте источника (5 из 12 замеренных промахов,
   T7.22/ADR-0011 §6)** — **не чинится**: value-окно закрывает 7 из 12, у 5
   точное значение отсутствует в источнике; эти claim'ы останутся E1
   (hypothesis) и выпадут из supported-знаменателей.
4. **Ручная проверка слепой выборки EVAL-3d не выполнена** (ADR-0008 §4/§7,
   `EVAL-3d-blind-sample.md`): полная приёмка EVAL-3d не завершена; для
   EVAL-4 ручная проверка выборки (freeze §5) тоже обязательна —
   `hostctl blind-sample` после рана.
5. **Модельные as_of-артефакты** (ADR-0008 §3.5: 4 из 8 due в EVAL-3d —
   модель написала прошедшие даты в типизированное `as_of`) — известное
   следствие ADR-0007, не меняется.
6. **Полуночь UTC** — freeze §9.6: старт утром UTC; пересечение полуночи
   отмечается в отчёте, прогон не останавливается.
7. **Bimodальность reuse** (freeze §6.1): passed только при ≥9 якорях из 11
   через точный dedup statement; поведение halogen на follow-up'ах не
   проверялось (см. §3, п.3) — если halogen перефразирует, гейт 5 может
   провалиться математически. Это честный результат.
8. **Context overflow** — исключён: жёсткий предел 262144 с явным 400
   fail-fast (§2.4) + запас бюджета 225280 (§2.1).
9. **Prefill-задержка движка** на огромных промптах (~3 мин на 253k токенов,
   §2.4) — на реалистичных контекстах сессии (max 15659 в smoke) не
   влияет; зафиксировано как факт.

## 6. Предстартовый чек-лист (до eval-run)

1. **Свежая БД**: `CREATE DATABASE noezema-eval4; alembic upgrade head`
   (NOEZEMA_DATABASE_URL=…noezema-eval4); замороженные БД не трогать (§2.5).
2. **Активный snapshot = canonical v4**: `runtime_config_heads
   (scope='global').active_config_snapshot_id → config_snapshots
   .payload_sha256` = `7ef0f579caebf4a9797af5c250ff92b5334130721114b540269fb248d3998edc`
   (после `activate-online --payload docs/eval/config-v4-payload.json`);
   не совпадает — останов и доклад (freeze §9.5).
3. **Модель loaded**: `GET /v1/models` → `halogen-flash-next`
   `status=loaded` + прогрев малым infer-запросом (llama-swap грузит модель
   в слот по первому запросу; freeze §2.4).
4. **Профиль схемы в env**: `NOEZEMA_LLM_SCHEMA_PROFILE=halogen` выставлен
   (env-файл `noezema-llm.env`, `set -a; . …; set +a`) — без него куратор
   падает HTTP 400 (SMOKE-HALOGEN).
5. **Свежий data-root** `/home/denis/dsh1/eval4-data`; логи
   `/home/denis/dsh1/eval4-logs/`.
6. **SearXNG запущен** (`docker start noezema-searxng`, если остановлен).
7. **Запуск через `systemd-run --user`** — eval-run, worker-цикл
   (reassessment-tick) и watchdog (freeze §3, §10.1: setsid/nohup из dsh
   headless не переживает завершение хода).
8. **Старт утром UTC** (риск опорной даты, freeze §9.6); время старта
   фиксировать в отчёте о запуске.
9. **Перепроверка хэшей замороженных артефактов** до seed'а (§7) и хэша
   корпуса в строке рана.
10. **Worker-цикл запущен ДО флипа v4→v5 и на всё время рана** (freeze §3,
    п.4); watchdog активирует v5 только при ≥20 claim'ов (freeze §3, п.6).

## 7. Что НЕ меняется (и перепроверено 2026-09-20)

- Пороги §22.2 (immutable), rules engine (rules-v2, код без изменений с
  `83d0ea9`), SQL гейтов (с учётом T7.19 — правка учёта, метод не меняет,
  freeze §10.4), ADR-0005/0006, staging/fenced-commit инварианты,
  `ARCHITECTURE.md`.
- **Замороженные артефакты EVAL-3 не тронуты** — хэши не изменились:

| Файл | sha256 (файл) |
|---|---|
| `docs/eval/config-v2-payload.json` | `2b22b417f2422b693854e3338505e9ba1540d2ddda89a1bcf49342de0668f8c3` |
| `docs/eval/config-v3-payload.json` | `87278c77e2919ed0a0019c5c3f2b2e7de937b940869967b016978b8978be5525` |
| `docs/eval/question-set-v2.jsonl` | `93c1a93a4d6cbb4b29d31a30dedecb8488e5cf2135345a42d04bedbee11d31f4` |

  Канонические (EVAL-3): v2 `ffc98c9e…`, v3 `2e93889c…` — как в freeze §2.1.
- Секции `token_budgets` в v4/v5 — байт в байт как v2/v3 (Σ 26624).
- EVAL-БД прошлых прогонов (закрыты/улики) — не трогать, SELECT только.
