"""User-defined metadata on a package: named, typed values in the manifest.

A package can carry whatever facts its author wants alongside it - a publish
date, a run number, the version of the pipeline that made it - as a list of
`{ name, type, value }` records. The value travels as text in the manifest;
the type says how to read it, and is checked at verify time, so `LL meta`
can hand a consumer properly typed JSON.

The types are deliberately few:

* ``int`` and ``float`` - numbers, as JSON numbers when read out;
* ``datetime`` - ISO 8601, kept as written but required to parse;
* ``string`` - anything;
* ``version`` - semantic versioning (``MAJOR.MINOR.PATCH`` with optional
  pre-release and build parts), so versions can be compared, not just shown.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

METADATA_TYPES = ("int", "float", "datetime", "string", "version")

# semver.org's grammar, without the leading-zero rules being enforced on the
# pre-release identifiers, which is as strict as anyone writes them.
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class MetadataError(ValueError):
    """A metadata value that does not read as its declared type."""


def parse_metadata_value(type_name: str, text: str) -> Any:
    """The typed value a metadata entry's text stands for, or MetadataError.

    Numbers come back as Python numbers; a datetime, string and version come
    back as the text, since that is their JSON form too - the point of parsing
    a datetime or a version is to know it is one.
    """
    if type_name == "int":
        try:
            return int(text.strip())
        except ValueError:
            raise MetadataError(f"{text!r} is not an int") from None
    if type_name == "float":
        try:
            value = float(text.strip())
        except ValueError:
            raise MetadataError(f"{text!r} is not a float") from None
        if value != value or value in (float("inf"), float("-inf")):
            raise MetadataError(f"{text!r} is not a finite float")
        return value
    if type_name == "datetime":
        try:
            datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            raise MetadataError(f"{text!r} is not an ISO 8601 datetime") from None
        return text.strip()
    if type_name == "string":
        return text
    if type_name == "version":
        if not _SEMVER.match(text.strip()):
            raise MetadataError(f"{text!r} is not a semantic version (MAJOR.MINOR.PATCH[-pre][+build])")
        return text.strip()
    raise MetadataError(f"unknown metadata type {type_name!r}; expected one of {', '.join(METADATA_TYPES)}")


def metadata_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The manifest's metadata as `{name, type, value}` dicts with typed values.

    A manifest written before metadata existed has none.
    """
    return [
        {"name": entry["name"], "type": entry["type"], "value": parse_metadata_value(entry["type"], entry["value"])}
        for entry in manifest.get("metadata") or []
    ]
