"""run_ranks re-requests a SERP that drops most of the keyword's usual sites (a 'poisoned' SERP, DataForSEO blog
2026-10-23) and marks it unconfirmed if the retry is poisoned too, without overwriting the trusted history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_baselines as rb  # noqa: E402

CLIENT = {"domains": ["o'brien-&-co.com"], "rank_checks": ["best crm | small biz"]}
USUAL = ["hubspot.com", "salesforce.com", "zoho.com", "pipedrive.com", "o'brien-&-co.com", "monday.com"]
POISONED = ["youtube.com", "reddit.com", "quora.com", "zoho.com", "wikipedia.org", "medium.com"]


def serp(domains):
    items = [{"type": "organic", "rank_group": n + 1, "domain": d, "url": f"https://{d}/x"} for n, d in enumerate(domains)]
    return {"status_code": 20000, "cost": 0.002, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}


def fake_calls(monkeypatch, responses):
    calls = []

    def post(payload, url=None, allow_repeat=False):
        calls.append(allow_repeat)
        return responses[len(calls) - 1]
    monkeypatch.setattr(rb, "post", post)
    return calls


def test_first_reading_is_trusted_and_becomes_history(monkeypatch):
    calls = fake_calls(monkeypatch, [serp(USUAL)])
    hist = {}
    row = rb.run_ranks(CLIENT, hist)[0]
    assert row["position"] == 5 and "unconfirmed" not in row
    assert hist["best crm | small biz"] == USUAL and calls == [False]


def test_poisoned_reading_is_retried_and_the_clean_retry_wins(monkeypatch):
    calls = fake_calls(monkeypatch, [serp(POISONED), serp(USUAL)])
    hist = {"best crm | small biz": USUAL}
    row = rb.run_ranks(CLIENT, hist)[0]
    assert calls == [False, True]                     # the retry is allowed past the repeat guard
    assert row["position"] == 5 and "unconfirmed" not in row
    assert abs(row["cost"] - 0.004) < 1e-9            # both readings are billed and counted


def test_twice_poisoned_is_unconfirmed_and_history_is_kept(monkeypatch):
    fake_calls(monkeypatch, [serp(POISONED), serp(POISONED)])
    hist = {"best crm | small biz": USUAL}
    row = rb.run_ranks(CLIENT, hist)[0]
    assert row["position"] is None and "poisoned" in row["unconfirmed"]
    assert hist["best crm | small biz"] == USUAL


def test_overlap_definition():
    assert rb.serp_overlap(USUAL, POISONED) == 1 / 6
    assert rb.serp_overlap([], POISONED) is None
