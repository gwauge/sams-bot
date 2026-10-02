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
uv run -m bvv_bot.sams snapshot           # dump the full REST snapshot as JSON
uv run -m bvv_bot.team name <regex>       # find League teams by name -> team ids
uv run -m bvv_bot.team games <team_id>    # list a team's matches -> match ids
```

Typical flow: `team name` → `team games` → `main.py <match_id>`.

The Twitch option needs `TWITCH_USERNAME` (the bot account's login) and `TWITCH_TOKEN` (a user OAuth token with `chat:read` + `chat:edit`; the `oauth:` prefix is optional), either in the environment or in a gitignored `.env` next to `main.py` (loaded via python-dotenv; real env vars win).

`htmx-example.py` is a standalone Flask + HTMX todo demo, unrelated to SAMS; it appears to be a reference for a future web UI (`uv run htmx-example.py`).

## Architecture

Two data sources from SAMS:
- **REST snapshot** (`sams.fetch_snapshot`, `REST_URL`): one JSON blob with `matchSeries` (dict of series; each has `class`, `gender`, `teams`), `matchDays[].matches[]` (fixtures with `id`, `team1`/`team2` ids, `teamDescription1`/`2` names, `date` in epoch **ms**, `matchSeries`), and `matchStates` (keyed by match id: `started`, `finished`, `setPoints`, `matchSets[].setScore`, `serving`).
- **WebSocket** (`WS_URL`): broadcasts `MATCH_UPDATE` messages for *all* matches on the ticker; payload has the same shape as a `matchStates` entry plus `matchUuid`. The server sends each update twice, so `main.py` dedupes via `score_key`. `websockets.connect` is used as an async iterator so it reconnects on drop.

Only series with `class == "League"` are treated as real teams (youth tournaments reuse club names). `FAVORITES` in `main.py` encodes this for named shortcuts.

Modules in `src/bvv_bot`: `sams` (fetch + `all_matches`, sorted by date), `match` (`Match` dataclass), `team` (`Team` dataclass, gender parsing, lookup by id), `format` (display helpers), `twitch` (`TwitchChat`: IRC-over-WebSocket client that logs in, joins one channel, sends `PRIVMSG`s, answers `PING`s in a background reader task, prints Twitch `NOTICE`s to stderr, and reconnects on the next `send` after a drop). In `main.py`, `report()` prints every score change and, if a `TwitchChat` is passed, posts `score_line()` (the console line without its timestamp); Twitch errors while following are logged and don't stop the ticker, but a failed login at startup exits. Each of `sams`/`team` doubles as a small CLI via `__main__`.

## Known inconsistencies (mid-refactor)

The code is partway through moving from raw dicts to dataclasses:
- `format_score` is duplicated in `main.py` and `format.py`; `TICKER_ID`/`REST_URL`/`WS_URL` are duplicated in `main.py` and `sams.py`.
- Gender is `"FEMALE"`/`"MALE"` in raw SAMS data but normalized to `"female"`/`"male"` in `Team`.
