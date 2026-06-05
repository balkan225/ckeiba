"""
parser.py のユニットテスト（JV-Link不要）
テスト用の模擬O1レコードバイト列を生成して検証する。
"""

import unittest
from parser import parse_o1, LAYOUT, WAKUREN_COMBOS


def make_dummy_o1(
    tansho_odds: list[int] = None,
    fukusho_lo: list[int] = None,
    fukusho_hi: list[int] = None,
) -> bytes:
    """
    テスト用O1レコードを生成（SJIS固定長）。
    オッズはすべて0（発売なし）で初期化し、指定値を上書きする。
    最小サイズ: wakuren末尾 = 550 + 36*6 - 1 = 765 byte
    """
    SIZE = 800
    buf = bytearray(b" " * SIZE)

    # ヘッダ
    buf[0:2] = b"O1"
    buf[2:3] = b"1"          # データ区分=速報
    buf[3:17] = b"20250810120000"  # make_date
    buf[17:25] = b"20250810"       # kaisai_date
    buf[25:27] = b"07"             # 中京
    buf[27:29] = b"03"
    buf[29:31] = b"06"
    buf[31:33] = b"07"
    buf[41:45] = b"1525"           # happ_time

    # 単勝: 28頭 × 6byte（デフォルト "000000"）
    base_t = LAYOUT["tansho_odds_offset"] - 1
    for i in range(28):
        buf[base_t + i * 6: base_t + i * 6 + 6] = b"000000"
    if tansho_odds:
        for i, v in enumerate(tansho_odds):
            if i >= 28:
                break
            buf[base_t + i * 6: base_t + i * 6 + 6] = f"{v:06d}".encode()

    # 複勝: 28頭 × 12byte（lo4 + hi4 + pad4）
    base_f = LAYOUT["fukusho_odds_offset"] - 1
    for i in range(28):
        buf[base_f + i * 12: base_f + i * 12 + 12] = b"000000000000"
    if fukusho_lo:
        for i, v in enumerate(fukusho_lo):
            if i >= 28:
                break
            buf[base_f + i * 12: base_f + i * 12 + 4] = f"{v:04d}".encode()
    if fukusho_hi:
        for i, v in enumerate(fukusho_hi):
            if i >= 28:
                break
            buf[base_f + i * 12 + 4: base_f + i * 12 + 8] = f"{v:04d}".encode()

    # 枠連: 36通り × 6byte
    base_w = LAYOUT["wakuren_odds_offset"] - 1
    for i in range(36):
        buf[base_w + i * 6: base_w + i * 6 + 6] = b"000000"

    return bytes(buf)


class TestO1Parser(unittest.TestCase):

    def test_header_fields(self):
        raw = make_dummy_o1()
        rec = parse_o1(raw)
        self.assertEqual(rec.data_kubun, "1")
        self.assertEqual(rec.kubun_label, "速報")
        self.assertEqual(rec.keibajo_code, "07")
        self.assertEqual(rec.keibajo_name, "中京")
        self.assertEqual(rec.race_num, "07")
        self.assertEqual(rec.happ_time, "1525")

    def test_tansho_all_zero_returns_none(self):
        raw = make_dummy_o1()
        rec = parse_o1(raw)
        self.assertEqual(len(rec.tansho), 28)
        for t in rec.tansho:
            self.assertIsNone(t.odds)

    def test_tansho_values(self):
        # 1番=35倍(350), 2番=52倍(520)
        odds_raw = [350, 520] + [0] * 26
        raw = make_dummy_o1(tansho_odds=odds_raw)
        rec = parse_o1(raw)
        self.assertAlmostEqual(rec.tansho[0].odds, 35.0)
        self.assertAlmostEqual(rec.tansho[1].odds, 52.0)
        self.assertIsNone(rec.tansho[2].odds)

    def test_fukusho_values(self):
        lo_raw = [150, 200] + [0] * 26
        hi_raw = [300, 450] + [0] * 26
        raw = make_dummy_o1(fukusho_lo=lo_raw, fukusho_hi=hi_raw)
        rec = parse_o1(raw)
        self.assertAlmostEqual(rec.fukusho[0].odds_lo, 15.0)
        self.assertAlmostEqual(rec.fukusho[0].odds_hi, 30.0)
        self.assertAlmostEqual(rec.fukusho[1].odds_lo, 20.0)
        self.assertAlmostEqual(rec.fukusho[1].odds_hi, 45.0)

    def test_wakuren_combo_count(self):
        raw = make_dummy_o1()
        rec = parse_o1(raw)
        self.assertEqual(len(rec.wakuren), 36)
        # 最初の組合せは(1,1)
        self.assertEqual(rec.wakuren[0].waku1, 1)
        self.assertEqual(rec.wakuren[0].waku2, 1)
        # 最後は(8,8)
        self.assertEqual(rec.wakuren[-1].waku1, 8)
        self.assertEqual(rec.wakuren[-1].waku2, 8)

    def test_invalid_record_id(self):
        raw = make_dummy_o1()
        buf = bytearray(raw)
        buf[0:2] = b"O2"
        with self.assertRaises(ValueError):
            parse_o1(bytes(buf))

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            parse_o1(b"O1" + b" " * 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
