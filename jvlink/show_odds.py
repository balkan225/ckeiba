"""
オッズ表示スクリプト（keibadata DB参照版）

使い方:
  python show_odds.py                              # 本日の全レース一覧
  python show_odds.py --date 20260606              # 指定日の全レース一覧
  python show_odds.py --date 20260606 --basho 05 --race 11   # 東京11R
  python show_odds.py --date 20260606 --basho 05 --race 11 --latest  # 最新のみ
"""

import argparse
from datetime import datetime
import sys

sys.path.insert(0, r"C:\Users\balka\Desktop\Ckeiba\jvlink")
from odds_db import get_odds_merged, list_races, KEIBAJO_MAP


def show_race(kaisai_nen, kaisai_gappi, keibajo_code, race_bango, latest_only=False):
    df = get_odds_merged(kaisai_nen, kaisai_gappi, keibajo_code, race_bango)
    if df.empty:
        print(f"データなし: {kaisai_nen}/{kaisai_gappi} {KEIBAJO_MAP.get(keibajo_code, keibajo_code)}{race_bango}R")
        return

    keibajo = KEIBAJO_MAP.get(keibajo_code, keibajo_code)
    snapshots = sorted(df["happyo_tsukihi_jifun"].unique())

    if latest_only:
        snapshots = [snapshots[-1]]

    for snap in snapshots:
        s = snap  # MMDDHHmm
        time_str = f"{s[0:2]}/{s[2:4]} {s[4:6]}:{s[6:8]}"
        sub = df[df["happyo_tsukihi_jifun"] == snap].sort_values("tansho_ninki")
        print(f"\n=== {keibajo} {int(race_bango)}R  {time_str} ({len(sub)}頭) ===")
        print(f"  {'馬番':>4}  {'単勝':>8}  {'人気':>4}  {'複勝下限':>8}  {'複勝上限':>8}")
        print("  " + "-" * 46)
        for _, row in sub.iterrows():
            t  = f"{row['tansho_odds']:.1f}倍"
            lo = f"{row['fukusho_lo']:.1f}" if row.get("fukusho_lo") and row["fukusho_lo"] > 0 else "---"
            hi = f"{row['fukusho_hi']:.1f}" if row.get("fukusho_hi") and row["fukusho_hi"] > 0 else "---"
            nk = f"{int(row['tansho_ninki'])}人気" if row.get("tansho_ninki") else "---"
            print(f"  {int(row['umaban']):>4}番  {t:>8}  {nk:>6}  {lo:>8}  {hi:>8}")


def parse_args():
    today = datetime.now().strftime("%Y%m%d")
    p = argparse.ArgumentParser(description="オッズ時系列表示（keibadata参照）")
    p.add_argument("--date",   default=today, help="開催日 YYYYMMDD")
    p.add_argument("--basho",  default=None,  help="競馬場コード (05=東京 など)")
    p.add_argument("--race",   default=None,  help="レース番号 (01〜12)")
    p.add_argument("--latest", action="store_true", help="最新スナップショットのみ表示")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    nen  = args.date[:4]
    gappi = args.date[4:]

    if args.basho and args.race:
        race_bango = args.race.zfill(2)
        show_race(nen, gappi, args.basho, race_bango, latest_only=args.latest)
    else:
        races = list_races(nen, gappi)
        if races.empty:
            print(f"{args.date} のデータはありません")
            sys.exit()
        print(f"\n{args.date} の開催レース一覧:")
        for _, row in races.iterrows():
            print(f"  {row['keibajo']}({row['keibajo_code']}) {int(row['race_bango']):2d}R")
        print(f"\n例: python show_odds.py --date {args.date} --basho {races.iloc[0]['keibajo_code']} --race {races.iloc[0]['race_bango']} --latest")
