"""Curated queries over the individually written tours, with graded relevance. These decide modelling choices.

How these labels were made (2026-11-06):
1. Claude proposed every judgment.
2. A second Claude pass checked each query against all 105 tour descriptions: 31 grade changes on 19 queries,
   122 labels to 142. Each changed query carries a comment with the reasoning.
3. The project owner decided the contested calls himself and signed off on the set: Q4 (Kolmanskop removed),
   Q13 (Mondesa added at 2), Q14 (Kunene lowered to 2 for "extreme"), Q24/25 (lions related only for the broader
   German "Raubkatzen"), Q37/38 (inaccessible waterfalls stay 0 so the pair tests the wheelchair constraint) and
   Q47 (Naukluft raised to 3 for the German query, which doesn't ask for a gorge).

Grades: 3 what was asked for, 2 a close match, 1 related. Anything unlisted is 0. Tours are named by title, in both
languages where a tour exists in both: a German query can rightly be answered by an English tour, which is what
multilingual models are tested on. Where an EN and a DE query are twins, their labels match unless the wording
itself differs; the per-query comments record every such call.

Written to avoid the tours' own wording wherever a real person wouldn't use it.
"""

NEEDS_REVIEW = False
REVIEWED_ON = "2026-11-06"

CURATED = [
    (
        "en",
        "vague",
        "abandoned town swallowed by the desert",
        {
            "Geisterstadt im Sand": 3,
            "Ghost town drowned in sand": 3,
            "The forbidden diamond zone": 2,
            "Art Nouveau houses of a diamond town": 1,
            "Jugendstil am Rand der W\u00fcste": 1,
        },
    ),
    (
        "de",
        "vague",
        "verlassene Stadt in der W\u00fcste",
        {
            "Geisterstadt im Sand": 3,
            "Ghost town drowned in sand": 3,
            "The forbidden diamond zone": 2,
            "Art Nouveau houses of a diamond town": 1,
            "Jugendstil am Rand der W\u00fcste": 1,
        },
    ),
    (
        "en",
        "vague",
        "see dead trees on a white clay floor",
        {
            "Deadvlei before the crowds": 3,
            "Fr\u00fchmorgens im Deadvlei": 3,
        },
    ),
    # Kolmanskop dropped: no trees and no pale clay floor. It was in only because the query says
    # fotografieren; the EN twin of this query never had it, and the pair must match.
    (
        "de",
        "vague",
        "tote B\u00e4ume auf wei\u00dfem Boden fotografieren",
        {
            "Deadvlei before the crowds": 3,
            "Fr\u00fchmorgens im Deadvlei": 3,
        },
    ),
    (
        "en",
        "vague",
        "jump out of a plane",
        {
            "Tandem skydive above the Atlantic": 3,
            "Tandemsprung \u00fcber W\u00fcste und Meer": 3,
            "Bungee from the old bridge": 1,
        },
    ),
    (
        "de",
        "vague",
        "aus dem Flugzeug springen",
        {
            "Tandem skydive above the Atlantic": 3,
            "Tandemsprung \u00fcber W\u00fcste und Meer": 3,
            "Bungee from the old bridge": 1,
        },
    ),
    # Added the Etosha bush walk: dung beetles and termites are the asked-for small creatures,
    # though the setting is bush, not desert. Left off the DE twin, which asks for snakes and
    # lizards.
    (
        "en",
        "vague",
        "tiny desert reptiles and insects",
        {
            "Die Kleinen F\u00fcnf der W\u00fcste": 3,
            "Little Five in the living desert": 3,
            "Bush walk on a private reserve": 1,
        },
    ),
    (
        "de",
        "vague",
        "kleine W\u00fcstentiere wie Schlangen und Eidechsen",
        {
            "Die Kleinen F\u00fcnf der W\u00fcste": 3,
            "Little Five in the living desert": 3,
        },
    ),
    # Added the brewery tour as the only other drink-tasting outing in the catalogue.
    (
        "en",
        "vague",
        "something for wine lovers",
        {
            "Orange River vineyards and raisins": 3,
            "Flamingos, Austern und Katamaran": 1,
            "Lagoon flamingos and oyster farm": 1,
            "Windhoek breweries and German bakers": 1,
        },
    ),
    # Raised to 2: colonial-era buildings plus Kaffee und Kuchen at Cafe Anton is half the query,
    # not merely related.
    (
        "en",
        "vague",
        "taste beer and cake from the colonial era",
        {
            "Windhoek breweries and German bakers": 3,
            "Deutsche Spuren am Atlantik": 2,
        },
    ),
    # Added the Windhoek monuments tour, which the DE twin of this query already graded 1.
    (
        "en",
        "vague",
        "learn what really happened during the genocide of the Herero",
        {
            "Gedenken am Waterberg": 3,
            "The battlefield and the graves": 3,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
            "Independence museum and Christuskirche": 1,
        },
    ),
    (
        "de",
        "vague",
        "V\u00f6lkermord an den Herero verstehen",
        {
            "Gedenken am Waterberg": 3,
            "The battlefield and the graves": 3,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
            "Independence museum and Christuskirche": 1,
        },
    ),
    # Mondesa was missing: a host who grew up on the streets she walks you round is exactly the
    # 'people who lived it' half, with apartheid history less to the fore. Salt works covers the
    # Walvis Bay enclave staying South African until 1994.
    (
        "en",
        "vague",
        "apartheid history told by people who lived it",
        {
            "Bo-Kaap and District Six stories": 3,
            "Katutura market and kapana grills": 3,
            "Katutura mit Einheimischen entdecken": 3,
            "Mondesa township with a resident guide": 2,
            "Windhoek zwischen Vergangenheit und Gegenwart": 2,
            "Salt works and a port at work": 1,
        },
    ),
    # Lowered to 2. Kunene is grade three with safety kayakers; only Batoka Gorge is grade five.
    # If both sit at 3, the eval cannot tell a system that honours 'extreme' from one that ignores
    # it. The DE twin asks only for a wild river, so it keeps Kunene at 3.
    (
        "en",
        "vague",
        "extreme rafting with big rapids",
        {
            "Grade five on the Batoka Gorge": 3,
            "Kunene whitewater rafting": 2,
            "Wildwasser auf dem Kunene": 2,
        },
    ),
    (
        "de",
        "vague",
        "Rafting auf wildem Fluss",
        {
            "Grade five on the Batoka Gorge": 3,
            "Kunene whitewater rafting": 3,
            "Wildwasser auf dem Kunene": 3,
        },
    ),
    (
        "en",
        "vague",
        "relaxing hot bath after travelling",
        {
            "Thermal springs and a slow soak": 3,
        },
    ),
    (
        "en",
        "vague",
        "technical rock climbing with ropes",
        {
            "Roped ascent of Namibia's Matterhorn": 3,
            "Seilschaft am Matterhorn Namibias": 3,
        },
    ),
    (
        "de",
        "vague",
        "Klettern mit Seil am Fels",
        {
            "Roped ascent of Namibia's Matterhorn": 3,
            "Seilschaft am Matterhorn Namibias": 3,
        },
    ),
    (
        "en",
        "vague",
        "shipwrecks and whale bones",
        {
            "Fly-in shipwreck expedition": 3,
            "Schiffswracks der Skelettk\u00fcste": 3,
        },
    ),
    (
        "de",
        "vague",
        "gestrandete Schiffe an einsamer K\u00fcste",
        {
            "Fly-in shipwreck expedition": 3,
            "Schiffswracks der Skelettk\u00fcste": 3,
        },
    ),
    (
        "en",
        "vague",
        "penguins",
        {
            "Sailing to Halifax Island penguins": 3,
        },
    ),
    # Added both Skeleton Coast tours for their roaring seal colonies. Kept off the DE twin, which
    # asks specifically for boardwalks.
    (
        "en",
        "vague",
        "enormous colony of seals, easy to walk",
        {
            "A hundred thousand fur seals": 3,
            "Hunderttausend Pelzrobben": 3,
            "Fly-in shipwreck expedition": 1,
            "Schiffswracks der Skelettk\u00fcste": 1,
        },
    ),
    (
        "de",
        "vague",
        "Robbenkolonie auf Holzstegen besuchen",
        {
            "A hundred thousand fur seals": 3,
            "Hunderttausend Pelzrobben": 3,
        },
    ),
    (
        "en",
        "vague",
        "see fast cats and how they're protected",
        {
            "Cheetah conservation behind the scenes": 3,
            "Geparden hautnah": 3,
        },
    ),
    (
        "de",
        "vague",
        "Schutzprojekt f\u00fcr Raubkatzen",
        {
            "Cheetah conservation behind the scenes": 3,
            "Geparden hautnah": 3,
            "Black-maned lions of the Auob": 1,
        },
    ),
    # Added the doors-off helicopter: same effortless flight, wrong landscape.
    (
        "en",
        "vague",
        "floating over the desert in the morning, no effort",
        {
            "Balloon over the Namib at dawn": 3,
            "Helicopter with the doors off": 1,
        },
    ),
    (
        "en",
        "vague",
        "ride an animal through the dunes",
        {
            "Camel ride beside the Swakop riverbed": 3,
            "Horse ride on a Kalahari farm": 3,
        },
    ),
    (
        "en",
        "vague",
        "slide down sand on a board",
        {
            "Sandboarding Dune 7": 3,
        },
    ),
    # Added the woodcarvers, who shape mokoro canoes by the river. Related, not a ride.
    (
        "en",
        "vague",
        "traditional canoe through reeds",
        {
            "Im Einbaum durchs Delta": 3,
            "Poled through the channels by mokoro": 3,
            "Woodcarvers of the Kavango": 1,
        },
    ),
    # Added the woodcarvers, to match the EN twin of this query.
    (
        "de",
        "vague",
        "im traditionellen Boot durch das Schilf",
        {
            "Im Einbaum durchs Delta": 3,
            "Poled through the channels by mokoro": 3,
            "Woodcarvers of the Kavango": 1,
        },
    ),
    (
        "en",
        "vague",
        "wildflowers in bloom",
        {
            "Namaqualand in flower": 3,
        },
    ),
    # Baobabs raised to 2: ancient baobabs on an easy sunset rim walk is nearly the whole query,
    # only the place is unexpected. Deadvlei added at 1, as the DE twin already had it.
    (
        "en",
        "vague",
        "strange trees at sunset, not much walking",
        {
            "K\u00f6cherbaumwald bei Sonnenuntergang": 3,
            "The quiver tree forest": 3,
            "Baobabs above the falls": 2,
            "Deadvlei before the crowds": 1,
            "Fr\u00fchmorgens im Deadvlei": 1,
        },
    ),
    # Baobabs raised to 2, and the EN Deadvlei added beside the DE one it already listed.
    (
        "de",
        "vague",
        "seltsame B\u00e4ume im Abendlicht",
        {
            "K\u00f6cherbaumwald bei Sonnenuntergang": 3,
            "The quiver tree forest": 3,
            "Baobabs above the falls": 2,
            "Deadvlei before the crowds": 1,
            "Fr\u00fchmorgens im Deadvlei": 1,
        },
    ),
    # Added the Huab desert elephants at 1, as the DE twin already had them. Dry riverbeds, no
    # swimming.
    (
        "en",
        "vague",
        "elephants swimming across a river",
        {
            "Elefanten beim Durchschwimmen": 3,
            "Elephants at the river's edge": 3,
            "Desert elephants of the Huab": 1,
            "W\u00fcstenelefanten im Huab-Tal": 1,
        },
    ),
    (
        "de",
        "vague",
        "Elefanten im Wasser sehen",
        {
            "Elefanten beim Durchschwimmen": 3,
            "Elephants at the river's edge": 3,
            "Desert elephants of the Huab": 1,
            "W\u00fcstenelefanten im Huab-Tal": 1,
        },
    ),
    (
        "en",
        "vague",
        "see endangered wild dogs",
        {
            "Painted dogs in teak woodland": 3,
        },
    ),
    (
        "en",
        "vague",
        "the waterfall, but I use a wheelchair",
        {
            "Der Rauch, der donnert": 3,
            "The smoke that thunders, step by step": 3,
        },
    ),
    (
        "de",
        "vague",
        "Wasserfall mit dem Rollstuhl besuchen",
        {
            "Der Rauch, der donnert": 3,
            "The smoke that thunders, step by step": 3,
        },
    ),
    (
        "en",
        "vague",
        "cross a very rough road by four by four",
        {
            "Van Zyl's Pass in a convoy": 3,
            "Sandwich Harbour by four-by-four": 1,
        },
    ),
    # Added two craft tours that show rather than teach: Masubia basket weavers, and Damara fire-
    # making and bow-making.
    (
        "en",
        "vague",
        "hand-made crafts you can try yourself",
        {
            "Community-run basket weaving morning": 3,
            "Woodcarvers of the Kavango": 3,
            "Living museum of the Damara": 1,
            "Masubia village on the floodplain": 1,
        },
    ),
    (
        "en",
        "vague",
        "lying under the stars on a salt flat",
        {
            "Sleeping out on the salt": 3,
        },
    ),
    # Added the Sperrgebiet drive: a worked-out diamond field, but no minerals to look at.
    (
        "en",
        "vague",
        "rare minerals and an old mine",
        {
            "Copper town museum and minerals": 3,
            "The forbidden diamond zone": 1,
        },
    ),
    (
        "en",
        "vague",
        "ancient rock engravings",
        {
            "A UNESCO gallery in sandstone": 3,
            "Felsgalerie der San": 3,
            "Bushman's Paradise paintings": 2,
        },
    ),
    (
        "de",
        "vague",
        "uralte Bilder im Fels",
        {
            "A UNESCO gallery in sandstone": 3,
            "Felsgalerie der San": 3,
            "Bushman's Paradise paintings": 2,
        },
    ),
    (
        "de",
        "vague",
        "Himba-Kultur kennenlernen",
        {
            "Besuch bei einer Himba-Familie": 3,
            "Himba homestead with a Himba guide": 3,
            "Ovatwa hunters' forest stories": 1,
        },
    ),
    (
        "en",
        "vague",
        "long multi-day backpacking through a gorge",
        {
            "Five days through Africa's great gorge": 3,
            "F\u00fcnf Tage durch den Fischfluss-Canyon": 3,
            "Two days on the Olive Trail and beyond": 2,
            "Zwei Tage im Naukluftgebirge": 2,
        },
    ),
    # Raised to 3. This query asks only for a multi-day pack hike, which Naukluft is exactly. The
    # EN twin asks for a long hike through a gorge, so it keeps them at 2 behind the five-day
    # canyon.
    (
        "de",
        "vague",
        "mehrt\u00e4gige Trekkingtour mit Rucksack",
        {
            "Five days through Africa's great gorge": 3,
            "F\u00fcnf Tage durch den Fischfluss-Canyon": 3,
            "Two days on the Olive Trail and beyond": 3,
            "Zwei Tage im Naukluftgebirge": 3,
        },
    ),
    (
        "en",
        "vague",
        "whitewashed fort with a violent past",
        {
            "The old German fort at Namutoni": 3,
        },
    ),
    # Added the two halves of the query that exist separately: Waterberg rhino hides (seated,
    # daytime) and the Etosha night drive (night, seated, no rhino).
    (
        "en",
        "vague",
        "rhinos at night while sitting down",
        {
            "Floodlit waterhole after dark": 3,
            "Nash\u00f6rner am beleuchteten Wasserloch": 3,
            "Night drive with a spotlight": 1,
            "Rhino breeding on the sandstone top": 1,
        },
    ),
    # Same two additions as the EN twin of this query.
    (
        "de",
        "vague",
        "abends am Wasserloch sitzen und Nash\u00f6rner sehen",
        {
            "Floodlit waterhole after dark": 3,
            "Nash\u00f6rner am beleuchteten Wasserloch": 3,
            "Night drive with a spotlight": 1,
            "Rhino breeding on the sandstone top": 1,
        },
    ),
]
