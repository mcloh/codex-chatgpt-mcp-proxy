import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3

from .config import ServerConfig, WorkerConfig
from .journal import Journal
from .store import Store


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="codex-bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("server")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker")
    sub.add_parser("approvals")
    decide = sub.add_parser("decide")
    decide.add_argument("approval_id")
    decide.add_argument("decision", choices=["accept", "decline", "cancel"])
    sub.add_parser("cleanup")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("task_id")
    reconcile.add_argument("attempt")
    reconcile.add_argument("state", choices=["succeeded", "failed", "cancelled", "timed_out"])
    reconcile.add_argument("--confirm-no-active-turn", action="store_true", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if args.command == "server":
        import uvicorn

        from .server import create_app

        config = ServerConfig()
        config.validate()
        if config.mode == "development" and args.host not in ("127.0.0.1", "::1", "localhost"):
            parser.error("Development server may only bind loopback")
        # Forwarded headers ignored here. TLS/Host routing belongs to authorized proxy.
        uvicorn.run(
            create_app(config),
            host=args.host,
            port=args.port,
            proxy_headers=False,
            access_log=False,
        )
    elif args.command == "worker":
        from .worker import Worker

        async def run():
            worker = Worker(WorkerConfig())
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, worker.stop.set)
            await worker.run()

        asyncio.run(run())
    elif args.command in ("approvals", "decide"):
        journal = Journal(WorkerConfig().state)
        if args.command == "approvals":
            print(json.dumps(journal.approvals(), indent=2))
        else:
            journal.decide(args.approval_id, args.decision)
            print("Decision saved locally")
    elif args.command == "reconcile":
        import httpx

        config = WorkerConfig()
        config.validate()
        with httpx.Client(
            base_url=config.url, headers={"Authorization": "Bearer " + config.token}, timeout=15
        ) as http:
            r = http.post(
                "/api/worker/tasks/" + args.task_id + "/reconcile",
                json={
                    "attempt": args.attempt,
                    "state": args.state,
                    "confirm_no_active_turn": args.confirm_no_active_turn,
                },
            )
            r.raise_for_status()
        print("Reconciliation recorded; no execution replayed")
    elif args.command == "cleanup":
        config = ServerConfig()
        config.validate()
        print(json.dumps(Store(config).cleanup()))
    elif args.command == "backup":
        config = ServerConfig()
        config.validate()
        with (
            sqlite3.connect(config.database) as source,
            sqlite3.connect(args.destination) as destination,
        ):
            source.backup(destination)
        os.chmod(args.destination, 0o600)
        print("Backup completed")


if __name__ == "__main__":
    main()
