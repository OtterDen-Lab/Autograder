"""GPU-backed trial runner for the CST463 ImageNet-100 assignment."""
import io
import json
import shlex
from pathlib import Path
from typing import Any, Dict

from Autograder.graders.docker_graders import TemplateGrader
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback


@GraderRegistry.register("imagenet-trial-grader")
class ImageNetTrialGrader(TemplateGrader):
  """Run a submitted PA3 model against the instructor-provided dataset cache."""
  COMPATIBLE_KINDS = {"ProgrammingAssignment"}

  @classmethod
  def normalize_settings(cls, settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
    raw = dict(settings)
    trial_keys = {
      "dataset_cache_host_path", "dataset_cache_container_path", "epochs",
      "trial_batch_size", "max_batch_size", "seed", "gpu_count",
      "command_timeout_seconds", "network_disabled", "memory_limit",
      "nano_cpus", "pids_limit",
    }
    trial = {key: raw.pop(key) for key in list(raw) if key in trial_keys}
    normalized = super().normalize_settings(raw, context_label)
    host_path = trial.get("dataset_cache_host_path")
    if not isinstance(host_path, str) or not host_path.startswith("/"):
      raise ValueError(f"Config error: {context_label}.dataset_cache_host_path must be an absolute path")
    container_path = trial.get("dataset_cache_container_path", "/data/huggingface")
    if not isinstance(container_path, str) or not container_path.startswith("/"):
      raise ValueError(f"Config error: {context_label}.dataset_cache_container_path must be an absolute path")
    defaults = {"epochs": 15, "trial_batch_size": 64, "max_batch_size": 128,
                "seed": 0, "gpu_count": 1, "command_timeout_seconds": 7200,
                "network_disabled": True, "memory_limit": "24g", "nano_cpus": None,
                "pids_limit": 1024}
    for key, default in defaults.items():
      value = trial.get(key, default)
      if key == "memory_limit":
        if value is not None and not isinstance(value, str):
          raise ValueError(f"Config error: {context_label}.{key} must be a string or null")
      elif key == "network_disabled":
        if not isinstance(value, bool):
          raise ValueError(f"Config error: {context_label}.{key} must be a boolean")
      elif value is not None and (not isinstance(value, int) or isinstance(value, bool)
                                  or (value < 0 if key == "seed" else value <= 0)):
        requirement = "a non-negative integer" if key == "seed" else "a positive integer"
        raise ValueError(f"Config error: {context_label}.{key} must be {requirement}")
      normalized[key] = value
    if normalized["trial_batch_size"] > normalized["max_batch_size"]:
      raise ValueError(f"Config error: {context_label}.trial_batch_size cannot exceed max_batch_size")
    normalized["mounts"] = [{"host_path": host_path, "container_path": container_path,
                             "read_only": True}]
    normalized["dataset_cache_container_path"] = container_path
    return normalized

  def __init__(self, dataset_cache_container_path="/data/huggingface", epochs=15,
               trial_batch_size=64, max_batch_size=128, seed=0, **kwargs):
    self.dataset_cache_container_path = dataset_cache_container_path
    self.epochs = epochs
    self.trial_batch_size = trial_batch_size
    self.max_batch_size = max_batch_size
    self.seed = seed
    super().__init__(**kwargs)

  def add_files_to_docker(self, files_to_copy=None) -> None:
    runner = io.BytesIO(Path(__file__).with_name("imagenet_trial_runner.py").read_bytes())
    runner.name = "imagenet_trial_runner.py"
    super().add_files_to_docker((files_to_copy or []) + [(runner, "/opt/autograder/imagenet_trial_runner.py")])

  def execute_grading(self, *args, **kwargs):
    command = (
      f"AUTOGRADER_TRIAL_EPOCHS={self.epochs} "
      f"AUTOGRADER_TRIAL_BATCH_SIZE={self.trial_batch_size} "
      f"AUTOGRADER_TRIAL_MAX_BATCH_SIZE={self.max_batch_size} "
      f"AUTOGRADER_TRIAL_SEED={self.seed} "
      f"AUTOGRADER_DATASET_CACHE={shlex.quote(self.dataset_cache_container_path)} "
      "AUTOGRADER_TRIAL_OUTPUT=/tmp/trial-output "
      "if [ -x /repo/.venv/bin/python ]; then "
      "/repo/.venv/bin/python /opt/autograder/imagenet_trial_runner.py; "
      "else python /opt/autograder/imagenet_trial_runner.py; fi")
    return self.execute_command_in_container(
      command=command, workdir=self._container_assignment_root())

  @staticmethod
  def _attachment(name: str, content: str) -> io.BytesIO:
    attachment = io.BytesIO(content.encode("utf-8"))
    attachment.name = name
    return attachment

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results
    run_json = self.read_file_from_container("/tmp/trial-output/run.json")
    metrics_csv = self.read_file_from_container("/tmp/trial-output/metrics.csv")
    attachments = []
    if metrics_csv:
      attachments.append(self._attachment("metrics.csv", metrics_csv))
    if run_json:
      attachments.append(self._attachment("run.json", run_json))
    if rc != 0:
      attachments.append(self._attachment("trial.log", f"STDOUT\n{stdout}\n\nSTDERR\n{stderr}\n"))
    try:
      result = json.loads(run_json or "{}")
    except json.JSONDecodeError:
      result = {}
    if rc == 0 and result.get("status") == "completed":
      comment = ("ImageNet-100 trial completed. "
                 f"Final validation accuracy: {result['final_validation_accuracy']:.4f}; "
                 f"loss: {result['final_validation_loss']:.4f}; "
                 f"parameters: {result['parameter_count']:,}; "
                 f"runtime: {result['elapsed_seconds']:.1f}s. "
                 "See the attached metrics.csv and run.json.")
      return Feedback(percentage_score=100.0, comments=comment, attachments=attachments)
    error = result.get("error", "The trial runner did not produce a usable result.")
    return Feedback(percentage_score=0.0,
                    comments=f"ImageNet-100 trial failed: {error}. See attached run.json and trial.log.",
                    attachments=attachments)
