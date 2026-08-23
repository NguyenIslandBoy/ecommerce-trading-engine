"""eCommerce Trading Engine -- Layers 2 and 3.

    venv/Scripts/python.exe -m streamlit run app.py

The as-of slider is the point of this app. Move it and the whole engine is
re-run as of that date, seeing only what had actually arrived by then. That is
what makes the backtest honest, and it is far easier to believe when you can
watch a signal appear the day it becomes detectable rather than the day the
event happened.
"""

from __future__ import annotations

import datetime as dt

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from engine.backtest import replay, score
from engine.context import Warehouse
from engine.recommend import dedupe, recommend, recommendations_frame
from engine.run import detect, signals_frame
from engine.signals import Classification

st.set_page_config(page_title="Trading Engine", page_icon="🧭", layout="wide")

# Note on sizing: st.dataframe takes width="stretch"; st.altair_chart still only
# takes use_container_width in Streamlit 1.50 and has no width parameter, so the
# two are written differently on purpose.

INK = "#12655E"
WARN = "#B4661E"
MUTED = "#6B7780"

CLASS_COLOUR = {
    "COMMERCIAL": INK,
    "ARTIFACT": MUTED,
    "DATA_QUALITY": WARN,
}


@st.cache_resource(show_spinner="Loading the warehouse...")
def get_warehouse() -> Warehouse:
    return Warehouse()


@st.cache_data(show_spinner=False)
def signals_at(cursor: dt.date) -> pd.DataFrame:
    warehouse = get_warehouse()
    return signals_frame(detect(warehouse.at(cursor)))


@st.cache_data(show_spinner="Simulating...")
def recommendations_at(cursor: dt.date) -> tuple[pd.DataFrame, list]:
    warehouse = get_warehouse()
    ctx = warehouse.at(cursor)
    recs = dedupe(recommend(ctx, signals_at(cursor)))
    return recommendations_frame(recs), recs


@st.cache_data(show_spinner="Replaying every cursor...")
def backtest_report(step: int) -> dict:
    result = replay(get_warehouse(), step_days=step)
    report = score(result)
    report["signals"] = result.signals
    return report


try:
    warehouse = get_warehouse()
except FileNotFoundError as exc:
    st.error(str(exc))
    st.stop()


# --------------------------------------------------------------------------
# Sidebar -- the cursor
# --------------------------------------------------------------------------

st.sidebar.title("🧭 Trading Engine")
st.sidebar.caption("Layer 2 detection · Layer 3 recommendation")

earliest = warehouse.first_day + dt.timedelta(days=120)
cursor = st.sidebar.slider(
    "As-of date",
    min_value=earliest,
    max_value=warehouse.latest_cursor,
    value=warehouse.latest_cursor,
    format="YYYY-MM-DD",
    help=("The engine is re-run as of this date, seeing only rows that had "
          "actually been ingested by then. Ads and email land a day late, so "
          "the newest day never has spend."),
)

st.sidebar.markdown(
    f"""
    <div style="font-size:0.82rem;color:{MUTED};line-height:1.5;
                border-left:2px solid {INK};padding-left:.6rem;margin:.8rem 0;">
    Everything below is computed from data available on <b>{cursor}</b>.
    Nothing after that date is visible to any detector.
    </div>
    """,
    unsafe_allow_html=True,
)

signals = signals_at(cursor)
actionable = signals[signals["is_actionable"]] if not signals.empty else signals

st.sidebar.metric("Signals", len(signals))
st.sidebar.metric("Actionable", len(actionable))
st.sidebar.caption(
    "A signal is actionable only if it is COMMERCIAL and survived FDR control."
)


# --------------------------------------------------------------------------

st.title("eCommerce Trading Engine")

tab_signals, tab_actions, tab_backtest, tab_method = st.tabs(
    ["Signals", "Recommendations", "Backtest", "How it works"]
)


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

with tab_signals:
    if signals.empty:
        st.info(f"No signals at {cursor}. Detectors need history before they "
                f"can say anything.")
    else:
        counts = signals["classification"].value_counts()
        columns = st.columns(3)
        for column, name in zip(columns, ["COMMERCIAL", "ARTIFACT", "DATA_QUALITY"]):
            column.metric(name.replace("_", " ").title(), int(counts.get(name, 0)))

        st.caption(
            "Only COMMERCIAL signals can become an action. ARTIFACT means the "
            "movement is real but not commercial — acting on it would be a "
            "mistake. DATA_QUALITY means a source stopped delivering, which "
            "makes cost metrics look better than reality."
        )

        chart_data = signals.assign(
            label=signals["detector"] + " · " + signals["entity_id"]
        )
        st.altair_chart(
            alt.Chart(chart_data)
            .mark_bar(cornerRadiusEnd=2, height=16)
            .encode(
                x=alt.X("confidence:Q", title="Confidence",
                        scale=alt.Scale(domain=[0, 1])),
                y=alt.Y("label:N", sort="-x", title=None),
                color=alt.Color(
                    "classification:N",
                    scale=alt.Scale(domain=list(CLASS_COLOUR),
                                    range=list(CLASS_COLOUR.values())),
                    legend=alt.Legend(title="Classification", orient="top"),
                ),
                tooltip=["detector", "entity_id", "classification",
                         "attribution_tier", "confidence", "severity"],
            )
            .properties(height=max(180, 24 * len(chart_data))),
            use_container_width=True,
        )

        st.subheader("Evidence")
        for _, row in signals.iterrows():
            marker = "🟢" if row["is_actionable"] else "⚪"
            with st.expander(
                f"{marker}  {row['detector']} · {row['entity_id']}  "
                f"— {row['classification']}, tier {row['attribution_tier']}, "
                f"confidence {row['confidence']:.2f}"
            ):
                evidence = row["evidence"]
                left, right = st.columns([2, 1])
                with left:
                    st.json(evidence, expanded=True)
                with right:
                    st.metric("Severity", f"{row['severity']:.2f}")
                    st.metric("Confidence", f"{row['confidence']:.2f}")
                    if row["p_value"] is not None and not pd.isna(row["p_value"]):
                        st.metric("p-value", f"{row['p_value']:.2e}")
                    st.caption(f"Passed FDR: {row['passed_fdr']}")


# --------------------------------------------------------------------------
# Recommendations
# --------------------------------------------------------------------------

with tab_actions:
    frame, recs = recommendations_at(cursor)

    if frame.empty:
        st.info("Nothing to recommend at this cursor.")
    else:
        st.caption(
            "Reversibility sets the **ceiling** on autonomy — an inventory "
            "purchase is reviewed at any confidence, because capital committed "
            "cannot be unspent. Confidence sets the **magnitude** — in the "
            "medium band the action still happens, capped small enough that "
            "being wrong is cheap."
        )

        order = [
            "AUTO-EXECUTE", "AUTO-EXECUTE (capped magnitude)",
            "FLAG FOR REVIEW", "WAIT", "MONITOR",
        ]
        for autonomy in order:
            group = [r for r in recs if r.autonomy == autonomy]
            if not group:
                continue

            st.subheader(autonomy, divider="gray")
            for rec in sorted(group, key=lambda r: -r.confidence):
                header = f"**{rec.action_type}** · {rec.entity_id}"
                if rec.magnitude:
                    header += f"  ·  {rec.magnitude:g} {rec.magnitude_unit}"
                st.markdown(header)
                st.caption(rec.rationale)

                if rec.outcome is not None:
                    summary = rec.outcome.summary()
                    metrics = st.columns(4)
                    metrics[0].metric("Median 30d margin",
                                      f"£{summary['median']:,.0f}")
                    if summary["p_positive"] is None:
                        metrics[1].metric("P(gain)", "n/a",
                                          help="This is a risk measure — "
                                               "negative by construction.")
                    else:
                        metrics[1].metric("P(gain)", f"{summary['p_positive']:.0%}")
                    metrics[2].metric("80% CI low", f"£{summary['ci80_low']:,.0f}")
                    metrics[3].metric("80% CI high", f"£{summary['ci80_high']:,.0f}")

                    draws = pd.DataFrame({"delta": rec.outcome.draws})
                    st.altair_chart(
                        alt.Chart(draws)
                        .mark_bar(opacity=0.85, color=INK)
                        .encode(
                            x=alt.X("delta:Q", bin=alt.Bin(maxbins=60),
                                    title="30-day contribution margin delta (£)"),
                            y=alt.Y("count()", title="Draws"),
                        )
                        .properties(height=150),
                        use_container_width=True,
                    )
                    st.caption(
                        f"{summary['draws']:,} Monte Carlo draws. Never a point "
                        f"estimate — with retention collapsing and 26.8% of "
                        f"orders unattributed, a single number would be the most "
                        f"confident-sounding way to be wrong."
                    )
                    with st.expander("Assumptions"):
                        st.json(summary["assumptions"])

                for note in rec.notes:
                    st.markdown(
                        f"<span style='color:{MUTED};font-size:.85rem'>— {note}</span>",
                        unsafe_allow_html=True,
                    )
                st.divider()


# --------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------

with tab_backtest:
    st.caption(
        "Replays detection at every cursor and scores it against hand labels "
        "taken from the profiling done before any detector was written."
    )
    step = st.select_slider(
        "Cursor step", options=[1, 3, 7], value=1,
        help=("Every day, every third day, or weekly. Daily takes ~25 seconds "
              "and is the figure quoted in the README."),
    )
    if step > 1:
        st.caption(
            f"Sampling every {step} days. Recall can read lower than the daily "
            f"figure — a short-lived first fire may fall between two cursors, "
            f"so the event looks missed when it was only unsampled."
        )

    if st.button("Run backtest", type="primary"):
        report = backtest_report(step)

        columns = st.columns(4)
        columns[0].metric("Recall", f"{report['recall']:.0%}")
        columns[1].metric("Trap violations", report["trap_violations"])
        columns[2].metric("Cursors", report["cursors"])
        columns[3].metric("Time", f"{report['seconds']:.0f}s")

        st.subheader("Events — did it find what profiling said was there")
        st.dataframe(report["events"], width="stretch", hide_index=True)

        st.subheader("Traps — did it stay quiet about what profiling said was not")
        traps = report["traps"]
        st.dataframe(
            traps.style.map(
                lambda v: f"color:{INK}" if v is True else
                          (f"color:{WARN}" if v is False else ""),
                subset=["clean"],
            ),
            width="stretch", hide_index=True,
        )
        st.caption(
            "The traps are the half that matters. Anyone can build a detector "
            "that fires; the test is whether it stays silent on four things "
            "that look exactly like signals and are not."
        )

        history = report["signals"]
        if not history.empty:
            timeline = (history[history["classification"] == "COMMERCIAL"]
                        .groupby(["cursor", "detector"]).size()
                        .reset_index(name="signals"))
            st.altair_chart(
                alt.Chart(timeline)
                .mark_circle(size=42, opacity=0.75)
                .encode(
                    x=alt.X("cursor:T", title="Cursor"),
                    y=alt.Y("detector:N", title=None),
                    size=alt.Size("signals:Q", legend=None),
                    color=alt.value(INK),
                    tooltip=["cursor:T", "detector:N", "signals:Q"],
                )
                .properties(height=260, title="When each detector first had "
                                              "something to say"),
                use_container_width=True,
            )

        st.warning(
            "Ground-truth labels are the author's own reading of the dataset "
            "(`config/ground_truth.yml`), and the detectors were built by the "
            "same person who wrote them. This measures **internal "
            "consistency**, not external validity. A high score is evidence the "
            "engine does what it was designed to do — not that the design was "
            "right.",
            icon="⚠️",
        )
    else:
        st.info("Press **Run backtest** to replay the engine across every cursor.")


# --------------------------------------------------------------------------
# Method
# --------------------------------------------------------------------------

with tab_method:
    st.markdown(
        """
### Why the as-of slider exists

Every staging model filters on `_weld_synced` — when a row became *available* —
not on when the event happened. Move the slider and the engine sees exactly what
it would have seen that morning, including the rows that had not arrived yet.

The ingestion lag was measured, not assumed: orders land same-day on 100% of
26,553 rows, both ad sources and email land **+1 day** on 100% of theirs. So the
newest day of any historical view has orders and **no spend** — by design, not
as a gap. A backtest must expect that 365 times over.

Reconstructing a cursor in memory takes ~100ms against ~35 seconds for a real
dbt rebuild, which is what makes a 365-cursor replay possible at all.
`scripts/verify_pit.py` rebuilds the warehouse at three cursors and asserts the
reconstruction is identical on every column — that is what makes the shortcut
legitimate rather than merely convenient.

### Why these statistics

The series has a November/December peak and a January trough, and covers exactly
12 months.

- **Theil–Sen** slopes, not least squares. Breakdown point ~29%, so December has
  to be more than a quarter of the window before it can drag the trend.
- **Mann–Kendall** for significance, not a t-test. Rank-based, so it does not
  assume daily CAC residuals are normal — they are not.
- **MAD z-scores**, not standard deviations, so a catalogue containing both a 3×
  bestseller and a long tail does not have the tail permanently flagged.
- **Benjamini–Hochberg** FDR at 0.10. Nine detectors across ~20 entities at 365
  cursors manufactures false positives by construction; Bonferroni at that scale
  would reject everything real along with them.
- **Day-of-week** is removed multiplicatively. **Month-of-year is not** — with 12
  months there is exactly one January, so the month effect is perfectly
  confounded with trend and cannot be identified. Stated rather than fitted.

### The three classifications

**DATA_QUALITY** — a source stopped delivering. Missing spend makes cost metrics
*improve*, so this suppresses anything whose evidence window overlaps it. Without
that, the engine would recommend more budget for a channel that merely stopped
reporting.

**ARTIFACT** — the movement is real but not commercial. Email opens fall ~20%
across every flow while conversion holds; acting on that would churn a programme
that is still earning.

**COMMERCIAL** — survives both, and clears FDR. Only these become actions.

### Attribution tiers

`landing_site` has seven distinct values and **zero** UTM parameters across all
26,553 orders, so last-click referrer is the only attribution available.

| Tier | Basis | Ceiling |
|---|---|---|
| A | Platform-reported: spend, CPC, CPM, CTR | full |
| B | Blended, attribution-free: blended CAC, new customers | full |
| C | Channel-attributed last-click | **0.55** |

A Tier C signal at severity 1.0 still cannot reach the autonomy threshold. That
is structural, not a tuned number: 26.8% of orders are unattributed and TikTok
has no cost file at all.

### What the engine will not do

- Auto-execute anything irreversible, at any confidence.
- Auto-execute on Tier C evidence alone.
- Take an action its own simulation says probably loses money — confidence says
  the *signal* is real, `P(gain)` says *acting on it* pays, and both must hold.
- Assume linear scaling. Marginal CAC is `CAC₀ · (spend/spend₀)^β` with β
  estimated from the observed relationship; β = 0 would make every reallocation
  free and is the most common way this kind of model produces nonsense.
- Quote a point estimate.
        """
    )
