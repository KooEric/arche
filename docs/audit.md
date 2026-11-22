# Local audit candidates

The normal `render.py --out RUN` step writes `audit.json` alongside the offline HTML report. It examines hashed Python sources from the scan and stops if a source changed. Findings are review leads, not proof of a defect or runtime behavior. Each gives a path and line; duplicate bodies list both locations. The report also retains its existing static import cycle and repeated-import checks.

The source audit reports private top-level functions and classes without an observed name reference, identical function bodies of at least four statements, and modules with no observed import connection. Public exports are excluded by default. To opt a repository-relative directory into an internal-only assumption, pass `--internal-exports internal/path` to render. In that directory, top-level functions and classes in a literal Python `__all__` with no observed name reference become candidates. Repeat the flag for multiple directories. This is an assumption about consumers, not proof; use it only where external consumers are ruled out. Wildcard imports suppress these candidates. It suppresses tests, fixtures, examples, generated and vendor paths, package entry points, decorated definitions, and identifiers observed in names, attributes, imports or literal strings. An isolated-module candidate is suppressed when the graph has excluded files or the module has an unresolved import. Dynamic loading, framework conventions, external clients, unrecognized strings and incomplete parsing can still hide uses; review source and callers before changing anything. There is no severity or auto-fix.

To compare findings across revisions, use clean snapshots of the same repository. Scan, import and render the base revision to a base run directory first; retain its `audit.json`. Then scan and import the head revision to a different run directory, run `changes.py --out HEAD_RUN --base BASE --head HEAD`, and run `render.py --out HEAD_RUN --base-out BASE_RUN`. The report and head `audit.json` classify candidates as introduced, resolved or unchanged. Identity ignores line movement; renames can appear as a resolution and introduction. This is a comparison of static candidates, not proof that a PR created or fixed a defect. The saved base audit permits checkout changes without rereading the old worktree.

The local regression cases cover a private unused symbol, a duplicate pair, an isolated module, public API, decorator registration, string reference, tests, generated files, entry points, skipped files, line movement, and a real two-commit comparison. These are rule examples, not a measured production precision or high-severity benchmark.

Run `python3 evals/audit_score.py` for a deterministic synthetic fixture with one expected candidate for each rule and false-positive controls. It reports per-rule true positives, false positives, false negatives, precision and recall. The fixture is small and its numbers are not production accuracy or high-severity precision. Supply identical internal-directory flags to both base and head renders; differing assumptions cannot be compared.

## Java and Kotlin candidates

Two rules run on Java and Kotlin files. Both report a name only when it appears once in code and never in a string literal or in a configuration file (`.xml`, `.properties`, `.yml`, `.json`, Gradle scripts).

| Rule | Candidate | Scope of the search |
|---|---|---|
| `unused-private-member` | A `private` method, field, property, function or nested type | The declaring file |
| `unused-nonpublic-type` | A package-private Java type or an `internal` Kotlin type at the top level | Every Java and Kotlin file |

Annotated declarations, serialization hooks such as `serialVersionUID` and `readObject`, and paths under `test`, `androidTest`, `generated`, `fixtures`, `examples` or `samples` are left out. Overloaded names are never reported, because each declaration counts as a mention of the other. Reflection and generated code can still use a reported name; read the callers before removing it.

On five published source archives (OkHttp 4.12, Gson 2.10.1, Commons Lang 3.14, Guava 33.0 and kotlinx-coroutines-core 1.7.3; 1,239 files) the two rules reported two candidates, both in Guava. Each name appears once in that archive, so both are unused there. This shows the rules stay quiet on maintained code; it does not measure how much dead code they miss.

`python3 evals/audit_recall.py SOURCE_DIR` copies a source tree, plants unused declarations with unique names in every eligible file and runs the audit on the copy. On the same five archives it planted 2,697 declarations and the rules reported 2,694 (99.9%); the three misses are in one Guava file. The planted declarations are plain one-line forms, so this measures whether the rules recognise a declaration amid real code. It does not cover dead code that hides behind an annotation, an overload or a name reused in a string, which the rules leave out on purpose.

## TypeScript, JavaScript, Dart and Go candidates

`unused-private-symbol` also covers declarations that are not exported in these languages. A name is reported when it appears once in the code that can reach it and in no string literal: the file for TypeScript and JavaScript, the library file for Dart (only names starting with `_`, and never in a file that has `part` directives), and the package directory for Go, test files included. Paths under test, fixture, example and generated folders, `.d.ts` files, declarations under a decorator or annotation, and the names `main`, `init` and `default` are left out. On a Flutter and Cloudflare Worker repository with 1,041 parsed files the rule reported one candidate, a function whose name appears once in its file.
