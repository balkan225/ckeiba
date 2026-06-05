"""SQLite → PostgreSQL 移行スクリプト
config.py の DB_CONFIG を使って接続し、
cushion_values テーブルを作成後に全データを投入する。
"""
import sqlite3
import sys
import os

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_CONFIG
from setup_db import DB_PATH

# ──────────────────────────────────────────
# PostgreSQL テーブル定義
# ──────────────────────────────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cushion_values (
    id                    SERIAL PRIMARY KEY,
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

INSERT_SQL = """
INSERT INTO cushion_values
    (racecourse, measured_date, weekday,
     cushion_value, turf_goal_moisture, turf_4corner_moisture,
     dirt_goal_moisture, dirt_4corner_moisture)
VALUES %s
ON CONFLICT (measured_date, racecourse) DO NOTHING
"""


def main():
    # SQLite から全データ取得
    sq = sqlite3.connect(DB_PATH)
    sq.row_factory = sqlite3.Row
    rows = sq.execute("""
        SELECT racecourse, measured_date, weekday,
               cushion_value, turf_goal_moisture, turf_4corner_moisture,
               dirt_goal_moisture, dirt_4corner_moisture
        FROM cushion_values
        ORDER BY measured_date, racecourse
    """).fetchall()
    sq.close()
    print(f"SQLite から {len(rows)} 件取得")

    # PostgreSQL へ接続
    pg = psycopg2.connect(**DB_CONFIG)
    cur = pg.cursor()

    # テーブル作成
    cur.execute(CREATE_SQL)
    pg.commit()
    print("テーブル作成 (既存の場合はスキップ)")

    # バルクインサート
    values = [
        (r["racecourse"], r["measured_date"], r["weekday"],
         r["cushion_value"], r["turf_goal_moisture"], r["turf_4corner_moisture"],
         r["dirt_goal_moisture"], r["dirt_4corner_moisture"])
        for r in rows
    ]
    psycopg2.extras.execute_values(cur, INSERT_SQL, values, page_size=500)
    pg.commit()

    cur.execute("SELECT COUNT(*) FROM cushion_values")
    total = cur.fetchone()[0]
    cur.close()
    pg.close()
    print(f"PostgreSQL 格納完了: {total} 件")


if __name__ == "__main__":
    main()
