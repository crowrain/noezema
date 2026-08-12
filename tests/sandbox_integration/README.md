# Rootless sandbox integration test

The test is opt-in because it requires a running rootless OCI runtime and a
local, digest-pinned image containing `/bin/sh` and `python3`.

Set:

- `NOEZEMA_TEST_SANDBOX_RUNTIME` to `podman` or `docker`;
- `NOEZEMA_TEST_SANDBOX_IMAGE` to `sha256:<digest>` or a registry reference
  ending in `@sha256:<digest>`.

The test never pulls an image and mounts only pytest's disposable directory.
