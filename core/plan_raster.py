"""تحديد الغرفة بنقرة داخلها (seed fill) على صورة نقطية للجدران.

الفكرة الهندسية: الغرفة مغلقة بجدرانها ولا ينفذ إليها إلا عبر باب، فنغلق فجوات الأبواب
بعملية closing بنصف قطر (عرض الباب/2)، ثم نملأ من نقطة النقر حتى الجدران.
إن تسرّب الملء (وصل للحدود الخارجية أو تجاوز الحد الأقصى لمساحة الغرفة) يُبلَّغ المستخدم
ليرسم المضلع يدوياً. كل الأرقام حتمية من الهندسة، لا ذكاء اصطناعي.
"""
from __future__ import annotations
import math
import re
from typing import Optional, Sequence

try:
    import cv2
    import numpy as np
    HAVE_CV = True
except Exception:                                   # noqa: BLE001
    HAVE_CV = False

Pt = tuple[float, float]
COLUMN_RE = re.compile(r"column|colom|coloum|col\b|عمود|اعمدة|أعمدة", re.I)


def poly_area(poly: Sequence[Pt]) -> float:
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2.0


def poly_perimeter(poly: Sequence[Pt]) -> float:
    return sum(math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]) for i in range(len(poly)))


class PlanRaster:
    """fills: مضلعات مغلقة تُملأ (جدران)، lines: خطوط تُرسم رفيعة (جدران/أعمدة)، window_lines: خطوط شبابيك (تغلق فتحاتها).
    الإحداثيات بالمتر."""

    def __init__(self, fills: Sequence[Sequence[Pt]], lines: Sequence[Sequence[Pt]], window_lines: Sequence[Sequence[Pt]] = (),
                 res: float = 0.05, max_cells: int = 16_000_000):
        if not HAVE_CV:
            raise RuntimeError("المكتبتان numpy و opencv غير مثبّتتين (pip install numpy opencv-python-headless)")
        pts = [p for g in list(fills) + list(lines) + list(window_lines) for p in g]
        if not pts:
            raise ValueError("لا توجد خطوط جدران لرسمها")
        self.x0 = min(p[0] for p in pts) - 1.0; self.y1 = max(p[1] for p in pts) + 1.0
        x1 = max(p[0] for p in pts) + 1.0; y0 = min(p[1] for p in pts) - 1.0
        while ((x1 - self.x0) / res) * ((self.y1 - y0) / res) > max_cells:
            res *= 1.4
        self.res = res
        self.W = int((x1 - self.x0) / res) + 1; self.H = int((self.y1 - y0) / res) + 1
        w = np.zeros((self.H, self.W), np.uint8)
        for g in fills:
            if len(g) >= 3:
                cv2.fillPoly(w, [np.array([self._px(p) for p in g], np.int32)], 1)
        for g in lines:
            if len(g) >= 2:
                cv2.polylines(w, [np.array([self._px(p) for p in g], np.int32)], False, 1, 1)
        for g in window_lines:
            if len(g) >= 2:
                cv2.polylines(w, [np.array([self._px(p) for p in g], np.int32)], False, 1, 2)
        self.walls = w
        self._cache: dict[int, tuple] = {}

    def _px(self, p: Pt) -> tuple[int, int]:
        return int(round((p[0] - self.x0) / self.res)), int(round((self.y1 - p[1]) / self.res))

    def _sealed(self, r: int):
        """الجدران بعد تمديدها بنصف قطر r (تغلق أي فجوة أضيق من 2r)."""
        if r not in self._cache:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            self._cache[r] = (cv2.dilate(self.walls, k), k)
        return self._cache[r]

    def _try(self, px: int, py: int, r: int, max_area: float):
        sealed, k = self._sealed(r)
        if sealed[py, px]:
            return None, "sealed"
        img = sealed.copy()
        mask = np.zeros((self.H + 2, self.W + 2), np.uint8)
        cv2.floodFill(img, mask, (px, py), 2, flags=4 | (2 << 8))
        core = (img == 2).astype(np.uint8)
        area_core = float(core.sum()) * self.res ** 2
        if core[0, :].any() or core[-1, :].any() or core[:, 0].any() or core[:, -1].any() or area_core > max_area:
            return None, ("leak", area_core)
        sq = np.ones((2 * r + 1, 2 * r + 1), np.uint8)          # مربّع لاسترجاع زوايا الغرفة المستديرة
        region = ((cv2.dilate(core, sq) == 1) & (self.walls == 0)).astype(np.uint8)
        _n, lab = cv2.connectedComponents(region, connectivity=4)
        return (lab == lab[py, px]).astype(np.uint8), "ok"

    def auto_rooms(self, door_gap: float = 1.5, min_area: float = 1.0, max_area: float = 150.0,
                   exclude: Sequence[Sequence[Pt]] = ()) -> list[dict]:
        """اكتشاف كل الغرف المغلقة دفعة واحدة (للمخططات بلا طبقة فراغات).
        تُغلق فتحات الأبواب بنصف قطر (عرض الفجوة/2)، وتُقسَّم المساحات الفارغة إلى مناطق متصلة؛ كل منطقة
        لا تلمس حدود الرسم ومساحتها معقولة تُعامل كنقرة داخل غرفة (أبعد نقطة عن الجدران) عبر fill_from_seed."""
        out: list[dict] = []
        taken = np.zeros((self.H, self.W), np.uint8)              # ما غطّته الغرف المكتشفة (والمحددة مسبقاً)
        for poly in exclude:
            if len(poly) >= 3:
                cv2.fillPoly(taken, [np.array([self._px(tuple(p)) for p in poly], np.int32)], 1)
        # من الفجوة الأكبر للأصغر: الغرف الصغيرة (حمامات) تختفي عند الإغلاق الواسع فتظهر في الجولات التالية
        for gap in sorted({round(g, 2) for g in (door_gap, 1.2, 1.0, 0.8, 0.6) if g <= door_gap + 1e-9}, reverse=True):
            r = max(1, int(round(gap / 2.0 / self.res)))
            sealed, _k = self._sealed(r)
            free = ((sealed == 0) & (taken == 0)).astype(np.uint8)
            n, lab, stats, _c = cv2.connectedComponentsWithStats(free, connectivity=4)
            dist = cv2.distanceTransform(free, cv2.DIST_L2, 3)
            for i in range(1, n):
                x, y, w, h, cnt = stats[i]
                if x == 0 or y == 0 or x + w >= self.W or y + h >= self.H:      # الخارج
                    continue
                if cnt * self.res ** 2 < min_area * 0.3 or cnt * self.res ** 2 > max_area:
                    continue
                sub = np.where(lab[y:y + h, x:x + w] == i, dist[y:y + h, x:x + w], 0)
                py, px = np.unravel_index(int(np.argmax(sub)), sub.shape)
                if taken[y + py, x + px]:
                    continue
                sx, sy = self.x0 + (x + px) * self.res, self.y1 - (y + py) * self.res
                res = self.fill_from_seed(sx, sy, gap, max_area)
                if not (res.get("ok") and min_area <= res["area"] <= max_area):
                    continue
                m = np.zeros_like(taken)
                cv2.fillPoly(m, [np.array([self._px(p) for p in res["poly"]], np.int32)], 1)
                if (m & taken).sum() > 0.2 * m.sum():                  # نفس غرفة سابقة
                    continue
                taken |= m
                res["seed"] = (sx, sy)
                out.append(res)
        return out

    def fill_from_seed(self, x: float, y: float, door_gap: float = 1.5, max_area: float = 150.0) -> dict:
        """يملأ من نقطة النقر. يبدأ بأكبر عرض فجوة ويتدرّج نزولاً حتى يبقى للنقطة حيّز (للغرف الضيقة).
        يعيد {'ok','reason','message','poly','area','perimeter','gap'}؛ poly بالمتر."""
        px, py = self._px((x, y))
        if not (0 <= px < self.W and 0 <= py < self.H):
            return {"ok": False, "reason": "outside", "message": "النقطة خارج الرسم"}
        if self.walls[py, px]:
            return {"ok": False, "reason": "on_wall", "message": "النقر وقع على جدار؛ انقر داخل الغرفة"}
        gaps = [g for g in (door_gap, 1.4, 1.2, 1.0, 0.9, 0.8, 0.7, 0.6) if g <= door_gap + 1e-9]
        gaps = sorted(set(round(g, 2) for g in gaps), reverse=True)
        last_leak = 0.0; region = None; used = door_gap
        for g in gaps:
            r = max(1, int(round(g / 2.0 / self.res)))
            region, st = self._try(px, py, r, max_area)
            if region is not None:
                used = g; break
            if isinstance(st, tuple):
                last_leak = max(last_leak, st[1])
                break                     # تسرّب عند أكبر فجوة متاحة بعد وجود حيّز: لا فائدة من التصغير
        if region is None:
            if last_leak:
                return {"ok": False, "reason": "leak",
                        "message": f"الغرفة غير مغلقة (تسرّب الملء إلى مساحة {last_leak:.0f} م²). زد «عرض الفجوة» أو ارسم الغرفة يدوياً."}
            return {"ok": False, "reason": "sealed", "message": "تعذّر تحديد الغرفة (ضيقة جداً)؛ ارسمها يدوياً"}
        area = float(region.sum()) * self.res ** 2
        cs, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs:
            return {"ok": False, "reason": "empty", "message": "لم يُستخرج مضلع"}
        c = max(cs, key=cv2.contourArea)
        ap = cv2.approxPolyDP(c, max(1.0, 0.03 / self.res), True)
        poly = [(self.x0 + float(p[0][0]) * self.res, self.y1 - float(p[0][1]) * self.res) for p in ap]
        if len(poly) < 3:
            return {"ok": False, "reason": "empty", "message": "مضلع غير صالح"}
        return {"ok": True, "poly": poly, "area": area, "perimeter": poly_perimeter(poly), "gap": used}


def collect_geometry(ents, role, scale: float, flatten, bbox: Optional[tuple] = None) -> dict:
    """يجمع الهندسة بالمتر لرسمها وتحليلها: walls_fill, walls_line, windows, doors, columns, sanitary."""
    out = {"walls_fill": [], "walls_line": [], "windows": [], "doors": [], "columns": [], "sanitary": []}
    for e in ents:
        if e.kind not in ("POLY", "LINE") or len(e.verts) < 2:
            continue
        r = role(e.layer)
        is_col = bool(COLUMN_RE.search(e.layer))
        if r in ("walls", "partitions"):
            key = "walls_fill" if (e.kind == "POLY" and e.closed) else "walls_line"
        elif r == "windows":
            key = "windows"
        elif r == "doors":
            key = "doors"
        elif r == "sanitary":
            key = "sanitary"
        elif is_col:
            key = "columns"
        else:
            continue
        pts = flatten(e.verts, bool(e.kind == "POLY" and e.closed))
        if e.kind == "POLY" and e.closed and pts:
            pts = pts + [pts[0]]
        g = [(x * scale, y * scale) for x, y in pts]
        out[key].append(g)
    if bbox is None:
        wp = [p for g in out["walls_fill"] + out["walls_line"] for p in g]
        if wp:
            bbox = (min(p[0] for p in wp) - 3, min(p[1] for p in wp) - 3, max(p[0] for p in wp) + 3, max(p[1] for p in wp) + 3)
    if bbox:
        def inside(g):
            return any(bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3] for x, y in g)
        for k in ("windows", "doors", "columns", "sanitary"):
            out[k] = [g for g in out[k] if inside(g)]
    out["bbox"] = bbox
    return out
