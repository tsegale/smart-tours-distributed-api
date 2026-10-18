from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import chromadb
from .ml_model import load_model, get_embedding
import threading
from .rabbitmq_consumer import start_consuming

app = FastAPI(title="Smart Tours - AI Recommendation Service")

# Initialize ChromaDB (Ephemeral for development, but can be persistent)
chroma_client = chromadb.Client()
# L2 (Euclidean distance) or Cosine Similarity are standard for text embeddings
collection = chroma_client.get_or_create_collection(
    name="tours_embeddings",
    metadata={"hnsw:space": "cosine"} # Use Cosine Similarity
)

class TourData(BaseModel):
    tour_id: str
    title: str
    description: str

@app.on_event("startup")
def startup_event():
    load_model() # Load the PyTorch model into memory when server starts
    
    # Start the RabbitMQ listener in the background
    consumer_thread = threading.Thread(target=start_consuming, daemon=True)
    consumer_thread.start()

@app.post("/api/ai/embed", status_code=201)
def embed_tour(tour: TourData):
    """
    Called whenever a new tour is created in the Java Core Service.
    Converts the title and description into math and saves it to ChromaDB.
    """
    text_to_embed = f"{tour.title}. {tour.description}"
    vector = get_embedding(text_to_embed)
    
    # Upsert inserts the vector if it's new, or updates it if the ID exists
    collection.upsert(
        documents=[text_to_embed],
        embeddings=[vector],
        metadatas=[{"title": tour.title}], # Store lightweight metadata
        ids=[tour.tour_id]
    )
    return {"status": "success", "message": f"Embedded tour {tour.tour_id}"}

@app.get("/api/ai/recommend/{tour_id}")
def get_recommendations(tour_id: str, limit: int = 3):
    """
    Returns the IDs of the most semantically similar tours.
    """
    # 1. Look up the embedding for the requested tour
    existing_tour = collection.get(ids=[tour_id], include=["embeddings"])
    if not existing_tour["embeddings"]:
        raise HTTPException(status_code=404, detail="Tour not found in vector database.")
        
    target_vector = existing_tour["embeddings"][0]

    # 2. Query ChromaDB for the closest vectors mathematically
    results = collection.query(
        query_embeddings=[target_vector],
        n_results=limit + 1 # +1 because the exact tour will be the #1 match
    )

    # 3. Filter out the target tour itself
    recommended_ids = []
    for returned_id in results["ids"][0]:
        if returned_id != tour_id:
            recommended_ids.append(returned_id)

    return {"recommended_tour_ids": recommended_ids[:limit]}