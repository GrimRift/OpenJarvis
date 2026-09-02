"""Secure file and directory creation helpers.

All OpenJarvis data files under ``~/.openjarvis/`` should be created
through these helpers to ensure consistent, restrictive permissions.

**These modes are enforced on POSIX only.** ``os.chmod`` on Windows sets
nothing but the read-only flag: it cannot express "owner only", because NTFS
uses ACLs rather than mode bits. A file created here on Windows is therefore
as readable as its inherited ACL allows -- in practice the user-profile ACL,
which keeps out other non-administrator accounts but is not the 0600 these
functions name. Credentials do pass through here, so restricting them
properly on Windows (an ``icacls`` grant to the owner alone) is real
outstanding work, not a documentation nicety.
"""

from __future__ import annotations

import os
from pathlib import Path


def secure_mkdir(path: Path, mode: int = 0o700) -> Path:
    """Create a directory with restrictive permissions.

    Creates parent directories as needed, then sets *mode* on the
    target directory (even if it already exists).
    """
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    return path


def secure_create(path: Path, mode: int = 0o600) -> Path:
    """Ensure a file exists with restrictive permissions.

    Creates the parent directory with ``0o700`` if needed, touches the
    file if it doesn't exist, and sets *mode* on it.
    """
    secure_mkdir(path.parent, mode=0o700)
    if not path.exists():
        path.touch()
    os.chmod(path, mode)
    return path
