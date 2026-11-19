"""GPU-backed trial runner for CST463 PA4's DBpedia text-classification work."""
import io
import json
import shlex
from pathlib import Path
from typing import Any, Dict

from Autograder.graders.imagenet_trial_grader import ImageNetTrialGrader
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback


@GraderRegistry.register("dbpedia-trial-grader")
class DBPediaTrialGrader(ImageNetTrialGrader):
  """Run PA4 submissions against the instructor-provided DBpedia cache."""
  COMPATIBLE_KINDS = {"ProgrammingAssignment"}

  @classmethod
  def normalize_settings(cls, settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
    # PA4's supplied benchmark configuration uses a 128-example batch by
    # default; preserve the ImageNet runner's resource and mount validation.
    raw = dict(settings)
    model_seeds = raw.pop("model_seeds", [0])
    if (not isinstance(model_seeds, list) or not model_seeds
        or any(not isinstance(seed, int) or isinstance(seed, bool) or seed < 0
               for seed in model_seeds)):
      raise ValueError(f"Config error: {context_label}.model_seeds must be a non-empty list of non-negative integers")
    raw.setdefault("trial_batch_size", 128)
    raw.setdefault("runner_managed_loop", True)
    normalized = super().normalize_settings(raw, context_label)
    normalized["model_seeds"] = model_seeds
    return normalized

  def __init__(self, model_seeds=None, **kwargs):
    self.model_seeds = list(model_seeds if model_seeds is not None else [0])
    super().__init__(**kwargs)

  def _execution_files(self, files_to_copy):
    runner = io.BytesIO(Path(__file__).with_name("dbpedia_trial_runner.py").read_bytes())
    runner.name = "dbpedia_trial_runner.py"
    return super()._execution_files(
      files_to_copy + [(runner, "/opt/autograder/dbpedia_trial_runner.py")])

  def _execution_command(self):
    command = (
      f"export AUTOGRADER_TRIAL_EPOCHS={self.epochs} "
      f"AUTOGRADER_TRIAL_BATCH_SIZE={self.trial_batch_size} "
      f"AUTOGRADER_TRIAL_MAX_BATCH_SIZE={self.max_batch_size} "
      f"AUTOGRADER_TRIAL_FORCED_BATCH_SIZE={self.forced_batch_size or ''} "
      f"AUTOGRADER_TRIAL_SEED={self.seed} "
      f"AUTOGRADER_TRIAL_MODEL_SEEDS={shlex.quote(json.dumps(self.model_seeds))} "
      f"AUTOGRADER_TRIAL_LOADER_NUM_WORKERS={self.loader_num_workers} "
      f"AUTOGRADER_DATASET_CACHE={shlex.quote(self.dataset_cache_container_path)} "
      "AUTOGRADER_TRIAL_OUTPUT=/tmp/trial-output "
      "HF_HUB_OFFLINE=1 DATASETS_OFFLINE=1; "
      "if [ -x /repo/.venv/bin/python ]; then "
      "/repo/.venv/bin/python /opt/autograder/dbpedia_trial_runner.py; "
      "else python /opt/autograder/dbpedia_trial_runner.py; fi")
    return command, self._container_assignment_root()

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results
    run_json = execution_results.files.get("/tmp/trial-output/run.json")
    try:
      result = json.loads(run_json or "{}")
    except json.JSONDecodeError:
      result = {}
    completed_epochs = int(result.get("completed_epochs", 0) or 0)
    metrics_csv = execution_results.files.get("/tmp/trial-output/metrics.csv") \
      if result.get("status") == "completed" or completed_epochs else None
    attachments = []
    if metrics_csv:
      attachments.append(self._attachment("metrics.csv", metrics_csv))
    if run_json:
      attachments.append(self._attachment("run.json", run_json))
    if rc != 0:
      attachments.append(self._attachment("trial.log", f"STDOUT\n{stdout}\n\nSTDERR\n{stderr}\n"))
    if rc == 0 and result.get("status") == "completed":
      mean_accuracy = result["mean_final_validation_accuracy"]
      std_accuracy = result["std_final_validation_accuracy"]
      return Feedback(
        percentage_score=100.0,
        comments=("DBpedia 14 trial completed. "
                  f"Final development accuracy: {mean_accuracy:.4f} ± {std_accuracy:.4f} "
                  f"across {len(self.model_seeds)} seeds; "
                  f"parameters: {result['parameter_count']:,}; "
                  f"runtime: {result['elapsed_seconds']:.1f}s. "
                  "See the attached metrics.csv and run.json."),
        attachments=attachments,
      )
    total_epochs = self.epochs * len(self.model_seeds)
    error = (f"Trial timed out after {completed_epochs} of {total_epochs} total seed-epochs. "
             "Partial metrics are attached." if rc == 124 and completed_epochs
             else result.get("error", "The trial runner did not produce a usable result."))
    return Feedback(
      percentage_score=0.0,
      comments=f"DBpedia 14 trial failed: {error}. See attached run.json and trial.log.",
      attachments=attachments,
    )
