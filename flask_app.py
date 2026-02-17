"""Flask frontend for Census Chat — local testing alternative to Streamlit."""

import uuid

import snowflake.connector
from flask import Flask, jsonify, render_template, request, session

from core import (
    SF_CONFIG,
    SYSTEM_PROMPT,
    chat_with_llm,
    extract_sql,
    is_off_topic,
    is_safe_sql,
    run_query,
)

app = Flask(__name__)
app.secret_key = "census-chat-dev-key"

# ---------------------------------------------------------------------------
# Snowflake connection — lazy singleton with reconnect
# ---------------------------------------------------------------------------
_sf_conn = None


def get_snowflake_connection():
    global _sf_conn
    if _sf_conn is None or _sf_conn.is_closed():
        _sf_conn = snowflake.connector.connect(**SF_CONFIG)
    return _sf_conn


# ---------------------------------------------------------------------------
# In-memory conversation store  (keyed by session id)
# ---------------------------------------------------------------------------
_conversations: dict[str, list[dict]] = {}


def _get_messages() -> list[dict]:
    sid = session.get("sid")
    if sid is None:
        sid = str(uuid.uuid4())
        session["sid"] = sid
    return _conversations.setdefault(sid, [])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    # Ensure session has an id on first visit
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_text = (data.get("message") or "").strip()
    if not user_text:
        return jsonify(error="Empty message"), 400

    # Guardrail
    if is_off_topic(user_text):
        return jsonify(steps=[{
            "type": "answer",
            "content": "I can only answer questions about US Census and population data. "
                       "Please ask something related to demographics, housing, commuting, "
                       "migration, or language statistics.",
        }])

    messages = _get_messages()
    messages.append({"role": "user", "content": user_text})

    # Build LLM conversation
    llm_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    steps: list[dict] = []
    conn = get_snowflake_connection()
    max_rounds = 5

    for _ in range(max_rounds):
        try:
            response_text = chat_with_llm(llm_messages)
        except Exception as exc:
            steps.append({"type": "error", "content": str(exc)})
            break

        sql_queries = extract_sql(response_text)

        if not sql_queries:
            # Final text answer
            steps.append({"type": "answer", "content": response_text})
            messages.append({"role": "assistant", "content": response_text})
            break

        # LLM produced SQL
        steps.append({"type": "llm_response", "content": response_text})
        messages.append({"role": "assistant", "content": response_text})

        all_results = []
        for sql in sql_queries:
            if not is_safe_sql(sql):
                msg = "That query was blocked for safety reasons. I can only run SELECT queries."
                steps.append({"type": "query_error", "content": msg})
                all_results.append(msg)
                continue

            result = run_query(sql, conn)
            if isinstance(result, dict) and "error" in result:
                steps.append({"type": "query_error", "content": result["error"]})
                all_results.append(f"Query error: {result['error']}")
            else:
                steps.append({"type": "query_result", "content": result})
                all_results.append(str(result))

        # Feed results back for summarisation
        results_text = "\n\n".join(
            f"Query result {i + 1}:\n{r}" for i, r in enumerate(all_results)
        )
        result_message = (
            f"Here are the query results:\n\n{results_text}\n\n"
            "Please summarize these results in a clear, conversational way "
            "to answer the user's question. Do not output any more SQL."
        )
        llm_messages.append({"role": "assistant", "content": response_text})
        llm_messages.append({"role": "user", "content": result_message})
    else:
        steps.append({
            "type": "error",
            "content": "I had trouble completing that query. Could you try rephrasing your question?",
        })

    return jsonify(steps=steps)


@app.route("/reset", methods=["POST"])
def reset():
    sid = session.get("sid")
    if sid and sid in _conversations:
        del _conversations[sid]
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
