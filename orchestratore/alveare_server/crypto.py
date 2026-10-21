"""Crittografia del server.

- Pacchetti: AES-256-GCM, una chiave nuova per ogni pacchetto, etichetta come
  dati autenticati (AAD): un pacchetto non può essere spostato o riusato.
- Risultati: il nodo li sigilla per il server con X25519 + HKDF + AES-GCM.
- Moduli: firmati con Ed25519; il nodo esegue solo moduli con firma valida.
- Dati a riposo (originali in attesa, ingressi dei pacchetti): AES-GCM con la
  chiave del server.
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

SEAL_INFO = b"alveare-result-v1"


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
    """Chiavi del server, create al primo avvio e salvate nella cartella dati."""

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

    # --- dati a riposo ---
    def seal_at_rest(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce, ct = encrypt(self.rest_key, plaintext, aad)
        return nonce + ct

    def open_at_rest(self, blob: bytes, aad: bytes = b"") -> bytes:
        return decrypt(self.rest_key, blob[:12], blob[12:], aad)

    # --- risultati sigillati dal nodo ---
    def open_sealed(self, epk_raw: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        shared = self.seal_key.exchange(X25519PublicKey.from_public_bytes(epk_raw))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk_raw, info=SEAL_INFO).derive(shared)
        return decrypt(key, nonce, ciphertext, aad)


def seal_for(server_pub_raw: bytes, plaintext: bytes, aad: bytes) -> dict:
    """Sigilla un risultato per il server (usato dai test; il nodo lo fa in JavaScript)."""
    eph = X25519PrivateKey.generate()
    epk = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(server_pub_raw))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk, info=SEAL_INFO).derive(shared)
    nonce, ct = encrypt(key, plaintext, aad)
    return {"epk": b64e(epk), "nonce": b64e(nonce), "ct": b64e(ct)}
