# 競馬出走馬 総合分析レポート ― 引き継ぎ書 / 仕様書

最終更新: 2026-06-24

---

## 0. これは何か（30秒サマリー）

JRA出走予定馬を多角的に分析し、**8つのタブを持つHTMLレポート**を生成して
GitHub Pagesで公開するシステム。土日のレース当日は10分おきに自動更新され、
スマホからパスワード付きで最新の馬体重・オッズ・調教評価・血統適性などを確認できる。

- **公開URL**: https://balkan225.github.io/ckeiba/report.html
- **閲覧パスワード**: `config.py` の `VIEW_PASSWORD`（非公開）
- **メインスクリプト**: `training_analyzer.py`（約3300行）
- **判定エンジン**: `trainer_rules.py`（調教師別買いパターン）

---

## 1. レポートの8タブ

| タブ | 内容 |
|------|------|
| 📅 当日情報 | 馬体重(増減)・体重推移3走・オッズ・人気・**なごり11秒**判定。上部に天候/芝馬場/ダ馬場/クッション値 |
| 🏋 調教 | 坂路/ウッドのタイム・ラップ・評価(A3〜B1)。過去5走の調教も表示。馬名横に**調教師買いパターン**マーク |
| 🌱 クッション | クッション値適性・過去10走のCV戦績 |
| 🏇 コース適性 | 距離別/芝ダ別/馬場別/競馬場別の成績(★=今回条件) |
| 🔄 前走相手 | 前走の対戦相手の次走着順(アコーディオン) |
| 🧬 血統 | 父・母・母父＋今回(競馬場×芝ダ×距離)での父成績(勝率/複勝率/単複回収率)。父名クリックで馬場別・クッション別詳細 |
| 📊 総評 | AI 4軸レーダーチャート＋S〜Dグレード |

各レースセクションの見出し下に **コース情報**（例: 芝1600m・左回り）と、
特別名のない条件戦は **クラス名**（例: 3歳以上1勝クラス）を表示。

---

## 2. システム全体のデータフロー

```
[3つのデータソース] → training_analyzer.py → report.html → GitHub Pages
```

### データソース
| # | DB/ツール | 管理 | 内容 |
|---|----------|------|------|
| A | PostgreSQL `pckeiba` | PC-KEIBA DATABASE | JRA-VAN標準(jvd_*): 成績・出馬表・調教・血統・払戻 |
| B | PostgreSQL `keibadata` | JvLinkToImporter | 速報系: オッズ時系列・馬体重・天候馬場 |
| C | クッション値 | cushion/fetch_live_data.py | JRAサイトから取得 |
| D | JV-Link COM直叩き | 32bit Python | B が止まった時の**フォールバック**(馬体重・天候馬場) |

### training_analyzer.py 処理順（__main__）
```
fetch_data()            ①出走馬リスト(se/tkマージ)＋過去走・調教・クッション・馬体重
fetch_live_weight()     ②当日馬体重(keibadata→空ならJV-Link補完→DB書戻し)
fetch_live_track()      ③天候・馬場(keibadata→空ならJV-Link補完→DB書戻し)
analyze()               ④調教分類・コース適性・なごり11秒・当日情報整形
fetch_senso_opponents() ⑤前走相手の次走着順
fetch_live_odds()       ⑥keibadataから最新オッズ・人気
fetch_trainer_data()    ⑦時系列調教＋馬齢/騎手/レース条件(対象厩舎のみ)
judge_trainer()         ⑧調教師買いパターン判定(trainer_rules.py)
fetch_pedigree()        ⑨血統成績(父×競馬場×芝ダ×距離、過去10年)
generate_html()         ⑩8タブHTML生成(パスワード保護込み)
push_to_github()        ⑪commit & push
```

---

## 3. データベース（重要テーブル）

### pckeiba（JRA-VAN / jvd_プレフィックス）
| テーブル | 用途 | 主カラム |
|---------|------|---------|
| jvd_tk | 特別登録馬(火曜) | torokuba_joho_001〜300([3:13]に血統登録番号) |
| jvd_se | 成績/出走表 | data_kubun, bataiju, kakutei_chakujun, tansho_odds, barei, kishumei_ryakusho |
| jvd_ra | レース情報 | kyori, track_code, tenko_code, babajotai_code_shiba/dirt, grade_code, kyoso_shubetsu_code, kyoso_joken_meisho |
| jvd_hc | 坂路調教 | time_gokei_4f, lap_time_4f/3f/2f/1f, chokyo_nengappi |
| jvd_wc | ウッド調教 | time_gokei_6f/5f/4f, lap_time_* |
| jvd_um | 競走馬マスタ | bamei, chokyoshimei_ryakusho, **ketto_joho_01〜14**(3代血統 a=登録番号 b=馬名) |
| jvd_hr | 払戻 | haraimodoshi_tansho_*, haraimodoshi_fukusho_*(a=馬番 b=払戻金) |
| cushion_values | クッション値 | racecourse(日本語名), measured_date, cushion_value |

### keibadata（JvLinkImporter / 速報系）
| テーブル | 用途 | 主カラム |
|---------|------|---------|
| odds1_tansho_jikeiretsu | 単勝オッズ時系列 | umaban, odds(÷10), ninki, happyo_tsukihi_jifun |
| umagoto_race_joho | 速報馬体重 | bataiju, race_code, kaisai_gappi(MMDD) |
| race_shosai | 速報天候馬場 | tenko_code, shiba_babajotai_code, dirt_babajotai_code, race_code |

**注意**: pckeibaは `kaisai_tsukihi`、keibadataは `kaisai_gappi`（どちらもMMDD）。

---

## 4. 重要なコード仕様・定数（間違えやすい点）

### コード変換マップ
```python
_BABA_MAP = {"1":"良","2":"稍重","3":"重","4":"不良"}          # 馬場(1-indexed)
_TENKO    = {"1":"晴","2":"曇","3":"小雨","4":"雨","5":"小雪","6":"雪"}  # 天候(4=雨!)
```
- **track_code**: 10-22=芝, 23-29=ダート, 51-59=障害(芝扱い)
  - 10-16=左回り, 17-22=右回り, 23=ダ左, 24=ダ右, 11/18等=外, 12/19等=内
  - 内外回り表記は新潟/中山/京都/阪神のみ(`_NAIGAI_VENUES`)。東京等は1周コースで省略
- **競走種別**(kyoso_shubetsu_code): 11=2歳, 12=3歳, 13=3歳以上, 14=4歳以上, 18/19=障害
- **競走条件**(kyoso_joken_code): 701=新馬, 703=未勝利, 005=1勝, 010=2勝, 016=3勝, 999=オープン
- **グレード**(grade_code): A=G1, B=G2, C=G3（A/B/C＝重賞）

### ラップ/数値
- ラップ整数3桁÷10=秒(118→11.8)、累計4桁÷10=秒。000/0000=測定不良→除外
- **加速判定**: L1<=L2（終い1Fが前区間と同等以上）。同タイム(12.5-12.5)も加速

### 調教分類（classify_training, 坂路）
L2=ラスト2F, L1=ラスト1F
- A(加速 L1<=L2): A3(L1<12), A2(両方12秒台), A1(L1のみ12秒台)
- B(減速 L1>L2): B3(L2<12), B2(両方12秒台), B1(L2が12秒台・L1≥13)

---

## 5. 主要機能の仕様

### なごり11秒台加速ラップ（当日情報タブ）
前走で全て満たす: ①前走1着 ②前走坂路の終い1Fが11.0〜11.9秒で加速 ③前走から91日以内(中12週)
- ◎(A): 馬体重 前走比 ±0以上 / ○(B): 体重減 / ○?: 体重未確定
- **惜(惜しい)**: 終い1Fが12.0〜12.3秒(11秒台まで0.4秒以下)

### 調教師別 買いパターン（調教タブ、trainer_rules.py）
27調教師のルールを判定。調教を週・曜日で分類(土日/前日/追い切り=当週水木/2週前)。
- ◎=高評価 / ○=該当 / △=併せ馬条件待ち(DB非保持のため注釈)
- 当週追い切り(水木)実施後のみ判定(tk段階の誤発火防止)
- データ基準: 坂路=4F, ウッドF明記なし=5F

### 血統（血統タブ、fetch_pedigree）
- アウトプット②: 各馬の父(01)・母(02)・母父(05) ※jvd_um.ketto_joho
- アウトプット①: 父×今回(競馬場×**芝ダ**×距離)の出走数・勝率・複勝率・**単複回収率**
  - **過去10年**に限定(`date.today().year - 10`)
  - 回収率はjvd_hrの確定払戻ベース、全頭均等購入
  - 適性: ◎=複勝率35%↑or回収率110%↑(10戦以上) / ○ / △ / ―(少)
- 父詳細(クリック展開): 馬場別(芝/ダ×良/やや重/重〜不良)・クッション値別(芝、4帯)＋勝率複勝率

### 馬体重・天候馬場のJV-Linkフォールバック（重要）
JvLinkImporterが止まると keibadata が空になる。その場合:
- `fetch_live_weight` → `jvlink/fetch_weight_jvlink.py`(32bit) で `JVRTOpen("0B12")`→SEレコード
- `fetch_live_track` → `jvlink/fetch_track_jvlink.py`(32bit) で RAレコード
- 取得値はkeibadataにも書き戻す
- **JV-Link COMは32bit専用**: `C:\Users\balka\AppData\Local\Programs\Python\Python314-32\python.exe`(`_PY32_PATH`)
- gen_py破損対策で `win32com.client.dynamic.Dispatch` を使用
- SEレコード(555B): 馬番=29, 血統登録=31, 馬体重=325, 増減符号=328
- RAレコード(1272B): 天候=888, 芝馬場=889, ダ馬場=890

---

## 6. ファイル構成

```
C:\Users\balka\Desktop\Ckeiba\
├── training_analyzer.py    ★メイン(約3300行)
├── trainer_rules.py        ★調教師判定エンジン(27調教師)
├── config.py               DB接続・パスワード(非公開・gitignore)
├── config.py.example       設定テンプレート
├── report.html             生成物(GitHub管理)
│
├── レポート生成.bat         ダブルクリックでtraining_analyzer.py実行
├── update_all.ps1          フル更新(PC-KEIBA＋クッション＋レポート)
├── update_quick.ps1        頻繁更新(レポートのみ)
├── run_pckeiba_update.ps1  PC-KEIBA DATABASE のUI自動操作
├── inspect_pckeiba.ps1     PC-KEIBA UI構造調査(デバッグ)
│
├── cushion/
│   └── fetch_live_data.py  ★JRAサイトからクッション値取得
└── jvlink/
    ├── fetch_weight_jvlink.py  ★JV-Linkから馬体重(32bit)
    ├── fetch_track_jvlink.py   ★JV-Linkから天候馬場(32bit)
    └── odds_db.py / collector.py / parser.py 等
```

---

## 7. 自動化（タスクスケジューラ）

| タスク名 | タイミング | 処理 |
|---------|----------|------|
| KeibaTotalUpdate | 土日 9:00(1回) | PC-KEIBA＋クッション＋レポート |
| KeibaQuickUpdate_Sat/Sun | 土日 9:00〜17:00 / 10分毎 | レポートのみ(オッズ・馬体重・天候を最新化) |

- スタートアップに `JvLinkToImporter.lnk`(PC起動時に常駐しオッズ・馬体重・天候を取込)
- PC-KEIBA DATABASEはGUIのみ→`run_pckeiba_update.ps1`がUI Automation(Alt+D→Enter→開始→閉じる)で自動操作

---

## 8. デプロイ（GitHub Pages）

- リポジトリ: https://github.com/balkan225/ckeiba （Public）
- `.gitignore`: report.html・ソース・ドキュメントのみ許可。**config.py / *.log / *.db / jvlink/_*.py は除外**
- `push_to_github()` が実行ごとに自動 commit & push
- Pages設定: Settings → Pages → Deploy from branch main / root

**セキュリティ(Publicリポジトリのため厳守)**:
- DBパスワード・閲覧パスワード・JRA-VANサービスキーは config.py / jvlink/_*.py のみに保持
- コミット前に必ず `git diff --cached` で実パスワード・サービスキーが含まれないか確認

---

## 9. 設定・環境

| 項目 | 値 |
|------|-----|
| pckeiba DB | 127.0.0.1:5432 / postgres / pw=＜config.py＞ |
| keibadata DB | localhost:5432 / postgres / pw=＜config.py＞ |
| Python(64bit) | C:\Users\balka\AppData\Local\Python\bin\python.exe |
| Python(32bit) | C:\Users\balka\AppData\Local\Programs\Python\Python314-32\python.exe (JV-Link用) |
| 必要パッケージ | psycopg2, requests, beautifulsoup4, sqlalchemy, pandas, pywin32(32bit側) |
| cmdのコードページ | UTF-8(65001)。.batはUTF-8保存・chcp行なし |

---

## 10. 手動実行

```
:: ダブルクリック
レポート生成.bat                    （training_analyzer.py のみ）

:: PowerShell
powershell -ExecutionPolicy Bypass -File update_all.ps1     （フル）
powershell -ExecutionPolicy Bypass -File update_quick.ps1   （レポートのみ）

:: 直接
python training_analyzer.py
```

---

## 11. トラブルシューティング

| 症状 | 原因 / 対処 |
|------|-----------|
| 出馬表が反映されない | PC-KEIBAでデータ取込前。`run_pckeiba_update.ps1`実行 or 待つ |
| 馬体重が空 | JvLinkImporter停止→fetch_live_weightがJV-Link補完(自動)。手動なら JvLinkToImporter 再起動 |
| 天候・馬場が空/おかしい | 同上(fetch_live_trackがJV-Link補完)。天候コードは4=雨(雪でない) |
| クッション値が古い | `cushion/fetch_live_data.py`実行。JRAは金曜分を除外する仕様 |
| 血統「該当データなし」 | 過去10年×今回(競馬場×芝ダ×距離)に該当なし。サンプル不足 |
| なごり0頭 | 条件(11秒台かつ前走1着)が厳しく通常少数。正常 |
| 調教師判定が出ない | 当週追い切り(水木)実施前は保留。木金以降に判定 |
| JV-Link "クラスが登録されていません" | 64bitで実行している。32bit Pythonを使う |
| JV-Link gen_pyエラー | dynamic.Dispatch使用済。再発時は %LOCALAPPDATA%\Temp\gen_py 削除 |
| .bat日本語が文字化け | cmdは65001。.batをUTF-8保存(chcp行なし)。Shift-JIS保存はNG |
| GitHub push失敗 | 認証切れ→`git push`手動で再認証 |
| ターミナルで日本語化け | cp932環境。`io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')`で出力 |

---

## 12. 新規環境構築

`SETUP.md` を参照（PostgreSQL・Python・JRA-VAN・PC-KEIBA・JvLinkImporterの導入手順、
config.py作成、タスク登録、環境依存パスの修正箇所一覧）。

---

## 13. Claudeへの引き継ぎ（このリポジトリで作業を続ける場合）

GitHubに **ソースコード全部＋SPEC.md＋SETUP.md** が入っているので、
それを渡せばコードの再現・改修は可能。ただし以下は別途必要:
- JRA-VAN Data Lab契約(有料)・JV-Link認証
- PC-KEIBA DATABASE / JvLinkToImporter(市販ツール)
- PostgreSQLの蓄積データ(数年分、pg_dumpで移行)

開発時の注意:
- ターミナルはcp932で日本語化けるが処理は正常。UTF-8出力ラッパーで確認
- 機能追加後は必ず秘密情報スキャン → commit → push
- 大きいデータ補完(馬体重・天候)はkeibadata優先→JV-Link(32bit)フォールバックのパターンを踏襲
