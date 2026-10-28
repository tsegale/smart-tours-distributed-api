package com.smarttours.core.tour.dto;

import org.springframework.data.domain.Page;

import java.util.List;
import java.util.function.Function;

/** Stable JSON shape for paged results (Spring Data's PageImpl is not a stable API contract). */
public record PageResponse<T>(List<T> content, int page, int size, long totalElements, int totalPages) {

    public static <E, T> PageResponse<T> of(Page<E> page, Function<E, T> mapper) {
        return new PageResponse<>(
                page.getContent().stream().map(mapper).toList(),
                page.getNumber(),
                page.getSize(),
                page.getTotalElements(),
                page.getTotalPages());
    }
}
