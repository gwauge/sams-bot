# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A volleyball live-score bot for the BBVV. It reads data from the public SAMS live ticker (`backend.sams-ticker.de`, ticker id `bbvv`) and prints score updates for a match. Early stage: no tests, linter, or formatter are configured yet.

## Commands

Uses `uv` with Python 3.13 (`.python-version`). The package lives under `src/bvv_bot` (uv_build backend), so run things through `uv run` to get it on the path.

```sh
uv sync                                   # install deps
uv run main.py <match_id>                 # follow one match live until it finishes
uv run main.py <match_id> --twitch-channel <channel>  # also post each update to Twitch chat
uv run main.py --matches matches.csv       # several matches; rows are "match_id,twitch_channel" (channel optional, header optional)
uv run -m bvv_bot.sams snapshot           # dump the full REST snapshot as JSON
uv run -m bvv_bot.team name <regex>       # find League teams by name -> team ids
uv run -m bvv_bot.team games <team_id>    # list a team's matches -> match ids
```

Typical flow: `team name` → `team games` → `main.py <match_id>`.

The Twitch option needs `TWITCH_USERNAME` (the bot account's login) and `TWITCH_TOKEN` (a user OAuth token with `chat:read` + `chat:edit`; the `oauth:` prefix is optional), either in the environment or in a gitignored `.env` next to `main.py` (loaded via python-dotenv; real env vars win).

### Docker (server deployment)

```sh
cp matches.example.csv matches.csv        # edit first; compose refuses to start if it's missing
docker compose up -d --build
docker compose logs -f
docker compose restart                    # after editing matches.csv
```

The container runs `main.py --matches /config/matches.csv` with `./matches.csv` bind-mounted read-only (`create_host_path: false`, so a missing file is an error rather than an empty directory). Twitch credentials come from `.env` via `env_file`; `.dockerignore` keeps `.env` and `matches.csv` out of the image. `restart: on-failure` is deliberate: `main.py` exits 0 once all matches are finished, so `always`/`unless-stopped` would restart-loop; config errors (e.g. unknown match id) exit 1 and do loop, visibly in the logs. `TZ=Europe/Berlin` is set because match times are formatted in local time.

`htmx-example.py` is a standalone Flask + HTMX todo demo, unrelated to SAMS; it appears to be a reference for a future web UI (`uv run htmx-example.py`).

## Architecture

Two data sources from SAMS:
- **REST snapshot** (`sams.fetch_snapshot`, `REST_URL`): one JSON blob with `matchSeries` (dict of series; each has `class`, `gender`, `teams`), `matchDays[].matches[]` (fixtures with `id`, `team1`/`team2` ids, `teamDescription1`/`2` names, `date` in epoch **ms**, `matchSeries`), and `matchStates` (keyed by match id: `started`, `finished`, `setPoints`, `matchSets[].setScore`, `serving`).
- **WebSocket** (`WS_URL`): broadcasts `MATCH_UPDATE` messages for *all* matches on the ticker; payload has the same shape as a `matchStates` entry plus `matchUuid`. The server sends each update twice, so `main.py` dedupes via `score_key`. `websockets.connect` is used as an async iterator so it reconnects on drop.

Only series with `class == "League"` are treated as real teams (youth tournaments reuse club names). `FAVORITES` in `main.py` encodes this for named shortcuts.

Modules in `src/bvv_bot`: `sams` (fetch + `all_matches`, sorted by date), `match` (`Match` dataclass), `team` (`Team` dataclass, gender parsing, lookup by id), `format` (display helpers), `twitch` (`TwitchChat`: IRC-over-WebSocket client that logs in, joins one channel, sends `PRIVMSG`s, answers `PING`s in a background reader task, prints Twitch `NOTICE`s to stderr, and reconnects on the next `send` after a drop). `main.py` turns either input into a list of `Followed` (a match + its `TwitchChat`s; one shared `TwitchChat` per channel) and listens on one SAMS WebSocket for all of them. Rules: scores always print to the console, but `report()` posts to Twitch only once `state["started"]` is true; a match is removed from `active` when it finishes (its final score is still posted), its chats are closed once no remaining match uses them, and the program exits when `active` is empty. Matches already finished in the startup snapshot are skipped. Twitch errors while following are logged and don't stop the ticker; a failed login at startup exits. Each of `sams`/`team` doubles as a small CLI via `__main__`.

## Known inconsistencies (mid-refactor)

The code is partway through moving from raw dicts to dataclasses:
- `format_score` is duplicated in `main.py` and `format.py`; `TICKER_ID`/`REST_URL`/`WS_URL` are duplicated in `main.py` and `sams.py`.
- Gender is `"FEMALE"`/`"MALE"` in raw SAMS data but normalized to `"female"`/`"male"` in `Team`.
