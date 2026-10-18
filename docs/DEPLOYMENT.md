# Deployment guide

This guide deploys ClearCloud to AWS. It is written for the AWS Academy Learner Lab but works in any AWS account that allows the services listed in the README.

## Prerequisites

- An AWS account or an active Learner Lab session
- AWS CloudShell (or a machine with the AWS CLI and AWS SAM CLI)
- Your account ID (shown in the top-right menu of the AWS console)

> **AWS Academy note:** student accounts cannot create IAM roles, so both Lambdas run as the existing **LabRole**. The template takes its ARN as a parameter.

## 1. Get the code into CloudShell

Either clone the repository:

```bash
git clone https://github.com/<your-username>/clearcloud.git
cd clearcloud
```

For a private repository, GitHub asks for a username and a personal access token as the password. Alternatively, download the repository as a ZIP from GitHub, upload it through CloudShell's Actions menu, and unzip it.

If `sam` is not installed: `pip install aws-sam-cli`

## 2. Build and deploy the backend

Find a role the Lambdas can run as. In a Learner Lab it is `LabRole`. In a sandbox without it, list the roles Lambda can assume and pick one:

```bash
aws iam list-roles --query "Roles[?contains(to_string(AssumeRolePolicyDocument),'lambda.amazonaws.com')].Arn" --output text
```

Then build and deploy with the script (replace the ARN with yours):

```bash
./scripts/deploy_backend.sh clearcloud arn:aws:iam::<ACCOUNT_ID>:role/<ROLE_NAME>
```

The script reads your current session credentials and passes them to the stack, so the Lambdas can call DynamoDB, SQS, Secrets Manager and the other services even when the execution role only allows logging. **These credentials expire when the session ends.** When scans start failing with `ExpiredToken`, start the lab again and re-run the script. In a normal AWS account, deploy with `sam deploy --guided` instead and leave the credential parameters empty.

Never commit these credentials or show them on screen.

The first build installs the Azure and GCP SDKs and can take a few minutes. If it fails on disk space or time, remove the `azure-*` and `google-*` lines from `src/scanner/requirements.txt` and rebuild; AWS scanning works without them.

The first deployment also creates a CloudFront distribution, which can take 5 to 10 minutes.

## 3. Publish the frontend

```bash
./scripts/deploy_frontend.sh clearcloud
```

This reads the stack outputs, writes the site's `config.json` (region and Cognito app client ID), uploads the site to S3, and invalidates the CloudFront cache. It prints the site URL when done.

Run it again whenever you change anything in `src/frontend/`.

## 4. Create your first user

Open the site URL and use **Create account**. Cognito emails a verification code. If email delivery is blocked in your environment, create and confirm a user from the CLI instead (take the pool ID from the stack outputs):

```bash
POOL_ID=$(aws cloudformation describe-stacks --stack-name clearcloud \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)

aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS

aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" \
  --username you@example.com --password '<A-Strong-Password1>' --permanent
```

To confirm a user who signed up on the site but never got the code:

```bash
aws cognito-idp admin-confirm-sign-up --user-pool-id "$POOL_ID" --username you@example.com
```

## 5. Make yourself an admin

Members of the `admins` group may scan the AWS account that hosts ClearCloud. Everyone else must connect their own account through a role.

```bash
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" \
  --username you@example.com --group-name admins
```

Sign out and back in afterwards, because group membership is read from the sign-in token.

## 6. Connect accounts and scan

Sign in, then use **Connect an account**:

- **AWS (this account):** choose AWS, add a label, leave the role ARN empty, and click Connect. This works only for admins.
- **Azure:** see below.
- **GCP:** see below.

Click **Scan now**. Results appear when the scan finishes, usually within a minute.

### Azure

Create a read-only service principal (Azure CLI or Cloud Shell):

```bash
az ad sp create-for-rbac --name clearcloud-reader --role Reader \
  --scopes /subscriptions/<SUBSCRIPTION_ID>
```

Paste the values into the form: `tenant` is the tenant ID, `appId` is the client ID, `password` is the client secret, and add your subscription ID.

### GCP

1. In IAM, create a service account with the **Compute Viewer** and **Monitoring Viewer** roles, and create a JSON key for it.
2. Enable the **Compute Engine API** and **Cloud Monitoring API** on the project.
3. Paste the entire key file into the form, then delete the key file from your machine.

### Another AWS account (cross-account)

Connecting an account with a role ARN shows an **External ID**. In the target account, create a role with this trust policy (replace the principal with the role the Lambdas run as, such as LabRole, and use the External ID shown):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::<CLEARCLOUD_ACCOUNT_ID>:role/LabRole" },
    "Action": "sts:AssumeRole",
    "Condition": { "StringEquals": { "sts:ExternalId": "<EXTERNAL_ID_FROM_THE_DASHBOARD>" } }
  }]
}
```

Give the role read-only permissions such as `ec2:Describe*`, `cloudwatch:GetMetricStatistics` and `ce:GetCostAndUsage`. The role that ClearCloud runs as must also be allowed to call `sts:AssumeRole`. Restricted sandboxes may not allow this.

## Seeing real findings

Create a few wasted resources in a connected account, then scan again:

- an unattached disk or volume
- a reserved public IP that is not attached to anything
- a small VM left idle (monitoring data takes a while to appear)

Delete them afterwards so they do not use up credits.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| The site says it is not configured | Run `./scripts/deploy_frontend.sh` to publish `config.json` |
| Account status shows `error: ...` | Open the account's message: bad credentials, an API not enabled, or a role that cannot be assumed |
| "Restricted to admins" when adding an AWS account | Add yourself to the `admins` group (step 5) and sign in again |
| Scans fail with `ExpiredToken` | The sandbox credentials expired; re-run `./scripts/deploy_backend.sh` |
| Scan never finishes | Check the dead-letter queue (`DeadLetterQueueUrl` output) and the scanner Lambda's CloudWatch logs |
| Spend chart says unavailable | Cost Explorer is blocked in this account; findings still work |
| Sign-up code never arrives | Use the CLI commands in step 4 |
| 401 after a while | The sign-in token expired; sign in again |

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
aws secretsmanager list-secrets --filters Key=name,Values=clearcloud/
```