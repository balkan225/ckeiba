"""JRA馬場クッション値PDF パースモジュール

2025形式 (新):
  1ページに全データが11列テーブルとしてまとめられている
  col0:開催日次  col1:測定日  col2:曜日  col3:コース
  col4:クッション計測時刻(skip) col5:クッション値
  col6:含水率計測時刻(skip) col7:芝ゴール前 col8:芝4コーナー
  col9:ダートゴール前 col10:ダート4コーナー

旧形式 (2020-2024):
  セクションごとに別テーブル (2×4 cushion + 5×5 moisture の繰り返し)
  2020-2021は cushion テーブルなし (測定未実施)
  日付はページの生テキストから抽出
"""
import io
import re
from datetime import date, timedelta
import pdfplumber

_WEEKDAYS_JP = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]

def _weekday_from_date(date_str: str) -> str:
    """'YYYY-MM-DD' → 日本語曜日名"""
    d = date.fromisoformat(date_str)
    return _WEEKDAYS_JP[d.weekday()]

RACECOURSE_MAP = {
    "sapporo":   "札幌",
    "hakodate":  "函館",
    "fukushima": "福島",
    "niigata":   "新潟",
    "tokyo":     "東京",
    "nakayama":  "中山",
    "chukyo":    "中京",
    "kyoto":     "京都",
    "hanshin":   "阪神",
    "kokura":    "小倉",
}


# ────────────────────────────────────────────
# 共通ユーティリティ
# ────────────────────────────────────────────

def _parse_date(cell, year: int) -> str | None:
    """'M月D日'（文字化け可）→ 'YYYY-MM-DD'"""
    if not cell:
        return None
    m = re.search(r"(\d{1,2})\D+(\d{1,2})", str(cell))
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year}-{month:02d}-{day:02d}"
    return None


def _float(cell) -> float | None:
    if not cell:
        return None
    try:
        return float(str(cell).strip())
    except ValueError:
        return None


def _parse_racecourse(filename: str) -> str:
    m = re.match(r"([a-z]+)\d+\.pdf", filename, re.IGNORECASE)
    if not m:
        raise ValueError(f"ファイル名が想定外: {filename}")
    return RACECOURSE_MAP.get(m.group(1).lower(), m.group(1))


# ────────────────────────────────────────────
# 新形式 (2025+)
# ────────────────────────────────────────────

def _parse_new_format(pdf_bytes: bytes, filename: str, year: int) -> list[dict]:
    """11列テーブル形式"""
    racecourse = _parse_racecourse(filename)
    records = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) < 11:
                        continue
                    cushion_raw = row[5]
                    if not cushion_raw:
                        continue
                    if not re.fullmatch(r"\d+\.?\d*", str(cushion_raw).strip()):
                        continue
                    measured_date = _parse_date(row[1], year)
                    if not measured_date:
                        continue
                    records.append({
                        "racecourse":            racecourse,
                        "measured_date":         measured_date,
                        "weekday":               str(row[2]).strip() if row[2] else None,
                        "cushion_value":         _float(row[5]),
                        "turf_goal_moisture":    _float(row[7]),
                        "turf_4corner_moisture": _float(row[8]),
                        "dirt_goal_moisture":    _float(row[9]),
                        "dirt_4corner_moisture": _float(row[10]),
                    })

    return records


# ────────────────────────────────────────────
# 旧形式 (2020-2024) ヘルパー
# ────────────────────────────────────────────

def _parse_section_dates(line: str, year: int) -> list[str] | None:
    """旧形式セクションヘッダーから3日分の日付を抽出。

    同月: 'YYYY年M月D1日〜D3日'  → 4グループ (year, month, start, end)
    月跨: 'YYYY年M1月D1日〜M2月D2日' → 5グループ (year, m1, d1, m2, d2)
    文字化けした非数字は \\D+ でまとめて対応。
    """
    # ── 月跨ぎ (5グループ) を先に試す ──
    m5 = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})", line)
    if m5:
        y, m1, d1, m2, d2 = [int(x) for x in m5.groups()]
        if y == year and m1 != m2 and 1 <= m1 <= 12 and 1 <= m2 <= 12:
            try:
                start = date(y, m1, d1)
                dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
                if dates[-1] == f"{y}-{m2:02d}-{d2:02d}":
                    return dates
            except ValueError:
                pass

    # ── 同月 (4グループ) ──
    m4 = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})", line)
    if not m4:
        return None
    y, month, start_day, end_day = [int(x) for x in m4.groups()]
    if y != year or not (1 <= month <= 12):
        return None
    if start_day < 1 or end_day > 31 or end_day <= start_day:
        return None
    if end_day - start_day != 2:
        return None
    return [f"{y}-{month:02d}-{d:02d}" for d in range(start_day, end_day + 1)]


def _is_cushion_table(table: list) -> bool:
    """2行×4列 かつ 後半3列が数値 → クッション値テーブル"""
    if len(table) != 2 or not table[1] or len(table[1]) != 4:
        return False
    try:
        for j in range(1, 4):
            if table[1][j]:
                float(str(table[1][j]))
        return True
    except (ValueError, TypeError):
        return False


def _is_moisture_table(table: list) -> bool:
    """5行×≥5列 かつ data列(2,3,4)が数値 → 含水率テーブル"""
    if len(table) < 5 or not table[1] or len(table[1]) < 5:
        return False
    try:
        for ri in range(1, 5):
            for ci in range(2, 5):
                v = table[ri][ci] if len(table[ri]) > ci else None
                if v:
                    float(str(v))
        return True
    except (ValueError, TypeError):
        return False


# ────────────────────────────────────────────
# 旧形式 (2020-2024) メインパーサー
# ────────────────────────────────────────────

def _parse_old_format(pdf_bytes: bytes, filename: str, year: int) -> list[dict]:
    """セクション別テーブル形式 (2020-2024)"""
    racecourse = _parse_racecourse(filename)
    records = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # 生テキストからセクション日付を抽出
            text = page.extract_text() or ""
            date_sections = []
            for line in text.splitlines():
                dates = _parse_section_dates(line, year)
                if dates:
                    date_sections.append(dates)

            if not date_sections:
                continue

            tables = page.extract_tables()
            cushion_tables  = [t for t in tables if _is_cushion_table(t)]
            moisture_tables = [t for t in tables if _is_moisture_table(t)]

            has_cushion = len(cushion_tables) > 0

            for sec_idx, dates in enumerate(date_sections):
                if sec_idx >= len(moisture_tables):
                    break

                # クッション値 (2022以前は None)
                cushion_vals = [None, None, None]
                if has_cushion and sec_idx < len(cushion_tables):
                    ct = cushion_tables[sec_idx]
                    if len(ct) >= 2 and len(ct[1]) >= 4:
                        cushion_vals = [_float(ct[1][j]) for j in range(1, 4)]

                # 含水率
                mt = moisture_tables[sec_idx]
                safe = lambda t, r, c: _float(t[r][c]) if len(t[r]) > c else None
                turf_goal    = [safe(mt, 1, j) for j in range(2, 5)]
                turf_4corner = [safe(mt, 2, j) for j in range(2, 5)]
                dirt_goal    = [safe(mt, 3, j) for j in range(2, 5)]
                dirt_4corner = [safe(mt, 4, j) for j in range(2, 5)]

                for k in range(3):
                    records.append({
                        "racecourse":            racecourse,
                        "measured_date":         dates[k],
                        "weekday":               _weekday_from_date(dates[k]),
                        "cushion_value":         cushion_vals[k],
                        "turf_goal_moisture":    turf_goal[k],
                        "turf_4corner_moisture": turf_4corner[k],
                        "dirt_goal_moisture":    dirt_goal[k],
                        "dirt_4corner_moisture": dirt_4corner[k],
                    })

    return records


# ────────────────────────────────────────────
# 公開API
# ────────────────────────────────────────────

def parse_pdf(pdf_bytes: bytes, filename: str, year: int) -> list[dict]:
    """新形式を試み、0件なら旧形式にフォールバック。"""
    records = _parse_new_format(pdf_bytes, filename, year)
    if not records:
        records = _parse_old_format(pdf_bytes, filename, year)
    return records
