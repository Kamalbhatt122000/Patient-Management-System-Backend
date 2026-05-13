"""
Salesforce schema helpers.

Field metadata (length, type) is fetched once via SObject.describe() and cached
in-process. Call reset_cache() if the org schema changes while the app is running.
"""
from threading import Lock

_field_cache: dict[tuple[str, str], dict] = {}
_describe_cache: dict[str, dict] = {}
_lock = Lock()


def _describe(sf, sobject_name: str) -> dict:
    """Return the cached describe() result for an SObject, fetching on miss."""
    with _lock:
        cached = _describe_cache.get(sobject_name)
        if cached is not None:
            return cached
    desc = getattr(sf, sobject_name).describe()
    with _lock:
        _describe_cache[sobject_name] = desc
    return desc


def get_field_metadata(sf, sobject_name: str, field_name: str) -> dict:
    """Return the field-level describe dict (name, type, length, ...)."""
    key = (sobject_name, field_name)
    with _lock:
        cached = _field_cache.get(key)
        if cached is not None:
            return cached

    desc = _describe(sf, sobject_name)
    for field in desc.get("fields", []):
        if field.get("name") == field_name:
            with _lock:
                _field_cache[key] = field
            return field

    raise LookupError(f"Field {field_name} not found on {sobject_name}")


def get_field_length(sf, sobject_name: str, field_name: str) -> int | None:
    """
    Return the max character length of a text field, or None if not applicable
    (number/date/formula fields have length 0 in the describe payload).
    """
    field = get_field_metadata(sf, sobject_name, field_name)
    length = field.get("length")
    return length if length and length > 0 else None


def reset_cache() -> None:
    """Clear cached describe results (e.g. after a schema change)."""
    with _lock:
        _field_cache.clear()
        _describe_cache.clear()
