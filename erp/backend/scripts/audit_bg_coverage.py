"""Check the original unresolved set against unbounded, known-family price evidence.

This diagnoses recall versus evidence gaps. It does not approve any price.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from app.modules.cost_match.bulgarian import parse_work
from app.modules.cost_match.matcher import Candidate, HIGH_CONFIDENCE, score_match, unit_rate_factor
from scripts.kcc_match_bg import OPERATOR_SOURCES, decimal


def audit_rows(path: Path) -> dict[int, dict]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    rows = iter(workbook['BG_MATCH_AUDIT'].values)
    columns = next(rows)
    result = {row['Excel row']: row for values in rows if (row := dict(zip(columns, values)))}
    workbook.close()
    return result


def audit(corpus: Path, baseline: Path, current: Path, output: Path) -> Counter:
    pool = defaultdict(list)
    with corpus.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            rate = decimal(row.get('rate'))
            if (str(row.get('is_active', '')).lower() not in {'true', '1'}
                    or rate is None or rate <= 0 or row.get('currency') not in {'EUR', 'BGN'}):
                continue
            family = parse_work(row['description']).object
            if family:
                pool[family].append(Candidate(row['code'], row['description'], row['unit'], row))
    before, after = audit_rows(baseline), audit_rows(current)
    counts = Counter()
    with output.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['Excel row', 'Position', 'Original KCC row', 'Effective work', 'Family',
                         'Current decision', 'Evidence diagnosis', 'Full-family pool size',
                         'Best operator code', 'Best operator description', 'Operator confidence',
                         'Operator reasons', 'Compatible reference code'])
        for row_no, original in before.items():
            if original['Decision'] in {'exact', 'compatible'}:
                continue
            row = after.get(row_no)
            effective = row['Effective work description'] if row else original['Query']
            family = parse_work(effective).object
            candidates = pool.get(family, [])
            best = None
            reference = ''
            for candidate in candidates:
                if unit_rate_factor(candidate.unit, original['Unit']) is None:
                    continue
                match = score_match(effective, candidate.text, query_unit=original['Unit'],
                                    candidate_unit=candidate.unit)
                if candidate.payload['source'] in OPERATOR_SOURCES:
                    if best is None or match.confidence > best[1].confidence:
                        best = (candidate, match)
                elif match.confidence >= HIGH_CONFIDENCE:
                    reference = reference or candidate.ref
            if row is None:
                kind = 'work_heading_not_priceable'
            elif row['Decision'] in {'exact', 'compatible'}:
                kind = 'representation_fixed'
            elif 'corpus_price_disagreement' in (row['Reasons'] or ''):
                kind = 'conflicting_prices'
            elif not family:
                kind = 'work_identity_or_context_unknown'
            elif best is not None and best[1].confidence >= HIGH_CONFIDENCE:
                kind = 'strong_operator_evidence_requires_recall_or_price_policy_review'
            elif reference:
                kind = 'compatible_reference_only_no_automatic_operator_price'
            elif best:
                kind = 'known_operator_family_but_incomplete_or_incompatible_evidence'
            else:
                kind = 'no_known_family_operator_price'
            counts[kind] += 1
            writer.writerow([row_no, original['Position'], original['Query'], effective,
                             family or '', row['Decision'] if row else 'nonwork', kind, len(candidates),
                             best[0].ref if best else '', best[0].text if best else '',
                             best[1].confidence if best else 0,
                             '; '.join(best[1].reasons) if best else '', reference])
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(dict(audit(args.corpus, args.baseline, args.current, args.output)))
