"""Keep original price records intact and attach versioned Bulgarian work meaning.

The JSONL sidecar is derived metadata, not newly verified price evidence.
It can be rebuilt after a reviewed vocabulary change. No database is changed.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.cost_match.work_catalog import work_metadata


def enrich(corpus: Path, output: Path) -> Counter:
    if output.resolve() == corpus.resolve():
        raise ValueError('Enrichment must not overwrite the original corpus.')
    counts = Counter()
    opener = gzip.open if output.suffix == '.gz' else open
    with corpus.open(encoding='utf-8-sig', newline='') as source, opener(output, 'wt', encoding='utf-8') as target:
        quotations = json.load(source) if corpus.suffix.lower() == '.json' else csv.DictReader(source)
        if isinstance(quotations, dict):
            raise ValueError('JSON corpus must contain an array of original quotations.')
        for quotation in quotations:
            metadata = work_metadata(quotation.get('description') or quotation.get('desc') or quotation.get('name') or '')
            # Quotation fields, including provenance and money, are unchanged.
            target.write(json.dumps({'quotation': quotation, 'canonical_work': metadata},
                                    ensure_ascii=False) + '\n')
            counts['records'] += 1
            counts['object_known' if metadata['work']['object'] else 'object_unknown'] += 1
            counts['scope_known' if metadata['work']['scope'] else 'scope_unknown'] += 1
            counts['operation_known' if metadata['work']['operations'] else 'operation_unknown'] += 1
            if metadata['work']['defaults_applied']:
                counts['profile_defaults_used'] += 1
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(dict(enrich(args.corpus, args.output)), ensure_ascii=False))
