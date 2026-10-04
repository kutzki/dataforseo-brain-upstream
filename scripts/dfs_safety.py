#!/usr/bin/env python3
"""Free preflight and balance guards for billed DataForSEO runs.

Both checks cost $0. Pattern adopted from DataForSEO's own Claude skills, which
make a free utility call to confirm connectivity before any paid work rather
than discovering a broken setup after the first billed call.

Free endpoints used (verified $0 in the account price table, 2026-09-17):
  GET /v3/appendix/user_data
  GET /v3/ai_optimization/llm_mentions/locations_and_languages
"""
import base64, json, os, sys, time, urllib.error, urllib.request

BASE = "https://api.dataforseo.com"
USER_DATA = "/v3/appendix/user_data"
LLM_LOCATIONS = "/v3/ai_optimization/llm_mentions/locations_and_languages"
# Same file the MCP guard hook writes, so MCP and REST spend sit in one record.
VAULT_LOG = os.environ.get("DFS_GUARD_VAULT_LOG") or os.path.join(
    os.path.expanduser("~"), "Documents", "DataForSEO Brain", "vault", "_attachments", "dfs-calls.jsonl")


def credentials():
    """DATAFORSEO_LOGIN is the vendor-canonical name; USERNAME kept as alias."""
    login = os.environ.get("DATAFORSEO_LOGIN") or os.environ.get("DATAFORSEO_USERNAME")
    password = os.environ.get("DATAFORSEO_PASSWORD")
    if not login or not password:
        sys.exit("set DATAFORSEO_LOGIN (or DATAFORSEO_USERNAME) and DATAFORSEO_PASSWORD")
    return login, password


def call(path, payload=None, timeout=60):
    """POST (or GET when payload is None) a /v3 path or full URL; billed calls are logged."""
    login, password = credentials()
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    log_rest(url, payload, data)
    return data


def log_rest(url, payload, data):
    """Append a billed REST call to the vault call log. Unlike MCP results, REST
    responses carry the exact cost, so these lines are true spend, not estimates."""
    if not isinstance(data, dict) or not data.get("cost"):
        return   # free calls (user_data, locations) would only add noise
    rec = {"phase": "rest", "t": time.time(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "source": os.path.basename(sys.argv[0] or "python"), "endpoint": url.split("/v3/", 1)[-1],
           "tasks": len(payload) if isinstance(payload, list) else 0, "cost": data.get("cost"),
           "status": data.get("status_code"), "tasks_error": data.get("tasks_error")}
    try:
        if os.path.isdir(os.path.dirname(VAULT_LOG)):
            with open(VAULT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:   # the result is already paid for; warn rather than lose it
        print(f"WARNING: could not log the call to {VAULT_LOG}: {exc}", file=sys.stderr)


def balance():
    """Account balance in USD. Free call. Returns None if unavailable."""
    try:
        data = call(USER_DATA)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"could not reach {USER_DATA}: {exc}"
    if data.get("status_code") != 20000:
        return None, f"{data.get('status_code')} {data.get('status_message')}"
    try:
        money = data["tasks"][0]["result"][0]["money"]
        return float(money["balance"]), None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return None, f"unexpected user_data shape: {exc}"


def preflight(verbose=True):
    """Confirm credentials and the llm_mentions family respond. Free. Exits on failure."""
    try:
        data = call(LLM_LOCATIONS)
    except urllib.error.HTTPError as exc:
        sys.exit(f"PREFLIGHT FAILED: HTTP {exc.code} on {LLM_LOCATIONS}\n"
                 f"  check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD. Nothing was billed.")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"PREFLIGHT FAILED: cannot reach the API ({exc}). Nothing was billed.")
    if data.get("status_code") != 20000:
        sys.exit(f"PREFLIGHT FAILED: {data.get('status_code')} {data.get('status_message')}\n"
                 f"  Nothing was billed.")
    if verbose:
        print(f"preflight ok ({LLM_LOCATIONS}, $0)")
    return True


def guard(estimate, max_spend, min_balance_multiple=2.0, verbose=True):
    """Hard-stop before any billed call.

    Refuses to run when the estimate exceeds max_spend, or when the balance
    would not comfortably cover it. Both checks are free.
    """
    if estimate > max_spend:
        sys.exit(f"ABORT: estimated ${estimate:.3f} exceeds --max-spend ${max_spend:.2f}. "
                 f"Nothing was billed.")
    bal, err = balance()
    if err:
        sys.exit(f"ABORT: could not verify balance ({err}). Nothing was billed.\n"
                 f"  Override with --skip-balance-check only if you accept the risk.")
    if verbose:
        print(f"balance ${bal:.2f} | estimate ${estimate:.3f} | cap ${max_spend:.2f}")
    if bal < estimate:
        sys.exit(f"ABORT: balance ${bal:.2f} is below the estimate ${estimate:.3f}. "
                 f"Nothing was billed.")
    if bal < estimate * min_balance_multiple:
        print(f"WARNING: balance ${bal:.2f} is under {min_balance_multiple}x the estimate.",
              file=sys.stderr)
    return bal
