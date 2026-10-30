#!/usr/bin/env python3
"""Evaluate labelled synthetic traces; does not claim field accuracy."""
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from bettergit_diagnostics import trace_report
from diagnostic_cases import CASES


def evaluate():
    labels = ('filesystem', 'transport', 'authentication', 'hook', 'interaction')
    totals = {label: {'true_positive': 0, 'false_positive': 0, 'false_negative': 0} for label in labels}
    cases = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'trace'
        for name, events, expected in CASES:
            path.write_text('\n'.join(json.dumps(e) for e in events))
            report = trace_report(path)
            actual = {f['code'] for f in report['findings']} - {'unknown'}
            for label in labels:
                totals[label]['true_positive'] += int(label in actual and label in expected)
                totals[label]['false_positive'] += int(label in actual and label not in expected)
                totals[label]['false_negative'] += int(label not in actual and label in expected)
            cases.append({'name': name, 'source': 'synthetic_trace', 'expected': sorted(expected),
                          'actual': sorted(actual), 'unknown': not actual, 'matched': actual == expected})
    for values in totals.values():
        tp, fp, fn = values['true_positive'], values['false_positive'], values['false_negative']
        values['precision'] = tp / (tp + fp) if tp + fp else None
        values['recall'] = tp / (tp + fn) if tp + fn else None
    return {'schema': 1, 'scope': 'labelled fixtures, not real-world accuracy or probability calibration',
            'passed': all(c['matched'] for c in cases), 'cases': cases, 'per_label': totals,
            'unknown_cases': sum(c['unknown'] for c in cases), 'seconds': round(time.monotonic() - started, 6)}


if __name__ == '__main__':
    result = evaluate()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
