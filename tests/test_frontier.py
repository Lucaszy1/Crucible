"""Frontier difficulty tier: longer chains blue sometimes loses to.

Locks pre-frontier seeds to byte-identical specs, then covers the
frontier shape (7-step chain over 4 hosts, decoy hosts, capped
noise, 3-hop jewel), solver solvability in the pure-Python
simulator, and the frontier seed set. No Docker, no model calls.
"""

import hashlib
import json
from pathlib import Path

from crucible import sensors
from crucible.scenarios import (
    chain, generate, schema, seeds, select, simulate,
)
from crucible.solver_red import solver_plan

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "frontier_seed_golden.json"
OLD_SEEDS = list(range(1001, 1041)) + list(range(9001, 9021))
OLD_DIFFICULTIES = ("easy", "medium", "hard")
FRONTIER_SEEDS = list(range(9101, 9111))


def _canonical(spec: dict) -> bytes:
    return json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()


def test_old_seeds_generate_identical_specs():
    golden = json.loads(GOLDEN_PATH.read_text())
    mismatched = []
    for difficulty in OLD_DIFFICULTIES:
        for seed in OLD_SEEDS:
            spec = generate.generate(seed, difficulty)
            digest = hashlib.sha256(_canonical(spec)).hexdigest()
            if digest != golden[difficulty][str(seed)]:
                mismatched.append(f"{difficulty}/{seed}")
    assert mismatched == []


def test_frontier_shape_and_validity():
    assert schema.PATH_LENGTHS["frontier"] == 7
    assert "frontier" in schema.DIFFICULTIES
    for seed in FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        assert spec["difficulty"] == "frontier"
        assert len(spec["hosts"]) == 10
        assert len(spec["path"]) == 7
        assert len(spec["dead_ends"]) == 3
        assert schema.validate_shape(spec) == []
        assert simulate.validate_spec(spec) == []
        assert simulate.validate_simulation(spec) == []
        assert generate.frontier_topology_errors(spec) == []


def test_frontier_chain_runs_three_hops_to_jewel():
    for seed in FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        kinds = [step["kind"] for step in spec["path"]]
        assert kinds[0] == "exposed_page"
        assert kinds.count("signin_known") == 2
        assert kinds[-1] == "jewel_read"
        ordered = list(dict.fromkeys(step["host"] for step in spec["path"]))
        assert len(ordered) == 4
        assert ordered[-1] == spec["jewel"]["host"]


def test_frontier_topology_checker_bites():
    spec = generate.generate(9101, "frontier")
    rigged = json.loads(json.dumps(spec))
    entry, jewel = rigged["entry_host"], rigged["jewel"]["host"]
    for host in rigged["hosts"]:
        if host["name"] == entry and jewel not in host["links"]:
            host["links"].append(jewel)
        if host["name"] == jewel and entry not in host["links"]:
            host["links"].append(entry)
    assert generate.frontier_topology_errors(rigged) != []


def test_frontier_decoy_hosts_look_like_targets():
    for seed in FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        decoys = spec["decoys"].get("decoy_hosts", [])
        assert len(decoys) == 2
        by_name = {host["name"]: host for host in spec["hosts"]}
        path_hosts = {step["host"] for step in spec["path"]}
        for name in decoys:
            assert name not in path_hosts
            assert name != spec["jewel"]["host"]
            assert by_name[name]["role"] in ("database", "file share")
            assert by_name[name]["segment"] == "data"


def test_frontier_noise_stays_below_alerts():
    for seed in FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        noise = spec["noise"]
        assert len(noise["failures"]) + len(noise["requests"]) == 20
        runner = simulate.Range(spec)
        simulate.emit_noise(runner, spec)
        connections = {host["name"]: set(host.get("links", []))
                       for host in spec["hosts"]}
        alerts = sensors.build_alerts(runner.logs, connections=connections,
                                      crown_jewel_host=spec["jewel"]["host"])
        assert alerts == []


def test_frontier_solvable_across_seeds():
    for seed in list(range(60)) + FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        assert simulate.validate_simulation(spec) == [], seed
        steps = chain.chain_steps(spec)
        ok, runner, step_lines = simulate.run_plan(
            spec, steps, known_only=True)
        assert ok, seed
        table = simulate.coverage(spec, runner, step_lines)
        assert all(table[index] for index in range(len(steps))), seed
        assert simulate.shortcut_errors(spec) == [], seed


def test_solver_plan_reaches_jewel_on_frontier():
    for seed in FRONTIER_SEEDS:
        spec = generate.generate(seed, "frontier")
        plan = solver_plan(spec, "verify")
        first_cred = next(index for index, move in enumerate(plan)
                          if move["tool"] == "try_credential")
        assert first_cred <= 2
        runner = simulate.Range(spec)
        jewel_content = None
        for move in plan:
            result = runner.run_action(move)
            assert result.get("ok"), (seed, move)
            if move["tool"] == "read_file" and \
                    move["args"].get("path") == spec["jewel"]["path"]:
                jewel_content = result.get("content", "")
        assert jewel_content is not None, seed
        assert spec["scenario_id"] in jewel_content, seed


def test_frontier_seed_set():
    assert seeds.FRONTIER_SEEDS == list(range(9101, 9111))
    assert not set(seeds.FRONTIER_SEEDS) & set(seeds.TRAIN_SEEDS)
    assert not set(seeds.FRONTIER_SEEDS) & set(seeds.HELDOUT_SEEDS)
    keys = select.expand_set("frontier", "frontier")
    assert keys == [("gen", seed, "frontier")
                    for seed in seeds.FRONTIER_SEEDS]
    assert select.parse_token("gen:9101:frontier") == ("gen", 9101, "frontier")
