import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import matcher, pipeline, workframe

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kcc_frame  # noqa: E402


TEXT = "Доставка и монтаж тоалетна чиния"


def claim(text=TEXT, unit="бр", header="ВиК", **updates):
    raw = {"trade": "plumbing", "trade_ev": "ВиК", "operation": "new", "op_ev": "монтаж",
           "operations": [{"value": "new", "ev": "монтаж"}], "scope": "supply_install",
           "scope_basis": "explicit", "scope_ev": "Доставка и монтаж", "object": "тоалетна чиния",
           "object_ev": "тоалетна чиния", "object_role": "element", "role_ev": "тоалетна чиния",
           "material": [], "specs": [], "includes": [], "excludes": [], "ambiguities": []}
    raw.update(updates)
    return workframe.validate(raw, {"text": text, "unit": unit, "header": header})


def candidate(price=290, id="row-1", status="active"):
    return {"id": id, "desc": TEXT, "unit": "бр", "amount_eur": price, "status": status,
            "section": "ВиК", "origin_ref": "друга оферта.xlsx", "source_key": "operator"}


class GroundingTests(unittest.TestCase):
    def test_explicit_full_work_matches(self):
        self.assertEqual(workframe.compare(claim(), claim())[0], "match")

    def test_installation_is_not_inside_demolition(self):
        frame = claim(text="Демонтаж тоалетна чиния", op_ev="монтаж")
        self.assertEqual(frame["operation"], "unknown")

    def test_missing_scope_evidence_never_becomes_inferred(self):
        frame = claim(scope_ev="липсващ цитат")
        self.assertEqual(frame["scope"], "unknown")
        self.assertEqual(frame["scope_basis"], "unknown")

    def test_real_quote_cannot_support_wrong_operation(self):
        frame = claim(operation="demolish", op_ev="монтаж")
        self.assertEqual(frame["operation"], "unknown")

    def test_omitted_operation_blocks_price(self):
        frame = claim(text=TEXT + " и извозване")
        self.assertIn("uncovered operation", frame["dropped"])
        self.assertEqual(workframe.compare(frame, frame)[0], "review")

    def test_omitted_dimension_blocks_price(self):
        frame = claim(text=TEXT + " размер 50x20 см")
        self.assertIn("uncovered technical specification", frame["dropped"])
        self.assertEqual(workframe.compare(frame, frame)[0], "review")

    def test_scope_norm_cannot_restore_ungrounded_claim(self):
        frame = claim(scope_basis="norm", scope_ev="несъществуващ цитат")
        self.assertEqual(frame["scope"], "unknown")

    def test_installation_does_not_prove_supply(self):
        frame = claim(text="Монтаж тоалетна чиния", scope_ev="Монтаж", op_ev="Монтаж")
        self.assertEqual(frame["scope"], "unknown")

    def test_header_cannot_invent_work(self):
        frame = claim(text="Тоалетна чиния", header="ВиК Доставка и монтаж", op_ev="монтаж")
        self.assertEqual(frame["trade"], "plumbing")
        self.assertEqual(frame["operation"], "unknown")
        self.assertEqual(frame["scope"], "unknown")

    def test_unsupported_material_and_inclusion_are_dropped(self):
        frame = claim(material=[{"value": "мед", "ev": "мед"}], includes=["извозване"])
        self.assertFalse(frame["material"])
        self.assertFalse(frame["includes"])
        self.assertIn("material", frame["dropped"])
        self.assertIn("includes", frame["dropped"])
        self.assertEqual(workframe.compare(frame, claim())[0], "review")

    def test_material_value_must_be_supported_by_quote(self):
        frame = claim(material=[{"value": "мед", "ev": "тоалетна чиния"}])
        self.assertIn("material", frame["dropped"])

    def test_fabricated_spec_with_real_quote_is_dropped(self):
        frame = claim(text=TEXT + " дебелина 3,2 мм", specs=[{"kind": "thickness_mm", "value": "8", "ev": "3,2 мм"}])
        self.assertFalse(frame["specs"])
        self.assertIn("spec", frame["dropped"])

    def test_spec_units_are_converted_from_evidence(self):
        frame = claim(text=TEXT + " дебелина 0,32 см", specs=[{"kind": "thickness_mm", "value": "3.2", "ev": "0,32 см"}])
        self.assertEqual(frame["specs"][0]["value"], "3.2")

    def test_all_dimensions_in_a_size_are_preserved(self):
        frame = claim(text=TEXT + " размери 500х200х100 мм", specs=[{"kind": "size_cm", "value": "50x20x10", "ev": "500х200х100 мм"}])
        self.assertEqual(frame["specs"][0]["value"], "50x20x10")

    def test_unit_dimension_cannot_be_invented(self):
        self.assertEqual(claim(unit="м2", unit_dim="count")["unit_dim"], "area")

    def test_malformed_fields_fail_closed(self):
        frame = claim(trade=[], scope={}, material={}, specs="bad", operations=42)
        self.assertEqual(workframe.compare(frame, claim())[0], "review")


class WorkCompatibilityTests(unittest.TestCase):
    def test_full_work_rejects_labour_only(self):
        labour = claim(text="Монтаж тоалетна чиния само труд", op_ev="Монтаж",
                       operations=[], scope="labour", scope_ev="само труд")
        self.assertEqual(workframe.compare(claim(), labour)[0], "reject")

    def test_toilet_brush_is_not_toilet(self):
        brush = claim(text="Доставка и монтаж четка за тоалетна чиния", object="четка за тоалетна чиния",
                      object_ev="четка за тоалетна чиния", object_role="accessory", role_ev="четка")
        self.assertEqual(workframe.compare(claim(), brush)[0], "reject")

    def test_inferred_scope_never_commits_even_when_equal(self):
        self.assertEqual(workframe.compare(claim(scope_basis="norm"), claim(scope_basis="norm"))[0], "review")

    def test_unknown_operation_cannot_match(self):
        self.assertEqual(workframe.compare(claim(operation="unknown", operations=[]), claim())[0], "review")

    def test_partial_spec_coverage_never_commits(self):
        query = claim(text=TEXT + " 3 мм 50х20 см", specs=[
            {"kind": "thickness_mm", "value": "3", "ev": "3 мм"},
            {"kind": "size_cm", "value": "50x20", "ev": "50х20 см"}])
        cand = claim(text=TEXT + " 3 мм", specs=[{"kind": "thickness_mm", "value": "3", "ev": "3 мм"}])
        self.assertEqual(workframe.compare(query, cand)[0], "review")

    def test_conflicting_specs_are_rejected(self):
        def spec(value):
            return claim(text=TEXT + f" {value} мм", specs=[{"kind": "thickness_mm", "value": value, "ev": f"{value} мм"}])
        self.assertEqual(workframe.compare(spec("3"), spec("8"))[0], "reject")

    def test_missing_included_work_is_blocking_not_a_small_penalty(self):
        query = claim(text=TEXT + " включително извозване", includes=[{"value": "извозване", "ev": "извозване"}])
        self.assertEqual(workframe.compare(query, claim())[0], "review")

    def test_demolition_and_reinstall_cannot_collapse_to_demolition(self):
        full = claim(text="Демонтаж и обратен монтаж тоалетна чиния само труд", operation="demolish",
                     op_ev="Демонтаж", operations=[{"value": "reinstall", "ev": "обратен монтаж"}],
                     scope="labour", scope_ev="само труд")
        partial = claim(text="Демонтаж тоалетна чиния само труд", operation="demolish", op_ev="Демонтаж",
                        operations=[], scope="labour", scope_ev="само труд")
        self.assertEqual(workframe.compare(full, partial)[0], "reject")

    def test_uncertainty_is_blocking(self):
        self.assertEqual(workframe.compare(claim(ambiguities=["Неясен размер"]), claim())[0], "review")


class PriceDecisionTests(unittest.TestCase):
    def test_observed_price_has_provenance(self):
        decision = workframe.select_price(claim(), [(candidate(), claim())])
        self.assertEqual(decision["price"], 290)
        self.assertEqual(decision["price_kind"], "observed")
        self.assertEqual(decision["evidence_ids"], ["row-1"])

    def test_consensus_is_labelled_derived(self):
        decision = workframe.select_price(claim(), [(candidate(290), claim()), (candidate(300, "row-2"), claim())])
        self.assertEqual(decision["price"], 295)
        self.assertEqual(decision["price_kind"], "derived_median")

    def test_price_dispersion_is_not_hidden_by_median(self):
        decision = workframe.select_price(claim(), [(candidate(25), claim()), (candidate(290, "row-2"), claim())])
        self.assertIsNone(decision["price"])
        self.assertEqual(decision["verdict"], "review")

    def test_pending_and_invalid_prices_do_not_commit(self):
        for row in [candidate(status="pending_review"), candidate(math.nan), candidate(math.inf), candidate(-2), candidate("bad")]:
            with self.subTest(row=row):
                self.assertIsNone(workframe.select_price(claim(), [(row, claim())])["price"])

    def test_rates_convert_after_compatibility(self):
        row = {**candidate(2000), "unit": "км"}
        decision = workframe.select_price(claim(unit="м"), [(row, claim(unit="км"))])
        self.assertEqual(decision["price"], 2)

    def test_misattributed_frame_cannot_price_a_row(self):
        row = {**candidate(), "desc": "Доставка четка"}
        self.assertIsNone(workframe.select_price(claim(), [(row, claim())])["price"])

    def test_untraceable_source_cannot_price_a_row(self):
        self.assertIsNone(workframe.select_price(claim(), [(candidate(id=None), claim())])["price"])


class CorpusCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_patch = patch.object(kcc_frame, "CACHE_DB", Path(self.tmp.name) / "frames.sqlite3")
        self.cache_patch.start()
        self.client = kcc_frame.Client("http://model.invalid/v1", "bulgarian-model", batch=2)

    def tearDown(self):
        self.client.db.close()
        self.cache_patch.stop()
        self.tmp.cleanup()

    def test_unindexed_corpus_does_not_call_llm(self):
        with patch.object(self.client, "_ask", side_effect=AssertionError("online corpus parsing")):
            frame = self.client.frames([{"text": TEXT, "unit": "бр"}], cached_only=True)[0]
        self.assertTrue(frame["ambiguities"])
        self.assertEqual(workframe.compare(claim(), frame)[0], "review")

    def test_cache_identity_includes_header_and_endpoint(self):
        row = {"text": TEXT, "unit": "бр", "header": "ВиК"}
        key = self.client._key(row)
        self.assertNotEqual(key, self.client._key({**row, "header": "Електро"}))
        self.client.base_url = "http://another-model.invalid/v1"
        self.assertNotEqual(key, self.client._key(row))

    def test_wrong_or_duplicate_response_ids_are_rejected(self):
        for items in [[{"id": "wrong"}], [{"id": "0"}, {"id": "0"}], [None]]:
            response = {"choices": [{"message": {"content": kcc_frame.json.dumps({"items": items})}}]}
            with patch.object(kcc_frame.llm, "chat", return_value=response):
                with self.assertRaises(ValueError):
                    self.client._ask([{"id": "0", "text": TEXT}])

    def test_endpoint_receives_text_and_context_not_prices(self):
        response = {"choices": [{"message": {"content": '{"items":[{"id":"0"}]}'}}]}
        with patch.object(kcc_frame.llm, "chat", return_value=response) as chat:
            self.client._ask([{"id": "0", "text": TEXT, "header": "ВиК", "unit": "бр"}])
        prompt = chat.call_args.args[2][-1]["content"]
        self.assertIn("ВиК", prompt)
        self.assertNotIn("amount_eur", prompt)
        self.assertNotIn("truth", prompt)


class LegacyRegressions(unittest.TestCase):
    def test_understood_path_only_interprets_query_online(self):
        def provider(items, cached_only=False):
            calls.append((items, cached_only))
            return [claim(**item) for item in items]

        calls = []
        with patch.object(pipeline.costdb, "search_candidates", return_value=[candidate()]):
            decision = pipeline.match_cost_understood({"desc": TEXT, "unit": "бр", "header": "ВиК"}, "unused", provider)
        self.assertEqual(decision["price"], 290)
        self.assertEqual([cached for _, cached in calls], [False, True])
        self.assertEqual(calls[1][0], [workframe.corpus_input(candidate())])

    def test_understood_path_has_no_lexical_fallback(self):
        with patch.object(pipeline.costdb, "search_candidates", return_value=[candidate()]):
            decision = pipeline.match_cost_understood({"desc": TEXT, "unit": "бр"}, "unused",
                lambda items, **kw: [workframe.validate({}, item) for item in items])
        self.assertIsNone(decision["price"])

    def test_bulgarian_linear_metres_not_litres(self):
        for unit in ("л.м.", "л. м.", "лм"):
            self.assertEqual(matcher.normalize_unit(unit), "length")
            self.assertEqual(matcher.unit_rate_factor(unit, "м"), 1)
        self.assertEqual(matcher._unit_key("л")[0], "volume")

    def test_exact_description_beats_saturated_loose_score(self):
        exact = {"id": "exact", "desc": TEXT, "status": "active", "amount_eur": 290, "unit": "бр"}
        loose = {"id": "loose", "desc": "друга работа", "status": "active", "amount_eur": 25, "unit": "бр"}

        def score(row, cand):
            return 100, "exact_description" if cand["id"] == "exact" else "semantic", {"unit_match": True, "reasons": []}

        with patch.object(pipeline.costdb, "search_candidates", return_value=[loose, exact]), \
                patch.object(pipeline, "_candidate_score", side_effect=score):
            chosen, _, evidence = pipeline.match_cost_v2({"desc": TEXT, "unit": "бр"}, "unused.sqlite3")
        self.assertEqual(chosen["id"], "exact")
        self.assertEqual(evidence["top_candidates"][0]["id"], "exact")


if __name__ == "__main__":
    unittest.main()
