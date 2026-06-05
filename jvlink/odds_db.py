"""
keibadata DB からオッズ時系列データを取得するモジュール。
JV-Link を使わずに PostgreSQL を直接参照する。
"""

import sys
import pandas as pd
from sqlalchemy import create_engine

sys.path.insert(0, r"C:\Users\balka\Desktop\Ckeiba")
from config import ODDS_DB_CONFIG

KEIBAJO_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


def get_engine():
    c = ODDS_DB_CONFIG
    url = f"postgresql+psycopg2://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['database']}"
    return create_engine(url)


def get_tansho_jikeiretsu(kaisai_nen: str, kaisai_gappi: str,
                          keibajo_code: str, race_bango: str) -> pd.DataFrame:
    """
    単勝オッズ時系列を DataFrame で返す。
    odds列は ÷10 した実オッズ値（float）。
    """
    sql = """
        SELECT
            happyo_tsukihi_jifun,
            umaban::int,
            odds::numeric / 10.0 AS tansho_odds,
            ninki::int            AS tansho_ninki
        FROM odds1_tansho_jikeiretsu
        WHERE kaisai_nen    = %s
          AND kaisai_gappi  = %s
          AND keibajo_code  = %s
          AND race_bango    = %s
        ORDER BY happyo_tsukihi_jifun, umaban::int
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn,
                         params=(kaisai_nen, kaisai_gappi,
                                 keibajo_code, race_bango))
    return df


def get_fukusho_jikeiretsu(kaisai_nen: str, kaisai_gappi: str,
                           keibajo_code: str, race_bango: str) -> pd.DataFrame:
    """複勝オッズ時系列を DataFrame で返す。"""
    sql = """
        SELECT
            happyo_tsukihi_jifun,
            umaban::int,
            odds_saitei::numeric / 10.0 AS fukusho_lo,
            odds_saikou::numeric / 10.0 AS fukusho_hi,
            ninki::int                   AS fukusho_ninki
        FROM odds1_fukusho_jikeiretsu
        WHERE kaisai_nen    = %s
          AND kaisai_gappi  = %s
          AND keibajo_code  = %s
          AND race_bango    = %s
        ORDER BY happyo_tsukihi_jifun, umaban::int
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn,
                         params=(kaisai_nen, kaisai_gappi,
                                 keibajo_code, race_bango))
    return df


def get_odds_merged(kaisai_nen: str, kaisai_gappi: str,
                    keibajo_code: str, race_bango: str) -> pd.DataFrame:
    """単勝+複勝をマージした時系列 DataFrame を返す。"""
    t = get_tansho_jikeiretsu(kaisai_nen, kaisai_gappi, keibajo_code, race_bango)
    f = get_fukusho_jikeiretsu(kaisai_nen, kaisai_gappi, keibajo_code, race_bango)
    if t.empty:
        return t
    df = t.merge(f, on=["happyo_tsukihi_jifun", "umaban"], how="left")
    # happyo_tsukihi_jifun (MMDDHHmm) を datetime に変換
    # 開催年とMMDD部分から日時を組み立てる
    def to_dt(row):
        s = row["happyo_tsukihi_jifun"]  # e.g. "06052343"
        mm, dd, hh, mi = s[0:2], s[2:4], s[4:6], s[6:8]
        return pd.Timestamp(
            year=int(kaisai_nen),
            month=int(mm), day=int(dd),
            hour=int(hh), minute=int(mi)
        )
    df["timestamp"] = df.apply(to_dt, axis=1)
    df["keibajo"] = KEIBAJO_MAP.get(keibajo_code, keibajo_code)
    df["race_bango"] = race_bango
    return df


def list_races(kaisai_nen: str, kaisai_gappi: str) -> pd.DataFrame:
    """指定日の開催レース一覧を返す。"""
    sql = """
        SELECT DISTINCT keibajo_code, race_bango
        FROM odds1_tansho_jikeiretsu
        WHERE kaisai_nen = %s AND kaisai_gappi = %s
        ORDER BY keibajo_code, race_bango::int
    """
    with get_engine().connect() as conn:
        df = pd.read_sql(sql, conn, params=(kaisai_nen, kaisai_gappi))
    df["keibajo"] = df["keibajo_code"].map(KEIBAJO_MAP)
    return df
