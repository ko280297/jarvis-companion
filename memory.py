import sqlite3
import time
import numpy as np
import requests

DB_PATH = "memories.db"
EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "all-minilm"


def _embed(text):
    """Turn text into a vector (a list of numbers that captures its meaning)."""
    r = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "input": text}, timeout=60)
    r.raise_for_status()
    v = np.array(r.json()["embeddings"][0], dtype=np.float32)
    return v / np.linalg.norm(v)


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
        self.db.commit()

    def add(self, text):
        vec = _embed(text)
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

    def search(self, query, k=3, min_score=0.4):
        rows = self.db.execute("SELECT text, vec FROM memories").fetchall()
        if not rows:
            return []
        q = _embed(query)
        scored = [(float(np.dot(q, np.frombuffer(v, dtype=np.float32))), t) for t, v in rows]
        scored.sort(reverse=True)
        return [(round(s, 2), t) for s, t in scored[:k] if s >= min_score]

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


if __name__ == "__main__":
    m = MemoryStore("test_memories.db")
    m.add("My meeting with Rahul is on Friday about the budget.")
    m.add("I kept my passport in the blue drawer.")
    for q in ["When is my meeting with Rahul?",
              "Where is my passport?",
              "What is my favourite colour?"]:
        print(q, "->", m.search(q))