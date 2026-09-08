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

For Podman, the runner also uses `keep-id` to map the fixed non-root container
UID and GID to the rootless service account. This lets the process read private
workspace snapshots owned by that account without weakening host-directory
permissions or making the container process root.

For the production `noezema` account, install
`infra/containers/storage.conf` as
`/var/lib/noezema/.config/containers/storage.conf` with owner `root:root` and
mode `0644`. Create `/var/lib/noezema-containers/storage` as
`noezema:noezema 0700`. The separate graph root is required because the
host-journal parent `/var/lib/noezema` is deliberately protected as
`root:noezema-host 2750`; placing overlay mounts below that group-owned parent
prevents `crun` from making the merged root private inside the rootless user
namespace.
