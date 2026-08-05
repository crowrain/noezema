"""RQ worker — start background workers for orchestrator jobs."""

import argparse
import logging
import sys

from redis import Redis
from rq import Connection, Worker, Queue

logger = logging.getLogger("noezema.rq.worker")


def parse_args():
    parser = argparse.ArgumentParser(description="Noezema RQ Worker")
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379/0",
        help="Redis connection URL",
    )
    parser.add_argument(
        "--queue",
        default="default",
        help="Queue name to listen on",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    redis = Redis.from_url(args.redis_url)

    # Register Noezema apps in Python path
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

    with Connection(redis):
        queue = Queue(args.queue)
        worker = Worker([queue], connection=redis)
        logger.info("RQ worker started — listening on queue '%s' via %s", args.queue, args.redis_url)
        worker.work(burst=False, log_job_description=True)


if __name__ == "__main__":
    main()
