"""C7 known-DEV paired model proposals; replay never calls a provider."""
import argparse
import json
from pathlib import Path

from . import research_r5_s2_actions as a
from . import research_r5_s2_controller as c
from . import research_r5_s2_native as n
from . import research_r5_s2_provider as p


def verify(bundle, plan):
    for name, digest in plan['bundle_files'].items():
        if n.sha(bundle / name) != digest:
            raise c.ControllerError('frozen bundle drift: ' + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credentials', type=Path)
    parser.add_argument('--replay', type=Path)
    args = parser.parse_args()
    bundle, out = args.bundle.resolve(), args.output.resolve()
    if bool(args.credentials) == bool(args.replay):
        raise c.ControllerError('choose exactly one live credentials or offline replay')
    plan = json.loads((bundle / 'plan.json').read_text())
    verify(bundle, plan)
    auth = json.loads((bundle / 'authorization.json').read_text())
    budget = p.live_plan(auth)
    p.validate_live(budget, auth)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    n.write(out / 'budget.json', budget)
    n.write(out / 'execution.json', {'mode': 'SAVED_RESPONSE_REPLAY' if args.replay else 'LIVE',
        'role': 'KNOWN_DEV_CALIBRATION_NOT_TRANSFER_NOT_FINAL', 'retry_limit': 0})
    ledger = p.LiveLedger.create(out / 'events.jsonl', budget, authorization=auth)
    source = {n.SOURCE: (bundle / 'source.sv').read_text()}
    key = p.flash_key(args.credentials) if args.credentials else None
    rows, http_attempts, actual_tokens = [], 0, 0
    halt = False
    for policy in c.POLICIES:
        previous = None
        for attempt in (1, 2):
            if halt:
                break
            arm = out / f'{policy}-{attempt}'
            arm.mkdir()
            public = json.loads((bundle / 'authority' / f'{policy}.public.json').read_text())
            private = json.loads((bundle / 'authority' / f'{policy}.private.json').read_text())
            prompt = a.prompt(source, public)
            prompt['user'].update(attempt=attempt, restart_from_original=True, previous_attempt=previous)
            body = p.request_body(prompt)
            n.write(arm / 'prompt.json', prompt)
            n.write(arm / 'http-request.json', body)
            n.write(arm / 'input-admission.json', {'request_utf8_bytes': len(c.canonical(body)),
                'framing_allowance': 512, 'reserved_input_tokens': 8000,
                'exact_tokenizer': False, 'counter_id': p.COUNTER})
            ident = ledger.reserve('dev_task_001', policy, body, input_tokens=8000, counter_id=p.COUNTER)
            ledger.claim(ident, body)
            if args.replay:
                saved = args.replay / arm.name
                if json.loads((saved / 'http-request.json').read_text()) != body:
                    raise c.ControllerError('replay request drift')
                transport = json.loads((saved / 'transport.json').read_text())
                raw = (saved / 'http-response.bin').read_bytes()
            else:
                http_attempts += 1
                transport, raw = p.dispatch(body, key)
            n.write(arm / 'transport.json', transport)
            with (arm / 'http-response.bin').open('xb') as handle:
                handle.write(raw)
            row = {'policy': policy, 'attempt': attempt, 'transport': transport,
                   'request_sha256': c.sha(c.canonical(body)), 'response_sha256': c.sha(raw)}
            if transport['terminal'] != 'RECEIVED':
                ledger.finish(ident, transport['terminal'], response=None, usage=None)
                row['outcome'] = 'TRANSPORT_UNKNOWN'
                rows.append(row)
                halt = True
                break
            try:
                response, usage, metadata = p.decode(raw)
            except (ValueError, TypeError, KeyError):
                ledger.finish(ident, 'ERROR', response=None, usage=None)
                row['outcome'] = 'RESPONSE_OR_USAGE_UNKNOWN'
                rows.append(row)
                halt = True
                break
            n.write(arm / 'provider-metadata.json', metadata)
            with (arm / 'proposal.txt').open('x') as handle:
                handle.write(response)
            ledger.finish(ident, 'RECEIVED', response=response, usage=usage)
            row.update(usage=usage, provider_model=metadata['model'], finish_reason=metadata['finish_reason'])
            actual_tokens += sum(usage.values())
            if ledger.snapshot()['halted'] or metadata['model'] not in {'deepseek-flash', 'deepseek-v4.1-flash'}:
                row['outcome'] = 'USAGE_OR_MODEL_MISMATCH'
                rows.append(row)
                halt = True
                break
            try:
                if metadata['finish_reason'] != 'stop':
                    raise c.ControllerError('non-stop completion')
                request = a.handoff(source, response, public, private)
            except (ValueError, TypeError, KeyError):
                ledger.reject(ident)
                previous = {'status': 'INVALID_PROPOSAL'}
                row['outcome'] = 'INVALID_PROPOSAL'
                rows.append(row)
                print(json.dumps(row), flush=True)
                continue
            n.write(arm / 'worker-request.json', request)
            actionout = arm / 'worker-output'
            actionout.mkdir()
            command = n.sandbox() + ['--ro-bind', str(bundle / 'code/memory'), '/code',
                '--ro-bind', str(bundle / 'source.sv'), '/source.sv',
                '--ro-bind', str(arm / 'worker-request.json'), '/request.json',
                '--ro-bind', str(bundle / 'worker.py'), '/worker.py', '--bind', str(actionout), '/out',
                '--setenv', 'PYTHONPATH', '/code', '--chdir', '/', '/usr/bin/python3', '/worker.py']
            process = n.execute(command, arm / 'worker-process', 30)
            if process['returncode'] != 0 or process['timed_out']:
                row['outcome'] = 'WORKER_UNKNOWN'
                rows.append(row)
                halt = True
                break
            receipt = json.loads((actionout / 'action.json').read_text())
            candidate = actionout / 'candidate'
            actual = {n.SOURCE: (candidate / n.SOURCE).read_text()}
            if receipt['after_digest'] != c.source_digest(actual):
                raise c.ControllerError('candidate identity drift')
            ledger.candidate(ident, receipt['after_digest'])
            ledger.evaluate(ident, receipt['after_digest'])
            result = n.evaluate(candidate, bundle / 'private', bundle / 'deps', plan, arm / 'evaluation')
            feedback = ledger.feedback(ident, result)
            n.write(arm / 'feedback.json', feedback)
            row.update(action=request['action'], action_status=receipt['status'],
                candidate_sha256=n.sha(candidate / n.SOURCE), feedback=feedback,
                outcome=feedback['task_verdict'])
            rows.append(row)
            print(json.dumps(row), flush=True)
            if feedback['stop']:
                break
            previous = {'action': request['action'], 'candidate_digest': receipt['after_digest'],
                        'overall': feedback['task_verdict'], 'obligations': feedback['obligations']}
    verify(bundle, plan)
    n.write(out / 'ledger-snapshot.json', ledger.snapshot())
    report = {'schema': 'r5-s2-provider-c7-report-v1', 'rows': rows,
        'role': 'KNOWN_DEV_CALIBRATION_NOT_TRANSFER_NOT_FINAL', 'http_attempts': http_attempts,
        'replayed_responses': len(rows) if args.replay else 0,
        'actual_tokens_from_saved_or_live_usage': actual_tokens,
        'model_version': auth['model_version'], 'halted': halt,
        'registered_tasks': 1, 'policy_count': 3, 'method_tasks': 0,
        'native_adapter_role_note': 'C2 inner scripted label describes adapter development, not C7 proposal provenance',
        'memory_writes': 0, 'runtime_learning': False, 'rows_include_all_attempts': True}
    n.write(out / 'report.json', report)
    print(json.dumps({'http_attempts': http_attempts, 'actual_tokens': actual_tokens, 'halted': halt}), flush=True)


if __name__ == '__main__':
    main()
