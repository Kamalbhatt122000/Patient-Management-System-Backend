"""
Salesforce connection module.
Uses simple-salesforce to authenticate and expose a reusable client.
"""
import os
from simple_salesforce import Salesforce
from dotenv import load_dotenv

load_dotenv()

_sf_instance = None


def get_salesforce_client() -> Salesforce:
    """Return a cached Salesforce client (singleton)."""
    global _sf_instance
    if _sf_instance is None:
        _sf_instance = Salesforce(
            username=os.getenv("SF_USERNAME"),
            password=os.getenv("SF_PASSWORD"),
            security_token=os.getenv("SF_SECURITY_TOKEN"),
        )
        print(f"✅ Connected to Salesforce org: {_sf_instance.sf_instance}")
    return _sf_instance


def reset_client():
    """Force reconnect on next call (useful after session expiry)."""
    global _sf_instance
    _sf_instance = None
