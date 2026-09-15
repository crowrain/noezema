
lines = s.splitlines(keepends=True)
cut = None
for i, ln in enumerate(lines):
    if ln.startswith("class ORMRuntimeConfigHead"):
        cut = i
        break
if cut is None:
    raise SystemExit("class not found")
s2 = "".join(lines[:cut])

doc1 = ("Single-row effective-config pointer (the row "
        "is always id=1). A config snapshot is "
        "effective only if it equals "
        "active_config_snapshot_id. A non-NULL "
        "activating_config_snapshot_id means an "
        "online activation is in flight; the "
        "worker must not start validation batches "
        "while it is set.")
cols1 = [
    ("active_config_snapshot_id", "uuid.UUID | None", "Uuid",
     "config_snapshots", ""),
    ("activating_config_snapshot_id", "uuid.UUID | None", "Uuid",
     "config_snapshots", ""),
    ("fence", "int", "Integer, nullable=False, "
     "default=0", None, ', server_default="0"'),
]
s2 += single_row_class("ORMRuntimeConfigHead",
    "runtime_config_heads", doc1, cols1)

doc2 = ("Single-row writer admission state (the "
        "row is always id=1). Set atomically "
        "by a session entering consolidation "
        "before heavy validation. The "
        "reassessment worker must not start a "
        "validation/write batch while a "
        "live commit intent exists. A session "
        "commit clears it in its terminal "
        "transaction.")
cols2 = [
    ("commit_intent_session_id", "uuid.UUID | None", "Uuid",
     "sessions", ""),
    ("commit_intent_at", "datetime | None", DT, None, ""),
    ("commit_intent_lease_expires_at", "datetime | None",
     DT, None, ""),
]
s2 += single_row_class("ORMWriterState", "writer_state", doc2, cols2)

s2 = re.sub(r"from sqlalchemy\.orm import Mapped, "
            r"\w+", "from sqlalchemy.orm import "
            "Mapped, " + mctok, s2)

open("r3b.py", "w").write(s2)
print("final lines:", len(s2.splitlines()))
