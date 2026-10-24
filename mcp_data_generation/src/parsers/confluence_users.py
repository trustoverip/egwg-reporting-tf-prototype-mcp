# parsers/confluence_users.py

"""Resolve Confluence account ids to display names.

Every Confluence export records a user mention as an opaque account id, so a
meeting page's attendee list comes out as a row of empty commas unless the ids
can be turned back into names. None of the export formats carries the mapping:

* the XML and CSV exports store only the id;
* the CSV export's ``user_mapping.csv`` repeats the id in every column --
  ``username``, ``lower_username`` and ``aaid`` are all equal to ``user_key``
  in 346 of its 352 rows, and not one value contains a space;
* the HTML export is the only one that renders names, and only for the mentions
  that appear in the space it covers.

A site's user directory, exported from Atlassian admin as ``export-users.csv``
and placed beside the space exports, covers the whole site instead:

    confluence/<site>/export-users.csv

That resolves 25,411 of 25,565 mentions (99.4%) in the HOME space, against
47.4% from harvesting the one HTML export available. It is also cleaner: the
HTML export renders a departed member as "Ken Adler (Deactivated)", which is a
UI annotation rather than a name, while the directory holds "Ken Adler".

Only the id and name columns are read. The file also carries email addresses,
group membership, org roles and last-seen dates, and none of that belongs in a
document that gets indexed and searched.
"""

import csv
from pathlib import Path

DIRECTORY_FILE = "export-users.csv"

# The admin export's column titles. "User id" is the Atlassian account id, the
# same value the exports write as ri:userkey or ri:account-id.
_ID_COLUMN = "User id"
_NAME_COLUMN = "User name"

_CACHE: dict[Path, dict[str, str]] = {}


def directory_file(site_root: Path) -> Path | None:
    """The user directory for a site, if one was exported beside the spaces."""

    candidate = site_root / DIRECTORY_FILE

    return candidate if candidate.is_file() else None


def names(site_root: Path) -> dict[str, str]:
    """Map account id to display name for a whole Confluence site.

    Returns an empty mapping when no directory was exported, which leaves the
    caller to fall back to whatever its own format offers.
    """

    if site_root in _CACHE:
        return _CACHE[site_root]

    resolved: dict[str, str] = {}
    path = directory_file(site_root)

    if path is not None:
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)

                if reader.fieldnames and _ID_COLUMN in reader.fieldnames:
                    for row in reader:
                        account_id = (row.get(_ID_COLUMN) or "").strip()
                        name = (row.get(_NAME_COLUMN) or "").strip()

                        # A member of several groups has one row per group, so
                        # the first name wins and the rest are the same.
                        if account_id and name:
                            resolved.setdefault(account_id, name)
                else:
                    print(
                        f"  note: {path.name} has no '{_ID_COLUMN}' column; "
                        f"ignoring it"
                    )
        except OSError as error:
            print(f"  note: cannot read {path.name}: {error}")

    _CACHE[site_root] = resolved

    return resolved
