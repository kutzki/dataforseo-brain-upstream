"""The guard's cost estimates must track the live price table (catches the next price change)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dfs_guard  # noqa: E402

VAULT_PRICES = (sorted((Path.home() / "Documents" / "DataForSEO Brain" / "vault" / "_attachments").glob("live-prices-*.json"))
                or sorted((Path(__file__).resolve().parents[1] / "references").glob("live-prices-*.json")))

# (tool or api path, payload, price-table endpoint, rows billed)
CASES = [
    ("mcp__x__kw_data_google_ads_search_volume", {"keywords": ["a"] * 700}, "keywords_data/google_ads/search_volume/live", 0),
    ("mcp__x__dataforseo_labs_google_keyword_overview", {"keywords": ["a"] * 100}, "dataforseo_labs/keyword_overview/live", 100),
    ("mcp__x__dataforseo_labs_google_keyword_ideas", {"limit": 50}, "dataforseo_labs/keyword_ideas/live", 50),
    ("mcp__x__business_data_business_listings_search", {"limit": 25}, "business_data/business_listings/search/live", 25),
    ("mcp__x__backlinks_backlinks", {"limit": 50}, "backlinks/backlinks/live", 50),
    ("mcp__x__content_analysis_search", {"limit": 50}, "content_analysis/search/live", 50),
    ("mcp__x__on_page_lighthouse", {}, "on_page/lighthouse/live", 0),
    ("mcp__x__dataforseo_labs_google_historical_rank_overview", {}, "dataforseo_labs/historical_rank_overview/live", 0),
    ("api_request", {"path": "/v3/domain_analytics/technologies/domains_by_technology/live", "data": [{"limit": 100}]},
     "domain_analytics/technologies/domains_by_technology/live", 100),
    ("mcp__x__ai_opt_llm_ment_search", {"limit": 10}, "ai_optimization/llm_mentions/search_mentions/live", 10),
]


def live_price(table, endpoint, rows):
    req = max((e["cost"] for e in table if e["endpoint"] == endpoint and e["cost_type"] == "per_request"), default=0)
    per = max((e["cost"] for e in table if e["endpoint"] == endpoint and e["cost_type"] == "per_result"), default=0)
    return req + per * rows


@pytest.mark.skipif(not VAULT_PRICES, reason="vault price table not on this machine")
@pytest.mark.parametrize("tool,inp,endpoint,rows", CASES)
def test_estimate_matches_live_price(tool, inp, endpoint, rows):
    table = json.loads(VAULT_PRICES[-1].read_text(encoding="utf-8"))["entries"]
    expected = live_price(table, endpoint, rows)
    ep = dfs_guard.endpoint_of(tool, inp)
    got = dfs_guard.estimate(ep, dfs_guard.payload(tool, inp))
    assert expected > 0, f"{endpoint} missing from the price table"
    assert abs(got - expected) <= max(0.0015, expected * 0.15), f"{ep}: guard ${got:.4f} vs live ${expected:.4f}"


def test_ai_mode_is_priced_above_organic_serp():
    ai = dfs_guard.estimate(dfs_guard.endpoint_of("api_request", {"path": "/v3/serp/google/ai_mode/live/advanced"}), {})
    organic = dfs_guard.estimate("serp_organic_live_advanced", {"depth": 10})
    assert ai == pytest.approx(0.004) and organic == pytest.approx(0.002)


def test_clickstream_global_volume_is_expensive():
    ep = dfs_guard.endpoint_of("api_request", {"path": "/v3/keywords_data/clickstream_data/global_search_volume/live"})
    assert dfs_guard.estimate(ep, {}) == pytest.approx(0.18)
