# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Double-entry honey ledger.

Every transaction has a unique key (it can never be recorded twice) and its
entries always sum to zero. An account balance is the sum of its entries.
System accounts:
  system:mint     honey created (welcome bonus, work done by nodes)
  system:escrow   honey reserved for jobs in progress
  system:spend    honey spent on completed jobs
  system:penalty  honey taken away as penalties
"""
import sqlite3

from .db import now


class LedgerError(Exception):
    pass


def user_acct(user_id: int) -> str:
    return f"user:{user_id}"


def post(c: sqlite3.Connection, idem_key: str, kind: str, entries: list[tuple[str, int]], memo: str = "") -> bool:
    """Records a transaction. Returns False if the key has already been used."""
    if sum(a for _, a in entries) != 0:
        raise LedgerError("unbalanced transaction")
    if c.execute("SELECT 1 FROM ledger_tx WHERE idem_key=?", (idem_key,)).fetchone():
        return False
    cur = c.execute("INSERT INTO ledger_tx(idem_key, kind, memo, created) VALUES (?,?,?,?)",
                    (idem_key, kind, memo, now()))
    tx = cur.lastrowid
    c.executemany("INSERT INTO ledger_entries(tx_id, account, amount) VALUES (?,?,?)",
                  [(tx, acct, amt) for acct, amt in entries if amt != 0])
    return True


def balance(c: sqlite3.Connection, account: str) -> int:
    r = c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account=?", (account,)).fetchone()
    return int(r[0])


def history(c: sqlite3.Connection, account: str, limit: int = 50):
    rows = c.execute(
        """SELECT t.created, t.kind, t.memo, e.amount FROM ledger_entries e
           JOIN ledger_tx t ON t.id=e.tx_id WHERE e.account=? ORDER BY t.id DESC LIMIT ?""",
        (account, limit)).fetchall()
    return [dict(r) for r in rows]


def total_is_zero(c: sqlite3.Connection) -> bool:
    return c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries").fetchone()[0] == 0
