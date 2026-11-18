"""AES-256-GCM + kunci OS keychain (S3), fallback keyfile 0600 (E16), redaction (S2)."""
import base64
import os
import re
import secrets
import stat
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .paths import keyfile_path

SERVICE = "scraping-local-auto"
KEY_NAME = "master-key"

_key: Optional[bytes] = None
using_keyfile = False


def _load_or_create_key() -> bytes:
    global _key, using_keyfile
    if _key is not None:
        return _key
    try:
        import keyring
        stored = keyring.get_password(SERVICE, KEY_NAME)
        if stored:
            _key = base64.b64decode(stored)
            return _key
        key = AESGCM.generate_key(bit_length=256)
        keyring.set_password(SERVICE, KEY_NAME, base64.b64encode(key).decode())
        _key = key
        return _key
    except Exception:
        # E16: keychain absen (mis. Linux minimal) -> keyfile 0600 + banner Dashboard
        using_keyfile = True
        kf = keyfile_path()
        if kf.exists():
            _key = base64.b64decode(kf.read_text().strip())
        else:
            key = AESGCM.generate_key(bit_length=256)
            kf.write_text(base64.b64encode(key).decode())
            try:
                os.chmod(kf, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            _key = key
        return _key


def encrypt(plaintext: str) -> bytes:
    key = _load_or_create_key()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt(blob: bytes) -> str:
    key = _load_or_create_key()
    return AESGCM(key).decrypt(blob[:12], blob[12:], None).decode("utf-8")


# ---------- redaction S2 ----------

_REDACT_PATTERNS = [
    # field password di form/JSON/log
    (re.compile(r'((?:password|passwd|pwd|pass)["\']?\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s&,;}\]]+)', re.I), r'\1[REDACTED]'),
    # header Cookie / Authorization
    (re.compile(r'((?:cookie|authorization|set-cookie)\s*[:=]\s*)[^\r\n]+', re.I), r'\1[REDACTED]'),
    # query param token/key/api_key/access_token
    (re.compile(r'((?:token|key|api_key|apikey|access_token|secret|csrf)["\']?\s*[:=]\s*)("[^"]*"|\'[^\']*\'|[^\s&,;}\]]+)', re.I), r'\1[REDACTED]'),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact_exc(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}")