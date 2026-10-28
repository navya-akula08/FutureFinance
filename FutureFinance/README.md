# FutureFinance

Explainable financial decision intelligence demo using Flask, Oracle Database 21c XE, pandas/NumPy, scikit-learn, and Plotly. Oracle is the persistent store; the app never falls back to SQLite.

## Setup on Windows

1. Install Python 3.11+ and Oracle Database 21c XE. Create an Oracle schema user and grant the required table/index privileges, or connect as a dedicated application schema.
2. In PowerShell:

```powershell
cd C:\Users\HP\Documents\ChatGPT\dbms\FutureFinance
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

3. Edit `.env`: set a random `FLASK_SECRET_KEY`, Oracle username/password and DSN such as `localhost:1521/XEPDB1`. Never commit `.env`.
4. Run `database/schema.sql` in SQLcl, SQL*Plus, or SQL Developer while connected as the application schema. For example: `@database/schema.sql`.
5. If you created tables from the earlier version, apply `database/migration_analytics.sql` once to add the unique anomaly-result index. For a fresh schema, the index is already in `database/schema.sql`.
6. Start with `python app.py`, then open http://127.0.0.1:5000 and register an account.
7. To populate a separate demo login after schema setup, set `DEMO_USER_PASSWORD` in `.env` and run `python -m database.seed_demo`. The script creates clearly fictional records and refuses to overwrite an existing demo account.
8. Run unit tests using `python -m pytest -v`. Unit tests mock database calls and can run without Oracle; a passing unit suite is not a live Oracle connectivity check.

If Oracle is stopped or credentials/DSN are invalid, database pages report a clear connection message. Registration creates standard categories for the account. The seed SQL labels the seed-data policy; use fictional data only for classroom demonstration. There are no production credentials or preloaded fabricated customer records.

## Features

- Account registration/login with Werkzeug password hashing, Flask sessions, CSRF tokens, and user-scoped reads/writes.
- Simplified primary navigation groups Dashboard, Transactions, Spending, Goals, Insights, and Planning; budget/category management is under Spending, while detailed reports and simulations are under Planning.
- Transaction create/read/update/delete, date/type/search filtering, history, CSV upload with column mapping, validation preview, category suggestions, duplicate checks, and explicit import confirmation, plus filtered CSV export with formula-injection protection. All SQL parameters are bound.
- Transactions also offers a paste-only SMS importer. It never reads a phone inbox; messages are split by blank lines or `---`, parsed conservatively, previewed for correction, and saved only after confirmation. OTP, promotional, and non-transaction text is rejected; uncertain messages need explicit user review. The parser supports common Indian debit/credit wording and explicit day-first or ISO dates, but is heuristic and does not cover every bank's wording.
- Per-user income/expense category creation, monthly category budgets with actual utilization, and savings goals with edit/delete and separately tracked contributions.
- Dashboard focuses on selectable-period income, expenses, net cash flow, recent transactions, a category-spending visualization, concise alerts, and recommendations. Spending contains budgets, categories, statistical monthly profiles, and collapsible detailed trends. Reports and simulation remain available from Planning.
- Dashboard and transaction pages run Isolation Forest on a user's expense history once at least eight records exist, explain unusual scores relative to category history, and upsert each result by transaction.
- Dashboard builds monthly category, essential/discretionary, and savings-rate profiles from Oracle transactions; scaled K-Means selects k by silhouette score when possible, derives descriptions from cluster centers, and persists monthly labels.
- Dashboard refreshes and displays persisted month-over-month category and budget-utilization recommendations, including the underlying numeric comparison. No SHAP explanation is claimed; these are transparent rule-based recommendations.
- Insights includes a conservative recurring-expense heuristic based on repeated payee/description, regular intervals, and similar amounts. Results are labeled as possible matches for the user to confirm.
- CSV import accepts UTF-8, UTF-8 with BOM, Windows-1252, and common Latin-1 text files, up to 2 MB and 5,000 rows. Unsupported binary/control-character and UTF-16/32 files receive an actionable error. The preview is held in application memory for up to 20 minutes and is bound to the signed-in user; if the Flask process restarts, upload the file again. Map date and amount; other columns are optional. Negative amounts become expenses. Category suggestions use visible merchant/description keyword rules, then a user's `Other`/`Salary` category or first compatible category as a clearly labeled fallback. Review suggestions. Invalid rows and duplicates are skipped and summarized; only confirmation writes valid nonduplicate rows to Oracle. A user may correct categories later in Transactions.
- Simulator uses completed monthly income/expense aggregates when at least three observed months are available, bootstraps paired scenarios with defined jitter, compares baseline against selected expense reduction, and reports P10/P50/P90 plus savings-goal attainment probability. Shorter histories are explicitly marked assumption-based. Inputs and summary outputs are saved in Oracle.

## Algorithms and limitations

The forecast uses historical bootstrap sampling with small jitter, not a parametric fitted distribution; it excludes returns, tax, inflation, and changes in user circumstances. With less than three complete prior months, it uses a labeled assumption-based distribution. The savings-goal probability compares the simulated terminal cash balance with the goal's remaining amount. SHAP is not used: recommendations are deterministic comparisons rather than outputs from a fitted prediction model, so their underlying values and thresholds are shown directly. Recurring-expense detection is heuristic and needs user confirmation. Oracle reads and writes for registration, transactions, budgets, goals, reports, insights, and simulation were exercised against the configured Oracle database using temporary test users and cleanup. The existing schema was not recreated or reapplied.

## Database documentation

See `docs/database_design.md` for 3NF notes and SQL examples and `docs/er_diagram.md` for the Mermaid ER model. Schema constraints and indexes are defined in `database/schema.sql`.
