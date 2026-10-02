# sams-bot

Follows volleyball matches on the [SAMS live ticker](https://backend.sams-ticker.de) (BBVV ticker) and posts every score change to Twitch chat.

- Nothing is posted while a match is still upcoming; posting starts once the match has started.
- The final score is posted when a match finishes, then that match is dropped. The bot exits once all followed matches are finished.
- Matches that are already finished at startup are skipped.

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.13 is installed automatically), or Docker for server deployment
- For Twitch: a Twitch account for the bot and a Twitch app (see [Twitch setup](#twitch-setup))

```sh
uv sync
```

## Finding match IDs

```sh
uv run -m sams_bot.team name "Potsdamer VC"   # team name (regex) -> team IDs
uv run -m sams_bot.team games <team_id>       # team ID -> its matches and match IDs
```

## Usage

Follow a single match:

```sh
uv run main.py <match_id>                                  # print to the terminal only
uv run main.py <match_id> --twitch-channel <channel>      # also post to Twitch
```

Follow several matches via a CSV file:

```sh
cp matches.example.csv matches.csv
uv run main.py --matches matches.csv
```

```csv
match_id,twitch_channel
8954150a-d246-4c29-8937-70879b4569c3,my_channel
7ded583d-04e0-418a-9876-cd5153c2031e,my_channel
7ded583d-04e0-418a-9876-cd5153c2031e,other_channel
6f0e1a2b-0000-0000-0000-000000000000,
```

- One row per match and channel. A match can post to several channels, and several matches can share a channel.
- Leave the channel empty to only print the match in the terminal.
- The header row is optional; blank lines and lines starting with `#` are ignored.
- All match IDs are checked at startup. The bot stops with an error if one is unknown.

## Twitch setup

The bot logs into Twitch chat as a user account and needs a **user access token** for it with the scopes `chat:read` and `chat:edit`. Your app's *Client Secret* is **not** that token, even though it looks like one.

With a refresh token, the bot fetches new access tokens itself, so it keeps working after the access token expires (about 4 hours). This is the recommended setup.

### 1. Create a Twitch app

1. Open the [Twitch developer console](https://dev.twitch.tv/console) and register an application.
2. Add `http://localhost:3000` as the **OAuth Redirect URL**, then click **Save**.
3. Set the client type to **Confidential** and create a **Client Secret**.
4. Note the **Client ID** and **Client Secret**.

### 2. Authorise the app as the bot account

Log into twitch.tv as the bot account, then open this URL with your Client ID filled in:

```
https://id.twitch.tv/oauth2/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:3000&scope=chat:read+chat:edit
```

Click **Authorize**. Your browser is sent to `http://localhost:3000/?code=…&scope=…` and shows an error page; that's expected. Copy the `code` value from the address bar. It's single-use and expires within minutes, so do the next step right away.

### 3. Exchange the code for tokens

```sh
curl -X POST https://id.twitch.tv/oauth2/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=THE_CODE \
  -d grant_type=authorization_code \
  -d redirect_uri=http://localhost:3000
```

The response contains `access_token`, `refresh_token` and `expires_in`.

As a shortcut, the [Twitch CLI](https://dev.twitch.tv/docs/cli/) does steps 2 and 3 for you: `twitch configure`, then `twitch token -u -s "chat:read chat:edit"`.

### 4. Configure `.env`

Create a `.env` file next to `main.py`. It's gitignored and never copied into the Docker image.

```sh
TWITCH_USERNAME=your_bot_login
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
TWITCH_REFRESH_TOKEN=...
```

Alternatively, set only `TWITCH_USERNAME` and `TWITCH_TOKEN=<access_token>`. That works until the access token expires and is then not renewed.

Variables set in the shell take precedence over `.env`.

## Docker deployment

Write `matches.csv` and `.env` first, then start the stack:

```sh
cp matches.example.csv matches.csv   # edit it
docker compose up -d --build
docker compose logs -f
```

| Change | Apply with |
| --- | --- |
| Edited `matches.csv` | `docker compose restart` |
| Edited `.env` | `docker compose up -d` (`restart` doesn't reload `.env`) |
| Updated code | `docker compose up -d --build` |

- `matches.csv` is mounted read-only. If it doesn't exist, `docker compose up` fails instead of creating an empty directory.
- The container restarts only after a crash. When all matches are finished, the bot exits normally and the container stays stopped; start it again for the next matches.
- An error in the CSV (for example an unknown match ID) also counts as a crash, so the container keeps restarting; `docker compose logs` shows the reason.
- Times are shown in `Europe/Berlin`.

## Troubleshooting

**`Twitch rejected the login; check TWITCH_USERNAME and the access token`**
The access token is invalid, expired or revoked, or it's actually the Client Secret or Client ID. Set up the refresh token as described above, or generate a new access token.

**`refreshing the Twitch token failed (400 …)`**
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` or `TWITCH_REFRESH_TOKEN` is wrong. The refresh token also becomes invalid if you change the bot account's password or disconnect the app under Twitch Settings → Connections; repeat steps 2–3.

**`redirect_mismatch` when authorising**
The `redirect_uri` in the URL must exactly match one registered on the app. Check for a trailing slash, `http` vs. `https`, `localhost` vs. `127.0.0.1`, and that you clicked **Save** in the console and used the Client ID of that same app. Changes can take a few minutes to take effect.

**Warning: `Twitch issued a new refresh token`**
The new refresh token is only kept in memory. If the bot fails to log in after its next restart, repeat steps 2–3 to get a new refresh token.

**Messages don't appear in chat**
Twitch reports dropped messages as `twitch: …` lines in the log, for example for rate limits or follower-only mode. Without mod or VIP status in a channel, an account may post about 20 messages per 30 seconds.
