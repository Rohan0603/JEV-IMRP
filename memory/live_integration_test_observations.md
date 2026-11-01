--- 
name: live_integration_test_observations
description: Observations and analysis of the live integration test setup and execution
metadata:
  type: reference
--- 

## **Observations and Analysis**

### **1. Test Script Overview**

The `scripts/live_integration_test.py` script was designed to test the complete pipeline from **Shoonya broker login → symbol resolution → historical data backfill → Shoonya feed client integration → JEV client integration → JEV runner**. However, **it cannot execute live operations without the required credentials** (`SHOONYA_ACCESS_TOKEN`, `SHOONYA_API_SECRET`, `TYPESAFE_API_KEY`).

### **2. Key Limitations**

#### **A. Missing Credentials**
- The script **fails at runtime** because the environment variables required for live operations are not set.
- **`SHOONYA_ACCESS_TOKEN`** is missing (OAuth flow not completed).
- **`SHOONYA_API_SECRET`** is missing (required for token exchange).
- **`TYPESAFE_API_KEY`** is present but not validated for live JEV calls.

#### **B. Credential Handling**
- The script **does not attempt to fetch credentials dynamically** (e.g., via OAuth flow).
- It **depends on manual setup** of `.env` with all required credentials.

#### **C. Test Execution**
- The script **prints a clear error** when credentials are missing:
  ```
  ERROR: Missing environment variable SHOONYA_ACCESS_TOKEN
  ERROR: Missing environment variable SHOONYA_API_SECRET
  ```

### **3. Required Actions**

#### **A. Complete the OAuth Flow**
- Run `scripts/oauth_login_script.py` to obtain a valid `SHOONYA_ACCESS_TOKEN`.
- Add the token and `SHOONYA_API_SECRET` to `.env`.

#### **B. Set `TYPESAFE_API_KEY`**
- Ensure `TYPESAFE_API_KEY` is set in `.env` or environment variables.

#### **C. Run the Test**
- After setting credentials, execute:
  ```bash
  cd scripts
  python live_integration_test.py
  ```

### **4. Expected Output**

If credentials are correct, the test should:
1. **Resolve `SBIN-EQ` via symbol master** (preferred) or `SearchScrip` (fallback).
2. **Fetch TPSeries and EODChartData** for `SBIN-EQ` from 2024-01-01 to 2024-01-02.
3. **Write RawEvents via Recorder** (successful if no errors).
4. **Call the live JEV model** (`jev-1.13.0`) and print the response.
5. **Run the `JevRunner`** and store attempts in `experiments/attempts.jsonl`.

### **5. Debugging Steps**

#### **A. Verify Credentials**
- Check `.env` for missing variables:
  ```bash
  grep -A 10 "SHOONYA" .env
  grep -A 10 "TYPESAFE" .env
  ```

#### **B. Test Symbol Resolution**
- Manually verify symbol resolution:
  ```bash
  python -c "
  from jev_imrp.ingest.symbol_resolver import resolve_symbol
  print(resolve_symbol('NSE', 'SBIN-EQ', use_master=True, user_id='FN248368_U'))
  "
  ```

#### **C. Test JEV Request**
- Manually verify JEV request (if credentials are correct):
  ```bash
  python -c "
  from jev_imrp.models.jev_client import LiveConfig, accept_reject_question, build_state, Candidate
  from jev_imrp.models.jev_mock import MockJevClient
  import os
  config = LiveConfig(model_id='jev-1.13.0', confirmatory=True)
  c = Candidate(candidate_id='test', symbol='SBIN-EQ', side='BUY', decision_timestamp_utc='2026-10-27T09:30:00+05:30', horizon_minutes=30, features={'spread_bps': 2.5, 'atr_pct': 0.8}, feature_set_version='fs_v1')
  state = build_state(c)
  q = accept_reject_question()
  req = {'state': state, 'model': 'jev-1.13.0', 'questions': {'jev_accept': q.to_payload()}}
  print('Request keys:', sorted(req.keys()))
  "
  ```

### **6. Next Steps**

1. **Complete the OAuth flow** using `scripts/oauth_login_script.py`.
2. **Set all required credentials** in `.env`.
3. **Run the test** and verify the output.
4. **Update `.gitignore`** to exclude `experiments/attempts.jsonl`.
5. **Document the test** in `README.md`.

--- 

## **Why This Matters**

This test is critical for verifying the **end-to-end pipeline** and ensuring that:
- The Shoonya broker integration works correctly.
- The JEV client integration is stable.
- The recorder and attempt store persist data correctly.

By completing these steps, you’ll have a **fully functional live integration test** that can be used for future validation and CI checks.