# journal-service

Backend and database for the journal app. It stores entries encrypted at rest in SQLite and
serves a small HTTP API on a Unix socket. journal-cli (or any other client) talks to the API
and never touches the database.

## How it works

### Encryption at rest

The journal has one [age](https://age-encryption.org) X25519 keypair:

- The **public key** ("recipient", `age1...`) is stored in plaintext. Every entry is encrypted
  to it, so **writing never needs the password**.
- The **private key** ("identity") is stored as an age file encrypted with your password
  (age's scrypt mode), so **reading needs the password**.
- **Changing the password** re-encrypts only the private key. The entries are left as they are.

| Readable without the password | Encrypted |
|---|---|
| id, date, time written and its UTC offset, created/updated time, which key it uses | body, tags, any field added later |

The encrypted payload is a small JSON document padded to 1 KiB blocks, so the stored size
gives away the length only roughly. It also carries the entry's id and date, so a ciphertext
copied onto another row will not decrypt. Everything stored is a standard age file, which
the `age` CLI can read without this code (see *Recovery* below).

**Sessions.** `POST /session` with the password unlocks the private key and returns a
token. The unlocked key stays in the service's memory only. It is dropped after 15 minutes
without requests, after 8 hours at most, on `DELETE /session`, or when the service
restarts. A client can unlock once at program start, or unlock, read and lock for every
single view.

### Schema

`entries`

| column | type | notes |
|---|---|---|
| `id` | UUID (v7, time-ordered) | primary key |
| `entry_date` | date | the journal day in the writer's local time; always set |
| `written_at` | UTC timestamp, nullable | NULL for date-only entries (e.g. imported files) |
| `utc_offset_minutes` | int, nullable | the writer's offset, so 23:45 stays 23:45 later |
| `key_id` | → `keys.id` | which keypair encrypted it (allows key rotation later) |
| `ciphertext` | blob | age file: `{"v", "id", "entry_date", "body", "tags"}` |
| `created_at` / `updated_at` | UTC timestamps | row bookkeeping |

`keys`: `id`, `recipient` (public key), `wrapped_identity` (password-encrypted private key),
`created_at`.

Tags and any future secret fields (title, mood, …) go inside the ciphertext. Adding them
needs no migration and leaks nothing.

### Directories (XDG)

| | path | contents |
|---|---|---|
| config | `~/.config/journal-service/` | `config.toml` (optional; every key documented in `settings.py`) |
| data | `~/.local/share/journal-service/` | `journal.db` (+ `-wal`/`-shm`) |
| state | `~/.local/state/journal-service/` | `logs/journal_service.log` (rotated) |
| cache | `~/.cache/journal-service/` | reserved; must never hold decrypted text |
| runtime | `$XDG_RUNTIME_DIR/journal-service/` | `api.sock` |

Each `XDG_*` variable is honoured when it is set to an absolute path. In the container the
same variables point at bind mounts of exactly these host directories, so the host and the
container see one layout. Directories are created `0700` and files `0600`.

### API

All requests go over the Unix socket. There is no TCP port, and the container has no network.

| method | path | needs | |
|---|---|---|---|
| GET | `/health` | – | status, and whether `init` has run |
| POST | `/session` | password | unlock → `{"token", "idle_timeout_seconds", ...}` |
| DELETE | `/session` | token | lock |
| POST | `/password` | password | change it: `{"current_password", "new_password"}` |
| GET | `/entries?from=YYYY-MM-DD&to=YYYY-MM-DD` | – | metadata list, oldest first |
| GET | `/entries?content=true` (+ `from`/`to`) | token | the same list, decrypted (for search) |
| POST | `/entries` | – | create: `{"body", "tags"?, "written_at"? \| "entry_date"?}` |
| GET | `/entries/{id}` | token | decrypted entry |
| PUT | `/entries/{id}` | token | replace `{"body", "tags"?}` |
| DELETE | `/entries/{id}` | token | delete |

- `written_at` is ISO 8601 and must include an offset, e.g. `2026-11-17T19:46:00+01:00`.
- `entry_date` is for date-only entries such as imports.
- With neither, the entry is stamped "now" in local time.
- The token goes in `Authorization: Bearer <token>`.
- Errors come back as `{"error": "..."}` with status 401, 404, 409, 413 or 422.

## Getting started (Arch, rootless Podman)

```bash
sudo pacman -S --needed podman
# Rootless Podman needs subordinate ids for your user (skipped if already present)
grep -q "^$USER:" /etc/subuid || sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"
podman system migrate

podman build -t journal-service .
install -Dm644 deploy/journal-service.container ~/.config/containers/systemd/journal-service.container
systemctl --user daemon-reload
systemctl --user start journal-service

podman exec -it journal-service journal-service init      # choose the password (min. 12 chars)
```

Try it with curl:

```bash
api() { curl -sS --unix-socket "$XDG_RUNTIME_DIR/journal-service/api.sock" "$@"; echo; }

api http://localhost/health
api -X POST http://localhost/entries -d '{"body": "Hello, journal", "tags": ["first"]}'
api http://localhost/entries                                  # no password needed

# Unlock. The password goes through stdin, never onto a command line.
TOKEN=$(python3 -c 'import getpass, json; print(json.dumps({"password": getpass.getpass()}))' \
  | api -X POST http://localhost/session --data @- \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')
printf 'Authorization: Bearer %s' "$TOKEN" | api -H @- http://localhost/entries/<id>
printf 'Authorization: Bearer %s' "$TOKEN" | api -H @- -X DELETE http://localhost/session
```

## Operations

- **Logs:** `podman logs -f journal-service`, or `~/.local/state/journal-service/logs/`.
  Entry bodies, tags, passwords and tokens are never logged.
- **Change password:** `p` in journal-cli, `POST /password`, or
  `podman exec -it journal-service journal-service passwd`. Open sessions keep working
  because the key itself does not change.
- **Update:** `podman build -t journal-service . && systemctl --user restart journal-service`.
  Migrations run automatically on start. A restart locks all sessions.
- **Backup:** `sqlite3 ~/.local/share/journal-service/journal.db ".backup '/path/journal-$(date +%F).db'"`
  is safe while the service runs. A backup holds only ciphertext and the password-protected
  key, so its safety off-site comes down to the strength of your password.
- **Host-side commands:** `uv run journal-service paths|init|passwd|migrate` work from a
  checkout on the host too: they resolve the same XDG directories the container mounts.

## Development

```bash
uv sync
uv run pytest            # ~30 s; scrypt makes every unlock take about a second
uv run ruff check && uv run ruff format --check
```

To run outside the container without touching your real journal, point every XDG
directory at a scratch location:

```bash
dev() { env XDG_CONFIG_HOME=/tmp/jdev/config XDG_DATA_HOME=/tmp/jdev/data XDG_STATE_HOME=/tmp/jdev/state \
            XDG_CACHE_HOME=/tmp/jdev/cache XDG_RUNTIME_DIR=/tmp/jdev/run "$@"; }
dev uv run journal-service init
dev uv run journal-service serve
```

New migration (review the generated file before committing):

```bash
dev uv run alembic upgrade head
dev uv run alembic revision --autogenerate --rev-id 0002 -m "describe the change"
```

## Recovery without journal-service

If this code is gone, `sqlite3` and `age` (both in the Arch repos) can still read everything:

```bash
umask 077; mkdir -p ~/journal-export && cd ~/journal-export
DB=~/.local/share/journal-service/journal.db
sqlite3 "$DB" "SELECT writefile('identity_' || id || '.age', wrapped_identity) FROM keys;"
sqlite3 "$DB" "SELECT writefile(entry_date || '_' || id || '.age', ciphertext) FROM entries;"

age -d -i identity_1.age 2026-11-17_<id>.age                       # asks for the password

# Many files: unwrap the key once into tmpfs, decrypt, delete the key again
age -d -o "$XDG_RUNTIME_DIR/identity.txt" identity_1.age
for f in 2*.age; do age -d -i "$XDG_RUNTIME_DIR/identity.txt" "$f" > "${f%.age}.json"; done
rm "$XDG_RUNTIME_DIR/identity.txt"
```

The `.json` files written by the loop are plaintext. Put them somewhere you trust.

## Moving to PostgreSQL later

Set `[database] url = "postgresql+psycopg://..."` in `config.toml` and add `psycopg` as a
dependency. Then run `journal-service migrate` against the new database and copy the rows
of `keys` and then `entries` across unchanged. The ciphertexts are plain bytes, and the key
row carries the password-protected private key, so the same password keeps working. Note
that the Quadlet unit sets `Network=none`, so a Postgres reached over TCP needs a network
there, while a Postgres socket can be bind-mounted instead.

For a future change of algorithm (for example post-quantum hybrid keys), add a new row to
`keys`. Re-encrypt the entries to it with one unlock and wrap the new key with the same
password. `key_id` records which key each entry uses during the transition.

## What this does not protect against

- **Visible metadata:** the dates and times of writing, the number of entries and their
  approximate sizes.
- **Socket access:** anything running as your user can reach the socket, so it can add
  entries and list dates. That is the flip side of writing without a password.
- **Forged entries:** with public-key encryption, whoever can write to the database can
  forge a well-formed entry. What it does stop is swapping ciphertexts between rows.
- **While unlocked:** the private key lives in the service's memory.
- **Lost password:** there is no recovery. Without the password the entries cannot be read.
