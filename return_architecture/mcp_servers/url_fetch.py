"""URL-fetch MCP server.

Fetches a URL and returns readable text using trafilatura.

Runnable as: python -m return_architecture.mcp_servers.url_fetch

The MCP runtime in Return Architecture launches this as a subprocess and
speaks JSON-RPC to it over stdio. The server side uses FastMCP from the
`mcp` package — minimal boilerplate per tool.
"""

from __future__ import annotations

import httpx
import trafilatura
from mcp.server.fastmcp import FastMCP


mcp = FastMCP("url-fetch")

MAX_RETURN_CHARS = 50_000


@mcp.tool()
def fetch_url(url: str) -> str:
    """Fetch a URL and return its readable text content.

    Args:
        url: The URL to fetch (http or https).

    Returns:
        Cleaned text content of the page, capped at ~50,000 characters.
        On failure, returns an error message describing what went wrong.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"Error: URL must start with http:// or https:// (got: {url})"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "return-architecture/0.1 (+url-fetch)"})
            resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error fetching {url}: {e}"
    except Exception as e:
        return f"Unexpected error fetching {url}: {e}"

    extracted = trafilatura.extract(resp.text)
    if extracted:
        return extracted[:MAX_RETURN_CHARS]
    # Fall back to a truncated stripped HTML pass.
    return (
        f"(No readable text extracted from {url}. "
        f"Raw response was {len(resp.text)} characters.)"
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
