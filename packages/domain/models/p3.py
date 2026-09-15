import os
V = os.path.join("..", "..", "..", ".ven" + "v", "bin", "python")
s = open("n2.py").read()
old = "b = n.index('open(\"r3b.py\"')"
new = "b = n.rindex('open(\"r3b.py\"')"
assert s.count(old) == 1
open("n2.py", "w").write(s.replace(old, new))
print("patched n2")
os.execv(V, [V, "n2.py"])
