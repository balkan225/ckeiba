"""SQLiteデータベースとテーブルの初期化"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cushion.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cushion_values (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    racecourse            TEXT    NOT NULL,
    measured_date         DATE    NOT NULL,
    weekday               TEXT,
    cushion_value         REAL,
    turf_goal_moisture    REAL,
    turf_4corner_moisture REAL,
    dirt_goal_moisture    REAL,
    dirt_4corner_moisture REAL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (measured_date, racecourse)
)
"""

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_CREATE_SQL)
    conn.commit()
    conn.close()
    print(f"DB initialized: {DB_PATH}")

if __name__ == "__main__":
    setup_db()
