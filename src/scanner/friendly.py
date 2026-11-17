"""Turn scan failures into a short message a user can act on.

Each message says what happened and what to do next. The technical code stays in brackets at the
end so it is still easy to search for in the logs.
"""

ACCESS_DENIED = {
    "UnauthorizedOperation", "AccessDenied", "AccessDeniedException", "UnauthorizedAccess",
    "Forbidden", "AuthorizationError",
}
BAD_CREDENTIALS = {
    "AuthFailure", "InvalidClientTokenId", "ExpiredToken", "ExpiredTokenException",
    "SignatureDoesNotMatch", "UnrecognizedClientException", "InvalidAccessKeyId",
    "Unauthorized", "Unauthenticated",
}
THROTTLED = {
    "RequestLimitExceeded", "Throttling", "ThrottlingException", "TooManyRequestsException",
    "RequestThrottled", "ProvisionedThroughputExceededException",
}
BAD_REGION = {"InvalidParameterValue",
              "InvalidRegion", "InvalidParameterCombination"}

MAX_LENGTH = 280


def _code(exc):
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def explain(exc):
    """Return one or two plain sentences, then the technical code in brackets."""
    code = _code(exc)
    name = type(exc).__name__
    text = str(exc).lower()

    if isinstance(exc, PermissionError):
        message = ("This AWS account is not set up for scanning. "
                   "Remove it and connect it again with a role ARN for your own account.")
    elif code in ACCESS_DENIED:
        message = ("AWS refused to let ClearCloud read this account. "
                   "Give the role read-only access (EC2 describe and CloudWatch read) and scan again.")
    elif code in BAD_CREDENTIALS:
        message = ("The credentials were rejected or have expired. "
                   "Reconnect the account with fresh credentials, or redeploy the backend to refresh them.")
    elif code in THROTTLED:
        message = ("AWS is limiting how fast ClearCloud can read this account. "
                   "Wait a minute and press Scan now again.")
    elif code == "OptInRequired":
        message = ("This region is not enabled in the AWS account. "
                   "Enable it in the AWS account settings, or connect the account with a different region.")
    elif code in BAD_REGION or "could not connect to the endpoint" in text or name in (
            "EndpointConnectionError", "ConnectTimeoutError", "NoRegionError"):
        message = ("ClearCloud could not use this region. "
                   "Check that the region name is right (for example us-east-2) and try again.")
    elif "401" in text or "403" in text or "permission" in text or "unauthorized" in text:
        message = ("The cloud rejected the credentials or they lack read access. "
                   "Check the keys and make sure they have a read-only role such as Reader or Viewer.")
    else:
        message = ("The scan failed for an unexpected reason. "
                   "Press Scan now to try again, and check the account settings if it keeps failing.")

    return f"{message} [{code}]"[:MAX_LENGTH]
