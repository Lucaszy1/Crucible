"""Static replay export: the bundle must be server-free and relative.

Builds docs/site/ into a temp folder and checks the export contract:
no references to localhost or /api anywhere, relative URLs only,
3 to 5 featured replays with events, scores, and real provenance,
a Layer 1 debrief per replay with citations into its own log,
Learning data with the frozen playbook, and disabled live controls
that point at a local run. No Docker, network, or model calls.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from crucible import debrief as debrief_mod

ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    path = ROOT / "scripts" / "build_static.py"
    spec = importlib.util.spec_from_file_location("build_static", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_to(tmp_path):
    builder = load_builder()
    out = tmp_path / "site"
    builder.build_site(out)
    return out, builder


def text_files(out):
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix.lower() not in builder_suffixes():
            yield path


def builder_suffixes():
    return load_builder().BINARY_SUFFIXES


def test_static_site_has_no_server_references(tmp_path):
    out, _builder = build_to(tmp_path)
    hits = []
    for path in text_files(out):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for marker in ("localhost", "/api"):
                if marker in line:
                    hits.append(f"{path.relative_to(out)}:{lineno}")
    assert hits == []


def test_static_site_urls_are_relative(tmp_path):
    out, _builder = build_to(tmp_path)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="/' not in html
    assert 'src="/' not in html
    assert "type=\"module\"" not in html
    css = (out / "css" / "style.css").read_text(encoding="utf-8")
    assert 'url("/' not in css


def test_static_site_featured_replays(tmp_path):
    out, _builder = build_to(tmp_path)
    featured = json.loads((out / "data" / "featured.json")
                          .read_text(encoding="utf-8"))["featured"]
    assert 3 <= len(featured) <= 5
    for run_id in featured:
        replay = json.loads((out / "data" / "runs" / f"{run_id}.json")
                            .read_text(encoding="utf-8"))
        assert len(replay["events"]) > 5
        assert replay["score"] is not None
    index = json.loads((out / "data" / "runs.json")
                       .read_text(encoding="utf-8"))["runs"]
    assert {entry["id"] for entry in index} == set(featured)


def test_static_site_replays_have_real_sources(tmp_path):
    out, _builder = build_to(tmp_path)
    featured = json.loads((out / "data" / "featured.json")
                          .read_text(encoding="utf-8"))["featured"]
    assert len(featured) > 0
    for run_id in featured:
        replay = json.loads((out / "data" / "runs" / f"{run_id}.json")
                            .read_text(encoding="utf-8"))
        prov = replay.get("provenance")
        assert prov is not None, f"{run_id} has no provenance"
        assert prov.get("bench"), f"{run_id} has no source bench"
        assert prov.get("episode"), f"{run_id} has no episode id"
        assert prov.get("seed") is not None, f"{run_id} has no seed"


def test_static_site_has_no_unlabeled_synthetic(tmp_path):
    out, _builder = build_to(tmp_path)
    hits = []
    for path in text_files(out):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "synthetic" in text.lower():
            hits.append(str(path.relative_to(out)))
    assert hits == []
    bundle = (out / "app.bundle.js").read_text(encoding="utf-8")
    assert "provenanceLine" in bundle
    assert "updateProvBadge" in bundle
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="provBadge"' in html


def test_static_site_debriefs(tmp_path):
    out, _builder = build_to(tmp_path)
    featured = json.loads((out / "data" / "featured.json")
                          .read_text(encoding="utf-8"))["featured"]
    assert len(featured) > 0
    for run_id in featured:
        payload = json.loads((out / "data" / "debriefs" / f"{run_id}.json")
                             .read_text(encoding="utf-8"))
        assert payload["id"] == run_id
        report = payload["debrief"]
        assert report["layer"] == 1
        assert report["model_used"] == "none"
        assert report["outcome_text"] != ""
        assert report["markdown"] != ""
        assert len(report["timeline"]) > 0
        replay = json.loads((out / "data" / "runs" / f"{run_id}.json")
                            .read_text(encoding="utf-8"))
        missing = debrief_mod.validate_citations(report, replay["events"])
        assert missing == []


def test_static_site_embeds_debriefs(tmp_path):
    out, _builder = build_to(tmp_path)
    text = (out / "data.js").read_text(encoding="utf-8")
    blob = text.split("=", 1)[1].strip().rstrip(";\n")
    embedded = json.loads(blob)
    featured = json.loads((out / "data" / "featured.json")
                          .read_text(encoding="utf-8"))["featured"]
    assert set(embedded["debriefs"]) == set(featured)
    for run_id in featured:
        on_disk = json.loads((out / "data" / "debriefs" / f"{run_id}.json")
                             .read_text(encoding="utf-8"))
        assert embedded["debriefs"][run_id] == on_disk
    bundle = (out / "app.bundle.js").read_text(encoding="utf-8")
    assert "function fetchDebrief(runId)" in bundle
    assert "embedded().debriefs" in bundle


def test_static_site_learning_data(tmp_path):
    out, _builder = build_to(tmp_path)
    stage3 = json.loads((out / "data" / "stage3.json")
                        .read_text(encoding="utf-8"))
    assert stage3["sensorsOn"]["on"]["n"] > 0
    assert stage3["sensorsOff"]["off"]["n"] > 0
    assert len(stage3["playbook"]["rules"]) > 0
    assert stage3["voidNote"] != ""
    playbook = json.loads((out / "data" / "playbook.json")
                          .read_text(encoding="utf-8"))
    assert len(playbook["rules"]) > 0
    results = json.loads((out / "data" / "results.json")
                         .read_text(encoding="utf-8"))
    assert results["stage"] == 3


def test_static_site_disables_live_controls(tmp_path):
    out, _builder = build_to(tmp_path)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'id="runBtn" class="btn primary" disabled' in html
    assert 'id="liveBtn" class="btn primary hero-cta" disabled' in html
    assert "run locally to try this" in html
    bundle = (out / "app.bundle.js").read_text(encoding="utf-8")
    assert "run locally to try this" in bundle
    assert "window.CRUCIBLE_STATIC" in bundle
