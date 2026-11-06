#!/usr/bin/env python3
"""Fill the AI read model for a demo: datasets/tours_v1 as events through the real broker, with departures.

The dataset is synthetic (docs/datasets.md). This publishes to the exchange as tour-core and booking-service would, so the
worker's consumer, its embeddings and its filters all run for real. It writes to no database. The tour ids (from 100001 for
written tours) don't exist in tour-core, so these tours are searchable and plannable but not bookable: for that, create
them through the tours API.

    python scripts/seed_ai_catalogue.py --limit 300

Needs pika, and RABBITMQ_USER and RABBITMQ_PASSWORD from .env in the environment. Embedding takes a while: watch
docker compose logs ai-worker, or queue depth in the RabbitMQ UI.
"""

import argparse
import json
import os
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pika

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "tours_v1" / "tours.jsonl"
EXCHANGE = "smarttours.events"
DEPARTURES_PER_TOUR = 8
HORIZON_DAYS = 90


def message(event_type: str, **fields: Any) -> bytes:
    return json.dumps(
        {
            "eventId": str(uuid.uuid4()),
            "eventType": event_type,
            "occurredAt": datetime.now(UTC).isoformat(),
            **fields,
        }
    ).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=300, help="How many tours: written ones first, they are the varied ones")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    with DATASET.open(encoding="utf-8") as lines:
        tours = sorted(map(json.loads, lines), key=lambda tour: not tour["authored"])[: args.limit]
    credentials = pika.PlainCredentials(os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"])
    connection = pika.BlockingConnection(pika.ConnectionParameters(args.host, credentials=credentials))
    channel = connection.channel()
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    properties = pika.BasicProperties(content_type="application/json", delivery_mode=pika.DeliveryMode.Persistent)

    def publish(key: str, body: bytes) -> None:
        channel.basic_publish(EXCHANGE, key, body, properties)

    rng = random.Random(5)
    midnight = datetime.now(UTC).replace(hour=4, minute=0, second=0, microsecond=0)
    for tour in tours:
        publish(
            "tour.updated",
            message(
                "tour.updated",
                tourId=tour["id"],
                name=tour["name"],
                description=tour["description"],
                language=tour["language"],
                version=1,
                latitude=tour["latitude"],
                longitude=tour["longitude"],
                price=str(tour["price"]),
                currency=tour["currency"],
                category=tour["category"],
                difficulty=tour["difficulty"],
                durationMinutes=tour["durationMinutes"],
            ),
        )
        for number in range(DEPARTURES_PER_TOUR):
            availability_id = tour["id"] * 100 + number
            starts_at = midnight + timedelta(days=rng.randint(1, HORIZON_DAYS), hours=rng.choice([0, 2, 5, 9, 12]))
            publish(
                "availability.changed",
                message(
                    "availability.changed",
                    availabilityId=availability_id,
                    tourId=tour["id"],
                    startsAt=starts_at.isoformat(),
                    totalSlots=12,
                    slotsLeft=rng.randint(2, 12),
                    version=1,
                ),
            )
    connection.close()
    print(f"Published {len(tours)} tours and {len(tours) * DEPARTURES_PER_TOUR} departures")


if __name__ == "__main__":
    main()
