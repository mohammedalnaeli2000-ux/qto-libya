"""محرك القرار: تلقائي / سؤال / تأكيد، وتخزين الإجابات مع تراجع (Undo).

مفتاح الإجابة:  rule|type:<نوع>|<فلتر>   (تنطبق على كل الفراغات المشابهة)
                rule|space:<معرّف>|<فلتر> (فراغ بعينه)   rule||<فلتر> (المشروع)
القيمة: معرّف بند (int كنص) أو "__skip__" (تخطَّ) أو "__manual__" (سأحدده يدوياً).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .models import BoqItem, Demand, Line, Space
from .rules_engine import RulesEngine

SKIP, MANUAL = "__skip__", "__manual__"


def filt_key(f: dict) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(f.items()))


@dataclass
class Question:
    key: str                         # مفتاح النوع (للتطبيق على المشابهة)
    rule_id: str
    title: str
    kind: str                        # variant | confirm
    space_ids: list[str]
    space_names: list[str]
    options: list[BoqItem]
    filt: dict = field(default_factory=dict)
    total_qty: float = 0.0
    unit: str = ""


@dataclass
class Resolution:
    lines: list[Line] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)     # كميات بلا بند مطابق في اللائحة
    skipped: list[dict] = field(default_factory=list)
    auto: list[dict] = field(default_factory=list)          # قرارات تلقائية (بند وحيد)


class DecisionEngine:
    def __init__(self, engine: RulesEngine, spaces: dict[str, Space], answers: Optional[dict] = None):
        self.engine = engine
        self.spaces = spaces
        self.answers: dict[str, str] = dict(answers or {})
        self.history: list[tuple[dict[str, str], dict]] = []      # للتراجع

    # ----- الإجابات -----
    def answer(self, key: str, value: str, space_ids: Optional[list[str]] = None, apply_all: bool = True) -> None:
        """apply_all=True: يُخزَّن بمفتاح النوع؛ وإلا بمفتاح أول فراغ فقط."""
        self.history.append((dict(self.answers), {"key": key}))
        value = str(value)                    # معرّف البند يصل رقماً من الواجهة؛ المقارنة في resolve نصية
        if apply_all or not space_ids:
            self.answers[key] = value
        else:
            rule, _, rest = key.partition("|type:")
            f = rest.split("|", 1)[1] if "|" in rest else ""
            self.answers[f"{rule}|space:{space_ids[0]}|{f}"] = value

    def undo(self) -> bool:
        if not self.history:
            return False
        self.answers, _ = self.history.pop()
        return True

    def _lookup(self, d: Demand) -> Optional[str]:
        f = filt_key(d.filter)
        for k in (f"{d.rule_id}|space:{d.space_id}|{f}", f"{d.rule_id}|type:{d.space_type}|{f}", f"{d.rule_id}||{f}"):
            if k in self.answers:
                return self.answers[k]
        return None

    # ----- الحل -----
    def resolve(self, demands: list[Demand]) -> Resolution:
        self.engine.check_isolation(demands)
        res = Resolution()
        groups: dict[tuple, list[Demand]] = {}
        for d in demands:
            rule = self.engine.rule_by_id[d.rule_id]
            cands = self.engine.candidates(d.rule_id, d.filter)
            sp = self.spaces.get(d.space_id or "")
            name = sp.name or sp.id if sp else "المشروع"
            if not cands:
                res.unmatched.append({"demand": d, "space": name,
                                      "reason": f"لا يوجد في اللائحة بند مطابق لـ «{rule['title']}»"
                                                + (f" بسماكة {d.filter['thickness_cm']} سم" if d.filter else "")})
                continue
            ans = self._lookup(d)
            if ans == SKIP or ans == MANUAL:
                res.skipped.append({"demand": d, "space": name, "manual": ans == MANUAL})
                continue
            if ans is not None:
                item = next((c for c in cands if str(c.id) == str(ans)), None)   # str: مشاريع قديمة حُفظت إجاباتها أرقاماً
                if item is None:                                   # إجابة قديمة لم تعد صالحة
                    ans = None
                else:
                    res.lines.append(self._line(d, item, name))
                    continue
            if len(cands) == 1 and not rule.get("confirm") and not rule.get("optional"):
                res.lines.append(self._line(d, cands[0], name))
                res.auto.append({"rule": rule["title"], "space": name, "item": cands[0].code})
                continue
            groups.setdefault((d.rule_id, d.space_type, filt_key(d.filter)), []).append(d)
        for (rid, stype, fk), ds in groups.items():
            rule = self.engine.rule_by_id[rid]
            cands = self.engine.candidates(rid, ds[0].filter)
            names = [(self.spaces[d.space_id].name or d.space_id) if d.space_id else "المشروع" for d in ds]
            total = sum(d.quantity for d in ds)
            label = self.engine.space_types.get(stype or "", {}).get("label", "") if stype else ""
            if ds[0].space_id:
                joined = "، ".join(f"{n} ({d.quantity:.2f})" for n, d in zip(names, ds))
                title = f"{label}: {rule['question']} — {joined}"
            else:
                thk = f"سماكة {ds[0].filter['thickness_cm']} سم، " if ds[0].filter.get("thickness_cm") else ""
                title = f"{rule['question']} — {thk}{total:.2f} {ds[0].unit}"
            res.questions.append(Question(
                key=f"{rid}|type:{stype}|{fk}" if stype else f"{rid}||{fk}", rule_id=rid, title=title,
                kind="confirm" if (len(cands) == 1 and rule.get("confirm")) else "variant",
                space_ids=[d.space_id for d in ds if d.space_id], space_names=names, options=cands,
                filt=ds[0].filter, total_qty=total, unit=ds[0].unit))
        return res

    @staticmethod
    def _line(d: Demand, item: BoqItem, space_name: str) -> Line:
        return Line(demand_id=d.id, space_id=d.space_id, space_name=space_name, space_type=d.space_type,
                    rule_id=d.rule_id, item_code=item.code, description=item.description if not item.ancestors
                    else f"{item.ancestors.split(' ← ')[-1]} — {item.description}",
                    unit=d.unit, quantity=round(d.quantity, 2), unit_price=item.unit_price, chapter_no=item.chapter_no,
                    formula=d.formula, inputs=d.inputs, estimated=d.estimated, item_id=item.id)
