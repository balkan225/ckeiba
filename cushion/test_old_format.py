"""旧形式PDF (2024/2022/2020) のパーステスト"""
import json
import requests
from parser import parse_pdf

TESTS = [
    ("https://www.jra.go.jp/keiba/baba/archive/2024pdf/tokyo01.pdf", "tokyo01.pdf", 2024),
    ("https://www.jra.go.jp/keiba/baba/archive/2022pdf/tokyo01.pdf", "tokyo01.pdf", 2022),
    ("https://www.jra.go.jp/keiba/baba/archive/2020pdf/tokyo01.pdf", "tokyo01.pdf", 2020),
]
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JRA-CushionDB/1.0)"}

for url, fname, year in TESTS:
    print(f"\n--- {year} ---")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    records = parse_pdf(resp.content, fname, year)
    print(f"  {len(records)} records")
    for r in records[:3]:
        print(f"  {r['measured_date']} kai_day={r['kai_day']} "
              f"cushion={r['cushion_value']} "
              f"turf_goal={r['turf_goal_moisture']}")

    # JSON保存
    out = f"test_old_{year}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"  saved: {out}")
