# Trial ledger

`experiments/trial_ledger.csv` is the append-only record of **every** experiment
attempted against the candidate stream: each feature configuration, prompt,
model version, threshold, horizon, target, cost scenario and test, per plan
sections 3.3 and 9.4. Failed and abandoned attempts are recorded with a
`status` of `failed` or `abandoned` and still count as trials for
selection-bias accounting; the header-only file means zero trials have been run,
not that the ledger is unused. One row is appended per trial *before* its result
is inspected, `trial_id` is unique and monotonic, rows are never edited or
deleted (a correction is a new row that references the original `trial_id` in
`notes`), and `secondary_metrics_json` holds a JSON object for secondary results
while `primary_metric_value` holds only the single pre-registered primary
metric. The exact schema is the CSV header in that file, and it is loaded and
round-tripped by `tests/test_trial_ledger.py`; adding a column is a dated
amendment to `PREREGISTRATION.md`, not an ad-hoc edit.
