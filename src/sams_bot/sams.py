import argparse
import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime

import websockets

from sams_bot.match import Match

TICKER_ID = "bbvv"
REST_URL = f"https://backend.sams-ticker.de/live/indoor/tickers/{TICKER_ID}"
WS_URL = f"wss://backend.sams-ticker.de/indoor/{TICKER_ID}"


def fetch_snapshot() -> dict:
    with urllib.request.urlopen(REST_URL, timeout=15) as resp:
        return json.load(resp)


def all_matches(snapshot: dict) -> list[Match]:
    matches = [Match.from_dict(m) for day in snapshot["matchDays"] for m in day["matches"]]
    return sorted(matches, key=lambda m: m.date)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and print SAMS data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Fetch and print the current snapshot")

    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot = fetch_snapshot()
        print(json.dumps(snapshot, indent=2))

