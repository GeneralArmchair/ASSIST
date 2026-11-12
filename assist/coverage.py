"""Scanner coverage of a POI: the share of the POI disk within 2 km of a counted ping.

Worked out by drawing the disk and the ping circles into a hidden black-and-white image and counting
pixels (Armchair's approach). It's a separate image from the visible map, so the result
doesn't depend on the window's size, antialiasing or what's drawn on top.

With no radius measured, the POI radius is assumed to be 7 km (the largest seen is 7.05 km), which
errs towards showing ground as possibly unsurveyed.
"""

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from .survey import SCAN_RANGE

SCAN_RADIUS = SCAN_RANGE  # m: the Rhino's deposit scanner range
ASSUMED_RADIUS = 7000.0  # m: the POI radius assumed until it's measured (Armchair: 7.05 km is the largest seen)
MASK_SIZE = 1000         # pixels across the hidden image: about 14 m per pixel at 7 km

_DISK = 100     # grey: POI disk, not covered
_COVERED = 255  # white: POI disk, covered


@dataclass(frozen=True)
class Coverage:
    fraction: float  # 0 to 1
    radius: float    # m: the measured radius, or the assumed one
    assumed: bool    # True when the radius isn't measured

    def text(self):
        """'91% of 5.12 km', or '84% (assumed 7km radius)'."""
        percent = f"{self.fraction * 100:.0f}%"
        if self.assumed:
            return f"{percent} (assumed {self.radius / 1000:g}km radius)"
        return f"{percent} of {self.radius / 1000:.2f} km"


def poi_radius(survey):
    """(radius in m, assumed?)"""
    if survey.poi.radius is not None:
        return survey.poi.radius, False
    return ASSUMED_RADIUS, True


def counted_positions(survey):
    """(east, north) of every ping that counts: "no ping detected" entries don't."""
    return [survey.east_north(p.lat, p.lon) for p in survey.pings if p.counts]


def scan_area(positions, scale=1.0, center=QPointF(0, 0)):
    """All the 2 km circles as one shape, so overlaps are one area. Positions are (east, north) in m;
    the result is in pixels, with north up, scale pixels per meter around center."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    r = SCAN_RADIUS * scale
    for east, north in positions:
        path.addEllipse(QPointF(center.x() + east * scale, center.y() - north * scale), r, r)
    return path


_cache = {}


def coverage(survey):
    """The Coverage of a survey. Results are kept per survey, so redrawing four times a second costs nothing."""
    radius, assumed = poi_radius(survey)
    positions = counted_positions(survey)
    key = (radius, tuple(positions))
    cached = _cache.get(survey.id)
    if cached and cached[0] == key:
        return cached[1]
    result = Coverage(_fraction(radius, positions), radius, assumed)
    _cache[survey.id] = (key, result)
    return result


def _fraction(radius, positions):
    image = QImage(MASK_SIZE, MASK_SIZE, QImage.Format.Format_Grayscale8)
    image.fill(0)
    scale = MASK_SIZE / (2 * radius)
    center = QPointF(MASK_SIZE / 2, MASK_SIZE / 2)
    disk = QPainterPath()
    disk.addEllipse(center, radius * scale, radius * scale)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # every pixel is in or out
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(disk, QColor(_DISK, _DISK, _DISK))
    if positions:
        painter.fillPath(disk.intersected(scan_area(positions, scale, center)), QColor(_COVERED, _COVERED, _COVERED))
    painter.end()
    pixels = bytes(image.constBits())  # MASK_SIZE is a multiple of 4, so rows have no padding
    covered = pixels.count(_COVERED)
    total = covered + pixels.count(_DISK)
    return covered / total if total else 0.0
