# Deployment guide

This guide deploys ClearCloud to AWS. It is written for the AWS Academy Learner Lab but works in any AWS account that allows the services listed in the README.

## Prerequisites

- An AWS account or an active Learner Lab session
- AWS CloudShell (or a machine with the AWS CLI and AWS SAM CLI)
- Your account ID (shown in the top-right menu of the AWS console)

> **AWS Academy note:** student accounts cannot create IAM roles, so the Lambdas run as an existing role (`LabRole`, or `lambda-run-role` in the sandbox). The template takes its ARN as a parameter.

> **Normal AWS account:** the script can instead create its own least-privilege role. Use `create` in place of the role ARN (see step 2).

> **Sandbox note:** the sandbox blocks creating Cognito, API Gateway, SQS and Secrets Manager resources, so this stack uses DynamoDB-backed sign-in, a Lambda Function URL, SNS and SSM Parameter Store instead (see the README).

## 1. Get the code into CloudShell

Either clone the repository:

```bash
git clone https://github.com/<your-username>/ClearCloud.git
cd ClearCloud
```

For a private repository, GitHub asks for a username and a personal access token as the password. Alternatively, download the repository as a ZIP from GitHub, upload it through CloudShell's Actions menu, and unzip it.

If `sam` is not installed: `pip install aws-sam-cli`

## 2. Build and deploy the backend

Find a role the Lambdas can run as. In a Learner Lab it is `LabRole`. In a sandbox without it, list the roles Lambda can assume and pick one:

```bash
aws iam list-roles --query "Roles[?contains(to_string(AssumeRolePolicyDocument),'lambda.amazonaws.com')].Arn" --output text
```

CloudShell has Python 3.13, which matches the template. Then build and deploy with the script. The usage is:

```
./scripts/deploy_backend.sh <stack-name> <role-arn | create> [admin-email] [alert-email]
```

- **Normal AWS account:** use `create`. The stack builds its own role and no credentials are copied.
- **AWS Academy sandbox:** pass the ARN of an existing role instead of `create`.
- The first email gets admin rights (it may scan the account that hosts ClearCloud), so use the email you sign in with. The optional second email is the operator address. It receives every alert and every alarm (see "Monitoring and alerts" below).
- Set `ENABLE_ALARMS=true` to create the CloudWatch alarms, and `OPEN_HOST_SCAN=true` to let every signed-in user scan the account that hosts ClearCloud. Use the second only for a class demo.

Normal account, for example:

```bash
OPEN_HOST_SCAN=true ENABLE_ALARMS=true ./scripts/deploy_backend.sh clearcloud create you@example.com alerts@example.com
```

Sandbox, for example (replace the ARN and emails with yours):

```bash
chmod +x scripts/deploy_backend.sh
./scripts/deploy_backend.sh clearcloud arn:aws:iam::<ACCOUNT_ID>:role/<ROLE_NAME> you@example.com alerts@example.com
```

If CloudShell cannot fetch credentials (`Unable to retrieve credentials ... 500`), copy the `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` values from AWS Academy (AWS Details, then AWS CLI) into environment variables in the shell. The script uses them when they are already set.

The script also creates the secret that signs sign-in tokens and stores it in `~/.clearcloud_jwt_secret`, so redeploys keep users signed in. If an earlier attempt left the stack in `ROLLBACK_COMPLETE`, the script deletes it first.

The script reads your current session credentials and passes them to the stack, so the Lambdas can call DynamoDB, SNS, SSM and the other services even when the execution role only allows logging. **These credentials expire when the session ends.** When scans start failing with `ExpiredToken`, start the lab again and re-run the script. In a normal AWS account, deploy with `sam deploy --guided` instead and leave the credential parameters empty.

Never commit these credentials or show them on screen.

The first build installs the Azure and GCP SDKs and can take a few minutes. If it fails on disk space or time, remove the `azure-*` and `google-*` lines from `src/scanner/requirements.txt` and rebuild; AWS scanning works without them.

The first deployment also creates a CloudFront distribution, which can take 5 to 10 minutes.

## 3. Publish the frontend

```bash
./scripts/deploy_frontend.sh clearcloud
```

This reads the stack outputs, writes the site's `config.json`, uploads the site to S3, and invalidates the CloudFront cache. It prints the site URL when done. The `config.json` includes the ARN of the role ClearCloud runs as, which the "How to connect your own AWS account" panel puts into the trust policy.

Run it again whenever you change anything in `src/frontend/`, and after every backend deploy that could change the role. If CloudShell says `Permission denied`, run it as `bash scripts/deploy_frontend.sh clearcloud`.

## 4. Create your first user

Open the site URL and use **Create account**. Passwords need 8 or more characters with an uppercase letter, a lowercase letter and a number. You are signed in straight away. There is no email verification.

## 5. Make yourself an admin

Emails listed in the `AdminEmails` parameter may scan the AWS account that hosts ClearCloud. Everyone else must connect their own account through a role, unless you deployed with `OPEN_HOST_SCAN=true`, which lets every signed-in user scan the host account (a class demo mode). To change the admin list, re-run the deploy script with the email as the third argument (keep the alert email as the fourth), then sign out and back in, because the admin flag is stored in the sign-in token.

## 6. Connect accounts and scan

Sign in, then use **Connect an account**:

- **AWS (this account):** choose AWS, add a label, set the region to where your resources are (for example `us-east-2`), leave the role ARN empty, and click Connect. This works for admins, or for everyone when `OPEN_HOST_SCAN=true`.
- **Another AWS account:** see "Another AWS account" below. The account must be verified before it is scanned.
- **Azure:** see below. The credentials are tested with Azure when you connect.
- **GCP:** see below. The key is tested with Google when you connect.

Click **Scan now**. Results appear when the scan finishes, usually within a minute.

### Azure

Create a read-only service principal (Azure CLI or Cloud Shell):

```bash
az ad sp create-for-rbac --name clearcloud-reader --role Reader \
  --scopes /subscriptions/<SUBSCRIPTION_ID>
```

Paste the values into the form (Azure is asked to confirm them, so a wrong value is rejected with an explanation): `tenant` is the tenant ID, `appId` is the client ID, `password` is the client secret, and add your subscription ID.

### GCP

1. In IAM, create a service account with the **Compute Viewer** and **Monitoring Viewer** roles, and create a JSON key for it.
2. Enable the **Compute Engine API** and **Cloud Monitoring API** on the project.
3. Paste the entire key file into the form, then delete the key file from your machine.

### Another AWS account (cross-account)

The Connect form has a collapsible panel, **How to connect your own AWS account**, with the same steps and both policies ready to copy. In short, in the account you want to scan:

1. In IAM, create a role named `ClearCloudReadOnly` with this permissions policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ec2:Describe*", "cloudwatch:GetMetricStatistics", "ce:GetCostAndUsage"],
    "Resource": "*"
  }]
}
```

2. Give it this trust policy. The principal is the role ClearCloud runs as, which the panel fills in for you (it is also `aws lambda get-function-configuration --function-name clearcloud-scanner --query Role --output text`). Leave `PASTE-EXTERNAL-ID-HERE` for now:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::<CLEARCLOUD_ACCOUNT_ID>:role/<CLEARCLOUD_LAMBDA_ROLE>" },
    "Action": "sts:AssumeRole",
    "Condition": { "StringEquals": { "sts:ExternalId": "PASTE-EXTERNAL-ID-HERE" } }
  }]
}
```

3. In ClearCloud, connect the account with the role's ARN. It appears as **Not verified** and shows its **External ID**.
4. Edit the role's trust policy and replace `PASTE-EXTERNAL-ID-HERE` with that External ID.
5. Press **Verify** on the account card. ClearCloud tries to assume the role. If AWS refuses, the message says what to check. When it succeeds, the account is verified and can be scanned. Scans skip an unverified account.

The role that ClearCloud runs as must be allowed to call `sts:AssumeRole`, which the template grants in a normal account. Restricted sandboxes may not allow this. The principal is the role's ARN, and it changes if the stack is deleted and created again. Connected accounts then need their trust policy updated. Using `arn:aws:iam::<CLEARCLOUD_ACCOUNT_ID>:root` as the principal survives that, but it trusts every identity in the ClearCloud account that is allowed to assume roles.

## Seeing real findings

Create a few wasted resources in a connected account, then scan again:

- an unattached disk or volume
- a reserved public IP that is not attached to anything
- a small VM left idle (monitoring data takes a while to appear)

Delete them afterwards so they do not use up credits.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Blank page or 403 on the site | Run `./scripts/deploy_frontend.sh`, then wait a minute for CloudFront |
| `/api/...` returns 403 `Forbidden` | The Function URL needs a second permission in newer accounts: `aws lambda add-permission --function-name clearcloud-api --statement-id invoke --action lambda:InvokeFunction --principal '*' --invoked-via-function-url` |
| Account status shows `error: ...` | Open the account's message: bad credentials, an API not enabled, or a role that cannot be assumed |
| "Restricted to admins" when adding an AWS account | Deploy with your email as the third argument and sign in again, or deploy with `OPEN_HOST_SCAN=true` for a class demo (step 5) |
| A cross-account AWS account stays "Not verified" | The role's trust policy must name the ClearCloud role as principal and contain this account's External ID. Fix the trust policy and press Verify again |
| Azure or GCP account is rejected when connecting | The message says what is wrong (secret, tenant, client ID, subscription or key). Fix that value and connect again |
| "Too many new accounts were created in the last hour" | The site-wide sign-up limit (60 per hour by default). Try again later, or set `MAX_SIGNUPS_PER_HOUR` on the API function |
| `Permission denied` running a script in CloudShell | Run it with `bash`, for example `bash scripts/deploy_frontend.sh clearcloud` |
| Scans fail with `ExpiredToken` | The sandbox credentials expired; re-run `./scripts/deploy_backend.sh` |
| Alert email never arrives | Alerts must be turned on in the app (Accounts page) and the AWS confirmation email must be confirmed (check spam). `aws sns list-subscriptions-by-topic --topic-arn <AlertTopicArn>` shows `PendingConfirmation` until you do. A repeat scan only emails findings that are new |
| Turning on alerts says the address already receives every alert | That address is the operator subscription from the deploy script. Sign in with a different email, or use a different operator address |
| Scan never finishes | Check the scanner Lambda's CloudWatch logs (`/aws/lambda/clearcloud-scanner`) |
| Spend chart says unavailable | Cost Explorer is blocked in this account; findings still work |
| 401 after a while | The sign-in token expired (12 hours); sign in again |

## Clean up

Empty the site bucket first, because CloudFormation cannot delete a bucket that still has files:

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name clearcloud \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
aws s3 rm "s3://$BUCKET" --recursive
sam delete --stack-name clearcloud
```

Removing an account in the dashboard deletes its stored credentials. To check for leftovers:

```bash
aws ssm describe-parameters --parameter-filters Key=Name,Option=BeginsWith,Values=/clearcloud/
```

## Monitoring and alerts

There are two kinds of alert email.

**Per-user alerts.** Each signed-in user opens the Accounts page and presses **Turn on email alerts**. AWS sends a confirmation email to their address, and they click **Confirm subscription**. From then on they get an email only about new waste in their own accounts. Pressing **Turn off** removes the subscription.

**Operator alerts.** Deploy with an alert email as the fourth argument:

```bash
./scripts/deploy_backend.sh clearcloud <role-arn | create> <admin-email> <alert-email>
```

AWS sends a confirmation email to that address. Click **Confirm subscription**, or nothing arrives. That subscription is not filtered, so it receives every user's alerts and the alarm notifications. The same address cannot also be used for per-user alerts. To add another operator address later without redeploying:

```bash
aws sns subscribe --topic-arn <AlertTopicArn> --protocol email --notification-endpoint <email>
```

The topic ARN is in the stack outputs as `AlertTopicArn`.

**What gets sent.** After each scan, the scanner compares the new findings with the account's previous report and emails only the findings that were not there before. The first scan of an account treats every finding as new. A scan with no new findings sends nothing. A failed alert never fails the scan.

**Dashboard.** In the AWS console, open CloudWatch, then Dashboards, then `clearcloud-monitoring`. It shows invocations, errors, scanner duration and scanner concurrency for the three Lambdas.

**Alarms (optional).** Alarms for errors on each Lambda and for a scan that runs close to its 300 s timeout are created only when you pass `ENABLE_ALARMS=true`:

```bash
ENABLE_ALARMS=true ./scripts/deploy_backend.sh clearcloud <role-arn | create> <admin-email> <alert-email>
```

Some student sandboxes deny alarm creation. If the stack fails on the alarm resources, redeploy without `ENABLE_ALARMS`; the dashboard and email alerts do not depend on it.