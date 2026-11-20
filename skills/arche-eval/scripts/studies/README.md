# Paired synthetic skill experiment

The manifest fixes synthetic tasks, model, read-only sandbox, per-trial wall-clock timeout and two paired repetitions. Inspect the plan without executing models:

```sh
python3 evals/experiments/run.py --dry-run --output /tmp/arche-experiment
```

To run actual Codex CLI trials, choose a new output directory outside the fixtures and omit `--dry-run`. This invokes `codex exec --json` and requires working Codex authentication and access to the specified model. Each arm receives the same prompt and settings; the on arm alone gets a copy of the repository's Arche skill at `../skill`, and the prompt explicitly excludes other skills. Both arms run in fresh temporary copies of the same synthetic fixture. No real repository or golden answer is supplied to the model. The skill may need temporary output space for its report; sandbox restrictions are not bypassed, so any resulting failure must be reported as such.

The output contains the manifest, golden, input SHA-256 hashes, incremental `runs.jsonl`, per-trial JSON event traces and stderr, and `table.md`. Raw traces may contain the model's reasoning and local paths; inspect before sharing. `status` distinguishes execution errors and timeouts from invalid responses and scored responses; missing usage or cost stays null. A failure is never scored as zero quality. The step recall and extra-edge counts use `evals/score_tour.py`. Citation validity only checks that a path and line exist within the fixture, not whether the cited text supports the claim; semantic citation accuracy and skill adherence require independent trace review. If no citations are supplied, validity is unavailable. The configured wall-clock timeout is a time budget, not a token or monetary cap; this CLI does not enforce a separate token budget here. Use `python3 evals/harness_score.py evals/experiments/golden.json OUTPUT/runs.jsonl` to rescore saved answers. When the golden includes labelled function lines, citation accuracy measures the fraction of citations in the labelled function's definition or body. Import sites can be relevant context but fall outside that metric. The original two-case Terra study and its separate trace review are recorded in the development repository.

## One-line walkthrough regression

From the Arche checkout, run the following after installing the skill in Codex. Choose a new output directory. The runner starts two independent sessions on temporary Git copies of the synthetic UI and CLI fixtures. Use `--codex /absolute/path/to/codex` to select the executable. The default model is gpt-6.1-terra.

```sh
python3 evals/walkthrough.py run /tmp/arche-walkthrough
python3 evals/walkthrough.py score /tmp/arche-walkthrough
```

Both commands emit named pass/fail checks and exit nonzero on a failed check. The second uses only the archived captures and versioned fixtures. Checks cover the linked report, fixture file hashes, Git revision/status, source line bounds, CLI example and UI labels/states. The label checks measure presence; confirming the meaning of each transition still requires reading the explanation. Chrome interaction is a separate release smoke test. The recorded prompt is `$arche 레포 분석`. Live runs require existing Codex authentication. Keep local traces out of public releases.

For the repeatable offline browser check, run `node evals/check_reports.cjs /tmp/arche-walkthrough` with playwright-core and Chromium available. It opens both archived HTML files with networking disabled and follows a screen evidence link. Additional report paths are optional. Set `ARCHE_PLAYWRIGHT` to an existing playwright-core installation and `ARCHE_BROWSER_CHANNEL=chrome` to use local Chrome.

## Harder cases and controlled comparisons

The manifest also includes three fresh synthetic fixtures: a conjunction controlling UI output, an absent translator implementation, and config-selected registry dispatch. `harness_score.py` reports exact `(id, expression)` condition matches and explicitly forbidden direct calls when those labels are present. Exact expression matching is deliberately lexical: equivalent paraphrases do not match. A dispatch edge is distinct from a direct `calls` edge. Review citations and traces independently.

Use `--model MODEL` to repeat the same paired arms with another model, or `--prompt-file PATH` to supply a UTF-8 Python format template containing `{question}` (escape literal braces as `{{` and `}}`). Keep all other inputs fixed when comparing runs. Each output archives the effective prompt template, manifest, fixture and skill hashes, runner and scorer hashes, and the resolved Codex executable hash when available. Compare these hashes before attributing differences to the intended change. The manifest contains five cases, so the default plan has twenty trials. The original two case prompts and labels remain unchanged.

The files in this directory are self-contained in an installed `arche-eval` skill. Install `arche` alongside it, then invoke `python3 /path/to/arche-eval/scripts/studies/run.py --cases dispatch --codex /path/to/codex --output /tmp/study`; omit `--cases` to run all cases. The `--codex` default is `codex` on PATH. In the development checkout, `evals/experiments`, `evals/harness_score.py` and `evals/score_tour.py` are compatibility links to this directory.
