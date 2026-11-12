"""Great-circle math on a perfect sphere (the planet).

Positions are (latitude, longitude) in degrees, distances in meters, bearings in
degrees clockwise from north. No Qt in here.
"""

import math


def distance(lat1, lon1, lat2, lon2, radius):
    """Great-circle distance between two points (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 towards point 2, 0-360 degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def destination(lat, lon, bearing_deg, dist, radius):
    """The point reached by going dist meters from (lat, lon) on the given initial bearing."""
    p1, l1 = math.radians(lat), math.radians(lon)
    b = math.radians(bearing_deg)
    d = dist / radius  # angular distance
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    lon2 = (math.degrees(l2) + 540) % 360 - 180  # normalize to -180..180
    return math.degrees(p2), lon2
