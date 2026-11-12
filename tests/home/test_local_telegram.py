"""End-to-end tests: real HTTP transport, polling loop, SQLite, and ZIP uploads."""
from __future__ import annotations

import copy
import contextlib
import html
import io
import json
from pathlib import Path
import random
import re
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from unittest import mock

from home_bot.bot import LOCAL_TEST_TOKEN, MENU_COMMANDS, Telegram, TelegramError
from home_bot.simulator import BotWorker, GROUP, OTHER_GROUP, OWNER, PARTNER, STRANGER, SimulatorServer, TelegramState
from home_bot.storage import Store


class HTTPScenario:
    def __init__(self, fixture="paired", seed=True):
        self.temp = tempfile.TemporaryDirectory(prefix="our-home-http-")
        self.folder = Path(self.temp.name)
        self.state = TelegramState()
        self.server = SimulatorServer(self.state)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.worker = BotWorker(self.folder / "bot", self.server, fixture=fixture, seed=seed)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            self.worker.start()
            self.wait(lambda: self.state.commands == MENU_COMMANDS and
                      (fixture != "paired" or self.meta("ui_dirty") is False))
        except Exception:
            self.close()
            raise

    def close(self):
        self.worker.stop()
        self.state.closing = True
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        self.temp.cleanup()

    def wait(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.worker.error:
                raise AssertionError(f"Bot crashed: {self.worker.error}")
            if predicate():
                return
            time.sleep(0.015)
        raise AssertionError("Timed out waiting for local bot")

    def query(self, sql, args=()):
        with contextlib.closing(sqlite3.connect(self.worker.folder / "tasks.sqlite3")) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, args)]

    def meta(self, key):
        rows = self.query("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else None

    def tasks(self):
        return self.query("SELECT * FROM tasks ORDER BY id")

    def post(self, route, payload):
        request = urllib.request.Request(self.server.url + route,
            data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)["result"]

    def processed(self, update):
        if update:
            self.wait(lambda: (self.meta("last_update_id") or -1) >= update["update_id"])
        # Functional assertions observe settled network deletions. Latency
        # benchmarks override this helper and time the raw offset/visible panel.
        def cleanup_settled():
            jobs = dict(getattr(self.worker.bot, 'cleanup_jobs', {}))
            if any(not job.done() for job in jobs.values()):
                return False
            queued = self.meta('prompt_deletions') or []
            return all((entry['chat_id'], entry['message_id']) not in self.state.messages
                       or (entry['chat_id'], entry['message_id']) in jobs
                       or entry.get('retry_at', 0) > time.time() for entry in queued)
        self.wait(cleanup_settled)
        return update

    def send(self, text, uid=OWNER, chat=GROUP, reply=None):
        return self.processed(self.post("/sim/message", {
            "text": text, "uid": uid, "chat_id": chat, "reply_id": reply}))

    def panel(self):
        with self.state.condition:
            return copy.deepcopy(self.state.messages[(GROUP, self.meta("list_id"))])

    def buttons(self):
        return [b for row in self.panel().get("reply_markup", {}).get("inline_keyboard", []) for b in row]

    def click(self, prefix, uid=OWNER, *, exact=False, message=None, chat=GROUP):
        panel = message or self.panel()
        buttons = [b for row in panel.get("reply_markup", {}).get("inline_keyboard", []) for b in row]
        button = next(b for b in buttons if b["callback_data"] == prefix or
                      (not exact and b["callback_data"].startswith(prefix)))
        if message is not None and (chat, panel['message_id']) not in self.state.messages:
            # A click queued before replacement can arrive after the old menu
            # was deleted. Deliver its captured Telegram message through polling.
            with self.state.condition:
                update = self.state.enqueue({'callback_query': {
                    'id': f'local-{self.state.next_update}', 'from': {'id': uid},
                    'message': panel, 'data': button['callback_data']}})
            return self.processed(update)
        return self.processed(self.post("/sim/click", {
            "uid": uid, "chat_id": chat, "message_id": panel["message_id"], "data": button["callback_data"]}))

    def bot_messages(self, chat=GROUP):
        return [m for m in self.state.snapshot()["messages"] if m["chat"]["id"] == chat and m["from"]["is_bot"]]

    def private_panel(self, uid):
        with self.state.condition:
            return copy.deepcopy(self.state.messages[(uid, self.meta(f"private_panel_{uid}"))])

    def wait_backups(self):
        self.wait(lambda: not self.worker.bot.backup_jobs)

    def restart(self):
        self.worker.stop()
        self.worker = BotWorker(self.folder / "bot", self.server)
        self.worker.start()
        self.wait(lambda: self.meta("ui_dirty") is False)


class LocalTelegramTests(unittest.TestCase):
    def setUp(self):
        self.s = HTTPScenario()

    def tearDown(self):
        self.s.close()

    def test_start_changes_and_restart_never_generate_backup(self):
        s = self.s
        self.assertEqual(len(s.bot_messages()), 1)
        self.assertEqual(s.state.commands, [{"command": "menu", "description": "Open the main menu"}])
        s.click("cat:shopping")
        s.click("task:complete:")
        s.send("/backup")  # Retired command only opens the menu.
        s.restart()
        self.assertEqual(s.state.files, {})
        self.assertEqual(list(s.worker.folder.glob("backups/*.zip")), [])
        self.assertEqual(len(s.bot_messages()), 1)
        s.click("backup")
        s.wait_backups()
        self.assertEqual(len(s.state.files), 1)

    def test_stale_menu_choices_do_not_overwrite_current_operation(self):
        s = self.s
        choices = ("cat:shopping", "cat:home", "cat:household", "showlist", "cat:admin",
                   "cat:study", "view:all:trash:0", "category:add")
        for chosen in choices:
            for stale in choices:
                if chosen == stale:
                    continue
                s.send("/menu")
                menu = s.panel()
                s.click(chosen, exact=True)
                current = s.panel()
                s.click(stale, message=menu, exact=True)
                self.assertEqual(s.panel(), current, (chosen, stale))
        self.assertEqual(len(s.tasks()), 32)
        s.send('/menu')  # Close the final temporary category-name prompt.
        self.assertEqual(len(s.bot_messages()), 1)
        self.assertEqual(s.state.files, {})

    def test_two_users_share_one_message_without_crossing_add_categories(self):
        s = self.s
        s.click("cat:home")
        s.click("add:home")
        mid = s.meta(f'pending_{OWNER}')['prompt_id']
        s.send("/menu", PARTNER)
        s.click("cat:study", PARTNER)
        s.click("add:study", PARTNER)
        partner_screen = s.panel()
        s.send("Owner's home task", OWNER, reply=mid)
        self.assertEqual(s.panel(), partner_screen)
        s.send("Partner's study task", PARTNER, reply=s.meta(f'pending_{PARTNER}')['prompt_id'])
        self.assertEqual([(t["category"], t["title"]) for t in s.tasks()[32:]],
                         [("home", "Owner's home task"), ("study", "Partner's study task")])
        self.assertEqual(len(s.bot_messages()), 1)
        s.send("/menu")
        s.click("cat:shopping")
        s.click("add:shopping")
        s.click("menu")
        s.click("showlist")
        help_screen = s.panel()
        s.send("Cancelled add", reply=mid)
        self.assertEqual(s.panel(), help_screen)
        self.assertEqual(len(s.tasks()), 34)

    def test_late_backup_failure_does_not_replace_a_new_menu_visit(self):
        from home_bot.bot import NetworkError
        s = self.s
        entered, release = threading.Event(), threading.Event()
        original = s.worker.bot.api.call
        def fail_later(method, *args, **kwargs):
            if method == "sendDocument":
                entered.set()
                release.wait(8)
                raise NetworkError("Simulated lost upload response")
            return original(method, *args, **kwargs)
        try:
            with mock.patch.object(s.worker.bot.api, "call", side_effect=fail_later):
                s.click("backup")
                self.assertTrue(entered.wait(3))
                s.click("showlist")
                s.click("menu")
                current = s.panel()
                release.set()
                s.wait_backups()
                self.assertEqual(s.panel(), current)
        finally:
            release.set()

    def test_every_main_menu_button_and_each_category(self):
        s = self.s
        for category, size in (("shopping", 7), ("household", 9), ("admin", 7), ("home", 7), ("study", 2)):
            s.send("/menu@local_home_bot")
            s.click("cat:" + category, exact=True)
            self.assertIn(f"All: {size}", s.panel()["text"])
            self.assertEqual(len([b for b in s.buttons() if b["callback_data"].startswith("task:")]), size)
            self.assertTrue(any(b["callback_data"].startswith(f"finish:{category}:") for b in s.buttons()))
            s.click("menu", exact=True)
        for prefix in ("showlist", "view:all:trash:0", "category:add"):
            s.click(prefix, exact=True)
            s.click("menu", exact=True)
        s.click("backup")
        s.wait_backups()
        self.assertTrue(s.state.files)
        self.assertEqual(len(s.tasks()), 32)

    def test_whole_list_is_explicit_and_add_does_not_publish_it(self):
        s = self.s
        original_id = s.panel()["message_id"]
        self.assertIsNone(s.meta("last_snapshot_revision"))
        s.click("showlist")
        for task in s.tasks():
            self.assertIn(task["title"], html.unescape(s.panel()["text"]))
        self.assertEqual([b["callback_data"] for b in s.buttons()], ["menu"])
        self.assertNotIn("Page ", s.panel()["text"])
        old_revision = s.meta("last_snapshot_revision")
        s.click("menu")
        s.click("cat:shopping")
        before = len(s.state.calls)
        s.click("task:complete:")
        edits = [c for c in s.state.calls[before:] if c["method"] == "editMessageText"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["payload"]["message_id"], original_id)
        self.assertNotIn("Updated list", edits[0]["payload"]["text"])
        self.assertEqual(s.meta("last_snapshot_revision"), old_revision)
        s.click("add:shopping")
        prompt = s.meta(f"pending_{OWNER}")["prompt_id"]
        before = len(s.bot_messages())
        s.send("New quiet task", reply=prompt)
        self.assertIn("Added 1 task", s.panel()["text"])
        self.assertNotIn("Milk", s.panel()["text"])
        self.assertEqual(len(s.bot_messages()), before - 1)
        self.assertEqual(s.meta("last_snapshot_revision"), old_revision)
        s.click("menu")
        s.click("showlist")
        self.assertIn("New quiet task", s.panel()["text"])
        self.assertEqual(s.panel()["message_id"], original_id)

    def test_slow_backup_does_not_block_actions_or_duplicate_uploads(self):
        s = self.s
        s.wait_backups()
        entered, release = threading.Event(), threading.Event()
        original = s.worker.bot.api.call
        def delayed_upload(method, *args, **kwargs):
            if method == "sendDocument":
                entered.set()
                if not release.wait(8):
                    raise AssertionError("Test upload was not released")
            return original(method, *args, **kwargs)
        previous_files = len(s.state.files)
        try:
            with mock.patch.object(s.worker.bot.api, "call", side_effect=delayed_upload):
                s.send("/menu")
                s.click("backup")
                self.assertTrue(entered.wait(3))
                s.click("backup", uid=PARTNER)
                start = time.perf_counter()
                s.click("cat:shopping")
                s.click("task:complete:", uid=PARTNER)
                self.assertLess(time.perf_counter() - start, 2)
                self.assertTrue(s.tasks()[0]["done"])
                self.assertEqual(len(s.state.files), previous_files)
                release.set()
                s.wait_backups()
        finally:
            release.set()
        self.assertEqual(len(s.state.files), previous_files + 1)
        _, uploaded = list(s.state.files.values())[-1]
        with zipfile.ZipFile(io.BytesIO(uploaded)) as archive:
            saved = json.loads(archive.read("tasks.json"))
            self.assertFalse(saved["tasks"][0]["done"])
            self.assertEqual(len(saved["tasks"]), 32)

    def test_add_in_every_category_invalid_input_and_cancel(self):
        s = self.s
        for index, category in enumerate(("shopping", "household", "admin", "home", "study")):
            s.send("/menu")
            s.click("cat:" + category, exact=True)
            s.click("add:" + category, exact=True)
            prompt = s.meta(f"pending_{OWNER}")["prompt_id"]
            s.send("x" * 241, reply=prompt)
            self.assertIsNotNone(s.meta(f"pending_{OWNER}"))
            s.send(f"HTTP item {index}\nPersian فارسی <&> {index}", reply=s.meta(f'pending_{OWNER}')['prompt_id'])
            self.assertIsNone(s.meta(f"pending_{OWNER}"))
            self.assertEqual(len(s.query("SELECT * FROM tasks WHERE category=?", (category,))),
                             {"shopping": 9, "household": 11, "admin": 9, "home": 9, "study": 4}[category])
        s.send("/menu")
        s.click("cat:home")
        s.click("add:home")
        prompt = s.meta(f"pending_{OWNER}")["prompt_id"]
        s.click("cancel:")
        s.send("Cancelled task", reply=prompt)
        self.assertEqual(len(s.tasks()), 42)

    def test_check_uncheck_delete_restore_and_all_undo_actions(self):
        s = self.s
        s.click("cat:shopping")
        for action, inverse in (("complete", "reopen"), ("reopen", "complete"),
                                ("delete", "restore"), ("restore", "delete")):
            if action == "reopen":
                s.click("task:complete:")
                s.send("/menu")
                s.click("cat:shopping")
            elif action == "delete":
                s.send("/menu")
                s.click("cat:shopping")
                s.click("remove:shopping")
            elif action == "restore":
                s.send("/menu")
                s.click("cat:shopping")
                s.click("remove:shopping")
                s.click("pick:")
                s.click("batch:remove:")
                s.send("/menu")
                s.click("view:all:trash:0")
            before = {t["id"]: t for t in s.tasks()}
            if action in ('delete', 'restore'):
                prefix = 'pick:' if action == 'delete' else 'rpick:'
                selected = next(b['callback_data'] for b in s.buttons() if b['callback_data'].startswith(prefix))
                task_id = int(selected.split(':')[1])
                s.click(selected, exact=True)
                self.assertEqual({t['id']: t for t in s.tasks()}, before)
                s.click('batch:remove:' if action == 'delete' else 'batch:restore:')
            else:
                s.click("task:" + action + ":")
            receipt = s.meta("undo_" + s.meta("last_action"))
            self.assertEqual(receipt["inverse"], inverse)
            if action in ('delete', 'restore'):
                self.assertEqual(receipt['tasks'], {str(task_id): before[task_id]['revision'] + 1})
            else:
                task_id = receipt['task_id']
            if action in ("complete", "reopen"):
                self.assertFalse(any(b['callback_data'].startswith('undo:') for b in s.buttons()))
                s.click(f"task:{inverse}:{receipt['task_id']}:", uid=PARTNER)
            else:
                s.click("undo:", uid=PARTNER)
            after = {t["id"]: t for t in s.tasks()}
            self.assertEqual((after[task_id]["done"], after[task_id]["deleted"]),
                             (before[task_id]["done"], before[task_id]["deleted"]))

    def test_one_message_list_and_two_users_changing_same_task(self):
        s = self.s
        s.click("cat:household")
        self.assertNotIn("Page ", s.panel()["text"])
        self.assertEqual(len([b for b in s.buttons() if b["callback_data"].startswith("task:")]), 9)
        old = s.panel()
        action = next(b["callback_data"] for b in s.buttons() if b["callback_data"].startswith("task:complete:"))
        updates = [s.post("/sim/click", {"uid": uid, "chat_id": GROUP,
                    "message_id": old["message_id"], "data": action}) for uid in (OWNER, PARTNER)]
        s.processed(updates[-1])
        task = next(t for t in s.tasks() if t["id"] == int(action.split(":")[2]))
        self.assertEqual(task["revision"], 2)
        self.assertEqual(task["done"], 1)

    def test_same_circle_checks_and_unchecks_without_hiding_or_moving_task(self):
        s = self.s
        s.click("cat:household")
        initial = s.panel()
        control = next(b for b in s.buttons() if b["callback_data"].startswith("task:complete:"))
        task_id = int(control["callback_data"].split(":")[2])
        title = next(t["title"] for t in s.tasks() if t["id"] == task_id)
        count = len(s.bot_messages())
        for action, mark in (("complete", "🟢"), ("reopen", "⚪")):
            s.click(f"task:{action}:{task_id}:", uid=PARTNER)
            self.assertIn(title, s.panel()["text"])
            self.assertNotIn("Page ", s.panel()["text"])
            self.assertEqual(s.panel()["message_id"], initial["message_id"])
            self.assertTrue(any(b["text"] == f"{mark} {task_id}" for b in s.buttons()))
            self.assertEqual(len(s.bot_messages()), count)

    def test_concurrent_add_prompts_and_privacy(self):
        s = self.s
        s.send("/menu", OWNER)
        s.click("cat:home", OWNER)
        s.click("add:home", OWNER)
        owner_prompt = s.meta(f"pending_{OWNER}")["prompt_id"]
        s.send("/menu", PARTNER)
        s.click("cat:study", PARTNER)
        s.click("add:study", PARTNER)
        partner_prompt = s.meta(f"pending_{PARTNER}")["prompt_id"]
        self.assertIsNone(s.send("Regular group conversation"))
        s.send("Owner's home item", OWNER, reply=owner_prompt)
        s.send("Partner's study item", PARTNER, reply=partner_prompt)
        self.assertEqual([(t["category"], t["title"]) for t in s.tasks()[32:]],
                         [("home", "Owner's home item"), ("study", "Partner's study item")])

    def test_private_and_unrelated_chat_authorization(self):
        s = self.s
        before = s.tasks()
        for command in ("/menu", "/add", "/list", "/done", "/trash", "/backup", "/members", "/invite", "/setup"):
            s.send(command, STRANGER, STRANGER)
            s.send(command, OWNER, OTHER_GROUP)
        s.send("/start", OWNER, OWNER)
        private = s.private_panel(OWNER)
        for callback in ("members", "menu", "backup", "invite"):
            # Use the matching rendered private-menu message, never a fabricated callback.
            s.send("/menu", OWNER, OWNER)
            private = s.private_panel(OWNER)
            if callback == "menu":
                s.click("members", message=private, chat=OWNER)
                private = s.private_panel(OWNER)
            s.click(callback, message=private, chat=OWNER)
        self.assertEqual(s.tasks(), before)
        self.assertFalse(any(m.get("document") for m in s.bot_messages(STRANGER)))

    def test_backup_upload_download_and_restore(self):
        s = self.s
        s.click("cat:shopping")
        s.click("task:complete:")
        expected = s.tasks()
        previous_files = len(s.state.files)
        s.send("/menu")
        s.click("backup")
        s.wait_backups()
        self.assertEqual(len(s.state.files), previous_files + 1)
        document = next(m["document"] for m in reversed(s.bot_messages()) if m.get("document"))
        with s.opener.open(s.server.url + "/sim/download/" + document["file_id"]) as response:
            data = response.read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), {"tasks.sqlite3", "tasks.json", "tasks.txt", "tasks.html"})
            tasks = json.loads(archive.read("tasks.json"))["tasks"]
            self.assertEqual([t["title"] for t in tasks], [t["title"] for t in expected])
            self.assertNotIn(LOCAL_TEST_TOKEN.encode(), b"".join(archive.read(n) for n in archive.namelist()))
        s.worker.stop()
        backup = s.folder / "downloaded.zip"
        backup.write_bytes(data)
        store = Store(s.worker.folder)
        store.add_task("home", "Remove on restore", OWNER)
        store.restore_backup(backup)
        restored = store.list_tasks(mode="all")
        self.assertEqual([t["title"] for t in restored], [t["title"] for t in expected])
        self.assertTrue(restored[0]["done"])
        store.close()
        s.worker = BotWorker(s.folder / "bot", s.server)
        s.worker.start()
        s.wait(lambda: s.meta("ui_dirty") is False)
        s.send("/menu")
        self.assertEqual(len(s.tasks()), 32)

    def test_duplicate_updates_and_transient_edit_failures(self):
        s = self.s
        s.click("cat:shopping")
        for failure in ("500", "429", "disconnect_before", "disconnect_after"):
            with self.subTest(failure=failure):
                before_messages = len(s.bot_messages())
                s.state.fail_next("editMessageText", failure)
                update = s.click("task:complete:")
                s.state.replay(update)
                s.send("/members")  # Barrier after duplicate delivery.
                task_id = int(update["callback_query"]["data"].split(":")[2])
                task = next(t for t in s.tasks() if t["id"] == task_id)
                self.assertEqual(task["revision"], 2)
                self.assertEqual(len(s.bot_messages()), before_messages)
                s.send("/menu")
                s.click("cat:shopping")

    def test_bulk_add_limits_repeated_clicks_and_restart(self):
        s = self.s
        s.send("/menu")
        s.click("cat:home")
        choice = s.panel()
        s.click("add:home")
        first = s.meta(f"pending_{OWNER}")
        s.click("add:home", message=choice)
        self.assertEqual(s.meta(f"pending_{OWNER}"), first)
        s.send("\n".join(f"too many {n}" for n in range(21)), reply=first["prompt_id"])
        self.assertEqual(len(s.tasks()), 32)
        s.send("\n".join(f"bulk {n}" for n in range(20)), reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.assertEqual(len(s.tasks()), 52)
        expected = s.tasks()
        s.restart()
        s.send("/menu")
        s.click("showlist")
        self.assertEqual(s.tasks(), expected)

    def test_seeded_button_walk_uses_real_rendered_controls(self):
        s = self.s
        rng = random.Random(20261109)
        visited = set()
        for _ in range(80):
            buttons = [b for b in s.buttons() if b["callback_data"] not in {"backup", "help"}
                       and not b["callback_data"].startswith("add:")]
            chosen = rng.choice(buttons)
            callback = chosen["callback_data"]
            visited.add(callback.split(":")[0])
            s.click(callback, rng.choice((OWNER, PARTNER)), exact=True)
            self.assertEqual(len(s.tasks()), 32)
            self.assertEqual(len({t["id"] for t in s.tasks()}), 32)
        self.assertTrue({"cat", "view", "task", "menu"} <= visited)

    def test_repeated_navigation_commands_do_not_duplicate_bot_messages(self):
        s = self.s
        before = len(s.bot_messages())
        for command in ("/menu", "/list", "/done", "/trash", "/help", "/add", "/members", "/setup", "/unknown"):
            s.send(command)
            s.send(command)
        self.assertEqual(len(s.bot_messages()), before)

    def test_lost_add_screen_edit_retries_without_extra_messages(self):
        s = self.s
        s.click("cat:home")
        before = len(s.bot_messages())
        s.state.fail_next("editMessageText", "disconnect_after")
        s.click("add:home")
        self.assertEqual(len(s.bot_messages()), before + 1)
        prompt = s.meta(f'pending_{OWNER}')['prompt_id']
        self.assertNotEqual(prompt, s.panel()['message_id'])
        s.send("Reply to the targeted prompt", reply=prompt)
        self.assertEqual(len(s.tasks()), 33)
        self.assertEqual(len(s.bot_messages()), before)

    def test_add_screen_not_delivered_retries_same_message(self):
        s = self.s
        s.click("cat:shopping")
        before = len(s.bot_messages())
        s.state.fail_next("editMessageText", "disconnect_before")
        s.click("add:shopping")
        self.assertEqual(s.meta("screen"), "input")
        self.assertEqual(len(s.bot_messages()), before + 1)
        s.click("cancel:", uid=PARTNER)
        self.assertIsNotNone(s.meta(f"pending_{OWNER}"))
        s.send("Recovered add", reply=s.meta(f'pending_{OWNER}')['prompt_id'])
        self.assertEqual(len(s.tasks()), 33)

    def test_uncertain_backup_upload_does_not_duplicate_the_file(self):
        s = self.s
        before = len(s.state.files)
        s.state.fail_next("sendDocument", "disconnect_after")
        s.send("/menu")
        s.click("backup")
        s.wait_backups()
        self.assertEqual(len(s.state.files), before + 1)
        self.assertIn("unconfirmed", s.panel()["text"])
        self.assertEqual(s.meta("cloud_backup_uncertain_revision"), s.meta("_data_revision"))
        s.click("menu")
        s.click("backup")
        s.wait_backups()
        self.assertIsNone(s.meta("cloud_backup_uncertain_revision"))
        self.assertEqual(len(list((s.worker.folder / "backups").glob("*.zip"))), 1)


class LocalSetupTests(unittest.TestCase):
    def test_repeated_invite_keeps_one_working_link(self):
        s = HTTPScenario("owner", seed=False)
        try:
            s.send("/menu", OWNER, OWNER)
            s.click("invite", message=s.private_panel(OWNER), chat=OWNER)
            claim = s.meta("join_claim")
            count = len(s.bot_messages(OWNER))
            s.send("/menu", OWNER, OWNER)
            s.send("/menu", OWNER, OWNER)
            s.click("invite", message=s.private_panel(OWNER), chat=OWNER)
            self.assertEqual(s.meta("join_claim"), claim)
            self.assertEqual(len(s.bot_messages(OWNER)), count)
            s.send("/start " + claim["code"], PARTNER, PARTNER)
            self.assertEqual(len(s.query("SELECT * FROM members")), 2)
        finally:
            s.close()

    def test_pairing_setup_invitation_and_owner_permissions_over_http(self):
        s = HTTPScenario("unpaired", seed=False)
        try:
            s.send("/start owner_local_test", OWNER, OWNER)
            invite = next(m["text"] for m in s.bot_messages(OWNER) if "?start=join_" in m.get("text", ""))
            code = re.search(r"start=(join_[A-Za-z0-9_-]+)", invite).group(1)
            s.send("/start " + code, PARTNER, PARTNER)
            s.send("/setup", PARTNER)
            self.assertIsNone(s.meta("chat_id"))
            s.send("/setup@local_home_bot")
            self.assertEqual(s.meta("chat_id"), GROUP)
            s.send("/start " + code, STRANGER, STRANGER)
            self.assertEqual(len(s.query("SELECT * FROM members")), 2)
            s.send("/start")
            s.send("/unknown")
            s.send("/help")
            s.send("/cancel")
            s.send("/members")
            self.assertFalse(s.worker.error)
        finally:
            s.close()

    def test_local_transport_rejects_real_token_or_nonloopback_address(self):
        for token, base in (("123:real-token", "http://127.0.0.1:8877"),
                            (LOCAL_TEST_TOKEN, "http://example.com:8877"),
                            (LOCAL_TEST_TOKEN, "http://127.0.0.1:8877/evil")):
            with self.assertRaises(ValueError):
                Telegram(token, api_base=base)
