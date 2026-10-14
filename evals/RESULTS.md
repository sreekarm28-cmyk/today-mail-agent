# Classification evaluation results

Dataset: `evals/dataset.json` (49 synthetic emails; 9–10 examples per category, including three prompt-injection examples).

The baseline uses the original classification prompt. The current run changes one thing: the prompt spells out category boundaries more precisely, especially the difference between an actual requested action and a message that merely mentions a deadline. Both use the same model, dataset, batch size, and evaluation code.

| Prompt | Correct | Total | Accuracy |
|---|---:|---:|---:|
| Baseline | Not run yet | 49 | — |
| Current | Not run yet | 49 | — |

Run `py -m evals.run_eval --compare` to run both prompts, print accuracy and misses, and update this table automatically. This makes 34 model calls over the 49-email dataset and may use OpenRouter API credits. The scores remain “Not run yet” until that command is run with a local `OPENROUTER_API_KEY`.
