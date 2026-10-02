import re
import sqlite3
import time
import numpy as np
import requests

DB_PATH = "memories.db"
EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "all-minilm"
KEEP_ALIVE = "30m"     # keep the small embedding model loaded, so searches stay fast


def _core(text):
    """What a memory is about, without bookkeeping like '(saved on ...)' or '[date: ...]'."""
    return re.sub(r"\s*\[date:.*?\]", "", text.split(" (saved on")[0]).strip()


def _embed(text):
    """Turn text into a vector (a list of numbers that captures its meaning)."""
    r = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "input": text,
                                       "keep_alive": KEEP_ALIVE}, timeout=60)
    r.raise_for_status()
    v = np.array(r.json()["embeddings"][0], dtype=np.float32)
    return v / np.linalg.norm(v)


def embed_many(texts):
    """Embed many texts in ONE call (much faster than one call per text)."""
    r = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "input": list(texts),
                                       "keep_alive": KEEP_ALIVE}, timeout=60)
    r.raise_for_status()
    vecs = np.array(r.json()["embeddings"], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


class MemoryStore:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memories "
            "(id INTEGER PRIMARY KEY, text TEXT, created REAL, vec BLOB)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS list_items "
            "(id INTEGER PRIMARY KEY, list TEXT, item TEXT, created REAL)"
        )
        self.db.commit()

    # ---------- Free-form memories ----------
    def add(self, text):
        vec = _embed(_core(text))          # search by meaning of the fact itself, not the date note
        self.db.execute(
            "INSERT INTO memories (text, created, vec) VALUES (?, ?, ?)",
            (text, time.time(), vec.tobytes()),
        )
        self.db.commit()

    def all(self):
        return [t for (t,) in self.db.execute("SELECT text FROM memories ORDER BY id")]

    def forget_last(self):
        row = self.db.execute(
            "SELECT id, text FROM memories ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.db.execute("DELETE FROM memories WHERE id = ?", (row[0],))
        self.db.commit()
        return row[1]

    def forget_all(self):
        """Erase every memory AND every list item."""
        count = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        count += self.db.execute("SELECT COUNT(*) FROM list_items").fetchone()[0]
        self.db.execute("DELETE FROM memories")
        self.db.execute("DELETE FROM list_items")
        self.db.commit()
        return count

    def reindex(self):
        """Re-embed every saved memory using only its core text (run once after updating this file)."""
        rows = self.db.execute("SELECT id, text FROM memories").fetchall()
        for rid, text in rows:
            vec = _embed(_core(text))
            self.db.execute("UPDATE memories SET vec = ? WHERE id = ?", (vec.tobytes(), rid))
        self.db.commit()
        return len(rows)

    def search(self, query, k=3, min_score=0.5):
        rows = self.db.execute("SELECT text, vec FROM memories").fetchall()
        if not rows:
            return []
        q = _embed(query)
        scored = [(float(np.dot(q, np.frombuffer(v, dtype=np.float32))), t) for t, v in rows]
        scored.sort(reverse=True)
        return [(round(s, 2), t) for s, t in scored[:k] if s >= min_score]

    # ---------- Lists (grocery, ideas, schedule, ...) ----------
    def list_add(self, name, item):
        self.db.execute(
            "INSERT INTO list_items (list, item, created) VALUES (?, ?, ?)",
            (name, item, time.time()),
        )
        self.db.commit()

    def list_get(self, name):
        return [i for (i,) in self.db.execute(
            "SELECT item FROM list_items WHERE list = ? ORDER BY id", (name,))]

    def list_names(self):
        return [n for (n,) in self.db.execute("SELECT DISTINCT list FROM list_items")]

    def list_remove(self, name, fragment):
        row = self.db.execute(
            "SELECT id FROM list_items WHERE list = ? AND lower(item) LIKE ? ORDER BY id DESC LIMIT 1",
            (name, f"%{fragment.lower()}%"),
        ).fetchone()
        if not row:
            return False
        self.db.execute("DELETE FROM list_items WHERE id = ?", (row[0],))
        self.db.commit()
        return True

    def list_clear(self, name):
        self.db.execute("DELETE FROM list_items WHERE list = ?", (name,))
        self.db.commit()

    # ---------- Settings (home city, user name, voice, ...) ----------
    def set_setting(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        self.db.commit()

    def get_setting(self, key):
        row = self.db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None