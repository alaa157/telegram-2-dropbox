# Telegram 2 Dropbox

A Telegram bot (Telethon / MTProto) that streams files straight into Dropbox.
It does **not** use the Bot API `getFile` endpoint, so files up to **2 GB** are
supported. File contents are never written to disk; only the Telethon session
database is persisted. Large files are streamed with a bounded buffer; only
files up to the single-request threshold are held in memory.

If you are an AI coding agent, read [`AGENTS.md`](AGENTS.md) before changing
code. It records the architecture invariants, verification commands, and
operational pitfalls.

## How it works

- Logs in as a bot over MTProto with `TELEGRAM_BOT_TOKEN` + `TELEGRAM_API_ID` /
  `TELEGRAM_API_HASH`.
- Receives any photo/document/video/audio message.
- Checks the sender against `ALLOWED_USER_IDS`.
- Downloads the file in chunks and uploads them immediately:
  - files <= 8 MB: one `files_upload` call
  - larger files: `files_upload_session_start` → `append_v2` → `finish`
  - upload chunks are 4 MB by default (configurable 4–16 MB)
  - large-file memory use is bounded at roughly 8 MB (one chunk plus the
    immutable copy required by the Dropbox SDK)
- Dropbox SDK calls run in worker threads (`asyncio.to_thread`) so the
  Telethon event loop stays responsive.
- Transient Dropbox errors (rate limits, 5xx, network) are retried with
  exponential backoff and jitter.
- Dropbox filenames are sanitized and duplicates are auto-renamed by Dropbox
  (`file (1).jpg`, `file (2).jpg`, …).
- Large uploads show progress in the chat; every file gets a success or
  failure reply.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with real values
python -m app.telegram_bot
```

Required values (see `.env.example`):

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | MTProto app credentials from https://my.telegram.org/apps |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` | Dropbox app credentials |
| `DROPBOX_REFRESH_TOKEN` | Long-lived Dropbox refresh token |
| `DROPBOX_PATH` | Destination folder (default `/telegram_2_dropbox`) |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs allowed to upload |
| `SESSION_NAME` | Telethon session path (default `bot_session`, gitignored) |

Credentials are intentionally not committed. Create a Telegram app at
<https://my.telegram.org/apps> for `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`, a
bot with [@BotFather](https://t.me/BotFather) for `TELEGRAM_BOT_TOKEN`, and a
Dropbox app with `files.content.write` access. Use Dropbox OAuth2 with
`token_access_type=offline` and put the resulting refresh token in `.env`.

If `ALLOWED_USER_IDS` is empty, the bot denies every upload and replies with
the sender's user ID so you can add it. You can also send `/start` or `/id`
to the bot at any time to get your Telegram user ID.

## Docker

```bash
docker build -t tg2dropbox .
docker run --env-file .env --restart unless-stopped \
  -v tg2dropbox-session:/data tg2dropbox
```

The `/data` volume keeps the Telethon session so restarts do not trigger a new
bot login (Telegram rate-limits repeated logins).

## Development

Install the development tools and run the checks:

```bash
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

The tests use fakes and never contact Telegram or Dropbox. Do not add tests that
require real credentials to the default `pytest` run.

Manual smoke test:

1. Add your Telegram user ID to `ALLOWED_USER_IDS`.
2. Start the bot and send `/start`; it should reply with your ID.
3. Send a small file; verify the success reply and the Dropbox path.
4. Send a file larger than 20 MB; the MTProto bot must handle it.

## Security

- `.env`, `.env.*` and `*.session` are gitignored; `.env.example` contains only
  placeholders.
- The repository history was reset to remove previously committed secrets.
  Treat any secret that was ever committed as compromised and rotate it; old
  unreachable objects may still be reachable by commit SHA on GitHub.
