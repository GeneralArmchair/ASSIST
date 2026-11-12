"""Read and decode Elite Dangerous' Status.json.

No Qt in here, so it can be used and tested on its own.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GAME_DIR = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
STATUS_PATH = GAME_DIR / "Status.json"

# The Status.json fields ASSIST uses. record_status.py keeps only these (Milestone 11): the file also holds the
# player's credit balance, cargo, fuel and legal state, which a recording sent with a bug report shouldn't carry.
RECORDED_FIELDS = ("timestamp", "event", "Flags", "Flags2", "GuiFocus", "Latitude", "Longitude", "Heading",
                   "Altitude", "BodyName", "PlanetRadius", "Destination")


def recorded_fields(data):
    """Status.json with only the fields ASSIST uses (RECORDED_FIELDS)."""
    return {key: value for key, value in data.items() if key in RECORDED_FIELDS}


# Flags bits this tool relies on (verified in-game).
LANDED = 1
SHIELDS_UP = 3
SUPERCRUISE = 4
HARDPOINTS = 6
LIGHTS = 8
SRV_HANDBRAKE = 12
SRV_TURRET_VIEW = 13
SRV_TURRET_RETRACTED = 14
MASS_LOCKED = 16
HAS_LAT_LONG = 21
IN_SHIP = 24
IN_SRV = 26
ALTITUDE_FROM_AVERAGE_RADIUS = 29

# Flags2 bits.
GLIDE = 12

# Destination (nav target) raw names for mining POIs look like
# "$SAA_Unknown_Signal:#type=$PlanetaryMiningLocation_Name;:#index=5;".
MINING_SIGNAL_MARKER = "$PlanetaryMiningLocation_Name"

# Names for display, from the game's journal documentation.
FLAG_NAMES = {
    0: "docked", 1: "landed", 2: "landing gear down", 3: "shields up", 4: "supercruise",
    5: "flight assist off", 6: "hardpoints deployed", 7: "in wing", 8: "lights on",
    9: "cargo scoop deployed", 10: "silent running", 11: "scooping fuel", 12: "SRV handbrake",
    13: "SRV turret view", 14: "SRV turret retracted", 15: "SRV drive assist", 16: "mass locked",
    17: "FSD charging", 18: "FSD cooldown", 19: "low fuel", 20: "overheating",
    21: "has lat/long", 22: "in danger", 23: "being interdicted", 24: "in main ship",
    25: "in fighter", 26: "in SRV", 27: "analysis mode", 28: "night vision",
    29: "altitude from average radius", 30: "FSD jump", 31: "SRV high beam",
}
FLAGS2_NAMES = {
    0: "on foot", 1: "in taxi", 2: "in multicrew", 3: "on foot in station",
    4: "on foot on planet", 5: "aim down sight", 6: "low oxygen", 7: "low health",
    8: "cold", 9: "hot", 10: "very cold", 11: "very hot", 12: "glide mode",
    13: "on foot in hangar", 14: "on foot in social space", 15: "on foot exterior",
    16: "breathable atmosphere", 17: "telepresence multicrew", 18: "physical multicrew",
    19: "FSD hyperdrive charging",
}


def read_status_file(path=STATUS_PATH):
    """Return the parsed Status.json, or None if it's missing, locked or half-written.

    The game rewrites the file constantly; on None the caller just tries again next tick.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def set_bits(value):
    """Bit numbers that are on in value, lowest first."""
    return [bit for bit in range(value.bit_length()) if value >> bit & 1]


def bit_names(value, names):
    """Readable names for the bits that are on; unknown bits show as 'bit N'."""
    return [names.get(bit, f"bit {bit}") for bit in set_bits(value)]


@dataclass(frozen=True)
class Status:
    """One decoded Status.json snapshot. Fields the game didn't write are None."""

    raw: dict
    timestamp: str | None
    flags: int
    flags2: int
    latitude: float | None
    longitude: float | None
    heading: int | None
    altitude: float | None
    body_name: str | None
    planet_radius: float | None
    destination_name: str | None
    destination_is_mining_signal: bool

    @classmethod
    def from_json(cls, data):
        destination = data.get("Destination") or {}
        return cls(
            raw=data,
            timestamp=data.get("timestamp"),
            flags=data.get("Flags", 0),
            flags2=data.get("Flags2", 0),
            latitude=data.get("Latitude"),
            longitude=data.get("Longitude"),
            heading=data.get("Heading"),
            altitude=data.get("Altitude"),
            body_name=data.get("BodyName"),
            planet_radius=data.get("PlanetRadius"),
            destination_name=destination.get("Name_Localised") or destination.get("Name"),
            # The raw name carries a type marker that doesn't depend on the game's language.
            destination_is_mining_signal=MINING_SIGNAL_MARKER in (destination.get("Name") or ""),
        )

    def has(self, bit):
        return bool(self.flags >> bit & 1)

    def has2(self, bit):
        return bool(self.flags2 >> bit & 1)

    @property
    def has_position(self):
        return self.has(HAS_LAT_LONG) and self.latitude is not None and self.longitude is not None

    @property
    def gui_focus(self):
        """Which panel or view has the game's focus: 0 none, 1-4 side panels, 5+ full-screen views."""
        return self.raw.get("GuiFocus", 0)

    @property
    def mode(self):
        """Where the player is, in a word or two."""
        if self.has(IN_SRV):
            return "SRV"
        if self.has(IN_SHIP):
            if self.has(SUPERCRUISE):
                return "ship, supercruise"
            if self.has2(GLIDE):
                return "ship, glide"
            return "ship"
        if self.has(25):
            return "fighter"
        if self.has2(0):
            return "on foot"
        return "unknown"

    @property
    def altitude_reference(self):
        """What the altitude is measured from: bit 29 on means the average radius, off means terrain."""
        return "average radius" if self.has(ALTITUDE_FROM_AVERAGE_RADIUS) else "terrain"

    @property
    def local_time(self):
        """The game's UTC timestamp as a local datetime, or None."""
        try:
            return datetime.fromisoformat(self.timestamp).astimezone()
        except (TypeError, ValueError):
            return None
