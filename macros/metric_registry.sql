{#
  THE metric registry. One place, read by dbt (models/metrics/metric_registry.sql builds a table from it)
  and served by the MCP server from that table. Python never holds its own copy.
#}
{% macro metric_registry() %}
{%- set w = var('trial_conversion_window_days') %}
{% set registry_yaml %}
# Quality policy, applied by models/metrics/metric_quality.sql and served as-is by the MCP server:
#   unavailable  a freshness marker the metric depends on is stale at the cutoff: no number is served
#   degraded     a reconciliation check linked to the metric has failed: the number is served together with
#                the named exceptions
#   available    otherwise
quality_policy: "unavailable when a freshness marker the metric depends on is stale at the cutoff (no number served); degraded when a linked reconciliation check has failed (number served with the named exceptions); available otherwise"

# Freshness markers: one per synthetic feed. In production, one per critical feed plus its sync status.
sources:
  - id: platform_events
    label: subscription platform, lifecycle events
    max_age_hours: 6
  - id: platform_orders
    label: subscription platform, orders
    max_age_hours: 6
  - id: shopify_orders
    label: Shopify orders, as landed by the ELT connector
    max_age_hours: 6
  - id: shopify_refunds
    label: Shopify refund transactions, as landed by the ELT connector
    max_age_hours: 72
  - id: ads_spend
    label: Meta, Google and TikTok daily spend (Europe/London dates)
    max_age_hours: 36

metrics:
  - id: trial_conversion_rate
    version: "1-w{{ w }}"
    status: implemented
    label: Trial to first paid subscription box
    definition: "Share of trials whose first subscription box was paid within {{ w }} days of the trial, counted only over trials whose {{ w }}-day window had fully closed at the cutoff; cancelled trials stay in the denominator."
    grain: trial cohort month (Europe/London)
    numerator: trials whose first subscription box was paid within the window
    denominator: trials whose window had closed by the cutoff
    unit: ratio
    aggregation: ratio_of_sums
    allowed_slices: [total, cats_on_plan, referred]
    min_group_size: 10
    threshold_entity: trial
    window_days: {{ w }}
    maturity_policy: "a trial enters the denominator only once its {{ w }}-day window has closed; younger trials are reported as pending"
    source_dependencies: [platform_events]
    reconciliation_checks: [trial_eligibility]
    required_quality_status: fresh

  - id: net_revenue
    version: "2"
    status: implemented
    label: Net revenue
    definition: "Order amounts after discounts and after refunds actually paid out, UK VAT included, sales booked on the order date and refunds on the refund date (Europe/London), blank orders and card checks excluded; VAT split, delivery and accounting reconciliation with Xero are out of scope."
    grain: month (Europe/London)
    numerator: sum of sales minus paid refunds, in pence
    denominator: none
    unit: GBP pence
    aggregation: sum
    allowed_slices: [total, order_kind]
    min_group_size: 10
    threshold_entity: order
    maturity_policy: "a month stays open to late refunds; figures are as known at the cutoff"
    source_dependencies: [shopify_orders, shopify_refunds, platform_orders]
    reconciliation_checks: [order_count, completeness, lines_to_order_total, order_total_to_cash]
    required_quality_status: fresh

  - id: active_subscriptions
    version: "2"
    status: implemented
    label: Active paying subscriptions at month end
    definition: "Subscriptions whose first box has been paid and whose state at the end of the month, as known that day, is active or in payment retry; paused, cancelled, lapsed (renewal more than 35 days overdue) and trial-only subscriptions are excluded."
    grain: month end (Europe/London), or the cutoff date for the current month
    numerator: subscriptions in state active or payment_retry
    denominator: none
    unit: subscriptions
    aggregation: point_in_time
    allowed_slices: [total, cats_on_plan]
    min_group_size: 10
    threshold_entity: subscription
    maturity_policy: "state is rebuilt from events as they were known on each day, never from later corrections"
    source_dependencies: [platform_events]
    reconciliation_checks: []
    required_quality_status: fresh

  - id: ad_spend_per_trial
    version: "1"
    status: implemented
    label: Paid media spend per trial (blended, DTC)
    definition: "Meta, Google and TikTok spend for the month, deduplicated on the full source key, divided by trials purchased in the same month; a blended DTC figure, not a channel CAC."
    grain: month (Europe/London)
    numerator: ad spend in pence
    denominator: trials purchased
    unit: GBP pence per trial
    aggregation: ratio_of_sums
    allowed_slices: [total]
    min_group_size: 10
    threshold_entity: trial
    maturity_policy: "a month is complete once the ads connector has delivered its last day"
    source_dependencies: [ads_spend, platform_events]
    reconciliation_checks: [ad_spend]
    required_quality_status: fresh

  - id: subscription_churn_rate
    version: "0"
    status: contract
    label: Subscription churn
    definition: "Share of subscriptions active at the start of a period that reach the cancelled state during it; pauses, moved renewal dates and payment retries are reported beside it and never count as churn."
    missing_inputs: ["agreed period: calendar month or 28-day cycle", "agreed treatment of long pauses", "whether reactivations net off"]

  - id: retention_by_trial_cohort
    version: "0"
    status: contract
    label: Retention by trial cohort
    definition: "Share of a trial cohort whose subscription is active or in payment retry 3, 6 and 12 months after the trial."
    missing_inputs: ["agreed meaning of active", "cohort basis: trial date or first box date"]

  - id: observed_net_revenue_180d
    version: "0"
    status: contract
    label: Observed value per trial at 180 days
    definition: "Net revenue per trial in the 180 days after the trial, observed only, never predicted; trials that did not convert stay in the denominator."
    maturity_policy: "only cohorts in which every trial has 180 days of observation at the cutoff are reported"
    missing_inputs: ["whether one-off add-on orders count", "whether retail purchases by the same household are in scope"]

  - id: cac
    version: "0"
    status: contract
    label: Customer acquisition cost (DTC)
    definition: "Acquisition spend for DTC (media, agency and creative) divided by new trials or new paying subscribers, as agreed; referral discounts are already netted from revenue and are not added again."
    missing_inputs: ["agency and creative costs", "attribution mapping from the attribution tool", "denominator: trial or first paid box"]

  - id: contribution_margin
    version: "0"
    status: contract
    label: Contribution margin
    definition: "Net revenue minus product cost, packaging, fulfilment, delivery and payment fees, per order and per cohort."
    missing_inputs: ["cost per SKU", "fulfilment and courier costs", "payment fees", "accounting mapping"]

  - id: payback_period
    version: "0"
    status: contract
    label: Payback period
    definition: "Months until the cumulative contribution margin of a trial cohort, including trials that never converted, covers its acquisition cost."
    missing_inputs: ["contribution_margin", "cac"]
{% endset %}
{{ return(fromyaml(registry_yaml)) }}
{% endmacro %}
