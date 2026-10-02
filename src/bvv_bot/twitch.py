"""Post messages into a Twitch channel's chat via Twitch's IRC-over-WebSocket interface."""

import asyncio
import sys

import websockets

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
MAX_MESSAGE_LENGTH = 500  # Twitch drops longer PRIVMSGs
HANDSHAKE_TIMEOUT = 10


class TwitchError(Exception):
    pass


class TwitchChat:
    def __init__(self, username: str, token: str, channel: str, url: str = IRC_URL) -> None:
        self.username = username.lower()
        self.token = token.removeprefix("oauth:")
        self.channel = channel.lower().removeprefix("#")
        self.url = url
        self._ws = None
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.url)
        try:
            await self._ws.send(f"PASS oauth:{self.token}")
            await self._ws.send(f"NICK {self.username}")
            await self._wait_for(" 001 ")  # welcome = login accepted
            await self._ws.send(f"JOIN #{self.channel}")
            await self._wait_for(f" JOIN #{self.channel}")
        except BaseException:
            await self.close()
            raise
        self._reader = asyncio.create_task(self._read_loop(self._ws))

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
                    raise TwitchError("Twitch login failed, check TWITCH_USERNAME and TWITCH_TOKEN")
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
