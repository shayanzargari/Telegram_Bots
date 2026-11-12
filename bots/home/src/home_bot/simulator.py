"""Loopback Telegram Bot API simulator and browser chat. Uses synthetic data only."""
from __future__ import annotations

import argparse
import copy
from email import policy
from email.parser import BytesParser
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
import urllib.parse

from .bot import Bot, LOCAL_TEST_TOKEN, ROOT, Telegram, digest, process_lock
from .storage import Store, _atomic_write

OWNER, PARTNER, STRANGER = 101, 202, 303
GROUP, OTHER_GROUP = -100111, -100222
USERS = {OWNER: "Alex (owner)", PARTNER: "Sam (partner)", STRANGER: "Visitor"}


class APIError(Exception):
    def __init__(self, code, text):
        self.code, self.text = code, text
        super().__init__(text)


class TelegramState:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else None
        self.condition = threading.Condition(threading.RLock())
        self.messages = {}
        self.updates = []
        self.next_update = 1
        self.next_message = 1
        self.calls = []
        # All HTTP attempts, including rejected requests and lost responses;
        # calls remains the dispatch log for existing acceptance tests.
        self.requests = []
        self.answers = []
        self.commands = []
        self.menu = {"type": "default"}
        self.files = {}
        self.failures = {}
        self.pins = {}
        self.version = 0
        self.last_acknowledged_update = -1
        self.delete_user_messages_allowed = True
        self.worker_error = None
        self.closing = False
        self.pin_allowed = True
        if self.folder:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / "telegram-state.json"
            if path.exists():
                data = json.loads(path.read_text("utf-8"))
                self.messages = {(m["chat"]["id"], m["message_id"]): m for m in data["messages"]}
                self.next_message = data["next_message"]
                self.next_update = data["next_update"]
                self.updates = data["updates"]

    def save(self):
        self.version += 1
        if self.folder:
            path = self.folder / "telegram-state.json"
            _atomic_write(path, json.dumps({
                "messages": list(self.messages.values()), "next_message": self.next_message,
                "next_update": self.next_update, "updates": self.updates,
            }, ensure_ascii=False))

    def chat(self, chat_id):
        return {"id": chat_id, "type": "private" if chat_id > 0 else "supergroup"}

    def new_message(self, chat_id, uid, **content):
        message = {"message_id": self.next_message, "date": int(time.time()),
                   "chat": self.chat(chat_id),
                   "from": {"id": uid, "is_bot": uid == 999,
                            "username": "local_home_bot" if uid == 999 else f"local_user_{uid}",
                            "first_name": "Our Home" if uid == 999 else USERS.get(uid, "User")},
                   **content}
        self.next_message += 1
        self.messages[(chat_id, message["message_id"])] = message
        return message

    def enqueue(self, content):
        update = {"update_id": self.next_update, **copy.deepcopy(content)}
        self.next_update += 1
        self.updates.append(update)
        self.save()
        self.condition.notify_all()
        return copy.deepcopy(update)

    def send_user(self, text, uid=OWNER, chat_id=GROUP, reply_id=None):
        with self.condition:
            message = self.new_message(chat_id, uid, text=text)
            reply = self.messages.get((chat_id, reply_id))
            if reply:
                message["reply_to_message"] = copy.deepcopy(reply)
                if reply.get("parse_mode") == "HTML":
                    message["reply_to_message"]["text"] = html.unescape(re.sub(r"<[^>]*>", "", reply["text"]))
            # Emulate group privacy: ordinary group conversation is not delivered.
            if chat_id < 0 and not text.startswith("/") and not (reply and reply["from"]["is_bot"]):
                self.save()
                return None
            return self.enqueue({"message": message})

    def click(self, message_id, data, uid=OWNER, chat_id=GROUP):
        with self.condition:
            message = self.messages.get((chat_id, message_id))
            if not message or not message["from"]["is_bot"]:
                raise APIError(400, "Message not found")
            return self.enqueue({"callback_query": {
                "id": f"local-{self.next_update}", "from": {"id": uid},
                "message": copy.deepcopy(message), "data": data,
            }})

    def replay(self, update):
        with self.condition:
            self.updates.append(copy.deepcopy(update))
            self.condition.notify_all()

    def fail_next(self, method, mode):
        with self.condition:
            self.failures.setdefault(method, []).append(mode)

    def validate_content(self, payload):
        text = payload.get("text", "")
        visible = html.unescape(re.sub(r"<[^>]*>", "", text)) if payload.get("parse_mode") == "HTML" else text
        if not visible or len(visible.encode("utf-16-le")) // 2 > 4096:
            raise APIError(400, "Bad Request: message text is empty or too long")
        self.validate_markup(payload.get("reply_markup"))

    def validate_markup(self, markup):
        markup = markup or {}
        for row in markup.get("inline_keyboard", []):
            for button in row:
                if not button.get("text") or not 1 <= len(button.get("callback_data", "").encode("utf-8")) <= 64:
                    raise APIError(400, "Bad Request: BUTTON_DATA_INVALID")

    def dispatch(self, method, payload, upload=None):
        with self.condition:
            self.calls.append({"method": method, "payload": copy.deepcopy(payload)})
            if method == "getMe":
                return {"id": 999, "is_bot": True, "first_name": "Our Home", "username": "local_home_bot"}
            if method == "getWebhookInfo":
                return {"url": "", "pending_update_count": len(self.updates)}
            if method == "getUpdates":
                offset = int(payload.get("offset", 0))
                self.last_acknowledged_update = max(self.last_acknowledged_update, offset - 1)
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                if not self.updates and not self.closing:
                    self.condition.wait(min(float(payload.get("timeout", 0)), 0.25))
                return copy.deepcopy(self.updates[:100])
            if method == "setMyCommands":
                self.commands = payload["commands"]
                return True
            if method == "getMyCommands":
                return self.commands
            if method == "setChatMenuButton":
                self.menu = payload["menu_button"]
                return True
            if method == "getChatMenuButton":
                return self.menu
            if method == "answerCallbackQuery":
                self.answers.append(payload)
                self.version += 1
                return True
            chat_id = int(payload.get("chat_id", 0))
            if method == "sendMessage":
                self.validate_content(payload)
                result = self.new_message(chat_id, 999, **{k: v for k, v in payload.items() if k != "chat_id"})
            elif method == "sendDocument":
                if not upload:
                    raise APIError(400, "Missing document upload")
                caption = payload.get('caption', '')
                if payload.get('parse_mode') == 'HTML':
                    caption = html.unescape(re.sub(r'<[^>]*>', '', caption))
                if len(caption.encode('utf-16-le')) // 2 > 1024:
                    raise APIError(400, 'Bad Request: message caption is too long')
                filename, content = upload
                file_id = str(self.next_message)
                self.files[file_id] = (filename, content)
                if self.folder:
                    downloads = self.folder / "downloads"
                    downloads.mkdir(exist_ok=True)
                    (downloads / f"{file_id}.zip").write_bytes(content)
                result = self.new_message(chat_id, 999, caption=payload.get("caption", ""),
                    document={"file_id": file_id, "file_name": filename, "file_size": len(content)})
            elif method in {"editMessageText", "editMessageReplyMarkup", "deleteMessage", "pinChatMessage"}:
                key = (chat_id, int(payload["message_id"]))
                if key not in self.messages:
                    raise APIError(400, "Bad Request: message to edit not found")
                result = self.messages[key]
                if method == "deleteMessage":
                    if chat_id < 0 and not result['from']['is_bot'] and not self.delete_user_messages_allowed:
                        raise APIError(400, "Bad Request: not enough rights to delete messages")
                    del self.messages[key]
                    result = True
                elif method == "pinChatMessage":
                    if not self.pin_allowed:
                        raise APIError(403, "Not enough rights to pin messages")
                    self.pins[chat_id] = payload["message_id"]
                    result = True
                else:
                    if not result['from']['is_bot']:
                        raise APIError(400, "Bad Request: message can't be edited")
                    if method == "editMessageText":
                        self.validate_content(payload)
                        change = {"text": payload["text"], "parse_mode": payload.get("parse_mode"),
                                  "reply_markup": payload.get("reply_markup", {})}
                    else:
                        self.validate_markup(payload.get('reply_markup'))
                        change = {"reply_markup": payload.get("reply_markup", {})}
                    if all(result.get(k) == v for k, v in change.items()):
                        raise APIError(400, "Bad Request: message is not modified")
                    result.update(change)
            else:
                raise APIError(404, f"Unsupported simulated API method: {method}")
            self.save()
            return copy.deepcopy(result)

    def snapshot(self):
        with self.condition:
            return copy.deepcopy({"messages": list(self.messages.values()), "answers": self.answers[-15:],
                "commands": self.commands, "version": self.version, "error": self.worker_error,
                "api_calls": len(self.calls), "api_attempts": len(self.requests), "queued_updates": len(self.updates),
                "last_acknowledged_update": self.last_acknowledged_update})


class SimulatorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state, port=0):
        self.state = state
        super().__init__(("127.0.0.1", port), SimulatorHandler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"


class SimulatorHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value, content_type="application/json; charset=utf-8"):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8") if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self.respond(200, (ROOT / "simulator.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/sim/state":
            self.respond(200, self.server.state.snapshot())
        elif re.fullmatch(r"/sim/download/\d+", path):
            file_id = path.rsplit("/", 1)[1]
            file = self.server.state.files.get(file_id)
            persisted = self.server.state.folder / "downloads" / f"{file_id}.zip" if self.server.state.folder else None
            if file or (persisted and persisted.exists()):
                self.respond(200, file[1] if file else persisted.read_bytes(), "application/zip")
            else:
                self.respond(404, {"error": "File not found"})
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self):
        state = self.server.state
        origin = self.headers.get("Origin")
        if origin and origin != self.server.url:
            self.respond(403, {"error": "Use the local simulator page."})
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 0:
                raise ValueError
        except ValueError:
            self.close_connection = True
            self.respond(400, {"error": "Invalid Content-Length"})
            return
        if size > 20 * 1024 * 1024:
            self.respond(413, {"error": "Request too large"})
            return
        raw = self.rfile.read(size)
        try:
            upload = None
            if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                mail = BytesParser(policy=policy.default).parsebytes(
                    f'Content-Type: {self.headers["Content-Type"]}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + raw)
                payload = {}
                for part in mail.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    content = part.get_payload(decode=True)
                    if part.get_filename():
                        upload = (part.get_filename(), content)
                    else:
                        payload[name] = content.decode("utf-8")
            else:
                payload = json.loads(raw or b"{}")
            if self.path == "/sim/message":
                result = state.send_user(payload["text"], int(payload.get("uid", OWNER)),
                                         int(payload.get("chat_id", GROUP)), payload.get("reply_id"))
            elif self.path == "/sim/click":
                result = state.click(int(payload["message_id"]), payload["data"],
                                     int(payload.get("uid", OWNER)), int(payload.get("chat_id", GROUP)))
            elif self.path == "/sim/fail":
                if payload["mode"] not in {"500", "429", "disconnect_before", "disconnect_after"}:
                    raise APIError(400, "Unknown failure mode")
                state.fail_next(payload["method"], payload["mode"])
                result = True
            elif self.path.startswith(f"/bot{LOCAL_TEST_TOKEN}/"):
                method = self.path.rsplit("/", 1)[1]
                with state.condition:
                    modes = state.failures.get(method, [])
                    failure = modes.pop(0) if modes else None
                    state.requests.append({'method': method, 'payload': copy.deepcopy(payload),
                                           'failure': failure})
                if failure in {"500", "429"}:
                    raise APIError(int(failure), "Simulated temporary failure")
                if failure == "disconnect_before":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                result = state.dispatch(method, payload, upload)
                if failure == "disconnect_after":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.respond(200, {"ok": True, "result": result})
                return
            else:
                raise APIError(404, "Unknown simulator endpoint")
            self.respond(200, {"ok": True, "result": result})
        except APIError as exc:
            self.respond(exc.code, {"ok": False, "error_code": exc.code, "description": exc.text,
                                   "parameters": {"retry_after": 1} if exc.code == 429 else {}})
        except (ValueError, KeyError, TypeError) as exc:
            self.respond(400, {"ok": False, "error_code": 400, "description": f"Invalid request: {exc}"})


class BotWorker:
    def __init__(self, folder, server, *, fixture="paired", seed=True):
        self.folder, self.server = Path(folder), server
        self.bot = None
        self.error = None
        self.ready = threading.Event()
        self.stop_requested = threading.Event()
        store = Store(self.folder)
        try:
            if not store.get("simulator_fixture"):
                if seed:
                    store.import_tasks(json.loads((ROOT / "seed_tasks.json").read_text("utf-8")))
                else:
                    store.set("seed_imported", True)
                if fixture in {"paired", "owner"}:
                    store.add_member(OWNER, USERS[OWNER], "owner")
                if fixture == "paired":
                    store.add_member(PARTNER, USERS[PARTNER])
                    store.set("chat_id", GROUP)
                if fixture == "unpaired":
                    store.set("owner_claim", {"hash": digest("owner_local_test"), "expires": time.time() + 86400})
                store.set("simulator_fixture", True)
        finally:
            store.close()
        self.thread = threading.Thread(target=self.run, name="local-bot", daemon=True)

    def run(self):
        try:
            with process_lock(self.folder / "bot.lock"):
                store = Store(self.folder)
                try:
                    self.bot = Bot(store, Telegram(LOCAL_TEST_TOKEN, api_base=self.server.url),
                                   "local_home_bot", self.folder)
                    self.ready.set()
                    if not self.stop_requested.is_set():
                        self.bot.run()
                finally:
                    store.close()
        except Exception as exc:
            self.error = str(exc)
            self.server.state.worker_error = self.error
            self.ready.set()

    def start(self):
        self.thread.start()
        if not self.ready.wait(15) or self.error:
            raise RuntimeError(self.error or "Local bot did not start")

    def stop(self):
        self.stop_requested.set()
        if self.bot:
            self.bot.running = False
        with self.server.state.condition:
            self.server.state.condition.notify_all()
        # Telegram uploads can take 20 seconds; let bounded requests and saved
        # exports finish before a restart tries to acquire the same bot lock.
        self.thread.join(30)
        if self.thread.is_alive():
            raise RuntimeError("Local bot did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--fresh", action="store_true", help="Use a new timestamped test folder")
    args = parser.parse_args()
    from bot_center_runtime.paths import installation_root
    work = installation_root() / 'work/simulator/home'
    folder = work / (f"run-{time.time_ns()}" if args.fresh else "demo")
    stop_file = work / "stop.request"
    stop_file.unlink(missing_ok=True)
    state = TelegramState(folder / "server")
    server = SimulatorServer(state, args.port)
    worker = BotWorker(folder / "bot", server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    worker.start()
    print(f"Local Telegram simulator: {server.url}", flush=True)
    print(f"Synthetic test files: {folder}", flush=True)
    try:
        while worker.thread.is_alive() and not stop_file.exists():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        state.closing = True
        server.shutdown()
        server.server_close()
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
