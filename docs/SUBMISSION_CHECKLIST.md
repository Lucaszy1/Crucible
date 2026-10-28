# Submission checklist

Deadline: Wednesday Nov 4, 9 PM ET (6 PM PT). Plan to submit the
night of Nov 3 so Nov 4 is buffer for form or video fixes.

## Before pushing

- [ ] Decide on `dashboard/results.json` and `docs/STAGE3_RESULTS.md`:
  run `scripts/stage3_report.py` once at the end and commit both, or
  add both to `.gitignore`. Neither exists yet and neither is ignored.
- [ ] README Demo section still has three TBDs (demo video, X post,
  screenshots). Record the video, publish the post, then replace the
  TBD lines with links. Screenshots already exist under
  `dashboard/screenshots/`; README points at `docs/media/`, which does
  not exist, so point the links at the real files.
- [ ] README "What was built" is stale (says 197 commits through
  Oct 27 17:14 PT; history is past 234). Update counts and extend the
  timeline to cover Oct 26 through submission day, and resolve the
  TODO about the organizers' Oct 26 start answer.
- [ ] Link art provenance from the README models table:
  `dashboard/assets/art/PROVENANCE.md` (all dashboard art is
  muse-image-1.0 generated with prompts on file).
- [ ] Fix the stale Baseten URL in the `crucible/bench.py` docstring
  (`api.baseten.co`); every other file uses `inference.baseten.co`.
- [ ] Qualify two shorthand paths in `docs/STAGE2_NOTES.md` as
  `crucible/scenarios/compat.py` and
  `crucible/scenarios/render.py`.
- [ ] Commit or revert the two modified working-tree files
  (`dashboard/js/stage.js`, `tests/test_stage.mjs`) so the tree is
  clean, then run the routine gates once:
  `python -m pytest tests -m "not live" -q` and
  `node --test tests/*.mjs`.

## Publication

- [ ] Push `main` to origin (local is ahead; confirm the remote is
  the public repo).
- [ ] Repo set to public.
- [ ] Demo video link recorded (README + form).
- [ ] X post published and linked (README + form).
- [ ] Team member added (name/handle: ___).
- [ ] Track chosen (track: ___).
- [ ] Open-weights flag: YES, Spark Open blue variant runs on Baseten
  (`meta-models/muse-spark-open-1026`); keep the flag set and the
  Baseten repro command in the README.

## Submission form fields

Fill from the README so the form and repo agree:

- Project name: Crucible
- One-line pitch: ___ (suggested: simulated range where AI red and
  blue teams exercise containment judgment, audited and replayable)
- Repo URL: https://github.com/Lucaszy1/Crucible
- Demo video URL: ___
- X post URL: ___
- Track: ___
- Uses open weights: yes (Muse Spark Open blue on Baseten)
- Team: ___ (solo + added member)
- What was built Oct 26 to Nov 4: mirror the README timeline section.

## Audit notes (Oct 28, read-only)

- Secrets: no API keys, tokens, or key values anywhere in the 234
  commits of history or the tracked tree. `.env` is gitignored and
  was never committed; `.env.example` holds placeholders only.
  Tracked `runs/sample.jsonl` and the frozen playbook carry toy
  credentials only. Author emails in git metadata
  (`lucaszy08@gmail.com`) will be visible on
  a public repo; that is normal, change nothing without a reason.
- History: zero commit messages or bodies mention an AI assistant,
  co-author line, or session link.
- Tracked tree is about 5 MB; largest file is a 667 KB dashboard art
  asset. Caches, runs, venv, and playbooks are ignored. `runs/`
  tracks only `.gitkeep`, `sample.jsonl`, and the frozen playbook.
- License: MIT (`LICENSE`, "Copyright (c) 2026 Crucible
  contributors").
