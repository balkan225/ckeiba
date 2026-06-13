# -*- coding: utf-8 -*-
"""
JV-Link 経由で当日の天候・芝馬場・ダート馬場を取得する（32bit Python 専用）。

JV-Link COM は 32bit のため、必ず 32bit Python で実行すること:
  C:\\Users\\balka\\AppData\\Local\\Programs\\Python\\Python314-32\\python.exe

使い方:
  python fetch_track_jvlink.py <race_id1> <race_id2> ...
  race_id = 16桁

出力（標準出力）:
  {"<race_id>": {"tenko": "3", "shiba": "2", "dirt": "0"}, ...}

RAレコード(1272バイト)の天候馬場位置（2026-06-13 実測確定）:
  天候=888, 芝馬場=889, ダート馬場=890（各1バイト, 1-based）
"""
import sys
import json


def fetch_one_race(jv, race_id: str) -> dict | None:
    rc = jv.JVRTOpen("0B12", race_id)
    if isinstance(rc, tuple):
        rc = rc[0]
    if rc < 0:
        return None
    ra = None
    for _ in range(100):
        r = jv.JVRead("", 256000, "")
        if not isinstance(r, tuple):
            break
        ret, data = r[0], r[1]
        if ret == 0 or ret < 0:
            break
        if isinstance(data, str):
            data = data.encode("cp932", errors="replace")
        if data[0:2].decode("cp932", errors="replace") == "RA":
            ra = data  # 最新のRAを採用
    if not ra or len(ra) < 890:
        return None

    def _b(pos):
        return ra[pos - 1:pos].decode("cp932", errors="replace").strip()

    tenko, shiba, dirt = _b(888), _b(889), _b(890)
    # 全て0/空なら未発表とみなす
    if tenko in ("", "0") and shiba in ("", "0") and dirt in ("", "0"):
        return None
    return {"tenko": tenko, "shiba": shiba, "dirt": dirt}


def main():
    race_ids = sys.argv[1:]
    if not race_ids:
        print("{}")
        return

    try:
        import win32com.client.dynamic
        jv = win32com.client.dynamic.Dispatch("JVDTLab.JVLink")
        jv.JVInit("UNKNOWN")
    except Exception as e:
        sys.stderr.write(f"JV-Link init error: {e}\n")
        print("{}")
        return

    out = {}
    try:
        for rid in race_ids:
            try:
                t = fetch_one_race(jv, rid)
                if t:
                    out[rid] = t
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
