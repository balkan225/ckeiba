# セットアップ手順書（新規環境構築）

このシステムをゼロから別環境に構築する場合の手順。
※ 詳細仕様は `SPEC.md` を参照。

---

## 前提：必要なアカウント・ライセンス

| 項目 | 必須 | 備考 |
|------|------|------|
| JRA-VAN Data Lab 契約 | 🔴必須 | 月額有料。JV-Link認証に必要 |
| PC-KEIBA DATABASE | 🔴必須 | JRA-VANデータをPostgreSQLに取込 |
| JvLink To Importer | 🟡推奨 | オッズ時系列が必要な場合 |
| GitHubアカウント | 🟡推奨 | Web公開する場合 |

---

## STEP 1: 基盤ソフトのインストール

```
1. PostgreSQL 15+ をインストール（user=postgres）
2. Python 3.12+ をインストール
3. Git をインストール
4. JRA-VAN Data Lab + JV-Link をインストール・認証
5. PC-KEIBA DATABASE をインストール・DB設定
6. JvLink To Importer をインストール（オッズ用）
```

## STEP 2: Pythonパッケージ

```bash
pip install psycopg2 requests beautifulsoup4 sqlalchemy pandas
```

## STEP 3: データベース準備

```
1. PC-KEIBA DATABASE で「セットアップデータ登録」を実行
   → pckeiba DB に過去データを一括取込（時間がかかる）
2. 以降は「通常データ登録」で差分更新
3. JvLink To Importer で keibadata DB にオッズ取込開始
```

## STEP 4: クッション値DB

```bash
cd cushion
python setup_db.py            # テーブル作成
python scraper.py             # 過去データ一括取得（PDF）
python fetch_live_data.py     # 最新データ取得
```

## STEP 5: 設定ファイル

`config.py` を作成し、DB接続情報を環境に合わせて記入:
```python
DB_CONFIG      = {"host":"127.0.0.1","port":5432,"database":"pckeiba","user":"postgres","password":"＜PW＞"}
ODDS_DB_CONFIG = {"host":"localhost","port":5432,"database":"keibadata","user":"postgres","password":"＜PW＞"}
DAYS_AHEAD = 7
TRAINING_DAYS_BACK = 21
DATA_SOURCE = "auto"
```

## STEP 6: 動作確認

```bash
python training_analyzer.py
# → report.html が生成されればOK
```

## STEP 7: GitHub Pages（Web公開する場合）

```bash
git init
git branch -M main
# .gitignore で report.html のみ対象にする
git add report.html .gitignore
git commit -m "Initial commit"
git remote add origin https://github.com/＜ユーザー＞/＜リポジトリ＞.git
git push -u origin main
# GitHub: Settings → Pages → Deploy from branch main/root
```
※ `training_analyzer.py` の `push_to_github()` 内のURL・ユーザー名を環境に合わせて修正。

## STEP 8: 自動化（タスクスケジューラ）

```
1. JvLinkToImporter のショートカットをスタートアップに配置
   （shell:startup フォルダ）
2. update_all.ps1 / update_quick.ps1 のパスを環境に合わせて修正
3. schtasks でタスク登録（SPEC.md 第6章のコマンド参照）
```

---

## パスの修正が必要なファイル（環境依存）

別環境では以下のハードコードされたパスを書き換える:

| ファイル | 修正箇所 |
|---------|---------|
| `update_all.ps1` / `update_quick.ps1` | `$python`, `$ckeiba` のパス |
| `run_pckeiba_update.ps1` | `$appref`, `$ckeiba` のパス |
| `cushion/fetch_live_data.py` | config.py へのパス |
| `jvlink/odds_db.py` | config.py へのパス、sys.path |
| `training_analyzer.py` | `push_to_github()` のGitHub URL、`_PW` |

---

## 動作に必要な常時稼働の前提

- PCが起動しログインしている（タスクは Interactive ユーザーで実行）
- JvLink To Importer が常駐している（オッズ取得）
- PostgreSQL サービスが起動している
- ネットワーク接続がある
