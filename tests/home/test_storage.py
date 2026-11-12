import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import zipfile

from home_bot.storage import Store, _atomic_write


class ExistingStoreProtectionTests(unittest.TestCase):
    def reject_unchanged(self,folder):
        names={path.name for path in folder.iterdir()}
        before={path.name:path.read_bytes() for path in folder.iterdir() if path.is_file()}
        with self.assertRaisesRegex(ValueError,'restore a verified set'):Store(folder)
        self.assertEqual({path.name for path in folder.iterdir()},names)
        self.assertEqual({path.name:path.read_bytes() for path in folder.iterdir() if path.is_file()},before)

    def test_existing_blank_database_is_not_initialized(self):
        for materialized_header in (False,True):
            with self.subTest(materialized_header=materialized_header),tempfile.TemporaryDirectory() as temporary:
                folder=Path(temporary);database=folder/'tasks.sqlite3'
                if materialized_header:
                    connection=sqlite3.connect(database)
                    try:connection.execute('PRAGMA user_version=42')
                    finally:connection.close()
                else:database.write_bytes(b'')
                self.reject_unchanged(folder)

    def test_existing_incomplete_schema_is_not_repaired_or_initialized(self):
        for schema in ('CREATE TABLE meta(key TEXT,value TEXT);',
                       'CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE members(user_id INTEGER,name TEXT,role TEXT);CREATE TABLE tasks(id INTEGER);'):
            with self.subTest(schema=schema),tempfile.TemporaryDirectory() as temporary:
                folder=Path(temporary);connection=sqlite3.connect(folder/'tasks.sqlite3')
                try:connection.executescript(schema)
                finally:connection.close()
                self.reject_unchanged(folder)

    def test_existing_corrupt_database_is_preserved_without_writable_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'tasks.sqlite3').write_bytes(b'Not a SQLite database. Preserve these exact bytes.\x00\xff')
            self.reject_unchanged(folder)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_persistence_and_unicode(self):
        self.store.add_member(101, "شایان", "owner")
        self.store.set("title", "خانه ما")
        task_id = self.store.add_task("shopping", "شیر و موز", 101)
        revision = self.store.revision()
        self.store.close()
        self.store = Store(self.folder)
        self.assertEqual(self.store.task(task_id)["title"], "شیر و موز")
        self.assertEqual(self.store.get("title"), "خانه ما")
        self.assertTrue(self.store.authorized(101))
        self.assertEqual(self.store.revision(), revision)

    def test_uncheck_all_preserves_items_and_trash_without_creating_backup(self):
        open_id = self.store.add_task("shopping", "Milk", 101)
        done_id = self.store.add_task("home", "Mirror", 101)
        deleted_id = self.store.add_task("study", "French", 101)
        self.store.change_task(done_id, "complete", 1, 101)
        self.store.change_task(deleted_id, "complete", 1, 101)
        self.store.change_task(deleted_id, "delete", 2, 101)
        before = {i: self.store.task(i) for i in (open_id, done_id, deleted_id)}
        revision = self.store.revision()
        self.assertEqual(self.store.uncheck_all(101), 2)
        self.assertEqual(self.store.revision(), revision + 1)
        for task_id, previous in before.items():
            after = self.store.task(task_id)
            self.assertFalse(after["done"])
            for key in ("id", "title", "category", "deleted", "created_at", "created_by", "source_key"):
                self.assertEqual(after[key], previous[key])
        self.assertEqual(self.store.task(open_id), before[open_id])
        self.assertFalse(self.store.change_task(done_id, "reopen", 2, 101))
        self.assertEqual(self.store.uncheck_all(101), 0)
        self.assertEqual(self.store.revision(), revision + 1)
        self.assertEqual(list(self.store.backups_dir.glob("*.zip")), [])

    def test_windows_export_lock_retries_without_losing_the_old_file(self):
        import os
        path = self.folder / "readable.txt"
        path.write_text("old content", encoding="utf-8")
        replace = os.replace
        attempts = []

        def temporarily_locked(source, destination):
            attempts.append(1)
            if len(attempts) < 3:
                self.assertEqual(path.read_text("utf-8"), "old content")
                error = PermissionError("file is temporarily in use")
                error.winerror = 32
                raise error
            return replace(source, destination)

        with mock.patch("home_bot.storage.os.replace", side_effect=temporarily_locked), mock.patch("home_bot.storage.time.sleep"):
            _atomic_write(path, "new content")
        self.assertEqual(path.read_text("utf-8"), "new content")
        self.assertEqual(len(attempts), 3)

    def test_legacy_category_upgrade_preserves_tasks_and_id_high_water(self):
        first = self.store.add_task("shopping", "Speaker", 101, "original-source")
        self.store.change_task(first, "complete", 1, 101)
        removed = self.store.add_task("home", "Temporary", 101)
        with self.store.conn:
            self.store.conn.execute("DELETE FROM tasks WHERE id=?", (removed,))
        before = self.store.task(first)
        revision = self.store.revision()
        # Recreate the exact previous CHECK constraint as an old installation.
        sql = self.store.conn.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
        with self.store.conn:
            self.store.conn.execute("PRAGMA writable_schema=ON")
            self.store.conn.execute("UPDATE sqlite_master SET sql=? WHERE name='tasks'",
                                    (sql.replace("category TEXT NOT NULL", "category TEXT NOT NULL CHECK(category IN ('shopping','home','admin','study'))"),))
            self.store.conn.execute("PRAGMA writable_schema=OFF")
        self.store.close()
        self.store = Store(self.folder)
        self.assertEqual(self.store.task(first), before)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(len(list(self.store.backups_dir.glob('*.zip'))), 0)
        household = self.store.add_task("household", "Mirror", 101)
        self.assertGreater(household, removed)
        backup = self.store.create_backup()
        self.store.restore_backup(backup)
        self.assertEqual(self.store.task(household)["category"], "household")
        self.assertEqual(self.store.counts()["household"]["open"], 1)

    def test_requested_english_checklist_has_all_five_sections(self):
        items = json.loads((Path(__import__("home_bot").__file__).parent / "seed_tasks.json").read_text("utf-8"))
        self.assertEqual(self.store.import_tasks(items), 32)
        self.assertEqual({category: len(self.store.list_tasks(category)) for category in
                          ("shopping", "household", "admin", "home", "study")},
                         {"shopping": 7, "household": 9, "admin": 7, "home": 7, "study": 2})
        self.assertFalse(any("please clarify" in t["title"].lower() for t in self.store.list_tasks()))
        export = self.store.export_latest()
        self.assertIn("Home shopping", export["html"].read_text("utf-8"))

    def test_optimistic_state_changes_and_trash(self):
        task_id = self.store.add_task("home", "Install mirror", 101)
        first = self.store.task(task_id)
        self.assertTrue(self.store.change_task(task_id, "complete", first["revision"], 102))
        self.assertFalse(self.store.change_task(task_id, "delete", first["revision"], 101))
        completed = self.store.task(task_id)
        self.assertFalse(self.store.change_task(task_id, "complete", completed["revision"], 101))
        self.assertTrue(self.store.change_task(task_id, "delete", completed["revision"], 101))
        deleted = self.store.task(task_id)
        self.assertTrue(deleted["done"])
        self.assertTrue(deleted["deleted"])
        self.assertFalse(self.store.change_task(task_id, "reopen", deleted["revision"], 101))
        self.assertEqual(self.store.list_tasks(mode="all"), [])
        self.assertEqual(len(self.store.list_tasks(mode="trash")), 1)
        self.assertTrue(self.store.change_task(task_id, "restore", deleted["revision"], 101))
        restored = self.store.task(task_id)
        self.assertTrue(restored["done"])
        self.assertFalse(restored["deleted"])
        self.assertTrue(self.store.change_task(task_id, "reopen", restored["revision"], 102))
        self.assertEqual(self.store.counts()["home"], {"open": 1, "done": 0, "trash": 0})

    def test_member_cap_and_owner_role_preservation(self):
        self.assertTrue(self.store.add_member(101, "Shayan", "owner"))
        self.assertTrue(self.store.add_member(102, "Dorsa"))
        self.assertFalse(self.store.add_member(101, "Shayan Zargari"))
        self.assertEqual(self.store.list_members()[0]["role"], "owner")
        with self.assertRaises(ValueError):
            self.store.add_member(103, "Third")
        self.assertFalse(self.store.authorized(103))
        self.assertFalse(self.store.authorized(True))
        self.assertEqual(len(self.store.list_members()), 2)

    def test_idempotency_survives_completion_and_restart(self):
        task_id = self.store.add_task("shopping", "Milk", 101, "telegram:-555:123")
        revision = self.store.revision()
        duplicate_id = self.store.add_task("shopping", "Milk", 101, "telegram:-555:123")
        self.assertEqual(duplicate_id, task_id)
        self.assertEqual(self.store.revision(), revision)
        self.store.change_task(task_id, "complete", 1, 101)
        self.store.close()
        self.store = Store(self.folder)
        self.assertEqual(self.store.add_task("shopping", "Milk", 101, "telegram:-555:123"), task_id)
        self.assertEqual(len(self.store.list_tasks(mode="all")), 1)
        self.assertTrue(self.store.task(task_id)["done"])

    def test_seed_import_is_atomic_and_once(self):
        with self.assertRaises(ValueError):
            self.store.import_tasks([{"category": "home", "title": "Desk"},
                                     {"category": "invalid", "title": "Bad"}])
        self.assertEqual(self.store.list_tasks(), [])
        self.assertFalse(self.store.get("seed_imported", False))
        items = [{"category": "home", "title": "Desk"}, {"category": "study", "title": "French"}]
        self.assertEqual(self.store.import_tasks(items), 2)
        self.assertEqual(self.store.import_tasks(items), 0)
        self.assertEqual(self.store.counts()["all"]["open"], 2)

    def test_text_is_sql_safe_and_html_escaped(self):
        title = "شیر <script>alert('x')</script> & '); DROP TABLE tasks; --"
        task_id = self.store.add_task("shopping", title, 101)
        paths = self.store.export_latest()
        document = paths["html"].read_text(encoding="utf-8")
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)
        self.assertIn("شیر", document)
        self.assertEqual(self.store.task(task_id)["title"], title)
        self.store.add_task("home", "Still works", 101)
        self.assertEqual(self.store.counts()["all"]["open"], 2)

    def test_backup_restores_tasks_members_settings_and_preserves_config(self):
        self.store.add_member(101, "Shayan", "owner")
        self.store.add_member(102, "Dorsa")
        self.store.set("title", "خانه ما")
        self.store.set("timezone", "America/Toronto")
        self.store.set("chat_id", -1234567)
        self.store.set("pending_secret", "do-not-export-this-secret")
        self.store.set("owner_claim", "owner-claim-secret")
        self.store.set("dashboard_id", 444)
        self.store.set("last_update_id", 99)
        first = self.store.add_task("home", "Mirror", 101, "source-1")
        second = self.store.add_task("study", "French", 102, "source-2")
        self.store.change_task(first, "complete", 1, 102)
        self.store.change_task(second, "delete", 1, 101)
        before_tasks = [self.store.task(first), self.store.task(second)]
        config = self.folder / "config.json"
        config.write_text('{"bot_token":"private-token"}', encoding="utf-8")
        backup = self.store.create_backup()
        with zipfile.ZipFile(backup) as archive:
            self.assertEqual(set(archive.namelist()), {"tasks.json", "tasks.txt", "tasks.html", "tasks.sqlite3"})
            data = archive.read("tasks.json").decode("utf-8")
            self.assertNotIn("pending_secret", data)
            self.assertNotIn("owner_claim", data)
            self.assertNotIn("dashboard_id", data)
            self.assertNotIn("last_update_id", data)
            copied_database = self.folder / "inspect.sqlite3"
            copied_database.write_bytes(archive.read("tasks.sqlite3"))
            self.assertNotIn(b"do-not-export-this-secret", copied_database.read_bytes())
            database = sqlite3.connect(copied_database)
            keys = {row[0] for row in database.execute("SELECT key FROM meta")}
            database.close()
            self.assertNotIn("pending_secret", keys)
            self.assertNotIn("owner_claim", keys)
        temporary_id = self.store.add_task("admin", "Temporary item", 101)
        current_revision = self.store.revision()
        self.store.restore_backup(backup)
        self.assertGreater(self.store.revision(), current_revision)
        for restored, saved in zip([self.store.task(first), self.store.task(second)], before_tasks):
            self.assertGreater(restored['revision'], saved['revision'])
            self.assertEqual({k:v for k,v in restored.items() if k != 'revision'},
                             {k:v for k,v in saved.items() if k != 'revision'})
        self.assertEqual(len(self.store.list_members()), 2)
        self.assertEqual(self.store.get("chat_id"), -1234567)
        self.assertIsNone(self.store.get("pending_secret"))
        self.assertIsNone(self.store.get("last_update_id"))
        self.assertEqual(config.read_text(), '{"bot_token":"private-token"}')
        self.assertEqual(len(list(self.store.backups_dir.glob("*.zip"))), 1)
        new_id = self.store.add_task("shopping", "New item", 101)
        self.assertGreater(new_id, temporary_id)

    def test_restore_uses_json_not_untrusted_sqlite_payload(self):
        task_id = self.store.add_task("admin", "WES", 101)
        exported = self.store.export_latest()["json"].read_bytes()
        path = self.folder / "untrusted-sqlite.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("tasks.json", exported)
            archive.writestr("tasks.sqlite3", b"This is not a SQLite database")
        self.store.restore_backup(path)
        self.assertEqual(self.store.task(task_id)["title"], "WES")

    def test_invalid_restore_leaves_live_data_and_rejects_zip_traversal(self):
        task_id = self.store.add_task("home", "Keep me", 101)
        paths = self.store.export_latest()
        original = json.loads(paths["json"].read_text(encoding="utf-8"))
        invalid = dict(original)
        invalid["tasks"] = [dict(original["tasks"][0], category="bad")]
        bad_json = self.folder / "invalid.json"
        bad_json.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.restore_backup(bad_json)
        self.assertEqual(self.store.task(task_id)["title"], "Keep me")
        bad_zip = self.folder / "unsafe.zip"
        with zipfile.ZipFile(bad_zip, "w") as archive:
            archive.writestr("tasks.json", json.dumps(original))
            archive.writestr("../outside.txt", "bad")
        with self.assertRaises(ValueError):
            self.store.restore_backup(bad_zip)
        self.assertEqual(self.store.task(task_id)["title"], "Keep me")
        self.assertFalse((self.folder.parent / "outside.txt").exists())

    def test_plain_json_restore_rejects_duplicate_ids(self):
        self.store.add_task("shopping", "Milk", 101)
        exported = self.store.export_latest()["json"]
        data = json.loads(exported.read_text(encoding="utf-8"))
        data["tasks"].append(dict(data["tasks"][0]))
        bad = self.folder / "duplicate.json"
        bad.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.restore_backup(bad)
        self.assertEqual(self.store.counts()["all"]["open"], 1)


if __name__ == "__main__":
    unittest.main()
