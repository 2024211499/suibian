"""供应商配置加密落盘；读取接口从不返回密钥。"""

import json
import os
import sqlite3
import threading
from contextlib import closing

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings

PROVIDERS = {"qwen": "qwen", "deepseek": "ds", "amap": "amap_web"}


class ProviderConfigurationError(Exception):
    """凭据损坏或密钥不匹配；禁止静默退回另一组凭据。"""


class ProviderStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._key_lock = threading.Lock()
        self.path = settings.state_dir / "providers.sqlite3"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS providers (name TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)")

    def _cipher(self) -> Fernet:
        with self._key_lock:
            return self._load_cipher()

    def _load_cipher(self) -> Fernet:
        configured = self.settings.provider_master_key
        if configured:
            try:
                return Fernet(configured.encode())
            except (ValueError, TypeError):
                raise ProviderConfigurationError("PROVIDER_MASTER_KEY 不是有效加密密钥") from None
        path = self.settings.state_dir / "provider.key"
        if not path.exists():
            with closing(sqlite3.connect(self.path, timeout=5)) as db:
                if db.execute("SELECT 1 FROM providers LIMIT 1").fetchone():
                    raise ProviderConfigurationError("团队凭据加密文件缺失，请恢复 provider.key")
            key = Fernet.generate_key()
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
        try:
            return Fernet(path.read_bytes())
        except (OSError, ValueError, TypeError):
            raise ProviderConfigurationError("团队凭据加密文件损坏，请恢复备份") from None

    def effective_settings(self, base: Settings | None = None) -> Settings:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = db.execute("SELECT name, ciphertext FROM providers").fetchall()
        values = {}
        if rows:
            cipher = self._cipher()
            try:
                for name, ciphertext in rows:
                    item = json.loads(cipher.decrypt(ciphertext))
                    prefix = PROVIDERS[name]
                    values[f"{prefix}_key"] = item["key"]
                    if name != "amap":
                        values.update({f"{prefix}_base": item["base_url"], f"{prefix}_main": item["main_model"]})
                        if name == "qwen":
                            values["qwen_lite"] = item["lite_model"]
            except (InvalidToken, ValueError, KeyError, TypeError):
                raise ProviderConfigurationError("团队凭据无法解密，请核对加密密钥和备份") from None
        return (base or self.settings).model_copy(update=values)

    def set(self, name: str, data: dict) -> None:
        encrypted = self._cipher().encrypt(json.dumps(data).encode())
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                "INSERT INTO providers VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET ciphertext=excluded.ciphertext",
                (name, encrypted),
            )

    def list_public(self) -> dict:
        settings = self.effective_settings()
        public = {}
        for name, prefix in PROVIDERS.items():
            item = {"configured": bool(getattr(settings, f"{prefix}_key"))}
            if name != "amap":
                item.update(
                    base_url=getattr(settings, f"{prefix}_base"), main_model=getattr(settings, f"{prefix}_main"),
                )
            if name == "qwen":
                item["lite_model"] = settings.qwen_lite
            public[name] = item
        return {"providers": public}
