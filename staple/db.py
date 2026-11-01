"""One SQLite file holds the whole made-up world and everything computed from it."""
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "out"
DB_PATH = OUT / "staple.db"


def connect(path=DB_PATH):
    return sqlite3.connect(path)


def fresh(path=DB_PATH):
    """A new, empty database file, replacing any earlier run's."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    return connect(path)
