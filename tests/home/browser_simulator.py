"""Optional browser acceptance run; requires Playwright and the local server."""
import argparse
import json
from pathlib import Path
import sqlite3
import zipfile
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8877')
    parser.add_argument('--report-dir', type=Path, default=Path(__import__('os').environ.get('BOT_CENTER_TEST_OUTPUT',Path(__file__).resolve().parents[2]/'work/tests/manual/home-browser')))
    parser.add_argument('--data-dir', type=Path, help='Optional synthetic bot directory for read-only database assertions')
    args = parser.parse_args()
    report = args.report_dir
    report.mkdir(parents=True, exist_ok=True)
    if args.data_dir:
        args.data_dir = args.data_dir.resolve()
        synthetic_root = Path(__file__).resolve().parents[2] / 'work'
        if not args.data_dir.is_relative_to(synthetic_root.resolve()):
            raise ValueError('Browser data assertions require an isolated .local-telegram fixture.')
    def task_rows():
        if not args.data_dir:
            return None
        with sqlite3.connect((args.data_dir / 'tasks.sqlite3').as_uri() + '?mode=ro', uri=True) as database:
            return {row[0]: row[1:] for row in database.execute(
                'SELECT id,title,category,done,deleted,revision FROM tasks ORDER BY id')}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 950}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base_url, wait_until="domcontentloaded")
        def settled(update=None):
            page.wait_for_function("""updateId => state &&
                state.last_acknowledged_update >= updateId && state.queued_updates === 0
            """, arg=update['update_id'] if update else -1)

        def click(selector):
            with page.expect_response(lambda response: response.url.endswith('/sim/click')) as response:
                page.locator(selector).first.click()
            settled(response.value.json()['result'])

        def send(text):
            page.locator('#text').fill(text)
            with page.expect_response(lambda response: response.url.endswith('/sim/message')) as response:
                page.locator('#form .send').click()
            settled(response.value.json()['result'])

        def open_menu():
            selector = '[data-callback="menu"]'
            if page.locator(selector).count():
                click(selector)
            else:
                # Cancel already returns to Main menu. Do not seek a redundant
                # Main menu button on the Main menu itself.
                page.locator('[data-callback="category:add"]').wait_for()

        def screenshot_panel(panel, name):
            style = page.add_style_tag(content='.layout{height:auto!important}.messages{overflow:visible!important}.chat{min-height:0!important}#toast{display:none!important}')
            panel.screenshot(path=str(report / name))
            style.evaluate('(node)=>node.remove()')

        send('/menu')
        assert page.locator('#commands button').all_text_contents() == ['/menu']
        page.locator('[data-callback="cat:shopping"]').wait_for()
        click('[data-callback="cat:shopping"]')
        click('[data-callback="add:shopping"]')
        page.locator("#replying").wait_for(state="visible")
        assert page.evaluate('state.messages.some(m => m.reply_markup?.force_reply && m.message_id === replyId)')
        send("Browser test item\nBrowser second item")
        page.get_by_text("Added 2 tasks", exact=False).first.wait_for()
        assert page.locator('[data-callback^="task:"]').count() == 0
        open_menu()
        click('[data-callback="cat:shopping"]')
        click('[data-callback^="task:complete:"]')
        assert page.locator('[data-callback^="undo:"]').count() == 0
        click('[data-callback^="task:reopen:"]')
        category_panel = page.locator('article').filter(has=page.locator('[data-callback="remove:shopping"]')).first
        assert 'Tap the numbered circle' not in category_panel.inner_text()
        before_selection = task_rows()
        click('[data-callback="remove:shopping"]')
        assert page.locator('[data-callback^="batch:remove:"]').count() == 0
        pick_ids = [value.split(':')[1] for value in page.locator('[data-callback^="pick:"]').evaluate_all(
            'buttons => buttons.map(button => button.dataset.callback)')][:2]
        assert len(pick_ids) == 2
        for task_id in pick_ids:
            click(f'[data-callback^="pick:{task_id}:"]')
            if before_selection is not None:
                assert task_rows() == before_selection
        assert page.locator('[data-callback^="batch:remove:"]').inner_text() == '🗑️ Move to Trash (2)'
        click(f'[data-callback^="pick:{pick_ids[0]}:"]')
        assert page.locator('[data-callback^="batch:remove:"]').inner_text() == '🗑️ Move to Trash (1)'
        if before_selection is not None:
            assert task_rows() == before_selection
        click(f'[data-callback^="pick:{pick_ids[0]}:"]')
        assert page.locator('[data-callback^="batch:remove:"]').inner_text() == '🗑️ Move to Trash (2)'
        if before_selection is not None:
            assert task_rows() == before_selection
        page.set_viewport_size({'width': 320, 'height': 844})
        screenshot_panel(page.locator('article').filter(has=page.locator('[data-callback^="batch:remove:"]')).first,
                         'simulator-multiselect-remove-320.png')
        page.set_viewport_size({'width': 1280, 'height': 950})
        click('[data-callback^="batch:remove:"]')
        if before_selection is not None:
            expected = dict(before_selection)
            for task_id in map(int, pick_ids):
                title, category, done, deleted, revision = expected[task_id]
                expected[task_id] = (title, category, done, 1, revision + 1)
            assert task_rows() == expected
        click('[data-callback^="undo:"]')
        if before_selection is not None:
            expected = dict(before_selection)
            for task_id in map(int, pick_ids):
                title, category, done, deleted, revision = expected[task_id]
                expected[task_id] = (title, category, done, deleted, revision + 2)
            assert task_rows() == expected
        # Remove both again, then exercise the distinct Trash selection and
        # atomic restore flow, including Undo restore and another restoration.
        open_menu()
        click('[data-callback="cat:shopping"]')
        click('[data-callback="remove:shopping"]')
        for task_id in pick_ids:
            click(f'[data-callback^="pick:{task_id}:"]')
        click('[data-callback^="batch:remove:"]')
        for restore_round in range(2):
            open_menu()
            click('[data-callback="view:all:trash:0"]')
            before_restore = task_rows()
            assert page.locator('[data-callback^="batch:restore:"]').count() == 0
            for task_id in pick_ids:
                click(f'[data-callback^="rpick:{task_id}:"]')
                if before_restore is not None:
                    assert task_rows() == before_restore
            assert page.locator('[data-callback^="batch:restore:"]').inner_text() == '♻️ Restore selected (2)'
            if restore_round == 0:
                page.set_viewport_size({'width': 320, 'height': 844})
                screenshot_panel(page.locator('article').filter(has=page.locator('[data-callback^="batch:restore:"]')).first,
                                 'simulator-multiselect-restore-320.png')
                page.set_viewport_size({'width': 1280, 'height': 950})
            click('[data-callback^="batch:restore:"]')
            if before_restore is not None:
                expected = dict(before_restore)
                for task_id in map(int, pick_ids):
                    title, category, done, deleted, revision = expected[task_id]
                    expected[task_id] = (title, category, done, 0, revision + 1)
                assert task_rows() == expected
            if restore_round == 0:
                click('[data-callback^="undo:"]')
                if before_restore is not None:
                    expected = dict(before_restore)
                    for task_id in map(int, pick_ids):
                        title, category, done, deleted, revision = expected[task_id]
                        expected[task_id] = (title, category, done, 1, revision + 2)
                    assert task_rows() == expected
        open_menu()
        main_callbacks = page.locator('.row button').evaluate_all('buttons => buttons.map(b => b.dataset.callback)')
        assert main_callbacks[0] == 'category:add'
        assert 'add:choose' not in main_callbacks
        assert main_callbacks[-1] == 'backup'
        assert 'help' not in main_callbacks
        assert not any(v.startswith('remove:') or ':done:' in v for v in main_callbacks)
        click('[data-callback="category:add"]')
        page.locator('#replying').wait_for(state='visible')
        assert page.evaluate('state.messages.some(m => m.reply_markup?.force_reply && m.message_id === replyId)')
        send('Weekend projects')
        page.get_by_text('Added category: Weekend projects', exact=False).first.wait_for()
        custom = page.locator('[data-callback^="cat:custom_"]').first.get_attribute('data-callback').split(':')[1]
        click(f'[data-callback="cat:{custom}"]')
        click(f'[data-callback="add:{custom}"]')
        page.locator('#replying').wait_for(state='visible')
        send('Build a shelf')
        open_menu()
        click(f'[data-callback="cat:{custom}"]')
        click(f'[data-callback^="category:remove:{custom}"]')
        open_menu()
        assert page.locator(f'[data-callback="cat:{custom}"]').count() == 0
        click('[data-callback="view:all:trash:0"]')
        click(f'[data-callback^="category:restore:{custom}:"]')
        open_menu()
        assert page.locator(f'[data-callback="cat:{custom}"]').count() == 1
        assert page.evaluate("state.messages.filter(m=>m.from.is_bot&&!m.document&&m.chat.id===-100111).length") == 1
        assert page.locator('a.document').count() == 0
        previous_files = page.locator('a.document').count()
        click('[data-callback="backup"]')
        page.wait_for_function("count => document.querySelectorAll('a.document').length > count", arg=previous_files)
        assert page.locator('a.document').count() == previous_files + 1
        with page.expect_download() as pending:
            page.locator("a.document").last.click()
        download = pending.value
        backup = report / "browser-backup.zip"
        download.save_as(str(backup))
        with zipfile.ZipFile(backup) as archive:
            assert archive.testzip() is None
            assert len(json.loads(archive.read("tasks.json"))["tasks"]) >= 34
            assert json.loads(archive.read('tasks.json'))['settings']['custom_categories'][0]['key'] == custom
        click('[data-callback="cat:household"]')
        panel = page.locator('article').filter(has=page.locator('[data-callback="menu"]')).first
        screenshot_panel(panel, "simulator-desktop.png")
        for width in (320, 390, 768):
            print(f"Checking full button labels at {width}px", flush=True)
            page.set_viewport_size({"width": width, "height": 844})
            open_menu()
            for callback in ('cat:shopping', 'cat:household', 'cat:admin', 'cat:home', 'cat:study',
                             'cat:' + custom, 'showlist', 'category:add', 'view:all:trash:0'):
                click(f'[data-callback="{callback}"]')
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                labels = page.locator('button[data-callback]').all_text_contents()
                assert all(label and ord(label[0]) > 127 for label in labels), labels
                clipped = page.locator('.row button').evaluate_all("""buttons => buttons.filter(b =>
                    b.scrollWidth > b.clientWidth + 1 || b.textContent.includes('…') ||
                    b.textContent.includes('...')).map(b => b.textContent)""")
                assert not clipped, (width, clipped)
                assert page.get_by_role('button', name='Next ›', exact=True).count() == 0
                assert page.get_by_role('button', name='‹ Previous', exact=True).count() == 0
                if callback == 'cat:household':
                    assert page.locator('[data-callback^="task:"]').count() == 9
                if callback == 'showlist':
                    assert page.locator('[data-callback^="task:"]').count() == 0
                    for title in ('Milk', 'Long extension cord', 'Learn French'):
                        page.get_by_text(title, exact=False).first.wait_for()
                if callback.startswith('cat:'):
                    category = callback.split(':')[1]
                    for control in ('add:' + category, 'remove:' + category):
                        assert page.locator(f'[data-callback="{control}"]').count() == 1
                    bottom_rows = page.locator('article').filter(has=page.locator(f'[data-callback="remove:{category}"]')).locator('.row').evaluate_all(
                        'rows => rows.slice(-5).map(row => Array.from(row.querySelectorAll("button"), b => b.textContent))')
                    assert bottom_rows == [['➕ Add task'], ['➖ Remove task'], ['🗑️ Remove category'], ['✅ Done'], ['🏠 Main menu']], bottom_rows
                    click(f'[data-callback^="finish:{category}:"]')
                    click('[data-callback^="undo:"]')
                    click(f'[data-callback="remove:{category}"]')
                    click(f'[data-callback="cat:{category}"]')
                open_menu()
            menu = page.locator('article').filter(has=page.locator('[data-callback="cat:shopping"]')).first
            screenshot_panel(menu, f"simulator-mobile-menu-{width}.png")
            click('[data-callback="cat:household"]')
            task = page.locator('[data-callback^="task:complete:"]').first
            task_id = task.get_attribute('data-callback').split(':')[2]
            click(f'[data-callback^="task:complete:{task_id}:"]')
            green = page.locator(f'[data-callback^="task:reopen:{task_id}:"]')
            assert green.inner_text() == f'🟢 {task_id}'
            panel = page.locator('article').filter(has=page.locator(f'[data-callback^="task:reopen:{task_id}:"]')).first
            screenshot_panel(panel, f"simulator-mobile-tasks-{width}.png")
            click('[data-callback^="finish:household:"]')
            done_panel = page.locator('article').filter(has=page.locator('[data-callback^="undo:"]')).first
            assert done_panel.locator('button[data-callback]').all_text_contents() == ['↩️ Undo remove tasks', '🏠 Main menu']
            screenshot_panel(done_panel, f'simulator-done-{width}.png')
            click('[data-callback^="undo:"]')
            click(f'[data-callback^="task:reopen:{task_id}:"]')
            assert page.locator(f'[data-callback^="task:complete:{task_id}:"]').inner_text() == f'⚪ {task_id}'
            click('[data-callback="remove:household"]')
            click(f'[data-callback^="pick:{task_id}:"]')
            screenshot_panel(page.locator('article').filter(has=page.locator('[data-callback^="batch:remove:"]')).first,
                             f'simulator-remove-{width}.png')
            click('[data-callback^="batch:remove:"]')
            open_menu()
            click('[data-callback="view:all:trash:0"]')
            screenshot_panel(page.locator('article').filter(has=page.locator('[data-callback^="rpick:"]')).first,
                             f'simulator-trash-{width}.png')
            click(f'[data-callback^="rpick:{task_id}:"]')
            click('[data-callback^="batch:restore:"]')
            open_menu()
            click('[data-callback="category:add"]')
            page.locator('#replying').wait_for(state='visible')
            screenshot_panel(page.locator('#messages'), f'simulator-category-input-{width}.png')
            click('[data-callback^="cancel:"]')
            open_menu()
            click(f'[data-callback="cat:{custom}"]')
            click(f'[data-callback^="category:remove:{custom}"]')
            screenshot_panel(page.locator('article').filter(has=page.locator('[data-callback^="category:restore:"]')).first,
                             f'simulator-category-removed-{width}.png')
            click('[data-callback^="category:restore:"]')
            open_menu()
        open_menu()
        page.locator("#user").select_option("202")
        click('[data-callback="cat:study"]')
        click('[data-callback="add:study"]')
        page.locator("#replying").wait_for(state="visible")
        send("Partner browser task")
        page.get_by_text("Added 1 task", exact=False).first.wait_for()
        open_menu()
        assert page.evaluate("state.messages.filter(m=>m.from.is_bot&&!m.document&&m.chat.id===-100111).length") == 1
        assert not errors, errors
        (report / "browser-result.json").write_text(json.dumps({
            "passed": True, "page_errors": errors,
            "checks": ["desktop navigation", "two-line add", "check and uncheck without undo", "remove and undo",
                       "downloaded ZIP contents", "all menu labels at 320px, 390px and 768px",
                       "all categories at phone widths", "green circle reopens in place",
                       "partner add", "return to menu", "whole list only on request",
                       "all nine home shopping items without paging", "compact add confirmation",
                       "one bot message including add and errors", "only menu command", "backup only on selection",
                       "custom category creation and task addition", "category-specific Done and Remove",
                       "Add category first and Backup last; no Help or Done on main menu",
                       "custom categories included in downloaded backup", "category name opens typing automatically",
                       "full-width category actions and Main menu", "remove whole category and restore from Trash",
                       "task input opens automatic targeted reply", "Done Remove Trash category input and removal screenshots at three widths",
                       "select two tasks, unselect and reselect with confirmation count",
                       "multiselected Remove screenshot at 320px", "concise category instructions",
                       "select two trashed tasks without changing data until restore confirmation",
                       "bulk restore, Undo restore, and restore again",
                       "multiselected Restore screenshot at 320px"],
            "selection_database_assertions": bool(args.data_dir),
        }, indent=2), encoding="utf-8")
        browser.close()
    print(f"Browser acceptance passed; screenshots and backup are in {report}.")


if __name__ == "__main__":
    main()
