from __future__ import annotations

import os

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class RegistrySecretStore:
    """Encrypts agent credentials before they are persisted in Registry SQLite."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.key_path = Path(
            os.getenv(
                "REGISTRY_SECRET_KEY_FILE",
                str(self.database_path.with_name("registry-secrets.key")),
            )
        )
        self._cipher = Fernet(self._load_key())

    def encrypt(self, value: str) -> bytes:
        return self._cipher.encrypt(value.encode("utf-8"))

    def decrypt(self, value: bytes) -> str:
        try:
            return self._cipher.decrypt(value).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "Registry could not decrypt an agent credential; check "
                "REGISTRY_SECRET_KEY or REGISTRY_SECRET_KEY_FILE"
            ) from exc

    def _load_key(self) -> bytes:
        configured = os.getenv("REGISTRY_SECRET_KEY", "").strip()
        if configured:
            return self._validate_key(configured.encode("ascii"))

        if self.key_path.exists():
            return self._validate_key(self.key_path.read_bytes().strip())

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            with self.key_path.open("xb") as handle:
                handle.write(key)
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
        except FileExistsError:
            key = self.key_path.read_bytes().strip()
        return self._validate_key(key)

    @staticmethod
    def _validate_key(key: bytes) -> bytes:
        try:
            Fernet(key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "REGISTRY_SECRET_KEY must be a valid Fernet key"
            ) from exc
        return key
