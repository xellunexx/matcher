from pathlib import Path
p = Path("app/spatial_scene.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
assert lines[257].startswith("_BID_MARKERS"), lines[257]
assert lines[298].strip() == "" and lines[300].startswith("def _refs"), (lines[298], lines[300])
assert lines[465].startswith("def compile_scene"), lines[465]
a = Path("_chunk_a.py").read_text(encoding="utf-8")
b = Path("_chunk_b.py").read_text(encoding="utf-8")
out = "".join(lines[:257]) + a + "\n\n" + "".join(lines[298:465]) + b
p.write_text(out, encoding="utf-8")
print("ok", len(out.splitlines()))
