"""
オッズ時系列グラフ（keibadata DB参照版）

使い方:
  python visualize.py --date 20260606 --basho 05 --race 11
  python visualize.py --date 20260606 --basho 05 --race 11 --top 8
  python visualize.py --date 20260606 --basho 05 --race 11 --horses 9,12,6
  python visualize.py --date 20260606 --basho 05 --race 11 --save
"""

import argparse
import sys
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import rcParams

sys.path.insert(0, r"C:\Users\balka\Desktop\Ckeiba\jvlink")
from odds_db import get_odds_merged, KEIBAJO_MAP

rcParams["font.family"] = "MS Gothic"
rcParams["axes.unicode_minus"] = False


def plot(kaisai_nen, kaisai_gappi, keibajo_code, race_bango,
         top_n=10, horses=None, save=False, output=None):

    df = get_odds_merged(kaisai_nen, kaisai_gappi, keibajo_code, race_bango)
    if df.empty:
        print("データなし")
        return

    keibajo = KEIBAJO_MAP.get(keibajo_code, keibajo_code)
    title = f"{keibajo} {int(race_bango)}R  単勝オッズ時系列  ({kaisai_nen}/{kaisai_gappi})"

    # 対象馬を絞る
    if horses:
        df = df[df["umaban"].isin(horses)]
    else:
        last_ts = df["timestamp"].max()
        top = (df[df["timestamp"] == last_ts]
               .sort_values("tansho_ninki")
               .head(top_n)["umaban"].tolist())
        df = df[df["umaban"].isin(top)]

    fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    # 上段: 単勝オッズ推移
    ax1 = axes[0]
    for umaban, grp in df.groupby("umaban"):
        grp = grp.sort_values("timestamp")
        ax1.plot(grp["timestamp"], grp["tansho_odds"],
                 marker="o", markersize=4, linewidth=1.5, label=f"{int(umaban)}番")

    ax1.set_ylabel("単勝オッズ（倍）", fontsize=11)
    ax1.set_title(title, fontsize=13)
    ax1.legend(loc="upper right", fontsize=9, ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.invert_yaxis()

    # 下段: 複勝帯グラフ
    ax2 = axes[1]
    colors = [line.get_color() for line in ax1.get_lines()]
    for idx, (umaban, grp) in enumerate(df.groupby("umaban")):
        grp = grp.sort_values("timestamp").dropna(subset=["fukusho_lo", "fukusho_hi"])
        if grp.empty:
            continue
        c = colors[idx % len(colors)]
        ax2.fill_between(grp["timestamp"], grp["fukusho_lo"], grp["fukusho_hi"],
                         alpha=0.2, color=c)
        ax2.plot(grp["timestamp"], grp["fukusho_lo"], linewidth=1, linestyle="--", color=c)
        ax2.plot(grp["timestamp"], grp["fukusho_hi"], linewidth=1, linestyle=":", color=c,
                 label=f"{int(umaban)}番")

    ax2.set_ylabel("複勝オッズ（倍）", fontsize=11)
    ax2.set_xlabel("時刻", fontsize=11)
    ax2.legend(loc="upper right", fontsize=9, ncol=2)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=10))
    plt.xticks(rotation=30)
    plt.tight_layout()

    if save:
        out = output or f"odds_{kaisai_nen}{kaisai_gappi}_{keibajo_code}{race_bango}.png"
        plt.savefig(out, dpi=150, bbox_inches="tight")
        print(f"保存: {out}")
    else:
        plt.show()


def parse_args():
    today = datetime.now().strftime("%Y%m%d")
    p = argparse.ArgumentParser(description="オッズ時系列グラフ（keibadata参照）")
    p.add_argument("--date",   default=today)
    p.add_argument("--basho",  required=True, help="競馬場コード")
    p.add_argument("--race",   required=True, help="レース番号")
    p.add_argument("--top",    type=int, default=10)
    p.add_argument("--horses", type=str, default=None, help="馬番カンマ区切り: 9,12,6")
    p.add_argument("--save",   action="store_true")
    p.add_argument("--output", type=str, default=None)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    horses = [int(x) for x in args.horses.split(",")] if args.horses else None
    plot(
        kaisai_nen=args.date[:4],
        kaisai_gappi=args.date[4:],
        keibajo_code=args.basho,
        race_bango=args.race.zfill(2),
        top_n=args.top,
        horses=horses,
        save=args.save,
        output=args.output,
    )
