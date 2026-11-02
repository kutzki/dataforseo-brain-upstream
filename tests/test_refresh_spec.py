"""refresh_spec.diff reports added/removed endpoints, request params (via $ref and arrays) and schemas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_spec as rs  # noqa: E402


def spec(paths, schemas):
    return {"paths": paths, "components": {"schemas": schemas}}


def post(ref):
    return {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "array", "items": {"$ref": f"#/components/schemas/{ref}"}}}}}}}


OLD = spec({"/v3/serp/google/local_finder/live": post("LF"), "/v3/gone": post("G")},
           {"LF": {"properties": {"keyword": {}, "min_rating": {}, "depth": {}}}, "G": {"properties": {"x": {}}}})
NEW = spec({"/v3/serp/google/local_finder/live": post("LF"), "/v3/ai_mode/live": post("AM")},
           {"LF": {"properties": {"keyword": {}, "depth": {}, "o'brien & co|x": {}}}, "AM": {"properties": {"keyword": {}}},
            "ai_overview_code_block_element": {}})


def test_diff_lists_every_kind_of_change():
    out = rs.diff(rs.summarize(OLD), rs.summarize(NEW))
    assert "+ endpoint POST /v3/ai_mode/live" in out
    assert "- endpoint POST /v3/gone" in out
    assert "~ POST /v3/serp/google/local_finder/live: removed params min_rating" in out
    assert "~ POST /v3/serp/google/local_finder/live: new params o'brien & co|x" in out
    assert "+ schema ai_overview_code_block_element" in out
    assert "- schema G" in out


def test_identical_specs_have_no_changes():
    assert rs.diff(rs.summarize(OLD), rs.summarize(OLD)) == []
