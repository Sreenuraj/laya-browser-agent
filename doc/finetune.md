# Fine-Tuning the Laya Decision Model — Step-by-Step Guide

> This guide is written so that **you can run every step yourself**. Inference always stays 100% local.
> Training can run locally (Apple Silicon MPS, CUDA or CPU) or, for large full fine-tunes, on a **free Kaggle 2×T4** session.
> The guide is application-agnostic: **never** train on PostQode Nexus traces (see §0).

Sources: upstream `docs/finetune.md` (NandhaKishorM/laya), laya-ai.com fine-tune guide, `cklxx/laya-browser` model card.

> **Status:** this is a Phase 3 guide (see `doc/plan.md`). §2 (setup) and §4B (Kaggle) can be run today. The
> `bench export-training`, `bench compare`, `bench calibrate` and `--tier T0` / `--model` commands below are planned
> and do not exist yet.
> The flags `--loss`, `--shuffle-options`, `--freeze-encoder`, `--target-error`, `--eval` and `--dry-run` come from upstream docs.
> `--device`, `--epochs`, `--micro-batch` and `--grad-accum` are expected names. Confirm them with `laya-train --help`
> and adjust before the first run.

---

## 0. Ground Rules (non-negotiable)

1. **No Nexus data in training.** Nexus is the local test ground only. Trajectories whose origin is
   `localhost:3000` (or any configured `LAYA_EVAL_ORIGINS`) are excluded by the exporter.
   They are **evaluation-only**, exactly like held-out sites.
2. **Training sources:** public datasets (Mind2Web, WebChain CC-BY-4.0, NNetNav), our procedurally generated
   synthetic sites (`bench/sites/synthetic/*`), and real public sites from the T4 *train* split.
3. **Held-out discipline:** sites in a `heldout` split are never trained on, never used to tune prompts, and never used to fit calibration.
4. **Keep only real improvements:** a new checkpoint replaces the default only if it does better on tasks and sites it was not trained on, and does not start claiming success on tasks it failed.

---

## 1. Which model we start from

| Checkpoint | Encoder | Why |
| :--- | :--- | :--- |
| `cklxx/laya-browser` (v19s) | mmBERT-base 322M | Already a browser-agent decision head (operation + target), trained on Mind2Web, NNetNav, WebChain, webgym and DAgger. **Default base.** |
| `convaiinnovations/laya` | ModernBERT-large 421M | English base. Used only if the bake-off (Plan P2-3) shows it beats laya-browser after fine-tuning. |
| `aac6fef/laya-mlx` | (MLX export of `convaiinnovations/laya`) | Current runtime model. Baseline for comparison. |

---

## 2. One-time local setup

```bash
cd laya-browser-agent
# Separate training venv: keeps torch/transformers out of the runtime venv
python3.12 -m venv .venv-train
source .venv-train/bin/activate
pip install --upgrade pip
pip install "laya>=0.3.28" torch transformers datasets huggingface_hub
laya-train --help          # sanity check: the command exists
python -c "import torch; print('MPS available:', torch.backends.mps.is_available())"
```

Expected output: `MPS available: True`.

---

## 3. Produce training data (done by the agent code, Plan P3-1)

Run the benchmark with trajectory logging on **train** sites only:

```bash
./.venv/bin/python -m bench run --tier T2 --split train --repeat 3 --log-trajectories
./.venv/bin/python -m bench export-training \
    --trajectories ~/.laya/trajectories \
    --exclude-origins "$LAYA_EVAL_ORIGINS" \
    --out data/train.jsonl --eval-out data/heldout.jsonl
```

Each JSONL line is one decision case, in exactly the format `laya-train` reads:

```json
{"state": "{\"goal\": \"...\", \"step\": \"...\", \"url\": \"...\", \"page_text\": \"...\", \"history\": [...]}",
 "questions": {
   "operation": {"type": "choice", "instructions": "Which operation advances the current step?",
                 "criteria": {"CLICK": "...", "TYPE": "...", "SELECT": "...", "ENTER": "...", "SCROLL": "...",
                              "TOOL": "...", "NAVIGATE": "...", "READ": "...", "DONE": "..."}},
   "target":    {"type": "choice", "instructions": "Which element should the operation act on?",
                 "criteria": {"A": "[e3] button 'Buy' · row 'Wireless Mouse'", "B": "...", "...": "..."}},
   "step_done": {"type": "noul", "instructions": "Is the current step's success criterion already met on this screen?"},
   "blocker":   {"type": "choice", "instructions": "What, if anything, blocks the step?",
                 "criteria": {"none": "...", "overlay": "...", "login": "...", "captcha": "...", "unavailable": "...", "error": "..."}}
 },
 "expected": {"operation": "CLICK", "target": "A", "step_done": false, "blocker": "none"}}
```

Rules the exporter enforces:
- **Opaque option keys** (`A`, `B`, …) for `target`. Never use `yes`/`no` or `true`/`false` as choice keys.
- Keep options within the base model's head budget (192 tokens for `laya`, 768 for `laya-browser`, so ~10 vs ~60 options of ≤ 50 chars). Larger sets are chunked like `predict_chunked` (interleaved chunks, winners compete in a second pass).
- The *same* instructions and option strings as used at inference time. The exporter imports them from the runtime module, so they cannot drift.
- Labels come only from **verified** outcomes (oracle-passed steps, scripted experts on synthetic sites, or `qwen3:14b` teacher labels with ≥ 5% human spot-check).

Check the data before training:

```bash
source .venv-train/bin/activate
laya-train --data data/train.jsonl --base cklxx/laya-browser --out /tmp/dry --dry-run
```

Read the skip table. If `options_collapsed` or `options_beyond_max_len` > 1%, shorten the option strings and re-export.

---

## 4A. Train locally (fast iterations)

**Head-only** (~minutes to an hour; good for checking the data pipeline):

```bash
laya-train --data data/train.jsonl --eval data/heldout.jsonl \
  --base cklxx/laya-browser --out ~/.laya/models/laya-browser-ft-head-$(date +%Y%m%d) \
  --freeze-encoder --loss soft-ce --shuffle-options --device mps --epochs 3 --target-error 0.05
```

**Full fine-tune** (overnight; close other apps, keep the machine plugged in):

```bash
laya-train --data data/train.jsonl --eval data/heldout.jsonl \
  --base cklxx/laya-browser --out ~/.laya/models/laya-browser-ft-$(date +%Y%m%d) \
  --loss soft-ce --shuffle-options --device mps --epochs 4 --micro-batch 4 --grad-accum 16 \
  --target-error 0.05
```

`--loss soft-ce --shuffle-options` was the best upstream setting for many-option questions (0.941 vs 0.730 base on 20-option MASSIVE).
If MPS runs out of memory, halve `--micro-batch` and double `--grad-accum`.

---

## 4B. Train on free Kaggle 2×T4 (large full fine-tunes)

Only training data leaves the machine. It contains page text from public and synthetic sites only, never credentials (the exporter masks password fields) and never Nexus data.

1. Create a free account at kaggle.com and verify your phone number (needed to enable GPUs).
2. **Datasets → New Dataset**. Upload `data/train.jsonl` and `data/heldout.jsonl`. Name it `laya-agent-decisions`. Keep it Private.
3. **Code → New Notebook**. In the right panel set *Accelerator = GPU T4 ×2*, *Internet = On*. Use *Add Input* to attach your dataset.
4. Paste and run this single cell:

   ```python
   !pip install -q "laya>=0.3.28" transformers datasets huggingface_hub
   !laya-train --data /kaggle/input/laya-agent-decisions/train.jsonl \
       --eval /kaggle/input/laya-agent-decisions/heldout.jsonl \
       --base cklxx/laya-browser --out /kaggle/working/laya-browser-ft \
       --loss soft-ce --shuffle-options --epochs 4 --target-error 0.05
   !cd /kaggle/working && tar czf laya-browser-ft.tgz laya-browser-ft
   ```

   (For 2-GPU DDP speed, use the upstream notebook `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` and replace its two `load_dataset` calls with your JSONL files. Single-GPU `laya-train` is simpler and fine for < 30k decisions.)
5. When it finishes, open the **Output** tab and download `laya-browser-ft.tgz`. Also download `train_report.json` (before/after accuracy, ECE, Brier).
6. On your machine:

   ```bash
   mkdir -p ~/.laya/models && tar xzf ~/Downloads/laya-browser-ft.tgz -C ~/.laya/models/
   ```

Expected time: ~4–5 h for ~30k decisions × 4 epochs. Kaggle gives ~30 GPU-hours per week for free.

---

## 5. Convert to MLX (Metal runtime) and evaluate

```bash
cd laya-browser-agent
./.venv/bin/python -c "from laya_mlx.convert import convert; \
convert('$HOME/.laya/models/laya-browser-ft', '$HOME/.laya/models/laya-browser-ft-mlx', dtype='float16')"

# Offline decision accuracy + calibration on held-out decisions (T0)
./.venv/bin/python -m bench run --tier T0 --model ~/.laya/models/laya-browser-ft-mlx
# End-to-end held-out sites (T2-heldout), 3 runs each
./.venv/bin/python -m bench run --tier T2 --split heldout --repeat 3 --model ~/.laya/models/laya-browser-ft-mlx
```

If the new checkpoint completes more held-out tasks correctly than the current one (and does not report success on
tasks it failed), make it the default:

```bash
export LAYA_MODEL=~/.laya/models/laya-browser-ft-mlx    # or pass --model
```

---

## 6. Calibration refresh (cheap, no retraining)

After any checkpoint, harness or option-format change, refit temperatures and abstention thresholds on the held-out calibration slice:

```bash
./.venv/bin/python -m bench calibrate --model ~/.laya/models/laya-browser-ft-mlx --data data/heldout.jsonl
```

This writes `calibration.json` next to the model (loaded through `Agent.load_calibration`). Never fit calibration on training data.

---

## 7. DAgger loop (repeat 3–5 rounds)

1. Run the current best model on train sites with `--log-trajectories --expert-label`.
   The model drives, and the expert (scripted oracle on synthetic sites, `qwen3:14b` teacher on real train sites) labels every visited state.
2. Export → train (§4) → convert/evaluate (§5) → promote if better.
3. Hard negatives (wrong element chosen with confidence ≥ 0.8) are up-weighted in the next export.

---

## 8. Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `options_collapsed` in dry-run | Option strings are too similar within the 192/256-token head budget. Shorten them and lead with the distinguishing text. |
| MPS OOM | `--micro-batch 2 --grad-accum 32`, close Chromium/Ollama during training. |
| Accuracy up, but agent success flat | The harness is the bottleneck (waits, shortlist recall). Check T0 shortlist recall@10 first. |
| Confidence always ~1.0 | Stale `temperature_by_options` in `rl_agent_config.json`. Rerun §6. |
| Kaggle "GPU not available" | Phone verification missing, or the weekly GPU quota is exhausted. |