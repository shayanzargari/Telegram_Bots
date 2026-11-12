"""Deterministic browser regressions using synthetic Telegram responses only."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge',headless=True)
        page = browser.new_page(viewport={'width':320,'height':844})
        state = {'version':1,'error':None,'api_calls':1,'queued_updates':0,'answers':[],
                 'commands':[{'command':'menu','description':'Open menu'}],
                 'messages':[{'message_id':1,'chat':{'id':-100111},
                    'from':{'is_bot':True,'first_name':'Our Home'},'text':'Synthetic checklist',
                    'reply_markup':{'inline_keyboard':[[{'text':'W'*24,'callback_data':'cat:long'}],
                                                      [{'text':'Main menu','callback_data':'menu'}]]}}]}
        page.route('http://audit.local/**', lambda route: route.fulfill(
            content_type='application/json' if route.request.url.endswith('/sim/state') else 'text/html',
            body=json.dumps(state) if route.request.url.endswith('/sim/state') else args.source.read_text('utf-8')))
        page.goto('http://audit.local/')
        page.locator('[data-callback="menu"]').wait_for()

        def check(name, predicate):
            print('Checking ' + name, flush=True)
            try:
                assert predicate(), name
                checks.append({'name':name,'passed':True})
            except Exception as exc:
                checks.append({'name':name,'passed':False,'error':str(exc)})

        check('long_category_label_320px', lambda: page.locator('[data-callback="cat:long"]').evaluate(
            'b => b.scrollWidth <= b.clientWidth + 1') and page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        page.locator('[data-callback="menu"]').focus()
        page.evaluate('window.oldPanel=document.querySelector("article"); state.version++; render()')
        check('acknowledgement_preserves_panel_and_keyboard_focus', lambda: page.evaluate(
            'oldPanel === document.querySelector("article") && document.activeElement.dataset.callback === "menu"'))
        page.evaluate('reply(1); state.messages=[]; render()')
        check('removed_prompt_clears_reply_target', lambda: page.evaluate('replyId === null && document.getElementById("replying").hidden'))
        page.evaluate('''window.submissions=0; window.finishSend=null;
            post=()=>{submissions++; return new Promise(resolve=>finishSend=resolve)};
            document.getElementById('text').value='First draft';
            send('First draft'); send('First draft'); void 0;''')
        check('rapid_send_submits_once', lambda: page.evaluate('submissions === 1'))
        page.evaluate("document.getElementById('text').value='Next draft'; finishSend({});")
        page.wait_for_timeout(30)
        check('send_completion_preserves_new_draft', lambda: page.locator('#text').input_value() == 'Next draft')
        page.reload()
        page.locator('[data-callback="menu"]').wait_for()
        page.evaluate('''window.counterPanel=document.querySelector('article');
            fetch=async()=>new Response(JSON.stringify({...state,api_attempts:99,queued_updates:7}),{status:200}); void 0;''')
        page.evaluate('poll()')
        check('same_version_counters_refresh_without_panel_replacement', lambda: page.evaluate(
            "document.getElementById('status').textContent.includes('99') && document.getElementById('status').textContent.includes('7') && counterPanel === document.querySelector('article')"))
        page.reload()
        page.locator('[data-callback="menu"]').wait_for()
        page.evaluate("fetch=async()=>new Response(JSON.stringify({error:'server unavailable'}),{status:500});")
        page.evaluate('poll()')
        check('failed_poll_preserves_last_valid_state', lambda: page.evaluate('Array.isArray(state?.messages)'))
        page.reload()
        page.locator('[data-callback="menu"]').wait_for()
        page.evaluate('''fetch=async (url,options)=>new Promise((resolve,reject)=>{
            options?.signal?.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError')));
        });
        const originalTimer=setTimeout;
        window.setTimeout=(fn,ms,...args)=>originalTimer(fn,Math.min(ms,20),...args);
        document.getElementById('text').value='Timeout draft';
        send('Timeout draft'); void 0;''')
        page.wait_for_timeout(120)
        check('stalled_send_recovers_without_losing_draft', lambda: page.evaluate(
            "!document.querySelector('#form .send').disabled && document.getElementById('text').value === 'Timeout draft'"))
        browser.close()
    result={'checks':checks,'passed':all(c['passed'] for c in checks)}
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    raise SystemExit(not result['passed'])


if __name__=='__main__':
    main()
