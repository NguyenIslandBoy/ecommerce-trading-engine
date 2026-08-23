"""Loads the metric spine and slices it to a cursor.

Every detector reads a :class:`Context` and nothing else. That is what makes the
backtest honest: a detector physically cannot see a row that had not arrived by
its cursor, because the Context never hands it one.

Cohort marts get a different treatment from the daily spine. A cohort's SIZE
changes with the cursor -- at 2025-06-15 the June cohort is half-formed -- so
truncating a finished mart would misstate it. But a cohort only becomes usable
once its observation window has closed, which is at least 90 days after the
cohort month ends, by which point it has long been complete. So filtering on the
exposure dates is exact for every row a detector is allowed to use, and wrong
only for rows the exposure guard already excludes. scripts/verify_pit.py checks
that against real rebuilds rather than leaving it as an argument.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from engine.pit import _as_date, daily_trading_as_of, visible_window_end

ROOT = Path(__file__).resolve().parents[1]
MARTS = ROOT / "data" / "marts"
CONFIG = ROOT / "config"


def _read(name: str) -> pd.DataFrame:
    path = MARTS / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Build the warehouse and export it first:\n"
            f"  cd dbt && ../venv/Scripts/dbt.exe build --profiles-dir .\n"
            f"  venv/Scripts/python.exe scripts/export_marts.py"
        )
    return pd.read_parquet(path)


@dataclass
class Context:
    as_of: dt.date
    window_end: dt.date          # last day actually visible (cursor, clamped)
    daily: pd.DataFrame
    product: pd.DataFrame
    email: pd.DataFrame
    quality: pd.DataFrame
    ltv: pd.DataFrame
    retention: pd.DataFrame
    ads: pd.DataFrame
    config: dict

    def detector_config(self, name: str) -> dict:
        return self.config.get("detectors", {}).get(name, {})

    @property
    def gap_days(self) -> set[dt.date]:
        """Source-days with an ingestion problem, for DATA_QUALITY classification."""
        if self.quality.empty:
            return set()
        flagged = self.quality[self.quality["issue_type"] != "ok"]
        return set(flagged["date_day"].map(_as_date))


class Warehouse:
    """Holds one full build in memory and serves any cursor from it."""

    def __init__(self) -> None:
        self.daily = _read("mart_daily_trading")
        self.product = _read("mart_product_daily")
        self.email = _read("mart_email_flow_weekly")
        self.quality = _read("mart_data_quality")
        self.ltv = _read("mart_ltv")
        self.retention = _read("mart_cohort_retention")
        self.ads = _read("fct_ad_spend_daily")
        with open(CONFIG / "detectors.yml", encoding="utf-8") as handle:
            self.config = yaml.safe_load(handle)

    @property
    def last_day(self) -> dt.date:
        return _as_date(self.daily["date_day"].max())

    @property
    def first_day(self) -> dt.date:
        return _as_date(self.daily["date_day"].min())

    def at(self, cursor) -> Context:
        cursor = _as_date(cursor)
        end = visible_window_end(self.daily, cursor)

        def upto(frame: pd.DataFrame, column: str) -> pd.DataFrame:
            return frame[frame[column].map(_as_date) <= end].copy()

        # Ads and email land a day late, so at cursor D only D-1 is visible.
        lagged = cursor - dt.timedelta(days=1)

        def upto_lagged(frame: pd.DataFrame, column: str) -> pd.DataFrame:
            cutoff = min(lagged, end)
            return frame[frame[column].map(_as_date) <= cutoff].copy()

        ltv = self.ltv.copy()
        ltv["has_full_exposure"] = ltv["exposure_end"].map(_as_date) <= end
        ltv.loc[~ltv["has_full_exposure"], ["ltv_revenue", "ltv_margin"]] = pd.NA

        retention = self.retention.copy()
        retention["has_full_exposure"] = retention["window_end"].map(_as_date) <= end
        retention["has_full_90d_exposure"] = (
            retention["exposure_90d_end"].map(_as_date) <= end
        )
        retention.loc[~retention["has_full_exposure"], "retention_rate"] = pd.NA
        retention.loc[~retention["has_full_90d_exposure"], "repeat_rate_90d"] = pd.NA
        # A cohort that has not started yet does not exist at this cursor.
        retention = retention[retention["cohort_month"].map(_as_date) <= end]
        ltv = ltv[ltv["cohort_month"].map(_as_date) <= end]

        return Context(
            as_of=cursor,
            window_end=end,
            daily=daily_trading_as_of(self.daily, cursor),
            product=upto(self.product, "date_day"),
            email=upto_lagged(self.email, "week_start"),
            quality=upto(self.quality, "date_day"),
            ltv=ltv,
            retention=retention,
            ads=upto_lagged(self.ads, "ad_date"),
            config=self.config,
        )
