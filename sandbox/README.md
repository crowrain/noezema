# NOEZEMA sandbox image

The runtime accepts only a registry reference pinned with `@sha256:` or a local
image ID in `sha256:<digest>` form. Tags alone are rejected by the domain
contract and the runtime always uses `--pull=never`.

Build with an already pinned Python base image:

```sh
podman build \
  --build-arg BASE_IMAGE=registry.example/python@sha256:<base-digest> \
  --tag localhost/noezema-sandbox:dev \
  sandbox
```

Read the resulting local image ID and place that exact `sha256:<digest>` value
in `SandboxProfile.image`. A deployment pipeline may instead push the image and
use its immutable registry digest.

The Containerfile is intentionally minimal. Isolation is enforced by the host
runner on every action: rootless runtime, read-only root filesystem, no network,
all capabilities dropped, no-new-privileges, numeric non-root user, resource
limits, bounded output, and one fresh `--rm` container per action. The
configured workspace is mounted read-only; transient writes are confined to
the size-limited `/tmp`. Persistent writes must later pass through a separate
typed and transactional workspace capability.
