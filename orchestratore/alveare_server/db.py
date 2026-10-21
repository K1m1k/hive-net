"""Database SQLite (V1). Un solo file, modalità WAL, scritture serializzate.

Lo schema usa SQL portabile: il passaggio a PostgreSQL + pgvector è previsto
per quando la rete cresce (vedi docs/ROADMAP.md).
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    os TEXT,
    token_hash TEXT UNIQUE NOT NULL,
    created REAL NOT NULL,
    last_seen REAL,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    missed INTEGER NOT NULL DEFAULT 0,
    cheats INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    ops TEXT NOT NULL,                -- es. "clean,stats,embed"
    status TEXT NOT NULL,             -- phase1 | phase2 | done | failed
    created REAL NOT NULL,
    finished REAL,
    n_records INTEGER NOT NULL,
    n_unique INTEGER,
    stats TEXT,                       -- JSON
    reserved INTEGER NOT NULL DEFAULT 0,
    units_distributed REAL NOT NULL DEFAULT 0,
    units_cached REAL NOT NULL DEFAULT 0,
    cost INTEGER,
    memory_deleted INTEGER NOT NULL DEFAULT 0
);
-- originali in attesa: cifrati, cancellati a fine lavoro o allo scadere
CREATE TABLE IF NOT EXISTS staging (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    blob BLOB NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    phase INTEGER NOT NULL,
    op TEXT NOT NULL,
    seq INTEGER NOT NULL,
    first_record INTEGER NOT NULL,
    n_records INTEGER NOT NULL,
    input_sha TEXT NOT NULL,
    input_blob BLOB,                  -- cifrato a riposo, cancellato a fine lavoro
    units REAL NOT NULL,
    status TEXT NOT NULL,             -- queued | running | verifying | done | dead
    result_sha TEXT,
    result_blob BLOB,                 -- cifrato a riposo finché il lavoro non è ricomposto
    from_cache INTEGER NOT NULL DEFAULT 0,
    record_keys TEXT,                 -- fase 2: impronte dei testi del pacchetto (JSON)
    primary_node INTEGER,
    done_at REAL
);
CREATE INDEX IF NOT EXISTS chunks_job ON chunks(job_id, phase, status);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
    kind TEXT NOT NULL,               -- primary | verify | dispute
    status TEXT NOT NULL,             -- queued | leased | done | dead | cancelled
    node_id INTEGER,
    exclude_nodes TEXT NOT NULL DEFAULT '',
    exclude_users TEXT NOT NULL DEFAULT '',
    lease_token TEXT,
    lease_deadline REAL,
    packet_key BLOB,
    attempts INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    finished REAL,
    result_sha TEXT,
    result_blob BLOB,                 -- solo per verifiche/contestazioni, cifrato a riposo
    input_blob BLOB,                  -- solo contestazioni: ingresso proprio, cifrato a riposo
    dispute_id INTEGER
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created);
CREATE TABLE IF NOT EXISTS delivered (
    idem_key TEXT PRIMARY KEY,
    task_id INTEGER NOT NULL,
    at REAL NOT NULL
);
-- risultati già calcolati, riusabili: impronta + operazione + versione
CREATE TABLE IF NOT EXISTS result_cache (
    input_sha TEXT NOT NULL,
    op TEXT NOT NULL,
    version TEXT NOT NULL,
    result_blob BLOB NOT NULL,
    result_sha TEXT NOT NULL,
    created REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (input_sha, op, version)
);
-- memoria centrale: un testo elaborato per impronta, mai duplicato
CREATE TABLE IF NOT EXISTS items (
    content_hash TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    embedding BLOB,                   -- float16
    emb_version TEXT,
    emb_node INTEGER,                 -- nodo che ha calcolato l'embedding
    created REAL NOT NULL,
    last_access REAL,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(content_hash UNINDEXED, text);
CREATE TABLE IF NOT EXISTS item_refs (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    content_hash TEXT NOT NULL,
    PRIMARY KEY (job_id, content_hash)
);
CREATE TABLE IF NOT EXISTS job_records (
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    idx INTEGER NOT NULL,
    content_hash TEXT,               -- testo pulito (NULL se vuoto)
    dup_of INTEGER,                  -- indice del primo record identico
    orig_hash TEXT,                  -- impronta del testo originale (per verificare le contestazioni)
    PRIMARY KEY (job_id, idx)
);
CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    op TEXT NOT NULL,
    chunk_id INTEGER,
    expected_sha TEXT NOT NULL,      -- risultato registrato
    status TEXT NOT NULL,            -- open | confirmed | rejected
    created REAL NOT NULL,
    outcome TEXT
);
-- libro del miele a partita doppia: ogni movimento somma a zero
CREATE TABLE IF NOT EXISTS ledger_tx (
    id INTEGER PRIMARY KEY,
    idem_key TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    memo TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    tx_id INTEGER NOT NULL REFERENCES ledger_tx(id),
    account TEXT NOT NULL,
    amount INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_account ON ledger_entries(account);
CREATE TABLE IF NOT EXISTS penalties (
    id INTEGER PRIMARY KEY,
    node_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    evidence TEXT NOT NULL,          -- JSON: impronte dei risultati a confronto
    amount INTEGER NOT NULL,
    created REAL NOT NULL,
    appeal TEXT,
    appeal_status TEXT
);
CREATE TABLE IF NOT EXISTS work_log (
    node_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    job_id INTEGER NOT NULL,
    records INTEGER NOT NULL,
    units REAL NOT NULL,
    miele INTEGER NOT NULL,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS work_log_user ON work_log(user_id, at);
CREATE TABLE IF NOT EXISTS groups_ (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups_(id),
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    show_in_board INTEGER NOT NULL DEFAULT 1
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.connect() as c:
            c.executescript(SCHEMA)
            # aggiornamenti di database creati con versioni precedenti
            cols = {r[1] for r in c.execute("PRAGMA table_info(job_records)")}
            if "orig_hash" not in cols:
                c.execute("ALTER TABLE job_records ADD COLUMN orig_hash TEXT")

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self):
        """Transazione di scrittura: tutto o niente, una alla volta."""
        with self.write_lock:
            c = self.connect()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def q(self, sql, args=()):
        return self.connect().execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.connect().execute(sql, args).fetchone()


def now() -> float:
    return time.time()
