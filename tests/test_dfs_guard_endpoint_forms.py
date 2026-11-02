"""api_request calls must resolve to the same endpoint whether they name it by path, by full url, or with the .ai
suffix (DataForSEO MCP 3.x). An unresolved endpoint skips every cap and price rule (found 2026-11-01)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dfs_guard  # noqa: E402

TOOL = "mcp__dataforseo__api_request"
WANT = "api:serp/google/organic/live/advanced"


def test_path_form():
    assert dfs_guard.endpoint_of(TOOL, {"path": "/v3/serp/google/organic/live/advanced"}) == WANT


def test_full_url_form():
    url = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
    assert dfs_guard.endpoint_of(TOOL, {"url": url}) == WANT


def test_url_with_query_string():
    url = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced?x=1"
    assert dfs_guard.endpoint_of(TOOL, {"url": url}) == WANT


def test_ai_suffix_is_the_same_endpoint():
    assert dfs_guard.endpoint_of(TOOL, {"path": "v3/serp/google/organic/live/advanced.ai"}) == WANT


def test_relative_url_without_scheme():
    assert dfs_guard.endpoint_of(TOOL, {"url": "/v3/serp/google/organic/live/advanced"}) == WANT


def test_named_tools_unchanged():
    assert dfs_guard.endpoint_of("mcp__x__dataforseo_labs_google_keyword_overview", {}) == "dataforseo_labs_google_keyword_overview"
