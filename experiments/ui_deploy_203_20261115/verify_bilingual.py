from playwright.sync_api import sync_playwright
from pathlib import Path
import json
root=Path('/home/yangao/r2g-skills/experiments/ui_deploy_203_20261115')
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1000})
 errors=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.goto('http://127.0.0.1:18870/',wait_until='networkidle')
 page.get_by_role('button',name='EN',exact=True).click()
 page.wait_for_function("localStorage.getItem('r2g-lang') === 'en'")
 en=page.locator('body').inner_text()
 assert 'Start' in en or 'Jobs' in en,en[:500]
 page.screenshot(path=str(root/'ui_english.png'),full_page=True)
 page.reload(wait_until='networkidle')
 assert page.evaluate("localStorage.getItem('r2g-lang')")=='en'
 page.get_by_role('button',name='ZH',exact=True).click()
 page.wait_for_function("localStorage.getItem('r2g-lang') === 'zh'")
 zh=page.locator('body').inner_text()
 assert '开始' in zh and '任务' in zh
 page.screenshot(path=str(root/'ui_chinese.png'),full_page=True)
 page.goto('http://127.0.0.1:18870/?lang=en',wait_until='networkidle')
 assert page.get_by_role('button',name='EN',exact=True).get_attribute('aria-pressed')=='true'
 assert not errors,errors
 report={'language_toggle':True,'saved_language_survives_reload':True,'query_lang_en':True,'browser_errors':errors,'english_opening':en[:350],'chinese_opening':zh[:350]}
 (root/'browser_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(report,ensure_ascii=False))
 browser.close()
