"""Course-owned, configuration-driven trial runner for PA4 DBpedia models."""
import csv
import importlib
import json
import os
import statistics
import sys
import time
import traceback
from pathlib import Path

import yaml


DEFAULT_CONFIG = {
  "model": {"embedding_dim": 128, "num_classes": 14, "dropout": 0.2,
            "optimizer_name": "adamw", "learning_rate": 0.001,
            "weight_decay": 0.0001, "pad_id": 0},
  "batch_size": 128,
  "max_length": 128,
  "vocab_size": 30_000,
}


def _write_json(path: Path, payload: dict) -> None:
  temporary = path.with_suffix(f"{path.suffix}.tmp")
  temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
  temporary.replace(path)


def _write_metrics(path: Path, histories: dict[int, dict[str, list[float]]]) -> None:
  fields = ["seed", "epoch", "train_loss", "train_accuracy", "validation_loss",
            "validation_accuracy", "epoch_seconds"]
  temporary = path.with_suffix(f"{path.suffix}.tmp")
  with temporary.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for seed, history in histories.items():
      for epoch in range(len(history["train_loss"])):
        writer.writerow({"seed": seed, "epoch": epoch + 1,
                         **{field: history[field][epoch] for field in fields[2:]}})
  temporary.replace(path)


def _writable_cache_overlay(source_cache: Path, overlay_cache: Path) -> Path:
  """Keep datasets' advisory locks writable while mounting data read-only."""
  overlay_cache.mkdir(parents=True, exist_ok=True)
  for entry in source_cache.iterdir():
    if entry.name.endswith(".lock"):
      continue
    target = overlay_cache / entry.name
    if not target.exists() and not target.is_symlink():
      target.symlink_to(entry)
  return overlay_cache


def _read_config(path: Path, max_batch_size: int, fallback_batch_size: int) -> dict:
  """Read the student config, falling back to the documented PA4 baseline."""
  config = {"model": dict(DEFAULT_CONFIG["model"]),
            **{key: value for key, value in DEFAULT_CONFIG.items() if key != "model"}}
  if path.exists():
    supplied = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(supplied, dict):
      raise ValueError("benchmark-config.yaml must contain a mapping")
    unknown = set(supplied) - set(DEFAULT_CONFIG)
    if unknown:
      raise ValueError(f"unknown benchmark configuration keys: {sorted(unknown)}")
    supplied_model = supplied.pop("model", {})
    if not isinstance(supplied_model, dict):
      raise ValueError("benchmark-config.yaml model must contain a mapping")
    unknown_model = set(supplied_model) - set(DEFAULT_CONFIG["model"])
    if unknown_model:
      raise ValueError(f"unknown model configuration keys: {sorted(unknown_model)}")
    config["model"].update(supplied_model)
    config.update(supplied)
  else:
    config["batch_size"] = fallback_batch_size
  for key in ("batch_size", "max_length", "vocab_size"):
    value = config[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
      raise ValueError(f"{key} must be a positive integer")
  if config["batch_size"] > max_batch_size:
    raise ValueError(f"batch_size cannot exceed {max_batch_size}")
  return config


def _run_training_loop(student_code, model, train_loader, validation_loader,
                       n_epochs: int, on_epoch_end) -> dict[str, list[float]]:
  import torch

  device = "cuda"
  model.to(device)
  optimizer = model.configure_optimizers()
  scheduler = model.configure_scheduler(optimizer)
  history = {"train_loss": [], "train_accuracy": [], "validation_loss": [],
             "validation_accuracy": [], "epoch_seconds": []}
  for _ in range(n_epochs):
    started = time.perf_counter()
    model.train()
    loss_total = 0.0
    correct_total = 0
    example_total = 0
    for token_ids, labels in train_loader:
      optimizer.zero_grad()
      loss, correct, total = model.training_step((token_ids.to(device), labels.to(device)))
      loss.backward()
      optimizer.step()
      loss_total += loss.item() * total
      correct_total += correct
      example_total += total
    if not example_total:
      raise ValueError("cannot train on an empty data loader")
    validation = student_code.evaluate(model, validation_loader, device)
    history["train_loss"].append(loss_total / example_total)
    history["train_accuracy"].append(correct_total / example_total)
    history["validation_loss"].append(validation["loss"])
    history["validation_accuracy"].append(validation["accuracy"])
    history["epoch_seconds"].append(time.perf_counter() - started)
    if scheduler is not None:
      scheduler.step()
    on_epoch_end(history)
  return history


def main() -> int:
  output_dir = Path(os.environ.get("AUTOGRADER_TRIAL_OUTPUT", "/tmp/trial-output"))
  output_dir.mkdir(parents=True, exist_ok=True)
  epochs = int(os.environ["AUTOGRADER_TRIAL_EPOCHS"])
  fallback_batch_size = int(os.environ["AUTOGRADER_TRIAL_BATCH_SIZE"])
  max_batch_size = int(os.environ["AUTOGRADER_TRIAL_MAX_BATCH_SIZE"])
  forced_batch_size = os.environ.get("AUTOGRADER_TRIAL_FORCED_BATCH_SIZE", "").strip()
  loader_num_workers = int(os.environ["AUTOGRADER_TRIAL_LOADER_NUM_WORKERS"])
  data_seed = int(os.environ["AUTOGRADER_TRIAL_SEED"])
  model_seeds = json.loads(os.environ.get("AUTOGRADER_TRIAL_MODEL_SEEDS", "[0]"))
  if (not isinstance(model_seeds, list) or not model_seeds
      or any(not isinstance(seed, int) or isinstance(seed, bool) or seed < 0
             for seed in model_seeds)):
    raise ValueError("AUTOGRADER_TRIAL_MODEL_SEEDS must be a non-empty list of non-negative integers")
  source_cache = Path(os.environ["AUTOGRADER_DATASET_CACHE"])
  cache_dir = _writable_cache_overlay(source_cache, Path("/tmp/huggingface-cache"))
  started = time.perf_counter()
  result = {"epochs_requested": epochs, "data_seed": data_seed,
            "model_seeds": model_seeds,
            "loader_num_workers": loader_num_workers,
            "dataset_cache_source": str(source_cache), "dataset_cache": str(cache_dir),
            "status": "failed", "completed_epochs": 0}
  _write_json(output_dir / "run.json", result)
  try:
    sys.path.insert(0, os.getcwd())
    student_code = importlib.import_module("student_code")
    import torch
    if not torch.cuda.is_available():
      raise RuntimeError("CUDA is unavailable inside the trial container")
    config = _read_config(Path("benchmark-config.yaml"), max_batch_size,
                          fallback_batch_size)
    if forced_batch_size:
      config["batch_size"] = int(forced_batch_size)
    if not 1 <= config["batch_size"] <= max_batch_size:
      raise ValueError(f"effective batch size must be between 1 and {max_batch_size}")
    histories = {}
    seed_results = []
    for model_seed in model_seeds:
      # Rebuild with a fixed data seed, so every repeat has the same
      # train/development partition and initial shuffle sequence.  Only model
      # initialization and stochastic model operations vary by model_seed.
      train_loader, validation_loader, vocabulary = student_code.make_dbpedia14_loaders(
        batch_size=config["batch_size"], max_length=config["max_length"],
        vocab_size=config["vocab_size"], seed=data_seed, num_workers=loader_num_workers,
        cache_dir=str(cache_dir))
      if hasattr(student_code, "set_seed"):
        student_code.set_seed(model_seed)
      model = student_code.TextClassifier(vocab_size=len(vocabulary), **config["model"])

      def on_epoch_end(history, active_seed=model_seed):
        histories[active_seed] = history
        _write_metrics(output_dir / "metrics.csv", histories)
        result["completed_epochs"] = sum(len(item["train_loss"])
                                         for item in histories.values())
        result["last_epoch_by_seed"] = {
          str(seed): {key: values[key][-1] for key in values}
          for seed, values in histories.items()
        }
        _write_json(output_dir / "run.json", result)

      history = _run_training_loop(student_code, model, train_loader,
                                   validation_loader, epochs, on_epoch_end)
      seed_results.append({
        "seed": model_seed,
        "final_validation_loss": history["validation_loss"][-1],
        "final_validation_accuracy": history["validation_accuracy"][-1],
        "elapsed_seconds": sum(history["epoch_seconds"]),
      })
    accuracies = [item["final_validation_accuracy"] for item in seed_results]
    losses = [item["final_validation_loss"] for item in seed_results]
    result.update({
      "status": "completed", "completed_epochs": epochs * len(model_seeds),
      "config": config, "seed_results": seed_results,
      "parameter_count": sum(p.numel() for p in model.parameters() if p.requires_grad),
      "device": torch.cuda.get_device_name(0),
      "mean_final_validation_loss": statistics.mean(losses),
      "std_final_validation_loss": statistics.pstdev(losses),
      "mean_final_validation_accuracy": statistics.mean(accuracies),
      "std_final_validation_accuracy": statistics.pstdev(accuracies),
    })
  except Exception as exc:
    result.update({"error_type": type(exc).__name__, "error": str(exc),
                   "traceback": traceback.format_exc()})
  result["elapsed_seconds"] = time.perf_counter() - started
  _write_json(output_dir / "run.json", result)
  if result["status"] == "completed":
    print(f"DBpedia 14 trial completed across {len(model_seeds)} seeds.")
    return 0
  print(f"DBpedia 14 trial failed: {result['error_type']}: {result['error']}", file=sys.stderr)
  return 1


if __name__ == "__main__":
  raise SystemExit(main())
