# Electronics Inventory System

A personal inventory management system for electronic components (resistors, capacitors, transistors, microcontrollers, etc.), built with a Python backend, native iOS app, and Claude AI for automatic component identification from photos.

## What it does

- **Scan a component** → take a photo in the iOS app → Claude AI identifies the component (name, category, specs) automatically
- **Full CRUD inventory** → add, edit, delete components with quantity tracking and location info
- **Activity log** → every change is logged with timestamp
- **MCP server** → the backend exposes a [Model Context Protocol](https://modelcontextprotocol.io) server, so you can query the inventory directly from Claude Desktop or any MCP-compatible AI client
- **Home Assistant integration** → `/ha/summary` endpoint for smart home dashboards

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python (WSGI / FastAPI), MySQL |
| AI | Anthropic Claude API (image analysis) |
| Mobile | React Native (Expo, TypeScript) |
| Protocol | MCP (Model Context Protocol) over HTTP |
| Hosting | Shared hosting via Passenger WSGI |

## Project structure

```
├── app.py              # Main WSGI application — REST API + MCP server
├── main.py             # FastAPI version (local development)
├── ai.py               # Claude AI image analysis
├── database.py         # MySQL database layer
├── passenger_wsgi.py   # Passenger entry point for shared hosting
├── requirements.txt    # Python dependencies
├── .env.example        # Required environment variables
└── lager-app/          # React Native / Expo mobile app (separate repo)
```

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/components` | List all components |
| GET | `/components/{id}` | Get single component |
| GET | `/components/{id}/image` | Get component image |
| POST | `/components` | Add new component |
| PUT | `/components/{id}` | Edit component |
| PATCH | `/components/{id}/quantity` | Update quantity |
| DELETE | `/components/{id}` | Delete component |
| POST | `/analyze-base64` | AI image analysis |
| GET | `/log` | Activity log |
| GET | `/ha/summary` | Home Assistant summary |
| POST | `/mcp` | MCP JSON-RPC endpoint |

## MCP tools (for Claude Desktop)

The backend exposes these tools via MCP:

- `list_components` — list all components, optionally filtered by category
- `search_components` — full-text search across name, specs, description, location
- `get_low_stock` — find components below a quantity threshold
- `get_summary` — inventory summary by category
- `get_activity_log` — recent activity with optional component filter

### Claude Desktop config

```json
{
  "mcpServers": {
    "lager": {
      "command": "npx",
      "args": ["mcp-remote", "https://your-server.com/api/mcp"]
    }
  }
}
```

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/yourusername/lager.git
cd lager
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your actual values
```

Required variables:

```
ANTHROPIC_API_KEY=sk-ant-...
DB_HOST=localhost
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=your_db_name
```

### 3. Run locally

```bash
python main.py
# API available at http://localhost:8000
```

### 4. Deploy to shared hosting (Passenger)

Upload files to your server. Passenger will use `passenger_wsgi.py` as the entry point automatically.

## How the AI identification works

1. User takes a photo of a component in the iOS app
2. Image is base64-encoded and sent to `POST /analyze-base64`
3. Backend sends the image to Claude with a detailed prompt asking for JSON output
4. Claude returns component name, category, specs, and confidence level
5. The app pre-fills the form — user confirms and saves

Categories supported: Transistors, Capacitors, Microcontrollers, LEDs, Resistors, Cables, Sensors, Servo motors, Relays, Diodes, Regulators, Switches, Other.

## Mobile app

The iOS/Android app is a separate repository built with **React Native (Expo) and TypeScript**. It includes the camera flow for AI scanning, component browsing, quantity editing, and the activity log.

👉 [lager-app repo](https://github.com/yourusername/lager-app)

## Background

Built to manage my personal collection of ~180 unique electronic components (2400+ units) used in hobby projects like ESP32-based automation, custom PCBs, and home automation with Home Assistant.
