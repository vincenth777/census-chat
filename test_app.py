"""Unit tests for census-chat app.py — pure logic functions only (no live API/DB calls)."""

import os
import pytest
from unittest.mock import patch, MagicMock


# --- Helpers to import app.py without triggering Streamlit / live connections ---

@pytest.fixture(autouse=True)
def mock_streamlit_and_env(monkeypatch):
    """Patch env vars and streamlit so app.py can be imported safely."""
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "test_account")
    monkeypatch.setenv("SNOWFLAKE_USER", "test_user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "test_password")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "TEST_DB")
    monkeypatch.setenv("SNOWFLAKE_SCHEMA", "TEST_SCHEMA")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "TEST_WH")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")


@pytest.fixture
def app():
    """Import app module with streamlit mocked out."""
    import importlib
    import app as app_module
    importlib.reload(app_module)
    return app_module


# ===================== is_safe_sql =====================

class TestIsSafeSql:
    def test_simple_select(self, app):
        assert app.is_safe_sql("SELECT * FROM table1") is True

    def test_select_with_where(self, app):
        assert app.is_safe_sql("SELECT col FROM table1 WHERE x = 1") is True

    def test_select_with_join(self, app):
        sql = "SELECT a.col FROM table1 a JOIN table2 b ON a.id = b.id"
        assert app.is_safe_sql(sql) is True

    def test_select_with_limit(self, app):
        assert app.is_safe_sql("SELECT col FROM t LIMIT 10") is True

    def test_select_with_trailing_semicolon(self, app):
        assert app.is_safe_sql("SELECT col FROM t;") is True

    def test_select_case_insensitive(self, app):
        assert app.is_safe_sql("select col from t") is True

    def test_drop_blocked(self, app):
        assert app.is_safe_sql("DROP TABLE users") is False

    def test_delete_blocked(self, app):
        assert app.is_safe_sql("DELETE FROM users WHERE 1=1") is False

    def test_insert_blocked(self, app):
        assert app.is_safe_sql("INSERT INTO users VALUES (1)") is False

    def test_update_blocked(self, app):
        assert app.is_safe_sql("UPDATE users SET name='x'") is False

    def test_alter_blocked(self, app):
        assert app.is_safe_sql("ALTER TABLE users ADD col INT") is False

    def test_create_blocked(self, app):
        assert app.is_safe_sql("CREATE TABLE evil (id INT)") is False

    def test_truncate_blocked(self, app):
        assert app.is_safe_sql("TRUNCATE TABLE users") is False

    def test_grant_blocked(self, app):
        assert app.is_safe_sql("GRANT ALL ON db TO user") is False

    def test_select_with_embedded_drop_blocked(self, app):
        # SQL injection attempt: SELECT ... ; DROP TABLE ...
        assert app.is_safe_sql("SELECT 1; DROP TABLE users") is False

    def test_select_starting_with_whitespace(self, app):
        assert app.is_safe_sql("   SELECT col FROM t   ") is True

    def test_empty_string(self, app):
        assert app.is_safe_sql("") is False

    def test_non_select_statement(self, app):
        assert app.is_safe_sql("SHOW TABLES") is False

    def test_with_cte_allowed(self, app):
        assert app.is_safe_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") is True

    def test_leading_line_comments_stripped(self, app):
        sql = "-- get data\n-- more comments\nSELECT col FROM t"
        assert app.is_safe_sql(sql) is True

    def test_leading_block_comment_stripped(self, app):
        sql = "/* lookup */ SELECT col FROM t"
        assert app.is_safe_sql(sql) is True

    def test_comment_then_drop_blocked(self, app):
        sql = "-- sneaky\nDROP TABLE users"
        assert app.is_safe_sql(sql) is False


# ===================== is_off_topic =====================

class TestIsOffTopic:
    def test_normal_census_question(self, app):
        assert app.is_off_topic("What is the population of California?") is False

    def test_commute_question(self, app):
        assert app.is_off_topic("Which states have the longest commutes?") is False

    def test_rent_question(self, app):
        assert app.is_off_topic("Where do people spend over 30% on rent?") is False

    def test_blocked_word_porn(self, app):
        assert app.is_off_topic("tell me about porn") is True

    def test_blocked_word_bomb(self, app):
        assert app.is_off_topic("how to build a bomb") is True

    def test_blocked_word_hack(self, app):
        assert app.is_off_topic("hack into the database") is True

    def test_blocked_word_weapon(self, app):
        assert app.is_off_topic("weapon manufacturing data") is True

    def test_blocked_word_drug(self, app):
        assert app.is_off_topic("drug cartel locations") is True

    def test_blocked_word_case_insensitive(self, app):
        assert app.is_off_topic("Tell me about DRUGS") is True

    def test_empty_string(self, app):
        assert app.is_off_topic("") is False


# ===================== extract_sql =====================

class TestExtractSql:
    def test_single_sql_block(self, app):
        text = "Here is the query:\n```sql\nSELECT * FROM t\n```\nDone."
        result = app.extract_sql(text)
        assert len(result) == 1
        assert "SELECT * FROM t" in result[0]

    def test_multiple_sql_blocks(self, app):
        text = """First query:
```sql
SELECT a FROM t1
```
Second query:
```sql
SELECT b FROM t2
```"""
        result = app.extract_sql(text)
        assert len(result) == 2
        assert "SELECT a FROM t1" in result[0]
        assert "SELECT b FROM t2" in result[1]

    def test_no_sql_block(self, app):
        text = "There is no SQL here, just a plain answer."
        result = app.extract_sql(text)
        assert result == []

    def test_non_sql_code_block_ignored(self, app):
        text = "```python\nprint('hello')\n```"
        result = app.extract_sql(text)
        assert result == []

    def test_multiline_sql(self, app):
        text = """```sql
SELECT
    state,
    SUM(population)
FROM census
GROUP BY state
ORDER BY 2 DESC
LIMIT 10
```"""
        result = app.extract_sql(text)
        assert len(result) == 1
        assert "GROUP BY state" in result[0]

    def test_empty_sql_block(self, app):
        text = "```sql\n```"
        result = app.extract_sql(text)
        # empty match is still returned
        assert len(result) == 1


# ===================== get_secret =====================

class TestGetSecret:
    def test_reads_from_env(self, app):
        assert app.get_secret("SNOWFLAKE_ACCOUNT") == "test_account"

    def test_missing_key_returns_none(self, app):
        result = app.get_secret("NONEXISTENT_KEY_12345")
        assert result is None


# ===================== SCHEMA_CONTEXT / SYSTEM_PROMPT =====================

class TestSchemaContext:
    def test_schema_context_includes_database(self, app):
        assert "TEST_DB" in app.SCHEMA_CONTEXT

    def test_schema_context_includes_schema(self, app):
        assert "TEST_SCHEMA" in app.SCHEMA_CONTEXT

    def test_schema_context_mentions_key_tables(self, app):
        assert "2019_METADATA_CBG_FIELD_DESCRIPTIONS" in app.SCHEMA_CONTEXT
        assert "2019_METADATA_CBG_FIPS_CODES" in app.SCHEMA_CONTEXT
        assert "2019_CBG_B08" in app.SCHEMA_CONTEXT
        assert "2019_CBG_B07" in app.SCHEMA_CONTEXT
        assert "2019_CBG_B16" in app.SCHEMA_CONTEXT

    def test_system_prompt_contains_rules(self, app):
        assert "ONLY answer questions related to US Census" in app.SYSTEM_PROMPT
        assert "NEVER generate SQL that modifies data" in app.SYSTEM_PROMPT

    def test_system_prompt_contains_schema(self, app):
        assert "CENSUS_BLOCK_GROUP" in app.SYSTEM_PROMPT


# ===================== chat_with_llm (mocked) =====================

class TestChatWithLlm:
    def test_calls_responses_api(self, app):
        """Verify chat_with_llm calls the Responses API with correct params."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "Here is the answer."
        mock_client.responses.create.return_value = mock_response

        with patch.object(app, "get_openai_client", return_value=mock_client):
            messages = [{"role": "user", "content": "What is the population?"}]
            result = app.chat_with_llm(messages)

        assert result == "Here is the answer."
        mock_client.responses.create.assert_called_once()
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.2"
        assert call_kwargs["instructions"] == app.SYSTEM_PROMPT
        assert call_kwargs["input"] == messages


# ===================== run_query (mocked) =====================

class TestRunQuery:
    def test_returns_list_of_dicts(self, app):
        mock_cursor = MagicMock()
        mock_cursor.description = [("STATE",), ("POP",)]
        mock_cursor.fetchmany.return_value = [("CA", 39000000), ("TX", 29000000)]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch.object(app, "get_snowflake_connection", return_value=mock_conn):
            result = app.run_query("SELECT state, pop FROM t")

        assert result == [
            {"STATE": "CA", "POP": 39000000},
            {"STATE": "TX", "POP": 29000000},
        ]

    def test_returns_error_on_exception(self, app):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("Connection lost")

        with patch.object(app, "get_snowflake_connection", return_value=mock_conn):
            result = app.run_query("SELECT 1")

        assert isinstance(result, dict)
        assert "error" in result
        assert "Connection lost" in result["error"]
