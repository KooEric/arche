# Choosing the parts of a map

A part is something a newcomer would name when asked "what is this system made of": the mobile app, the API, the job runner, the database. Read `brief.txt`, the README, the manifests and the entry points before you write `map.json`.

- Aim for 3 to 9 parts at the top level and at most 3 levels. Split a part with `parent` where its pieces do different jobs. Size alone is no reason: a part with many files is shown folder by folder.
- Follow how the code runs and deploys, then how the folders are laid out. Two folders that ship as one service are one part.
- Give every part a `label` in the reader's words and a one-sentence `summary` of what it does today. With `"audience": "everyone"`, use everyday words: "login" rather than "auth middleware".
- `paths` are folders or globs. A file goes to the deepest part that claims it. Run `map.py` and fix the list until fewer than 10% of files are unassigned.
- Things outside the repository (a database, a login provider, a framework) are parts with `"kind": "external"`. List `packages` or `hosts` so the scan connects them. Add an `edges` entry with `evidence` for a connection the scan cannot see, such as a SQL query.
- An `edges` entry is a claim you make. `map.py` checks that the cited lines exist, and the viewer draws the link as cited. Cite the line that shows the connection.
- A `flows` entry walks one user task across parts. Each step names a part, says what happens in one sentence, and cites the line where it happens.

- A `screens` entry lists what a user sees: a `title`, an optional `part`, and `items` with a `label`, a `behavior` sentence and `evidence`.

`kind` is one of `ui`, `service`, `worker`, `library`, `data`, `test`, `script`, `config`, `external`.

```json
{"version": 1,
 "project": {"name": "shop", "summary": "Customers browse a catalog and place orders."},
 "audience": "everyone",
 "parts": [
  {"id": "app", "label": "Mobile app", "kind": "ui", "summary": "Screens for browsing and ordering.", "paths": ["mobile/lib"],
   "evidence": {"path": "mobile/lib/main.dart", "start_line": 1, "end_line": 30}},
  {"id": "api", "label": "API", "kind": "worker", "summary": "Answers the app's requests.", "paths": ["worker/src"]},
  {"id": "db", "label": "Database", "kind": "external", "summary": "Stores orders."}],
 "edges": [{"from": "api", "to": "db", "kind": "data", "label": "reads and writes",
            "evidence": {"path": "worker/src/db.ts", "start_line": 12, "end_line": 20}}],
 "flows": [{"id": "order", "title": "Place an order", "steps": [
  {"part": "app", "note": "The checkout screen sends the order.", "evidence": {"path": "mobile/lib/api.dart", "start_line": 40, "end_line": 44}},
  {"part": "api", "note": "The API stores it.", "evidence": {"path": "worker/src/orders.ts", "start_line": 8, "end_line": 19}}]}]}
```
