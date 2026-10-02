"""Print live score updates for one match from the BBVV SAMS live ticker.

With --twitch-channel, each update is also posted to that channel's chat, as the
account given by the TWITCH_USERNAME and TWITCH_TOKEN environment variables
(read from a .env file next to main.py if present).
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

import websockets
from dotenv import load_dotenv

from bvv_bot import format, sams
from bvv_bot.match import Match
from bvv_bot.twitch import TwitchChat, TwitchError


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


def score_line(match: Match, state: dict) -> str:
    team1, team2 = match.teamDescription1, match.teamDescription2
    sets = state["setPoints"]
    set_scores = " ".join(
        f"{s['setScore']['team1']}:{s['setScore']['team2']}" for s in state["matchSets"]
    )
    status = " (finished)" if state.get("finished") else ""
    serving = team1 if state.get("serving") == "team1" else team2
    return (
        f"{team1} {sets['team1']}:{sets['team2']} {team2}"
        f" | sets: {set_scores} | serving: {serving}{status}"
    )


def format_score(match: Match, state: dict) -> str:
    return f"[{datetime.now():%H:%M:%S}] {score_line(match, state)}"


def score_key(state: dict) -> tuple:
    return (
        state["setPoints"]["team1"],
        state["setPoints"]["team2"],
        tuple((s["setScore"]["team1"], s["setScore"]["team2"]) for s in state["matchSets"]),
        state.get("finished"),
    )


async def follow(snapshot: dict, match: Match, chat: TwitchChat | None = None) -> None:
    matches_by_id = {m.id: m for m in sams.all_matches(snapshot)}
    last_keys: dict[str, tuple] = {}

    async def report(state: dict) -> None:
        print(format_score(match, state), flush=True)
        if chat is None:
            return
        try:
            await chat.send(score_line(match, state))
        except (TwitchError, OSError, websockets.WebSocketException) as e:
            # keep following the match even if Twitch is unreachable
            print(f"twitch: failed to post update: {e}", file=sys.stderr, flush=True)

    print(f"\nFollowing: {match_label(match, snapshot['matchStates'].get(match.id))}\n")
    initial = snapshot["matchStates"].get(match.id)
    if initial:
        last_keys[match.id] = score_key(initial)
        await report(initial)
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
                await report(state)

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
    parser.add_argument(
        "--twitch-channel",
        metavar="CHANNEL",
        help="also post each score update to this Twitch channel's chat",
    )
    args = parser.parse_args()
    match_id = args.match_id

    chat = None
    if args.twitch_channel:
        load_dotenv()  # real environment variables take precedence over .env
        username, token = os.environ.get("TWITCH_USERNAME"), os.environ.get("TWITCH_TOKEN")
        if not (username and token):
            sys.exit("--twitch-channel needs TWITCH_USERNAME and TWITCH_TOKEN (set them in .env or the environment).")
        chat = TwitchChat(username, token, args.twitch_channel)

    snapshot = sams.fetch_snapshot()
    matches_by_id = {m.id: m for m in sams.all_matches(snapshot)}
    found_match = matches_by_id.get(match_id)

    if found_match is None:
        sys.exit(f"Match with ID {match_id} not found.")

    asyncio.run(run(snapshot, found_match, chat))


async def run(snapshot: dict, match: Match, chat: TwitchChat | None) -> None:
    if chat is None:
        await follow(snapshot, match)
        return
    try:
        await chat.connect()
    except (TwitchError, OSError, websockets.WebSocketException) as e:
        sys.exit(f"Could not join Twitch channel #{chat.channel}: {e}")
    print(f"Posting updates to twitch.tv/{chat.channel}", flush=True)
    try:
        await follow(snapshot, match, chat)
    finally:
        await chat.close()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass
