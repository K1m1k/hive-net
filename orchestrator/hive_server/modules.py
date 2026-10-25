# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Registry of the signed WebAssembly modules and of the available operations."""
import json
from dataclasses import dataclass
from pathlib import Path

from .crypto import ServerKeys, b64e, sha256_hex


@dataclass(frozen=True)
class Op:
    name: str          # operation name used in jobs
    module: str        # .wasm file
    version: str
    bytes_per_unit: int  # how many input bytes count as 1 work unit


# Units calibrated on a reference PC; to be recalibrated with real benchmarks.
OPS = {
    "clean": Op("clean", "text_clean", "1", 256 * 1024),
    "stats": Op("stats", "text_stats", "1", 1024 * 1024),
    "embed": Op("embed", "embed_hash", "1", 64 * 1024),
}
# pii_clean only runs at the origin (in the browser), it is never distributed
ORIGIN_MODULES = {"pii_clean": "1"}

EMBED_DIM = 256


def units_for(op: Op, nbytes: int) -> float:
    return max(0.05, nbytes / op.bytes_per_unit)


def signed_payload(name: str, version: str, sha: str) -> bytes:
    return f"hivenet-module|{name}|{version}|{sha}".encode()


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
                raise FileNotFoundError(f"Missing module: {path} (run module-src/build.sh)")
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
