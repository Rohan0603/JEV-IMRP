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

## Audit

Run the deterministic integrity audit with:

```powershell
python -c "from jev_imrp.common.ledger import audit_ledger; print(*audit_ledger(), sep='\n')"
```

The audit rejects reused trial IDs, unknown statuses, placeholder provenance
or version values (`unknown`, `TBD`, and similar), and malformed or non-object
`secondary_metrics_json`. The current shipped baseline deliberately reports
four unresolved provenance placeholders in its single registered row:
`code_commit`, `data_manifest`, `universe_version`, and
`feature_set_version`. Until those fields are replaced by reproducible
metadata, the ledger is not evidence for confirmatory research acceptance.
The CSV itself cannot prove historical edits without an external snapshot, so
append order and unique trial IDs are the audit boundary; corrections remain
new rows that reference the original ID in `notes`.
