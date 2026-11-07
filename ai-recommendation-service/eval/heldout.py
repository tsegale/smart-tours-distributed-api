"""Held-out queries for testing lexical-precision candidates. Signed off by the owner on 2026-11-07, before any system
ran on them.

The candidates (a lexical weight that drops on long queries, one stemmer per query, a minimum share of matching terms)
came out of looking at the other sets, so they can't be judged on those alone. These 24 queries are the fresh test.

How these labels were made (2026-11-07):
1. Claude wrote the queries and proposed every judgment from the 105 tour descriptions, and committed them before any
   system had been run on them. Neither the queries nor the labels change after seeing a result.
2. This isn't a blind test of the author: Claude already knew how the candidates work and which queries hurt lexical
   search. The queries aim for a natural mix instead of targeting any candidate: seven pairs of six words or more in
   English, five shorter, some with the tours' own words and some without.
3. The owner reviewed the labels and accepted every proposed judgment unchanged, on 2026-11-07. Only then did any
   system run on them (eval/lexical_precision.py).

Calls the owner was asked to check, and kept:
- "hippos and crocodiles": no tour has both. The photo boat gets 3 because hippos surface a few metres away, the Kunene
  rafting 2 because the crocodiles stay on the far bank, and the Bayei walk 1 because a hippo path is only a warning.
- "fishing": a single relevant tour, by design. It is one of three tours no signed-off set labels, as are the photo boat
  and the stone church, which also appear here.
- "indoors and cool": the copper museum is the only tour that says air-conditioned, and the stone church's museum is 2
  on its thick walls. The other museums and the cafe are 1, since being indoors is implied rather than stated.
- "street food": oysters, bakery cake and family lunches are 1, as local food that isn't from a street or a market.
- "protecting wildlife": 3 only where the description says the money funds the work; 2 for visiting a protection effort.

Grades as in curated.py: 3 what was asked for, 2 a close match, 1 related, anything unlisted 0. The German of each pair
is a translation of the English, so a pair shares one dict.
"""

NEEDS_REVIEW = False
REVIEWED_ON = "2026-11-07"

_BIRDS = {
    "Lagoon flamingos and oyster farm": 3,
    "Flamingos, Austern und Katamaran": 3,
    # a colony of penguins past the guano platforms
    "Sailing to Halifax Island penguins": 2,
    # kingfishers dive beside the boat, among other things
    "Photo boat with swivel seats": 2,
    # raptors on every dead tree and a sociable weaver nest, though the lions lead
    "Black-maned lions of the Auob": 1,
    "Schwarzmähnenlöwen im Auob-Tal": 1,
    # a picnic among seabirds at the far end
    "Sandwich Harbour by four-by-four": 1,
    # Cape vultures circle the cliffs
    "Sandstone climb to the plateau rim": 1,
    "Aufstieg zum roten Felsrand": 1,
}

_FISHING = {
    "Tigerfish on the Zambezi": 3,
}

_BOAT_PHOTOS = {
    # built for cameras, at water height
    "Photo boat with swivel seats": 3,
    # a low boat, close enough to hear them breathe; nothing says it is for photographers
    "Elephants at the river's edge": 2,
    "Elefanten beim Durchschwimmen": 2,
    # birds and seals around a larger boat, not at water level
    "Lagoon flamingos and oyster farm": 1,
    "Flamingos, Austern und Katamaran": 1,
    # low in a dugout, though the walk is where the animals are looked for
    "Poled through the channels by mokoro": 1,
    "Im Einbaum durchs Delta": 1,
}

_COLONIAL = {
    "Art Nouveau houses of a diamond town": 3,
    "Jugendstil am Rand der Wüste": 3,
    "Deutsche Spuren am Atlantik": 3,
    "Stone church and a railway past": 3,
    # the sandstone church of the colonial era, alongside a modern museum
    "Independence museum and Christuskirche": 2,
    "Windhoek zwischen Vergangenheit und Gegenwart": 2,
    "The old German fort at Namutoni": 2,
    # ruins of the diamond era: buildings, but abandoned rather than colonial sights
    "Ghost town drowned in sand": 1,
    "Geisterstadt im Sand": 1,
    "The forbidden diamond zone": 1,
}

_STREET_FOOD = {
    "Katutura market and kapana grills": 3,
    "Katutura mit Einheimischen entdecken": 3,
    # omajova mushrooms and a neighbourhood bar
    "Mondesa township with a resident guide": 2,
    "Woodcarvers of the Kavango": 1,
    "Living museum of the Damara": 1,
    "Bo-Kaap and District Six stories": 1,
    "Windhoek breweries and German bakers": 1,
    "Lagoon flamingos and oyster farm": 1,
    "Flamingos, Austern und Katamaran": 1,
}

_FAMILY = {
    # says small children and grandparents in as many words
    "Organ Pipes and Burnt Mountain": 3,
    "Camel ride beside the Swakop riverbed": 2,
    # children from eight only, so not for small ones
    "Horse ride on a Kalahari farm": 1,
    "Die Kleinen Fünf der Wüste": 2,
    "A hundred thousand fur seals": 2,
    "Hunderttausend Pelzrobben": 2,
    "Little Five in the living desert": 1,
    "Thermal springs and a slow soak": 1,
    "Cheetah conservation behind the scenes": 1,
    "Geparden hautnah": 1,
    "Namaqualand in flower": 1,
}

_ADRENALINE = {
    "Tandem skydive above the Atlantic": 3,
    "Tandemsprung über Wüste und Meer": 3,
    "Bungee from the old bridge": 2,
    "Sandwich Harbour by four-by-four": 2,
    # a lifeguard holds your ankles
    "Swimming at the edge of the falls": 2,
    "Sandboarding Dune 7": 1,
    "Cage diving at Gansbaai day trip": 1,
    "Kunene whitewater rafting": 1,
    "Wildwasser auf dem Kunene": 1,
    "Helicopter with the doors off": 1,
}

_BELIEFS = {
    "Himba homestead with a Himba guide": 3,
    "Besuch bei einer Himba-Familie": 3,
    # shamanic meanings, explained from images rather than living practice
    "A UNESCO gallery in sandstone": 2,
    "Felsgalerie der San": 2,
    "Living museum of the Damara": 1,
    "Ovatwa hunters' forest stories": 1,
}

_INDOORS = {
    "Copper town museum and minerals": 3,
    "Stone church and a railway past": 2,
    "Independence museum and Christuskirche": 1,
    "Windhoek zwischen Vergangenheit und Gegenwart": 1,
    "Windhoek breweries and German bakers": 1,
}

_CONSERVATION = {
    # proceeds fund the programme
    "Cheetah conservation behind the scenes": 3,
    "Geparden hautnah": 3,
    "Painted dogs in teak woodland": 2,
    "Rhino breeding on the sandstone top": 2,
    "Desert elephants of the Huab": 1,
    "Wüstenelefanten im Huab-Tal": 1,
}

_HIPPOS = {
    "Photo boat with swivel seats": 3,
    "Kunene whitewater rafting": 2,
    "Wildwasser auf dem Kunene": 2,
    "Walking with a Bayei tracker": 1,
}

_QUIET = {
    "Deadvlei before the crowds": 3,
    "Frühmorgens im Deadvlei": 3,
    # dawn, and drifting in silence
    "Balloon over the Namib at dawn": 2,
    # very lightly visited, but nothing says morning
    "Mahango and the Bwabwata floodplain": 2,
    "Poled through the channels by mokoro": 1,
    "Im Einbaum durchs Delta": 1,
    "Masubia village on the floodplain": 1,
    "Sleeping out on the salt": 1,
}

HELDOUT = [
    ("en", "heldout", "where can I see lots of birds", _BIRDS),
    ("de", "heldout", "wo kann ich viele Vögel beobachten", _BIRDS),
    ("en", "heldout", "fishing on a big river", _FISHING),
    ("de", "heldout", "Angeln auf einem großen Fluss", _FISHING),
    ("en", "heldout", "I want to photograph animals from a boat at water level", _BOAT_PHOTOS),
    ("de", "heldout", "ich möchte Tiere vom Boot aus auf Wasserhöhe fotografieren", _BOAT_PHOTOS),
    ("en", "heldout", "old colonial buildings and churches", _COLONIAL),
    ("de", "heldout", "alte Kolonialbauten und Kirchen", _COLONIAL),
    ("en", "heldout", "try local street food", _STREET_FOOD),
    ("de", "heldout", "einheimisches Essen auf dem Markt probieren", _STREET_FOOD),
    ("en", "heldout", "we are travelling with small children and grandparents, nothing too hard", _FAMILY),
    ("de", "heldout", "wir reisen mit kleinen Kindern und Großeltern, nichts zu Anstrengendes", _FAMILY),
    ("en", "heldout", "a real adrenaline rush where a professional takes care of everything", _ADRENALINE),
    ("de", "heldout", "ein echter Adrenalinkick, bei dem sich ein Profi um alles kümmert", _ADRENALINE),
    ("en", "heldout", "traditional beliefs and rituals", _BELIEFS),
    ("de", "heldout", "traditionelle Glaubensvorstellungen und Rituale", _BELIEFS),
    ("en", "heldout", "somewhere indoors and cool to get out of the midday heat", _INDOORS),
    ("de", "heldout", "irgendwo drinnen und kühl, um der Mittagshitze zu entkommen", _INDOORS),
    ("en", "heldout", "my money should go towards protecting wildlife", _CONSERVATION),
    ("de", "heldout", "mein Geld soll dem Schutz der Tierwelt zugutekommen", _CONSERVATION),
    ("en", "heldout", "hippos and crocodiles", _HIPPOS),
    ("de", "heldout", "Flusspferde und Krokodile", _HIPPOS),
    ("en", "heldout", "a quiet morning away from other tourists", _QUIET),
    ("de", "heldout", "ein ruhiger Morgen abseits anderer Touristen", _QUIET),
]
