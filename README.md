# slackBU01

Slackのバックアップツール検証用リポジトリです。

## このツールでできること
- OAuth 2.0 を使って Slack ワークスペースからアクセストークンを取得し、会話履歴をエクスポート
- すべてのメッセージを CSV 形式で出力
- メッセージに添付されたアップロードファイルをまとめてローカルにダウンロード

## 前提条件
- Python 3.10 以上
- Slack アプリ（User Token）を事前に作成し、次の OAuth スコープを付与してください
  - `channels:history`, `groups:history`, `im:history`, `mpim:history`, `channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`, `files:read`, `team:read`
  - リダイレクト URL に `http://localhost:8721/callback` を追加

## 使い方
1. 依存ライブラリをインストールします。
   ```bash
   pip install -r requirements.txt
   ```
2. エクスポートを実行します。
   ```bash
   python slack_export.py \
     --client-id <SlackのClient ID> \
     --client-secret <SlackのClient Secret>
   ```
   - 初回実行時はブラウザで認可画面が開きます。許可後、`tokens.json` に取得したトークンが保存され、次回以降はそのまま再利用されます。
   - 異なるスコープで再取得したい場合は `tokens.json` を削除してください。

## 出力内容
- `exports/<team名>_<timestamp>/messages.csv`
  - `channel_name, channel_id, user_id, user_name, timestamp, text, file_names` を含みます。
- `exports/<team名>_<timestamp>/files/` 以下にメッセージ添付ファイルを保存します。

## 注意点
- 本スクリプトは API レート制限を考慮した簡易実装です。大量データを扱う場合は適宜ウェイトを追加してください。
- ワークスペースやスコープ設定により取得できるデータが変わります。
