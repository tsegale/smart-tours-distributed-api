"""Queries aimed at written tours the reviewed set never labels.

The owner signed off the 50 queries in curated.py, and this file exists so that they stay signed off. They label 82 of
the 105 written tours, so for the other 23 a system could return anything without the evaluation noticing.

How these labels were made (2026-11-06):
1. Claude wrote the queries and proposed every judgment from the tour descriptions, before seeing any retrieval
   result.
2. A second Claude pass checked each pair against all 105 tour descriptions: 54 labels to 73. Each change carries a
   comment with the reasoning ("Review:" above a dict, or above the entry).
3. The project owner decided the contested calls himself and signed off on the set: Batoka rafting stays 2 for
   "terrifying in the water" (swimming is a risk on that trip, not the point of it); the copper museum is 1 for
   "rock shapes and geology" (minerals in cases are not rock shapes); Baobabs above the falls 3 for the easy evening;
   the scrambles lowered for the undemanding-rocks query; half-day drives 1 and the eight-hour Huab drive 2 for the
   all-day drive; and the broad own-power query labelling everything self-propelled.

They reach 20 of the 23. Still unlabelled by anything: "Tigerfish on the Zambezi", "Photo boat with swivel seats"
and "Stone church and a railway past".

Grades as in curated.py: 3 what was asked for, 2 a close match, 1 related, anything unlisted 0. Twins carry the same
labels unless their wording differs, in which case each has its own dict. In these six pairs the German is a literal
translation of the English, so every pair shares one dict.
"""

NEEDS_REVIEW = False
REVIEWED_ON = "2026-11-06"

# Review: Epupa raised to 3 and Spitzkoppe to 2, on the test of easy + sunset + a drink + big scenery. The Walvis
# Bay catamaran stays out: it pours sparkling wine, but the fog burns off in the morning.
_EVENING = {
    # raised to 3: easy, at sunset, a cold drink, and a gorge full of waterfalls — all four parts of the query are
    # there
    "Baobabs above the falls": 3,
    "Canyon rim at sunset": 3,
    "Kalahari red dunes by sundowner truck": 3,
    "Sundowner am Canyonrand": 3,
    # golden hour, the whole plain framed, and you stay for a fireside meal; no drink is named and there are short
    # scrambles
    "Natural arches at golden hour": 2,
    "Köcherbaumwald bei Sonnenuntergang": 1,
    # glows at dusk, but nothing says you stay for it with a drink
    "Organ Pipes and Burnt Mountain": 1,
    # evening under a huge empty sky, but an overnight quad expedition rather than an easy sundowner
    "Sleeping out on the salt": 1,
    "The quiver tree forest": 1,
    # a thousand-metre abyss reached by car, so the scenery and the ease fit; no evening and no drink
    "Tundavala's great escarpment": 1,
}

# The Waterberg climb is 2: uphill with a view, but "a steady hour" isn't the effort asked for.
# Review: Daan Viljoen raised to sit level with the Waterberg climb, and three chain-and-staircase walks added. The
# roped Spitzkoppe ascent stays at 1 because the query asks to walk.
_CLIMB = {
    "Table Mountain by Platteklip Gorge": 3,
    "Zu Fuß auf den Tafelberg": 3,
    "Aufstieg zum roten Felsrand": 2,
    # hauling yourself up a granite slab on a chain is the effort asked for; what waits at the top is a hollow, not
    # a view
    "Bushman's Paradise paintings": 2,
    # raised to 2 to sit level with the Waterberg climb: both are moderate uphill walks to a viewpoint, neither is
    # strenuous
    "Daan Viljoen sunrise ridge walk": 2,
    "Sandstone climb to the plateau rim": 2,
    # a rough staircase that climbs the valley, but the engravings are the point
    "A UNESCO gallery in sandstone": 1,
    "Five days through Africa's great gorge": 1,
    "Fünf Tage durch den Fischfluss-Canyon": 1,
    # clambering, and downwards
    "Inside the Sesriem gorge": 1,
    "Roped ascent of Namibia's Matterhorn": 1,
    "Seilschaft am Matterhorn Namibias": 1,
    "Two days on the Olive Trail and beyond": 1,
    "Zwei Tage im Naukluftgebirge": 1,
}

# Sesriem is 2: the geology is the point, but you clamber down to see it.
# Review: both tours that make you clamber were lowered, per the constraint, which leaves Organ Pipes as the only 3.
# The Tsumeb mineral museum was missing entirely.
_ROCKS = {
    "Organ Pipes and Burnt Mountain": 3,
    # added by the review at 2; owner set 1: crystals found nowhere else and no walking, but minerals in display
    # cases are not the rock shapes asked for
    "Copper town museum and minerals": 1,
    "Köcherbaumwald bei Sonnenuntergang": 2,
    # lowered to 2: boulders and arches are exactly the subject, but short scrambles are still scrambles
    "Natural arches at golden hour": 2,
    # for the Giant's Playground beside it
    "The quiver tree forest": 2,
    "Tundavala's great escarpment": 2,
    "Canyon rim at sunset": 1,
    # lowered to 1: clambering down a slot gorge fails the one constraint this query makes
    "Inside the Sesriem gorge": 1,
    "Sundowner am Canyonrand": 1,
}

# Review: nothing else in the catalogue is frightening and wet; the skydive and the choppy penguin schooner stay at 0
# so that "in the water" keeps its teeth.
_WATER = {
    "Cage diving at Gansbaai day trip": 3,
    # owner kept at 2: the review raised it to 3 for flips and long swims, but swimming is a risk on this trip, not
    # what it is; cage diving and the lip pool put you in the water by design
    "Grade five on the Batoka Gorge": 2,
    "Swimming at the edge of the falls": 3,
    # frightening, and over water rather than in it
    "Bungee from the old bridge": 1,
    "Kunene whitewater rafting": 1,
    "Wildwasser auf dem Kunene": 1,
}

# Review: half-day drives lowered to 1 as the rule says, which moves Kgalagadi and Mahango down; the eight-hour Huab
# elephant drive moves up to fill the 2 band on merit.
_GAME_DRIVE = {
    "Full day across the great white pan": 3,
    "Ganztägig rund um die Salzpfanne": 3,
    # raised to 2: eight hours in a vehicle across open riverbeds is an all-day drive, narrowed to one species
    "Desert elephants of the Huab": 2,
    # raised to 2, with its twin
    "Wüstenelefanten im Huab-Tal": 2,
    # lowered to 1: the open red Kalahari is right, but four hours is a half-day
    "Black-maned lions of the Auob": 1,
    # a game drive over open dunes past oryx, but a short evening one
    "Kalahari red dunes by sundowner truck": 1,
    # lowered to 1: three and a half hours is not the all-day drive asked for
    "Mahango and the Bwabwata floodplain": 1,
    "Night drive with a spotlight": 1,
    "Painted dogs in teak woodland": 1,
    "Rhino breeding on the sandstone top": 1,
    # lowered to 1, with its twin
    "Schwarzmähnenlöwen im Auob-Tal": 1,
}

# Review: the broadest query here, and the one with most missing. Everything self-propelled in the catalogue is now
# labelled, graded by how much of it is exploring wild country: walking safaris 3, long hikes and scrambles 2, being
# carried or paddled 1.
_OWN_POWER = {
    "Bush walk on a private reserve": 3,
    "Mountain bike down the Aba-Huab": 3,
    "Walking with a Bayei tracker": 3,
    "Aufstieg zum roten Felsrand": 2,
    "Daan Viljoen sunrise ridge walk": 2,
    "Five days through Africa's great gorge": 2,
    "Fünf Tage durch den Fischfluss-Canyon": 2,
    # you get through it by clambering, under nobody's power but your own
    "Inside the Sesriem gorge": 2,
    # four hours uphill on foot through wild country, vultures overhead
    "Sandstone climb to the plateau rim": 2,
    "Two days on the Olive Trail and beyond": 2,
    "Zwei Tage im Naukluftgebirge": 2,
    # the animal does the work, as with the Kalahari horses already listed
    "Camel ride beside the Swakop riverbed": 1,
    # you paddle, in a guided raft
    "Grade five on the Batoka Gorge": 1,
    "Horse ride on a Kalahari farm": 1,
    "Im Einbaum durchs Delta": 1,
    "Kunene whitewater rafting": 1,
    # someone else poles, and then you walk
    "Poled through the channels by mokoro": 1,
    # your own power entirely, though a technical route is climbing rather than exploring
    "Roped ascent of Namibia's Matterhorn": 1,
    "Seilschaft am Matterhorn Namibias": 1,
    # you drive the quad yourself, which is at least not a safari car
    "Sleeping out on the salt": 1,
    # all your own effort, but a mountain above a city is not the wild
    "Table Mountain by Platteklip Gorge": 1,
    "Wildwasser auf dem Kunene": 1,
    "Zu Fuß auf den Tafelberg": 1,
}

CURATED_COVERAGE = [
    (
        "en",
        "vague",
        "easy evening with a drink watching the sun go down over huge scenery",
        _EVENING,
    ),
    (
        "de",
        "vague",
        "entspannter Abend mit einem Drink, während die Sonne über einer riesigen Landschaft untergeht",
        _EVENING,
    ),
    (
        "en",
        "vague",
        "strenuous walk uphill to a big view, I want to feel it in my legs",
        _CLIMB,
    ),
    (
        "de",
        "vague",
        "anstrengender Aufstieg zu Fuß mit weitem Ausblick, ich will es in den Beinen spüren",
        _CLIMB,
    ),
    (
        "en",
        "vague",
        "weird rock shapes and geology without anything physically demanding",
        _ROCKS,
    ),
    (
        "de",
        "vague",
        "bizarre Felsformationen und Geologie, ohne dass es körperlich anstrengend wird",
        _ROCKS,
    ),
    (
        "en",
        "vague",
        "something truly terrifying in the water, so I have a story to tell",
        _WATER,
    ),
    (
        "de",
        "vague",
        "etwas richtig Furchteinflößendes im Wasser, damit ich etwas zu erzählen habe",
        _WATER,
    ),
    (
        "en",
        "vague",
        "all-day game drive through wide open country",
        _GAME_DRIVE,
    ),
    (
        "de",
        "vague",
        "Pirschfahrt den ganzen Tag durch offene, weite Landschaft",
        _GAME_DRIVE,
    ),
    (
        "en",
        "vague",
        "exploring the wild under my own power, not sitting in a safari car",
        _OWN_POWER,
    ),
    (
        "de",
        "vague",
        "die Wildnis aus eigener Kraft erkunden, statt im Safariwagen zu sitzen",
        _OWN_POWER,
    ),
]
