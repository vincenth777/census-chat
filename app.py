import os
import streamlit as st
import snowflake.connector

# --- Bridge Streamlit secrets → env vars so core.py can read them ---
try:
    for key in (
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA", "SNOWFLAKE_WAREHOUSE",
        "OPENAI_API_KEY",
    ):
        val = st.secrets.get(key)
        if val is not None:
            os.environ[key] = str(val)
except Exception:
    pass  # No st.secrets available (local dev) — core.py will use .env / env vars

# --- Import shared logic from core ---
from core import (  # noqa: E402
    get_secret,
    SF_CONFIG,
    OPENAI_API_KEY,
    DB,
    SCHEMA,
    DANGEROUS_KEYWORDS,
    OFF_TOPIC_PATTERNS,
    SCHEMA_CONTEXT,
    SYSTEM_PROMPT,
    _strip_sql_comments,
    is_safe_sql,
    is_off_topic,
    extract_sql,
    get_openai_client,
    run_query as _core_run_query,
)


def chat_with_llm(messages):
    """Send messages to OpenAI using the Responses API."""
    client = get_openai_client()
    response = client.responses.create(
        model="gpt-5.2",
        instructions=SYSTEM_PROMPT,
        input=messages,
    )
    return response.output_text

# --- Snowflake connection ---
@st.cache_resource
def get_snowflake_connection():
    return snowflake.connector.connect(**SF_CONFIG)


def run_query(sql, max_rows=500):
    """Execute a read-only SQL query and return results as list of dicts."""
    conn = get_snowflake_connection()
    return _core_run_query(sql, conn, max_rows=max_rows)


# --- Streamlit UI ---
st.set_page_config(page_title="Census Chat", page_icon="📊", layout="centered")
st.title("📊 Census Chat")
st.caption("Ask questions about US population data (2019 American Community Survey)")

# Session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if prompt := st.chat_input("Ask a question about US census data..."):
    # Guardrail check
    if is_off_topic(prompt):
        with st.chat_message("assistant"):
            st.markdown("I can only answer questions about US Census and population data. Please ask something related to demographics, housing, commuting, migration, or language statistics.")
        st.stop()

    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build message history for LLM
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]

    # Multi-turn: LLM may generate SQL, we execute it, feed results back
    max_rounds = 5
    for _ in range(max_rounds):
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response_text = chat_with_llm(llm_messages)

            # Check for SQL in response
            sql_queries = extract_sql(response_text)

            if not sql_queries:
                # No SQL — just a text response, we're done
                st.markdown(response_text)
                st.session_state.messages.append({"role": "assistant", "content": response_text})
                break

            # There's SQL to execute
            st.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

            # Execute each SQL query
            all_results = []
            for sql in sql_queries:
                if not is_safe_sql(sql):
                    error_msg = "⚠️ That query was blocked for safety reasons. I can only run SELECT queries."
                    st.warning(error_msg)
                    all_results.append(error_msg)
                    continue

                with st.spinner("Querying Snowflake..."):
                    result = run_query(sql)

                if isinstance(result, dict) and "error" in result:
                    error_msg = f"Query error: {result['error']}"
                    st.error(error_msg)
                    all_results.append(error_msg)
                else:
                    st.dataframe(result, use_container_width=True)
                    all_results.append(str(result))

            # Feed results back to LLM for summarization
            results_text = "\n\n".join(
                f"Query result {i+1}:\n{r}" for i, r in enumerate(all_results)
            )
            result_message = f"Here are the query results:\n\n{results_text}\n\nPlease summarize these results in a clear, conversational way to answer the user's question. Do not output any more SQL."
            llm_messages.append({"role": "assistant", "content": response_text})
            llm_messages.append({"role": "user", "content": result_message})
    else:
        # Exhausted max rounds
        with st.chat_message("assistant"):
            st.markdown("I had trouble completing that query. Could you try rephrasing your question?")
