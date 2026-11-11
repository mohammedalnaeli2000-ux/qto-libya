"""المساعد الذكي: محادثة حرة مع النموذج حول المشروع المفتوح + اقتراح تصحيحات لا تُطبَّق إلا بموافقة المهندس.

المبدأ: النموذج يقرأ ملخص المشروع (الفراغات، أسطر المقايسة، الأسئلة، التحذيرات، الإعدادات) ويجيب ويشرح.
إن رأى خطأ يقترح «إجراءات» بصيغة JSON محددة. كل إجراء يُتحقق منه هنا (المعرّفات موجودة، البند موجود في اللائحة،
القيمة في المدى) ثم يُعرض للمهندس كبطاقة «تطبيق / تجاهل». لا شيء يتغير في المشروع بدون ضغطة المهندس،
والكميات نفسها تبقى من الحساب الحتمي ما لم يعدّلها المهندس بقبول اقتراح.
"""
from __future__ import annotations
import json
import re
from typing import Optional

from core import config
from core.decision_engine import SKIP

UNIT_AR = {"m2": "م2", "lm": "م.ط", "m3": "م3", "count": "عدد", "lump": "مقطوعية"}
NUM_SETTINGS = {"room_height": (2.0, 8.0), "wet_wall_tile_height": (0.5, 8.0), "door_height": (1.5, 4.0),
                "window_height": (0.3, 3.0), "window_sill": (0.0, 2.0), "opening_tolerance_m": (0.05, 2.0),
                "price_markup_pct": (-50.0, 300.0)}
ACTION_TYPES = ("set_space_type", "rename_space", "answer_question", "skip_question", "set_quantity",
                "set_price", "add_item", "set_setting")

SYSTEM = """أنت «مساعد QTO Libya»، مساعد ذكي داخل برنامج لحصر الكميات وإعداد المقايسات وفق لائحة الأسعار الاسترشادية الليبية.
تكلّم مهندساً مدنياً ليبياً: أجب بالعربية بلهجة واضحة ومهذبة، باختصار ودقة، وبمصطلحات الحصر المعروفة.

ما تستطيعه:
- شرح أي كمية أو بند أو إجمالي في المشروع المفتوح، وكيف حُسب (عمود «الحساب»).
- مراجعة المقايسة واكتشاف ما يبدو خطأً: نوع فراغ غير منطقي، بند غير مناسب للفراغ، كمية شاذة، بند ناقص، سؤال لم يُجب.
- الإجابة عن أسئلة عامة في الحصر والمواصفات واللائحة.
- اقتراح تصحيحات ينفّذها البرنامج بعد موافقة المهندس.

قواعد صارمة:
1. استعمل فقط البيانات في «سياق المشروع» أدناه. لا تخترع أرقام بنود ولا أسعاراً ولا معرّفات. إن لم تكن المعلومة موجودة قل ذلك.
2. الكميات يحسبها البرنامج بكود ثابت. لا تقترح تغيير كمية إلا مع سبب هندسي واضح، واذكر الرقم القديم والجديد.
3. أي تغيير تقترحه يكون كإجراء في كتلة واحدة في نهاية ردك بالشكل:
```actions
[{"type": "...", ..., "reason": "سبب مختصر"}]
```
   الأنواع المسموحة فقط:
   - {"type":"set_space_type","space_id":"...","value":"<مفتاح نوع من القائمة>"}
   - {"type":"rename_space","space_id":"...","value":"اسم"}
   - {"type":"answer_question","question_key":"...","item_id":<رقم من خيارات ذلك السؤال: option_sets[options_set]>}
   - {"type":"skip_question","question_key":"..."}
   - {"type":"set_quantity","line_key":"<مفتاح السطر>","qty":<رقم>}
   - {"type":"set_price","item_id":<رقم>,"price":<سعر الوحدة بالدينار>}
   - {"type":"add_item","item_code":"<رقم بند في اللائحة>","qty":<رقم>,"space_id":"<اختياري>"}  أو بند خارج اللائحة: {"type":"add_item","description":"...","unit":"م2|م.ط|م3|عدد|مقطوعية","price":<رقم>,"qty":<رقم>}
   - {"type":"set_setting","key":"room_height|wet_wall_tile_height|door_height|window_height|window_sill|opening_tolerance_m|price_markup_pct","value":<رقم>}
4. لا تضع كتلة actions إلا إن كان هناك تصحيح فعلي. لا تكرر اقتراحاً رفضه المهندس.
5. وضّح دائماً أن التطبيق بيد المهندس (زر «تطبيق» تحت كل اقتراح).
"""


def build_context(win) -> str:
    """ملخص المشروع المفتوح (مضغوط) لإرساله مع كل سؤال."""
    p, r = win.project, win.result
    st = p.effective_settings()
    types = config.load("space_types.json")
    ctx: dict = {"project": p.name, "boq_version": p.boq_version,
                 "space_types": {k: v["label"] for k, v in types.items()},
                 "settings": {k: st.get(k) for k in ("room_height", "wet_wall_tile_height", "door_height", "window_height",
                                                    "window_sill", "opening_tolerance_m", "price_markup_pct")},
                 "files": [{"file": f.path.split("\\")[-1].split("/")[-1], "floor": f.floor, "kind": f.kind} for f in p.files]}
    if r is None:
        ctx["status"] = "لم يُحلَّل أي مخطط بعد"
        return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    ctx["spaces"] = [{"id": s.id, "name": s.name, "type": s.type, "area": round(s.area, 2), "perimeter": round(s.perimeter, 2),
                      "confidence": round(s.confidence, 2), "openings": len(s.openings), "fixtures": s.fixtures}
                     for s in r.spaces[:150]]
    ctx["lines"] = [{"key": f"{L.demand_id}|{L.item_id}", "space": L.space_name, "code": L.item_code,
                     "desc": L.description[:90], "unit": UNIT_AR.get(L.unit, L.unit), "qty": round(L.quantity, 2),
                     "price": L.unit_price, "total": L.total, "calc": L.formula[:120],
                     "flags": [f for f, on in (("تقديرية", L.estimated), ("يدوي", L.manual), ("معدّلة", L.modified)) if on]}
                    for L in r.lines[:300]]
    ctx["total"] = round(sum(L.total for L in r.lines), 2)
    sets: dict[str, list] = {}                      # الأسئلة تتكرر بنفس الخيارات لكل غرفة: نرسل كل قائمة خيارات مرة واحدة
    qs = []
    for q in r.resolution.questions[:30]:
        opts = [{"item_id": o.id, "code": o.code, "desc": o.description[:45], "price": o.unit_price} for o in q.options[:6]]
        sig = json.dumps([o["item_id"] for o in opts])
        sid = next((k for k, v in sets.items() if json.dumps([o["item_id"] for o in v]) == sig), None)
        if sid is None:
            sid = f"S{len(sets) + 1}"; sets[sid] = opts
        qs.append({"key": q.key, "title": q.title, "spaces": q.space_ids[:10], "options_set": sid})
    ctx["questions"] = qs
    ctx["option_sets"] = sets
    ctx["unclassified_spaces"] = [s.id for s in r.unclassified[:60]]
    ctx["pending_chapters"] = [x.get("title") for x in r.pending]
    ctx["warnings"] = r.warnings[:12]
    return json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))


_BLOCK = re.compile(r"```actions\s*(.*?)```", re.S)


def split_reply(text: str) -> tuple[str, list[dict]]:
    """يفصل نص الرد عن كتلة الإجراءات (إن وُجدت)."""
    m = _BLOCK.search(text or "")
    if not m:
        return (text or "").strip(), []
    body = _BLOCK.sub("", text).strip()
    try:
        acts = json.loads(m.group(1))
        acts = acts if isinstance(acts, list) else [acts]
    except (ValueError, TypeError):
        return body, []
    return body, [a for a in acts if isinstance(a, dict)]


def validate(action: dict, win) -> tuple[bool, str]:
    """(صالح؟, وصف عربي للإجراء أو سبب الرفض)."""
    t = action.get("type")
    r, p = win.result, win.project
    if t not in ACTION_TYPES:
        return False, f"نوع إجراء غير مسموح: {t}"
    spaces = {s.id: s for s in (r.spaces if r else [])}
    types = config.load("space_types.json")
    try:
        if t in ("set_space_type", "rename_space"):
            s = spaces.get(str(action.get("space_id")))
            if not s:
                return False, "فراغ غير موجود"
            if t == "set_space_type":
                v = str(action.get("value"))
                if v not in types:
                    return False, "نوع فراغ غير معروف"
                return True, f"تغيير نوع «{s.name or s.id}» من {types.get(s.type, {}).get('label', s.type)} إلى {types[v]['label']}"
            v = str(action.get("value", "")).strip()
            if not v or len(v) > 60:
                return False, "اسم غير صالح"
            return True, f"تسمية «{s.name or s.id}» باسم «{v}»"
        if t in ("answer_question", "skip_question"):
            q = next((q for q in (r.resolution.questions if r else []) if q.key == action.get("question_key")), None)
            if not q:
                return False, "سؤال غير موجود (ربما أُجيب عنه)"
            if t == "skip_question":
                return True, f"تخطّي السؤال: {q.title}"
            it = next((o for o in q.options if str(o.id) == str(action.get("item_id"))), None)
            if not it:
                return False, "البند ليس من خيارات هذا السؤال"
            return True, f"الإجابة عن «{q.title}» بالبند {it.code} — {it.description[:60]} ({it.unit_price:,.2f} د.ل)"
        if t == "set_quantity":
            key = str(action.get("line_key"))
            L = next((L for L in (r.lines if r else []) if f"{L.demand_id}|{L.item_id}" == key), None)
            q = float(action.get("qty"))
            if not L:
                return False, "سطر غير موجود"
            if q < 0 or q > 1e7:
                return False, "كمية خارج المدى"
            return True, f"تعديل كمية {L.item_code} في «{L.space_name}» من {L.quantity:.2f} إلى {q:.2f} {UNIT_AR.get(L.unit, L.unit)}"
        if t == "set_price":
            iid = int(action.get("item_id")); pr = float(action.get("price"))
            it = win.db.by_id(iid)
            if not it:
                return False, "بند غير موجود في اللائحة"
            if pr < 0 or pr > 1e7:
                return False, "سعر خارج المدى"
            return True, f"سعر البند {it.code} من {it.unit_price:,.2f} إلى {pr:,.2f} د.ل"
        if t == "add_item":
            q = float(action.get("qty", 0))
            if q <= 0 or q > 1e7:
                return False, "كمية غير صالحة"
            if action.get("item_code"):
                it = next((i for i in win.db.items() if i.code == str(action["item_code"]).strip()), None)
                if not it:
                    return False, f"البند {action['item_code']} غير موجود في اللائحة"
                action["item_id"] = it.id
                return True, f"إضافة البند {it.code} — {it.description[:60]} بكمية {q:.2f} {UNIT_AR.get(it.unit or '', it.unit)}"
            desc = str(action.get("description", "")).strip(); pr = float(action.get("price", 0))
            if not desc or pr < 0:
                return False, "بند حر ناقص الوصف أو السعر"
            return True, f"إضافة بند خارج اللائحة: {desc[:60]} — {q:.2f} {action.get('unit', '')} × {pr:,.2f} د.ل"
        if t == "set_setting":
            k = action.get("key"); v = float(action.get("value"))
            if k not in NUM_SETTINGS:
                return False, "إعداد غير مسموح"
            lo, hi = NUM_SETTINGS[k]
            if not lo <= v <= hi:
                return False, "قيمة خارج المدى"
            return True, f"تغيير الإعداد {k} إلى {v:g}"
    except (TypeError, ValueError) as e:
        return False, f"قيمة غير صالحة: {e}"
    return False, "غير مدعوم"


def apply(action: dict, win) -> str:
    """ينفّذ إجراءً صالحاً (بعد موافقة المهندس) ويعيد الحساب."""
    t = action["type"]; p = win.project
    if t == "set_space_type":
        p.edit_space(str(action["space_id"]), type=str(action["value"]))
    elif t == "rename_space":
        p.edit_space(str(action["space_id"]), name=str(action["value"]).strip())
    elif t in ("answer_question", "skip_question"):
        q = next(q for q in win.result.resolution.questions if q.key == action["question_key"])
        val = SKIP if t == "skip_question" else str(action["item_id"])
        win.de.answer(q.key, val, q.space_ids, True)
        p.decision_log.append({"key": q.key, "value": val, "by": "مساعد (بموافقة المهندس)"})
    elif t == "set_quantity":
        p.qty_overrides[str(action["line_key"])] = float(action["qty"])
    elif t == "set_price":
        p.price_overrides[str(int(action["item_id"]))] = float(action["price"])
    elif t == "add_item":
        if action.get("item_id"):
            p.manual_lines.append({"item_id": int(action["item_id"]), "qty": float(action["qty"]),
                                   "space_id": action.get("space_id") or None, "note": "اقتراح المساعد"})
        else:
            p.manual_lines.append({"free": True, "description": str(action["description"]).strip(),
                                   "unit": str(action.get("unit", "")), "price": float(action.get("price", 0)),
                                   "qty": float(action["qty"]), "space_id": action.get("space_id") or None, "note": "اقتراح المساعد"})
    elif t == "set_setting":
        p.settings[str(action["key"])] = float(action["value"])
        win._load_settings_ui()
    win.recompute()
    return "✓ طُبِّق"


def ask(client, history: list[dict], win) -> str:
    """يرسل المحادثة مع سياق المشروع الحالي. history بلا السياق؛ يُضاف للنظام في كل مرة ليبقى محدّثاً."""
    system = SYSTEM + "\n\nسياق المشروع (JSON):\n" + build_context(win)
    return client.chat(system, history[-16:])
