import py_compile, os, sys
sys.path.insert(0, os.path.join("..", "..", ".."))
py_compile.compile("r3b.py", doraise=True)
print("COMPILE OK")
import importlib
m = importlib.import_module("packages.domain.models.r3b")
from packages.domain.models.base import Base
tables = ["reassessment_jobs", "closure_manifests",
          "dependency_barriers", "runtime_config_heads", "writer_state"]
for t in tables:
    tbl = Base.metadata.tables[t]
    print("==", t, "cols:", len(tbl.columns), "indexes:", len(tbl.indexes),
          "ucs:", len(tbl.constraints))
    for c in tbl.columns:
        fk = ""
        if c.foreign_keys:
            fk = "fk:" + ",".join(str(x.target_fullname) for x in c.foreign_keys)
        print("  ", c.name, "|", str(c.type), "| null=", c.nullable, "|", fk)
    for ix in tbl.indexes:
        w = ""
        if hasattr(ix.dialect_options, "postgresql") or True:
            pass
        print("  IX", ix.name, "uniq=", ix.unique,
              "cols=", [str(x) for x in ix.columns.keys()])
    for con in tbl.constraints:
        print("  CON", type(con).__name__, getattr(con, "name", None),
              str(getattr(con, "sqltext", ""))[:60])
