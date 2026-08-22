# Data Engineer — Technical task

**eComplete · Confidential**

## The problem

eComplete builds managed data platforms for D2C brands. We’re working toward a capability we think of as an **eCommerce Trading Engine** — a system that monitors brand performance data, detects meaningful signals, generates commercial recommendations, and quantifies the confidence behind those recommendations.

Think algorithmic trading, but instead of financial instruments, the system trades on ecommerce levers: channel spend, email flows, inventory, pricing, promotional timing.

We’d like you to build the foundation for this.

## What we’re giving you

A synthetic dataset representing 12 months of a D2C brand’s operations. The zip folder contains the following files:

| File | Description | Rows |
|---|---|---:|
| `orders.csv` | Order-level data (Shopify schema) | 26,553 |
| `order_lines.csv` | Line-item detail per order | 42,779 |
| `customers.csv` | Customer profiles and marketing consent | 20,817 |
| `products.csv` | Product catalogue with variants and pricing | 24 |
| `meta_ads_daily.csv` | Daily Meta Ads campaign performance | 2,178 |
| `google_ads_daily.csv` | Daily Google Ads campaign performance | 1,825 |
| `email_flows.csv` | Klaviyo email flow engagement data | 636 |

The data contains real-looking patterns and planted signals. Part of the challenge is finding them.

## What we’re asking you to build

The full scope has three layers. Go as deep as you can. Where you stop building, show us your thinking — a clear explanation of your approach, architecture, and reasoning is valued as highly as working code.

### 1. Data foundation and signal detection

Model the raw data into a clean, analytics-ready data layer. Then build a detection layer that surfaces meaningful signals — things a brand operator or investor would want to act on.

- Transform the raw CSVs into a structured data model (staging, core, semantic tables)
- Detect anomalies and patterns: cohort retention shifts, CAC/LTV threshold breaches, product velocity changes, conversion rate movements, engagement decay
- Distinguish between signals that indicate a data quality issue and signals that indicate a genuine commercial event

### 2. Recommendation engine

Map detected signals to recommended actions. Each recommendation should include a confidence score based on signal strength and data quality.

For example:

> “Meta CAC has exceeded the 60-day LTV threshold for 5 consecutive days — recommend reallocating 20% of Meta budget to Google.”

The quality we’re looking for here is commercial judgement — can you connect a quantitative signal to an action that makes business sense?

### 3. Outcome simulation

Build a simulation layer that models the expected outcome of each recommendation under uncertainty.

- Simulate scenarios (Monte Carlo or similar) using historical variance to define input ranges
- Produce a distribution of expected outcomes: revenue impact, margin impact, probability of positive ROI
- Score each recommendation with a confidence interval rather than a point estimate
- Consider whether the recommendation is reversible (ad spend reallocation) or irreversible (inventory purchase) and how that should affect the engine’s willingness to act autonomously vs flag for human review

## Tech stack

Use whatever you’re comfortable with. We suggest:

| Component | Suggested tool |
|---|---|
| SQL modelling | DuckDB — free, local, reads CSVs natively, BigQuery-adjacent syntax |
| ML / simulation | Python (pandas, numpy, scikit-learn, scipy) |
| Walkthrough | Jupyter Notebook |
| Deliverable | GitHub repository |

No cloud accounts or paid tools required.

## Deliverable

A GitHub repository. Suggested structure:

- `/data` — the provided CSVs
- `/sql` — your data models
- `/src` — Python code (detection, recommendations, simulation)
- `/notebooks` — Jupyter walkthrough of your approach and results
- `README.md` — your approach, assumptions, and what you’d build next

Structure it however makes sense to you — the above is a suggestion, not a requirement.

## How we’ll evaluate this

We’re looking at five things:

1. **Data modelling quality** — is the SQL clean, modular, and well-documented? Does the model handle ecommerce data properly?
2. **Signal detection** — did you find the meaningful patterns? Can you distinguish signal from noise?
3. **Commercial reasoning** — do your recommendations make business sense? Is the logic connecting signal to action sound?
4. **Depth of thinking** — where you built it, does it work? Where you didn’t, does your write-up show you understand the problem well enough to design a solution?
5. **Communication** — can you explain what you did, why, and what you’d do next in a way a non-technical stakeholder would follow?

A partial build with strong reasoning and clear documentation is more valuable to us than a complete build with shallow thinking.

## A few other things

- Use AI tools freely. We use Claude extensively and expect this role to do the same. If AI helps you move faster or think better, use it. We’re interested in the output and your judgement, not whether you wrote every line from scratch.
- We respect your time. This is an open-ended problem. Go as deep as your interest and availability allow. We’d rather see something focused and well-explained than something sprawling and unfinished.
- Ask questions if you need to. If something in the dataset or brief is unclear, reach out. That’s a signal of good judgement, not a weakness.
