package com.smarttours.core.messaging;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

import java.util.function.Consumer;

/**
 * Runs a listener's handler and decides what happens to the message, like the AI worker's
 * {@code decide()}:
 * <ul>
 *   <li>success: acknowledged;</li>
 *   <li>{@link PoisonMessageException}: rejected without requeue, so the queue's DLX sends it to the DLQ;</li>
 *   <li>any other failure: a copy with {@code x-retry-count + 1} goes to the retry queue and the original
 *       is acknowledged, until {@code maxRetries}; then it is rejected to the DLQ as well.</li>
 * </ul>
 * The retry copy is published before the original is acknowledged. A crash in between means a
 * duplicate, never a loss, so handlers must be idempotent.
 */
@Component
public class RetryingHandler {

    static final String RETRY_HEADER = "x-retry-count";

    private static final Logger log = LoggerFactory.getLogger(RetryingHandler.class);

    private final RabbitTemplate rabbitTemplate;
    private final MessagingProperties properties;

    public RetryingHandler(RabbitTemplate rabbitTemplate, MessagingProperties properties) {
        this.rabbitTemplate = rabbitTemplate;
        this.properties = properties;
    }

    public void handle(String queue, Message message, Consumer<Message> handler) {
        int attempt = retryCount(message);
        try {
            handler.accept(message);
        } catch (PoisonMessageException ex) {
            log.error("Poison message on {}, dead-lettering without retry: {}", queue, ex.getMessage());
            throw new AmqpRejectAndDontRequeueException(ex.getMessage(), ex);
        } catch (RuntimeException ex) {
            if (attempt < properties.maxRetries()) {
                log.warn("Handler on {} failed (attempt {} of {}), retrying in {}", queue, attempt + 1,
                        properties.maxRetries() + 1, properties.retryDelay(), ex);
                Message retry = MessageBuilder.fromMessage(message).setHeader(RETRY_HEADER, attempt + 1).build();
                rabbitTemplate.send("", ConsumerTopology.retryQueue(queue), retry);
                return;
            }
            log.error("Handler on {} failed {} times, dead-lettering", queue, attempt + 1, ex);
            throw new AmqpRejectAndDontRequeueException("retries exhausted", ex);
        }
    }

    static int retryCount(Message message) {
        Object value = message.getMessageProperties().getHeaders().get(RETRY_HEADER);
        return value instanceof Number number ? number.intValue() : 0;
    }
}
