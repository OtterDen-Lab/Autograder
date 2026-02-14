"""
Tests for Docker utilities (Autograder/docker_utils.py).

Covers:
- DockerClient initialization and connection
- DockerContainer lifecycle (start/stop)
- DockerContainer file operations
- DockerContainer command execution
- DockerContainerManager multi-container handling
- Cleanup and error handling

Note: These tests mock the Docker API to avoid requiring a running Docker daemon.
"""

import io
import json
import pytest
import tarfile
from unittest.mock import MagicMock, patch, PropertyMock

from Autograder import docker_utils
from Autograder.docker_utils import (
    DockerClient,
    DockerContainer,
    DockerContainerManager,
)
from Autograder.exceptions import (
    ContainerError,
    DockerError,
    ImageBuildError,
)


@pytest.fixture
def mock_docker_module():
    """Create a mock docker module for testing."""
    mock_docker = MagicMock()

    # Set up basic structure
    mock_docker.from_env.return_value = MagicMock()
    mock_docker.from_env.return_value.ping.return_value = True

    # Set up error classes
    mock_docker.errors.DockerException = Exception
    mock_docker.errors.APIError = Exception
    mock_docker.errors.BuildError = Exception
    mock_docker.errors.ContainerError = Exception
    mock_docker.errors.ImageNotFound = Exception
    mock_docker.errors.NotFound = Exception

    return mock_docker


@pytest.fixture
def mock_docker_client(mock_docker_module, monkeypatch):
    """Create a DockerClient with mocked Docker module."""
    # Clear any cached docker module
    monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

    client = DockerClient()
    return client


class TestDockerClientInit:
    """Tests for DockerClient initialization."""

    def test_init_connects_to_docker(self, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        client = DockerClient()

        mock_docker_module.from_env.assert_called_once()
        mock_docker_module.from_env.return_value.ping.assert_called_once()
        assert client.client is not None

    def test_init_raises_on_connection_failure(self, mock_docker_module, monkeypatch):
        mock_docker_module.from_env.side_effect = mock_docker_module.errors.DockerException(
            "Docker not running"
        )
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        with pytest.raises(DockerError, match="Docker daemon not available"):
            DockerClient()

    def test_init_raises_on_api_error(self, mock_docker_module, monkeypatch):
        # Note: In the actual code, APIError is caught by DockerException handler first
        # because mock errors all inherit from base Exception
        mock_docker_module.from_env.return_value.ping.side_effect = mock_docker_module.errors.APIError(
            "API Error"
        )
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        with pytest.raises(DockerError, match="Docker daemon not available"):
            DockerClient()


class TestDockerClientBuildImage:
    """Tests for DockerClient image building."""

    def test_build_image_from_dockerfile(self, mock_docker_client):
        mock_image = MagicMock()
        mock_image.tags = ["test:latest"]
        mock_docker_client.client.images.build.return_value = (mock_image, [])

        dockerfile = "FROM python:3.12\nRUN echo hello"
        image = mock_docker_client.build_image(dockerfile, "test:latest")

        assert image == mock_image
        mock_docker_client.client.images.build.assert_called_once()
        assert mock_image in DockerClient._images

    def test_build_image_handles_build_error(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)
        mock_docker_client.client.images.build.side_effect = mock_docker_module.errors.BuildError(
            "Build failed", []
        )

        with pytest.raises(ImageBuildError, match="Failed to build image"):
            mock_docker_client.build_image("FROM bad:image", "fail:tag")

    def test_build_image_from_context_handles_api_error(self, mock_docker_client, mock_docker_module, monkeypatch):
        class MockAPIError(Exception):
            pass

        class MockBuildError(Exception):
            pass

        mock_docker_module.errors.APIError = MockAPIError
        mock_docker_module.errors.BuildError = MockBuildError
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)
        mock_docker_client.client.images.build.side_effect = MockAPIError(
            "Build API failed"
        )

        with pytest.raises(DockerError, match="Docker API error building fail:tag"):
            mock_docker_client.build_image_from_context("/tmp/missing-context", "fail:tag")


class TestDockerClientCleanup:
    """Tests for DockerClient cleanup."""

    def test_cleanup_stops_and_removes_containers(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container = MagicMock()
        DockerClient._containers.add(mock_container)

        DockerClient.cleanup()

        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

    def test_cleanup_removes_images(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_image = MagicMock()
        DockerClient._images.add(mock_image)

        DockerClient.cleanup()

        mock_image.remove.assert_called_once()

    def test_cleanup_handles_already_removed_container(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container = MagicMock()
        # Simulate 404 error (container already removed)
        error = mock_docker_module.errors.APIError("not found")
        error.status_code = 404
        mock_container.stop.side_effect = error
        DockerClient._containers.add(mock_container)

        # Should not raise
        DockerClient.cleanup()
        assert mock_container not in DockerClient._containers


class TestDockerContainerLifecycle:
    """Tests for DockerContainer start/stop lifecycle."""

    def test_container_generates_unique_name(self, mock_docker_client):
        container1 = DockerContainer(mock_docker_client, "test:image", "grader")
        container2 = DockerContainer(mock_docker_client, "test:image", "grader")

        assert container1.container_name != container2.container_name
        assert container1.container_name.startswith("grader_")
        assert container2.container_name.startswith("grader_")

    def test_container_start(self, mock_docker_client):
        mock_docker_client.client.containers.run.return_value = MagicMock()

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        mock_docker_client.client.containers.run.assert_called_once()
        assert container.container is not None

    def test_container_start_applies_security_defaults(self, mock_docker_client, tmp_path):
        mock_docker_client.client.containers.run.return_value = MagicMock()
        seccomp_file = tmp_path / "seccomp.json"
        seccomp_file.write_text(
            '{"defaultAction":"SCMP_ACT_ALLOW","syscalls":[]}',
            encoding="utf-8",
        )

        container = DockerContainer(
            mock_docker_client,
            "test:image",
            memory_limit="512m",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            seccomp_profile=str(seccomp_file),
            read_only_root_fs=True,
        )
        container.start()

        kwargs = mock_docker_client.client.containers.run.call_args.kwargs
        assert kwargs["mem_limit"] == "512m"
        assert kwargs["memswap_limit"] == "512m"
        assert kwargs["nano_cpus"] == 1_000_000_000
        assert kwargs["pids_limit"] == 128
        assert kwargs["read_only"] is True
        assert kwargs["tmpfs"]["/tmp"].startswith("rw,noexec,nosuid")
        assert "no-new-privileges:true" in kwargs["security_opt"]
        seccomp_opt = next(
            opt for opt in kwargs["security_opt"] if opt.startswith("seccomp=")
        )
        assert json.loads(seccomp_opt.split("=", 1)[1]) == {
            "defaultAction": "SCMP_ACT_ALLOW",
            "syscalls": [],
        }

    def test_container_start_with_context_manager(self, mock_docker_client):
        mock_docker_client.client.containers.run.return_value = MagicMock()

        with DockerContainer(mock_docker_client, "test:image") as container:
            assert container.container is not None

        # Container should be stopped after exit
        container.container = None  # Already stopped in __exit__

    def test_container_stop(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()
        container.stop()

        mock_container_obj.stop.assert_called_once()
        mock_container_obj.remove.assert_called_once()

    def test_container_context_manager_stops_container_on_inner_exception(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        with pytest.raises(RuntimeError, match="boom"):
            with DockerContainer(mock_docker_client, "test:image"):
                raise RuntimeError("boom")

        mock_container_obj.stop.assert_called_once()
        mock_container_obj.remove.assert_called_once()

    def test_container_start_cleans_up_partial_container_on_start_exception(self, mock_docker_client, mock_docker_module, monkeypatch):
        class MockContainerError(Exception):
            pass

        mock_docker_module.errors.ContainerError = MockContainerError
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        container = DockerContainer(mock_docker_client, "test:image")
        partial_container = MagicMock()

        def fail_run_image(**_kwargs):
            container.container = partial_container
            raise MockContainerError("startup failed")

        container.client.run_image = MagicMock(side_effect=fail_run_image)

        with pytest.raises(ContainerError, match="Failed to start container"):
            container.start()

        partial_container.remove.assert_called_once_with(force=True)

    def test_container_stop_handles_api_error_and_clears_handle(self, mock_docker_client, mock_docker_module, monkeypatch):
        class MockAPIError(Exception):
            pass

        mock_docker_module.errors.APIError = MockAPIError
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        stop_error = MockAPIError("conflict")
        stop_error.status_code = 409

        mock_container_obj = MagicMock()
        mock_container_obj.stop.side_effect = stop_error
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()
        container.stop()

        assert container.container is None
        mock_container_obj.remove.assert_not_called()

    def test_container_handles_image_not_found(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)
        mock_docker_client.client.containers.run.side_effect = mock_docker_module.errors.ImageNotFound(
            "Image not found"
        )

        container = DockerContainer(mock_docker_client, "missing:image")

        with pytest.raises(DockerError, match="Image not found"):
            container.start()


class TestDockerContainerFileOperations:
    """Tests for DockerContainer file copy operations."""

    def test_copy_files_creates_directory(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (0, b"")
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        # Create a test file
        test_file = io.BytesIO(b"test content")
        test_file.name = "test.txt"

        container.copy_files([(test_file, "/app/test.txt")])

        # Verify mkdir was called
        mock_container_obj.exec_run.assert_called()
        # Verify put_archive was called
        mock_container_obj.put_archive.assert_called_once()

    def test_copy_files_handles_mkdir_failure(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (1, b"mkdir failed")
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        test_file = io.BytesIO(b"content")
        test_file.name = "file.txt"

        with pytest.raises(ContainerError, match="Failed to create directory"):
            container.copy_files([(test_file, "/new/path/file.txt")])

    def test_copy_files_without_running_container_raises(self, mock_docker_client):
        container = DockerContainer(mock_docker_client, "test:image")
        # Don't start the container

        test_file = io.BytesIO(b"content")
        test_file.name = "file.txt"

        with pytest.raises(ContainerError, match="no running container"):
            container.copy_files([(test_file, "/path/file.txt")])


class TestDockerContainerCommandExecution:
    """Tests for DockerContainer command execution."""

    def test_execute_command_returns_output(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (0, (b"stdout output", b"stderr output"))
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        rc, stdout, stderr = container.execute_command("echo hello")

        assert rc == 0
        assert stdout == b"stdout output"
        assert stderr == b"stderr output"

    def test_execute_command_with_workdir(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (0, (b"", b""))
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        container.execute_command("ls", workdir="/app")

        # Verify workdir was passed
        call_kwargs = mock_container_obj.exec_run.call_args[1]
        assert call_kwargs.get("workdir") == "/app"

    def test_execute_command_wraps_with_timeout_guard(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (0, (b"", b""))
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        container.execute_command("python main.py")

        call_kwargs = mock_container_obj.exec_run.call_args.kwargs
        assert call_kwargs["cmd"] == 'bash -c "timeout 60 python main.py"'

    def test_execute_command_returns_timeout_exit_code(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (124, (b"", b""))
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        rc, stdout, stderr = container.execute_command("sleep 999")

        assert rc == 124
        assert stdout == b""
        assert stderr == b""

    def test_execute_command_wraps_api_error_as_container_error(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.side_effect = mock_docker_module.errors.APIError(
            "exec failed"
        )
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        with pytest.raises(ContainerError, match="Failed to execute command in container"):
            container.execute_command("echo hello")

    def test_execute_command_handles_none_output(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.exec_run.return_value = (0, (None, None))
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        rc, stdout, stderr = container.execute_command("true")

        assert rc == 0
        assert stdout == b""
        assert stderr == b""

    def test_execute_command_without_running_container_raises(self, mock_docker_client):
        container = DockerContainer(mock_docker_client, "test:image")

        with pytest.raises(ContainerError, match="no running container"):
            container.execute_command("echo hello")


class TestDockerContainerCommit:
    """Tests for DockerContainer commit (snapshot) operations."""

    def test_commit_creates_image(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_image = MagicMock()
        mock_container_obj.commit.return_value = mock_image
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        result = container.commit("my-repo", "v1.0")

        assert result == mock_image
        mock_container_obj.commit.assert_called_once_with(
            repository="my-repo", tag="v1.0"
        )

    def test_commit_without_running_container_raises(self, mock_docker_client):
        container = DockerContainer(mock_docker_client, "test:image")

        with pytest.raises(ContainerError, match="Cannot commit"):
            container.commit("repo", "tag")


class TestDockerContainerManager:
    """Tests for DockerContainerManager multi-container handling."""

    def test_create_container_adds_to_manager(self, mock_docker_client):
        manager = DockerContainerManager(mock_docker_client)

        container = manager.create_container("worker", "test:image")

        assert "worker" in manager.containers
        assert manager.containers["worker"] == container

    def test_create_container_with_start(self, mock_docker_client):
        mock_docker_client.client.containers.run.return_value = MagicMock()
        manager = DockerContainerManager(mock_docker_client)

        container = manager.create_container(
            "worker", "test:image", start_immediately=True
        )

        assert container.container is not None

    def test_get_container_returns_existing(self, mock_docker_client):
        manager = DockerContainerManager(mock_docker_client)
        created = manager.create_container("worker", "test:image")

        retrieved = manager.get_container("worker")

        assert retrieved == created

    def test_get_container_raises_for_missing(self, mock_docker_client):
        manager = DockerContainerManager(mock_docker_client)

        with pytest.raises(Exception, match="not found"):
            manager.get_container("nonexistent")

    def test_stop_all_cleans_up_containers(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        manager = DockerContainerManager(mock_docker_client)
        manager.create_container("worker1", "test:image", start_immediately=True)
        manager.create_container("worker2", "test:image", start_immediately=True)

        manager.stop_all()

        assert len(manager.containers) == 0

    def test_context_manager_cleanup(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        with DockerContainerManager(mock_docker_client) as manager:
            manager.create_container("worker", "test:image", start_immediately=True)
            assert len(manager.containers) == 1

        # After exit, containers should be cleaned up
        assert len(manager.containers) == 0


class TestDockerContainerThreadSafety:
    """Tests for thread-safety of container operations."""

    def test_container_names_unique_across_threads(self, mock_docker_client):
        import threading

        containers = []
        errors = []

        def create_container():
            try:
                container = DockerContainer(mock_docker_client, "test:image")
                containers.append(container.container_name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_container) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(containers) == 10
        # All names should be unique
        assert len(set(containers)) == 10


class TestDockerContainerReadFile:
    """Tests for reading files from containers."""

    def test_read_file_extracts_content(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        # Create a mock tarball with file content
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            content = b"file contents here"
            info = tarfile.TarInfo(name="test.txt")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_buffer.seek(0)

        mock_container_obj = MagicMock()
        mock_container_obj.get_archive.return_value = (
            iter([tar_buffer.getvalue()]),
            {"size": len(tar_buffer.getvalue())}
        )
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        content = container.read_file("/app/test.txt")

        assert content == "file contents here"

    def test_read_file_returns_none_on_error(self, mock_docker_client, mock_docker_module, monkeypatch):
        monkeypatch.setattr(docker_utils, 'docker', mock_docker_module)

        mock_container_obj = MagicMock()
        mock_container_obj.get_archive.side_effect = mock_docker_module.errors.APIError(
            "File not found"
        )
        mock_docker_client.client.containers.run.return_value = mock_container_obj

        container = DockerContainer(mock_docker_client, "test:image")
        container.start()

        content = container.read_file("/nonexistent/file.txt")

        assert content is None
