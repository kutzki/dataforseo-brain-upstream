#!/usr/bin/env python3
"""Free: refresh the vault's copy of DataForSEO's OpenAPI spec and report what changed.

  python refresh_spec.py [--vault PATH] [--dry-run]

Downloads the latest spec from GitHub, archives the current copy, updates PROVENANCE.json (with history), and
prints a diff: endpoints added/removed, request parameters added/removed per endpoint, and schemas (response
element types) added/removed. Paste the summary into wiki/sources/dfs-changelog.md. Replaces the manual
"re-download it, update PROVENANCE.json" step the drift watch asks for."""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_baselines as rb  # noqa: E402

RAW = "https://raw.githubusercontent.com/dataforseo/OpenApiDocumentation/{sha}/openapi_specification.yaml"
METHODS = ("get", "post", "put", "delete", "patch")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "dataforseo-brain"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _schema_props(spec, schema, depth=0):
    """Property names of a request schema, following $ref and array items a few levels."""
    if not isinstance(schema, dict) or depth > 4:
        return set()
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _schema_props(spec, spec.get("components", {}).get("schemas", {}).get(name, {}), depth + 1)
    if schema.get("type") == "array":
        return _schema_props(spec, schema.get("items", {}), depth + 1)
    props = set(schema.get("properties", {}))
    for part in schema.get("allOf", []):
        props |= _schema_props(spec, part, depth + 1)
    return props


def summarize(spec):
    """{endpoint: set(request params)} and the set of schema names."""
    endpoints = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            if method not in METHODS:
                continue
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema", {})
            params = _schema_props(spec, body) | {p.get("name") for p in op.get("parameters", []) if isinstance(p, dict)}
            endpoints[f"{method.upper()} {path}"] = {p for p in params if p}
    return endpoints, set((spec.get("components") or {}).get("schemas") or {})


def diff(old, new):
    """Human-readable change list between two summarize() results."""
    (old_ep, old_sc), (new_ep, new_sc) = old, new
    out = []
    added, removed = sorted(set(new_ep) - set(old_ep)), sorted(set(old_ep) - set(new_ep))
    out += [f"+ endpoint {e}" for e in added] + [f"- endpoint {e}" for e in removed]
    for e in sorted(set(old_ep) & set(new_ep)):
        plus, minus = sorted(new_ep[e] - old_ep[e]), sorted(old_ep[e] - new_ep[e])
        if plus:
            out.append(f"~ {e}: new params {', '.join(plus)}")
        if minus:
            out.append(f"~ {e}: removed params {', '.join(minus)}")
    out += [f"+ schema {s}" for s in sorted(new_sc - old_sc)] + [f"- schema {s}" for s in sorted(old_sc - new_sc)]
    return out


def main():
    import yaml
    # BaseLoader keeps scalars as strings: the spec has example "dates" PyYAML can't parse (month 13).
    LOADER = getattr(yaml, "CBaseLoader", yaml.BaseLoader)
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", type=pathlib.Path, default=rb.DEFAULT_VAULT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    folder = a.vault / ".raw" / "sources" / "dataforseo-openapi"
    prov_path, spec_path = folder / "PROVENANCE.json", folder / "openapi_specification.yaml"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    latest = json.loads(fetch(rb.SPEC_COMMITS))[0]
    sha, date = latest["sha"], latest["commit"]["committer"]["date"][:10]
    if sha.startswith(prov.get("commit", "-")):
        print(f"Spec already current (commit {prov['commit']}).")
        return 0
    raw = fetch(RAW.format(sha=sha))
    old_spec = yaml.load(spec_path.read_text(encoding="utf-8"), Loader=LOADER)
    new_spec = yaml.load(raw, Loader=LOADER)
    changes = diff(summarize(old_spec), summarize(new_spec))
    n_new = sum(1 for p in new_spec.get("paths") or {} if p.startswith("/v3/"))   # same count as earlier PROVENANCE entries
    print(f"Spec {prov.get('commit')} ({prov.get('commit_date')}) -> {sha[:12]} ({date}); endpoints {prov.get('endpoints')} -> {n_new}")
    print("\n".join(changes) if changes else "No endpoint, parameter or schema changes (descriptions only).")
    if a.dry_run:
        return 0
    archived = folder / "archive" / f"openapi_specification-{prov.get('commit_date')}-{prov.get('commit')}.yaml"
    archived.parent.mkdir(exist_ok=True)
    if not archived.exists():
        archived.write_bytes(spec_path.read_bytes())
    spec_path.write_bytes(raw)
    history = [{k: prov.get(k) for k in ("retrieved", "commit", "commit_date", "sha256", "endpoints")}
               | {"archived_as": str(archived.relative_to(a.vault)).replace("\\", "/")}] + prov.get("history", [])
    prov.update({"retrieved": datetime.date.today().isoformat(), "commit": sha[:12], "commit_date": date,
                 "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "endpoints": n_new, "history": history,
                 "changes_vs_previous": "; ".join(changes[:40]) or "descriptions only"})
    prov_path.write_text(json.dumps(prov, indent=2), encoding="utf-8")
    print(f"Saved. Archived the old copy as {archived.name}; PROVENANCE.json updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
