"""Durable, dependency-free storage for the two-person Our Home bot.

Public exports use schema_version=1 and include every task, including trash.
Backups contain a sanitized SQLite snapshot plus JSON/TXT/HTML. Restores import
validated JSON only; they never open or execute an uploaded SQLite database.
Only explicitly allowed list settings travel in backups.
Restoring atomically replaces members/tasks/settings, drops pending actions,
preserves data/config.json and same-chat panel identity, and creates no backup.
"""

from __future__ import annotations

import html
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import tempfile
import time
from datetime import datetime, timezone
from typing import Any
import uuid
import zipfile
from contextlib import contextmanager


CATEGORY_LABELS = {
    "shopping": "Groceries",
    "household": "Home shopping",
    "admin": "Admin & personal",
    "home": "Home tasks",
    "study": "Languages & study",
}
CATEGORIES = tuple(CATEGORY_LABELS)
SAFE_SETTINGS = frozenset({"chat_id", "title", "timezone", "seed_imported", "custom_categories", "removed_categories"})
SCHEMA_VERSION = 1
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_BACKUP_BYTES = 100 * 1024 * 1024
MAX_TASKS = 100_000
MAX_INT = (1 << 63) - 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if not minimum <= value <= MAX_INT:
        raise ValueError(f"{label} is outside the allowed range")
    return value


def _text(value: Any, label: str, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    value = value.strip()
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label} is too long or contains a null character")
    return value


def _category(value: Any, categories=CATEGORIES) -> str:
    if not isinstance(value, str) or value not in categories:
        raise ValueError("Choose an existing category")
    return value


def _category_name(value: Any) -> str:
    name = _text(value, "Category name", 24)
    if any(ord(c) < 32 for c in name):
        raise ValueError("Use one line for the category name")
    return " ".join(name.split())


def _custom_categories(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 45:
        raise ValueError("At most 45 custom categories are allowed")
    result, keys, names = [], set(CATEGORIES), {v.casefold() for v in CATEGORY_LABELS.values()}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Invalid custom category")
        key, label = item.get("key"), _category_name(item.get("label"))
        if not isinstance(key, str) or not re.fullmatch(r"custom_[a-f0-9]{12}", key):
            raise ValueError("Invalid category key")
        if key in keys or label.casefold() in names:
            raise ValueError("Duplicate category")
        keys.add(key)
        names.add(label.casefold())
        result.append({"key": key, "label": label})
    return result


def _source_key(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value, "source_key", 512)


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must be an ISO UTC timestamp")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _replace_with_retry(source, destination) -> None:
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            # Windows viewers and file scanners can briefly hold either file.
            time.sleep(min(0.05 * (2 ** attempt), 0.5))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AtomicUpdateConnection(sqlite3.Connection):
    """Keep legacy per-method transactions inside one update commit boundary."""
    _managed_update = False
    _scopes = None

    def __enter__(self):
        if self._managed_update:
            if self._scopes is None:self._scopes=[]
            name='update_scope_'+str(len(self._scopes))
            super().execute('SAVEPOINT '+name);self._scopes.append(name)
            return self
        return super().__enter__()

    def __exit__(self, kind, value, traceback):
        if self._managed_update:
            name=self._scopes.pop()
            if kind is not None:super().execute('ROLLBACK TO '+name)
            super().execute('RELEASE '+name)
            return False
        return super().__exit__(kind,value,traceback)

    def execute(self, sql, parameters=(), /):
        if self._managed_update and sql.strip().upper().startswith('BEGIN'):
            return super().execute('SELECT 1')
        return super().execute(sql,parameters)

    def rollback(self):
        if self._managed_update and self._scopes:
            super().execute('ROLLBACK TO '+self._scopes[-1])
        else:super().rollback()


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "tasks.sqlite3"
        self.exports_dir = self.data_dir / "exports"
        self.backups_dir = self.data_dir / "backups"
        self.delivery_path=self.data_dir/'delivery-intentions.json'
        self._current_update_id=None
        if self.db_path.exists():
            self._validate_existing_database()
        self.conn = sqlite3.connect(str(self.db_path), timeout=10, factory=AtomicUpdateConnection)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','member'))
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
                    deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
                    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    updated_by INTEGER NOT NULL,
                    source_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS tasks_visible
                    ON tasks(category,deleted,done,id);
            """)
            if self.get("_data_revision") is None:
                self._set_value("_data_revision", 0)
        self._upgrade_categories()
        self._recover_delivery_intentions()

    def _validate_existing_database(self) -> None:
        """An existing namespace must be valid before any writable open."""
        message='Existing Home database is invalid or incomplete; restore a verified set instead of initializing it.'
        try:
            connection=sqlite3.connect(self.db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
            try:
                if connection.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                    raise ValueError(message)
                tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'meta','members','tasks'}<=tables:raise ValueError(message)
                # Older fixed-category schemas retain the same columns and are
                # upgraded by the existing transaction after this preflight.
                connection.execute('SELECT key,value FROM meta LIMIT 0')
                connection.execute('SELECT user_id,name,role FROM members LIMIT 0')
                connection.execute('SELECT id,category,title,done,deleted,revision,created_at,updated_at,created_by,updated_by,source_key FROM tasks LIMIT 0')
            finally:connection.close()
        except sqlite3.DatabaseError as error:
            if (getattr(error,'sqlite_errorcode',0)&255) in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:raise
            raise ValueError(message) from None

    def _upgrade_categories(self) -> None:
        definition = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
        if not re.search(r"CHECK\s*\(\s*category\s+IN", definition, re.IGNORECASE):
            return
        # Remove the fixed-category constraint in one SQLite transaction. The
        # original table survives a rollback; no unsolicited ZIP is generated.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            sequence = self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name='tasks'").fetchone()
            high_water = int(sequence[0]) if sequence else 0
            self.conn.execute("""
                CREATE TABLE tasks_expanded (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0,1)),
                    deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
                    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    created_by INTEGER NOT NULL, updated_by INTEGER NOT NULL,
                    source_key TEXT UNIQUE
                )
            """)
            self.conn.execute("INSERT INTO tasks_expanded SELECT * FROM tasks")
            self.conn.execute("DROP TABLE tasks")
            self.conn.execute("ALTER TABLE tasks_expanded RENAME TO tasks")
            self.conn.execute("CREATE INDEX tasks_visible ON tasks(category,deleted,done,id)")
            cursor = self.conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='tasks'", (high_water,))
            if not cursor.rowcount:
                self.conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('tasks',?)", (high_water,))

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def durable_update(self, update_id):
        """Commit update identity with all its required local mutations.

        Telegram acknowledges only on the *next* getUpdates offset. A process
        crash before this transaction commits therefore safely replays it.
        """
        if update_id <= int(self.get('last_update_id', -1)):
            yield False
            return
        self.conn.execute('BEGIN IMMEDIATE')
        self.conn._managed_update=True;self.conn._scopes=[]
        self._current_update_id=update_id
        try:
            yield True
            self._set_value('last_update_id',update_id)
            sqlite3.Connection.commit(self.conn)
            # SQLite now owns either the completed panel ID or its unresolved
            # send marker. Removing the auxiliary intention is safe only now.
            self.delivery_path.unlink(missing_ok=True)
        except BaseException:
            sqlite3.Connection.rollback(self.conn)
            self.conn._managed_update=False;self.conn._scopes=[]
            self._recover_delivery_intentions()
            raise
        finally:
            self.conn._managed_update=False;self.conn._scopes=[]
            self._current_update_id=None

    def _recover_delivery_intentions(self):
        if not self.delivery_path.exists():return
        records=json.loads(self.delivery_path.read_text('utf-8'))
        with self.conn:
            for key,record in records.items():
                update=record.get('update_id')
                panel=key.removeprefix('panel_send_')
                acknowledged=int(self.get('last_update_id',-1))
                # If the whole update committed, SQLite has final truth. A
                # pre-commit crash must retain the uncertain remote intention.
                if update is not None and int(update)<=acknowledged:continue
                if update is None and self.get(panel) and not self.get(key):continue
                self._set_value(key,record['pending'])
                self._set_value(panel,None)
                for context_key,value in record.get('context',{}).items():self._set_value(context_key,value)
        self.delivery_path.unlink(missing_ok=True)

    def _record_delivery_intention(self,key,value):
        records=json.loads(self.delivery_path.read_text('utf-8')) if self.delivery_path.exists() else {}
        if value is None:
            if self.conn._managed_update:return
            records.pop(key,None)
        else:
            context={row['key']:json.loads(row['value']) for row in self.conn.execute("SELECT key,value FROM meta WHERE key LIKE 'pending_%' OR key LIKE 'panel_state_%'")}
            records[key]={'update_id':self._current_update_id,'pending':value,'context':context}
        if records:_atomic_write(self.delivery_path,_json(records))
        else:self.delivery_path.unlink(missing_ok=True)

    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def _set_value(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _json(value)),
        )

    def set(self, key: str, value: Any) -> None:
        key = _text(key, "Metadata key", 200)
        encoded = _json(value)
        if key.startswith('panel_send_') and value is not None:
            # Seal the uncertain delivery BEFORE issuing any remote send.
            self._record_delivery_intention(key,value)
        with self.conn:
            old = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if old is not None and old["value"] == encoded:
                return
            self._set_value(key, value)
            if key in SAFE_SETTINGS:
                self._bump_revision()
        if key.startswith('panel_send_') and value is None:
            self._record_delivery_intention(key,None)

    def revision(self) -> int:
        return int(self.get("_data_revision", 0))

    def categories(self, include_removed=False) -> dict[str, str]:
        categories = {**CATEGORY_LABELS, **{c["key"]: c["label"] for c in self.get("custom_categories", [])}}
        removed = set() if include_removed else {c['key'] for c in self.get('removed_categories', [])}
        return {key: label for key, label in categories.items() if key not in removed}

    def category_revision(self, category: str) -> int:
        """Generation for category removal controls; old buttons cannot remove again."""
        return int(self.get('category_revision_' + category, 0))

    def remove_category(self, category: str, actor_id: int,
                        expected_revision: int | None = None) -> dict | None:
        actor_id = _integer(actor_id, 'Actor ID')
        if expected_revision is not None:
            expected_revision = _integer(expected_revision, 'Expected category revision')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            category = _category(category, self.categories(include_removed=True))
            if expected_revision is not None and self.category_revision(category) != expected_revision:
                return None
            removed = self.get('removed_categories', [])
            existing = next((c for c in removed if c['key'] == category), None)
            if existing:
                return existing
            ids = [r[0] for r in self.conn.execute(
                'SELECT id FROM tasks WHERE category=? AND deleted=0 ORDER BY id', (category,))]
            entry = {'key': category, 'token': uuid.uuid4().hex[:12], 'task_ids': ids}
            self.conn.execute('UPDATE tasks SET deleted=1,revision=revision+1,updated_at=?,updated_by=? '
                              'WHERE category=? AND deleted=0', (_now(), actor_id, category))
            self._set_value('removed_categories', removed + [entry])
            self._set_value('category_revision_' + category, self._bump_revision())
        return entry

    def restore_category(self, category: str, token: str, actor_id: int) -> bool:
        actor_id = _integer(actor_id, 'Actor ID')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            removed = self.get('removed_categories', [])
            entry = next((c for c in removed if c['key'] == category and c['token'] == token), None)
            if entry is None:
                return False
            self.conn.executemany('UPDATE tasks SET deleted=0,revision=revision+1,updated_at=?,updated_by=? '
                                  'WHERE id=? AND category=? AND deleted=1',
                                 [(_now(), actor_id, tid, category) for tid in entry['task_ids']])
            self._set_value('removed_categories', [c for c in removed if c['key'] != category])
            self._set_value('category_revision_' + category, self._bump_revision())
        return True

    def add_category(self, label: str, actor_id: int, source_key: str | None = None) -> str:
        label = _category_name(label)
        _integer(actor_id, "Actor ID")
        source_key = _source_key(source_key)
        receipt = "category_source_" + hashlib.sha256(source_key.encode()).hexdigest() if source_key else None
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if receipt and self.get(receipt):
                return self.get(receipt)
            if label.casefold() in {name.casefold() for name in self.categories(include_removed=True).values()}:
                raise ValueError("That category already exists. Choose a different name.")
            key = "custom_" + uuid.uuid4().hex[:12]
            categories = self.get("custom_categories", []) + [{"key": key, "label": label}]
            self._set_value("custom_categories", _custom_categories(categories))
            if receipt:
                self._set_value(receipt, key)
            self._set_value('category_revision_' + key, self._bump_revision())
        return key

    def _bump_revision(self) -> int:
        value = self.revision() + 1
        self._set_value("_data_revision", value)
        return value

    def list_members(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT user_id,name,role FROM members "
            "ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END,user_id"
        )]

    def add_member(self, user_id: int, name: str, role: str = "member") -> bool:
        """Add a member, or refresh an existing name. Return True only if added."""
        user_id = _integer(user_id, "User ID", 1)
        name = _text(name, "Member name", 256)
        if role not in ("owner", "member"):
            raise ValueError("Member role must be owner or member")
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            existing = self.conn.execute(
                "SELECT name FROM members WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing is not None:
                if existing["name"] != name:
                    self.conn.execute("UPDATE members SET name=? WHERE user_id=?", (name, user_id))
                    self._bump_revision()
                return False
            if self.conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] >= 2:
                raise ValueError("This shared list already has two members")
            if role == "owner" and self.conn.execute(
                "SELECT 1 FROM members WHERE role='owner'"
            ).fetchone():
                raise ValueError("This shared list already has an owner")
            self.conn.execute(
                "INSERT INTO members(user_id,name,role) VALUES(?,?,?)", (user_id, name, role)
            )
            self._bump_revision()
            return True

    def authorized(self, user_id: int) -> bool:
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            return False
        if not 0 < user_id <= MAX_INT:
            return False
        return self.conn.execute("SELECT 1 FROM members WHERE user_id=?", (user_id,)).fetchone() is not None

    def add_task(self, category: str, title: str, actor_id: int,
                 source_key: str | None = None) -> int:
        return self._add_tasks(category, [title], actor_id, [source_key])[0]

    def add_tasks(self, category: str, titles: list[str], actor_id: int,
                  source_prefix: str | None = None) -> list[int]:
        """Commit a complete input batch once, or roll back every new item."""
        if not isinstance(titles, list) or not 1 <= len(titles) <= 20:
            raise ValueError('Use 1-20 task titles')
        source_prefix = _source_key(source_prefix)
        sources = [f'{source_prefix}:{index}' if source_prefix is not None else None
                   for index in range(len(titles))]
        return self._add_tasks(category, titles, actor_id, sources)

    def _add_tasks(self, category, titles, actor_id, sources):
        titles = [_text(title, 'Task title') for title in titles]
        actor_id = _integer(actor_id, "Actor ID")
        sources = [_source_key(source) for source in sources]
        now = _now()
        ids = []
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            category = _category(category, self.categories())
            for title, source_key in zip(titles, sources):
                if source_key is not None:
                    existing = self.conn.execute(
                        "SELECT id FROM tasks WHERE source_key=?", (source_key,)
                    ).fetchone()
                    if existing is not None:
                        ids.append(int(existing['id']))
                        continue
                cursor = self.conn.execute(
                    "INSERT INTO tasks(category,title,created_at,updated_at,created_by,updated_by,source_key) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (category, title, now, now, actor_id, actor_id, source_key),
                )
                self._bump_revision()
                ids.append(int(cursor.lastrowid))
        return ids

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["done"] = bool(result["done"])
        result["deleted"] = bool(result["deleted"])
        return result

    def task(self, task_id: int) -> dict[str, Any] | None:
        task_id = _integer(task_id, "Task ID", 1)
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return None if row is None else self._task_dict(row)

    def list_tasks(self, category: str | None = None, mode: str = "open") -> list[dict[str, Any]]:
        """all means all non-deleted tasks; trash returns deleted tasks only."""
        conditions = {
            "open": "deleted=0 AND done=0", "done": "deleted=0 AND done=1",
            "all": "deleted=0", "trash": "deleted=1",
        }
        if mode not in conditions:
            raise ValueError("Mode must be open, done, all or trash")
        where = conditions[mode]
        parameters: tuple[Any, ...] = ()
        if category not in (None, "all"):
            category = _category(category, self.categories())
            where += " AND category=?"
            parameters = (category,)
        return [self._task_dict(row) for row in self.conn.execute(
            f"SELECT * FROM tasks WHERE {where} ORDER BY id", parameters
        )]

    def counts(self) -> dict[str, dict[str, int]]:
        result = {key: {"open": 0, "done": 0, "trash": 0} for key in (*self.categories(include_removed=True), "all")}
        for row in self.conn.execute(
            "SELECT category,done,deleted,COUNT(*) AS amount FROM tasks GROUP BY category,done,deleted"
        ):
            state = "trash" if row["deleted"] else ("done" if row["done"] else "open")
            result[row["category"]][state] += row["amount"]
            result["all"][state] += row["amount"]
        return result

    def change_task(self, task_id: int, action: str, expected_revision: int,
                    actor_id: int) -> bool:
        task_id = _integer(task_id, "Task ID", 1)
        expected_revision = _integer(expected_revision, "Expected revision", 1)
        actor_id = _integer(actor_id, "Actor ID")
        transitions = {
            "complete": ("done=1", "deleted=0 AND done=0"),
            "reopen": ("done=0", "deleted=0 AND done=1"),
            "delete": ("deleted=1", "deleted=0"),
            "restore": ("deleted=0", "deleted=1"),
        }
        if action not in transitions:
            raise ValueError("Unknown task action")
        assignment, condition = transitions[action]
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            task = self.task(task_id)
            if task and task['category'] not in self.categories():
                return False
            cursor = self.conn.execute(
                f"UPDATE tasks SET {assignment},revision=revision+1,updated_at=?,updated_by=? "
                f"WHERE id=? AND revision=? AND {condition}",
                (_now(), actor_id, task_id, expected_revision),
            )
            if cursor.rowcount != 1:
                return False
            self._bump_revision()
            return True

    def change_tasks(self, category: str | None, action: str,
                     expected_revisions: dict[int, int], actor_id: int,
                     source_key: str, *, require_all: bool = False) -> list[dict] | None:
        """Atomically delete/restore a selection, or reject the whole conflict.

        A category of None permits a selection across active categories.
        The source receipt is committed with the task changes. An identical
        request returns its original result even if those tasks were restored
        or changed later; reusing the key for a different request is invalid.
        """
        if category is not None and (not isinstance(category, str) or not category.strip()
                                     or len(category) > 200 or '\x00' in category):
            raise ValueError('Invalid batch category')
        if not isinstance(action, str) or action not in {'delete', 'restore'}:
            raise ValueError('Batch action must be delete or restore')
        if require_all and (category is None or action != 'delete'):
            raise ValueError('Clearing requires one category and the delete action')
        if (not isinstance(expected_revisions, dict) or not expected_revisions
                or len(expected_revisions) > MAX_TASKS):
            raise ValueError('Select one or more tasks with their revisions')
        requested = sorted([
            [_integer(tid, 'Task ID', 1), _integer(revision, 'Expected revision', 1)]
            for tid, revision in expected_revisions.items()
        ])
        actor_id = _integer(actor_id, 'Actor ID')
        source_key = _text(source_key, 'source_key', 512)
        receipt_key = 'batch_source_' + hashlib.sha256(source_key.encode('utf-8')).hexdigest()
        request = {'category': category, 'action': action, 'actor_id': actor_id,
                   'expected_revisions': requested}
        if require_all:
            request['require_all'] = True
        result = [{'id': tid, 'revision': revision + 1} for tid, revision in requested]
        old_deleted, new_deleted = (0, 1) if action == 'delete' else (1, 0)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            receipt = self.get(receipt_key)
            if receipt is not None:
                if (not isinstance(receipt, dict) or receipt.get('request') != request
                        or receipt.get('result') != result):
                    raise ValueError('This batch source key belongs to a different request')
                return receipt['result']
            active_categories = self.categories()
            if category is not None and category not in active_categories:
                return None
            if require_all:
                current = {row['id'] for row in self.conn.execute(
                    'SELECT id FROM tasks WHERE category=? AND deleted=0', (category,))}
                if current != {tid for tid, _ in requested}:
                    return None
            # Chunk parameter lists so the contract is independent of SQLite's
            # compiled variable limit. UI selections normally fit in one chunk.
            actual = {}
            for offset in range(0, len(requested), 500):
                ids = [tid for tid, _ in requested[offset:offset + 500]]
                placeholders = ','.join('?' for _ in ids)
                rows = self.conn.execute(
                    f'SELECT id,category,revision,deleted FROM tasks WHERE id IN ({placeholders})', ids)
                actual.update((row['id'], row) for row in rows)
            for tid, revision in requested:
                task = actual.get(tid)
                if (task is None or task['category'] not in active_categories
                        or (category is not None and task['category'] != category)
                        or task['revision'] != revision or task['deleted'] != old_deleted):
                    return None
            now = _now()
            cursor = self.conn.executemany(
                'UPDATE tasks SET deleted=?,revision=revision+1,updated_at=?,updated_by=? '
                'WHERE id=? AND category=? AND revision=? AND deleted=?',
                [(new_deleted, now, actor_id, tid, actual[tid]['category'], revision, old_deleted)
                 for tid, revision in requested])
            if cursor.rowcount != len(requested):
                # A database trigger could change another selected row even
                # while external writers are excluded by BEGIN IMMEDIATE.
                self.conn.rollback()
                return None
            self._set_value(receipt_key, {'request': request, 'result': result})
            self._bump_revision()
        return result

    def uncheck_all(self, actor_id: int) -> int:
        """Reset completion only; preserve titles, categories, IDs, and Trash."""
        actor_id = _integer(actor_id, "Actor ID")
        with self.conn:
            changed = self.conn.execute(
                "UPDATE tasks SET done=0,revision=revision+1,updated_at=?,updated_by=? WHERE done=1",
                (_now(), actor_id),
            ).rowcount
            if changed:
                self._bump_revision()
            return changed

    def import_tasks(self, items: list[dict[str, Any]]) -> int:
        """Seed items once, atomically. Returns zero after the first import."""
        if self.get("seed_imported", False):
            return 0
        if not isinstance(items, list) or len(items) > MAX_TASKS:
            raise ValueError("Invalid seed task list")
        now = _now()
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if self.get("seed_imported", False):
                return 0
            categories = self.categories()
            validated = [(_category(item.get("category"), categories), _text(item.get("title"), "Task title"))
                         for item in items if isinstance(item, dict)]
            if len(validated) != len(items):
                raise ValueError("Each seed task must be an object")
            self.conn.executemany(
                "INSERT INTO tasks(category,title,created_at,updated_at,created_by,updated_by) "
                "VALUES(?,?,?,?,0,0)",
                [(category, title, now, now) for category, title in validated],
            )
            self._set_value("seed_imported", True)
            self._bump_revision()
        return len(validated)

    def _snapshot(self, connection=None) -> dict[str, Any]:
        # SELECTs do not begin a transaction automatically. A read savepoint
        # keeps settings, revision and tasks on one WAL snapshot while a second
        # connection continues to handle user changes.
        connection = self.conn if connection is None else connection
        connection.execute('SAVEPOINT readable_snapshot')
        try:
            settings = {}
            for key in sorted(SAFE_SETTINGS):
                row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                if row is not None:
                    value = json.loads(row['value'])
                    if value is not None:
                        settings[key] = value
            revision = connection.execute("SELECT value FROM meta WHERE key='_data_revision'").fetchone()
            return {
                "schema_version": SCHEMA_VERSION,
                "exported_at": _now(),
                "revision": int(json.loads(revision['value'])) if revision else 0,
                "members": [dict(row) for row in connection.execute(
                    "SELECT user_id,name,role FROM members "
                    "ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END,user_id")],
                "settings": settings,
                "tasks": [self._task_dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY id")],
            }
        finally:
            connection.execute('RELEASE SAVEPOINT readable_snapshot')

    @staticmethod
    def _render(snapshot: dict[str, Any]) -> dict[str, str]:
        title = str(snapshot["settings"].get("title") or "Our Home")
        lines = [title, f"Saved: {snapshot['exported_at']}", f"Revision: {snapshot['revision']}", ""]
        sections = []
        categories = {**CATEGORY_LABELS, **{c["key"]: c["label"] for c in snapshot["settings"].get("custom_categories", [])}}
        removed = snapshot['settings'].get('removed_categories', [])
        removed_keys = {c['key'] for c in removed}
        for category, label in categories.items():
            if category in removed_keys:
                continue
            tasks = [task for task in snapshot["tasks"] if task["category"] == category and not task["deleted"]]
            lines.append(label.upper())
            rows = []
            for task in tasks:
                marker = "x" if task["done"] else " "
                lines.append(f"[{marker}] #{task['id']} {task['title']}")
                rows.append("<li>" + ("&#9745; " if task["done"] else "&#9744; ") +
                            f"<span dir='auto'>{html.escape(task['title'])}</span></li>")
            if not tasks:
                lines.append("(empty)")
                rows.append("<li class='empty'>No tasks</li>")
            lines.append("")
            sections.append(f"<section><h2>{html.escape(label)}</h2><ul>{''.join(rows)}</ul></section>")
        deleted = [task for task in snapshot["tasks"] if task["deleted"]]
        lines.append("TRASH (restorable)")
        lines.extend(f"[removed category] {categories[c['key']]}" for c in removed)
        lines.extend(f"[deleted] #{task['id']} {task['title']}" for task in deleted)
        if not deleted and not removed:
            lines.append("(empty)")
        trash = "".join(f"<li><span dir='auto'>{html.escape(task['title'])}</span></li>" for task in deleted)
        trash = ''.join('<li>Category: ' + html.escape(categories[c['key']]) + '</li>' for c in removed) + trash
        if trash:
            sections.append(f"<section><h2>Trash (restorable)</h2><ul>{trash}</ul></section>")
        document = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{html.escape(title)}</title><style>"
            "body{font:16px system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 18px;color:#192e38;background:#f5f8f7}"
            "section{background:white;border:1px solid #dfe7e3;border-radius:12px;padding:14px 20px;margin:18px 0}"
            "h1{font-size:26px}h2{font-size:18px}ul{list-style:none;padding:0}li{padding:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}"
            ".meta,.empty{color:#687b75;font-size:14px}</style></head><body>"
            f"<h1 dir='auto'>{html.escape(title)}</h1>"
            f"<p class='meta'>Saved: {html.escape(snapshot['exported_at'])}<br>Revision: {snapshot['revision']}</p>"
            + "".join(sections) + "</body></html>\n"
        )
        return {"json": json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                "txt": "\n".join(lines) + "\n", "html": document}

    def export_latest(self) -> dict[str, Path]:
        content = self._render(self._snapshot())
        paths = {extension: self.exports_dir / f"tasks-latest.{extension}" for extension in content}
        for extension, path in paths.items():
            _atomic_write(path, content[extension])
        return paths

    def create_backup(self) -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        destination = self.backups_dir / f"our-home-{stamp}-{uuid.uuid4().hex[:6]}.zip"
        with tempfile.TemporaryDirectory(prefix=".backup-", dir=str(self.backups_dir)) as temporary:
            folder = Path(temporary)
            database_path = folder / "tasks.sqlite3"
            backup_db = sqlite3.connect(str(database_path))
            backup_db.row_factory = sqlite3.Row
            try:
                self.conn.backup(backup_db)
                backup_db.execute("PRAGMA secure_delete=ON")
                allowed = tuple(sorted(SAFE_SETTINGS | {"_data_revision"}))
                placeholders = ",".join("?" for _ in allowed)
                with backup_db:
                    backup_db.execute(f"DELETE FROM meta WHERE key NOT IN ({placeholders})", allowed)
                backup_db.execute("VACUUM")
                # All four files describe the exact copied database, even when
                # live tasks change while the ZIP is being rendered/written.
                snapshot = self._snapshot(backup_db)
                content = self._render(snapshot)
            finally:
                backup_db.close()
            temporary_zip = folder / "backup.zip"
            with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(database_path, "tasks.sqlite3")
                for extension, value in content.items():
                    archive.writestr(f"tasks.{extension}", value.encode("utf-8"))
            with temporary_zip.open("r+b") as handle:
                os.fsync(handle.fileno())
            _replace_with_retry(temporary_zip, destination)
        return destination

    @staticmethod
    def _read_backup(path: Path) -> Any:
        if not path.is_file():
            raise ValueError("Backup file does not exist")
        if path.stat().st_size > MAX_BACKUP_BYTES:
            raise ValueError("Backup is too large")
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if len(infos) > 20 or sum(info.file_size for info in infos) > MAX_BACKUP_BYTES:
                    raise ValueError("Backup contains too much data")
                names = set()
                for info in infos:
                    name = info.filename
                    member_path = PurePosixPath(name)
                    if ("\\" in name or ":" in name or member_path.is_absolute()
                            or ".." in member_path.parts or name in names
                            or stat.S_ISLNK(info.external_attr >> 16)):
                        raise ValueError("Unsafe or duplicate path in backup")
                    names.add(name)
                    if info.flag_bits & 1:
                        raise ValueError("Encrypted ZIP archives are not supported")
                if "tasks.json" not in names:
                    raise ValueError("Backup is missing tasks.json")
                if archive.getinfo("tasks.json").file_size > MAX_JSON_BYTES:
                    raise ValueError("Task export is too large")
                raw = archive.read("tasks.json")
        else:
            if path.stat().st_size > MAX_JSON_BYTES:
                raise ValueError("Task export is too large")
            raw = path.read_bytes()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Backup does not contain valid UTF-8 JSON") from exc

    @staticmethod
    def _validate_snapshot(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported backup schema")
        revision = _integer(data.get("revision"), "Data revision")
        if revision >= MAX_INT:
            raise ValueError("Data revision is too large")
        raw_members = data.get("members")
        if not isinstance(raw_members, list) or len(raw_members) > 2:
            raise ValueError("Backup must have at most two members")
        members, member_ids = [], set()
        for item in raw_members:
            if not isinstance(item, dict):
                raise ValueError("Invalid backup member")
            user_id = _integer(item.get("user_id"), "User ID", 1)
            name = _text(item.get("name"), "Member name", 256)
            role = item.get("role")
            if user_id in member_ids or role not in ("owner", "member"):
                raise ValueError("Invalid or duplicate backup member")
            members.append({"user_id": user_id, "name": name, "role": role})
            member_ids.add(user_id)
        if sum(member["role"] == "owner" for member in members) > 1:
            raise ValueError("Backup contains multiple owners")
        raw_settings = data.get("settings", {})
        if not isinstance(raw_settings, dict):
            raise ValueError("Invalid backup settings")
        settings = {}
        for key in SAFE_SETTINGS:
            if key not in raw_settings:
                continue
            value = raw_settings[key]
            if key == "chat_id":
                if isinstance(value, bool) or not isinstance(value, int) or not -MAX_INT <= value <= MAX_INT or value == 0:
                    raise ValueError("Invalid backup chat_id")
            elif key == "seed_imported":
                if not isinstance(value, bool):
                    raise ValueError("Invalid backup seed_imported")
            elif key == "custom_categories":
                value = _custom_categories(value)
            elif key == "removed_categories":
                if not isinstance(value, list) or len(value) > 50:
                    raise ValueError('Invalid removed categories')
            else:
                value = _text(value, key, 256)
            settings[key] = value
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list) or len(raw_tasks) > MAX_TASKS:
            raise ValueError("Invalid backup task list")
        tasks, task_ids, sources = [], set(), set()
        categories = {*CATEGORIES, *(c["key"] for c in settings.get("custom_categories", []))}
        for item in raw_tasks:
            if not isinstance(item, dict):
                raise ValueError("Invalid backup task")
            task_id = _integer(item.get("id"), "Task ID", 1)
            if task_id in task_ids:
                raise ValueError("Duplicate task ID in backup")
            task_ids.add(task_id)
            source_key = _source_key(item.get("source_key"))
            if source_key is not None:
                if source_key in sources:
                    raise ValueError("Duplicate source_key in backup")
                sources.add(source_key)
            done, deleted = item.get("done"), item.get("deleted")
            if not isinstance(done, bool) or not isinstance(deleted, bool):
                raise ValueError("Task states must be true or false")
            task_revision = _integer(item.get("revision"), "Task revision", 1)
            if task_revision >= MAX_INT:
                raise ValueError("Task revision is too large")
            task = {
                "id": task_id, "category": _category(item.get("category"), categories),
                "title": _text(item.get("title"), "Task title"),
                "done": done, "deleted": deleted, "revision": task_revision,
                "created_at": _timestamp(item.get("created_at"), "created_at"),
                "updated_at": _timestamp(item.get("updated_at"), "updated_at"),
                "created_by": _integer(item.get("created_by", 0), "Created by"),
                "updated_by": _integer(item.get("updated_by", 0), "Updated by"),
                "source_key": source_key,
            }
            tasks.append(task)
        removed_keys, removed_ids = set(), set()
        task_map = {t['id']: t for t in tasks}
        for entry in settings.get('removed_categories', []):
            if not isinstance(entry, dict):
                raise ValueError('Invalid removed category')
            key = _category(entry.get('key'), categories)
            token, ids = entry.get('token'), entry.get('task_ids')
            if (key in removed_keys or not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{12}', token)
                    or not isinstance(ids, list) or len(ids) > MAX_TASKS):
                raise ValueError('Invalid removed category')
            removed_keys.add(key)
            for tid in ids:
                _integer(tid, 'Removed task ID', 1)
                task = task_map.get(tid)
                if tid in removed_ids or not task or task['category'] != key or not task['deleted']:
                    raise ValueError('Invalid removed category tasks')
                removed_ids.add(tid)
        if any(t['category'] in removed_keys and not t['deleted'] for t in tasks):
            raise ValueError('Removed categories cannot contain active tasks')
        return {"revision": revision, "members": members, "settings": settings, "tasks": tasks}

    def restore_backup(self, zip_or_json: Path) -> None:
        """Validate first, then atomically replace data without creating a ZIP."""
        snapshot = self._validate_snapshot(self._read_backup(Path(zip_or_json)))
        for entry in snapshot['settings'].get('removed_categories', []):
            entry['token'] = uuid.uuid4().hex[:12]
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            next_revision = max(self.revision(), snapshot["revision"]) + 1
            if next_revision >= MAX_INT:
                raise ValueError('Data revision is too large to restore safely')
            live_revisions = {row['id']: row['revision'] for row in self.conn.execute('SELECT id,revision FROM tasks')}
            for task in snapshot['tasks']:
                # Restoring is a new committed state. Never make a historical
                # Telegram callback valid again by rolling its revision back.
                task['revision'] = max(task['revision'], live_revisions.get(task['id'], 0)) + 1
                if task['revision'] >= MAX_INT:
                    raise ValueError('Task revision is too large to restore safely')
            sequence = self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name='tasks'").fetchone()
            high_water = max(int(sequence[0]) if sequence else 0,
                             max((task["id"] for task in snapshot["tasks"]), default=0))
            panel = {}
            previous_chat = self.get('chat_id')
            if previous_chat is not None and previous_chat == snapshot['settings'].get('chat_id'):
                for key in ('list_id', 'dashboard_id', 'single_panel_layout'):
                    value = self.get(key)
                    if value is not None:
                        panel[key] = value
                # Restore cancels pending input. Clean known temporary bot
                # prompts without deleting user replies for changes reverted
                # by this restore.
                cleanup = [entry for entry in self.get('prompt_deletions', []) if entry.get('key')]
                for member in self.list_members():
                    pending = self.get(f"pending_{member['user_id']}") or {}
                    key = pending.get('prompt_key')
                    mid = self.get(key) if key else None
                    if mid and not any(e['chat_id'] == previous_chat and e['message_id'] == mid for e in cleanup):
                        cleanup.append({'key': key, 'chat_id': previous_chat, 'message_id': mid,
                                        'expires': time.time() + 47 * 3600})
                if cleanup:
                    panel['prompt_deletions'] = cleanup
            self.conn.execute("DELETE FROM tasks")
            self.conn.execute("DELETE FROM members")
            self.conn.execute("DELETE FROM meta")
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name='tasks'")
            self.conn.executemany(
                "INSERT INTO members(user_id,name,role) VALUES(:user_id,:name,:role)", snapshot["members"]
            )
            self.conn.executemany(
                "INSERT INTO tasks(id,category,title,done,deleted,revision,created_at,updated_at,created_by,updated_by,source_key) "
                "VALUES(:id,:category,:title,:done,:deleted,:revision,:created_at,:updated_at,:created_by,:updated_by,:source_key)",
                snapshot["tasks"],
            )
            # Never reuse a task ID visible in an old Telegram button, even
            # when restoring a snapshot from before that task was created.
            cursor = self.conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='tasks'", (high_water,))
            if cursor.rowcount == 0:
                self.conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('tasks',?)", (high_water,))
            for key, value in snapshot["settings"].items():
                self._set_value(key, value)
            self._set_value("_data_revision", next_revision)
            for key, value in panel.items():
                self._set_value(key, value)
            for category in self.categories(include_removed=True):
                self._set_value('category_revision_' + category, next_revision)
        self.export_latest()
