"""دوال الحساب الحتمية (Registry/Strategy): اسم القياس -> دالة. لا ذكاء اصطناعي هنا إطلاقاً.

كل دالة تعيد Result(qty, formula, inputs, estimated). التقريب النهائي (منزلتان) يتم عند التسعير.
لإضافة قاعدة قياس جديدة: سجّلها بـ @measure("اسم") دون تعديل الكود الأساسي.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from .models import Opening, Space


@dataclass
class Result:
    qty: float
    formula: str
    inputs: dict = field(default_factory=dict)
    estimated: bool = False
    note: str = ""


@dataclass
class Ctx:
    settings: dict
    openings: dict[str, Opening]
    spaces: dict[str, Space]
    wall_lengths: dict[str, float] = field(default_factory=dict)   # على مستوى المشروع


REGISTRY: dict[str, Callable[..., Result]] = {}


def measure(name: str):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def calc(name: str, ctx: Ctx, space: Optional[Space]) -> Result:
    base, _, arg = name.partition(":")
    if base not in REGISTRY:
        raise KeyError(f"قاعدة قياس غير معروفة: {name}")
    return REGISTRY[base](ctx, space, arg)


def r2(x: float) -> float:
    return round(x + 1e-12, 2)


# ---------- أدوات مشتركة ----------
def adjacent(ctx: Ctx, sp: Space, kind: Optional[str] = None) -> list[Opening]:
    ops = [ctx.openings[i] for i in sp.openings if i in ctx.openings]
    return [o for o in ops if kind is None or o.kind == kind]


def door_class(op: Opening, ctx: Ctx) -> str:
    """main | bath | interior للأبواب؛ window للشبابيك."""
    if op.kind == "window":
        return "window"
    if op.subkind == "main":
        return "main"
    owner = ctx.spaces.get(op.owner or "")
    return "bath" if owner is not None and owner.type == "bathroom" else "interior"


def overlap_height(op: Opening, top: float) -> float:
    """ارتفاع الجزء من الفتحة الواقع تحت منسوب 'top' (لحساب خصم التبليط)."""
    lo, hi = op.sill, op.sill + op.height
    return max(0.0, min(hi, top) - lo)


def _op_list(ops: list[Opening]) -> list[dict]:
    return [{"id": o.id, "w": o.width, "h": o.height} for o in ops]


# ---------- قواعد على مستوى الفراغ ----------
@measure("floor_area")
def _floor(ctx, sp, arg):
    return Result(sp.area, f"مساحة الفراغ = {sp.area:.2f}", {"area": sp.area})


@measure("ceiling_area")
def _ceiling(ctx, sp, arg):
    return Result(sp.area, f"مساحة السقف = مساحة الفراغ = {sp.area:.2f}", {"area": sp.area})


@measure("wall_area_full")
def _wall_full(ctx, sp, arg):
    H = ctx.settings["room_height"]
    ops = adjacent(ctx, sp)
    ded = sum(o.width * o.height for o in ops)
    est = True  # الارتفاع من الإعدادات
    return Result(sp.perimeter * H - ded, f"المحيط {sp.perimeter:.2f} × الارتفاع {H} − الفتحات {ded:.2f}",
                  {"perimeter": sp.perimeter, "height": H, "openings": _op_list(ops)}, est,
                  "الارتفاع افتراضي من الإعدادات")


@measure("wall_plus_ceiling")
def _wall_ceiling(ctx, sp, arg):
    w = _wall_full(ctx, sp, "")
    return Result(w.qty + sp.area, w.formula + f" + السقف {sp.area:.2f}", {**w.inputs, "ceiling": sp.area}, True, w.note)


@measure("wall_area_tiled")
def _wall_tiled(ctx, sp, arg):
    Ht = ctx.settings["wet_wall_tile_height"]
    ops = adjacent(ctx, sp)
    ded = sum(o.width * overlap_height(o, Ht) for o in ops)
    return Result(sp.perimeter * Ht - ded, f"المحيط {sp.perimeter:.2f} × ارتفاع التبليط {Ht} − الفتحات تحته {ded:.2f}",
                  {"perimeter": sp.perimeter, "tile_height": Ht, "openings": _op_list(ops)}, True,
                  "ارتفاع التبليط افتراضي من الإعدادات")


@measure("skirting_length")
def _skirting(ctx, sp, arg):
    doors = adjacent(ctx, sp, "door")
    w = sum(d.width for d in doors)
    return Result(sp.perimeter - w, f"المحيط {sp.perimeter:.2f} − عروض الأبواب {w:.2f}",
                  {"perimeter": sp.perimeter, "doors": _op_list(doors)}, any(d.estimated and d.size_source == "default" for d in doors))


@measure("door_area")
def _door_area(ctx, sp, arg):
    ops = [o for o in adjacent(ctx, sp, "door") if o.owner == sp.id and door_class(o, ctx) == arg]
    a = sum(o.width * o.height for o in ops)
    return Result(a, " + ".join(f"{o.width}×{o.height}" for o in ops) or "0", {"doors": _op_list(ops)},
                  any(o.estimated for o in ops), "ارتفاع/عرض مقدّر لبعض الأبواب" if any(o.estimated for o in ops) else "")


@measure("window_area")
def _window_area(ctx, sp, arg):
    ops = [o for o in adjacent(ctx, sp, "window") if o.owner == sp.id]
    a = sum(o.width * o.height for o in ops)
    return Result(a, " + ".join(f"{o.width}×{o.height}" for o in ops) or "0", {"windows": _op_list(ops)},
                  any(o.estimated for o in ops), "ارتفاع/عرض مقدّر لبعض الشبابيك" if any(o.estimated for o in ops) else "")


@measure("fixture")
def _fixture(ctx, sp, arg):
    n = sp.fixtures.get(arg, 0)
    return Result(float(n), f"عدد بلوكات {arg} داخل الفراغ = {n}", {"kind": arg, "count": n})


# ---------- قواعد على مستوى المشروع ----------
@measure("blockwork")
def _blockwork(ctx, sp, arg):
    """arg = 'walls' أو 'partitions': مساحة المباني = الطول × الارتفاع − حصة من مساحة كل الفتحات."""
    H = ctx.settings["room_height"]
    L = ctx.wall_lengths.get(arg, 0.0)
    total_L = sum(ctx.wall_lengths.values()) or 1.0
    ops = list(ctx.openings.values())
    ded = sum(o.width * o.height for o in ops if o.spaces) if ctx.settings.get("wall_deduct_openings_in_blockwork", True) else 0.0
    ded_share = ded * L / total_L
    return Result(max(0.0, L * H - ded_share), f"الطول {L:.2f} × الارتفاع {H} − حصة الفتحات {ded_share:.2f}",
                  {"length": L, "height": H, "openings_total": ded, "share": ded_share}, True,
                  "تقدير: لا تُعرف مواضع الفتحات على الجدران/القواطع بدقة")


def assign_owners(spaces: list[Space], openings: list[Opening], priority: list[str]) -> None:
    """الفراغ المالك للفتحة = الأعلى أولوية بين الفراغات المجاورة (حمام > مطبخ > نوم ...)."""
    rank = {t: i for i, t in enumerate(priority)}
    by = {s.id: s for s in spaces}
    for o in openings:
        if not o.spaces:
            o.owner = None
            continue
        o.owner = sorted(o.spaces, key=lambda sid: (rank.get(by[sid].type, 99), sid))[0]
