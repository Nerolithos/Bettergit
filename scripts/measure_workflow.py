#!/usr/bin/env python3
"""Measure helper overhead on local temporary repositories; no external remote."""
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_bettergit as fixtures
import bettergit as bg
from bettergit_workflow import Engine


def measure(diverged):
    fixture = fixtures.RepositoryTests()
    fixture.setUp()
    try:
        if diverged:
            Path('local.txt').write_text('local')
            fixture.commit(fixture.local)
        fixture.remote_change()
        calls = Counter()
        def probe(args, **kwargs):
            calls['probe:' + args[0]] += 1
            return bg.probe(args, **kwargs)
        def run(args):
            calls['run:' + args[0]] += 1
            return bg.run_git(args)
        start = time.monotonic()
        workflow = Engine(probe, run, lambda _: True, lambda _: None)
        with workflow.store.lock():
            operation = workflow.prepare()
            plan_count, plan_seconds = sum(calls.values()), time.monotonic() - start
            start = time.monotonic()
            rc = workflow.apply(operation)
        if rc:
            raise RuntimeError('measurement merge failed')
        return {'scenario': 'diverged_clean_merge' if diverged else 'fast_forward',
                'plan_git_invocations': plan_count, 'plan_seconds': round(plan_seconds, 4),
                'apply_git_invocations': sum(calls.values()) - plan_count, 'apply_seconds': round(time.monotonic() - start, 4),
                'commands': dict(calls), 'per_exec_250ms_overhead_model_seconds': sum(calls.values()) * .25,
                'model_is_not_observed_runtime': True}
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    print(json.dumps([measure(False), measure(True)], indent=2))
