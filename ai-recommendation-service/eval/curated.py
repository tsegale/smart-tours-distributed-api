"""Curated queries over the individually written tours, with graded relevance. These decide modelling choices.

Every judgment here was proposed by Claude and is `needs_review`: they aren't human labels until the owner has
reviewed them. Grades: 3 what was asked for, 2 a close match, 1 related. Anything unlisted is 0. Tours are named by
title, in both languages where a tour exists in both: a German query can rightly be answered by an English tour, which
is what multilingual models are tested on.

Written to avoid the tours' own wording wherever a real person wouldn't use it.
"""

NEEDS_REVIEW = True

CURATED = [
    (
        "en",
        "vague",
        "abandoned town swallowed by the desert",
        {
            "Ghost town drowned in sand": 3,
            "Geisterstadt im Sand": 3,
            "The forbidden diamond zone": 2,
            "Art Nouveau houses of a diamond town": 1,
            "Jugendstil am Rand der Wüste": 1,
        },
    ),
    (
        "de",
        "vague",
        "verlassene Stadt in der Wüste",
        {
            "Ghost town drowned in sand": 3,
            "Geisterstadt im Sand": 3,
            "The forbidden diamond zone": 2,
            "Art Nouveau houses of a diamond town": 1,
            "Jugendstil am Rand der Wüste": 1,
        },
    ),
    (
        "en",
        "vague",
        "see dead trees on a white clay floor",
        {"Deadvlei before the crowds": 3, "Frühmorgens im Deadvlei": 3},
    ),
    (
        "de",
        "vague",
        "tote Bäume auf weißem Boden fotografieren",
        {
            "Deadvlei before the crowds": 3,
            "Frühmorgens im Deadvlei": 3,
            "Ghost town drowned in sand": 1,
            "Geisterstadt im Sand": 1,
        },
    ),
    (
        "en",
        "vague",
        "jump out of a plane",
        {
            "Tandem skydive above the Atlantic": 3,
            "Tandemsprung über Wüste und Meer": 3,
            "Bungee from the old bridge": 1,
        },
    ),
    (
        "de",
        "vague",
        "aus dem Flugzeug springen",
        {
            "Tandem skydive above the Atlantic": 3,
            "Tandemsprung über Wüste und Meer": 3,
            "Bungee from the old bridge": 1,
        },
    ),
    (
        "en",
        "vague",
        "tiny desert reptiles and insects",
        {"Little Five in the living desert": 3, "Die Kleinen Fünf der Wüste": 3},
    ),
    (
        "de",
        "vague",
        "kleine Wüstentiere wie Schlangen und Eidechsen",
        {"Little Five in the living desert": 3, "Die Kleinen Fünf der Wüste": 3},
    ),
    (
        "en",
        "vague",
        "something for wine lovers",
        {
            "Orange River vineyards and raisins": 3,
            "Lagoon flamingos and oyster farm": 1,
            "Flamingos, Austern und Katamaran": 1,
        },
    ),
    (
        "en",
        "vague",
        "taste beer and cake from the colonial era",
        {"Windhoek breweries and German bakers": 3, "Deutsche Spuren am Atlantik": 1},
    ),
    (
        "en",
        "vague",
        "learn what really happened during the genocide of the Herero",
        {
            "The battlefield and the graves": 3,
            "Gedenken am Waterberg": 3,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
        },
    ),
    (
        "de",
        "vague",
        "Völkermord an den Herero verstehen",
        {
            "The battlefield and the graves": 3,
            "Gedenken am Waterberg": 3,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
            "Independence museum and Christuskirche": 1,
        },
    ),
    (
        "en",
        "vague",
        "apartheid history told by people who lived it",
        {
            "Bo-Kaap and District Six stories": 3,
            "Katutura market and kapana grills": 3,
            "Katutura mit Einheimischen entdecken": 3,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
        },
    ),
    (
        "en",
        "vague",
        "extreme rafting with big rapids",
        {"Grade five on the Batoka Gorge": 3, "Kunene whitewater rafting": 3, "Wildwasser auf dem Kunene": 3},
    ),
    (
        "de",
        "vague",
        "Rafting auf wildem Fluss",
        {"Grade five on the Batoka Gorge": 3, "Kunene whitewater rafting": 3, "Wildwasser auf dem Kunene": 3},
    ),
    ("en", "vague", "relaxing hot bath after travelling", {"Thermal springs and a slow soak": 3}),
    (
        "en",
        "vague",
        "technical rock climbing with ropes",
        {"Roped ascent of Namibia's Matterhorn": 3, "Seilschaft am Matterhorn Namibias": 3},
    ),
    (
        "de",
        "vague",
        "Klettern mit Seil am Fels",
        {"Roped ascent of Namibia's Matterhorn": 3, "Seilschaft am Matterhorn Namibias": 3},
    ),
    (
        "en",
        "vague",
        "shipwrecks and whale bones",
        {"Fly-in shipwreck expedition": 3, "Schiffswracks der Skelettküste": 3},
    ),
    (
        "de",
        "vague",
        "gestrandete Schiffe an einsamer Küste",
        {"Fly-in shipwreck expedition": 3, "Schiffswracks der Skelettküste": 3},
    ),
    ("en", "vague", "penguins", {"Sailing to Halifax Island penguins": 3}),
    (
        "en",
        "vague",
        "enormous colony of seals, easy to walk",
        {"A hundred thousand fur seals": 3, "Hunderttausend Pelzrobben": 3},
    ),
    (
        "de",
        "vague",
        "Robbenkolonie auf Holzstegen besuchen",
        {"A hundred thousand fur seals": 3, "Hunderttausend Pelzrobben": 3},
    ),
    (
        "en",
        "vague",
        "see fast cats and how they're protected",
        {"Cheetah conservation behind the scenes": 3, "Geparden hautnah": 3},
    ),
    (
        "de",
        "vague",
        "Schutzprojekt für Raubkatzen",
        {"Cheetah conservation behind the scenes": 3, "Geparden hautnah": 3, "Black-maned lions of the Auob": 1},
    ),
    ("en", "vague", "floating over the desert in the morning, no effort", {"Balloon over the Namib at dawn": 3}),
    (
        "en",
        "vague",
        "ride an animal through the dunes",
        {"Camel ride beside the Swakop riverbed": 3, "Horse ride on a Kalahari farm": 3},
    ),
    ("en", "vague", "slide down sand on a board", {"Sandboarding Dune 7": 3}),
    (
        "en",
        "vague",
        "traditional canoe through reeds",
        {"Poled through the channels by mokoro": 3, "Im Einbaum durchs Delta": 3},
    ),
    (
        "de",
        "vague",
        "im traditionellen Boot durch das Schilf",
        {"Poled through the channels by mokoro": 3, "Im Einbaum durchs Delta": 3},
    ),
    ("en", "vague", "wildflowers in bloom", {"Namaqualand in flower": 3}),
    (
        "en",
        "vague",
        "strange trees at sunset, not much walking",
        {"The quiver tree forest": 3, "Köcherbaumwald bei Sonnenuntergang": 3, "Baobabs above the falls": 1},
    ),
    (
        "de",
        "vague",
        "seltsame Bäume im Abendlicht",
        {
            "The quiver tree forest": 3,
            "Köcherbaumwald bei Sonnenuntergang": 3,
            "Baobabs above the falls": 1,
            "Frühmorgens im Deadvlei": 1,
        },
    ),
    (
        "en",
        "vague",
        "elephants swimming across a river",
        {"Elephants at the river's edge": 3, "Elefanten beim Durchschwimmen": 3},
    ),
    (
        "de",
        "vague",
        "Elefanten im Wasser sehen",
        {
            "Elephants at the river's edge": 3,
            "Elefanten beim Durchschwimmen": 3,
            "Desert elephants of the Huab": 1,
            "Wüstenelefanten im Huab-Tal": 1,
        },
    ),
    ("en", "vague", "see endangered wild dogs", {"Painted dogs in teak woodland": 3}),
    (
        "en",
        "vague",
        "the waterfall, but I use a wheelchair",
        {"The smoke that thunders, step by step": 3, "Der Rauch, der donnert": 3},
    ),
    (
        "de",
        "vague",
        "Wasserfall mit dem Rollstuhl besuchen",
        {"The smoke that thunders, step by step": 3, "Der Rauch, der donnert": 3},
    ),
    (
        "en",
        "vague",
        "cross a very rough road by four by four",
        {"Van Zyl's Pass in a convoy": 3, "Sandwich Harbour by four-by-four": 1},
    ),
    (
        "en",
        "vague",
        "hand-made crafts you can try yourself",
        {"Woodcarvers of the Kavango": 3, "Community-run basket weaving morning": 3},
    ),
    ("en", "vague", "lying under the stars on a salt flat", {"Sleeping out on the salt": 3}),
    ("en", "vague", "rare minerals and an old mine", {"Copper town museum and minerals": 3}),
    (
        "en",
        "vague",
        "ancient rock engravings",
        {"A UNESCO gallery in sandstone": 3, "Felsgalerie der San": 3, "Bushman's Paradise paintings": 2},
    ),
    (
        "de",
        "vague",
        "uralte Bilder im Fels",
        {"A UNESCO gallery in sandstone": 3, "Felsgalerie der San": 3, "Bushman's Paradise paintings": 2},
    ),
    (
        "de",
        "vague",
        "Himba-Kultur kennenlernen",
        {
            "Himba homestead with a Himba guide": 3,
            "Besuch bei einer Himba-Familie": 3,
            "Ovatwa hunters' forest stories": 1,
        },
    ),
    (
        "en",
        "vague",
        "long multi-day backpacking through a gorge",
        {
            "Five days through Africa's great gorge": 3,
            "Fünf Tage durch den Fischfluss-Canyon": 3,
            "Two days on the Olive Trail and beyond": 2,
            "Zwei Tage im Naukluftgebirge": 2,
        },
    ),
    (
        "de",
        "vague",
        "mehrtägige Trekkingtour mit Rucksack",
        {
            "Five days through Africa's great gorge": 3,
            "Fünf Tage durch den Fischfluss-Canyon": 3,
            "Two days on the Olive Trail and beyond": 2,
            "Zwei Tage im Naukluftgebirge": 2,
        },
    ),
    ("en", "vague", "whitewashed fort with a violent past", {"The old German fort at Namutoni": 3}),
    (
        "en",
        "vague",
        "rhinos at night while sitting down",
        {"Floodlit waterhole after dark": 3, "Nashörner am beleuchteten Wasserloch": 3},
    ),
    (
        "de",
        "vague",
        "abends am Wasserloch sitzen und Nashörner sehen",
        {"Floodlit waterhole after dark": 3, "Nashörner am beleuchteten Wasserloch": 3},
    ),
]
