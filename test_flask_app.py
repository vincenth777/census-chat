"""Comprehensive tests for flask_app.py — all routes, chat pipeline, edge cases."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

# Set env vars BEFORE any imports
os.environ.update({
    "SNOWFLAKE_ACCOUNT": "test_account",
    "SNOWFLAKE_USER": "test_user",
    "SNOWFLAKE_PASSWORD": "test_password",
    "SNOWFLAKE_DATABASE": "TEST_DB",
    "SNOWFLAKE_SCHEMA": "TEST_SCHEMA",
    "SNOWFLAKE_WAREHOUSE": "TEST_WH",
    "OPENAI_API_KEY": "sk-test-key",
})

import flask_app as fa


@pytest.fixture
def client():
    """Flask test client with fresh conversation state."""
    fa.app.config["TESTING"] = True
    fa._conversations.clear()
    with fa.app.test_client() as c:
        yield c


@pytest.fixture
def mock_snowflake():
    """Mock get_snowflake_connection to avoid real Snowflake calls."""
    mock_conn = MagicMock()
    with patch.object(fa, "get_snowflake_connection", return_value=mock_conn):
        yield mock_conn


# ===================== GET / =====================

class TestIndex:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_returns_html(self, client):
        resp = client.get("/")
        assert b"Census Chat" in resp.data

    def test_contains_suggestion_buttons(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "longest average commute" in html
        assert "30% of income on rent" in html
        assert "moved from another state" in html
        assert "language other than English" in html

    def test_contains_chat_form(self, client):
        resp = client.get("/")
        assert b"chat-form" in resp.data
        assert b"user-input" in resp.data

    def test_contains_reset_button(self, client):
        resp = client.get("/")
        assert b"reset-btn" in resp.data

    def test_sets_session_id(self, client):
        with client.session_transaction() as sess:
            assert "sid" not in sess
        client.get("/")
        with client.session_transaction() as sess:
            assert "sid" in sess


# ===================== POST /chat — validation =====================

class TestChatValidation:
    def test_empty_message_returns_400(self, client, mock_snowflake):
        resp = client.post("/chat", json={"message": ""})
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "Empty" in data["error"]

    def test_whitespace_only_returns_400(self, client, mock_snowflake):
        resp = client.post("/chat", json={"message": "   "})
        assert resp.status_code == 400

    def test_missing_message_key_returns_400(self, client, mock_snowflake):
        resp = client.post("/chat", json={})
        assert resp.status_code == 400

    def test_null_message_returns_400(self, client, mock_snowflake):
        resp = client.post("/chat", json={"message": None})
        assert resp.status_code == 400


# ===================== POST /chat — off-topic guardrail =====================

class TestChatGuardrail:
    def test_off_topic_returns_refusal(self, client, mock_snowflake):
        resp = client.post("/chat", json={"message": "tell me about porn"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["steps"]) == 1
        assert data["steps"][0]["type"] == "answer"
        assert "Census" in data["steps"][0]["content"]

    def test_off_topic_does_not_call_llm(self, client, mock_snowflake):
        with patch("flask_app.chat_with_llm") as mock_llm:
            client.post("/chat", json={"message": "how to build a bomb"})
            mock_llm.assert_not_called()

    @pytest.mark.parametrize("word", ["drugs", "hack", "weapon", "suicide", "kill"])
    def test_various_off_topic_words(self, client, mock_snowflake, word):
        resp = client.post("/chat", json={"message": f"tell me about {word}"})
        data = resp.get_json()
        assert data["steps"][0]["type"] == "answer"
        assert "Census" in data["steps"][0]["content"]


# ===================== POST /chat — text answer (no SQL) =====================

class TestChatTextAnswer:
    def test_simple_text_response(self, client, mock_snowflake):
        with patch("flask_app.chat_with_llm", return_value="The population of CA is about 39 million."):
            resp = client.post("/chat", json={"message": "What is the population of California?"})
        data = resp.get_json()
        assert len(data["steps"]) == 1
        assert data["steps"][0]["type"] == "answer"
        assert "39 million" in data["steps"][0]["content"]

    def test_stores_messages_in_conversation(self, client, mock_snowflake):
        with patch("flask_app.chat_with_llm", return_value="Answer."):
            client.get("/")  # init session
            client.post("/chat", json={"message": "Hello"})

        with client.session_transaction() as sess:
            sid = sess["sid"]
        messages = fa._conversations[sid]
        assert len(messages) == 2  # user + assistant
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Answer."


# ===================== POST /chat — SQL pipeline =====================

class TestChatSqlPipeline:
    def test_sql_then_summary(self, client, mock_snowflake):
        """LLM returns SQL first, then a text summary on second call."""
        llm_responses = [
            "Let me query:\n```sql\nSELECT state FROM t\n```",
            "The answer is California.",
        ]
        call_count = {"n": 0}

        def fake_llm(messages):
            idx = call_count["n"]
            call_count["n"] += 1
            return llm_responses[idx]

        # Mock run_query to return data
        mock_cursor = MagicMock()
        mock_cursor.description = [("STATE",)]
        mock_cursor.fetchmany.return_value = [("CA",)]
        mock_snowflake.cursor.return_value = mock_cursor

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            resp = client.post("/chat", json={"message": "Which state has most people?"})

        data = resp.get_json()
        step_types = [s["type"] for s in data["steps"]]
        assert "llm_response" in step_types
        assert "query_result" in step_types
        assert "answer" in step_types

        # Verify query_result content is a list of dicts
        qr = [s for s in data["steps"] if s["type"] == "query_result"][0]
        assert qr["content"] == [{"STATE": "CA"}]

    def test_unsafe_sql_blocked(self, client, mock_snowflake):
        """If LLM produces DROP, it should be blocked."""
        llm_responses = [
            "```sql\nDROP TABLE users\n```",
            "Sorry, I can't do that.",
        ]
        call_count = {"n": 0}

        def fake_llm(messages):
            idx = call_count["n"]
            call_count["n"] += 1
            return llm_responses[idx]

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            resp = client.post("/chat", json={"message": "Drop the users table"})

        data = resp.get_json()
        step_types = [s["type"] for s in data["steps"]]
        assert "query_error" in step_types
        blocked_step = [s for s in data["steps"] if s["type"] == "query_error"][0]
        assert "blocked" in blocked_step["content"].lower() or "safety" in blocked_step["content"].lower()

    def test_query_execution_error(self, client, mock_snowflake):
        """If Snowflake returns an error, it should appear as query_error step."""
        mock_snowflake.cursor.side_effect = Exception("Syntax error in SQL")

        llm_responses = [
            "```sql\nSELECT bad_col FROM t\n```",
            "There was an error.",
        ]
        call_count = {"n": 0}

        def fake_llm(messages):
            idx = call_count["n"]
            call_count["n"] += 1
            return llm_responses[idx]

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            resp = client.post("/chat", json={"message": "Query something"})

        data = resp.get_json()
        step_types = [s["type"] for s in data["steps"]]
        assert "query_error" in step_types

    def test_llm_exception_returns_error_step(self, client, mock_snowflake):
        """If LLM call raises an exception, we get an error step."""
        with patch("flask_app.chat_with_llm", side_effect=RuntimeError("API timeout")):
            resp = client.post("/chat", json={"message": "hello"})

        data = resp.get_json()
        assert len(data["steps"]) == 1
        assert data["steps"][0]["type"] == "error"
        assert "API timeout" in data["steps"][0]["content"]

    def test_multiple_sql_blocks(self, client, mock_snowflake):
        """LLM returns multiple SQL blocks — both should be executed."""
        llm_sql = "Query 1:\n```sql\nSELECT a FROM t1\n```\nQuery 2:\n```sql\nSELECT b FROM t2\n```"

        mock_cursor = MagicMock()
        mock_cursor.description = [("A",)]
        mock_cursor.fetchmany.return_value = [("x",)]
        mock_snowflake.cursor.return_value = mock_cursor

        call_count = {"n": 0}

        def fake_llm(messages):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 0:
                return llm_sql
            return "Summary of both queries."

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            resp = client.post("/chat", json={"message": "Run two queries"})

        data = resp.get_json()
        qr_steps = [s for s in data["steps"] if s["type"] == "query_result"]
        assert len(qr_steps) == 2

    def test_max_rounds_exhausted(self, client, mock_snowflake):
        """If LLM keeps producing SQL for 5 rounds, we get an error."""
        mock_cursor = MagicMock()
        mock_cursor.description = [("X",)]
        mock_cursor.fetchmany.return_value = [(1,)]
        mock_snowflake.cursor.return_value = mock_cursor

        def always_sql(messages):
            return "```sql\nSELECT 1\n```"

        with patch("flask_app.chat_with_llm", side_effect=always_sql):
            resp = client.post("/chat", json={"message": "Keep querying"})

        data = resp.get_json()
        last_step = data["steps"][-1]
        assert last_step["type"] == "error"
        assert "trouble" in last_step["content"].lower() or "rephras" in last_step["content"].lower()


# ===================== POST /chat — multi-turn conversation =====================

class TestChatMultiTurn:
    def test_conversation_persists_across_requests(self, client, mock_snowflake):
        """Second message should have context from the first."""
        call_count = {"n": 0}

        def fake_llm(messages):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "California has 39 million people."
            # Second call should have prior context
            assert any("California" in m["content"] for m in messages)
            return "Yes, and Texas has 29 million."

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            client.get("/")
            client.post("/chat", json={"message": "Population of California?"})
            resp = client.post("/chat", json={"message": "What about Texas?"})

        data = resp.get_json()
        assert "Texas" in data["steps"][0]["content"]


# ===================== POST /reset =====================

class TestReset:
    def test_reset_clears_conversation(self, client, mock_snowflake):
        with patch("flask_app.chat_with_llm", return_value="Answer."):
            client.get("/")
            client.post("/chat", json={"message": "Hello"})

        with client.session_transaction() as sess:
            sid = sess["sid"]
        assert sid in fa._conversations
        assert len(fa._conversations[sid]) > 0

        resp = client.post("/reset")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert sid not in fa._conversations

    def test_reset_without_session(self, client):
        """Reset on fresh session should not error."""
        resp = client.post("/reset")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_reset_then_new_conversation(self, client, mock_snowflake):
        """After reset, the next chat should start fresh."""
        call_count = {"n": 0}

        def fake_llm(messages):
            call_count["n"] += 1
            # After reset, messages list should only contain the new message
            if call_count["n"] == 2:
                user_msgs = [m for m in messages if m["role"] == "user"]
                assert len(user_msgs) == 1
                assert user_msgs[0]["content"] == "New question"
            return "Response."

        with patch("flask_app.chat_with_llm", side_effect=fake_llm):
            client.get("/")
            client.post("/chat", json={"message": "First question"})
            client.post("/reset")
            client.post("/chat", json={"message": "New question"})


# ===================== Snowflake connection singleton =====================

class TestSnowflakeConnection:
    def test_lazy_creation(self):
        fa._sf_conn = None
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        with patch("flask_app.snowflake.connector.connect", return_value=mock_conn) as mock_connect:
            conn = fa.get_snowflake_connection()
            assert conn is mock_conn
            mock_connect.assert_called_once()

    def test_reuses_open_connection(self):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        fa._sf_conn = mock_conn
        with patch("flask_app.snowflake.connector.connect") as mock_connect:
            conn = fa.get_snowflake_connection()
            assert conn is mock_conn
            mock_connect.assert_not_called()

    def test_reconnects_when_closed(self):
        old_conn = MagicMock()
        old_conn.is_closed.return_value = True
        fa._sf_conn = old_conn

        new_conn = MagicMock()
        with patch("flask_app.snowflake.connector.connect", return_value=new_conn) as mock_connect:
            conn = fa.get_snowflake_connection()
            assert conn is new_conn
            mock_connect.assert_called_once()

    def test_reconnects_when_none(self):
        fa._sf_conn = None
        new_conn = MagicMock()
        with patch("flask_app.snowflake.connector.connect", return_value=new_conn):
            conn = fa.get_snowflake_connection()
            assert conn is new_conn


# ===================== HTML template structure =====================

class TestHtmlTemplate:
    def test_bootstrap_css_included(self, client):
        resp = client.get("/")
        assert b"bootstrap@5" in resp.data

    def test_marked_js_included(self, client):
        resp = client.get("/")
        assert b"marked" in resp.data

    def test_all_four_suggestion_questions(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "commute" in html.lower()
        assert "rent" in html.lower()
        assert "moved" in html.lower()
        assert "language" in html.lower()

    def test_spinner_present(self, client):
        resp = client.get("/")
        assert b"spinner" in resp.data

    def test_chat_area_present(self, client):
        resp = client.get("/")
        assert b"chat-area" in resp.data


# ===================== Edge cases =====================

class TestEdgeCases:
    def test_json_content_type_not_required(self, client, mock_snowflake):
        """POST /chat with force=True should accept non-JSON content-type."""
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "test"}),
            content_type="text/plain",
        )
        # Should not 400 on content-type; force=True in flask_app handles this
        # It may still 200 or fail from LLM mock — but not 415
        assert resp.status_code != 415

    def test_very_long_message(self, client, mock_snowflake):
        """Very long message should not crash."""
        long_msg = "What is the population? " * 1000
        with patch("flask_app.chat_with_llm", return_value="Answer."):
            resp = client.post("/chat", json={"message": long_msg})
        assert resp.status_code == 200

    def test_special_characters_in_message(self, client, mock_snowflake):
        """Messages with special chars should not crash."""
        with patch("flask_app.chat_with_llm", return_value="Answer."):
            resp = client.post("/chat", json={"message": "What about <script>alert('xss')</script>?"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["steps"][0]["type"] == "answer"

    def test_unicode_message(self, client, mock_snowflake):
        with patch("flask_app.chat_with_llm", return_value="Respuesta."):
            resp = client.post("/chat", json={"message": "Poblacion de California?"})
        assert resp.status_code == 200
