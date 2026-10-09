"""Refresh with a fixed clock, on a copy of the defective warehouse.

1. replay: the connector delivers the same records again -> no metric moves
2. late refund, delivered twice -> net revenue drops by exactly that refund, once
3. clock moves on, ads connector stops -> metrics that need ads become unavailable, others stay available
"""
import os
import shutil
import subprocess
import sys

import duckdb
import pytest

from mcp_client import run_session

T0 = "2026-09-30 21:00:00"
T1 = "2026-10-02 12:00:00"


def dbt_run(root, db, target, as_of):
    dbt = os.path.join(os.path.dirname(sys.executable), "dbt")
    cmd = [dbt, "run", "--profiles-dir", root, "--project-dir", root, "--target-path", target,
           "--vars", f"{{as_of: '{as_of}'}}", "--quiet"]
    res = subprocess.run(cmd, env={**os.environ, "DBT_DUCKDB_PATH": db}, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def snapshot(db):
    con = duckdb.connect(db, read_only=True)
    rows = con.execute("""select metric_id, cast(period as varchar), slice_name, slice_value, numerator, denominator
                          from main_metrics.metric_values order by 1, 2, 3, 4""").fetchall()
    con.close()
    return {r[:4]: r[4:] for r in rows}


@pytest.fixture(scope="module")
def work(root, defective_db, tmp_path_factory):
    d = tmp_path_factory.mktemp("refresh")
    db = str(d / "refresh.duckdb")
    shutil.copy(defective_db, db)
    return root, db, str(d / "target")


def test_refresh_sequence(work, tmp_path):
    root, db, target = work
    dbt_run(root, db, target, T0)
    s0 = snapshot(db)

    # 1. replay: every Shopify order, line and transaction of the last week arrives again, newer sync stamp
    con = duckdb.connect(db)
    for table, where in [
        ('shopify."order"', "created_at >= '2026-09-23'"),
        ("shopify.order_line_items", "order_id in (select id from shopify.\"order\" where created_at >= '2026-09-23')"),
        ("shopify.\"transaction\"", "created_at >= '2026-09-23'"),
    ]:
        con.execute(f"insert into {table} select * replace ('2026-09-30T20:10:00Z' as _weld_synced) "
                    f"from {table} where {where}")
    con.close()
    dbt_run(root, db, target, T0)
    assert snapshot(db) == s0, "a replayed batch changed a metric"

    # 2. a late refund on an August box, paid on 30/09, delivered twice by the connector
    con = duckdb.connect(db)
    order_id, line_id, price = con.execute("""
        select o.id, l.id, l.price from shopify."order" o join shopify.order_line_items l on l.order_id = o.id
        where o.source_identifier = 'PO-F1-B10' order by l.id limit 1""").fetchone()
    amount = round(float(price) * 100)
    for synced in ["2026-09-30T20:15:00Z", "2026-09-30T20:40:00Z"]:
        con.execute("insert into shopify.refund values ('7999001', ?, '2026-09-30T14:00:00Z', 'late damage claim', ?)",
                    [order_id, synced])
        con.execute("insert into shopify.order_line_refund values ('9999001', '7999001', ?, '1', ?, ?)",
                    [line_id, price, synced])
        con.execute("insert into shopify.\"transaction\" values ('3999001', ?, '7999001', 'refund', 'success', ?, "
                    "'2026-09-30T15:00:00Z', ?)", [order_id, price, synced])
    con.close()
    dbt_run(root, db, target, T0)
    s2 = snapshot(db)
    changed = {k for k in s0 if s0[k] != s2.get(k)}
    sept = ("net_revenue", "2026-09-01", "total", "all")
    assert sept in changed
    assert s2[sept][0] == s0[sept][0] - amount, "the late refund must apply exactly once"
    assert all(k[0] == "net_revenue" and k[1] == "2026-09-01" for k in changed)
    con = duckdb.connect(db, read_only=True)
    n = con.execute("select count(*) from main_marts.fct_revenue_movements "
                    "where movement_id = 'shopify_transaction:3999001'").fetchone()[0]
    con.close()
    assert n == 1

    # 3. the clock moves on; platform and Shopify keep syncing, the ads connector does not
    con = duckdb.connect(db)
    con.execute("""insert into shopify."order" select * replace ('5999999' as id, '#U9999' as name,
                   '2026-10-02T10:00:00Z' as created_at, '2026-10-02T10:00:00Z' as processed_at,
                   '2026-10-02T10:30:00Z' as _weld_synced)
                   from shopify."order" where customer_id = '70081' order by created_at desc limit 1""")
    con.execute("""insert into platform.subscription_events select * replace ('EV-9999999' as event_id,
                   'payment_method_updated' as event_type, '2026-10-02T09:00:00Z' as event_at,
                   '2026-10-02T09:05:00Z' as ingested_at, null as next_renewal_date, null as previous_renewal_date)
                   from platform.subscription_events where subscription_id = 'SUB-J' limit 1""")
    con.close()
    dbt_run(root, db, target, T1)
    con = duckdb.connect(db, read_only=True)
    q = dict(con.execute("select metric_id, availability from main_metrics.metric_quality").fetchall())
    fresh = dict(con.execute("select source_id, freshness from main_metrics.source_freshness").fetchall())
    con.close()
    assert fresh == {"platform": "fresh", "shopify": "fresh", "ads": "stale"}
    assert q["ad_spend_per_trial"] == "unavailable"
    assert q["trial_conversion_rate"] == "available" and q["net_revenue"] == "available"

    _, [(err1, ads), (err2, conv)] = run_session(db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "ad_spend_per_trial", "start_month": "2026-01", "end_month": "2026-09"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01", "end_month": "2026-06"}),
    ])
    assert not err1 and ads["status"] == "unavailable" and "ads last synced" in ads["reason"]
    assert "rows" not in ads
    assert not err2 and conv["status"] == "ok" and conv["cutoff_utc"].startswith("2026-10-02 12:00")
