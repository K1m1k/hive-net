# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Server-side cryptography.

- Packets: AES-256-GCM with a fresh key per packet and a label bound as
  authenticated data (AAD), so a packet can never be moved or replayed.
- Results: the node seals them for the server with X25519 + HKDF + AES-GCM.
- Modules: signed with Ed25519; nodes only run modules with a valid signature.
- Data at rest (pending originals, packet inputs): AES-GCM with the server key.
"""
import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SEAL_INFO = b"hivenet-result-v1"


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return nonce, AESGCM(key).encrypt(nonce, plaintext, aad or None)


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes = b"") -> bytes:
    return AESGCM(key).decrypt(nonce, ciphertext, aad or None)


class ServerKeys:
    """Server keys, created on first start and stored in the data directory."""

    def __init__(self, keys_dir: Path):
        keys_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(keys_dir, 0o700)
        except OSError:
            pass
        self.sign_key = self._load_or_create(keys_dir / "modules_ed25519.pem", Ed25519PrivateKey)
        self.seal_key = self._load_or_create(keys_dir / "results_x25519.pem", X25519PrivateKey)
        rest = keys_dir / "at_rest.key"
        if not rest.exists():
            rest.write_bytes(new_key())
            os.chmod(rest, 0o600)
        self.rest_key = rest.read_bytes()

    @staticmethod
    def _load_or_create(path: Path, cls):
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        key = cls.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        os.chmod(path, 0o600)
        return key

    @property
    def sign_public_raw(self) -> bytes:
        return self.sign_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def seal_public_raw(self) -> bytes:
        return self.seal_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(self, data: bytes) -> bytes:
        return self.sign_key.sign(data)

    # --- data at rest ---
    def seal_at_rest(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce, ct = encrypt(self.rest_key, plaintext, aad)
        return nonce + ct

    def open_at_rest(self, blob: bytes, aad: bytes = b"") -> bytes:
        return decrypt(self.rest_key, blob[:12], blob[12:], aad)

    # --- results sealed by the node ---
    def open_sealed(self, epk_raw: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        shared = self.seal_key.exchange(X25519PublicKey.from_public_bytes(epk_raw))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk_raw, info=SEAL_INFO).derive(shared)
        return decrypt(key, nonce, ciphertext, aad)


def seal_for(server_pub_raw: bytes, plaintext: bytes, aad: bytes) -> dict:
    """Seals a result for the server (used by the tests; the node does this in JavaScript)."""
    eph = X25519PrivateKey.generate()
    epk = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(server_pub_raw))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk, info=SEAL_INFO).derive(shared)
    nonce, ct = encrypt(key, plaintext, aad)
    return {"epk": b64e(epk), "nonce": b64e(nonce), "ct": b64e(ct)}
