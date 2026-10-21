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
  path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")


def main() -> int:
  output_dir = Path(os.environ.get("AUTOGRADER_TRIAL_OUTPUT", "/tmp/trial-output"))
  output_dir.mkdir(parents=True, exist_ok=True)
  epochs = int(os.environ["AUTOGRADER_TRIAL_EPOCHS"])
  default_batch_size = int(os.environ["AUTOGRADER_TRIAL_BATCH_SIZE"])
  max_batch_size = int(os.environ["AUTOGRADER_TRIAL_MAX_BATCH_SIZE"])
  seed = int(os.environ["AUTOGRADER_TRIAL_SEED"])
  cache_dir = os.environ["AUTOGRADER_DATASET_CACHE"]
  started = time.perf_counter()
  result = {
    "epochs_requested": epochs,
    "seed": seed,
    "dataset_cache": cache_dir,
    "status": "failed",
  }
  try:
    student_code = importlib.import_module("student_code")
    batch_size = getattr(student_code, "TRIAL_BATCH_SIZE", default_batch_size)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
      raise ValueError("TRIAL_BATCH_SIZE must be an integer")
    if not 1 <= batch_size <= max_batch_size:
      raise ValueError(
        f"TRIAL_BATCH_SIZE must be between 1 and {max_batch_size}; got {batch_size}")
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
      batch_size=batch_size, seed=seed, cache_dir=cache_dir)
    model = student_code.CNNClassifier()
    history = student_code.train_model(
      model, train_loader, validation_loader, n_epochs=epochs, device="cuda")
    required = ["train_loss", "train_accuracy", "validation_loss",
                "validation_accuracy", "epoch_seconds"]
    if not isinstance(history, dict) or any(key not in history for key in required):
      raise ValueError("train_model must return all required per-epoch history fields")
    if any(len(history[key]) != epochs for key in required):
      raise ValueError("train_model history must contain one value per epoch")

    with (output_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
      writer = csv.DictWriter(f, fieldnames=["epoch", *required])
      writer.writeheader()
      for epoch in range(epochs):
        writer.writerow({"epoch": epoch + 1, **{key: history[key][epoch] for key in required}})
    result.update({
      "status": "completed",
      "batch_size": batch_size,
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
