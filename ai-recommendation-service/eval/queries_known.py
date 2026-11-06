"""Known-item queries: the exact, rare word a person types when they already know what they want.

The curated set was written to avoid the tours' wording, which is the hard case for lexical search and the easy one
for embeddings. This set is the opposite on purpose, so neither shape alone decides whether lexical search earns its
place. The labels are a rule, not a judgment: 3 if the term is in the tour's name, 2 if only in its description. Terms
that match nothing in the written tours are dropped when the set is built.
"""

import re

TERMS = {
    "en": [
        "Deadvlei",
        "Sossusvlei",
        "Kolmanskop",
        "Spitzkoppe",
        "Namutoni",
        "Waterberg",
        "Twyfelfontein",
        "Epupa",
        "Cape Cross",
        "Sandwich Harbour",
        "Kunene",
        "Batoka",
        "Chobe",
        "Katutura",
        "kapana",
        "mokoro",
        "quiver tree",
        "Halifax Island",
        "Dune 7",
        "Van Zyl's Pass",
        "Naukluft",
        "Damaraland",
        "Tsumeb",
        "Okavango",
        "Kavango",
    ],
    "de": [
        "Deadvlei",
        "Kolmanskop",
        "Köcherbaum",
        "Fischfluss",
        "Skelettküste",
        "Pelzrobben",
        "Einbaum",
        "Himba",
        "Geparden",
        "Kajak",
        "Wüstenelefanten",
    ],
}


def build(tours: list[dict]) -> list[dict]:
    queries = []
    for language, terms in TERMS.items():
        for term in terms:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            judgments = {}
            for tour in tours:
                if pattern.search(tour["name"]):
                    judgments[tour["id"]] = 3
                elif pattern.search(tour["description"]):
                    judgments[tour["id"]] = 2
            if judgments:
                queries.append(
                    {"id": f"known-{language}-{term}", "q": term, "language": language, "judgments": judgments}
                )
    return queries
