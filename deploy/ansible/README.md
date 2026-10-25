# Autograder systemd timer

This role installs the newest PyPI release into uv's isolated tool environment
on every Ansible apply, then installs a `systemd` service and timer for
periodic runs.
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
# Defaults to the newest PyPI release. Pin only for a deliberate rollback.
autograder_package: Otter-Autograder
autograder_config_file: /home/grader/.config/otter-autograder/grading.yaml
autograder_env_file: /home/grader/.tokens/autograder.env
autograder_log_dir: /var/log/grading
autograder_max_workers: 2
autograder_timer_on_calendar: '*:0/15'
# Directory containing grading.yaml and every included YAML fragment.
autograder_config_source_dir: "{{ playbook_dir }}/files"
```

When `autograder_config_source_dir` is set, the role recursively copies its
contents into the target configuration directory. This keeps `grading.yaml`
and files such as `cst334.yaml` together, so their relative include paths work.
Leave it empty (the default) when another task manages configuration files.

The default `autograder_package: Otter-Autograder` follows the newest release
whenever the playbook is applied: the role runs `uv tool install --upgrade`.
To hold or roll back a deployment, explicitly set an exact version, for
example `Otter-Autograder==0.17.0`, in inventory or `group_vars`. `uv tool
install` uses an isolated tool environment, so this deployment never runs
application code from a repository checkout.

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
tail -f /var/log/grading/teachingtools.log
```

The systemd journal contains the service's stdout/stderr. Application logs are
also written to `autograder_log_dir` as rotating `teachingtools.log` and
`teachingtools_errors.log` files. The legacy cron-only `last_run.log` is not
created by the systemd service.
