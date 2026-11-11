"""دوال هندسية حتمية: مساحة ومحيط مع الأقواس (bulge) ونقطة داخل مضلع وتحويلات."""
from __future__ import annotations
import math
from typing import Sequence

Vert = tuple[float, float, float]   # x, y, bulge


def _seg_geom(b: float, c: float) -> tuple[float, float]:
    """(مساحة القطعة الدائرية الموجبة، طول القوس) لقوس بانتفاخ |b| ووتر c."""
    ab = abs(b)
    if ab < 1e-12 or c < 1e-12:
        return 0.0, c
    theta = 4.0 * math.atan(ab)
    r = c / (2.0 * math.sin(theta / 2.0))
    return 0.5 * r * r * (theta - math.sin(theta)), r * theta


def _edges(verts: Sequence[Vert], closed: bool):
    n = len(verts)
    last = n if closed else n - 1
    for i in range(last):
        x1, y1, b = verts[i]
        x2, y2, _ = verts[(i + 1) % n]
        yield x1, y1, x2, y2, b


def polygon_area(verts: Sequence[Vert]) -> float:
    s = 0.0
    arcs = 0.0
    for x1, y1, x2, y2, b in _edges(verts, True):
        s += x1 * y2 - x2 * y1
        seg, _ = _seg_geom(b, math.hypot(x2 - x1, y2 - y1))
        arcs += seg if b > 0 else -seg
    return abs(s / 2.0 + arcs)


def polyline_length(verts: Sequence[Vert], closed: bool) -> float:
    total = 0.0
    for x1, y1, x2, y2, b in _edges(verts, closed):
        c = math.hypot(x2 - x1, y2 - y1)
        total += _seg_geom(b, c)[1]
    return total


def flatten(verts: Sequence[Vert], closed: bool = True, max_deg: float = 10.0) -> list[tuple[float, float]]:
    """تحويل الأقواس إلى نقاط لاستعمالها في اختبار الاحتواء والمسافات."""
    out: list[tuple[float, float]] = []
    for x1, y1, x2, y2, b in _edges(verts, closed):
        out.append((x1, y1))
        if abs(b) > 1e-12:
            c = math.hypot(x2 - x1, y2 - y1)
            if c < 1e-12:
                continue
            theta = 4.0 * math.atan(b)             # بإشارة الاتجاه
            r = c / (2.0 * math.sin(abs(theta) / 2.0))
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            dx, dy = (x2 - x1) / c, (y2 - y1) / c
            h = r * math.cos(abs(theta) / 2.0)     # بعد المركز عن الوتر
            side = 1.0 if b > 0 else -1.0          # المركز على يسار الاتجاه إن كان b>0
            cx, cy = mx - dy * h * side, my + dx * h * side
            a0 = math.atan2(y1 - cy, x1 - cx)
            steps = max(1, int(abs(math.degrees(theta)) / max_deg))
            for k in range(1, steps):
                a = a0 + theta * k / steps
                out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    if not closed and verts:
        out.append((verts[-1][0], verts[-1][1]))
    return out


def point_in_polygon(pt: tuple[float, float], poly: Sequence[tuple[float, float]]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def dist_point_segment(p, a, b) -> float:
    px, py = p; ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_point_polygon(p, poly: Sequence[tuple[float, float]]) -> float:
    """أقل مسافة من نقطة إلى حدود المضلع."""
    n = len(poly)
    return min(dist_point_segment(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def centroid(poly: Sequence[tuple[float, float]]) -> tuple[float, float]:
    a = cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        a += f; cx += (x1 + x2) * f; cy += (y1 + y2) * f
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (3 * a), cy / (3 * a))


class Affine:
    """تحويل أفيني: (x,y) -> (a x + c y + tx, b x + d y + ty)."""
    __slots__ = ("a", "b", "c", "d", "tx", "ty")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0):
        self.a, self.b, self.c, self.d, self.tx, self.ty = a, b, c, d, tx, ty

    @staticmethod
    def insert(x, y, sx, sy, rot_deg, bx=0.0, by=0.0) -> "Affine":
        r = math.radians(rot_deg)
        cr, sr = math.cos(r), math.sin(r)
        # p' = R * S * (p - base) + t
        a, b, c, d = cr * sx, sr * sx, -sr * sy, cr * sy
        return Affine(a, b, c, d, x - (a * bx + c * by), y - (b * bx + d * by))

    def apply(self, x, y):
        return self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty

    def then(self, outer: "Affine") -> "Affine":
        """self أولاً ثم outer."""
        return Affine(
            outer.a * self.a + outer.c * self.b, outer.b * self.a + outer.d * self.b,
            outer.a * self.c + outer.c * self.d, outer.b * self.c + outer.d * self.d,
            outer.a * self.tx + outer.c * self.ty + outer.tx,
            outer.b * self.tx + outer.d * self.ty + outer.ty)

    @property
    def det(self) -> float:
        return self.a * self.d - self.b * self.c

    @property
    def scale_x(self) -> float:
        return math.hypot(self.a, self.b)

    @property
    def scale_y(self) -> float:
        return math.hypot(self.c, self.d)
