# Repository structure contract

These instructions apply to the entire repository. Read this file and
`docs/architecture.md` before creating, moving, or renaming files.

## Required layout

- Keep production Python code under `a_stock_tracker/`.
- Keep `pipeline.py` as a thin backward-compatible launcher only. Do not add
  business logic or reusable functions to repository-root Python files.
- Place modules by responsibility:
  - `a_stock_tracker/data/`: database, providers, fetching, and caches;
  - `a_stock_tracker/signals/`: deterministic signal calculations;
  - `a_stock_tracker/reporting/`: notifications and presentation;
  - `a_stock_tracker/qualitative/`: read-only selection of local qualitative scores.
- Put tracked configuration in `config/`. Put runtime output in the ignored
  `data/`, `logs/`, or `artifacts/` directories.
- Never track credentials, private keys, tokens, or service-account JSON.
  Local credential files belong only under the ignored `credentials/` directory.
- Keep executable operational/research entrypoints in `scripts/`, tests in
  `tests/`, and maintained documentation in `docs/`.
- Do not recreate `lib/`, add root-level `qualitative_v2_*.py` modules, or add
  root-level JSON configuration files.

## Dependency and import rules

- Use absolute imports beginning with `a_stock_tracker` in production code.
- Do not use the retired import names `lib`, `config`, `scorer`,
  `gemini_scorer`, `telegram_push`, `sheets_sync`, or `qualitative_v2_*`.
- Production modules must not modify `sys.path`.
- Only launchers may import `a_stock_tracker.cli`; reusable package modules
  must not depend on the application composition root.
- Preserve the dependency direction documented in `docs/architecture.md`.

## Changing the structure

- Do not add a new top-level file or directory merely for convenience.
- A genuinely new architectural area requires explicit user approval. In the
  same change, update `docs/architecture.md`, this file, and
  `tests/test_project_structure.py`.
- Do not weaken or delete structure assertions to make an unrelated task pass.

## Required verification

Run the active product checks after any source or structure change:

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
