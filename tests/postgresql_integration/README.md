# PostgreSQL integration check

The regular suite uses SQLite only to exercise transaction boundaries. The
PostgreSQL-specific migration chain can be verified against a disposable
PostgreSQL 15+ database explicitly:

```powershell
$env:NOEZEMA_TEST_DATABASE_URL = "postgresql+psycopg://user:password@localhost/noezema_test"
.venv\Scripts\pytest.exe -m postgresql_integration
```

The test upgrades an empty database, verifies the bootstrap rows, and downgrades it
back to an empty schema. Never point this variable at a database containing data.
