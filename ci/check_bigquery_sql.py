"""Static check of the project compiled for BigQuery (no connection, nothing executed).

1. every compiled model, test and analysis parses with sqlglot's BigQuery dialect;
2. no DATETIME arithmetic leaks into a comparison: datetime_add / datetime_sub are forbidden
   (dbt.dateadd produces them; use ts_add_days / date_add_days from macros/cross_db.sql);
3. no DuckDB-only function survived the dispatch.
"""
import glob
import re
import sys

import sqlglot

FORBIDDEN = [r"\bdatetime_add\b", r"\bdatetime_sub\b", r"\bgenerate_series\b", r"\btimezone\s*\(",
             r"\btimestamptz\b", r"\bto_days\b", r"\bto_hours\b", r"'g'\s*\)"]
files = sorted(glob.glob("target/bigquery/compiled/**/*.sql", recursive=True))
problems = []
for f in files:
    sql = open(f).read()
    try:
        sqlglot.parse(sql, read="bigquery")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{f}: does not parse as BigQuery: {str(exc)[:160]}")
    for pat in FORBIDDEN:
        if re.search(pat, sql, re.I):
            problems.append(f"{f}: contains {pat}")
if not files:
    problems.append("nothing compiled")
for p in problems:
    print(p)
print(f"{len(files)} compiled BigQuery files checked, {len(problems)} problems")
sys.exit(1 if problems else 0)
