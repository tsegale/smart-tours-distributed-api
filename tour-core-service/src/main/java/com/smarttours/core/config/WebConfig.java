package com.smarttours.core.config;

import com.smarttours.core.search.SearchSort;
import com.smarttours.core.tour.Category;
import com.smarttours.core.tour.Difficulty;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.convert.converter.Converter;
import org.springframework.format.FormatterRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.Locale;

/** Query-string conversions: sort=price_asc, and case-insensitive category=safari / difficulty=easy. */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    @Override
    public void addFormatters(FormatterRegistry registry) {
        registry.addConverter(new SearchSortConverter());
        registry.addConverter(new CategoryConverter());
        registry.addConverter(new DifficultyConverter());
    }

    static final class SearchSortConverter implements Converter<String, SearchSort> {
        @Override
        public SearchSort convert(String source) {
            return SearchSort.fromParam(source);
        }
    }

    static final class CategoryConverter implements Converter<String, Category> {
        @Override
        public Category convert(String source) {
            return Category.valueOf(source.trim().toUpperCase(Locale.ROOT));
        }
    }

    static final class DifficultyConverter implements Converter<String, Difficulty> {
        @Override
        public Difficulty convert(String source) {
            return Difficulty.valueOf(source.trim().toUpperCase(Locale.ROOT));
        }
    }
}
