# Census Chat

An interactive chat agent that answers natural language questions about US population data using the 2019 American Community Survey (ACS) dataset from the Snowflake Marketplace.

## Live Demo

**https://census-chat.streamlit.app**

No login required. Open the link and start asking questions.

## Example Questions

- In which areas of the country do residents spend, on average, over 30% of their income on rent?
- Which states have the longest average commutes?
- Which cities have the highest amount of migration (people moving in)?
- What are the top states with non-English speaking populations?

## How It Works

```
User question --> GPT-5.2 generates SQL --> Snowflake executes query --> GPT-5.2 summarizes results --> Answer
```

The app uses a multi-turn pipeline: if the LLM needs to first look up column descriptions in the metadata table before writing the data query, or if the first query errors, it retries automatically (up to 5 rounds per question).

### Guardrails

- **Input filtering:** Regex keyword scan blocks off-topic/NSFW input before it reaches the LLM.
- **System prompt:** Instructs the model to only answer US Census questions.
- **SQL safety:** Only `SELECT` / `WITH ... SELECT` statements are allowed. DML/DDL keywords are blocked.

## Architecture

```
census-chat/
  core.py            # Shared logic: config, guardrails, SQL safety, LLM integration
  app.py             # Streamlit frontend (deployed to Streamlit Cloud)
  flask_app.py       # Flask frontend (for local testing)
  templates/
    index.html       # Bootstrap 5 chat UI for Flask
  test_app.py        # 48 tests — original app.py contract
  test_core.py       # 77 tests — core module
  test_flask_app.py  # 42 tests — Flask routes and pipeline
  requirements.txt
  DEVLOG.md          # Development process and future improvements
```

`core.py` contains all framework-agnostic logic (config, guardrails, SQL safety, LLM calls, query execution). Both `app.py` (Streamlit) and `flask_app.py` (Flask) import from it.

## Local Development

### Prerequisites

- Python 3.11+
- Snowflake account with the [US Open Census Data](https://app.snowflake.com/marketplace/listing/GZSNZ2UNN0/safegraph-us-open-census-data-neighborhood-insights-free-dataset) dataset installed
- OpenAI API key

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
OPENAI_API_KEY=sk-...
```

### Run

**Streamlit** (same as production):
```bash
streamlit run app.py
```

**Flask** (local testing with Bootstrap UI):
```bash
python flask_app.py
# Open http://localhost:5000
```

### Tests

```bash
pytest test_app.py test_core.py test_flask_app.py -v
# 167 tests, all passing
```

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | Streamlit / Flask + Bootstrap 5 | Chat UI built-in (Streamlit), free hosting via Streamlit Cloud |
| LLM | OpenAI GPT-5.2 (Responses API) | Strong text-to-SQL capabilities |
| Database | Snowflake | Required by assignment; hosts the ACS dataset |
| Data | 2019 American Community Survey | Free on Snowflake Marketplace |
