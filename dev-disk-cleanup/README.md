# DevDiskCleanup

> Audit-first disk cleanup for developers: find and remove `node_modules`, build output and tool caches.

Fast, single-file disk cleanup for developer machines. It finds the things that quietly eat disk space (`node_modules`, build output, package-manager caches, IDE caches, Docker leftovers), shows what it found, and deletes only when you ask.

- Windows and Linux, Python 3.10+, no dependencies
- **Audit-only by default** - nothing is deleted without `--delete`
- Confirms each item before deleting, unless `--yes` is given
- Never follows symlinks or Windows junctions

## Quick start

```bash
# 1. See what would be cleaned (nothing is deleted)
python DevDiskCleanup.py --groups ALL --sizes --roots "C:\src;C:\work"

# 2. Delete every node_modules under those roots, confirming each one
python DevDiskCleanup.py --groups JS --types node_modules --delete --roots "C:\src;C:\work"
```

## What it finds

| Group | Items |
|---|---|
| `JS` | `node_modules` folders, npm cache, pnpm store (`pnpm store prune`), Yarn cache, Bun cache, node-gyp headers |
| `Java` | `target`, `build`, `out` folders, Maven repository and wrapper, Gradle caches, wrapper and daemon logs |
| `Python` | `.venv` / `venv` folders, pip, uv and Poetry caches |
| `AI` | Claude Code, Codex, Kiro and Hugging Face **caches only** |
| `Docker` | Build cache, stopped containers, dangling images (via `docker ... prune`) |
| `JetBrains` | Per-version `caches` and `log` folders (settings are never touched) |
| `General` | Contents of the user temp folder |

Not touched: Docker volumes, downloaded AI models, credentials, IDE settings.

## Usage

```
python DevDiskCleanup.py [--groups LIST] [--types LIST] [--roots PATHS]
                         [--sizes] [--delete] [--yes]
```

| Option | Meaning |
|---|---|
| `--groups` | Comma-separated groups (case-insensitive) or `ALL` (default) |
| `--types` | Keep only these item types, e.g. `node_modules,target`, or cache ids such as `npm-cache`, `temp`. Default / `ALL`: every type |
| `--roots` | Extra project roots to scan, separated by `;` on Windows and `:` on Linux |
| `--sizes` | Measure sizes (slow on large trees; skip for routine runs) |
| `--delete` | Clean the discovered items, asking per item |
| `--yes` | With `--delete`: skip the per-item confirmation |

Project roots always scanned: `~/source`, `~/projects`, `~/dev`, `~/src`, `~/workspace`, `~/Documents`, `~/Desktop`.

### More examples

```bash
# Several folder types at once
python DevDiskCleanup.py --groups JS,Java --types node_modules,target,build,out --delete --roots "C:\src"

# Everything in three groups
python DevDiskCleanup.py --groups JS,Java,AI --types ALL --delete

# Unattended (review the audit output first!)
python DevDiskCleanup.py --groups JS --types node_modules --delete --yes
```

## How it works

- Discovery uses `os.scandir()` and never does recursive size calculations unless `--sizes` is set.
- A discovered folder is recorded as one unit and is not traversed further, so huge pnpm trees are cheap to find.
- Folders are matched **by name**, without requiring `package.json` or `pom.xml`.
- Read-only files (common in `node_modules` on Windows) are handled during deletion.
- The temp folder is emptied, not removed; files in use are skipped and counted.
- Docker and pnpm items run the tool's own prune command.

## Safety notes

- `build`, `out` and `target` are generic names. `--types build` can match Flutter, ESPHome or other non-Java projects. **Review the audit output before using `--yes`.**
- Deleting `node_modules`, `venv` and caches is recoverable by reinstalling, but deleting `.m2/repository` or `.gradle/caches` means re-downloading dependencies.
- Deletion is permanent (no recycle bin).
- Run the audit first, then narrow with `--groups` and `--types`.

## License

Apache License 2.0 - see the [LICENSE](../LICENSE) file in the repository root.
