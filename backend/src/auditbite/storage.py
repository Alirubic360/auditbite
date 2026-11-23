"""Private file storage with short-lived signed URLs. Local-disk backend; the Protocol is what a
Supabase Storage adapter would implement."""
from __future__ import annotations
import hashlib, hmac, os, time
from pathlib import Path
from typing import Protocol

MAX_BYTES = 20 * 1024 * 1024
MAGIC = [(b"%PDF-", "application/pdf"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg")]


def sniff_media_type(data: bytes) -> str | None:
    """Trust file content, not the client-supplied Content-Type."""
    for sig, mt in MAGIC:
        if data.startswith(sig):
            return mt
    return None


class Storage(Protocol):
    def put(self, path: str, data: bytes) -> None: ...
    def get(self, path: str) -> bytes: ...
    def signed_url(self, path: str, ttl: int = 300) -> str: ...


class LocalStorage:
    def __init__(self, root: str, secret: str):
        self.root, self.secret = Path(root).resolve(), secret.encode()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if self.root not in p.parents:
            raise ValueError("path escapes storage root")
        return p

    def put(self, path, data):
        p = self._safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, path):
        return self._safe(path).read_bytes()

    def _sig(self, path, exp):
        return hmac.new(self.secret, f"{path}|{exp}".encode(), hashlib.sha256).hexdigest()

    def signed_url(self, path, ttl=300):
        exp = int(time.time()) + ttl
        return f"/files/{path}?exp={exp}&sig={self._sig(path, exp)}"

    def verify(self, path: str, exp: int, sig: str) -> bool:
        return exp >= time.time() and hmac.compare_digest(self._sig(path, exp), sig)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
