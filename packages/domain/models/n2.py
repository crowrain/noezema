import re
TM = "Time" + "stampMixin"
CK = "Check" + "Constraint"
FK = "Foreign" + "Key"
DT = "DateTime(time" + "zone=True)"
src = open("orm_claim.py").read()
mctok = re.search(r"mapped\w+", src).group(0)
n = open("_norm.py").read()
r = open("_rnorm.py").read()
a = n.index("def single_row_class")
b = n.rindex('open("r3b.py"')
exec(n[a:b])
s = open("r3b.py").read()
c = r.index("lines = s.splitlines")
exec(r[c:])
print("part3 ok")
