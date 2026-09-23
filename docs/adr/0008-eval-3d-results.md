# ADR-0008: EVAL-3d — исходы гейтов §22.2 (50 сессий, mid-run-активация v2→v3)

- Статус: принято
- Дата: 2026-09-19 (досчёт 20:34:26Z)
- Контекст: `docs/eval/EVAL-3-freeze.md` §9–§10 (дозаморозка, EVAL-3c, дефект T7.19);
  ADR-0004 (механизм), ADR-0005 (EVAL-1), ADR-0006 (EVAL-2), ADR-0007 (rules-v2);
  T7.19 (коммит `61f38a5`)

Числа гейтов и ci95 в этом ADR взяты из `evaluation_runs.gates` строки рана
(записаны при досчёте), а не пересчитаны; аналитические выборки — только
SELECT по `noezema-eval3d`.

> **Пометка (T7.35, ADR-0019, 2026-09-23):** payload'ы v2/v3, объявленные
> для EVAL-3d, несли ярлыки `curator-v2/explorer-v2` без пина
> содержимого; код `83d0ea9` уже содержал промпты T7.14 — **фактически
> шло `curator-v3/explorer-v3`**. См. ADR-0019, таблица криминалистики.
> Исторический текст не переписан.

## 1. Конфигурация и хронология

| Параметр | Значение |
| --- | --- |
| run | `faa3cded-2eec-4087-bc1e-60c242728b90` (label EVAL-3d) |
| БД | `noezema-eval3d` (оставлена как улика, данные не менять) |
| код прогона | `83d0ea9` (T7.18, HEAD на момент запуска) + документ `868695c` (freeze §9, без кода); досчёт выполнен на `61f38a5` (T7.19 — единственный кодовый дельта, правка учёта head'ов, замороженные артефакты не тронуты — сверка хэшей freeze §10.4) |
| модель | `qwen36-35b-a3b-q6-mtp` @ `http://192.168.1.48:8080/v1`, `max_output_tokens=8192` |
| config | старт — snapshot `974d4158` (canonical v2 `ffc98c9e…`); после флипа — `db622010` (canonical v3 `2e93889c…`), v2 → superseded |
| правила | `rules-v2` (ADR-0007, T7.17/T7.18); rules_hash строки рана `a0b78e2d…` (v2); после флипа `f96eeffc…` (v3) |
| пороги / SLO | §22.2 (immutable), `reassessment_slo_seconds=3600` — зафиксировано в строке рана |
| корпус | `docs/eval/question-set-v2.jsonl` (sha256 `93c1a93a…`), 50 вопросов |
| blind | seed 20260915, size 50 |
| сессий | 50 (окно 13:05:55 → 16:33:26 UTC), 246 model runs |

Хронология (UTC; EVAL-3c — `docs/eval/EVAL-3-freeze.md` §10.1):

| Время | Событие |
| --- | --- |
| 08:25:25–08:36:51 | **EVAL-3c** (`bd21973c…`) сорван по технической причине: `eval-run`/worker/watchdog были запущены `setsid nohup` из headless-сессии dsh и убиты при завершении хода; 2 сессии из 50. Закрыт штатно как прерванный (freeze §10.6) |
| 13:05:32.7 | активация v2 (fence 1, snapshot `974d4158` = canonical v2) — freeze рана |
| 13:05:54 | старт EVAL-3d через `systemd-run --user` (переживает завершение хода) |
| 13:05:55–16:33:26 | 50/50 сессий завершены: 24 succeeded, 26 succeeded_partial, **0 failed, 0 LeaseLost** (40 сессий под v2, 10 под v3) |
| 15:47:21.8 / 15:50:59.8 | сессия `6f45deea`: phase-1-транзакция открыта **до** флипа, коммит — **после** → claim `8bbbb06a` с head'ом только на v2 (quiesce-race, freeze §10.5; дефект активации — T7.20) |
| 15:47:32.977 | watchdog (et=20) активировал v2→v3: fence 2, audit `activation_published`, указатель `runtime_config_heads` → v3 |
| 15:47:32.9–37.7 | 20 reassessment-задач активации выполнены (completed=20, очередь пуста, pending heads = 0) |
| 16:33:26 | **eval-run упал** в `_finish → compute_gates → _provenance_complete`: `MultipleResultsFound` (дефект учёта дублей head'ов — T7.19); строка рана осталась `outcome=running` |
| 20:34:26.882 | **досчёт**: штатный `compute_gates + finish_evaluation_run` на коде `61f38a5` (скрипт `/home/denis/dsh1/finish-eval3d-run.py`, лог `/home/denis/dsh1/eval3d-logs/eval3d-finish.log`): eligible=50, completed=50, **outcome=insufficient_sample** |

## 2. Исходы 11 гейтов

EVAL-1/EVAL-2 — из ADR-0005/ADR-0006 (ci95 не публиковался до EVAL-3 —
правка отчётности freeze §2.5). EVAL-3d — из `evaluation_runs.gates`:

| # | Гейт | Порог | EVAL-1 | EVAL-2 | EVAL-3d: числ/знам | ci95 (EVAL-3d) | Исход (EVAL-3d) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | new_supported_refuted_e2 | ≥80% | 29/29 passed | 28/28 passed | **18/18** | 0.824–1.0 | **insufficient_sample** (N<20) |
| 2 | external_temporal_e3 | 100% | 0/0 insuff. | 0/0 insuff. | **14/14** | 0.785–1.0 | **insufficient_sample** (N<20) |
| 3 | eligible_sessions_with_outcome | ≥60% | 48/48 passed | 43/43 passed | 50/50 | 0.929–1.0 | passed |
| 4 | near_duplicate_questions | ≤15% | 0/48 passed | 0/43 passed | 0/50 | 0–0.071 | passed |
| 5 | significant_claim_reuse | ≥25% | **1/29 failed** (3.4%) | **1/28 failed** (3.6%) | **5/18** (0.278) | 0.125–0.509 | **insufficient_sample** (N<20) |
| 6 | due_stale_time_sensitive | <20% | 0/0 insuff. | 0/0 insuff. | **8/17** (0.471) | 0.262–0.690 | **insufficient_sample** (N<20) |
| 7 | reassessment_slo (3600 с) | 100% | 0/0 insuff. | 0/0 insuff. | 20/20 | 0.839–1.0 | passed |
| 8 | current_pending_invalid_ancestor | 0 | 0 passed (29 current) | 0 passed (28 current) | 0/34 | — | passed |
| 9 | high_severity_incidents | 0 | 0 passed | 0 passed | 0 | — | passed |
| 10 | blind_provenance_path | ≥90% | 29/29 passed | 28/28 passed | 34/34 | 0.899–1.0 | passed — **структурная проверка, §4** |
| 11 | blind_scope | ≥80% | 29/29 passed | 28/28 passed | 34/34 | 0.899–1.0 | passed — **структурная проверка, §4** |

**Overall: `insufficient_sample`** (нет failed, есть insufficient_sample —
правило finish ADR-0004/freeze §4).

### Отдельно: что дал бы СТАРЫЙ код с дублями (отчёт T7.19, freeze §10.5)

Без фикса T7.19 гейты считали СТРОКИ head'ов на всех snapshot'ах (25 current
на v2 + 34 на v3 = 59 при 35 claim'ах). Числа перепроверены SELECT'ом:

| Гейт | Старый код (дубли) | Исход, который опубликовал бы | Новый код (T7.19) | Почему старый исход ложен |
| --- | --- | --- | --- | --- |
| 1 | 32/32 | **passed** (1.0, N=32≥20) | 18/18 insufficient | «достаточная» выборка — дубли; реальный N=18<20 — измерение не состоялось |
| 2 | 24/24 | **passed** (1.0, N=24≥20) | 14/14 insufficient | то же; реальный N=14<20 |
| 5 | 10/32 (0.312) | **passed** (≥0.25, N=32≥20) | 5/18 insufficient | дубли и в знаменателе, и в числителе; реальный N=18<20 |
| 6 | 16/30 (0.533) | failed | 8/17 insufficient | знаменатель раздут; при N≥20 0.471>0.20 был бы failed, но N=17<20 — измерение не состоялось |
| 8 | 0/59 | passed | 0/34 passed | результат тот же, знаменатель раздут |
| 10/11 | — | **крах** `MultipleResultsFound` | 34/34 + 34/34 | per-claim запросы по мульти-результату — именно это убило ран в `_finish` |

Старый overall был бы `failed` (гейт 6) — с ТРЁМЯ ложными pass (гейты 1, 2, 5:
«пройдено на достаточной выборке» при выборке, раздутой дублями) и крахом
blind-гейтов. T7.19 предотвратил публикацию ложных pass: все четыре head-гейта
с малым реальным N честно закрыты как `insufficient_sample`.

## 3. Разбор (данные — SELECT по `noezema-eval3d`)

### 3.1 Что дал корпус v2

35 claims всего: **external_fact 13, temporal_fact 18, local_observation 4,
computed_result 0**. Head'ы на активном snapshot'е (v3, `db622010`): 34
current — E1/hypothesis ×16 (external 5 + temporal 11), E2/supported ×4
(local), E3/supported ×14 (external 8 + temporal 6). Плюс 1 claim (`8bbbb06a`,
temporal, E1) — только на superseded v2 (quiesce-race, §3.6).

Корпус v2 (freeze §2.2): 24 URL-факта (20 temporal + 4 external), 11 паков
(7 URL + 4 workspace; якорь + 2 follow-up), 12 workspace-вопросов. Модель
создала 31 external/temporal claim на 24 URL-вопроса (+7 — дробление факта в
несколько claims, например ставка ЦБ → 2 claims, Конституция → 2, PostgreSQL
18.6 → 2, ООН-193 → 4, ЕС-27 → 4) и 4 local claims на 12 workspace-вопросов.
`computed_result = 0`: в отличие от корпуса v1 (12 вычислительных вопросов),
в v2 их нет — это напрямую урезает знаменатель гейта 1.

### 3.2 Почему четыре гейта insufficient_sample (N<20)

- **Гейт 1 (N=18)**: знаменатель = новые claims с current supported/refuted
  head'ом на активном snapshot'е = 14 E3 (external/temporal) + 4 E2
  (local_observation). 16 E1/hypothesis external/temporal claims не входят
  (не supported). Корпус v2 не даёт computed-claims, а workspace дал всего 4
  local claims → 18 < 20.
- **Гейт 2 (N=14)**: знаменатель = supported external/temporal = ровно 14 E3.
  Из 31 созданного external/temporal claim 16 остались hypothesis/E1: у 15 из
  16 ровно **1 source_assertion и 1 independence-группа** (куратор зафиксировал
  факт по одному из двух источников — дробление, известное поведение
  freeze §9.3: «куратор разбил факт на два claim по одному источнику»), у 1
  (`0c3c2ebf`) — 2 assertion, но 1 группа. Ещё 1 (`8bbbb06a`) не имеет active
  head'а вовсе (гонка). Осталось 14 < 20.
- **Гейт 5 (N=18)**: значимые claims (current E2+ supported/disputed/refuted
  на активном snapshot'е) = те же 18 (14 E3 + 4 E2) → 18 < 20.
- **Гейт 6 (N=17)**: знаменатель = temporal claims с current head'ом на
  активном snapshot'е = 17 (из 18 temporal; `8bbbb06a` — без active head'а).
  20+ temporal-вопросов дали 18 temporal claims: follow-up'ы с точным
  statement dedup'ятся в якорный claim (новой строки нет) и дробление не
  добавляет temporal-массы → 17 < 20.

Вывод: все четыре insufficient — следствие одного корня — **малого числа
supported-claims**: дробление фактов куратором (16 E1) и отсутствия
computed-вопросов в корпусе v2. При N≥20 гейт 1 (1.0), гейт 2 (1.0) и гейт 5
(0.278) прошли бы, а гейт 6 (0.471 > 0.20) — **провалился бы**.

### 3.3 Reuse 5/18 (ci95 0.125–0.509): что доказывает и что нет

Переиспользованы (≥2 distinct-сессии через evidence/реvisions/зависимости)
ровно 5 из 18 значимых claims — все URL-якоря:

| claim | Факт | Сессий |
| --- | --- | --- |
| `d5461857` | Python 3.14.7 @15.04.2026 (T19) | 3 |
| `06f7b36e` | PostgreSQL 18.6 (T5) | 2 |
| `33c1cc65` | ЕС 27 стран @01.01.2026 (T20) | 2 |
| `8c569397` | ООН 193 гос. на текущую дату (T1) | 2 |
| `c49ca212` | Ключевая ставка ЦБ 14,00% (T3) | 2 |

**Доказывает**: механизм переиспользования (T7.8–T7.17: `memory.search`,
правило `dependencies`, cross-lingual FTS, assertion-текст в evidence)
отработал end-to-end на curated-профиле: 5 из 7 URL-якорей сосланы из ≥2
сессий (dedup-путём и dependency-рёбрами), доля 0.278 **выше порога 0.25** —
против 3.4–3.6% failed на корпусах v1 (EVAL-1/2, там перекрытия нет по
конструкции). Гипотеза «модель переиспользует накопленное знание на корпусе
с перекрытием» — не опровергнута и превысила порог на точке.

**Не доказывает**: N=18 < 20 → по §22.2 измерение не состоялось; ci95
0.125–0.509 **перекрывает порог 0.25** (нижняя граница ниже порога) —
статистически устойчивого вывода «доля reuse ≥ 25%» нет. Workspace-якоря
(4 local claims) переиспользованы не были вообще (по 1 сессии на claim) —
на них механизм не проверен.

### 3.4 E3: 14/14

Все 14 supported external/temporal claims — ровно **E3** (ни одного E4):
правило lift (≥2 независимые группы + ≥2 source_assertion) сработало на
curated-профиле, «строго по двум источникам» удержало модель от over-fetch.
Дорога к E3, отсутствовавшая в EVAL-1/2 (sealed-профиль, 0/0), работает;
но N=14 < 20 → insufficient_sample, а не passed.

### 3.5 due/stale: 8/17 (0.471)

8 due из 17 temporal claims с current head'ом на v3:

| claim | as_of | Источник due |
| --- | --- | --- |
| `55bf6368` (ООН @15.04), `d5461857` (Python @15.04), `50856fc7` (Python @15.04, follow-up) | 2026-04-15 | **по дизайну** — 3 old-as_of вопроса (T18/T19, priority 100): reverify_after = as_of+30d в прошлом |
| `e75aa02b` (ЕС 27, follow-up T20) | 2024-01-01 | **по дизайну** (якорь T20 @01.01.2026 → due даже с правильной датой 2026-01-01); модель записала as_of 2024 (опечатка года) |
| `2c372922`, `aa2626c5`, `8c569397`, `b56208f3` | 2026-01-01, 2024-05-22, 2026-05-20, 2026-08-13 | **артефакт модельного as_of**: вопросы «на текущую дату», но модель написала в типизированное поле `as_of` прошедшие даты → reverify_after в прошлом. Известное следствие ADR-0007/T7.18: scope-якорь = дата сессии (хост), а `reverify_after = as_of + 30d` — от типизированного as_of claim (поле модели) |

Итог: 4 due по дизайну + 4 due из-за модельных as_of. При N≥20 гейт дал бы
**failed** (0.471 > 0.20); модельные артефакты дают 4 из 8 — без них 4/17 =
0.235, всё ещё > 0.20.

### 3.6 Claim `8bbbb06a` — quiesce-race (freeze §10.5)

`8bbbb06a` (temporal_fact, «Спутник-1 запущен 04.10.1957 19:28:34 UTC»,
1 source_assertion: ru.wikipedia.org) — сессия `6f45deea` открыла
phase-1-транзакцию 15:47:21.8Z до флипа (15:47:32.9Z); незакоммиченные строка
сессии и claim были невидимы проверке «нет активных сессий» и cohort freeze;
коммит 15:50:59.8Z записал head под v2 (`sessions.config_snapshot_id`).
После T7.19 такой claim **тихо выпадает из текущего знания и из всех гейтов**
(нет current lifecycle по effective config, §14.1) — детерминированно и
покрено регрессионным тестом. Сам дефект активации (quiesce не закрывает
окно невидимой in-flight сессии) — **T7.20**. Claim не влиял на исходы:
hypothesis/E1 — выпал бы из supported-знаменателей в любом случае.

## 4. Blind-гейты — структурная проверка, не пройденные гейты §22.2

По freeze §5 («Ограничение метода»): §22.2 определяет слепую выборку как
**РУЧНУЮ** процедуру (человек судит, следует ли statement из evidence, и
публикует 95% ДИ). Автоматические `blind_provenance_path` / `blind_scope`
измеряют только структурную проекцию (linked evidence существует, evidence
решился в source/artifact, scope заявлен). Поэтому исходы 34/34 / 34/34 в
этом ADR — **структурная проверка, вынесенная отдельно, и не приравниваются
к пройденным гейтам §22.2**; полная приёмка требует ручной проверки
выгрузки §7.

## 5. Варианты набора выборки для следующего прогона

Ожидания — линейная экстраполяция фактических ставок EVAL-3d на сессию
(35 claims, 18 supported, 14 supported external/temporal, 17 temporal
current из 50 сессий), при том же поведении модели. **Решение — за
пользователем; здесь варианты, не выбор.**

| Вариант | Сессии / корпус | Ожидаемое N: g1 / g2 / g5 / g6 | Комментарий |
| --- | --- | --- | --- |
| A. 100 сессий (верх §22.2 «50–100 eligible sessions») | 100, новый корпус v3 (100 вопросов: ~40 URL-фактов, паки, workspace) | ≈36 / ≈28 / ≈36 / ≈34 | запас 8–14 над MIN_SAMPLE у всех четырёх гейтов; ~2× wall-time (EVAL-3d: ~3.5 ч на 50) и 2× нагрузка модели; требуется дозаморозка корпуса v3 (тестовый артефакт, не конфиг §22.2) |
| B. 75 сессий | 75, корпус v2 + 25 новых URL-вопросов | ≈27 / ≈21 / ≈27 / ≈25 | g2 впритык (запас 1) — риск insufficient при любом отклонении; экономия ~25% времени против A |
| C. 50 сессий, корпус v2, код без изменений (только T7.20) | 50, тот же | ≈18 / ≈14 / ≈18 / ≈17 | **честный baseline**: insufficient_sample снова — путь к приёмке нет; полезен как регрессионная проверка T7.20 на живой модели |
| D. 50 сессий + устранение корня малого N | 50, корпус v2 (+ 5–8 temporal-вопросов) | g1 ≈32–38, g2 ≈26–31, g5 ≈32–38, g6 ≈20–24 | (а) дробление фактов куратором — 16 E1 single-source claims → один claim с двумя источниками (правило в промпте, T7.14-механика); (б) g6 упирается в ЧИСЛО temporal claims (17): порога 20 не закрыть без добавления temporal-вопросов в корпус; (в) модельные as_of-артефакты (4 из 8 due) — известное следствие ADR-0007, либо фикс промптом, либо явное принятие |

Перпендикулярно вариантам: mid-run-флип v2→v3 (triggers due/stale и
reassessment SLO) в следующем прогоне можно повторить (только после T7.20 —
иначе quiesce-race снова даст claim без active head'а) или опустить; сам
флип — не гейт, а механизм, который проверяют гейты 6 и 7.

## 6. Итог

**Acceptance §22.2 НЕ пройден.** Overall = `insufficient_sample`: по правилу
(ADR-0004, freeze §4) overall = passed только если ни один гейт не failed и
ни один не insufficient_sample — `insufficient_sample` закрывает приёмку так
же, как `failed` (измерение не состоялось — порог не может быть
подтверждён), разница лишь в семантике: что именно не подтверждено.

Что изменилось относительно EVAL-2 (ADR-0006):

| | EVAL-2 | EVAL-3d |
| --- | --- | --- |
| failed-гейты | 1 (reuse 3.6%) | **0** |
| insufficient | 3 (e3, due, slo) | 4 (g1, e3, reuse, due) |
| reuse | 1/28 = 3.6% — failed | 5/18 = 27.8% — выше порога, но N<20 |
| E3-путь | 0/0 (sealed) | 14/14 = 100% E3 (curated) — путь работает, N<20 |
| due/stale | 0/0 | 8/17 = 47.1% (был бы failed при N≥20; 4 из 8 — модельные as_of) |
| reassessment SLO | 0/0 | 20/20 passed (~5 с на 20 задач активации) |
| сессии | 43/43, 7 LeaseLost (14%) | 50/50, **0 failed, 0 LeaseLost** (phase_deadline 1800) |
| новое | — | mid-run-активация v2→v3: механизм сработал, выявлены 2 латентных дефекта — T7.19 (дубли в гейтах, исправлен, цифры этого ADR — после T7.19) и quiesce-race (T7.20) |

Гипотеза «переиспользование накопленного знания» перешла из «не подтверждена»
(EVAL-1/2: failed 3.4–3.6% на безперекрытиевых корпусах) в «выше порога на
точке, измерение недостаточно» (27.8%, ci95 0.125–0.509). Следующий прогон —
по решению пользователя (варианты §5).

## 7. Слепая выборка для ручной проверки

`docs/eval/EVAL-3d-blind-sample.md` — выгрузка ручной проверки §22.2:
**34 claim'а** (все current-claims активного snapshot'а; запрошено 50 > 34 →
вся совокупность), **seed 20260915**, size 50, 52 evidence-строки.
Сгенерирована 2026-09-19 командой `noezemactl blind-sample --run
faa3cded-2eec-4087-bc1e-60c242728b90 --out …` (`hostctl/cli.py:741`) —
**только чтение БД** (SELECT).

**Как проверить, что выгрузка = измеренная гейтами выборка:**

1. Заголовок файла: `seed=20260915 size=50 sampled=34` — совпадает со строкой
   рана (`evaluation_runs.blind_sample_seed/size`).
2. **Порядок — тот же, что у гейтов:** и гейты (`compute_gates` →
   `blind_sample_claim_ids`), и выгрузка (`blind_sample_details`) вызывают
   ОДНУ и ту же функцию отбора `packages/evaluation/blind.py:
   blind_sample_claim_ids` — seeded-shuffle (seed 20260915) внутри страт
   `(claim_type, epistemic_status)` head'ов активного snapshot'а,
   пропорциональная аллокация size 50, остаток — из глобального seeded-
   shuffle. Файл перечисляет claims ровно в этом порядке (5 страт:
   temporal/supported → external/supported → local/supported →
   temporal/hypothesis → external/hypothesis).
3. Детерминизм: повторный запуск той же команды даёт **побайт идентичный**
   файл (проверено 2026-09-19 `diff'ом`) — выборка воспроизводится seed'ом.
4. Полнота: SELECT — 34 current head'а на активном snapshot'е `db622010…`;
   файл содержит ровно эти 34 claim'а (claim'а без head'а на активном
   snapshot'е — `8bbbb06a` — в выборку не входят, правило T7.19).
5. Сходимость с гейтами: знаменатели blind-гейтов 34/34 (`evaluation_runs.
   gates`) = размер выборки.

Оговорка для проверяющего: 5 из 52 evidence-фрагментов (local_observation,
4 workspace-claims) помечены «в artifact-хранилище недоступен» — замороженный
код (T7.5–T7.19) регистрирует строку observation-artifact в реестре, но
байты в content-addressed store не записывает. Эти 4 claim'а проверяются по
сохранённым workspace-файлам `/home/denis/dsh1/eval3d-data/workspace/notes/
{plan,reading,todo,glossary}.md` (statements с ними сходятся: reading.md —
3 книги, glossary.md — 2 строки, и т.д.).

## Доказательная база

- БД `noezema-eval3d` (run `faa3cded…`, закрыт 2026-09-19 20:34:26Z,
  outcome=insufficient_sample; гейты и ci95 — `evaluation_runs.gates`);
  БД не трогать.
- БД `noezema-eval3c` (EVAL-3c, сорван, закрыт как прерванный — freeze §10.6).
- Логи: `/home/denis/dsh1/eval3d-logs/` (`eval3d-run.log`,
  `eval3d-activate-v2.log`, `eval3d-finish.log`); data-root
  `/home/denis/dsh1/eval3d-data` (workspace + artifacts).
- EVAL-1/EVAL-2: ADR-0005/0006, БД `noezema-eval`/`noezema-eval2`.
