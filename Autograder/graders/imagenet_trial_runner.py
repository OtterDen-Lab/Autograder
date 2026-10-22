"""Course-owned runner for ImageNet model-design trials.

This module deliberately invokes the public PA3 contracts in ``student_code``
instead of importing a student-provided executable.  Its JSON and CSV output
is consumed by :class:`ImageNetTrialGrader`.
"""
import csv
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
  temporary = path.with_suffix(f"{path.suffix}.tmp")
  temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
  temporary.replace(path)


def _write_metrics(path: Path, history: dict[str, list[float]]) -> None:
  fields = ["epoch", "train_loss", "train_accuracy", "validation_loss",
            "validation_accuracy", "epoch_seconds"]
  temporary = path.with_suffix(f"{path.suffix}.tmp")
  with temporary.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for epoch in range(len(history["train_loss"])):
      writer.writerow({"epoch": epoch + 1,
                       **{field: history[field][epoch] for field in fields[1:]}})
  temporary.replace(path)


def _writable_cache_overlay(source_cache: Path, overlay_cache: Path) -> Path:
  """Expose a read-only datasets cache through a writable lock-file overlay."""
  overlay_cache.mkdir(parents=True, exist_ok=True)
  for entry in source_cache.iterdir():
    # datasets creates top-level advisory locks beside its cached dataset
    # directories.  Keep those paths local and writable; data remains mounted
    # read-only through symlinks.
    if entry.name.endswith(".lock"):
      continue
    target = overlay_cache / entry.name
    if not target.exists() and not target.is_symlink():
      target.symlink_to(entry)
  return overlay_cache


def _run_pa3_training_loop(student_code: object, model: object,
                           train_loader: object, validation_loader: object,
                           n_epochs: int, on_epoch_end: object) -> dict[str, list[float]]:
  """Run the supplied PA3 loop while persisting a callback after each epoch."""
  import torch

  device = "cuda"
  model.to(device)
  optimizer = model.configure_optimizers()
  scheduler = model.configure_scheduler(optimizer)
  history = {
    "train_loss": [], "train_accuracy": [], "validation_loss": [],
    "validation_accuracy": [], "epoch_seconds": [],
  }
  for _ in range(n_epochs):
    epoch_start = time.perf_counter()
    model.train()
    loss_total = 0.0
    correct_total = 0
    example_total = 0
    for batch in train_loader:
      optimizer.zero_grad()
      X, labels = batch
      loss, correct, total = model.training_step((X.to(device), labels.to(device)))
      loss.backward()
      optimizer.step()
      loss_total += loss.item() * total
      correct_total += correct
      example_total += total
    if example_total == 0:
      raise ValueError("cannot train on an empty data loader")
    validation_metrics = student_code.evaluate(model, validation_loader, device)
    history["train_loss"].append(loss_total / example_total)
    history["train_accuracy"].append(correct_total / example_total)
    history["validation_loss"].append(validation_metrics["loss"])
    history["validation_accuracy"].append(validation_metrics["accuracy"])
    history["epoch_seconds"].append(time.perf_counter() - epoch_start)
    if scheduler is not None:
      scheduler.step()
    on_epoch_end(history)
  return history


def main() -> int:
  output_dir = Path(os.environ.get("AUTOGRADER_TRIAL_OUTPUT", "/tmp/trial-output"))
  output_dir.mkdir(parents=True, exist_ok=True)
  epochs = int(os.environ["AUTOGRADER_TRIAL_EPOCHS"])
  default_batch_size = int(os.environ["AUTOGRADER_TRIAL_BATCH_SIZE"])
  max_batch_size = int(os.environ["AUTOGRADER_TRIAL_MAX_BATCH_SIZE"])
  forced_batch_size = os.environ.get("AUTOGRADER_TRIAL_FORCED_BATCH_SIZE", "").strip()
  runner_managed_loop = os.environ.get(
    "AUTOGRADER_TRIAL_RUNNER_MANAGED_LOOP", "0") == "1"
  loader_num_workers = int(os.environ["AUTOGRADER_TRIAL_LOADER_NUM_WORKERS"])
  seed = int(os.environ["AUTOGRADER_TRIAL_SEED"])
  source_cache_dir = Path(os.environ["AUTOGRADER_DATASET_CACHE"])
  cache_dir = _writable_cache_overlay(source_cache_dir,
                                      Path("/tmp/huggingface-cache"))
  started = time.perf_counter()
  result = {
    "epochs_requested": epochs,
    "loader_num_workers": loader_num_workers,
    "seed": seed,
    "dataset_cache_source": str(source_cache_dir),
    "dataset_cache": str(cache_dir),
    "status": "failed",
    "completed_epochs": 0,
  }
  _write_json(output_dir / "run.json", result)
  try:
    # Python puts the directory of this course-owned runner (/opt/autograder)
    # first on sys.path.  Add the assignment working directory so this import
    # resolves the student file copied there by the grader.
    sys.path.insert(0, os.getcwd())
    student_code = importlib.import_module("student_code")
    requested_batch_size = getattr(student_code, "TRIAL_BATCH_SIZE", default_batch_size)
    if not isinstance(requested_batch_size, int) or isinstance(requested_batch_size, bool):
      raise ValueError("TRIAL_BATCH_SIZE must be an integer")
    batch_size = int(forced_batch_size) if forced_batch_size else requested_batch_size
    if not 1 <= batch_size <= max_batch_size:
      raise ValueError(f"effective batch size must be between 1 and {max_batch_size}; got {batch_size}")
    if not hasattr(student_code, "CNNClassifier"):
      raise AttributeError("student_code must define CNNClassifier")
    if not hasattr(student_code, "make_imagenet100_loaders"):
      raise AttributeError("student_code must define make_imagenet100_loaders")
    if not hasattr(student_code, "train_model"):
      raise AttributeError("student_code must define train_model")

    import torch
    if not torch.cuda.is_available():
      raise RuntimeError("CUDA is unavailable inside the trial container")
    if hasattr(student_code, "set_seed"):
      student_code.set_seed(seed)
    train_loader, validation_loader = student_code.make_imagenet100_loaders(
      batch_size=batch_size, seed=seed, num_workers=loader_num_workers,
      cache_dir=str(cache_dir))
    model = student_code.CNNClassifier()
    def on_epoch_end(history: dict[str, list[float]]) -> None:
      _write_metrics(output_dir / "metrics.csv", history)
      result["completed_epochs"] = len(history["train_loss"])
      result["last_epoch"] = {
        key: history[key][-1] for key in ("train_loss", "train_accuracy",
                                           "validation_loss", "validation_accuracy",
                                           "epoch_seconds")
      }
      _write_json(output_dir / "run.json", result)

    if runner_managed_loop:
      history = _run_pa3_training_loop(student_code, model, train_loader,
                                       validation_loader, epochs, on_epoch_end)
    else:
      history = student_code.train_model(
        model, train_loader, validation_loader, n_epochs=epochs, device="cuda")
    required = ["train_loss", "train_accuracy", "validation_loss",
                "validation_accuracy", "epoch_seconds"]
    if not isinstance(history, dict) or any(key not in history for key in required):
      raise ValueError("train_model must return all required per-epoch history fields")
    if any(len(history[key]) != epochs for key in required):
      raise ValueError("train_model history must contain one value per epoch")

    _write_metrics(output_dir / "metrics.csv", history)
    result.update({
      "status": "completed",
      "completed_epochs": epochs,
      "batch_size": batch_size,
      "requested_batch_size": requested_batch_size,
      "forced_batch_size": int(forced_batch_size) if forced_batch_size else None,
      "parameter_count": sum(p.numel() for p in model.parameters() if p.requires_grad),
      "device": torch.cuda.get_device_name(0),
      "final_validation_loss": history["validation_loss"][-1],
      "final_validation_accuracy": history["validation_accuracy"][-1],
    })
  except Exception as exc:
    result.update({"error_type": type(exc).__name__, "error": str(exc),
                   "traceback": traceback.format_exc()})
  result["elapsed_seconds"] = time.perf_counter() - started
  _write_json(output_dir / "run.json", result)
  if result["status"] == "completed":
    print("ImageNet trial completed.")
    return 0
  print(f"ImageNet trial failed: {result['error_type']}: {result['error']}", file=sys.stderr)
  return 1


if __name__ == "__main__":
  raise SystemExit(main())
