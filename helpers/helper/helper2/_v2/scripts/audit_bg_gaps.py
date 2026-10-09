"""Classify unresolved KCC2 lines without mistaking missing prices for synonyms."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from app.modules.cost_match.bulgarian import parse_work


def categorize(description: str, candidate: str, reasons: str) -> str:
    if 'corpus_price_disagreement' in reasons:
        return 'conflicting_prices'
    if 'work_context_missing' in reasons:
        return 'work_context_missing'
    if not candidate:
        return 'no_retrieved_candidate'
    if any(reason in reasons for reason in ('work_object_conflict', 'work_operation_bundle_conflict',
                                            'work_material_conflict', 'work_spec_conflict', 'spec_conflict')):
        return 'incompatible_candidate'
    q, c = parse_work(description), parse_work(candidate)
    q_specs, c_specs = dict(q.specs), dict(c.specs)
    if any(key not in c_specs for key in q_specs):
        return 'candidate_missing_required_spec'
    if any(key not in q_specs for key in c_specs):
        return 'candidate_has_unrequested_spec'
    if q.operations != c.operations or q.scope != c.scope:
        return 'operation_or_scope_unverified'
    if q.tail != c.tail or q.excluded != c.excluded:
        return 'inclusions_unverified'
    if q.object != c.object or not q.object:
        return 'object_unknown_or_mismatch'
    if q.materials != c.materials or q.locations != c.locations:
        return 'material_or_location_unverified'
    return 'wording_or_retrieval_gap'


def audit(workbook_path: Path, output_path: Path) -> Counter:
    sheet = load_workbook(workbook_path, read_only=True, data_only=True)['BG_MATCH_AUDIT']
    rows = iter(sheet.values)
    columns = next(rows)
    counts = Counter()
    with output_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Excel row', 'Position', 'Family', 'Gap type', 'KCC description',
                         'Nearest quote description', 'Source code', 'Reasons'])
        for values in rows:
            row = dict(zip(columns, values))
            if row['Decision'] in ('exact', 'compatible'):
                continue
            kind = categorize(row.get('Effective work description') or row['Query'],
                              row['Source description'] or '', row['Reasons'] or '')
            counts[kind] += 1
            writer.writerow([row['Excel row'], row['Position'], row['Object'] or '', kind,
                             row['Query'], row['Source description'] or '',
                             row['Source code'] or '', row['Reasons'] or ''])
    return counts


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(dict(audit(args.workbook, args.output)))
