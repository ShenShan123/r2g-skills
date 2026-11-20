"""Submit the selected MD5 RTL through the deployed frontend on 203."""
from pathlib import Path
import hashlib
import json
import time

from playwright.sync_api import sync_playwright

ROOT = Path('/data/yangao/r2g_ui_20261115/showcase_20261116')
BASE = 'http://127.0.0.1:8770'
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1440, 'height': 1100}, device_scale_factor=2)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE + '/?lang=en', wait_until='networkidle')
    page.locator('input[type=file]').set_input_files(str(ROOT / 'inputs/md5'))
    page.get_by_placeholder('Top module name (optional, auto-detected)').fill('md5')
    button = page.get_by_role('button', name='Process 1 design', exact=True)
    # Uploads and submission are performed by the real frontend event handler.
    with page.expect_response(lambda response: response.url == BASE + '/api/jobs' and response.request.method == 'POST') as response:
        button.click()
    result = response.value.json()
    assert response.value.ok, result
    page.wait_for_url('**/jobs/' + result['job_id'])
    page.screenshot(path=str(ROOT / 'screenshots/submitted.png'), full_page=True)
    receipt = {'submitted_at_unix': time.time(), 'job_id': result['job_id'], 'method': 'Playwright frontend upload and Start button',
               'url': page.url, 'platform': 'nangate45', 'output': 'graph', 'params': 'r2g defaults',
               'browser_errors': errors, 'input_files': [{'path': str(path.relative_to(ROOT / 'inputs/md5')),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted((ROOT / 'inputs/md5').rglob('*.v'))]}
    (ROOT / 'launch.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    browser.close()
