import os
import sqlite3
import json
import hashlib
from datetime import datetime, timedelta

DB_PATH = "/data/ssdihunter.db"
os.makedirs("/data", exist_ok=True)
CACHE_TTL_HOURS = 12


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS keyword_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT UNIQUE NOT NULL,
            query_text TEXT NOT NULL,
            results TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seed_term TEXT NOT NULL,
            mode TEXT NOT NULL,
            result_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS saved_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS list_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL REFERENCES saved_lists(id) ON DELETE CASCADE,
            keyword TEXT NOT NULL,
            google_rank INTEGER,
            ddg_rank INTEGER,
            best_rank INTEGER,
            source_count INTEGER,
            frequency INTEGER,
            intent TEXT,
            opportunity_score REAL
        );
    """)
    conn.commit()
    conn.close()


def cache_key(query_type: str, params: dict) -> str:
    raw = json.dumps({"type": query_type, **params}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_cached(query_hash: str):
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    row = conn.execute(
        "SELECT results FROM keyword_cache WHERE query_hash=? AND created_at > ?",
        (query_hash, cutoff)
    ).fetchone()
    conn.close()
    return json.loads(row["results"]) if row else None


def set_cache(query_hash: str, query_text: str, results: list):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO keyword_cache (query_hash, query_text, results) VALUES (?,?,?)",
        (query_hash, query_text, json.dumps(results))
    )
    conn.commit()
    conn.close()


def add_history(seed_term: str, mode: str, result_count: int):
    conn = get_conn()
    conn.execute(
        "INSERT INTO search_history (seed_term, mode, result_count) VALUES (?,?,?)",
        (seed_term, mode, result_count)
    )
    conn.commit()
    conn.close()


def get_history(limit: int = 50):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_history ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_list(name: str) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO saved_lists (name) VALUES (?)", (name,))
    list_id = cur.lastrowid
    conn.commit()
    conn.close()
    return list_id


def get_lists():
    conn = get_conn()
    rows = conn.execute(
        """SELECT sl.id, sl.name, sl.created_at, COUNT(lk.id) as keyword_count
           FROM saved_lists sl
           LEFT JOIN list_keywords lk ON lk.list_id = sl.id
           GROUP BY sl.id ORDER BY sl.created_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_list_keywords(list_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM list_keywords WHERE list_id=? ORDER BY opportunity_score DESC",
        (list_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_keywords_to_list(list_id: int, keywords: list):
    conn = get_conn()
    conn.executemany(
        """INSERT INTO list_keywords
           (list_id, keyword, google_rank, ddg_rank, best_rank, source_count,
            frequency, intent, opportunity_score)
           VALUES (:list_id, :keyword, :google_rank, :ddg_rank, :best_rank,
                   :source_count, :frequency, :intent, :opportunity_score)""",
        [{"list_id": list_id,
          "google_rank":      kw.get("google_rank"),
          "ddg_rank":         kw.get("ddg_rank"),
          "best_rank":        kw.get("best_rank"),
          "source_count":     kw.get("source_count", 1),
          "frequency":        kw.get("frequency", 1),
          "intent":           kw.get("intent", "General SSDI"),
          "opportunity_score": kw.get("opportunity_score", 0),
          "keyword":          kw.get("keyword", ""),
        } for kw in keywords]
    )
    conn.commit()
    conn.close()


def delete_list(list_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM saved_lists WHERE id=?", (list_id,))
    conn.commit()
    conn.close()


def delete_list_keyword(keyword_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM list_keywords WHERE id=?", (keyword_id,))
    conn.commit()
    conn.close()
