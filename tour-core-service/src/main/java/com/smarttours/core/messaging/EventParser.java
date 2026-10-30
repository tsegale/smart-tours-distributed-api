package com.smarttours.core.messaging;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import org.springframework.amqp.core.Message;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.json.JsonMapper;

import java.util.Set;
import java.util.stream.Collectors;

/**
 * Turns a message body into a validated event record. Anything that doesn't parse or validate is a
 * {@link PoisonMessageException}: no number of retries will make it valid.
 */
@Component
public class EventParser {

    private final JsonMapper json;
    private final Validator validator;

    public EventParser(JsonMapper json, Validator validator) {
        this.json = json;
        this.validator = validator;
    }

    public <T> T parse(Message message, Class<T> type) {
        T event;
        try {
            event = json.readValue(message.getBody(), type);
        } catch (JacksonException ex) {
            throw new PoisonMessageException("Unparseable " + type.getSimpleName() + ": " + ex.getOriginalMessage(), ex);
        }
        if (event == null) {
            throw new PoisonMessageException("Empty " + type.getSimpleName());
        }
        Set<ConstraintViolation<T>> violations = validator.validate(event);
        if (!violations.isEmpty()) {
            throw new PoisonMessageException("Invalid " + type.getSimpleName() + ": " + violations.stream()
                    .map(v -> v.getPropertyPath() + " " + v.getMessage())
                    .sorted()
                    .collect(Collectors.joining(", ")));
        }
        return event;
    }
}
