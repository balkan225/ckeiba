"""
JV-Link 時系列オッズ収集スクリプト

使い方:
  python collector.py                         # config.yaml を使用
  python collector.py --race 2025081007030607
  python collector.py --race 2025081007030607 --interval 30 --duration 120
  python collector.py --race 2025081007030607 --once   # 1回だけ取得して終了
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from parser import parse_o1, record_to_rows

# JV-Link のリターンコード（仕様書 付表）
RC_OK_CONTINUE = 1        # データあり、継続
RC_OK_EOF = 0             # データなし（終端）
RC_NO_DATA = -1           # 対象データなし
RC_NEED_SETUP = -101      # JVInit 未実施
RC_AUTH_ERROR = -111      # 認証エラー
RC_FILE_ERROR = -200      # ファイルエラー

RC_MESSAGES = {
    0:    "データなし（終端）",
    -1:   "対象データなし",
    -3:   "通信エラー",
    -101: "JVInit未実施",
    -111: "認証エラー（会員登録・ソフトID確認）",
    -114: "使用不可ソフトID",
    -200: "ファイルエラー",
    -201: "ダウンロードエラー",
    -202: "解凍エラー",
}

MAX_RETRY = 3
RETRY_WAIT_SEC = 10


def load_config(path: str = "config.yaml") -> dict:
    config_path = Path(__file__).parent / path
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_jvlink():
    """JV-Link COM オブジェクトを取得"""
    try:
        import win32com.client as win32
        return win32.Dispatch("JVDTLab.JVLink")
    except ImportError:
        raise RuntimeError("pywin32 がインストールされていません: pip install pywin32")
    except Exception as e:
        raise RuntimeError(f"JV-Link の初期化に失敗しました: {e}")


def fetch_rt_odds(race_id: str, dataspec: str = "0B31") -> list[bytes]:
    """
    JVRTOpen で速報オッズを1回取得。bytes リストで返す。
    リターンコードに応じた例外処理付き。
    """
    jv = get_jvlink()

    rc_init = jv.JVInit("UNKNOWN")
    # JVInit は void の場合と 0/負の場合がある（実装差異）
    if isinstance(rc_init, int) and rc_init < 0:
        msg = RC_MESSAGES.get(rc_init, f"不明エラー({rc_init})")
        raise RuntimeError(f"JVInit 失敗: {msg}")

    rc_open = jv.JVRTOpen(dataspec, race_id)
    # win32com から tuple で返る場合の対応
    if isinstance(rc_open, tuple):
        rc_open = rc_open[0]

    if rc_open < 0:
        jv.JVClose()
        msg = RC_MESSAGES.get(rc_open, f"不明エラー({rc_open})")
        raise RuntimeError(f"JVRTOpen 失敗: {msg}")

    results = []
    while True:
        r = jv.JVRead("", 256000, "")
        if isinstance(r, tuple):
            ret_code, data, read_size = r[0], r[1], r[2]
        else:
            break

        if ret_code < 0:
            msg = RC_MESSAGES.get(ret_code, f"不明エラー({ret_code})")
            print(f"  JVRead エラー: {msg}", file=sys.stderr)
            break
        elif ret_code == RC_OK_EOF:
            break

        # str → bytes (SJIS) 変換
        if isinstance(data, str):
            data = data.encode("cp932", errors="replace")
        results.append(data)

    jv.JVClose()
    return results


def fetch_with_retry(race_id: str, dataspec: str) -> list[bytes]:
    """リトライ付きオッズ取得"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return fetch_rt_odds(race_id, dataspec)
        except RuntimeError as e:
            print(f"  [{attempt}/{MAX_RETRY}] 取得失敗: {e}", file=sys.stderr)
            if attempt < MAX_RETRY:
                print(f"  {RETRY_WAIT_SEC}秒後にリトライ...", file=sys.stderr)
                time.sleep(RETRY_WAIT_SEC)
    raise RuntimeError(f"最大リトライ回数({MAX_RETRY})を超えました")


def collect(race_id: str, interval_sec: int, duration_min: int,
            dataspec: str = "0B31", output_dir: str = "odds_data",
            once: bool = False):
    """
    時系列オッズ取得ループ。
    once=True の場合は1回だけ取得して終了。
    """
    os.makedirs(output_dir, exist_ok=True)
    ts_start = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(output_dir, f"odds_{race_id}_{ts_start}.csv")

    fieldnames = ["timestamp", "race_id", "kubun", "make_date",
                  "umaban", "tansho_odds", "fukusho_lo", "fukusho_hi"]

    iterations = 1 if once else (duration_min * 60) // interval_sec

    print(f"出力先: {csv_path}")
    print(f"取得回数: {'1回（--once）' if once else iterations}回 "
          f"/ 間隔: {interval_sec}秒 / 継続: {duration_min}分")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i in range(iterations):
            ts = datetime.now().isoformat()
            print(f"\n[{ts}] 取得中 ({i + 1}/{iterations})...")
            try:
                raw_list = fetch_with_retry(race_id, dataspec)
                if not raw_list:
                    print("  データなし（レース未発売またはrace_id確認）")
                for raw in raw_list:
                    rec = parse_o1(raw)
                    rows = record_to_rows(rec, ts, race_id)
                    writer.writerows(rows)
                    f.flush()
                    print(f"  [{rec.kubun_label}] {rec.keibajo_name} "
                          f"{rec.race_num}R / {len(rows)}頭分 取得")
                    # 単勝上位5頭を表示
                    top5 = sorted(
                        [r for r in rows if r["tansho_odds"]],
                        key=lambda x: x["tansho_odds"]
                    )[:5]
                    for r in top5:
                        print(f"    {r['umaban']:2}番  単勝 {r['tansho_odds']:6.1f}倍  "
                              f"複勝 {r['fukusho_lo'] or '---'}〜{r['fukusho_hi'] or '---'}倍")
            except Exception as e:
                print(f"  エラー: {e}", file=sys.stderr)

            if not once and i < iterations - 1:
                time.sleep(interval_sec)

    print(f"\n完了: {csv_path}")
    return csv_path


def parse_args():
    cfg = load_config()
    p = argparse.ArgumentParser(description="JV-Link 時系列オッズ取得")
    p.add_argument("--race", default=cfg.get("race_id"),
                   help="レースID (例: 2025081007030607)")
    p.add_argument("--interval", type=int, default=cfg.get("interval_sec", 60),
                   help="取得間隔（秒）")
    p.add_argument("--duration", type=int, default=cfg.get("duration_min", 60),
                   help="継続時間（分）")
    p.add_argument("--dataspec", default=cfg.get("dataspec", "0B31"),
                   help="データ種別ID (0B31/0B32/0B33)")
    p.add_argument("--output-dir", default=cfg.get("output_dir", "odds_data"),
                   help="CSV出力先ディレクトリ")
    p.add_argument("--once", action="store_true",
                   help="1回だけ取得して終了")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    collect(
        race_id=args.race,
        interval_sec=args.interval,
        duration_min=args.duration,
        dataspec=args.dataspec,
        output_dir=args.output_dir,
        once=args.once,
    )
