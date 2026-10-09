# Clean v1 → v2 path (fixed)

The shipped `bulgarian-costmatch-v1-to-v2.patch` does not apply to a real
v1-installed backend: it was generated bundle-to-bundle, so files that v1 never
touched but v2 modifies (notably `app/modules/cost_match/schemas.py`) are
encoded as *new files* and `git apply` refuses on an existing tree.

Two corrected patches live beside it:

- `bulgarian-costmatch-v1-to-v2-FIXED.patch`
  Real v1 → real v2. Applies cleanly on a backend that already has the v1
  bundle applied (verified with `git apply --check`). Code only:
  `app/`, `scripts/`, `tests/`, plus removal of the v1 readme.
  Binary/large payloads are NOT in it — copy `data/corpus-bg-canonical.jsonl.gz`
  and `analysis/` from the v2 ZIP separately if wanted.

- `local-delta-on-v2.patch`
  Changes present in the live backend that the v2 authors never saw. MUST be
  applied on top of v2 (either overlay or FIXED patch):
  1. `service.py` cascade `keep_prior` fix — an unpriced line is offered to
     every base; without this, a later/weaker base rewrites the visible
     suggestion (sek "блажна боя" masking opr "латексова боя").
  2. `test_cost_match_boq.py` operator price 99→12, keeping the test inside
     the 1.5x corpus-disagreement band so it exercises cascade order, not
     the price-spread demotion.

Apply order on a v1 backend:

    git apply bulgarian-costmatch-v1-to-v2-FIXED.patch
    git apply local-delta-on-v2.patch
    # then run:
    python -m pytest --confcutdir=tests/unit tests/unit/test_cost_match_matcher.py tests/unit/cost_match -q   # 163 pass
    python -m pytest tests/modules/cost_match -q                                                            # 128 pass

Equivalent state can also be reached by overlaying the v2 ZIP and applying
only `local-delta-on-v2.patch`.
