package com.smarttours.core.search;

import java.util.List;

/**
 * A page of search results. {@code nextCursor} is null on the last page; otherwise send it back as
 * {@code cursor}, with the same filters, for the next one.
 */
public record SearchResponse(List<TourSearchHit> items, int size, String nextCursor) {
}
