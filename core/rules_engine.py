"""مصفوفة الفراغات والبنود: أي قاعدة تنطبق على أي نوع فراغ، وأي بنود من اللائحة تخدم القاعدة.

قاعدة العزل:  بند B مسموح في فراغ S  ⇔  نوع S ∈ B.applies_to   (يُفحص بـ check_isolation)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .boq_db import BoqDb
from .models import BoqItem, Demand, Plan, Space
from .quantity_calc import Ctx, calc, r2
from .textnorm import norm_ar

EPS = 0.005


class IsolationError(Exception):
    pass


@dataclass
class BuildResult:
    demands: list[Demand] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)


def item_thickness_cm(it: BoqItem) -> Optional[int]:
    """السماكة من الوصف: 'سمك 20سم' أو أصغر بُعد في 'مقاس (20*20*40)'."""
    t = norm_ar(it.description)
    m = re.search(r"سمك\s*(\d+)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"مقاس\s*\(?\s*(\d+)\s*[*×x]\s*(\d+)\s*[*×x]\s*(\d+)", t)
    return min(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


class RulesEngine:
    def __init__(self, db: BoqDb, settings: Optional[dict] = None):
        self.db = db
        self.settings = settings or config.load_settings()
        self.rules: list[dict] = config.load("item_rules.json")
        self.rule_by_id = {r["id"]: r for r in self.rules}
        self.space_types = config.load("space_types.json")
        self._cand: dict[str, list[BoqItem]] = {}

    # ---------- بنود اللائحة المرشحة لقاعدة ----------
    def rule_items(self, rule_id: str) -> list[BoqItem]:
        if rule_id in self._cand:
            return self._cand[rule_id]
        rule = self.rule_by_id[rule_id]
        spec = rule["item"]
        anys = [re.compile(norm_ar(p)) for p in spec["any"]]
        nones = [re.compile(norm_ar(p)) for p in spec.get("none", []) if p]
        out = []
        for it in self.db.items():
            if it.chapter_no not in spec["chapters"] or it.unit != spec["unit"]:
                continue
            text = norm_ar(it.ancestors + " " + it.description)
            if not any(a.search(text) for a in anys) or any(n.search(text) for n in nones):
                continue
            if spec.get("thickness_from_text") and item_thickness_cm(it) is None:
                continue
            out.append(it)
        self._cand[rule_id] = out
        return out

    def candidates(self, rule_id: str, filt: Optional[dict] = None) -> list[BoqItem]:
        items = self.rule_items(rule_id)
        if filt and "thickness_cm" in filt:
            items = [i for i in items if item_thickness_cm(i) == filt["thickness_cm"]]
        return items

    # ---------- بناء الكميات المطلوبة ----------
    def build(self, plans: list[Plan]) -> BuildResult:
        res = BuildResult()
        spaces = {s.id: s for p in plans for s in p.spaces}
        openings = {o.id: o for p in plans for o in p.openings}
        lengths: dict[str, float] = {}
        for p in plans:
            for k, v in p.wall_lengths.items():
                lengths[k] = lengths.get(k, 0.0) + v
        ctx = Ctx(self.settings, openings, spaces, lengths)
        for sp in spaces.values():
            for rule in self.rules:
                if rule.get("scope") == "project" or sp.type not in rule["applies_to"]:
                    continue
                r = calc(rule["measure"], ctx, sp)
                if r.qty < EPS:
                    continue
                res.demands.append(Demand(id=f"{rule['id']}:{sp.id}", rule_id=rule["id"], space_id=sp.id,
                                          space_type=sp.type, quantity=r.qty, unit=rule["unit"], formula=r.formula,
                                          inputs=r.inputs, estimated=r.estimated, note=r.note))
            if sp.type == "bathroom" and not sp.fixtures:
                res.pending.append({"title": f"أجهزة صحية: {sp.name or sp.id}",
                                    "reason": "لم تُرصد بلوكات أجهزة صحية داخل الفراغ؛ أدخل الأجهزة يدوياً", "chapters": [11], "space": sp.id})
        # مباني الطوب على مستوى المشروع (حسب سماكة الجدران والقواطع من الإعدادات)
        th = self.settings["wall_thickness_cm"]
        for role in ("walls", "partitions"):
            if lengths.get(role, 0) < EPS:
                continue
            r = calc("blockwork:" + role, ctx, None)
            if r.qty >= EPS:
                res.demands.append(Demand(id=f"blockwork:PRJ:{role}", rule_id="blockwork", space_id=None, space_type=None,
                                          quantity=r.qty, unit="m2", formula=r.formula, inputs=r.inputs,
                                          estimated=True, note=r.note, filter={"thickness_cm": int(th[role])}))
        chapters = self.db.chapters()
        for p in config.load("pending.json"):
            res.pending.append({**p, "chapter_names": [chapters.get(c, str(c)) for c in p["chapters"]]})
        return res

    # ---------- فحص العزل ----------
    def check_isolation(self, demands: list[Demand]) -> None:
        for d in demands:
            rule = self.rule_by_id[d.rule_id]
            if d.space_type is not None and d.space_type not in rule["applies_to"]:
                raise IsolationError(f"بند '{rule['title']}' أُسند إلى فراغ من نوع {d.space_type} وهو غير مسموح له")
