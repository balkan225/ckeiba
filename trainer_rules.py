# -*- coding: utf-8 -*-
"""
調教師別 買いパターン判定エンジン。

入力: horse dict（training_analyzer の fetch_trainer_data で付与された各フィールド）
  - trainer_rule, barei, kishu, grade_code, is_shinba
  - keibajo_code, race_track_code, tansho_ninkijun
  - past_races（前走着順・間隔）
  - tr（時系列調教 dict: ss_hc/ss_wc/mae_hc/mae_wc/oi_hc/oi_wc/oi_type/ss2_hc/ss2_wc）

出力: {"mark": "◎"/"○"/"△", "reasons": [..], "notes": [..]} または None

データ基準:
  坂路タイム = 4F(t4f) / ウッドF明記なし = 5F(t5f) / 明記時は t4f,t5f,t6f
  「○○秒台以下」=「○○.9以下」/ ラップ分類 A1〜B3 を流用
  併せ馬/単走/先着 はDB非保持 → △＋注釈で対応
"""


# ── 汎用ヘルパー ─────────────────────────────────────────────
def _track_type(tc):
    try:
        n = int(str(tc).strip())
    except (ValueError, TypeError):
        return ""
    if 10 <= n <= 22:
        return "芝"
    if 23 <= n <= 29:
        return "ダート"
    if 51 <= n <= 59:
        return "芝"
    return ""


def _kansai(h):  # 関西圏: 京都・阪神・中京
    return h.get("keibajo_code") in ("07", "08", "09")


def _kanto(h):   # 関東圏: 東京・中山
    return h.get("keibajo_code") in ("05", "06")


def _young(h):   # 2・3歳
    return h.get("barei") in (2, 3)


def _age2(h):
    return h.get("barei") == 2


def _shiba(h):
    return _track_type(h.get("race_track_code")) == "芝"


def _dirt(h):
    return _track_type(h.get("race_track_code")) == "ダート"


def _juusho(h):  # 重賞 = G1/G2/G3
    return (h.get("grade_code") or "").strip() in ("A", "B", "C")


def _shinba(h):
    return bool(h.get("is_shinba"))


def _kishu(h):
    return h.get("kishu") or ""


def _ninki(h):
    # 当日オッズ反映後の ninki_fmt("2人気") を優先
    import re
    m = re.match(r"(\d+)人気", str(h.get("ninki_fmt") or ""))
    if m:
        return int(m.group(1))
    try:
        v = int(str(h.get("tansho_ninkijun") or "").strip())
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


def _zensoo_chaku(h):
    pr = h.get("past_races") or []
    if not pr:
        return None
    try:
        return int(str(pr[0].get("chakujun") or "").strip().lstrip("0") or "99")
    except (ValueError, TypeError):
        return None


# ── 調教データアクセス ───────────────────────────────────────
def _d(tr, key):
    return tr.get(key)


def _t4(d):
    return d.get("t4f") if d else None


def _t5(d):
    return d.get("t5f") if d else None


def _t6(d):
    return d.get("t6f") if d else None


def _le(t, lim):
    """t <= lim（Noneは不成立）"""
    return t is not None and t <= lim


def _ge(t, lim):
    return t is not None and t >= lim


def _rank(d):
    return d.get("rank") if d else None


def _accel(d):
    return bool(d and d.get("accel"))


def _shimai11_accel(d):
    """終い11秒台の加速ラップ (=A3相当)"""
    return bool(d and d.get("l1") is not None and d["l1"] < 12.0 and d.get("accel"))


def _shimai12_accel(d):
    """終いのみ12秒台の加速ラップ (=A1相当)"""
    return _rank(d) == "A1"


def _12_12_accel(d):
    """12-12加速 (2F・1Fとも12秒台で加速 =A2相当)"""
    return _rank(d) == "A2"


def _2f11_decel(d):
    """2F11秒台の減速ラップ (=B3相当)"""
    return _rank(d) == "B3"


def _oi_is_hc(tr):
    return tr.get("oi_type") == "坂路"


def _oi_is_wc(tr):
    return tr.get("oi_type") == "ウッド"


# ── 各調教師の判定 ───────────────────────────────────────────
# 返り値: list of (mark, reason)。空なら非該当。notes は別 list。

def _j_uemura(h, tr):
    R, N = [], []
    ss_hc, ss_wc = _d(tr, "ss_hc"), _d(tr, "ss_wc")
    if _le(_t4(ss_hc), 55.9) or _le(_t5(ss_wc), 55.9):
        R.append(("○", "土日最速(坂路/ウッド)が55.9秒以下"))
    if _kansai(h) and _le(_t4(_d(tr, "mae_hc")), 63.9):
        R.append(("○", "関西圏×前日坂路63.9秒以下"))
    if _young(h) and _le(_t5(_d(tr, "ss2_wc")), 53.9):
        R.append(("○", "2・3歳×2週前土日ウッド53.9秒以下"))
    return R, N


def _j_nakauchida(h, tr):
    R, N = [], []
    if _oi_is_wc(tr) and _le(_t5(_d(tr, "oi_wc")), 65.9):
        R.append(("○", "追い切りウッド65.9秒以下"))
    if _le(_t4(_d(tr, "ss_hc")), 55.9) and _oi_is_hc(tr):
        R.append(("○", "土日坂路55.9秒以下×追い切り坂路"))
    if (_young(h) and _kishu(h) == "川田" and _le(_t4(_d(tr, "ss_hc")), 59.9)
            and _rank(_d(tr, "oi_hc")) not in ("B1",)):
        R.append(("◎", "2・3歳×川田×土日坂路59.9秒以下×追い切り坂路が地雷以外"))
    return R, N


def _j_satoyuta(h, tr):
    R, N = [], []
    oi_hc = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and _shimai11_accel(oi_hc):
        R.append(("○", "追い切り坂路で終い11秒台の加速ラップ"))
    oi_wc = _d(tr, "oi_wc")
    if _le(_t4(_d(tr, "ss_hc")), 59.9) and _oi_is_wc(tr) and _le((oi_wc or {}).get("l1"), 11.6):
        R.append(("○", "土日坂路59.9秒以下×追い切りウッド終い11.6秒以下"))
    if _young(h) and (_le(_t5(oi_wc), 67.9) or _le(_t4(oi_hc), 53.9)):
        R.append(("○", "2・3歳×追い切りウッド67.9秒以下 or 坂路53.9秒以下"))
    return R, N


def _j_katoshizuya(h, tr):
    R, N = [], []
    mae = _d(tr, "mae_hc")
    if mae:
        if _le(_t4(mae), 63.9):
            R.append(("◎", "前日坂路あり(63.9秒以下で高評価)"))
        else:
            R.append(("○", "前日坂路あり"))
    if (_young(h) and _le(_t4(_d(tr, "ss_hc")), 57.9)
            and _oi_is_hc(tr) and mae):
        R.append(("◎", "2・3歳×土日坂路57.9秒以下×追い切り坂路×前日坂路あり"))
    return R, N


def _j_chida(h, tr):
    R, N = [], []
    if _oi_is_wc(tr) and _ge(_t5(_d(tr, "oi_wc")), 68.0):
        R.append(("○", "追い切りウッド68.0秒以上"))
    ss2 = _d(tr, "ss2_hc")
    if ss2 is None or _le(_t4(ss2), 59.9):
        R.append(("○", "2週前土日坂路なし or 59.9秒以下"))
    N.append("要確認: 併せ馬で先着 or 単走仕上げ なら買い")
    return R, N


def _j_yoshioka(h, tr):
    R, N = [], []
    mae = _d(tr, "mae_hc")
    if _le(_t4(mae), 65.9):
        ss = _d(tr, "ss_hc")
        if _le(_t4(ss), 51.9) or _d(tr, "ss_wc"):
            R.append(("◎", "前日坂路65.9秒以下(土日坂路51.9秒以下/ウッド併用で高評価)"))
        else:
            R.append(("○", "前日坂路65.9秒以下"))
    oi = _d(tr, "oi_hc")
    if _young(h) and _oi_is_hc(tr) and _shimai12_accel(oi):
        R.append(("○", "2・3歳×追い切り坂路で終いのみ12秒台の加速ラップ"))
    return R, N


def _j_shii(h, tr):
    R, N = [], []
    if _le(_t5(_d(tr, "ss_wc")), 73.9):
        R.append(("○", "土日ウッド最速73.9秒以下"))
    if _d(tr, "ss_hc") and _d(tr, "ss_wc"):
        R.append(("○", "土日に坂路・ウッド両方の調教あり"))
    if _young(h) and _d(tr, "ss2_wc") and _le(_t5(_d(tr, "ss_wc")), 71.9):
        R.append(("○", "2・3歳×2週前土日ウッドあり×1週前土日ウッド71.9秒以下"))
    return R, N


def _j_hori(h, tr):
    R, N = [], []
    if _kanto(h) and _le(_t4(_d(tr, "mae_hc")), 65.9):
        R.append(("○", "関東圏×前日坂路65.9秒以下"))
    oi_wc = _d(tr, "oi_wc")
    if _oi_is_wc(tr) and _le((oi_wc or {}).get("l1"), 11.5):
        R.append(("○", "追い切りウッド終い11.5秒以下"))
    if _young(h) and (_d(tr, "ss_wc") or _d(tr, "ss2_wc")) and _d(tr, "mae_hc"):
        R.append(("○", "2・3歳×土日/2週前土日ウッドあり×前日坂路あり"))
    if _shinba(h) and _le(_t4(oi_wc), 53.9):
        R.append(("◎", "新馬戦×追い切りウッド4Fが53.9秒以下"))
    return R, N


def _j_okubo(h, tr):
    R, N = [], []
    if _le(_t4(_d(tr, "oi_wc")), 53.9):
        R.append(("○", "追い切りウッド4Fが53.9秒以下"))
    if (_young(h) and _dirt(h) and _le(_t4(_d(tr, "ss_hc")), 59.9)
            and _le(_t4(_d(tr, "oi_wc")), 59.9)):
        R.append(("○", "2・3歳×ダート×土日坂路59.9秒以下×追い切りウッド4F59.9秒以下"))
    N.append("要確認: 休み明け2〜4戦目×追い切り坂路で終い11秒台加速 or 2F11秒台減速×併せ馬で先着/併入 なら買い")
    return R, N


def _j_yasudasho(h, tr):
    R, N = [], []
    oi_wc = _d(tr, "oi_wc")
    if _d(tr, "ss_hc") and _le(_t5(oi_wc), 69.9):
        l1 = (oi_wc or {}).get("l1")
        if l1 is not None and 12.0 <= l1 <= 12.8:
            tag = "◎" if (_kansai(h) and _d(tr, "mae_hc")) else "○"
            R.append((tag, "土日坂路あり×追い切りウッド69.9秒以下×終い12.4秒前後"))
    if _age2(h) and _le(_t5(_d(tr, "oi_wc")), 69.9) and _le(_t5(_d(tr, "ss_wc")), 69.9):
        R.append(("○", "2歳×水木ウッド69.9秒以下×同週坂路69.9秒以下"))
    return R, N


def _j_terashima(h, tr):
    R, N = [], []
    ss = _d(tr, "ss_hc")
    if _shimai12_accel(ss) and _oi_is_wc(tr):
        tag = "◎" if _le(_t4(_d(tr, "oi_wc")), 53.9) else "○"
        R.append((tag, "土日坂路で終いのみ12秒台加速×追い切りウッド"))
    if (_young(h) and _shiba(h) and _d(tr, "ss_hc")
            and _le(_t4(_d(tr, "oi_hc")), 55.9)):
        R.append(("○", "2・3歳×芝×土日坂路あり×追い切り坂路55.9秒以下"))
    return R, N


def _j_tezuka(h, tr):
    R, N = [], []
    if _le(_t5(_d(tr, "oi_wc")), 69.9):
        R.append(("○", "追い切りウッド69.9秒以下"))
    if _d(tr, "mae_hc") is None and _d(tr, "mae_wc") is None:
        R.append(("○", "前日坂路なし"))
    if (_young(h) and _shiba(h) and _d(tr, "ss_hc")
            and _le(_t4(_d(tr, "oi_hc")), 55.9)):
        R.append(("○", "2・3歳×芝×土日坂路あり×追い切り坂路55.9秒以下"))
    return R, N


def _j_saito(h, tr):
    R, N = [], []
    if _dirt(h) and _oi_is_hc(tr):
        tag = "◎" if _le(_t4(_d(tr, "oi_hc")), 53.9) else "○"
        R.append((tag, "ダート×追い切り坂路(53.9秒以下で高評価)"))
    if _le(_t4(_d(tr, "ss_hc")), 59.9) and _d(tr, "mae_hc"):
        R.append(("○", "土日坂路59.9秒以下×前日坂路あり"))
    N.append("要確認: 追い切り併せ馬で先着 or 併入 なら買い")
    return R, N


def _j_kimura(h, tr):
    R, N = [], []
    ss = _d(tr, "ss_hc")
    if _le(_t4(ss), 55.9) and _oi_is_wc(tr):
        tag = "◎" if _shimai11_accel(ss) else "○"
        R.append((tag, "土日坂路55.9秒以下×追い切りウッド(土日坂路終い11秒台加速で高評価)"))
    if _juusho(h) and _kishu(h) in ("ルメール", "戸崎", "戸崎圭") and _le(_t4(ss), 53.9):
        R.append(("◎", "重賞×ルメール/戸崎×土日坂路53.9秒以下"))
    if _age2(h) and _le(_t4(ss), 53.9) and (_d(tr, "oi_hc") or _d(tr, "oi_wc")):
        R.append(("○", "2歳×土日坂路53.9秒以下×追い切りあり"))
    return R, N


def _j_sugiyama(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and _shimai11_accel(oi):
        tag = "◎" if _kanto(h) else "○"
        R.append((tag, "追い切り坂路で終い11秒台加速(関東遠征で高評価)"))
    oi_wc = _d(tr, "oi_wc")
    if _oi_is_wc(tr) and _le((oi_wc or {}).get("l1"), 11.9) and _le(_t4(_d(tr, "ss_hc")), 69.9):
        R.append(("○", "追い切りウッド終い11.9秒以下×土日坂路69.9秒以下"))
    if _young(h) and _le(_t4(_d(tr, "oi_hc")), 53.9) and _le(_t4(_d(tr, "mae_hc")), 69.9):
        R.append(("○", "2・3歳×追い切り坂路53.9秒以下×前日坂路69.9秒以下"))
    return R, N


def _j_moriissei(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and _12_12_accel(oi):
        R.append(("○", "追い切り坂路で12-12加速ラップ"))
    if _le(_t4(oi), 53.9):
        tag = "◎" if _accel(oi) else "○"
        R.append((tag, "追い切り坂路53.9秒以下(加速ラップで高評価)"))
    if (_young(h) or _shinba(h)) and _le(_t4(oi), 53.9) and _accel(oi):
        R.append(("◎", "2・3歳/新馬×追い切り坂路53.9秒以下×加速ラップ"))
    return R, N


def _j_morihide(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and (_accel(oi) or _2f11_decel(oi)):
        R.append(("○", "追い切り坂路で加速 or 2F11秒台減速ラップ"))
    if _d(tr, "ss_wc"):
        R.append(("○", "土日ウッド最速あり"))
    if _young(h) and _oi_is_hc(tr) and _shimai11_accel(oi):
        R.append(("◎", "2・3歳×追い切り坂路で終い11秒台加速ラップ"))
    return R, N


def _j_takeyuki(h, tr):
    R, N = [], []
    if _le(_t4(_d(tr, "oi_hc")), 53.9) or _le(_t5(_d(tr, "oi_wc")), 65.9):
        R.append(("○", "追い切り坂路53.9秒以下 or ウッド65.9秒以下"))
    if h.get("keibajo_code") in ("01", "02"):
        R.append(("○", "夏競馬(札幌・函館)滞在"))
    if _d(tr, "ss_hc") and _12_12_accel(_d(tr, "oi_hc")):
        R.append(("○", "土日坂路あり×追い切り坂路12-12加速ラップ"))
    if _young(h) and _d(tr, "mae_wc"):
        R.append(("○", "2・3歳×前日ウッド調教あり"))
    return R, N


def _j_takehide(h, tr):
    R, N = [], []
    if _le(_t4(_d(tr, "ss_hc")), 51.9):
        R.append(("○", "土日坂路最速51.9秒以下"))
    if _oi_is_wc(tr):
        R.append(("○", "追い切りウッド"))
    if _rank(_d(tr, "oi_hc")) in ("A2", "A3", "B3"):
        R.append(("○", "追い切り坂路ラップがA2/A3/B3(綺麗なラップ)"))
    return R, N


def _j_ikee(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and (_12_12_accel(oi) or _shimai11_accel(oi)):
        R.append(("○", "追い切り坂路で12-12加速 or 終い11秒台加速"))
    if (_zensoo_chaku(h) in (2, 3) and _kansai(h) and _le(_t4(_d(tr, "mae_hc")), 63.9)
            and (_d(tr, "ss_hc") is None or _le(_t4(_d(tr, "ss_hc")), 59.9))
            and _rank(oi) in ("A2", "A3")):
        R.append(("◎", "前走2・3着×関西圏×前日坂路63.9秒以下×土日坂路59.9秒以下/なし×追い切り坂路A2/A3"))
    if _young(h) and _ge(_t4(_d(tr, "ss_hc")), 58.0) and _12_12_accel(oi):
        R.append(("○", "2・3歳×土日坂路58.0秒以上×追い切り坂路12-12加速"))
    return R, N


def _j_makiura(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if oi and _rank(oi) != "B1" and _le(_t4(oi), 55.9):
        R.append(("○", "追い切り坂路が地雷(B1)以外×全体55.9秒以下"))
    if (_juusho(h) or _young(h)) and _le(_t4(_d(tr, "ss_hc")), 59.9):
        R.append(("○", "重賞/2・3歳×土日坂路59.9秒以下"))
    if _young(h) and _le(_t4(_d(tr, "ss_hc")), 59.9) and _accel(oi):
        R.append(("○", "2・3歳×土日坂路59.9秒以下×追い切り坂路で加速ラップ"))
    return R, N


def _j_tanakahiro(h, tr):
    R, N = [], []
    ss_hc, ss_wc = _d(tr, "ss_hc"), _d(tr, "ss_wc")
    mae_hc, mae_wc = _d(tr, "mae_hc"), _d(tr, "mae_wc")
    if (_le(_t4(ss_hc), 54.9) or _le(_t4(ss_wc), 50.9)
            or _le(_t4(mae_hc), 61.9) or _le(_t4(mae_wc), 63.9)):
        R.append(("○", "土日坂路54.9/ウッド4F50.9 or 前日坂路61.9/ウッド4F63.9秒以下"))
    if _juusho(h) and (_le(_t4(_d(tr, "ss2_hc")), 56.9) or _le(_t4(_d(tr, "ss2_wc")), 54.9)):
        R.append(("◎", "重賞×2週前土日坂路56.9 or ウッド4F54.9秒以下"))
    if _young(h) and _le(_t4(ss_hc), 55.9) and mae_hc:
        R.append(("○", "2・3歳×土日坂路55.9秒以下×前日坂路あり"))
    return R, N


def _j_yahagi(h, tr):
    R, N = [], []
    if _oi_is_wc(tr):
        tag = "◎" if _dirt(h) else "○"
        R.append((tag, "追い切りウッド(特にダートで高評価)"))
    if (_young(h) or _shinba(h)) and _d(tr, "ss_wc") and _d(tr, "ss2_wc"):
        R.append(("◎", "2・3歳(特に新馬)×2週連続で土日ウッド最速あり"))
    oi_wc, oi_hc = _d(tr, "oi_wc"), _d(tr, "oi_hc")
    if _le((oi_wc or {}).get("l1"), 12.0) or _le(_t4(oi_hc), 53.9):
        R.append(("○", "木曜ウッド終い12.0秒以下 or 坂路53.9秒以下"))
    return R, N


def _j_ishizaka(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _le(_t4(oi), 55.9) and _shimai12_accel(oi):
        R.append(("○", "追い切り坂路55.9秒以下×終いのみ12秒台加速ラップ"))
    if _d(tr, "ss_hc") and _le(_t5(_d(tr, "oi_wc")), 67.9):
        R.append(("○", "土日坂路あり×追い切りウッド67.9秒以下"))
    return R, N


def _j_takano(h, tr):
    R, N = [], []
    oi = _d(tr, "oi_hc")
    if _oi_is_hc(tr) and _shimai12_accel(oi):
        R.append(("○", "追い切り坂路で終いのみ12秒台加速ラップ(A1)"))
    ss = _d(tr, "ss_hc")
    if _shiba(h) and ss and (ss.get("t4f") is not None and 51.0 <= ss["t4f"] <= 56.9) and _rank(oi) == "A1":
        R.append(("○", "芝×土日坂路51.0〜56.9秒×追い切り坂路A1ラップ"))
    if (_juusho(h) or (_ninki(h) is not None and _ninki(h) <= 3)) and _shimai11_accel(oi):
        R.append(("◎", "重賞/有力馬×追い切り坂路で終い11秒台加速ラップ(A3)"))
    return R, N


def _j_kanoto(h, tr):
    R, N = [], []
    ss = _d(tr, "ss_hc")
    if _rank(ss) in ("A1", "A2", "A3", "B1", "B2", "B3") and _oi_is_wc(tr) and _le(_t5(_d(tr, "oi_wc")), 69.9):
        R.append(("○", "土日坂路が特定ラップ×追い切りウッド×ウッド5F69.9秒以下"))
    if _young(h) and _accel(_d(tr, "oi_hc")):
        R.append(("○", "2・3歳×追い切り坂路で加速ラップ"))
    return R, N


def _j_kuroiwa(h, tr):
    R, N = [], []
    if _le(_t5(_d(tr, "oi_wc")), 65.9):
        R.append(("○", "追い切りウッド65.9秒以下"))
    nk = _ninki(h)
    if nk is not None and nk <= 3 and _le(_t4(_d(tr, "ss_hc")), 55.9) and _le(_t5(_d(tr, "oi_wc")), 66.9):
        R.append(("◎", "1〜3番人気×土日坂路55.9秒以下×追い切りウッド66.9秒以下"))
    if _young(h) and (_le(_t5(_d(tr, "oi_wc")), 67.9) or _rank(_d(tr, "oi_hc")) in ("A1", "A2", "B2")):
        R.append(("○", "2・3歳×追い切りウッド67.9秒以下 or 坂路A1/A2/B2"))
    return R, N


_JUDGES = {
    "上村洋行": _j_uemura, "中内田充正": _j_nakauchida, "佐藤悠太": _j_satoyuta,
    "加藤士津八": _j_katoshizuya, "千田輝彦": _j_chida, "吉岡辰弥": _j_yoshioka,
    "四位洋文": _j_shii, "堀宣行": _j_hori, "大久保龍志": _j_okubo,
    "安田翔伍": _j_yasudasho, "寺島良": _j_terashima, "手塚貴久": _j_tezuka,
    "斎藤誠": _j_saito, "木村哲也": _j_kimura, "杉山晴紀": _j_sugiyama,
    "森一誠": _j_moriissei, "森秀行": _j_morihide, "武幸四郎": _j_takeyuki,
    "武英智": _j_takehide, "池江泰寿": _j_ikee, "牧浦充徳": _j_makiura,
    "田中博康": _j_tanakahiro, "矢作芳人": _j_yahagi, "石坂公一": _j_ishizaka,
    "高野友和": _j_takano, "鹿戸雄一": _j_kanoto, "黒岩陽一": _j_kuroiwa,
}

_MARK_RANK = {"◎": 3, "○": 2, "△": 1}


def judge_trainer(h: dict) -> dict | None:
    """馬1頭の買いパターン判定。該当なしは None。"""
    rule = h.get("trainer_rule")
    if not rule:
        return None
    fn = _JUDGES.get(rule)
    if not fn:
        return None
    tr = h.get("tr") or {}
    # 当週追い切り(水木)が未実施なら判定保留（tk段階の誤発火防止）。
    # 実運用では木金以降の追い切り後に判定される。
    if not (tr.get("oi_hc") or tr.get("oi_wc")):
        return None
    try:
        results, notes = fn(h, tr)
    except Exception:
        return None

    if not results:
        # 該当条件なし。ただし注釈(併せ馬待ち)があれば △ で出す
        if notes:
            return {"mark": "△", "trainer": rule, "reasons": [], "notes": notes}
        return None

    best = max(results, key=lambda x: _MARK_RANK.get(x[0], 0))[0]
    reasons = [f"{m} {txt}" for m, txt in results]
    return {"mark": best, "trainer": rule, "reasons": reasons, "notes": notes}
