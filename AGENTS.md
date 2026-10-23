# AGENTS.md

Instructions for coding agents working on this repository.

## Read first

- `README.md` — setup, configuration, Docker, security.
- `.env.example` — supported environment variables and defaults.
- `app/config.py` — parsing rules and tuning limits.
- `tests/` — executable specification; start here when changing behavior.

## What this service is

A private Telegram bot that downloads files over **MTProto** with Telethon and
streams them to Dropbox. It is not a Bot API webhook service. The Bot API
`getFile` endpoint is capped at 20 MB, so it must not be used for file
downloads.

## Invariants

Keep these true unless the user explicitly asks to change the product:

- **MTProto download path.** Use `TelegramClient.iter_download` (or equivalent
  MTProto streaming). Never use Bot API `getFile`.
- **Stream, do not spool.** File contents stay in memory and go straight to
  Dropbox. The only persistent files allowed are the Telethon session database
  (`SESSION_NAME`; `/data` in Docker), logs, and normal Python caches. Do not
  add temp files or directory spooling.
- **Bounded memory.** Memory is bounded by one upload chunk plus the SDK's
  immutable copy. `UPLOAD_CHUNK_SIZE_MB` is 4–148 MiB and must be a multiple of
  4; the default is 4, and the current deployment intentionally uses 148.
  `SMALL_FILE_THRESHOLD_MB` (default 8, max 150) controls when a single
  `files_upload` is used. Higher chunk sizes trade memory for fewer round trips.
- **Upload sessions for large files.** Above the threshold, use
  `files_upload_session_start`, `files_upload_session_append_v2`, and
  `files_upload_session_finish` with exact byte offsets. Keep chunks in the
  4–148 MiB range; Dropbox caps a request at 150 MiB and requires non-final
  chunks to be multiples of 4 MiB.
- **Thread offload.** Run every blocking Dropbox SDK call through
  `asyncio.to_thread` (the uploader centralizes this in `_call`).
- **Access control.** Only users in `ALLOWED_USER_IDS` may upload. Empty means
  deny all; `/start` and `/id` may still reply with the sender's ID.
- **Filename safety.** Pass every Telegram-provided name through
  `sanitize_filename`; never build a Dropbox path from raw user input.
- **Failure behavior.** Retry transient Dropbox errors with backoff; fail
  permanent errors immediately; always send a success or failure reply.
- **Progress.** Large uploads update the chat, throttled to avoid Telegram
  flood limits.
- **No overwrites.** Upload with `WriteMode.add` and `autorename=True` so
  duplicate names become `file (1).ext`.
- **Secrets.** `.env`, `*.session`, tokens, app secrets, and refresh tokens
  stay out of git. Update `.env.example` with placeholders only. Never print
  these values in logs, shell output, commits, or chat reports.
- **One process per bot token.** Stop the existing bot before starting another.
  Concurrent MTProto clients for the same bot can duplicate updates and trigger
  Telegram login rate limits.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# fill in real values
```

## Verify

Run both before claiming completion:

```bash
pytest -q
ruff check .
```

Tests must not contact Telegram or Dropbox; they use fakes. Do not add tests that
require real credentials to the default run. For behavior changes, cover the
relevant branch (chunk boundaries, exact-multiple final empty chunk, retry vs
permanent error, allowlist denial, sanitization, success/failure replies).

## Run

```bash
python -m app.telegram_bot
```

Docker:

```bash
docker build -t tg2dropbox .
docker run --env-file .env --restart unless-stopped \
  -v tg2dropbox-session:/data tg2dropbox
```

## Manual smoke test

1. Add your Telegram user ID to `ALLOWED_USER_IDS`.
2. Start the bot and send `/start`; it should reply with your ID.
3. Send a small file; verify the success reply and the Dropbox path under
   `DROPBOX_PATH`.
4. Send a file over 20 MB; the MTProto bot must handle it. If it does not,
   check the session/login path and logs before changing upload logic.

## Operational pitfalls

- Telegram rate-limits fresh `ImportBotAuthorization` calls. Repeated restarts
  without a saved session can produce `FloodWaitError` for tens of minutes.
  Keep the session DB; do not loop login retries.
- Do not delete a live `bot_session.session` or Docker `/data` volume unless
  the user explicitly wants to re-authenticate.
- Telegram caps a single file at 2 GB. Reject or explain larger files instead
  of trying to buffer them.
- Dropbox upload sessions are stateful: an offset bug corrupts the file. Test
  exact chunk multiples and empty final chunks.

## Before finishing

- `pytest -q` passes.
- `ruff check .` passes.
- `git status` shows no `.env`, `*.session`, or other secrets.
- README and `.env.example` are updated for any new variable or behavior.
- Report what changed, what was verified, and any live-service checks that
  could not be run.
