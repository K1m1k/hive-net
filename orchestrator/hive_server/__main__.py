# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Entry point: python -m hive_server [--host 0.0.0.0] [--port 8000]"""
import argparse
import logging

import uvicorn

from .app import create_app


def main():
    p = argparse.ArgumentParser(description="HiveNet orchestrator server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=True)


if __name__ == "__main__":
    main()
