import re, sys
path = sys.argv[1]
s = open(path, encoding="utf-8").read()
for m in re.finditer(r'router\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)', s):
    print(m.group(1).upper(), "|", m.group(2))
