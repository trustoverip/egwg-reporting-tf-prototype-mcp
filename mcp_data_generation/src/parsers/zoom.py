# parsers/zoom.py

import json
import re
from pathlib import Path
from datetime import datetime
from src.models import Document

def process(file_path: Path) -> Document:
    content_type = _get_content_type(file_path)

    if content_type is None:
        print(f"Skipping unsupported Zoom file: {file_path.name}")
        return

    date_object = _extract_date(file_path)

    raw_content = _read_file(file_path)

    if file_path.suffix.lower() == ".vtt":
        content = _extract_vtt_text(raw_content)
    elif content_type == "AI Summary":
        content = _strip_summary_front_matter(raw_content)
    else:
        content = raw_content

    if not content.strip():
        # e.g. a summary record LFX created with no overview or next steps
        print(f"Skipping empty {content_type}: {file_path.name}")
        return None

    metadata = _load_metadata(file_path)

    document = _build_document(
        file_path=file_path,
        content=content,
        content_type=content_type,
        date_object=date_object,
        metadata=metadata,
    )

    return document

def _is_zoom_export_file(file_path: Path) -> bool:
    return bool(
        re.match(
            r"^GMT\d{8}-\d{6}_Recording.*\.(txt|vtt|md)$",
            file_path.name,
            re.IGNORECASE,
        )
    )

def _get_content_type(file_path: Path) -> str | None:
    if not _is_zoom_export_file(file_path):
        return None

    file_name = file_path.name.lower()
    if file_name.endswith(".summary.md"):
        return "AI Summary"

    if file_name.endswith(".md"):
        # Some other markdown that happens to sit beside the exports.
        return None

    if file_name.endswith(".txt"):
        return "Chat"

    if ".cc." in file_name:
        return "ClosedCaptioning"

    if ".transcript." in file_name:
        return "Transcript"

    return None


def _extract_date(file_path: Path) -> datetime:
    file_name = file_path.name

    date_string = file_name[3:11]

    return datetime.strptime(date_string, "%Y%m%d")


def _read_file(file_path: Path) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


_SUMMARY_META_LINE = re.compile(r"^- \*\*[^*]+:\*\*")


def _strip_summary_front_matter(content: str) -> str:
    """Drop the generated H1 and the meeting-metadata bullets.

    Title, date and meeting id are already fields on the document, so repeating
    them in the embedded text only adds noise — and the Zoom UUID and duration
    are pure noise for retrieval. What is left is the prose: agenda, overview,
    details and next steps.
    """

    lines = content.splitlines()
    index = 0

    while index < len(lines) and not lines[index].strip():
        index += 1

    if index < len(lines) and lines[index].startswith("# "):
        index += 1

    # Blank lines and "- **Key:** value" bullets, up to the first real content.
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not _SUMMARY_META_LINE.match(stripped):
            break
        index += 1

    return "\n".join(lines[index:]).strip()


def _extract_vtt_text(content: str) -> str:
    timestamp_pattern = re.compile(
        r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
    )

    lines = []

    for line in content.splitlines():
        line = line.strip()

        if not line:
            continue

        if line == "WEBVTT":
            continue

        if timestamp_pattern.match(line):
            continue

        lines.append(line)

    return " ".join(lines)

def _extract_source_identifier(file_path: Path) -> str:
    return file_path.name.split("_")[0]


def _build_document(
    file_path: Path,
    content: str,
    content_type: str,
    date_object: datetime,
    metadata: dict,
) -> Document:

    return Document(
        project=metadata["project"],
        organizational_unit=metadata["organizational_unit"],
        source_system="Zoom",
        source_identifier=_extract_source_identifier(file_path),
        source_url=metadata.get("source_url", ""),
        date=date_object.strftime("%Y-%m-%d"),
        asset_type="Meeting",
        content_type=content_type,
        title=metadata["title"],
        content=content,
    )


def _load_metadata(file_path: Path) -> dict:
    current_path = file_path.parent

    while current_path != current_path.parent:

        metadata_file = current_path / "metadata.json"

        if metadata_file.exists():
            with open(metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)

        current_path = current_path.parent

    raise FileNotFoundError(
        f"No metadata.json found for {file_path}"
    )