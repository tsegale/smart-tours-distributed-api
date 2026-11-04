#!/usr/bin/env python3
"""Does running several services' databases on one Postgres server save memory? Measured, dev only.

Compares, at idle and after the same warm-up workload, the memory of
  separate:      bookings-db, ai-db, notifications-db as they run in compose (one server each)
  consolidated:  one pgvector/pgvector:pg16 server (the image that already has every extension these three need)
                 with three databases, three owners and no cross access (CONNECT revoked from PUBLIC),
                 loaded from pg_dump of the three live databases, then held open with the same number of
                 client connections per database as the live services have
tours-db stays separate in both: it is the only one that needs PostGIS.

Each server gets the same warm-up: one full sequential read of every table in each database, so the shared
buffers they would use under load are touched. Memory is `docker stats` (cgroup usage, page cache included)
sampled 10 times, 3 s apart, after 30 s of settling; the median is reported.

    python benchmarks/measure_postgres_consolidation.py --env-file <extra env file>
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
from memory_footprint import to_mib  # noqa: E402
from run_booking_contention import OUT_DIR  # noqa: E402

DATABASES = {  # compose service -> (database, owner env var prefix)
    "bookings-db": ("bookings", "BOOKINGS_DB"),
    "ai-db": ("ai", "AI_DB"),
    "notifications-db": ("notifications", "NOTIFICATIONS_DB"),
}
CANDIDATE = "pg-consolidated"
IMAGE = "pgvector/pgvector:pg16"


def sh(*args: str, check: bool = True, stdin: bytes | None = None) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, input=stdin)
    if check and result.returncode != 0:
        raise SystemExit(f"failed ({result.returncode}): {' '.join(args)}\n{result.stderr.decode(errors='replace')}")
    return result.stdout.decode(errors="replace").strip()


def env_value(name: str, files: list[Path]) -> str:
    value = ""
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip()
    return value


def container(service: str) -> str:
    return f"{ROOT.name}-{service}-1"


def memory_mib(name: str, samples: int = 10) -> float:
    values = []
    for _ in range(samples):
        usage = sh("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name)
        values.append(to_mib(usage.split("/")[0]))
        time.sleep(3)
    return statistics.median(values)


def live_connections(name: str, user: str, database: str) -> int:
    count = sh("docker", "exec", name, "psql", "-U", user, "-d", database, "-tAc",
               f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{database}'")  # fmt: skip
    return max(int(count or 0) - 1, 0)  # not counting this psql


def warm_up(name: str, user: str, database: str) -> None:
    tables = sh("docker", "exec", name, "psql", "-U", user, "-d", database, "-tAc",
                "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'public'")  # fmt: skip
    for table in tables.splitlines():
        sh("docker", "exec", name, "psql", "-U", user, "-d", database, "-tAc", f"SELECT count(*) FROM {table}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", action="append", default=[])
    args = parser.parse_args()
    files = [ROOT / ".env", *(Path(f) for f in args.env_file)]
    owners = {svc: (db, env_value(f"{p}_USER", files) or db, env_value(f"{p}_PASSWORD", files))
              for svc, (db, p) in DATABASES.items()}  # fmt: skip

    connections = {}
    for service, (database, user, _) in owners.items():
        warm_up(container(service), user, database)
        connections[service] = live_connections(container(service), user, database)
    time.sleep(30)
    separate = {service: memory_mib(container(service)) for service in owners}

    sh("docker", "rm", "-f", CANDIDATE, check=False)
    sh("docker", "run", "-d", "--name", CANDIDATE, "-e", "POSTGRES_PASSWORD=consolidation-test", IMAGE)
    for _ in range(60):
        if sh("docker", "exec", CANDIDATE, "pg_isready", "-U", "postgres", check=False).endswith("accepting connections"):
            break
        time.sleep(1)
    holders = []
    try:
        for service, (database, user, password) in owners.items():
            # One owner per database, no cross access: ADR 0001's guarantees, on a shared server.
            sh("docker", "exec", CANDIDATE, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-c",
               f"CREATE ROLE {user} LOGIN PASSWORD '{password}'", "-c", f"CREATE DATABASE {database} OWNER {user}",
               "-c", f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC")  # fmt: skip
            if database == "ai":
                sh("docker", "exec", CANDIDATE, "psql", "-U", "postgres", "-d", database, "-c",
                   "CREATE EXTENSION IF NOT EXISTS vector")  # fmt: skip
            dump = subprocess.run(["docker", "exec", container(service), "pg_dump", "-U", user, "-d", database,
                                   "--no-owner", "--no-privileges"], capture_output=True, check=True).stdout  # fmt: skip
            sh("docker", "exec", "-i", CANDIDATE, "psql", "-q", "-U", user, "-d", database, stdin=dump)
            warm_up(CANDIDATE, user, database)
        # booking-service's owner must not get into the ai database.
        cross = subprocess.run(["docker", "exec", CANDIDATE, "psql", "-U", owners["bookings-db"][1], "-d", "ai",
                                "-tAc", "SELECT 1"], capture_output=True, text=True)  # fmt: skip
        cross_refused = cross.returncode != 0 and "permission denied" in cross.stderr.lower()
        for service, (database, user, _) in owners.items():
            for _ in range(connections[service]):
                holders.append(subprocess.Popen(
                    ["docker", "exec", "-i", CANDIDATE, "psql", "-U", user, "-d", database, "-q"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))  # fmt: skip
        time.sleep(30)
        consolidated = memory_mib(CANDIDATE)
        held = int(sh("docker", "exec", CANDIDATE, "psql", "-U", "postgres", "-tAc",
                      "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'"))  # fmt: skip
    finally:
        for holder in holders:
            holder.stdin.close()
            holder.wait(timeout=30)
        sh("docker", "rm", "-f", CANDIDATE, check=False)

    result = {
        "separate": separate,
        "separateTotal": sum(separate.values()),
        "consolidated": consolidated,
        "connections": connections,
        "heldInCandidate": held,
        "crossAccessRefused": cross_refused,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "postgres-consolidation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
