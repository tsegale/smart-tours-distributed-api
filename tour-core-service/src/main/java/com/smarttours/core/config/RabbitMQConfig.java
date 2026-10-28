package com.smarttours.core.config;

import org.springframework.amqp.core.ExchangeBuilder;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.support.converter.JacksonJsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Producer-side messaging setup. The producer declares only the exchange it publishes to:
 * each consumer owns its own queue, bindings and dead-letter setup, so adding a consumer
 * never needs a change here.
 */
@Configuration
public class RabbitMQConfig {

    /** One topic exchange for all integration events; routing keys look like "tour.created". */
    public static final String EVENTS_EXCHANGE = "smarttours.events";

    @Bean
    public TopicExchange eventsExchange() {
        return ExchangeBuilder.topicExchange(EVENTS_EXCHANGE).durable(true).build();
    }

    /** Serialises event records as JSON (Jackson 3) instead of Java serialisation. */
    @Bean
    public MessageConverter jsonMessageConverter() {
        return new JacksonJsonMessageConverter();
    }
}
