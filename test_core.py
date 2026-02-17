"""Unit tests for core.py — shared pure logic module."""

import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Set env vars before importing core."""
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "test_account")
    monkeypatch.setenv("SNOWFLAKE_USER", "test_user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "test_password")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "TEST_DB")
    monkeypatch.setenv("SNOWFLAKE_SCHEMA", "TEST_SCHEMA")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "TEST_WH")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")


@pytest.fixture
def core():
    import importlib
    import core as core_module
    importlib.reload(core_module)
    return core_module


# ===================== get_secret =====================

class TestGetSecret:
    def test_reads_env_var(self, core):
        assert core.get_secret("SNOWFLAKE_ACCOUNT") == "test_account"

    def test_missing_key_returns_none(self, core):
        assert core.get_secret("TOTALLY_MISSING_XYZ") is None

    def test_no_streamlit_dependency(self, core):
        """core.get_secret should not touch streamlit at all."""
        import sys
        # core should not have imported streamlit
        assert "streamlit" not in dir(core)


# ===================== SF_CONFIG =====================

class TestSFConfig:
    def test_sf_config_keys(self, core):
        expected_keys = {"account", "user", "password", "database", "schema", "warehouse"}
        assert set(core.SF_CONFIG.keys()) == expected_keys

    def test_sf_config_values(self, core):
        assert core.SF_CONFIG["account"] == "test_account"
        assert core.SF_CONFIG["database"] == "TEST_DB"
        assert core.SF_CONFIG["schema"] == "TEST_SCHEMA"
        assert core.SF_CONFIG["warehouse"] == "TEST_WH"

    def test_db_and_schema_derived(self, core):
        assert core.DB == "TEST_DB"
        assert core.SCHEMA == "TEST_SCHEMA"


# ===================== _strip_sql_comments =====================

class TestStripSqlComments:
    def test_no_comments(self, core):
        assert core._strip_sql_comments("SELECT 1") == "SELECT 1"

    def test_single_line_comment(self, core):
        assert core._strip_sql_comments("-- comment\nSELECT 1") == "SELECT 1"

    def test_multiple_line_comments(self, core):
        result = core._strip_sql_comments("-- a\n-- b\nSELECT 1")
        assert result == "SELECT 1"

    def test_block_comment(self, core):
        assert core._strip_sql_comments("/* comment */ SELECT 1") == "SELECT 1"

    def test_nested_block_comment_partial(self, core):
        """Block comment without closing still strips prefix."""
        result = core._strip_sql_comments("/* unclosed comment")
        assert result == "/* unclosed comment"

    def test_whitespace_only(self, core):
        assert core._strip_sql_comments("   ") == ""

    def test_empty_string(self, core):
        assert core._strip_sql_comments("") == ""

    def test_mixed_comments(self, core):
        sql = "-- line\n/* block */ SELECT 1"
        result = core._strip_sql_comments(sql)
        assert result == "SELECT 1"


# ===================== is_safe_sql =====================

class TestIsSafeSql:
    def test_select(self, core):
        assert core.is_safe_sql("SELECT * FROM t") is True

    def test_with_cte(self, core):
        assert core.is_safe_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") is True

    def test_trailing_semicolons(self, core):
        assert core.is_safe_sql("SELECT 1;") is True
        assert core.is_safe_sql("SELECT 1;;;") is True

    def test_drop_blocked(self, core):
        assert core.is_safe_sql("DROP TABLE t") is False

    def test_delete_blocked(self, core):
        assert core.is_safe_sql("DELETE FROM t") is False

    def test_insert_blocked(self, core):
        assert core.is_safe_sql("INSERT INTO t VALUES (1)") is False

    def test_update_blocked(self, core):
        assert core.is_safe_sql("UPDATE t SET x=1") is False

    def test_alter_blocked(self, core):
        assert core.is_safe_sql("ALTER TABLE t ADD col INT") is False

    def test_create_blocked(self, core):
        assert core.is_safe_sql("CREATE TABLE t (id INT)") is False

    def test_truncate_blocked(self, core):
        assert core.is_safe_sql("TRUNCATE TABLE t") is False

    def test_replace_blocked(self, core):
        assert core.is_safe_sql("REPLACE INTO t VALUES (1)") is False

    def test_merge_blocked(self, core):
        assert core.is_safe_sql("MERGE INTO t USING s ON t.id=s.id") is False

    def test_grant_blocked(self, core):
        assert core.is_safe_sql("GRANT ALL ON db TO user") is False

    def test_revoke_blocked(self, core):
        assert core.is_safe_sql("REVOKE ALL ON db FROM user") is False

    def test_exec_blocked(self, core):
        assert core.is_safe_sql("EXEC sp_helpdb") is False

    def test_execute_blocked(self, core):
        assert core.is_safe_sql("EXECUTE sp_helpdb") is False

    def test_select_with_embedded_drop(self, core):
        assert core.is_safe_sql("SELECT 1; DROP TABLE t") is False

    def test_select_subquery_with_dangerous_keyword_in_name(self, core):
        """A column alias or string containing 'update' should still be caught."""
        assert core.is_safe_sql("SELECT 'update' FROM t") is False

    def test_empty(self, core):
        assert core.is_safe_sql("") is False

    def test_whitespace_only(self, core):
        assert core.is_safe_sql("   ") is False

    def test_show_tables(self, core):
        assert core.is_safe_sql("SHOW TABLES") is False

    def test_comment_hiding_drop(self, core):
        assert core.is_safe_sql("-- sneaky\nDROP TABLE t") is False

    def test_block_comment_hiding_drop(self, core):
        assert core.is_safe_sql("/* sneaky */ DROP TABLE t") is False

    def test_case_insensitive(self, core):
        assert core.is_safe_sql("select col from t") is True
        assert core.is_safe_sql("Select Col From T") is True


# ===================== is_off_topic =====================

class TestIsOffTopic:
    @pytest.mark.parametrize("text", [
        "What is the population of California?",
        "Which states have the longest commutes?",
        "Where do people spend over 30% on rent?",
        "Tell me about migration patterns",
        "",
    ])
    def test_on_topic(self, core, text):
        assert core.is_off_topic(text) is False

    @pytest.mark.parametrize("text", [
        "tell me about porn",
        "nude photos",
        "sex trafficking data",
        "kill someone",
        "how to build a bomb",
        "hack into the database",
        "crack the password",
        "drug cartel locations",
        "drugs usage stats",
        "weapon manufacturing",
        "weapons data",
        "suicide methods",
    ])
    def test_off_topic(self, core, text):
        assert core.is_off_topic(text) is True

    def test_case_insensitive(self, core):
        assert core.is_off_topic("DRUGS") is True
        assert core.is_off_topic("Bomb") is True


# ===================== extract_sql =====================

class TestExtractSql:
    def test_single_block(self, core):
        text = "Here:\n```sql\nSELECT 1\n```\nDone."
        result = core.extract_sql(text)
        assert len(result) == 1
        assert "SELECT 1" in result[0]

    def test_multiple_blocks(self, core):
        text = "A:\n```sql\nSELECT a\n```\nB:\n```sql\nSELECT b\n```"
        result = core.extract_sql(text)
        assert len(result) == 2

    def test_no_sql(self, core):
        assert core.extract_sql("No SQL here.") == []

    def test_python_block_ignored(self, core):
        assert core.extract_sql("```python\nprint(1)\n```") == []

    def test_empty_sql_block(self, core):
        result = core.extract_sql("```sql\n```")
        assert len(result) == 1

    def test_multiline_sql(self, core):
        text = "```sql\nSELECT\n  a,\n  b\nFROM t\n```"
        result = core.extract_sql(text)
        assert len(result) == 1
        assert "FROM t" in result[0]

    def test_sql_with_backticks_inside(self, core):
        """SQL that has inner content but no nested triple-backtick."""
        text = '```sql\nSELECT "col" FROM t WHERE x = \'abc\'\n```'
        result = core.extract_sql(text)
        assert len(result) == 1


# ===================== SCHEMA_CONTEXT / SYSTEM_PROMPT =====================

class TestSchemaContextAndPrompt:
    def test_schema_context_has_db_and_schema(self, core):
        assert "TEST_DB" in core.SCHEMA_CONTEXT
        assert "TEST_SCHEMA" in core.SCHEMA_CONTEXT

    def test_schema_context_has_key_tables(self, core):
        for table in [
            "2019_METADATA_CBG_FIELD_DESCRIPTIONS",
            "2019_METADATA_CBG_FIPS_CODES",
            "2019_METADATA_CBG_GEOGRAPHIC_DATA",
            "2019_RENT_PERCENTAGE_HOUSEHOLD_INCOME",
            "2019_CBG_B01",
            "2019_CBG_B07",
            "2019_CBG_B08",
            "2019_CBG_B16",
            "2019_CBG_B19",
            "2019_CBG_B25",
            "2019_CBG_PATTERNS",
        ]:
            assert table in core.SCHEMA_CONTEXT, f"{table} not in SCHEMA_CONTEXT"

    def test_schema_context_has_quoting_rules(self, core):
        assert "double quotes" in core.SCHEMA_CONTEXT.lower() or 'double-quote' in core.SCHEMA_CONTEXT.lower()

    def test_system_prompt_embeds_schema_context(self, core):
        assert core.SCHEMA_CONTEXT in core.SYSTEM_PROMPT

    def test_system_prompt_has_rules(self, core):
        assert "ONLY answer questions related to US Census" in core.SYSTEM_PROMPT
        assert "NEVER generate SQL that modifies data" in core.SYSTEM_PROMPT


# ===================== get_openai_client =====================

class TestGetOpenaiClient:
    def test_returns_openai_client(self, core):
        with patch("core.OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = core.get_openai_client()
            mock_cls.assert_called_once_with(api_key=core.OPENAI_API_KEY)
            assert client is mock_cls.return_value


# ===================== chat_with_llm =====================

class TestChatWithLlm:
    def test_calls_responses_api(self, core):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = "Answer."
        mock_client.responses.create.return_value = mock_response

        with patch.object(core, "get_openai_client", return_value=mock_client):
            msgs = [{"role": "user", "content": "hi"}]
            result = core.chat_with_llm(msgs)

        assert result == "Answer."
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["model"] == "gpt-5.2"
        assert call_kwargs["instructions"] == core.SYSTEM_PROMPT
        assert call_kwargs["input"] == msgs

    def test_propagates_exception(self, core):
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = RuntimeError("API down")

        with patch.object(core, "get_openai_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                core.chat_with_llm([{"role": "user", "content": "hi"}])


# ===================== run_query =====================

class TestRunQuery:
    def test_returns_list_of_dicts(self, core):
        mock_cursor = MagicMock()
        mock_cursor.description = [("STATE",), ("POP",)]
        mock_cursor.fetchmany.return_value = [("CA", 39_000_000), ("TX", 29_000_000)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = core.run_query("SELECT state, pop FROM t", mock_conn)
        assert result == [
            {"STATE": "CA", "POP": 39_000_000},
            {"STATE": "TX", "POP": 29_000_000},
        ]

    def test_returns_error_on_exception(self, core):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("Connection lost")

        result = core.run_query("SELECT 1", mock_conn)
        assert isinstance(result, dict)
        assert "error" in result
        assert "Connection lost" in result["error"]

    def test_empty_result(self, core):
        mock_cursor = MagicMock()
        mock_cursor.description = [("COL",)]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        result = core.run_query("SELECT 1 WHERE FALSE", mock_conn)
        assert result == []

    def test_max_rows_passed_to_fetchmany(self, core):
        mock_cursor = MagicMock()
        mock_cursor.description = [("X",)]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        core.run_query("SELECT 1", mock_conn, max_rows=10)
        mock_cursor.fetchmany.assert_called_once_with(10)

    def test_default_max_rows_is_500(self, core):
        mock_cursor = MagicMock()
        mock_cursor.description = [("X",)]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        core.run_query("SELECT 1", mock_conn)
        mock_cursor.fetchmany.assert_called_once_with(500)

    def test_conn_is_required_param(self, core):
        """run_query requires a conn argument (unlike the old app.run_query)."""
        with pytest.raises(TypeError):
            core.run_query("SELECT 1")
