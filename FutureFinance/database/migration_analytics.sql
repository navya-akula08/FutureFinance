-- Apply once to an existing database created with the earlier FutureFinance schema.
-- The initial implementation did not persist anomaly rows, so the index should be clear to create.
CREATE UNIQUE INDEX uq_anomaly_transaction ON anomaly_results(transaction_id);
