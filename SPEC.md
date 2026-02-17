# Census Chat — Project Spec (v1)

## What We're Building

A chat app where users ask natural language questions about US census data, and an LLM translates those into SQL queries against Snowflake, then returns a human-readable answer.

**Core loop:**
```
User question → LLM generates SQL → Query Snowflake → LLM summarizes results → Answer
```

## Tech Stack (Minimal)

| Layer       | Choice           | Why                                      |
|-------------|------------------|------------------------------------------|
| Frontend    | Streamlit        | Chat UI built-in, zero JS, free hosting  |
| Backend     | Streamlit (same) | No separate server needed                |
| Database    | Snowflake        | Required by assignment                   |
| LLM         | Claude API       | Text-to-SQL + answer formatting          |
| Deployment  | Streamlit Cloud  | Free, deploys from GitHub, zero devops   |

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────┐     ┌───────────┐
│  Streamlit   │────▶│  Python (app.py)                  │────▶│ Snowflake │
│  Chat UI     │◀────│  - guardrails (prompt)            │◀────│ Census DB │
└─────────────┘     │  - text-to-SQL (Claude)           │     └───────────┘
                    │  - result summarization (Claude)   │
                    └──────────────────────────────────┘
```

Single file (`app.py`) + a config/secrets file. That's it.

## Part 1: Get It Working Locally

### What YOU (human) need to do first:

1. **Create a Snowflake trial account** (free 30-day, no credit card)
   - Go to https://signup.snowflake.com/
   - Pick any cloud provider/region
   - Note your **account identifier**, **username**, and **password**

2. **Install the census dataset from Snowflake Marketplace**
   - Log into Snowflake → Marketplace → search "US Open Census Data" by SafeGraph
   - Click "Get" → accept terms → it creates a shared database (usually named `US_OPEN_CENSUS_DATA` or similar)
   - Open a worksheet and run: `SHOW SCHEMAS IN DATABASE <database_name>;` and `SHOW TABLES IN SCHEMA <database_name>.<schema_name>;`
   - **Copy-paste the table names and a few column samples** so I know the exact schema to target

3. **Get a Claude API key**
   - Go to https://console.anthropic.com/ → API Keys → Create Key
   - (Or if you prefer OpenAI, I can swap it — just say so)

4. **Give me the credentials** — I'll set up a `.env` file:
   - `SNOWFLAKE_ACCOUNT` — your account identifier
   - `SNOWFLAKE_USER` — your username
   - `SNOWFLAKE_PASSWORD` — your password
   - `SNOWFLAKE_DATABASE` — the marketplace database name
   - `SNOWFLAKE_SCHEMA` — the schema containing the census tables
   - `SNOWFLAKE_WAREHOUSE` — your warehouse name (usually `COMPUTE_WH`)
   - `ANTHROPIC_API_KEY` — your Claude API key

### What I (Claude) will build:

1. **`app.py`** — Single Streamlit app with:
   - Chat interface (using `st.chat_message` / `st.chat_input`)
   - Snowflake connection via `snowflake-connector-python`
   - System prompt with table schemas → Claude generates SQL
   - Execute SQL against Snowflake → pass results back to Claude for summarization
   - Simple guardrail: system prompt instructs Claude to refuse off-topic/NSFW, plus a lightweight check on user input

2. **`requirements.txt`** — Dependencies:
   - `streamlit`
   - `snowflake-connector-python`
   - `anthropic`

3. **`.env`** — Secrets (gitignored)

### To run locally:
```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Part 2: Deploy to the Web

### What YOU (human) need to do:

1. **Push the repo to GitHub** (you've already got it set up)

2. **Go to https://share.streamlit.io/**
   - Sign in with GitHub
   - Click "New app"
   - Select this repo → branch `main` → file `app.py`
   - Under "Advanced settings", add your secrets in TOML format:
     ```toml
     SNOWFLAKE_ACCOUNT = "..."
     SNOWFLAKE_USER = "..."
     SNOWFLAKE_PASSWORD = "..."
     SNOWFLAKE_DATABASE = "..."
     SNOWFLAKE_SCHEMA = "..."
     SNOWFLAKE_WAREHOUSE = "..."
     ANTHROPIC_API_KEY = "..."
     ```
   - Click "Deploy"

3. **(Optional) Add basic auth** — If you want a login gate for the reviewers, Streamlit has a simple password check pattern I can add, or you can just share the URL directly.

### What I (Claude) will build:

- Adjust `app.py` to read secrets from `st.secrets` (Streamlit Cloud) with `.env` fallback (local dev)
- That's it — the same code runs locally and deployed

## Guardrails (Kept Simple)

- **System prompt**: Explicitly instructs Claude to only answer US census/population questions and refuse everything else
- **Input check**: Quick keyword/pattern scan to reject obvious NSFW before hitting the API
- **SQL safety**: Only `SELECT` statements allowed — reject anything with `DROP`, `DELETE`, `INSERT`, `UPDATE`, etc.
- No need for a separate moderation API in v1

## Questions the App Must Answer

Per the assignment:
1. Areas where residents spend >30% of income on rent
2. States with longest average commutes
3. Cities with highest migration (people moving in)
4. Top states with non-English speaking populations

These will work naturally once I have the correct table/column names from your Snowflake schema exploration in step 2.

## File Structure (Final)

```
census-chat/
├── app.py              # The entire application
├── requirements.txt    # Dependencies
├── .env                # Local secrets (gitignored)
├── .gitignore          # Ignore .env, .venv, __pycache__
├── SPEC.md             # This file
└── DEVLOG.md           # Development process writeup (deliverable)
```

## What's NOT in v1

- No fancy UI (charts, maps, animations)
- No conversation memory beyond current session
- No caching of query results
- No multi-turn SQL refinement
- No streaming responses

These are all easy adds later if time allows.
