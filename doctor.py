#!/usr/bin/env python3
"""doctor.py — off-Drive preflight guard for quant-tracker.

Port of ruflo's bin/ruflo-doctor.mjs (Insight B1), adapted to this repo's two
distinct path roles:

  * STORE  (the SQLite cache + run artifacts)  MUST be on a LOCAL filesystem.
    Sync-backed / virtual filesystems (Google Drive, Dropbox, iCloud, OneDrive,
    Box, SMB/NFS) silently dehydrate files to 0 bytes, drop FSEvents, and
    corrupt locked SQLite + WAL files. Creating the DB there is the exact
    mistake this guard refuses.

  * VAULT  (where Markdown is rendered for Obsidian)  MUST be the canonical
    CloudStorage "My Drive 2" mount. A stale path missing the " 2" silently
    points at an orphaned, non-synced copy — the trap that once killed the
    cron (see memory: jobhunt-vault-canonical-path). The guard fails closed if
    the vault is not the canonical synced path.

YOU MUST run this (exit 0) before creating any DB or rendering to the vault.
Non-zero exit ⇒ STOP.

Usage:
    python doctor.py            # human-readable
    python doctor.py --json     # machine-readable
Exit code: 0 = all clear, 1 = at least one path is unsafe / misconfigured.
"""
from __future__ import annotations

import json as _json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Path substrings that mark a cloud-sync or virtual mount (case-insensitive).
PATH_MARKERS = [
    "my drive", "google drive", "googledrive-", "/library/cloudstorage/",
    "dropbox", "onedrive", "icloud", "mobile documents", "com~apple~clouddocs",
    "/box/", "box sync", "pcloud", "sync.com", "creative cloud files",
    "proton drive", "mega", "tresorit",
]

# Filesystem types (from `mount`) that are virtual or networked.
FS_TYPE_MARKERS = [
    "fuse", "macfuse", "osxfuse", "dfsfuse", "fileprovider", "smbfs", "cifs",
    "nfs", "afpfs", "webdav", "ftp", "davfs", "9p", "vboxsf", "drvfs",
]

# The one true vault mount. The " 2" is load-bearing — see module docstring.
CANONICAL_VAULT = (
    "/Users/user/Library/CloudStorage/"
    "GoogleDrive-user@example.com/My Drive 2/"
    "02_Knowledge/Obsidian/TJ_Vault/Investment_AI"
)


def _real(p: str | Path) -> Path:
    try:
        return Path(p).resolve()
    except Exception:
        return Path(os.path.abspath(p))


def fs_type_for(abs_path: Path) -> str:
    """Return the mount filesystem type for a path (macOS/Linux), or '' if unknown.

    Shells out with list args only (no shell=True) per command-injection
    discipline — never a string command.
    """
    if sys.platform.startswith("win"):
        return ""
    try:
        df = subprocess.run(
            ["df", "-P", str(abs_path)], capture_output=True, text=True, check=False
        ).stdout
        lines = df.strip().split("\n")
        cols = (lines[1] if len(lines) > 1 else "").split()
        mount_point = " ".join(cols[5:]) if len(cols) > 5 else (cols[-1] if cols else "")
        mount = subprocess.run(
            ["mount"], capture_output=True, text=True, check=False
        ).stdout
        for line in mount.split("\n"):
            # macOS: "Dev on /mount/point (fstype, opts)"
            # Linux: "Dev on /mp type fstype (opts)"
            if f" on {mount_point} " in line + " ":
                if "(" in line:
                    seg = line.split("(", 1)[1]
                    return seg.split(",")[0].split(")")[0].strip().lower()
                if " type " in line:
                    return line.split(" type ", 1)[1].split()[0].strip().lower()
    except Exception:
        pass  # df/mount unavailable — fall back to path markers only
    return ""


def _is_synced(abs_path: Path) -> tuple[bool, list[str]]:
    """Return (on_sync_or_virtual_fs, reasons)."""
    hay = str(abs_path).lower()
    reasons: list[str] = []
    hit = next((m for m in PATH_MARKERS if m in hay), None)
    if hit:
        reasons.append(f'path matches cloud-sync marker "{hit}"')
    fstype = fs_type_for(abs_path)
    fshit = next((m for m in FS_TYPE_MARKERS if m in fstype), None)
    if fshit:
        reasons.append(f'filesystem type "{fstype}" is virtual/networked')
    return (len(reasons) > 0, reasons)


def check_store_local(store_dir: str | Path) -> dict:
    """STORE must be LOCAL — fail if it resolves onto a sync/virtual filesystem."""
    abs_path = _real(store_dir)
    synced, reasons = _is_synced(abs_path)
    return {
        "role": "store",
        "target": str(store_dir),
        "resolved": str(abs_path),
        "fs_type": fs_type_for(abs_path) or "(unknown)",
        "safe": not synced,
        "reasons": reasons or (["on a sync/virtual filesystem"] if synced else []),
    }


def check_vault_canonical(vault_dir: str | Path) -> dict:
    """VAULT must be the canonical synced 'My Drive 2' mount AND exist."""
    abs_path = _real(vault_dir)
    hay = str(abs_path).lower()
    reasons: list[str] = []

    # 1) Must be ON a sync mount (the vault SHOULD live on Drive).
    on_sync, _ = _is_synced(abs_path)
    if not on_sync:
        reasons.append("vault is NOT on a cloud-sync mount (expected Google Drive)")
    # 2) Must be the canonical 'My Drive 2' path (the load-bearing ' 2').
    if "my drive 2" not in hay:
        reasons.append(
            "vault path is missing the canonical 'My Drive 2' segment "
            "(a path without the ' 2' points at the orphaned non-synced copy)"
        )
    # 3) Must actually exist.
    if not abs_path.exists():
        reasons.append("vault path does not exist on disk")

    return {
        "role": "vault",
        "target": str(vault_dir),
        "resolved": str(abs_path),
        "fs_type": fs_type_for(abs_path) or "(unknown)",
        "safe": len(reasons) == 0,
        "reasons": reasons,
    }


def resolve_store_dir() -> Path:
    """Where the SQLite cache lives. DB_PATH may override; default store/ under ROOT."""
    db_rel = os.getenv("DB_PATH", "store/cockpit.sqlite")
    p = Path(db_rel)
    db_path = p if p.is_absolute() else (ROOT / p)
    return db_path.resolve().parent


def resolve_vault_dir() -> Path:
    """Where Markdown is rendered. VAULT_PATH may override; default canonical."""
    return _real(os.getenv("VAULT_PATH", CANONICAL_VAULT))


def run() -> dict:
    store = check_store_local(resolve_store_dir())
    vault = check_vault_canonical(resolve_vault_dir())
    results = [store, vault]
    return {"all_safe": all(r["safe"] for r in results), "results": results}


def main(argv: list[str]) -> int:
    report = run()
    if "--json" in argv:
        print(_json.dumps(report, indent=2))
    else:
        print("quant-tracker doctor — off-Drive preflight\n")
        for r in report["results"]:
            tag = "✓ OK    " if r["safe"] else "✗ UNSAFE"
            print(f"{tag}  [{r['role']}] {r['resolved']}")
            print(f"          fs-type: {r['fs_type']}")
            for reason in r["reasons"]:
                print(f"          → {reason}")
        if report["all_safe"]:
            print("\nPASS: store is local; vault is the canonical synced mount.")
        else:
            print("\nFAIL: fix the path(s) above before creating a DB or rendering.")
            print("  store/  must be a LOCAL path (keep it under ~/dev/quant-tracker).")
            print(f"  VAULT_PATH must be the canonical mount:\n    {CANONICAL_VAULT}")
    return 0 if report["all_safe"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
