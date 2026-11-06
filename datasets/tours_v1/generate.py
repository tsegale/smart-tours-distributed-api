#!/usr/bin/env python3
"""Generates datasets/tours_v1/tours.jsonl: a synthetic catalogue for evaluating search. See docs/datasets.md.

Two parts: tours composed here from fragments (useful for rule-based relevance, too repetitive to judge meaning by), and
the individually written ones in authored.py (the ones that decide modelling choices).

Run once and commit the output. The seed makes it reproducible. Every record's attributes are known exactly, which is
what lets relevance be decided by rule in the evaluation. Standard library only.

    python datasets/tours_v1/generate.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20261106
COUNT = 2400
GERMAN_SHARE = 0.25
OUT = Path(__file__).with_name("tours.jsonl")

# name, country, region, latitude, longitude, German name if different, what's there
PLACES = [
    ("Sossusvlei", "NA", "Namib-Naukluft", -24.733, 15.293, None, "dunes"),
    ("Sesriem", "NA", "Namib-Naukluft", -24.487, 15.801, None, "dunes"),
    ("Swakopmund", "NA", "Erongo", -22.678, 14.527, None, "coast"),
    ("Walvis Bay", "NA", "Erongo", -22.957, 14.508, "Walfischbucht", "coast"),
    ("Windhoek", "NA", "Khomas", -22.559, 17.083, None, "city"),
    ("Etosha National Park", "NA", "Oshikoto", -18.855, 16.329, "Etosha-Nationalpark", "savanna"),
    ("Okaukuejo", "NA", "Kunene", -19.178, 15.917, None, "savanna"),
    ("Namutoni", "NA", "Oshikoto", -18.806, 16.942, None, "savanna"),
    ("Fish River Canyon", "NA", "Karas", -27.583, 17.583, "Fischfluss-Canyon", "canyon"),
    ("Ai-Ais", "NA", "Karas", -27.917, 17.483, None, "canyon"),
    ("Luderitz", "NA", "Karas", -26.648, 15.159, "Lüderitz", "coast"),
    ("Kolmanskop", "NA", "Karas", -26.705, 15.231, "Kolmannskuppe", "desert"),
    ("Twyfelfontein", "NA", "Kunene", -20.595, 14.373, None, "rock"),
    ("Damaraland", "NA", "Kunene", -20.350, 14.300, None, "rock"),
    ("Spitzkoppe", "NA", "Erongo", -21.827, 15.195, "Spitzkoppe", "rock"),
    ("Skeleton Coast", "NA", "Kunene", -20.000, 13.100, "Skelettküste", "coast"),
    ("Cape Cross", "NA", "Erongo", -21.774, 13.952, "Kreuzkap", "coast"),
    ("Opuwo", "NA", "Kunene", -18.061, 13.840, None, "culture"),
    ("Epupa Falls", "NA", "Kunene", -17.000, 13.246, "Epupa-Fälle", "river"),
    ("Rundu", "NA", "Kavango East", -17.918, 19.766, None, "river"),
    ("Divundu", "NA", "Kavango East", -18.097, 21.527, None, "river"),
    ("Katima Mulilo", "NA", "Zambezi", -17.500, 24.267, None, "river"),
    ("Waterberg Plateau", "NA", "Otjozondjupa", -20.507, 17.238, "Waterberg-Plateau", "plateau"),
    ("Otjiwarongo", "NA", "Otjozondjupa", -20.463, 16.648, None, "savanna"),
    ("Tsumeb", "NA", "Oshikoto", -19.233, 17.717, None, "city"),
    ("Keetmanshoop", "NA", "Karas", -26.583, 18.133, None, "desert"),
    ("Naukluft Mountains", "NA", "Hardap", -24.260, 16.240, "Naukluftberge", "rock"),
    ("Mariental", "NA", "Hardap", -24.633, 17.967, None, "desert"),
    ("Chobe National Park", "BW", "Chobe", -18.667, 24.500, "Chobe-Nationalpark", "river"),
    ("Kasane", "BW", "Chobe", -17.817, 25.150, None, "river"),
    ("Maun", "BW", "North-West", -19.983, 23.417, None, "river"),
    ("Okavango Delta", "BW", "North-West", -19.283, 22.900, "Okavango-Delta", "river"),
    ("Makgadikgadi Pans", "BW", "Central", -20.750, 25.500, "Makgadikgadi-Salzpfannen", "desert"),
    ("Victoria Falls", "ZW", "Matabeleland North", -17.925, 25.857, "Viktoriafälle", "river"),
    ("Hwange National Park", "ZW", "Matabeleland North", -18.629, 26.500, "Hwange-Nationalpark", "savanna"),
    ("Livingstone", "ZM", "Southern", -17.841, 25.854, None, "river"),
    ("Cape Town", "ZA", "Western Cape", -33.925, 18.424, "Kapstadt", "coast"),
    ("Upington", "ZA", "Northern Cape", -28.448, 21.256, None, "desert"),
    ("Kgalagadi Transfrontier Park", "ZA", "Northern Cape", -25.600, 20.800, "Kgalagadi-Nationalpark", "desert"),
    ("Springbok", "ZA", "Northern Cape", -29.665, 17.886, None, "desert"),
    ("Lubango", "AO", "Huila", -14.917, 13.500, None, "rock"),
]

# Each activity: where it happens, category, its difficulties, minutes, price band in NAD, then wording.
# German wording is written, not translated by a tool. Several variants per field, so texts differ in more than names.
ACTIVITIES = {
    "dune_climb": {
        "where": {"dunes"}, "category": "ADVENTURE", "difficulty": ["MODERATE", "CHALLENGING"], "minutes": [240, 300, 360],
        "price": (1200, 2600),
        "title_en": ["Sunrise climb at {p}", "{p} dune walk at first light", "Dawn on the red dunes of {p}"],
        "title_de": ["Sonnenaufgang auf den Dünen bei {p}", "Dünenwanderung im Morgenlicht: {p}"],
        "en": ["We set out before dawn and climb a crest of soft red sand while the valley below turns from grey to orange.",
               "Walk the ridge barefoot if you like, then slide down the steep face and cross the white clay pan.",
               "Expect loose sand underfoot and a real effort on the final stretch, rewarded by long shadows across the sea of dunes."],
        "de": ["Noch vor der Dämmerung brechen wir auf und steigen über weichen roten Sand, während sich das Tal unter uns von grau nach orange färbt.",
               "Auf dem Kamm geht es, wer mag, barfuß weiter, danach hinunter über den steilen Hang und durch die weiße Tonpfanne.",
               "Der lockere Sand kostet auf dem letzten Stück Kraft, dafür liegen lange Schatten über dem Dünenmeer."],
    },
    "scenic_flight": {
        "where": {"dunes", "coast", "river", "desert"}, "category": "ADVENTURE", "difficulty": ["EASY"], "minutes": [60, 90, 120],
        "price": (3200, 7800),
        "title_en": ["Light aircraft over {p}", "{p} from the air", "Scenic flight: {p}"],
        "title_de": ["Rundflug über {p}", "{p} von oben"],
        "en": ["A small high-wing plane gives every passenger a window seat for the patterns you can't read from the ground.",
               "The pilot narrates the geology on the way, and low passes make the scale of the landscape obvious.",
               "No walking at all, which suits anyone who wants the view without the effort."],
        "de": ["In der kleinen Hochdeckermaschine hat jeder einen Fensterplatz und sieht Muster, die vom Boden aus verborgen bleiben.",
               "Unterwegs erklärt der Pilot die Geologie, und tiefe Überflüge machen die Weite der Landschaft greifbar.",
               "Laufen muss man hier gar nicht: ideal für alle, die den Blick ohne Anstrengung wollen."],
    },
    "game_drive": {
        "where": {"savanna", "river", "desert"}, "category": "SAFARI", "difficulty": ["EASY"], "minutes": [180, 240, 480],
        "price": (950, 2900),
        "title_en": ["Game drive in {p}", "{p} wildlife safari", "Waterholes of {p} by open vehicle"],
        "title_de": ["Pirschfahrt in {p}", "Tierbeobachtung in {p}", "Wasserlöcher von {p} im offenen Wagen"],
        "en": ["An open vehicle and an experienced guide take you between waterholes, where elephant, giraffe and springbok come to drink.",
               "Lions are most active early, so we leave at the gate's opening and stop often for photographs.",
               "Binoculars and a field guide are on board, and the guide tracks sightings by radio with other vehicles."],
        "de": ["Im offenen Geländewagen geht es mit erfahrenem Guide von Wasserloch zu Wasserloch, wo Elefanten, Giraffen und Springböcke trinken.",
               "Löwen sind früh am aktivsten, deshalb starten wir mit der Toröffnung und halten oft zum Fotografieren.",
               "Ferngläser und ein Bestimmungsbuch sind an Bord, Sichtungen teilt der Guide per Funk mit anderen Fahrzeugen."],
    },
    "rhino_tracking": {
        "where": {"rock", "savanna", "plateau"}, "category": "SAFARI", "difficulty": ["MODERATE", "CHALLENGING"], "minutes": [300, 420],
        "price": (2400, 5200),
        "title_en": ["Tracking desert rhino near {p}", "On foot after black rhino: {p}"],
        "title_de": ["Spitzmaulnashörner zu Fuß bei {p}", "Nashorn-Tracking in {p}"],
        "en": ["Local trackers read the spoor from the vehicle, then we continue quietly on foot over rocky ground.",
               "Conservation fees from each trip support the community rangers who protect these animals.",
               "Group sizes stay small and everyone follows the tracker's signals, since the rhino decides how close we get."],
        "de": ["Einheimische Fährtenleser lesen die Spuren vom Fahrzeug aus, danach geht es leise zu Fuß über steiniges Gelände weiter.",
               "Die Schutzgebühr jeder Tour unterstützt die Gemeinde-Ranger, die diese Tiere bewachen.",
               "Die Gruppen bleiben klein und alle folgen den Handzeichen des Fährtenlesers, denn das Nashorn bestimmt den Abstand."],
    },
    "kayak": {
        "where": {"coast", "river"}, "category": "ADVENTURE", "difficulty": ["EASY", "MODERATE"], "minutes": [180, 240],
        "price": (780, 1650),
        "title_en": ["Kayaking at {p}", "Paddle the water at {p}", "{p} by sea kayak"],
        "title_de": ["Kajaktour bei {p}", "Paddeln in {p}"],
        "en": ["Stable double kayaks make this easy for first-timers, and curious seals often swim right under the hull.",
               "We launch in the calm of the morning before the wind rises and paddle along the shoreline with a guide.",
               "Everything is provided, including dry bags and a hot drink when we land."],
        "de": ["Stabile Zweierkajaks machen es auch Anfängern leicht, und neugierige Robben tauchen oft direkt unter dem Boot hindurch.",
               "Wir starten in der Morgenruhe, bevor der Wind auffrischt, und paddeln mit Guide die Küste entlang.",
               "Alles ist inklusive, auch wasserdichte Taschen und ein heißes Getränk nach der Landung."],
    },
    "boat_cruise": {
        "where": {"coast", "river"}, "category": "NATURE", "difficulty": ["EASY"], "minutes": [120, 180, 240],
        "price": (650, 1900),
        "title_en": ["Sunset cruise at {p}", "{p} boat trip", "On the water at {p}"],
        "title_de": ["Bootsfahrt bei {p}", "Sonnenuntergangsfahrt: {p}"],
        "en": ["A shaded deck, snacks and cold drinks make this the gentlest way to watch hippos, birds and the evening light.",
               "Pelicans follow the boat, and the crew knows where dolphins and seals have been seen that week.",
               "Step aboard from a level jetty and stay seated for the whole trip, with a toilet on board."],
        "de": ["Schattiges Deck, Snacks und kalte Getränke: die entspannteste Art, Flusspferde, Vögel und das Abendlicht zu erleben.",
               "Pelikane begleiten das Boot, und die Crew weiß, wo diese Woche Delfine und Robben gesichtet wurden.",
               "Über einen ebenen Steg an Bord, die ganze Fahrt über sitzen, Toilette an Bord."],
    },
    "canyon_hike": {
        "where": {"canyon", "rock", "plateau"}, "category": "ADVENTURE", "difficulty": ["CHALLENGING", "EXTREME"], "minutes": [480, 720, 1440],
        "price": (1600, 4800),
        "title_en": ["Guided hike at {p}", "Steep trails of {p}", "Long day on foot: {p}"],
        "title_de": ["Geführte Wanderung bei {p}", "Bergtour: {p}"],
        "en": ["A demanding walk with scrambling over boulders, long climbs and little shade, for hikers who are properly fit.",
               "Carry at least three litres of water. The guide sets a steady pace and watches the heat closely.",
               "This is a serious trek with exposed sections, not suitable for anyone with knee trouble or a fear of heights."],
        "de": ["Eine anspruchsvolle Tour mit Kletterstellen über Felsblöcke, langen Anstiegen und wenig Schatten, nur für wirklich trainierte Wanderer.",
               "Mindestens drei Liter Wasser mitnehmen. Der Guide geht ein gleichmäßiges Tempo und behält die Hitze im Blick.",
               "Eine ernsthafte Bergtour mit ausgesetzten Stellen, nicht geeignet bei Knieproblemen oder Höhenangst."],
    },
    "rock_art": {
        "where": {"rock"}, "category": "CULTURAL", "difficulty": ["EASY", "MODERATE"], "minutes": [120, 180, 240],
        "price": (480, 1200),
        "title_en": ["Ancient engravings of {p}", "{p} rock art walk", "San paintings near {p}"],
        "title_de": ["Felsgravuren von {p}", "Felsmalereien bei {p}"],
        "en": ["A short walk between sandstone slabs carved thousands of years ago with animals, footprints and maps of waterholes.",
               "A local guide explains how San hunter-gatherers passed on knowledge through these engravings.",
               "The path is uneven but short, with plenty of stops and shade at the main panels."],
        "de": ["Ein kurzer Weg zwischen Sandsteinplatten, vor Jahrtausenden mit Tieren, Fußspuren und Karten von Wasserstellen graviert.",
               "Ein einheimischer Guide erklärt, wie die San ihr Wissen über diese Gravuren weitergaben.",
               "Der Pfad ist uneben, aber kurz, mit vielen Pausen und Schatten an den wichtigsten Tafeln."],
    },
    "village_visit": {
        "where": {"culture", "river", "savanna"}, "category": "CULTURAL", "difficulty": ["EASY"], "minutes": [180, 240],
        "price": (520, 1400),
        "title_en": ["Community visit near {p}", "A morning with families at {p}", "Living culture of {p}"],
        "title_de": ["Dorfbesuch bei {p}", "Begegnung mit Familien in {p}"],
        "en": ["Run with the community itself: fees go directly to the village, and you are a guest in someone's home, not at a show.",
               "A translator introduces you, and you learn about herding, crafts and how the household runs.",
               "Please ask before taking photographs. Children are welcome and usually end up playing together."],
        "de": ["Von der Gemeinde selbst organisiert: Die Einnahmen gehen direkt ans Dorf, und Sie sind Gast in einem Zuhause, nicht bei einer Show.",
               "Ein Übersetzer stellt Sie vor, Sie erfahren mehr über Viehzucht, Handwerk und den Alltag der Familie.",
               "Bitte vor dem Fotografieren fragen. Kinder sind willkommen und spielen meist bald miteinander."],
    },
    "town_walk": {
        "where": {"city", "coast"}, "category": "CULTURAL", "difficulty": ["EASY"], "minutes": [120, 180],
        "price": (280, 780),
        "title_en": ["Historic walk through {p}", "Colonial history of {p}", "{p} on foot with a historian"],
        "title_de": ["Stadtrundgang in {p}", "Kolonialgeschichte von {p}"],
        "en": ["German colonial architecture, the independence story and a coffee stop, all on flat and paved streets.",
               "An honest account of the country's history, including the genocide of the Herero and Nama, told by a local historian.",
               "Short distances and many benches make this a good choice for older travellers and for wheelchair users."],
        "de": ["Deutsche Kolonialarchitektur, die Geschichte der Unabhängigkeit und eine Kaffeepause, alles auf ebenen, gepflasterten Straßen.",
               "Ein ehrlicher Blick auf die Geschichte des Landes, auch auf den Völkermord an den Herero und Nama, erzählt von einem einheimischen Historiker.",
               "Kurze Wege und viele Bänke: gut geeignet für ältere Reisende und Rollstuhlfahrer."],
    },
    "astro": {
        "where": {"dunes", "desert", "rock", "plateau"}, "category": "NATURE", "difficulty": ["EASY"], "minutes": [120, 180],
        "price": (620, 1500),
        "title_en": ["Stargazing at {p}", "Dark skies over {p}", "Night sky session near {p}"],
        "title_de": ["Sternenbeobachtung bei {p}", "Der Nachthimmel über {p}"],
        "en": ["Some of the darkest skies on Earth: an astronomer guides you through the southern constellations with a telescope.",
               "Bring a warm jacket, since desert nights get cold, and see the Milky Way with the naked eye.",
               "No fitness needed. We sit by the fire between looks through the telescope, with hot chocolate."],
        "de": ["Einer der dunkelsten Himmel der Erde: Ein Astronom führt Sie mit dem Teleskop durch die Sternbilder des Südens.",
               "Warme Jacke mitbringen, Wüstennächte werden kalt, und die Milchstraße ist mit bloßem Auge zu sehen.",
               "Keine Kondition nötig. Zwischen den Blicken durchs Teleskop sitzen wir am Feuer, mit heißer Schokolade."],
    },
    "fishing": {
        "where": {"coast", "river"}, "category": "NATURE", "difficulty": ["EASY", "MODERATE"], "minutes": [240, 360, 480],
        "price": (900, 3200),
        "title_en": ["Angling trip at {p}", "Fishing with a guide: {p}", "Catch and release at {p}"],
        "title_de": ["Angeltour bei {p}", "Angeln mit Guide: {p}"],
        "en": ["Rods, bait and licences are arranged. Expect kob and steenbras on the coast, and tigerfish on the rivers.",
               "Your guide knows the channels and tides, and teaches beginners without any fuss.",
               "Catch and release is encouraged, and lunch is packed for the day."],
        "de": ["Ruten, Köder und Angelscheine sind organisiert. An der Küste warten Kob und Steenbras, in den Flüssen Tigerfische.",
               "Ihr Guide kennt die Rinnen und Gezeiten und bringt auch Anfängern alles geduldig bei.",
               "Fangen und Freilassen wird empfohlen, Mittagessen ist für den Tag eingepackt."],
    },
    "quad": {
        "where": {"dunes", "coast", "desert"}, "category": "ADVENTURE", "difficulty": ["MODERATE", "CHALLENGING"], "minutes": [120, 180],
        "price": (950, 1800),
        "title_en": ["Quad biking the dunes at {p}", "{p} dune quad adventure"],
        "title_de": ["Quad-Tour durch die Dünen bei {p}", "Mit dem Quad durch {p}"],
        "en": ["An adrenaline ride through coastal dune fields, with helmets, a briefing and stops on the highest ridges.",
               "Automatic quads are easy to handle after a short practice, and the guide adjusts the route to the group.",
               "Riders must be at least 16, and the tracks are kept within the permitted area to protect the dunes."],
        "de": ["Ein Adrenalinkick durch die Dünenfelder, mit Helm, Einweisung und Stopps auf den höchsten Kämmen.",
               "Automatik-Quads sind nach kurzer Übung leicht zu fahren, und der Guide passt die Route der Gruppe an.",
               "Fahrer müssen mindestens 16 sein, und die Spuren bleiben im erlaubten Gebiet, um die Dünen zu schützen."],
    },
    "food": {
        "where": {"city", "coast"}, "category": "CULTURAL", "difficulty": ["EASY"], "minutes": [150, 180, 240],
        "price": (420, 1200),
        "title_en": ["Taste your way around {p}", "{p} food walk", "Oysters, kapana and craft beer: {p}"],
        "title_de": ["Kulinarische Tour durch {p}", "Probieren in {p}: Austern und Kapana"],
        "en": ["Grilled kapana from the market, fresh oysters and local craft beer, with vegetarian choices arranged on request.",
               "Small tastings at family-run places where visitors rarely find their own way.",
               "Come hungry and expect to walk slowly, since most of the time is spent at the table."],
        "de": ["Gegrilltes Kapana vom Markt, frische Austern und einheimisches Craft-Bier, vegetarische Alternativen auf Anfrage.",
               "Kleine Kostproben in Familienbetrieben, die Besucher selten allein finden.",
               "Hungrig kommen und gemütlich gehen, denn die meiste Zeit verbringt man am Tisch."],
    },
    "birding": {
        "where": {"river", "savanna", "coast"}, "category": "NATURE", "difficulty": ["EASY"], "minutes": [180, 240, 300],
        "price": (680, 1900),
        "title_en": ["Birding with a specialist at {p}", "{p} birdwatching morning"],
        "title_de": ["Vogelbeobachtung bei {p}", "Ornithologische Tour: {p}"],
        "en": ["Fish eagles, flamingos and carmine bee-eaters, depending on the season, with a guide who knows the calls.",
               "A spotting scope is provided, and the pace is slow enough for keen photographers.",
               "We keep a list of everything seen and send it to you afterwards."],
        "de": ["Schreiseeadler, Flamingos und Scharlachspinte, je nach Jahreszeit, mit einem Guide, der die Rufe kennt.",
               "Ein Spektiv ist dabei, und das Tempo passt auch für begeisterte Fotografen.",
               "Wir führen eine Liste aller Sichtungen und schicken sie Ihnen danach zu."],
    },
    "photo": {
        "where": {"desert", "dunes", "rock", "savanna"}, "category": "NATURE", "difficulty": ["EASY", "MODERATE"], "minutes": [240, 360, 480],
        "price": (1100, 3600),
        "title_en": ["Photography workshop at {p}", "Golden hour at {p} with a photographer", "Abandoned places and light: {p}"],
        "title_de": ["Fotoworkshop bei {p}", "Goldene Stunde in {p}"],
        "en": ["A professional photographer helps with composition and exposure, and timing is built around the best light.",
               "Small groups and early access mean clean frames with nobody else in the picture.",
               "Bring any camera, even a phone. An evening review of your images is included."],
        "de": ["Ein Profifotograf hilft bei Bildaufbau und Belichtung, der Ablauf richtet sich nach dem besten Licht.",
               "Kleine Gruppen und früher Einlass sorgen für Bilder ohne andere Menschen im Motiv.",
               "Jede Kamera ist willkommen, sogar das Handy. Eine abendliche Bildbesprechung ist inklusive."],
    },
}

FAMILY_EN = ["Children are welcome, and guides adapt the pace for younger travellers.", "A good fit for families: short stretches and plenty to keep kids interested."]
FAMILY_DE = ["Kinder sind willkommen, der Guide passt das Tempo an die Jüngsten an.", "Ideal für Familien: kurze Etappen und viel, das Kinder begeistert."]
ADULTS_EN = ["Not recommended for children under 12.", "Minimum age is 14, because of the distance and terrain."]
ADULTS_DE = ["Für Kinder unter 12 Jahren nicht empfohlen.", "Mindestalter 14 Jahre, wegen Strecke und Gelände."]
ACCESS_EN = ["Fully wheelchair accessible, including vehicle access and an adapted restroom.", "Step-free throughout, and our vehicle has a wheelchair ramp."]
ACCESS_DE = ["Vollständig rollstuhlgerecht, einschließlich Fahrzeug und barrierefreier Toilette.", "Durchgehend stufenlos, unser Fahrzeug hat eine Rollstuhlrampe."]
LANG_EN = {"en": "Guided in English.", "de": "Guided in German.", "both": "Guided in English and German.", "af": "Guided in English and Afrikaans."}
LANG_DE = {"en": "Führung auf Englisch.", "de": "Führung auf Deutsch.", "both": "Führung auf Deutsch und Englisch.", "af": "Führung auf Englisch und Afrikaans."}
MEET_EN = ["Pick-up from your lodge is included.", "We meet at the park gate.", "Includes water and a light snack.", "Lunch is included.", "Hotel transfers can be arranged."]
MEET_DE = ["Abholung von Ihrer Lodge ist inklusive.", "Treffpunkt ist das Parktor.", "Wasser und ein kleiner Imbiss sind inklusive.", "Mittagessen ist inklusive.", "Hoteltransfers können organisiert werden."]
SEASON_EN = {"dry": "Best in the dry months from May to October.", "green": "At its finest after the summer rains.", "any": "Runs all year round."}
SEASON_DE = {"dry": "Am schönsten in der Trockenzeit von Mai bis Oktober.", "green": "Am schönsten nach dem Sommerregen.", "any": "Das ganze Jahr über möglich."}


def tour(rng: random.Random, tour_id: int) -> dict:
    activity_key = rng.choice(list(ACTIVITIES))
    activity = ACTIVITIES[activity_key]
    place = rng.choice([p for p in PLACES if p[6] in activity["where"]])
    name, country, region, latitude, longitude, german_name, _ = place
    language = "de" if rng.random() < GERMAN_SHARE else "en"
    display = (german_name or name) if language == "de" else name

    difficulty = rng.choice(activity["difficulty"])
    minutes = rng.choice(activity["minutes"])
    low, high = activity["price"]
    price = round(rng.uniform(low, high) / 10) * 10
    gentle = difficulty == "EASY" and minutes <= 300
    family = gentle and rng.random() < 0.6
    accessible = gentle and activity_key in {"boat_cruise", "town_walk", "scenic_flight", "astro", "game_drive"} and rng.random() < 0.35
    guided = rng.choice(["en", "en", "both", "af"] if language == "en" else ["de", "both", "both"])
    season = rng.choice(["dry", "green", "any", "any"])

    sentences = list(activity[language])
    rng.shuffle(sentences)
    body = sentences[: rng.choice([2, 3])]
    extras = []
    if family:
        extras.append(rng.choice(FAMILY_DE if language == "de" else FAMILY_EN))
    elif difficulty in {"CHALLENGING", "EXTREME"}:
        extras.append(rng.choice(ADULTS_DE if language == "de" else ADULTS_EN))
    if accessible:
        extras.append(rng.choice(ACCESS_DE if language == "de" else ACCESS_EN))
    extras.append((LANG_DE if language == "de" else LANG_EN)[guided])
    extras.append((SEASON_DE if language == "de" else SEASON_EN)[season])
    extras.append(rng.choice(MEET_DE if language == "de" else MEET_EN))
    rng.shuffle(extras)

    title = rng.choice(activity["title_de" if language == "de" else "title_en"]).format(p=display)
    return {
        "id": tour_id,
        "name": title,
        "description": " ".join(body + extras),
        "language": language,
        "place": name,
        "country": country,
        "region": region,
        # Jitter within a few km: tours start at different spots around a place.
        "latitude": round(latitude + rng.uniform(-0.04, 0.04), 5),
        "longitude": round(longitude + rng.uniform(-0.04, 0.04), 5),
        "activity": activity_key,
        "category": activity["category"],
        "difficulty": difficulty,
        "durationMinutes": minutes,
        "price": price,
        "currency": "NAD",
        "familyFriendly": family,
        "wheelchairAccessible": accessible,
        "guidedIn": guided,
        "season": season,
        "ratingAverage": round(rng.uniform(3.4, 5.0), 1),
        "ratingCount": rng.randint(0, 240),
        "authored": False,
    }


def authored_tours() -> list[dict]:
    """The individually written tours, numbered from 100001, with attributes filled in the same way."""
    from authored import AUTHORED

    places = {p[0]: p for p in PLACES}
    tours = []
    for index, row in enumerate(AUTHORED):
        place, language, category, difficulty, minutes, price, family, wheelchair, guided, name, description = row
        _, country, region, latitude, longitude, _, _ = places[place]
        tours.append({
            "id": 100001 + index, "name": name, "description": description, "language": language, "place": place,
            "country": country, "region": region, "latitude": latitude, "longitude": longitude, "activity": None,
            "category": category, "difficulty": difficulty, "durationMinutes": minutes, "price": price,
            "currency": "NAD", "familyFriendly": family, "wheelchairAccessible": wheelchair, "guidedIn": guided,
            "season": "any", "ratingAverage": 4.4, "ratingCount": 40, "authored": True,
        })  # fmt: skip
    return tours


def main() -> None:
    rng = random.Random(SEED)
    tours = [tour(rng, index + 1) for index in range(COUNT)] + authored_tours()
    OUT.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tours), encoding="utf-8", newline="\n")
    texts = {t["description"] for t in tours}
    print(f"{len(tours)} tours, {sum(t['language'] == 'de' for t in tours)} German, {len(texts)} distinct descriptions")


if __name__ == "__main__":
    main()
