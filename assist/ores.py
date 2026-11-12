"""The ores a deposit can be, with their galactic average prices. No Qt in here.

To change the list, edit ORES: one line per ore, and nothing else needs to change. Saved deposits keep
their ore's name even if it's later removed from this list.

The list is every surface-mineable commodity Armchair found in player reports (2026-11-08). Names are the
game's own. Prices are `MeanPrice` from the game's Market.json (Homer Platform, 2026-10-26), a fixed table
because the tool never uses the network.
"""

ORES = {
    "Alexandrite": 205143,
    "Bastnasite": 66401,
    "Copper": 631,
    "Deuterium": 34171,
    "Diamond": 90081,
    "Gold": 47113,
    "Grandidierite": 184576,
    "Haematite": 1791,
    "Helium": 73920,
    "Helium-3": 69130,
    "Iridium": 129763,
    "Jadeite": 41895,
    "Lithium": 1830,
    "Low Temp. Diamonds": 96438,
    "Magnesite": 31985,
    "Methanol Monohydrate Crystals": 2492,
    "Monazite": 195083,
    "Olivine": 26242,
    "Osmium": 44051,
    "Palladium": 50356,
    "Periclase Dunite": 129763,
    "Platinum": 55505,
    "Quartz Pyroxenite": 39009,
    "Rhodplumsite": 171300,
    "Ruby": 73655,
    "Samarium": 25711,
    "Sapphire": 81044,
    "Serendibite": 161765,
    "Silver": 36553,
    "Tantalum": 13735,
    "Thorium": 11307,
    "Thortveitite": 129763,
    "Titanium": 4446,
    "Tritium": 51294,
    "Uraninite": 2861,
    "Uranium": 7138,
    "Water": 260,
}

# Shorter names for the overlay only (Armchair): the full name would widen the box for every target.
OVERLAY_NAMES = {"Methanol Monohydrate Crystals": "Methanol"}


def overlay_label(label):
    """A waypoint label ("D5 Methanol Monohydrate Crystals") with any long ore name shortened for the overlay."""
    for name, short in OVERLAY_NAMES.items():
        if label.endswith(" " + name):
            return label[:-len(name)] + short
    return label


# Sort orders.
ALPHABETICAL = "alphabetical"
BY_PRICE = "price"


def sorted_ores(names=ORES, order=ALPHABETICAL):
    """The names that are in the ore list, alphabetically or by price (highest first, then alphabetically)."""
    known = [name for name in names if name in ORES]
    if order == BY_PRICE:
        return sorted(known, key=lambda name: (-ORES[name], name))
    return sorted(known)
