# Lager API

Backend for **Lager**, my inventory system for electronic components. Take a photo of a part in the [mobile app](https://github.com/PolsaFH/lager-app), Claude identifies it, and the backend keeps track of what I have, where it is and how many are left. The same data is available to Home Assistant and to AI assistants through MCP.

Plain Python + WSGI, no framework, so it runs on cheap shared hosting (Passenger) as well as locally.

```
 Mobile app (Expo)  ──┐  write key
                      ├──►  Lager API  ──►  MySQL / MariaDB
 Home Assistant ──────┤  read key      │
 Claude / MCP clients ┘                └──►  Claude API (photo identification)
```

## Features

- **Inventory REST API**: components with category, quantity, location, specs, description and photo
- **AI identification**: a photo goes to Claude, which answers through a forced tool call with a JSON schema, so the response is always structured data
- **Small photos**: uploads larger than 1600 px (or 900 KB) are scaled down and stored as JPEG, whatever client sent them
- **Thumbnails**: `GET /components/{id}/image?size=thumb` serves a ~256 px JPEG (made on upload, or lazily for older photos and then cached), so lists scroll smoothly instead of decoding megabytes of photos
- **Activity log**: every change is written in the same database transaction as the change itself
- **MCP server**: read-only tools (`search_components`, `get_low_stock`, ...) for Claude and other MCP clients
- **Home Assistant endpoint**: a compact summary for REST sensors and dashboards

## Security

The repository is public and the API is on the internet, so security is designed in from the start:

| Threat | Protection |
|---|---|
| Anyone reading, changing or deleting data | Every endpoint except `/` requires an API key, compared in constant time (`hmac.compare_digest`). |
| A fresh deploy left open by mistake | **Fails closed**: with no key (or a key shorter than 32 characters) the API answers `503` to everything. |
| A leaked key from Home Assistant or an AI client | Separate **read-only key**. It can never write, delete or use AI credits. |
| Someone burning your Anthropic credits | AI calls need the write key and have a **daily limit** (`AI_DAILY_LIMIT`), counted atomically in the database so it holds across worker processes. Invalid images are rejected before any money is spent. |
| HTML uploaded as an "image" (stored XSS) | The client's MIME type is ignored. The real type is detected from the file's magic bytes and only JPEG/PNG/WebP/GIF are accepted. Responses carry `nosniff` and `Content-Security-Policy: default-src 'none'`. |
| Huge request bodies | Body and image size limits (`413`). |
| Bad input | Strict validation of types, ranges and lengths (`422`). All SQL is parameterised. |
| Information leaks in errors | Stack traces go to the server log only. Clients get a generic message. |
| Browser-based attacks | No CORS unless you allow specific origins in `LAGER_CORS_ORIGINS`. |
| Lost updates when tapping +/- quickly | Quantity changes can be sent as a **delta**, applied atomically with row locking. |

Secrets live in environment variables (or a `.env` file that is git-ignored). Nothing secret is in this repository.

## API

All requests except `GET /` need `Authorization: Bearer <key>` (or `X-API-Key: <key>`). The app is often mounted under `/api`, and both `/api/components` and `/components` work.

| Method | Endpoint | Key | Description |
|---|---|---|---|
| GET | `/` | none | Health check |
| GET | `/me` | read | Which scope the key has, categories, whether AI is enabled |
| GET | `/components` | read | All components (`?category=` to filter) |
| GET | `/components/{id}` | read | One component |
| GET | `/components/{id}/image` | read | Photo (with `ETag` and caching). `?size=thumb` for a small JPEG |
| POST | `/components` | write | Create. JSON body, optional base64 `image` |
| PUT | `/components/{id}` | write | Update all fields |
| PATCH | `/components/{id}/quantity` | write | `{"delta": -1}` or `{"quantity": 5}` |
| DELETE | `/components/{id}` | write | Delete |
| POST | `/analyze-base64` | write | Identify a component from a base64 photo |
| GET | `/log` | read | Activity log (`?limit=1-200`) |
| GET | `/ha/summary` | read | Summary for Home Assistant (`?low=5` sets the low stock limit) |
| POST | `/mcp` | read | MCP endpoint (JSON-RPC 2.0, Streamable HTTP) |

Errors are always JSON: `{"error": "message"}` with a fitting status code (`401`, `403`, `404`, `413`, `415`, `422`, `429`, ...).

## Setup

### 1. Install

```bash
git clone https://github.com/PolsaFH/lager-backend.git
cd lager-backend
pip install -r requirements.txt
```

Requires Python 3.7+ and MySQL 5.7+ / MariaDB 10.3+. Pillow is optional; without it the thumbnail endpoint falls back to the full photo. Tables are created and migrated automatically on first request.

### 2. Configure

```bash
python -m lager.keygen      # prints two strong random keys
cp .env.example .env        # then fill in the values
```

| Variable | Required | Description |
|---|---|---|
| `LAGER_API_KEY` | yes | Full access, used by the app. At least 32 characters. |
| `LAGER_READ_KEY` | no | Read-only access for Home Assistant and MCP. |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | yes | Database connection |
| `ANTHROPIC_API_KEY` | for AI | From [console.anthropic.com](https://console.anthropic.com) |
| `ANTHROPIC_MODEL` | no | Default `claude-sonnet-4-5` |
| `AI_DAILY_LIMIT` | no | Max AI analyses per day, default `50`. `0` turns AI off. |
| `LAGER_CORS_ORIGINS` | no | Browser origins allowed to call the API (only for the web build) |
| `MAX_BODY_BYTES`, `MAX_IMAGE_BYTES` | no | Size limits, default 8 MB and 5 MB |

Real environment variables always override `.env`.

### 3. Run locally

```bash
python app.py               # http://localhost:8000
```

### 4. Deploy on shared hosting (Passenger / cPanel)

1. Create a Python app in the hosting panel and point it to this folder. The entry point is `passenger_wsgi.py`.
2. Upload `app.py`, `passenger_wsgi.py`, `requirements.txt` and the `lager/` folder, then install the requirements in the app's virtualenv.
3. Set the variables above in the panel's environment settings. If you use a `.env` file instead, make sure the app folder is **not** inside `public_html`, so the file can never be downloaded.
4. Restart the app and check `https://your-domain/api/` returns `{"status": "ok"}`, and that `/api/components` without a key returns `401`.

### Upgrading from an older version

Photos uploaded before automatic shrinking can be shrunk in one go. It is safe to run more than once, but shrinking is lossy, so back up the database first:

```bash
python scripts/shrink_images.py           # dry run: shows what would change
python scripts/shrink_images.py --apply   # shrink and rebuild thumbnails
```

## Connecting clients

**Mobile app:** open settings in the app and enter the API address (for example `https://your-domain/api`) and `LAGER_API_KEY`.

**Home Assistant** (`configuration.yaml`):

```yaml
rest:
  - resource: https://your-domain/api/ha/summary
    headers:
      Authorization: !secret lager_read_key   # "Bearer <LAGER_READ_KEY>" in secrets.yaml
    scan_interval: 300
    sensor:
      - name: Lager komponenter
        value_template: "{{ value_json.total_components }}"
      - name: Lager lavt på lager
        value_template: "{{ value_json.low_stock_count }}"
```

**Claude (custom connector) and other MCP clients** that only take a URL:

```
https://your-domain/api/mcp?key=<LAGER_READ_KEY>
```

The `?key=` form is accepted on `/mcp` only, and only makes sense with the read-only key, because URLs can end up in server logs.

**Claude Desktop / MCP clients with header support:**

```json
{
  "mcpServers": {
    "lager": {
      "command": "npx",
      "args": ["mcp-remote", "https://your-domain/api/mcp",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer <your read key>" }
    }
  }
}
```

### MCP tools

All tools are read-only:

- `list_components`: everything, optionally filtered by category
- `search_components`: search across name, category, specs, description and location (all words must match)
- `get_low_stock`: components below a quantity threshold
- `get_summary`: totals per category
- `get_activity_log`: recent events, optionally filtered by component name

## Tests

The test suite runs against a real MySQL/MariaDB database (the tables in it are wiped, so use an empty test database):

```bash
pip install pytest
TEST_DB_USER=lager TEST_DB_PASSWORD=... TEST_DB_NAME=lager_test python -m pytest
```

It covers authentication on every endpoint, read vs write scope, validation, image sniffing, error leaks, CORS, the AI budget, concurrent quantity updates, the activity log and the MCP protocol.

## Project structure

```
├── app.py               # WSGI entry point (and local dev server)
├── passenger_wsgi.py    # Entry point for Passenger on shared hosting
├── lager/
│   ├── api.py           # Routes and the WSGI application
│   ├── auth.py          # API keys and scopes
│   ├── http.py          # Request/response helpers, security headers
│   ├── validation.py    # Input validation and image type detection
│   ├── database.py      # MySQL access, schema and migrations
│   ├── ai.py            # Claude photo identification
│   ├── images.py        # Shrinks uploads, makes list thumbnails (Pillow)
│   ├── mcp.py           # MCP server (JSON-RPC)
│   ├── config.py        # Settings from environment / .env
│   └── keygen.py        # python -m lager.keygen
├── scripts/
│   └── shrink_images.py # One-off: shrink photos from older versions
└── tests/               # pytest suite
```

## Background

I built this to keep track of my own collection of around 180 different components (2,400+ parts) for hobby projects like ESP32-based home automation with Home Assistant.
