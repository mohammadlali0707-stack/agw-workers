"""Offline proof of concept, not a network listener or an authentication system.

The caller must establish source, owner and room independently of message body.
Only a single source/room and integer IDs are modeled. No automatic retry is
allowed after dispatch begins: an ambiguous outcome requires reconciliation.
"""
import hashlib
import math
import re
import secrets
import sqlite3

_TAG = re.compile(r'(?<![\w@])@(?:Notion2|نوشن2|نوشن۲)(?![\w])', re.IGNORECASE)


class Intake:
    def __init__(self, db):
        self.db = db
        db.execute('''CREATE TABLE IF NOT EXISTS intake (
            message_id INTEGER PRIMARY KEY, digest TEXT NOT NULL,
            state TEXT NOT NULL, lease_token TEXT, lease_until REAL,
            session_id TEXT
        )''')
        db.commit()

    def capture(self, message_id, text, *, trusted_source, owner_verified, room_verified):
        if trusted_source is not True or owner_verified is not True or room_verified is not True:
            return 'unauthorized'
        if type(message_id) is not int or not 0 < message_id <= 2**63 - 1 or not isinstance(text, str):
            return 'invalid'
        try:
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        except UnicodeEncodeError:
            return 'invalid'
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT digest FROM intake WHERE message_id=?', (message_id,)).fetchone()
            if row:
                result = 'duplicate' if secrets.compare_digest(row[0], digest) else 'conflict'
            elif not _TAG.search(text):
                result = 'not_addressed'
            else:
                self.db.execute('INSERT INTO intake(message_id,digest,state) VALUES(?,?,?)', (message_id, digest, 'pending'))
                result = 'captured'
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def claim(self, now, lease_seconds):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (now, lease_seconds)) or lease_seconds <= 0 or not math.isfinite(now + lease_seconds):
            raise ValueError('invalid clock or lease')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute("SELECT message_id FROM intake WHERE state='pending' OR (state='claimed' AND lease_until<=?) ORDER BY message_id LIMIT 1", (now,)).fetchone()
            if row is None:
                self.db.commit()
                return None
            token = secrets.token_hex(32)
            self.db.execute("UPDATE intake SET state='claimed',lease_token=?,lease_until=? WHERE message_id=?", (token, now+lease_seconds, row[0]))
            self.db.commit()
            return row[0], token
        except Exception:
            self.db.rollback()
            raise

    def begin_dispatch(self, message_id, token, text, now):
        if type(message_id) is not int or not isinstance(token, str) or not isinstance(text, str) or type(now) not in (int, float) or not math.isfinite(now):
            return False
        try:
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        except UnicodeEncodeError:
            return False
        try:
            self.db.execute('BEGIN IMMEDIATE')
            cur = self.db.execute("UPDATE intake SET state='dispatching' WHERE message_id=? AND state='claimed' AND lease_token=? AND lease_until>? AND digest=?", (message_id, token, now, digest))
            self.db.commit()
            return cur.rowcount == 1
        except Exception:
            self.db.rollback()
            raise

    def record_session(self, message_id, token, session_id):
        if type(message_id) is not int or not isinstance(token, str) or not isinstance(session_id, str) or not session_id.strip():
            return False
        try:
            self.db.execute('BEGIN IMMEDIATE')
            cur = self.db.execute("UPDATE intake SET state='session_known',session_id=? WHERE message_id=? AND state='dispatching' AND lease_token=?", (session_id, message_id, token))
            self.db.commit()
            return cur.rowcount == 1
        except Exception:
            self.db.rollback()
            raise
