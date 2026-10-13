"""
Publish the exported marts to BigQuery, so they can be queried there.

The warehouse is BUILT in DuckDB by dbt and only PUBLISHED to BigQuery: this
script loads the Parquet files that scripts/export_marts.py wrote, one table
each, into dataset `trading_engine_marts` with WRITE_TRUNCATE. It is a manual
step and not part of CI.

The three mart_marketing_* tables rest on SYNTHETIC Finance inputs; their
BigQuery table descriptions say so.

Config, from the environment or a .env file in the repo root (the
environment wins):
    BQ_PROJECT_ID                    target project (required)
    GOOGLE_APPLICATION_CREDENTIALS   service-account key path (required)
    BQ_LOCATION                      dataset location, default US

    venv/Scripts/python.exe scripts/publish_to_bigquery.py --dry-run
    venv/Scripts/python.exe scripts/publish_to_bigquery.py

--dry-run lists the tables and row counts it would load and never imports the
BigQuery client or touches the network. A real run re-reads each loaded
table's row count from BigQuery and exits 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
MARTS_DIR = REPO_ROOT / "data" / "marts"
DATASET = "trading_engine_marts"

TABLES = [
    # Marketing reporting marts, on SYNTHETIC Finance inputs.
    "mart_marketing_budget_vs_actual",
    "mart_marketing_pacing_daily",
    "mart_marketing_invoice_recon",
    # The existing marts. All small, so publishing them costs nothing extra.
    "mart_daily_trading",
    "mart_product_daily",
    "mart_cohort_retention",
    "mart_ltv",
    "mart_email_flow_weekly",
    "mart_data_quality",
]

SYNTHETIC_DESCRIPTION = (
    "Built on SYNTHETIC Finance inputs (invented budgets and invoices) against real-shaped ad "
    "spend. Not company data. Built in DuckDB by dbt; published from Parquet."
)
DEFAULT_DESCRIPTION = "Built in DuckDB by dbt; published from Parquet by scripts/publish_to_bigquery.py."


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; the real environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value


def local_tables() -> list[tuple[str, Path, int]]:
    found = []
    missing = []
    for name in TABLES:
        path = MARTS_DIR / f"{name}.parquet"
        if not path.exists():
            missing.append(name)
            continue
        found.append((name, path, pq.ParquetFile(path).metadata.num_rows))
    if missing:
        sys.exit(f"Missing Parquet for: {', '.join(missing)}. Run dbt build and scripts/export_marts.py first.")
    return found


def publish(tables: list[tuple[str, Path, int]], project: str, location: str) -> bool:
    from google.cloud import bigquery  # imported only for a real run

    client = bigquery.Client(project=project, location=location)
    dataset = bigquery.Dataset(f"{project}.{DATASET}")
    dataset.location = location
    dataset.description = "Reporting marts from the ecommerce trading engine (dbt on DuckDB)."
    client.create_dataset(dataset, exists_ok=True)

    config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        # The existing marts carry DECIMAL(38,2) sums, wider than NUMERIC's 29
        # integer digits; let BigQuery pick NUMERIC where it fits, else BIGNUMERIC.
        decimal_target_types=["NUMERIC", "BIGNUMERIC"],
    )

    ok = True
    for name, path, expected in tables:
        table_id = f"{project}.{DATASET}.{name}"
        with path.open("rb") as handle:
            client.load_table_from_file(handle, table_id, job_config=config).result()
        table = client.get_table(table_id)
        table.description = SYNTHETIC_DESCRIPTION if name.startswith("mart_marketing_") else DEFAULT_DESCRIPTION
        client.update_table(table, ["description"])
        actual = client.get_table(table_id).num_rows
        status = "ok" if actual == expected else "MISMATCH"
        ok &= actual == expected
        print(f"{name:<34} parquet {expected:>6}  bigquery {actual:>6}  {status}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish exported marts to BigQuery.")
    parser.add_argument("--dry-run", action="store_true",
                        help="list tables and row counts; do not connect")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    tables = local_tables()
    location = os.environ.get("BQ_LOCATION") or "US"
    project = os.environ.get("BQ_PROJECT_ID")

    if args.dry_run:
        target = f"{project}.{DATASET}" if project else f"<BQ_PROJECT_ID>.{DATASET}"
        print(f"DRY RUN: would load {len(tables)} tables into {target} ({location}), WRITE_TRUNCATE")
        for name, _, rows in tables:
            label = "  SYNTHETIC Finance inputs" if name.startswith("mart_marketing_") else ""
            print(f"  {name:<34} {rows:>6} rows{label}")
        return

    missing = [v for v in ("BQ_PROJECT_ID", "GOOGLE_APPLICATION_CREDENTIALS") if not os.environ.get(v)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (environment or .env), or use --dry-run.")
    if not Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]).exists():
        sys.exit("GOOGLE_APPLICATION_CREDENTIALS does not point to an existing file.")

    sys.exit(0 if publish(tables, project, location) else 1)


if __name__ == "__main__":
    main()
