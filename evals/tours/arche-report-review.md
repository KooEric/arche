# Arche report source tour: provenance and review

This is the first reference tour, prepared by manually reading the cited source
ranges on 2026-11-19.  It records the data/artifact flow that leads to HTML,
not an inferred Python call graph: each edge is `artifact_transfer` because the
three CLI scripts are separately invoked programs.

## Evidence map

| Tour step | Cited source evidence | Artifact result |
| --- | --- | --- |
| `scan_artifacts` | `scan.py:53` builds `inventory`; `scan.py:59` writes `inventory.json`; `scan.py:60-66` records the `scan` run step. | Creates `inventory.json` and the run record that carries `status_before`. |
| `imports_graph` | `imports.py:46` reads `inventory.json`; `imports.py:48` builds `graph`; `imports.py:51-52` writes `graph.json` and records the import step. | Consumes `inventory.json`; creates `graph.json` and updates the run record. |
| `render_html` | `render.py:50-52` reads `inventory.json`, `graph.json`, and the run file; `render.py:100` renders the report; `render.py:104-107` writes `report.html` and records the render step. | Consumes all upstream artifacts; creates `report.html`. |

Thus `scan_artifacts → imports_graph` carries `inventory.json`,
`scan_artifacts → render_html` carries `inventory.json` plus the scan-created
run state, and `imports_graph → render_html` carries `graph.json` plus the
updated run state.  The JSON edge contract has no artifact-name field, so this
note preserves that detail without adding non-contract fields.

## Negative examples (kept separate from the tour)

- Do **not** add `scan_artifacts → imports_graph` with `calls`: `scan.py` does
  not invoke `imports.py`; the two programs only meet through `inventory.json`.
- Do **not** add `imports_graph → render_html` with `calls`: `imports.py` does
  not invoke `render.py`; the handoff is `graph.json`.
- Do **not** add `render_html → scan_artifacts` with `writes`: `render.py`
  reads the scan artifacts and writes `report.html`; it does not regenerate the
  scan inventory.

## Verification and review

Review was performed against the working-tree source, using one-based,
inclusive line ranges shown in `arche-report.json`.  The `sha256` values are
whole-file SHA-256 digests (not range digests) and were checked with
`shasum -a 256` for each cited path.  The tour was also validated by
`arche_lib.tour.validate_tour` against a generated inventory containing these
three source files; this confirms the schema, edge endpoints/kinds, source
hashes, and line bounds.
