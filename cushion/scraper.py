"""JRA馬場クッション値 全件自動収集スクリプト

アーカイブページ (2020〜2025) からPDF一覧を取得し、
順次ダウンロード・パース・SQLite格納を行う。
"""
import re
import time
import logging
import sqlite3

import requests
from bs4 import BeautifulSoup

from setup_db import DB_PATH, setup_db
from parser import parse_pdf

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────
ARCHIVE_URLS = [
    "https://www.jra.go.jp/keiba/baba/archive/",        # 2026（当年・随時更新）
    "https://www.jra.go.jp/keiba/baba/archive/2025.html",
    "https://www.jra.go.jp/keiba/baba/archive/2024.html",
    "https://www.jra.go.jp/keiba/baba/archive/2023.html",
    "https://www.jra.go.jp/keiba/baba/archive/2022.html",
]
BASE_URL    = "https://www.jra.go.jp"
HEADERS     = {"User-Agent": "Mozilla/5.0 (compatible; JRA-CushionDB/1.0)"}
SLEEP_SEC   = 1.5   # リクエスト間隔

_INSERT_SQL = """
INSERT OR IGNORE INTO cushion_values
    (racecourse, measured_date, weekday,
     cushion_value, turf_goal_moisture, turf_4corner_moisture,
     dirt_goal_moisture, dirt_4corner_moisture)
VALUES
    (:racecourse, :measured_date, :weekday,
     :cushion_value, :turf_goal_moisture, :turf_4corner_moisture,
     :dirt_goal_moisture, :dirt_4corner_moisture)
"""

# ──────────────────────────────────────────
# ログ設定
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler("cushion_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────
# ヘルパー関数
# ──────────────────────────────────────────
def get_pdf_urls(archive_url: str) -> list[str]:
    """アーカイブページのHTMLからPDF URLリストを返す"""
    resp = requests.get(archive_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            full = href if href.startswith("http") else BASE_URL + href
            urls.append(full)
    return urls


def year_from_url(url: str) -> int | None:
    """URLから年を抽出。例: '.../2025pdf/tokyo01.pdf' → 2025"""
    m = re.search(r"/(\d{4})(?:pdf)?/", url)
    return int(m.group(1)) if m else None


def process_pdf(pdf_url: str, conn: sqlite3.Connection) -> tuple[int, int]:
    """
    1つのPDFをDL・パース・DB格納。
    Returns: (parsed_count, inserted_count)
    """
    year     = year_from_url(pdf_url)
    filename = pdf_url.split("/")[-1]

    resp = requests.get(pdf_url, headers=HEADERS, timeout=60)
    resp.raise_for_status()

    records = parse_pdf(resp.content, filename, year)
    records = [r for r in records if r.get("weekday") != "金曜日"]
    if not records:
        log.warning("レコードなし: %s", pdf_url)
        return 0, 0

    inserted = 0
    for rec in records:
        cur = conn.execute(_INSERT_SQL, rec)
        inserted += cur.rowcount
    conn.commit()

    return len(records), inserted


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────
def main():
    setup_db()
    conn = sqlite3.connect(DB_PATH)

    total_parsed   = 0
    total_inserted = 0
    skipped        = 0

    for archive_url in ARCHIVE_URLS:
        log.info("=== アーカイブ: %s ===", archive_url)

        try:
            pdf_urls = get_pdf_urls(archive_url)
            log.info("PDF数: %d", len(pdf_urls))
        except Exception as exc:
            log.error("アーカイブ取得失敗 %s: %s", archive_url, exc)
            continue

        time.sleep(SLEEP_SEC)

        for pdf_url in pdf_urls:
            try:
                parsed, inserted = process_pdf(pdf_url, conn)
                total_parsed   += parsed
                total_inserted += inserted
                log.info("OK  %s  解析=%d 挿入=%d", pdf_url, parsed, inserted)
            except Exception as exc:
                log.error("NG  %s  %s", pdf_url, exc)
                skipped += 1

            time.sleep(SLEEP_SEC)

    conn.close()
    log.info(
        "完了 — 解析合計=%d 挿入合計=%d スキップ=%d",
        total_parsed, total_inserted, skipped,
    )


if __name__ == "__main__":
    main()
