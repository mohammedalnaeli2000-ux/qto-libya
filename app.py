"""QTO Libya — نسخة الويب (تعمل على الهاتف والكمبيوتر من رابط). نفس حسابات البرنامج المكتبي (core/ و ai/).
تشغيل محلي:  streamlit run web/app.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "core").exists():          # محلياً: web/app.py ؛ على الخادم: app.py في الجذر
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from ai import assistant as A
from ai.client import AiClient, AiUnavailable
from core import config
from core.boq_db import DEFAULT_DB, BoqDb
from core.decision_engine import SKIP
from core.exporter import export_xlsx
from core.plan_tools import auto_detect_rooms, render_plan_png
from core.project import Project, ProjectFile

SURVEY_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdOj29ZTkyWcIoAOiweAhomz8jgWsD4vF4QxP3axcbja9OgVQ/viewform"
PHONE = "0915108809"
UNIT_AR = {"m2": "م2", "lm": "م.ط", "m3": "م3", "count": "عدد", "lump": "مقطوعية"}
TYPES = config.load("space_types.json")
TYPE_LABELS = {k: v["label"] for k, v in TYPES.items()}
LABEL_TO_TYPE = {v: k for k, v in TYPE_LABELS.items()}

st.set_page_config(page_title="QTO Libya", page_icon="📐", layout="centered")
st.markdown("""<style>
html, body, [class*="css"], .stApp { direction: rtl; text-align: right; font-family: Tahoma, "Segoe UI", sans-serif; }
.stTabs [data-baseweb="tab-list"] { flex-wrap: wrap; gap: 4px; }
.stTabs [data-baseweb="tab"] { padding: 6px 10px; }
[data-testid="stMetricValue"] { direction: ltr; text-align: right; }
.stDataFrame, .stDataEditor { direction: rtl; }
.small { color: #5b6782; font-size: 0.9rem; }
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- الحالة
class Session:
    """بديل النافذة الرئيسية بما تحتاجه دوال المساعد (project/db/result/de/recompute)."""
    def __init__(self):
        self.project = Project()
        self.db = BoqDb(DEFAULT_DB)
        self.project.boq_version = self.db.version
        self.result = None
        self.de = None
        self.workdir = Path(tempfile.mkdtemp(prefix="qto_web_"))
        self.chat: list[dict] = []
        self.proposals: list[dict] = []

    def recompute(self, show: bool = True):
        self.db = BoqDb(DEFAULT_DB, self.project.boq_version or None)
        self.result, self.de = self.project.compute(self.db, self.de)

    def _load_settings_ui(self):
        pass


if "s" not in st.session_state:
    st.session_state.s = Session()
S: Session = st.session_state.s
P = S.project


def ai_client():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:                                   # Streamlit Cloud: الأسرار في st.secrets
        try:
            key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:                                            # noqa: BLE001
            key = ""
    if not key:
        try:
            d = json.loads((config.CONFIG_DIR / "ai_default.json").read_text(encoding="utf-8")); key = d.get("key", "")
        except (OSError, ValueError):
            key = ""
    if not key:                                   # تشغيل محلي: إعدادات الذكاء الاصطناعي الخاصة بالمستخدم على جهازه
        from ai import settings as ai_settings
        return ai_settings.build_client()
    try:
        return AiClient("gemini", os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"), key)
    except AiUnavailable:
        return None


def reanalyze():
    P._drawings.clear()
    P.analyze()
    S.de = None
    S.recompute()


# ---------------------------------------------------------------- الرأس
st.markdown("## 📐 QTO Libya")
st.markdown("<div class='small'>حصر الكميات وإعداد المقايسات وفق اللائحة الاسترشادية الليبية — نسخة تجريبية تعمل من المتصفح</div>",
            unsafe_allow_html=True)

with st.expander("⚙️ إعدادات المشروع", expanded=False):
    P.name = st.text_input("اسم المشروع", P.name)
    versions = S.db.versions()
    P.boq_version = st.selectbox("إصدار لائحة الأسعار", versions, index=max(0, versions.index(P.boq_version)) if P.boq_version in versions else len(versions) - 1)
    st_ = P.effective_settings()
    c1, c2 = st.columns(2)
    room_h = c1.number_input("ارتفاع الفراغ (م)", 2.0, 8.0, float(st_["room_height"]), 0.05)
    wet_h = c2.number_input("ارتفاع تبليط الجدران المبللة (م)", 0.5, 8.0, float(st_["wet_wall_tile_height"]), 0.05)
    door_h = c1.number_input("ارتفاع الباب (م)", 1.5, 4.0, float(st_["door_height"]), 0.05)
    win_h = c2.number_input("ارتفاع الشباك (م)", 0.3, 3.0, float(st_["window_height"]), 0.05)
    markup = c1.number_input("نسبة زيادة الأسعار %", -50.0, 300.0, float(st_.get("price_markup_pct", 0) or 0), 1.0)
    prices = c2.selectbox("الأسعار في Excel", ["بأسعار اللائحة/السوق", "خانة السعر فارغة"], index=1 if st_.get("export_prices") == "blank" else 0)
    unit = st.selectbox("وحدة الرسم", ["تلقائي من الملف", "mm", "cm", "m"],
                        index=["تلقائي من الملف", "mm", "cm", "m"].index(st_.get("unit_override") or "تلقائي من الملف"))
    if st.button("حفظ الإعدادات وإعادة الحساب"):
        P.settings.update(room_height=room_h, wet_wall_tile_height=wet_h, door_height=door_h, window_height=win_h,
                          price_markup_pct=markup, export_prices="blank" if prices.startswith("خانة") else "list",
                          unit_override=None if unit.startswith("تلقائي") else unit)
        if P.files:
            with st.spinner("جارٍ إعادة الحساب..."):
                reanalyze()
        st.success("حُفظت الإعدادات")

# ---------------------------------------------------------------- 1) رفع المخطط
st.markdown("### 1️⃣ ارفع مسقط الأوتوكاد (DXF)")
st.markdown("<div class='small'>من الأوتوكاد: SAVEAS ← DXF. ملفات DWG غير مدعومة في نسخة الويب.</div>", unsafe_allow_html=True)
up = st.file_uploader("ملف DXF", type=["dxf"], label_visibility="collapsed")
sample = ROOT / "data" / "sample_flat.dxf"
use_sample = sample.exists() and st.button("🏠 ليس معي ملف الآن: جرّب بمخطط شقة نموذجية", use_container_width=True)
if use_sample:
    up = type("Up", (), {"name": "شقة-نموذجية.dxf", "getbuffer": lambda self: sample.read_bytes()})()
if up is not None and (not P.files or Path(P.files[0].path).name != up.name):
    path = S.workdir / up.name
    path.write_bytes(up.getbuffer())
    P.files = [ProjectFile(str(path), "الأرضي", "plan")]
    P.manual_spaces, P.space_edits, P.answers, P.qty_overrides, P.price_overrides, P.manual_lines = [], {}, {}, {}, {}, []
    S.chat, S.proposals = [], []
    with st.spinner("جارٍ قراءة المخطط وتحليله... (الملفات الكبيرة تأخذ حتى دقيقة)"):
        try:
            reanalyze()
            if not S.result.spaces:                                  # بلا طبقة غرف: اكتشاف تلقائي مباشرة
                n = auto_detect_rooms(P, P.files[0])
                if n:
                    reanalyze()
                    st.toast(f"لم تكن في الملف طبقة غرف، فاكتُشفت {n} غرفة تلقائياً من الجدران")
        except Exception as e:                                       # noqa: BLE001
            st.error(f"تعذّر قراءة الملف: {e}"); st.stop()

if not P.files or S.result is None:
    st.info("ارفع ملف DXF لتبدأ. يمكنك تجربة البرنامج بمخطط شقة بسيطة.")
    st.markdown(f"📝 [استبيان المقترحات]({SURVEY_URL}) · 📞 واتساب {PHONE}")
    st.stop()

pf = P.files[0]
R = S.result
for w in R.warnings[:3]:
    if R.spaces and ("لم يُعثر على فراغات" in w or "ربط الطبقات" in w):
        continue
    st.caption("⚠ " + w)

# ---------------------------------------------------------------- 2) الغرف
st.markdown("### 2️⃣ الغرف")
c1, c2 = st.columns([1, 1])
if c1.button("✨ اكتشاف الغرف تلقائياً", use_container_width=True, help="للمخططات بدون طبقة غرف: يحدد كل غرفة مغلقة بالجدران"):
    with st.spinner("جارٍ اكتشاف الغرف..."):
        try:
            n = auto_detect_rooms(P, pf)
            reanalyze()
            st.success(f"اكتُشفت {n} غرفة")
        except Exception as e:                                       # noqa: BLE001
            st.error(f"تعذّر الاكتشاف: {e}")
if c2.button("🗑 حذف الغرف المكتشفة", use_container_width=True):
    P.manual_spaces = []; reanalyze()
R = S.result
spaces = [s for s in R.spaces if s.source_file == Path(pf.path).name] or R.spaces
try:
    st.image(render_plan_png(P, pf, spaces), caption="الأرقام على المخطط تقابل أرقام الجدول", use_container_width=True)
except Exception as e:                                               # noqa: BLE001
    st.caption(f"تعذّر رسم المخطط: {e}")

if not spaces:
    st.warning("لا توجد غرف بعد. اضغط «اكتشاف الغرف تلقائياً».")
else:
    df = pd.DataFrame([{"#": i, "id": s.id, "الاسم": s.name or s.id, "النوع": TYPE_LABELS.get(s.type, s.type),
                        "المساحة م2": round(s.area, 2), "المحيط م": round(s.perimeter, 2)} for i, s in enumerate(spaces, 1)])
    ed = st.data_editor(df, hide_index=True, use_container_width=True, disabled=["#", "id", "المساحة م2", "المحيط م"],
                        column_config={"id": None, "النوع": st.column_config.SelectboxColumn("النوع", options=list(TYPE_LABELS.values()), required=True)},
                        key=f"rooms_{len(spaces)}_{len(P.space_edits)}")
    changed = False
    for (_, a), (_, b) in zip(df.iterrows(), ed.iterrows()):
        if a["النوع"] != b["النوع"]:
            P.edit_space(a["id"], type=LABEL_TO_TYPE[b["النوع"]]); changed = True
        if a["الاسم"] != b["الاسم"] and str(b["الاسم"]).strip():
            P.edit_space(a["id"], name=str(b["الاسم"]).strip()); changed = True
    if changed:
        S.recompute(); st.rerun()
    unk = sum(1 for s in spaces if s.type == "unknown")
    if unk:
        st.info(f"{unk} غرفة «غير مصنّفة»: حدّد نوعها من عمود «النوع» حتى تُحسب بنودها.")

# ---------------------------------------------------------------- 3) الأسئلة
qs = R.resolution.questions
st.markdown(f"### 3️⃣ الأسئلة ({len(qs)})")
if not qs:
    st.success("لا أسئلة معلّقة ✓")
else:
    st.markdown("<div class='small'>اختر البند المناسب من اللائحة لكل سؤال، ثم «اعتماد الإجابات». السؤال يُطبَّق على كل الغرف المشابهة.</div>",
                unsafe_allow_html=True)
    with st.form("questions"):
        picks = {}
        for q in qs[:25]:
            opts = ["— لاحقاً —", "لا ينطبق (تخطٍّ)"] + [f"{o.code} — {o.description[:70]} [{o.unit_price:,.2f} د.ل]" for o in q.options]
            picks[q.key] = (q, st.selectbox(q.title, opts, key=f"q_{q.key}"))
        if st.form_submit_button("اعتماد الإجابات", use_container_width=True):
            for key, (q, choice) in picks.items():
                if choice.startswith("—"):
                    continue
                val = SKIP if choice.startswith("لا ينطبق") else str(q.options[[f"{o.code} — {o.description[:70]} [{o.unit_price:,.2f} د.ل]" for o in q.options].index(choice)].id)
                S.de.answer(q.key, val, q.space_ids, True)
                P.decision_log.append({"key": q.key, "value": val, "by": "web"})
            S.recompute(); st.rerun()
    if len(qs) > 25:
        st.caption(f"تظهر أول 25 سؤالاً من {len(qs)}؛ اعتمدها لتظهر البقية.")

# ---------------------------------------------------------------- 4) المقايسة
lines = R.lines
total = sum(L.total for L in lines)
st.markdown("### 4️⃣ المقايسة")
m1, m2, m3 = st.columns(3)
m1.metric("الإجمالي (د.ل)", f"{total:,.2f}"); m2.metric("الأسطر", len(lines)); m3.metric("أسئلة معلّقة", len(qs))
if lines:
    bq = pd.DataFrame([{"key": f"{L.demand_id}|{L.item_id}", "الفراغ": L.space_name, "البند": L.item_code, "الوصف": L.description[:80],
                        "الوحدة": UNIT_AR.get(L.unit, L.unit), "الكمية": round(L.quantity, 2), "السعر": L.unit_price,
                        "الإجمالي": L.total,
                        "ملاحظة": "؛ ".join(n for n, f in (("تقديرية", L.estimated), ("يدوي", L.manual), ("معدّلة", L.modified),
                                                           ("سعر معدّل", L.price_modified)) if f)} for L in lines])
    st.caption("✎ عدّل «الكمية» أو «السعر» مباشرة في الجدول (السعر يُطبَّق على كل أسطر البند).")
    bed = st.data_editor(bq, hide_index=True, use_container_width=True, column_config={"key": None},
                         disabled=["الفراغ", "البند", "الوصف", "الوحدة", "الإجمالي", "ملاحظة"], key=f"boq_{len(lines)}_{total:.2f}")
    changed = False
    for (_, a), (_, b) in zip(bq.iterrows(), bed.iterrows()):
        demand, iid = a["key"].rsplit("|", 1)
        if abs(float(a["الكمية"]) - float(b["الكمية"])) > 1e-9:
            if demand.startswith("manual_free:"):
                P.manual_lines[int(demand.split(":")[1])]["qty"] = float(b["الكمية"])
            elif demand.startswith("manual:"):
                for m in P.manual_lines:
                    if not m.get("free") and int(m["item_id"]) == int(iid): m["qty"] = float(b["الكمية"]); break
            else:
                P.qty_overrides[a["key"]] = float(b["الكمية"])
            changed = True
        if abs(float(a["السعر"]) - float(b["السعر"])) > 1e-9:
            if demand.startswith("manual_free:"):
                P.manual_lines[int(demand.split(":")[1])]["price"] = float(b["السعر"])
            else:
                P.price_overrides[iid] = float(b["السعر"])
            changed = True
    if changed:
        S.recompute(); st.rerun()

with st.expander("➕ إضافة بند يدوي (من اللائحة أو بند حر)"):
    free = st.toggle("بند غير موجود في اللائحة (بند حر)")
    space_opts = {"— بلا فراغ (مشروع) —": None} | {(s.name or s.id): s.id for s in R.spaces}
    if free:
        desc = st.text_input("وصف البند")
        c1, c2, c3 = st.columns(3)
        unit_f = c1.selectbox("الوحدة", list(UNIT_AR.keys()), format_func=lambda u: UNIT_AR[u])
        price_f = c2.number_input("السعر", 0.0, 1e9, 0.0)
        qty_f = c3.number_input("الكمية", 0.0, 1e9, 0.0, key="qf")
        sp_f = st.selectbox("الفراغ", list(space_opts), key="spf")
        if st.button("إضافة البند الحر") and desc.strip() and qty_f > 0:
            P.manual_lines.append({"free": True, "description": desc.strip(), "unit": unit_f, "price": price_f, "qty": qty_f,
                                   "space_id": space_opts[sp_f], "note": "web"}); S.recompute(); st.rerun()
    else:
        qtxt = st.text_input("ابحث برقم البند أو كلمة من الوصف (مثل: حفر)")
        hits = []
        if qtxt.strip():
            from core.textnorm import norm_ar
            words = norm_ar(qtxt).split()
            hits = [it for it in S.db.items() if all(w in norm_ar(it.code + " " + it.ancestors + " " + it.description) for w in words)][:40]
        if hits:
            lab = {f"{it.code} — {it.description[:80]} [{it.unit_price:,.2f}/{UNIT_AR.get(it.unit or '', it.unit)}]": it for it in hits}
            pick = st.selectbox("البند", list(lab))
            c1, c2 = st.columns(2)
            qty = c1.number_input("الكمية", 0.0, 1e9, 0.0, key="qi")
            sp = c2.selectbox("الفراغ", list(space_opts), key="spi")
            if st.button("إضافة البند") and qty > 0:
                P.manual_lines.append({"item_id": lab[pick].id, "qty": qty, "space_id": space_opts[sp], "note": "web"})
                S.recompute(); st.rerun()

if R.pending:
    with st.expander(f"⏳ البنود المعلّقة ({len(R.pending)}): أعمال لا تحتويها المساقط"):
        for p in R.pending:
            st.markdown(f"- **{p['title']}**: {p['reason']}")

# ---------------------------------------------------------------- 5) التصدير
st.markdown("### 5️⃣ التصدير")
if lines:
    out = S.workdir / f"{P.name or 'QTO'}.xlsx"
    try:
        export_xlsx(R, P, S.db, out)
        st.download_button("📥 تحميل المقايسة Excel (بشكل اللائحة)", out.read_bytes(), file_name=out.name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    except Exception as e:                                           # noqa: BLE001
        st.error(f"تعذّر التصدير: {e}")

# ---------------------------------------------------------------- 6) المساعد الذكي
st.markdown("### 💬 المساعد الذكي")
st.markdown("<div class='small'>يقرأ مشروعك ويشرح ويراجع ويقترح تصحيحات. <b>لا يتغير شيء إلا بموافقتك</b> (زر «تطبيق»).</div>", unsafe_allow_html=True)
for m in S.chat:
    with st.chat_message("user" if m["role"] == "user" else "assistant"):
        st.markdown(m["content"])
for i, p in enumerate(S.proposals):
    if p["state"] != "pending":
        continue
    with st.container(border=True):
        st.markdown(f"**{p['desc']}**" + (f"  \n<span class='small'>السبب: {p['reason']}</span>" if p["reason"] else ""), unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if c1.button("تطبيق", key=f"ap_{i}", type="primary", use_container_width=True):
            ok, why = A.validate(p["action"], S)
            if ok:
                A.apply(p["action"], S); p["state"] = "done"
            else:
                p["state"] = "invalid"; st.warning(why)
            st.rerun()
        if c2.button("تجاهل", key=f"rj_{i}", use_container_width=True):
            p["state"] = "rejected"; S.chat.append({"role": "user", "content": f"(رفضتُ الاقتراح: {p['desc']})"}); st.rerun()

quick = st.columns(2)
q1 = quick[0].button("راجع المقايسة واقترح تصحيحات", use_container_width=True)
q2 = quick[1].button("ساعدني في الأسئلة المعلّقة", use_container_width=True)
prompt = st.chat_input("اسأل المساعد عن مشروعك...")
if q1: prompt = "راجع المقايسة الحالية كمهندس حصر خبير وأعطني أهم الملاحظات باختصار، واقترح التصحيحات كإجراءات."
if q2: prompt = "ساعدني في الإجابة عن الأسئلة المعلّقة: اقترح الخيار الأنسب لمبنى سكني ليبي عادي مع السبب، كإجراءات."
if prompt:
    client = ai_client()
    if client is None:
        st.error("المساعد غير مفعّل على هذا الخادم.")
    else:
        S.chat.append({"role": "user", "content": prompt})
        with st.spinner("المساعد يكتب..."):
            try:
                reply = client.chat(A.SYSTEM + "\n\nسياق المشروع (JSON):\n" + A.build_context(S), S.chat[-16:])
            except Exception as e:                                   # noqa: BLE001
                reply = f"تعذّر الوصول للمساعد: {e}"
        body, actions = A.split_reply(reply)
        S.chat.append({"role": "assistant", "content": body or reply})
        for a in actions:
            ok, desc = A.validate(a, S)
            if ok:
                S.proposals.append({"action": a, "desc": desc, "reason": str(a.get("reason", "")), "state": "pending"})
        st.rerun()

st.divider()
st.markdown(f"📝 [استبيان المقترحات]({SURVEY_URL}) · 📞 للتواصل والنقاش: واتساب {PHONE} · "
            "💻 [النسخة المكتبية](https://drive.google.com/drive/folders/1viOs2zS5lkoHYr94zReUzcOfwdltvx1a?usp=sharing)")
