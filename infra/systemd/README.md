# systemd deployment slice

This slice targets systemd 249 or newer. Install the unit files under
`/etc/systemd/system` and the application under `/opt/noezema`. Materialize the
two example environment files without the `.example` suffix, with mode `0640`:

- `noezema-runtime.env` is owned by `root:noezema` and contains the local-model
  fingerprint, backend settings and writer database role;
- `noezema-web.env` is owned by `root:noezema-observer` and contains only the
  web secret, origins and its least-privilege database role.

Do not merge them: the observer must not receive LLM credentials or writer
configuration.

Create a dedicated `noezema-host` group for the read-only host journal shared
by admission, runtime and web processes. Keep it distinct from `noezema` and
`noezema-observer`, so membership does not expose either environment file.
Prepare `/var/lib/noezema` as `root:noezema-host` with mode `2750`; privileged
host units write records as `0640`, while their child directories inherit the
same group.
Install `infra/tmpfiles.d/noezema.conf` as `/usr/lib/tmpfiles.d/noezema.conf`
and run `systemd-tmpfiles --create`; it also pre-creates the shared bounded
host lock as `root:noezema-host 0660` before any unprivileged admission check.

Install `host-recovery.defaults.toml` as
`/usr/lib/noezema/host-recovery.defaults.toml` with owner `root:root` and mode
`0644`. Enable `noezema-web.service`, `noezema-unit-state.timer`,
`noezema-runtime-resume.service` and `noezema-runtime-resume-retry.timer`. Do
not enable `noezema-runtime.target` directly: the initial recovery probe starts
it after durable admission. The periodic retry service never creates a new
transition when the active head is absent. The web service is deliberately
outside the cognitive target and
continues in read-only degraded mode while that target is inactive.

`noezema-runtime.target` and every writer member share the admission unit and
maintenance marker condition. Each writer process also repeats admission in
`ExecStartPre`, so an automatic restart cannot bypass a newly active host
operation. The observer never opens the systemd bus; it
reads the bounded `/run/noezema/unit-state.json` snapshot published by the
privileged timer.

Host-transition recovery is journaled under `/var/lib/noezema`; classified DB
outages exit successfully and wait for the 30-second timer, permanent
inconsistencies stop with exit 78, and only unclassified crashes consume the
bounded systemd restart burst. Admission permits a journaled target start only
from `ready_to_start` (or terminal cleanup from `resolved`). Offline rules
maintenance uses the same host journal and remains a separate privileged unit.
After correcting a permanent invariant, retry it explicitly with
`python -m apps.host_control.ctl resume-runtime --reason '<reason>'`; the
operator decision becomes the next immutable host event before systemd is
asked to resume.
Install a validated override only through
`python -m apps.host_control.ctl install-host-recovery-policy --file <path> --reason '<reason>'`.
The command serializes against runtime transitions and records `prepared`
before replacing the file, then `committed` or `aborted` from the observed
effective hash.
If an interrupted change observes a third or invalid file, replace it inside
the same change stream with
`python -m apps.host_control.ctl resolve-host-policy --file <path> --reason '<reason>'`.
A valid third file may instead be accepted explicitly with `--accept-current`;
it is recorded as `resolved`, never mislabeled `aborted`.
