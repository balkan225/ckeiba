# -*- coding: utf-8 -*-
"""
JV-Link 経由で馬体重を取得する（32bit Python 専用）。

JV-Link COM は 32bit コンポーネントのため、必ず 32bit Python で実行すること:
  C:\\Users\\balka\\AppData\\Local\\Programs\\Python\\Python314-32\\python.exe

使い方:
  python fetch_weight_jvlink.py <race_id1> <race_id2> ...
  race_id = 16桁 (kaisai_nen4 + kaisai_gappi4 + keibajo2 + kaiji2 + nichiji2 + race2)

出力（標準出力）:
  {"<race_id>": {"<ketto_toroku_bango>": {"bataiju": "468", "zogen": "-4"}, ...}, ...}

SEレコード(555バイト)レイアウト（2026-06-06 実測確定）:
  馬番=29(2), 血統登録番号=31(10), 馬体重=325(3), 増減符号=328(1), 増減差=329(3)
"""
import sys
import json


def _decode(raw: bytes, start_1based: int, length: int) -> str:
    s = start_1based - 1
    return raw[s:s + length].decode("cp932", errors="replace").strip()


def fetch_one_race(jv, race_id: str) -> dict:
    """1レース分の馬体重を {ketto: {bataiju, zogen}} で返す。"""
    rc = jv.JVRTOpen("0B12", race_id)
    if isinstance(rc, tuple):
        rc = rc[0]
    if rc < 0:
        return {}

    result = {}
    for _ in range(100):
        r = jv.JVRead("", 256000, "")
        if not isinstance(r, tuple):
            break
        ret, data = r[0], r[1]
        if ret == 0 or ret < 0:
            break
        if isinstance(data, str):
            data = data.encode("cp932", errors="replace")
        if data[0:2].decode("cp932", errors="replace") != "SE":
            continue

        ketto = _decode(data, 31, 10)
        bataiju = _decode(data, 325, 3)
        fugo = _decode(data, 328, 1)
        sa = _decode(data, 329, 3)

        # 有効な馬体重のみ（000/空白は無効）
        if not (bataiju.isdigit() and int(bataiju) > 0):
            continue

        zogen = ""
        if sa.isdigit():
            diff = int(sa)
            if fugo == "+":
                zogen = f"+{diff}"
            elif fugo == "-":
                zogen = f"-{diff}"
            elif diff == 0:
                zogen = "0"

        if ketto:
            result[ketto] = {"bataiju": str(int(bataiju)), "zogen": zogen}

    return result


def main():
    race_ids = sys.argv[1:]
    if not race_ids:
        print("{}")
        return

    try:
        import win32com.client as w
        jv = w.Dispatch("JVDTLab.JVLink")
        jv.JVInit("UNKNOWN")
    except Exception as e:
        sys.stderr.write(f"JV-Link init error: {e}\n")
        print("{}")
        return

    out = {}
    try:
        for rid in race_ids:
            try:
                weights = fetch_one_race(jv, rid)
                if weights:
                    out[rid] = weights
            except Exception as e:
                sys.stderr.write(f"race {rid} error: {e}\n")
            finally:
                try:
                    jv.JVClose()
                except Exception:
                    pass
    finally:
        try:
            jv.JVClose()
        except Exception:
            pass

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
