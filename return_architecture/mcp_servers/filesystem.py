"""Filesystem MCP server, scoped to one root path.

All operations are confined to ROOT. Any path that resolves outside it
(via `..`, absolute paths, or symlinks resolving outward) is rejected
with an error. The agent literally cannot read or write anything outside
the configured folder.

Run as:
    python -m return_architecture.mcp_servers.filesystem /path/to/folder
    python -m return_architecture.mcp_servers.filesystem /path/to/folder --read-only
    python -m return_architecture.mcp_servers.filesystem /path/to/folder --prefix code_

The `--read-only` flag disables write_file and append_file.

The `--prefix NAME` option prepends NAME to every tool name (e.g. `code_read_file`).
Use it when an agent runs more than one filesystem server so their tool names
don't collide — the runtime keys tools by name, so two unprefixed instances
would shadow each other.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def _parse_args() -> tuple[Path, bool, str]:
    args = sys.argv[1:]
    read_only = "--read-only" in args
    args = [a for a in args if a != "--read-only"]
    prefix = ""
    if "--prefix" in args:
        i = args.index("--prefix")
        if i + 1 >= len(args):
            sys.stderr.write("--prefix requires a value.\n")
            sys.exit(2)
        prefix = args[i + 1]
        del args[i:i + 2]
    if not args:
        sys.stderr.write(
            "Usage: filesystem.py <root_path> [--read-only] [--prefix NAME]\n"
        )
        sys.exit(2)
    root = Path(args[0]).expanduser().resolve()
    if not root.exists():
        sys.stderr.write(f"Root does not exist: {root}\n")
        sys.exit(2)
    if not root.is_dir():
        sys.stderr.write(f"Root is not a directory: {root}\n")
        sys.exit(2)
    return root, read_only, prefix


ROOT, READ_ONLY, PREFIX = _parse_args()
MAX_READ_BYTES = 200_000        # ~200KB per read_file
MAX_SEARCH_HITS = 30
MAX_SEARCH_LINE = 300

mcp = FastMCP(f"filesystem-{PREFIX}" if PREFIX else "filesystem")


def _safe_path(rel_path: str) -> Path:
    """Resolve `rel_path` against ROOT and refuse if it escapes."""
    if not rel_path or rel_path == ".":
        return ROOT
    candidate = (ROOT / rel_path).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        raise ValueError(
            f"Path escapes the configured root. Path '{rel_path}' is not allowed."
        )
    return candidate


@mcp.tool(name=f"{PREFIX}list_directory")
def list_directory(path: str = ".") -> str:
    """List the contents of a directory inside the root.

    Args:
        path: Relative path under the root. Default is the root itself.

    Returns:
        One line per entry: "<kind>\\t<size>\\t<name>". Directories show
        '-' for size. Up to one level deep.
    """
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.is_dir():
        return f"Not a directory: {path}"
    entries: list[str] = []
    for child in sorted(target.iterdir()):
        kind = "dir " if child.is_dir() else "file"
        try:
            size = child.stat().st_size if child.is_file() else "-"
        except OSError:
            size = "?"
        entries.append(f"{kind}\t{size}\t{child.name}")
    return "\n".join(entries) if entries else "(empty)"


@mcp.tool(name=f"{PREFIX}read_file")
def read_file(path: str) -> str:
    """Read a file's text content (UTF-8, up to ~200,000 bytes).

    Args:
        path: Relative path under the root.

    Returns:
        The file's text content, or an error message.
    """
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.is_file():
        return f"Not a file: {path}"
    try:
        data = target.read_bytes()
    except OSError as e:
        return f"Error reading {path}: {e}"
    if len(data) > MAX_READ_BYTES:
        truncated = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        return (
            f"(File is {len(data)} bytes; only the first {MAX_READ_BYTES} are returned.)\n\n"
            f"{truncated}"
        )
    return data.decode("utf-8", errors="replace")


@mcp.tool(name=f"{PREFIX}write_file")
def write_file(path: str, content: str) -> str:
    """Create or overwrite a UTF-8 text file at `path` inside the root."""
    if READ_ONLY:
        return "Error: this filesystem server is configured as read-only."
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error writing {path}: {e}"
    return f"Wrote {len(content)} characters to {path}."


@mcp.tool(name=f"{PREFIX}append_file")
def append_file(path: str, content: str) -> str:
    """Append text to a file at `path` (creates the file if it does not exist)."""
    if READ_ONLY:
        return "Error: this filesystem server is configured as read-only."
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"Error: {e}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return f"Error appending to {path}: {e}"
    return f"Appended {len(content)} characters to {path}."


@mcp.tool(name=f"{PREFIX}search_files")
def search_files(query: str, max_results: int = MAX_SEARCH_HITS) -> str:
    """Case-insensitive substring search across all files under the root.

    Args:
        query: Substring to search for.
        max_results: Maximum number of matching lines to return.

    Returns:
        Lines like "<rel_path>:<line_no>: <matching line>" — up to max_results.
    """
    if not query.strip():
        return "Error: empty query."
    q = query.lower()
    hits: list[str] = []
    for f in ROOT.rglob("*"):
        if len(hits) >= max_results:
            break
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if q not in text.lower():
            continue
        rel = f.relative_to(ROOT)
        for i, line in enumerate(text.splitlines(), start=1):
            if q in line.lower():
                snippet = line.strip()[:MAX_SEARCH_LINE]
                hits.append(f"{rel}:{i}: {snippet}")
                if len(hits) >= max_results:
                    break
    return "\n".join(hits) if hits else "(no matches)"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
