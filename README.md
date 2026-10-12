# ClearCloud

A serverless AWS cost-waste scanner with a plain-language dashboard.

**What it does:** once a day it scans your AWS account for wasted resources
(unattached EBS volumes, unassociated Elastic IPs, idle EC2 instances), estimates
how much each one costs per month, and shows a to-do list of what to fix.
It is read-only: it never changes your infrastructure.

## Architecture

```
EventBridge (daily) -> Lambda "scanner" -> DynamoDB (on-demand)
                          |  reads: EC2, EBS, CloudWatch, Cost Explorer
Browser -> Lambda Function URL -> Lambda "api" -> DynamoDB
```

| Piece | AWS service |
|---|---|
| Scheduled scan | EventBridge + Lambda |
| Storage | DynamoDB (pay-per-request) |
| Dashboard + JSON API | Lambda Function URL |
| Permissions | IAM role (LabRole in AWS Academy) |

Nothing runs between scans, so you pay only for the few seconds of Lambda time.

## Try it locally first (no AWS needed)

```bash
python local_demo.py
# open http://localhost:8000
```

This serves the real dashboard with sample data produced by the real detection rules.

## Run the tests

```bash
python -m unittest discover tests
```

## Deploy to AWS Academy

> AWS Academy accounts cannot create IAM roles, so the template runs both
> functions as the existing **LabRole**.

1. Start your Learner Lab and open the AWS Console. Note your **account ID**.
2. Open **CloudShell** (top bar of the console), then upload this project folder
   (Actions > Upload file with the zip, then `unzip clearcloud.zip`).
3. Install SAM if CloudShell does not have it: `pip install aws-sam-cli`
4. Deploy:

```bash
cd clearcloud
sam build
sam deploy --guided \
  --stack-name clearcloud \
  --parameter-overrides LabRoleArn=arn:aws:iam::<ACCOUNT_ID>:role/LabRole Regions=us-east-1
```

Accept the defaults when prompted. Answer **y** when it warns that the
Function URL is publicly accessible (see Security below).

5. Run the first scan manually so the dashboard has data:

```bash
aws lambda invoke --function-name clearcloud-scanner out.json && cat out.json
```

6. Open the `DashboardUrl` printed in the stack outputs.

### To see real findings during the demo

Create a few wasted resources in the sandbox, then re-run the scanner:
- an unattached EBS volume (EC2 > Volumes > Create volume, don't attach it)
- an unassociated Elastic IP (EC2 > Elastic IPs > Allocate)
- a small EC2 instance left idle (CPU data takes a little while to appear in CloudWatch)

Delete them afterwards so they don't use up your lab budget.

## Known limits (be upfront about these in the pitch/Q&A)

- **Cost Explorer may be blocked in AWS Academy.** If so, the spend chart shows
  "unavailable" and the resource findings still work. The code handles this
  without failing.
- **Savings are estimates** from a small built-in us-east-1 price table, used to
  rank findings, not exact billing figures.
- **AWS-only.** Multi-cloud is the long-term vision, not this build.
- **Rule-based detection**, not ML yet. A natural mid-report extension is
  anomaly detection on daily spend history.

## Security notes

- The dashboard Function URL is public (`AuthType: NONE`) to keep the demo simple.
  It exposes resource IDs and spend figures. For anything beyond a class demo, put
  it behind `AWS_IAM` auth or Amazon Cognito, or delete the stack after presenting.
- Lambda only needs read access to EC2, CloudWatch, Cost Explorer and the
  DynamoDB table. LabRole is broader than that; in your own account, create a
  least-privilege role instead.

## Clean up

```bash
sam delete --stack-name clearcloud
```

## Ideas for the mid-report (Oct 19-21)

- Spend anomaly detection using the stored `history#` items
- "Scan now" button (API Lambda invokes the scanner)
- More rules: old snapshots, stopped instances with large volumes, idle RDS
- SNS/email alert when a new high-savings finding appears