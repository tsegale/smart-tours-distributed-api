"""The pure parts of the lexical-precision experiment (eval/lexical_precision.py). None of it is in the service."""

from eval.lexical_precision import Config, detect, needed, weight_for

STOPWORDS = {
    "a": (True, False),
    "the": (True, False),
    "in": (True, False),
    "und": (False, True),
    "die": (False, True),
    "im": (False, True),
    "dunes": (False, False),
    "wüste": (False, False),
    "sunrise": (False, False),
}


def test_a_minimum_share_rounds_up_and_never_asks_for_nothing():
    assert needed(0.34, 3) == 2
    assert needed(0.5, 4) == 2
    assert needed(0.5, 5) == 3
    assert needed(0.25, 1) == 1
    assert needed(0.2, 0) == 1


def test_the_language_is_whichever_has_more_stopwords_in_the_query():
    assert detect(STOPWORDS, "the dunes in a sunrise") == "english"
    assert detect(STOPWORDS, "die dunes und im sunrise") == "german"
    assert detect(STOPWORDS, "dunes sunrise") == "both"
    assert detect(STOPWORDS, "wüste sunrise") == "german"  # an umlaut tips a tie


def test_the_six_word_rule_counts_words_after_the_parser():
    rule = Config(long_rule=True)
    assert weight_for(rule, "one two three four five") == 0.5
    assert weight_for(rule, "one two three four five six") == 0.1
    assert weight_for(Config(), "one two three four five six") == 0.5


def test_names_say_what_each_configuration_is():
    assert Config().name == "current"
    assert Config(True, "detected", 0.34).name == "6-word rule + one stemmer + min share 0.34"
