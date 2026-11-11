"""من ملف DXF إلى مسقط: فراغات (مساحة/محيط/اسم) + فتحات + أطوال جدران + أجهزة صحية."""
from __future__ import annotations
import math
import re
from pathlib import Path
from typing import Optional

from . import config
from .dxf_reader import Drawing, Ent, INSUNITS_NAME, INSUNITS_TO_M, read_dxf
from .geometry import (centroid, dist_point_polygon, flatten, point_in_polygon,
                       polygon_area, polyline_length)
from .models import Opening, Plan, Space
from .textnorm import norm_ar
from . import rooms as _rooms

ROLES = ["spaces", "walls", "partitions", "doors", "windows", "texts", "sanitary", "ignore", "other"]
UNIT_FALLBACK = {"mm": 0.001, "cm": 0.01, "m": 1.0}
ATTR_W = {"W", "WIDTH", "العرض", "عرض"}
ATTR_H = {"H", "HEIGHT", "الارتفاع", "ارتفاع"}
ATTR_S = {"SILL", "الحلق", "ارتفاع_الحلق"}


def suggest_layer_roles(layers: list[str]) -> dict[str, str]:
    """اقتراح دور لكل طبقة بالأنماط (الترتيب: ignore ثم الأدق فالأعم)."""
    pats = {r: [re.compile(norm_ar(p), re.I) for p in ps] for r, ps in config.load("layer_patterns.json").items()}
    order = ["ignore", "partitions", "walls", "windows", "doors", "sanitary", "spaces", "texts"]
    out = {}
    for ly in layers:
        n = norm_ar(ly)
        role = "other"
        for r in order:
            if any(p.search(n) for p in pats[r]):
                role = r; break
        out[ly] = role
    return out


def drawing_layers(dr: Drawing) -> list[str]:
    return sorted({e.layer for e in dr.iter_entities()})


def unit_scale(dr: Drawing, settings: dict) -> tuple[float, str]:
    if settings.get("unit_override"):
        u = settings["unit_override"]
        return UNIT_FALLBACK[u], f"مفروضة يدوياً: {u}"
    if dr.insunits in INSUNITS_TO_M:
        return INSUNITS_TO_M[dr.insunits], f"من الملف: {INSUNITS_NAME.get(dr.insunits, dr.insunits)}"
    u = settings["fallback_unit"]
    return UNIT_FALLBACK[u], f"الملف بلا وحدات؛ استُخدمت الاحتياطية: {u}"


def _num_to_m(v: float) -> float:
    """تخمين وحدة قيمة نصية: ≤10 متر، 10-500 سم، >500 مم."""
    if v <= 10: return v
    if v <= 500: return v / 100.0
    return v / 1000.0


def _parse_float(s: str) -> Optional[float]:
    m = re.search(r"\d+(?:[.,]\d+)?", s or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _opening_size(ent: Ent, kind: str, dr: Drawing, scale: float, st: dict) -> tuple[float, float, float, bool, str]:
    """(عرض، ارتفاع، حلق) بالمتر + هل مقدّر + المصدر."""
    dh = st["door_height"] if kind == "door" else st["window_height"]
    sill = 0.0 if kind == "door" else st["window_sill"]
    w = h = None
    src = ""
    a = ent.attribs
    for k, v in a.items():
        f = _parse_float(v)
        if f is None: continue
        if k in ATTR_W: w = _num_to_m(f); src = "attribute"
        elif k in ATTR_H: h = _num_to_m(f); src = "attribute"
        elif k in ATTR_S and kind == "window": sill = _num_to_m(f)
    if w is None and not ent.name.startswith("*"):      # *U427: اسم تلقائي لبلوك ديناميكي، أرقامه ليست مقاساً
        m = re.search(r"(\d{2,4})\s*[xX×*]\s*(\d{2,4})", ent.name)
        if m:
            w, h = _num_to_m(float(m.group(1))), _num_to_m(float(m.group(2))); src = "name"
        else:
            m = re.search(r"[A-Za-z_\-؀-ۿ](\d{2,3})(?!\d)", ent.name)
            if m: w = _num_to_m(float(m.group(1))); src = "name"
    if w is None:
        ext = dr.block_extents(ent.name)
        if ext and ent.xf is not None:
            dx = (ext[2] - ext[0]) * ent.xf.scale_x * scale
            dy = (ext[3] - ext[1]) * ent.xf.scale_y * scale
            w = max(dx, dy); src = "extents"
    estimated = False
    if w is None or w <= 0:
        w = 0.9 if kind == "door" else 1.2; src = "default"; estimated = True
    if h is None:
        h = dh; estimated = True
    if src in ("extents", "default"):
        estimated = True
    return round(w, 3), round(h, 3), round(sill, 3), estimated, src


def _closed_polys(ents: list[Ent], tol: float):
    for e in ents:
        if e.kind != "POLY" or len(e.verts) < 3:
            continue
        closed = e.closed
        if not closed:
            (x1, y1, _), (x2, y2, _) = e.verts[0], e.verts[-1]
            closed = math.hypot(x1 - x2, y1 - y2) <= tol
            if closed and len(e.verts) > 3:
                e = Ent("POLY", e.layer, verts=e.verts[:-1], closed=True)
        if closed:
            yield e


def _text_is_label(t: str) -> bool:
    t = t.strip()
    if not t or re.fullmatch(r"[\d\s.,×xX*\-/²m]+", t): return False
    if re.search(r"\d+(?:[.,]\d+)?\s*(?:م2|م²|m2|sqm)", norm_ar(t)): return False
    return True


def resolve_scale(dr: Drawing, ents, role, st: dict, scale: float, note: str):
    """يصحّح الوحدة تلقائياً إن ناقضت أبعاد المبنى المعقولة. يعيد (scale, note, تحذير|None)."""
    if st.get("unit_override"):
        return scale, note, None
    wp = [v for e in ents if role(e.layer) in ("walls", "partitions") and e.kind in ("POLY", "LINE") for v in e.verts[:50]]
    if not wp:
        return scale, note, None
    span = max(max(v[0] for v in wp) - min(v[0] for v in wp), max(v[1] for v in wp) - min(v[1] for v in wp))
    span_m = span * scale
    if span_m < 3.0 and 8.0 <= span <= 800.0:        # مبنى أقل من 3 م لا يُعقل: الرسم غالباً بالمتر
        n2 = "الرسم بدا بالمتر (الوحدة المعلنة في الملف لا تناسب أبعاد المبنى)؛ طُبّق المتر تلقائياً"
        return 1.0, n2, "⚠ " + n2
    if span_m > 2000.0:
        return scale, note, f"⚠ أبعاد المبنى {span_m:.0f} م غير معقولة؛ راجع وحدة الرسم في إعدادات المشروع"
    return scale, note, None


def parse_plan(path: str | Path, layer_map: dict[str, str], settings: Optional[dict] = None,
               floor: str = "", dr: Optional[Drawing] = None, manual: Optional[list[dict]] = None) -> Plan:
    st = settings or config.load_settings()
    dr = dr or read_dxf(path)
    scale, note = unit_scale(dr, st)
    manual = manual or []
    plan = Plan(file=str(path), floor=floor, unit_scale=scale, unit_note=note)
    ents = list(dr.iter_entities())
    role = lambda ly: layer_map.get(ly, "other")
    scale, note, uwarn = resolve_scale(dr, ents, role, st, scale, note)
    if uwarn:
        plan.unit_scale, plan.unit_note = scale, note
        plan.warnings.append(uwarn)
    tol_close = 0.01 / scale if scale else 0.01        # 1 سم بوحدات الرسم

    # --- الفراغات ---
    cands: list[Space] = []
    seen = set()
    for e in _closed_polys([x for x in ents if role(x.layer) == "spaces"], tol_close):
        area = polygon_area(e.verts) * scale * scale
        if area < st["min_space_area_m2"]:
            continue
        flat = flatten(e.verts, True)
        c = centroid(flat)
        key = (round(area, 2), round(c[0] * scale, 1), round(c[1] * scale, 1))
        if key in seen:
            continue
        seen.add(key)
        sp = Space(id="", name="", area=round(area, 4), perimeter=round(polyline_length(e.verts, True) * scale, 4),
                   polygon=flat, layer=e.layer, floor=floor, source_file=Path(str(path)).name)
        cands.append(sp)
    # حذف حدود المبنى الخارجية إن احتوت فراغات أخرى بمجموع مساحة مقارب
    drop = set()
    for i, a in enumerate(cands):
        inner = [b for j, b in enumerate(cands) if j != i and point_in_polygon(centroid(b.polygon), a.polygon) and b.area < a.area]
        if len(inner) >= 2 and a.area >= 0.9 * sum(b.area for b in inner):
            drop.add(i); plan.warnings.append(f"استُبعد مضلع كبير ({a.area:.1f} م²) لأنه يحوي فراغات أخرى (حدود مبنى)")
    cands = [s for i, s in enumerate(cands) if i not in drop]
    mode_sp = st.get("space_detection", "auto")           # auto | closed | walls | off
    wall_segs = []
    if mode_sp != "off":
        for e in ents:
            if e.kind in ("POLY", "LINE") and role(e.layer) in ("walls", "partitions") and len(e.verts) >= 2:
                pts = flatten(e.verts, bool(e.kind == "POLY" and e.closed))
                if e.kind == "POLY" and e.closed and pts:
                    pts = pts + [pts[0]]
                for a, b in zip(pts, pts[1:]):
                    wall_segs.append(((a[0] * scale, a[1] * scale), (b[0] * scale, b[1] * scale)))
    det = None
    if not manual and wall_segs and len(wall_segs) <= 60000 and mode_sp in ("auto", "walls") and (mode_sp == "walls" or not cands or st.get("wall_mode") == "auto"):
        det = _rooms.detect_rooms(wall_segs, min_area=0.0, max_gap=st.get("door_gap_m", 1.3))
    wm = st.get("wall_mode", "centerline")
    if wm == "auto":
        wm = "centerline"
        wl = [e for e in ents if role(e.layer) in ("walls", "partitions") and e.kind in ("POLY", "LINE")]
        if wl and sum(1 for e in wl if e.kind == "POLY" and e.closed) / len(wl) > 0.6:
            wm = "closed_polygons"                       # جدران كمستطيلات مغلقة: الطول = المحيط ÷ 2
        elif det and det["thin"] >= max(3, 0.5 * max(1, len(det["rooms"]))):
            wm = "double_line"
        plan.warnings.append("نمط الجدران المكتشف: " + {"double_line": "خطان لكل جدار", "closed_polygons": "مستطيلات مغلقة لكل قطعة جدار"}.get(wm, "خط مركزي لكل جدار"))
    plan.wall_mode = wm
    if det and (mode_sp == "walls" or not cands):
        d_in = 0.0
        if wm == "centerline":
            th = st.get("wall_thickness_cm", {})
            d_in = (th.get("walls", 20) + th.get("partitions", 10)) / 400.0
        k = 0
        for poly, gross, _n, _p in det["rooms"]:
            na, nper = _rooms._offset_inward(poly, d_in) if d_in else (gross, sum(math.hypot(poly[i][0]-poly[i-1][0], poly[i][1]-poly[i-1][1]) for i in range(len(poly))))
            if na < st["min_space_area_m2"]:
                continue
            k += 1
            cands.append(Space(id="", name="", area=round(na, 4), perimeter=round(nper, 4),
                               polygon=[(x / scale, y / scale) for x, y in poly], layer="(من الجدران)", floor=floor,
                               source_file=Path(str(path)).name))
        if k:
            plan.warnings.append(f"استُخرج {k} فراغ من خطوط الجدران (جسر {det['bridges']} فجوة باب/شباك). راجع المخطط في معاينة الفراغات وعدّل ما يلزم.")
            if wm == "centerline":
                plan.warnings.append("المساحات صافية تقريباً بخصم نصف سمك الجدار من الخط المركزي.")
    cands.sort(key=lambda s: (round(-centroid(s.polygon)[1], 1), centroid(s.polygon)[0]))  # من الأعلى لليسار
    for n, sp in enumerate(cands, 1):
        sp.id = f"{floor or 'F'}-S{n:02d}"
    # «غير مصنّف» (من الاكتشاف التلقائي) لا يُعتبر قرار مستخدم: يُسمّى من النصوص ويظهر في أسئلة التصنيف
    msp = [Space(id=m["id"], name=m.get("name", ""), type=m.get("type", "unknown"),
                 confidence=0.0 if m.get("type", "unknown") == "unknown" else 1.0,
                 classified_by="" if m.get("type", "unknown") == "unknown" else "user",
                 area=round(float(m["area"]), 4), perimeter=round(float(m["perimeter"]), 4),
                 polygon=[(x / scale, y / scale) for x, y in m["poly"]], layer="(محدد يدوياً)", floor=floor,
                 source_file=Path(str(path)).name) for m in manual]
    plan.spaces = cands + msp             # المحدَّدة يدوياً تحتفظ بمعرّفاتها وأسمائها وأنواعها

    # --- أسماء الفراغات من النصوص ---
    texts_all = [e for e in ents if e.kind == "TEXT" and _text_is_label(e.text)]
    texts = [t for t in texts_all if role(t.layer) == "texts"] or texts_all
    for sp in plan.spaces:
        if sp.classified_by == "user" and sp.name:
            continue
        inside = [t for t in texts if point_in_polygon((t.pos[0], t.pos[1]), sp.polygon)]
        if inside:
            c = centroid(sp.polygon)
            inside.sort(key=lambda t: math.hypot(t.pos[0] - c[0], t.pos[1] - c[1]))
            sp.name = inside[0].text
        elif sp.layer != "(محدد يدوياً)":
            sp.name = ""                         # الفراغ المحدد يدوياً يحتفظ باسمه (مثل «فراغ 3») إن لم يوجد نص بداخله

    # --- الفتحات ---
    wind_pat = re.compile(norm_ar("wind|window|شباك|شبابيك|نافذ|نوافذ|w\\d"), re.I)
    # (?<![a-z])door: لا تطابق indoor/outdoor (وحدات تكييف)
    door_pat = re.compile(norm_ar("(?<![a-z])door|(?<![\\u0600-\\u06FF])باب|(?<![\\u0600-\\u06FF])ابواب|(?<![a-z])d\\d"), re.I)
    main_pats = [norm_ar(p) for p in st["door_main_patterns"]]
    k = {"door": 0, "window": 0}
    memo: dict[str, int] = {}

    def nested_openings(name: str, depth: int = 0) -> int:
        """عدد البلوكات على طبقات الأبواب/الشبابيك داخل بلوك (مع التداخل)."""
        if name in memo:
            return memo[name]
        memo[name] = 0
        blk = dr.blocks.get(name)
        n = 0
        if blk and depth < 8:
            for c in blk.ents:
                if c.kind == "INSERT":
                    n += (1 if role(c.layer) in ("doors", "windows") else 0) + nested_openings(c.name, depth + 1)
        memo[name] = n
        return n

    def holds_openings(name: str) -> bool:
        """بلوك حاوٍ (مثل «windows 1st» أو المسقط كله كبلوك) فيه 3 أبواب/شبابيك فأكثر: ليس فتحة بذاته.
        أما الباب الذي بداخله مقبض أو مقبضان على نفس الطبقة فيبقى باباً واحداً."""
        return nested_openings(name) >= 3

    open_depth = None                       # عمق آخر فتحة: ما بداخل بلوك الباب (مقبض، ضلفة...) ليس باباً آخر
    for e in ents:
        if e.kind != "INSERT":
            continue
        if open_depth is not None:
            if e.depth > open_depth:
                continue
            open_depth = None
        r = role(e.layer)
        nm = norm_ar(e.name)
        kind = "door" if r == "doors" else "window" if r == "windows" else None
        if kind is None and r in ("other", "texts"):
            kind = "window" if wind_pat.search(nm) else "door" if door_pat.search(nm) else None
        if kind is None or holds_openings(e.name):
            continue
        open_depth = e.depth
        w, h, sill, est, src = _opening_size(e, kind, dr, scale, st)
        k[kind] += 1
        sub = "window" if kind == "window" else ("main" if any(p in nm or p in norm_ar(e.layer) for p in main_pats) else "interior")
        plan.openings.append(Opening(id=f"{floor or 'F'}-{'D' if kind == 'door' else 'W'}{k[kind]:02d}", kind=kind, subkind=sub,
                                     block=e.name, layer=e.layer, pos=(e.pos[0] * scale, e.pos[1] * scale),
                                     width=w, height=h, sill=sill, estimated=est, size_source=src))
    # نسب الفتحات للفراغات بالقرب الهندسي
    tol = st["opening_tolerance_m"]
    polys_m = {sp.id: [(x * scale, y * scale) for x, y in sp.polygon] for sp in plan.spaces}
    byid = {sp.id: sp for sp in plan.spaces}
    prio = {t: i for i, t in enumerate(st["owner_priority"])}
    lost = []
    for op in plan.openings:
        near = [(dist_point_polygon(op.pos, poly), sid) for sid, poly in polys_m.items()]
        op.spaces = [sid for d, sid in sorted(near) if d <= tol]
        for sid in op.spaces:
            byid[sid].openings.append(op.id)
        if not op.spaces:
            lost.append(op.id)
    if lost:                                # رسالة واحدة بدل سطر لكل فتحة
        plan.warnings.append(f"{len(lost)} من {len(plan.openings)} فتحة لا تقع قرب أي فراغ ولم تُحتسب"
                             + (" (لا توجد فراغات محددة بعد)" if not plan.spaces else f": {', '.join(lost[:8])}{' ...' if len(lost) > 8 else ''}"))
    # owner يُحدَّد لاحقاً بعد التصنيف (space_classifier / rules_engine) لأنه يعتمد على نوع الفراغ
    _ = prio

    # --- أجهزة صحية داخل الفراغات ---
    fx_pats = {kd: [norm_ar(p) for p in ps] for kd, ps in st["fixture_patterns"].items()}
    for e in ents:
        if e.kind != "INSERT" or role(e.layer) != "sanitary":
            continue
        nm = norm_ar(e.name)
        kind = next((kd for kd in ("toilet_eastern", "bathtub", "shower", "kitchen_sink", "basin", "toilet_western")
                     if any(p in nm for p in fx_pats.get(kd, []))), "other_fixture")
        for sp in plan.spaces:
            if point_in_polygon(e.pos, sp.polygon):
                sp.fixtures[kind] = sp.fixtures.get(kind, 0) + 1
                break

    # --- أطوال الجدران ---
    for r in ("walls", "partitions"):
        total = 0.0
        for e in ents:
            if e.kind in ("POLY", "LINE") and role(e.layer) == r and len(e.verts) >= 2:
                closed = e.kind == "POLY" and e.closed
                ln = polyline_length(e.verts, closed) * scale
                total += ln / 2.0 if (closed and plan.wall_mode == "closed_polygons") else ln
        if plan.wall_mode == "double_line":
            total /= 2.0
        plan.wall_lengths[r] = round(total, 3)

    if not plan.spaces:
        plan.warnings.append("لم يُعثر على فراغات: لا مضلعات مغلقة على طبقة «فراغات» ولا غرف مغلقة من خطوط الجدران. تأكد من ربط طبقة الجدران (جدران/قواطع) في شاشة ربط الطبقات، أو "
                             "إن كانت الغرف مرسومة بخطوط منفصلة فنفّذ في الأوتوكاد الأمر BOUNDARY داخل كل غرفة (نوع الناتج Polyline) على طبقة جديدة مثل ROOMS ثم احفظ DXF من جديد.")
    if plan.spaces:
        big = max(s.area for s in plan.spaces)
        if big > 3000:
            plan.warnings.append(f"أكبر فراغ {big:.0f} م²: غالباً وحدة الرسم خاطئة (جرّب mm/cm/m يدوياً)")
        if big < 1.5 and len(plan.spaces) > 0:
            plan.warnings.append("المساحات صغيرة جداً: غالباً وحدة الرسم خاطئة")
    return plan
