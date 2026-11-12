"""Offline integration checks for Telegram authorization and task interaction."""
from __future__ import annotations

import copy
import html
import http.client
import io
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest import mock


from home_bot.bot import Bot, NetworkError, Telegram, TelegramError, digest, split_text, startup_call
from home_bot.storage import Store


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.message_id = 100
        self.fail_once = {}
        self.messages = {}

    def call(self, method, payload=None, **kwargs):
        self.calls.append((method, copy.deepcopy(payload or {}), kwargs))
        failure = self.fail_once.pop(method, None)
        if failure is not None:
            raise failure
        if method == "getWebhookInfo":
            return {"url": ""}
        if method in {"sendMessage", "sendDocument"}:
            self.message_id += 1
            self.messages[(payload["chat_id"], self.message_id)] = copy.deepcopy(payload)
            return {"message_id": self.message_id}
        if method in {"editMessageText", "editMessageReplyMarkup"}:
            key = (payload["chat_id"], payload["message_id"])
            self.messages.setdefault(key, {}).update(copy.deepcopy(payload))
            return {"message_id": payload["message_id"]}
        if method == 'deleteMessage':
            self.messages.pop((payload['chat_id'], payload['message_id']), None)
        return True

    def send(self, chat_id, text, **kwargs):
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, **kwargs})

    def last(self, method):
        return next(payload for name, payload, _ in reversed(self.calls) if name == method)


class BotTests(unittest.TestCase):
    OWNER, MEMBER, STRANGER, GROUP, OTHER_GROUP = 101, 202, 303, -100111, -100222

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.store = Store(self.directory)
        self.api = FakeTelegram()
        self.bot = Bot(self.store, self.api, "testbot", self.directory)
        self.sequence = 0
        self.store.add_member(self.OWNER, "Owner", "owner")
        self.store.add_member(self.MEMBER, "Partner")
        self.store.set("chat_id", self.GROUP)
        self.milk = self.store.add_task("shopping", "Milk", self.OWNER)
        self.mirror = self.store.add_task("home", "Mirror <&> with every detail visible", self.OWNER)
        self.french = self.store.add_task("study", "Learn French", self.OWNER)
        self.bot.sync()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def panel(self, chat=None):
        chat = self.GROUP if chat is None else chat
        key = "list_id" if chat == self.GROUP else f"private_panel_{chat}"
        return self.api.messages[(chat, self.store.get(key))]

    def buttons(self, chat=None):
        return [b for row in self.panel(chat)["reply_markup"]["inline_keyboard"] for b in row]

    def click(self, value, uid=OWNER, chat=None):
        chat = self.GROUP if chat is None else chat
        selected = next(b for b in self.buttons(chat) if b["callback_data"] == value
                        or b["callback_data"].startswith(value))
        self.sequence += 1
        key = "list_id" if chat == self.GROUP else f"private_panel_{chat}"
        update = {"update_id": self.sequence, "callback_query": {
            "id": f"query-{self.sequence}", "from": {"id": uid},
            "message": {"message_id": self.store.get(key),
                        "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup"}},
            "data": selected["callback_data"]}}
        self.bot.handle(update)
        return update

    def send(self, text, uid=OWNER, chat=None, reply=None):
        self.sequence += 1
        chat = self.GROUP if chat is None else chat
        message = {"message_id": 1000 + self.sequence, "text": text,
                   "from": {"id": uid, "first_name": f"User {uid}"},
                   "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup"}}
        if reply is not None:
            message["reply_to_message"] = {"message_id": reply}
        update = {"update_id": self.sequence, "message": message}
        self.bot.handle(update)
        return update

    def add(self, category="shopping", uid=OWNER):
        self.send("/menu", uid)
        self.click(f"cat:{category}", uid)
        self.click(f"add:{category}", uid)
        return self.store.get(f"pending_{uid}")["prompt_id"]

    def menu(self):
        self.click("menu")

    def test_only_menu_command_registered_and_registration_retries(self):
        self.api.fail_once["setMyCommands"] = NetworkError("offline")
        with mock.patch("home_bot.bot.log.warning"):
            self.bot.maintenance()
        self.assertFalse(self.bot.menu_registered)
        self.bot.next_maintenance = 0
        self.bot.maintenance()
        self.assertEqual(self.api.last("setMyCommands")["commands"],
                         [{"command": "menu", "description": "Open the main menu"}])
        self.assertEqual(self.api.last("setChatMenuButton")["menu_button"], {"type": "commands"})

    def test_main_menu_and_every_category_keep_the_same_message(self):
        mid = self.store.get("list_id")
        values = [b["callback_data"] for b in self.buttons()]
        self.assertEqual(values[:2], ["category:add", "cat:shopping"])
        self.assertNotIn("add:choose", values)
        self.assertEqual(self.buttons()[-1]["callback_data"], "backup")
        for choice in ("cat:shopping", "cat:household", "cat:admin", "cat:home", "cat:study",
                       "showlist", "view:all:trash:0", "category:add"):
            self.click(choice)
            self.assertEqual(self.store.get("list_id"), mid)
            self.menu()
        self.assertEqual(sum(m == 'sendMessage' and not p.get('reply_markup', {}).get('force_reply')
                             for m, p, _ in self.api.calls), 1)
        self.assertEqual(len(self.api.messages), 1)

    def test_member_menu_is_visible_after_hidden_history_boundary(self):
        hidden_through = self.api.message_id
        before = self.store.list_tasks(mode="all")
        self.send("/menu@testbot", self.MEMBER)
        current = self.store.get("list_id")
        visible = [m for (chat, mid), m in self.api.messages.items()
                   if chat == self.GROUP and mid > hidden_through]
        self.assertEqual(len(visible), 1)
        self.assertIn("cat:home", [b["callback_data"] for row in
                      visible[0]["reply_markup"]["inline_keyboard"] for b in row])
        self.assertEqual(self.store.list_tasks(mode="all"), before)
        self.click("cat:home", self.MEMBER)
        self.click("task:complete:", self.MEMBER)
        self.assertTrue(self.store.task(self.mirror)["done"])
        self.assertEqual(self.store.get("list_id"), current)
        self.click("menu", self.MEMBER)
        self.assertEqual(self.store.get("list_id"), current)
        self.assertEqual(len(self.api.messages), 1)

    def test_uncertain_menu_replacement_recovers_new_message_only(self):
        old_id = self.store.get("list_id")
        old = copy.deepcopy(self.panel())
        send = self.api.send

        def delivered_then_lost(*args, **kwargs):
            send(*args, **kwargs)
            raise NetworkError("replacement response lost")

        with mock.patch.object(self.api, "send", side_effect=delivered_then_lost):
            with self.assertRaises(NetworkError):
                self.send("/menu", self.MEMBER)
        delivered_id = self.api.message_id
        delivered = copy.deepcopy(self.api.messages[(self.GROUP, delivered_id)])
        for mid, panel in ((old_id, old), (delivered_id, delivered)):
            panel.update(message_id=mid, chat={"id": self.GROUP},
                         **{"from": {"is_bot": True, "username": "testbot"}})
        self.bot.recover_panel(old)
        self.assertIsNone(self.store.get("list_id"))
        self.assertIn((self.GROUP, old_id), self.api.messages)
        self.send("/menu", self.MEMBER)
        self.assertEqual(self.api.message_id, delivered_id)
        self.bot.recover_panel(delivered)
        self.assertEqual(self.store.get("list_id"), delivered_id)
        self.assertIsNone(self.store.get("panel_send_list_id"))
        self.assertNotIn((self.GROUP, old_id), self.api.messages)
        self.click("cat:home", self.MEMBER)
        self.assertEqual(len(self.api.messages), 1)

    def test_full_titles_colored_toggle_stay_visible_without_check_undo(self):
        self.click("cat:home")
        before = self.panel()["text"]
        self.click("task:complete:")
        self.assertIn("🟢", self.panel()["text"])
        self.assertIn("Mirror &lt;&amp;&gt; with every detail visible", self.panel()["text"])
        self.click("task:reopen:", self.MEMBER)
        self.assertFalse(self.store.task(self.mirror)["done"])
        self.assertFalse(any(b['callback_data'].startswith('undo:') for b in self.buttons()))
        self.click("task:complete:")
        self.assertTrue(self.store.task(self.mirror)["done"])
        self.assertEqual(sum(m == "sendMessage" for m, _, _ in self.api.calls), 1)

    def test_task_revision_guards_double_delivery(self):
        self.click("cat:shopping")
        update = self.click("task:complete:")
        self.bot.handle(update)
        self.assertEqual(self.store.task(self.milk)["revision"], 2)
        self.assertTrue(self.store.task(self.milk)["done"])

    def test_old_category_button_cannot_change_another_screen(self):
        self.click("cat:shopping")
        checked = self.click("task:complete:")
        self.menu()
        self.click("cat:home")
        before = copy.deepcopy(self.panel())
        self.bot.handle(checked)
        self.assertEqual(self.panel(), before)
        self.assertNotIn("undo:", str(self.buttons()))

    def test_old_remove_button_is_rejected_after_returning_to_menu(self):
        self.click("cat:shopping")
        self.click("remove:shopping")
        self.click("pick:")
        row = next(b for b in self.buttons() if b["callback_data"].startswith("batch:remove:"))
        self.menu()
        query = {"id": "old-delete", "from": {"id": self.OWNER},
                 "message": {"message_id": self.store.get("list_id"), "chat": {"id": self.GROUP}},
                 "data": row["callback_data"]}
        self.bot.handle({"callback_query": query})
        self.assertEqual(self.store.list_tasks(mode="trash"), [])
        self.assertEqual(self.store.get("screen"), "menu")

    def test_remove_restore_and_undo_keep_one_message(self):
        self.click("cat:shopping")
        self.click("remove:shopping")
        self.click("pick:")
        self.assertFalse(self.store.task(self.milk)["deleted"])
        self.click("batch:remove:")
        self.assertTrue(self.store.task(self.milk)["deleted"])
        self.click("undo:")
        self.assertFalse(self.store.task(self.milk)["deleted"])
        self.menu()
        self.click("cat:shopping")
        self.click("remove:shopping")
        self.click("pick:")
        self.click("batch:remove:")
        self.menu()
        self.click("view:all:trash:0")
        self.click("rpick:")
        self.assertTrue(self.store.task(self.milk)["deleted"])
        self.click("batch:restore:")
        self.click("undo:")
        self.assertTrue(self.store.task(self.milk)["deleted"])
        self.assertEqual(sum(m == "sendMessage" for m, _, _ in self.api.calls), 1)

    def test_add_instructions_use_one_panel_and_temporary_targeted_prompt(self):
        mid = self.store.get("list_id")
        reply = self.add()
        self.assertNotEqual(reply, mid)
        self.assertIn("Groceries — Add task", self.panel()["text"])
        self.assertNotIn("Telegram opens the reply field", self.panel()["text"])
        self.send("Bananas\nPersian فارسی <&>", reply=reply)
        self.assertIn("Added 2 tasks", self.panel()["text"])
        self.assertIsNone(self.store.get("pending_101"))
        self.assertEqual(len(self.store.list_tasks(mode="all")), 5)
        self.assertEqual(len(self.api.messages), 1)

    def test_invalid_input_reuses_panel_and_keeps_request(self):
        reply = self.add()
        for value in ("x" * 241, "\n".join("a" for _ in range(21)), "a\x00b", " "):
            self.send(value, reply=reply)
            self.assertIsNotNone(self.store.get("pending_101"))
            self.assertIn("Use 1-20", self.panel()["text"])
            reply = self.store.get('pending_101')['prompt_id']
        self.send("Valid item", reply=reply)
        self.assertEqual(len(self.store.list_tasks(mode="all")), 4)
        self.assertEqual(len(self.api.messages), 1)

    def test_leaving_add_cancels_only_that_users_pending_request(self):
        reply = self.add()
        self.menu()
        self.click("cat:home")
        before = copy.deepcopy(self.panel())
        self.send("Should not be added", reply=reply)
        self.assertEqual(self.panel(), before)
        self.assertEqual(len(self.store.list_tasks(mode="all")), 3)

    def test_partner_reply_saves_correct_category_without_taking_over_panel(self):
        owner_reply = self.add("home")
        partner_reply = self.add("study", self.MEMBER)
        self.assertNotEqual(owner_reply, partner_reply)
        partner_panel = copy.deepcopy(self.panel())
        self.send("Owner home task", reply=owner_reply)
        self.assertEqual(self.panel(), partner_panel)
        self.send("Partner study task", self.MEMBER, reply=partner_reply)
        self.assertEqual(self.store.list_tasks("home", "all")[-1]["title"], "Owner home task")
        self.assertEqual(self.store.list_tasks("study", "all")[-1]["title"], "Partner study task")
        self.assertEqual(len(self.api.messages), 1)

    def test_cancel_button_is_user_bound(self):
        self.add()
        self.click("cancel:", self.MEMBER)
        self.assertIsNotNone(self.store.get("pending_101"))
        self.click("cancel:")
        self.assertIsNone(self.store.get("pending_101"))
        self.assertEqual(self.store.get("screen"), "menu")

    def test_expired_add_request_updates_same_message(self):
        reply = self.add()
        pending = self.store.get("pending_101")
        pending["expires"] = time.time() - 1
        self.store.set("pending_101", pending)
        self.send("Expired", reply=reply)
        self.assertIn("expired", self.panel()["text"])
        self.assertEqual(len(self.store.list_tasks(mode="all")), 3)
        self.assertEqual(len(self.api.messages), 1)

    def test_add_retry_after_lost_edit_response_is_idempotent(self):
        reply = self.add()
        self.api.fail_once["editMessageText"] = NetworkError("offline")
        self.sequence += 1
        update = {"update_id": self.sequence, "message": {"message_id": 900,
                  "from": {"id": self.OWNER}, "chat": {"id": self.GROUP, "type": "supergroup"}, "text": "Retry item",
                  "reply_to_message": {"message_id": reply}}}
        with self.assertRaises(NetworkError):
            self.bot.handle(update)
        self.bot.handle(update)
        self.bot.handle(update)
        self.assertEqual(len(self.store.list_tasks(mode="all")), 4)
        self.assertIn("Added 1 task", self.panel()["text"])

    def test_check_retry_after_edit_failure_keeps_toggle(self):
        self.click("cat:shopping")
        self.api.fail_once["editMessageText"] = NetworkError("offline")
        row = next(b for b in self.buttons() if b["callback_data"].startswith("task:complete:"))
        update = {"callback_query": {"id": "retry-check", "from": {"id": self.OWNER},
                  "message": {"message_id": self.store.get("list_id"), "chat": {"id": self.GROUP}},
                  "data": row["callback_data"]}}
        with self.assertRaises(NetworkError):
            self.bot.handle(update)
        self.bot.handle(update)
        self.click("task:reopen:")
        self.assertFalse(self.store.task(self.milk)["done"])
        self.assertEqual(self.store.task(self.milk)["revision"], 3)

    def test_retired_commands_only_open_menu_and_never_create_backups(self):
        for command in ("/add", "/list", "/done", "/trash", "/help", "/members", "/invite", "/backup", "/cancel"):
            self.send(command)
            self.assertEqual(self.store.get("screen"), "menu")
        self.assertFalse(any(m == "sendDocument" for m, _, _ in self.api.calls))
        self.assertEqual(list(self.directory.glob("backups/*.zip")), [])

    def test_maintenance_never_creates_or_sends_backups(self):
        for age in (0, 30, 600, 86400, 10 * 365 * 86400):
            self.store.set("last_change", time.time() - age)
            self.store.set("last_cloud_backup", 0)
            self.bot.next_maintenance = 0
            self.bot.maintenance()
        self.assertFalse(any(m == "sendDocument" for m, _, _ in self.api.calls))
        self.assertEqual(list(self.directory.glob("backups/*.zip")), [])
        self.click("backup")
        self.assertEqual(sum(m == "sendDocument" for m, _, _ in self.api.calls), 1)
        self.assertEqual(len(list(self.directory.glob("backups/*.zip"))), 1)

    def test_stale_backup_selection_from_other_screen_does_not_upload(self):
        query = {"id": "stale-backup", "from": {"id": self.OWNER}, "data": "backup",
                 "message": {"message_id": self.store.get("list_id"), "chat": {"id": self.GROUP}}}
        self.click("showlist")
        before = copy.deepcopy(self.panel())
        self.bot.handle({"callback_query": query})
        self.assertEqual(self.panel(), before)
        self.assertFalse(any(m == "sendDocument" for m, _, _ in self.api.calls))

    def test_whole_list_is_explicit_and_not_refreshed_by_maintenance(self):
        self.click("showlist")
        before = copy.deepcopy(self.panel())
        self.store.add_task("study", "CELPIP", self.OWNER)
        self.bot.changed()
        self.bot.maintenance()
        self.assertEqual(self.panel(), before)
        self.menu()
        self.click("showlist")
        self.assertIn("CELPIP", self.panel()["text"])

    def test_all_items_without_paging_and_category_controls(self):
        for number in range(25):
            self.store.add_task("shopping", f"Item {number}", self.OWNER)
        self.click("cat:shopping")
        values = [b["callback_data"] for b in self.buttons()]
        self.assertEqual(sum(v.startswith("task:") for v in values), 26)
        self.assertTrue(any(v.startswith("finish:shopping:") for v in values))
        self.assertIn("remove:shopping", values)
        self.assertIn("add:shopping", values)
        self.assertFalse({"help", "backup", "view:all:trash:0"} & set(values))
        self.assertNotIn("Page ", self.panel()["text"])

    def test_each_member_sees_saved_counts_after_every_category_action(self):
        for uid in (self.OWNER, self.MEMBER):
            with self.subTest(uid=uid):
                self.send('/menu', uid)
                self.click('category:add', uid)
                self.send(f'Audit {uid}', uid, reply=self.store.get(f'pending_{uid}')['prompt_id'])
                category = next(k for k, label in self.store.categories().items() if label == f'Audit {uid}')

                def menu_count(expected):
                    self.click('menu', uid)
                    control = next(b for b in self.buttons() if b['callback_data'] == f'cat:{category}')
                    self.assertTrue(control['text'].endswith(f' · {expected}'), control)
                    self.click(f'cat:{category}', uid)

                menu_count(0)
                self.click(f'add:{category}', uid)
                self.send('First task\nSecond task', uid, reply=self.store.get(f'pending_{uid}')['prompt_id'])
                menu_count(2)
                tasks = self.store.list_tasks(category, 'all')
                first, second = (t['id'] for t in tasks)
                self.click(f'task:complete:{first}:', uid)
                menu_count(1)
                self.assertIn('First task', self.panel()['text'])
                self.assertIn('Second task', self.panel()['text'])
                self.click(f'task:reopen:{first}:', uid)
                menu_count(2)
                self.click(f'remove:{category}', uid)
                self.click(f'pick:{second}:', uid)
                self.click('batch:remove:', uid)
                menu_count(1)
                self.click('menu', uid)
                self.click('view:all:trash:0', uid)
                self.click(f'rpick:{second}:', uid)
                self.click('batch:restore:', uid)
                menu_count(2)
                self.click(f'finish:{category}:', uid)
                self.click('undo:', uid)
                menu_count(2)
                self.click(f'category:remove:{category}:', uid)
                self.click('menu', uid)
                self.assertNotIn(f'cat:{category}', [b['callback_data'] for b in self.buttons()])
                self.click('view:all:trash:0', uid)
                self.click(f'category:restore:{category}:', uid)
                menu_count(2)
                self.click(f'finish:{category}:', uid)
                menu_count(0)
                self.assertIn('No tasks here', self.panel()['text'])
                self.click('menu', uid)
                self.click('showlist', uid)
                self.assertNotIn('First task', self.panel()['text'])
                self.assertNotIn('Second task', self.panel()['text'])
                self.assertEqual(len(self.api.messages), 1)
                self.assertEqual(list(self.directory.glob('backups/*.zip')), [])

    def test_oversized_list_stays_safe_and_export_preserves_complete_title(self):
        title = "😀 & <>" * 600
        self.store.add_task("shopping", title, self.OWNER)
        self.click("cat:shopping")
        self.assertIn("single-message limit", self.panel()["text"])
        self.assertIn(title, self.store.export_latest()["txt"].read_text("utf-8"))
        for method, payload, _ in self.api.calls:
            if method in {"sendMessage", "editMessageText"}:
                visible = html.unescape(re.sub(r"<[^>]*>", "", payload["text"]))
                self.assertLessEqual(len(visible.encode("utf-16-le")) // 2, 4096)

    def test_export_failure_does_not_lose_task_and_retries(self):
        self.click("cat:shopping")
        self.click("task:complete:")
        with mock.patch.object(self.store, "export_latest", side_effect=PermissionError("locked")), mock.patch("home_bot.bot.log.warning"):
            self.bot.maintenance()
        self.assertTrue(self.store.task(self.milk)["done"])
        self.assertTrue(self.store.get("exports_dirty"))
        self.bot.next_maintenance = 0
        self.bot.maintenance()
        self.assertFalse(self.store.get("exports_dirty"))

    def test_unauthorized_user_and_other_group_cannot_change_data(self):
        self.click("cat:shopping")
        row = next(b for b in self.buttons() if b["callback_data"].startswith("task:"))
        for uid, chat in ((self.STRANGER, self.GROUP), (self.OWNER, self.OTHER_GROUP)):
            self.bot.handle({"callback_query": {"id": f"bad-{uid}-{chat}", "from": {"id": uid},
                "data": row["callback_data"], "message": {"message_id": self.store.get("list_id"), "chat": {"id": chat}}}})
        self.assertFalse(self.store.task(self.milk)["done"])

    def test_private_navigation_reuses_one_private_message(self):
        self.send("/menu", chat=self.OWNER)
        mid = self.store.get("private_panel_101")
        for value in ("members", "invite"):
            self.send("/menu", chat=self.OWNER)
            self.click(value, chat=self.OWNER)
            self.assertEqual(self.store.get("private_panel_101"), mid)
        self.assertEqual(sum(m == "sendMessage" and p["chat_id"] == self.OWNER for m, p, _ in self.api.calls), 1)


class TelegramTransportTests(unittest.TestCase):
    def test_truncated_success_json_is_retryable_network_error(self):
        with mock.patch("home_bot.bot.urllib.request.urlopen", return_value=io.BytesIO(b"{")):
            with self.assertRaises(NetworkError):
                Telegram("123456:test-token").call("getMe")

    def test_incomplete_http_response_is_retryable_network_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = http.client.IncompleteRead(b"{", 10)
        with mock.patch("home_bot.bot.urllib.request.urlopen", return_value=response):
            with self.assertRaises(NetworkError):
                Telegram("123456:test-token").call("getMe")


class StartupConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_transient_connection_failure_retries_then_succeeds(self):
        api = mock.Mock()
        expected = {"username": "testbot"}
        api.call.side_effect = [NetworkError("Wi-Fi not ready"), expected]
        clock = [0.0]

        def advance(seconds):
            clock[0] += seconds

        with mock.patch("home_bot.bot.time.monotonic", side_effect=lambda: clock[0]), \
                mock.patch("home_bot.bot.time.sleep", side_effect=advance) as sleep, \
                mock.patch("home_bot.bot.log.warning"):
            self.assertEqual(startup_call(api, "getMe", self.directory), expected)
        self.assertEqual(api.call.call_args_list, [mock.call("getMe"), mock.call("getMe")])
        self.assertTrue(sleep.called)
        self.assertEqual(clock[0], 2.0)

    def test_stop_request_cancels_startup_during_connection_retry(self):
        api = mock.Mock()
        api.call.side_effect = NetworkError("Wi-Fi not ready")
        clock = [0.0]

        def stop_during_pause(seconds):
            clock[0] += seconds
            (self.directory / "stop.request").touch()

        with mock.patch("home_bot.bot.time.monotonic", side_effect=lambda: clock[0]), \
                mock.patch("home_bot.bot.time.sleep", side_effect=stop_during_pause), \
                mock.patch("home_bot.bot.log.warning"):
            with self.assertRaisesRegex(RuntimeError, "Startup cancelled"):
                startup_call(api, "getMe", self.directory)
        api.call.assert_called_once_with("getMe")

    def test_invalid_credentials_fail_without_retry_or_wait(self):
        api = mock.Mock()
        api.call.side_effect = TelegramError(401, "Unauthorized")
        with mock.patch("home_bot.bot.time.sleep") as sleep:
            with self.assertRaises(TelegramError) as caught:
                startup_call(api, "getMe", self.directory)
        self.assertEqual(caught.exception.code, 401)
        api.call.assert_called_once_with("getMe")
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
