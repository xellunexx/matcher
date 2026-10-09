import json
from pathlib import Path

from app.modules.cost_match.schemas import MatchResultResponse


def test_frontend_price_fixture_matches_backend_serialization():
    frontend = Path(__file__).resolve().parents[4] / "frontend"
    fixture = json.loads(
        (frontend / "src/features/cost-match/priceContract.fixture.json").read_text(encoding="utf-8")
    )
    response = MatchResultResponse.model_validate(fixture).model_dump(mode="json")
    assert response == fixture
    assert response["suggested_rate"] == "5.575"
    assert response["alternatives"][0]["quotation_rate"] == "5"
