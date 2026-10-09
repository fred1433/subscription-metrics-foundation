"""Deterministic synthetic data for the subscription metrics foundation.

Nothing here is real customer or company data. The shapes follow public descriptions of a UK
cat-food subscription flow (see README for every source link); volumes, prices and names are invented.

Scenarios
  clean      normal business mechanics only (trials, Change Date, "I need it now", pauses, payment
             retries, blank orders, 73p card checks, partial refunds, a free replacement, DST edges)
  defective  clean + named faults: an order missing from the landing layer, a double push, an amount
             mismatch, a duplicated ad-spend row, a second trial in the same probable household, an
             email alias left unresolved

The independent "source export" is written BEFORE the ingestion faults are injected, so the
reconciliation compares the warehouse against something it did not produce.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import random
import shutil
import tempfile

import duckdb

SEED = 20261009
AS_OF = dt.datetime(2026, 9, 30, 21, 0, 0)          # UTC, the fixed clock of every scenario
WORLD_START = dt.date(2025, 4, 1)
LAST_CHARGE_DAY = dt.date(2026, 9, 29)             # renewals due on or after 30/09 are not charged yet

UTC = dt.timezone.utc

RECIPES = [
    ("WET-CHK-JLY", "Chicken in jelly, sleeve of 7 tins"),
    ("WET-TUN-GRV", "Tuna in gravy, sleeve of 7 tins"),
    ("WET-SAL-PTE", "Salmon pate, sleeve of 7 tins"),
    ("WET-CHK-BRT", "Chicken in broth, sleeve of 7 tins"),
    ("WET-DUK-JLY", "Chicken with duck in jelly, sleeve of 7 tins"),
]
ADDONS = [("LIT-CASSAVA-7L", "Plant litter 7L", 1100), ("TRT-CHK-PUREE", "Chicken puree treats x12", 650),
          ("DRY-POULTRY-1K", "Dry food poultry 1kg", 1200)]
# synthetic price per sleeve (pence) by tins per day: bigger boxes cost less per tin
SLEEVE_PRICE = {1: 725, 2: 675, 3: 640, 4: 620, 5: 610}
TRIAL_LIST_PENCE = 1200
TRIAL_PROMO_DISCOUNT = 500
FIRST_NAMES = ["amelia", "oliver", "isla", "noah", "ava", "leo", "freya", "arthur", "grace", "theo", "ivy",
               "henry", "rosie", "oscar", "evie", "jack", "poppy", "harry", "willow", "george", "sophie",
               "finley", "maya", "alfie", "ruby", "archie", "daisy", "louis", "phoebe", "ezra"]
LAST_NAMES = ["jones", "taylor", "brown", "wilson", "evans", "thomas", "johnson", "roberts", "walker",
              "wright", "robinson", "thompson", "white", "hughes", "edwards", "green", "hall", "wood",
              "harris", "lewis", "martin", "jackson", "clarke", "clark", "turner", "hill", "scott", "cooper"]
STREETS = ["Albion Road", "Beech Grove", "Canal Street", "Dover Lane", "Elm Terrace", "Fern Close",
           "Granby Street", "Hawthorn Way", "Kings Avenue", "Linden Road", "Mill Lane", "Nelson Street",
           "Orchard Rise", "Park View", "Queens Road", "Rectory Lane", "Station Road", "Victoria Street"]
POSTCODE_AREAS = ["N16", "E8", "SE15", "BS6", "M20", "LS6", "B13", "EH7", "CF24", "NG7", "BN2", "OX4"]
PAUSE_REASONS = ["too_much_food", "going_on_holiday", "cat_not_eating", "budget", "other"]
CANCEL_REASONS = ["cat_did_not_like_it", "too_expensive", "switching_brand", "moving_house", "cat_passed_away",
                  "other"]


def iso(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def money(pence: int) -> str:
    return f"{pence / 100:.2f}"


class World:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.t = {k: [] for k in [
            "platform.accounts", "platform.account_email_history", "platform.subscriptions",
            "platform.subscription_events", "platform.orders", "platform.payment_transactions",
            "shopify.customer", "shopify.order", "shopify.order_line", "shopify.refund",
            "shopify.order_line_refund", "shopify.transaction",
            "ads.daily_spend",
        ]}
        self._ev = 0
        self._shop_order = 5_000_000
        self._line = 9_000_000
        self._txn = 3_000_000
        self._refund = 7_000_000
        self._ptxn = 0
        self._order_name = 1000
        self.subs: dict[str, dict] = {}
        self._po_seq: dict[int, int] = {}

    # ----- identifiers -------------------------------------------------------------------------
    def next_shop_order(self):
        self._shop_order += 1
        self._order_name += 1
        return self._shop_order, f"#U{self._order_name}"

    def next_line(self):
        self._line += 1
        return self._line

    def next_txn(self):
        self._txn += 1
        return self._txn

    def next_ptxn(self):
        self._ptxn += 1
        return f"PT-{self._ptxn:07d}"

    # ----- accounts and subscriptions -------------------------------------------------------------
    def shopify_customer(self, cid: int, email: str, created: dt.datetime):
        self.t["shopify.customer"].append(dict(
            id=cid, email=email, created_at=iso(created),
            _weld_synced=iso(min(created + dt.timedelta(minutes=35), AS_OF - dt.timedelta(minutes=55)))))

    def account(self, acc: str, email: str, created: dt.datetime, line1: str, postcode: str,
                fingerprint: str, shopify_id: int | None, referred_by: str | None = None,
                shopify_email: str | None = None):
        self.t["platform.accounts"].append(dict(
            account_id=acc, email=email, created_at=iso(created), delivery_address_line1=line1,
            delivery_postcode=postcode, payment_fingerprint=fingerprint,
            shopify_customer_id=shopify_id if shopify_id else "", referred_by_account_id=referred_by or "",
            _ingested_at=iso(created + dt.timedelta(minutes=20))))
        self.t["platform.account_email_history"].append(dict(
            account_id=acc, email=email, valid_from=iso(created), valid_to=""))
        if shopify_id:
            self.shopify_customer(shopify_id, shopify_email or email, created + dt.timedelta(minutes=1))

    def subscription(self, sub: str, acc: str, created: dt.datetime, cats: int, tpd: int, shopify_id: int | None,
                     referred: bool):
        self.subs[sub] = dict(acc=acc, cats=cats, tpd=tpd, shop=shopify_id, referred=referred, boxes=0)
        self.t["platform.subscriptions"].append(dict(
            subscription_id=sub, account_id=acc, created_at=iso(created), cats_on_plan=cats,
            tins_per_day_total=tpd, _ingested_at=iso(created + dt.timedelta(minutes=20))))

    def event(self, sub: str, etype: str, at: dt.datetime, order: str | None = None,
              next_renewal: dt.date | None = None, previous_renewal: dt.date | None = None,
              reason: str | None = None, ingest_delay_min: int | None = None):
        delay = ingest_delay_min if ingest_delay_min is not None else self.rng.randint(1, 45)
        self.t["platform.subscription_events"].append(dict(
            event_id=None, subscription_id=sub, event_type=etype, event_at=iso(at),
            ingested_at=iso(at + dt.timedelta(minutes=delay)), platform_order_id=order or "",
            next_renewal_date=next_renewal.isoformat() if next_renewal else "",
            previous_renewal_date=previous_renewal.isoformat() if previous_renewal else "",
            reason=reason or "", _sort=(at, len(self.t["platform.subscription_events"]))))

    # ----- orders -------------------------------------------------------------------------------
    def box_lines(self, tpd: int):
        sleeves = 4 * tpd
        k = min(len(RECIPES), self.rng.choice([2, 2, 3]))
        chosen = self.rng.sample(RECIPES, k)
        split = [sleeves // k] * k
        for i in range(sleeves - sum(split)):
            split[i] += 1
        return [(sku, title, q, SLEEVE_PRICE[min(tpd, 5)]) for (sku, title), q in zip(chosen, split)]

    def platform_order(self, po: str, sub: str | None, acc: str, kind: str, status: str, due: dt.date,
                       paid_at: dt.datetime | None, gross: int, discount: int, discount_type: str = "",
                       motif: str = "", ingested: dt.datetime | None = None):
        ing = ingested or ((paid_at or dt.datetime.combine(due, dt.time(6))) + dt.timedelta(minutes=10))
        self.t["platform.orders"].append(dict(
            platform_order_id=po, subscription_id=sub or "", account_id=acc, order_kind=kind,
            order_status=status, due_date=due.isoformat(), paid_at=iso(paid_at) if paid_at else "",
            gross_pence=gross, discount_pence=discount, total_pence=gross - discount,
            discount_type=discount_type, motif=motif, _ingested_at=iso(ing)))

    def shopify_order(self, customer_id: int, created: dt.datetime, lines, discount: int,
                      source_name: str, source_identifier: str = "", tags: str = "",
                      sync_at: dt.datetime | None = None, with_sale: bool = True):
        oid, name = self.next_shop_order()
        gross = sum(q * p for _, _, q, p in lines)
        total = gross - discount
        synced = sync_at or min(created + dt.timedelta(minutes=self.rng.randint(15, 70)),
                                AS_OF - dt.timedelta(minutes=50))
        self.t["shopify.order"].append(dict(
            id=oid, customer_id=customer_id, name=name, created_at=iso(created), processed_at=iso(created),
            currency="GBP", subtotal_price=money(gross), total_discounts=money(discount),
            total_price=money(total), current_total_price=money(total), source_name=source_name,
            source_identifier=source_identifier, tags=tags, test="false", _weld_synced=iso(synced)))
        line_ids = []
        for sku, title, q, p in lines:
            lid = self.next_line()
            line_ids.append((lid, q, p))
            self.t["shopify.order_line"].append(dict(
                id=lid, order_id=oid, sku=sku, title=title, quantity=q, price=money(p), total_discount="0.00",
                _weld_synced=iso(synced)))
        if with_sale and total > 0:
            self.t["shopify.transaction"].append(dict(
                id=self.next_txn(), order_id=oid, refund_id="", kind="sale", status="success",
                amount=money(total), created_at=iso(created), _weld_synced=iso(synced)))
        return oid, line_ids, total

    def refund(self, order_id: int, created: dt.datetime, line_refunds, adjustment: int, txn_status: str,
               paid_at: dt.datetime | None = None, note: str = "", synced: dt.datetime | None = None):
        self._refund += 1
        rid = self._refund
        amount = sum(q * p for _, q, p in line_refunds) + adjustment
        s = synced or min(created + dt.timedelta(minutes=30), AS_OF - dt.timedelta(minutes=50))
        self.t["shopify.refund"].append(dict(id=rid, order_id=order_id, created_at=iso(created), note=note,
                                             _weld_synced=iso(s)))
        for lid, q, p in line_refunds:
            self.t["shopify.order_line_refund"].append(dict(
                id=self.next_line(), refund_id=rid, order_line_id=lid, quantity=q, subtotal=money(q * p),
                _weld_synced=iso(s)))
        self.t["shopify.transaction"].append(dict(
            id=self.next_txn(), order_id=order_id, refund_id=rid, kind="refund", status=txn_status,
            amount=money(amount), created_at=iso(paid_at or created), _weld_synced=iso(s)))
        # current_total_price follows recorded refunds, whether or not the money has left yet
        for o in self.t["shopify.order"]:
            if o["id"] == order_id:
                o["current_total_price"] = money(round(float(o["current_total_price"]) * 100) - amount)
        return rid, amount

    def paid_box(self, sub: str, po: str, due: dt.date, paid_at: dt.datetime, discount=0,
                 discount_type: str = "", kind: str = "subscription_box", push_to_shopify: bool = True):
        s = self.subs[sub]
        lines = self.box_lines(s["tpd"])
        gross = sum(q * p for _, _, q, p in lines)
        if discount == "half":          # referral: 50% off the first subscription box
            discount = gross // 2
        self.platform_order(po, sub, s["acc"], kind, "paid", due, paid_at, gross, discount, discount_type)
        self.ptxn(s["acc"], po, "charge", gross - discount, paid_at, "succeeded")
        res = None
        if push_to_shopify:
            res = self.shopify_order(s["shop"], paid_at + dt.timedelta(seconds=40), lines, discount,
                                     "subscription_platform", po, "subscription,box")
        s["boxes"] += 1
        return res

    def ptxn(self, acc, po, kind, amount, at, status, parent=""):
        tid = self.next_ptxn()
        self.t["platform.payment_transactions"].append(dict(
            transaction_id=tid, account_id=acc, platform_order_id=po or "", kind=kind, amount_pence=amount,
            status=status, created_at=iso(at), parent_transaction_id=parent,
            _ingested_at=iso(at + dt.timedelta(minutes=12))))
        return tid

    def trial(self, sub: str, po: str, at: dt.datetime, referred: bool):
        s = self.subs[sub]
        discount = TRIAL_LIST_PENCE // 2 if referred else TRIAL_PROMO_DISCOUNT
        dtype = "referral" if referred else "trial_promo"
        self.platform_order(po, sub, s["acc"], "trial", "paid", at.date(), at, TRIAL_LIST_PENCE, discount, dtype)
        self.ptxn(s["acc"], po, "charge", TRIAL_LIST_PENCE - discount, at, "succeeded")
        self.shopify_order(s["shop"], at + dt.timedelta(seconds=40),
                           [("TRIAL-12", "Trial box, 12 tins", 1, TRIAL_LIST_PENCE)], discount,
                           "subscription_platform", po, "subscription,trial")
        self.event(sub, "trial_purchased", at, po)
        first = at.date() + dt.timedelta(days=9)
        self.event(sub, "subscription_enrolled", at + dt.timedelta(seconds=5), next_renewal=first)
        return first

    def charge(self, sub: str, po: str, due: dt.date, at: dt.datetime, discount=0, dtype="",
               push_to_shopify=True):
        """Successful charge of a box: payment_succeeded (+ first_subscription_box_paid) and fulfilled."""
        s = self.subs[sub]
        first = s["boxes"] == 0
        self.paid_box(sub, po, due, at, discount, dtype, push_to_shopify=push_to_shopify)
        nxt = due + dt.timedelta(days=28)
        self.event(sub, "payment_succeeded", at, po, next_renewal=nxt)
        if first:
            self.event(sub, "first_subscription_box_paid", at + dt.timedelta(seconds=1), po)
        if at + dt.timedelta(days=1) < AS_OF:
            self.event(sub, "fulfilled", at + dt.timedelta(days=1, hours=3), po)
        return nxt

    def regular(self, sub: str, tag: str, first_due: dt.date, until: dt.date = LAST_CHARGE_DAY,
                first_discount=0, first_dtype="", start_n=1):
        due, n = first_due, start_n
        while due <= until:
            at = dt.datetime.combine(due, dt.time(6, 5))
            disc = first_discount if n == 1 else 0
            due = self.charge(sub, f"PO-{tag}-B{n}", due, at, disc, first_dtype if n == 1 else "")
            n += 1
        return due

    # ----- random lifecycle ---------------------------------------------------------------------
    def random_subscriber(self, n: int, accounts_so_far: list[str]):
        rng = self.rng
        span = (dt.date(2026, 9, 22) - WORLD_START).days
        # gentle growth: later dates more likely
        day = int(span * (rng.random() ** 0.8))
        trial_at = dt.datetime.combine(WORLD_START + dt.timedelta(days=day),
                                       dt.time(rng.randint(7, 22), rng.randint(0, 59)))
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        acc, sub, shop = f"ACC-{n:05d}", f"SUB-{n:05d}", 10_000 + n
        email = f"{first}.{last}{n}@example.com"
        referred = rng.random() < 0.18 and accounts_so_far
        ref_by = rng.choice(accounts_so_far) if referred else None
        line1 = f"{rng.randint(1, 240)} {rng.choice(STREETS)}, unit {n}"
        postcode = f"{rng.choice(POSTCODE_AREAS)} {rng.randint(1, 9)}{chr(65 + n % 26)}{chr(65 + (n // 26) % 26)}"
        self.account(acc, email, trial_at - dt.timedelta(minutes=4), line1, postcode, f"fp_{n:05d}", shop, ref_by)
        cats = rng.choices([1, 2, 3], [60, 30, 10])[0]
        tpd = min(5, sum(rng.choice([1, 1, 2]) for _ in range(cats)))
        self.subscription(sub, acc, trial_at - dt.timedelta(minutes=2), cats, tpd, shop, bool(referred))
        expected = self.trial(sub, f"PO-{n:05d}-T", trial_at, bool(referred))
        if rng.random() < 0.36:  # cancels during the 9 days before the first box
            at = trial_at + dt.timedelta(days=rng.randint(1, 8), hours=rng.randint(0, 5))
            self.event(sub, "cancelled", at, reason=rng.choice(CANCEL_REASONS))
            return
        k = 0
        while expected <= LAST_CHARGE_DAY:
            r = rng.random()
            k += 1
            if r < 0.10:   # Change Date
                at = dt.datetime.combine(expected - dt.timedelta(days=rng.randint(1, 4)), dt.time(rng.randint(8, 21)))
                new = expected + dt.timedelta(days=rng.randint(3, 16))
                self.event(sub, "renewal_date_changed", at, next_renewal=new, previous_renewal=expected)
                expected = new
                continue
            if r < 0.13:   # skip (event name is an assumption, see README)
                at = dt.datetime.combine(expected - dt.timedelta(days=rng.randint(1, 3)), dt.time(rng.randint(8, 21)))
                new = expected + dt.timedelta(days=28)
                self.event(sub, "skipped", at, next_renewal=new, previous_renewal=expected)
                expected = new
                continue
            if r < 0.16:   # I need it now: order now, cycle restarts at +28 days
                at = dt.datetime.combine(expected - dt.timedelta(days=rng.randint(3, 10)),
                                         dt.time(rng.randint(8, 21), rng.randint(0, 59)))
                if at.date() <= LAST_CHARGE_DAY:
                    self.event(sub, "expedited", at, previous_renewal=expected,
                               next_renewal=at.date() + dt.timedelta(days=28))
                    po = self._next_po(n)
                    disc = -1 if (referred and self.subs[sub]['boxes'] == 0) else 0
                    self._charge_with_referral(sub, po, at.date(), at + dt.timedelta(minutes=30), disc)
                    expected = at.date() + dt.timedelta(days=28)
                    continue
            if r < 0.19:   # pause with a reason, sometimes resumed
                at = dt.datetime.combine(expected - dt.timedelta(days=rng.randint(1, 5)), dt.time(rng.randint(8, 21)))
                self.event(sub, "paused", at, reason=rng.choice(PAUSE_REASONS))
                back = at.date() + dt.timedelta(days=rng.randint(20, 120))
                if rng.random() < 0.45 and back < LAST_CHARGE_DAY:
                    self.event(sub, "resumed", dt.datetime.combine(back, dt.time(9, 30)), next_renewal=back)
                    expected = back
                    continue
                return
            if r < 0.215 and self.subs[sub]["boxes"] > 0:   # cancel with a reason, sometimes reactivated
                at = dt.datetime.combine(expected - dt.timedelta(days=rng.randint(1, 6)), dt.time(rng.randint(8, 21)))
                self.event(sub, "cancelled", at, reason=rng.choice(CANCEL_REASONS))
                back = at.date() + dt.timedelta(days=rng.randint(30, 150))
                if rng.random() < 0.15 and back < LAST_CHARGE_DAY:
                    self.event(sub, "resumed", dt.datetime.combine(back, dt.time(10)), next_renewal=back,
                               reason="reactivated")
                    expected = back
                    continue
                return
            # renewal charge on the expected date
            po = self._next_po(n)
            at = dt.datetime.combine(expected, dt.time(6, rng.randint(0, 50)))
            disc = 0
            if referred and self.subs[sub]["boxes"] == 0:
                disc = -1  # computed after lines are drawn
            if rng.random() < 0.07:
                self.event(sub, "payment_failed", at, po)
                self.ptxn(self.subs[sub]["acc"], po, "charge", 0, at, "failed")
                retry = rng.randint(1, 10)
                if rng.random() < 0.75 and expected + dt.timedelta(days=retry) <= LAST_CHARGE_DAY:
                    at2 = at + dt.timedelta(days=retry, hours=2)
                    self._charge_with_referral(sub, po, expected, at2, disc)
                    expected = expected + dt.timedelta(days=28)
                    continue
                end = expected + dt.timedelta(days=30)
                if end <= LAST_CHARGE_DAY:
                    s = self.subs[sub]
                    self.platform_order(po, sub, s["acc"], "subscription_box", "unpaid_cancelled", expected, None,
                                        0, 0, motif="payment_retry_exhausted",
                                        ingested=dt.datetime.combine(end, dt.time(7)))
                    self.event(sub, "payment_retry_exhausted", dt.datetime.combine(end, dt.time(6, 30)), po)
                    expected = expected + dt.timedelta(days=28)
                    while expected <= end:
                        expected += dt.timedelta(days=28)
                    continue
                return
            self._charge_with_referral(sub, po, expected, at, disc)
            expected = expected + dt.timedelta(days=28)
            if self.subs[sub]["boxes"] == 1 and rng.random() < 0.05 and at + dt.timedelta(days=3) < AS_OF:
                self.event(sub, "cat_added", at + dt.timedelta(days=3))

    def _next_po(self, n):
        self._po_seq[n] = self._po_seq.get(n, 0) + 1
        return f"PO-{n:05d}-B{self._po_seq[n]}"

    def _charge_with_referral(self, sub, po, due, at, disc):
        s = self.subs[sub]
        if disc == -1:
            self.charge(sub, po, due, at, "half", "referral")
        else:
            self.charge(sub, po, due, at)
        # occasional partial refund on a box: one sleeve, paid out a few days later
        if self.rng.random() < 0.03:
            order = self.t["shopify.order"][-1]
            lines = [l for l in self.t["shopify.order_line"] if l["order_id"] == order["id"]]
            if lines:
                l = lines[0]
                created = at + dt.timedelta(days=self.rng.randint(2, 6))
                if created + dt.timedelta(days=5) < AS_OF:
                    self.refund(order["id"], created, [(l["id"], 1, round(float(l["price"]) * 100))], 0,
                                "success", paid_at=created + dt.timedelta(days=self.rng.randint(1, 4)),
                                note="damaged tins")


def build_fixtures(w: World, scenario: str):
    D = lambda s: dt.date.fromisoformat(s)
    T = lambda s: dt.datetime.fromisoformat(s)

    # Journey: J0 trial, J5 Change Date J9 -> J20, J6 blank order + 73p card check, J20 failure, J23 retry ok
    j0 = T("2026-06-01T10:00:00")
    w.account("ACC-J", "lena.morris@example.org", j0 - dt.timedelta(minutes=5), "7 Juniper Mews", "N16 0JQ",
              "fp_journey", 70001)
    w.subscription("SUB-J", "ACC-J", j0 - dt.timedelta(minutes=3), 1, 1, 70001, False)
    w.trial("SUB-J", "PO-J-T", j0, False)
    w.event("SUB-J", "renewal_date_changed", T("2026-06-06T19:12:00"), next_renewal=D("2026-06-21"),
            previous_renewal=D("2026-06-10"))
    w.event("SUB-J", "payment_method_updated", T("2026-06-07T08:40:00"), "PO-J-BLANK")
    w.platform_order("PO-J-BLANK", "SUB-J", "ACC-J", "blank_payment_update", "blank", D("2026-06-07"),
                     None, 0, 0, motif="payment_method_update")
    w.shopify_order(70001, T("2026-06-07T08:40:30"), [], 0, "subscription_platform", "PO-J-BLANK",
                    "subscription,payment-method-update")
    chk = w.ptxn("ACC-J", "", "card_check", 73, T("2026-06-07T08:40:05"), "succeeded")
    w.ptxn("ACC-J", "", "card_check_reversal", -73, T("2026-06-09T02:00:00"), "succeeded", parent=chk)
    w.event("SUB-J", "payment_failed", T("2026-06-21T06:00:00"), "PO-J-B1")
    w.ptxn("ACC-J", "PO-J-B1", "charge", 0, T("2026-06-21T06:00:00"), "failed")
    w.subs["SUB-J"]["boxes"] = 0
    w.charge("SUB-J", "PO-J-B1", D("2026-06-21"), T("2026-06-24T06:00:00"))
    due = D("2026-06-21") + dt.timedelta(days=28)
    n = 2
    while due <= LAST_CHARGE_DAY:
        due = w.charge("SUB-J", f"PO-J-B{n}", due, dt.datetime.combine(due, dt.time(6, 2)))
        n += 1

    # F1 email change: the platform account changed its email, the Shopify customer kept the old one.
    t = T("2025-12-01T18:20:00")
    w.account("ACC-F1", "mia.old@yahoo.co.uk", t - dt.timedelta(minutes=5), "22 Larch Walk", "LS6 2QT",
              "fp_f1", 70011)
    w.t["platform.accounts"][-1]["email"] = "mia.new@outlook.com"
    w.t["platform.account_email_history"][-1]["valid_to"] = iso(T("2026-02-03T11:00:00"))
    w.t["platform.account_email_history"].append(dict(account_id="ACC-F1", email="mia.new@outlook.com",
                                                      valid_from=iso(T("2026-02-03T11:00:00")), valid_to=""))
    w.subscription("SUB-F1", "ACC-F1", t - dt.timedelta(minutes=3), 2, 2, 70011, False)
    first = w.trial("SUB-F1", "PO-F1-T", t, False)
    w.regular("SUB-F1", "F1", first)

    # F2 flatmates: same address written differently, different emails and cards -> 2 customers, 1 possible household
    t = T("2026-02-10T20:05:00")
    w.account("ACC-F2A", "kit.barnes@example.org", t, "Flat 3, 12 Rowan Court", "E8 1AB", "fp_f2a", 70021)
    w.subscription("SUB-F2A", "ACC-F2A", t, 1, 1, 70021, False)
    w.regular("SUB-F2A", "F2A", w.trial("SUB-F2A", "PO-F2A-T", t + dt.timedelta(minutes=2), False))
    t = T("2026-03-15T12:30:00")
    w.account("ACC-F2B", "noor.haddad@example.net", t, "flat 3 12 rowan court", "e81ab", "fp_f2b", 70022)
    w.subscription("SUB-F2B", "ACC-F2B", t, 1, 1, 70022, False)
    w.trial("SUB-F2B", "PO-F2B-T", t + dt.timedelta(minutes=2), False)
    w.event("SUB-F2B", "cancelled", T("2026-03-20T09:00:00"), reason="cat_did_not_like_it")

    # F5 case-only email difference, linked by the explicit cross-reference, not by the email
    t = T("2025-09-09T07:45:00")
    w.account("ACC-F5", "Tom.Reed@Example.co.uk", t, "3 Quarry Hill", "BN2 9PL", "fp_f5", 70051,
              shopify_email="tom.reed@example.co.uk")
    w.subscription("SUB-F5", "ACC-F5", t, 1, 1, 70051, False)
    first = w.trial("SUB-F5", "PO-F5-T", t + dt.timedelta(minutes=2), False)
    w.regular("SUB-F5", "F5", first, until=D("2026-01-31"))
    w.event("SUB-F5", "paused", T("2026-02-20T08:00:00"), reason="going_on_holiday")

    # F6 refunds: 1 order, 3 lines, 2 refunds paid (one sleeve, one goodwill) + 1 refund recorded but still pending
    t = T("2026-04-04T09:00:00")
    w.account("ACC-F6", "priya.nair@example.org", t, "41 Weir Road", "OX4 1AA", "fp_f6", 70061)
    w.subscription("SUB-F6", "ACC-F6", t, 3, 3, 70061, False)
    first = w.trial("SUB-F6", "PO-F6-T", t + dt.timedelta(minutes=2), False)
    lines = [("WET-CHK-JLY", RECIPES[0][1], 5, 640), ("WET-TUN-GRV", RECIPES[1][1], 4, 640),
             ("WET-SAL-PTE", RECIPES[2][1], 3, 640)]
    paid = dt.datetime.combine(first, dt.time(6, 1))
    w.platform_order("PO-F6-B1", "SUB-F6", "ACC-F6", "subscription_box", "paid", first, paid, 7680, 0)
    w.ptxn("ACC-F6", "PO-F6-B1", "charge", 7680, paid, "succeeded")
    oid, line_ids, _ = w.shopify_order(70061, paid + dt.timedelta(seconds=40), lines, 0,
                                       "subscription_platform", "PO-F6-B1", "subscription,box")
    w.subs["SUB-F6"]["boxes"] = 1
    w.event("SUB-F6", "payment_succeeded", paid, "PO-F6-B1", next_renewal=first + dt.timedelta(days=28))
    w.event("SUB-F6", "first_subscription_box_paid", paid + dt.timedelta(seconds=1), "PO-F6-B1")
    w.refund(oid, T("2026-04-16T10:00:00"), [(line_ids[0][0], 1, 640)], 0, "success",
             paid_at=T("2026-04-18T10:00:00"), note="one sleeve dented")
    w.refund(oid, T("2026-04-17T15:00:00"), [], 300, "success", paid_at=T("2026-04-17T15:05:00"),
             note="goodwill, late delivery")
    w.refund(oid, T("2026-09-30T16:00:00"), [], 200, "pending", note="goodwill, awaiting bank")
    w.regular("SUB-F6", "F6", first + dt.timedelta(days=28), start_n=2)
    # F7 free replacement: a real shipment at zero price, kept as an order (it has a cost)
    rp = T("2026-05-06T11:00:00")
    w.platform_order("PO-F6-R1", "SUB-F6", "ACC-F6", "replacement", "paid", rp.date(), rp, 1280, 1280,
                     "replacement", motif="damaged_in_transit")
    w.shopify_order(70061, rp + dt.timedelta(seconds=30), [("WET-CHK-JLY", RECIPES[0][1], 2, 640)], 1280,
                    "subscription_platform", "PO-F6-R1", "subscription,replacement")

    # F8 time-zone edges: Shopify-only web orders around midnight and both 2025/26 clock changes
    w.shopify_customer(70081, "dev.patel@example.com", T("2025-06-30T12:00:00"))
    for ts in ["2025-07-01T23:15:00", "2025-10-25T23:30:00", "2025-10-26T23:30:00", "2026-03-28T23:30:00",
               "2026-03-29T23:30:00"]:
        sku, title, p = ADDONS[0]
        w.shopify_order(70081, T(ts), [(sku, title, 1, p)], 0, "web", "", "one-off")
    # web order on the cutoff day keeps the Shopify connector demonstrably fresh
    sku, title, p = ADDONS[1]
    w.shopify_order(70081, T("2026-09-30T18:40:00"), [(sku, title, 1, p)], 0, "web", "", "one-off",
                    sync_at=T("2026-09-30T19:35:00"))

    # F9 order paid 90 minutes before the cutoff, not landed yet: inside the arrival tolerance
    t = T("2026-08-31T19:00:00")
    w.account("ACC-F9", "rhys.owen@example.com", t, "9 Harbour Row", "CF24 3AA", "fp_f9", 70091)
    w.subscription("SUB-F9", "ACC-F9", t, 1, 2, 70091, False)
    first = w.trial("SUB-F9", "PO-F9-T", t + dt.timedelta(minutes=2), False)
    w.regular("SUB-F9", "F9", first, until=D("2026-09-29"))   # first box 09/09
    w.charge("SUB-F9", "PO-F9-X1", D("2026-09-30"), T("2026-09-30T19:30:00"), push_to_shopify=False)
    # the expedited order that produced it
    w.event("SUB-F9", "expedited", T("2026-09-30T19:29:00"), previous_renewal=D("2026-10-07"),
            next_renewal=D("2026-10-28"))

    # F10 renewal due on the cutoff day, scheduled but not charged yet: not missing revenue
    t = T("2026-08-24T08:00:00")
    w.account("ACC-F10", "ana.silva@example.org", t, "15 Copper Lane", "NG7 1AB", "fp_f10", 70101)
    w.subscription("SUB-F10", "ACC-F10", t, 1, 1, 70101, False)
    first = w.trial("SUB-F10", "PO-F10-T", t + dt.timedelta(minutes=2), False)   # first box 02/09
    w.regular("SUB-F10", "F10", first)                                            # next due 30/09, not charged by the cutoff
    w.platform_order("PO-F10-S", "SUB-F10", "ACC-F10", "subscription_box", "scheduled", D("2026-09-30"),
                     None, 2900, 0, ingested=T("2026-09-30T20:30:00"))

    # D1 to D3: regular subscribers whose orders the defective scenario will damage in the landing layer
    for tag, day, tpd in [("D1", "2026-05-02T10:00:00", 1), ("D2", "2026-06-12T17:00:00", 2),
                          ("D3", "2026-07-03T08:30:00", 1)]:
        t = T(day)
        acc, sub, sid = f"ACC-{tag}", f"SUB-{tag}", 70200 + int(tag[1])
        w.account(acc, f"{tag.lower()}.owner@example.net", t, f"{int(tag[1])} Fixture Street", "B13 8AA".replace("8", tag[1]),
                  f"fp_{tag.lower()}", sid)
        w.subscription(sub, acc, t, 1, tpd, sid, False)
        w.regular(sub, tag, w.trial(sub, f"PO-{tag}-T", t + dt.timedelta(minutes=2), False))

    if scenario == "defective":
        # F3: second trial in the same probable household (same card and address), new email
        t = T("2026-01-10T19:00:00")
        w.account("ACC-F3A", "sam.ward@example.com", t, "4 Elm Road", "BS6 5AA", "fp_f3", 70031)
        w.subscription("SUB-F3A", "ACC-F3A", t, 1, 1, 70031, False)
        first = w.trial("SUB-F3A", "PO-F3A-T", t + dt.timedelta(minutes=2), False)
        w.event("SUB-F3A", "cancelled", T("2026-01-15T10:00:00"), reason="too_expensive")
        t = T("2026-04-02T21:10:00")
        w.account("ACC-F3B", "sam.ward.cats@example.net", t, "4, Elm Road", "BS6 5AA", "fp_f3", 70032)
        w.subscription("SUB-F3B", "ACC-F3B", t, 1, 1, 70032, False)
        w.regular("SUB-F3B", "F3B", w.trial("SUB-F3B", "PO-F3B-T", t + dt.timedelta(minutes=2), False))
        # F4: plus-alias with dots on one side, a Shopify-only customer on the other: candidate, never merged
        t = T("2026-02-02T13:00:00")
        w.account("ACC-F4", "Ella.Hart+untamed@example.com", t, "88 Mill Lane", "M20 4AB", "fp_f4", 70041)
        w.subscription("SUB-F4", "ACC-F4", t, 1, 1, 70041, False)
        w.regular("SUB-F4", "F4", w.trial("SUB-F4", "PO-F4-T", t + dt.timedelta(minutes=2), False))
        w.shopify_customer(70042, "ellahart@example.com", T("2026-05-05T09:00:00"))
        sku, title, p = ADDONS[2]
        w.shopify_order(70042, T("2026-05-05T09:01:00"), [(sku, title, 1, p)], 0, "web", "", "one-off")


def add_web_one_offs(w: World):
    rng = w.rng
    for i in range(45):
        cid = 60_000 + i
        created = dt.datetime.combine(WORLD_START + dt.timedelta(days=rng.randint(0, 540)), dt.time(rng.randint(8, 22)))
        w.shopify_customer(cid, f"{rng.choice(FIRST_NAMES)}.{rng.choice(LAST_NAMES)}.shop{i}@example.net", created)
        for _ in range(rng.randint(1, 2)):
            sku, title, p = rng.choice(ADDONS)
            at = created + dt.timedelta(days=rng.randint(0, 60), minutes=rng.randint(1, 600))
            if at < dt.datetime(2026, 9, 29):
                w.shopify_order(cid, at, [(sku, title, rng.randint(1, 2), p)], 0, "web", "", "one-off")


def add_ads(w: World):
    rng = w.rng
    campaigns = [("meta", "act_meta_1", "cmp_meta_prospecting", "Prospecting UK", 55),
                 ("meta", "act_meta_1", "cmp_meta_retargeting", "Retargeting", 18),
                 ("google", "acc_google_1", "cmp_google_brand", "Brand search", 14),
                 ("google", "acc_google_1", "cmp_google_pmax", "Performance Max", 30),
                 ("tiktok", "adv_tiktok_1", "cmp_tiktok_spark", "Spark ads UK", 22)]
    day = WORLD_START
    while day <= LAST_CHARGE_DAY:
        growth = 1 + (day - WORLD_START).days / 400
        for platform, acct, cid, name, base in campaigns:
            spend = round(base * growth * rng.uniform(0.6, 1.4) * 100)
            w.t["ads.daily_spend"].append(dict(
                platform=platform, account_id=acct, campaign_id=cid, campaign_name=name, date=day.isoformat(),
                spend=money(spend), impressions=int(spend * rng.uniform(3, 6)), clicks=int(spend * rng.uniform(0.02, 0.06)),
                _synced_at=iso(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(3, 0)))))
        day += dt.timedelta(days=1)


def source_exports(w: World):
    """Independent exports, taken from the systems of record before any landing fault."""
    paid = [dict(platform_order_id=o["platform_order_id"], amount_pence=o["total_pence"], currency="GBP",
                 paid_at=o["paid_at"], exported_at=iso(AS_OF))
            for o in w.t["platform.orders"] if o["order_status"] == "paid"]
    totals: dict[tuple, int] = {}
    for r in w.t["ads.daily_spend"]:
        k = (r["platform"], r["date"])
        totals[k] = totals.get(k, 0) + round(float(r["spend"]) * 100)
    ads = [dict(platform=k[0], date=k[1], spend_pence=v, exported_at=iso(AS_OF)) for k, v in sorted(totals.items())]
    return {"source_export.platform_paid_orders": paid, "source_export.ads_platform_daily_totals": ads}


def inject_landing_faults(w: World):
    t = w.t
    by_src = {o["source_identifier"]: o for o in t["shopify.order"] if o["source_identifier"]}
    # D1: one paid box never reached the landing layer
    gone = by_src["PO-D1-B2"]["id"]
    t["shopify.order"] = [o for o in t["shopify.order"] if o["id"] != gone]
    t["shopify.order_line"] = [l for l in t["shopify.order_line"] if l["order_id"] != gone]
    t["shopify.transaction"] = [x for x in t["shopify.transaction"] if x["order_id"] != gone]
    # D2: the same platform order pushed twice, so two Shopify orders carry one source identifier
    src = by_src["PO-D2-B1"]
    oid, name = w.next_shop_order()
    dup = dict(src, id=oid, name=name, created_at=iso(dt.datetime.fromisoformat(src["created_at"][:-1]) + dt.timedelta(minutes=3)))
    t["shopify.order"].append(dup)
    for l in [l for l in t["shopify.order_line"] if l["order_id"] == src["id"]]:
        t["shopify.order_line"].append(dict(l, id=w.next_line(), order_id=oid))
    for x in [x for x in t["shopify.transaction"] if x["order_id"] == src["id"]]:
        t["shopify.transaction"].append(dict(x, id=w.next_txn(), order_id=oid, created_at=dup["created_at"]))
    # D3: a discount applied twice on the way in: landed total is 3.50 lower than the platform charged
    o = by_src["PO-D3-B1"]
    for f in ["total_price", "current_total_price"]:
        o[f] = money(round(float(o[f]) * 100) - 350)
    o["total_discounts"] = money(round(float(o["total_discounts"]) * 100) + 350)
    for x in t["shopify.transaction"]:
        if x["order_id"] == o["id"] and x["kind"] == "sale":
            x["amount"] = o["total_price"]
    # D4: the ads connector delivered one campaign-day twice (same full source key, later sync)
    row = next(r for r in t["ads.daily_spend"]
               if r["campaign_id"] == "cmp_meta_prospecting" and r["date"] == "2026-08-14")
    t["ads.daily_spend"].append(dict(row, _synced_at="2026-08-15T09:12:00Z"))


def write(world_tables: dict, out: str):
    tmp = tempfile.mkdtemp(prefix="smf_")
    try:
        if os.path.exists(out):
            os.remove(out)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        con = duckdb.connect(out)
        for full, rows in world_tables.items():
            schema, name = full.split(".")
            con.execute(f"create schema if not exists {schema}")
            if not rows:
                continue
            cols = [c for c in rows[0].keys() if not c.startswith("_sort")]
            path = os.path.join(tmp, f"{full}.csv")
            with open(path, "w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                wr.writeheader()
                wr.writerows(rows)
            con.execute(f'create or replace table {schema}."{name}" as select * from read_csv(?, header=true, '
                        f"auto_detect=true, all_varchar=true)", [path])
        con.close()
    finally:
        shutil.rmtree(tmp)


def generate(scenario: str, out: str):
    rng = random.Random(SEED)
    w = World(rng)
    build_fixtures(w, scenario)
    w.rng = random.Random(SEED + 1)   # the random world is identical in both scenarios
    accs: list[str] = []
    for n in range(1, 421):
        w.random_subscriber(n, accs)
        accs.append(f"ACC-{n:05d}")
    add_web_one_offs(w)
    add_ads(w)
    # stable, chronological event ids
    evs = sorted(w.t["platform.subscription_events"], key=lambda e: (e["subscription_id"], e["_sort"]))
    for i, e in enumerate(evs, 1):
        e["event_id"] = f"EV-{i:07d}"
    w.t["platform.subscription_events"] = evs
    exports = source_exports(w)
    if scenario == "defective":
        inject_landing_faults(w)
    tables = dict(w.t)
    tables.update(exports)
    write(tables, out)
    return {k: len(v) for k, v in tables.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["clean", "defective"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    counts = generate(a.scenario, a.out)
    print(f"{a.scenario}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
