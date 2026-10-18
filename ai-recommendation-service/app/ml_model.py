from sentence_transformers import SentenceTransformer

# all-MiniLM-L6-v2 is a fantastic, lightweight model.
# It converts any sentence into a 384-dimensional vector.
MODEL_NAME = 'all-MiniLM-L6-v2'
model = None

def load_model():
    global model
    if model is None:
        print(f"Loading embedding model: {MODEL_NAME}...")
        model = SentenceTransformer(MODEL_NAME)
        print("Model loaded successfully.")

def get_embedding(text: str) -> list[float]:
    """Converts text into a list of 384 floating-point numbers."""
    if model is None:
        load_model()
    # model.encode returns a numpy array, we convert it to a standard Python list
    return model.encode(text).tolist()