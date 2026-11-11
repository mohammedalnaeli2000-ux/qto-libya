"""أدوات المخطط المشتركة بين البرنامج المكتبي ونسخة الويب: صورة نقطية للجدران، اكتشاف الغرف تلقائياً، ورسم معاينة PNG.
لا تعتمد على أي واجهة (PyQt/Streamlit)."""
from __future__ import annotations

from .cad_parser import resolve_scale, unit_scale
from .geometry import flatten
from .plan_raster import HAVE_CV, PlanRaster, collect_geometry

TYPE_COLORS = {"bathroom": "#38bdf8", "kitchen": "#fb923c", "bedroom": "#a78bfa", "living": "#34d399", "majlis": "#f472b6",
               "corridor": "#fbbf24", "storage": "#94a3b8", "laundry": "#22d3ee", "balcony": "#a3e635", "stairs": "#f87171",
               "roof": "#cbd5e1", "parking": "#9ca3af", "yard": "#86efac", "unknown": "#e5e7eb"}


def plan_raster(project, pf):
    """(PlanRaster, geometry, scale, note) لمسقط مشروع. يرمي ValueError إن لم توجد جدران مربوطة."""
    if not HAVE_CV:
        raise RuntimeError("المكتبتان numpy وopencv غير متوفرتين")
    _p, dr = project._drawing(pf)
    roles = project.layers_of(pf)
    role = lambda ly: roles.get(ly, "other")
    st = project.effective_settings()
    ents = list(dr.iter_entities())
    scale, note = unit_scale(dr, st)
    scale, note, _warn = resolve_scale(dr, ents, role, st, scale, note)
    geo = collect_geometry(ents, role, scale, flatten)
    if not (geo["walls_fill"] or geo["walls_line"]):
        raise ValueError("لا توجد خطوط جدران: اربط طبقة الجدران أولاً")
    return PlanRaster(geo["walls_fill"], geo["walls_line"] + geo["columns"], geo["windows"]), geo, scale, note


def auto_detect_rooms(project, pf, door_gap: float = 1.5) -> int:
    """يكتشف كل الغرف المغلقة ويضيفها كفراغات «غير مصنّفة» (تُسمّى من النصوص إن وُجدت). يعيد عددها."""
    R, _geo, _s, _n = plan_raster(project, pf)
    st = project.effective_settings()
    mine = [m for m in project.manual_spaces if m.get("file") == pf.path]
    rooms = R.auto_rooms(door_gap, st.get("min_space_area_m2", 1.0), exclude=[m["poly"] for m in mine])
    n0 = sum(1 for m in project.manual_spaces if m.get("type") == "unknown")
    for i, r in enumerate(rooms, 1):
        project.add_manual_space(pf.path, r["poly"], r["area"], r["perimeter"], f"فراغ {n0 + i}", "unknown")
    return len(rooms)


def render_plan_png(project, pf, spaces, max_px: int = 1400) -> bytes:
    """صورة PNG للمسقط مع الغرف ملوّنة حسب النوع ومرقّمة (الأرقام تقابل جدول الغرف). spaces: فراغات هذا المسقط."""
    import cv2
    import numpy as np
    R, _geo, scale, _n = plan_raster(project, pf)
    img = cv2.cvtColor(((1 - R.walls) * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    up = max(1, int(np.ceil(900 / max(img.shape[:2]))))              # المساقط الصغيرة تُكبَّر حتى تكون واضحة على الهاتف
    if up > 1:
        img = cv2.resize(img, (img.shape[1] * up, img.shape[0] * up), interpolation=cv2.INTER_NEAREST)
    for i, s in enumerate(spaces, 1):
        pts = np.array([[c * up for c in R._px((x * scale, y * scale))] for x, y in s.polygon], np.int32)
        if len(pts) < 3:
            continue
        hexc = TYPE_COLORS.get(s.type, "#e5e7eb").lstrip("#")
        col = tuple(int(hexc[k:k + 2], 16) for k in (4, 2, 0))           # BGR
        over = img.copy(); cv2.fillPoly(over, [pts], col); img = cv2.addWeighted(over, 0.45, img, 0.55, 0)
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        label = str(i); fs = max(0.5, min(1.4, img.shape[1] / 900))
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
        cv2.putText(img, label, (int(cx - tw / 2), int(cy + th / 2)), cv2.FONT_HERSHEY_SIMPLEX, fs, (20, 20, 20), 2, cv2.LINE_AA)
    h, w = img.shape[:2]
    if max(h, w) > max_px:
        k = max_px / max(h, w); img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""
