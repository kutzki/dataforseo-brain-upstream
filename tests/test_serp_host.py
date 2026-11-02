"""serp_host finds the real site behind Google's GoTo redirects (live SERPs, 2026-11-02) and rank checks use it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dfs_safety  # noqa: E402
import run_baselines as rb  # noqa: E402

# Shapes copied from a real live SERP on 2026-11-02 (6 of 9 organic results looked like GOTO).
GOTO = {"type": "organic", "rank_group": 2, "domain": "google.com",
        "url": "https://google.com/goto?url=CAESdgHrOzAVHw8bGiOQI6X1hWFbtqXdbuhOnujm_f",
        "breadcrumb": "https://www.brevo.com › Blog", "website_name": "Brevo"}
PLAIN = {"type": "organic", "rank_group": 5, "domain": "www.expertmarket.com",
         "url": "https://www.expertmarket.com/ca/crm/best-crm-systems", "breadcrumb": "https://www.expertmarket.com › ca › crm"}
ODD = {"type": "organic", "rank_group": 7, "domain": "google.com", "url": "https://google.com/goto?url=x",
       "breadcrumb": "https://o'brien-&-co.example.co.uk › a|b"}


def test_goto_result_resolves_to_breadcrumb_site():
    assert dfs_safety.serp_host(GOTO) == "brevo.com"


def test_plain_result_keeps_its_domain():
    assert dfs_safety.serp_host(PLAIN) == "expertmarket.com"


def test_hostile_breadcrumb_host_is_kept_verbatim():
    assert dfs_safety.serp_host(ODD) == "o'brien-&-co.example.co.uk"


def test_goto_without_breadcrumb_is_unknown_not_google():
    assert dfs_safety.serp_host({"domain": "google.com", "url": "https://google.com/goto?url=x"}) == ""


def test_rank_check_finds_a_client_hidden_behind_goto():
    data = {"cost": 0.002, "tasks": [{"result": [{"items": [PLAIN, GOTO]}]}]}
    row = rb.score_rank({"domains": ["brevo.com"]}, "best crm", data)
    assert row["position"] == 2
