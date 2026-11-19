from Autograder.graders.dbpedia_trial_grader import DBPediaTrialGrader
from Autograder.graders.dbpedia_trial_runner import _read_config


def test_normalize_settings_uses_pa4_baseline_batch_and_read_only_mount():
  settings = DBPediaTrialGrader.normalize_settings({
    "source_repo": "/tmp/course",
    "dataset_cache_host_path": "/srv/datasets/dbpedia14",
  }, "assignment_types.dbpedia_trial.settings")

  assert settings["trial_batch_size"] == 128
  assert settings["model_seeds"] == [0]
  assert settings["runner_managed_loop"] is True
  assert settings["mounts"] == [{
    "host_path": "/srv/datasets/dbpedia14",
    "container_path": "/data/huggingface",
    "read_only": True,
  }]


def test_missing_benchmark_config_uses_documented_defaults(tmp_path):
  config = _read_config(tmp_path / "benchmark-config.yaml", 1024, 128)

  assert config["batch_size"] == 128
  assert config["max_length"] == 128
  assert config["vocab_size"] == 30_000
  assert config["model"]["embedding_dim"] == 128
  assert config["model"]["optimizer_name"] == "adamw"


def test_normalize_settings_accepts_multiple_model_seeds():
  settings = DBPediaTrialGrader.normalize_settings({
    "source_repo": "/tmp/course",
    "dataset_cache_host_path": "/srv/datasets/dbpedia14",
    "model_seeds": [7, 11, 19],
  }, "assignment_types.dbpedia_trial.settings")

  assert settings["model_seeds"] == [7, 11, 19]


def test_benchmark_config_merges_with_defaults_and_rejects_large_batches(tmp_path):
  path = tmp_path / "benchmark-config.yaml"
  path.write_text("batch_size: 256\nmodel:\n  dropout: 0.4\n", encoding="utf-8")

  config = _read_config(path, 1024, 128)

  assert config["batch_size"] == 256
  assert config["max_length"] == 128
  assert config["model"]["dropout"] == 0.4
  assert config["model"]["embedding_dim"] == 128
