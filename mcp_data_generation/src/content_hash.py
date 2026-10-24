# src/content_hash.py

"""Hash a source file by its contents rather than by its container.

A file's identity has to survive recompression. The Confluence CSV exports in
the cache were shipped uncompressed, then gzipped to make the cache clonable;
the text never changed, but every byte of every file did. Hashing the file as
it sits on disk made that look like new content, which re-dated the documents
it produced and, because the document SAID covers ``collected_at``, gave 2,103
unchanged documents new identifiers.

So the hash is taken over the *uncompressed* contents: a gzipped file and the
same file expanded hash alike, and ``gzip``/``gunzip`` becomes invisible to the
pipeline. Whether a file is compressed is decided by sniffing for the gzip
magic bytes, not by its name -- Confluence shipped 49 files named ``.csv.gz``
with not one of them compressed, so the suffix means nothing in either
direction.

This is the hash recorded as a raw artifact's id, so it is also what the cache
publishes alongside its source files; see ``src.cache_manifest``.
"""

import gzip
import hashlib
from pathlib import Path

GZIP_MAGIC = b"\x1f\x8b"

# Big enough that a 20 MB export is a few hundred reads, small enough that a
# directory of transcripts does not hold megabytes in flight.
CHUNK_SIZE = 1 << 16


def is_gzip(file_path: Path) -> bool:
    """True if the file really begins with the gzip magic bytes."""

    with open(file_path, "rb") as probe:
        return probe.read(2) == GZIP_MAGIC


def sha256_content(file_path: Path) -> str:
    """SHA-256 of the file's uncompressed contents, as hex."""

    sha = hashlib.sha256()
    opener = gzip.open if is_gzip(file_path) else open

    try:
        with opener(file_path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                sha.update(chunk)
    except (OSError, EOFError) as exc:
        # A truncated member reads as a stream that ends early, so a partial
        # hash would otherwise be returned as if it were the whole file.
        raise ValueError(f"{file_path}: unreadable as gzip: {exc}") from exc

    return sha.hexdigest()
