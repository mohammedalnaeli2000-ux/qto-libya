"""تصنيف الفراغات: 1) المرادفات 2) الذكاء الاصطناعي (اختياري) 3) سؤال المستخدم."""
from __future__ import annotations
from typing import Optional

from . import config
from .models import Plan, Space
from .textnorm import tokens


def classify_name(name: str, synonyms: Optional[dict] = None) -> tuple[str, float]:
    """(النوع، الثقة). الثقة: 0.95 مطابقة وحيدة، 0.5 التباس بين نوعين، 0 لا شيء."""
    syn = synonyms or config.load("synonyms.json")
    toks = tokens(name)
    if not toks:
        return "unknown", 0.0
    hits: dict[str, int] = {}
    for typ, words in syn.items():
        for w in words:
            wt = tokens(w)
            if not wt:
                continue
            for i in range(len(toks) - len(wt) + 1):
                if toks[i:i + len(wt)] == wt:
                    hits[typ] = max(hits.get(typ, 0), len(wt) * 10 + len("".join(wt)))
                    break
    if not hits:
        return "unknown", 0.0
    best = sorted(hits.items(), key=lambda kv: -kv[1])
    if len(best) > 1 and best[0][1] == best[1][1]:
        return best[0][0], 0.5
    # نوع غالب لكن بوجود نوع آخر: ثقة أقل من العتبة لتُسأل (مثل "غرفة نوم مع حمام")
    return best[0][0], 0.95 if len(best) == 1 else 0.7


def classify_plan(plan: Plan, ai=None, settings: Optional[dict] = None) -> list[Space]:
    """يصنّف الفراغات؛ يعيد قائمة الفراغات التي تحتاج سؤالاً (ثقة < العتبة)."""
    st = settings or config.load_settings()
    thr = st["ai_confidence_threshold"]
    syn = config.load("synonyms.json")
    need_ai: list[Space] = []
    for sp in plan.spaces:
        if sp.classified_by == "user":
            continue
        typ, conf = classify_name(sp.name, syn)
        if typ == "unknown" and sp.fixtures:                      # تلميح هندسي: الأجهزة الصحية داخل الفراغ
            fx = sp.fixtures
            if any(fx.get(k) for k in ("toilet_eastern", "toilet_western", "bathtub", "shower")):
                typ, conf = "bathroom", 0.9
            elif fx.get("kitchen_sink"):
                typ, conf = "kitchen", 0.9
        sp.type, sp.confidence, sp.classified_by = typ, conf, "rule"
        if conf < thr:
            need_ai.append(sp)
    if ai is not None and need_ai:
        try:
            ops_by_id = {o.id: o for o in plan.openings}
            payload = [{"id": s.id, "name": s.name, "area": round(s.area, 2), "perimeter": round(s.perimeter, 2),
                        "doors": sum(1 for oid in s.openings if ops_by_id[oid].kind == "door"),
                        "windows": sum(1 for oid in s.openings if ops_by_id[oid].kind == "window"),
                        "fixtures": s.fixtures} for s in need_ai]
            res = ai.classify_spaces(payload, list(config.load("space_types.json").keys()))
            for s in need_ai:
                r = res.get(s.id)
                if r and r["type"] in config.load("space_types.json"):
                    s.type, s.confidence, s.classified_by = r["type"], float(r["confidence"]), "ai"
        except Exception:                      # وضع عدم الاتصال: نكمل بالقواعد وحدها
            pass
    return [s for s in plan.spaces if s.confidence < thr and s.classified_by != "user"]
