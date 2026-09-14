# ADR-0002: Artifact Store — content-addressed filesystem

- Статус: принято
- Дата: 2026-09-14
- Контекст: `docs/PLAN_FROM_SCRATCH.md` §4 (T2.11), ARCHITECTURE §5.11

## Контекст

Спека (§5.11) требует content-addressed хранилище артефактов (SHA-256,
неизменяемость, provenance на chunk-уровне), но не предписывает транспорт:
локальный filesystem или локальный S3-совместимый сервер (открытый вопрос
§21.9).

## Решение

В v1 используется **filesystem content-addressed store**:
`$NOEZEMA_ARTIFACT_DIR` (по умолчанию `/var/lib/noezema/artifacts`),
layout `artifacts/xx/<sha256>` (первые 2 символа хеша — подкаталог),
метаданные — в доменной БД (`artifacts`, `artifact_chunks`).

- Запись: temporary file → `fsync` → atomic rename (crash-safe).
- Объекты неизменяемы: повтор put с тем же hash — идемпотентный no-op;
  hash mismatch — ошибка.
- Сетевой транспорт (S3-совместимый) — вне v1; интерфейс
  `ArtifactStore` (`put/get/exists/head`) спроектирован так, чтобы
  транспорт был сменным профилем.

## Последствия

- Плюсы: минимум зависимостей (нет отдельного сервиса), прозрачная
  отладка, backup = копирование каталога + `backup_manifests`.
- Минусы: нет встроенного реплицирования; при росте корпуса потребуется
  GC (§15.3) — учтён в M7.
