"""Google Docs MCP server.

Lets an agent read and edit a shared Google Doc through the Docs API,
authenticated as a service account that the doc has been shared with (Editor).

Runnable as: python -m return_architecture.mcp_servers.gdocs

Auth: set GDOCS_CREDENTIALS to the path of the service-account JSON key.
The same human-owned doc is editable from the Google Docs app (incl. phone);
the agent edits it here; Docs keeps revision history.

Tabs: a Doc may contain multiple tabs. Every tool takes an optional `tab`
(matched by title or tab ID); omitting it targets the first tab. Use
`list_tabs` to discover what tabs exist.

The MCP runtime in Return Architecture launches this as a subprocess and
speaks JSON-RPC to it over stdio. Server side uses FastMCP from the `mcp`
package, matching the url_fetch / filesystem servers.
"""

from __future__ import annotations

import os
import re

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("gdocs")

SCOPES = ["https://www.googleapis.com/auth/documents"]
MAX_RETURN_CHARS = 50_000

# A Doc URL looks like https://docs.google.com/document/d/<ID>/edit
_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")

_service = None


def _get_service():
    """Lazily build (and cache) the Docs API client.

    Lazy so the subprocess starts even if credentials are misconfigured;
    the error then surfaces as a tool result rather than a crash on import.
    """
    global _service
    if _service is None:
        cred_path = os.environ.get("GDOCS_CREDENTIALS")
        if not cred_path:
            raise RuntimeError(
                "GDOCS_CREDENTIALS is not set (path to the service-account JSON key)."
            )
        if not os.path.exists(cred_path):
            raise RuntimeError(f"Credentials file not found: {cred_path}")
        creds = Credentials.from_service_account_file(cred_path, scopes=SCOPES)
        _service = build("docs", "v1", credentials=creds, cache_discovery=False)
    return _service


def _doc_id(doc: str) -> str:
    """Accept either a full Doc URL or a bare document ID."""
    m = _DOC_ID_RE.search(doc)
    return m.group(1) if m else doc.strip()


def _fetch(doc: str) -> dict:
    """Get the document with all tab content included."""
    return (
        _get_service()
        .documents()
        .get(documentId=_doc_id(doc), includeTabsContent=True)
        .execute()
    )


def _flatten_tabs(document: dict) -> list[dict]:
    """Return all tabs in document order, descending into child tabs.

    Each entry: {"id": <tabId>, "title": <str>, "tab": <tab object>}.
    """
    flat: list[dict] = []

    def walk(tabs: list[dict]) -> None:
        for t in tabs:
            props = t.get("tabProperties", {})
            flat.append(
                {"id": props.get("tabId"), "title": props.get("title", ""), "tab": t}
            )
            walk(t.get("childTabs", []))

    walk(document.get("tabs", []))
    return flat


def _resolve_tab(document: dict, tab: str | None) -> dict | str:
    """Resolve a `tab` argument to a flattened tab entry.

    `tab` may be a tab title (case-insensitive) or a tab ID. None -> first tab.
    Returns the entry dict, or an error string listing the available tabs.
    """
    flat = _flatten_tabs(document)
    if not flat:
        return "Error: document has no tabs."
    if tab is None:
        return flat[0]
    needle = tab.strip().lower()
    for entry in flat:
        if entry["id"] == tab.strip() or entry["title"].lower() == needle:
            return entry
    available = ", ".join(f"{e['title']!r}" for e in flat)
    return f"Error: no tab matching {tab!r}. Available tabs: {available}."


def _extract_text(body: dict) -> str:
    """Walk a body's content and return plain text.

    Plain text only: bold/headings/etc. are not represented. Tables and
    images become a short marker. Good enough for a collaborative writing
    canvas, not a fidelity-preserving export.
    """
    out: list[str] = []
    for el in body.get("content", []):
        para = el.get("paragraph")
        if para:
            for pe in para.get("elements", []):
                run = pe.get("textRun")
                if run:
                    out.append(run.get("content", ""))
        elif "table" in el:
            out.append("[table omitted]\n")
    return "".join(out)


@mcp.tool()
def list_tabs(doc: str) -> str:
    """List the tabs in a shared Google Doc.

    Args:
        doc: The Doc URL or its document ID.

    Returns:
        Each tab's title, one per line, or a note that the doc has a single tab.
    """
    try:
        document = _fetch(doc)
    except HttpError as e:
        return f"Error reading doc: {e}"
    except Exception as e:
        return f"Unexpected error reading doc: {e}"
    flat = _flatten_tabs(document)
    if len(flat) <= 1:
        return "This document has a single tab."
    return "Tabs:\n" + "\n".join(f"- {e['title']}" for e in flat)


@mcp.tool()
def read_doc(doc: str, tab: str | None = None) -> str:
    """Read the current text of a shared Google Doc.

    Args:
        doc: The Doc URL or its document ID.
        tab: Which tab to read, by title or tab ID. Omit to read the first tab.

    Returns:
        The tab's plain text, capped at ~50,000 characters. Formatting is not
        preserved. On failure, an error message describing what went wrong.
    """
    try:
        document = _fetch(doc)
    except HttpError as e:
        return f"Error reading doc: {e}"
    except Exception as e:
        return f"Unexpected error reading doc: {e}"
    entry = _resolve_tab(document, tab)
    if isinstance(entry, str):
        return entry
    text = _extract_text(entry["tab"].get("documentTab", {}).get("body", {}))
    flat = _flatten_tabs(document)
    prefix = ""
    if tab is None and len(flat) > 1:
        others = ", ".join(f"{e['title']!r}" for e in flat)
        prefix = (
            f"(This doc has multiple tabs: {others}. Showing {entry['title']!r}. "
            f"Pass tab= to read another.)\n\n"
        )
    if not text.strip():
        return prefix + "(This tab is empty.)"
    return (prefix + text)[:MAX_RETURN_CHARS]


@mcp.tool()
def replace_text(
    doc: str, find: str, replace: str, match_case: bool = False, tab: str | None = None
) -> str:
    """Replace all occurrences of `find` with `replace` in a shared Google Doc.

    Use this to edit, fix, or rewrite existing text. To insert new text into the
    middle of the document, replace a unique nearby anchor with itself plus your
    new text, e.g. find "...end of the paragraph." and replace with
    "...end of the paragraph.\\n\\nYour new paragraph."

    Args:
        doc: The Doc URL or its document ID.
        find: The exact text to search for. Use a unique enough string to avoid
            matching more places than you intend.
        replace: The text to put in its place.
        match_case: Whether the search is case-sensitive (default False).
        tab: Which tab to edit, by title or tab ID. Omit to edit the first tab.

    Returns:
        How many occurrences were replaced. If this is higher than expected,
        your `find` text was too broad — use a more unique anchor.
    """
    try:
        document = _fetch(doc)
    except HttpError as e:
        return f"Error reading doc: {e}"
    except Exception as e:
        return f"Unexpected error reading doc: {e}"
    entry = _resolve_tab(document, tab)
    if isinstance(entry, str):
        return entry
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": match_case},
                "replaceText": replace,
                "tabsCriteria": {"tabIds": [entry["id"]]},
            }
        }
    ]
    try:
        result = (
            _get_service()
            .documents()
            .batchUpdate(documentId=_doc_id(doc), body={"requests": requests})
            .execute()
        )
    except HttpError as e:
        return f"Error editing doc: {e}"
    except Exception as e:
        return f"Unexpected error editing doc: {e}"
    replies = result.get("replies", [{}])
    count = replies[0].get("replaceAllText", {}).get("occurrencesChanged", 0)
    if count == 0:
        return "No occurrences of that text were found — nothing changed."
    return f"Replaced {count} occurrence{'s' if count != 1 else ''} in {entry['title']!r}."


@mcp.tool()
def append_text(doc: str, text: str, tab: str | None = None) -> str:
    """Add text to the end of a shared Google Doc.

    Args:
        doc: The Doc URL or its document ID.
        text: The text to append. Include a leading newline if you want it to
            start on its own line.
        tab: Which tab to append to, by title or tab ID. Omit for the first tab.

    Returns:
        A confirmation, or an error message describing what went wrong.
    """
    try:
        document = _fetch(doc)
    except HttpError as e:
        return f"Error reading doc: {e}"
    except Exception as e:
        return f"Unexpected error reading doc: {e}"
    entry = _resolve_tab(document, tab)
    if isinstance(entry, str):
        return entry
    requests = [
        {"insertText": {"endOfSegmentLocation": {"tabId": entry["id"]}, "text": text}}
    ]
    try:
        _get_service().documents().batchUpdate(
            documentId=_doc_id(doc), body={"requests": requests}
        ).execute()
    except HttpError as e:
        return f"Error appending to doc: {e}"
    except Exception as e:
        return f"Unexpected error appending to doc: {e}"
    return f"Appended {len(text)} characters to the end of {entry['title']!r}."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
