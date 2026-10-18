# Smart Tours Distributed API

A microservices-based backend platform for booking tours, handling concurrent transactions, and generating AI-driven semantic recommendations. 

## System Architecture

This project utilizes a polyglot microservices architecture (Java/Spring Boot & Python/FastAPI) communicating synchronously via REST/JWT and asynchronously via message brokers.

*(Architecture diagram placeholder - to be added in docs/)*

### Services Overview
* **API Gateway:** Centralized routing, rate limiting, and stateless JWT authentication.
* **Tour Core Service (Java/Spring Boot):** Manages tour inventory, pricing, and geospatial queries using PostgreSQL + PostGIS.
* **Booking Service (Python/FastAPI):** Handles transactional booking flows, race-condition mitigation, and payment events.
* **AI Recommendation Service (Python/FastAPI):** Generates vector embeddings for semantic tour searches and AI-driven itineraries using ChromaDB.

## Development Phases
- [✔️] **Phase 1:** Monorepo scaffolding and master documentation.
- [✔️] **Phase 2:** Global infrastructure setup (Docker Compose, PostgreSQL, Redis, RabbitMQ).
- [✔️] **Phase 3:** API Gateway and JWT Auth implementation.
- [✔️] **Phase 4:** Tour Core Service (Java/Spring Boot REST API).
- [✔️] **Phase 5:** Booking Service (Python/FastAPI with transaction locking).
- [✔️] **Phase 6:** AI Recommendation Service (Vector Embeddings).
- [ ✔️] **Phase 7:** Asynchronous Event Integration (RabbitMQ).

## Tech Stack
* **Languages:** Java 17+, Python 3.10+
* **Frameworks:** Spring Boot, FastAPI
* **Databases:** PostgreSQL (PostGIS), ChromaDB (Vector), Redis (Caching)
* **Infrastructure:** Docker, RabbitMQ