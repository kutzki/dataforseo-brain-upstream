#!/usr/bin/env python3
"""Monthly AI-visibility run for every client set in the vault's clients.json.

One billed LLM Mentions request per client set (~$0.11), plus one Google AI Mode
SERP call per probe question in the client's "probes" list ($0.004 each; skip with
--no-ai-mode). Appends every run to
_attachments/ai-visibility.jsonl and writes a dated summary into wiki/reports/.
Reports movement against the previous run once a series exists.
"""
import argparse, datetime, json, os, pathlib, re, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dfs_safety

URL = "https://api.dataforseo.com/v3/ai_optimization/llm_mentions/multi_target_metrics/live"
AI_MODE_URL = "https://api.dataforseo.com/v3/serp/google/ai_mode/live/advanced"
MENTIONS_COST, ROW_COST = 0.10, 0.001   # price table 2026-10-02
AI_MODE_COST = 0.004   # billed per live call, measured 2026-10-02 (the price table shows one flat SERP rate)
RANK_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
RANK_DEPTH = 20        # pages one and two; billed per 10 results
RANK_COST = 0.002 + 0.0015 * (RANK_DEPTH // 10 - 1)   # extra pages bill $0.0015 (measured 10-03)
DEFAULT_VAULT = pathlib.Path(os.environ.get("DFS_BRAIN_VAULT") or pathlib.Path.home() / "Documents" / "DataForSEO Brain" / "vault")


def post(payload, url=URL, allow_repeat=False):
    return dfs_safety.call(url, payload, timeout=180, allow_repeat=allow_repeat)   # logs the exact cost to the vault call log


def run_set(client):
    body = {"language_code": client.get("language_code", "en"),
            "location_code": client.get("location_code", 2840),
            "internal_list_limit": 5,
            "targets": [{"key": d, "target": [{"domain": d, "search_filter": "include"}]}
                        for d in client["domains"]]}
    data = post([body])
    if data.get("status_code") != 20000:
        return None, f"request failed: {data.get('status_code')} {data.get('status_message')}"
    task = data["tasks"][0]
    if task.get("status_code") != 20000:
        return None, f"task failed: {task.get('status_code')} {task.get('status_message')}"
    items = (task.get("result") or [{}])[0].get("items") or []
    rows = sorted((
        {"target": i.get("key"),
         "mentions": (i.get("total") or {}).get("mentions") or 0,
         "ai_search_volume": (i.get("total") or {}).get("ai_search_volume") or 0}
        for i in items), key=lambda r: r["ai_search_volume"], reverse=True)
    return {"cost": data.get("cost"), "rows": rows}, None


def estimate(clients, ai_mode=True):
    total = sum(MENTIONS_COST + ROW_COST * len(c["domains"]) for c in clients)
    if ai_mode:
        total += AI_MODE_COST * sum(len(c.get("probes", [])) for c in clients)
    total += RANK_COST * sum(len(c.get("rank_checks", [])) for c in clients)
    return total


def rank_payload(client, keyword):
    return [{"keyword": keyword, "location_code": client.get("location_code", 2840),
             "language_code": client.get("language_code", "en"), "depth": RANK_DEPTH}]


def score_rank(client, keyword, data):
    """Where the client's own domain ranks for one keyword (organic, top RANK_DEPTH), or None."""
    items = (((data.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items")) or []
    own = client["domains"][0]
    # serp_host, not the url: live SERPs hide about half the results behind google.com/goto (2026-11-02).
    hit = next((i for i in items if i.get("type") == "organic"
                and (dfs_safety.serp_host(i) == own or dfs_safety.serp_host(i).endswith("." + own))), None)
    serp = [dfs_safety.serp_host(i) for i in items if i.get("type") == "organic"]
    return {"keyword": keyword, "position": hit.get("rank_group") if hit else None,
            "url": hit.get("url") if hit else None, "cost": data.get("cost") or 0, "serp": [s for s in serp if s]}


POISON_OVERLAP = 0.5   # DataForSEO's definition: under half the usual sites anywhere on the page


def serp_overlap(usual, now):
    """Share of the sites that usually rank (last trusted reading's top 10) found anywhere in this reading."""
    usual = [s for s in usual[:10] if s]
    return sum(1 for s in usual if s in set(now)) / len(usual) if usual else None


def run_ranks(client, history=None):
    """Rank checks with a poisoned-SERP guard.

    Since 2026-09-26 Google serves bots 'poisoned' SERPs: normal-looking pages with most organic results swapped
    (20-30% of scraped SERPs per DataForSEO's CTO, blog 2026-10-23). A reading that keeps under half of the
    keyword's usual sites is re-requested once; if it still looks poisoned the row is marked unconfirmed and the
    trusted history isn't overwritten. `history` maps keyword -> last trusted top-10 sites (updated in place)."""
    history = {} if history is None else history
    rows = []
    for kw in client.get("rank_checks", []):
        row = None
        for attempt in (0, 1):
            data = post(rank_payload(client, kw), RANK_URL, allow_repeat=attempt > 0)
            task = (data.get("tasks") or [{}])[0]
            if data.get("status_code") != 20000 or task.get("status_code") != 20000:
                row = {"keyword": kw, "error": f"{task.get('status_code')} {task.get('status_message')}",
                       "cost": data.get("cost") or 0}
                break
            got = score_rank(client, kw, data)
            got["cost"] += row["cost"] if row else 0
            got["overlap"] = serp_overlap(history.get(kw, []), got["serp"])
            row = got
            if got["overlap"] is None or got["overlap"] >= POISON_OVERLAP:
                break
        if "error" not in row:
            if row["overlap"] is not None and row["overlap"] < POISON_OVERLAP:
                row["unconfirmed"] = (f"SERP looked poisoned twice ({row['overlap']:.0%} of the usual sites); "
                                      "confirm in Search Console or the next reading")
            else:
                history[kw] = row["serp"][:10]
        rows.append(row)
    return rows


def ai_mode_payload(client, probe):
    return [{"keyword": probe, "location_code": client.get("location_code", 2840),
             "language_code": client.get("language_code", "en")}]


def _host(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


def ai_mode_urls(answer):
    """Citations live in four places: top-level references, each section's references and links, and inline markdown links."""
    urls = [r.get("url") for r in answer.get("references") or []]
    for el in answer.get("items") or []:
        urls += [r.get("url") for r in (el.get("references") or []) + (el.get("links") or [])]
    urls += re.findall(r"\]\((https?://[^)\s]+)\)", answer.get("markdown") or "")
    return [u for u in urls if u]


def score_ai_mode(client, probe, data):
    """One probe's AI Mode answer: is it answered, is the client named or cited, which tracked domains are cited."""
    items = (((data.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items")) or []
    answer = next((i for i in items if i.get("type") == "ai_overview"), None)
    text = (answer or {}).get("markdown") or ""
    hosts = {_host(u) for u in ai_mode_urls(answer or {})}
    cited = [d for d in client["domains"] if any(h == d or h.endswith("." + d) for h in hosts)]
    brand = client.get("brand") or client["domains"][0].split(".")[0]
    return {"probe": probe, "answered": bool(text),
            "client_named": bool(re.search(r"\b" + re.escape(brand) + r"\b", text, re.I)),
            "client_cited": client["domains"][0] in cited,
            "cited": cited, "cost": data.get("cost") or 0}


def run_ai_mode(client):
    rows = []
    for probe in client.get("probes", []):
        data = post(ai_mode_payload(client, probe), AI_MODE_URL)
        task = (data.get("tasks") or [{}])[0]
        if data.get("status_code") != 20000 or task.get("status_code") != 20000:
            rows.append({"probe": probe, "error": f"{task.get('status_code')} {task.get('status_message')}",
                         "cost": data.get("cost") or 0})
            continue
        rows.append(score_ai_mode(client, probe, data))
    return rows


def previous(series_path, name):
    if not series_path.exists():
        return None
    prev = None
    for line in series_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("client") == name:
            prev = rec
    return prev


def previous_report(vault, stamp, slug):
    """Most recent earlier report of the same scope, so reports chain chronologically."""
    reports = vault / "wiki" / "reports"
    if not reports.exists():
        return None
    pattern = re.compile(r"^rep-ai-visibility-(\d{4}-\d{2}-\d{2})" + re.escape(slug) + r"\.md$")
    dated = []
    for f in reports.glob("rep-ai-visibility-*.md"):
        m = pattern.match(f.name)
        if m and m.group(1) < stamp:
            dated.append((m.group(1), f.stem))
    return sorted(dated)[-1][1] if dated else None


def index_report(vault, report_name, stamp, spent, scope):
    """Link the new report from wiki/reports/_index.md so it is never an orphan."""
    idx = vault / "wiki" / "reports" / "_index.md"
    if not idx.exists():
        return
    text = idx.read_text(encoding="utf-8")
    if "[[" + report_name + "]]" in text:
        return
    marker = "## Generated runs"
    if marker not in text:
        text = text.rstrip("\n") + "\n\n" + marker + "\n"
    entry = "- [[" + report_name + "]] - " + stamp + " | " + scope + " | $" + format(spent, ".3f")
    idx.write_text(text.rstrip("\n") + "\n" + entry + "\n", encoding="utf-8")


def commit_vault(vault, message):
    """Record the run in the vault's local git history, if it has one. Never fails the run."""
    if not (pathlib.Path(vault) / ".git").exists():
        return
    who = ["-c", "user.name=DataForSEO Brain (scheduled)", "-c", "user.email=dataforseo-brain@localhost"]
    try:
        subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True, timeout=60)
        r = subprocess.run(["git", "-C", str(vault), *who, "commit", "-q", "-m", message], capture_output=True, text=True, timeout=60)
        if r.returncode not in (0, 1):   # 1 = nothing to commit
            print(f"WARNING: vault commit failed: {r.stderr.strip()[:200]}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARNING: vault commit skipped: {exc}", file=sys.stderr)


SPEC_COMMITS = "https://api.github.com/repos/dataforseo/OpenApiDocumentation/commits?per_page=1"


def spec_check(vault):
    """Free: is DataForSEO's OpenAPI spec newer than the copy the lint validates against?
    Returns (report lines, needs_attention). Network trouble is reported, never fatal."""
    import urllib.request
    prov = pathlib.Path(vault) / ".raw" / "sources" / "dataforseo-openapi" / "PROVENANCE.json"
    if not prov.exists():
        return [], False
    ours = json.loads(prov.read_text(encoding="utf-8")).get("commit", "")
    try:
        req = urllib.request.Request(SPEC_COMMITS, headers={"User-Agent": "dataforseo-brain"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            latest = json.load(resp)[0]
    except Exception as exc:   # never lose the paid results over a free check
        return [f"\nOpenAPI spec check skipped: {exc}\n"], False
    sha, date = latest["sha"], latest["commit"]["committer"]["date"][:10]
    if ours and sha.startswith(ours):
        return [f"\nOpenAPI spec: current (commit {ours}).\n"], False
    return [f"\n**DataForSEO updated its OpenAPI spec** (commit {sha[:12]}, {date}; the vault has {ours or 'none'}). "
            "Run `python scripts/refresh_spec.py` (free): it archives the old copy, updates PROVENANCE.json and lists "
            "the endpoint, parameter and schema changes to record in the changelog note.\n"], True


UPDATES_API = "https://dataforseo.com/wp-json/wp/v2/update?per_page=20&_fields=date,title,link"


BLOG_API = "https://dataforseo.com/wp-json/wp/v2/posts?per_page=20&_fields=date,title,link"
# Blog posts that change how data should be read (the 2026-10-23 poisoned-SERP post never reached the update feed).
BLOG_RELEVANT = re.compile(r"(?i)\b(serp|google|api|data|poison|outage|incident|price|pricing|deprecat|retir|limit|change)")


def blog_check(vault):
    """Free: DataForSEO blog posts about data quality, API or pricing changes posted since the changelog note's
    Checked date and not linked in it. Same contract as changelog_check."""
    lines, attention = changelog_check(vault, api=BLOG_API, kind="blog post(s) about data or the API",
                                       keep=lambda title: bool(BLOG_RELEVANT.search(title)))
    return lines, attention


def changelog_check(vault, api=None, kind="product update(s)", keep=None):
    """Free: DataForSEO product updates (retirements, price and parameter changes) posted after the
    changelog note was last checked. Returns (report lines, needs_attention); network trouble is never fatal."""
    import urllib.request
    api = api or UPDATES_API
    note = pathlib.Path(vault) / "wiki" / "sources" / "dfs-changelog.md"
    if not note.exists():
        return [], False
    text = note.read_text(encoding="utf-8")
    m = re.search(r"\*\*Checked:\*\* (\d{4}-\d{2}-\d{2})", text)
    if not m:
        return [], False
    checked = m.group(1)
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0 (dataforseo-brain)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            posts = json.load(resp)
    except Exception as exc:   # never lose the paid results over a free check
        return [f"\nDataForSEO {kind} check skipped: {exc}\n"], False
    # same-day posts count too unless already recorded: a post later on the checked day was missed by ">"
    new = [p for p in posts if p.get("date", "")[:10] >= checked and p.get("link", "") not in text
           and (keep is None or keep(re.sub(r"<[^>]+>", "", (p.get("title") or {}).get("rendered", ""))))]
    if not new:
        return [f"\nDataForSEO {kind}: nothing new since {checked}.\n"], False
    lines = [f"\n**DataForSEO posted {len(new)} {kind} since the changelog note was checked ({checked}):**\n"]
    for p in new:
        title = re.sub(r"<[^>]+>", "", (p.get("title") or {}).get("rendered", "")).strip()
        lines.append(f"- {p['date'][:10]}: [{title}]({p.get('link', '')})")
    lines.append("\nRecord them in `wiki/sources/dfs-changelog.md` and update its **Checked** date.\n")
    return lines, True


def price_check(vault):
    """Free: refresh the account price table and lint the vault's price claims.
    Returns (report lines, needs_attention)."""
    import os
    import check_prices
    import refresh_prices
    os.environ.setdefault("DFS_VAULT", str(vault))
    try:
        changed = refresh_prices.main()
    except (SystemExit, Exception) as exc:   # never lose the paid results over a free check
        return [f"\n## Price check\n\nPrice refresh failed: {exc}\n"], True
    drift = check_prices.check(pathlib.Path(vault))
    lines = ["\n## Price check\n",
             ("**DataForSEO changed its prices.** A new `live-prices-*.json` was saved; re-run the guard price tests "
              "and update the notes that quote old prices." if changed else "Prices unchanged since the last table."),
             ""]
    if drift:
        lines += [f"**{len(drift)} note line(s) now disagree with the price table:**", ""] + [f"- {d}" for d in drift[:20]] + [""]
    return lines, bool(changed or drift)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", type=pathlib.Path, default=DEFAULT_VAULT)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and cost, call nothing")
    ap.add_argument("--only", action="append", default=[],
                    help="run only this client (repeatable); must match a name in clients.json")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip this client (repeatable)")
    ap.add_argument("--max-spend", type=float, default=1.00,
                    help="hard cap in USD (default 1.00). Refuses to start if the "
                         "estimate exceeds it; stops mid-run if actual spend does.")
    ap.add_argument("--no-ai-mode", action="store_true",
                    help="skip the Google AI Mode probes ($0.004 per probe)")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="skip the free connectivity check (not recommended)")
    args = ap.parse_args()

    cfg = json.loads((args.vault / "clients.json").read_text(encoding="utf-8"))
    clients = cfg["clients"]
    known = {c["name"] for c in clients}
    for name in args.only + args.exclude:
        if name not in known:
            sys.exit(f"unknown client {name!r}; clients.json has: {', '.join(sorted(known))}")
    if args.only:
        clients = [c for c in clients if c["name"] in args.only]
    if args.exclude:
        clients = [c for c in clients if c["name"] not in args.exclude]
    if not clients:
        sys.exit("no clients selected")
    slug = ""
    if args.only:
        slug = "-" + "-".join(re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-") for n in sorted(args.only))
    stamp = datetime.date.today().isoformat()
    series = args.vault / "_attachments" / "ai-visibility.jsonl"

    ai_mode = not args.no_ai_mode
    if args.dry_run:
        print(f"{len(clients)} client sets, estimated ${estimate(clients, ai_mode):.3f}")
        for c in clients:
            probes = len(c.get("probes", [])) if ai_mode else 0
            print(f"  {c['name']:<18} {len(c['domains'])} targets  {probes} AI Mode probes  "
                  f"{len(c.get('rank_checks', []))} rank checks  location {c['location_code']}")
        return

    # --- free safety checks; nothing below is billed until they pass
    if not args.skip_preflight:
        dfs_safety.preflight()
    dfs_safety.guard(estimate(clients, ai_mode), args.max_spend)

    spent, lines, stopped = 0.0, [], None
    summary = [f"# AI Visibility — {stamp}\n"]
    for c in clients:
        if spent >= args.max_spend:
            stopped = c["name"]
            print(f"!! HARD STOP: ${spent:.3f} hit the ${args.max_spend:.2f} cap "
                  f"before {c['name']}", file=sys.stderr)
            summary.append(f"\n## HARD STOP\n\nSpend cap ${args.max_spend:.2f} reached "
                           f"after ${spent:.3f}. Not run: {c['name']} and any client "
                           f"after it.\n")
            break
        res, err = run_set(c)
        if err:
            print(f"!! {c['name']}: {err}", file=sys.stderr)
            summary.append(f"\n## {c['name']}\n\n**FAILED:** {err}\n")
            continue
        spent += res["cost"] or 0
        prev = previous(series, c["name"])
        prev_map = {r["target"]: r for r in (prev or {}).get("rows", [])}

        print(f"\n{c['name']}  (location {c['location_code']})  ${res['cost']}")
        summary.append(f"\n## {c['name']}\n")
        summary.append(f"`location_code` {c['location_code']} · cost ${res['cost']}\n")
        summary.append("| Target | mentions | AI search volume | Δ mentions |")
        summary.append("|---|---|---|---|")
        for r in res["rows"]:
            p = prev_map.get(r["target"])
            delta = "—" if not p else f"{r['mentions'] - p['mentions']:+d}"
            mark = " **(client)**" if r["target"] == c["domains"][0] else ""
            print(f"  {r['target']:<28} {r['mentions']:>8} {r['ai_search_volume']:>12}  {delta}")
            summary.append(f"| {r['target']}{mark} | {r['mentions']:,} | {r['ai_search_volume']:,} | {delta} |")
        record = {"date": stamp, "client": c["name"], "location_code": c["location_code"],
                  "cost": res["cost"], "rows": res["rows"]}
        if ai_mode and c.get("probes") and spent < args.max_spend:
            probes = run_ai_mode(c)
            spent += sum(p["cost"] for p in probes)
            record["ai_mode"] = probes
            summary.append("\n### Google AI Mode\n")
            summary.append("| Probe | Answered | Client named | Client cited | Tracked domains cited |")
            summary.append("|---|---|---|---|---|")
            for p in probes:
                if "error" in p:
                    summary.append(f"| {p['probe']} | **error** {p['error']} | | | |")
                    continue
                yes = lambda b: "yes" if b else "**no**"
                summary.append(f"| {p['probe']} | {yes(p['answered'])} | {yes(p['client_named'])} | "
                               f"{yes(p['client_cited'])} | {', '.join(p['cited']) or '-'} |")
            print(f"  AI Mode: {sum(p.get('client_named', False) for p in probes)}/{len(probes)} probes name the client")
        if c.get("rank_checks") and spent < args.max_spend:
            hist_path = args.vault / "_attachments" / "rank-serp-history.json"
            all_hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
            hist = all_hist.setdefault(f"{c['name']}|{c.get('location_code', 2840)}", {})
            ranks = run_ranks(c, hist)
            hist_path.write_text(json.dumps(all_hist, indent=1), encoding="utf-8")
            spent += sum(r["cost"] for r in ranks)
            record["ranks"] = ranks
            summary.append(f"\n### Google rank (organic, top {RANK_DEPTH})\n")
            summary.append("| Keyword | Position | URL |")
            summary.append("|---|---|---|")
            for r in ranks:
                if "error" in r:
                    summary.append(f"| {r['keyword']} | **error** {r['error']} | |")
                else:
                    pos = r['position'] or f'not in top {RANK_DEPTH}'
                    pos = f"{pos} (unconfirmed: {r['unconfirmed']})" if r.get("unconfirmed") else pos
                    summary.append(f"| {r['keyword']} | {pos} | {r['url'] or '-'} |")
                print(f"  rank {r['keyword']!r}: {r.get('position') or r.get('error') or '-'}")
        lines.append(json.dumps(record))

    series.parent.mkdir(parents=True, exist_ok=True)
    with series.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")

    price_lines, price_alert = price_check(args.vault)
    summary += price_lines
    spec_lines, spec_alert = spec_check(args.vault)
    summary += spec_lines
    price_alert = price_alert or spec_alert
    log_lines, log_alert = changelog_check(args.vault)
    summary += log_lines
    price_alert = price_alert or log_alert
    summary.append(f"\n---\n\nTotal spend this run: **${spent:.3f}**. "
                   f"Series: `_attachments/ai-visibility.jsonl`.\n")
    summary.append("")
    summary.append("## Related")
    related = ["dfs-openapi-spec", "lesson-log"]
    prev_report = previous_report(args.vault, stamp, slug)
    if prev_report:
        related.insert(1, prev_report)
    for link in related:
        summary.append("- [[" + link + "]]")
    summary.append("")
    report = args.vault / "wiki" / "reports" / f"rep-ai-visibility-{stamp}{slug}.md"
    if report.exists():
        print(f"note: overwriting existing {report.name}", file=sys.stderr)
    report.write_text(
        f"---\ntype: report\ntitle: \"Report: AI Visibility {stamp}\"\nstatus: stable\n"
        f"created: {stamp}\nupdated: {stamp}\ntags: [dataforseo, ai-optimization, monthly]\n---\n\n"
        + "\n".join(summary), encoding="utf-8")
    scope = ", ".join(c["name"] for c in clients) if len(clients) <= 3 else f"{len(clients)} clients"
    index_report(args.vault, report.stem, stamp, spent, scope)
    commit_vault(args.vault, f"run: AI visibility {stamp} ({scope}, ${spent:.3f})")
    print(f"\ntotal ${spent:.3f} — wrote {report.name}")
    if stopped:
        print(f"INCOMPLETE: stopped at the spend cap before {stopped}", file=sys.stderr)
        return 1
    if price_alert:
        print("ATTENTION: DataForSEO prices, its OpenAPI spec or its changelog changed, or notes disagree; see the report's Price check section", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
