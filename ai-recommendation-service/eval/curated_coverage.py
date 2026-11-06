"""Queries aimed at written tours the reviewed set never labels. NOT REVIEWED: nothing is decided on these.

The owner signed off the 50 queries in curated.py, and this file exists so that they stay signed off. They label 82 of
the 105 written tours, so for the other 23 a system could return anything without the evaluation noticing.

Claude wrote these queries and proposed every judgment, from the tour descriptions and before seeing any retrieval
result. They are reported as their own set, and are not part of the CI baseline or of any comparison a decision
rests on.

They reach 20 of the 23. Six natural pairs can't take in everything, and bending a query to catch one more tour would
stop it being a query someone would type. Still unlabelled by anything: "Tigerfish on the Zambezi", "Photo boat with
swivel seats" and "Stone church and a railway past".

Grades as in curated.py: 3 what was asked for, 2 a close match, 1 related, anything unlisted 0. Twins carry the same
labels.
"""

NEEDS_REVIEW = True

_EVENING = {
    "Canyon rim at sunset": 3,
    "Sundowner am Canyonrand": 3,
    "Kalahari red dunes by sundowner truck": 3,
    "Baobabs above the falls": 2,
    "Natural arches at golden hour": 1,
    "The quiver tree forest": 1,
    "Köcherbaumwald bei Sonnenuntergang": 1,
    "Organ Pipes and Burnt Mountain": 1,  # glows at dusk, but nothing says you stay for it with a drink
}

# The Waterberg climb is 2: uphill with a view, but "a steady hour" isn't the effort asked for.
_CLIMB = {
    "Table Mountain by Platteklip Gorge": 3,
    "Zu Fuß auf den Tafelberg": 3,
    "Sandstone climb to the plateau rim": 2,
    "Aufstieg zum roten Felsrand": 2,
    "Daan Viljoen sunrise ridge walk": 1,
    "Two days on the Olive Trail and beyond": 1,
    "Zwei Tage im Naukluftgebirge": 1,
    "Five days through Africa's great gorge": 1,
    "Fünf Tage durch den Fischfluss-Canyon": 1,
    "Roped ascent of Namibia's Matterhorn": 1,
    "Seilschaft am Matterhorn Namibias": 1,
}

# Sesriem is 2: the geology is the point, but you clamber down to see it.
_ROCKS = {
    "Organ Pipes and Burnt Mountain": 3,
    "Natural arches at golden hour": 3,
    "The quiver tree forest": 2,  # for the Giant's Playground beside it
    "Köcherbaumwald bei Sonnenuntergang": 2,
    "Tundavala's great escarpment": 2,
    "Inside the Sesriem gorge": 2,
    "Canyon rim at sunset": 1,
    "Sundowner am Canyonrand": 1,
}

_WATER = {
    "Swimming at the edge of the falls": 3,
    "Cage diving at Gansbaai day trip": 3,
    "Grade five on the Batoka Gorge": 2,
    "Kunene whitewater rafting": 1,
    "Wildwasser auf dem Kunene": 1,
    "Bungee from the old bridge": 1,  # frightening, and over water rather than in it
}

_GAME_DRIVE = {
    "Full day across the great white pan": 3,
    "Ganztägig rund um die Salzpfanne": 3,
    "Mahango and the Bwabwata floodplain": 2,  # the right kind of place, though it never says a full day
    "Black-maned lions of the Auob": 2,
    "Schwarzmähnenlöwen im Auob-Tal": 2,
    "Desert elephants of the Huab": 1,
    "Wüstenelefanten im Huab-Tal": 1,
    "Painted dogs in teak woodland": 1,
    "Rhino breeding on the sandstone top": 1,
    "Night drive with a spotlight": 1,
}

_OWN_POWER = {
    "Walking with a Bayei tracker": 3,
    "Bush walk on a private reserve": 3,
    "Mountain bike down the Aba-Huab": 3,
    "Two days on the Olive Trail and beyond": 2,
    "Zwei Tage im Naukluftgebirge": 2,
    "Five days through Africa's great gorge": 2,
    "Fünf Tage durch den Fischfluss-Canyon": 2,
    "Daan Viljoen sunrise ridge walk": 2,
    "Poled through the channels by mokoro": 1,  # someone else poles, and then you walk
    "Im Einbaum durchs Delta": 1,
    "Horse ride on a Kalahari farm": 1,
}

CURATED_COVERAGE = [
    ("en", "vague", "easy evening with a drink watching the sun go down over huge scenery", _EVENING),
    (
        "de",
        "vague",
        "entspannter Abend mit einem Drink, während die Sonne über einer riesigen Landschaft untergeht",
        _EVENING,
    ),
    ("en", "vague", "strenuous walk uphill to a big view, I want to feel it in my legs", _CLIMB),
    ("de", "vague", "anstrengender Aufstieg zu Fuß mit weitem Ausblick, ich will es in den Beinen spüren", _CLIMB),
    ("en", "vague", "weird rock shapes and geology without anything physically demanding", _ROCKS),
    ("de", "vague", "bizarre Felsformationen und Geologie, ohne dass es körperlich anstrengend wird", _ROCKS),
    ("en", "vague", "something truly terrifying in the water, so I have a story to tell", _WATER),
    ("de", "vague", "etwas richtig Furchteinflößendes im Wasser, damit ich etwas zu erzählen habe", _WATER),
    ("en", "vague", "all-day game drive through wide open country", _GAME_DRIVE),
    ("de", "vague", "Pirschfahrt den ganzen Tag durch offene, weite Landschaft", _GAME_DRIVE),
    ("en", "vague", "exploring the wild under my own power, not sitting in a safari car", _OWN_POWER),
    ("de", "vague", "die Wildnis aus eigener Kraft erkunden, statt im Safariwagen zu sitzen", _OWN_POWER),
]
