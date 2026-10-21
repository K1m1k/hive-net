"""Registro dei moduli WebAssembly firmati e delle operazioni disponibili."""
import json
from dataclasses import dataclass
from pathlib import Path

from .crypto import ServerKeys, b64e, sha256_hex


@dataclass(frozen=True)
class Op:
    name: str          # nome dell'operazione nei lavori
    module: str        # file .wasm
    version: str
    bytes_per_unit: int  # quanti byte di ingresso valgono 1 unità di lavoro (cella)


# Unità calibrate su un PC di riferimento; da ritarare con i benchmark reali.
OPS = {
    "clean": Op("clean", "text_clean", "1", 256 * 1024),
    "stats": Op("stats", "text_stats", "1", 1024 * 1024),
    "embed": Op("embed", "embed_hash", "1", 64 * 1024),
}
# pii_clean gira solo sul nodo di origine o nel browser, non viene distribuito
ORIGIN_MODULES = {"pii_clean": "1"}

EMBED_DIM = 256


def units_for(op: Op, nbytes: int) -> float:
    return max(0.05, nbytes / op.bytes_per_unit)


def signed_payload(name: str, version: str, sha: str) -> bytes:
    return f"alveare-module|{name}|{version}|{sha}".encode()


class ModuleRegistry:
    def __init__(self, modules_dir: Path, keys: ServerKeys):
        self.dir = modules_dir
        self.keys = keys
        self.manifest = []
        self.files = {}
        wanted = {op.module: op.version for op in OPS.values()} | ORIGIN_MODULES
        for name, version in sorted(wanted.items()):
            path = modules_dir / f"{name}.wasm"
            if not path.exists():
                raise FileNotFoundError(f"Modulo mancante: {path} (eseguire moduli-sorgenti/build.sh)")
            data = path.read_bytes()
            sha = sha256_hex(data)
            self.files[name] = data
            self.manifest.append({
                "name": name, "version": version, "sha256": sha, "size": len(data),
                "signature": b64e(keys.sign(signed_payload(name, version, sha))),
            })

    def manifest_json(self) -> str:
        return json.dumps({"modules": self.manifest,
                           "ops": {k: {"module": v.module, "version": v.version} for k, v in OPS.items()}})
