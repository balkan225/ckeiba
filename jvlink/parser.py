"""
JVData O1レコード（速報オッズ：単勝・複勝・枠連）パーサー
レイアウト出典: mykeibadbテーブル一覧.xlsx（ODDS1/ODDS1_TANSHO/ODDS1_FUKUSHO/ODDS1_WAKUREN）

O1固定ヘッダ(76バイト) + 単勝繰返し(8×28=224) + 複勝繰返し(12×28=336) + 枠連繰返し(9×36=324)
"""

from dataclasses import dataclass, field
from typing import Optional


RECORD_ID = "O1"

# ヘッダ フィールド（1-based start, length）
# RACE_CODE(16バイト) = KAISAI_NEN(4)+KAISAI_GAPPI(4)+KEIBAJO_CODE(2)+KAISAI_KAIJI(2)+KAISAI_NICHIJI(2)+RACE_BANGO(2)
HEADER = {
    "record_id":            (1,   2),
    "data_kubun":           (3,   1),
    "data_sakusei_nengappi":(4,   8),   # 作成年月日 yyyymmdd
    "race_code":            (12, 16),   # RACE_CODE全体
    "kaisai_nen":           (12,  4),   # 開催年
    "kaisai_gappi":         (16,  4),   # 開催月日 mmdd
    "keibajo_code":         (20,  2),   # 競馬場コード
    "kaisai_kaiji":         (22,  2),   # 回次
    "kaisai_nichiji":       (24,  2),   # 日目
    "race_bango":           (26,  2),   # レース番号
    "happyo_tsukihi_jifun": (28,  8),   # 発表月日時分
    "toroku_tosu":          (36,  2),   # 登録頭数
    "shusso_tosu":          (38,  2),   # 出走頭数
    "hatsubai_flag_tansho": (40,  1),
    "hatsubai_flag_fukusho":(41,  1),
    "hatsubai_flag_wakuren":(42,  1),
    "fukusho_chakubarai":   (43,  1),
    "tansho_hyosu_gokei":   (44, 11),
    "fukusho_hyosu_gokei":  (55, 11),
    "wakuren_hyosu_gokei":  (66, 11),
    # ヘッダ末尾 = 76バイト
}

# 繰り返し部オフセット（1-based）
TANSHO_BASE   = 77   # 単勝: 8バイト×28スロット固定（UMABAN1+ODDS4+NINKI2+PAD1）
FUKUSHO_BASE  = 77 + 8 * 28        # = 301
WAKUREN_BASE  = 77 + 8 * 28 + 12 * 28  # = 637

# 実測レイアウト（2026-06-05確認）:
# UMABAN(1) + ODDS(4) + NINKI(2) + PAD(1) = 8バイト
# 馬番は1桁ASCII（一の位のみ: 1-9は'1'-'9'、10-19は'0'-'9'）
# 馬番の実際の値はインデックス位置から算出（出走頭数に依存）
# → 実際はUMABANをインデックス順に読み、空白エントリをスキップする
TANSHO_UNIT   = 8
FUKUSHO_UNIT  = 12   # UMABAN(1)+ODDS_LO(4)+ODDS_HI(4)+NINKI(2)+PAD(1) = 12
WAKUREN_UNIT  = 9    # KUMIBAN(2)+ODDS(5)+NINKI(2) = 9

MAX_UMABAN    = 28
WAKUREN_COUNT = 36

KUBUN_MAP = {"1": "速報", "2": "速報", "3": "速報", "4": "確定", "5": "確定(修正)", "0": "削除"}

KEIBAJO_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

WAKUREN_COMBOS = [(w1, w2) for w1 in range(1, 9) for w2 in range(w1, 9)]


@dataclass
class TanshoOdds:
    umaban: int
    odds: Optional[float]   # None = 発売なし/取消
    ninki: Optional[int]


@dataclass
class FukushoOdds:
    umaban: int
    odds_lo: Optional[float]
    odds_hi: Optional[float]
    ninki: Optional[int]


@dataclass
class WakurenOdds:
    waku1: int
    waku2: int
    odds: Optional[float]
    ninki: Optional[int]


@dataclass
class O1Record:
    data_kubun: str
    kubun_label: str
    make_date: str
    race_code: str
    kaisai_nen: str
    kaisai_gappi: str
    keibajo_code: str
    keibajo_name: str
    kaisai_kaiji: str
    kaisai_nichiji: str
    race_bango: str
    toroku_tosu: str
    shusso_tosu: str
    tansho: list = field(default_factory=list)
    fukusho: list = field(default_factory=list)
    wakuren: list = field(default_factory=list)


def _get(raw: bytes, start_1based: int, length: int) -> str:
    """1-basedオフセットで SJIS バイト列から文字列取得"""
    s = start_1based - 1
    return raw[s:s + length].decode("cp932", errors="replace").strip()


def _odds4(s: str) -> Optional[float]:
    """4桁オッズ文字列 → float（÷10）。0000/'    '/'****'/'???-' → None"""
    t = s.strip()
    if not t or not t.isdigit() or int(t) == 0:
        return None
    return int(t) / 10.0


def _odds5(s: str) -> Optional[float]:
    """5桁オッズ文字列 → float（÷10）。枠連用"""
    t = s.strip()
    if not t or not t.isdigit() or int(t) == 0:
        return None
    return int(t) / 10.0


def _ninki(s: str) -> Optional[int]:
    t = s.strip()
    if not t or not t.isdigit():
        return None
    return int(t)


def parse_o1(raw: bytes) -> O1Record:
    if len(raw) < 76:
        raise ValueError(f"O1レコードが短すぎます: {len(raw)} bytes")

    record_id = _get(raw, 1, 2)
    if record_id != RECORD_ID:
        raise ValueError(f"O1レコードIDが一致しません: '{record_id}'")

    kubun = _get(raw, 3, 1)
    rec = O1Record(
        data_kubun=kubun,
        kubun_label=KUBUN_MAP.get(kubun, kubun),
        make_date=_get(raw, 4, 8),
        race_code=_get(raw, 12, 16),
        kaisai_nen=_get(raw, 12, 4),
        kaisai_gappi=_get(raw, 16, 4),
        keibajo_code=_get(raw, 20, 2),
        keibajo_name=KEIBAJO_MAP.get(_get(raw, 20, 2), _get(raw, 20, 2)),
        kaisai_kaiji=_get(raw, 22, 2),
        kaisai_nichiji=_get(raw, 24, 2),
        race_bango=_get(raw, 26, 2),
        toroku_tosu=_get(raw, 36, 2),
        shusso_tosu=_get(raw, 38, 2),
    )

    def _restore_umaban(ones_char: str, prev_uma: int) -> int:
        """
        UMABAN一の位ASCIIと直前の馬番から実際の馬番を復元する。
        馬番は昇順格納。一の位が前回以下になったら繰り上がり。
        例: prev=9, ones='0' → 10, prev=14, ones='5' → 15
        """
        ones = int(ones_char)
        tens = prev_uma // 10
        candidate = tens * 10 + ones
        if candidate <= prev_uma:
            candidate += 10
        return candidate

    # 単勝（28スロット × 8バイト: UMABAN1+ODDS4+NINKI2+PAD1）
    prev_uma = 0
    for i in range(MAX_UMABAN):
        base = TANSHO_BASE + i * TANSHO_UNIT
        chunk = _get(raw, base, 8)
        if not chunk.strip():
            continue
        ones_char = chunk[0]
        if not ones_char.isdigit():
            continue
        umaban   = _restore_umaban(ones_char, prev_uma)
        odds_str  = chunk[1:5]
        ninki_str = chunk[5:7]
        rec.tansho.append(TanshoOdds(
            umaban=umaban,
            odds=_odds4(odds_str),
            ninki=_ninki(ninki_str),
        ))
        prev_uma = umaban

    # 複勝（28スロット × 12バイト: UMABAN1+ODDS_LO4+ODDS_HI4+NINKI2+PAD1）
    prev_uma = 0
    for i in range(MAX_UMABAN):
        base = FUKUSHO_BASE + i * FUKUSHO_UNIT
        chunk = _get(raw, base, 12)
        if not chunk.strip():
            continue
        ones_char = chunk[0]
        if not ones_char.isdigit():
            continue
        umaban    = _restore_umaban(ones_char, prev_uma)
        lo_str    = chunk[1:5]
        hi_str    = chunk[5:9]
        ninki_str = chunk[9:11]
        rec.fukusho.append(FukushoOdds(
            umaban=umaban,
            odds_lo=_odds4(lo_str),
            odds_hi=_odds4(hi_str),
            ninki=_ninki(ninki_str),
        ))
        prev_uma = umaban

    # 枠連（36通り × 9バイト: KUMIBAN2+ODDS5+NINKI2）
    for idx, (w1, w2) in enumerate(WAKUREN_COMBOS):
        base = WAKUREN_BASE + idx * WAKUREN_UNIT
        odds_str  = _get(raw, base + 2, 5)
        ninki_str = _get(raw, base + 7, 2)
        rec.wakuren.append(WakurenOdds(
            waku1=w1, waku2=w2,
            odds=_odds5(odds_str),
            ninki=_ninki(ninki_str),
        ))

    return rec


def record_to_rows(rec: O1Record, timestamp: str, race_id: str) -> list[dict]:
    """単勝・複勝オッズを CSV 行リストに変換"""
    fukusho_map = {f.umaban: f for f in rec.fukusho}
    rows = []
    # 出走頭数（確定前は00）→ 登録頭数で代用
    try:
        shusso = int(rec.shusso_tosu) if rec.shusso_tosu.strip().isdigit() else 0
        toroku = shusso if shusso > 0 else int(rec.toroku_tosu)
    except Exception:
        toroku = 28

    for t in rec.tansho:
        if t.odds is None:
            continue
        if t.umaban > toroku:  # 登録頭数を超えた馬番はゴミデータ
            continue
        f = fukusho_map.get(t.umaban)
        rows.append({
            "timestamp":    timestamp,
            "race_id":      race_id,
            "kubun":        rec.kubun_label,
            "make_date":    rec.make_date,
            "keibajo":      rec.keibajo_name,
            "race_bango":   rec.race_bango,
            "umaban":       t.umaban,
            "tansho_odds":  t.odds,
            "tansho_ninki": t.ninki,
            "fukusho_lo":   f.odds_lo if f else None,
            "fukusho_hi":   f.odds_hi if f else None,
        })
    return rows
