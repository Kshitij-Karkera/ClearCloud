# ClearCloud

A serverless, multi-tenant cost-waste scanner for **AWS, Azure and GCP**. Users sign in, connect read-only cloud accounts, and get one combined list of wasted resources with an estimated monthly saving for each.

> Built as a course project for CSE 5333 Cloud Computing. AWS is the primary platform: it hosts the whole system, while Azure and GCP are scanned remotely.

## The problem

Cloud waste is easy to create and hard to see. An engineer forgets a disk after deleting a VM, reserves an IP that never gets attached, or leaves a large instance running idle. Each provider reports this differently, and teams that use more than one cloud have to check several consoles. Existing FinOps platforms can find this waste, but they are aimed at enterprises: they are priced for dedicated FinOps teams, and the open-source options need self-hosting and setup. Small teams, student groups and research labs fall in between.

## What ClearCloud does

- Scans **AWS, Azure and GCP** for three kinds of waste: unattached disks, unused public IPs, and idle VMs.
- Estimates the monthly cost of each finding and ranks the list by savings.
- Shows everything in one dashboard, tagged by cloud and account.
- Supports many users, each with their own connected accounts and private results.
- Scans every night and on demand ("Scan now").
- Is **read-only**: it reports waste and never changes or deletes anything.

## Architecture

```
Browser (single-page app)
   |  sign in with Cognito, then call the API with a JWT
   v
CloudFront (CDN, HTTPS)
   |-- /*      --> S3 bucket (static site, private, served via Origin Access Control)
   '-- /api/*  --> API Gateway (HTTP API, JWT authorizer backed by Cognito)
                      |
                      v
                  API Lambda --> Secrets Manager   (Azure / GCP credentials, one secret per account)
                      |   |
                      |   '--> DynamoDB            (accounts, reports, history)
                      v
                  SQS scan queue  <-- Dispatcher Lambda <-- EventBridge (daily)
                      |  (dead-letter queue after 3 failed attempts)
                      v
                  Scanner Lambda (scales out, one message per account)
                      |
        +-------------+----------------+
        v             v                v
   AWS (STS        Azure APIs       GCP APIs
   AssumeRole)    (read-only)      (read-only)
```

### How a scan works

1. **Trigger.** EventBridge runs the dispatcher once a day, or a user clicks "Scan now".
2. **Fan-out.** One SQS message is queued per connected account, so accounts are scanned in parallel and one slow or failing account cannot block the others.
3. **Collect.** The scanner Lambda picks up a message and loads the account. A provider module (`aws.py`, `azure.py` or `gcp.py`) calls that cloud's read-only APIs and converts the results into a common format: disks, public IPs and VMs.
4. **Analyze.** A single provider-neutral rule engine (`rules.py`) finds waste in that common format and prices it.
5. **Store.** The report is saved to DynamoDB under the owning user. A history record is kept for 90 days.
6. **View.** The dashboard asks the API for the signed-in user's merged report.

### AWS services used

| Service | Role in the system |
|---|---|
| CloudFront | Serves the site and fronts the API on one domain over HTTPS |
| S3 | Hosts the static frontend privately, accessible only through CloudFront |
| Cognito | User sign-up, email verification, sign-in, and an `admins` group |
| API Gateway (HTTP API) | REST API with a JWT authorizer that validates Cognito tokens |
| Lambda | API handler, dispatcher and scanner, all event-driven |
| DynamoDB | Multi-tenant storage with a sparse index for listing all accounts and TTL for history |
| SQS (+ dead-letter queue) | Fan-out, retries, and a cap on scanner concurrency |
| EventBridge | Nightly schedule |
| Secrets Manager | Per-account Azure and GCP credentials |
| STS | Optional cross-account role assumption for scanning other AWS accounts |
| CloudFormation / SAM | The whole stack is defined as code in `template.yaml` |

## Design decisions

- **One rule engine for every cloud.** Each provider only has to translate its resources into three small shapes. The rules never see cloud-specific data, so adding a fourth cloud means writing one collector and a price table.
- **Serverless end to end.** Nothing runs between scans, so the system costs close to nothing at idle and scales out when many accounts are scanned at once.
- **Queue-based scanning.** SQS decouples "who needs a scan" from "doing the scan". It gives retries and a dead-letter queue for free, and `ScannerMaxConcurrency` protects the account's Lambda limits.
- **Read-only by design.** The tool asks for the least access possible (Reader on Azure, Compute and Monitoring Viewer on GCP, an `ec2:Describe*` style role on AWS), so a leaked credential cannot change infrastructure.
- **Same-origin API.** CloudFront routes `/api/*` to API Gateway, so the browser talks to a single domain and no CORS configuration is needed.
- **Failures are isolated.** If one account has bad credentials or a region fails, the others still complete. The failure shows up as a status and warning on the dashboard instead of an outage.

## Tech stack

Python 3.12 (Lambda), plain HTML/CSS/JavaScript (no framework), AWS SAM / CloudFormation, boto3, the Azure SDK for Python, and the Google Cloud Python SDK.

## Project structure

```
clearcloud/
|-- template.yaml                 # the whole AWS stack (SAM)
|-- local_demo.py                 # run the UI locally with simulated data
|-- scripts/deploy_frontend.sh    # publishes the site and its config to S3 / CloudFront
|-- docs/DEPLOYMENT.md            # step-by-step deployment guide
|-- src/
|   |-- api/                      # API Lambda: accounts, report, scan
|   |-- dispatcher/               # nightly fan-out to SQS
|   |-- scanner/                  # SQS worker, rule engine, cloud collectors
|   |   `-- providers/            # aws.py, azure.py, gcp.py
|   `-- frontend/                 # single-page app (Cognito sign-in, dashboard)
`-- tests/                        # unit tests
```

## Getting started

Try the interface locally with simulated data. No cloud account is needed:

```bash
python local_demo.py
```

Then open http://localhost:8000. The page is labelled "Demo mode", because all of its data is made up.

Run the tests:

```bash
python -m unittest discover tests
```

To deploy to AWS, see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

## Security

- **Authentication.** Cognito issues JWTs, and API Gateway rejects any request without a valid one.
- **Tenant isolation.** Every database key is built from the user ID in the verified token, never from client input, so one user cannot read or delete another's data.
- **Credentials.** Azure and GCP keys go straight into Secrets Manager. The API never returns them, and removing an account deletes its secret.
- **Host account protection.** Scanning the AWS account that hosts ClearCloud is limited to the `admins` group. Other users must connect their own account through a role.
- **Confused-deputy protection.** Cross-account AWS access uses a per-account External ID.
- **Input validation.** Account requests are validated for provider, region, ARN and credential formats, and for size.
- **Abuse limits.** There is a cap on connected accounts per user and a cooldown between scans.
- **Private storage.** The S3 bucket blocks public access and is readable only by CloudFront.

## Status and limitations

- **Azure and GCP collectors are new.** They are covered by unit tests using fake SDK objects, but have not yet been run against live accounts. Expect small SDK adjustments on first use.
- **Cost Explorer may be unavailable** in restricted sandboxes. When it is, spend charts show "unavailable" and findings still work.
- **Spend charts are AWS-only.** Azure and GCP currently report waste findings, not billing.
- **Savings are estimates** from built-in approximate US on-demand price tables. They rank findings and are not billing-accurate.
- **Detection is rule-based**, not machine learning.
- **Sessions last about an hour.** There is no silent token refresh yet, so users sign in again after expiry.
- **CloudFront and the API sit behind HTTPS only**, but there is no custom domain, WAF or alerting yet (see the roadmap).

## Roadmap

- Step Functions to orchestrate scans, and SNS email alerts for new high-savings findings
- CloudWatch dashboard and alarms, X-Ray tracing, and WAF on the public endpoints
- A load test showing scanner concurrency scaling and the queue draining
- Athena queries over archived scan snapshots in S3
- Spend anomaly detection using the stored history
- Azure and GCP billing data
- More rules: old snapshots, stopped VMs with large disks, idle databases