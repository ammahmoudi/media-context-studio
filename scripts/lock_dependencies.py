"""Snapshot installed dependency closures for reproducible API and worker builds.

Run from the verified Python 3.12 environment after installing .[worker,dev].
GPU vendor runtimes remain in the separate platform-specific lock.
"""
import importlib.metadata as metadata
import tomllib
from pathlib import Path
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def closure(requirements):
    pending = [Requirement(r) for r in requirements]
    seen, pinned = set(), {}
    environment = default_environment()
    # Produce portable Linux/Windows requirements by retaining both runtime
    # branches when installed; pure platform helpers remain guarded below.
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        identity = (name, tuple(sorted(requirement.extras)))
        if identity in seen:
            continue
        seen.add(identity)
        dist = metadata.distribution(name)
        pinned[name] = dist.version
        for raw in dist.requires or []:
            child = Requirement(raw)
            if child.marker and not any(child.marker.evaluate(dict(environment, extra=extra))
                                        for extra in ("", *requirement.extras)):
                continue
            pending.append(child)
    return "\n".join(f"{name}=={version}" for name, version in sorted(pinned.items())) + "\n"


if __name__ == "__main__":
    base = project["dependencies"]
    api = closure(base + ["setuptools>=68"])
    worker = closure(base + project["optional-dependencies"]["worker"] + project["optional-dependencies"]["dev"])
    # The Windows-only console helper is harmless to omit on Linux.
    api = api.replace("colorama==0.4.6\n", "colorama==0.4.6; sys_platform == 'win32'\n")
    worker = worker.replace("colorama==0.4.6\n", "colorama==0.4.6; sys_platform == 'win32'\n")
    (ROOT / "requirements-api.lock.txt").write_text(api, encoding="utf-8", newline="\n")
    (ROOT / "requirements.lock.txt").write_text(worker, encoding="utf-8", newline="\n")
    print("Wrote verified API and worker dependency snapshots.")
