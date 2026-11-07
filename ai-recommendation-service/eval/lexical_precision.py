#!/usr/bin/env python3
"""Can the lexical list stop pulling in tours on one stray common word? Candidates tested on a held-out set.

    python -m eval.lexical_precision select --out eval/results/lexical_selection.json   old sets only
    python -m eval.lexical_precision final --selection ... --out eval/results/lexical_precision.json

Run from ai-recommendation-service, with DATABASE_URL pointing at the database eval.run_eval filled. Nothing here is
what the service runs: the lexical SQL below is a copy of app/retrieval.py's, with switches, and the first thing each
phase checks is that the copy with every switch off ranks exactly as the service does.

The candidates:
- "6-word rule": lexical weight 0.1 instead of 0.5 once the query (after the parser) has 6 words or more.
- "one stemmer": stem the query in the language it is detected to be in, instead of in English and German both. The
  detector counts each language's stopwords in the query, and leans German on an umlaut or sharp s. A tie keeps both.
- "min share s": a tour must match at least ceil(s x n) of the query's n content words to enter the lexical list.

Selection, decided before any held-out query ran, and done by `select` without loading the held-out set:
- Score each configuration on the four old sets: curated, rule, known, coverage, plus pasted tour titles as a guard.
- Eligible: no clearly worse paired difference against the current system on any of those five.
- Thresholds: the three eligible shares with the highest mean nDCG@10 over the four old sets, each set weighted equally.
- Combination: of 6-word rule on or off x stemmer both or detected x no share or a chosen share, the eligible
  configuration with the highest such mean, other than the current system itself.
`final` then scores the current system and each chosen candidate on all five sets, the held-out one included.
"""

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from app.embeddings import MODELS, OnnxEmbedder
from app.gazetteer import load_csv
from app.query_parser import QueryParser
from app.retrieval import NO_CANDIDATES, Filters, _where, dense_candidates, search

from .metrics import bootstrap_ci, ndcg_at, paired_difference_ci
from .run_eval import curated_queries, load_tours, query_sets

MODEL = "paraphrase-ml-uint8"
OLD_SETS = ("curated", "rule", "known", "coverage")
GUARD = "titles"
SHARES = (0.2, 0.25, 0.34, 0.5, 0.67, 0.75)
LONG = 6
WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Config:
    long_rule: bool = False
    stemmer: str = "both"  # "both", "detected", or "labelled" (the query's true language: an upper bound only)
    min_share: float | None = None

    @property
    def name(self) -> str:
        if self == Config():
            return "current"
        parts = []
        if self.long_rule:
            parts.append("6-word rule")
        if self.stemmer == "detected":
            parts.append("one stemmer")
        if self.stemmer == "labelled":
            parts.append("one stemmer, true language (not deployable)")
        if self.min_share is not None:
            parts.append(f"min share {self.min_share}")
        return " + ".join(parts)


def words(query: str) -> list[str]:
    return WORD.findall(query.lower())


def needed(share: float, terms: int) -> int:
    """How many of a query's content words a tour must match. At least one, as with plain OR."""
    return max(1, math.ceil(share * terms - 1e-9))


def weight_for(config: Config, query: str) -> float:
    return 0.1 if config.long_rule and len(words(query)) >= LONG else 0.5


def detect(stopwords: dict[str, tuple[bool, bool]], query: str) -> str:
    """ "english", "german", or "both" when the stopwords don't say."""
    tokens = words(query)
    english = sum(stopwords[w][0] for w in tokens)
    german = sum(stopwords[w][1] for w in tokens) + (1 if re.search("[äöüß]", query.lower()) else 0)
    if english > german:
        return "english"
    if german > english:
        return "german"
    return "both"


def _query_expression(configs: list[str]) -> str:
    """The OR-ed tsquery, exactly as app/retrieval.py builds it when both are used."""
    return " || ".join(
        f"nullif(replace(plainto_tsquery('{c}', :text)::text, ' & ', ' | '), '')::tsquery" for c in configs
    )


def lexical_sql(where: str, configs: list[str], min_match: bool) -> str:
    share = (
        " AND (SELECT count(*) FROM unnest(CAST(:word_queries AS text[])) w WHERE t.search_vector @@ w::tsquery)"
        " >= :min_match"
        if min_match
        else ""
    )
    return f"""SELECT tour_id, row_number() OVER (ORDER BY score DESC, tour_id) AS rank FROM (
                SELECT t.tour_id, ts_rank_cd(t.search_vector, q.q) AS score
                FROM ai_tours t, (SELECT {_query_expression(configs)} AS q) q
                WHERE t.search_vector @@ q.q AND {where}{share}
                ORDER BY score DESC, t.tour_id LIMIT :n
            ) ranked"""


class Runner:
    def __init__(self, with_heldout: bool) -> None:
        tours = load_tours()
        self.sets = query_sets(tours)
        written = tuple(t["id"] for t in tours if t["authored"])
        composed = tuple(t["id"] for t in tours if not t["authored"])
        self.corpora = {"curated": written, "coverage": written, "known": written, "rule": composed, GUARD: written}
        self.sets[GUARD] = [
            {"q": t["name"], "language": t["language"], "judgments": {t["id"]: 3}} for t in tours if t["authored"]
        ]
        if with_heldout:
            from .heldout import HELDOUT

            self.sets["heldout"] = curated_queries(tours, HELDOUT, "heldout")
            self.corpora["heldout"] = written
        self.parser = QueryParser(load_csv())
        self.embedder = OnnxEmbedder(MODEL, MODELS[MODEL])
        self._vectors: dict[str, list[float]] = {}
        self._stopwords: dict[str, tuple[bool, bool]] = {}
        self._terms: dict[str, tuple[str, str]] = {}

    def _vector(self, query: str) -> list[float]:
        if query not in self._vectors:
            self._vectors[query] = self.embedder.embed_query(query)
        return self._vectors[query]

    def _learn(self, db, query: str) -> None:
        missing = [w for w in set(words(query)) if w not in self._terms]
        if not missing:
            return
        rows = db.execute(
            text(
                "SELECT w, plainto_tsquery('english', w)::text, plainto_tsquery('german', w)::text "
                "FROM unnest(CAST(:words AS text[])) w"
            ),
            {"words": missing},
        ).all()
        for w, english, german in rows:
            self._terms[w] = (english, german)
            self._stopwords[w] = (english == "", german == "")

    def language(self, query: str) -> str:
        with SessionLocal() as db:
            self._learn(db, query)
        return detect(self._stopwords, query)

    def ranking(self, config: Config, query: dict, corpus: tuple[int, ...]) -> list[int]:
        understood = self.parser.parse(query["q"])
        remainder = understood.text
        filters = Filters(
            latitude=understood.latitude,
            longitude=understood.longitude,
            radius_km=understood.radius_km,
            max_price=understood.max_price,
            max_minutes=understood.max_minutes,
            min_minutes=understood.min_minutes,
            only_ids=corpus,
        )
        vector = self._vector(remainder)
        if config.stemmer == "both":
            configs = ["english", "german"]
        elif config.stemmer == "labelled":
            configs = ["english" if query["language"] == "en" else "german"]
        else:
            detected = self.language(remainder)
            configs = ["english", "german"] if detected == "both" else [detected]
        params: dict = {
            "text": remainder,
            "model": MODEL,
            "n": 100,
            "limit": 20,
            "k": 60,
            "lexical_weight": weight_for(config, remainder),
            "vector": str(vector),
        }
        with SessionLocal() as db, db.begin():
            self._learn(db, remainder)
            where = _where(filters, params)
            use_share = False
            if config.min_share is not None:
                index = {"english": 0, "german": 1}
                word_queries = []
                for w in dict.fromkeys(words(remainder)):
                    kept = [self._terms[w][index[c]] for c in configs if self._terms[w][index[c]]]
                    if kept:
                        word_queries.append(" | ".join(f"({k})" for k in kept))
                if word_queries:
                    use_share = True
                    params["word_queries"] = word_queries
                    params["min_match"] = needed(config.min_share, len(word_queries))
            lexical = lexical_sql(where, configs, use_share) if remainder.strip() else NO_CANDIDATES
            db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            db.execute(text("SET LOCAL hnsw.ef_search = 100"))
            statement = f"""
                WITH dense AS ({dense_candidates(where)}), lexical AS ({lexical})
                SELECT COALESCE(dense.tour_id, lexical.tour_id) AS tour_id,
                       COALESCE(1.0 / (:k + dense.rank), 0)
                           + COALESCE(CAST(:lexical_weight AS float8) / (:k + lexical.rank), 0) AS fused_score
                FROM dense FULL JOIN lexical ON dense.tour_id = lexical.tour_id
                ORDER BY fused_score DESC, tour_id
                LIMIT :limit
            """
            return [row.tour_id for row in db.execute(text(statement), params)]

    def service_ranking(self, query: dict, corpus: tuple[int, ...]) -> list[int]:
        """What app/retrieval.py returns for the chosen system, for the reproduction check."""
        understood = self.parser.parse(query["q"])
        filters = Filters(
            latitude=understood.latitude,
            longitude=understood.longitude,
            radius_km=understood.radius_km,
            max_price=understood.max_price,
            max_minutes=understood.max_minutes,
            min_minutes=understood.min_minutes,
            only_ids=corpus,
        )
        with SessionLocal() as db, db.begin():
            hits = search(
                db,
                query_text=understood.text,
                query_vector=self._vector(understood.text),
                model=MODEL,
                filters=filters,
                mode="hybrid",
                limit=20,
            )
        return [hit.tour_id for hit in hits]

    def scores(self, config: Config, set_name: str) -> list[float]:
        corpus = self.corpora[set_name]
        return [ndcg_at(self.ranking(config, q, corpus), q["judgments"], 10) for q in self.sets[set_name]]

    def check_reproduction(self, set_names) -> None:
        for set_name in set_names:
            for query in self.sets[set_name]:
                corpus = self.corpora[set_name]
                ours, theirs = self.ranking(Config(), query, corpus), self.service_ranking(query, corpus)
                if ours != theirs:
                    raise SystemExit(f"the copy doesn't reproduce the service on {set_name}: {query['q']!r}")
            print("reproduces the service on", set_name, flush=True)


def verdict(low: float, high: float) -> str:
    return "better" if low > 0 else ("worse" if high < 0 else "no clear difference")


def evaluate(runner: Runner, configs: list[Config], set_names) -> dict:
    current = {s: runner.scores(Config(), s) for s in set_names}
    result = {}
    for config in configs:
        by_set = current if config == Config() else {s: runner.scores(config, s) for s in set_names}
        row: dict = {"config": asdict(config), "sets": {}}
        for set_name in set_names:
            cell: dict = {"ndcg": bootstrap_ci(by_set[set_name]), "per_query": by_set[set_name]}
            if config != Config():
                difference = paired_difference_ci(by_set[set_name], current[set_name])
                cell["paired"] = list(difference)
                cell["verdict"] = verdict(*difference[1:])
            row["sets"][set_name] = cell
        row["old_mean"] = sum(row["sets"][s]["ndcg"][0] for s in OLD_SETS) / len(OLD_SETS)
        row["eligible"] = all(row["sets"][s].get("verdict") != "worse" for s in (*OLD_SETS, GUARD))
        result[config.name] = row
        print(f"{config.name:60} old mean {row['old_mean']:.4f} eligible {row['eligible']}", flush=True)
    return result


def detector_accuracy(runner: Runner, set_names) -> dict:
    result = {}
    for set_name in set_names:
        queries = [q for q in runner.sets[set_name] if q.get("language") in ("en", "de")]
        calls = [(q["language"], runner.language(runner.parser.parse(q["q"]).text)) for q in queries]
        right = sum(
            (truth == "en" and call == "english") or (truth == "de" and call == "german") for truth, call in calls
        )
        undecided = sum(call == "both" for _, call in calls)
        result[set_name] = {"queries": len(calls), "right": right, "undecided": undecided}
    return result


def select(out: Path) -> None:
    runner = Runner(with_heldout=False)
    runner.check_reproduction((*OLD_SETS, GUARD))
    names = (*OLD_SETS, GUARD)
    grid = evaluate(runner, [Config(), *[Config(min_share=s) for s in SHARES]], names)
    eligible = [r for name, r in grid.items() if name != "current" and r["eligible"]]
    shares = [r["config"]["min_share"] for r in sorted(eligible, key=lambda r: -r["old_mean"])[:3]]
    combos = [
        Config(rule, stemmer, share)
        for rule in (False, True)
        for stemmer in ("both", "detected")
        for share in (None, *shares)
    ]
    combined = evaluate(runner, combos, names)
    best = max(
        (r for name, r in combined.items() if name != "current" and r["eligible"]),
        key=lambda r: r["old_mean"],
        default=None,
    )
    selection = {
        "shares": shares,
        "best": best["config"] if best else None,
        "share_grid": {name: _brief(r) for name, r in grid.items()},
        "combinations": {name: _brief(r) for name, r in combined.items()},
        "detector": detector_accuracy(runner, ("curated", "rule", "known", "coverage")),
    }
    out.write_text(json.dumps(selection, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("shares", shares, "best", selection["best"])


def _brief(row: dict) -> dict:
    return {
        "old_mean": row["old_mean"],
        "eligible": row["eligible"],
        "sets": {s: {k: v for k, v in cell.items() if k != "per_query"} for s, cell in row["sets"].items()},
    }


def final(selection_path: Path, out: Path) -> None:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    runner = Runner(with_heldout=True)
    runner.check_reproduction(("heldout",))
    configs = [
        Config(),
        Config(long_rule=True),
        Config(stemmer="detected"),
        Config(stemmer="labelled"),
        *[Config(min_share=s) for s in selection["shares"]],
    ]
    if selection["best"] and Config(**selection["best"]) not in configs:
        configs.append(Config(**selection["best"]))
    names = (*OLD_SETS, "heldout", GUARD)
    results = {
        "selection": str(selection_path.name),
        "best": Config(**selection["best"]).name if selection["best"] else None,
        "detector": detector_accuracy(runner, ("heldout",)) | selection["detector"],
        "systems": evaluate(runner, configs, names),
    }
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["select", "final"])
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "select":
        select(args.out)
    else:
        final(args.selection, args.out)


if __name__ == "__main__":
    main()
