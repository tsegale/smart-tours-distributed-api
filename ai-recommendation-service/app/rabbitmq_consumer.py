import pika
import json
import requests

RABBITMQ_HOST = "localhost" # Connects to the Docker container
QUEUE_NAME = "tours.ai.embedding.queue"

def process_message(ch, method, properties, body):
    try:
        # 1. Parse the JSON sent by Java
        data = json.loads(body)
        print(f"Received event for Tour ID {data['tour_id']}")

        # 2. Call the internal FastAPI endpoint to embed it
        # (Or  call the ml_model.py functions directly)
        response = requests.post("http://127.0.0.1:8000/api/ai/embed", json=data)
        
        if response.status_code == 201:
            # 3. Tell RabbitMQ the message was processed successfully so it can delete it
            ch.basic_ack(delivery_tag=method.delivery_tag)
            print(f"Successfully generated embeddings for Tour {data['tour_id']}")
        else:
            # If the internal logic failed, put the message back in the queue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            
    except Exception as e:
        print(f"Error processing message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def start_consuming():
    """Runs a blocking loop to listen for RabbitMQ messages."""
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    channel = connection.channel()

    # Ensure the queue exists (matches Java's durable=true)
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    
    # Don't give a worker more than 1 message at a time
    channel.basic_qos(prefetch_count=1) 
    
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=process_message)
    print("RabbitMQ Consumer started. Waiting for messages...")
    channel.start_consuming()