# Snapshot and evaluation compatibility

| Artifact | Current writer | Reader contract |
|---|---|---|
| inventory.json, graph.json, changes.json | `schema_version: "1.0"` | Accept RC `"0.1"` and current `"1.0"`. Legacy documents without the field follow the original structural checks. Reject other explicit versions. |
| tour and exported tour envelope | integer `version: 1` | Step IDs, source-relative paths, inclusive line ranges and whole-file SHA-256 are required for evidence. Optional screens, focused HTTP candidates, the tour `title` and `summary`, and step `title`, `note` and `range_sha256` are additive. `range_sha256` is the SHA-256 of the cited lines joined by newlines; with it, a step whose file changed elsewhere is re-anchored to the same lines instead of being rejected. A verified tour can be rendered again against the matching checkout. |
| audit.json | integer `schema_version: 1` | Bind captured findings to repository revision, inventory digest and internal-export assumptions before comparing. |
| Repair capture and comparison JSON | integer `schema_version: 1` | Preserve immutable fixture identity, execution outcome and derived checks. Replay a repair against its recorded baseline; a fixture change requires retaining the old skill version. |
| Understanding study | integer manifest version 1, JSONL trials | Keep the golden, manifest, prompt template, input hashes and raw responses with the capture. Legacy step-local conditions and top-level conditions are both scored. |

In graph.json `http_connections`, requests may carry an optional `host` and candidate routes an optional `prefix_unknown`, and the reason values `method_and_path_pattern`, `external_host`, `route_prefix_unknown` and `variable_only_route` were added; older readers can ignore them.

Snapshot identity covers repository name, revision, dirty state and scanned file entries. The 1.0 graph's SHA-256 uses UTF-8 JSON of the file entries sorted by path and key, with two-space indentation and a terminating newline. The checkout root stays out of that digest. RC graphs instead compare the available file-node hashes. Unsupported files have only their recorded metadata; changing an unscanned file is outside the content guarantee. Live source-backed tours also recheck the cited file hashes. Git status is a separate consistency signal.

To migrate an RC run, retain the original directory and recreate the snapshot with the new helpers in a new directory at the same revision. Use the old tour if its hashes still validate. An offline evaluation rescore uses the archived raw answers and the selected scorer version. Record that version when comparing scores across releases. New optional fields can be ignored by older consumers; a breaking change requires a new schema identifier and an explicit migration procedure.
