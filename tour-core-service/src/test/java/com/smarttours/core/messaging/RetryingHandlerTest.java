package com.smarttours.core.messaging;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.rabbit.core.RabbitTemplate;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

class RetryingHandlerTest {

    private final RabbitTemplate rabbit = mock(RabbitTemplate.class);
    private final RetryingHandler handler = new RetryingHandler(rabbit, new MessagingProperties(5, Duration.ofSeconds(10)));

    @Test
    void success_isAcknowledgedAndNothingIsRepublished() {
        handler.handle("core.q", message(0), m -> { });

        verify(rabbit, never()).send(anyString(), anyString(), any(Message.class));
    }

    @Test
    void poisonMessage_isRejectedToTheDlqWithoutRetry() {
        assertThatThrownBy(() -> handler.handle("core.q", message(0), m -> {
            throw new PoisonMessageException("bad json");
        })).isInstanceOf(AmqpRejectAndDontRequeueException.class);

        verify(rabbit, never()).send(anyString(), anyString(), any(Message.class));
    }

    @Test
    void transientFailure_goesToTheRetryQueueWithTheCountIncremented() {
        handler.handle("core.q", message(2), m -> {
            throw new IllegalStateException("database restarting");
        });

        ArgumentCaptor<Message> retried = ArgumentCaptor.forClass(Message.class);
        verify(rabbit).send(eq(""), eq("core.q.retry"), retried.capture());
        assertThat(RetryingHandler.retryCount(retried.getValue())).isEqualTo(3);
        assertThat(retried.getValue().getBody()).isEqualTo("{}".getBytes());
    }

    @Test
    void lastAllowedRetry_failing_isDeadLettered() {
        assertThatThrownBy(() -> handler.handle("core.q", message(5), m -> {
            throw new IllegalStateException("still failing");
        })).isInstanceOf(AmqpRejectAndDontRequeueException.class);

        verify(rabbit, never()).send(anyString(), anyString(), any(Message.class));
    }

    private static Message message(int retries) {
        MessageBuilder builder = MessageBuilder.withBody("{}".getBytes());
        if (retries > 0) {
            builder.setHeader(RetryingHandler.RETRY_HEADER, retries);
        }
        return builder.build();
    }
}
