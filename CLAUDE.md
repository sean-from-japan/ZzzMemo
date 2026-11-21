# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## このリポジトリについて

ZzzMemo は、人が1行ずつ書き、AIエージェントが後から仕分ける個人用メモ帳。FastAPI + SQLite + 素のJavaScript。本番は Fly.io（https://zzzmemo.fly.dev、region nrt、TZ=Asia/Tokyo）。アプリの中にLLMは置かない。仕分けはエージェント側で行う。

## ファイル構成

| パス | 役割 |
|---|---|
| `qcatch.py` | サーバー起動（Dockerfile の CMD） |
| `core/db.py` | 保存処理。`entries`（書いた行）・`kinds`（種類と項目の定義）・`items`（仕分け後）・`change_log`（全変更、取り消し用）・`opened`（資料を最後に開いた日時。変更ではないので記録しない）。HTML・PDFのファイルはDBに入れず `data/files/` に置く |
| `core/google_login.py` | Googleでのログイン。メールアドレスの確認だけで、トークンは保存しない |
| `web/server.py` | ログイン（パスワードとGoogle）、認証ミドルウェア、ページ、Service Worker |
| `web/deps.py` | 認証の設定とセッショントークン |
| `web/routers/api.py` | REST API と、本人／エージェントで許す操作の区別 |
| `web/static/` | `index.html`（CSS込み）、`app.js`、`manifest.json`、`sw.js`（キャッシュしない）、`slide_paging.js`（資料のHTMLに埋め込むスライド送り） |
| `mcp_server.py` | エージェント用の15ツール。引数なしでMCPサーバー、`call <tool> '<json>'` でCLI |
| `migrate_v1.py` | 第1版の表から一度だけ移す（本番では2026-11-19に実行済み） |
| `tests/` | `python -m pytest tests` |

## コマンド

```bash
python qcatch.py                 # http://localhost:5000（loopback ではログイン不要）
python -m pytest tests
fly deploy --app zzzmemo
fly logs --app zzzmemo
fly secrets list --app zzzmemo   # 名前だけ出る
```

## 設計上の決まり

- **本人とエージェントの区別はサーバーで行う。** `X-Api-Key` か `X-Actor` が付いた要求はエージェント。エージェントは種類を「確認待ち」でしか追加できず、種類の変更・削除、本人が書いた行の書き換え・削除はできない（403）。`X-Actor: human` を名乗っても本人扱いにしない
- **書き込みは必ず `core/db.py` の `_write` を通す。** 変更前と変更後が `change_log` に残り、取り消せる。取り消しは、その後に同じ行が変わっていれば断る。例外は `opened`（開いた日時）だけ
- **列を足すときは `_ADDED_COLS` に書く。** `init()` が既存のDBに列を足し、取り消しは古い記録に無い列を既定値で補う。書かないと、それまでの変更が取り消せなくなる
- **エージェントは種類を変えられないが、変更を提案できる**（`POST /api/kinds/{id}/proposal`、`kinds.proposed_fields`）。本人が確認画面で承認すると `fields` に入る
- **種類はデータ。** 画面とAPIの検査は `kinds.fields` の定義から作る。種類ごとのコードを足さない
- **認証なしで外部に公開しない。** `deps.assert_auth_configured()` が、loopback 以外の HOST でログイン設定が無いと起動を止める
- **資料のHTMLは `/files/{id}` で、CSPの `sandbox`（`allow-same-origin` なし）を付けて返す。** 中のスクリプトがクッキーやAPIに触れないようにするため。PDFはChromeが隔離された枠で表示しないので付けない。保存したファイルは書き換えず、直すときは新しいidにする
- **ページと `app.js` は `Cache-Control: no-cache`。** `sw.js` は古いキャッシュを消すだけで、何もキャッシュしない
- `data/` のファイルは消さない・上書きしない（本番データ、退避したDB）。テストは一時ファイルのDBで行い、リポジトリの中でサーバーを起動して `data/` を変えない
- 公開リポジトリなので、テストや例文に作者の実際のメモ・予定・個人の情報を入れない
- Fly.io では `fly secrets` が `fly.toml` の `[env]` を上書きする（同名キー）
- コミット・プッシュは明示的に求められたときだけ。`--no-verify` は使わない
