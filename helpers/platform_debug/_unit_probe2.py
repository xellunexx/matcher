import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r"\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from app.modules.cost_match.matcher import normalize_unit, units_compatible
for u in ["100 м3", "100 м2", "10 бр", "1000 кг", "100 м", "1000 м", "т", "100 т"]:
    print(repr(u), "->", normalize_unit(u))
