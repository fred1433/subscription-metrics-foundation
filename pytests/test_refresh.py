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
    con.execute("""insert into platform.orders select * replace ('PO-HEARTBEAT' as platform_order_id,
                   '2026-10-02T09:10:00Z' as _ingested_at)
                   from platform.orders where platform_order_id = 'PO-F10-S'""")
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
    assert fresh == {"platform_events": "fresh", "platform_orders": "fresh", "shopify_orders": "fresh",
                     "shopify_refunds": "fresh", "ads_spend": "stale"}
    assert q["ad_spend_per_trial"] == "unavailable"
    assert q["trial_conversion_rate"] != "unavailable" and q["net_revenue"] != "unavailable"

    _, [(err1, ads), (err2, conv)] = run_session(db, str(tmp_path / "log.jsonl"), [
        ("query_metric", {"metric_id": "ad_spend_per_trial", "start_month": "2026-01", "end_month": "2026-09"}),
        ("query_metric", {"metric_id": "trial_conversion_rate", "start_month": "2026-01", "end_month": "2026-06"}),
    ])
    assert not err1 and ads["status"] == "unavailable" and "ads_spend last synced" in ads["reason"]
    assert "rows" not in ads
    assert not err2 and conv["status"] in ("ok", "degraded") and conv["cutoff_utc"].startswith("2026-10-02 12:00")


def _copy(defective_db, tmp_path):
    db = str(tmp_path / "work.duckdb")
    shutil.copy(defective_db, db)
    return db


def _sept_revenue(db):
    con = duckdb.connect(db, read_only=True)
    v = con.execute("""select numerator from main_metrics.metric_values where metric_id = 'net_revenue'
                       and slice_name = 'total' and period = '2026-09-01'""").fetchone()[0]
    con.close()
    return v


def test_known_at_cutoff_respects_ingestion_time(root, defective_db, tmp_path):
    """A 5 GBP refund paid 30/09 20:00 UTC but ingested 01/10 10:00 UTC does not exist at a 30/09 21:00 cutoff,
    lowers September by exactly 5 GBP once the cutoff is after its ingestion, and a replay changes nothing.
    A later version of an order synced after the cutoff leaves the earlier admissible version in place."""
    db, target = _copy(defective_db, tmp_path), str(tmp_path / "t")
    dbt_run(root, db, target, T0)
    base = _sept_revenue(db)
    con = duckdb.connect(db)
    oid, total = con.execute("select id, total_price from shopify.\"order\" where source_identifier = 'PO-F1-B10'").fetchone()
    con.execute("insert into shopify.refund values ('7999101', ?, '2026-09-30T20:00:00Z', 'late claim', "
                "'2026-10-01T10:00:00Z')", [oid])
    con.execute("insert into shopify.\"transaction\" values ('3999101', ?, '7999101', 'refund', 'success', '5.00', "
                "'2026-09-30T20:00:00Z', '2026-10-01T10:00:00Z')", [oid])
    con.execute("""insert into shopify."order" select * replace ('99.99' as total_price, '2026-10-01T09:00:00Z' as _weld_synced)
                   from shopify."order" where id = ?""", [oid])
    con.close()
    dbt_run(root, db, target, T0)
    assert _sept_revenue(db) == base, "a refund ingested after the cutoff was used at the cutoff"
    con = duckdb.connect(db, read_only=True)
    seen_total = con.execute("select total_pence from main_staging.stg_shopify__orders where shopify_order_id = ?",
                             [int(oid)]).fetchone()[0]
    con.close()
    assert seen_total == round(float(total) * 100), "the version synced after the cutoff replaced the admissible one"
    late = "2026-10-01 12:00:00"
    dbt_run(root, db, target, late)
    after = _sept_revenue(db)
    assert after == base - 500
    con = duckdb.connect(db)
    con.execute("insert into shopify.\"transaction\" select * replace ('2026-10-01T11:00:00Z' as _weld_synced) "
                "from shopify.\"transaction\" where id = '3999101'")
    con.close()
    dbt_run(root, db, target, late)
    assert _sept_revenue(db) == after, "the replayed refund was counted twice"


def test_each_feed_has_its_own_freshness_marker(root, defective_db, tmp_path):
    """Events stop while orders, refunds and ads keep arriving: subscription-state metrics are blocked."""
    db, target = _copy(defective_db, tmp_path), str(tmp_path / "t")
    con = duckdb.connect(db)
    con.execute("""insert into platform.orders select * replace ('PO-LATER' as platform_order_id,
                   '2026-10-01T20:00:00Z' as _ingested_at) from platform.orders where platform_order_id = 'PO-F10-S'""")
    con.execute("""insert into shopify."order" select * replace ('5999998' as id, '#U9998' as name,
                   '2026-10-01T19:00:00Z' as created_at, '2026-10-01T20:00:00Z' as _weld_synced)
                   from shopify."order" where customer_id = '70081' order by created_at desc limit 1""")
    con.execute("""insert into shopify."transaction" select * replace ('2026-10-01T20:00:00Z' as _weld_synced)
                   from shopify."transaction" where kind = 'refund' order by created_at desc limit 1""")
    con.execute("""insert into ads.daily_spend select * replace ('2026-09-30' as date, '2026-10-01T03:00:00Z' as _synced_at)
                   from ads.daily_spend where date = '2026-09-29'""")
    con.close()
    dbt_run(root, db, target, "2026-10-01 21:00:00")
    con = duckdb.connect(db, read_only=True)
    fresh = dict(con.execute("select source_id, freshness from main_metrics.source_freshness").fetchall())
    q = dict(con.execute("select metric_id, availability from main_metrics.metric_quality").fetchall())
    con.close()
    assert fresh["platform_events"] == "stale" and fresh["platform_orders"] == "fresh"
    assert fresh["shopify_orders"] == "fresh" and fresh["ads_spend"] == "fresh"
    assert q["active_subscriptions"] == "unavailable" and q["trial_conversion_rate"] == "unavailable"
    assert q["net_revenue"] != "unavailable"


def test_bridge_is_order_by_order_and_failed_reversal_stays_an_exception(root, defective_db, tmp_path):
    """+1.00 on one trial's lines and -1.00 on another's: two exceptions, the check fails although the signed
    sum is zero. A card-check reversal whose transaction failed is not a reversal."""
    db, target = _copy(defective_db, tmp_path), str(tmp_path / "t")
    con = duckdb.connect(db)
    for po, delta in [("PO-00001-T", 1.0), ("PO-00002-T", -1.0)]:
        con.execute("""update shopify.order_line_items set price = cast(cast(price as double) + ? as varchar)
                       where order_id = (select id from shopify."order" where source_identifier = ?)""", [delta, po])
    con.execute("update platform.payment_transactions set status = 'failed' where kind = 'card_check_reversal' "
                "and account_id = 'ACC-J'")
    con.close()
    dbt_run(root, db, target, T0)
    con = duckdb.connect(db, read_only=True)
    bridge = sorted(con.execute("select item_key, residual_pence from main_reconciliation.rec_order_bridge").fetchall())
    summary = con.execute("""select unexplained, unexplained_gross, exceptions, status from main_reconciliation.rec_summary
                             where check_name = 'lines_to_order_total'""").fetchone()
    card = con.execute("""select category, classification from main_reconciliation.rec_exceptions
                          where item_key = 'ACC-J:card_check:2026-06-07'""").fetchone()
    con.close()
    assert bridge == [("PO-00001-T", 100), ("PO-00002-T", -100)]
    assert summary == (0, 200, 2, "exceptions")
    assert card == ("card_check_not_reversed", "exception")
