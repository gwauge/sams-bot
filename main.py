"""Print live score updates for one match from the BBVV SAMS live ticker."""

import argparse
import asyncio
import json
import sys
from datetime import datetime

import websockets

from bvv_bot import format, sams
from bvv_bot.match import Match


TICKER_ID = "bbvv"
REST_URL = f"https://backend.sams-ticker.de/live/indoor/tickers/{TICKER_ID}"
WS_URL = f"wss://backend.sams-ticker.de/indoor/{TICKER_ID}"

# alias -> (gender, exact team name); only matched against "League" series,
# so youth tournaments with the same club name are ignored
FAVORITES = {
    "chicas": ("FEMALE", "Potsdamer VC 91"),
    "cucks": ("MALE", "Potsdamer VC 91 II"),
}

GENDER_LABELS = {"FEMALE": "Frauen", "MALE": "Männer"}


def is_live(state: dict | None) -> bool:
    return bool(state and state.get("started") and not state.get("finished"))


def format_sets(state: dict) -> str:
    sets = state["setPoints"]
    set_scores = " ".join(
        f"{s['setScore']['team1']}:{s['setScore']['team2']}" for s in state["matchSets"]
    )
    return f"{sets['team1']}:{sets['team2']} ({set_scores})"


def match_label(match: Match, state: dict | None) -> str:
    teams = f"{match.teamDescription1} vs. {match.teamDescription2}"
    if is_live(state):
        status = f"LIVE {format_sets(state)}"
    elif state and state.get("finished"):
        status = f"final {format_sets(state)}"
    else:
        status = "upcoming"
    return f"{format.format_start(match)}  {teams}  [{status}]"


def format_score(match: Match, state: dict) -> str:
    team1, team2 = match.teamDescription1, match.teamDescription2
    sets = state["setPoints"]
    set_scores = " ".join(
        f"{s['setScore']['team1']}:{s['setScore']['team2']}" for s in state["matchSets"]
    )
    status = " (finished)" if state.get("finished") else ""
    serving = team1 if state.get("serving") == "team1" else team2
    return (
        f"[{datetime.now():%H:%M:%S}] {team1} {sets['team1']}:{sets['team2']} {team2}"
        f" | sets: {set_scores} | serving: {serving}{status}"
    )


def score_key(state: dict) -> tuple:
    return (
        state["setPoints"]["team1"],
        state["setPoints"]["team2"],
        tuple((s["setScore"]["team1"], s["setScore"]["team2"]) for s in state["matchSets"]),
        state.get("finished"),
    )


async def follow(snapshot: dict, match: Match) -> None:
    matches_by_id = {m.id: m for m in sams.all_matches(snapshot)}
    last_keys: dict[str, tuple] = {}

    print(f"\nFollowing: {match_label(match, snapshot['matchStates'].get(match.id))}\n")
    initial = snapshot["matchStates"].get(match.id)
    if initial:
        last_keys[match.id] = score_key(initial)
        print(format_score(match, initial), flush=True)
        if initial.get("finished"):
            print("\nMatch already finished. Exiting.")
            return

    # websockets.connect as async iterator reconnects automatically on drop
    async for ws in websockets.connect(WS_URL):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "MATCH_UPDATE":
                    continue
                state = msg["payload"]
                match_id = state.get("matchUuid")
                update_match = matches_by_id.get(match_id)
                if update_match is None:
                    continue

                if match_id != match.id:
                    continue
                key = score_key(state)
                if key == last_keys.get(match_id):  # server sends each update twice
                    continue
                last_keys[match_id] = key
                print(format_score(match, state), flush=True)

                if state.get("finished"):
                    print("\nMatch finished. Exiting.")
                    return
        except websockets.ConnectionClosed:
            print("connection lost, reconnecting...", flush=True)
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "match_id",
        help="ID of the match to follow",
        type=str,
    )
    args = parser.parse_args()
    match_id = args.match_id

    snapshot = sams.fetch_snapshot()
    matches_by_id = {m.id: m for m in sams.all_matches(snapshot)}
    found_match = matches_by_id.get(match_id)

    if found_match is None:
        sys.exit(f"Match with ID {match_id} not found.")

    asyncio.run(follow(snapshot, found_match))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass
