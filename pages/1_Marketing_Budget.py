"""Marketing budget, pacing and invoice reconciliation -- SYNTHETIC Finance inputs.

Streamlit adds this page alongside app.py:
    venv/Scripts/python.exe -m streamlit run app.py

Reads the three marketing marts exported by scripts/export_marts.py. The
budgets and invoices behind them are invented; delivered spend comes from the
ad platform extracts. Missing values render as "no data", never as 0.
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Marketing Budget", page_icon="💷", layout="wide")

MARTS = Path(__file__).resolve().parent.parent / "data" / "marts"
FILES = {
    "budget": "mart_marketing_budget_vs_actual.parquet",
    "pacing": "mart_marketing_pacing_daily.parquet",
    "recon": "mart_marketing_invoice_recon.parquet",
}
NO_DATA = "no data"

# Categorical slots 1 and 2 of the reference palette, stepped per theme and
# validated (lightness, chroma, CVD separation, contrast) on each surface.
PALETTE = {
    "light": {"Delivered": "#2a78d6", "Planned": "#eb6834"},
    "dark": {"Delivered": "#3987e5", "Planned": "#d95926"},
}


@st.cache_data(show_spinner=False)
def load() -> dict[str, pd.DataFrame]:
    frames = {}
    for key, name in FILES.items():
        frame = pd.read_parquet(MARTS / name)
        for col in ("month_start", "date_day", "as_of_date"):
            if col in frame:
                frame[col] = pd.to_datetime(frame[col])
        for col in frame.columns:
            if frame[col].dtype == object and col not in ("channel",) and frame[col].map(
                    lambda v: hasattr(v, "is_finite")).any():
                frame[col] = pd.to_numeric(frame[col])  # Decimal -> float, NULL -> NaN
        frames[key] = frame
    return frames


def gbp(value) -> str:
    return NO_DATA if pd.isna(value) else f"£{value:,.2f}"


def pct(value) -> str:
    return NO_DATA if pd.isna(value) else f"{value:+.1%}"


def variance(value) -> str:
    if pd.isna(value):
        return NO_DATA
    if value == 0:
        return "on budget"
    return f"+£{value:,.2f} over" if value > 0 else f"−£{-value:,.2f} under"


missing = [name for name in FILES.values() if not (MARTS / name).exists()]
if missing:
    st.error(
        "Marketing marts not found: " + ", ".join(missing)
        + ". Run `dbt build` and `scripts/export_marts.py` first."
    )
    st.stop()

data = load()
budget, pacing, recon = data["budget"], data["pacing"], data["recon"]
as_of = budget["as_of_date"].iloc[0].date()
delivered_days = pacing.loc[pacing["spend_status"] == "delivered", "date_day"]
data_through = delivered_days.max().date() if not delivered_days.empty else None

st.title("Marketing budget, pacing and invoice reconciliation")
st.warning(
    "**SYNTHETIC Finance inputs.** Budgets and invoices are invented for demonstration "
    "(see `data/finance/README.md`); delivered spend comes from the ad platform extracts. "
    f"As of **{as_of}** · ad spend data through **{data_through or 'none'}**.",
    icon="⚠️",
)

months = sorted(budget["month_start"].dt.date.unique(), reverse=True)
month = st.selectbox("Month", months, format_func=lambda m: m.strftime("%B %Y"))

# --------------------------------------------------------------------------
# Budget vs actual
# --------------------------------------------------------------------------
st.subheader("Budget vs actual")
st.caption(
    "Budgets cover media only. Variance = recognised media expense − approved media budget: "
    "**positive means overspend**. Agency fees are shown but not budgeted. TikTok has a budget "
    "but no cost source, so its spend and variance are unknown, not zero."
)
month_budget = budget[budget["month_start"].dt.date == month].copy()
month_budget["order"] = month_budget["channel"].map(
    {"meta": 0, "google": 1, "tiktok": 2, "unallocated": 3})
month_budget = month_budget.sort_values("order")

known = month_budget.dropna(subset=["budget_variance"])
cols = st.columns(3)
cols[0].metric("Media budget (channels with spend)", gbp(known["approved_budget"].sum()) if len(known) else NO_DATA)
cols[1].metric("Recognised media expense", gbp(known["recognised_media_expense"].sum()) if len(known) else NO_DATA)
total_var = known["budget_variance"].sum() if len(known) else float("nan")
cols[2].metric(
    "Variance (+ = overspend)", variance(total_var),
    # Ratio of sums, not an average of channel percentages.
    pct(total_var / known["approved_budget"].sum()) if len(known) else None,
    delta_color="inverse",
)

st.dataframe(
    pd.DataFrame({
        "Channel": month_budget["channel"],
        "Media budget": month_budget["approved_budget"].map(gbp),
        "Version": month_budget["budget_version_used"].fillna(NO_DATA),
        "Delivered spend": month_budget["delivered_spend"].map(gbp),
        "Media invoiced": month_budget["media_invoiced"].map(gbp),
        "Fees invoiced": month_budget["fee_invoiced"].map(gbp),
        "Unbilled accrual": month_budget["estimated_unbilled_accrual"].map(gbp),
        "Recognised media": month_budget["recognised_media_expense"].map(gbp),
        "Variance": month_budget["budget_variance"].map(variance),
        "Variance %": month_budget["budget_variance_pct"].map(pct),
        "Data status": month_budget["data_status"],
    }),
    hide_index=True,
    width="stretch",
)

# --------------------------------------------------------------------------
# Pacing
# --------------------------------------------------------------------------
st.subheader("Pacing: planned vs delivered, cumulative")
channel = st.radio("Channel", ["meta", "google", "tiktok"], horizontal=True,
                   format_func=lambda c: {"meta": "Meta", "google": "Google", "tiktok": "TikTok"}[c])
month_pacing = pacing[(pacing["month_start"].dt.date == month) & (pacing["channel"] == channel)]
month_pacing = month_pacing.sort_values("date_day")
seen = month_pacing[month_pacing["delivered_cumulative"].notna()]

if month_pacing["approved_budget"].isna().all():
    st.info("No approved budget for this channel and month.")
else:
    budget_value = float(month_pacing["approved_budget"].iloc[0])
    profile = month_pacing["pacing_profile"].iloc[0]
    if seen.empty:
        st.info(
            "No delivered spend to compare: "
            + ("this channel has no cost source." if channel == "tiktok"
               else "no day of this month is visible at the cursor yet.")
        )
        view_day = None
    else:
        days = seen["date_day"].dt.date.tolist()
        view_day = st.select_slider(
            "Forecast from day", options=days, value=days[-1],
            help="Shows the month as it looked on that day: delivered spend up to it, "
                 "and the run-rate forecast made from it.",
        )

    theme = "dark" if getattr(st.context.theme, "type", "light") == "dark" else "light"
    colours = PALETTE[theme]
    planned = month_pacing[["date_day", "planned_cumulative"]].rename(
        columns={"planned_cumulative": "value"}).assign(series="Planned")
    layers_data = [planned]
    forecast = None
    if view_day is not None:
        upto = seen[seen["date_day"].dt.date <= view_day]
        layers_data.append(upto[["date_day", "delivered_cumulative"]].rename(
            columns={"delivered_cumulative": "value"}).assign(series="Delivered"))
        last = upto.iloc[-1]
        month_end = month_pacing["date_day"].max()
        forecast = pd.DataFrame({
            "date_day": [last["date_day"], month_end],
            "value": [last["delivered_cumulative"], last["forecast_month_end"]],
            "series": ["Delivered", "Delivered"],
        })
    lines = pd.concat(layers_data)

    colour = alt.Color("series:N", title=None,
                       scale=alt.Scale(domain=list(colours), range=list(colours.values())),
                       legend=alt.Legend(orient="top"))
    base = alt.Chart(lines).encode(
        x=alt.X("date_day:T", title=None, axis=alt.Axis(format="%d %b", grid=False)),
        y=alt.Y("value:Q", title="Cumulative spend (£)", axis=alt.Axis(format=",.0f")),
        color=colour,
    )
    hover = alt.selection_point(fields=["date_day"], nearest=True, on="pointerover", empty=False)
    chart = (
        base.mark_line(strokeWidth=2)
        + base.mark_point(size=80, filled=True).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("date_day:T", title="Date", format="%d %b %Y"),
                     alt.Tooltip("series:N", title="Series"),
                     alt.Tooltip("value:Q", title="Cumulative £", format=",.2f")],
        ).add_params(hover)
        + alt.Chart(pd.DataFrame({"y": [budget_value]})).mark_rule(
            strokeDash=[2, 3], strokeWidth=1, color="gray").encode(y="y:Q")
    )
    if forecast is not None:
        chart += alt.Chart(forecast).mark_line(strokeWidth=2, strokeDash=[6, 4]).encode(
            x="date_day:T", y="value:Q", color=colour)
    st.altair_chart(chart.properties(height=340), width="stretch")
    st.caption(
        f"Grey dotted line: approved media budget £{budget_value:,.2f} ({profile} pacing). "
        "Dashed line: a **simple 7-day run-rate forecast** to month-end (delivered to date + "
        "average daily spend over the last 7 days × days remaining). It ignores seasonality "
        "and planned changes."
    )
    if forecast is not None:
        row = seen[seen["date_day"].dt.date == view_day].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Pacing (delivered ÷ planned, to date)",
                  NO_DATA if pd.isna(row["pacing_ratio"]) else f"{row['pacing_ratio']:.1%}")
        c2.metric("Forecast month-end", gbp(row["forecast_month_end"]))
        c3.metric("Forecast vs budget", variance(row["forecast_overspend"]))

# --------------------------------------------------------------------------
# Invoice reconciliation
# --------------------------------------------------------------------------
st.subheader("Invoice reconciliation, all months")
st.caption(
    "Platform media invoices against delivered spend. Difference = media invoiced − delivered. "
    "Tolerance is the dbt var `recon_tolerance_pct` (default 1.0%). Agency fees are not compared."
)
statuses = ["exception", "unbilled", "unallocated_cost", "within_tolerance", "no_cost_source"]
chosen = st.multiselect("Recon status", statuses, default=["exception", "unbilled", "unallocated_cost"])
shown = recon[recon["recon_status"].isin(chosen)].sort_values(["month_start", "channel"], ascending=[False, True])
st.dataframe(
    pd.DataFrame({
        "Month": shown["month_start"].dt.strftime("%Y-%m"),
        "Channel": shown["channel"],
        "Delivered spend": shown["delivered_spend"].map(gbp),
        "Media invoiced": shown["media_invoiced"].map(gbp),
        "Difference": shown["difference"].map(gbp),
        "Difference %": shown["difference_pct"].map(pct),
        "Fees invoiced": shown["fee_invoiced"].map(gbp),
        "Unbilled accrual": shown["estimated_unbilled_accrual"].map(gbp),
        "Recon status": shown["recon_status"],
        "Data status": shown["data_status"],
    }),
    hide_index=True,
    width="stretch",
)
st.caption(f"{len(shown)} of {len(recon)} month × channel rows.")
