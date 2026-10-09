# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The one-click BOQ pricing path, end to end.

``POST /cost-match/boq/{boq_id}/run`` is the flow the product is sold on:
upload a bill, press one button, get an estimate populated from the local
cost base. These tests pin what that button must do - write each suggestion
back onto its position with provenance, never price a line without evidence,
flag a foreign-currency offer instead of pretending it converts, refuse a
locked bill outright, and keep the position in step with the ruling a
reviewer records afterwards.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

BASE = "/api/v1/cost-match"
BOQ = "/api/v1/boq"

WALL = "Reinforced concrete wall C30/37"
NONSENSE = "Bituminous shingle underlay felt"


async def _boq_with_positions(
    client: AsyncClient,
    header: dict[str, str],
    project_id: str,
    items: list[dict[str, object]],
) -> dict:
    response = await client.post(
        f"{BOQ}/boqs/",
        json={"project_id": project_id, "name": f"Bill {uuid.uuid4().hex[:6]}"},
        headers=header,
    )
    assert response.status_code == 201, response.text
    boq = response.json()
    response = await client.post(
        f"{BOQ}/boqs/{boq['id']}/positions/bulk/",
        json={"items": items},
        headers=header,
    )
    assert response.status_code == 201, response.text
    return boq


async def _positions(client: AsyncClient, header: dict[str, str], boq_id: str) -> list[dict]:
    response = await client.get(f"{BOQ}/boqs/{boq_id}", headers=header)
    assert response.status_code == 200, response.text
    return response.json()["positions"]


async def _run_boq_match(
    client: AsyncClient,
    header: dict[str, str],
    boq_id: str,
    **overrides: object,
) -> dict:
    from tests.modules.cost_match.conftest import TEST_REGION, TEST_SOURCE

    payload: dict[str, object] = {"region": TEST_REGION, "cost_source": TEST_SOURCE}
    payload.update(overrides)
    response = await client.post(
        f"{BASE}/boq/{boq_id}/run", json=payload, headers=header
    )
    assert response.status_code == 201, response.text
    return response.json()


def _by_description(positions: list[dict], fragment: str) -> dict:
    match = [p for p in positions if fragment in (p["description"] or "")]
    assert len(match) == 1, f"expected exactly one position containing {fragment!r}"
    return match[0]


class TestBoqRun:
    async def test_matching_a_boq_writes_suggestions_with_provenance(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": WALL, "unit": "m3", "quantity": 44.3},
                {"description": NONSENSE, "unit": "m2", "quantity": 310},
            ],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        assert summary["lines"] == 2
        assert summary["counts"]["exact"] == 1
        assert summary["positions_priced"] == 1
        assert summary["positions_unpriced"] == 1
        assert summary["run_ids"]

        positions = await _positions(client, header, boq["id"])
        wall = _by_description(positions, "concrete wall")
        # The suggestion landed with its evidence, not as a bare number.
        assert Decimal(wall["unit_rate"]) == Decimal("185")
        assert wall["price_basis"] == "corpus_exact"
        cm = wall["metadata"]["cost_match"]
        assert cm["code"] == "CM-C30-WALL"
        assert cm["tier"] == "exact"
        assert cm["currency"] == "EUR"
        assert cm["run_id"] == summary["run_ids"][0]
        assert cm["state"] == "pending_review"

        # No evidence in the base -> the line stays unresolved, not guessed.
        nonsense = _by_description(positions, "shingle")
        assert Decimal(nonsense["unit_rate"]) == 0
        assert "cost_match" not in (nonsense["metadata"] or {})

    async def test_section_and_header_rows_are_not_matched(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": "Section 1 - Structural works", "unit": "section", "quantity": 0},
                {"description": WALL, "unit": "m3", "quantity": 10},
            ],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        assert summary["lines"] == 1
        assert summary["counts"]["total"] == 1

    async def test_a_locked_boq_refuses_pricing_with_409(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        lock = await client.post(f"{BOQ}/boqs/{boq['id']}/lock/", headers=header)
        assert lock.status_code == 200, lock.text

        response = await client.post(
            f"{BASE}/boq/{boq['id']}/run",
            json={"region": "OE_TEST_COST_MATCH", "cost_source": "cwicr"},
            headers=header,
        )
        assert response.status_code == 409
        position = _by_description(await _positions(client, header, boq["id"]), "concrete wall")
        assert Decimal(position["unit_rate"]) == 0

    async def test_an_unknown_boq_is_404_not_403(self, client: AsyncClient, header) -> None:
        response = await client.post(
            f"{BASE}/boq/{uuid.uuid4()}/run", json={}, headers=header
        )
        assert response.status_code == 404

    async def test_authentication_is_required(self, client: AsyncClient) -> None:
        response = await client.post(f"{BASE}/boq/{uuid.uuid4()}/run", json={})
        assert response.status_code in (401, 403)


class TestDecisionWriteBack:
    @pytest.mark.parametrize('action,unit', [('confirmed', 'm2'), ('overridden', 'm3')])
    async def test_incompatible_decision_preserves_prices_and_pending_history(
        self, client: AsyncClient, header, project_id, cost_base, action, unit
    ) -> None:
        boq = await _boq_with_positions(client, header, project_id,
                                        [{'description': WALL, 'unit': unit, 'quantity': 10}])
        summary = await _run_boq_match(client, header, boq['id'])
        items = (await client.get(f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header)).json()['items']
        result_id = items[0]['id']
        before = _by_description(await _positions(client, header, boq['id']), 'concrete wall')
        payload = {'decision': action}
        if action == 'overridden':
            payload['cost_item_id'] = str(cost_base['CM-FORMWORK'])
        response = await client.post(f'{BASE}/results/{result_id}/decision', json=payload, headers=header)
        assert response.status_code == 422, response.text
        assert 'manual price' in response.json()['detail']
        after = _by_description(await _positions(client, header, boq['id']), 'concrete wall')
        assert (after['unit_rate'], after['total'], after['price_basis']) == (
            before['unit_rate'], before['total'], before['price_basis'])
        detail = (await client.get(f'{BASE}/results/{result_id}', headers=header)).json()
        assert detail['decision_state'] == 'pending'
        assert detail['decisions'] == []

    async def test_confirm_keeps_the_price_and_marks_it_ruled(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "confirmed"},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        position = _by_description(await _positions(client, header, boq["id"]), "concrete wall")
        assert Decimal(position["unit_rate"]) == Decimal("185")
        assert Decimal(position["total"]) == Decimal("1850")
        assert position["price_basis"] == "corpus_confirmed"
        assert position["metadata"]["cost_match"]["decision"] == "confirmed"

    async def test_an_override_replaces_the_position_rate(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": "Stahlbetonwand", "unit": "m2", "quantity": 12}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "overridden", "cost_item_id": str(cost_base["CM-FORMWORK"])},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        position = _by_description(await _positions(client, header, boq["id"]), "Stahlbetonwand")
        # Formwork and the BOQ line are both priced per m2.
        assert Decimal(position["unit_rate"]) == Decimal("42.5")
        assert position["price_basis"] == "corpus_override"
        cm = position["metadata"]["cost_match"]
        assert cm["decision"] == "overridden"
        assert cm["decided_code"] == "CM-FORMWORK"

    async def test_a_rejection_clears_the_rate_from_the_line(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "rejected", "note": "not this wall"},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        position = _by_description(await _positions(client, header, boq["id"]), "concrete wall")
        # Keeping the offered number on a line a person just ruled wrong
        # would resurrect the suggestion under their own authority.
        assert position["unit_rate"] in ("0", "0.0000")
        assert position["price_basis"] == "corpus_rejected"
        assert position["metadata"]["cost_match"]["decision"] == "rejected"

    async def test_a_locked_bills_position_does_not_move_on_a_ruling(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        await client.post(f"{BOQ}/boqs/{boq['id']}/lock/", headers=header)
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "rejected"},
            headers=header,
        )
        # The ruling is still recorded on the run; the frozen line keeps
        # its numbers behind the lock.
        assert decision.status_code == 201, decision.text
        position = _by_description(await _positions(client, header, boq["id"]), "concrete wall")
        assert Decimal(position["unit_rate"]) == Decimal("185")


class TestCurrencyMismatch:
    async def test_a_foreign_currency_offer_is_flagged_not_applied(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        """185 EUR suggested on a USD bill is not a 185 USD figure."""
        project = await client.post(
            "/api/v1/projects/",
            json={
                "name": f"FX bill {uuid.uuid4().hex[:6]}",
                "description": "currency mismatch suite",
                "currency": "USD",
            },
            headers=header,
        )
        assert project.status_code in (200, 201), project.text
        project_id = project.json()["id"]
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        # The evidence exists but nothing was priced: the rate was withheld.
        assert summary["positions_priced"] == 0
        assert summary["positions_unpriced"] == 1

        position = _by_description(await _positions(client, header, boq["id"]), "concrete wall")
        assert Decimal(position["unit_rate"]) == 0
        assert position["price_basis"] == "corpus_currency_mismatch"
        cm = position["metadata"]["cost_match"]
        assert cm["currency_mismatch"] is True
        assert cm["suggested_rate"] == "185.0000"
        assert cm["currency"] == "EUR"

        items = (await client.get(f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header)).json()['items']
        result_id = items[0]['id']
        decision = await client.post(f'{BASE}/results/{result_id}/decision',
                                     json={'decision': 'confirmed'}, headers=header)
        assert decision.status_code == 422, decision.text
        assert 'manual price in project currency' in decision.json()['detail']
        position = _by_description(await _positions(client, header, boq['id']), 'concrete wall')
        assert Decimal(position['unit_rate']) == 0
        detail = (await client.get(f'{BASE}/results/{result_id}', headers=header)).json()
        assert detail['decision_state'] == 'pending'
        assert detail['decisions'] == []


async def _seed_item(
    code: str,
    description: str,
    unit: str,
    rate: str,
    currency: str,
    source: str | None = None,
) -> uuid.UUID:
    """One corpus row in the suite's isolated region, cleaned by the base."""
    from app.database import async_session_factory
    from app.modules.costs.models import CostItem
    from tests.modules.cost_match.conftest import TEST_REGION, TEST_SOURCE

    async with async_session_factory() as session:
        item = CostItem(
            code=code,
            description=description,
            descriptions={"en": description},
            unit=unit,
            rate=rate,
            currency=currency,
            source=source or TEST_SOURCE,
            region=TEST_REGION,
            is_active=True,
        )
        session.add(item)
        await session.commit()
        return item.id


class TestWriteBackGates:
    async def test_bgn_offer_converts_at_the_fixed_peg(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        """A BGN corpus price lands on an EUR bill converted at 1.95583."""
        tag = uuid.uuid4().hex[:6]
        await _seed_item(
            f"CM-BGN-{tag}", f"Pegged lev widget {tag}", "m3", "195.583", "BGN"
        )
        project = await client.post(
            "/api/v1/projects/",
            json={
                "name": f"EUR bill {uuid.uuid4().hex[:6]}",
                "description": "fixed-peg conversion suite",
                "currency": "EUR",
            },
            headers=header,
        )
        assert project.status_code in (200, 201), project.text
        boq = await _boq_with_positions(
            client,
            header,
            project.json()["id"],
            [{"description": f"Pegged lev widget {tag}", "unit": "m3", "quantity": 2}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        position = _by_description(await _positions(client, header, boq["id"]), "Pegged lev widget")
        # 195.583 BGN / 1.95583 = 100.0 EUR, within rounding.
        assert abs(Decimal(position["unit_rate"]) - Decimal("100")) < Decimal("0.01")
        cm = position["metadata"]["cost_match"]
        # Bulgaria is euro: the lev figure is restated in EUR at the peg
        # when the candidate is built, so the suggestion itself is EUR -
        # the corpus row keeps the original 195.583 BGN as its provenance.
        assert cm["currency"] == "EUR"
        assert cm["currency_mismatch"] is False
        assert abs(Decimal(cm["suggested_rate"]) - Decimal("100")) < Decimal("0.01")
        assert summary["positions_priced"] == 1

    async def test_a_unit_conflicting_offer_is_withheld(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """A per-piece price must never land on a cubic-metre line."""
        tag = uuid.uuid4().hex[:6]
        await _seed_item(
            f"CM-PIECE-{tag}", f"Frobnicate sprocket {tag}", "piece", "1.00", "EUR"
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": f"Frobnicate sprocket {tag}", "unit": "m3", "quantity": 962.8}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        assert summary["positions_priced"] == 0
        position = _by_description(await _positions(client, header, boq["id"]), "Frobnicate sprocket")
        assert Decimal(position["unit_rate"]) == 0
        assert position["price_basis"] == "corpus_unit_mismatch"
        cm = position["metadata"]["cost_match"]
        assert cm["unit_mismatch"] is True
        assert cm["suggested_rate"] == "1.0000"


class TestBulkConfirm:
    """``POST /boq/{id}/confirm`` - the grid's confirm selected / confirm all.

    A machine-written rate is a suggestion until a person rules on it. The
    bulk path goes through the same append-only decision flow as the review
    queue and marks positions ``corpus_confirmed``. A plain confirm adopts a
    machine suggestion, so it is NOT authored evidence: nothing learns back
    into the corpus from it. Only a rate standing on an operator-declared
    ``price_basis`` (invoice, quotation, ...) may become corpus evidence.
    """

    async def test_duplicate_selected_scope_and_remaining_counts(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(client, header, project_id,
                                        [{'description': WALL, 'unit': 'm3', 'quantity': 10} for _ in range(3)])
        await _run_boq_match(client, header, boq['id'])
        positions = await _positions(client, header, boq['id'])
        selected = positions[0]['id']
        response = await client.post(f"{BASE}/boq/{boq['id']}/confirm",
                                     json={'position_ids': [selected]}, headers=header)
        assert response.status_code == 200, response.text
        assert response.json()['confirmed'] == 1
        positions = await _positions(client, header, boq['id'])
        assert sum(p['price_basis'] == 'corpus_confirmed' for p in positions) == 1
        response = await client.post(f"{BASE}/boq/{boq['id']}/confirm", json={}, headers=header)
        assert response.status_code == 200, response.text
        assert (response.json()['confirmed'], response.json()['skipped'], response.json()['learned']) == (2, 0, 0)
        positions = await _positions(client, header, boq['id'])
        assert sum(p['price_basis'] == 'corpus_confirmed' for p in positions) == 3

    async def test_confirm_all_rules_pending_lines_and_learns_to_corpus(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": WALL, "unit": "m3", "quantity": 10},
                {"description": NONSENSE, "unit": "m2", "quantity": 5},
            ],
        )
        await _run_boq_match(client, header, boq["id"])

        response = await client.post(
            f"{BASE}/boq/{boq['id']}/confirm", json={}, headers=header
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # One line matched, one had nothing to confirm.
        assert body["confirmed"] == 1
        assert body["skipped"] == 1
        # Confirming a matcher suggestion teaches the corpus nothing -
        # rubber-stamped machine output must never become corpus evidence.
        assert body["learned"] == 0

        positions = await _positions(client, header, boq["id"])
        wall = _by_description(positions, "concrete wall")
        assert wall["price_basis"] == "corpus_confirmed"
        assert Decimal(wall["unit_rate"]) == Decimal("185")
        assert wall["metadata"]["cost_match"]["decision"] == "confirmed"
        nonsense = _by_description(positions, "shingle")
        assert Decimal(nonsense["unit_rate"]) == 0

        # No CONF- echo row was minted for the rubber-stamped suggestion.
        learned = await client.get(
            f"/api/v1/costs/?q=CONF-{wall['id']}&limit=5", headers=header
        )
        assert learned.status_code == 200, learned.text
        items = learned.json()["items"]
        assert not any(i["code"] == f"CONF-{wall['id']}" for i in items)

    async def test_confirm_selected_scopes_to_the_given_positions(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": WALL, "unit": "m3", "quantity": 10},
                {"description": "Reinforcement steel B500B", "unit": "kg", "quantity": 500},
            ],
        )
        await _run_boq_match(client, header, boq["id"])
        positions = await _positions(client, header, boq["id"])
        wall = _by_description(positions, "concrete wall")
        rebar = _by_description(positions, "Reinforcement steel")

        response = await client.post(
            f"{BASE}/boq/{boq['id']}/confirm",
            json={"position_ids": [wall["id"]]},
            headers=header,
        )
        assert response.status_code == 200, response.text
        assert response.json()["confirmed"] == 1

        positions = await _positions(client, header, boq["id"])
        # The selected row is ruled; the unselected one is still a suggestion.
        assert _by_description(positions, "concrete wall")["price_basis"] == "corpus_confirmed"
        assert _by_description(positions, "Reinforcement steel")["price_basis"] == "corpus_exact"

    async def test_confirm_on_an_unmatched_bill_confirms_nothing(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """No runs for the bill -> nothing to confirm, nothing invented."""
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": WALL, "unit": "m3", "quantity": 10}],
        )
        response = await client.post(
            f"{BASE}/boq/{boq['id']}/confirm", json={}, headers=header
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "boq_id": boq["id"],
            "confirmed": 0,
            "skipped": 0,
            "learned": 0,
        }

    async def test_a_bulk_unit_rate_scales_down_to_per_unit(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """A catalogue row priced per '100 m2' contributes rate/100 per m2."""
        tag = uuid.uuid4().hex[:6]
        await _seed_item(
            f"CM-BULK-{tag}", f"Bulk plaster batch {tag}", "100 m2", "1800.00", "EUR"
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": f"Bulk plaster batch {tag}", "unit": "m2", "quantity": 310}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        assert summary["positions_priced"] == 1
        position = _by_description(await _positions(client, header, boq["id"]), "Bulk plaster batch")
        assert Decimal(position["unit_rate"]) == Decimal("18")
        assert position["metadata"]["cost_match"]["unit_factor"] == "0.01"

    async def test_a_withheld_rerun_clears_an_auto_written_rate(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        """If the corpus stops backing a price, the number must not linger."""
        tag = uuid.uuid4().hex[:6]
        await _seed_item(
            f"CM-FLIP-{tag}", f"Flip currency doodad {tag}", "m3", "50.00", "USD"
        )
        project = await client.post(
            "/api/v1/projects/",
            json={
                "name": f"EUR rerun {uuid.uuid4().hex[:6]}",
                "description": "stale-rate clearing suite",
                "currency": "EUR",
            },
            headers=header,
        )
        assert project.status_code in (200, 201), project.text
        # First run: USD offer on an EUR project is withheld, rate stays 0.
        boq = await _boq_with_positions(
            client,
            header,
            project.json()["id"],
            [{"description": f"Flip currency doodad {tag}", "unit": "m3", "quantity": 4}],
        )
        await _run_boq_match(client, header, boq["id"])
        # Now flip the item to EUR and re-run: the rate lands.
        from sqlalchemy import update

        from app.database import async_session_factory
        from app.modules.costs.models import CostItem

        async with async_session_factory() as session:
            await session.execute(
                update(CostItem)
                .where(CostItem.code == f"CM-FLIP-{tag}")
                .values(currency="EUR", rate="60.00")
            )
            await session.commit()
        await _run_boq_match(client, header, boq["id"])
        position = _by_description(await _positions(client, header, boq["id"]), "Flip currency doodad")
        assert Decimal(position["unit_rate"]) == Decimal("60")
        # Flip back to USD and re-run: the auto-written price is retracted.
        async with async_session_factory() as session:
            await session.execute(
                update(CostItem)
                .where(CostItem.code == f"CM-FLIP-{tag}")
                .values(currency="USD")
            )
            await session.commit()
        await _run_boq_match(client, header, boq["id"])
        position = _by_description(await _positions(client, header, boq["id"]), "Flip currency doodad")
        assert Decimal(position["unit_rate"]) == 0
        assert position["price_basis"] == "corpus_currency_mismatch"


class TestManualRulingLearning:
    """A typed review price is operator evidence: it must teach the corpus
    and survive re-runs, not die on the one position it was typed into."""

    async def test_manual_ruling_learns_an_opr_item(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """decision=manual upserts OPR-<position id> into operator_pricelist."""
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": NONSENSE, "unit": "m2", "quantity": 5}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "manual", "rate": 77.5, "currency": "EUR"},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        position = _by_description(await _positions(client, header, boq["id"]), "shingle")
        assert position["price_basis"] == "manual"
        assert Decimal(position["unit_rate"]) == Decimal("77.5")

        learned = await client.get(
            f"/api/v1/costs/?q=OPR-{position['id']}&limit=5", headers=header
        )
        assert learned.status_code == 200, learned.text
        items = [i for i in learned.json()["items"] if i["code"] == f"OPR-{position['id']}"]
        assert len(items) == 1
        assert items[0]["source"] == "operator_pricelist"
        assert Decimal(items[0]["rate"]) == Decimal("77.5")

    async def test_rematch_never_overwrites_a_manual_price(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """A second run may offer again; the typed number stands."""
        tag = uuid.uuid4().hex[:6]
        # Token-free names: the suite shares one corpus, so a stray common
        # word here would leak into later tests' candidate pools.
        desc = f"Handpriced zorble {tag}"
        await _seed_item(f"SEK-RE-{tag}", desc, "m3", "10.00", "EUR")
        boq = await _boq_with_positions(
            client, header, project_id,
            [{"description": desc, "unit": "m3", "quantity": 10}],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        result_id = results.json()["items"][0]["id"]
        decision = await client.post(
            f"{BASE}/results/{result_id}/decision",
            json={"decision": "manual", "rate": 250, "currency": "EUR"},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        # Re-match everything: the corpus hit exists, but a human basis wins.
        await _run_boq_match(client, header, boq["id"], bases=["all"])
        position = _by_description(await _positions(client, header, boq["id"]), desc)
        assert Decimal(position["unit_rate"]) == Decimal("250")
        assert position["price_basis"] == "manual"
        cm = position["metadata"]["cost_match"]
        assert cm["decision"] == "manual"
        assert cm["state"] == "manual"

    async def test_rematch_still_prices_unpriced_lines_around_manual(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """The guard is surgical: untouched neighbours still get priced."""
        tag = uuid.uuid4().hex[:6]
        manual_desc = f"Protected quizzle {tag}"
        open_desc = f"Open splindle {tag}"
        await _seed_item(f"SEK-A-{tag}", manual_desc, "m3", "10.00", "EUR")
        await _seed_item(f"SEK-B-{tag}", open_desc, "m2", "20.00", "EUR")
        boq = await _boq_with_positions(
            client, header, project_id,
            [
                {"description": manual_desc, "unit": "m3", "quantity": 1},
                {"description": open_desc, "unit": "m2", "quantity": 1},
            ],
        )
        summary = await _run_boq_match(client, header, boq["id"])
        results = await client.get(
            f"{BASE}/runs/{summary['run_ids'][0]}/results", headers=header
        )
        items = results.json()["items"]
        # The manual ruling goes to whichever result scored the protected line.
        manual_result = next(r for r in items if manual_desc in r["source_description"])
        decision = await client.post(
            f"{BASE}/results/{manual_result['id']}/decision",
            json={"decision": "manual", "rate": 123, "currency": "EUR"},
            headers=header,
        )
        assert decision.status_code == 201, decision.text

        await _run_boq_match(client, header, boq["id"], bases=["all"])
        positions = await _positions(client, header, boq["id"])
        assert Decimal(_by_description(positions, manual_desc)["unit_rate"]) == Decimal("123")
        # The unruled line still takes the corpus price on the second pass.
        assert Decimal(_by_description(positions, open_desc)["unit_rate"]) == Decimal("20")


class TestBaseCascade:
    """Named bases and the ordered ``bases`` cascade on the BOQ match path."""

    async def test_named_base_scopes_candidates_to_its_codes(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """bases=["sek"] sees only SEK-/BUILDLY-coded rows, never plain cwicr."""
        tag, tag2 = uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
        await _seed_item(
            f"SEK-CM-{tag}", f"Sek-gated widget {tag}", "pcs", "10.00", "EUR"
        )
        await _seed_item(
            f"CM-NONSEK-{tag2}", f"Obscure frobnicate {tag2}", "pcs", "20.00", "EUR"
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": f"Sek-gated widget {tag}", "unit": "pcs", "quantity": 1},
                {"description": f"Obscure frobnicate {tag2}", "unit": "pcs", "quantity": 1},
            ],
        )
        summary = await _run_boq_match(client, header, boq["id"], bases=["sek"])
        positions = await _positions(client, header, boq["id"])
        sek_pos = _by_description(positions, "Sek-gated widget")
        plain_pos = _by_description(positions, "Obscure frobnicate")
        assert Decimal(sek_pos["unit_rate"]) == Decimal("10")
        assert Decimal(plain_pos["unit_rate"]) == 0
        assert summary["positions_priced"] == 1
        assert summary["positions_unpriced"] == 1

    async def test_later_base_only_receives_unpriced_lines(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """Stage two prices the gap, not the whole bill again."""
        tag, tag2 = uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
        await _seed_item(
            f"SEK-CM-{tag}", f"Sek-first doodad {tag}", "pcs", "10.00", "EUR"
        )
        await _seed_item(
            f"CM-OP-{tag2}",
            f"Operator-only gizmo {tag2}",
            "pcs",
            "30.00",
            "EUR",
            source="operator_pricelist",
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": f"Sek-first doodad {tag}", "unit": "pcs", "quantity": 1},
                {"description": f"Operator-only gizmo {tag2}", "unit": "pcs", "quantity": 1},
            ],
        )
        summary = await _run_boq_match(
            client, header, boq["id"], bases=["sek", "operator"]
        )
        positions = await _positions(client, header, boq["id"])
        sek_pos = _by_description(positions, "Sek-first doodad")
        op_pos = _by_description(positions, "Operator-only gizmo")
        assert Decimal(sek_pos["unit_rate"]) == Decimal("10")
        assert sek_pos["metadata"]["cost_match"]["code"] == f"SEK-CM-{tag}"
        assert Decimal(op_pos["unit_rate"]) == Decimal("30")
        assert op_pos["metadata"]["cost_match"]["code"] == f"CM-OP-{tag2}"
        assert summary["positions_priced"] == 2
        assert summary["positions_unpriced"] == 0

    async def test_earlier_base_wins_over_later_base(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """Cascade order is the priority order: first base's price stands."""
        tag = uuid.uuid4().hex[:6]
        await _seed_item(
            f"SEK-CM-{tag}", f"Contested thingamajig {tag}", "pcs", "10.00", "EUR"
        )
        await _seed_item(
            f"CM-OP-{tag}",
            f"Contested thingamajig {tag}",
            "pcs",
            # 12 vs sek's 10 stays inside the 1.5x disagreement band - an
            # exact verbatim hit prices either way, so the test isolates
            # cascade ordering without the disagreement guard firing.
            "12.00",
            "EUR",
            source="operator_pricelist",
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [{"description": f"Contested thingamajig {tag}", "unit": "pcs", "quantity": 1}],
        )
        summary = await _run_boq_match(
            client, header, boq["id"], bases=["operator", "sek"]
        )
        position = _by_description(
            await _positions(client, header, boq["id"]), "Contested thingamajig"
        )
        # "operator" ran first, so the 12.00 operator price - not SEK's 10.00 -
        # is what landed.
        assert Decimal(position["unit_rate"]) == Decimal("12")
        assert position["metadata"]["cost_match"]["code"] == f"CM-OP-{tag}"
        assert summary["positions_priced"] == 1

    async def test_opr_base_covers_seed_files_and_learned_rows(
        self, client: AsyncClient, header, project_id, cost_base
    ) -> None:
        """Operator rulings remain reachable alongside uploaded price books."""
        tag, tag2 = uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
        seed_code = f"OPR3-9{int(tag, 16) % 100000:05d}"
        await _seed_item(
            seed_code, f"Seedfile contraption {tag}", "pcs", "12.00", "EUR",
            source="operator_pricelist",
        )
        await _seed_item(
            f"OPR-{uuid.uuid4()}",
            f"Learned-only contraption {tag2}",
            "pcs",
            "7.00",
            "EUR",
            source="operator_pricelist",
        )
        boq = await _boq_with_positions(
            client,
            header,
            project_id,
            [
                {"description": f"Seedfile contraption {tag}", "unit": "pcs", "quantity": 1},
                {"description": f"Learned-only contraption {tag2}", "unit": "pcs", "quantity": 1},
            ],
        )
        summary = await _run_boq_match(client, header, boq["id"], bases=["opr"])
        positions = await _positions(client, header, boq["id"])
        seed_pos = _by_description(positions, "Seedfile contraption")
        learned_pos = _by_description(positions, "Learned-only contraption")
        assert Decimal(seed_pos["unit_rate"]) == Decimal("12")
        assert seed_pos["metadata"]["cost_match"]["code"] == seed_code
        assert Decimal(learned_pos["unit_rate"]) == Decimal("7")
        assert summary["positions_priced"] == 2
        assert summary["positions_unpriced"] == 0


class TestPriceSpread:
    async def test_equivalent_unit_spellings_cannot_hide_a_price_disagreement(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        from app.database import async_session_factory
        from app.modules.cost_match.service import CostMatchService

        tag = uuid.uuid4().hex[:6]
        desc = f"Бетонна рампа {tag}"
        await _seed_item(f"CM-ALIAS-A-{tag}", desc, "м²", "85", "EUR")
        await _seed_item(f"CM-ALIAS-B-{tag}", desc, "кв.м.", "25", "EUR")
        async with async_session_factory() as session:
            spread = await CostMatchService(session)._price_spread(desc, unit="м²")
        assert spread is not None
        assert spread["dup_count"] == 2
        assert spread["dup_rate_min"] == 25
        assert spread["dup_rate_max"] == 85

    async def test_cyrillic_duplicate_names_report_a_spread(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        """Postgres lower() does not fold Cyrillic - the same-name query must
        fold the bound literal on the DB side, or a Bulgarian duplicate never
        registers and the price lottery is silent."""
        from app.database import async_session_factory
        from app.modules.cost_match.service import CostMatchService

        tag = uuid.uuid4().hex[:6]
        desc = f"ПЕВП Тръба Ф32 SDR17 {tag}"
        await _seed_item(f"CM-DUP-A-{tag}", desc, "м", "4.80", "EUR")
        await _seed_item(f"CM-DUP-B-{tag}", desc, "м", "13.20", "EUR")
        async with async_session_factory() as session:
            spread = await CostMatchService(session)._price_spread(desc, unit="м")
        assert spread is not None
        assert spread["dup_count"] == 2
        assert spread["dup_rate_min"] == 4.80
        assert spread["dup_rate_max"] == 13.20

    async def test_spread_is_restated_in_euros(
        self, client: AsyncClient, header, cost_base
    ) -> None:
        """A lev twin belongs in the spread at its pegged euro value, never
        as a raw number mixed with euros."""
        from app.database import async_session_factory
        from app.modules.cost_match.service import CostMatchService

        tag = uuid.uuid4().hex[:6]
        desc = f"Тръба ППР ф20 {tag}"
        await _seed_item(f"CM-FX-A-{tag}", desc, "м", "2.00", "EUR")
        await _seed_item(f"CM-FX-B-{tag}", desc, "м", "7.82332", "BGN")
        async with async_session_factory() as session:
            spread = await CostMatchService(session)._price_spread(desc, unit="м")
        assert spread is not None
        # 7.82332 BGN / 1.95583 = 4.0 EUR: min is the euro row, max the pegged one.
        assert abs(spread["dup_rate_min"] - 2.0) < 0.01
        assert abs(spread["dup_rate_max"] - 4.0) < 0.01
