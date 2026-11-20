---
name: arche-eval
description: Evaluate an agent's repository explanations or code repairs on bundled synthetic tasks. Compare controlled configurations, replay saved trials without a model, and link quality failures to source and behavioral evidence. Use for FDE 평가, 모델 비교, or 에이전트 작업 검증.
license: MIT
---

# Arche evaluation

Resolve this skill's `scripts/evaluate.py`. Use Python 3.9+ and create new output directories outside the installation. The understanding study also expects the sibling `arche` skill for the on arm. For a bare invocation, list the bundled tasks and ask which comparison the user wants unless their request already identifies one. For a provided run, replay it first. Report task, outcome, failed checks, supporting lines, and observed time and usage in the user's language.

```sh
python3 -B "$scripts/evaluate.py" list
python3 -B "$scripts/evaluate.py" run --case conjunction --model "$model" --output "$new_study"
python3 -B "$scripts/evaluate.py" run --case pickup-undo --model "$model" --prompt-variant task-only --output "$new_repair"
python3 -B "$scripts/evaluate.py" score --run "$saved_run"
python3 -B "$scripts/evaluate.py" report --runs "$saved_study" "$saved_repair" --output "$new_report_dir"
```

A live run invokes the selected Codex CLI with the existing user's model access. Use `--codex` for an explicit executable. Use only bundled synthetic repositories. The understanding tasks use a read-only sandbox and repeat on/off trials. Each repair trial uses a fresh temporary writable copy and records the answer, patch, path changes and execution metadata. For a prompt comparison, repeat both `task-only` and `verify` at least twice on the same task and model. For a model comparison keep the task, prompt variant and budget constant. For a read-only study use `--prompt-file` to substitute a controlled prompt template containing `{question}`.

The CLI can replay understanding and translation repair records with the standard library. Pickup behavior replay also requires Python Playwright and Chromium; use `--browser-executable` or `ARCHE_EVAL_BROWSER_EXECUTABLE` for an existing Chrome binary. Score the immutable fixture and strictly accepted candidate edits. Keep raw model event logs and stderr local; use the derived report for review, and inspect it before sharing. Report infrastructure errors separately from incorrect behavior. Include the trial count when comparing configurations, preserve missing cost as unknown, and avoid generalizing from these synthetic tasks. A standalone score exits nonzero for a failed task; report construction can succeed with failed trials.
