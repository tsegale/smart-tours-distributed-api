# Datasets

## `datasets/tours_v1`: a synthetic tour catalogue

**Everything here is fiction.** The places are real, but no tour, operator, price or rating exists. It's for evaluating
search; it says nothing about what real visitors want.

| | |
| --- | --- |
| Records | 2,505: 2,400 composed, 105 individually written |
| Languages | 607 German (577 composed, 30 written), the rest English |
| Coverage | Namibia, Botswana, Zimbabwe, Zambia, South Africa, Angola |
| Made | 2026-11-06, by `datasets/tours_v1/generate.py` (seed 20261106), with no LLM API call at run time |
| Text by | Claude (Opus 5.5), in a coding session: the fragments in `generate.py` and every tour in `authored.py`. There was no separate prompt: the instruction was the project brief's description of the catalogue |

### Two parts, and why

**Composed (`authored: false`).** Each tour combines a place and an activity with 2 or 3 sentences from a small library, plus
sentences for family suitability, access, language, season and logistics. Attributes are known exactly, which is what makes
relevance decidable by rule. **It's repetitive: the 2,400 descriptions use only 172 distinct sentences.** A query sharing words
with a fragment matches every tour using it, so this part flatters lexical search and can't show much about meaning.

**Written (`authored: true`).** Tours written one at a time, with their own vocabulary, including German written directly rather
than translated. There are too few to stand in for a real catalogue, and one author's style runs through them. But
this is the part that tests understanding, so **`docs/benchmarks/search-quality.md` reports it separately, and it decides
modelling choices.**

### Known limitations

- Small, and from a single author. Differences between models here may not hold on a real catalogue.
- Attributes are chosen by rule, so they're tidier than a real operator's listings: no typos, missing fields or marketing noise.
- German is a quarter of the catalogue by design, because German visitors are a major market. The share isn't measured.
- `tours_v2` could replace this if an LLM or a real catalogue becomes available: the version in the name is there for that.

## `ai-recommendation-service/datasets/gazetteer`: place names

`places.csv` is derived from [GeoNames](https://www.geonames.org/), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): the country dumps for NA, BW, ZW, ZM, ZA and AO, downloaded on
2026-11-06. `ai-recommendation-service/datasets/gazetteer/build.py` keeps towns of some size, parks and well-known Namibian landforms, because most
GeoNames entries are farms and wells named with ordinary words, which would turn query words into false place filters. A
short list of names visitors use, which GeoNames has only under a longer name, is added by hand in that script.
