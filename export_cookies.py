#!/usr/bin/env python3
"""Export cookies from sessions.db to CookieEditor-compatible JSON files.

Database schema:
  table: session
    id       INTEGER  primary key
    name     TEXT     account name (e.g. henry, frank); one account has multiple rows
    session  TEXT     cookie content, format:
                        name: <cookie name>
                        value: <cookie value>
                        domain: <domain>
                        path: <path>
    flag     TEXT     'true' / 'false'

Export behavior:
  - Each account (name) gets one JSON file under ./out/<account>.json
  - JSON format is CookieEditor-importable array:
    [{"name": ..., "value": ..., "domain": ..., "path": ...}, ...]
  - Records with flag='false' are skipped by default (use --all to include them)

Usage:
    python3 export_cookies.py                 # export all accounts -> out/<name>.json
    python3 export_cookies.py henry           # export single account -> out/henry.json
    python3 export_cookies.py --all           # include flag=false records
    python3 export_cookies.py henry --all     # single account + include flag=false
    python3 export_cookies.py -o merged.json  # merge all accounts into one file
"""

import json
import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


# ──────────────────────────────────────────────
# Parse session text -> cookie dict
# ──────────────────────────────────────────────
def parse_session(session_text: str) -> dict | None:
    """
    Parse the session field and return the 4 fields required by CookieEditor.

    Expected format (all keys use half-width colon + space):
        name: VSPHERE-UI-JSESSIONID
        value: BFCF97C93CDF73F76E9F2E6C6F0C3B74
        domain: 192.168.1.6
        path: /ui
    """
    result = {}
    for line in session_text.strip().splitlines():
        line = line.strip()
        if not line or ": " not in line:
            continue
        key, _, val = line.partition(": ")
        key = key.strip().lower()
        val = val.strip()
        if key in ("name", "value", "domain", "path"):
            result[key] = val

    if "name" not in result or "value" not in result:
        return None

    return {
        "name":   result.get("name", ""),
        "value":  result.get("value", ""),
        "domain": result.get("domain", ""),
        "path":   result.get("path", "/"),
    }


# ──────────────────────────────────────────────
# Database helpers
# ──────────────────────────────────────────────
def get_accounts(conn: sqlite3.Connection, include_all: bool = False) -> list[str]:
    """Return sorted list of distinct account names."""
    if include_all:
        sql = "SELECT DISTINCT name FROM session ORDER BY name"
    else:
        sql = "SELECT DISTINCT name FROM session WHERE flag = 'true' ORDER BY name"
    return [r[0] for r in conn.execute(sql).fetchall()]


def get_account_cookies(
    conn: sqlite3.Connection,
    account: str,
    include_all: bool = False,
) -> list[dict]:
    """Return all valid cookies for an account in CookieEditor format."""
    if include_all:
        sql = "SELECT session FROM session WHERE name = ? ORDER BY id"
    else:
        sql = "SELECT session FROM session WHERE name = ? AND flag = 'true' ORDER BY id"

    cookies = []
    for (session_text,) in conn.execute(sql, (account,)).fetchall():
        if not session_text:
            continue
        cookie = parse_session(session_text)
        if cookie:
            cookies.append(cookie)
    return cookies


# ──────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────
def safe_filename(name: str) -> str:
    """Replace unsafe characters in account name for use as filename."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def write_json(path: str, data) -> None:
    """Write JSON file, creating parent directories as needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_args() -> tuple[str | None, str | None, bool]:
    """
    Parse command-line arguments.
    Returns (account, output_file, include_all).
      account     : specific account name, or None for all accounts
      output_file : path from -o flag, or None for per-account files
      include_all : True when --all is present
    """
    args = sys.argv[1:]
    include_all = "--all" in args
    if include_all:
        args = [a for a in args if a != "--all"]

    if len(args) >= 2 and args[0] == "-o":
        return None, args[1], include_all

    if len(args) >= 1 and not args[0].startswith("-"):
        return args[0], None, include_all

    return None, None, include_all


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found: {DB_PATH}")
        sys.exit(1)

    account, output_file, include_all = parse_args()
    conn = sqlite3.connect(DB_PATH)
    flag_note = "(including flag=false)" if include_all else "(flag=true only)"

    # Mode 1: -o <file>  merge all accounts into a single file
    if output_file is not None:
        all_cookies: list[dict] = []
        for acc in get_accounts(conn, include_all):
            all_cookies.extend(get_account_cookies(conn, acc, include_all))
        write_json(output_file, all_cookies)
        print(f"[OK] Exported {len(all_cookies)} cookies {flag_note} -> {output_file}")

    # Mode 2: <account>  export single account -> out/<account>.json
    elif account is not None:
        cookies = get_account_cookies(conn, account, include_all)
        if not cookies:
            print(f"[WARN] No valid cookies found for account '{account}' {flag_note}")
            sys.exit(1)
        out_path = os.path.join(OUT_DIR, f"{safe_filename(account)}.json")
        write_json(out_path, cookies)
        print(f"[OK] {account:20s}  {len(cookies):>3} cookies  ->  {out_path}")

    # Mode 3 (default): export all accounts, one file each under out/
    else:
        accounts = get_accounts(conn, include_all)
        if not accounts:
            print(f"[WARN] No accounts found in database {flag_note}")
            sys.exit(0)

        os.makedirs(OUT_DIR, exist_ok=True)
        total_cookies = 0
        exported = 0

        for acc in accounts:
            cookies = get_account_cookies(conn, acc, include_all)
            if not cookies:
                print(f"[SKIP] {acc:20s}  no valid cookies")
                continue
            out_path = os.path.join(OUT_DIR, f"{safe_filename(acc)}.json")
            write_json(out_path, cookies)
            print(f"[OK]   {acc:20s}  {len(cookies):>3} cookies  ->  {out_path}")
            total_cookies += len(cookies)
            exported += 1

        print(f"\nDone. {exported} accounts, {total_cookies} cookies {flag_note}")
        print(f"Output directory: {OUT_DIR}")

    conn.close()


if __name__ == "__main__":
    main()
