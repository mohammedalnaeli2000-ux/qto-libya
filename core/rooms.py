"""استخراج الفراغات (الغرف) من خطوط الجدران عند غياب مضلعات مغلقة.

الخطوات (حتمية بالكامل):
 1) تحويل الخطوط/البولي لاين إلى قطع مستقيمة بالمتر.
 2) تقطيع القطع عند التقاطعات والملامسة (noding).
 3) جسر فجوات الأبواب والشبابيك (أطراف معلّقة متقابلة أو امتداد شعاعي).
 4) حذف الفروع المعلّقة ثم استخراج الأوجه (faces) من رسم مستوٍ.
 5) تصفية الأوجه الرفيعة (جسم الجدار في الخط المزدوج) والوجه الخارجي.
"""
from __future__ import annotations
import math
from collections import defaultdict
from typing import Iterable, Sequence

Pt = tuple[float, float]
EPS = 0.02          # 2 سم: دمج النقاط والملامسة


def _cell(p: Pt, size: float) -> tuple[int, int]:
    return int(math.floor(p[0] / size)), int(math.floor(p[1] / size))


def _seg_inter(a: Pt, b: Pt, c: Pt, d: Pt) -> Pt | None:
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return a[0] + t * rx, a[1] + t * ry
    return None


def _proj_param(p: Pt, a: Pt, b: Pt) -> tuple[float, float]:
    """(t على القطعة، مسافة p عن القطعة)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return 0.0, math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    tc = max(0.0, min(1.0, t))
    return t, math.hypot(p[0] - (a[0] + tc * dx), p[1] - (a[1] + tc * dy))


def node_segments(segs: Sequence[tuple[Pt, Pt]], eps: float = EPS) -> list[tuple[Pt, Pt]]:
    """يقطّع القطع عند كل تقاطع/ملامسة ويعيد قطعاً بلا تقاطعات داخلية."""
    segs = [(a, b) for a, b in segs if math.hypot(a[0] - b[0], a[1] - b[1]) > eps]
    size = 2.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (a, b) in enumerate(segs):
        x0, x1 = sorted((a[0], b[0])); y0, y1 = sorted((a[1], b[1]))
        for cx in range(int(math.floor((x0 - eps) / size)), int(math.floor((x1 + eps) / size)) + 1):
            for cy in range(int(math.floor((y0 - eps) / size)), int(math.floor((y1 + eps) / size)) + 1):
                grid[(cx, cy)].append(i)
    splits: list[list[float]] = [[0.0, 1.0] for _ in segs]
    done = set()
    for ids in grid.values():
        for ii in range(len(ids)):
            i = ids[ii]
            a, b = segs[i]
            for jj in range(ii + 1, len(ids)):
                j = ids[jj]
                if (i, j) in done:
                    continue
                done.add((i, j))
                c, d = segs[j]
                p = _seg_inter(a, b, c, d)
                if p is not None:
                    ti, _ = _proj_param(p, a, b); tj, _ = _proj_param(p, c, d)
                    splits[i].append(ti); splits[j].append(tj)
                for q in (c, d):            # طرف قطعة يلامس منتصف أخرى
                    t, dist = _proj_param(q, a, b)
                    if dist <= eps and 0 < t < 1:
                        splits[i].append(t)
                for q in (a, b):
                    t, dist = _proj_param(q, c, d)
                    if dist <= eps and 0 < t < 1:
                        splits[j].append(t)
    out = []
    for (a, b), ts in zip(segs, splits):
        ts = sorted(set(round(min(1.0, max(0.0, t)), 9) for t in ts))
        pts = [(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])) for t in ts]
        for p, q in zip(pts, pts[1:]):
            if math.hypot(p[0] - q[0], p[1] - q[1]) > eps / 2:
                out.append((p, q))
    return out


class Graph:
    def __init__(self, segs: Iterable[tuple[Pt, Pt]], eps: float = EPS):
        self.eps = eps
        self.pts: list[Pt] = []
        self._idx: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.adj: dict[int, set[int]] = defaultdict(set)
        for a, b in segs:
            u, v = self._node(a), self._node(b)
            if u != v:
                self.adj[u].add(v); self.adj[v].add(u)

    def _node(self, p: Pt) -> int:
        cx, cy = _cell(p, self.eps * 2)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in self._idx.get((cx + dx, cy + dy), ()):
                    q = self.pts[k]
                    if math.hypot(p[0] - q[0], p[1] - q[1]) <= self.eps:
                        return k
        self.pts.append(p)
        self._idx[(cx, cy)].append(len(self.pts) - 1)
        return len(self.pts) - 1

    def prune(self):
        stack = [n for n, s in self.adj.items() if len(s) <= 1]
        while stack:
            n = stack.pop()
            for m in list(self.adj.get(n, ())):
                self.adj[m].discard(n)
                if len(self.adj[m]) <= 1:
                    stack.append(m)
            self.adj[n] = set()
        self.adj = defaultdict(set, {n: s for n, s in self.adj.items() if s})

    def faces(self) -> list[list[int]]:
        order: dict[int, list[int]] = {}
        for n, nb in self.adj.items():
            px, py = self.pts[n]
            order[n] = sorted(nb, key=lambda m: math.atan2(self.pts[m][1] - py, self.pts[m][0] - px))
        seen = set()
        res = []
        for u in list(self.adj):
            for v in self.adj[u]:
                if (u, v) in seen:
                    continue
                face = []
                a, b = u, v
                guard = 0
                while (a, b) not in seen and guard < 100000:
                    seen.add((a, b)); face.append(a)
                    lst = order[b]
                    k = lst.index(a)
                    c = lst[(k - 1) % len(lst)]      # أقرب جار باتجاه عقارب الساعة من (b→a)
                    a, b = b, c
                    guard += 1
                if len(face) >= 3:
                    res.append(face)
        return res


def signed_area(poly: Sequence[Pt]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2.0


def _dangling(g: Graph):
    return [n for n, s in g.adj.items() if len(s) == 1]


def bridge_gaps(segs: list[tuple[Pt, Pt]], max_gap: float, eps: float = EPS) -> tuple[list[tuple[Pt, Pt]], int]:
    """يجسر الفجوات (أبواب/شبابيك) بين الأطراف المعلّقة وبالامتداد الشعاعي. يعيد (قطع الجسور، العدد)."""
    nsegs = node_segments(segs, eps)
    g = Graph(nsegs, eps)
    dang = _dangling(g)
    bridges: list[tuple[Pt, Pt]] = []
    used = set()
    info = {}
    for n in dang:
        m = next(iter(g.adj[n]))
        p, q = g.pts[n], g.pts[m]
        L = math.hypot(p[0] - q[0], p[1] - q[1]) or 1.0
        info[n] = (p, ((p[0] - q[0]) / L, (p[1] - q[1]) / L))   # اتجاه الخروج من الطرف
    # 1) طرف لطرف
    cand = []
    for i, n in enumerate(dang):
        for m in dang[i + 1:]:
            p, dp = info[n]; q, dq = info[m]
            d = math.hypot(p[0] - q[0], p[1] - q[1])
            if d <= eps or d > max_gap:
                continue
            ux, uy = (q[0] - p[0]) / d, (q[1] - p[1]) / d
            # الاتجاه لا يشترط التطابق تماماً: إما امتداد (محاذٍ) أو عمودي كجدار قصير للباب
            align = dp[0] * ux + dp[1] * uy
            align2 = dq[0] * -ux + dq[1] * -uy
            if align > 0.8 and align2 > 0.8:
                cand.append((d, n, m))
    for d, n, m in sorted(cand):
        if n in used or m in used:
            continue
        used.add(n); used.add(m)
        bridges.append((g.pts[n], g.pts[m]))
    # 2) امتداد شعاعي لطرف حتى أقرب قطعة
    for n in dang:
        if n in used:
            continue
        p, dp = info[n]
        best = None
        for a, b in nsegs:
            if (a == p or b == p):
                continue
            far = (p[0] + dp[0] * max_gap, p[1] + dp[1] * max_gap)
            hit = _seg_inter(p, far, a, b)
            if hit is None:
                continue
            d = math.hypot(hit[0] - p[0], hit[1] - p[1])
            if eps < d <= max_gap and (best is None or d < best[0]):
                best = (d, hit)
        if best:
            bridges.append((p, best[1])); used.add(n)
    return bridges, len(bridges)


def _offset_inward(poly: Sequence[Pt], d: float) -> tuple[float, float]:
    """(مساحة، محيط) بعد إزاحة المضلع للداخل بمسافة d (تقريب هندسي دقيق للزوايا)."""
    n = len(poly)
    A = abs(signed_area(poly)); P = 0.0
    ccw = signed_area(poly) > 0
    tsum = 0.0
    for i in range(n):
        p0, p1, p2 = poly[i - 1], poly[i], poly[(i + 1) % n]
        P += math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        a1 = math.atan2(p1[1] - p0[1], p1[0] - p0[0]); a2 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        turn = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        if not ccw:
            turn = -turn
        tsum += math.tan(max(-1.4, min(1.4, turn / 2)))
    return max(0.0, A - P * d + d * d * tsum), max(0.0, P - 2 * d * tsum)


def detect_rooms(segs: Sequence[tuple[Pt, Pt]], min_area: float = 1.0, max_gap: float = 1.3,
                 inset: float = 0.0, min_width: float = 0.6) -> dict:
    """يعيد {'rooms': [(poly, gross_area, net_area, net_perimeter)], 'thin': عدد الأوجه الرفيعة,
    'bridges': عدد الجسور, 'faces': ...}.  الإحداثيات بالمتر."""
    segs = list(segs)
    bridges, nb = bridge_gaps(segs, max_gap) if max_gap > 0 else ([], 0)
    nsegs = node_segments(segs + bridges)
    g = Graph(nsegs)
    g.prune()
    rooms, thin = [], 0
    for face in g.faces():
        poly = [g.pts[i] for i in face]
        sa = signed_area(poly)
        if sa <= 1e-6:                          # الوجه الخارجي/السالب
            continue
        per = sum(math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]) for i in range(len(poly)))
        if per <= 0:
            continue
        if 2 * sa / per < min_width:           # رفيع: جسم جدار أو فراغ ضيق جداً
            if sa > 0.05:
                thin += 1
            continue
        if inset > 0:
            na, nper = _offset_inward(poly, inset)
        else:
            na, nper = sa, per
        if na < min_area:
            continue
        rooms.append((poly, sa, na, nper))
    return {"rooms": rooms, "thin": thin, "bridges": nb}
