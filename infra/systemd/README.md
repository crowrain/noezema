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

Enable `noezema-web.service` and `noezema-unit-state.timer`. Do not enable
`noezema-runtime.target` directly: a successful admission or recovery action
starts it. The web service is deliberately outside the cognitive target and
continues in read-only degraded mode while that target is inactive.

`noezema-runtime.target` and every writer member share the admission unit and
maintenance marker condition. Each writer process also repeats admission in
`ExecStartPre`, so an automatic restart cannot bypass a newly active host
operation. The observer never opens the systemd bus; it
reads the bounded `/run/noezema/unit-state.json` snapshot published by the
privileged timer.

Host-transition recovery and offline rules maintenance build on this graph in
the next implementation slice. Until their journals are reconciled, either
active head makes admission fail closed.
