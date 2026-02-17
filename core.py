"""Shared pure logic for Census Chat — no framework dependencies (Streamlit/Flask)."""

import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


# --- Config ---
def get_secret(key):
    """Read from environment variables."""
    return os.getenv(key)


SF_CONFIG = {
    "account": get_secret("SNOWFLAKE_ACCOUNT"),
    "user": get_secret("SNOWFLAKE_USER"),
    "password": get_secret("SNOWFLAKE_PASSWORD"),
    "database": get_secret("SNOWFLAKE_DATABASE"),
    "schema": get_secret("SNOWFLAKE_SCHEMA"),
    "warehouse": get_secret("SNOWFLAKE_WAREHOUSE"),
}
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
DB = SF_CONFIG["database"]
SCHEMA = SF_CONFIG["schema"]


# --- SQL safety ---
DANGEROUS_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _strip_sql_comments(sql):
    """Remove leading -- line comments and /* block comments */ so we can inspect the first keyword."""
    s = sql.strip()
    while s.startswith("--"):
        s = s.split("\n", 1)[-1].strip()
    while s.startswith("/*"):
        end = s.find("*/")
        if end == -1:
            break
        s = s[end + 2 :].strip()
    return s


def is_safe_sql(sql):
    """Only allow SELECT / WITH ... SELECT statements."""
    stripped = _strip_sql_comments(sql).rstrip(";").strip()
    first_word = stripped.split()[0].upper() if stripped.split() else ""
    if first_word not in ("SELECT", "WITH"):
        return False
    if DANGEROUS_KEYWORDS.search(stripped):
        return False
    return True


# --- Guardrails ---
OFF_TOPIC_PATTERNS = re.compile(
    r"\b(porn|nude|sex|kill|bomb|hack|crack|drugs?|weapons?|suicide)\b",
    re.IGNORECASE,
)


def is_off_topic(text):
    return bool(OFF_TOPIC_PATTERNS.search(text))


# --- Schema context for the LLM ---
SCHEMA_CONTEXT = f"""
You have access to a Snowflake database with US Census data (American Community Survey 2019).

Database: {DB}
Schema: {SCHEMA}

=== KEY TABLES ===

1. **2019_METADATA_CBG_FIELD_DESCRIPTIONS**
   Maps coded column names (like B08303e1) to human-readable descriptions.
   Columns: TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_TOPICS, TABLE_UNIVERSE,
            FIELD_LEVEL_1 through FIELD_LEVEL_10
   - TABLE_ID is the column name used in data tables (e.g. "B08303e1")
   - FIELD_LEVEL_1, FIELD_LEVEL_2, etc. describe the hierarchy of what the field measures

2. **2019_METADATA_CBG_FIPS_CODES**
   Maps FIPS codes to state and county names. This is a COUNTY-level table (one row per county).
   Columns: STATE (text name), STATE_FIPS (2-digit code), COUNTY_FIPS (3-digit county code within state), COUNTY (text name), CLASS_CODE
   - IMPORTANT: COUNTY_FIPS is only 3 digits (e.g. '001'), NOT 5 digits.
   - To match to CBG data: LEFT(CENSUS_BLOCK_GROUP, 2) = STATE_FIPS AND SUBSTRING(CENSUS_BLOCK_GROUP, 3, 3) = COUNTY_FIPS
   - Or equivalently: STATE_FIPS || COUNTY_FIPS = LEFT(CENSUS_BLOCK_GROUP, 5)
   - For state-level joins, just use: SELECT DISTINCT STATE_FIPS, STATE FROM this table

3. **2019_METADATA_CBG_GEOGRAPHIC_DATA**
   Lat/long for each census block group.
   Columns: CENSUS_BLOCK_GROUP, AMOUNT_LAND, AMOUNT_WATER, LATITUDE, LONGITUDE

4. **2019_RENT_PERCENTAGE_HOUSEHOLD_INCOME** (pre-computed, human-readable columns!)
   Columns: CENSUS_BLOCK_GROUP,
   "Total: Renter-occupied housing units",
   "50.0 percent or more: Renter-occupied housing units",
   "Not computed: Renter-occupied housing units",
   "Less than 10.0 percent: Renter-occupied housing units",
   "10.0 to 14.9 percent: Renter-occupied housing units",
   "15.0 to 19.9 percent: Renter-occupied housing units",
   "20.0 to 24.9 percent: Renter-occupied housing units",
   "25.0 to 29.9 percent: Renter-occupied housing units",
   "30.0 to 34.9 percent: Renter-occupied housing units",
   "35.0 to 39.9 percent: Renter-occupied housing units",
   "40.0 to 49.9 percent: Renter-occupied housing units"

5. **2019_CBG_B01** - Sex and Age (population demographics)
6. **2019_CBG_B07** - Geographical Mobility / Migration (B07201, B07202, B07203)
7. **2019_CBG_B08** - Commuting / Transportation (B08303=travel time, B08301=means of transport, B08134, B08135, B08136)
8. **2019_CBG_B16** - Language Spoken at Home (B16004)
9. **2019_CBG_B19** - Income
10. **2019_CBG_B25** - Housing Characteristics
11. **2019_CBG_PATTERNS** - Visitor patterns (RAW_VISIT_COUNT, RAW_VISITOR_COUNT, etc.)

=== CRITICAL: COLUMN QUOTING RULES ===

**ALL coded column names (like B08303e1, B07201e1, B16004e1, etc.) are CASE-SENSITIVE in this Snowflake database and MUST be wrapped in double quotes.**

CORRECT:   SELECT "B08135e1", "B08303e1" FROM ...
WRONG:     SELECT B08135e1, B08303e1 FROM ...  (This will error!)

The lowercase "e" and "m" suffixes are part of the stored name. Without double quotes, Snowflake uppercases identifiers and the query will fail with "invalid identifier".

- Metadata table columns (TABLE_ID, STATE, STATE_FIPS, COUNTY, etc.) are uppercase and do NOT need quoting.
- Rent table columns have spaces and MUST be quoted: "Total: Renter-occupied housing units"
- CENSUS_BLOCK_GROUP is uppercase and does NOT need quoting.

=== HOW THE DATA IS STRUCTURED ===

- Each data table has a CENSUS_BLOCK_GROUP column (12-digit FIPS code).
  - First 2 digits = state FIPS code
  - Next 3 digits = county FIPS code
  - Next 6 digits = census tract
  - Last 1 digit = block group
- Column names like B08303e1 mean: table B08303, "e" = estimate, "m" = margin of error, number = field position.
- To find what a column means, query 2019_METADATA_CBG_FIELD_DESCRIPTIONS WHERE TABLE_ID = 'B08303e1'.
- To get state names, join with 2019_METADATA_CBG_FIPS_CODES using LEFT(CENSUS_BLOCK_GROUP, 2) = STATE_FIPS (use SELECT DISTINCT STATE_FIPS, STATE).
- To get county names, join using STATE_FIPS || COUNTY_FIPS = LEFT(CENSUS_BLOCK_GROUP, 5). COUNTY_FIPS is 3 digits, NOT 5.

=== IMPORTANT QUERY PATTERNS ===

- ALWAYS double-quote coded column names: "B08135e1", "B08303e1", "B07201e1", etc.
- Column names with spaces MUST be quoted with double quotes: "30.0 to 34.9 percent: Renter-occupied housing units"
- ALL table names start with a number and MUST be double-quoted. Fully qualify them as:
  {DB}.{SCHEMA}."2019_CBG_B08"
  {DB}.{SCHEMA}."2019_CBG_B07"
  {DB}.{SCHEMA}."2019_CBG_B16"
  {DB}.{SCHEMA}."2019_METADATA_CBG_FIPS_CODES"
  {DB}.{SCHEMA}."2019_METADATA_CBG_FIELD_DESCRIPTIONS"
  {DB}.{SCHEMA}."2019_RENT_PERCENTAGE_HOUSEHOLD_INCOME"
  Without the double quotes around the table name, Snowflake will error with 'unexpected .2019'.
- To aggregate by state: GROUP BY LEFT(cbg.CENSUS_BLOCK_GROUP, 2), then join to FIPS codes.
- When the user asks about "over 30% of income on rent", sum the columns for 30-34.9%, 35-39.9%, 40-49.9%, and 50%+ from the rent table.
- For commute time: avg commute = SUM("B08135e1") / SUM("B08303e1"). IMPORTANT: many CBGs have NULL "B08135e1", so always filter WHERE "B08135e1" IS NOT NULL to avoid deflating the average.
- For migration, use COALESCE to handle NULLs: COALESCE("B07201e4",0) + COALESCE("B07201e5",0) + COALESCE("B07201e6",0) for people who moved in.
- For language, "B16004e1" = total population 5+, and subsequent fields break down by language and English proficiency.
- GENERAL: many numeric columns can be NULL. Use COALESCE(..., 0) when summing multiple columns in an expression, or filter with IS NOT NULL.

=== COMMON ACS TABLE REFERENCES (remember to double-quote all coded column names!) ===

Key B08 (Commuting) fields — use "B08303e1" not B08303e1:
- "B08303e1": Total workers 16+ (travel time universe)
- "B08135e1": Aggregate travel time to work (minutes) for all workers
- "B08301e1": Total workers (means of transportation universe)

Key B07 (Migration) fields:
- "B07201e1": Total population 1 year and over
- "B07201e2": Same house 1 year ago
- "B07201e3": Moved within same county
- "B07201e4": Moved from different county, same state
- "B07201e5": Moved from different state
- "B07201e6": Moved from abroad

Key B16 (Language) fields:
- "B16004e1": Total population 5 years and over
- "B16004e2": 5-17 years total
- "B16004e3": 5-17, speak only English
- "B16004e4" through "B16004e20": 5-17 by language (Spanish, other Indo-European, Asian/Pacific, Other)
- "B16004e21": 18-64 years total
- "B16004e22": 18-64, speak only English
- "B16004e23" through "B16004e44": 18-64 by language
- "B16004e45": 65+ years total
- "B16004e46": 65+, speak only English
- "B16004e47" through "B16004e67": 65+ by language
- To get non-English speakers: total population minus English-only speakers = "B16004e1" - ("B16004e3" + "B16004e22" + "B16004e46")
"""

SYSTEM_PROMPT = f"""You are a helpful US Census data analyst. You answer questions about US population, demographics, housing, commuting, migration, and language data using the 2019 American Community Survey.

{SCHEMA_CONTEXT}

RULES:
1. ONLY answer questions related to US Census data, demographics, population, housing, commuting, migration, language, income, and related topics. For anything else, politely decline.
2. NEVER generate SQL that modifies data (no INSERT, UPDATE, DELETE, DROP, etc.). Only SELECT queries.
3. When you need data to answer a question, output a SQL query wrapped in ```sql ... ``` code blocks.
4. After receiving query results, summarize them in a clear, conversational way. Use specific numbers.
5. If you need to look up what a column code means, query the 2019_METADATA_CBG_FIELD_DESCRIPTIONS table first.
6. Keep queries efficient — use LIMIT, avoid SELECT *.
7. If a question is ambiguous, make reasonable assumptions and state them.
8. When referring to areas, always try to include state and/or county names (join to FIPS codes).
"""


# --- OpenAI client ---
def get_openai_client():
    return OpenAI(api_key=OPENAI_API_KEY)


def extract_sql(text):
    """Extract SQL from ```sql ... ``` code blocks."""
    pattern = r"```sql\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches


def chat_with_llm(messages):
    """Send messages to OpenAI using the Responses API."""
    client = get_openai_client()
    response = client.responses.create(
        model="gpt-5.2",
        instructions=SYSTEM_PROMPT,
        input=messages,
    )
    return response.output_text


def run_query(sql, conn, max_rows=500):
    """Execute a read-only SQL query and return results as list of dicts."""
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchmany(max_rows)
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        return {"error": str(e)}
