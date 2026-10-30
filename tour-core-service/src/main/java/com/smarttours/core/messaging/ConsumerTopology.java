package com.smarttours.core.messaging;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.FanoutExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.rabbit.config.SimpleRabbitListenerContainerFactory;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.support.converter.SimpleMessageConverter;
import org.springframework.boot.amqp.autoconfigure.SimpleRabbitListenerContainerFactoryConfigurer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.ArrayList;
import java.util.List;

/**
 * Queues tour-core consumes from. Each consumer owns its queue, bindings, retry queue and DLQ,
 * with the same shape as the AI worker's (ADR 0005):
 *
 * <pre>
 *   smarttours.events --key--> {queue} --(handler fails)--> {queue}.retry --(TTL)--> {queue}
 *   poison message, or retries exhausted:  {queue} --reject--> {queue}.dlx --> {queue}.dlq
 * </pre>
 */
@Configuration
public class ConsumerTopology {

    /** availability.changed from booking-service: the departure read model. */
    public static final String AVAILABILITY_QUEUE = "core.availability-summary";
    /** review.submitted from booking-service: tour ratings. */
    public static final String RATING_QUEUE = "core.tour-rating";

    /** Listener container factory for the consumers below. */
    public static final String CONTAINER_FACTORY = "eventListenerContainerFactory";

    /**
     * Boot's listener settings, but without JSON conversion: the listeners take the raw message and
     * {@link EventParser} parses and validates it once, so a malformed body is classified as poison by our
     * code (straight to the DLQ) instead of failing inside the framework's converter first.
     */
    @Bean(CONTAINER_FACTORY)
    SimpleRabbitListenerContainerFactory eventListenerContainerFactory(
            SimpleRabbitListenerContainerFactoryConfigurer configurer, ConnectionFactory connectionFactory) {
        SimpleRabbitListenerContainerFactory factory = new SimpleRabbitListenerContainerFactory();
        configurer.configure(factory, connectionFactory);
        factory.setMessageConverter(new SimpleMessageConverter());
        return factory;
    }

    @Bean
    Declarables availabilityConsumerTopology(TopicExchange eventsExchange, MessagingProperties properties) {
        return consumerTopology(AVAILABILITY_QUEUE, eventsExchange, properties, "availability.changed");
    }

    @Bean
    Declarables ratingConsumerTopology(TopicExchange eventsExchange, MessagingProperties properties) {
        return consumerTopology(RATING_QUEUE, eventsExchange, properties, "review.submitted");
    }

    public static String retryQueue(String queue) {
        return queue + ".retry";
    }

    public static String deadLetterQueue(String queue) {
        return queue + ".dlq";
    }

    static Declarables consumerTopology(String queue, TopicExchange events, MessagingProperties properties,
                                        String... routingKeys) {
        FanoutExchange dlx = new FanoutExchange(queue + ".dlx", true, false);
        // Quorum queues: replicated and durable, RabbitMQ's recommended default.
        Queue dlq = QueueBuilder.durable(deadLetterQueue(queue)).quorum().build();
        Queue main = QueueBuilder.durable(queue).quorum().deadLetterExchange(dlx.getName()).build();
        // Messages wait here for retryDelay, then dead-letter back into the main queue.
        Queue retry = QueueBuilder.durable(retryQueue(queue)).quorum()
                .ttl(Math.toIntExact(properties.retryDelay().toMillis()))
                .deadLetterExchange("")
                .deadLetterRoutingKey(queue)
                .build();

        List<Declarable> declarables = new ArrayList<>(List.of(dlx, dlq, main, retry,
                BindingBuilder.bind(dlq).to(dlx)));
        for (String key : routingKeys) {
            Binding binding = BindingBuilder.bind(main).to(events).with(key);
            declarables.add(binding);
        }
        return new Declarables(declarables);
    }
}
