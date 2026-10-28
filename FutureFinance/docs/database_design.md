# Relational schema and normalization

The Oracle schema is in `database/schema.sql`. Surrogate numeric keys identify entities. `TRANSACTIONS` references an owner and category; `BUDGETS` is unique per owner/category/month; goal deposits live in `GOAL_CONTRIBUTIONS` and are not mirrored into transactions, preventing double counting. Analysis outputs are separate records with JSON payloads and timestamps.

**Normalization through 3NF.** 1NF holds because each column stores one value and each table has a primary key. 2NF holds because non-key attributes depend on each table's full key; composite uniqueness is represented with constraints rather than partially dependent attributes. 3NF holds because descriptive category, user, goal, and analysis details are stored in their own relations and referenced by keys; transaction rows do not duplicate category names or user credentials. JSON columns are bounded analysis snapshots, not the authoritative transaction store.

## Application data flow

`app.py` authenticates a user and passes that user's key to the service layer. Services issue Oracle SQL through `database/db.py`; user-facing transaction, budget, goal, recommendation, and simulation records are stored in Oracle. Dashboard anomaly and monthly cluster outputs are derived from those stored transactions and persisted in their respective analysis tables. The app uses parameterized binds, including Oracle-safe names, and applies owner checks before transaction or goal changes.

Each `execute` or `execute_many` helper call owns a connection and commits on success or rolls back that statement on failure. Registration uses one connection for the user row and all starter categories, so that workflow commits or rolls back as a unit. Other multi-statement workflows that call the helpers separately are not automatically one atomic database transaction.

## Query examples

```sql
-- Join transaction to category for one user
SELECT t.transaction_date, c.name, t.amount
FROM transactions t JOIN categories c ON c.category_id=t.category_id
WHERE t.user_id=:user_id ORDER BY t.transaction_date DESC;

-- Monthly category aggregation
SELECT c.name, SUM(t.amount) total
FROM transactions t JOIN categories c ON c.category_id=t.category_id
WHERE t.user_id=:user_id AND t.transaction_type='EXPENSE'
  AND t.transaction_date >= :month_start AND t.transaction_date < ADD_MONTHS(:month_start,1)
GROUP BY c.name;

-- Filter transactions by date and type
SELECT transaction_id, amount FROM transactions
WHERE user_id=:user_id AND transaction_type=:type AND transaction_date>=:start_date;

-- Example transaction boundary for a future multi-statement workflow:
-- execute all statements on one connection, commit after all succeed, rollback on error.
```
