# DiveRoast 🌊

**A conversational agent that roasts your SCUBA diving — backed by real safety data.**

[![CI](https://github.com/alex-kolmakov/diveroast/actions/workflows/ci.yaml/badge.svg)](https://github.com/alex-kolmakov/diveroast/actions/workflows/ci.yaml)
![Hetzner](https://img.shields.io/badge/Hetzner-VPS-D50C2D?logo=hetzner&logoColor=white)
![Docker](https://img.shields.io/badge/Docker%20Compose-2496ED?logo=docker&logoColor=white)
![dlt](https://img.shields.io/badge/dlt-data%20pipeline-teal)
![LanceDB](https://img.shields.io/badge/LanceDB-vectors-white)
![MCP](https://img.shields.io/badge/MCP-tool%20server-purple)
![Arize Phoenix](https://img.shields.io/badge/Arize-Phoenix-orange)
![React](https://img.shields.io/badge/React-61DAFB?logo=react&logoColor=black)

![Screen Recording 2026-02-17 at 5 14 59 PM](https://github.com/user-attachments/assets/c81ba2e6-4cf5-4e92-8f8e-9ef496a26aa6)


## My story behind it

> I started diving in a pretty shallow pool. When I was studing for my first OWD license I was caught off guard by two pictures of diving profiles that my instructor showed me and said: "There are two dives here, but one is clearly worse than the other - can you guess?" And he was right, at the time I could only guess.

Any diver with experience will tell you instantly which of these two dive profiles is a problem (and yes both are mine):

| Dive #1 — the rough one | Dive #2 — the clean one |
| ----------------------- | ----------------------- |
| ![Dive #1](https://github.com/alex-kolmakov/divelog-autoreport/assets/3127175/5d043a91-39bb-4b77-a49c-bd19b82cf04a) | ![Dive #2](https://github.com/alex-kolmakov/divelog-autoreport/assets/3127175/86bc990c-55e9-4c14-9db9-310b88b3c4bb) |

*Dive profiles aren't just squiggly lines. They can tell a story.*

**So what a computer could read them the way an experienced instructor does?**
Not only scolding you for fast ascents(dive computers do that well already), but showing your overall stats, knowing what DAN considers good practice and roasting your bad diving habits so you can improve?

**That's DiveRoast.**

It analyzes your SCUBA dive logs, identifies safety issues, and delivers personalized safety critiques grounded in real incident reports from [Divers Alert Network (DAN)](https://www.diversalertnetwork.org/). Upload a dive log, get a full safety analysis, learn something.

- **Agentic analysis** — Gemini with function-calling tools reviews your dives and delivers personalized safety commentary with dry humor
- **RAG over DAN content** — hybrid search (semantic + full-text) over DAN incident reports and guidelines via LanceDB
- **Interactive dashboard** — per-dive gauges for ascent rate, SAC rate, NDL, depth; top 3 worst dives with LLM-generated explanations; diver profile with water types, regions, experience level; mini maps for dive sites
- **MCP server** — all diving tools exposed via the Model Context Protocol for use in Claude Desktop, Cursor, or any MCP client
- **Observability** — full LLM/tool/RAG tracing with Arize Phoenix

## Quick Start (Docker)

```bash
git clone https://github.com/alex-kolmakov/diveroast.git
cd diveroast
cp .env.sample .env   # add your GEMINI_API_KEY
docker compose up --build
```

Open http://localhost:3000 in your browser.

## Local Development

### Backend

```bash
uv pip install -e ".[dev]"
uvicorn src.api.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Services

| Service  | URL                    | Description            |
| -------- | ---------------------- | ---------------------- |
| Frontend | http://localhost:5173   | React dev server       |
| Backend  | http://localhost:8000   | FastAPI + SSE          |
| Phoenix  | http://localhost:6006   | Tracing UI             |

## MCP Server

DiveRoast exposes its diving tools as an MCP server (stdio transport). Add to your Claude Desktop or Cursor config:

```json
{
  "mcpServers": {
    "diveroast": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/diveroast"
    }
  }
}
```

Available tools: `search_dan_incidents`, `search_dan_guidelines`, `parse_dive_log`, `analyze_dive_profile`, `get_dive_summary`, `list_dives`, `refresh_dan_data`.

## Production Deployment (Hetzner VPS)

DiveRoast runs on a **Hetzner VPS** (cpx22, ~€4.50/mo) with Docker Compose, nginx as an SSL-terminating reverse proxy, and Cloudflare in front for DNS and DDoS protection.

```bash
# On a fresh Debian 12 VPS — run once as root:
bash deploy/setup-vps.sh

# Place your Cloudflare Origin Certificate at:
#   /etc/ssl/cloudflare/cert.pem
#   /etc/ssl/cloudflare/key.pem

# Configure environment:
cp .env.prod.sample .env
# Set GEMINI_API_KEY in .env

# Start everything:
docker compose -f docker-compose.prod.yml up -d --build
```

**Architecture:**

| Container | Role | Exposed |
| --------- | ---- | ------- |
| `nginx` | SSL termination, reverse proxy | 80, 443 |
| `diveroast-backend` | FastAPI + agent + RAG | internal only |
| `diveroast-frontend` | React SPA (nginx:alpine) | internal only |
| `diveroast-phoenix` | Arize Phoenix tracing UI | internal only (SSH tunnel to access) |

All data (snapshots, donated dive logs) lives in named Docker volumes — persistent across restarts. Phoenix is not exposed publicly; access it via `ssh -L 6006:localhost:6006 root@<vps-ip>`.


## Project Structure

```
src/
├── agent/         # Gemini client, system prompts, function-calling tools
├── analysis/      # Feature engineering (ascent speed, NDL, SAC rate)
├── api/           # FastAPI gateway with SSE streaming
├── mcp/           # MCP server (7 tools via FastMCP)
├── parsers/       # Dive log parsing (ABC + Subsurface XML)
├── pipelines/     # CLI scripts for DAN ingestion & dive processing
├── rag/           # dlt pipeline + LanceDB hybrid search
├── config.py
└── observability.py

frontend/src/
├── components/    # React UI components
├── hooks/         # Custom React hooks
├── services/      # API client
├── types/         # TypeScript types
└── App.tsx
```

## Testing

```bash
# Backend
pytest tests/ -x --tb=short

# Frontend type-check
cd frontend && npx tsc --noEmit
```

## Contributing

Install pre-commit hooks before pushing:

```bash
pre-commit install
pre-commit run --all-files
```

The pre-commit pipeline runs **ruff** (lint + format), **pyrefly** (type check), and **pytest**.

## Acknowledgements

Thanks to [#DataTalksClub](https://datatalks.club/) for mentoring during 2024 cohort. 

And to everyone who taught me to be a better diver, so I can care for others!


![image](https://github.com/user-attachments/assets/52d2ee9e-7a54-49d8-a44b-633aae10f34a)
