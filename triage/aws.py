"""Read-only access to AWS CloudTrail event history, used by the agent's `search_cloudtrail` tool.

Only `cloudtrail:LookupEvents` is ever called. Credentials come from the standard AWS chain: in GitHub Actions
that is the short-lived role from the OIDC login, locally it is your AWS profile. The feature is off unless
TRIAGE_CLOUDTRAIL=1, so a machine that happens to have AWS credentials never calls AWS by accident.

CloudTrail event history lags: an API call can take up to about 15 minutes to appear.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

LOOKUP_ATTRIBUTES = {"event_name": "EventName", "user_name": "Username", "resource_name": "ResourceName"}
MAX_PAGES = 3


class CloudTrailClient:
    def __init__(self, client):
        self._client = client

    @classmethod
    def from_env(cls) -> "CloudTrailClient | None":
        """Return a client if CloudTrail lookups are switched on and credentials exist, else None."""
        if os.environ.get("TRIAGE_CLOUDTRAIL", "0") != "1":
            return None
        try:
            import boto3
        except ImportError:
            return None
        region = os.environ.get("TRIAGE_AWS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        session = boto3.session.Session(region_name=region or "us-east-1")
        if session.get_credentials() is None:
            return None
        return cls(session.client("cloudtrail"))

    def lookup(self, start: datetime, end: datetime, attribute: str | None = None, value: str | None = None,
               limit: int = 20) -> list[dict]:
        """Events between start and end, newest first. With no attribute, only events that changed something."""
        if attribute:
            attrs = [{"AttributeKey": LOOKUP_ATTRIBUTES[attribute], "AttributeValue": value}]
        else:
            attrs = [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]

        events, token = [], None
        for _ in range(MAX_PAGES):
            kwargs = dict(LookupAttributes=attrs, StartTime=start, EndTime=end, MaxResults=50)
            if token:
                kwargs["NextToken"] = token
            page = self._client.lookup_events(**kwargs)
            events.extend(_simplify(e) for e in page.get("Events", []))
            token = page.get("NextToken")
            if not token or len(events) >= limit:
                break
        return events


def _utc(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _simplify(event: dict) -> dict:
    """Keep only what helps a diagnosis. Source IPs, access key ids and raw request bodies are left out."""
    try:
        detail = json.loads(event.get("CloudTrailEvent") or "{}")
    except ValueError:
        detail = {}
    return {
        "time": _utc(event.get("EventTime")),
        "event": event.get("EventName", ""),
        "service": event.get("EventSource", ""),
        "user": event.get("Username") or detail.get("userIdentity", {}).get("arn", ""),
        "error_code": detail.get("errorCode", ""),
        "error_message": detail.get("errorMessage", ""),
        "resources": [r.get("ResourceName", "") for r in event.get("Resources", [])][:3],
    }
