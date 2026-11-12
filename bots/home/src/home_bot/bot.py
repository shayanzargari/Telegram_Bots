#!/usr/bin/env python3
"""Our Home: a private two-person Telegram checklist. Python 3.10+, no pip."""
from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
import getpass
import hashlib
import html
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .storage import Store

ROOT = Path(__file__).resolve().parent
CATEGORIES = {
    "shopping": "🛒 Groceries",
    "household": "🏠 Home shopping",
    "admin": "📄 Admin & personal",
    "home": "🔧 Home tasks",
    "study": "📚 Languages & study",
}
MENU_COMMANDS = [
    {"command": "menu", "description": "Open the main menu"},
]
log = logging.getLogger("our_home")
LOCAL_TEST_TOKEN = "000000:local-simulator-only"


class TelegramError(Exception):
    def __init__(self, code: int, description: str, retry_after: int = 0):
        self.code, self.description, self.retry_after = code, description, retry_after
        super().__init__(f"Telegram {code}: {description}")


class NetworkError(Exception):
    pass


class Telegram:
    """Small HTTPS Bot API client. Never includes the token in error messages."""
    def __init__(self, token: str, *, api_base: str | None = None):
        self.token = token
        self.api_base = "https://api.telegram.org"
        self.local_opener = None
        if api_base is not None:
            address = urllib.parse.urlsplit(api_base)
            if (token != LOCAL_TEST_TOKEN or address.scheme != "http"
                    or address.hostname != "127.0.0.1" or not address.port
                    or address.username or address.password or address.query or address.fragment
                    or address.path not in {"", "/"}):
                raise ValueError("The simulator requires a loopback HTTP address and the dummy test token.")
            self.api_base = api_base.rstrip("/")
            self.local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, payload=None, *, file: Path | None = None, timeout=15):
        payload = payload or {}
        if file is None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        else:
            boundary = "OurHome" + secrets.token_hex(16)
            parts = []
            for key, value in payload.items():
                parts.append((
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\""
                    f"\r\n\r\n{value}\r\n"
                ).encode("utf-8"))
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", file.name)
            parts.append((
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\";"
                f" filename=\"{safe_name}\"\r\nContent-Type: application/zip\r\n\r\n"
            ).encode("ascii") + file.read_bytes() + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode("ascii"))
            data = b"".join(parts)
            content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            f"{self.api_base}/bot{self.token}/{method}",
            data=data,
            headers={"Content-Type": content_type, "User-Agent": "OurHomeBot/1.0"},
        )
        try:
            open_request = self.local_opener.open if self.local_opener else urllib.request.urlopen
            with open_request(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeError, http.client.HTTPException, OSError):
                raise TelegramError(exc.code, "Request rejected") from None
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException, ValueError, UnicodeError) as exc:
            reason = str(getattr(exc, "reason", exc)).replace(self.token, "[TOKEN]")
            raise NetworkError(reason) from None
        if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
            raise NetworkError('Invalid Telegram response envelope')
        if not result['ok']:
            description = str(result.get("description", "Request failed"))
            parameters = result.get('parameters') or {}
            try:
                code = int(result.get('error_code', 0))
                retry_after = int(parameters.get('retry_after', 0))
            except (TypeError, ValueError, AttributeError):
                raise NetworkError('Invalid Telegram error response') from None
            raise TelegramError(
                code,
                description.replace(self.token, "[TOKEN]"),
                retry_after,
            )
        if 'result' not in result:
            raise NetworkError('Telegram success response has no result')
        return result["result"]

    def send(self, chat_id, text, **kwargs):
        return self.call("sendMessage", {"chat_id": chat_id, "text": text, **kwargs})


ACTION_LABELS = {
    "+ Add category": "➕ Add category", "+ Add task": "➕ Add task",
    "- Remove": "➖ Remove task", "✓ Done": "✅ Done",
    "Remove category": "🗑️ Remove category", "Trash": "🗑️ Trash",
    "Main menu": "🏠 Main menu", "Open current menu": "🏠 Open current menu",
    "Back to main menu": "🏠 Back to main menu",
    "Back to category": "⬅️ Back to category", "Back to Trash": "⬅️ Back to Trash",
    "Cancel adding": "❌ Cancel adding", "Members": "👥 Members",
    "Undo remove category": "↩️ Undo remove category",
}


def button(text, callback, style=None):
    text = ACTION_LABELS.get(text, text)
    if text.startswith("↩ "):
        text = "↩️ " + text[2:]
    elif text.startswith("Restore "):
        text = "♻️ " + text
    elif text.startswith("Move to Trash"):
        text = "🗑️ " + text
    elif text.startswith("Invite "):
        text = "🔗 " + text
    result = {"text": text, "callback_data": callback}
    if style:
        result["style"] = style
    return result


def keyboard(rows):
    return {"inline_keyboard": rows}


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_text(lines, limit=1800):
    # A Unicode code point uses at most two UTF-16 units. Keep a margin below
    # Telegram's 4096-character limit even for lists made entirely of emoji.
    pages, current = [], ""
    for line in lines:
        while len(line) > limit:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            pages.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        pages.append(current.rstrip())
    return pages or ["No tasks yet."]


def fits_message(text):
    visible = html.unescape(re.sub(r"<[^>]*>", "", text))
    return len(visible.encode("utf-16-le")) // 2 <= 4096


class Bot:
    def __init__(self, store, api, username, data_dir):
        self.store, self.api, self.username = store, api, username
        self.data_dir = Path(data_dir)
        self.running = True
        self.render_cache = {}
        self.next_maintenance = 0.0
        self.menu_registered = False
        self.backup_executor = None
        self.backup_jobs = {}
        self.in_polling_loop = False
        self.backup_unconfirmed = False
        self.current_update_id = None
        self.answer_executor = None
        self.answer_jobs = set()
        self.export_executor = None
        self.export_job = None
        self.export_retry_at = 0
        self.cleanup_executor = None
        self.cleanup_jobs = {}

    def owner(self, user_id):
        return any(m["user_id"] == user_id and m["role"] == "owner"
                   for m in self.store.list_members())

    def group(self):
        return self.store.get("chat_id")

    def categories(self):
        return {key: CATEGORIES.get(key, "📁 " + label)
                for key, label in self.store.categories().items()}

    def changed(self):
        # Database writes have already committed before any network request.
        self.store.set("ui_dirty", True)
        self.store.set("last_change", time.time())
        self.store.set("exports_dirty", True)
        # Keep file rendering and fsync out of the button's critical path.
        # Maintenance coalesces a batch of updates into one export.
        self.next_maintenance = 0

    def refresh_exports(self):
        if self.in_polling_loop:
            self.collect_exports()
            if self.export_job is None and time.time() >= self.export_retry_at:
                if self.export_executor is None:
                    self.export_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='readable-export')
                self.export_job = self.export_executor.submit(self._export_worker)
            return
        try:
            self.store.export_latest()
            self.store.set("exports_dirty", False)
        except OSError as exc:
            log.warning("Readable export delayed; task data is saved: %s", exc)

    def _export_worker(self):
        store = Store(self.data_dir)
        try:
            revision = store.revision()
            store.export_latest()
            return revision
        finally:
            store.close()

    def collect_exports(self):
        if self.export_job is None or not self.export_job.done():
            return
        try:
            exported_revision = self.export_job.result()
            # A user may have saved another change while file I/O was pending.
            if exported_revision == self.store.revision():
                self.store.set('exports_dirty', False)
            else:
                self.next_maintenance = 0
        except Exception as exc:
            log.warning('Readable export delayed; task data is saved: %s', exc)
            self.export_retry_at = time.time() + 15
        finally:
            self.export_job = None

    def message(self, chat_id, text, **kwargs):
        # All ordinary bot text shares one editable message per conversation.
        # Documents are sent only after an explicit Backup menu selection.
        rows = [[button("Main menu", "menu")]]
        if chat_id == self.group():
            if kwargs.get("parse_mode") != "HTML":
                text = html.escape(text)
            message_id = self.panel("notice", text, rows)
        else:
            message_id = self.private_panel(chat_id, text, rows, parse_mode=kwargs.get("parse_mode"))
        return {"message_id": message_id}

    def answer(self, query, text="", alert=False):
        if self.in_polling_loop:
            self.answer_jobs = {job for job in self.answer_jobs if not job.done()}
            if len(self.answer_jobs) >= 16:
                # Acknowledgements expire quickly. Keep a click storm from
                # building an unbounded queue or delaying saved task changes.
                return
            if self.answer_executor is None:
                self.answer_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="click-feedback")
            self.answer_jobs.add(self.answer_executor.submit(self._answer, query, text, alert))
            return
        self._answer(query, text, alert)

    def _answer(self, query, text="", alert=False):
        try:
            self.api.call("answerCallbackQuery", {
                "callback_query_id": query["id"], "text": text,
                "show_alert": alert,
            }, timeout=3)
        except (TelegramError, NetworkError):
            # Old callbacks can expire; their committed data changes remain valid.
            pass

    def upsert(self, key, text, markup=None, *, parse_mode=None, fresh=False, chat_id=None):
        chat_id = self.group() if chat_id is None else chat_id
        if not chat_id:
            return None
        kwargs = {}
        if markup is not None:
            kwargs["reply_markup"] = markup
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        content_key = json.dumps([text, kwargs], sort_keys=True, ensure_ascii=False)
        previous = self.store.get(key) if fresh else None
        message_id = None if fresh else self.store.get(key)
        if message_id and self.render_cache.get(key) == content_key:
            return message_id
        if message_id:
            try:
                self.api.call("editMessageText", {
                    "chat_id": chat_id, "message_id": message_id,
                    "text": text, **kwargs,
                })
            except TelegramError as exc:
                if "message is not modified" in exc.description.lower():
                    pass
                elif exc.code == 400 and any(s in exc.description.lower() for s in (
                    "message to edit not found", "message can't be edited",
                    "message_id_invalid", "message identifier is not specified",
                )):
                    message_id = None
                else:
                    raise
        if not message_id:
            send_key = f"panel_send_{key}"
            if self.store.get(send_key):
                # A lost send response may hide a successful delivery. Poll for
                # a click/reply to recover its ID instead of posting duplicates.
                return None
            self.store.set(key, None)
            self.store.set(send_key, {"chat_id": chat_id, "text": text,
                                      "update_id": self.current_update_id,
                                      "previous_message_id": previous, **kwargs})
            try:
                result = self.api.send(chat_id, text, **kwargs)
            except TelegramError:
                # A definite API rejection is safe to retry.
                self.store.set(send_key, None)
                raise
            message_id = result["message_id"]
            self.store.set(key, message_id)
            self.store.set(send_key, None)
        self.render_cache[key] = content_key
        return message_id

    def recover_panel(self, message, key=None):
        """Recover an uncertain send from Telegram's copy of our own message."""
        chat_id = message.get("chat", {}).get("id")
        key = key or ("list_id" if chat_id == self.group() else f"private_panel_{chat_id}")
        pending = self.store.get(f"panel_send_{key}")
        sender = message.get("from") or {}
        if (not pending or self.store.get(key) or not sender.get("is_bot")
                or sender.get("username", "").lower() != self.username.lower()):
            return
        previous = pending.get("previous_message_id")
        if previous and message.get("message_id", 0) <= previous:
            # A matching older menu cannot prove a replacement was delivered.
            return
        expected = pending["text"]
        if pending.get("parse_mode") == "HTML":
            expected = html.unescape(re.sub(r"<[^>]*>", "", expected))
        actual = message.get("text", "")
        if message.get("parse_mode") == "HTML":  # Local simulator representation.
            actual = html.unescape(re.sub(r"<[^>]*>", "", actual))
        if (pending["chat_id"] != chat_id or expected != actual
                or (not pending.get('reply_markup', {}).get('force_reply')
                    and pending.get("reply_markup", {}) != message.get("reply_markup", {}))):
            return
        self.store.set(key, message["message_id"])
        self.store.set(f"panel_send_{key}", None)
        self.render_cache.pop(key, None)
        if key == "list_id" and previous:
            self.retire_panel(previous)

    def retire_panel(self, message_id):
        try:
            self.api.call("editMessageReplyMarkup", {
                "chat_id": self.group(), "message_id": message_id,
                "reply_markup": keyboard([[button("Open current menu", "menu")]]),
            })
        except (TelegramError, NetworkError):
            # The new panel is usable even if an old message cannot be edited.
            log.info("An older panel could not be updated.")
        self.queue_message_cleanup(self.group(), message_id)
        self.cleanup_prompts()

    def panel(self, screen, text, rows, *, fresh=False):
        """Navigation shares one message; explicit commands can bring it forward."""
        previous = self.store.get("list_id")
        self.store.set("screen", screen)
        self.record_panel("list_id", screen, text, rows)
        message_id = self.upsert("list_id", text, keyboard(rows), parse_mode="HTML", fresh=fresh)
        if fresh and previous and message_id and previous != message_id:
            self.retire_panel(previous)
        return message_id

    def record_panel(self, key, screen, text, rows):
        signature = digest(json.dumps([screen, text, rows], sort_keys=True, ensure_ascii=False))
        previous = self.store.get(f"panel_state_{key}") or {}
        state = {"signature": signature, "screen": screen,
                 "epoch": previous.get("epoch") if previous.get("signature") == signature else secrets.token_hex(6),
                 "callbacks": [b["callback_data"] for row in rows for b in row],
                 "text": text, "rows": rows}
        self.store.set(f"panel_state_{key}", state)

    def navigation(self, uid, chat_id):
        """Leave this user's previous operation without cancelling the partner's."""
        if chat_id == self.group():
            self.clear_pending(uid)
            self.store.set("remove_mode", False)
            self.store.set("remove_selection", None)
            self.store.set("last_action", None)
            self.store.set("list_notice", "")
            self.store.set("finish_action", None)

    def clear_pending(self, uid):
        pending = self.store.get(f'pending_{uid}') or {}
        key = pending.get('prompt_key')
        if key:
            self.queue_prompt_cleanup(key, pending['chat_id'])
        self.store.set(f'pending_{uid}', None)
        self.cleanup_prompts()

    def queue_prompt_cleanup(self, key, chat_id):
        mid = self.store.get(key)
        if mid:
            self.queue_message_cleanup(chat_id, mid, key=key)
        elif self.store.get(f'panel_send_{key}'):
            # The prompt may exist even though its response was lost. Keep its
            # identity recoverable if the user later replies after cancellation.
            orphans = self.store.get('orphan_prompt_sends', [])
            if not any(entry['key'] == key for entry in orphans):
                orphans.append({'key': key, 'chat_id': chat_id, 'expires': time.time() + 47 * 3600})
                self.store.set('orphan_prompt_sends', orphans)

    def recover_abandoned_prompt(self, reply):
        orphans = self.store.get('orphan_prompt_sends', [])
        remaining = []
        recovered = False
        for entry in orphans:
            key = entry['key']
            if entry['expires'] <= time.time():
                self.store.set(f'panel_send_{key}', None)
                continue
            if reply.get('chat', {}).get('id') == entry['chat_id']:
                self.recover_panel(reply, key)
            if self.store.get(key):
                self.queue_prompt_cleanup(key, entry['chat_id'])
                recovered = True
            else:
                remaining.append(entry)
        if remaining != orphans:
            self.store.set('orphan_prompt_sends', remaining)
        if recovered:
            self.cleanup_prompts()

    def expire_inputs(self):
        for member in self.store.list_members():
            uid = member['user_id']
            pending = self.store.get(f'pending_{uid}') or {}
            if not pending or pending.get('expires', 0) > time.time():
                continue
            state = self.store.get('panel_state_list_id') or {}
            owns_panel = state.get('screen') == 'input' and state.get('epoch') == pending.get('panel_epoch')
            self.clear_pending(uid)
            if owns_panel:
                self.message(self.group(), 'That add request expired. Open the main menu to start again.')
        self.recover_abandoned_prompt({})

    def queue_message_cleanup(self, chat_id, message_id, *, key=None, sent_at=None):
        """Persist cleanup for a known bot message or successfully saved input."""
        if not message_id:
            return
        entries = self.store.get('prompt_deletions', [])
        if any(e['chat_id'] == chat_id and e['message_id'] == message_id for e in entries):
            return
        entries.append({'key': key, 'chat_id': chat_id, 'message_id': message_id,
                        'expires': (sent_at or time.time()) + 47 * 3600})
        self.store.set('prompt_deletions', entries)

    def cleanup_prompts(self):
        self.collect_cleanup()
        entries = self.store.get('prompt_deletions', [])
        now = time.time()
        cooldown = self.store.get('cleanup_retry_at', 0)
        attempts = 0
        for entry in list(entries):
            # Telegram cannot delete messages older than 48 hours. Pending
            # permissions/network retries must not accumulate indefinitely.
            if entry.get('expires', now + 1) <= now:
                if entry.get('key'):
                    self.store.set(entry['key'], None)
                entries.remove(entry)
                continue
            identity = (entry['chat_id'], entry['message_id'])
            if (cooldown > now or entry.get('retry_at', 0) > now or attempts >= 4
                    or identity in self.cleanup_jobs):
                continue
            if self.in_polling_loop:
                if len(self.cleanup_jobs) >= 2:
                    continue
                if self.cleanup_executor is None:
                    self.cleanup_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='input-cleanup')
                # Only network I/O runs in these workers. Durable queue changes
                # and SQLite access remain on the polling thread.
                self.cleanup_jobs[identity] = self.cleanup_executor.submit(self._delete_message, dict(entry))
                continue
            attempts += 1
            retry_after, rate_limited = self._delete_message(entry)
            if retry_after:
                entry['retry_at'] = now + retry_after
                if rate_limited:
                    cooldown = now + retry_after
                    self.store.set('cleanup_retry_at', cooldown)
                continue
            if entry.get('key'):
                self.store.set(entry['key'], None)
            entries.remove(entry)
        self.store.set('prompt_deletions', entries)

    def _delete_message(self, entry):
        """Network-only result: retry delay and whether it limits all deletions."""
        try:
            self.api.call('deleteMessage', {'chat_id': entry['chat_id'],
                                           'message_id': entry['message_id']}, timeout=3)
        except TelegramError as exc:
            if exc.code == 400 and 'not found' in exc.description.lower():
                return 0, False
            return (max(15, exc.retry_after), True) if exc.code == 429 else (
                60 if exc.code in {400, 403} else 15, False)
        except NetworkError:
            return 15, False
        return 0, False

    def collect_cleanup(self):
        completed = [(identity, job) for identity, job in self.cleanup_jobs.items() if job.done()]
        if not completed:
            return
        entries = self.store.get('prompt_deletions', [])
        for identity, job in completed:
            try:
                retry_after, rate_limited = job.result()
            except Exception as exc:
                log.warning('Input cleanup delayed; saved tasks are unaffected: %s', exc)
                retry_after, rate_limited = 15, False
            if rate_limited:
                self.store.set('cleanup_retry_at', max(self.store.get('cleanup_retry_at', 0), time.time() + retry_after))
            entry = next((entry for entry in entries
                          if (entry['chat_id'], entry['message_id']) == identity), None)
            if entry is not None:
                if retry_after:
                    entry['retry_at'] = time.time() + retry_after
                else:
                    if entry.get('key'):
                        self.store.set(entry['key'], None)
                    entries.remove(entry)
            del self.cleanup_jobs[identity]
        self.store.set('prompt_deletions', entries)
        # Drain another bounded pair promptly; delayed entries still honor their
        # recorded retry timestamp instead of consuming the worker slots.
        self.next_maintenance = 0

    def current_button(self, query):
        message = query.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        value = query.get("data", "")
        # Old menu links are harmless and keep historical messages usable.
        if value == "menu":
            return True
        if self.store.get("retry_callback") == query.get("id"):
            return True
        key = "list_id" if chat_id == self.group() else f"private_panel_{chat_id}"
        state = self.store.get(f"panel_state_{key}") or {}
        return (message.get("message_id") == self.store.get(key)
                and value in state.get("callbacks", []))

    def current_view(self):
        view = self.store.get("view", {})
        category = view.get("category", "shopping")
        mode = view.get("mode", "all")
        if category not in self.categories() and category != "all":
            category = next(iter(self.categories()), 'all')
        if mode not in {"open", "done", "all", "trash"}:
            mode = "all"
        return category, mode, 0

    def select(self, category="shopping", mode="all", page=0):
        self.store.set("view", {"category": category, "mode": mode, "page": 0})

    def register_menu(self):
        self.api.call("setMyCommands", {"commands": MENU_COMMANDS})
        self.api.call("setChatMenuButton", {"menu_button": {"type": "commands"}})
        self.menu_registered = True

    def private_panel(self, chat_id, text, rows=None, *, parse_mode=None):
        self.record_panel(f"private_panel_{chat_id}", "private", text, rows or [])
        return self.upsert(f"private_panel_{chat_id}", text, keyboard(rows or []),
                           parse_mode=parse_mode, chat_id=chat_id)

    def show_menu(self, chat_id, *, fresh=False):
        key = "list_id" if chat_id == self.group() else f"private_panel_{chat_id}"
        # An explicit menu request checks whether a known message still exists.
        # An unknown send must be recovered from its actual reply/callback;
        # another /menu cannot prove that Telegram never accepted it.
        self.render_cache.pop(key, None)
        if chat_id == self.group():
            self.store.set("remove_mode", False)
            self.dashboard(fresh=fresh)
            return
        if self.group():
            text = ("🏠 Our Home\n\n"
                    "Your shared checklist is in your group.\n"
                    f"Open the group and send /menu@{self.username}.\n\n"
                    "You can also download a backup or view connected members here.")
        else:
            text = ("🏠 Our Home\n\n"
                    "To connect your shared checklist:\n"
                    f"1. Add @{self.username} to your private group.\n"
                    f"2. The owner sends /setup@{self.username} there.\n"
                    "3. Open /menu in that group.")
        rows = [[button("💾 Backup", "backup")], [button("Members", "members")]]
        if self.owner(chat_id):
            rows.append([button("Invite Dorsa", "invite")])
        self.private_panel(chat_id, text, rows)

    def show_help(self, chat_id, *, fresh=False):
        self.show_menu(chat_id, fresh=fresh)

    def show_members(self, chat_id):
        text = "Connected members:\n" + "\n".join(
            f"• {m['name']} ({m['role']})" for m in self.store.list_members())
        rows = [[button("Main menu", "menu")]]
        if chat_id == self.group():
            self.panel("members", html.escape(text), rows)
        else:
            self.private_panel(chat_id, text, rows)

    def choose_category(self, *, fresh=False):
        rows = [[button(label, f"add:{category}")] for category, label in self.categories().items()]
        rows.append([button("Back to main menu", "menu")])
        self.panel("choose", "<b>Add task</b>", rows, fresh=fresh)

    def dashboard(self, *, fresh=False):
        counts = self.store.counts()
        total = counts["all"]
        members = self.store.list_members()
        names = " + ".join(m["name"] for m in members) or "Shayan + Dorsa"
        text = (
            "🏠 <b>Our Home — Main menu</b>\n"
            f"{html.escape(names)}\n"
            f"{total['open']} open · {total['done']} done"
        )
        rows = [[button("+ Add category", "category:add", "primary")]]
        rows.extend([[button(f"{label} · {counts[c]['open']}", f"cat:{c}")]
                     for c, label in self.categories().items()])
        rows.extend([
            [button("📋 Show updated list", "showlist")],
            [button("Trash", "view:all:trash:0")],
            [button("💾 Backup", "backup")],
        ])
        message_id = self.panel("menu", text, rows, fresh=fresh)
        self.store.set("dashboard_id", message_id)

    def undo_control(self):
        receipt_id = self.store.get("last_action")
        receipt = self.store.get(f"undo_{receipt_id}") or {}
        if receipt.get("inverse") in {"complete", "reopen"}:
            return None, None, None
        if receipt.get('tasks'):
            valid = not receipt.get('undone') and not receipt.get('expired')
            for tid, revision in receipt['tasks'].items():
                task = self.store.task(int(tid))
                valid = valid and bool(task and task['revision'] == revision)
            if valid:
                label = receipt.get('undo_label', 'Undo remove')
                if label == 'Undo Done':
                    label = 'Undo remove tasks'
                return button(f"↩ {label}", f'undo:{receipt_id}'), receipt_id, receipt
            return None, None, None
        task = self.store.task(receipt["task_id"]) if receipt.get("task_id") else None
        if (task and not receipt.get("undone") and not receipt.get("expired")
                and task["revision"] == receipt["revision"]):
            label = receipt.get("undo_label", "Undo check")
            return button(f"↩ {label}", f"undo:{receipt_id}"), receipt_id, receipt
        return None, None, None

    def action_panel(self, screen, text, rows, *, fresh=False):
        undo, receipt_id, receipt = self.undo_control()
        if undo:
            rows.insert(0, [undo])
        message_id = self.panel(screen, text, rows, fresh=fresh)
        if receipt:
            receipt["message_id"] = message_id
            self.store.set(f"undo_{receipt_id}", receipt)

    def task_list(self, *, fresh=False):
        category, mode, _ = self.current_view()
        tasks = self.store.list_tasks(None if category == "all" else category, mode)
        removed = self.store.get('removed_categories', []) if mode == 'trash' else []
        removed_keys = {c['key'] for c in removed}
        tasks = [t for t in tasks if t['category'] not in removed_keys]
        title = self.categories().get(category, "📋 Tasks")
        mode_label = {"open": "Open", "done": "Done", "all": "All", "trash": "Trash"}[mode]
        text = f"<b>{html.escape(title)}</b>\n{mode_label}: {len(tasks)}"
        removing = self.store.get("remove_mode", False) and mode != "trash"
        selection = self.store.get('remove_selection') or {}
        if removing and selection.get('category') != category:
            removing = False
            self.store.set('remove_mode', False)
        restoring = (mode == 'trash' and selection.get('action') == 'restore'
                     and selection.get('category') == category and not selection.get('committed'))
        selecting = removing or restoring
        selected = selection.get('selected', {}) if selecting else {}
        if mode == "trash":
            text = f"<b>Trash</b>\n{len(selected)} selected" if restoring else '<b>Trash</b>'
            if not tasks and not removed:
                text += "\nNo deleted tasks."
        elif removing:
            text = f"<b>{html.escape(title)} — Remove</b>\n{len(selected)} selected"
        elif not tasks:
            text += "\nNo completed tasks yet." if mode == "done" else "\nNo tasks here."
        notice = self.store.get("list_notice", "")
        if notice:
            text += f"\n\n{html.escape(notice)}"
        controls = []
        for task in tasks:
            tid, rev = task["id"], task["revision"]
            mark = "🟢" if task["done"] else "⚪"
            if selecting:
                mark = '🔘' if str(tid) in selected else '⚪'
            text += f"\n\n{mark} <b>{tid}.</b> {html.escape(task['title'])}"
            if selecting:
                desired = 0 if str(tid) in selected else 1
                generation = selection.get('items', {}).get(str(tid), 0)
                prefix = 'rpick' if restoring else 'pick'
                controls.append(button(f"{mark} {tid}", f"{prefix}:{tid}:{rev}:{selection['nonce']}:{desired}:{generation}"))
            elif mode != 'trash':
                action = "reopen" if task["done"] else "complete"
                controls.append(button(f"{mark} {tid}", f"task:{action}:{tid}:{rev}"))
        rows = [controls[index:index + 2] for index in range(0, len(controls), 2)]
        for entry in removed:
            label = self.store.categories(include_removed=True)[entry['key']]
            text += f"\n\n📁 {html.escape(label)} · {len(entry['task_ids'])} tasks"
            rows.append([button(f"Restore {label}", f"category:restore:{entry['key']}:{entry['token']}")])
        if not fits_message(text) or len(controls) + len(removed) > 90:
            text = (f"<b>{html.escape(title)}</b>\n{len(tasks)} tasks\n\n"
                    "This list exceeds Telegram's single-message limit. "
                    "The full readable list is available through Backup in the main menu.")
            rows = []
        if mode == 'trash':
            if selected and rows:
                rows.append([button(f'Restore selected ({len(selected)})',
                                    f"batch:restore:{selection['nonce']}:{selection['version']}", 'primary')])
            elif tasks and not restoring:
                rows.append([button('Restore tasks', 'restore:select')])
        elif category != "all" and not removing:
            rows.append([button("+ Add task", f"add:{category}", "primary")])
            rows.append([button("- Remove", f"remove:{category}")])
            expected = {str(t['id']): t['revision'] for t in self.store.list_tasks(category, 'all')}
            finish = self.store.get('finish_action') or {}
            if finish.get('category') != category or finish.get('tasks') != expected:
                finish = {'category': category, 'tasks': expected, 'nonce': secrets.token_hex(6)}
                self.store.set('finish_action', finish)
            done = button("✓ Done", f"finish:{category}:{finish['nonce']}")
            rows.append([button("Remove category", f"category:remove:{category}:{self.store.category_revision(category)}")])
            rows.append([done])
        elif category != "all":
            if selected and rows:
                rows.append([button(f"Move to Trash ({len(selected)})",
                                    f"batch:remove:{selection['nonce']}:{selection['version']}", 'danger')])
            rows.append([button("Back to category", f"cat:{category}")])
        rows.append([button("Main menu", "menu")])
        self.action_panel("list", text, rows, fresh=fresh)

    def result_panel(self):
        text = html.escape(self.store.get("list_notice", "Saved ✓"))
        receipt = self.store.get(f"undo_{self.store.get('last_action')}") or {}
        task = self.store.task(receipt["task_id"]) if receipt.get('task_id') else None
        rows = []
        if task and not task["deleted"]:
            mark = "🟢" if task["done"] else "⚪"
            detail = f"\n\n{mark} <b>{task['id']}.</b> {html.escape(task['title'])}"
            if fits_message(text + detail):
                text += detail
            action = "reopen" if task["done"] else "complete"
            rows.append([button(f"{mark} {task['id']}", f"task:{action}:{task['id']}:{task['revision']}")])
        category, mode, _ = self.current_view()
        if receipt.get('undo_label') in {'Undo Done', 'Undo remove tasks'}:
            pass  # Clearing a category has only Undo and Main menu.
        elif mode == 'trash':
            rows.append([button('Back to Trash', f'view:{category}:trash:0')])
        elif category in self.categories():
            rows.append([button("Back to category", f"cat:{category}")])
        rows.append([button("Main menu", "menu")])
        self.action_panel("result", text, rows)

    def start_selection(self, uid, category, action, request_id):
        if (self.store.get('remove_selection') or {}).get('request_id') != request_id:
            self.store.set('remove_selection', {'owner': uid, 'category': category, 'action': action,
                'nonce': secrets.token_hex(6), 'selected': {}, 'items': {}, 'version': 0, 'request_id': request_id})

    def remove_selection_action(self, query, parts):
        """Select without mutating tasks; commit the reviewed set atomically."""
        selection = self.store.get('remove_selection') or {}
        uid = query['from']['id']
        if selection.get('owner') != uid:
            self.answer(query, 'This selection belongs to the other member.')
            return
        selecting = parts[0] in {'pick', 'rpick'}
        action = selection.get('action', 'delete')
        restoring = action == 'restore'
        if ((selecting and parts[0] != ('rpick' if restoring else 'pick'))
                or (not selecting and parts[1] != ('restore' if restoring else 'remove'))):
            self.answer(query, 'This selection has closed.')
            return
        nonce = parts[3] if selecting else parts[2]
        if nonce != selection.get('nonce'):
            self.answer(query, 'This selection has closed.')
            return
        selected = selection['selected']
        category = selection['category']
        active_categories = self.categories()
        def eligible(task, revision):
            return (task and (category == 'all' or task['category'] == category)
                    and task['category'] in active_categories
                    and task['deleted'] == restoring and task['revision'] == revision)
        if selecting:
            if (selection.get('committed') or
                    (not restoring and not self.store.get('remove_mode'))
                    or (restoring and (self.store.get('screen') != 'list' or self.current_view()[1] != 'trash'))):
                self.answer(query, 'This selection has closed.')
                return
            try:
                tid, revision, desired, generation = int(parts[1]), int(parts[2]), int(parts[4]), int(parts[5])
            except ValueError:
                self.answer(query, 'Invalid selection.')
                return
            if selection.get('last_pick') == query['id']:
                self.answer(query)
                self.task_list()
                return
            key = str(tid)
            task = self.store.task(tid)
            if (desired not in (0, 1) or generation != selection['items'].get(key, 0)
                    or not eligible(task, revision)):
                self.answer(query, 'This item has changed.')
                self.task_list()
                return
            if desired:
                selected[key] = revision
            else:
                selected.pop(key, None)
            selection['items'][key] = generation + 1
            if generation >= 999:
                selection['nonce'] = secrets.token_hex(6)
                selection['items'] = {}
            selection['version'] += 1
            selection['last_pick'] = query['id']
            self.store.set('remove_selection', selection)
            self.store.set('list_notice', '')
            self.answer(query)
            self.task_list()
            return
        try:
            version = int(parts[3])
        except ValueError:
            self.answer(query, 'Invalid selection.')
            return
        if version != selection['version'] or not selected:
            self.answer(query, 'The selection has changed.')
            return
        source_key = f"batch:{selection['request_id']}:{nonce}:{version}"
        scope = None if category == 'all' else category
        result = self.store.change_tasks(scope, action, {int(k): v for k, v in selected.items()}, uid, source_key)
        if result is None:
            active_categories = self.categories()
            for key, revision in list(selected.items()):
                task = self.store.task(int(key))
                if not eligible(task, revision):
                    selected.pop(key)
                    selection['items'][key] = selection['items'].get(key, 0) + 1
            if any(generation > 999 for generation in selection['items'].values()):
                selection['nonce'] = secrets.token_hex(6)
                selection['items'] = {}
            selection['version'] += 1
            self.store.set('remove_selection', selection)
            self.store.set('list_notice', 'Some items changed. Review your selection.')
            self.answer(query, 'No items were restored.' if restoring else 'No items were removed.')
            self.task_list()
            return
        selection['committed'] = result
        self.store.set('remove_selection', selection)
        receipt_id = digest(source_key)[:16]
        self.store.set(f'undo_{receipt_id}', {
            'category': scope, 'tasks': {str(t['id']): t['revision'] for t in result},
            'inverse': 'delete' if restoring else 'restore',
            'undo_label': 'Undo restore' if restoring else 'Undo remove', 'inline': True,
        })
        self.store.set('last_action', receipt_id)
        self.store.set('remove_mode', False)
        self.store.set('screen', 'result')
        count = f"{len(result)} {'task' if len(result) == 1 else 'tasks'}"
        self.store.set('list_notice', f'Restored {count}.' if restoring else f'Moved {count} to Trash.')
        self.changed()
        self.answer(query)
        self.sync()

    def finish_category(self, query, category, nonce):
        """Clear the displayed category as one recoverable, replay-safe action."""
        request_key = 'finish_request_' + digest(query['id'])[:24]
        finish = self.store.get(request_key) or self.store.get('finish_action') or {}
        if finish.get('category') != category or finish.get('nonce') != nonce:
            self.answer(query, 'That category has changed. Open it again.')
            return
        self.store.set(request_key, finish)
        expected = {int(k): v for k, v in finish['tasks'].items()}
        source_key = f"finish:{query['id']}:{nonce}"
        result = self.store.change_tasks(category, 'delete', expected, query['from']['id'],
                                         source_key, require_all=True) if expected else (
                                             None if self.store.list_tasks(category, 'all') else [])
        if result is None:
            self.answer(query, 'The tasks changed. Review the category and tap Done again.')
            self.store.set('list_notice', '')
            self.task_list()
            return
        self.clear_pending(query['from']['id'])
        self.store.set('remove_selection', None)
        self.store.set('remove_mode', False)
        self.select(category)
        self.store.set('screen', 'result')
        label = self.store.categories()[category]
        receipt_id = digest(source_key)[:16]
        self.store.set(f'undo_{receipt_id}', {
            'category': category, 'tasks': {str(t['id']): t['revision'] for t in result},
            'inverse': 'restore', 'undo_label': 'Undo remove tasks', 'inline': True,
        })
        self.store.set('last_action', receipt_id)
        if result:
            count = f"{len(result)} {'task' if len(result) == 1 else 'tasks'}"
            self.store.set('list_notice', f'{label} cleared ✓\nMoved {count} to Trash.')
            self.changed()
        else:
            self.store.set('list_notice', f'{label} is empty.')
        self.answer(query)
        self.sync()

    def undo_batch(self, query, receipt_id, receipt):
        if not receipt.get('undone'):
            result = self.store.change_tasks(receipt['category'], receipt['inverse'],
                {int(k): v for k, v in receipt['tasks'].items()}, query['from']['id'], f'undo-batch:{receipt_id}')
            if result is None:
                receipt['expired'] = True
                self.store.set(f'undo_{receipt_id}', receipt)
                self.store.set('list_notice', 'Some items changed. Nothing was undone.')
            else:
                receipt['undone'] = True
                self.store.set(f'undo_{receipt_id}', receipt)
                count = f"{len(result)} {'task' if len(result) == 1 else 'tasks'}"
                self.store.set('list_notice', f'Restored {count}.' if receipt['inverse'] == 'restore' else f'Moved {count} to Trash.')
                self.changed()
        self.store.set('remove_mode', False)
        self.store.set('remove_selection', None)
        self.store.set('screen', 'result')
        if receipt.get('undone') and receipt.get('undo_label') in {'Undo Done', 'Undo remove tasks'}:
            self.select(receipt['category'])
            self.store.set('last_action', None)
            self.store.set('screen', 'list')
        self.answer(query)
        self.sync()

    def snapshot(self):
        """Whole-list view, shown only following an explicit request."""
        text = "<b>📋 Our Home — Updated list</b>"
        for category, label in self.categories().items():
            text += f"\n\n<b>{html.escape(label)}</b>"
            tasks = self.store.list_tasks(category, "all")
            for task in tasks:
                mark = "🟢" if task["done"] else "⚪"
                text += f"\n{mark} {task['id']}. {html.escape(task['title'])}"
            if not tasks:
                text += "\nNo tasks."
        text += f"\n\nSaved revision: {self.store.revision()}"
        if not fits_message(text):
            text = ("<b>📋 Updated list</b>\n\n"
                    "This list exceeds Telegram's single-message limit. "
                    "Use Backup in the main menu to download the complete readable list.")
        self.panel("snapshot", text, [[button("Main menu", "menu")]])
        self.store.set("last_snapshot_revision", self.store.revision())

    def sync(self, *, fresh=False):
        if not self.group():
            return
        if not self.store.get("single_panel_layout"):
            old_menu, old_list = self.store.get("dashboard_id"), self.store.get("list_id")
            if old_menu and old_list and old_menu != old_list:
                # Keep the original main menu and retire the previous separate list.
                try:
                    self.api.call("editMessageReplyMarkup", {
                        "chat_id": self.group(), "message_id": old_list,
                        "reply_markup": keyboard([[button("Main menu", "menu")]]),
                    })
                except (TelegramError, NetworkError):
                    log.info("An older list could not be retired; its task buttons remain guarded.")
                self.store.set("list_id", old_menu)
                self.store.set("screen", "menu")
                self.render_cache.clear()
            self.store.set("single_panel_layout", True)
        screen = self.store.get("screen", "menu")
        if screen == "list":
            self.task_list(fresh=fresh)
        elif screen == "result":
            self.result_panel()
        elif screen == "menu" or not self.store.get("list_id"):
            self.dashboard(fresh=fresh)
        else:
            state = self.store.get('panel_state_list_id') or {}
            if state.get('screen') == screen and 'text' in state and 'rows' in state:
                # Re-deliver the saved content, including input/result notices,
                # without generating a new whole-list view or input request.
                self.upsert('list_id', state['text'], keyboard(state['rows']), parse_mode='HTML')
        self.store.set("ui_dirty", False)

    def _backup_worker(self, target):
        # SQLite connections belong to their thread. The interactive loop stays
        # free while creating and uploading the ZIP.
        store = Store(self.data_dir)
        try:
            worker = Bot(store, self.api, self.username, self.data_dir)
            worker.send_backup(target, notify=False)
            return worker.backup_unconfirmed
        finally:
            store.close()

    def collect_backups(self):
        for destination, job in list(self.backup_jobs.items()):
            if not job["future"].done():
                continue
            try:
                uncertain = job["future"].result()
                text = ("Backup delivery is unconfirmed. If the file did not arrive, "
                        "use Backup in the main menu to try again.") if uncertain else None
            except Exception as exc:
                log.warning("Background backup delayed: %s", exc)
                text = "Backup could not be sent. Use Backup in the main menu to try again."
            state = self.store.get(f"panel_state_{job['panel_key']}") or {}
            if text and job["manual"] and state.get("epoch") == job["panel_epoch"]:
                if destination == self.group():
                    self.panel("backup", text, [[button("Main menu", "menu")]])
                else:
                    self.private_panel(destination, text, [[button("Main menu", "menu")]])
            del self.backup_jobs[destination]

    def send_backup(self, target=None, *, notify=True):
        destination = target or self.group()
        if self.in_polling_loop:
            self.collect_backups()
            if destination not in self.backup_jobs:
                if self.backup_executor is None:
                    self.backup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backup")
                key = "list_id" if destination == self.group() else f"private_panel_{destination}"
                self.backup_jobs[destination] = {
                    "future": self.backup_executor.submit(self._backup_worker, target),
                    "manual": target is not None, "panel_key": key,
                    "panel_epoch": (self.store.get(f"panel_state_{key}") or {}).get("epoch"),
                }
            return None
        # Keep revision, readable files, and the SQLite snapshot consistent even
        # when another thread saves a task while this backup is being built.
        with self.store.conn:
            self.store.conn.execute("BEGIN")
            revision = self.store.revision()
            pending = self.store.get("pending_backup") or {}
            path = Path(pending.get("path", ""))
            if (pending.get("revision") != revision or not path.is_file()
                    or path.resolve().parent != self.store.backups_dir.resolve()):
                path = self.store.create_backup()
        self.store.set("pending_backup", {"revision": revision, "path": str(path.resolve())})
        destination = target or self.group()
        if destination:
            try:
                self.api.call("sendDocument", {
                    "chat_id": destination,
                    "caption": (
                        f"💾 Our Home backup · revision {revision}\n"
                        "Includes the readable list and recovery data. No bot token.\n"
                        "Keep a downloaded copy if you want access without Telegram."
                    ),
                    "disable_notification": "true",
                }, file=path, timeout=20)
            except NetworkError:
                self.backup_unconfirmed = True
                # Sending a document has no Telegram idempotency key. Do not
                # automatically resend when its acceptance is unknown.
                if destination == self.group():
                    self.store.set("cloud_backup_uncertain_revision", revision)
                if target is not None and notify:
                    text = ("Your backup is saved on the PC. I couldn't confirm its delivery here.\n\n"
                            "If the file did not arrive, choose Backup from the main menu again.")
                    rows = [[button("Main menu", "menu")]]
                    if destination == self.group():
                        self.panel("backup", text, rows)
                    else:
                        self.private_panel(destination, text, rows)
                log.warning("Backup delivery is unconfirmed; the upload was not repeated.")
                return path
            if destination == self.group():
                self.store.set("last_cloud_backup", time.time())
                self.store.set("last_cloud_backup_revision", revision)
                self.store.set("cloud_backup_uncertain_revision", None)
        return path

    def issue_invite(self, user_id):
        if len(self.store.list_members()) >= 2:
            self.private_panel(user_id, "Both places are already connected.", [
                [button("Members", "members")], [button("Main menu", "menu")]])
            return
        claim = self.store.get("join_claim") or {}
        if not claim.get("code") or claim.get("expires", 0) <= time.time():
            code = "join_" + secrets.token_urlsafe(18)
            claim = {"code": code, "hash": digest(code), "expires": time.time() + 86400}
            self.store.set("join_claim", claim)
        self.private_panel(
            user_id,
            "Send this one-time private link to Dorsa. It expires in 24 hours.\n"
            "Do not post it publicly.\n\n"
            f"https://t.me/{self.username}?start={claim['code']}",
            [[button("Main menu", "menu")]],
        )

    def start(self, message, argument):
        chat, user = message["chat"], message["from"]
        uid = user["id"]
        if chat["type"] != "private":
            if self.store.authorized(uid) and chat["id"] == self.group():
                self.navigation(uid, chat["id"])
                self.show_menu(chat["id"])
            elif self.store.authorized(uid):
                self.message(chat["id"], "First connect privately using the link shown on your PC, then run /setup here.")
            return
        name = str(user.get("first_name") or "Member")[:80]
        if self.store.authorized(uid):
            self.show_menu(uid)
            return
        kind = "owner_claim" if argument.startswith("owner_") else "join_claim"
        claim = self.store.get(kind) or {}
        valid = bool(argument and claim.get("expires", 0) > time.time()
                     and secrets.compare_digest(str(claim.get("hash", "")), digest(argument)))
        if kind == "owner_claim" and self.store.list_members():
            valid = False
        if kind == "join_claim" and (not self.store.list_members() or len(self.store.list_members()) >= 2):
            valid = False
        if not valid:
            self.message(uid, "This private bot needs a valid one-time invitation link.")
            return
        self.store.add_member(uid, name, "owner" if kind == "owner_claim" else "member")
        self.store.set(kind, None)
        self.changed()
        if kind == "owner_claim":
            self.message(uid,
                "Connected as owner ✓\n\n"
                "1. Make a private Telegram group with Dorsa.\n"
                f"2. Add @{self.username} to that group.\n"
                f"3. Send /setup@{self.username} in the group.\n\n"
                "I will also send you Dorsa's invitation link.")
            self.issue_invite(uid)
            return
        else:
            self.message(uid, "Connected ✓\nOpen your shared group. You can now add, tick, delete and restore tasks.")
            if self.group():
                self.message(self.group(), f"{name} is connected ✓")
        self.show_menu(uid)

    def setup_group(self, message):
        uid, chat = message["from"]["id"], message["chat"]
        current = self.group()
        if current == chat["id"] and self.store.authorized(uid):
            self.navigation(uid, chat["id"])
            self.show_menu(chat["id"])
            return
        if not self.owner(uid):
            self.message(chat["id"], "Only the connected owner can set up the group.")
            return
        if chat["type"] not in {"group", "supergroup"}:
            self.message(chat["id"], "Send /setup inside your private group with Dorsa and the bot.")
            return
        if current and current != chat["id"]:
            self.message(chat["id"], "This bot is already linked to a different group. Use the existing shared group.")
            return
        if not current:
            self.store.set("chat_id", chat["id"])
            self.store.set("last_snapshot_revision", -1)
            self.changed()
        self.sync()

    def ask_task(self, uid, category):
        if category not in self.categories():
            return
        pending = self.store.get(f"pending_{uid}") or {}
        if (pending.get("kind", "task") != "task" or pending.get("category") != category or pending.get("chat_id") != self.group()
                or pending.get("expires", 0) <= time.time()):
            self.clear_pending(uid)
            pending = {"category": category, "chat_id": self.group(),
                       "prompt_id": self.store.get("list_id"), "expires": time.time() + 1800,
                       "request_id": secrets.token_hex(6)}
        self.store.set(f"pending_{uid}", pending)
        self.add_prompt_panel(uid, pending)

    def ask_category(self, uid):
        pending = self.store.get(f"pending_{uid}") or {}
        if (pending.get("kind") != "category" or pending.get("chat_id") != self.group()
                or pending.get("expires", 0) <= time.time()):
            self.clear_pending(uid)
            pending = {"kind": "category", "chat_id": self.group(),
                       "prompt_id": self.store.get("list_id"), "expires": time.time() + 1800,
                       "request_id": secrets.token_hex(6)}
        self.store.set(f"pending_{uid}", pending)
        self.add_prompt_panel(uid, pending)

    def add_prompt_panel(self, uid, pending, error="", *, update_panel=True):
        member = next(m for m in self.store.list_members() if m["user_id"] == uid)
        if pending.get("kind") == "category":
            text = "<b>Add category</b>"
        else:
            text = f'<b>{html.escape(self.categories()[pending["category"]])} — Add task</b>'
        if error:
            text += "\n\n" + html.escape(error)
        rows = [[button("Cancel adding", f"cancel:{uid}:{pending['request_id']}")],
                [button("Main menu", "menu")]]
        message_id = pending.get('prompt_id')
        if update_panel:
            message_id = self.panel("input", text, rows)
            pending["panel_epoch"] = self.store.get("panel_state_list_id")["epoch"]
        kind = pending.get('kind', 'task')
        key = f"{kind}_prompt_{uid}_{pending['request_id']}_{pending.get('prompt_serial', 0)}"
        old_key = pending.get('prompt_key')
        if old_key and old_key != key:
            self.queue_prompt_cleanup(old_key, pending['chat_id'])
            self.cleanup_prompts()
        pending['prompt_key'] = key
        self.store.set(f'pending_{uid}', pending)
        message_id = self.store.get(key)
        if not message_id:
            instruction = ('Category name' if kind == 'category' else 'Tasks, one per line')
            prompt = f'<a href="tg://user?id={uid}">{html.escape(member["name"])}</a> · {instruction}'
            if error:
                prompt += '\n' + html.escape(error)
            message_id = self.upsert(key, prompt, {'force_reply': True, 'selective': True,
                'input_field_placeholder': 'Category name' if kind == 'category' else 'Your tasks'},
                parse_mode='HTML', chat_id=pending['chat_id'])
        pending["prompt_id"] = message_id
        self.store.set(f"pending_{uid}", pending)

    def add_text(self, message, update_id):
        uid = message["from"]["id"]
        pending = self.store.get(f"pending_{uid}") or {}
        reply = message.get("reply_to_message") or {}
        reply_id = reply.get("message_id")
        if not pending or pending.get("chat_id") != message["chat"]["id"]:
            return
        if pending.get('prompt_key'):
            self.recover_panel(reply, pending['prompt_key'])
            pending['prompt_id'] = self.store.get(pending['prompt_key'])
        if not reply_id or pending.get('prompt_id') != reply_id:
            return
        state = self.store.get("panel_state_list_id") or {}
        owns_panel = state.get("screen") == "input" and state.get("epoch") == pending.get("panel_epoch")
        if pending.get("expires", 0) < time.time():
            self.clear_pending(uid)
            if owns_panel:
                self.message(self.group(), "That add request expired. Open the main menu to start again.")
            return
        text = message.get("text", "")
        titles = [line.strip() for line in text.splitlines() if line.strip()]
        creating_category = pending.get("kind") == "category"
        if not titles or len(titles) > (1 if creating_category else 20) or any(len(t) > (24 if creating_category else 240) or "\x00" in t for t in titles):
            error = ("Use one category name, up to 24 characters." if creating_category else
                     "Use 1-20 text lines, each at most 240 characters.")
            pending['prompt_serial'] = update_id
            self.add_prompt_panel(uid, pending, error + (" Type the name again." if creating_category else " Type your tasks again."), update_panel=owns_panel)
            return
        if creating_category:
            try:
                category = self.store.add_category(titles[0], uid, f"update:{update_id}")
            except ValueError as exc:
                if owns_panel or creating_category:
                    pending['prompt_serial'] = update_id
                    self.add_prompt_panel(uid, pending, str(exc), update_panel=owns_panel)
                return
            notice = f"Added category: {titles[0]} ✓"
        else:
            category = pending["category"]
            if category not in self.categories():
                self.clear_pending(uid)
                return
            self.store.add_tasks(category, titles, uid, f'update:{update_id}')
            notice = f"Added {len(titles)} {'task' if len(titles) == 1 else 'tasks'} ✓"
        # All additions have committed. Keep cleanup durable even if updating
        # the shared panel fails or the process restarts before deletion.
        self.queue_message_cleanup(message['chat']['id'], message.get('message_id'),
                                   sent_at=message.get('date'))
        # A partner may have opened another screen while this reply was typed.
        # Save it to its own category, but do not take over the shared panel.
        if pending.get("completing_update") != update_id:
            state = self.store.get("panel_state_list_id") or {}
            pending["owns_panel"] = (state.get("screen") == "input"
                                     and state.get("epoch") == pending.get("panel_epoch"))
            pending["completing_update"] = update_id
            self.store.set(f"pending_{uid}", pending)
        self.changed()
        if pending["owns_panel"]:
            self.select(category)
            self.store.set("screen", "result")
            self.store.set("remove_mode", False)
            self.store.set("last_action", None)
            self.store.set("list_notice", notice)
            self.sync()
        else:
            # Keep the partner on the same screen. A visible main menu must
            # still show newly saved categories and current task counts.
            if self.store.get('screen') == 'menu':
                self.dashboard()
            self.store.set("ui_dirty", False)
        self.clear_pending(uid)

    def callback(self, query):
        user = query.get("from") or {}
        message = query.get("message") or {}
        value = query.get("data", "")
        chat = message.get("chat", {})
        private_menu = (chat.get("type") == "private" and chat.get("id") == user.get("id")
                        and value in {"menu", "help", "members", "invite", "backup"})
        if (not self.store.authorized(user.get("id"))
                or not (chat.get("id") == self.group() or private_menu)):
            self.answer(query, "Only the two connected members can use this shared list.", True)
            return
        self.recover_panel(message)
        parts = value.split(":")
        uid = user["id"]
        if not self.current_button(query):
            self.answer(query, "That screen has changed. Use the buttons currently shown.")
            return
        self.store.set("retry_callback", query["id"])
        if (value in {"menu", "help", "members", "invite", "showlist", "add:choose", 'restore:select'}
                or parts[0] in {"cat", "view", "remove"}):
            retrying_remove = ((parts[0] == 'remove' or value == 'restore:select'
                                or (parts[0] == 'view' and len(parts) == 4 and parts[2] == 'trash'))
                               and (self.store.get('remove_selection') or {}).get('request_id') == query['id'])
            if not retrying_remove:
                self.navigation(uid, chat["id"])
        if value == "menu":
            self.answer(query)
            self.show_menu(chat["id"])
            return
        if value in {"help", "members", "invite"}:
            self.answer(query)
            if value == "help":
                self.show_help(chat["id"])
            elif value == "members":
                self.show_members(chat["id"])
            elif self.owner(uid):
                self.issue_invite(uid)
            else:
                self.message(chat["id"], "Only the owner can create invitations.")
            return
        if value == "showlist":
            self.answer(query)
            self.snapshot()
            return
        if value == "category:add":
            self.answer(query)
            self.ask_category(uid)
            return
        if parts[:2] == ['category', 'remove'] and len(parts) == 4:
            category = parts[2]
            if category not in self.store.categories(include_removed=True):
                self.answer(query, 'That category is no longer available.')
                return
            self.navigation(uid, chat['id'])
            try:
                entry = self.store.remove_category(category, uid, expected_revision=int(parts[3]))
            except ValueError:
                self.answer(query, 'Invalid category action.')
                return
            if entry is None:
                self.answer(query, 'This category has already changed. Open it again.')
                return
            for member in self.store.list_members():
                pending = self.store.get(f"pending_{member['user_id']}") or {}
                if pending.get('category') == category:
                    self.clear_pending(member['user_id'])
            self.changed()
            self.answer(query)
            label = self.store.categories(include_removed=True)[category]
            self.panel('category_removed', f"<b>{html.escape(label)}</b> moved to Trash with its tasks.",
                       [[button('Undo remove category', f"category:restore:{category}:{entry['token']}")],
                        [button('Main menu', 'menu')]])
            self.store.set('ui_dirty', False)
            return
        if parts[:2] == ['category', 'restore'] and len(parts) == 4:
            self.navigation(uid, chat['id'])
            receipt_key = 'category_restore_request_' + digest(query['id'])[:24]
            request = {'category': parts[2], 'token': parts[3]}
            restored = (self.store.get(receipt_key) == request
                        or self.store.restore_category(parts[2], parts[3], uid))
            self.answer(query, '' if restored else 'This category has already changed.')
            if restored:
                self.store.set(receipt_key, request)
                self.changed()
                self.select(parts[2])
                self.store.set('list_notice', 'Category restored ✓')
                self.task_list()
            else:
                self.message(chat['id'], 'This category has already changed.')
            self.store.set('ui_dirty', False)
            return
        if value == "backup":
            state = self.store.get(f"panel_state_{'list_id' if chat['id'] == self.group() else 'private_panel_' + str(chat['id'])}") or {}
            if state.get("screen") not in {"menu", "private"}:
                self.answer(query, "Choose Backup from the main menu.")
                return
            receipt = f"backup_request_{digest(query['id'])[:24]}"
            if self.store.get(receipt):
                self.answer(query, 'This backup was already requested. Select Backup again for another attempt.')
                return
            # Persist before dispatch: if the process stops after an upload but
            # before acknowledging its update, re-delivery must not upload twice.
            # A fresh intentional click has a different Telegram callback ID.
            self.store.set(receipt, {'chat_id': chat['id'], 'requested_at': time.time()})
            self.answer(query)
            self.send_backup(chat["id"])
            return
        if parts[0] == "cancel" and len(parts) == 3:
            pending = self.store.get(f"pending_{uid}") or {}
            if parts[1] != str(uid):
                self.answer(query, "Only the person adding can cancel this request.", True)
                return
            if parts[2] != pending.get("request_id"):
                self.answer(query, "This add request is already closed.")
                return
            self.navigation(uid, chat["id"])
            self.answer(query)
            self.show_menu(chat["id"])
            return
        if parts[0] == "undo" and len(parts) == 2:
            receipt = self.store.get(f"undo_{parts[1]}") or {}
            if not receipt or receipt.get("message_id") != message.get("message_id"):
                self.answer(query, "This Undo is no longer available.", True)
                return
            if receipt.get('tasks'):
                self.undo_batch(query, parts[1], receipt)
                return
            changed = False
            if not receipt.get("undone"):
                changed = self.store.change_task(receipt["task_id"], receipt.get("inverse", "reopen"),
                                                 receipt["revision"], uid)
                if changed:
                    receipt["undone"] = True
                    self.store.set(f"undo_{parts[1]}", receipt)
            undone = receipt.get("undone", False)
            self.answer(query, "" if undone else "This item has changed since that action.")
            if changed:
                task = self.store.task(receipt["task_id"])
                self.store.set("last_action", parts[1])
                self.store.set("list_notice", f"Undone ✓ Item {task['id']}")
                self.changed()
            if not undone:
                receipt["expired"] = True
                self.store.set(f"undo_{parts[1]}", receipt)
            category, mode, _ = self.current_view()
            if self.store.get("screen") != "list" or category == "all" or mode != "all":
                self.store.set("screen", "result")
            self.store.set("remove_mode", False)
            self.sync()
            if not receipt.get("inline"):
                # Retire standalone Undo messages created by older bot versions.
                try:
                    self.api.call("editMessageText", {
                        "chat_id": chat["id"], "message_id": message["message_id"],
                        "text": "Undone ✓" if undone else "This item has changed since that action.",
                        "reply_markup": keyboard([]),
                    })
                except TelegramError as exc:
                    if "message is not modified" not in exc.description.lower():
                        raise
            return
        if parts[0] == "finish" and len(parts) == 3 and parts[1] in self.categories():
            self.finish_category(query, parts[1], parts[2])
            return
        if parts[0] == "remove" and len(parts) == 2 and parts[1] in self.categories():
            self.answer(query)
            self.select(parts[1], "all")
            self.start_selection(uid, parts[1], 'delete', query['id'])
            self.store.set("remove_mode", True)
            self.store.set("list_notice", "")
            self.task_list()
            return
        if ((parts[0] in {'pick', 'rpick'} and len(parts) == 6)
                or (parts[0] == 'batch' and len(parts) == 4 and parts[1] in {'remove', 'restore'})):
            self.remove_selection_action(query, parts)
            return
        if value == 'restore:select':
            category, mode, _ = self.current_view()
            if mode == 'trash':
                self.start_selection(uid, category, 'restore', query['id'])
                self.answer(query)
                self.task_list()
            return
        if parts[0] == "cat" and len(parts) == 2 and parts[1] in self.categories():
            self.store.set("remove_mode", False)
            self.store.set("list_notice", "")
            self.select(parts[1])
        elif parts[0] == "view" and len(parts) == 4:
            if parts[1] not in {*self.categories(), "all"} or parts[2] not in {"open", "done", "all", "trash"}:
                self.answer(query, "Unknown view.")
                return
            try:
                page = min(100000, max(0, int(parts[3])))
            except ValueError:
                self.answer(query, "Invalid page.")
                return
            self.select(parts[1], parts[2], page)
            self.store.set("remove_mode", False)
            self.store.set("list_notice", "")
            if parts[2] == 'trash':
                self.start_selection(uid, parts[1], 'restore', query['id'])
        elif parts[0] == "add" and len(parts) == 2:
            self.answer(query)
            if parts[1] == "choose":
                self.choose_category()
            elif parts[1] in self.categories():
                self.ask_task(uid, parts[1])
            return
        elif parts[0] == "task" and len(parts) == 4:
            if message.get("message_id") != self.store.get("list_id"):
                self.answer(query, "Opening the current list. Tap the item again.")
                self.store.set("remove_mode", False)
                self.task_list()
                return
            action = parts[1]
            if action not in {"complete", "reopen", "delete", "restore"}:
                self.answer(query, "Invalid action.")
                return
            try:
                tid, rev = int(parts[2]), int(parts[3])
                changed = self.store.change_task(tid, action, rev, uid)
            except ValueError:
                self.answer(query, "Invalid task.")
                return
            self.answer(query, "" if changed else "Already changed. Refreshing the list.")
            if changed:
                self.changed()
            receipt_id = digest(query["id"])[:16]
            if changed:
                inverse, label, notice = {
                    "complete": ("reopen", "Undo check", "Checked ✓"),
                    "reopen": ("complete", "Undo uncheck", "Unchecked"),
                    "delete": ("restore", "Undo remove", "Moved to Trash"),
                    "restore": ("delete", "Undo restore", "Restored"),
                }[action]
                self.store.set(f"undo_{receipt_id}", {
                    "task_id": tid, "revision": rev + 1, "inverse": inverse,
                    "undo_label": label, "inline": True,
                })
                self.store.set("last_action", receipt_id)
                self.store.set("list_notice", f"{notice} · Item {tid}")
            category, mode, _ = self.current_view()
            # Only refresh the category already on screen. Other operations show
            # a compact result, never an unsolicited whole-list view.
            if (self.store.get("screen") != "list" or category == "all" or mode not in {"all", "done"}
                    or action in {"delete", "restore"}):
                self.store.set("screen", "result")
            self.store.set("remove_mode", False)
            self.sync()
            return
        else:
            self.answer(query, "This button is no longer available. Open /menu.")
            return
        self.answer(query)
        self.task_list()

    def handle(self, update):
        self.current_update_id = update.get("update_id")
        if "callback_query" in update:
            self.callback(update["callback_query"])
            self.store.set("retry_callback", None)
            return
        message = update.get("message")
        if not message or "from" not in message:
            return
        chat, user = message["chat"], message["from"]
        uid = user["id"]
        if message.get("migrate_to_chat_id") and chat["id"] == self.group():
            self.store.set("chat_id", message["migrate_to_chat_id"])
            for key in ["dashboard_id", "list_id", "pinned_snapshot_id"]:
                self.store.set(key, None)
            for i in range(int(self.store.get("snapshot_page_count", 0))):
                self.store.set(f"snapshot_{i}", None)
            self.store.set("last_snapshot_revision", -1)
            self.render_cache.clear()
            self.changed()
            return
        text = message.get("text", "")
        command, _, argument = text.partition(" ")
        if command.startswith("/") and "@" in command:
            if command.split("@", 1)[1].lower() != self.username.lower():
                return
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        if command == "/start":
            self.start(message, argument)
            return
        if not self.store.authorized(uid):
            if chat["type"] == "private":
                self.message(chat["id"], "Access is limited to the two connected members.")
            return
        self.recover_panel(message.get("reply_to_message") or {})
        self.recover_abandoned_prompt(message.get('reply_to_message') or {})
        if command == "/setup":
            self.setup_group(message)
            return
        in_group = chat["id"] == self.group()
        in_private = chat["type"] == "private"
        if not (in_group or in_private):
            return
        if command.startswith("/"):
            # Retired shortcuts only return to the menu. Setup/pairing above are
            # connection steps, not registered everyday commands.
            self.navigation(uid, chat["id"])
            # Editing an older panel cannot make it visible to a member who
            # joined a group with hidden history. An explicit /menu brings the
            # shared panel forward; button navigation still edits it in place.
            self.show_menu(chat["id"], fresh=in_group and command == "/menu")
        elif in_group:
            self.add_text(message, update["update_id"])

    def maintenance(self):
        self.collect_backups()
        self.collect_exports()
        self.collect_cleanup()
        now = time.time()
        if now < self.next_maintenance:
            return
        self.next_maintenance = now + 15
        self.expire_inputs()
        self.cleanup_prompts()
        if self.store.get("exports_dirty", False):
            self.refresh_exports()
        if not self.menu_registered:
            try:
                self.register_menu()
            except (TelegramError, NetworkError) as exc:
                if isinstance(exc, TelegramError) and exc.code in {401, 409, 429}:
                    raise
                log.warning("Command menu registration delayed: %s", exc)
        if self.group() and self.store.get("ui_dirty", False):
            self.sync()

    def run(self):
        try:
            self._run()
        finally:
            if self.answer_executor:
                self.answer_executor.shutdown(wait=True, cancel_futures=True)
            if self.backup_executor:
                self.backup_executor.shutdown(wait=True, cancel_futures=True)
            if self.export_executor:
                self.export_executor.shutdown(wait=True, cancel_futures=True)
            if self.cleanup_executor:
                self.cleanup_executor.shutdown(wait=True, cancel_futures=True)
                self.collect_cleanup()
            self.in_polling_loop = False
            self.refresh_exports()

    def _run(self):
        stop_file = self.data_dir / "stop.request"
        self.in_polling_loop = True
        if not self.store.get("on_demand_lists"):
            self.store.set("on_demand_lists", True)
            self.store.set("screen", "menu")
        self.store.set("ui_dirty", True)
        log.info("Bot started. Waiting for Telegram updates.")
        delay = 2
        while self.running and not stop_file.exists():
            try:
                try:
                    self.maintenance()
                except (TelegramError, NetworkError) as exc:
                    if isinstance(exc, TelegramError) and exc.code in {401, 409, 429}:
                        raise
                    # A missing group permission must not block private setup/help.
                    log.warning("List or backup sync delayed: %s", exc)
                    self.next_maintenance = time.time() + 60
                updates = self.api.call("getUpdates", {
                    "offset": int(self.store.get("last_update_id", -1)) + 1,
                    "timeout": 1 if self.backup_jobs or self.cleanup_jobs else 5,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=10)
                for update in updates:
                    if not self.running or stop_file.exists():
                        break
                    if update["update_id"] <= int(self.store.get("last_update_id", -1)):
                        continue
                    try:
                        action_started = time.monotonic()
                        self.process_update(update)
                        elapsed = time.monotonic() - action_started
                        if elapsed > 1:
                            kind = "button" if "callback_query" in update else "message"
                            log.info("Slow %s update: %.2fs", kind, elapsed)
                    except TelegramError as exc:
                        if exc.code in {429, 500, 502, 503, 504}:
                            raise
                        if exc.code in {401, 409}:
                            raise
                        log.warning("Update could not be delivered: %s", exc)
                        self.store.set("ui_dirty", True)
                    # Network failures leave offset unconfirmed for a safe retry.
                    # process_update commits the identity with its local effects.
                delay = 2
            except TelegramError as exc:
                if exc.code in {401, 409}:
                    log.error("%s. Check token or stop the other bot instance.", exc)
                    raise
                log.warning("%s; will retry.", exc)
                self.pause(max(delay, exc.retry_after))
                delay = min(delay * 2, 60)
            except NetworkError as exc:
                log.warning("Connection unavailable: %s; will retry.", exc)
                self.pause(delay)
                delay = min(delay * 2, 60)
            except sqlite3.OperationalError as exc:
                code = getattr(exc, 'sqlite_errorcode', 0) & 255
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} and not any(
                        phrase in str(exc).lower() for phrase in (
                            'database is locked', 'database table is locked', 'database is busy')):
                    raise
                # Do not acknowledge a partially handled update. Existing
                # source keys/revisions make re-delivery safe after the lock.
                log.warning('Database is temporarily busy; retrying the current action.')
                self.pause(delay)
                delay = min(delay * 2, 60)
        with contextlib.suppress(OSError):
            stop_file.unlink()
        log.info("Bot stopped. Data is saved.")

    def process_update(self, update):
        with self.store.durable_update(update['update_id']) as fresh:
            if fresh:
                try:self.handle(update)
                except TelegramError as error:
                    if error.code in {401,409,429,500,502,503,504}:raise
                    log.warning('Update could not be delivered: %s',error)
                    self.store.set('ui_dirty',True)

    def pause(self, seconds):
        deadline = time.monotonic() + seconds
        while self.running and time.monotonic() < deadline:
            if (self.data_dir / "stop.request").exists():
                return
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))


@contextlib.contextmanager
def process_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("Another copy is running. Use STOP_BOT.bat first.") from None
    try:
        yield
    finally:
        handle.close()


def configure_logging(data_dir, token, background, log_dir=None):
    class Redact(logging.Filter):
        def filter(self, record):
            record.msg = record.getMessage().replace(token, "[TOKEN]") if token else record.getMessage()
            record.args = ()
            return True
    log.setLevel(logging.INFO)
    directory=Path(log_dir) if log_dir is not None else data_dir
    directory.mkdir(parents=True,exist_ok=True)
    handlers = [RotatingFileHandler(directory / "bot.log", maxBytes=1_000_000,
                                   backupCount=3, encoding="utf-8")]
    if not background:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.addFilter(Redact())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)


def startup_call(api, method, data_dir):
    """Wait for connectivity at sign-in, when Wi-Fi may not be ready yet."""
    delay = 2
    while True:
        if (Path(data_dir) / "stop.request").exists():
            raise RuntimeError("Startup cancelled by STOP_BOT.bat.")
        try:
            return api.call(method)
        except TelegramError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            pause = max(delay, exc.retry_after)
        except NetworkError:
            pause = delay
        log.warning("Waiting for Telegram connectivity; retrying in %s seconds.", pause)
        deadline = time.monotonic() + pause
        while time.monotonic() < deadline:
            if (Path(data_dir) / "stop.request").exists():
                raise RuntimeError("Startup cancelled by STOP_BOT.bat.")
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        delay = min(delay * 2, 30)


def load_config(data_dir, interactive=True):
    path = data_dir / "config.json"
    config = json.loads(path.read_text("utf-8")) if path.exists() else {}
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.get("bot_token", "")
    if not token:
        try:
            from bot_center_runtime.paths import Installation
            from bot_center_runtime.credentials import CredentialStore
            installation=Installation();manifest=installation.bot('home')
            if Path(manifest['data_dir']).resolve()==Path(data_dir).resolve():
                installation.require_state('home')
                token=CredentialStore(installation.root).load(manifest['credential_ref'])
        except (RuntimeError,OSError,ValueError,KeyError):
            if not interactive:raise
    if not token:
        if not interactive:
            raise RuntimeError("Run START_BOT.bat once to finish local setup first.")
        print("\nOUR HOME — first-time setup")
        print("Create your bot in the official @BotFather chat using /newbot.")
        print("Paste its token HERE on your own PC. Input is hidden.")
        token = getpass.getpass("Bot token: ").strip()
        if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
            raise RuntimeError("That does not look like a BotFather token. Run START_BOT.bat again.")
        me = startup_call(Telegram(token), "getMe", data_dir)
        config = {"bot_token": token, "bot_username": me["username"]}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(config, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            temp.chmod(0o600)
        os.replace(temp, path)
    config["bot_token"] = token
    return config


def bootstrap(store, api, username, interactive):
    if not store.get("seed_imported", False):
        use_seed = interactive and input(
            "\nImport your 32 screenshot checklist items? [Y/n]: "
        ).strip().lower() not in {"n", "no"}
        if use_seed:
            items = json.loads((ROOT / "seed_tasks.json").read_text("utf-8"))
            number = store.import_tasks(items)
            print(f"Imported {number} tasks. All start unchecked.")
        store.set("seed_imported", True)
        store.export_latest()
    if not store.list_members():
        if not interactive:
            raise RuntimeError("Connect the owner using START_BOT.bat before enabling background mode.")
        code = "owner_" + secrets.token_urlsafe(18)
        store.set("owner_claim", {"hash": digest(code), "expires": time.time() + 86400})
        print("\n1. Open this PRIVATE one-time link yourself and tap START:")
        print(f"\nhttps://t.me/{username}?start={code}\n")
        print("2. Make a private group with Dorsa and add this bot.")
        print(f"3. Send /setup@{username} in that group.")
        print("4. Forward Dorsa the private invitation the bot sends you.")
    elif interactive:
        print(f"\nConnected bot: @{username}")
        print("Use /menu in your shared group, or /setup if this is the first run.")
    # Do not silently overwrite webhook configuration of an existing bot.
    webhook = startup_call(api, "getWebhookInfo", store.data_dir)
    if webhook.get("url"):
        raise RuntimeError(
            "This token already has a webhook configured. Use a new dedicated bot from "
            "BotFather, or deliberately remove its old webhook before running this copy."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root')
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--backup", action="store_true", help="Create a local recovery ZIP and exit")
    parser.add_argument("--restore", type=Path, help="Restore a ZIP or JSON backup while stopped")
    parser.add_argument("--doctor", action="store_true", help="Check local data and Telegram access")
    args = parser.parse_args(argv)
    installation=None
    if args.data_dir is None:
        from bot_center_runtime.paths import Installation
        installation=Installation(args.root)
        manifest=installation.require_state('home')
        args.data_dir=Path(manifest['data_dir'])
        if not (args.backup or args.restore or args.doctor):
            from bot_center_runtime.runner import main as standalone
            return standalone(['--root',str(installation.root),'--bot','home'])
    data_dir = args.data_dir.resolve()
    if installation is None:
        from bot_center_runtime.paths import Installation,ConfigurationError
        try:
            candidate=Installation(args.root)
            candidate_manifest=candidate.bot('home')
        except ConfigurationError:
            if args.root:raise
            candidate=None
        if candidate and Path(candidate_manifest['data_dir']).resolve()==data_dir:
            candidate.require_state('home');installation=candidate;manifest=candidate_manifest
    if installation and args.restore:
        raise RuntimeError('Canonical restoration requires exclusive core recovery with stopped/no-replay activation. Use the recovery runbook; --restore remains available for explicitly isolated --data-dir copies.')
    if not (data_dir/'tasks.sqlite3').is_file():
        raise RuntimeError('Expected Home state is missing. Restore or explicitly create/register a new project; startup does not initialize an empty list.')
    if not (args.backup or args.restore or args.doctor):
        if not installation:raise RuntimeError('Polling requires registered canonical state. Use --root for the registered installation; --data-dir alone cannot bypass identity protection.')
        from bot_center_runtime.runner import main as standalone
        return standalone(['--root',str(installation.root),'--bot','home'])
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        with contextlib.ExitStack() as ownership:
            if installation:
                from bot_center_runtime.locks import FileLease
                ownership.enter_context(FileLease(installation.root/'data/maintenance.lock',exclusive=bool(args.backup)))
            ownership.enter_context(process_lock(data_dir / "bot.lock"))
            store = Store(data_dir)
            if args.restore:
                store.restore_backup(args.restore.resolve())
                print("Restored. Credentials were not changed. Start the bot to rebuild the shared view.")
                return 0
            if args.backup:
                print(store.create_backup())
                return 0
            with contextlib.suppress(OSError):
                (data_dir / "stop.request").unlink()
            config = load_config(data_dir, not args.background)
            configure_logging(data_dir, config["bot_token"], args.background,log_dir=manifest['log_dir'] if installation else None)
            api = Telegram(config["bot_token"])
            me = startup_call(api, "getMe", data_dir)
            if args.doctor:
                info = api.call("getWebhookInfo")
                print(f"Telegram connection: OK (@{me['username']})")
                print(f"Connected members: {len(store.list_members())}/2")
                print(f"Shared group configured: {bool(store.get('chat_id'))}")
                print(f"Webhook present: {bool(info.get('url'))}")
                print(f"Local database: {data_dir}")
                print(f"Open tasks: {store.counts()['all']['open']}")
                return 0
            bootstrap(store, api, me["username"], not args.background)
            bot = Bot(store, api, me["username"], data_dir)
            def stop(signum, frame):
                bot.running = False
            signal.signal(signal.SIGINT, stop)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, stop)
            if not args.background:
                print("\nBot is running. Keep this window open, or later enable background startup.")
                print("Press Ctrl+C to stop safely. No pip install is needed.\n")
            bot.run()
            return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # Never print request objects, raw config, or a traceback containing token URLs.
        token = locals().get("config", {}).get("bot_token", "")
        safe = str(exc).replace(token, "[TOKEN]") if token else str(exc)
        log.error("Stopped: %s", safe)
        if not args.background:
            print(f"\nCould not start/continue: {safe}")
            print("Your saved list and backups remain in the data folder.")
        return 1
    finally:
        if "store" in locals():
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
