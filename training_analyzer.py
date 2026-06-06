"""
坂路調教評価スクリプト（JRA-VAN PostgreSQL対応版）

データソース:
  jvd_se  … 成績テーブル
              data_kubun='2': 出馬表（枠番・馬番確定、木〜金曜）
              data_kubun='1': 出走馬名表（出走確定、水〜木曜）
  jvd_tk  … 特別登録馬（出走予定レース＋登録馬リスト、火曜）
              torokuba_joho_001〜300 に [3:13] で血統登録番号が埋め込まれている
  jvd_um  … 競走馬マスタ（馬名取得）
  jvd_hc  … 坂路調教（ラップタイム）
  jvd_wc  … ウッドチップ調教

データソース選択（config.DATA_SOURCE）:
  "auto" → jvd_se data_kubun='2' → '1' → jvd_tk の順で自動選択
  "tk"   → 常に jvd_tk を使用
  "se"   → 常に jvd_se を優先（kubun='2' → '1'）

調教取得ルール:
  - 期間: レース前週の土曜日（以降）〜レース前日まで
  - 選択: 期間内で 4Fトータルタイム(time_gokei_4f) が最速の1本
  - 除外: 測定不良（0000/000）
"""

import math
import psycopg2
import psycopg2.extras
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import config as cfg


# ── 1. 調教分類ロジック ────────────────────────────────────────────────────

def classify_training(l2: float, l1: float) -> str:
    """ラスト2F(l2)とラスト1F(l1)から A3/A2/A1/B3/B2/B1 を返す。"""
    if l1 < l2:  # 加速
        if l1 < 12.0:
            return "A3"
        elif 12.0 <= l2 < 13.0 and 12.0 <= l1 < 13.0:
            return "A2"
        elif 12.0 <= l1 < 13.0:
            return "A1"
    elif l1 > l2:  # 減速
        if l2 < 12.0:
            return "B3"
        elif 12.0 <= l2 < 13.0 and 12.0 <= l1 < 13.0:
            return "B2"
        elif 12.0 <= l2 < 13.0 and l1 >= 13.0:
            return "B1"
    return "-"


def classify_wc(l1: float) -> str:
    """ウッドチップ: ラスト1F(l1)のみで A3/A2/- を返す。"""
    if l1 < 11.0:
        return "A3"
    elif l1 < 12.0:
        return "A2"
    return "-"


def prev_saturday(race_date_str: str) -> str:
    """レース日(YYYYMMDD文字列)から前週土曜日(YYYYMMDD文字列)を計算する。
    例: 2026-05-31(日) → 2026-05-23(前週土曜)
        2026-05-30(土) → 2026-05-23(前週土曜)
    """
    d = date(int(race_date_str[:4]), int(race_date_str[4:6]), int(race_date_str[6:]))
    # weekday(): Mon=0 ... Sat=5, Sun=6
    days_since_sat = (d.weekday() - 5) % 7   # 土=0, 日=1, 月=2 ...
    this_sat = d - timedelta(days=days_since_sat)
    return (this_sat - timedelta(days=7)).strftime("%Y%m%d")



# ── 2. データ取得 ──────────────────────────────────────────────────────────

def fetch_data() -> tuple[list[dict], str]:
    """
    出走馬リストを取得（DATA_SOURCE 設定に従い jvd_se or jvd_tk を使用）し、
    jvd_um で馬名、jvd_hc/jvd_wc で「前週土曜以降・最速トータルタイム」の調教を付与する。
    戻り値: (rows, source)  source = 'se2' / 'se1' / 'tk'
    """
    today = date.today()
    until = today + timedelta(days=cfg.DAYS_AHEAD)
    today_str = today.strftime("%Y%m%d")
    until_str = until.strftime("%Y%m%d")

    torokuba_cols = ", ".join(f"torokuba_joho_{i:03d}" for i in range(1, 301))

    conn = psycopg2.connect(**cfg.DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── Step 0: クッション値を一括ロード ──────────────────────────
            cur.execute("SELECT racecourse, measured_date, cushion_value FROM cushion_values")
            cushion_map: dict[tuple, float] = {
                (r["racecourse"], r["measured_date"].strftime("%Y%m%d")): r["cushion_value"]
                for r in cur.fetchall()
            }

            # ── Step 1: データソース設定確認 ────────────────────────────
            src_cfg = getattr(cfg, "DATA_SOURCE", "auto")  # "auto" / "tk" / "se"

            # ── Step 2: 出走馬リストの取得（優先度マージ） ──────────────
            # key=(kaisai_nen, kaisai_tsukihi, keibajo_code, race_bango, ketto)
            # priority: 2=se2, 1=se1, 0=tk
            entry_map: dict[tuple, tuple[dict, int]] = {}

            def _add_entry(e: dict, prio: int) -> None:
                key = (e["kaisai_nen"], e["kaisai_tsukihi"], e["keibajo_code"],
                       e["race_bango"], e["ketto_toroku_bango"])
                if key not in entry_map or prio > entry_map[key][1]:
                    entry_map[key] = (e, prio)

            # jvd_se から取得 (auto または se の場合)
            if src_cfg in ("auto", "se"):
                cur.execute("""
                    SELECT DISTINCT ON (se.kaisai_nen, se.kaisai_tsukihi, se.keibajo_code,
                                        se.race_bango, se.ketto_toroku_bango)
                           se.ketto_toroku_bango, se.kaisai_nen, se.kaisai_tsukihi,
                           se.keibajo_code, se.race_bango, se.data_kubun,
                           se.wakuban, se.umaban,
                           se.bataiju, se.tansho_odds, se.tansho_ninkijun,
                           COALESCE(ra.kyosomei_hondai, '') AS kyosomei_hondai,
                           ra.tenko_code
                    FROM jvd_se se
                    LEFT JOIN jvd_ra ra
                        ON  ra.kaisai_nen     = se.kaisai_nen
                        AND ra.kaisai_tsukihi = se.kaisai_tsukihi
                        AND ra.keibajo_code   = se.keibajo_code
                        AND ra.race_bango     = se.race_bango
                    WHERE (se.kaisai_nen || se.kaisai_tsukihi) BETWEEN %s AND %s
                      AND se.data_kubun IN ('1', '2')
                      AND se.ijo_kubun_code = '0'
                    ORDER BY se.kaisai_nen, se.kaisai_tsukihi, se.keibajo_code,
                             se.race_bango, se.ketto_toroku_bango, se.data_kubun DESC
                """, [today_str, until_str])
                for row in cur.fetchall():
                    ketto = (row["ketto_toroku_bango"] or "").strip()
                    if not ketto:
                        continue
                    prio = 2 if row["data_kubun"] == "2" else 1
                    _add_entry({
                        "kaisai_nen":         row["kaisai_nen"],
                        "kaisai_tsukihi":     row["kaisai_tsukihi"],
                        "race_date_str":      row["kaisai_nen"] + row["kaisai_tsukihi"],
                        "keibajo_code":       row["keibajo_code"],
                        "race_bango":         row["race_bango"],
                        "kyosomei_hondai":    (row["kyosomei_hondai"] or "").strip(),
                        "ketto_toroku_bango": ketto,
                        "wakuban":            row.get("wakuban"),
                        "umaban":             row.get("umaban"),
                        "bataiju":            row.get("bataiju"),
                        "tansho_odds":        row.get("tansho_odds"),
                        "tansho_ninkijun":    row.get("tansho_ninkijun"),
                        "tenko_code":         row.get("tenko_code"),
                    }, prio)

            # jvd_tk から取得（auto: jvd_seがカバーしていないレースのみ補完、tk: 全レース）
            if src_cfg in ("auto", "tk"):
                # jvd_se がカバー済みのレースキーセット（auto時はこのレースはtkを使わない）
                se_covered_races: set[tuple] = {
                    (e["kaisai_nen"], e["kaisai_tsukihi"], e["keibajo_code"], e["race_bango"])
                    for e, _ in entry_map.values()
                }
                cur.execute(f"""
                    SELECT kaisai_nen, kaisai_tsukihi, keibajo_code, race_bango,
                           kyosomei_hondai, toroku_tosu, {torokuba_cols}
                    FROM jvd_tk
                    WHERE (kaisai_nen || kaisai_tsukihi) BETWEEN %s AND %s
                    ORDER BY kaisai_nen, kaisai_tsukihi, keibajo_code, race_bango
                """, [today_str, until_str])
                for row in cur.fetchall():
                    race_key = (row["kaisai_nen"], row["kaisai_tsukihi"],
                                row["keibajo_code"], row["race_bango"])
                    # autoの場合: jvd_seがすでにカバーしているレースはスキップ
                    if src_cfg == "auto" and race_key in se_covered_races:
                        continue
                    n = int(row.get("toroku_tosu") or 0)
                    race_date_str = row["kaisai_nen"] + row["kaisai_tsukihi"]
                    for i in range(1, n + 1):
                        val = row.get(f"torokuba_joho_{i:03d}", "") or ""
                        if len(val) >= 13:
                            ketto = val[3:13].strip()
                            if ketto:
                                _add_entry({
                                    "kaisai_nen":         row["kaisai_nen"],
                                    "kaisai_tsukihi":     row["kaisai_tsukihi"],
                                    "race_date_str":      race_date_str,
                                    "keibajo_code":       row["keibajo_code"],
                                    "race_bango":         row["race_bango"],
                                    "kyosomei_hondai":    (row["kyosomei_hondai"] or "").strip(),
                                    "ketto_toroku_bango": ketto,
                                    "wakuban":            None,
                                    "umaban":             None,
                                }, 0)  # 最低優先度

            horse_entries = [e for e, _ in entry_map.values()]
            ketto_set     = {e["ketto_toroku_bango"] for e in horse_entries}

            if not horse_entries:
                return [], "none"

            # データソース表示ラベルを決定
            priorities = {p for _, p in entry_map.values()}
            if 2 in priorities and 0 in priorities:
                source = "mixed"
            elif 2 in priorities:
                source = "se2"
            elif 1 in priorities and 0 in priorities:
                source = "mixed"
            elif 1 in priorities:
                source = "se1"
            else:
                source = "tk"
            _SRC_LABEL = {
                "se2":   "出馬表 (jvd_se data_kubun=2)",
                "se1":   "出走馬名表 (jvd_se data_kubun=1)",
                "tk":    "特別登録 (jvd_tk)",
                "mixed": "混在 (jvd_se + jvd_tk補完)",
                "none":  "データなし",
            }
            print(f"  データソース: {_SRC_LABEL.get(source, source)}")

            ketto_list = list(ketto_set)
            ph_all = ", ".join(["%s"] * len(ketto_list))

            # ── Step 2c: 出走レース情報（距離・コース）を jvd_ra から取得 ──
            cur.execute("""
                SELECT kaisai_nen, kaisai_tsukihi, keibajo_code, race_bango,
                       kyori, track_code, tenko_code,
                       babajotai_code_shiba, babajotai_code_dirt
                FROM jvd_ra
                WHERE (kaisai_nen || kaisai_tsukihi) BETWEEN %s AND %s
            """, [today_str, until_str])
            ra_race_map: dict[tuple, dict] = {
                (r["kaisai_nen"], r["kaisai_tsukihi"], r["keibajo_code"], r["race_bango"]): dict(r)
                for r in cur.fetchall()
            }
            for e in horse_entries:
                ra = ra_race_map.get(
                    (e["kaisai_nen"], e["kaisai_tsukihi"], e["keibajo_code"], e["race_bango"]), {}
                )
                e["race_kyori"]             = ra.get("kyori")
                e["race_track_code"]        = ra.get("track_code")
                e["race_babajotai_shiba"]   = ra.get("babajotai_code_shiba")
                e["race_babajotai_dirt"]    = ra.get("babajotai_code_dirt")
                # tenko は jvd_se JOIN で既に取得しているが jvd_ra からも補完
                if not e.get("tenko_code"):
                    e["tenko_code"] = ra.get("tenko_code")

            # ── Step 3: jvd_um から馬名を一括取得 ─────────────────────────
            cur.execute(
                f"SELECT ketto_toroku_bango, bamei, chokyoshimei_ryakusho FROM jvd_um WHERE ketto_toroku_bango IN ({ph_all})",
                ketto_list
            )
            um_map: dict[str, tuple] = {
                r["ketto_toroku_bango"]: (
                    (r["bamei"] or "").strip(),
                    (r["chokyoshimei_ryakusho"] or "").strip(),
                )
                for r in cur.fetchall()
            }

            # ── Step 3b: 過去走を jvd_se から取得（コース適性情報含む） ──
            cur.execute(f"""
                SELECT t.ketto_toroku_bango, t.kaisai_nen, t.kaisai_tsukihi,
                       t.keibajo_code, t.race_bango, t.kakutei_chakujun,
                       t.kyakushitsu_hantei, t.wakuban, t.bataiju,
                       ra.kyori, ra.track_code,
                       ra.babajotai_code_shiba, ra.babajotai_code_dirt
                FROM (
                    SELECT ketto_toroku_bango, kaisai_nen, kaisai_tsukihi,
                           keibajo_code, race_bango, kakutei_chakujun,
                           kyakushitsu_hantei, wakuban, bataiju,
                           ROW_NUMBER() OVER (
                               PARTITION BY ketto_toroku_bango
                               ORDER BY kaisai_nen DESC, kaisai_tsukihi DESC, race_bango DESC
                           ) AS rn
                    FROM jvd_se
                    WHERE ketto_toroku_bango IN ({ph_all})
                      AND (kaisai_nen || kaisai_tsukihi) < %s
                      AND kakutei_chakujun <> '00'
                ) t
                LEFT JOIN jvd_ra ra
                    ON  ra.kaisai_nen     = t.kaisai_nen
                    AND ra.kaisai_tsukihi = t.kaisai_tsukihi
                    AND ra.keibajo_code   = t.keibajo_code
                    AND ra.race_bango     = t.race_bango
                WHERE t.rn <= 30
                ORDER BY t.ketto_toroku_bango, t.rn
            """, ketto_list + [today_str])
            past_se_map: dict[str, list[dict]] = defaultdict(list)
            for r in cur.fetchall():
                past_se_map[r["ketto_toroku_bango"]].append(dict(r))

            # ── Step 3c: 過去走の調教データを日付別バッチ取得 ────────────
            past_hc_map: dict[tuple, dict] = {}   # (ketto, race_date_str) → hc row
            past_wc_map: dict[tuple, dict] = {}   # (ketto, race_date_str) → wc row
            past_date_groups: dict[str, set[str]] = defaultdict(set)
            for ketto, races in past_se_map.items():
                for race in races:
                    rd = race["kaisai_nen"] + race["kaisai_tsukihi"]
                    past_date_groups[rd].add(ketto)

            for rd, kettos in past_date_groups.items():
                cutoff_p = prev_saturday(rd)
                kl_p = list(kettos)
                ph_p = ", ".join(["%s"] * len(kl_p))
                cur.execute(f"""
                    SELECT DISTINCT ON (ketto_toroku_bango)
                        ketto_toroku_bango, chokyo_nengappi,
                        time_gokei_4f, lap_time_4f, lap_time_3f, lap_time_2f, lap_time_1f
                    FROM jvd_hc
                    WHERE ketto_toroku_bango IN ({ph_p})
                      AND chokyo_nengappi >= %s AND chokyo_nengappi < %s
                      AND time_gokei_4f <> '0000'
                      AND lap_time_2f   <> '000' AND lap_time_1f <> '000'
                    ORDER BY ketto_toroku_bango, time_gokei_4f ASC
                """, kl_p + [cutoff_p, rd])
                for r in cur.fetchall():
                    past_hc_map[(r["ketto_toroku_bango"], rd)] = dict(r)

                cur.execute(f"""
                    SELECT DISTINCT ON (ketto_toroku_bango)
                        ketto_toroku_bango, chokyo_nengappi,
                        time_gokei_6f, lap_time_6f,
                        time_gokei_5f, lap_time_5f,
                        lap_time_4f, lap_time_3f, lap_time_2f, lap_time_1f
                    FROM jvd_wc
                    WHERE ketto_toroku_bango IN ({ph_p})
                      AND chokyo_nengappi >= %s AND chokyo_nengappi < %s
                      AND time_gokei_6f <> '0000'
                      AND lap_time_1f   <> '000'
                    ORDER BY ketto_toroku_bango, time_gokei_6f ASC
                """, kl_p + [cutoff_p, rd])
                for r in cur.fetchall():
                    past_wc_map[(r["ketto_toroku_bango"], rd)] = dict(r)

            # ── Step 4: レース日ごとに「前週土曜以降・最速」調教を取得 ─────
            # レース日が同じ馬はまとめて1クエリで取得
            hc_map: dict[str, dict] = {}   # ketto → hc row

            date_groups: dict[str, set[str]] = defaultdict(set)
            for entry in horse_entries:
                date_groups[entry["race_date_str"]].add(entry["ketto_toroku_bango"])

            wc_map: dict[str, dict] = {}   # ketto → jvd_wc row

            for race_date_str, kettos in date_groups.items():
                cutoff = prev_saturday(race_date_str)   # 前週土曜 YYYYMMDD
                kl = list(kettos)
                ph = ", ".join(["%s"] * len(kl))

                _hc_sql = f"""
                    SELECT DISTINCT ON (ketto_toroku_bango)
                        ketto_toroku_bango, chokyo_nengappi, chokyo_jikoku, tracen_kubun,
                        time_gokei_4f, time_gokei_3f, time_gokei_2f,
                        lap_time_4f, lap_time_3f, lap_time_2f, lap_time_1f
                    FROM jvd_hc
                    WHERE ketto_toroku_bango IN ({ph})
                      AND chokyo_nengappi >= %s AND chokyo_nengappi < %s
                      AND time_gokei_4f <> '0000'
                      AND lap_time_2f   <> '000' AND lap_time_1f <> '000'
                    ORDER BY ketto_toroku_bango, time_gokei_4f ASC
                """
                _wc_sql = f"""
                    SELECT DISTINCT ON (ketto_toroku_bango)
                        ketto_toroku_bango, chokyo_nengappi, chokyo_jikoku, tracen_kubun, course,
                        time_gokei_6f, lap_time_6f,
                        time_gokei_5f, lap_time_5f,
                        time_gokei_4f, lap_time_4f,
                        time_gokei_3f, lap_time_3f,
                        time_gokei_2f, lap_time_2f,
                        lap_time_1f
                    FROM jvd_wc
                    WHERE ketto_toroku_bango IN ({ph})
                      AND chokyo_nengappi >= %s AND chokyo_nengappi < %s
                      AND time_gokei_6f <> '0000'
                      AND lap_time_1f   <> '000'
                    ORDER BY ketto_toroku_bango, time_gokei_6f ASC
                """

                # 坂路
                cur.execute(_hc_sql, kl + [cutoff, race_date_str])
                for r in cur.fetchall():
                    hc_map[r["ketto_toroku_bango"]] = dict(r)

                # ウッドチップ（6F基準）
                cur.execute(_wc_sql, kl + [cutoff, race_date_str])
                for r in cur.fetchall():
                    wc_map[r["ketto_toroku_bango"]] = dict(r)

            # ── Step 5: 結合 ───────────────────────────────────────────────
            results: list[dict] = []
            for entry in horse_entries:
                ketto = entry["ketto_toroku_bango"]
                hc    = hc_map.get(ketto, {})
                wc    = wc_map.get(ketto, {})
                # 直近5走（日付降順）
                selected_se = past_se_map.get(ketto, [])[:5]

                def _cv(keibajo, rd):
                    return cushion_map.get((KEIBAJO_NAME.get(keibajo, ""), rd))

                past_races = []
                for pr in selected_se:
                    rd = pr["kaisai_nen"] + pr["kaisai_tsukihi"]
                    past_races.append({
                        "race_date_str": rd,
                        "keibajo_code":  pr["keibajo_code"],
                        "race_bango":    pr["race_bango"],
                        "chakujun":      (pr["kakutei_chakujun"] or "").strip(),
                        "hc": past_hc_map.get((ketto, rd), {}),
                        "wc": past_wc_map.get((ketto, rd), {}),
                        "cushion":       _cv(pr["keibajo_code"], rd),
                        "bataiju":       (pr.get("bataiju") or "").strip(),
                    })

                # 直近10走分のクッション情報を収集（表示 + 好走評価に使用）
                cushion_history = []
                for pr_all in past_se_map.get(ketto, [])[:10]:
                    rd_all = pr_all["kaisai_nen"] + pr_all["kaisai_tsukihi"]
                    cv_all = _cv(pr_all["keibajo_code"], rd_all)
                    try:
                        cj_int = int((pr_all.get("kakutei_chakujun") or "99").strip())
                    except ValueError:
                        cj_int = 99
                    cushion_history.append({
                        "date_short":   f"{rd_all[4:6]}/{rd_all[6:]}",
                        "keibajo_code": pr_all["keibajo_code"],
                        "race_bango":   int(pr_all["race_bango"]),
                        "chakujun":     (pr_all.get("kakutei_chakujun") or "").strip().lstrip("0") or "-",
                        "chakujun_int": cj_int,
                        "cushion":      cv_all,
                    })
                # コース適性分析用（全30走）
                course_history = past_se_map.get(ketto, [])

                results.append({
                    **entry,
                    "bamei":            um_map.get(ketto, (ketto, ""))[0],
                    "trainer":          um_map.get(ketto, (ketto, ""))[1],
                    "cushion_history":  cushion_history,
                    "course_history":   course_history,
                    "past_races":       past_races,
                    # 坂路
                    "chokyo_nengappi":  hc.get("chokyo_nengappi"),
                    "chokyo_jikoku":    hc.get("chokyo_jikoku"),
                    "tracen_kubun":     hc.get("tracen_kubun"),
                    "tg4": hc.get("time_gokei_4f"),
                    "tg3": hc.get("time_gokei_3f"),
                    "tg2": hc.get("time_gokei_2f"),
                    "lt4": hc.get("lap_time_4f"),
                    "lt3": hc.get("lap_time_3f"),
                    "lt2": hc.get("lap_time_2f"),
                    "lt1": hc.get("lap_time_1f"),
                    # ウッドチップ
                    "wc_chokyo_nengappi": wc.get("chokyo_nengappi"),
                    "wc_tracen_kubun":    wc.get("tracen_kubun"),
                    "wc_course":          wc.get("course"),
                    "wc_tg6": wc.get("time_gokei_6f"),
                    "wc_lt6": wc.get("lap_time_6f"),
                    "wc_tg5": wc.get("time_gokei_5f"),
                    "wc_lt5": wc.get("lap_time_5f"),
                    "wc_tg4": wc.get("time_gokei_4f"),
                    "wc_tg3": wc.get("time_gokei_3f"),
                    "wc_tg2": wc.get("time_gokei_2f"),
                    "wc_lt4": wc.get("lap_time_4f"),
                    "wc_lt3": wc.get("lap_time_3f"),
                    "wc_lt2": wc.get("lap_time_2f"),
                    "wc_lt1": wc.get("lap_time_1f"),
                })

            return results, source

    finally:
        conn.close()


# ── 3. 分析処理 ────────────────────────────────────────────────────────────

def _lap(raw) -> float | None:
    """整数3桁の生値 → 秒（float）。000 または None は None を返す。"""
    if raw is None:
        return None
    v = int(str(raw).strip())
    return None if v == 0 else round(v / 10, 1)


def _tg(raw) -> float | None:
    """整数4桁の累計タイム生値 → 秒（float）。0000 は None を返す。"""
    if raw is None:
        return None
    v = int(str(raw).strip())
    return None if v == 0 else round(v / 10, 1)


_YOBI = ["月", "火", "水", "木", "金", "土", "日"]
_WC_COURSE = {"0": "A", "1": "B", "2": "C", "3": "D", "4": "E"}

def fmt_yobi(nengappi) -> str:
    """調教年月日 YYYYMMDD → 曜日（月〜日）。"""
    s = str(nengappi or "").strip()
    if len(s) != 8:
        return "-"
    d = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return _YOBI[d.weekday()]


def _date_fmt(nengappi) -> str:
    s = str(nengappi or "").strip()
    return f"{s[:4]}/{s[4:6]}/{s[6:]}" if len(s) == 8 else "-"


def _tracen_name(kubun) -> str:
    return "美浦" if str(kubun or "").strip() == "0" else "栗東"


_CUSHION_BINS = [
    ("10.5以上",  lambda c: c >= 10.5),
    ("9.5-10.4",  lambda c: 9.5 <= c < 10.5),
    ("8.5-9.4",   lambda c: 8.5 <= c < 9.5),
    ("8.4以下",   lambda c: c < 8.5),
]

def _cushion_eval(history: list[dict]) -> tuple[str, str]:
    """クッション戦績を返す。(最優秀帯ラベル, 戦績テキスト)"""
    stats = []
    for label, pred in _CUSHION_BINS:
        races = [h for h in history if h["cushion"] is not None and pred(h["cushion"])]
        c1 = sum(1 for h in races if h["chakujun_int"] == 1)
        c2 = sum(1 for h in races if h["chakujun_int"] == 2)
        c3 = sum(1 for h in races if h["chakujun_int"] == 3)
        c4 = sum(1 for h in races if h["chakujun_int"] > 3)
        stats.append((label, c1, c2, c3, c4, len(races)))

    # 出走数≥2 のビンで最良を判定
    valid = [(l, c1, c2, c3, c4, n) for l, c1, c2, c3, c4, n in stats if n >= 2]
    best_label = "-"
    if valid:
        best = max(valid, key=lambda x: (x[1]+x[2]+x[3]) / x[5])
        rate = (best[1]+best[2]+best[3]) / best[5]
        mark = "◎" if rate >= 0.5 else ("○" if rate >= 0.33 else "△")
        best_label = f"{best[0]}{mark}"

    # 1-0-2-3 形式の戦績テキスト（出走あるビンのみ）
    lines = []
    for label, c1, c2, c3, c4, n in stats:
        if n > 0:
            top3 = c1 + c2 + c3
            mark = ""
            if n >= 2:
                rate = top3 / n
                mark = " ◎" if rate >= 0.5 else (" ○" if rate >= 0.33 else "")
            lines.append(f"{label}：{c1}-{c2}-{c3}-{c4}{mark}")
    stat_txt = "\n".join(lines) if lines else "-"
    return (best_label, stat_txt)


def _baba_code(r: dict, field: str) -> str:
    """0 が falsy になるバグを回避して babajotai コードを文字列で返す。"""
    v = r.get(field)
    return str(v).strip() if v is not None else ""


def _track_type(track_code) -> str:
    """track_code → '芝' / 'ダート' / ''
    実データ確認済み範囲:
      10-22 = 芝コース各種
      23-29 = ダートコース各種（23=ダート左, 24=ダート右, 29等）
      51-59 = 障害コース（芝扱い）
    """
    if track_code is None:
        return ""
    try:
        n = int(str(track_code).strip())
    except ValueError:
        return ""
    if 10 <= n <= 22:
        return "芝"
    if 23 <= n <= 29:
        return "ダート"
    if 51 <= n <= 59:
        return "芝"   # 障害は芝コースを使用
    return ""


def _track_type_from_row(r: dict) -> str:
    """過去レース行から芝/ダートを判定。
    babajotai_code（より確実）を優先し、未設定の場合は track_code にフォールバック。
    """
    dirt_code  = _baba_code(r, "babajotai_code_dirt")
    shiba_code = _baba_code(r, "babajotai_code_shiba")
    if dirt_code  in ("1", "2", "3", "4"):
        return "ダート"
    if shiba_code in ("1", "2", "3", "4"):
        return "芝"
    return _track_type(r.get("track_code"))


_BABA_MAP    = {"1": "良", "2": "稍重", "3": "重", "4": "不良"}
_KY_MAP      = {"0": "逃", "1": "先", "2": "差", "3": "追"}
_SEASON_MAP  = {1:"冬",2:"冬",3:"春",4:"春",5:"春",6:"夏",7:"夏",8:"夏",9:"秋",10:"秋",11:"秋",12:"冬"}


def _course_eval(history: list[dict], cur_keibajo: str, cur_kyori, cur_track_code) -> dict:
    """コース適性評価（距離/馬場/芝ダ/競馬場/脚質/枠/季節）を計算して返す。"""

    def _cj(r):
        try:
            return int((r.get("kakutei_chakujun") or "99").strip())
        except ValueError:
            return 99

    def _s(races):
        c1 = sum(1 for r in races if _cj(r) == 1)
        c2 = sum(1 for r in races if _cj(r) == 2)
        c3 = sum(1 for r in races if _cj(r) == 3)
        c4 = sum(1 for r in races if _cj(r) > 3)
        return c1, c2, c3, c4, len(races)

    cur_tt = _track_type(cur_track_code)
    try:
        cur_kyori_int = int(str(cur_kyori or "").strip()) if cur_kyori else None
    except ValueError:
        cur_kyori_int = None

    # ── 距離別 ──────────────────────────────────────────────────
    dist_grp: dict[int, list] = {}
    for r in history:
        try:
            d = int(str(r.get("kyori") or "").strip())
            if d > 0:
                dist_grp.setdefault(d, []).append(r)
        except (ValueError, TypeError):
            pass
    dist_stats = [
        (f"{d}m", *_s(races), d == cur_kyori_int)
        for d, races in sorted(dist_grp.items())
    ]

    # ── 芝/ダート別 ─────────────────────────────────────────────
    tt_grp: dict[str, list] = {}
    for r in history:
        tt = _track_type_from_row(r)
        if tt:
            tt_grp.setdefault(tt, []).append(r)
    tt_stats = [(tt, *_s(tt_grp[tt]), tt == cur_tt) for tt in ["芝","ダート"] if tt in tt_grp]

    # ── 馬場別（芝・ダートを分離）───────────────────────────────
    shiba_baba_grp: dict[str, list] = {}
    dart_baba_grp:  dict[str, list] = {}
    for r in history:
        tt = _track_type_from_row(r)
        if tt == "芝":
            baba = _BABA_MAP.get(_baba_code(r, "babajotai_code_shiba"), "")
            if baba:
                shiba_baba_grp.setdefault(baba, []).append(r)
        elif tt == "ダート":
            baba = _BABA_MAP.get(_baba_code(r, "babajotai_code_dirt"), "")
            if baba:
                dart_baba_grp.setdefault(baba, []).append(r)

    shiba_baba_stats = [(b, *_s(shiba_baba_grp[b])) for b in ["良","稍重","重","不良"] if b in shiba_baba_grp]
    dart_baba_stats  = [(b, *_s(dart_baba_grp[b]))  for b in ["良","稍重","重","不良"] if b in dart_baba_grp]

    # ── 競馬場（当該）/ 競馬場×距離（当該） ────────────────────
    venue_races      = [r for r in history if r.get("keibajo_code") == cur_keibajo]
    venue_dist_races = [r for r in venue_races
                        if cur_kyori_int and r.get("kyori") and
                        int(str(r["kyori"]).strip()) == cur_kyori_int]

    return {
        "dist_stats":        dist_stats,
        "tt_stats":          tt_stats,
        "shiba_baba_stats":  shiba_baba_stats,
        "dart_baba_stats":   dart_baba_stats,
        "venue_stats":       _s(venue_races),
        "vd_stats":          _s(venue_dist_races),
        "cur_keibajo":       cur_keibajo,
        "cur_kyori_int":     cur_kyori_int,
        "cur_tt":            cur_tt,
    }


# ── AI総評スコアリング ─────────────────────────────────────────────────────

_RANK_SCORE = {"A3": 5, "A2": 4, "A1": 3, "B3": 2, "B2": 1, "B1": 0,
               "-": 1, "調教なし": 0}

_GRADE_COLORS = {
    "S": ("#c0392b", "#fff"),
    "A": ("#e67e00", "#fff"),
    "B": ("#1565c0", "#fff"),
    "C": ("#555",    "#fff"),
    "D": ("#999",    "#fff"),
}


def _ai_score(h: dict) -> dict:
    """4軸スコア(0-5)と総合グレードを算出する。"""

    # ── 調教スコア ────────────────────────────────────────────
    hc_s = _RANK_SCORE.get(h.get("rank",    "調教なし"), 0)
    wc_s = _RANK_SCORE.get(h.get("wc_rank", "調教なし"), 0)
    training = max(hc_s, wc_s)

    # ── クッション適性スコア ──────────────────────────────────
    ce_lbl = h.get("cushion_eval", "-")
    if "◎" in ce_lbl:
        cushion = 5
    elif "○" in ce_lbl:
        cushion = 4
    elif "△" in ce_lbl:
        cushion = 2
    else:
        cushion = 2   # データなし → ニュートラル

    # ── コース適性スコア（距離 + 競馬場の平均） ───────────────
    def _rate_to_score(c1, c2, c3, n) -> float:
        if n == 0: return 2.5
        r = (c1 + c2 + c3) / n
        if r >= 0.60: return 5.0
        if r >= 0.45: return 4.0
        if r >= 0.30: return 3.0
        if r >= 0.15: return 2.0
        return 1.0

    ce = h.get("course_eval", {})
    dist_s = 2.5
    for item in ce.get("dist_stats", []):
        if len(item) > 6 and item[6]:   # is_cur=True
            _, c1, c2, c3, c4, n = item[:6]
            dist_s = _rate_to_score(c1, c2, c3, n)
            break
    vs = ce.get("venue_stats", (0, 0, 0, 0, 0))
    venue_s = _rate_to_score(vs[0], vs[1], vs[2], vs[4]) if len(vs) >= 5 else 2.5

    if dist_s == 2.5 and venue_s == 2.5:
        course = 2.5
    elif dist_s == 2.5:
        course = venue_s
    elif venue_s == 2.5:
        course = dist_s
    else:
        course = (dist_s + venue_s) / 2
    course = round(course)

    # ── 前走相手レベルスコア ──────────────────────────────────
    opponents = h.get("senso_opponents", [])
    senso     = h.get("senso")
    if not senso:
        senso_sc = 2
    else:
        rivals = [o for o in opponents if o.get("next_run") and not o.get("is_self")]
        if rivals:
            n3 = sum(1 for o in rivals
                     if (o["next_run"]["kakutei_chakujun"].lstrip("0") or "99") in ("1","2","3"))
            opp_rate = n3 / len(rivals)
        else:
            opp_rate = 0.0

        try:
            self_pos = int(str(senso.get("chakujun","99")).strip().lstrip("0") or "99")
        except ValueError:
            self_pos = 99
        pos_bonus = 2 if self_pos <= 3 else (1 if self_pos <= 8 else 0)
        senso_sc  = min(int(opp_rate * 3) + pos_bonus, 5)

    # ── 総合グレード ──────────────────────────────────────────
    total = training + cushion + course + senso_sc   # max 20
    grade = ("S" if total >= 16 else
             "A" if total >= 12 else
             "B" if total >= 8  else
             "C" if total >= 4  else "D")

    return {
        "training": training,
        "cushion":  cushion,
        "course":   course,
        "senso":    senso_sc,
        "total":    total,
        "grade":    grade,
    }


def _radar_svg(sc: dict, size: int = 160) -> str:
    """4軸レーダーチャートのSVG文字列を返す。"""
    axes   = ["training", "cushion", "course", "senso"]
    labels = ["調教", "CV適性", "コース", "前走"]
    n      = 4
    cx = cy = size // 2
    r_max   = size // 2 - 22   # グリッド半径
    r_lbl   = r_max + 14       # ラベル半径

    def _pt(i, r):
        angle = -math.pi / 2 + (2 * math.pi / n) * i
        return cx + r * math.cos(angle), cy + r * math.sin(angle)

    parts = [f'<svg width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">']

    # 背景グリッド（5段階）
    for lv in range(1, 6):
        r  = r_max * lv / 5
        bg = "#f5f5f5" if lv % 2 == 0 else "#fff"
        pts = " ".join(f"{_pt(i,r)[0]:.1f},{_pt(i,r)[1]:.1f}" for i in range(n))
        parts.append(f'<polygon points="{pts}" fill="{bg}" stroke="#ddd" stroke-width="1"/>')

    # 軸線
    for i in range(n):
        x, y = _pt(i, r_max)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
                     f'stroke="#ccc" stroke-width="1"/>')

    # データポリゴン
    data_pts = " ".join(
        f"{_pt(i, r_max * sc.get(a,0) / 5)[0]:.1f},"
        f"{_pt(i, r_max * sc.get(a,0) / 5)[1]:.1f}"
        for i, a in enumerate(axes)
    )
    parts.append(f'<polygon points="{data_pts}" '
                 f'fill="rgba(21,101,192,.25)" stroke="#1565c0" stroke-width="2"/>')

    # 頂点ドット
    for i, a in enumerate(axes):
        x, y = _pt(i, r_max * sc.get(a, 0) / 5)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#1565c0"/>')

    # ラベル（上/右/下/左）
    anchors = ["middle", "start", "middle", "end"]
    for i, (label, anchor) in enumerate(zip(labels, anchors)):
        x, y = _pt(i, r_lbl)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" '
            f'font-size="10" fill="#444" font-family="Meiryo,sans-serif">'
            f'{label}</text>'
        )

    parts.append('</svg>')
    return "".join(parts)


def analyze(rows: list[dict]) -> list[dict]:
    for r in rows:
        # ── 坂路 ──────────────────────────────────────────────────────
        lt2 = _lap(r.get("lt2"))
        lt1 = _lap(r.get("lt1"))
        if lt2 is None or lt1 is None:
            r["l4"] = r["l3"] = r["l2"] = r["l1"] = None
            r["rank"] = "調教なし"
        else:
            r["l4"] = _lap(r.get("lt4"))
            r["l3"] = _lap(r.get("lt3"))
            r["l2"] = lt2
            r["l1"] = lt1
            r["rank"] = classify_training(lt2, lt1)
        r["f4"] = _tg(r.get("tg4"))
        r["f3"] = _tg(r.get("tg3"))
        r["f2"] = _tg(r.get("tg2"))
        r["f1"] = lt1
        r["chokyo_date_fmt"] = _date_fmt(r.get("chokyo_nengappi"))
        r["chokyo_yobi"]     = fmt_yobi(r.get("chokyo_nengappi"))
        r["tracen_name"]     = _tracen_name(r.get("tracen_kubun"))

        # ── ウッドチップ ───────────────────────────────────────────────
        wlt1 = _lap(r.get("wc_lt1"))
        if wlt1 is None:
            r["wc_l6"] = r["wc_l5"] = r["wc_l4"] = r["wc_l3"] = r["wc_l2"] = r["wc_l1"] = None
            r["wc_rank"] = "調教なし"
        else:
            r["wc_l6"] = _lap(r.get("wc_lt6"))
            r["wc_l5"] = _lap(r.get("wc_lt5"))
            r["wc_l4"] = _lap(r.get("wc_lt4"))
            r["wc_l3"] = _lap(r.get("wc_lt3"))
            r["wc_l2"] = _lap(r.get("wc_lt2"))
            r["wc_l1"] = wlt1
            r["wc_rank"] = classify_wc(wlt1)
        r["wc_f6"] = _tg(r.get("wc_tg6"))
        r["wc_f5"] = _tg(r.get("wc_tg5"))
        r["wc_f4"] = _tg(r.get("wc_tg4"))
        r["wc_f3"] = _tg(r.get("wc_tg3"))
        r["wc_f2"] = _tg(r.get("wc_tg2"))
        r["wc_f1"] = wlt1
        r["wc_chokyo_date_fmt"] = _date_fmt(r.get("wc_chokyo_nengappi"))
        r["wc_chokyo_yobi"]     = fmt_yobi(r.get("wc_chokyo_nengappi"))
        course_letter = _WC_COURSE.get(str(r.get("wc_course") or "").strip(), "")
        r["wc_tracen_course"]   = _tracen_name(r.get("wc_tracen_kubun")) + course_letter


        # ── 過去走（3着以内優先） ──────────────────────────────────────
        analyzed_past = []
        for pr in r.get("past_races", []):
            hc_p = pr["hc"]
            wc_p = pr["wc"]
            lt2_p = _lap(hc_p.get("lap_time_2f"))
            lt1_p = _lap(hc_p.get("lap_time_1f"))
            hc_rank_p = classify_training(lt2_p, lt1_p) if (lt2_p and lt1_p) else "調教なし"
            wlt1_p = _lap(wc_p.get("lap_time_1f"))
            wc_rank_p = classify_wc(wlt1_p) if wlt1_p else "調教なし"
            ts_p = pr["race_date_str"]
            chakujun_p = pr["chakujun"].lstrip("0") or "-"

            # 坂路ツールチップ
            if lt2_p and lt1_p:
                hc_date = _date_fmt(hc_p.get("chokyo_nengappi"))
                hc_f4   = _f(_tg(hc_p.get("time_gokei_4f")))
                hc_laps = " - ".join([
                    _f(_lap(hc_p.get("lap_time_4f"))),
                    _f(_lap(hc_p.get("lap_time_3f"))),
                    _f(lt2_p), _f(lt1_p),
                ])
                hc_tt = f"坂路 {hc_date}　4F:{hc_f4}　{hc_laps}"
            else:
                hc_tt = "坂路データなし"

            # ウッドツールチップ
            if wlt1_p:
                wc_date = _date_fmt(wc_p.get("chokyo_nengappi"))
                wc_f6   = _f(_tg(wc_p.get("time_gokei_6f")))
                wc_f5   = _f(_tg(wc_p.get("time_gokei_5f")))
                wc_laps = " - ".join([
                    _f(_lap(wc_p.get("lap_time_6f"))),
                    _f(_lap(wc_p.get("lap_time_5f"))),
                    _f(_lap(wc_p.get("lap_time_4f"))),
                    _f(_lap(wc_p.get("lap_time_3f"))),
                    _f(_lap(wc_p.get("lap_time_2f"))),
                    _f(wlt1_p),
                ])
                wc_tt = f"ウッド {wc_date}　6F:{wc_f6}　5F:{wc_f5}　{wc_laps}"
            else:
                wc_f6 = None
                wc_f5 = None
                wc_tt = "ウッドデータなし"

            analyzed_past.append({
                "date_short":   f"{ts_p[4:6]}/{ts_p[6:]}",
                "keibajo_code": pr["keibajo_code"],
                "race_bango":   int(pr["race_bango"]),
                "chakujun":     chakujun_p,
                "hc_rank":      hc_rank_p,
                "wc_rank":      wc_rank_p,
                "hc_tooltip":   hc_tt,
                "wc_tooltip":   wc_tt,
                "wc_f6":        wc_f6,
                "wc_f5":        wc_f5,
                "cushion":      pr.get("cushion"),
            })
        r["past_races_analyzed"] = analyzed_past
        r["cushion_eval"], r["cushion_stat"] = _cushion_eval(r.get("cushion_history", []))

        # ── コース適性 ─────────────────────────────────────────────
        r["course_eval"] = _course_eval(
            r.get("course_history", []),
            r.get("keibajo_code", ""),
            r.get("race_kyori"),
            r.get("race_track_code"),
        )

        # ── 当日情報 ───────────────────────────────────────────────────
        # 馬体重（今回）
        bataiju_raw = str(r.get("bataiju") or "").strip()
        if bataiju_raw and bataiju_raw.isdigit():
            cur_w = int(bataiju_raw)
            # 前走体重から増減計算
            prev_w_str = (r.get("past_races") or [{}])[0].get("bataiju", "") if r.get("past_races") else ""
            if prev_w_str and prev_w_str.isdigit():
                diff = cur_w - int(prev_w_str)
                sign = "+" if diff > 0 else ""
                r["bataiju_fmt"] = f"{cur_w}kg ({sign}{diff})"
                r["bataiju_diff"] = diff
            else:
                r["bataiju_fmt"] = f"{cur_w}kg"
                r["bataiju_diff"] = None
        else:
            r["bataiju_fmt"]  = "-"
            r["bataiju_diff"] = None

        # 体重推移（直近3走 + 差分）
        # past_races は新しい順。4走分取って各走の前走比を計算する
        past_bw = []
        for pr in (r.get("past_races") or [])[:4]:
            bw = str(pr.get("bataiju") or "").strip()
            past_bw.append(int(bw) if (bw and bw.isdigit()) else None)

        trend = []
        for i in range(3):
            w      = past_bw[i]     if i     < len(past_bw) else None
            w_prev = past_bw[i + 1] if i + 1 < len(past_bw) else None
            if w is not None and w_prev is not None:
                diff = w - w_prev
                sign = "+" if diff > 0 else ""
                trend.append({"weight": w, "diff": diff, "diff_str": f"{sign}{diff}"})
            elif w is not None:
                trend.append({"weight": w, "diff": None, "diff_str": None})
            else:
                trend.append({"weight": None, "diff": None, "diff_str": None})
        r["bataiju_trend"] = trend

        # 天候・馬場状態
        _TENKO = {"1": "晴", "2": "曇", "3": "雨", "4": "雪", "5": "霙"}
        tenko = str(r.get("tenko_code") or "").strip()
        r["tenko_fmt"] = _TENKO.get(tenko, "-")
        r["baba_shiba_fmt"] = _BABA_MAP.get(str(r.get("race_babajotai_shiba") or "").strip(), "-")
        r["baba_dirt_fmt"]  = _BABA_MAP.get(str(r.get("race_babajotai_dirt")  or "").strip(), "-")

        # オッズ・人気
        odds_raw = str(r.get("tansho_odds") or "").strip()
        ninki_raw = str(r.get("tansho_ninkijun") or "").strip()
        try:
            odds_val = int(odds_raw)
            r["odds_fmt"] = f"{odds_val/10:.1f}倍" if odds_val > 0 else "-"
        except (ValueError, TypeError):
            r["odds_fmt"] = "-"
        try:
            ninki_val = int(ninki_raw)
            r["ninki_fmt"] = f"{ninki_val}人気" if ninki_val > 0 else "-"
        except (ValueError, TypeError):
            r["ninki_fmt"] = "-"

        # ── なごり11秒台加速ラップフラグ ──────────────────────────────
        # None=対象外, "A"=最推奨(体重±0以上), "B"=推奨(体重減), "?"=体重未確定
        nagori_flag = None
        past0 = (r.get("past_races") or [None])[0]
        if past0 is not None:
            # 条件1: 前走1着
            cj0 = str(past0.get("chakujun", "")).strip().lstrip("0")
            is_first = (cj0 == "1")

            # 条件2: 前走坂路 11秒台加速ラップ（L1が11.0〜11.9秒 かつ L1 < L2）
            hc0 = past0.get("hc", {})
            l1  = _lap(hc0.get("lap_time_1f"))
            l2  = _lap(hc0.get("lap_time_2f"))
            is_sakuji_accel = (
                l1 is not None and l2 is not None and
                11.0 <= l1 <= 11.9 and l1 < l2
            )

            # 条件3: 出走間隔が中10週以内（84日未満）
            try:
                def _to_date(s):
                    return date(int(s[:4]), int(s[4:6]), int(s[6:]))
                race_dt = _to_date(r["race_date_str"])
                prev_dt = _to_date(past0["race_date_str"])
                days_gap = (race_dt - prev_dt).days
                is_interval_ok = 0 < days_gap <= 91  # 中12週以内（13週間＝91日）
            except Exception:
                is_interval_ok = False

            if is_first and is_sakuji_accel and is_interval_ok:
                bw_diff = r.get("bataiju_diff")
                if bw_diff is None:
                    nagori_flag = "?"     # 体重未発表（当日再チェック）
                elif bw_diff >= 0:
                    nagori_flag = "A"     # 最推奨: 体重±0 or 増加
                else:
                    nagori_flag = "B"     # 推奨: 体重減

        r["nagori_flag"] = nagori_flag

        # ── レース共通 ─────────────────────────────────────────────────
        ts = str(r["kaisai_tsukihi"]).zfill(4)
        r["race_date"]   = f"{r['kaisai_nen']}/{ts[:2]}/{ts[2:]}"
        r["race_label"]  = f"{r['keibajo_code']} R{int(r['race_bango']):02d}"
        r["cutoff_date"] = prev_saturday(r["race_date_str"])

    return rows


# ── 3b. 前走相手関係データ取得 ─────────────────────────────────────────────

_JRA_CODES = {'01','02','03','04','05','06','07','08','09','10'}


def _get_senso(horse: dict) -> dict | None:
    """最新のJRA完走レースを返す（新馬・海外/地方のみの場合はNone）
    past_races のキー: race_date_str, keibajo_code, race_bango, chakujun
    """
    for pr in horse.get("past_races", []):   # newest-first
        kj       = str(pr.get("keibajo_code", "")).strip().zfill(2)
        chakujun = str(pr.get("chakujun", "")).strip()
        if kj in _JRA_CODES and chakujun not in ("00", "", "None"):
            return pr
    return None


def fetch_senso_opponents(rows: list[dict]) -> None:
    """各馬の前走（最新JRA完走レース）の相手馬と、その次走結果を rows に付与する。"""

    # ── 前走特定 ──────────────────────────────────────────────────
    for r in rows:
        r["senso"] = _get_senso(r)

    unique_races: set[tuple] = {
        (str(r["senso"]["race_date_str"])[:4],   # kaisai_nen
         str(r["senso"]["race_date_str"])[4:],   # kaisai_tsukihi
         str(r["senso"]["keibajo_code"]),
         str(r["senso"]["race_bango"]))
        for r in rows if r.get("senso")
    }
    if not unique_races:
        for r in rows:
            r["senso_opponents"] = []
        return

    conn = psycopg2.connect(**cfg.DB_CONFIG)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── 前走レースの全出走馬を取得（取消含む） ───────────
            # data_kubun IN ('7','9') = 成績確定済みレコード
            # kakutei_chakujun='00' は取消として表示する
            competitors_map: dict[tuple, list[dict]] = {}
            for (nen, tsukihi, jo, bango) in unique_races:
                cur.execute("""
                    SELECT s.ketto_toroku_bango, s.kakutei_chakujun,
                           COALESCE(u.bamei, s.bamei, '') AS bamei
                    FROM jvd_se s
                    LEFT JOIN jvd_um u
                           ON u.ketto_toroku_bango = s.ketto_toroku_bango
                    WHERE s.kaisai_nen = %s AND s.kaisai_tsukihi = %s
                      AND s.keibajo_code = %s AND s.race_bango = %s
                      AND s.data_kubun IN ('7', '9')
                    ORDER BY CASE WHEN s.kakutei_chakujun = '00' THEN '99'
                                  ELSE s.kakutei_chakujun END
                """, [nen, tsukihi, jo, bango])
                competitors_map[(nen, tsukihi, jo, bango)] = [dict(x) for x in cur.fetchall()]

            # ── 競合馬の次走を一括取得 ────────────────────────────
            all_kettos: set[str] = {
                c["ketto_toroku_bango"]
                for comps in competitors_map.values()
                for c in comps
            }
            next_runs_map: dict[str, list[dict]] = defaultdict(list)
            if all_kettos:
                ph = ", ".join(["%s"] * len(all_kettos))
                cur.execute(f"""
                    SELECT ketto_toroku_bango, kaisai_nen, kaisai_tsukihi,
                           keibajo_code, race_bango, kakutei_chakujun
                    FROM jvd_se
                    WHERE ketto_toroku_bango IN ({ph})
                      AND kakutei_chakujun <> '00'
                    ORDER BY ketto_toroku_bango,
                             kaisai_nen ASC, kaisai_tsukihi ASC, race_bango ASC
                """, list(all_kettos))
                for x in cur.fetchall():
                    next_runs_map[x["ketto_toroku_bango"]].append(dict(x))

            # ── 今回出走馬ルックアップ（ketto → 出走エントリ）────────
            current_entry_map: dict[str, dict] = {
                r["ketto_toroku_bango"]: r for r in rows
            }

            # ── 各馬に付与 ────────────────────────────────────────
            for r in rows:
                senso = r.get("senso")
                if not senso:
                    r["senso_opponents"] = []
                    continue
                rds        = str(senso["race_date_str"])
                key        = (rds[:4], rds[4:], str(senso["keibajo_code"]), str(senso["race_bango"]))
                senso_date = rds
                my_ketto   = r["ketto_toroku_bango"]

                opponents = []
                for c in competitors_map.get(key, []):
                    ketto   = c["ketto_toroku_bango"]
                    is_self = (ketto == my_ketto)   # 本馬フラグ（除外せず表示）
                    all_runs = next_runs_map.get(ketto, [])
                    next_run = next(
                        (x for x in all_runs
                         if x["kaisai_nen"] + x["kaisai_tsukihi"] > senso_date),
                        None
                    )
                    opponents.append({
                        "bamei":           c.get("bamei", "?"),
                        "senso_chakujun":  c["kakutei_chakujun"],
                        "next_run":        next_run,
                        "current_entry":   current_entry_map.get(ketto),
                        "is_self":         is_self,
                    })
                r["senso_opponents"] = opponents

    finally:
        conn.close()


# ── 3c. リアルタイムオッズ取得（keibadata） ────────────────────────────────

def fetch_live_odds(results: list[dict]) -> None:
    """
    keibadata.odds1_tansho_jikeiretsu から最新スナップショットのオッズ・人気を取得し、
    各馬の odds_fmt / ninki_fmt を上書きする。
    umaban が設定されている馬（出馬表取得済み）のみ対象。
    """
    # umaban → result index マップを構築
    race_umaban_idx: dict[tuple, int] = {}
    for i, r in enumerate(results):
        ub = str(r.get("umaban") or "").strip().lstrip("0")
        if not ub:
            continue
        try:
            key = (r["kaisai_nen"], r["kaisai_tsukihi"],
                   r["keibajo_code"], r["race_bango"], int(ub))
            race_umaban_idx[key] = i
        except (ValueError, TypeError):
            pass

    if not race_umaban_idx:
        return

    unique_races = {(nen, gappi, jo, bango)
                    for nen, gappi, jo, bango, _ in race_umaban_idx}

    try:
        odds_conn = psycopg2.connect(**cfg.ODDS_DB_CONFIG)
    except Exception as e:
        print(f"  [オッズDB接続エラー: {e}]")
        return

    try:
        with odds_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for (nen, gappi, jo, bango) in unique_races:
                try:
                    cur.execute("""
                        SELECT DISTINCT ON (umaban::int)
                               umaban::int                    AS umaban,
                               odds::numeric / 10.0           AS tansho_odds,
                               ninki::int                     AS tansho_ninki
                        FROM odds1_tansho_jikeiretsu
                        WHERE kaisai_nen   = %s
                          AND kaisai_gappi = %s
                          AND keibajo_code = %s
                          AND race_bango   = %s
                        ORDER BY umaban::int, happyo_tsukihi_jifun DESC
                    """, [nen, gappi, jo, bango])
                except Exception:
                    continue

                for row in cur.fetchall():
                    key = (nen, gappi, jo, bango, row["umaban"])
                    if key not in race_umaban_idx:
                        continue
                    idx  = race_umaban_idx[key]
                    odds = float(row["tansho_odds"])
                    ninki = int(row["tansho_ninki"])
                    if odds > 0:
                        results[idx]["odds_fmt"]  = f"{odds:.1f}倍"
                    if ninki > 0:
                        results[idx]["ninki_fmt"] = f"{ninki}人気"
    finally:
        odds_conn.close()


# JV-Link 馬体重取得（32bit Python専用スクリプト）
_PY32_PATH = r"C:\Users\balka\AppData\Local\Programs\Python\Python314-32\python.exe"


def fetch_live_weight(rows: list[dict]) -> None:
    """
    当日の馬体重を取得して各馬の bataiju を上書きする。
    ① keibadata.umagoto_race_joho（JvLinkImporter経由）を優先取得
    ② 未取得レースは JV-Link を 32bit Python から直接叩いて補完（0B12→SEレコード）
       取得した馬体重は keibadata.umagoto_race_joho にも書き戻す（DB補完）
    ※ analyze() の前に実行すること（体重推移・なごり判定に反映するため）。
    """
    import subprocess, json

    # (nen,tsukihi,keibajo,race,umaban) → row index
    idx_map: dict[tuple, int] = {}
    # ketto → [row index]（JV-Link補完の照合用）
    ketto_idx: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        k = r.get("ketto_toroku_bango")
        if k:
            ketto_idx.setdefault(k, []).append(i)
        ub = str(r.get("umaban") or "").strip().lstrip("0")
        if not ub:
            continue
        try:
            idx_map[(r["kaisai_nen"], r["kaisai_tsukihi"],
                     r["keibajo_code"], r["race_bango"], int(ub))] = i
        except (ValueError, TypeError):
            pass

    if not idx_map:
        return

    try:
        conn = psycopg2.connect(**cfg.ODDS_DB_CONFIG)
    except Exception as e:
        print(f"  [馬体重DB接続エラー: {e}]")
        return

    def _valid(bw) -> bool:
        s = str(bw or "").strip()
        return s.isdigit() and int(s) > 0

    n_db = 0
    race_code_map: dict[tuple, str] = {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            unique_races = {(nen, gappi, jo, bango)
                            for nen, gappi, jo, bango, _ in idx_map}
            for (nen, gappi, jo, bango) in unique_races:
                try:
                    cur.execute("""
                        SELECT race_code, umaban::int AS umaban, bataiju
                        FROM umagoto_race_joho
                        WHERE kaisai_nen=%s AND kaisai_gappi=%s
                          AND keibajo_code=%s AND race_bango=%s
                    """, [nen, gappi, jo, bango])
                except Exception:
                    continue
                for row in cur.fetchall():
                    if row.get("race_code"):
                        race_code_map[(nen, gappi, jo, bango)] = row["race_code"]
                    if _valid(row["bataiju"]):
                        key = (nen, gappi, jo, bango, row["umaban"])
                        if key in idx_map:
                            rows[idx_map[key]]["bataiju"] = str(row["bataiju"]).strip()
                            n_db += 1
        print(f"  馬体重(keibadata): {n_db}頭")

        # ── 未取得レースを JV-Link で補完 ──────────────────────
        races_need = {
            (n, g, j, b)
            for (n, g, j, b, _ub), idx in idx_map.items()
            if not _valid(rows[idx].get("bataiju"))
        }
        race_ids = [race_code_map[k] for k in races_need if k in race_code_map]

        if race_ids:
            script = str(Path(__file__).parent / "jvlink" / "fetch_weight_jvlink.py")
            data = {}
            try:
                res = subprocess.run(
                    [_PY32_PATH, script] + race_ids,
                    capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace",
                )
                if res.stdout.strip():
                    data = json.loads(res.stdout)
            except Exception as e:
                print(f"  [JV-Link馬体重取得エラー: {e}]")

            n_jv = 0
            db_updates = []  # (bataiju, race_code, ketto)
            for rid, weights in data.items():
                for ketto, info in weights.items():
                    bw = info.get("bataiju")
                    if not bw:
                        continue
                    for i in ketto_idx.get(ketto, []):
                        rows[i]["bataiju"] = bw
                        n_jv += 1
                    db_updates.append((bw, rid, ketto))

            # keibadata に書き戻し（DB補完）
            if db_updates:
                try:
                    with conn.cursor() as ucur:
                        for bw, rid, ketto in db_updates:
                            ucur.execute(
                                "UPDATE umagoto_race_joho SET bataiju=%s "
                                "WHERE race_code=%s AND ketto_toroku_bango=%s",
                                [bw, rid, ketto]
                            )
                    conn.commit()
                except Exception as e:
                    print(f"  [馬体重DB書き戻しエラー: {e}]")
            print(f"  馬体重(JV-Link補完): {n_jv}頭 / DB書き戻し{len(db_updates)}件")
    finally:
        conn.close()


# ── 4. HTML生成 ────────────────────────────────────────────────────────────

RANK_META = {
    "A3": ("#c8f7c5", "#1a6b0f", "★★★ 特注！"),
    "A2": ("#d4edda", "#155724", "★★　優良"),
    "A1": ("#e8f5e9", "#2e7d32", "★　　良好"),
    "B3": ("#fff3cd", "#856404", "注目（L2が11秒台）"),
    "B2": ("#ffeeba", "#664d03", "普通"),
    "B1": ("#f8d7da", "#721c24", "⚠ 危険！"),
    "-":  ("#f5f5f5", "#555",    "分類外"),
    "調教なし": ("#eeeeee", "#888", "調教データなし"),
}


def _badge(rank: str) -> str:
    bg, fg, label = RANK_META.get(rank, ("#eee", "#333", rank))
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:4px;font-weight:bold;font-size:.85em">'
        f'{rank}　{label}</span>'
    )


def _f(v) -> str:
    return f"{v:.1f}" if v is not None else "-"


KEIBAJO_NAME = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}


_SOURCE_DISP = {
    "se2":   "出馬表（確定）",
    "se1":   "出走馬名表",
    "tk":    "特別登録",
    "mixed": "出馬表＋特別登録補完",
    "none":  "データなし",
}


# 閲覧パスワードは config.py の VIEW_PASSWORD で設定（未設定なら "abc"）
_PW = getattr(cfg, "VIEW_PASSWORD", "abc")


def _pw_hash(pw: str) -> str:
    """JavaScriptの Math.imul ハッシュと同じ計算をPythonで行う。"""
    import ctypes
    h = 0
    for c in pw:
        h = ctypes.c_int32(31 * h + ord(c)).value
    return format(h & 0xFFFFFFFF, 'x')


def _cv_label(cv) -> str:
    """クッション値 → 硬さラベル"""
    if cv is None:
        return ""
    if cv >= 10.5:  return "硬め"
    if cv >= 9.5:   return "やや硬め"
    if cv >= 8.5:   return "やや柔"
    return "柔らかめ"


def generate_html(results: list[dict], output_path: str = "report.html", data_source: str = "tk") -> None:
    today = date.today()
    source_disp = _SOURCE_DISP.get(data_source, data_source)
    _pw_hash_val = _pw_hash(_PW)

    # 当日のクッション値を競馬場名→CVで取得（当日情報ヘッダー用）
    today_cv_map: dict[str, float] = {}
    try:
        _conn = psycopg2.connect(**cfg.DB_CONFIG)
        with _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as _cur:
            _cur.execute(
                "SELECT racecourse, cushion_value FROM cushion_values "
                "WHERE measured_date::text = %s",
                [today.strftime("%Y-%m-%d")]
            )
            for _r in _cur.fetchall():
                if _r["cushion_value"] is not None:
                    today_cv_map[_r["racecourse"]] = float(_r["cushion_value"])
        _conn.close()
    except Exception as e:
        print(f"  [当日クッション値取得エラー: {e}]")

    # key = (race_date, keibajo_code, race_bango, kyosomei, cutoff_date)
    groups: dict[tuple, list] = {}
    for r in results:
        key = (
            r["race_date"],
            r["keibajo_code"],
            r["race_bango"],
            r.get("kyosomei_hondai", ""),
            r.get("cutoff_date", ""),
        )
        groups.setdefault(key, []).append(r)

    # セクションHTML（data属性付き）を生成
    sections_html = ""
    for (race_date, keibajo, race_bango, kyosomei, cutoff), horses in sorted(groups.items()):
        cutoff_fmt = f"{cutoff[:4]}/{cutoff[4:6]}/{cutoff[6:]}" if len(cutoff) == 8 else "-"
        venue_name = KEIBAJO_NAME.get(keibajo, keibajo)
        race_no    = int(race_bango)

        def _cv_badge(cv) -> str:
            if cv is None:
                return '<span style="color:#bbb;font-size:.8em">-</span>'
            if cv >= 10.5:
                label, bg, fg = "硬め",     "#cce0ff", "#0a3a7a"
            elif cv >= 9.5:
                label, bg, fg = "やや硬め", "#e0eeff", "#1a4a8a"
            elif cv >= 8.5:
                label, bg, fg = "やや柔",   "#fff0d4", "#7a4a00"
            else:
                label, bg, fg = "柔らかめ", "#ffe0cc", "#9a2a00"
            return (
                f'<span style="background:{bg};color:{fg};border-radius:3px;'
                f'padding:2px 7px;font-size:.82em;font-weight:bold">'
                f'CV {cv:.1f}<br><span style="font-size:.9em">{label}</span></span>'
            )

        training_html  = ""
        cushion_html   = ""
        senso_html     = ""
        ai_cards       = []
        today_html     = ""   # 当日情報タブ

        def _umaban_key(h):
            try:
                return (int(str(h.get("umaban") or "0").strip()), h["bamei"])
            except (ValueError, TypeError):
                return (99, h["bamei"])

        for h in sorted(horses, key=_umaban_key):
            hc_rank = h["rank"]
            wc_rank = h["wc_rank"]
            hc_bg, _, _ = RANK_META.get(hc_rank, ("#fff", "#000", ""))
            wc_bg, _, _ = RANK_META.get(wc_rank, ("#fff", "#000", ""))
            hc_tg = f'{_f(h["f4"])} - {_f(h["f3"])} - {_f(h["f2"])} - {_f(h["f1"])}'
            hc_lp = f'{_f(h.get("l4"))} - {_f(h.get("l3"))} - {_f(h.get("l2"))} - {_f(h.get("l1"))}'
            wc_tg = f'{_f(h["wc_f6"])} - {_f(h["wc_f5"])} - {_f(h["wc_f4"])} - {_f(h["wc_f3"])} - {_f(h["wc_f2"])} - {_f(h["wc_f1"])}'
            wc_lp = f'{_f(h.get("wc_l6"))} - {_f(h.get("wc_l5"))} - {_f(h.get("wc_l4"))} - {_f(h.get("wc_l3"))} - {_f(h.get("wc_l2"))} - {_f(h.get("wc_l1"))}'
            past  = h.get("past_races_analyzed", [])

            # ── 調教タブ用セル ──────────────────────────────────────────
            def _train_hc_cell(i):
                if i >= len(past):
                    return '<td class="past-cell" style="color:#ccc">-</td>'
                pr   = past[i]
                vname = KEIBAJO_NAME.get(pr["keibajo_code"], pr["keibajo_code"])
                tt   = pr["hc_tooltip"].replace('"', '&quot;')
                return (
                    f'<td class="past-cell"><span class="tt">{tt}</span>'
                    f'<div class="past-meta">{pr["date_short"]} {vname}{pr["race_bango"]}R　{pr["chakujun"]}着</div>'
                    f'<div>{_badge(pr["hc_rank"])}</div></td>'
                )
            def _train_wc_cell(i):
                if i >= len(past):
                    return '<td class="past-cell" style="color:#ccc">-</td>'
                pr = past[i]
                tt = pr["wc_tooltip"].replace('"', '&quot;')
                f6 = pr.get("wc_f6") or ""
                f5 = pr.get("wc_f5") or ""
                time_parts = []
                if f6: time_parts.append(f"6F:{f6}")
                if f5: time_parts.append(f"5F:{f5}")
                time_html = (
                    f'<div style="font-family:monospace;font-size:.78em;color:#444;margin-bottom:2px">'
                    + " / ".join(time_parts) + "</div>"
                ) if time_parts else ""
                return (
                    f'<td class="past-cell"><span class="tt">{tt}</span>'
                    f'{time_html}'
                    f'<div>{_badge(pr["wc_rank"])}</div></td>'
                )

            # ── クッションタブ用セル（直近10走） ───────────────────────
            cv_hist = h.get("cushion_history", [])
            def _cv_cell(i):
                if i >= len(cv_hist):
                    return '<td class="past-cell" style="color:#ccc;text-align:center">-</td>'
                pr    = cv_hist[i]
                vname = KEIBAJO_NAME.get(pr["keibajo_code"], pr["keibajo_code"])
                return (
                    f'<td class="past-cell">'
                    f'<div class="past-meta">{pr["date_short"]} {vname}{pr["race_bango"]}R</div>'
                    f'<div style="font-size:.85em;margin-bottom:3px">{pr["chakujun"]}着</div>'
                    f'<div>{_cv_badge(pr.get("cushion"))}</div>'
                    f'</td>'
                )

            # 馬番表示
            _ub = str(h.get("umaban") or "").strip().lstrip("0") or ""
            _wb = str(h.get("wakuban") or "").strip().lstrip("0") or ""
            _num_prefix = f'<span style="font-size:.78em;color:#666">[{_wb}枠{_ub}番]</span><br>' if _ub else ""

            # ── 調教タブ: 2行（坂路 + ウッド） ─────────────────────────
            training_html += (
                f'<tr class="horse-sep" style="background:{hc_bg}">'
                f'<td rowspan="2" class="horse-name">'
                f'{_num_prefix}{h["bamei"]}<br><span class="trainer-name">{h["trainer"]}</span></td>'
                f'<td class="type-hc">坂路</td>'
                f'<td style="text-align:center">{h["chokyo_date_fmt"]}</td>'
                f'<td style="text-align:center">{h["chokyo_yobi"]}</td>'
                f'<td style="text-align:center">{h["tracen_name"]}</td>'
                f'<td style="font-family:monospace">{hc_tg}</td>'
                f'<td style="font-family:monospace">{hc_lp}</td>'
                f'<td style="text-align:center">{_badge(hc_rank)}</td>'
                f'{"".join(_train_hc_cell(i) for i in range(5))}'
                f'</tr>'
                f'<tr class="wc-row" style="background:{wc_bg}">'
                f'<td class="type-wc">ウッド</td>'
                f'<td style="text-align:center">{h["wc_chokyo_date_fmt"]}</td>'
                f'<td style="text-align:center">{h["wc_chokyo_yobi"]}</td>'
                f'<td style="text-align:center">{h["wc_tracen_course"]}</td>'
                f'<td style="font-family:monospace">{wc_tg}</td>'
                f'<td style="font-family:monospace">{wc_lp}</td>'
                f'<td style="text-align:center">{_badge(wc_rank)}</td>'
                f'{"".join(_train_wc_cell(i) for i in range(5))}'
                f'</tr>'
            )

            # ── クッションタブ: 1行 ────────────────────────────────────
            eval_label = h.get("cushion_eval", "-")
            eval_stat  = h.get("cushion_stat", "-")
            stat_lines_html = "".join(
                f'<div style="white-space:nowrap">{line}</div>'
                for line in eval_stat.split("\n") if line.strip()
            ) if eval_stat != "-" else "<div>-</div>"
            cushion_html += (
                f'<tr class="horse-sep">'
                f'<td class="horse-name" style="vertical-align:middle">'
                f'{_num_prefix}{h["bamei"]}<br><span class="trainer-name">{h["trainer"]}</span></td>'
                f'<td style="vertical-align:middle;font-size:.82em;padding:6px 10px">'
                f'<div style="font-weight:bold;margin-bottom:4px;color:#1a1a2e">{eval_label}</div>'
                f'{stat_lines_html}</td>'
                f'{"".join(_cv_cell(i) for i in range(10))}'
                f'</tr>'
            )

        # ── コース適性タブHTML生成 ─────────────────────────────────
        course_html = ""

        def _sb(stats, cur_check=False):
            """stat list → HTMLブロック。(label,c1,c2,c3,c4,n[,is_cur]) 形式"""
            if not stats:
                return '<span style="color:#bbb;font-size:.8em">-</span>'
            rows = []
            for item in stats:
                label, c1, c2, c3, c4, n = item[:6]
                is_cur = item[6] if len(item) > 6 else False
                bold = "font-weight:bold;" if is_cur else ""
                star = "★" if is_cur else "　"
                n3 = c1 + c2 + c3
                rate = n3 / n if n > 0 else 0
                bg = "background:#d4f4d4;" if n >= 2 and rate >= 0.5 else (
                     "background:#fde8e8;" if n >= 3 and rate <= 0.1 else "")
                rows.append(
                    f'<div style="white-space:nowrap;{bold}{bg}padding:1px 4px">'
                    f'{star}<span style="font-size:.78em">{label}</span>'
                    f'<span style="font-family:monospace;font-size:.82em"> {c1}-{c2}-{c3}-{c4}</span>'
                    f'</div>'
                )
            return "\n".join(rows)

        def _single_stat(label, stats_tuple):
            """(c1,c2,c3,c4,n) → 1行HTML"""
            c1, c2, c3, c4, n = stats_tuple
            if n == 0:
                return f'<span style="color:#bbb;font-size:.8em">-</span>'
            n3 = c1+c2+c3
            rate = n3/n
            bg = "background:#d4f4d4;" if n >= 2 and rate >= 0.5 else (
                 "background:#fde8e8;" if n >= 3 and rate <= 0.1 else "")
            return (
                f'<div style="{bg}padding:2px 4px;font-size:.85em">'
                f'<b>{label}</b><br>'
                f'<span style="font-family:monospace">{c1}-{c2}-{c3}-{c4}</span>'
                f'</div>'
            )

        for h in sorted(horses, key=_umaban_key):
            ce = h.get("course_eval", {})
            kj_name = KEIBAJO_NAME.get(h.get("keibajo_code",""), h.get("keibajo_code",""))
            cur_kyr = ce.get("cur_kyori_int")
            kyr_label = f"{cur_kyr}m" if cur_kyr else "-"

            course_html += (
                f'<tr class="horse-sep">'
                f'<td class="horse-name" style="vertical-align:middle">'
                f'{_num_prefix if False else ""}'
            )
            # 馬名セル（枠番あれば表示）
            _ub2 = str(h.get("umaban") or "").strip().lstrip("0") or ""
            _wb2 = str(h.get("wakuban") or "").strip().lstrip("0") or ""
            _np2 = f'<span style="font-size:.78em;color:#666">[{_wb2}枠{_ub2}番]</span><br>' if _ub2 else ""
            course_html += f'{_np2}{h["bamei"]}<br><span class="trainer-name">{h["trainer"]}</span></td>'

            # 距離戦績
            course_html += f'<td class="cs-cell">{_sb(ce.get("dist_stats",[]))}</td>'

            # 芝/ダート
            course_html += f'<td class="cs-cell">{_sb(ce.get("tt_stats",[]))}</td>'

            # 馬場（芝）
            course_html += f'<td class="cs-cell">{_sb(ce.get("shiba_baba_stats",[]))}</td>'

            # 馬場（ダート）
            course_html += f'<td class="cs-cell">{_sb(ce.get("dart_baba_stats",[]))}</td>'

            # 競馬場（当該）
            course_html += (
                f'<td class="cs-cell">'
                f'{_single_stat(kj_name+"（全）", ce.get("venue_stats",(0,0,0,0,0)))}'
                f'{_single_stat(kj_name+kyr_label, ce.get("vd_stats",(0,0,0,0,0)))}'
                f'</td>'
            )

            course_html += '</tr>\n'

            # ── 前走相手タブ: サマリー行 + アコーディオン詳細行 ─────────
            senso       = h.get("senso")
            opponents   = h.get("senso_opponents", [])
            _ub_s = str(h.get("umaban") or "").strip().lstrip("0") or ""
            _wb_s = str(h.get("wakuban") or "").strip().lstrip("0") or ""
            _np_s = f'<span style="font-size:.78em;color:#666">[{_wb_s}枠{_ub_s}番]</span><br>' if _ub_s else ""

            def _cj_bg(cj: str) -> str:
                s = str(cj).strip()
                if s == "00": return "#e0e0e0"   # 取消 → グレー
                try: n = int(s.lstrip("0") or "99")
                except ValueError: n = 99
                if n == 1:        return "#FFD700"
                if n == 2:        return "#C8C8C8"
                if n == 3:        return "#CD853F"
                if 4 <= n <= 8:   return "#e8f5e9"
                return "#fff"

            def _cj_disp(cj: str) -> str:
                s = str(cj).strip()
                if s == "00": return "取消"
                return (s.lstrip("0") or s) + "着"

            if not senso:
                # 新馬・前走データなし
                senso_html += (
                    f'<tr class="horse-sep">'
                    f'<td class="horse-name">{_np_s}{h["bamei"]}'
                    f'<br><span class="trainer-name">{h["trainer"]}</span></td>'
                    f'<td colspan="5" style="color:#bbb;text-align:center;font-size:.85em">前走データなし（新馬等）</td>'
                    f'</tr>\n'
                )
            else:
                rds    = str(senso["race_date_str"])
                s_date = f"{rds[:4]}/{rds[4:6]}/{rds[6:]}"
                s_venue = KEIBAJO_NAME.get(str(senso["keibajo_code"]).zfill(2), senso["keibajo_code"])
                s_race  = int(senso["race_bango"])
                s_cj    = _cj_disp(str(senso.get("chakujun", "?")).strip())

                # サマリー集計
                total   = len(opponents)
                has_next = [o for o in opponents if o["next_run"]]
                n1 = sum(1 for o in has_next if (o["next_run"]["kakutei_chakujun"].lstrip("0") or "99") == "1")
                n2 = sum(1 for o in has_next if (o["next_run"]["kakutei_chakujun"].lstrip("0") or "99") == "2")
                n3 = sum(1 for o in has_next if (o["next_run"]["kakutei_chakujun"].lstrip("0") or "99") == "3")
                n_out = len(has_next) - n1 - n2 - n3
                rate  = round((n1+n2+n3) / len(has_next) * 100) if has_next else None
                rate_str = f"{rate}%" if rate is not None else "-"

                summary = (
                    f'<span style="font-size:.82em">'
                    f'相手{total}頭 / 次走完了{len(has_next)}頭　'
                    f'<b style="color:#b8860b">{n1}</b>-'
                    f'<b style="color:#888">{n2}</b>-'
                    f'<b style="color:#8b6914">{n3}</b>-{n_out}'
                    f'</span>'
                )

                uid = f"so_{id(h)}"
                senso_html += (
                    f'<tr class="horse-sep senso-main-row" onclick="toggleSenso(\'{uid}\')" style="cursor:pointer">'
                    f'<td class="horse-name">{_np_s}{h["bamei"]}'
                    f'<br><span class="trainer-name">{h["trainer"]}</span></td>'
                    f'<td style="text-align:center;white-space:nowrap">{s_date} {s_venue}{s_race}R</td>'
                    f'<td style="text-align:center;background:{_cj_bg(str(senso.get("kakutei_chakujun","")).strip())}">'
                    f'{s_cj}</td>'
                    f'<td>{summary}</td>'
                    f'<td style="text-align:center;font-weight:bold">{rate_str}</td>'
                    f'<td style="text-align:center;font-size:.9em;color:#666">▶ 詳細</td>'
                    f'</tr>\n'
                )

                # 詳細行（アコーディオン）
                detail_rows = ""
                for o in opponents:
                    nr = o["next_run"]
                    s_cj_bg  = _cj_bg(str(o["senso_chakujun"]).strip())
                    s_cj_txt = _cj_disp(str(o["senso_chakujun"]).strip())
                    ce      = o.get("current_entry")
                    is_self = o.get("is_self", False)

                    # 本馬バッジ（自分自身の行）
                    self_badge = (
                        ' <span style="background:#1565c0;color:#fff;border-radius:3px;'
                        'padding:1px 5px;font-size:.75em;font-weight:bold">本馬</span>'
                    ) if is_self else ""
                    self_row_style = ' style="background:#e3f2fd"' if is_self else ""

                    if nr:
                        nr_nen = str(nr["kaisai_nen"])
                        nr_ts  = str(nr["kaisai_tsukihi"]).zfill(4)
                        nr_date  = f"{nr_nen}/{nr_ts[:2]}/{nr_ts[2:]}"
                        nr_venue = KEIBAJO_NAME.get(str(nr["keibajo_code"]).zfill(2), nr["keibajo_code"])
                        nr_race  = int(nr["race_bango"])
                        nr_cj    = str(nr["kakutei_chakujun"]).strip()
                        nr_cj_bg = _cj_bg(nr_cj)
                        nr_cj_txt = _cj_disp(nr_cj)
                        # 今回出走の場合は行をハイライト（本馬優先）
                        if is_self:
                            row_style = ' style="background:#e3f2fd"'
                        elif ce:
                            row_style = ' style="background:#fffbe6"'
                        else:
                            row_style = ''
                        today_badge = (
                            ' <span style="background:#e67e00;color:#fff;border-radius:3px;'
                            'padding:1px 5px;font-size:.75em;font-weight:bold">今回同走</span>'
                        ) if (ce and not is_self) else ''
                        detail_rows += (
                            f'<tr{row_style}>'
                            f'<td style="text-align:center;background:{s_cj_bg};font-weight:bold">{s_cj_txt}</td>'
                            f'<td>{o["bamei"]}{self_badge}{today_badge}</td>'
                            f'<td style="text-align:center;white-space:nowrap">'
                            f'{nr_date} {nr_venue}{nr_race}R</td>'
                            f'<td style="text-align:center;background:{nr_cj_bg};font-weight:bold">'
                            f'{nr_cj_txt}</td>'
                            f'</tr>'
                        )
                    elif ce:
                        # 完走済み次走はないが今回のレースに出走する
                        ce_ts   = str(ce["kaisai_tsukihi"]).zfill(4)
                        ce_date = f"{ce['kaisai_nen']}/{ce_ts[:2]}/{ce_ts[2:]}"
                        ce_venue = KEIBAJO_NAME.get(str(ce["keibajo_code"]).zfill(2), ce["keibajo_code"])
                        ce_race  = int(ce["race_bango"])
                        ce_row_style = self_row_style if is_self else ' style="background:#fffbe6"'
                        ce_extra = '' if is_self else (
                            ' <span style="background:#e67e00;color:#fff;border-radius:3px;'
                            'padding:1px 5px;font-size:.75em;font-weight:bold">今回出走</span>'
                        )
                        detail_rows += (
                            f'<tr{ce_row_style}>'
                            f'<td style="text-align:center;background:{s_cj_bg};font-weight:bold">{s_cj_txt}</td>'
                            f'<td>{o["bamei"]}{self_badge}{ce_extra}</td>'
                            f'<td style="text-align:center;white-space:nowrap">'
                            f'{ce_date} {ce_venue}{ce_race}R</td>'
                            f'<td style="text-align:center;color:#e67e00;font-weight:bold">出走予定</td>'
                            f'</tr>'
                        )
                    else:
                        detail_rows += (
                            f'<tr{self_row_style}>'
                            f'<td style="text-align:center;background:{s_cj_bg};font-weight:bold">{s_cj_txt}</td>'
                            f'<td>{o["bamei"]}{self_badge}</td>'
                            f'<td colspan="2" style="color:#bbb;text-align:center;font-size:.82em">次走予定なし</td>'
                            f'</tr>'
                        )

                senso_html += (
                    f'<tr id="{uid}" class="senso-detail-row" style="display:none">'
                    f'<td colspan="6" style="padding:0;background:#f9f9fb">'
                    f'<table style="width:100%;font-size:.85em;border-collapse:collapse">'
                    f'<thead><tr style="background:#2e3a4e;color:#fff">'
                    f'<th style="padding:4px 10px;width:70px">前走着</th>'
                    f'<th style="padding:4px 10px;text-align:left">相手馬名</th>'
                    f'<th style="padding:4px 10px">次走レース</th>'
                    f'<th style="padding:4px 10px;width:70px">次走着</th>'
                    f'</tr></thead>'
                    f'<tbody>{detail_rows}</tbody>'
                    f'</table>'
                    f'</td></tr>\n'
                )

            # ── AI総評カード ──────────────────────────────────────────
            sc = _ai_score(h)
            h["ai_scores"] = sc
            grade   = sc["grade"]
            g_bg, g_fg = _GRADE_COLORS.get(grade, ("#999", "#fff"))
            chart   = _radar_svg(sc, size=160)

            def _bar(v, max_v=5):
                pct = int(v / max_v * 100)
                return (
                    f'<div style="display:inline-block;background:#e8ecf0;'
                    f'border-radius:3px;height:6px;width:72px;vertical-align:middle">'
                    f'<div style="background:#1565c0;width:{pct}%;height:100%;'
                    f'border-radius:3px"></div></div>'
                )

            score_rows = "".join(
                f'<div style="display:flex;align-items:center;gap:4px;margin-bottom:3px">'
                f'<span style="width:52px;font-size:.75em;color:#666">{lbl}</span>'
                f'{_bar(val)}'
                f'<span style="font-size:.72em;color:#999;white-space:nowrap">{val}/5</span>'
                f'</div>'
                for lbl, val in [
                    ("調教",   sc["training"]),
                    ("CV適性", sc["cushion"]),
                    ("コース", sc["course"]),
                    ("前走相手", sc["senso"]),
                ]
            )
            card_html = (
                f'<div class="ai-card">'
                f'<div class="ai-card-head">'
                f'<span style="font-size:.72em;color:#888">{_np_s.replace("<br>","") if _ub else ""}</span>'
                f'<span class="ai-grade" style="background:{g_bg};color:{g_fg}">{grade}</span>'
                f'</div>'
                f'<div class="ai-bamei">{h["bamei"]}</div>'
                f'<div style="display:flex;justify-content:center">{chart}</div>'
                f'<div style="padding:4px 0">{score_rows}</div>'
                f'<div style="font-size:.7em;color:#777;text-align:center;margin-top:2px">'
                f'合計 {sc["total"]}/20</div>'
                f'</div>'
            )
            ai_cards.append((sc["total"], card_html))

            # ── 当日情報タブ: 1行 ─────────────────────────────────────
            bw_diff = h.get("bataiju_diff")
            if bw_diff is not None:
                diff_color = "#1565c0" if bw_diff > 0 else ("#c0392b" if bw_diff < 0 else "#666")
            else:
                diff_color = "#666"

            # 体重推移セル（体重 + 増減を縦2行で各走表示）
            trend_cells = []
            for item in h.get("bataiju_trend", []):
                w         = item.get("weight")
                diff      = item.get("diff")
                diff_str  = item.get("diff_str")
                if w is None:
                    trend_cells.append(
                        '<td style="text-align:center;font-size:.82em;color:#bbb">-</td>'
                    )
                else:
                    if diff is not None:
                        dc = "#1565c0" if diff > 0 else ("#c0392b" if diff < 0 else "#666")
                        diff_html = (f'<div style="font-size:.75em;color:{dc};'
                                     f'font-weight:bold">{diff_str}</div>')
                    else:
                        diff_html = '<div style="font-size:.75em;color:#bbb">-</div>'
                    trend_cells.append(
                        f'<td style="text-align:center;font-size:.82em">'
                        f'<div style="font-weight:bold">{w}</div>{diff_html}</td>'
                    )
            # 3走分に満たない場合は空セルで埋める
            while len(trend_cells) < 3:
                trend_cells.append('<td style="text-align:center;color:#bbb;font-size:.82em">-</td>')

            # なごりフラグセル
            nf = h.get("nagori_flag")
            if nf == "A":
                nagori_cell = (
                    '<td style="text-align:center">'
                    '<span style="background:#e67e00;color:#fff;font-weight:bold;'
                    'border-radius:4px;padding:2px 8px;font-size:.9em" '
                    'title="なごり11秒加速・体重±0以上（最推奨）">◎</span></td>'
                )
            elif nf == "B":
                nagori_cell = (
                    '<td style="text-align:center">'
                    '<span style="background:#1565c0;color:#fff;font-weight:bold;'
                    'border-radius:4px;padding:2px 8px;font-size:.9em" '
                    'title="なごり11秒加速・体重減（推奨）">○</span></td>'
                )
            elif nf == "?":
                nagori_cell = (
                    '<td style="text-align:center">'
                    '<span style="background:#888;color:#fff;font-weight:bold;'
                    'border-radius:4px;padding:2px 8px;font-size:.9em" '
                    'title="なごり11秒加速・体重未確定（当日再確認）">○?</span></td>'
                )
            else:
                nagori_cell = '<td style="text-align:center;color:#ccc">-</td>'

            today_html += (
                f'<tr class="horse-sep">'
                f'<td class="horse-name">{_np_s}{h["bamei"]}'
                f'<br><span class="trainer-name">{h["trainer"]}</span></td>'
                f'<td style="text-align:center;font-weight:bold;color:{diff_color}">'
                f'{h.get("bataiju_fmt","-")}</td>'
                + "".join(trend_cells) +
                f'<td style="text-align:center">{h.get("odds_fmt","-")}</td>'
                f'<td style="text-align:center">{h.get("ninki_fmt","-")}</td>'
                + nagori_cell +
                f'</tr>\n'
            )

        # スコア降順でカードを並べる
        ai_cards.sort(key=lambda x: -x[0])
        ai_html = '<div class="ai-grid">' + "".join(c for _, c in ai_cards) + '</div>'

        sections_html += f"""
        <section data-date="{race_date}" data-venue="{keibajo}" data-race="{race_no:02d}" style="display:none">
          <h2>{race_date}　{venue_name}　{race_no}R　{kyosomei}</h2>
          <p class="sub">調教取得期間: {cutoff_fmt}（前週土曜）〜 レース前日　／　選択基準: 4F最速タイム</p>
          <div class="tab-bar">
            <button class="tab-btn active" onclick="switchTab(this,'today')">📅 当日情報</button>
            <button class="tab-btn"        onclick="switchTab(this,'training')">🏋 調教</button>
            <button class="tab-btn"        onclick="switchTab(this,'cushion')">🌱 クッション</button>
            <button class="tab-btn"        onclick="switchTab(this,'course')">🏇 コース適性</button>
            <button class="tab-btn"        onclick="switchTab(this,'senso')">🔄 前走相手</button>
            <button class="tab-btn"        onclick="switchTab(this,'ai')">📊 総評</button>
          </div>
          <div class="tab-pane" data-pane="today">
            <div style="margin-bottom:10px;padding:8px 12px;background:#f5f7fa;border-radius:6px;font-size:.85em;display:flex;gap:24px;flex-wrap:wrap">
              <span>天候: <b>{horses[0].get('tenko_fmt','-') if horses else '-'}</b></span>
              <span>芝馬場: <b>{horses[0].get('baba_shiba_fmt','-') if horses else '-'}</b></span>
              <span>ダ馬場: <b>{horses[0].get('baba_dirt_fmt','-') if horses else '-'}</b></span>
              <span>クッション値: <b>{(lambda cv: f"{cv:.1f}（{_cv_label(cv)}）" if cv is not None else "-")(today_cv_map.get(venue_name))}</b></span>
              <span style="color:#888;font-size:.9em">※ 天候・馬場・体重・オッズはレース当日に反映</span>
            </div>
            <table>
              <thead><tr>
                <th>馬名</th>
                <th style="text-align:center">馬体重</th>
                <th style="text-align:center">前走</th>
                <th style="text-align:center">2走前</th>
                <th style="text-align:center">3走前</th>
                <th style="text-align:center">単勝オッズ</th>
                <th style="text-align:center">人気</th>
                <th style="text-align:center">なごり<br><span style="font-size:.72em;font-weight:normal">11秒加速</span></th>
              </tr></thead>
              <tbody>{today_html}</tbody>
            </table>
            <p style="font-size:.78em;color:#888;margin-top:8px">
              ※ 馬体重・オッズ・人気はレース当日（発走前）に反映されます。
            </p>
          </div>
          <div class="tab-pane" data-pane="training" style="display:none">
            <table>
              <thead><tr>
                <th>馬名</th><th>種別</th><th>調教日</th><th>曜日</th><th>T区分</th>
                <th>累計タイム</th><th>ラップタイム</th><th>評価</th>
                <th class="past-th">過去1走</th><th class="past-th">過去2走</th>
                <th class="past-th">過去3走</th><th class="past-th">過去4走</th><th class="past-th">過去5走</th>
              </tr></thead>
              <tbody>{training_html}</tbody>
            </table>
          </div>
          <div class="tab-pane" data-pane="cushion" style="display:none">
            <table>
              <thead><tr>
                <th>馬名</th><th>CV適性</th>
                <th class="past-th">過去1走</th><th class="past-th">過去2走</th>
                <th class="past-th">過去3走</th><th class="past-th">過去4走</th><th class="past-th">過去5走</th>
                <th class="past-th">過去6走</th><th class="past-th">過去7走</th>
                <th class="past-th">過去8走</th><th class="past-th">過去9走</th><th class="past-th">過去10走</th>
              </tr></thead>
              <tbody>{cushion_html}</tbody>
            </table>
          </div>
          <div class="tab-pane" data-pane="course" style="display:none">
            <table>
              <thead><tr>
                <th>馬名</th>
                <th class="cs-th">距離戦績<br><span style="font-size:.78em;font-weight:normal">★=今回距離</span></th>
                <th class="cs-th">芝/ダート<br><span style="font-size:.78em;font-weight:normal">★=今回</span></th>
                <th class="cs-th">馬場（芝）</th>
                <th class="cs-th">馬場（ダート）</th>
                <th class="cs-th">競馬場適性<br><span style="font-size:.78em;font-weight:normal">全/今回距離</span></th>
              </tr></thead>
              <tbody>{course_html}</tbody>
            </table>
          </div>
          <div class="tab-pane" data-pane="senso" style="display:none">
            <table>
              <thead><tr>
                <th>馬名</th>
                <th style="text-align:center">前走レース</th>
                <th style="text-align:center;width:60px">前走着</th>
                <th>相手サマリー（次走 1着-2着-3着-着外）</th>
                <th style="text-align:center;width:70px">3着内率</th>
                <th style="width:60px"></th>
              </tr></thead>
              <tbody>{senso_html}</tbody>
            </table>
            <p style="font-size:.78em;color:#888;margin-top:8px">
              ※ 行をクリックすると相手馬の詳細が展開されます。
              3着内率が高い ＝ 前走は強いメンバー構成。
            </p>
          </div>
          <div class="tab-pane" data-pane="ai" style="display:none">
            <p style="font-size:.78em;color:#888;margin:4px 0 10px">
              ※ 総合スコア降順。各軸は 0〜5 点（調教・CV適性・コース適性・前走相手レベル）、合計 20 点満点。
            </p>
            {ai_html}
          </div>
        </section>"""

    legend_rows = "".join(
        f'<tr style="background:{bg};color:{fg}"><td style="font-weight:bold">{rk}</td><td>{lb}</td></tr>'
        for rk, (bg, fg, lb) in RANK_META.items()
        if rk not in ("調教なし", "-")
    )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>競馬出走馬 総合分析レポート {today}</title>
  <style>
    /* パスワードオーバーレイ */
    #pw-overlay {{
      position: fixed; inset: 0; z-index: 9999;
      background: #1a1a2e;
      display: flex; align-items: center; justify-content: center;
    }}
    #pw-box {{
      background: #fff; border-radius: 12px;
      padding: 36px 32px; width: 300px; max-width: 90vw;
      text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,.4);
    }}
    #pw-box h2 {{
      margin: 0 0 6px; font-size: 1.1em; color: #1a1a2e;
    }}
    #pw-box p {{
      margin: 0 0 20px; font-size: .82em; color: #888;
    }}
    #pw-input {{
      width: 100%; padding: 10px 14px; font-size: 1em;
      border: 2px solid #ddd; border-radius: 8px;
      outline: none; text-align: center; letter-spacing: .1em;
      box-sizing: border-box;
    }}
    #pw-input:focus {{ border-color: #1a1a2e; }}
    #pw-btn {{
      margin-top: 12px; width: 100%; padding: 11px;
      background: #1a1a2e; color: #fff; font-size: .95em;
      font-weight: bold; border: none; border-radius: 8px;
      cursor: pointer; transition: background .15s;
    }}
    #pw-btn:hover {{ background: #2d2d4e; }}
    #pw-err {{
      margin-top: 10px; font-size: .82em; color: #c0392b;
      min-height: 18px;
    }}
  </style>
  <script>
    (function() {{
      var _k = "{_pw_hash_val}";
      function _h(s) {{
        var h = 0;
        for (var i = 0; i < s.length; i++) {{
          h = Math.imul(31, h) + s.charCodeAt(i) | 0;
        }}
        return h.toString(16);
      }}
      if (sessionStorage.getItem('_auth') === _k) {{
        document.addEventListener('DOMContentLoaded', function() {{
          var ov = document.getElementById('pw-overlay');
          if (ov) ov.remove();
        }});
        return;
      }}
      document.addEventListener('DOMContentLoaded', function() {{
        var inp = document.getElementById('pw-input');
        var err = document.getElementById('pw-err');
        function tryLogin() {{
          if (_h(inp.value) === _k) {{
            sessionStorage.setItem('_auth', _k);
            document.getElementById('pw-overlay').remove();
          }} else {{
            err.textContent = 'パスワードが違います';
            inp.value = '';
            inp.focus();
          }}
        }}
        document.getElementById('pw-btn').addEventListener('click', tryLogin);
        inp.addEventListener('keydown', function(e) {{
          if (e.key === 'Enter') tryLogin();
        }});
        inp.focus();
      }});
    }})();
  </script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: "Meiryo", sans-serif; margin: 0; background: #f0f2f5; color: #222; }}

    /* ヘッダー */
    header {{
      background: #1a1a2e; color: #fff;
      padding: 12px 20px; display: flex; align-items: center; gap: 16px;
    }}
    header h1 {{ margin: 0; font-size: 1.1em; white-space: nowrap; }}
    header .generated {{ font-size: .8em; color: #aaa; margin-left: auto; }}

    /* フィルターバー */
    #filter-bar {{
      background: #fff; border-bottom: 2px solid #ddd;
      padding: 10px 20px; display: flex; gap: 24px; align-items: flex-start;
      flex-wrap: wrap;
    }}
    .filter-group {{ display: flex; flex-direction: column; gap: 4px; min-width: 120px; }}
    .filter-group label {{
      font-size: .72em; font-weight: bold; color: #666; letter-spacing: .05em; text-transform: uppercase;
    }}
    .btn-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .btn {{
      padding: 5px 14px; border: 1.5px solid #bbb; border-radius: 20px;
      background: #fff; color: #444; font-size: .88em; cursor: pointer;
      transition: all .15s; white-space: nowrap;
    }}
    .btn:hover {{ border-color: #4a90d9; color: #4a90d9; }}
    .btn.active {{ background: #1a1a2e; border-color: #1a1a2e; color: #fff; font-weight: bold; }}
    .btn:disabled {{ opacity: .35; cursor: default; pointer-events: none; }}

    /* メインコンテンツ */
    #main {{ padding: 20px; }}
    section {{ background: #fff; border-radius: 8px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    section h2 {{ margin: 0 0 4px; font-size: 1.05em; color: #1a1a2e; }}
    .sub {{ font-size: .8em; color: #888; margin: 0 0 12px; }}

    table {{ border-collapse: collapse; width: 100%; font-size: .88em; }}
    th, td {{ border: 1px solid #ddd; padding: 5px 10px; white-space: nowrap; }}
    thead th {{ background: #1a1a2e; color: #fff; font-weight: 600; }}
    tbody tr:hover {{ filter: brightness(.96); }}

    /* 2行レイアウト */
    .horse-name {{ vertical-align: middle; font-weight: bold; background: #fafafa; text-align: center; }}
    .trainer-name {{ font-size: .75em; font-weight: normal; color: #666; }}

    /* タブ */
    .tab-bar {{ display: flex; gap: 6px; margin: 10px 0 0; border-bottom: 2px solid #ddd; }}
    .tab-btn {{
      padding: 6px 18px; border: 1.5px solid #ddd; border-bottom: none;
      border-radius: 6px 6px 0 0; background: #f5f5f5; color: #666;
      font-size: .88em; cursor: pointer; transition: all .12s;
    }}
    .tab-btn:hover {{ background: #eef; color: #1a1a2e; }}
    .tab-btn.active {{ background: #fff; color: #1a1a2e; font-weight: bold; border-color: #ddd; border-bottom-color: #fff; margin-bottom: -2px; }}
    .tab-pane {{ padding-top: 12px; }}
    .type-hc {{ background: #e3f2fd; color: #1565c0; font-weight: bold; font-size: .8em; text-align: center; }}
    .type-wc {{ background: #f3e5f5; color: #6a1b9a; font-weight: bold; font-size: .8em; text-align: center; }}
    .wc-row td {{ border-top: none; }}
    .horse-sep td {{ border-top: 2.5px solid #6a8fc8 !important; }}

    /* 過去走 */
    .past-th {{ background: #2e3a4e; font-size: .82em; }}
    .past-cell {{ text-align: center; font-size: .82em; vertical-align: middle; min-width: 110px; position: relative; cursor: default; }}
    .past-meta {{ color: #555; font-size: .78em; margin-bottom: 3px; }}

    /* ツールチップ */
    .tt {{
      display: none; position: absolute; bottom: calc(100% + 4px); left: 50%;
      transform: translateX(-50%);
      background: #1a1a2e; color: #f0f0f0; padding: 6px 10px;
      border-radius: 5px; font-size: .78em; white-space: nowrap;
      z-index: 200; pointer-events: none;
      box-shadow: 0 2px 8px rgba(0,0,0,.35);
    }}
    .tt::after {{
      content: ""; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
      border: 5px solid transparent; border-top-color: #1a1a2e;
    }}
    .past-cell:hover .tt {{ display: block; }}

    /* コース適性タブ */
    .cs-th {{ background: #2e3a4e; font-size: .82em; text-align:center; min-width:100px; }}
    .cs-cell {{ vertical-align:top; font-size:.82em; padding:4px 6px; min-width:100px; }}

    /* 前走相手タブ */
    .senso-main-row:hover {{ filter: brightness(.94); }}
    .senso-detail-row td {{ padding: 0 !important; border-top: none !important; }}
    .senso-detail-row table td, .senso-detail-row table th {{
      border: 1px solid #ddd; padding: 4px 8px;
    }}

    /* AI総評タブ */
    .ai-grid {{
      display: flex; flex-wrap: wrap; gap: 14px; padding: 8px 0;
    }}
    .ai-card {{
      background: #fff; border: 1px solid #ddd; border-radius: 10px;
      padding: 12px 14px; width: 200px;
      box-shadow: 0 2px 6px rgba(0,0,0,.07);
      transition: box-shadow .15s;
    }}
    .ai-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,.13); }}
    .ai-card-head {{
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 4px;
    }}
    .ai-grade {{
      font-size: 1.15em; font-weight: bold;
      padding: 2px 12px; border-radius: 5px; letter-spacing: .05em;
    }}
    .ai-bamei {{
      font-weight: bold; font-size: .9em; margin-bottom: 6px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}

    /* 凡例 */
    #legend {{ margin-top: 24px; background: #fff; border-radius: 8px; padding: 16px 20px; box-shadow: 0 1px 4px rgba(0,0,0,.08); display: inline-block; }}
    #legend h3 {{ margin: 0 0 8px; font-size: .95em; color: #444; }}
    #legend table {{ width: auto; }}

    #placeholder {{
      text-align: center; color: #aaa; padding: 60px 0; font-size: 1.1em;
    }}

    /* ── テーブル横スクロール ── */
    .tab-pane {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}

    /* ── タブレット (〜768px) ── */
    @media (max-width: 768px) {{
      header {{
        flex-direction: column; align-items: flex-start;
        gap: 2px; padding: 10px 14px;
      }}
      header h1 {{ font-size: 1em; }}
      header .generated {{ margin-left: 0; font-size: .72em; }}

      #filter-bar {{ padding: 8px 14px; gap: 12px; }}
      .filter-group label {{ font-size: .78em; }}
      .btn {{ padding: 6px 10px; font-size: .78em; min-height: 36px; }}

      #main {{ padding: 10px 8px; }}
      section {{ padding: 12px 10px; border-radius: 6px; }}
      section h2 {{ font-size: .98em; }}

      .tab-bar {{ overflow-x: auto; flex-wrap: nowrap; padding-bottom: 2px;
                  -webkit-overflow-scrolling: touch; scrollbar-width: none; }}
      .tab-bar::-webkit-scrollbar {{ display: none; }}
      .tab-btn {{ font-size: .76em; padding: 5px 10px; white-space: nowrap; }}

      table {{ font-size: .80em; }}
      th, td {{ padding: 4px 6px; }}
      .horse-name {{ min-width: 80px; font-size: .82em; }}
      .past-cell {{ min-width: 86px; font-size: .76em; }}
      .past-th {{ font-size: .74em; }}

      .ai-grid {{ gap: 10px; }}
      .ai-card {{ width: calc(50% - 5px); min-width: 150px; padding: 10px; }}
      .ai-bamei {{ font-size: .82em; }}

      .cs-cell {{ min-width: 80px; font-size: .76em; padding: 3px 4px; }}
      .cs-th {{ min-width: 80px; font-size: .74em; }}

      #legend {{ width: 100%; }}
    }}

    /* ── スマホ (〜480px) ── */
    @media (max-width: 480px) {{
      header h1 {{ font-size: .92em; }}

      .btn {{ padding: 7px 8px; font-size: .74em; }}

      table {{ font-size: .74em; }}
      th, td {{ padding: 3px 5px; }}

      .ai-grid {{ gap: 8px; }}
      .ai-card {{ width: 100%; }}

      .past-cell {{ min-width: 76px; }}
    }}
  </style>
</head>
<body>

<div id="pw-overlay">
  <div id="pw-box">
    <h2>競馬出走馬 総合分析レポート</h2>
    <p>パスワードを入力してください</p>
    <input id="pw-input" type="password" placeholder="password" autocomplete="current-password">
    <button id="pw-btn">ログイン</button>
    <div id="pw-err"></div>
  </div>
</div>

<header>
  <h1>競馬出走馬 総合分析レポート</h1>
  <span class="generated">生成日: {today}　／　対象: {today} ～ {today + timedelta(days=cfg.DAYS_AHEAD)}　／　ソース: {source_disp}</span>
</header>

<div id="filter-bar">
  <div class="filter-group">
    <label>開催日</label>
    <div class="btn-row" id="date-btns"></div>
  </div>
  <div class="filter-group">
    <label>開催場所</label>
    <div class="btn-row" id="venue-btns"></div>
  </div>
  <div class="filter-group">
    <label>レース</label>
    <div class="btn-row" id="race-btns"></div>
  </div>
</div>

<div id="main">
  <div id="placeholder">← 開催日・開催場所・レースを選択してください</div>
  {sections_html}
  <div id="legend">
    <h3>評価凡例</h3>
    <table><thead><tr><th>ランク</th><th>説明</th></tr></thead>
    <tbody>{legend_rows}</tbody></table>
  </div>
</div>

<script>
const VENUES = {{"01":"札幌","02":"函館","03":"福島","04":"新潟","05":"東京",
                 "06":"中山","07":"中京","08":"京都","09":"阪神","10":"小倉"}};

let sel = {{ date: null, venue: null, race: null }};

function sections() {{
  return [...document.querySelectorAll('section[data-date]')];
}}

function uniq(arr) {{
  return [...new Set(arr)].sort();
}}

function makeBtn(text, value, clickFn) {{
  const b = document.createElement('button');
  b.className = 'btn';
  b.textContent = text;
  b.dataset.value = value;
  b.onclick = () => clickFn(value);
  return b;
}}

function setActive(rowId, value) {{
  document.querySelectorAll(`#${{rowId}} .btn`).forEach(b => {{
    b.classList.toggle('active', b.dataset.value === value);
  }});
}}

function showSection() {{
  const ph = document.getElementById('placeholder');
  sections().forEach(s => s.style.display = 'none');
  if (!sel.date || !sel.venue || !sel.race) {{
    ph.style.display = 'block';
    return;
  }}
  ph.style.display = 'none';
  const target = document.querySelector(
    `section[data-date="${{sel.date}}"][data-venue="${{sel.venue}}"][data-race="${{sel.race}}"]`
  );
  if (target) target.style.display = 'block';
}}

function selectRace(r) {{
  sel.race = r;
  setActive('race-btns', r);
  showSection();
}}

function selectVenue(v) {{
  sel.venue = v;
  sel.race = null;
  setActive('venue-btns', v);

  const races = uniq(
    sections()
      .filter(s => s.dataset.date === sel.date && s.dataset.venue === v)
      .map(s => s.dataset.race)
  );
  const rb = document.getElementById('race-btns');
  rb.innerHTML = '';
  races.forEach(r => rb.appendChild(makeBtn(r + 'R', r, selectRace)));

  showSection();
  if (races.length === 1) selectRace(races[0]);
}}

function selectDate(d) {{
  sel.date = d;
  sel.venue = null;
  sel.race = null;
  setActive('date-btns', d);

  const venues = uniq(
    sections()
      .filter(s => s.dataset.date === d)
      .map(s => s.dataset.venue)
  );
  const vb = document.getElementById('venue-btns');
  vb.innerHTML = '';
  venues.forEach(v => vb.appendChild(makeBtn(VENUES[v] || v, v, selectVenue)));

  document.getElementById('race-btns').innerHTML = '';
  showSection();
  if (venues.length === 1) selectVenue(venues[0]);
}}

function switchTab(btn, pane) {{
  const section = btn.closest('section');
  section.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  section.querySelectorAll('.tab-pane').forEach(p => p.style.display = 'none');
  btn.classList.add('active');
  section.querySelector(`.tab-pane[data-pane="${{pane}}"]`).style.display = 'block';
}}

function toggleSenso(uid) {{
  const detail = document.getElementById(uid);
  if (!detail) return;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : '';
  // 矢印を切り替える
  const mainRow = detail.previousElementSibling;
  if (mainRow) {{
    const arrow = mainRow.querySelector('td:last-child');
    if (arrow) arrow.textContent = isOpen ? '▶ 詳細' : '▲ 閉じる';
  }}
}}

function init() {{
  const dates = uniq(sections().map(s => s.dataset.date));
  const db = document.getElementById('date-btns');
  dates.forEach(d => db.appendChild(makeBtn(d, d, selectDate)));
  if (dates.length > 0) selectDate(dates[0]);
}}

init();
</script>
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
    print(f"レポート出力完了: {output_path}")


# ── 5. エントリポイント ────────────────────────────────────────────────────

def push_to_github(report_path: str) -> None:
    """report.html を GitHub にプッシュして GitHub Pages を更新する。"""
    import subprocess
    repo_dir = str(Path(report_path).parent)

    def _run(cmd):
        r = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        return r.stdout.strip()

    try:
        print("GitHub へアップロード中...")
        _run(["git", "add", "report.html"])
        # 差分がなければ commit をスキップ
        status = _run(["git", "status", "--porcelain"])
        if status:
            today_str = date.today().strftime("%Y-%m-%d")
            _run(["git", "commit", "-m", f"Update report {today_str}"])
            _run(["git", "push", "origin", "main"])
            print("[OK] GitHub Pages 更新完了")
            print("     URL: https://balkan225.github.io/ckeiba/report.html")
        else:
            print("（レポートに変更なし、アップロードをスキップ）")
    except Exception as e:
        print(f"[ERROR] GitHub へのアップロードに失敗しました: {e}")
        print("  手動で git push を実行してください。")


if __name__ == "__main__":
    print("データ取得中...")
    rows, source = fetch_data()
    print(f"  → {len(rows)} 件の出走馬")

    if rows:
        print("当日馬体重取得中...")
        fetch_live_weight(rows)
        results = analyze(rows)
        print("前走相手データ取得中...")
        fetch_senso_opponents(results)
        print("リアルタイムオッズ取得中...")
        fetch_live_odds(results)
        out = str(Path(__file__).parent / "report.html")
        generate_html(results, out, data_source=source)
        push_to_github(out)
    else:
        print("対象レースが見つかりませんでした。")
