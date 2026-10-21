"""Libro del miele a partita doppia.

Ogni movimento ha una chiave unica (non può essere registrato due volte) e
le sue righe sommano sempre a zero. Il saldo di un conto è la somma delle sue
righe. Conti di sistema:
  system:mint     miele creato (benvenuto, lavoro dei nodi)
  system:escrow   miele riservato per i lavori in corso
  system:spend    miele speso per lavori completati
  system:penalty  miele tolto per penalità
"""
import sqlite3

from .db import now


class LedgerError(Exception):
    pass


def user_acct(user_id: int) -> str:
    return f"user:{user_id}"


def post(c: sqlite3.Connection, idem_key: str, kind: str, entries: list[tuple[str, int]], memo: str = "") -> bool:
    """Registra un movimento. Restituisce False se la chiave era già stata usata."""
    if sum(a for _, a in entries) != 0:
        raise LedgerError("movimento non bilanciato")
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
