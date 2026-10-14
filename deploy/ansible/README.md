# Autograder systemd timer

This role installs a pinned release from PyPI into uv's isolated tool
environment, then installs a `systemd` service and timer for periodic runs.
It does not create the course YAML or secrets: manage those in the surrounding
provisioning role and keep secrets in Ansible Vault or another secret store.

Add it to an existing playbook, or run the included example:

```bash
ansible-playbook -i inventory deploy/ansible/autograder.yml
```

The important defaults are a 15-minute timer and two grading workers. Override
them in inventory or `group_vars`:

```yaml
autograder_user: grader
autograder_package: Otter-Autograder==0.15.9
autograder_config_file: /home/grader/.config/otter-autograder/grading.yaml
autograder_env_file: /home/grader/.tokens/autograder.env
autograder_max_workers: 2
autograder_timer_on_calendar: '*:0/15'
# Directory containing grading.yaml and every included YAML fragment.
autograder_config_source_dir: "{{ playbook_dir }}/files"
```

When `autograder_config_source_dir` is set, the role recursively copies its
contents into the target configuration directory. This keeps `grading.yaml`
and files such as `cst334.yaml` together, so their relative include paths work.
Leave it empty (the default) when another task manages configuration files.

Set `autograder_package` to the precise release approved for deployment; a
subsequent play applies that version. `uv tool install` uses an isolated tool
environment, so this deployment never runs application code from a repository
checkout. To follow the newest compatible release instead, use an unpinned
package value, though pinning is recommended for grading infrastructure.

The launcher sources `autograder_env_file` before invoking the grader, so it
is compatible with the existing `export KEY=value` credential file. Use it for
Canvas and model credentials as well as settings such as:

```ini
AUTOGRADER_DOCKER_MEMORY_LIMIT=1g
AUTOGRADER_DOCKER_NANO_CPUS=2000000000
```

The default role adds the service account to the local `docker` group. Docker
group membership is effectively privileged access to the host, so restrict
access to this VM accordingly. Set `autograder_manage_docker_group: false` if
you use rootless Docker or a different authorization arrangement.

Useful host-side checks:

```bash
systemctl list-timers autograder.timer
systemctl status autograder.timer
journalctl -u autograder.service -f
```
