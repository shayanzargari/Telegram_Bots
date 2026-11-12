"""Bot-owned SQLite state. All authorization transitions are transactions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from .audit import desktop_state
from ..store import atomic_json


def outside(path, protected):
    path, protected = Path(path).resolve(), Path(protected).resolve()
    return path != protected and protected not in path.parents


class State:
    def __init__(self, directory, source_root):
        self.root = Path(directory).resolve()
        if not outside(self.root, source_root) or not outside(self.root, desktop_state(source_root)):
            raise ValueError('Bot state must be outside notes and desktop application state.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'reader.sqlite3'
        existed = self.path.exists()
        if not self.path.exists() and (self.root / 'identity.json').exists():
            raise ValueError('Access database is missing. Restore its backup; ownership was not reset.')
        self.lock = threading.RLock()
        self._managed_update = False
        self._transaction_sequence = 0
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize(existed)
            self.delivery_path=self.root/'delivery-intentions.json'
            self._delivery_intentions()  # Corrupt recovery markers must not be reset.
        except BaseException:
            self.db.close()
            raise

    def _initialize(self, existed):
        if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Access database failed its integrity check.')
        if existed:
            tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'meta','users','invitations','sessions','buttons','updates'} <= tables:
                raise ValueError('Access database structure is incomplete; restore a backup instead of resetting it.')
            row=self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if not row or row[0]!='1':
                raise ValueError('Unsupported database version; access state was left unchanged.')
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL,
          owner INTEGER NOT NULL DEFAULT 0,epoch INTEGER NOT NULL DEFAULT 1,
          voice TEXT NOT NULL DEFAULT 'denise',speed TEXT NOT NULL DEFAULT '1',position TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON users(owner) WHERE owner=1;
        CREATE TABLE IF NOT EXISTS invitations(hash TEXT PRIMARY KEY,kind TEXT NOT NULL,expires REAL NOT NULL,
          used INTEGER,revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(chat INTEGER PRIMARY KEY,generation INTEGER NOT NULL DEFAULT 0,
          anchor INTEGER,continuation INTEGER,audio INTEGER,view TEXT NOT NULL DEFAULT '{"screen":"menu"}');
        CREATE TABLE IF NOT EXISTS buttons(handle TEXT PRIMARY KEY,user INTEGER NOT NULL,chat INTEGER NOT NULL,
          generation INTEGER NOT NULL,epoch INTEGER NOT NULL,version TEXT NOT NULL,action TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY,seen REAL NOT NULL);
        ''')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
        if self.meta('schema') != '1':
            raise ValueError('Unsupported database version; do not reset access state.')
        marker = self.root / 'identity.json'
        if not marker.exists():
            marker.write_text('{"schema":1}', 'utf-8')

    def close(self):
        with self.lock:
            self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            if self._managed_update:
                self._transaction_sequence += 1
                name='update_scope_'+str(self._transaction_sequence)
                self.db.execute('SAVEPOINT '+name)
                try:
                    yield self.db
                    self.db.execute('RELEASE '+name)
                except BaseException:
                    self.db.execute('ROLLBACK TO '+name)
                    self.db.execute('RELEASE '+name)
                    raise
                return
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    @contextmanager
    def durable_update(self, update_id):
        """Apply replay-sensitive local state and its receipt in one commit."""
        with self.lock:
            if self.db.execute('SELECT 1 FROM updates WHERE id=?',(update_id,)).fetchone():
                self._clear_text_deliveries(update_id)
                yield False
                return
            self.db.execute('BEGIN IMMEDIATE');self._managed_update=True
            try:
                yield True
                self.db.execute('INSERT OR IGNORE INTO updates VALUES(?,?)',(update_id,time.time()))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('polling_offset',?)",(str(update_id+1),))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
            finally:self._managed_update=False
            self._clear_text_deliveries(update_id)

    def _delivery_intentions(self):
        if not self.delivery_path.exists():return {}
        records=json.loads(self.delivery_path.read_text('utf-8'))
        if not isinstance(records,dict) or any(not isinstance(key,str) or not isinstance(value,int) for key,value in records.items()):
            raise ValueError('French delivery recovery markers are invalid; preserve them for local recovery.')
        return records

    def reserve_text_delivery(self,update_id,sequence,chat):
        """Record a new message before sending; rollback cannot erase uncertainty."""
        with self.lock:
            records=self._delivery_intentions();key=f'{update_id}:{sequence}:{chat}'
            if key in records:return False
            records[key]=int(update_id);atomic_json(self.delivery_path,records)
            return True

    def _clear_text_deliveries(self,update_id):
        # This is cleanup after the SQLite receipt committed. A failed cleanup
        # must neither undo that receipt nor turn it into a second remote send.
        try:
            records=self._delivery_intentions()
            remaining={key:value for key,value in records.items() if value!=update_id}
            if remaining!=records:
                if remaining:atomic_json(self.delivery_path,remaining)
                else:self.delivery_path.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning('Committed French delivery recovery cleanup is deferred.')

    def meta(self, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def set_meta(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))

    def bind_bot(self, bot_id):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM meta WHERE key='bot_id'").fetchone()
            if row and row[0] != str(bot_id):
                raise ValueError('This state belongs to another bot. Use a separate state directory.')
            db.execute("INSERT OR IGNORE INTO meta VALUES('bot_id',?)", (str(bot_id),))

    def user(self, user_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if row:
                result=dict(row)
                result['auto_audio']=self.meta('auto_audio:'+str(user_id))!='0'
                return result
            return None

    def allowed(self, user_id, epoch=None, owner=False):
        row = self.user(user_id)
        return bool(row and row['active'] and (epoch is None or row['epoch'] == epoch) and (not owner or row['owner']))

    def has_owner(self):
        with self.lock:
            return self.db.execute('SELECT 1 FROM users WHERE owner=1').fetchone() is not None

    def invitation(self, kind='reader'):
        token = secrets.token_urlsafe(24)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction() as db:
            if kind == 'owner':
                if db.execute('SELECT 1 FROM users WHERE owner=1').fetchone():
                    raise ValueError('Owner already paired.')
                db.execute("UPDATE invitations SET revoked=1 WHERE kind='owner'")
            db.execute('INSERT INTO invitations(hash,kind,expires) VALUES(?,?,?)',
                       (hashed, kind, time.time() + (600 if kind == 'owner' else 86400)))
        return token

    def redeem(self, token, user_id, name):
        if not isinstance(token, str) or len(token) > 64:
            return False
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction() as db:
            existing = db.execute('SELECT active FROM users WHERE id=?', (user_id,)).fetchone()
            if existing and existing['active']:
                return True  # Existing members do not burn someone else's invitation.
            row = db.execute('SELECT * FROM invitations WHERE hash=? AND used IS NULL AND revoked=0 AND expires>?',
                             (hashed, time.time())).fetchone()
            if not row or (row['kind'] == 'owner' and db.execute('SELECT 1 FROM users WHERE owner=1').fetchone()):
                return False
            changed = db.execute('UPDATE invitations SET used=? WHERE hash=? AND used IS NULL AND revoked=0', (user_id, hashed)).rowcount
            if changed != 1:
                return False
            db.execute('''INSERT INTO users(id,name,active,owner) VALUES(?,?,1,?)
                       ON CONFLICT(id) DO UPDATE SET active=1,name=excluded.name,epoch=users.epoch+1''',
                       (user_id, name[:150], int(row['kind'] == 'owner')))
            return True

    def revoke(self, actor, target):
        with self.transaction() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=? AND active=1 AND owner=1', (actor,)).fetchone():
                raise PermissionError('Owner access required.')
            db.execute('UPDATE users SET active=0,epoch=epoch+1 WHERE id=? AND owner=0', (target,))
            db.execute('DELETE FROM buttons WHERE user=?', (target,))

    def cancel_invitation(self, hashed):
        with self.transaction() as db:
            db.execute('UPDATE invitations SET revoked=1 WHERE hash=? AND used IS NULL', (hashed,))

    def readers(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT id,name,active,owner FROM users ORDER BY owner DESC,id')]

    def invitations(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT hash,expires FROM invitations WHERE kind='reader' AND used IS NULL AND revoked=0 AND expires>? ORDER BY expires", (time.time(),))]

    def setting(self, user_id, voice=None, speed=None, position=None,auto_audio=None):
        if voice is not None and voice not in ('denise', 'henri', 'vivienne'):
            raise ValueError('Unknown voice.')
        if speed is not None and speed not in ('1', '0.8'):
            raise ValueError('Unknown speed.')
        if auto_audio is not None and not isinstance(auto_audio,bool):raise ValueError('Unknown audio mode.')
        with self.transaction() as db:
            for key, value in (('voice', voice), ('speed', speed), ('position', position)):
                if value is not None:
                    db.execute(f'UPDATE users SET {key}=? WHERE id=? AND active=1',
                               (json.dumps(value) if key == 'position' else value, user_id))
            if auto_audio is not None and db.execute('SELECT 1 FROM users WHERE id=? AND active=1',(user_id,)).fetchone():
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('auto_audio:'+str(user_id),'1' if auto_audio else '0'))

    def session(self, chat):
        with self.lock:
            self.db.execute('INSERT OR IGNORE INTO sessions(chat) VALUES(?)', (chat,))
            row = dict(self.db.execute('SELECT * FROM sessions WHERE chat=?', (chat,)).fetchone())
            row['view'] = json.loads(row['view'])
            return row

    def save_session(self, chat, **values):
        allowed = {'anchor', 'continuation', 'audio', 'generation', 'view'}
        if not set(values) <= allowed:
            raise ValueError('Unknown session property.')
        self.session(chat)
        with self.transaction() as db:
            for key, value in values.items():
                db.execute(f'UPDATE sessions SET {key}=? WHERE chat=?',
                           (json.dumps(value, ensure_ascii=False) if key == 'view' else value, chat))

    def buttons(self, user, chat, generation, version, actions, replace=True):
        handles = []
        with self.transaction() as db:
            epoch = db.execute('SELECT epoch FROM users WHERE id=? AND active=1', (user,)).fetchone()
            if not epoch:
                raise PermissionError('Access revoked.')
            if replace:
                db.execute('DELETE FROM buttons WHERE chat=?', (chat,))
            db.execute('DELETE FROM buttons WHERE expires<?', (time.time(),))
            for action in actions:
                handle = secrets.token_urlsafe(16)
                db.execute('INSERT INTO buttons VALUES(?,?,?,?,?,?,?,?)',
                           (handle,user,chat,generation,epoch[0],version,json.dumps(action),time.time()+7*86400))
                handles.append(handle)
        return handles

    def resolve(self, handle, user, chat):
        if not self.allowed(user):
            return None
        with self.lock:
            row = self.db.execute('''SELECT b.* FROM buttons b JOIN sessions s ON s.chat=b.chat
                 JOIN users u ON u.id=b.user WHERE b.handle=? AND b.user=? AND b.chat=?
                 AND b.generation=s.generation AND b.epoch=u.epoch AND u.active=1 AND b.expires>?''',
                                  (handle,user,chat,time.time())).fetchone()
            return dict(version=row['version'], action=json.loads(row['action'])) if row else None

    def seen(self, update_id):
        with self.transaction() as db:
            inserted = db.execute('INSERT OR IGNORE INTO updates VALUES(?,?)', (update_id,time.time())).rowcount
            db.execute('DELETE FROM updates WHERE seen<?', (time.time()-7*86400,))
            return not bool(inserted)

    def backup(self):
        with self.lock:
            target = sqlite3.connect(self.root / 'reader.backup.sqlite3')
            try:
                self.db.backup(target)
            finally:
                target.close()
