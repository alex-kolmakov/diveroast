# DiveRoast 🌊

**A conversational agent that roasts your SCUBA diving — backed by real safety data.**

[![CI](https://github.com/alex-kolmakov/diveroast/actions/workflows/ci.yaml/badge.svg)](https://github.com/alex-kolmakov/diveroast/actions/workflows/ci.yaml)
![Terraform](https://img.shields.io/badge/Terraform-GCP-844FBA?logo=terraform&logoColor=white)
![Cloud Run](https://img.shields.io/badge/Cloud%20Run-4285F4?logo=googlecloud&logoColor=white)
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

## Cloud Deployment (GCP)

DiveRoast deploys to **Google Cloud Run** with Terraform. Three services (backend, frontend, Phoenix tracing), managed through three `make` commands:

```bash
# Prerequisites: gcloud CLI, terraform, docker
# One-time GCP setup:
gcloud auth login
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com

# Configure
cp infra/terraform.tfvars.example infra/terraform.tfvars
# Edit terraform.tfvars with your GCP project ID and region
# Ensure GEMINI_API_KEY is set in .env

# Deploy
make prepare    # bootstrap infra (APIs, registry, secrets), build & push images
make deploy     # apply Terraform, deploy all Cloud Run services

# Tear down — verifies nothing billable remains
make destroy
```

`make prepare` creates the Artifact Registry and secrets via a targeted Terraform apply, then builds and pushes both Docker images. `make deploy` applies the full Terraform config, then rebuilds the frontend with the real backend URL (Vite bakes `VITE_API_URL` at build time). `make destroy` empties GCS buckets, runs `terraform destroy`, and verifies that all Cloud Run services, buckets, secrets, and repos are actually gone.

The Gemini API key flows from `.env` → `TF_VAR_gemini_api_key` → Secret Manager. It never appears in Terraform state or source control.

**Architecture:**

| Resource | Service | Notes |
| -------- | ------- | ----- |
| Cloud Run | `diveroast-backend` | FastAPI + agent + RAG, scale 0-3, 4Gi RAM |
| Cloud Run | `diveroast-frontend` | Nginx serving Vite build, scale 0-2 |
| Cloud Run | `diveroast-phoenix` | Arize Phoenix tracing UI, always-on |
| GCS | Phoenix traces bucket | FUSE-mounted for trace persistence |
| Artifact Registry | `diveroast` | Docker images for backend + frontend |
| Secret Manager | `gemini-api-key` | Injected into backend via `secret_key_ref` |

Terraform files live in `infra/`, one per concern. See [Article 06](articles/06-from-localhost-to-cloud-run.md) for the full deployment story.


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

Thanks to [#DataTalksClub](https://datatalks.club/) for mentoring and to everyone who taught me to be a better diver!
![image](https://github.com/user-attachments/assets/52d2ee9e-7a54-49d8-a44b-633aae10f34a)
