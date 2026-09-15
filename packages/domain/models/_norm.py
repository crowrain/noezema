import re

TM = "Time" + "stampMixin"
CK = "Check" + "Constraint"
FK = "Foreign" + "Key"
DT = "DateTime(time" + "zone=True)"

src = open("orm_claim.py").read()
mctok = re.search(r"mapped\w+", src).group(0)
assert len(mctok) == 13, mctok

s = open("r3b.py").read() + open("rb4.py").read() + open("rb5.py").read()
import os
os.remove("rb4.py")
os.remove("rb5.py")

# 1) join wrapped identifiers
s = s.replace("named_null\n    able=", "named_nullable=")
s = s.replace("named_null\nable=", "named_nullable=")
s = re.sub(r"default\s*\n\s*=\s*0\)", "default=0)", s)
s = re.sub(r"nullable\s*\n\s*=\s*True", "nullable=True", s)
s = re.sub(r"Chec\s*\n\s*kConstraint", CK, s)
s = re.sub(r"DateTim\s*\n\s*e\(", "DateTime(", s)
s = re.sub(r"uuid\.\s*\n\s*UUID", "uuid.UUID", s)
s = re.sub(r"Foreign\s*\n\s*Key", FK, s)
s = re.sub(r"mapped_\w*column|mappable_column|ma_column|mapping_column", mctok, s)

# 2) MCD placeholder variants -> real token
s = re.sub(r"\bM[A-Z]{0,3}(?=\()", mctok, s)

# 3) named_nullable=False is never intended -> nullable=False
s = s.replace("named_nullable=False", "nullable=False")

# 4) rewrite the predicate line wholesale
SAT = "sa" + ".text"
s = re.sub(r"_ACTIVE_JOB_PREDICATE = [^\n]*\n\s*\"status[^\n]*\n\s*\)",
           "_ACTIVE_JOB_PREDICATE = " + SAT + "(\n"
           "    \"status IN ('queued', 'leased', '\" + \"retry')\"\n)", s)

# 5) rewrite the two single-row class bodies wholesale
def single_row_class(name, table, doc, cols):
    L = []
    L.append("")
    L.append("")
    L.append("class %s(Base, %s):" % (name, TM))
    L.append('    """%s' % doc)
    L.append('    """')
    L.append("")
    L.append('    __tablename__ = "%s"' % table)
    L.append('    __table_args__ = (%s("id = 1"),)' % CK)
    L.append("")
    L.append('    id: Mapped[int] = %s(' % mctok)
    L.append('        "id", Integer, primary_key=True, server_default="1"')
    L.append("    )")
    for cname, ann, typeref, fk, extra in cols:
        L.append("    %s: Mapped[%s] = %s(" % (cname, ann, mctok))
        if fk:
            L.append('        "%s", %s, %s("%s.id"), nullable=True%s' % (cname, typeref, FK, fk, extra))
        else:
            L.append('        "%s", %s%s' % (cname, typeref, extra))
        L.append("    )")
    return "\n".join(L) + "\n"

open("r3b.py", "w").write(s)
print("norm done")
