"""What produced a results table (D-19, gate A8).

Every generated table gets a ``<name>.manifest.json`` beside it recording the git commit, a hash of
the configuration, the RNG seed, library versions and the digest of any input file. Without it a
number in ``results/`` cannot be traced back to the code that made it, which is the whole of D-19.

A8 as written in D-18 asks for a clean-machine end-to-end reproduction. That cannot be checked from
inside the process, so the automated gate covers what can be: the manifest exists and is complete,
the config hash is stable, and the simulation is bit-for-bit reproducible from its seed. The
clean-clone run stays a manual step and is listed in TO_DO.md.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from src.config import Settings, settings

TRACKED_PACKAGES = ("numpy", "scipy", "polars", "pandas", "pyarrow")


def config_hash(cfg: Settings = settings) -> str:
    """Stable digest of the settings. Changing a threshold changes the hash, and therefore the
    manifest, which is how a table stops silently claiming to come from a different configuration."""
    payload = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def manifest(
    name: str,
    cfg: Settings = settings,
    seed: int | None = None,
    inputs: tuple[Path, ...] = (),
    extra: dict | None = None,
) -> dict:
    versions = {}
    for package in TRACKED_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "absent"
    return {
        "name": name,
        "git_commit": git_commit(),
        "config_hash": config_hash(cfg),
        "seed": seed,
        "python": platform.python_version(),
        "packages": versions,
        "inputs": {str(p): file_digest(p) for p in inputs if Path(p).exists()},
        **(extra or {}),
    }


def write_manifest(table_path: Path, payload: dict) -> Path:
    path = Path(table_path).with_suffix(".manifest.json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
