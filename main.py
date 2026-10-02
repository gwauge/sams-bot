"""Print live score updates for matches from the BBVV SAMS live ticker.

Follow one match by ID (optionally with --twitch-channel), or several via
--matches, a CSV file of "match_id,twitch_channel" rows (channel may be empty).
Updates are posted to Twitch only once a match has started; a match is dropped
once it finishes, and the program exits when all followed matches are done.
Twitch posts use the account given by the TWITCH_USERNAME and TWITCH_TOKEN
environment variables (read from a .env file next to main.py if present).
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from dataclasses import dataclass, field
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


@dataclass
class Followed:
    match: Match
    chats: list[TwitchChat] = field(default_factory=list)
    last_key: tuple | None = None


async def post(chat: TwitchChat, message: str) -> None:
    try:
        await chat.send(message)
    except (TwitchError, OSError, websockets.WebSocketException) as e:
        # keep following the match even if Twitch is unreachable
        print(f"twitch #{chat.channel}: failed to post update: {e}", file=sys.stderr, flush=True)


async def report(f: Followed, state: dict) -> None:
    print(format_score(f.match, state), flush=True)
    if not state.get("started"):  # never post while the match is still upcoming
        return
    await asyncio.gather(*(post(chat, score_line(f.match, state)) for chat in f.chats))


async def finish(f: Followed, active: dict[str, Followed]) -> None:
    del active[f.match.id]
    still_needed = {id(c) for other in active.values() for c in other.chats}
    for chat in f.chats:
        if id(chat) not in still_needed:
            await chat.close()


async def follow(snapshot: dict, followed: list[Followed]) -> None:
    active = {f.match.id: f for f in followed}

    for f in followed:
        initial = snapshot["matchStates"].get(f.match.id)
        print(f"Following: {match_label(f.match, initial)}", flush=True)
        if initial is None:
            continue
        if initial.get("finished"):
            print("  already finished, skipping", flush=True)
            await finish(f, active)
            continue
        f.last_key = score_key(initial)
        if initial.get("started"):
            await report(f, initial)
    print(flush=True)

    if not active:
        return

    # websockets.connect as async iterator reconnects automatically on drop
    async for ws in websockets.connect(WS_URL):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "MATCH_UPDATE":
                    continue
                state = msg["payload"]
                f = active.get(state.get("matchUuid"))
                if f is None:
                    continue
                key = score_key(state)
                if key == f.last_key:  # server sends each update twice
                    continue
                f.last_key = key
                await report(f, state)

                if state.get("finished"):
                    print(f"Match finished: {match_label(f.match, state)}", flush=True)
                    await finish(f, active)
                    if not active:
                        print("\nAll matches finished. Exiting.")
                        return
        except websockets.ConnectionClosed:
            print("connection lost, reconnecting...", flush=True)
            continue


def load_matches_csv(path: str) -> dict[str, list[str]]:
    """Read "match_id,twitch_channel" rows; a header row and blank lines are skipped."""
    targets: dict[str, list[str]] = {}
    with open(path, newline="") as fh:
        for lineno, row in enumerate(csv.reader(fh), start=1):
            row = [cell.strip() for cell in row]
            if not any(row) or row[0].startswith("#"):
                continue
            if lineno == 1 and row[0].lower() == "match_id":
                continue
            if len(row) > 2:
                sys.exit(f"{path}:{lineno}: expected \"match_id,twitch_channel\", got {len(row)} columns")
            match_id = row[0]
            channel = row[1].lower().removeprefix("#") if len(row) > 1 else ""
            channels = targets.setdefault(match_id, [])
            if channel and channel not in channels:
                channels.append(channel)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("match_id", nargs="?", help="ID of a single match to follow")
    source.add_argument("--matches", metavar="CSV", help='CSV file of "match_id,twitch_channel" rows')
    parser.add_argument(
        "--twitch-channel",
        metavar="CHANNEL",
        help="with a single match_id: also post its score updates to this Twitch channel's chat",
    )
    args = parser.parse_args()

    if args.matches:
        if args.twitch_channel:
            parser.error("--twitch-channel can't be combined with --matches; put channels in the CSV")
        targets = load_matches_csv(args.matches)
        if not targets:
            sys.exit(f"{args.matches} lists no matches.")
    else:
        targets = {args.match_id: [args.twitch_channel] if args.twitch_channel else []}

    snapshot = sams.fetch_snapshot()
    matches_by_id = {m.id: m for m in sams.all_matches(snapshot)}
    missing = [match_id for match_id in targets if match_id not in matches_by_id]
    if missing:
        sys.exit(f"Match(es) not found: {', '.join(missing)}")

    channels = {c for cs in targets.values() for c in cs}
    chats: dict[str, TwitchChat] = {}
    if channels:
        load_dotenv()  # real environment variables take precedence over .env
        username, token = os.environ.get("TWITCH_USERNAME"), os.environ.get("TWITCH_TOKEN")
        if not (username and token):
            sys.exit("Posting to Twitch needs TWITCH_USERNAME and TWITCH_TOKEN (set them in .env or the environment).")
        chats = {c: TwitchChat(username, token, c) for c in sorted(channels)}

    followed = [
        Followed(matches_by_id[match_id], [chats[c] for c in cs])
        for match_id, cs in sorted(targets.items(), key=lambda t: matches_by_id[t[0]].date)
    ]
    asyncio.run(run(snapshot, followed, list(chats.values())))


async def run(snapshot: dict, followed: list[Followed], chats: list[TwitchChat]) -> None:
    for chat in chats:
        try:
            await chat.connect()
        except (TwitchError, OSError, websockets.WebSocketException) as e:
            await asyncio.gather(*(c.close() for c in chats))
            sys.exit(f"Could not join Twitch channel #{chat.channel}: {e}")
        print(f"Posting to twitch.tv/{chat.channel}", flush=True)
    try:
        await follow(snapshot, followed)
    finally:
        await asyncio.gather(*(c.close() for c in chats))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        pass
