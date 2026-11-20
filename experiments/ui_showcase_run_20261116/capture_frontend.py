"""Capture the completed, real UI job card with its six stage previews."""
from pathlib import Path
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path('/data/yangao/r2g_ui_20261115/showcase_20261116')
BASE = 'http://127.0.0.1:8770'
launch = json.loads((ROOT / 'launch.json').read_text())
job_id = launch['job_id']
job = json.loads(urllib.request.urlopen(BASE + '/api/jobs/' + job_id).read())
design = job['designs'][0]
assert job['status'] == 'done' and design['status'] == 'passed', (job['status'], design['stage'], design['detail'])
views = json.loads(urllib.request.urlopen(BASE + '/api/jobs/' + job_id + '/designs/md5/stage-views?version=final').read())
assert len(views['stages']) == 6 and all(view['available'] for view in views['stages']), views
errors = []
outputs = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path='/usr/bin/google-chrome', args=['--no-sandbox'],
        env={**os.environ, 'FONTCONFIG_FILE': str(ROOT / 'fontconfig.xml')})
    page = browser.new_page(viewport={'width': 1440, 'height': 1200}, device_scale_factor=2)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE + '/jobs/' + job_id + '?lang=en', wait_until='networkidle')
    if page.get_by_role('button', name='Light', exact=True).count():
        page.get_by_role('button', name='Light', exact=True).click()
    page.get_by_role('button', name='Show layout evolution by stage', exact=True).click()
    card = page.locator('div.card').filter(has=page.get_by_role('button', name='Details', exact=True))
    assert card.count() == 1
    card.scroll_into_view_if_needed()
    page.wait_for_function('''() => {
      const images = [...document.querySelectorAll('img[src*="stage-views/"]')];
      return images.length === 6 && images.every(image => image.complete && image.naturalWidth > 0);
    }''', timeout=120000)
    page.evaluate('document.fonts.ready')
    page.mouse.move(0, 0)
    image = ROOT / 'screenshots/md5_ui_card_en.png'
    card.screenshot(path=str(image), animations='disabled')
    outputs.append(str(image))
    page.screenshot(path=str(ROOT / 'screenshots/md5_ui_page_en.png'), full_page=True, animations='disabled')
    page.get_by_role('button', name='ZH', exact=True).click()
    show_zh = page.get_by_role('button', name='显示各阶段版图演化', exact=True)
    if show_zh.count():
        show_zh.click()
    page.wait_for_function('''() => {
      const images = [...document.querySelectorAll('img[src*="stage-views/"]')];
      return images.length === 6 && images.every(image => image.complete && image.naturalWidth > 0);
    }''', timeout=120000)
    page.evaluate('document.fonts.ready')
    page.mouse.move(0, 0)
    # Use the same card after switching language; its text and image geometry are real.
    card_zh = page.locator('div.card').filter(has=page.get_by_role('button', name='详情', exact=True))
    card_zh.screenshot(path=str(ROOT / 'screenshots/md5_ui_card_zh.png'), animations='disabled')
    outputs.append(str(ROOT / 'screenshots/md5_ui_card_zh.png'))
    assert not errors, errors
    browser.close()
receipt = {'job_id': job_id, 'url': BASE + '/jobs/' + job_id, 'job': job, 'stage_views': views,
           'screenshots': outputs, 'capture': 'Unmodified live UI, 1440 px viewport, 2x device scale, light theme',
           'browser_errors': errors}
(ROOT / 'capture.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'job_id': job_id, 'screenshots': outputs, 'browser_errors': errors}, ensure_ascii=False))
