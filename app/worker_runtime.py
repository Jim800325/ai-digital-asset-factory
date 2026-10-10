from redis import Redis
from rq import Queue, Worker

from app.config import settings

QUEUE_NAME = "asset-factory"


def main() -> None:
    connection = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    connection.ping()
    queue = Queue(QUEUE_NAME, connection=connection)
    worker = Worker([queue], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
