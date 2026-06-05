"""JRA馬場ページからクッション値・含水率をリアルタイム取得してDBに保存"""
import re
import sys
import sqlite3
import requests
from datetime import date, datetime
from bs4 import BeautifulSoup

BASE = "https://www.jra.go.jp/keiba/baba"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.jra.go.jp/keiba/baba/",
}

WEEKDAY_MAP = {
    "月曜": "月曜日", "火曜": "火曜日", "水曜": "水曜日",
    "木曜": "木曜日", "金曜": "金曜日", "土曜": "土曜日", "日曜": "日曜日",
}


def _fetch(path: str) -> str:
    url = f"{BASE}/{path}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp.content.decode("shift_jis", errors="replace")


def _parse_date(time_str: str) -> tuple[str, str]:
    """'5月24日（日曜）7時00分' → ('2026-05-24', '日曜日')"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日（(\w+)）", time_str)
    if not m:
        return None, None
    month, day, wd_short = int(m.group(1)), int(m.group(2)), m.group(3)
    weekday = WEEKDAY_MAP.get(wd_short, wd_short)

    # 年の推定: 月が現在月より大幅に大きければ前年
    today = date.today()
    year = today.year
    if month > today.month + 1:
        year -= 1

    try:
        d = date(year, month, day)
        return d.isoformat(), weekday
    except ValueError:
        return None, None


def fetch_cushion() -> dict[tuple, float]:
    """クッション値を {(racecourse, measured_date): value} で返す"""
    html = _fetch("_data_cushion.html")
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for sec in soup.find_all(id=re.compile(r"^rc[A-Z]")):
        racecourse = sec.get("title", "")
        for unit in sec.find_all("div", class_="unit"):
            time_div = unit.find("div", class_="time")
            cushion_div = unit.find("div", class_="cushion")
            if not (time_div and cushion_div):
                continue
            measured_date, weekday = _parse_date(time_div.get_text(strip=True))
            if not measured_date:
                continue
            try:
                value = float(cushion_div.get_text(strip=True))
            except ValueError:
                continue
            key = (racecourse, measured_date)
            if key not in result:  # 最新計測を優先（最初に出てくる）
                result[key] = (value, weekday)

    return result


def fetch_moist() -> dict[tuple, dict]:
    """含水率を {(racecourse, measured_date): {turf_mg, turf_4c, dirt_mg, dirt_4c}} で返す"""
    html = _fetch("_data_moist.html")
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    for sec in soup.find_all(id=re.compile(r"^rc[A-Z]")):
        racecourse = sec.get("title", "")
        for unit in sec.find_all("div", class_="unit"):
            time_div = unit.find("div", class_="time")
            if not time_div:
                continue
            measured_date, weekday = _parse_date(time_div.get_text(strip=True))
            if not measured_date:
                continue

            def get_val(parent_class, span_class):
                parent = unit.find("div", class_=parent_class)
                if not parent:
                    return None
                span = parent.find("span", class_=span_class)
                if not span:
                    return None
                try:
                    return float(span.get_text(strip=True))
                except ValueError:
                    return None

            key = (racecourse, measured_date)
            if key not in result:
                result[key] = {
                    "turf_goal_moisture":    get_val("turf", "mg"),
                    "turf_4corner_moisture": get_val("turf", "m4c"),
                    "dirt_goal_moisture":    get_val("dirt", "mg"),
                    "dirt_4corner_moisture": get_val("dirt", "m4c"),
                    "weekday": weekday,
                }

    return result


def merge_records(cushion: dict, moist: dict) -> list[dict]:
    """クッション・含水率を (racecourse, date) をキーにマージ"""
    all_keys = set(cushion) | set(moist)
    records = []
    for key in sorted(all_keys):
        racecourse, measured_date = key
        c_val, c_wd = cushion.get(key, (None, None))
        m_data = moist.get(key, {})
        weekday = c_wd or m_data.get("weekday")

        # 金曜日はスキップ
        if weekday == "金曜日":
            continue

        records.append({
            "racecourse": racecourse,
            "measured_date": measured_date,
            "weekday": weekday,
            "cushion_value": c_val,
            "turf_goal_moisture":    m_data.get("turf_goal_moisture"),
            "turf_4corner_moisture": m_data.get("turf_4corner_moisture"),
            "dirt_goal_moisture":    m_data.get("dirt_goal_moisture"),
            "dirt_4corner_moisture": m_data.get("dirt_4corner_moisture"),
        })
    return records


def save_to_sqlite(records: list[dict], db_path: str) -> int:
    import os
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM cushion_values")
    before = cur.fetchone()[0]
    for r in records:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO cushion_values
                  (racecourse, measured_date, weekday,
                   cushion_value,
                   turf_goal_moisture, turf_4corner_moisture,
                   dirt_goal_moisture, dirt_4corner_moisture)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                r["racecourse"], r["measured_date"], r["weekday"],
                r["cushion_value"],
                r["turf_goal_moisture"], r["turf_4corner_moisture"],
                r["dirt_goal_moisture"], r["dirt_4corner_moisture"],
            ))
        except Exception as e:
            print(f"  SQLite error: {e}")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM cushion_values")
    after = cur.fetchone()[0]
    conn.close()
    return after - before


def save_to_postgres(records: list[dict]) -> int:
    sys.path.insert(0, "..")
    try:
        from config import DB_CONFIG
    except ImportError:
        print("config.py が見つかりません。PostgreSQL スキップ。")
        return 0

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    rows = [
        (r["racecourse"], r["measured_date"], r["weekday"],
         r["cushion_value"],
         r["turf_goal_moisture"], r["turf_4corner_moisture"],
         r["dirt_goal_moisture"], r["dirt_4corner_moisture"])
        for r in records
    ]
    cur.execute("SELECT COUNT(*) FROM cushion_values")
    before = cur.fetchone()[0]
    execute_values(cur, """
        INSERT INTO cushion_values
          (racecourse, measured_date, weekday,
           cushion_value,
           turf_goal_moisture, turf_4corner_moisture,
           dirt_goal_moisture, dirt_4corner_moisture)
        VALUES %s
        ON CONFLICT (measured_date, racecourse) DO NOTHING
    """, rows)
    cur.execute("SELECT COUNT(*) FROM cushion_values")
    after = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return after - before


if __name__ == "__main__":
    import os

    print("クッション値を取得中...")
    cushion = fetch_cushion()
    print(f"  {len(cushion)} 件")

    print("含水率を取得中...")
    moist = fetch_moist()
    print(f"  {len(moist)} 件")

    records = merge_records(cushion, moist)
    print(f"\nマージ結果（金曜除く）: {len(records)} 件")
    for r in records:
        cv = f"クッション={r['cushion_value']}" if r["cushion_value"] else ""
        tm = (f"芝含水(G={r['turf_goal_moisture']},4C={r['turf_4corner_moisture']})"
              if r["turf_goal_moisture"] else "")
        print(f"  {r['measured_date']} {r['racecourse']} {r['weekday']}  {cv}  {tm}")

    # SQLite 保存
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cushion.db")
    n_sqlite = save_to_sqlite(records, db_path)
    print(f"\nSQLite: {n_sqlite} 件追加 ({db_path})")

    # PostgreSQL 保存
    n_pg = save_to_postgres(records)
    print(f"PostgreSQL: {n_pg} 件追加")
