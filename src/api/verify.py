"""Checks that cloud credentials really work before ClearCloud stores them."""
import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 10


class VerifyError(Exception):
    """A credential problem, worded for the person who has to fix it."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _http(url, data=None, token=None):
    """Return (status, parsed JSON or {}). Network failures raise VerifyError."""
    headers = {"Accept": "application/json"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise VerifyError(
            "ClearCloud could not reach the cloud provider to check these credentials. "
            "Try again in a moment.", 503)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def azure_problem(code_text):
    """Plain-language reason for an Azure AD sign-in error."""
    text = code_text or ""
    if "AADSTS90002" in text or "AADSTS900023" in text:
        return "Azure could not find that Tenant ID. Check that you copied the Directory (tenant) ID."
    if "AADSTS700016" in text:
        return ("Azure could not find an app with that Client ID in this tenant. "
                "Check the Application (client) ID.")
    if "AADSTS7000215" in text or "AADSTS7000222" in text or "AADSTS50012" in text:
        return ("Azure rejected the client secret. It is wrong or has expired. "
                "Paste the secret's Value, not its Secret ID.")
    if "AADSTS" in text:
        return "Azure refused to sign in with these details. Check the tenant, client ID and secret."
    return "Azure refused to sign in with these details."


def check_azure(creds, http=_http):
    tenant = urllib.parse.quote(creds["tenant_id"], safe="")
    status, body = http(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": "https://management.azure.com/.default",
        },
    )
    token = body.get("access_token") if status == 200 else None
    if not token:
        raise VerifyError(azure_problem(
            body.get("error_description") or body.get("error")))
    sub = urllib.parse.quote(creds["subscription_id"], safe="")
    status, _ = http(
        f"https://management.azure.com/subscriptions/{sub}?api-version=2022-12-01",
        token=token)
    if status != 200:
        raise VerifyError(
            "The app can sign in, but it cannot see that subscription. Check the Subscription ID, "
            "and make sure the app has the Reader role on it.")


def check_gcp(info, request=None):
    from google.auth import exceptions as gexc
    from google.oauth2 import service_account

    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"])
    except (ValueError, KeyError, TypeError):
        raise VerifyError(
            "That service account key is not valid. Download a fresh JSON key from the Google "
            "Cloud console and paste the whole file.")
    if request is None:
        import google.auth.transport.requests

        request = google.auth.transport.requests.Request()
    try:
        creds.refresh(request)
    except gexc.TransportError:
        raise VerifyError(
            "ClearCloud could not reach Google to check this key. Try again in a moment.", 503)
    except gexc.RefreshError:
        raise VerifyError(
            "Google rejected this service account key. It may have been deleted or disabled. "
            "Create a new key and paste it.")


ASSUME_ROLE_PROBLEMS = {
    "AccessDenied": (
        "AWS refused to let ClearCloud use this role. Open the role's trust policy and check "
        "that it trusts the ClearCloud role shown in the help panel and that its External ID "
        "matches the one on this card."),
    "ValidationError": "That role ARN is not valid. Copy it again from the role's page in IAM.",
    "RegionDisabledException": "That AWS Region is disabled for the account.",
}


def assume_role_problem(code):
    return ASSUME_ROLE_PROBLEMS.get(
        code, f"AWS could not confirm this role. Try again in a moment. [{code or 'Error'}]")
