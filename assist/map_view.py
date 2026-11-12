"""The survey map: east/north meters around the survey center, with the golden ring, scanner coverage,
survey points, pings, deposits, a lat/long grid, a three-row title and a deposit legend.
Milestone 7.

draw_map() paints it all onto any QPainter, so the window and the saved image are the same picture.
Coverage itself is worked out separately, in coverage.py.
"""

import math
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from . import __version__
from .coverage import SCAN_RADIUS, counted_positions, coverage, poi_radius, scan_area
from .guidance import format_distance
from .survey import file_name
from .ores import ORES, overlay_label
from .paths import shown

IMAGE_SIZE = (1600, 1300)  # the saved image: a title band, then a 1180 px map and a 420 px legend column
TITLE_ROWS = (1.45, 1.3, 1.3)  # font size of each title row (system, body, POI), as a multiple of the base size
GRID_LINES = 8                 # at least this many lat lines, and this many long lines, across the map

BACKGROUND = QColor("#14181f")
TEXT = QColor("#e6e6e6")
DIM_TEXT = QColor("#9aa0a8")
DISTANCE_RING = QColor(255, 255, 255, 28)
GOLD = QColor("#d4af37")
SCAN_FILL = QColor(220, 40, 40, 85)       # one tint for the whole covered area (Armchair: red)
SCAN_OUTLINE = QColor(235, 70, 70, 110)
PENDING = QColor("#e0e0e0")
DONE = QColor("#4caf50")
FAR_OFF = QColor("#e69a28")               # done, but the ping landed outside the point's circle
SKIPPED = QColor("#777777")
TARGET = QColor("#ffd54f")
PLAYER = QColor("#ffffff")
NO_PING_COLOR = QColor("#8a8a8a")
CUSTOM_SCAN = QColor(255, 255, 255, 190)  # the scan circle at the custom destination (Armchair: white, dashed)
GRID = QColor(120, 175, 255, 75)
GRID_TEXT = QColor("#8fb8e8")
LABEL_BACKING = QColor(20, 24, 31, 200)  # BACKGROUND, nearly opaque: labels stay readable over lines
# Deposit colours by ore, re-used from the sixth ore on (Armchair). None is red or gold.
ORE_COLORS = [QColor("#4fc3f7"), QColor("#81c784"), QColor("#ce93d8"), QColor("#ffffff"), QColor("#e040fb")]


def ores_by_value(survey):
    """The ores of the survey's deposits, most valuable first (MeanPrice), then by name."""
    return sorted({d.ore for d in survey.deposits}, key=lambda ore: (-ORES.get(ore, 0), ore))


def ore_colors(survey):
    return {ore: ORE_COLORS[i % len(ORE_COLORS)] for i, ore in enumerate(ores_by_value(survey))}


def legend_groups(survey):
    """[(ore, [deposits])]: ores most valuable first; within an ore, largest first and unsized last."""
    groups = []
    for ore in ores_by_value(survey):
        deposits = [d for d in survey.deposits if d.ore == ore]
        deposits.sort(key=lambda d: (d.size is None, -(d.size or 0), d.id))
        groups.append((ore, deposits))
    return groups


def deposit_text(deposit):
    """'D3 Magnesite 4' (with the size when known), with long ore names shortened as on the overlay."""
    label = overlay_label(deposit.label)
    return f"{label} {deposit.size}" if deposit.size else label


def game_date(timestamp):
    """A game timestamp as 'YYYY-MM-DD HH:MM UTC': the game's own time, and no hint of the player's time zone."""
    try:
        return f"{datetime.fromisoformat(timestamp).astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC"
    except (TypeError, ValueError):
        return timestamp or "?"


def title_lines(survey):
    """The title: system, body and mining POI, a row each (Armchair). The body is in full even when it
    starts with the system's name, as the game shows it."""
    poi = survey.poi
    return [poi.system or "(system unknown)", poi.body, poi.name or "(unnamed POI)"]


def title_height(font_px):
    return sum(TITLE_ROWS) * font_px * 1.25 + font_px * 0.8


# --- the lat/long grid ---------------------------------------------------------------------

def nice_step(span):
    """The largest round step in degrees (1, 2 or 5 times a power of ten) giving GRID_LINES lines or more
    over span: 8 to 20 lines, close enough together to read a position off to about a tenth of a step."""
    raw = max(span, 1e-9) / GRID_LINES
    power = 10 ** math.floor(math.log10(raw) + 1e-9)
    return max(m * power for m in (1, 2, 5) if m * power <= raw * 1.001)


def step_decimals(step):
    """Decimals that show every line of the grid apart: 0.05 needs 2, 0.1 needs 1, 2 needs none."""
    return max(0, -math.floor(math.log10(step) + 1e-9))


def degrees_text(value, decimals):
    return f"{round(value, decimals) + 0.0:.{decimals}f}"  # + 0.0: no "-0.00"


def _relative_lon(lon, center_lon):
    return (lon - center_lon + 540) % 360 - 180


def grid(survey, extent):
    """The lat/long lines over a square extent meters from the center to each edge.

    (lat_lines, lon_lines, lat_step, lon_step): each line is (value in degrees, [(east, north), ...]).
    Lines are curves sampled along their length, so they're right however the map is projected.
    """
    poi = survey.poi
    edge = [i / 8 - 1 for i in range(17)]  # -1 to 1
    around = [(a * extent, b * extent) for a in edge for b in (-1, 1)] + \
             [(b * extent, a * extent) for a in edge for b in (-1, 1)]
    points = [survey.lat_lon(east, north) for east, north in around]
    lats = [lat for lat, _ in points]
    lons = [_relative_lon(lon, poi.center_lon) for _, lon in points]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    pole_distance = math.radians(90 - abs(poi.center_lat)) * poi.planet_radius
    if pole_distance < extent * math.sqrt(2):  # a pole is on the map: every longitude is too
        lon_min, lon_max = -180.0, 180.0
        lat_min, lat_max = (lat_min, 90.0) if poi.center_lat > 0 else (-90.0, lat_max)
    lat_step, lon_step = nice_step(lat_max - lat_min), nice_step(lon_max - lon_min)

    def values(low, high, step, offset=0.0):
        first = math.ceil((low + offset) / step)
        return [k * step for k in range(first, math.floor((high + offset) / step) + 1)]

    def samples(low, high, n=48):
        return [low + (high - low) * i / n for i in range(n + 1)]

    lat_lines = [(lat, [survey.east_north(lat, poi.center_lon + lon) for lon in samples(lon_min, lon_max)])
                 for lat in values(lat_min, lat_max, lat_step)]
    lon_lines = []
    for lon in values(lon_min, lon_max, lon_step, poi.center_lon):  # whole multiples of the step, absolute
        relative = _relative_lon(lon, poi.center_lon)
        line = [survey.east_north(lat, poi.center_lon + relative) for lat in samples(lat_min, lat_max)]
        lon_lines.append(((lon + 540) % 360 - 180, line))
    return lat_lines, lon_lines, lat_step, lon_step


def image_name(survey):
    """'LFT 509 B 3 b - Signal (5) - 2026-11-08 07-36.png' (Armchair, 2026-11-08 and 09); see survey.file_name."""
    return file_name(survey, ".png")


def view_extent(survey):
    """Meters from the center to the edge of the map: the ring and every ping circle fit."""
    radius, _ = poi_radius(survey)
    extent = radius
    for east, north in counted_positions(survey):
        extent = max(extent, math.hypot(east, north) + SCAN_RADIUS)
    for w in survey.survey_points:
        extent = max(extent, math.hypot(*survey.east_north(w.lat, w.lon)) + 300)
    for p in survey.pings:
        extent = max(extent, math.hypot(*survey.east_north(p.lat, p.lon)) + 300)
    return extent * 1.04


def caption_lines(survey):
    """The caption under the title, in the legend column. No paths and no user name."""
    poi = survey.poi
    radius = f"{poi.radius / 1000:.2f} km (measured)" if poi.radius is not None else "not measured"
    return [f"Surveyed {game_date(survey.created)}",
            f"Radius: {radius}",
            f"Coverage: {coverage(survey).text()}"]


class _Painter:
    """Draws one map onto a painter, in a rectangle."""

    def __init__(self, painter, rect, survey, player=None, font_px=13):
        self.p = painter
        self.survey = survey
        self.player = player  # (lat, lon, heading or None) for the live map
        self.font_px = font_px
        self.title_rect = QRectF(rect.left(), rect.top(), rect.width(), title_height(font_px))
        rect = QRectF(rect.left(), self.title_rect.bottom(), rect.width(), rect.height() - self.title_rect.height())
        legend_width = min(max(rect.width() * 0.27, 230), 420)
        side = min(rect.width() - legend_width, rect.height())
        self.map_rect = QRectF(rect.left(), rect.top() + (rect.height() - side) / 2, side, side)
        self.legend_rect = QRectF(self.map_rect.right() + font_px, rect.top() + font_px,
                                  rect.right() - self.map_rect.right() - 2 * font_px, rect.height() - 2 * font_px)
        self.center = self.map_rect.center()
        self.extent = view_extent(survey)
        self.scale = (side / 2) / self.extent  # pixels per meter

    def font(self, factor=1.0, bold=False):
        font = QFont()
        font.setPixelSize(max(8, round(self.font_px * factor)))
        font.setBold(bold)
        return font

    def at(self, lat, lon):
        east, north = self.survey.east_north(lat, lon)
        return QPointF(self.center.x() + east * self.scale, self.center.y() - north * self.scale)

    def meters(self, m):
        return m * self.scale

    def draw(self):
        p = self.p
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.save()
        p.setClipRect(self.map_rect)
        self._distance_rings()
        self._coverage()
        self._grid()  # above the red, so it can be read everywhere
        self._golden_ring()  # on top of the red tint, which would otherwise turn it orange
        self._grid_labels()  # on top of the ring, so the ring never hides one
        self._survey_points()
        self._pings()
        self._deposits()
        self._custom()
        self._player()
        self._north_and_scale()
        p.restore()
        self._title()
        self._legend()

    # --- the map --------------------------------------------------------------------------

    def _distance_rings(self):
        p = self.p
        p.setPen(QPen(DISTANCE_RING, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setFont(self.font(0.8))
        for km in range(1, int(self.extent / 1000) + 1):
            r = self.meters(km * 1000)
            p.drawEllipse(self.center, r, r)
            if km % 2 == 0 and km * 1000 < self.extent * 0.93:  # labels near the edge would be cut off
                p.setPen(DIM_TEXT)
                p.drawText(QPointF(self.center.x() + 3, self.center.y() - r - 3), f"{km} km")
                p.setPen(QPen(DISTANCE_RING, 1))

    def _screen(self, east, north):
        return QPointF(self.center.x() + east * self.scale, self.center.y() - north * self.scale)

    def _grid(self):
        """Lat/long lines. Their labels, along the top (long) and the right edge (lat), are drawn later."""
        p = self.p
        lat_lines, lon_lines, lat_step, lon_step = grid(self.survey, self.extent)
        rect = self.map_rect
        p.setFont(self.font(0.75))
        metrics = QFontMetricsF(p.font())
        pad = self.font_px * 0.3

        def curve(line):
            return [self._screen(east, north) for east, north in line]

        self.grid_labels = []

        def label(at, text):
            self.grid_labels.append((at, text))

        def crossing(points, x=None, y=None):
            """Where the curve crosses the vertical line x, or the horizontal line y."""
            for a, b in zip(points, points[1:]):
                pa, pb = (a.x(), b.x()) if x is not None else (a.y(), b.y())
                target = x if x is not None else y
                if (pa - target) * (pb - target) <= 0 and pa != pb:
                    t = (target - pa) / (pb - pa)
                    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
            return None

        decimals = step_decimals(lat_step)
        edge_x = rect.right() - pad
        for lat, line in lat_lines:
            points = curve(line)
            p.setPen(QPen(GRID, 1))
            p.drawPolyline(QPolygonF(points))
            at = crossing(points, x=edge_x)
            if at and rect.top() + self.font_px * 2.5 < at.y() < rect.bottom() - self.font_px:
                text = f"lat {degrees_text(lat, decimals)}"
                label(QPointF(edge_x - metrics.horizontalAdvance(text), at.y() - pad), text)
        decimals = step_decimals(lon_step)
        edge_y = rect.top() + pad + metrics.ascent()
        for lon, line in lon_lines:
            points = curve(line)
            p.setPen(QPen(GRID, 1))
            p.drawPolyline(QPolygonF(points))
            at = crossing(points, y=rect.top() + pad)
            text = f"long {degrees_text(lon, decimals)}"
            if at and rect.left() + self.font_px * 3 < at.x() < rect.right() - metrics.horizontalAdvance(text) - pad:
                label(QPointF(at.x() + pad, edge_y), text)

    def _grid_labels(self):
        p = self.p
        p.setFont(self.font(0.75))
        metrics = QFontMetricsF(p.font())
        for at, text in self.grid_labels:
            p.fillRect(metrics.boundingRect(text).translated(at).adjusted(-2, -1, 2, 1), LABEL_BACKING)
            p.setPen(GRID_TEXT)
            p.drawText(at, text)

    def _golden_ring(self):
        p = self.p
        radius, assumed = poi_radius(self.survey)
        r = self.meters(radius)
        pen = QPen(GOLD, max(2.0, self.font_px / 5))
        if assumed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(self.center, r, r)
        if assumed:
            p.setFont(self.font(0.9))
            p.setPen(GOLD)
            text = f"assumed {radius / 1000:g}km radius, not measured"
            width = QFontMetricsF(p.font()).horizontalAdvance(text)
            p.drawText(QPointF(self.center.x() - width / 2, self.center.y() + r + self.font_px * 1.1), text)

    def _coverage(self):
        p = self.p
        positions = counted_positions(self.survey)
        if not positions:
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(scan_area(positions, self.scale, self.center), SCAN_FILL)  # one shape, one tint
        p.setPen(QPen(SCAN_OUTLINE, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = self.meters(SCAN_RADIUS)
        for east, north in positions:
            p.drawEllipse(QPointF(self.center.x() + east * self.scale, self.center.y() - north * self.scale), r, r)

    def _survey_points(self):
        p = self.p
        survey = self.survey
        size = self.font_px * 0.45
        p.setFont(self.font(0.8))
        for w in survey.survey_points:
            at = self.at(w.lat, w.lon)
            ping = survey.done_by(w)
            if ping:
                off = survey.distance(w.lat, w.lon, ping.lat, ping.lon)
                color = FAR_OFF if off > w.tolerance else DONE
            else:
                color = SKIPPED if w.skipped else PENDING
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(at.x() - size, at.y()), QPointF(at.x() + size, at.y()))
            p.drawLine(QPointF(at.x(), at.y() - size), QPointF(at.x(), at.y() + size))
            if w.label == survey.target:
                p.setPen(QPen(TARGET, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(at, size * 2, size * 2)
            p.setPen(color)
            p.drawText(QPointF(at.x() + size + 2, at.y() - size - 1), w.label)

    def _pings(self):
        """Only the "no ping detected" entries get a marker: a ping that counts already shows as its red circle,
        and dots and numbers on top cluttered the map (Armchair, 2026-11-08)."""
        p = self.p
        r = self.font_px * 0.5
        p.setPen(QPen(NO_PING_COLOR, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ping in self.survey.pings:
            if not ping.counts:
                p.drawEllipse(self.at(ping.lat, ping.lon), r, r)

    def _inside(self, point):
        return math.hypot(point.x() - self.center.x(), point.y() - self.center.y()) <= self.map_rect.width() / 2 * 0.96

    def _edge_arrow(self, point, color, text):
        """Something beyond the map: an arrow at the edge pointing at it, with its distance."""
        p = self.p
        dx, dy = point.x() - self.center.x(), point.y() - self.center.y()
        angle = math.atan2(dy, dx)
        r = self.map_rect.width() / 2 * 0.93
        tip = QPointF(self.center.x() + r * math.cos(angle), self.center.y() + r * math.sin(angle))
        size = self.font_px * 0.7
        back = angle + math.pi
        arrow = QPolygonF([tip,
                           QPointF(tip.x() + size * math.cos(back + 0.45), tip.y() + size * math.sin(back + 0.45)),
                           QPointF(tip.x() + size * math.cos(back - 0.45), tip.y() + size * math.sin(back - 0.45))])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawPolygon(arrow)
        p.setPen(color)
        p.setFont(self.font(0.8))
        width = QFontMetricsF(p.font()).horizontalAdvance(text)
        label_at = QPointF(tip.x() - size * 1.6 * math.cos(angle), tip.y() - size * 1.6 * math.sin(angle))
        p.drawText(QPointF(label_at.x() - width / 2, label_at.y() + self.font_px * 0.3), text)

    def _deposits(self):
        p = self.p
        colors = ore_colors(self.survey)
        size = self.font_px * 0.55
        for d in self.survey.deposits:
            color = colors[d.ore]
            at = self.at(*d.position)
            if not self._inside(at):
                away = self.survey.distance_from_center(*d.position)
                self._edge_arrow(at, color, f"{overlay_label(d.label)} {format_distance(away)}")
                continue
            diamond = QPolygonF([QPointF(at.x(), at.y() - size), QPointF(at.x() + size, at.y()),
                                 QPointF(at.x(), at.y() + size), QPointF(at.x() - size, at.y())])
            p.setPen(QPen(color, 2))
            p.setBrush(QBrush(color) if d.size else Qt.BrushStyle.NoBrush)  # filled: size known (Armchair)
            p.drawPolygon(diamond)
            if d.label == self.survey.target:
                p.setPen(QPen(TARGET, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(at, size * 1.8, size * 1.8)
            p.setPen(color)
            p.setFont(self.font(0.85, bold=True))
            p.drawText(QPointF(at.x() + size + 3, at.y() + self.font_px * 0.35), deposit_text(d))

    def _custom(self):
        """The custom destination and its scan circle: on the live map only, since it's never saved (Milestones 8, 9)."""
        survey = self.survey
        if not self.player or not survey.target_is_custom():
            return
        p = self.p
        at = self.at(survey.custom.lat, survey.custom.lon)
        # The scanner's reach from there: the coverage a ping at the destination would add (Milestone 9).
        # Drawn even when the destination is off the map, since part of the circle may be on it.
        pen = QPen(CUSTOM_SCAN, max(1.5, self.font_px / 7))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = self.meters(SCAN_RADIUS)
        p.drawEllipse(at, r, r)
        if not self._inside(at):
            away = survey.distance_from_center(survey.custom.lat, survey.custom.lon)
            self._edge_arrow(at, TARGET, f"Custom {format_distance(away)}")
            return
        size = self.font_px * 0.6
        p.setPen(QPen(TARGET, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(at, size * 1.8, size * 1.8)  # the target ring, as for points and deposits
        p.drawLine(QPointF(at.x() - size, at.y() - size), QPointF(at.x() + size, at.y() + size))
        p.drawLine(QPointF(at.x() - size, at.y() + size), QPointF(at.x() + size, at.y() - size))
        p.setPen(TARGET)
        p.setFont(self.font(0.85, bold=True))
        p.drawText(QPointF(at.x() + size * 1.8 + 3, at.y() + self.font_px * 0.35), "Custom")

    def lat_lon_at(self, point):
        """(lat, lon) under a point on the map, or None outside the map's circle."""
        if not self._inside(point):
            return None
        east = (point.x() - self.center.x()) / self.scale
        north = (self.center.y() - point.y()) / self.scale
        return self.survey.lat_lon(east, north)

    def _player(self):
        if not self.player:
            return
        lat, lon, heading = self.player
        at = self.at(lat, lon)
        if not self._inside(at):
            self._edge_arrow(at, PLAYER, f"you {format_distance(self.survey.distance_from_center(lat, lon))}")
            return
        p = self.p
        size = self.font_px * 1.1
        angle = math.radians(heading if heading is not None else 0)
        def point(a, r):  # a: clockwise from north
            return QPointF(at.x() + r * math.sin(a), at.y() - r * math.cos(a))
        if heading is None:
            p.setPen(QPen(BACKGROUND, 2))
            p.setBrush(QBrush(PLAYER))
            p.drawEllipse(at, size * 0.5, size * 0.5)
            return
        triangle = QPolygonF([point(angle, size), point(angle + 2.5, size * 0.7), point(angle - 2.5, size * 0.7)])
        p.setPen(QPen(BACKGROUND, 2))
        p.setBrush(QBrush(PLAYER))
        p.drawPolygon(triangle)

    def _north_and_scale(self):
        p = self.p
        rect = self.map_rect
        p.setFont(self.font(1.0, bold=True))
        p.setPen(TEXT)
        top = QPointF(rect.left() + self.font_px * 1.5, rect.top() + self.font_px * 1.2)
        size = self.font_px * 0.6
        p.setBrush(QBrush(TEXT))
        p.drawPolygon(QPolygonF([top, QPointF(top.x() - size / 2, top.y() + size), QPointF(top.x() + size / 2, top.y() + size)]))
        p.drawText(QPointF(top.x() - self.font_px * 0.35, top.y() + size + self.font_px * 1.1), "N")
        # Scale bar: 1 km, or 2 km when 1 km would be tiny.
        km = 1 if self.meters(1000) >= self.font_px * 4 else 2
        length = self.meters(km * 1000)
        y = rect.bottom() - self.font_px * 1.2
        x = rect.left() + self.font_px
        p.setPen(QPen(TEXT, 2))
        p.drawLine(QPointF(x, y), QPointF(x + length, y))
        p.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        p.drawLine(QPointF(x + length, y - 4), QPointF(x + length, y + 4))
        p.setFont(self.font(0.8))
        p.drawText(QPointF(x + length + 6, y + self.font_px * 0.3), f"{km} km")

    # --- the title and the legend column ------------------------------------------------------

    def _title(self):
        p = self.p
        rect = self.title_rect
        y = rect.top() + self.font_px * 0.4
        for row, factor in zip(title_lines(self.survey), TITLE_ROWS):
            p.setFont(self.font(factor, bold=True))
            p.setPen(TEXT)
            metrics = QFontMetricsF(p.font())
            row = metrics.elidedText(row, Qt.TextElideMode.ElideRight, rect.width() - 2 * self.font_px)
            y += self.font_px * factor * 1.25
            p.drawText(QPointF(rect.center().x() - metrics.horizontalAdvance(row) / 2, y - metrics.descent()), row)


    def _legend(self):
        p = self.p
        rect = self.legend_rect
        line = self.font_px * 1.45
        y = rect.top() + self.font_px
        x = rect.left()

        def text(s, color=TEXT, factor=1.0, bold=False, indent=0.0):
            nonlocal y
            p.setFont(self.font(factor, bold))
            p.setPen(color)
            metrics = QFontMetricsF(p.font())
            p.drawText(QPointF(x + indent, y), metrics.elidedText(s, Qt.TextElideMode.ElideRight, rect.width() - indent))
            y += line * factor

        for s in caption_lines(self.survey):
            text(s, DIM_TEXT if s.startswith("Surveyed") else TEXT)
        y += line * 0.5
        text("Deposits", factor=1.1, bold=True)
        groups = legend_groups(self.survey)
        if not groups:
            text("none recorded", DIM_TEXT)
        colors = ore_colors(self.survey)
        bottom = rect.bottom() - line * 8  # room for the key
        total, listed = len(self.survey.deposits), 0
        for ore, deposits in groups:
            if y > bottom:
                break
            text(ore, colors[ore], bold=True)
            for d in deposits:
                if y > bottom:
                    break
                lat, lon = d.position
                size = f"size {d.size}" if d.size else "no size"
                density = f", {d.density}" if d.density else ""
                how = "measured" if d.measured else "est."
                text(f"D{d.id}  {size}{density}  {lat:.4f}, {lon:.4f}  {how}", factor=0.9, indent=self.font_px)
                listed += 1
        if listed < total:
            text(f"... {total - listed} more (see the deposit list)", DIM_TEXT, 0.9)
        # Key to the symbols, at the bottom.
        y = rect.bottom() - line * 6.5
        text("Red: within 2 km of a ping", DIM_TEXT, 0.85)
        text("+ planned point (green done, amber off target)", DIM_TEXT, 0.85)
        text("○ no ping detected (no coverage)", DIM_TEXT, 0.85)
        text("◆ deposit with size   ◇ size not recorded", DIM_TEXT, 0.85)
        text("Blue: lat/long grid", DIM_TEXT, 0.85)
        text(f"ASSIST {__version__}", DIM_TEXT, 0.85)


def draw_map(painter, rect, survey, player=None, font_px=13):
    _Painter(painter, QRectF(rect), survey, player, font_px).draw()


def render_image(survey):
    """The map as a fixed-size image, whatever the window's size."""
    width, height = IMAGE_SIZE
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(BACKGROUND)
    painter = QPainter(image)
    draw_map(painter, QRectF(0, 0, width, height), survey, font_px=17)
    painter.end()
    return image


class MapView(QWidget):
    def __init__(self, get_survey, get_player=lambda: None, on_navigate=None, parent=None):
        super().__init__(parent)
        self.get_survey = get_survey
        self.get_player = get_player
        self.on_navigate = on_navigate  # on_navigate(lat, lon): the live map's "Navigate here"; None: no menu
        self.setMinimumSize(640, 460)

    def lat_lon_at(self, point):
        """(lat, lon) under a widget position, or None (outside the map, or no survey)."""
        survey = self.get_survey()
        if survey is None:
            return None
        return _Painter(None, QRectF(self.rect()), survey).lat_lon_at(QPointF(point))

    def navigate_menu(self, point):
        """The right-click menu at a widget position, or None if there's nothing to offer there."""
        where = self.lat_lon_at(point) if self.on_navigate else None
        if where is None:
            return None
        lat, lon = where
        menu = QMenu(self)
        action = menu.addAction(f"Navigate here ({lat:.4f}, {lon:.4f})")
        action.triggered.connect(lambda: self.on_navigate(lat, lon))
        return menu

    def contextMenuEvent(self, event):
        # Only an explicit menu choice sets anything: plain clicks still change nothing (Milestone 7).
        menu = self.navigate_menu(event.pos())
        if menu is not None:
            menu.exec(event.globalPos())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), BACKGROUND)
        survey = self.get_survey()
        if survey is None:
            painter.setPen(TEXT)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No survey to show")
        else:
            draw_map(painter, self.rect(), survey, self.get_player())
        painter.end()


class MapWindow(QWidget):
    """The map in a window of its own. Without survey_id it shows the active survey live; with one, that archived survey."""

    def __init__(self, tracker, survey_id=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.tracker = tracker
        self.survey_id = survey_id
        self.archived = tracker.store.load_archived(survey_id) if survey_id else None
        self.view = MapView(self.survey, self._player, None if survey_id else tracker.set_custom)
        self.save_button = QPushButton("Save image...")
        self.save_button.clicked.connect(self.save_image)
        self.message = QLabel()
        self.message.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.message, stretch=1)
        bottom.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view, stretch=1)
        layout.addLayout(bottom)
        self.resize(1000, 720)
        self._last_player = None
        self._listener = self._survey_changed
        tracker.add_listener(self._listener)
        self.destroyed.connect(lambda: tracker.remove_listener(self._listener))
        self._update_title()

    def closeEvent(self, event):
        self.tracker.remove_listener(self._listener)
        super().closeEvent(event)

    def showEvent(self, event):
        if self._listener not in self.tracker._listeners:
            self.tracker.add_listener(self._listener)
        self.refresh()
        super().showEvent(event)

    def survey(self):
        return self.archived if self.survey_id else self.tracker.survey

    def _player(self):
        if self.survey_id:
            return None  # an archived survey's map shows no live position
        status = self.tracker.status
        if status is None or not status.has_position:
            return None
        return status.latitude, status.longitude, status.heading

    def _survey_changed(self, message):
        if self.survey_id and message:
            fresh = self.tracker.store.load_archived(self.survey_id)
            if fresh is None:
                self.message.setText("This survey isn't in the archive any more: it's the active one now, "
                                     "on the main window's map.")
            else:
                self.archived = fresh
        self.refresh()

    def refresh(self):
        if self.isVisible():
            self._update_title()
            self.view.update()

    def status_changed(self):
        """A new status: redraw the live map only when the player has moved or turned."""
        player = self._player()
        if player != self._last_player:
            self._last_player = player
            self.refresh()

    def _update_title(self):
        survey = self.survey()
        what = "active survey" if not self.survey_id else "archived survey"
        name = (survey.poi.name or survey.poi.body) if survey else "no survey"
        self.setWindowTitle(f"Map - {name} ({what})")

    def default_image_path(self, survey):
        return Path(self.tracker.store.folder) / "maps" / image_name(survey)

    def save_image(self):
        survey = self.survey()
        if survey is None:
            return
        default = self.default_image_path(survey)
        default.parent.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save map image", str(default), "PNG image (*.png)")
        if path:
            self.write_image(survey, path)

    def write_image(self, survey, path):
        if render_image(survey).save(str(path), "PNG"):
            self.message.setText(f"Saved {shown(path)}")
        else:
            self.message.setText(f"Couldn't save {shown(path)}")
