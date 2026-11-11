"""المشروع: ملفات + ربط طبقات + قرارات + تعديلات، وخط المعالجة الكامل (تحليل -> تصنيف -> حساب)."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import config
from .boq_db import BoqDb
from .cad_parser import drawing_layers, parse_plan, suggest_layer_roles
from .decision_engine import DecisionEngine, Resolution
from .dwg_convert import convert_dwg_to_dxf
from .dxf_reader import Drawing, read_dxf
from .models import Line, Plan, Space

FREE_CHAPTER = 999                 # باب افتراضي للبنود الخارجة عن اللائحة
from .quantity_calc import assign_owners
from .rules_engine import RulesEngine
from .space_classifier import classify_plan


@dataclass
class ProjectFile:
    path: str
    floor: str = ""
    kind: str = "plan"            # plan | elevation | section  (الواجهات والقطاعات: مرحلة لاحقة)


@dataclass
class Result:
    plans: list[Plan]
    spaces: list[Space]
    lines: list[Line]
    resolution: Resolution
    pending: list[dict]
    warnings: list[str]
    unclassified: list[Space]


@dataclass
class Project:
    name: str = "مشروع جديد"
    boq_version: str = ""
    settings: dict = field(default_factory=dict)          # تجاوزات الإعدادات
    files: list[ProjectFile] = field(default_factory=list)
    layer_maps: dict[str, dict[str, str]] = field(default_factory=dict)
    space_edits: dict[str, dict] = field(default_factory=dict)   # {space_id: {name,type}}
    answers: dict[str, str] = field(default_factory=dict)
    decision_log: list[dict] = field(default_factory=list)
    manual_lines: list[dict] = field(default_factory=list)       # {item_id, qty, space_id, note}
    manual_spaces: list[dict] = field(default_factory=list)      # {id,file,name,type,poly(م),area,perimeter}
    qty_overrides: dict[str, float] = field(default_factory=dict)  # {demand_id|item_id: qty}
    price_overrides: dict[str, float] = field(default_factory=dict)  # {item_id: سعر الوحدة} سعر السوق بدل سعر اللائحة
    # غير محفوظة
    _drawings: dict[str, Drawing] = field(default_factory=dict, repr=False)
    _plans: list[Plan] = field(default_factory=list, repr=False)
    _converted: dict[str, str] = field(default_factory=dict, repr=False)

    # ---------- حفظ/فتح ----------
    def save(self, path: str | Path) -> None:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        d["files"] = [f.__dict__ for f in self.files]
        Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Project":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["files"] = [ProjectFile(**f) for f in d.get("files", [])]
        return Project(**d)

    def effective_settings(self) -> dict:
        return config.load_settings(self.settings)

    # ---------- قراءة الملفات ----------
    def _drawing(self, pf: ProjectFile) -> tuple[str, Drawing]:
        path = pf.path
        if path.lower().endswith(".dwg"):
            if path not in self._converted:
                self._converted[path] = str(convert_dwg_to_dxf(Path(path)))
            path = self._converted[path]
        if path not in self._drawings:
            self._drawings[path] = read_dxf(path)
        return path, self._drawings[path]

    def layers_of(self, pf: ProjectFile) -> dict[str, str]:
        """الطبقات مع دورها (المحفوظ أو المقترح)."""
        path, dr = self._drawing(pf)
        sug = suggest_layer_roles(drawing_layers(dr))
        saved = self.layer_maps.get(pf.path, {})
        return {ly: saved.get(ly, sug[ly]) for ly in sug}

    def analyze(self, progress: Optional[Callable[[int, str], None]] = None, ai=None) -> list[Plan]:
        st = self.effective_settings()
        plans = []
        plan_files = [f for f in self.files if f.kind == "plan"]
        for i, pf in enumerate(plan_files):
            if progress: progress(int(100 * i / max(1, len(plan_files))), f"قراءة {Path(pf.path).name}")
            path, dr = self._drawing(pf)
            roles = self.layers_of(pf)
            mine = [m for m in self.manual_spaces if m.get("file") == pf.path]
            plan = parse_plan(path, roles, st, pf.floor or Path(pf.path).stem, dr, manual=mine)
            plan.file = pf.path
            classify_plan(plan, ai, st)
            self._apply_edits(plan)
            assign_owners(plan.spaces, plan.openings, st["owner_priority"])
            plans.append(plan)
        for pf in self.files:
            if pf.kind != "plan":
                plans_warn = f"ملف {Path(pf.path).name} ({pf.kind}): قراءة الواجهات والقطاعات غير مفعّلة بعد؛ استُخدم الارتفاع الافتراضي"
                if plans:
                    plans[0].warnings.append(plans_warn)
        self._plans = plans
        if progress: progress(100, "تمت القراءة")
        return plans

    def _apply_edits(self, plan: Plan) -> None:
        for sp in plan.spaces:
            e = self.space_edits.get(sp.id)
            if e:
                if "name" in e: sp.name = e["name"]
                if "type" in e:
                    sp.type, sp.confidence, sp.classified_by = e["type"], 1.0, "user"

    def add_manual_space(self, file: str, poly: list, area: float, perimeter: float, name: str, typ: str) -> str:
        """يضيف فراغاً حدّده المستخدم (مضلع بالمتر). يعيد المعرّف الثابت."""
        n = 1 + max([int(m["id"].split("-M")[-1]) for m in self.manual_spaces if "-M" in m["id"]] or [0])
        pf = next((f for f in self.files if f.path == file), None)
        sid = f"{(pf.floor if pf and pf.floor else Path(file).stem)}-M{n:02d}"
        self.manual_spaces.append({"id": sid, "file": file, "name": name, "type": typ, "poly": [list(p) for p in poly],
                                   "area": float(area), "perimeter": float(perimeter)})
        return sid

    def remove_manual_space(self, sid: str) -> None:
        self.manual_spaces = [m for m in self.manual_spaces if m["id"] != sid]
        self.space_edits.pop(sid, None)

    def edit_space(self, space_id: str, **kw) -> None:
        self.space_edits.setdefault(space_id, {}).update(kw)
        for p in self._plans:
            self._apply_edits(p)
            st = self.effective_settings()
            assign_owners(p.spaces, p.openings, st["owner_priority"])

    # ---------- الحساب ----------
    def compute(self, db: Optional[BoqDb] = None, de: Optional[DecisionEngine] = None) -> tuple[Result, DecisionEngine]:
        st = self.effective_settings()
        db = db or BoqDb(version=self.boq_version or None)
        self.boq_version = db.version
        engine = RulesEngine(db, st)
        spaces = {s.id: s for p in self._plans for s in p.spaces}
        built = engine.build(self._plans)
        de = de or DecisionEngine(engine, spaces, self.answers)
        de.engine, de.spaces = engine, spaces
        res = de.resolve(built.demands)
        self.answers = de.answers
        lines = list(res.lines)
        for L in lines:                                    # تعديلات الكميات اليدوية
            k = f"{L.demand_id}|{L.item_id}"
            if k in self.qty_overrides:
                L.quantity, L.modified = float(self.qty_overrides[k]), True
        for idx, m in enumerate(self.manual_lines):        # بنود يدوية (منها المعلّقة)
            sp = spaces.get(m.get("space_id") or "")
            if m.get("free"):                              # بند خارج اللائحة بوصف وسعر يدويين
                lines.append(Line(demand_id=f"manual_free:{idx}", space_id=sp.id if sp else None,
                                  space_name=(sp.name or sp.id) if sp else "يدوي", space_type=sp.type if sp else None,
                                  rule_id="manual_free", item_code="خارج اللائحة", description=str(m.get("description", "")),
                                  unit=str(m.get("unit", "")), quantity=float(m["qty"]), unit_price=float(m.get("price", 0)),
                                  chapter_no=FREE_CHAPTER, formula="بند خارج اللائحة (إدخال يدوي)", inputs={}, manual=True,
                                  item_id=-(idx + 1), note=m.get("note", "")))
                continue
            it = db.by_id(int(m["item_id"]))
            if it is None:
                continue
            lines.append(Line(demand_id=f"manual:{it.id}", space_id=sp.id if sp else None,
                              space_name=(sp.name or sp.id) if sp else "يدوي", space_type=sp.type if sp else None,
                              rule_id="manual", item_code=it.code,
                              description=it.description if not it.ancestors else f"{it.ancestors.split(' ← ')[-1]} — {it.description}",
                              unit=it.unit or "", quantity=float(m["qty"]), unit_price=it.unit_price or 0.0,
                              chapter_no=it.chapter_no, formula="إدخال يدوي", inputs={}, manual=True,
                              item_id=it.id, note=m.get("note", "")))
        markup = float(st.get("price_markup_pct", 0) or 0)
        for L in lines:                                    # أسعار السوق: سعر يدوي للبند أو نسبة زيادة عامة
            L.base_price = L.unit_price
            if L.item_id < 0:
                continue                                   # البند الحر سعره من إدخال المستخدم
            ov = self.price_overrides.get(str(L.item_id))
            if ov is not None:
                L.unit_price, L.price_modified = round(float(ov), 2), True
            elif markup:
                L.unit_price, L.price_modified = round(L.unit_price * (1 + markup / 100.0), 2), True
        warnings = [w for p in self._plans for w in p.warnings]
        unclassified =[s for s in spaces.values() if s.confidence < st["ai_confidence_threshold"] and s.classified_by != "user"]
        return Result(self._plans, list(spaces.values()), lines, res, built.pending, warnings, unclassified), de
