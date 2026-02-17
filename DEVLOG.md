# Census Chat — Development Log

## Overview

Census Chat is a Streamlit-based chat application that answers natural language questions about US population data using the 2019 American Community Survey (ACS) dataset from the Snowflake Marketplace. It uses OpenAI's GPT-5.2 model (via the Responses API) to translate user questions into SQL, executes them against Snowflake, and summarizes the results conversationally.

**Live demo:** https://census-chat.streamlit.app

## Development Process

### 1. Architecture Decision

Chose a single-file Streamlit app (`app.py`) for simplicity. The entire pipeline — chat UI, LLM integration, Snowflake queries, guardrails — lives in one file with no separate backend. Streamlit Cloud handles deployment directly from GitHub with zero infrastructure management.

### 2. Schema Discovery & Prompt Engineering

The hardest part was getting the LLM to generate correct Snowflake SQL on the first try. Three issues required iterative debugging:

- **Case-sensitive column names:** The ACS data columns (e.g., `B08135e1`) were created with double quotes in Snowflake, making them case-sensitive. Unquoted references get uppercased and fail. Added explicit quoting rules to the system prompt.
- **Table names starting with numbers:** Tables like `2019_CBG_B08` require double quotes in Snowflake. The LLM would sometimes forget to quote the metadata tables, causing `unexpected '.2019'` syntax errors. Added fully-qualified examples to the prompt.
- **COUNTY_FIPS is 3 digits, not 5:** The FIPS codes table stores county codes as 3-digit values within the state (e.g., `'001'`), not the full 5-digit state+county FIPS. Joins using `LEFT(CENSUS_BLOCK_GROUP, 5) = COUNTY_FIPS` silently returned zero rows. Fixed the prompt to specify `STATE_FIPS || COUNTY_FIPS = LEFT(CENSUS_BLOCK_GROUP, 5)`.

### 3. Multi-Turn SQL Loop

The app supports up to 5 rounds of LLM ↔ Snowflake interaction per question. If the LLM generates SQL, the app executes it, feeds the results (or errors) back to the LLM, and asks for a summary. This handles cases where the LLM needs to first look up column descriptions in the metadata table before writing the actual data query, and also allows recovery from SQL errors.

### 4. Guardrails

Three layers of protection:
- **Input filtering:** Regex-based keyword scan blocks obvious off-topic/NSFW input before it hits the API.
- **System prompt:** Instructs the model to only answer US Census questions and refuse everything else.
- **SQL safety:** Only `SELECT` and `WITH ... SELECT` statements are allowed. A regex check blocks any DML/DDL keywords (DROP, DELETE, INSERT, etc.). Leading SQL comments are stripped before validation.

### 5. Testing

- **48 unit tests** covering all pure logic functions (SQL safety, guardrails, SQL extraction, secret loading, schema context integrity, mocked LLM and Snowflake calls).
- **End-to-end smoke tests** against live GPT-5.2 and Snowflake verified all 4 required questions return real data and coherent summaries.

## What I Would Improve With More Time

- **Streaming responses:** Use the Responses API streaming mode to show the LLM's response as it generates, rather than waiting for the full response.
- **Query result caching:** Cache Snowflake query results in `st.session_state` keyed by SQL hash to avoid re-running identical queries.
- **Chart generation:** Detect when results are tabular rankings and auto-render bar charts or maps alongside the text summary.
- **Conversation memory with `previous_response_id`:** Use the Responses API's built-in conversation state instead of manually passing message history, reducing token usage on long conversations.
- **Better error recovery:** When the LLM generates bad SQL, automatically retry with the error message rather than showing the error to the user and asking them to rephrase.
- **Schema introspection at startup:** Query Snowflake for actual table/column metadata on app load and inject it into the system prompt dynamically, rather than hardcoding it.
- **Input moderation API:** Replace the simple regex keyword filter with OpenAI's moderation endpoint for more robust off-topic detection.
- **Rate limiting and cost controls:** Add per-session query limits and token budget tracking to prevent abuse on the public demo.
