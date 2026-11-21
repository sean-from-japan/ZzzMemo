# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## このリポジトリについて

ZzzMemo は、人が1行ずつ書き、AIエージェントが後から仕分ける個人用メモ帳。FastAPI + SQLite + 素のJavaScript。本番は Fly.io（https://zzzmemo.fly.dev、region nrt、TZ=Asia/Tokyo）。アプリの中にLLMは置かない。仕分けはエージェント側で行う。

## ファイル構成

| パス | 役割 |
|---|---|
| `qcatch.py` | サーバー起動（Dockerfile の CMD） |
| `core/db.py` | 保存処理。`entries`（書いた行）・`kinds`（種類と項目の定義）・`items`（仕分け後）・`change_log`（全変更、取り消し用）・`opened`（資料を最後に開いた日時。変更ではないので記録しない）・`page_state`（資料のページが `localStorage` に書いた内容。入力のたびに保存するので記録しない）。HTML・PDFのファイルはDBに入れず `data/files/` に置く |
| `core/google_login.py` | Googleでのログイン。メールアドレスの確認だけで、トークンは保存しない |
| `web/server.py` | ログイン（パスワードとGoogle）、認証ミドルウェア、ページ、Service Worker |
| `web/deps.py` | 認証の設定とセッショントークン |
| `web/routers/api.py` | REST API と、本人／エージェントで許す操作の区別 |
| `web/static/` | `index.html`（CSS込み）、`app.js`、`manifest.json`、`sw.js`（キャッシュしない）、`slide_paging.js`（資料のHTMLに埋め込むスライド送り）、`page_storage.js`（資料のHTMLの先頭に埋め込む `localStorage` の代わり） |
| `mcp_server.py` | エージェント用の15ツール。引数なしでMCPサーバー、`call <tool> '<json>'` でCLI |
| `migrate_v1.py` | 第1版の表から一度だけ移す（本番では2026-11-19に実行済み） |
| `tests/` | `python -m pytest tests` |

## コマンド

```bash
python dev.py pull               # 本番のDBとファイルの写しを ~/.cache/zzzmemo-dev に取る
python dev.py                    # 写しで http://localhost:5001 を起動（loopback ではログイン不要）
python -m pytest tests
fly deploy --app zzzmemo
fly logs --app zzzmemo
fly secrets list --app zzzmemo   # 名前だけ出る
```

## 進め方

- **画面の変更は、本番の写しで動く手元のサーバー（`python dev.py`）で確かめてから出す。** 本番に出してから本人に確かめてもらう、を繰り返さない。手元でPC幅とスマホ幅、明るい配色と暗い配色を見て、本人にも `http://localhost:5001` で見てもらい、直し終えてからまとめて1回デプロイする
- 写しへの変更は手元に残るだけで本番には届かない。本番のデータを変えるのは、エージェントのツール（`mcp_server.py`）か本人の操作だけ

## 設計上の決まり

- **本人とエージェントの区別はサーバーで行う。** `X-Api-Key` か `X-Actor` が付いた要求はエージェント。エージェントは種類を「確認待ち」でしか追加できず、種類の変更・削除、本人が書いた行の書き換え・削除はできない（403）。`X-Actor: human` を名乗っても本人扱いにしない
- **書き込みは必ず `core/db.py` の `_write` を通す。** 変更前と変更後が `change_log` に残り、取り消せる。取り消しは、その後に同じ行が変わっていれば断る。例外は `opened`（開いた日時）と `page_state`（ページの入力内容）だけ
- **列を足すときは `_ADDED_COLS` に書く。** `init()` が既存のDBに列を足し、取り消しは古い記録に無い列を既定値で補う。書かないと、それまでの変更が取り消せなくなる
- **エージェントは種類を変えられないが、変更を提案できる**（`POST /api/kinds/{id}/proposal`、`kinds.proposed_fields`）。本人が確認画面で承認すると `fields` に入る
- **ファイルの項目の日付は `items.file_at`。** `add_file` が上げる前のファイルの更新日時を入れ、画面は「8月29日更新」と出す。項目の作成日（上げた日）とは別
- **種類はデータ。** 画面とAPIの検査は `kinds.fields` の定義から作る。種類ごとのコードを足さない
- **一覧は2つの画面。** `#list` は種類を大きなカードで選ぶ画面で、`kinds.main` が1のもの（よく使う種類）を大きく、残りを「ほかの種類」に小さく出す。`#list/<種類のid>` がその種類の画面で、ファイルの項目を持つ種類では分類を大きなカードで選ぶ画面、それ以外は一覧になる。`#list/<種類のid>/<分類>` がその分類の一覧（`*` はすべて、`_none` は分類なし）。深い画面へは右から、戻るときは左から滑らせる。本人だけが一覧のタイトル横の☆で「よく使う」を切り替えられる。`main` の列を足したときに1回だけ、タスクとファイルの項目を持つ種類を1にした
- **一覧は「分類」（無ければ最初の選択肢の項目）でカードに分ける。** カードの順は選択肢の順、色も選択肢の位置で決まる。選択肢のアイコンは項目の定義の `icons`（選択肢→絵文字）に持たせる。並び替えもアイコンも、エージェントは `propose_kind_change` で提案し、本人が確認画面で承認する
- **認証なしで外部に公開しない。** `deps.assert_auth_configured()` が、loopback 以外の HOST でログイン設定が無いと起動を止める
- **資料のHTMLは `/files/{id}` で、CSPの `sandbox`（`allow-same-origin` なし）を付けて返す。** 中のスクリプトがクッキーやAPIに触れないようにするため。PDFはChromeが隔離された枠で表示しないので付けない。保存したファイルは書き換えず、直すときは新しいidにする。隔離した枠では `localStorage` が使えないので、返すときに `page_storage.js` を先頭に入れ、前回の内容を渡す。ページの変更は `postMessage` で読む画面に届き、`PUT /api/items/{id}/page-state`（本人だけ）で保存する
- **ページと `app.js` は `Cache-Control: no-cache`。** `sw.js` は古いキャッシュを消すだけで、何もキャッシュしない
- `data/` のファイルは消さない・上書きしない（本番データ、退避したDB）。テストは一時ファイルのDBで行い、リポジトリの中でサーバーを起動して `data/` を変えない
- 公開リポジトリなので、テストや例文に作者の実際のメモ・予定・個人の情報を入れない
- Fly.io では `fly secrets` が `fly.toml` の `[env]` を上書きする（同名キー）
- コミット・プッシュは明示的に求められたときだけ。`--no-verify` は使わない
