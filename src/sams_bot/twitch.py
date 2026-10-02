"""Post messages into a Twitch channel's chat via Twitch's IRC-over-WebSocket interface."""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
MAX_MESSAGE_LENGTH = 500  # Twitch drops longer PRIVMSGs
HANDSHAKE_TIMEOUT = 10
REFRESH_MARGIN = 300  # refresh this many seconds before the access token expires


class TwitchError(Exception):
    pass


class TwitchAuthError(TwitchError):
    pass


class TwitchAuth:
    """Supplies the user access token for chat logins.

    With a client id, client secret and refresh token, it fetches new access
    tokens itself (at first use, shortly before expiry, and after a rejected
    login). Without them, the static token is used as-is.
    """

    def __init__(
        self,
        token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        token_url: str = TOKEN_URL,
    ) -> None:
        self.token = token.removeprefix("oauth:") if token else None
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_url = token_url
        self._expires_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def can_refresh(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    async def get_token(self) -> str:
        if self.can_refresh and (self.token is None or self._expiring()):
            await self.refresh(stale=self.token)
        if self.token is None:
            raise TwitchAuthError("no Twitch access token configured")
        return self.token

    async def refresh(self, stale: str | None) -> None:
        """Fetch a new access token, unless another caller already replaced `stale`."""
        if not self.can_refresh:
            raise TwitchAuthError(
                "Twitch rejected the access token, and it can't be refreshed without "
                "TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET and TWITCH_REFRESH_TOKEN"
            )
        async with self._lock:  # channels share one TwitchAuth; refresh only once
            if self.token != stale and not self._expiring():
                return
            data = await asyncio.to_thread(self._request_refresh)
            self.token = data["access_token"]
            self._expires_at = time.monotonic() + data["expires_in"] if data.get("expires_in") else None
            # Twitch may rotate the refresh token; keep using the newest one in memory
            new_refresh = data.get("refresh_token")
            if new_refresh and new_refresh != self.refresh_token:
                self.refresh_token = new_refresh
                print(
                    "twitch: Twitch issued a new refresh token (kept in memory). If a later restart "
                    "fails to log in, the one in TWITCH_REFRESH_TOKEN went stale; generate a new one.",
                    file=sys.stderr,
                    flush=True,
                )
            print("twitch: refreshed access token", flush=True)

    def _expiring(self) -> bool:
        return self._expires_at is not None and time.monotonic() > self._expires_at - REFRESH_MARGIN

    def _request_refresh(self) -> dict:
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        try:
            with urllib.request.urlopen(self.token_url, body, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                reason = json.load(e).get("message", "")
            except ValueError:
                reason = ""
            if 400 <= e.code < 500:
                raise TwitchAuthError(
                    f"refreshing the Twitch token failed ({e.code} {reason}); "
                    "check TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET and TWITCH_REFRESH_TOKEN"
                ) from None
            raise TwitchError(f"refreshing the Twitch token failed ({e.code} {reason})") from None
        except urllib.error.URLError as e:
            raise TwitchError(f"could not reach Twitch to refresh the token: {e.reason}") from None


class TwitchChat:
    def __init__(self, username: str, auth: TwitchAuth, channel: str, url: str = IRC_URL) -> None:
        self.username = username.lower()
        self.auth = auth
        self.channel = channel.lower().removeprefix("#")
        self.url = url
        self._ws = None
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        token = await self.auth.get_token()
        try:
            await self._login(token)
        except TwitchAuthError:
            if not self.auth.can_refresh:
                raise
            # the token was revoked or expired early; get a new one and retry once
            await self.auth.refresh(stale=token)
            await self._login(await self.auth.get_token())
        self._reader = asyncio.create_task(self._read_loop(self._ws))

    async def _login(self, token: str) -> None:
        self._ws = await websockets.connect(self.url)
        try:
            await self._ws.send(f"PASS oauth:{token}")
            await self._ws.send(f"NICK {self.username}")
            await self._wait_for(" 001 ")  # welcome = login accepted
            await self._ws.send(f"JOIN #{self.channel}")
            await self._wait_for(f" JOIN #{self.channel}")
        except BaseException:
            await self.close()
            raise

    async def send(self, message: str) -> None:
        """Post a message, reconnecting once if the connection dropped."""
        line = f"PRIVMSG #{self.channel} :{message[:MAX_MESSAGE_LENGTH]}"
        if self._ws is None:
            await self.connect()
        try:
            await self._ws.send(line)
        except websockets.ConnectionClosed:
            await self.close()
            await self.connect()
            await self._ws.send(line)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> "TwitchChat":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _lines(self, ws):
        """Yield IRC lines, answering keep-alive PINGs along the way."""
        async for frame in ws:
            lines = [line for line in frame.split("\r\n") if line]
            # answer PINGs before yielding, so a consumer that stops early can't skip one
            for line in lines:
                if line.startswith("PING"):
                    await ws.send("PONG" + line[4:])
            for line in lines:
                if not line.startswith("PING"):
                    yield line

    async def _wait_for(self, marker: str) -> None:
        async def wait() -> None:
            async for line in self._lines(self._ws):
                if "NOTICE" in line and ("authentication failed" in line or "formatted auth" in line):
                    raise TwitchAuthError("Twitch rejected the login; check TWITCH_USERNAME and the access token")
                if marker in line:
                    return
            raise TwitchError("Twitch closed the connection during login")

        try:
            await asyncio.wait_for(wait(), HANDSHAKE_TIMEOUT)
        except TimeoutError:
            raise TwitchError(f"no response from Twitch while waiting for {marker.strip()!r}") from None

    async def _read_loop(self, ws) -> None:
        # Twitch never echoes our own messages; NOTICEs are how it reports
        # dropped ones (rate limits, missing permissions, bans)
        try:
            async for line in self._lines(ws):
                if " NOTICE " in line:
                    print(f"twitch: {line.split(' :', 1)[-1]}", file=sys.stderr, flush=True)
        except websockets.ConnectionClosed:
            pass
        if self._ws is ws:
            self._ws = None  # next send() reconnects
