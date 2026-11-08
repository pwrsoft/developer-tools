#!/usr/bin/env python3
"""
DevDiskCleanup.py - Fast development disk cleanup.
Windows / Linux, Python 3.10+

Examples:
  Inspect all candidates and their sizes (nothing is deleted):
    python DevDiskCleanup.py --groups ALL --sizes --roots "C:\\src;C:\\MEGAsync"

  Recommended - clean every node_modules under both roots:
    python DevDiskCleanup.py --groups JS --types node_modules --delete --roots "C:\\src;C:\\MEGAsync"

  Clean several types:
    python DevDiskCleanup.py --groups JS,Java --types node_modules,target,build,out --delete --roots "C:\\src;C:\\MEGAsync"

  Other:
    python DevDiskCleanup.py
    python DevDiskCleanup.py --groups Java --types target,build,out --delete
    python DevDiskCleanup.py --groups JS,Java,AI --types ALL --delete
    python DevDiskCleanup.py --groups JS --types node_modules --delete --yes

Notes:
  - Discovery uses os.scandir(), not recursive file-size calculations.
  - A discovered node_modules is treated as one unit; .pnpm internals are
    not traversed.
  - Symlinks and Windows junctions are skipped.
  - Folder types are found by name, without requiring package.json or
    pom.xml. "build", "out" and "target" are generic names, so --types build
    can match non-Java projects: review the paths before using --yes.
  - --yes enables non-interactive deletion.
  - Docker volumes and downloaded AI models are deliberately excluded.
  - --sizes can be slow on large pnpm trees; leave it off for routine
    cleanup.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
WIN = os.name == "nt"

LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / ".local/share"))
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))

SKIP = {
    ".git", ".svn", ".hg", ".idea", ".vscode",
    ".gradle", "__pycache__", ".tox", ".next",
    ".terraform", ".mypy_cache",
}

# Folder name -> ecosystem
FOLDER_TYPES = {
    "node_modules": "JS",
    "target": "Java",
    "build": "Java",
    "out": "Java",
    ".venv": "Python",
    "venv": "Python",
}

GROUPS = ["JS", "Java", "AI", "Python", "Docker", "JetBrains", "General"]

items = {}
errors = 0


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def is_link(path):
    """Detect symlinks and Windows junctions."""
    try:
        if path.is_symlink():
            return True
        if hasattr(os.path, "isjunction") and os.path.isjunction(path):
            return True
    except OSError:
        return True
    return False


def resolve_command(cmd):
    """Resolve the executable (npm, pnpm... are .cmd shims on Windows)."""
    return [shutil.which(cmd[0]) or cmd[0], *cmd[1:]]


def add(group, kind, label, path=None, command=None, keep_root=False):
    """Register a candidate. keep_root: delete contents, not the folder."""
    if path is not None:
        path = Path(path).expanduser()

        try:
            if not path.is_dir() or is_link(path):
                return
            path = path.resolve()
        except OSError:
            return

    key = (group, kind, str(path).lower() if WIN else str(path),
           tuple(command) if command else None)

    if key not in items:
        items[key] = {
            "group": group,
            "type": kind,
            "label": label,
            "path": path,
            "command": command,
            "keep_root": keep_root,
        }


def run_output(*cmd):
    try:
        result = subprocess.run(
            resolve_command(cmd), capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------
# Known cache locations
# --------------------------------------------------

def discover_known():
    print("[+] Discovering known cache locations...")

    # JavaScript
    npm = run_output("npm", "config", "get", "cache")
    if npm and npm != "undefined":
        add("JS", "npm-cache", "npm cache", npm)

    if run_output("pnpm", "store", "path"):
        add("JS", "pnpm-store", "pnpm store (prune unreferenced)",
            command=["pnpm", "store", "prune"])

    add("JS", "yarn-cache", "Yarn cache", CACHE / "yarn")
    add("JS", "bun-cache", "Bun cache", CACHE / "bun")
    add("JS", "node-gyp", "Node-gyp headers", LOCAL / "node-gyp")

    # Java
    add("Java", "maven-repository", "Maven repository",
        HOME / ".m2/repository")
    add("Java", "maven-wrapper", "Maven wrapper distributions",
        HOME / ".m2/wrapper/dists")
    add("Java", "gradle-cache", "Gradle caches",
        HOME / ".gradle/caches")
    add("Java", "gradle-wrapper", "Gradle wrapper distributions",
        HOME / ".gradle/wrapper/dists")
    add("Java", "gradle-daemon", "Gradle daemon logs",
        HOME / ".gradle/daemon")

    # AI - cache directories only. Never remove credentials/settings.
    add("AI", "claude-cache", "Claude Code cache",
        HOME / ".claude/cache")
    add("AI", "codex-cache", "Codex cache",
        HOME / ".codex/cache")
    add("AI", "kiro-cache", "Kiro cache",
        HOME / ".kiro/cache")
    add("AI", "huggingface-cache", "Hugging Face cache",
        CACHE / "huggingface")

    # Python
    add("Python", "pip-cache", "pip cache", CACHE / "pip")
    add("Python", "uv-cache", "uv cache", CACHE / "uv")
    add("Python", "poetry-cache", "Poetry cache",
        CACHE / "pypoetry")

    if WIN:
        add("Python", "pip-cache", "pip cache",
            LOCAL / "pip/Cache")

    # JetBrains - only caches and logs, not settings.
    for base in (LOCAL / "JetBrains", CACHE / "JetBrains"):
        if not base.is_dir():
            continue
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        version = Path(entry.path)
                        add("JetBrains", "jb-cache",
                            f"JetBrains cache: {version.name}",
                            version / "caches")
                        add("JetBrains", "jb-log",
                            f"JetBrains logs: {version.name}",
                            version / "log")
        except OSError:
            pass

    # General temporary files: clear contents, keep the folder itself.
    add("General", "temp", "User temporary files",
        LOCAL / "Temp", keep_root=True)


# --------------------------------------------------
# Fast folder-type scanner
# --------------------------------------------------

def discover_roots(roots, groups, types):
    """
    Uses scandir instead of recursive file-size calculations.

    Once a candidate is found, it is recorded and never traversed.
    Symlinks and junctions are never followed.
    """
    global errors

    for root in roots:
        root = Path(root)

        if not root.is_dir() or is_link(root):
            continue

        stack = [root]

        while stack:
            current = stack.pop()

            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue

                            path = Path(entry.path)

                            if is_link(path):
                                continue

                            name = entry.name

                            # Candidate: record and DO NOT descend.
                            if name in FOLDER_TYPES:
                                group = FOLDER_TYPES[name]

                                if group in groups and (
                                    types is None or name in types
                                ):
                                    add(group, name,
                                        f"Folder type: {name}", path)
                                continue

                            # Ignore irrelevant subtrees.
                            if name in SKIP:
                                continue

                            stack.append(path)

                        except OSError:
                            errors += 1

            except OSError:
                errors += 1


# --------------------------------------------------
# Docker
# --------------------------------------------------

def discover_docker():
    if not shutil.which("docker"):
        return

    # -f: our own confirmation replaces docker's interactive prompt.
    add("Docker", "docker-build-cache", "Docker build cache",
        command=["docker", "builder", "prune", "-a", "-f"])

    add("Docker", "docker-containers", "Stopped containers",
        command=["docker", "container", "prune", "-f"])

    add("Docker", "docker-images", "Dangling images",
        command=["docker", "image", "prune", "-f"])


# --------------------------------------------------
# Filtering and reporting
# --------------------------------------------------

def selected_items(groups, types):
    return [
        item for item in items.values()
        if item["group"] in groups
        and (types is None or item["type"] in types)
    ]


def directory_size(path):
    """Slow optional size calculation."""
    total = 0

    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [
            d for d in dirs if not is_link(Path(root) / d)
        ]

        for name in files:
            p = Path(root) / name
            try:
                if not is_link(p):
                    total += p.stat().st_size
            except OSError:
                pass

    return total


def report(candidates, measure=False):
    print("\n========== DISCOVERED ITEMS ==========")

    for group in GROUPS:
        subset = [x for x in candidates if x["group"] == group]

        if not subset:
            continue

        print(f"\n[{group}]")

        for item in subset:
            path = item["path"]

            if path:
                amount = human(directory_size(path)) if measure \
                    else "not measured"
                location = str(path)
            else:
                amount = "managed action"
                location = " ".join(item["command"])

            print(f"  {item['type']:<22} {amount}")
            print(f"    {location}")

    print(f"\nTotal candidates: {len(candidates)}")
    if errors:
        print(f"Directories inaccessible during scan: {errors}")


# --------------------------------------------------
# Cleanup
# --------------------------------------------------

def _retry_writable(func, path, _exc):
    """rmtree error handler: clear the read-only bit (common on Windows)."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_tree(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        shutil.rmtree(path, onerror=_retry_writable)


def remove_contents(path):
    """Remove everything inside path, continuing past entries in use."""
    failed = 0

    for child in path.iterdir():
        try:
            if is_link(child) or not child.is_dir():
                child.unlink()
            else:
                remove_tree(child)
        except OSError:
            failed += 1

    if failed:
        raise OSError(f"{failed} entries could not be removed (in use?)")


def clean(candidates, show_sizes=False, assume_yes=False):
    deleted = []
    skipped = []
    failed = []
    reclaimed = 0

    total = len(candidates)

    print("\n========== CLEANUP ==========\n")

    for index, item in enumerate(candidates, 1):
        path = item["path"]
        command = item["command"]
        name = str(path) if path else " ".join(command)

        print(f"\n[{index}/{total}] {item['group']} / {item['type']}")
        print(f"Reason:     {item['label']}")

        size = None
        if path:
            print(f"Path:       {path}")
            if item["type"] in FOLDER_TYPES:
                print(f"Project:    {path.parent.name}")
            print("Action:     Delete directory " + (
                "contents" if item["keep_root"] else "recursively"))

            if not path.exists():
                print("Status:     Already missing")
                skipped.append(name)
                continue

            if show_sizes:
                size = directory_size(path)
                print(f"Size:       {human(size)}")
        else:
            print(f"Command:    {name}")

        if assume_yes:
            answer = "y"
            print("Confirmation: automatic (--yes)")
        else:
            answer = input("\nProceed? [y/N] ").strip().lower()

        if answer not in ("y", "yes"):
            print("Status:     Skipped")
            skipped.append(name)
            continue

        try:
            if command:
                result = subprocess.run(resolve_command(command))
                if result.returncode:
                    raise OSError(f"exit code {result.returncode}")
            elif item["keep_root"]:
                remove_contents(path)
            else:
                remove_tree(path)

            print("Status:     Done")
            deleted.append(name)
            if size is not None:
                reclaimed += size

        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Status:     FAILED - {exc}")
            failed.append((name, str(exc)))

    print("\n========== CLEANUP SUMMARY ==========\n")
    print(f"Candidates:  {total}")
    print(f"Done:        {len(deleted)}")
    print(f"Skipped:     {len(skipped)}")
    print(f"Failed:      {len(failed)}")

    if show_sizes:
        print(f"Estimated deleted size: {human(reclaimed)}")

    if deleted:
        print("\nCleaned:")
        for name in deleted:
            print(f"  {name}")

    if skipped:
        print("\nSkipped:")
        for name in skipped:
            print(f"  {name}")

    if failed:
        print("\nFailed:")
        for name, error in failed:
            print(f"  {name}")
            print(f"    {error}")


def main():
    parser = argparse.ArgumentParser(
        description="Fast development disk cleanup. Audit-only unless "
                    "--delete is given.",
        epilog=__doc__.split("Examples:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--groups", default="ALL",
        help="comma-separated groups, case-insensitive: "
             + ", ".join(GROUPS) + " (default: ALL)")
    parser.add_argument(
        "--types", default=None,
        help="comma-separated item types to keep, e.g. node_modules,target "
             "(folder names or cache ids such as npm-cache, temp); "
             "default/ALL: every type")
    parser.add_argument(
        "--delete", action="store_true",
        help="clean the discovered items (asks per item unless --yes)")
    parser.add_argument(
        "--yes", action="store_true",
        help="with --delete: skip per-item confirmation")
    parser.add_argument(
        "--sizes", action="store_true",
        help="measure sizes (slow on large trees)")
    parser.add_argument(
        "--roots", default="",
        help=f"extra project roots to scan for node_modules/target/build/"
             f"out/.venv/venv, separated by '{os.pathsep}'. Always scanned: "
             "~/source, projects, dev, src, workspace, Documents, Desktop")

    args = parser.parse_args()

    by_lower = {g.lower(): g for g in GROUPS}
    if args.groups.upper() == "ALL":
        groups = set(GROUPS)
    else:
        requested = {x.strip() for x in args.groups.split(",") if x.strip()}
        unknown = {x for x in requested if x.lower() not in by_lower}
        if unknown:
            parser.error(f"Unknown groups: {sorted(unknown)}")
        groups = {by_lower[x.lower()] for x in requested}

    types = None
    if args.types and args.types.upper() != "ALL":
        types = {x.strip() for x in args.types.split(",") if x.strip()}

    discover_known()

    roots = [
        HOME / "source",
        HOME / "projects",
        HOME / "dev",
        HOME / "src",
        HOME / "workspace",
        HOME / "Documents",
        HOME / "Desktop",
    ]

    roots += [
        Path(x.strip())
        for x in args.roots.split(os.pathsep)
        if x.strip()
    ]

    discover_roots(roots, groups, types)

    if "Docker" in groups:
        discover_docker()

    candidates = selected_items(groups, types)

    report(candidates, args.sizes)

    if not args.delete:
        print("\nAudit only. Nothing deleted.")
        print("Use --delete to enable cleanup.")
        return

    if not candidates:
        print("\nNo matching cleanup candidates.")
        return

    if args.yes:
        print("\nWARNING: automatic deletion enabled.")

    clean(candidates, show_sizes=args.sizes, assume_yes=args.yes)

    print("\nCleanup finished.")


if __name__ == "__main__":
    main()
