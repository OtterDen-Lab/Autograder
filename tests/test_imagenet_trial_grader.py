from Autograder.graders.imagenet_trial_grader import ImageNetTrialGrader


def test_normalize_settings_adds_read_only_dataset_mount():
  settings = ImageNetTrialGrader.normalize_settings({
    "source_repo": "/tmp/course",
    "dataset_cache_host_path": "/srv/datasets/imagenet100",
  }, "assignment_types.imagenet_trial.settings")

  assert settings["gpu_count"] == 1
  assert settings["command_timeout_seconds"] == 7200
  assert settings["network_disabled"] is True
  assert settings["mounts"] == [{
    "host_path": "/srv/datasets/imagenet100",
    "container_path": "/data/huggingface",
    "read_only": True,
  }]


def test_normalize_settings_rejects_invalid_student_batch_bounds():
  try:
    ImageNetTrialGrader.normalize_settings({
      "source_repo": "/tmp/course",
      "dataset_cache_host_path": "/srv/datasets/imagenet100",
      "trial_batch_size": 129,
      "max_batch_size": 128,
    }, "assignment_types.imagenet_trial.settings")
  except ValueError as exc:
    assert "cannot exceed" in str(exc)
  else:
    raise AssertionError("expected invalid batch bounds to fail")
