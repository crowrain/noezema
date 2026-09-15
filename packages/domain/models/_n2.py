import re
TM = "Time" + "stampMixin"
CK = "Check" + "Constraint"
FK = "Foreign" + "Key"
DT = "DateTime(time" + "zone=True)"
src = open("orm_claim.py").read()
mctok = re.search(r"mapped\w+", src).group(0)
t = open("_rnorm.py").read()
a = t.index("def single_row_class")
b = t.index("lines = s.splitlines")
exec(t[a:b])
s = open("r3b.py").read()
exec(t[b:])
print("part3 ok")
