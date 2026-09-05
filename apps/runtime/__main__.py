"""Signal-aware executable for the autonomous runtime service."""

from __future__ import annotations

import logging
import signal
import socket
import threading
from uuid import uuid4

from apps.runtime import RuntimeConfig, build_runtime


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stopped = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    owner = f"{socket.gethostname()}/{uuid4()}"
    config = RuntimeConfig.from_environment()
    with build_runtime(config, owner=owner) as runtime:
        runtime.supervisor.run_forever(stop_requested=stopped.is_set)


if __name__ == "__main__":
    main()
