# 競馬出走馬 総合分析レポート ― 仕様書 / 引き継ぎ資料

最終更新: 2026-06-06

---

## 1. システム概要

JRA出走予定馬について、調教・クッション値・コース適性・前走相手関係・当日情報・AI総評を
1つのHTMLレポートにまとめ、GitHub Pagesで公開するシステム。

- **入力**: PostgreSQL（JRA-VANデータ）＋ クッション値DB ＋ リアルタイムオッズDB
- **出力**: `report.html`（パスワード保護つき、レスポンシブ対応）
- **公開URL**: https://balkan225.github.io/ckeiba/report.html
- **閲覧パスワード**: `training_analyzer.py` の `_PW` に設定（※実値は非公開）
- **対象期間**: 今日から7日後まで（`config.DAYS_AHEAD`）

---

## 2. ディレクトリ構成

```
C:\Users\balka\Desktop\Ckeiba\
├── training_analyzer.py    ★メインスクリプト（レポート生成）約2400行
├── config.py               DB接続設定・パラメータ
├── report.html             生成されるレポート（GitHub管理対象）
├── .gitignore              report.html 以外を除外
│
├── update_all.ps1          フル更新（PC-KEIBA＋クッション＋レポート）
├── update_quick.ps1        頻繁更新（PC-KEIBA＋レポートのみ）
├── run_pckeiba_update.ps1  PC-KEIBA DATABASE のUI自動操作
├── inspect_pckeiba.ps1     PC-KEIBA UI構造調査用（デバッグ）
├── update_all.log          フル更新ログ
├── update_quick.log        頻繁更新ログ
│
├── cushion\                クッション値取得サブプロジェクト
│   ├── fetch_live_data.py  ★JRAサイトからクッション値取得→DB保存
│   ├── parser.py / scraper.py / setup_db.py / migrate_to_postgres.py
│   └── cushion.db          SQLite（PostgreSQLにも同期保存）
│
└── jvlink\                 オッズ取得サブプロジェクト（別途運用）
    ├── odds_db.py          ★keibadata DBからオッズ取得
    ├── fetch_weight_jvlink.py ★JV-Linkから馬体重を直接取得（32bit Python専用）
    ├── show_odds.py        オッズ確認CLI
    ├── collector.py / parser.py / visualize.py
    └── （_test_*, _debug_* 等は開発用）
```

### 馬体重のフォールバック取得（重要）
当日馬体重は通常 keibadata.umagoto_race_joho（JvLinkImporter経由）から取得するが、
JvLinkImporterが停止すると空になる。その場合 `training_analyzer.fetch_live_weight()` が
**32bit Pythonで `jvlink/fetch_weight_jvlink.py` を subprocess 起動**し、
JV-Link を直接叩いて（`JVRTOpen("0B12")` → SEレコード）馬体重を補完、keibadataにも書き戻す。
- JV-Link COMは32bit専用 → `C:\...\Python314-32\python.exe`（`_PY32_PATH`）を使用
- SEレコード(555B)の馬体重位置: 馬番=29, 血統登録=31, 馬体重=325, 増減符号=328, 増減差=329

---

## 3. データソース

### DB① `pckeiba`（PostgreSQL / JRA-VAN標準データ）

PC-KEIBA DATABASE ツールが管理。接続情報は `config.DB_CONFIG`。

| テーブル | 用途 | 主なカラム |
|---------|------|-----------|
| `jvd_tk` | 特別登録馬（火曜公開） | torokuba_joho_001〜300（[3:13]に血統登録番号） |
| `jvd_se` | 成績/出走表 | data_kubun, ketto_toroku_bango, wakuban, umaban, bataiju, kakutei_chakujun, tansho_odds, tansho_ninkijun |
| `jvd_ra` | レース情報 | kyori, track_code, tenko_code, babajotai_code_shiba/dirt, kyosomei_hondai |
| `jvd_hc` | 坂路調教 | time_gokei_4f, lap_time_4f/3f/2f/1f, chokyo_nengappi |
| `jvd_wc` | ウッド調教 | time_gokei_6f/5f, lap_time_6f〜1f |
| `jvd_um` | 競走馬マスタ | bamei, chokyoshimei_ryakusho |
| `cushion_values` | クッション値 | racecourse, measured_date, cushion_value |

**`jvd_se.data_kubun` の意味**
- `'1'` = 出走馬名表（水〜木）
- `'2'` = 出馬表（枠・馬番確定、木〜金）
- `'7'`/`'9'` = 成績確定（レース後）

### DB② `keibadata`（PostgreSQL / JV-Linkリアルタイム）

JvLinkToImporter ツールが管理。接続情報は `config.ODDS_DB_CONFIG`。

| テーブル | 用途 | 主なカラム |
|---------|------|-----------|
| `odds1_tansho_jikeiretsu` | 単勝オッズ時系列 | umaban, odds（÷10）, ninki, happyo_tsukihi_jifun |
| `odds1_fukusho_jikeiretsu` | 複勝オッズ時系列 | odds_saitei, odds_saikou |

---

## 4. 重要な仕様・ロジック

### 4-1. データソースの自動選択（`config.DATA_SOURCE = "auto"`）
レースごとに `jvd_se(kubun=2) > jvd_se(kubun=1) > jvd_tk` の優先度でマージ。
jvd_se がカバーするレースは jvd_tk の登録を無視（除外馬の混入を防ぐ）。

### 4-2. 調教分類（坂路）
- ラスト2F(L2)・ラスト1F(L1)から `A3/A2/A1/B3/B2/B1` を判定（`classify_training`）
- A系=加速ラップ、B系=減速。A3が最高評価。

### 4-3. コード変換マップ（重要：1-indexed）
```python
_BABA_MAP = {"1":"良", "2":"稍重", "3":"重", "4":"不良"}   # 馬場状態
_TENKO    = {"1":"晴", "2":"曇", "3":"雨", "4":"雪", "5":"霙"}
```
- `track_code`: 10-22=芝, 23-29=ダート, 51-59=障害(芝扱い)
- 過去走の芝ダ判定は `babajotai_code`（埋まっている方）を優先（`_track_type_from_row`）

### 4-4. なごり11秒台加速ラップ判定（当日情報タブ）
前走で以下を**すべて満たす**馬に印をつける（`analyze()` 内）:
1. 前走1着
2. 前走の坂路ラスト1Fが 11.0〜11.9秒 かつ L1 < L2（加速）
3. 前走から **91日以内（中12週以内）** の出走
- グループA（◎/オレンジ）: 馬体重 前走比 ±0以上
- グループB（○/青）: 馬体重 前走比 マイナス
- `?`（グレー）: 馬体重未確定（当日再実行で確定）

### 4-5. AI総評（レーダーチャート）
4軸を各0〜5点で評価、合計20点で `S/A/B/C/D` グレード化（`_ai_score`）:
- 調教 / クッション適性 / コース適性（距離＋競馬場） / 前走相手レベル
- SVGで描画（外部ライブラリ不要、`_radar_svg`）

### 4-6. 前走相手関係タブ
前走の全出走馬を取得し、各相手の「次走着順」を表示。
前走レベルの指標（相手の次走3着内率）が分かる。本馬・今回同走馬も識別表示。

---

## 5. レポートのタブ構成（HTML）

| タブ | 内容 |
|------|------|
| 📅 当日情報 | 馬体重(増減)・体重推移3走・オッズ・人気・なごり11秒判定。天候/馬場は上部に表示 |
| 🏋 調教 | 坂路/ウッドのタイム・ラップ・評価。過去5走の調教も表示 |
| 🌱 クッション | クッション値適性・過去10走のCV戦績 |
| 🏇 コース適性 | 距離別/芝ダ別/馬場別/競馬場別の成績（★=今回条件） |
| 🔄 前走相手 | 前走相手の次走着順（アコーディオン展開） |
| 📊 総評 | AI4軸レーダーチャート＋グレード（スコア降順カード） |

---

## 6. 自動化（タスクスケジューラ）

| タスク名 | タイミング | 処理内容 |
|---------|----------|---------|
| `KeibaTotalUpdate` | 土日 9:00（1回） | PC-KEIBA + クッション + レポート |
| `KeibaQuickUpdate_Sat` | 土 9:00〜17:00 / 10分毎 | PC-KEIBA + レポート |
| `KeibaQuickUpdate_Sun` | 日 9:00〜17:00 / 10分毎 | PC-KEIBA + レポート |

**スタートアップ登録**: `JvLinkToImporter.lnk`（PC起動時に自動起動しオッズ取得）

### タスク再登録コマンド（参考）
```powershell
# 頻繁更新タスクの例（schtasksで10分間隔を設定）
schtasks /create /tn "KeibaQuickUpdate_Sat" `
  /tr "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File `"C:\Users\balka\Desktop\Ckeiba\update_quick.ps1`"" `
  /sc weekly /d SAT /st 09:00 /ri 10 /du 0008:00 /f
```

---

## 7. PC-KEIBA DATABASE のUI自動操作（`run_pckeiba_update.ps1`）

GUIツールにCLIがないため、Windows UI Automation + キー操作で自動化:
1. アプリ未起動なら `.appref-ms` から起動
2. ウィンドウにフォーカス → `Alt+D`（データメニュー）→ `Enter`（通常データ登録）
3. 「通常データ登録」ダイアログの「開始」ボタンをクリック
4. 完了後に表示される「閉じる」ボタンを自動クリック（最大15分待機）

**注意**: 画面解像度やウィンドウ位置が変わるとマウス座標クリックがずれる可能性。
その場合は InvokePattern が優先されるため通常は問題ないが、UI構造変化時は
`inspect_pckeiba.ps1` で再調査する。

---

## 8. デプロイ（GitHub Pages）

- リポジトリ: https://github.com/balkan225/ckeiba （Public）
- `.gitignore` で `report.html` のみコミット対象
- `training_analyzer.py` の `push_to_github()` が実行ごとに自動 commit & push
- GitHub Pages 設定: Settings → Pages → Deploy from branch `main` / root

**重要**: `config.py`（DBパスワード入り）は絶対にコミットしないこと（.gitignore済）

---

## 9. 設定・認証情報

| 項目 | 値 |
|------|-----|
| pckeiba DB | host=127.0.0.1:5432, user=postgres, pw=＜config.py参照＞ |
| keibadata DB | host=localhost:5432, user=postgres, pw=＜config.py参照＞ |
| 閲覧パスワード | `training_analyzer.py` の `_PW`（実値は非公開） |
| Python | `C:\Users\balka\AppData\Local\Python\bin\python.exe`（3.14.5） |
| 必要パッケージ | psycopg2, requests, beautifulsoup4, sqlalchemy, pandas |
| GitHubユーザー | balkan225 |

---

## 10. 手動実行方法

```bat
:: フル更新（クッション値含む）
powershell -ExecutionPolicy Bypass -File "C:\Users\balka\Desktop\Ckeiba\update_all.ps1"

:: 頻繁更新（レポートのみ高速）
powershell -ExecutionPolicy Bypass -File "C:\Users\balka\Desktop\Ckeiba\update_quick.ps1"

:: レポート生成のみ（DBは更新しない）
cd C:\Users\balka\Desktop\Ckeiba
python training_analyzer.py
```

---

## 11. データ更新の責任分担（手動/自動）

| データ | 更新ツール | 自動化状況 |
|--------|-----------|-----------|
| pckeiba（jvd_*） | PC-KEIBA DATABASE | ✅ UI自動操作で自動化済 |
| keibadata（オッズ） | JvLinkToImporter | ✅ スタートアップ常駐で自動 |
| cushion_values | cushion/fetch_live_data.py | ✅ 9時タスクで自動 |
| report.html + GitHub | training_analyzer.py | ✅ タスクで自動 |

---

## 12. トラブルシューティング

| 症状 | 原因 / 対処 |
|------|-----------|
| 出馬表が反映されない | JRA-VANのデータ取込前。PC-KEIBAで通常データ登録を実行 |
| オッズが空欄 | レース当日午前以降に反映。`fetch_live_odds`はumaban必須 |
| クッション値が古い | `cushion/fetch_live_data.py` 実行。JRAは金曜分を除外する仕様 |
| GitHub pushが失敗 | 認証切れ→`git push` を手動実行して再認証 |
| なごり判定が0頭 | 条件（11秒台かつ前走1着）が厳しいため通常少数。正常 |
| 文字化け（ターミナル） | cp932環境。UTF-8で出力するには `io.TextIOWrapper` を使用 |
| PC-KEIBA自動操作が失敗 | ウィンドウ位置変化等。`inspect_pckeiba.ps1`でUI再調査 |

---

## 13. 関連プロジェクト（別リポジトリ/フォルダ）

| プロジェクト | 場所 | 役割 |
|-------------|------|------|
| JRA cushion value PDF database | `cushion\` | クッション値のPDF/HTML取得 |
| JVLink data importer | `C:\Program Files (x86)\JvLink To Importer\` | オッズ等リアルタイムDB取込 |
| PC-KEIBA DATABASE | ClickOnce（AppData配下） | JRA-VAN標準データ取込 |

---

## 14. メインスクリプトの処理フロー（training_analyzer.py）

```
__main__
  ├─ fetch_data()              # ①出走馬リスト取得（se/tkマージ）
  │                            #   ②過去走・調教・クッション・馬体重も取得
  ├─ analyze(rows)             # ③分類・コース適性・なごり判定・当日情報整形
  ├─ fetch_senso_opponents()   # ④前走相手の次走着順を取得
  ├─ fetch_live_odds()         # ⑤keibadataから最新オッズ取得
  ├─ generate_html()           # ⑥6タブHTML生成（パスワード保護込み）
  └─ push_to_github()          # ⑦commit & push
```

主要関数:
- `fetch_data()` (79行〜) — データ取得の中核
- `analyze()` (847行〜) — 全分析ロジック
- `_ai_score()` / `_radar_svg()` — AI総評
- `generate_html()` (1314行〜) — HTML生成の中核
