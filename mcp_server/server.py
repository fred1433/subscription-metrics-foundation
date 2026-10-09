"""Governed metrics over MCP (stdio).

What an agent can do here:
  list_metrics   the registry: id, version, one-sentence definition, unit, allowed slices, status
  query_metric   one implemented metric, a month range, one allowed slice

What it cannot do:
  run SQL, name a table or a column, see a row about a person, see a cell below the minimum group size,
  or get a number from a source that is stale at the cutoff.

Disclosure guardrails (this file): aggregates only, allow-listed slices, minimum group size, bounded ranges,
read-only connection, call log. Access control (who may call at all) belongs to the deployment: in BigQuery,
a service account that can only read the metrics dataset. Not implemented here.

The server computes nothing. Values come from main_metrics.metric_values, built by dbt. The only combination
it performs is the registry's own rule for a range total, executed in SQL: sum of numerators over sum of
denominators for ratios, a sum for sums, none for point-in-time metrics.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import duckdb
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DB_PATH = os.environ.get("METRICS_DB", "warehouse/defective.duckdb")
CALL_LOG = os.environ.get("MCP_CALL_LOG", "logs/mcp_calls.jsonl")
MAX_MONTHS = 24
MAX_ROWS = 150
MONTH = re.compile(r"^(20\d\d)-(0[1-9]|1[0-2])$")
METRIC_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# Fixed SQL. Values are always bound parameters; no identifier ever comes from the caller.
SQL_REGISTRY = """
    select r.metric_id, r.version, r.status, r.label, r.definition, r.grain, r.numerator, r.denominator, r.unit,
           r.aggregation, r.allowed_slices_json, r.min_group_size, r.window_days, r.maturity_policy,
           r.source_dependencies_json, r.missing_inputs_json,
           q.availability, q.stale_sources, q.open_reconciliation_exceptions, q.checked_at_utc
    from main_metrics.metric_registry as r
    left join main_metrics.metric_quality as q on q.metric_id = r.metric_id
"""
SQL_ROWS = """
    select cast(period as varchar), slice_value,
           case when group_size >= ? then numerator end,
           case when group_size >= ? then denominator end,
           case when group_size >= ? then value end,
           group_size >= ? as published,
           case when pending_count >= ? then pending_count end
    from main_metrics.metric_values
    where metric_id = ? and slice_name = ? and period between ? and ?
    order by period, slice_value
    limit ?
"""
SQL_TOTAL = {
    "ratio_of_sums": """
        select sum(numerator), sum(denominator), 1.0 * sum(numerator) / nullif(sum(denominator), 0),
               sum(group_size), sum(pending_count)
        from main_metrics.metric_values
        where metric_id = ? and slice_name = 'total' and period between ? and ?""",
    "sum": """
        select sum(numerator), null, 1.0 * sum(numerator), sum(group_size), null
        from main_metrics.metric_values
        where metric_id = ? and slice_name = 'total' and period between ? and ?""",
}

server = MCPServer(
    "governed-subscription-metrics",
    instructions=(
        "Governed subscription metrics built by dbt. Call list_metrics first, then query_metric with a metric id, "
        "a month range (YYYY-MM) and one allowed slice. Never estimate a metric yourself: if a metric is "
        "unavailable or a contract only, say so and quote the reason."
    ),
)


def _log(tool: str, args: dict, outcome: str, detail: str = "", rows: int = 0) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(CALL_LOG)), exist_ok=True)
    entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "tool": tool, "args": args,
             "outcome": outcome, "detail": detail, "rows": rows}
    with open(CALL_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _refuse(tool: str, args: dict, reason: str):
    _log(tool, args, "refused", reason)
    raise ToolError(reason)


def _registry() -> dict[str, dict]:
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        cols = ["metric_id", "version", "status", "label", "definition", "grain", "numerator", "denominator", "unit",
                "aggregation", "allowed_slices", "min_group_size", "window_days", "maturity_policy",
                "source_dependencies", "missing_inputs", "availability", "stale_sources",
                "open_reconciliation_exceptions", "checked_at_utc"]
        out = {}
        for row in con.execute(SQL_REGISTRY).fetchall():
            d = dict(zip(cols, row))
            for k in ("allowed_slices", "source_dependencies", "missing_inputs"):
                d[k] = json.loads(d[k] or "[]")
            d["checked_at_utc"] = str(d["checked_at_utc"])
            out[d["metric_id"]] = d
        return out
    finally:
        con.close()


@server.tool()
def list_metrics() -> dict:
    """List every governed metric: id, version, one-sentence definition, unit, grain, allowed slices,
    minimum group size, status (implemented or contract) and availability at the cutoff."""
    reg = _registry()
    metrics = []
    for m in reg.values():
        item = {k: m[k] for k in ("metric_id", "version", "status", "label", "definition", "unit", "grain",
                                  "allowed_slices", "min_group_size", "window_days", "maturity_policy",
                                  "availability")}
        if m["status"] != "implemented":
            item["missing_inputs"] = m["missing_inputs"]
        metrics.append(item)
    _log("list_metrics", {}, "ok", rows=len(metrics))
    return {"cutoff_utc": next(iter(reg.values()))["checked_at_utc"] if reg else None, "metrics": metrics}


@server.tool()
def query_metric(metric_id: str, start_month: str, end_month: str, slice_by: str = "total") -> dict:
    """Return one implemented metric per month between start_month and end_month (YYYY-MM, at most 24 months),
    split by one allowed slice ('total' for no split). Cells below the metric's minimum group size are
    suppressed. Every answer carries the definition version, the window, the cutoff and the quality status."""
    args = {"metric_id": metric_id, "start_month": start_month, "end_month": end_month, "slice_by": slice_by}
    if not isinstance(metric_id, str) or not METRIC_ID.match(metric_id):
        _refuse("query_metric", args, "metric_id must be a registry id such as 'trial_conversion_rate'")
    reg = _registry()
    m = reg.get(metric_id)
    if m is None:
        _refuse("query_metric", args, f"unknown metric '{metric_id}'. Known: {', '.join(sorted(reg))}")
    if m["status"] != "implemented":
        _refuse("query_metric", args,
                f"'{metric_id}' is a contract only, not implemented: missing inputs: {'; '.join(m['missing_inputs'])}")
    if slice_by not in m["allowed_slices"]:
        _refuse("query_metric", args,
                f"slice '{slice_by}' is not allowed for '{metric_id}'. Allowed: {', '.join(m['allowed_slices'])}")
    for v in (start_month, end_month):
        if not isinstance(v, str) or not MONTH.match(v):
            _refuse("query_metric", args, "months must be written YYYY-MM")
    start = dt.date(int(start_month[:4]), int(start_month[5:]), 1)
    end = dt.date(int(end_month[:4]), int(end_month[5:]), 1)
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if months < 1:
        _refuse("query_metric", args, "start_month must not be after end_month")
    if months > MAX_MONTHS:
        _refuse("query_metric", args, f"at most {MAX_MONTHS} months per call")

    base = {
        "metric_id": metric_id,
        "definition_version": m["version"],
        "definition": m["definition"],
        "unit": m["unit"],
        "grain": m["grain"],
        "slice_by": slice_by,
        "window": {"start_month": start_month, "end_month": end_month, "conversion_window_days": m["window_days"],
                   "maturity_policy": m["maturity_policy"]},
        "cutoff_utc": m["checked_at_utc"],
        "quality": {"availability": m["availability"], "stale_sources": m["stale_sources"],
                    "open_reconciliation_exceptions": m["open_reconciliation_exceptions"],
                    "min_group_size": m["min_group_size"]},
    }
    if m["availability"] != "available":
        _log("query_metric", args, "unavailable", m["stale_sources"] or "")
        return {**base, "status": "unavailable",
                "reason": f"a source this metric depends on is stale at the cutoff: {m['stale_sources']}"}

    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        g = m["min_group_size"]
        rows = con.execute(SQL_ROWS, [g, g, g, g, g, metric_id, slice_by, start, end, MAX_ROWS + 1]).fetchall()
        if len(rows) > MAX_ROWS:
            _refuse("query_metric", args, f"more than {MAX_ROWS} rows; narrow the range")
        total = None
        if m["aggregation"] in SQL_TOTAL:
            t = con.execute(SQL_TOTAL[m["aggregation"]], [metric_id, start, end]).fetchone()
            published = t[3] is not None and t[3] >= g
            total = {"numerator": t[0] if published else None, "denominator": t[1] if published else None,
                     "value": t[2] if published else None, "published": published,
                     "pending": t[4], "rule": m["aggregation"]}
    finally:
        con.close()
    out_rows = [{"period": r[0], "slice_value": r[1], "numerator": r[2], "denominator": r[3], "value": r[4],
                 "published": r[5], "pending": r[6]} for r in rows]
    _log("query_metric", args, "ok", rows=len(out_rows))
    return {**base, "status": "ok", "rows": out_rows, "range_total": total}


if __name__ == "__main__":
    server.run("stdio")
