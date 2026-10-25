# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""The server dependencies must include all known security fixes.

Run from the orchestrator directory:  ../.venv/bin/python -m unittest tests.test_dependencies -v
If a test fails:  ../.venv/bin/pip install -U -r requirements.txt
"""
import re
import unittest
from importlib.metadata import version
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

# minimum version with the known fixes (October 2026) and why
SECURITY_MINIMUMS = {
    "starlette": ((1, 1, 0), "CVE-2026-48710 BadHost (fixed in 1.0.1) and CVE-2026-48818 StaticFiles (1.1.0)"),
    "cryptography": ((49, 0, 0), "CVE-2026-39892 memory safety (46.0.7) and CVE-2026-69248 certificate validation (49.0.0)"),
}


def num(v: str) -> tuple:
    parts = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


class Dependencies(unittest.TestCase):
    def test_installed_versions_are_patched(self):
        for pkg, (minimum, reason) in SECURITY_MINIMUMS.items():
            with self.subTest(pkg=pkg):
                v = version(pkg)
                self.assertGreaterEqual(num(v), minimum, f"{pkg} {v} is vulnerable: {reason}")

    def test_requirements_exclude_vulnerable_versions(self):
        pins = {}
        for r in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
            r = r.split("#")[0].strip()
            m = re.match(r"([A-Za-z0-9_.-]+)\s*>=\s*([0-9.]+)", r)
            if m:
                pins[m.group(1).lower()] = num(m.group(2))
        for pkg, (minimum, reason) in SECURITY_MINIMUMS.items():
            with self.subTest(pkg=pkg):
                self.assertIn(pkg, pins, f"{pkg} has no minimum version in requirements.txt")
                self.assertGreaterEqual(pins[pkg], minimum, f"requirements.txt allows a vulnerable {pkg}: {reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
