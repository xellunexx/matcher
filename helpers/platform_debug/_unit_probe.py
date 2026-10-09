import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r"\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from app.modules.cost_match.matcher import normalize_unit, units_compatible
for u in ["м3", "100 м2", "м2", "PIECE", "бр", "M3", "M2", "куб.м"]:
    print(repr(u), "->", normalize_unit(u))
print("м3 vs 100 м2:", units_compatible("м3", "100 м2"))
print("м3 vs PIECE:", units_compatible("м3", "PIECE"))
