#!/usr/bin/env python3
"""Create/rename tabs in the Aether Google Sheet.

Uses gog's stored OAuth token to make direct Sheets API calls.
This handles tab management that gog CLI doesn't support.
"""

from __future__ import annotations

import json
import subprocess
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

SHEET_ID = "1DM5qOV3mfPcVbgx_Fj3gVEKS_PPYtAsinIu9Zg5Oxy4"


def get_access_token() -> str:
    """Get OAuth access token by exchanging gog's stored refresh token."""
    import os

    # Export refresh token from gog
    env = os.environ.copy()
    env["GOG_KEYRING_PASSWORD"] = "aether"
    subprocess.run(
        ["gog", "auth", "tokens", "export", "norgordjacob@gmail.com",
         "--out", "/tmp/gog_token.json", "--overwrite", "--no-input"],
        capture_output=True, text=True, env=env, check=True,
    )

    with open("/tmp/gog_token.json") as f:
        token_data = json.load(f)
    refresh_token = token_data["refresh_token"]

    # Load client credentials
    creds_path = os.path.expanduser(
        "~/Library/Application Support/gogcli/credentials.json"
    )
    with open(creds_path) as f:
        creds = json.load(f)
    client_id = creds.get("installed", creds).get("client_id", creds.get("client_id"))
    client_secret = creds.get("installed", creds).get("client_secret", creds.get("client_secret"))

    # Exchange refresh token for access token
    from urllib.parse import urlencode
    body = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urlopen(req) as resp:
        result = json.loads(resp.read())

    # Clean up
    os.remove("/tmp/gog_token.json")
    return result["access_token"]


def sheets_api(token: str, body: dict) -> dict:
    """Make a batchUpdate request to the Sheets API."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate"
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urlopen(req) as resp:
        return json.loads(resp.read())


def get_sheet_metadata(token: str) -> dict:
    """Get spreadsheet metadata."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}?fields=sheets.properties"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(req) as resp:
        return json.loads(resp.read())


def main() -> int:
    token = get_access_token()
    meta = get_sheet_metadata(token)

    existing = {}
    for sheet in meta.get("sheets", []):
        props = sheet["properties"]
        existing[props["title"]] = props["sheetId"]

    requests = []

    # Rename Sheet1 to Leads if it exists
    if "Sheet1" in existing and "Leads" not in existing:
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": existing["Sheet1"],
                    "title": "Leads",
                },
                "fields": "title",
            }
        })
        print("Renaming Sheet1 -> Leads")

    # Create Feed History tab if it doesn't exist
    if "Feed History" not in existing:
        requests.append({
            "addSheet": {
                "properties": {
                    "title": "Feed History",
                }
            }
        })
        print("Creating Feed History tab")

    if not requests:
        print("Tabs already set up correctly")
        return 0

    result = sheets_api(token, {"requests": requests})
    print(f"Done: {len(requests)} changes applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
