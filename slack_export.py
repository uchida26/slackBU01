#!/usr/bin/env python3
"""Slack export helper for OAuth-based downloads."""

import argparse
import csv
import datetime as dt
import http.server
import json
import pathlib
import secrets
import threading
import urllib.parse
import webbrowser
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from slack_sdk import WebClient

TOKEN_STORE = pathlib.Path("tokens.json")
DEFAULT_SCOPES = [
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "channels:read",
    "groups:read",
    "im:read",
    "mpim:read",
    "users:read",
    "files:read",
    "team:read",
]
DEFAULT_PORT = 8721


class OAuthResult:
    def __init__(self) -> None:
        self.code: Optional[str] = None
        self.state: Optional[str] = None
        self.error: Optional[str] = None


def build_auth_url(client_id: str, scopes: List[str], redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "scope": " ".join(scopes),
        "user_scope": "",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, result: OAuthResult, expected_state: str, *args, **kwargs):
        self.result = result
        self.expected_state = expected_state
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # silence default logging

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        normalized_path = parsed.path.rstrip("/") or "/"
        if normalized_path != "/callback":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = """
                <h2>Slack export OAuth callback server</h2>
                <p>The OAuth redirect has not been received yet.</p>
                <p>If you reached this page manually, please return to the authorization flow in Slack.</p>
            """
            self.wfile.write(body.encode("utf-8"))
            return

        params = urllib.parse.parse_qs(parsed.query)
        self.result.code = params.get("code", [None])[0]
        self.result.state = params.get("state", [None])[0]
        self.result.error = params.get("error", [None])[0]

        if self.result.state != self.expected_state:
            self.result.error = "State mismatch"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if self.result.error:
            body = f"<h2>OAuth failed</h2><p>{self.result.error}</p>"
        else:
            body = "<h2>Authorization received</h2><p>You can close this window.</p>"
        self.wfile.write(body.encode("utf-8"))


def obtain_token(client_id: str, client_secret: str, scopes: List[str], port: int = DEFAULT_PORT) -> Dict:
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://localhost:{port}/callback"
    result = OAuthResult()

    def handler(*args, **kwargs):
        OAuthHandler(result, state, *args, **kwargs)

    server = http.server.HTTPServer(("localhost", port), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    auth_url = build_auth_url(client_id, scopes, redirect_uri, state)
    print("Open the following URL to authorize the app (a browser will attempt to open automatically):")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    while result.code is None and result.error is None:
        thread.join(0.2)

    server.shutdown()
    thread.join()

    if result.error:
        raise RuntimeError(f"OAuth failed: {result.error}")

    response = requests.post(
        "https://slack.com/api/oauth.v2.access",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": result.code,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Slack token exchange failed: {payload}")

    TOKEN_STORE.write_text(json.dumps(payload, indent=2))
    print(f"Saved token information to {TOKEN_STORE}")
    return payload


def load_token() -> Optional[Dict]:
    if not TOKEN_STORE.exists():
        return None
    return json.loads(TOKEN_STORE.read_text())


def ensure_token(client_id: str, client_secret: str, scopes: List[str], port: int) -> Dict:
    existing = load_token()
    if existing:
        return existing
    return obtain_token(client_id, client_secret, scopes, port)


def build_output_dir(team_name: str) -> pathlib.Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = pathlib.Path("exports") / f"{team_name}_{timestamp}"
    base.mkdir(parents=True, exist_ok=True)
    (base / "files").mkdir(exist_ok=True)
    return base


def fetch_users(client: WebClient) -> Dict[str, str]:
    users: Dict[str, str] = {}
    cursor: Optional[str] = None
    while True:
        resp = client.users_list(cursor=cursor)
        for member in resp.get("members", []):
            users[member["id"]] = member.get("real_name") or member.get("name")
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return users


def iter_conversations(client: WebClient) -> Iterable[Tuple[str, str]]:
    cursor: Optional[str] = None
    types = ",".join(["public_channel", "private_channel", "im", "mpim"])
    while True:
        resp = client.conversations_list(types=types, cursor=cursor, limit=200)
        for conv in resp.get("channels", []):
            yield conv["id"], conv.get("name") or conv.get("user") or conv["id"]
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break


def convert_ts(ts: str) -> str:
    try:
        return dt.datetime.fromtimestamp(float(ts)).isoformat()
    except Exception:
        return ts


def download_file(token: str, file_info: Dict, dest_dir: pathlib.Path) -> Optional[pathlib.Path]:
    url = file_info.get("url_private_download") or file_info.get("url_private")
    if not url:
        return None
    filename = file_info.get("name") or f"file_{file_info.get('id', 'unknown')}"
    safe_name = filename.replace("/", "-")
    dest_path = dest_dir / safe_name
    headers = {"Authorization": f"Bearer {token}"}
    with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
    return dest_path


def export_history(client: WebClient, token_payload: Dict, output_dir: pathlib.Path) -> None:
    token = token_payload.get("access_token") or token_payload.get("authed_user", {}).get("access_token")
    if not token:
        raise RuntimeError("No access token found in OAuth response.")

    users = fetch_users(client)
    csv_path = output_dir / "messages.csv"
    file_dir = output_dir / "files"

    with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "channel_name",
            "channel_id",
            "user_id",
            "user_name",
            "timestamp",
            "text",
            "file_names",
        ])

        for channel_id, channel_name in iter_conversations(client):
            print(f"Exporting {channel_name} ({channel_id})")
            cursor: Optional[str] = None
            while True:
                resp = client.conversations_history(channel=channel_id, cursor=cursor, limit=200)
                for message in resp.get("messages", []):
                    user_id = message.get("user") or message.get("bot_id") or "unknown"
                    text = message.get("text", "")
                    ts = convert_ts(message.get("ts", ""))
                    files = message.get("files", []) or []
                    downloaded: List[str] = []
                    for file_info in files:
                        path = download_file(token, file_info, file_dir)
                        if path:
                            downloaded.append(path.name)
                    writer.writerow([
                        channel_name,
                        channel_id,
                        user_id,
                        users.get(user_id, "unknown"),
                        ts,
                        text,
                        ";".join(downloaded),
                    ])
                cursor = resp.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break
    print(f"Messages saved to {csv_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Slack message history and files to CSV")
    parser.add_argument("--client-id", required=True, help="Slack app client ID")
    parser.add_argument("--client-secret", required=True, help="Slack app client secret")
    parser.add_argument("--scopes", nargs="*", default=DEFAULT_SCOPES, help="OAuth scopes to request")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Local port for OAuth redirect")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token_payload = ensure_token(args.client_id, args.client_secret, args.scopes, args.port)
    access_token = token_payload.get("access_token") or token_payload.get("authed_user", {}).get("access_token")
    if not access_token:
        raise RuntimeError("No usable access token found after OAuth flow.")

    client = WebClient(token=access_token)
    team_info = client.team_info()
    team_name = team_info.get("team", {}).get("name", "slack")
    output_dir = build_output_dir(team_name)
    export_history(client, token_payload, output_dir)


if __name__ == "__main__":
    main()
