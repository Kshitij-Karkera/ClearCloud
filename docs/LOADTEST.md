# Load test

This document describes how ClearCloud's scan fan-out was load tested, what the results were, and what they do and do not show.

## What was tested

The daily scan path is: EventBridge → Dispatcher Lambda → SNS scan topic (one message per account) → Scanner Lambda, running in parallel. The load test exercises everything from the SNS topic onward, exactly as the dispatcher does:

1. `scripts/loadtest.py` creates N synthetic AWS "host account" records in DynamoDB.
2. It publishes one scan message per account to the scan topic, in batches of 10.
3. It polls DynamoDB once per second until every account has a scan status, then records when each scan started and finished.
4. It deletes the synthetic records and writes `loadtest_<N>_<run_id>.json`.
5. With `--metrics` it also reads the scanner Lambda's CloudWatch metrics for the run window.

Run it in CloudShell, in the account that hosts the stack:

```
python3 scripts/loadtest.py --accounts 100 --metrics
```

The script refuses to run if the account has waste (an unattached volume, a free Elastic IP or a running instance), because every synthetic account would otherwise send an alert email. `--force` overrides this.

## Results

Environment: AWS Academy sandbox, us-east-1, scanner Lambda (Python 3.13), one real host account with no waste. Every synthetic account scans that same real account.

| Accounts | OK | Failed | Wall time (s) | Mean scan (s) | p95 scan (s) | Run |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 2.07 | 1.93 | 1.93 | b14ccb26 |
| 10 | 10 | 0 | 4.12 | 3.42 | 3.59 | 7190c8ac |
| 50 | 50 | 0 | 4.27 | 3.37 | 3.73 | c4173bc2 |
| 100 | 100 | 0 | 4.45 | 2.98 | 3.81 | 79a6dcf0 |
| 300 (default retries) | 220 | 80 | 38.66 | 10.77 | 21.42 | 6b6f0868 |
| 300 (default retries) | 212 | 88 | 25.87 | 10.57 | 21.60 | a0f3d0ec |
| 300 (adaptive retries) | 298 | 2 | 112.41 | 16.49 | 58.95 | 5eb077f2 |

Wall time is the time from publishing the messages until the last scan finished. Scan time is the time from a scan starting until it was observed finished.

CloudWatch, scanner Lambda, for the three 300-account runs:

| Run | Invocations | Errors | Throttles | Max concurrent | Mean duration (ms) | Max duration (ms) |
|---|---:|---:|---:|---:|---:|---:|
| 6b6f0868 | 300 | 0 | 0 | 300 | 10,341 | 37,031 |
| a0f3d0ec | 300 | 0 | 0 | 300 | 10,160 | 23,703 |
| 5eb077f2 | 300 | 0 | 0 | 300 | 15,851 | 110,983 |

### Scaling from 1 to 100 accounts

Going from 1 to 100 accounts (100 times the work) increased wall time from 2.07 s to 4.45 s, with no failures. The scans run in parallel, so total time stays nearly flat. Scanning 100 accounts one after another would take about 193 s (100 × the 1.93 s single-account time). That figure is calculated from the single-account run, not measured.

### The 300-account runs

At 300 accounts, Lambda itself behaved well: all 300 scans ran concurrently, with no Lambda errors and no throttles. The failures came from the AWS EC2 API.

- **Cause (run a0f3d0ec, with the error text captured):** all 88 failures were `RequestLimitExceeded` on `DescribeVolumes` after 4 retries (the default). The account's EC2 API was throttled. The error text of the first 300 run (6b6f0868, 80 failures) was not captured, since the script only started recording error text afterwards. It is likely to have the same cause, but that was not verified.
- **Why it happens in this test:** all 300 synthetic accounts scan the same real AWS account, so they share one EC2 API request quota. In production each customer account has its own quota, so this pattern would not normally occur.
- **Change made:** the AWS provider now uses botocore's adaptive retry mode with up to 10 attempts (`RETRY_CONFIG` in `src/scanner/providers/aws.py`). Adaptive mode slows requests down when it sees throttling.
- **Effect (run 5eb077f2):** failures dropped from 80 and 88 to 2. The 2 remaining failures were the same `RequestLimitExceeded` error, after 10 attempts. The cost is time: wall time rose from about 26–39 s to 112 s, the p95 scan time from about 21 s to 59 s, and the longest scan to 111 s.
- A failed scan is recorded as an error on the account and is retried by the next scheduled or manual scan. No data is lost.

## Limits of this test

- **Shared API quota.** See above. The result shows how ClearCloud behaves when many scans hit one account at once. It is not a measurement of how many separate customer accounts it supports.
- **Small scan workload.** The scanned account had no waste and few resources, so each scan was short. A larger account takes longer per scan.
- **One to three runs per configuration.** The two default-retry runs differ (80 vs 88 failures, 38.7 s vs 25.9 s), so the numbers vary from run to run. The improvement from adaptive retries is real but is not tightly characterised.
- **Measurement resolution.** The script polls DynamoDB about once per second, so timings are accurate to about a second. Start times come from the scanner's own `last_scan_at` timestamp.
- **Not tested beyond 300 accounts**, and the sandbox is a single AWS Academy account with its own service limits. The API currently limits a user to 10 accounts, so real use is far below the tested range.
- **Azure and GCP collectors were not load tested.** Only the AWS path was exercised.