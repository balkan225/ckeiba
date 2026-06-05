"""1枚のPDFで抽出テストを行うスクリプト。
成功したら全件処理スクリプト (scraper.py) へ進む。
"""
import sys
import json
import requests
from parser import parse_pdf

TEST_URL  = "https://www.jra.go.jp/keiba/baba/archive/2025pdf/tokyo01.pdf"
FILENAME  = "tokyo01.pdf"
YEAR      = 2025
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; JRA-CushionDB/1.0)"}


def main():
    print(f"Downloading: {TEST_URL}")
    resp = requests.get(TEST_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"Downloaded: {len(resp.content):,} bytes")

    records = parse_pdf(resp.content, FILENAME, YEAR)

    if not records:
        print("FAILED: レコードが1件も抽出できませんでした。", file=sys.stderr)
        sys.exit(1)

    print(f"\nOK: {len(records)} 件のレコードを抽出しました。\n")

    for i, r in enumerate(records):
        print(
            f"  [{i+1:2d}] {r['measured_date']} ({r['weekday']}) "
            f"コース={r['course_type']} "
            f"クッション={r['cushion_value']} "
            f"kai_day={'前日' if r['kai_day'] is None else r['kai_day']}"
        )

    print("\n--- 1件目の全フィールド ---")
    print(json.dumps(records[0], ensure_ascii=False, indent=2))

    # JSONファイルにも保存（文字化け確認用）
    out_path = "cushion_test_output.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"\n全件を {out_path} に保存しました。")


if __name__ == "__main__":
    main()
