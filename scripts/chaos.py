#!/usr/bin/env python3
"""Switch the fault scenario of the provider stubs (compose profile `chaos`, WireMock on 127.0.0.1:8090).

    python scripts/chaos.py weather slow     # Open-Meteo forecast and archive answer after 2 s
    python scripts/chaos.py fx errors        # Frankfurter answers 500
    python scripts/chaos.py all dead         # every provider resets the connection
    python scripts/chaos.py all healthy
    python scripts/chaos.py status           # current state of each scenario

Each provider is a WireMock scenario whose state selects the stub: healthy (WireMock's initial state
"Started"), slow (2 s delay), errors (HTTP 500) or dead (connection reset). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ADMIN = os.environ.get("CHAOS_ADMIN_URL", "http://127.0.0.1:8090/__admin")
SCENARIOS = {"fx": ["fx"], "weather": ["weather"], "all": ["fx", "weather"]}
STATES = {"healthy": "Started", "slow": "slow", "errors": "errors", "dead": "dead"}


def request(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(ADMIN + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        text = response.read().decode()
    return json.loads(text) if text else {}


def status() -> None:
    for scenario in request("GET", "/scenarios")["scenarios"]:
        state = scenario["state"]
        print(f"{scenario['name']:8} {'healthy' if state == 'Started' else state}")


def switch(provider: str, state: str) -> None:
    for scenario in SCENARIOS[provider]:
        request("PUT", f"/scenarios/{scenario}/state", {"state": STATES[state]})
        print(f"{scenario:8} {state}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", choices=[*SCENARIOS, "status"])
    parser.add_argument("state", nargs="?", choices=list(STATES))
    args = parser.parse_args()
    try:
        if args.provider == "status":
            status()
        elif args.state is None:
            parser.error("state is required")
        else:
            switch(args.provider, args.state)
    except OSError as ex:
        sys.exit(f"provider stubs unreachable at {ADMIN} ({ex}); start them with the chaos profile")


if __name__ == "__main__":
    main()
