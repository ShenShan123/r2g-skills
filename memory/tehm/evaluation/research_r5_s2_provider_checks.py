"""Offline transport/authorization checks. No credentials or network access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from . import research_r5_s2_controller as c
from . import research_r5_s2_provider as p

AUTH = {'schema': 'r5-s2-user-authorization-approved-v1', 'role': 'DEV_ONLY_NOT_FINAL',
    'retry_limit': 0, 'authorized_tasks': 1, 'authorized_policies': list(c.POLICIES),
    'maximum_calls_per_policy': 2, 'maximum_total_calls': 6,
    'maximum_input_tokens_per_call': 8000, 'maximum_output_tokens_per_call': 2000,
    'maximum_total_tokens': 60000, 'model_version': 'DeepSeek-V4.1-Flash',
    'official_api_model_id': p.MODEL, 'provider_host': p.HOST}


class Checks(unittest.TestCase):
    def test_authorization(self):
        p.validate_live(p.live_plan(AUTH), AUTH)
        for field, value in [('retry_limit', 1), ('maximum_total_calls', 7),
                             ('official_api_model_id', 'deepseek-pro'), ('role', 'FINAL')]:
            changed = dict(AUTH, **{field: value})
            with self.assertRaises(ValueError):
                p.validate_live(p.live_plan(changed), changed)
        with self.assertRaises(ValueError):
            p.validate_live(dict(p.live_plan(AUTH), calls_total=7), AUTH)

    def test_budget_reopen_six_calls(self):
        with tempfile.TemporaryDirectory(prefix='tehm-c7-check-') as temp:
            path = Path(temp) / 'ledger.jsonl'
            ledger = p.LiveLedger.create(path, p.live_plan(AUTH), authorization=AUTH)
            for policy in c.POLICIES:
                for _ in range(2):
                    ident = ledger.reserve('dev_task_001', policy, {}, input_tokens=8000, counter_id=p.COUNTER)
                    ledger.claim(ident, {})
                    with self.assertRaises(ValueError):
                        ledger.claim(ident, {})
                    ledger.finish(ident, 'RECEIVED', response='{}', usage={'input_tokens':10,'output_tokens':1})
                    ledger.reject(ident)
            ledger = p.LiveLedger(path, p.live_plan(AUTH), authorization=AUTH)
            self.assertEqual(ledger.snapshot()['reserved_tokens'], 60000)
            self.assertEqual(ledger.snapshot()['mode'], 'LIVE_AUTHORIZED')
            with self.assertRaises(ValueError):
                ledger.reserve('dev_task_001', 'tehm', {}, input_tokens=8000, counter_id=p.COUNTER)

    def test_usage_halt(self):
        with tempfile.TemporaryDirectory(prefix='tehm-c7-check-') as temp:
            ledger = p.LiveLedger.create(Path(temp)/'ledger.jsonl', p.live_plan(AUTH), authorization=AUTH)
            ident = ledger.reserve('dev_task_001', 'tehm', {}, input_tokens=8000, counter_id=p.COUNTER)
            ledger.claim(ident, {})
            ledger.finish(ident, 'RECEIVED', response='{}', usage={'input_tokens':8001,'output_tokens':1})
            self.assertTrue(ledger.snapshot()['halted'])

    def test_credentials_flash_not_pro(self):
        fake = Mock()
        fake.read_text.return_value = 'DEEPSEEK_API_KEY=fixture_flash\nDEEPSEEK_MODEL=deepseek-v4.1-flash\nDEEPSEEK_BASE_URL=https://api.deepseek.com\nDEEPSEEK_API_KEY=fixture_pro\nDEEPSEEK_MODEL=deepseek-v4.1-pro\n'
        self.assertEqual(p.flash_key(fake), 'fixture_flash')
        fake.read_text.return_value = fake.read_text.return_value.replace('deepseek-v4.1-flash','deepseek-v4.1-pro')
        with self.assertRaises(ValueError):
            p.flash_key(fake)

    def test_request_and_usage(self):
        body = p.request_body({'system':'Return JSON', 'user':{}})
        self.assertEqual(body['max_tokens'],2000)
        self.assertEqual(body['thinking'],{'type':'disabled'})
        with self.assertRaises(ValueError):
            p.request_body({'system':'x'*8000,'user':{}})
        value = {'model':p.MODEL,'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12,
                     'prompt_cache_hit_tokens':3,'prompt_cache_miss_tokens':7}}
        self.assertEqual(p.decode(json.dumps(value).encode())[1],{'input_tokens':10,'output_tokens':2})
        for k, v in [('total_tokens',13),('prompt_tokens',True),('prompt_cache_hit_tokens',4)]:
            bad=copy.deepcopy(value);bad['usage'][k]=v
            with self.assertRaises(ValueError):p.decode(json.dumps(bad).encode())

    def test_no_retry_no_redirect(self):
        opener=Mock();opener.open.side_effect=TimeoutError()
        with patch.object(p.urllib.request,'build_opener',return_value=opener):
            status, raw=p.dispatch({},'fixture_not_a_real_key')
        self.assertEqual(opener.open.call_count,1)
        self.assertEqual(status['terminal'],'TIMEOUT')
        self.assertEqual(raw,b'')
        self.assertIsNone(p.NoRedirect().redirect_request(None,None,302,None,None,'https://elsewhere/'))


if __name__=='__main__':unittest.main()
