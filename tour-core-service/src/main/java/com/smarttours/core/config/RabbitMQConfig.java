package main.java.com.smarttours.core.config;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class RabbitMQConfig {

    public static final String EXCHANGE_NAME = "tours.exchange";
    public static final String QUEUE_AI_EMBEDDING = "tours.ai.embedding.queue";
    public static final String ROUTING_KEY_CREATED = "tour.created";

    @Bean
    public DirectExchange toursExchange() {
        return new DirectExchange(EXCHANGE_NAME);
    }

    @Bean
    public Queue aiEmbeddingQueue() {
        // true means the queue survives a RabbitMQ restart
        return new Queue(QUEUE_AI_EMBEDDING, true); 
    }

    @Bean
    public Binding bindingAI(Queue aiEmbeddingQueue, DirectExchange toursExchange) {
        // Links the inbox to the router
        return BindingBuilder.bind(aiEmbeddingQueue).to(toursExchange).with(ROUTING_KEY_CREATED);
    }
}
