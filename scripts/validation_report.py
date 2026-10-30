#!/usr/bin/env python3
"""Deterministic CI evidence, quality gates, and English job summaries."""
import argparse
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

LIMITS = {'fast_forward': 25, 'diverged_clean_merge': 30}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def read_json(path):
    with path.open('rb') as source:
        raw = source.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Evidence exceeds its size limit')
    return json.loads(raw)


def run_stage(name, command, directory, cwd, env, timeout=120):
    started = time.monotonic()
    destination = directory / (name + '.log')
    status, rc = 'launch_failed', 127
    with destination.open('wb') as log:
        process = None
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = process.wait(timeout=timeout)
                status = 'passed' if rc == 0 else 'failed'
            except subprocess.TimeoutExpired:
                rc, status = 124, 'timeout'
                log.write(b'\n[isolated] Stage exceeded its time budget.\n')
        except OSError:
            log.write(b'[isolated] Stage could not start; check executable availability.\n')
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    return {'name': name, 'status': status, 'exit_code': rc,
            'seconds': round(time.monotonic() - started, 3), 'log': str(destination.resolve())}


def metrics_gate(directory):
    issues, counts = [], {}
    try:
        rows = read_json(directory / 'workflow-measurement.json')
        if not isinstance(rows, list) or len(rows) != len(LIMITS):
            raise ValueError('Expected both reference workflow scenarios')
        for row in rows:
            if not isinstance(row, dict) or row.get('scenario') not in LIMITS or row['scenario'] in counts:
                raise ValueError('Invalid or duplicate workflow scenario')
            values = [row.get('plan_git_invocations'), row.get('apply_git_invocations')]
            if any(type(value) is not int or value < 0 for value in values):
                raise ValueError('Invalid invocation count')
            total = sum(values)
            counts[row['scenario']] = {'actual': total, 'maximum': LIMITS[row['scenario']]}
            if total > LIMITS[row['scenario']]:
                issues.append(row['scenario'] + ': Git invocation budget exceeded')
    except (OSError, ValueError, TypeError, KeyError):
        issues.append('Workflow measurement evidence is missing or invalid')
    try:
        data = read_json(directory / 'diagnostic-evaluation.json')
        cases = data.get('cases') if isinstance(data, dict) else None
        if (not isinstance(cases, list) or not cases or data.get('passed') is not True
                or any(not isinstance(case, dict) or case.get('matched') is not True for case in cases)):
            issues.append('Diagnostic fixture expectations did not all pass')
    except (OSError, ValueError, TypeError):
        issues.append('Diagnostic fixture evidence is missing or invalid')
    result = {'passed': not issues, 'invocation_counts': counts, 'issues': issues,
              'scope': 'Fixtures and invocation counts; no field-accuracy or wall-clock speed claim'}
    write_json(directory / 'quality-gates.json', result)
    return result


def safe(value):
    value = ''.join(c for c in str(value) if c.isprintable())[:300]
    value = html.escape(value).replace('|', '&#124;').replace('`', '&#96;')
    for character in '[]()':
        value = value.replace(character, '&#' + str(ord(character)) + ';')
    return value


def report(directory):
    lines = ['# Bettergit validation', '']
    try:
        summary = read_json(directory / 'summary.json')
        if not isinstance(summary, dict) or not isinstance(summary.get('checks'), list):
            raise ValueError('Invalid summary')
    except (OSError, ValueError, TypeError):
        return '\n'.join(lines + ['**INCOMPLETE**: summary missing or invalid. Inspect setup/runner logs. Missing evidence is not a pass.']) + '\n', False
    checks = summary['checks']
    required = {'versions', 'unittest', 'zsh_syntax', 'zsh_smoke'}
    if summary.get('measure_requested'):
        required |= {'workflow_measurement', 'diagnostic_evaluation', 'quality_gates'}
    names = [c.get('name') for c in checks if isinstance(c, dict) and isinstance(c.get('name'), str)]
    passed = (summary.get('passed') is True and summary.get('complete') is True and set(names) == required and len(names) == len(required)
              and all(isinstance(c, dict) and c.get('status') == 'passed' and c.get('exit_code') == 0 for c in checks)
              and summary.get('isolated_config_unchanged') is True
              and summary.get('protected_user_configs_unchanged') is True)
    lines += ['**' + ('PASS' if passed else 'FAIL / INCOMPLETE') + '**', '',
              '| Stage | Result | Seconds |', '| --- | --- | --- |']
    for check in checks:
        if isinstance(check, dict):
            lines.append('| ' + safe(check.get('name', 'unknown')) + ' | ' + safe(check.get('status', 'unknown')) + ' | ' + safe(check.get('seconds', '?')) + ' |')
    for key, label in [('isolated_config_unchanged', 'Private test configuration unchanged'),
                       ('protected_user_configs_unchanged', 'Protected user configuration unchanged')]:
        lines += ['', label + ': **' + safe(summary.get(key, 'unknown')) + '**.']
    try:
        with (directory / 'unittest.log').open('rb') as source:
            source.seek(0, 2); source.seek(max(0, source.tell() - 262144))
            tail = source.read().decode('utf-8', errors='replace')
        matches = re.findall(r'^Ran (\d+) tests? in ', tail, re.M)
        if matches:
            lines += ['', 'Tests executed: **' + matches[-1] + '** (consult stage result for success).']
        failures = re.findall(r'^(?:FAIL|ERROR): (.+)$', tail, re.M)
        if failures:
            lines += ['', 'Failing test identifiers:'] + ['- ' + safe(name) for name in failures[:20]]
    except OSError:
        lines += ['', 'Test log unavailable; inspect setup/runner logs.']
    if summary.get('measure_requested'):
        try:
            gates = read_json(directory / 'quality-gates.json')
            if not isinstance(gates, dict) or gates.get('passed') is not True:
                passed = False
            lines += ['', 'Git invocation counts (plan + apply):']
            for name, value in gates.get('invocation_counts', {}).items():
                lines.append('- ' + safe(name) + ': ' + safe(value['actual']) + ' / maximum ' + safe(value['maximum']))
            lines += ['- ' + safe(issue) for issue in gates.get('issues', [])]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            passed = False
            lines += ['', 'Quality-gate evidence unavailable or invalid.']
    if not passed:
        lines[2] = '**FAIL / INCOMPLETE**'
    lines += ['', 'Download this matrix job artifact for stage logs and JSON evidence.',
              'Timing is informational. Diagnostic fixtures do not measure real-world accuracy.',
              'This report does not inspect user repositories or authorize publication/recovery.']
    return '\n'.join(lines) + '\n', passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--check-metrics', action='store_true')
    parser.add_argument('--github-summary', action='store_true')
    ns = parser.parse_args()
    ns.input.mkdir(parents=True, exist_ok=True)
    if ns.check_metrics:
        result = metrics_gate(ns.input)
        print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 1
    markdown, passed = report(ns.input)
    (ns.input / 'report.md').write_text(markdown)
    if ns.github_summary and os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(markdown)
    print(markdown)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
