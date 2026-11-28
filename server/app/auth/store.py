"""SQLite 账户存储：Argon2id 密码、令牌摘要和持久化尝试次数。"""

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class AuthStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.dummy_hash = HASHER.hash(secrets.token_urlsafe(32))
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user', 'admin'))
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
                CREATE TABLE IF NOT EXISTS attempts (
                    bucket TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def throttle(self, bucket: str, limit: int, period: int = 900) -> None:
        now = int(time.time())
        window = now // period
        bucket = hashlib.sha256(bucket.encode()).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "INSERT INTO attempts VALUES (?, ?, 1) ON CONFLICT(bucket) DO UPDATE SET "
                "count = CASE WHEN window = excluded.window THEN count + 1 ELSE 1 END, "
                "window = excluded.window RETURNING count", (bucket, window),
            ).fetchone()
            db.execute("DELETE FROM attempts WHERE window < ?", (window - 1,))
        if row[0] > limit:
            raise AuthError(429, "尝试次数过多，请稍后再试")

    def register(self, username: str, password: str) -> dict:
        username = username.lower()
        hashed = HASHER.hash(password)
        user = {"id": secrets.token_hex(16), "username": username, "role": "user"}
        try:
            with self.connection() as db:
                db.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                           (user["id"], username, hashed, user["role"]))
                return self._session(db, user)
        except sqlite3.IntegrityError as error:
            raise AuthError(409, "用户名已被使用") from error

    def bootstrap_admin(self, username: str, password: str) -> None:
        username = username.lower()
        with self.connection() as db:
            existing = db.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                if existing["role"] != "admin":
                    raise ValueError("管理员用户名已被普通账户使用，请指定独立用户名")
                return
            db.execute("INSERT INTO users VALUES (?, ?, ?, 'admin')",
                       (secrets.token_hex(16), username, HASHER.hash(password)))

    def login(self, username: str, password: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (username.lower(),)).fetchone()
        try:
            valid = HASHER.verify(row["password_hash"] if row else self.dummy_hash, password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not row or not valid:
            raise AuthError(401, "用户名或密码错误")
        with self.connection() as db:
            if HASHER.check_needs_rehash(row["password_hash"]):
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (HASHER.hash(password), row["id"]))
            return self._session(db, {key: row[key] for key in ("id", "username", "role")})

    def _session(self, db, user: dict) -> dict:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (self.digest(token), user["id"], now + 86400))
        return {"access_token": token, "token_type": "bearer", "expires_in": 86400, "user": user}

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticate(self, token: str) -> dict:
        if not 32 <= len(token) <= 128:
            raise AuthError(401, "请登录或重新登录")
        with self.connection() as db:
            row = db.execute(
                "SELECT users.id, username, role FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE digest = ? AND expires > ?", (self.digest(token), time.time()),
            ).fetchone()
        if not row:
            raise AuthError(401, "请登录或重新登录")
        return dict(row)

    def logout(self, token: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE digest = ?", (self.digest(token),))
