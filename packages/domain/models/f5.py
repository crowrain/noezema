import re
s = open("r3b.py").read()
n1 = s.count("named_nullable")
s = s.replace("named_nullable", "nullable")
TM = "Time" + "stampMixin"
out = []
fixed = []
for ln in s.splitlines(keepends=True):
    if ln.startswith("class ORM") and TM not in ln:
        ln2 = re.sub(r"\(Base, \w*Mixin,", "(Base, " + TM + ",", ln)
        if ln2 != ln:
            fixed.append(ln2.strip()[:50])
        ln = ln2
    out.append(ln)
open("r3b.py", "w").write("".join(out))
print("replaced:", n1, "fixed:", fixed)
