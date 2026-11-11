"""تصدير المقايسة إلى Excel بنفس شكل وتنسيق لائحة الأسعار (ورقة «المباني» في ملف 2023).

الورقة الرئيسية تحاكي الأصل: RTL، Times New Roman عريض (24 للرقم و28 للباقي)، حدود مزدوجة، تعبئة رمادية
لعناوين الأبواب والأقسام ولخانتي رقم البند والسعر، عرض أعمدة 20/130/21/30، ارتفاع الصف حسب طول النص،
وتُضاف إليها خانتا (الكمية) و(الإجمالي) بصيغ حقيقية. تُسرد فيها الأبواب/الأقسام/العناوين الهرمية الأصلية للبنود المستخدمة فقط.
"""
from __future__ import annotations
import math
import re
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from . import config
from .boq_db import BoqDb
from .decision_engine import MANUAL, SKIP
from .project import FREE_CHAPTER, Project, Result

FREE_TITLE = "بنود إضافية خارج اللائحة"

FONT = "Times New Roman"
GRAY = PatternFill("solid", fgColor="F2F2F2")          # أصل الملف: theme 0 بإضاءة -5%
GRAY2 = PatternFill("solid", fgColor="D9D9D9")          # رأس الفهرس في الأصل: -15%
YELLOW = PatternFill("solid", fgColor="FFF2CC")
BLUE = PatternFill("solid", fgColor="DDEBF7")
DBL = Side(style="double", color="000000")
THIN = Side(style="thin", color="000000")
BOX_DBL = Border(left=DBL, right=DBL, top=DBL, bottom=DBL)
BOX_THIN = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
NUM2, MONEY = "0.00", "#,##0.00"
CHARS_PER_LINE = 72                                      # معايَر من الملف الأصلي (عمود 130 بخط 28)
UNIT_AR = {"m2": "م2", "lm": "م.ط", "m3": "م3", "count": "عدد", "lump": "مقطوعية", "kg": "كجم", "km": "كم", "ton": "طن"}
MAIN = "المقايسة النهائية"


def _code_key(c: str):
    return [int(x) if x.isdigit() else 0 for x in c.split(".")]


def row_height(text: str) -> float:
    lines = max(1, math.ceil(len(text or "") / CHARS_PER_LINE))
    return 36.0 if lines == 1 else round(34.8 * lines + 1, 1)


def _cell(ws, r, c, value=None, size=28, fill=None, nf=None, border=BOX_DBL, color=None):
    cell = ws.cell(r, c, value)
    cell.font = Font(name=FONT, size=size, bold=True, color=color)
    cell.alignment = CENTER
    cell.border = border
    if fill: cell.fill = fill
    if nf: cell.number_format = nf
    return cell


def _page(ws, last_row: int, ncols: int, landscape=False):
    ws.sheet_view.rightToLeft = True
    ws.page_setup.paperSize = 9
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:1"
    ws.print_area = f"A1:{get_column_letter(ncols)}{max(1, last_row)}"
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.83


def _main_sheet(wb: Workbook, result: Result, db: BoqDb, project: Project):
    """الورقة الرئيسية: بنية اللائحة الأصلية + الكمية + الإجمالي."""
    ws = wb.create_sheet(MAIN)
    ws.sheet_properties.tabColor = "00B050"
    ws.sheet_view.zoomScale = 59
    for col, w in zip("ABCDEF", (20.0, 130.25, 21.125, 30.0, 26, 38)):
        ws.column_dimensions[col].width = w
    hdr = ["رقم البند", "بيـــــــــــــــــــــان الاعمـــــــــــــــــــــال", "الوحدة", "السعر  (د. ل)", "الكمية", "الإجمالي  (د. ل)"]
    for c, h in enumerate(hdr, 1):
        _cell(ws, 1, c, h, size=24 if c in (1, 4, 5, 6) else 28, fill=GRAY)
    ws.row_dimensions[1].height = 36

    blank = project.effective_settings().get("export_prices") == "blank"     # مقايسة للتسعير: خانة السعر فارغة
    agg: dict[int, dict] = {}
    for L in result.lines:
        a = agg.setdefault(L.item_id, {"q": 0.0, "est": False, "man": False, "mod": False, "price": L.unit_price, "L": L})
        a["q"] += L.quantity; a["est"] |= L.estimated; a["man"] |= L.manual; a["mod"] |= L.modified
    outline = db.outline({k for k in agg if k > 0})
    chapters = db.chapters()
    r = 2
    ch_first: dict[int, int] = {}; chapter_total_cells: dict[int, str] = {}
    by_chapter: dict[int, list[dict]] = {}
    for row in outline:
        by_chapter.setdefault(row["chapter_no"], []).append(row)
    item_rows_all: list[int] = []
    for ch in sorted(by_chapter):
        rows = by_chapter[ch]
        item_rows: list[int] = []
        has_chapter_row = any(x["kind"] == "chapter" for x in rows)
        if not has_chapter_row:                                  # إن لم يُحفظ صف الباب نكتبه من قائمة الأبواب
            rows = [{"id": 0, "code": str(ch), "description": chapters.get(ch, ""), "kind": "chapter", "chapter_no": ch,
                     "unit_raw": "", "unit_price": None}] + rows
        for x in rows:
            h = row_height(x["description"])
            if x["kind"] != "item":
                _cell(ws, r, 1, x["code"], 24, GRAY, nf="0.0")
                _cell(ws, r, 2, x["description"], 28, GRAY)
                for c in (3, 4, 5, 6): _cell(ws, r, c, None, 28, GRAY)
                ws.row_dimensions[r].height = h
            else:
                a = agg[x["id"]]
                item = db.by_id(x["id"])
                _cell(ws, r, 1, x["code"], 24, GRAY, nf="0.0")
                _cell(ws, r, 2, x["description"], 28, YELLOW if a["est"] else (BLUE if a["man"] else None))
                _cell(ws, r, 3, x["unit_raw"] or UNIT_AR.get(item.unit if item else "", ""), 28)
                _cell(ws, r, 4, None if blank else float(a["price"]), 28, GRAY, nf=NUM2)
                _cell(ws, r, 5, round(a["q"], 2), 28, YELLOW if a["est"] else (BLUE if a["man"] else GRAY), nf=NUM2)
                _cell(ws, r, 6, f"=ROUND(ROUND(E{r},2)*D{r},2)", 28, GRAY, nf=MONEY)
                ws.row_dimensions[r].height = h
                item_rows.append(r)
            r += 1
        # إجمالي الباب
        _cell(ws, r, 1, None, 24, GRAY2); _cell(ws, r, 2, f"إجمالي الباب: {chapters.get(ch, ch)}", 28, GRAY2)
        for c in (3, 4, 5): _cell(ws, r, c, None, 28, GRAY2)
        _cell(ws, r, 6, "=" + "+".join(f"SUM(F{a}:F{b})" for a, b in _ranges(item_rows)), 28, GRAY2, nf=MONEY)
        ws.row_dimensions[r].height = 40
        chapter_total_cells[ch] = f"F{r}"
        item_rows_all += item_rows
        r += 1
    # بنود خارج اللائحة (أدخلها المستخدم بوصف وسعر)
    free = [a for k, a in agg.items() if k < 0]
    if free:
        _cell(ws, r, 1, None, 24, GRAY); _cell(ws, r, 2, FREE_TITLE, 28, GRAY)
        for c in (3, 4, 5, 6): _cell(ws, r, c, None, 28, GRAY)
        r += 1
        item_rows = []
        for a in free:
            L = a["L"]
            _cell(ws, r, 1, "—", 24, GRAY); _cell(ws, r, 2, L.description, 28, BLUE)
            _cell(ws, r, 3, UNIT_AR.get(L.unit, L.unit), 28)
            _cell(ws, r, 4, None if blank else float(a["price"]), 28, GRAY, nf=NUM2)
            _cell(ws, r, 5, round(a["q"], 2), 28, BLUE, nf=NUM2)
            _cell(ws, r, 6, f"=ROUND(ROUND(E{r},2)*D{r},2)", 28, GRAY, nf=MONEY)
            ws.row_dimensions[r].height = row_height(L.description); item_rows.append(r); r += 1
        _cell(ws, r, 1, None, 24, GRAY2); _cell(ws, r, 2, f"إجمالي: {FREE_TITLE}", 28, GRAY2)
        for c in (3, 4, 5): _cell(ws, r, c, None, 28, GRAY2)
        _cell(ws, r, 6, "=" + "+".join(f"SUM(F{a}:F{b})" for a, b in _ranges(item_rows)), 28, GRAY2, nf=MONEY)
        chapter_total_cells[FREE_CHAPTER] = f"F{r}"; r += 1
    # إجمالي المشروع
    last = r
    _cell(ws, r, 1, None, 24, GRAY2); _cell(ws, r, 2, "إجمالي المشروع (دون البنود المعلّقة)", 28, GRAY2)
    for c in (3, 4, 5): _cell(ws, r, c, None, 28, GRAY2)
    _cell(ws, r, 6, "=" + "+".join(chapter_total_cells.values()) if chapter_total_cells else 0, 28, GRAY2, nf=MONEY)
    ws.row_dimensions[r].height = 44
    _page(ws, last, 6)
    return ws, chapter_total_cells, f"F{last}"


def _ranges(rows: list[int]):
    """تجميع أرقام الصفوف المتتالية في مجالات لتقصير الصيغ."""
    out, start, prev = [], None, None
    for x in rows:
        if start is None: start = prev = x
        elif x == prev + 1: prev = x
        else: out.append((start, prev)); start = prev = x
    if start is not None: out.append((start, prev))
    return out


def _table(wb: Workbook, title: str, headers: list[str], widths: list[float], rows: list[list], fills=None, size=14,
           money_cols=(), num_cols=(), wrap_cols=None):
    ws = wb.create_sheet(title)
    ws.sheet_view.zoomScale = 85
    for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w
    for c, h in enumerate(headers, 1): _cell(ws, 1, c, h, size, GRAY2, border=BOX_THIN)
    ws.row_dimensions[1].height = 34
    for r, row in enumerate(rows, 2):
        for c, v in enumerate(row, 1):
            cell = _cell(ws, r, c, v, size - 2, (fills or {}).get(r), border=BOX_THIN)
            cell.font = Font(name=FONT, size=size - 2, bold=False)
            if c in money_cols: cell.number_format = MONEY
            if c in num_cols: cell.number_format = NUM2
        longest = max((len(str(v)) for v in row if isinstance(v, str) and not str(v).startswith("=")), default=0)
        ws.row_dimensions[r].height = max(24, 20 * math.ceil(longest / 60))
    ws.freeze_panes = "A2"
    if rows: ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    _page(ws, len(rows) + 1, len(headers), landscape=True)
    return ws


def export_xlsx(result: Result, project: Project, db: BoqDb, path: str | Path) -> Path:
    types = config.load("space_types.json")
    chapters = db.chapters()
    st = project.effective_settings()
    wb = Workbook(); wb.remove(wb.active)

    # ---- الفهرس والملخص (أول ورقة، على نمط فهرس اللائحة) ----
    idx = wb.create_sheet("الفهرس والملخص")          # تُملأ بعد معرفة خلايا الإجماليات
    main, ch_cells, grand_cell = _main_sheet(wb, result, db, project)

    idx.sheet_properties.tabColor = "00B050"; idx.sheet_view.zoomScale = 70
    for col, w in zip("ABCD", (11, 70, 60, 28)): idx.column_dimensions[col].width = w
    idx.merge_cells("A1:D1")
    _cell(idx, 1, 1, "الفهرس", 36, GRAY, border=Border(left=Side(style="medium"), right=Side(style="medium"), top=Side(style="medium")))
    idx.row_dimensions[1].height = 56
    info = [f"المشروع: {project.name}", f"لائحة الأسعار: {db.version} — الأسعار بالدينار الليبي (د.ل)", f"تاريخ الإعداد: {date.today():%Y/%m/%d}"]
    for i, t in enumerate(info, 2):
        idx.merge_cells(start_row=i, start_column=1, end_row=i, end_column=4)
        _cell(idx, i, 1, t, 20, None, border=BOX_THIN); idx.row_dimensions[i].height = 34
    r0 = len(info) + 2
    for c, h in enumerate(["ر.ت", "بيان العمل", "المحتويات", "الإجمالي (د.ل)"], 1): _cell(idx, r0, c, h, 18, GRAY2, border=BOX_THIN)
    r = r0 + 1
    for ch in sorted(ch_cells):
        name = FREE_TITLE if ch == FREE_CHAPTER else chapters.get(ch, "")
        _cell(idx, r, 1, ch, 20, GRAY2, border=BOX_THIN); _cell(idx, r, 2, name, 22, GRAY, border=BOX_THIN)
        n_items = len({L.item_id for L in result.lines if L.chapter_no == ch})
        _cell(idx, r, 3, f"{n_items} بند", 18, None, border=BOX_THIN)
        _cell(idx, r, 4, f"='{MAIN}'!{ch_cells[ch]}", 20, None, nf=MONEY, border=BOX_THIN)
        idx.row_dimensions[r].height = 36; r += 1
    for c in (1, 2, 3): _cell(idx, r, c, None, 20, GRAY2, border=BOX_THIN)
    _cell(idx, r, 2, "إجمالي المشروع", 22, GRAY2, border=BOX_THIN)
    _cell(idx, r, 4, f"='{MAIN}'!{grand_cell}", 22, GRAY2, nf=MONEY, border=BOX_THIN)
    idx.row_dimensions[r].height = 40
    idx.merge_cells(start_row=r + 2, start_column=1, end_row=r + 2, end_column=4)
    _cell(idx, r + 2, 1, "ملاحظة: الإجمالي لا يشمل البنود المعلّقة (ورقة «بنود معلّقة»). الأصفر = كمية تقديرية، الأزرق = بند يدوي.", 16, None, border=BOX_THIN)
    idx.row_dimensions[r + 2].height = 40
    _page(idx, r + 2, 4)
    idx.print_title_rows = None

    # ---- الحصر: تفصيل الكميات لكل بند وفراغ ----
    rows, fills = [], {}
    for L in sorted(result.lines, key=lambda x: (x.chapter_no, _code_key(x.item_code), x.space_name)):
        typ = types.get(L.space_type or "", {}).get("label", "") if L.space_type else ""
        rr = len(rows) + 2
        rows.append([L.item_code, L.description, L.space_name, typ, L.unit_raw if hasattr(L, "unit_raw") else UNIT_AR.get(L.unit, L.unit),
                     L.quantity, None if st.get("export_prices") == "blank" else L.unit_price, f"=ROUND(ROUND(F{rr},2)*G{rr},2)",
                     L.formula + ("  [تقديرية]" if L.estimated else "") + ("  [معدّلة يدوياً]" if L.modified else "")
                     + (f"  [سعر معدّل: اللائحة {L.base_price:,.2f}]" if L.price_modified else ""),
                     "إدخال يدوي" if L.manual else f"قاعدة: {L.rule_id}"])
        if L.estimated: fills[rr] = YELLOW
        elif L.manual: fills[rr] = BLUE
    _table(wb, "الحصر", ["رقم البند", "الوصف", "الفراغ", "نوع الفراغ", "الوحدة", "الكمية", "سعر الوحدة", "الإجمالي", "طريقة الحصر (المعادلة)", "المصدر"],
           [11, 60, 20, 16, 9, 12, 13, 15, 60, 20], rows, fills, money_cols=(7, 8), num_cols=(6,))

    # ---- الكميات التقديرية والافتراضات ----
    assum = [["افتراض", "ارتفاع الفراغ (م)", st["room_height"]], ["افتراض", "ارتفاع تبليط الجدران المبللة (م)", st["wet_wall_tile_height"]],
             ["افتراض", "ارتفاع الباب الافتراضي (م)", st["door_height"]], ["افتراض", "ارتفاع الشباك الافتراضي (م)", st["window_height"]],
             ["افتراض", "ارتفاع حلق الشباك (م)", st["window_sill"]],
             ["افتراض", "سماكة الجدران/القواطع (سم)", f"{st['wall_thickness_cm']['walls']} / {st['wall_thickness_cm']['partitions']}"],
             ["افتراض", "طريقة قياس أطوال الجدران", st["wall_mode"]], ["افتراض", "مسافة نسب الفتحات للفراغات (م)", st["opening_tolerance_m"]]]
    for p in result.plans: assum.append(["وحدة الرسم", Path(p.file).name, p.unit_note])
    for L in result.lines:
        if L.estimated: assum.append(["كمية تقديرية", f"{L.space_name} — {L.item_code}", L.formula])
    for p in result.plans:
        for o in p.openings:
            if o.estimated: assum.append(["فتحة مقدّرة الأبعاد", f"{o.id} ({o.block})", f"{o.width}×{o.height} م — مصدر العرض: {o.size_source}"])
    for w in result.warnings: assum.append(["تحذير", w, ""])
    _table(wb, "الكميات التقديرية والافتراضات", ["النوع", "البيان", "القيمة/السبب"], [24, 70, 70], assum)

    # ---- سجل القرارات ----
    rules = {x["id"]: x for x in config.load("item_rules.json")}
    dec = []
    for key, val in project.answers.items():
        rid, scope, _ = (key.split("|") + ["", ""])[:3]
        val = str(val)                       # إجابة السؤال تُحفظ رقماً (معرّف البند) أو نصاً (تخطّي/يدوي)
        it = db.by_id(int(val)) if val.lstrip("-").isdigit() else None
        d = "تخطّي" if val == SKIP else "سأحدده يدوياً" if val == MANUAL else (f"{it.code} — {it.description}" if it else val)
        dec.append([rules.get(rid, {}).get("title", rid), scope or "المشروع", d, key])
    for a in result.resolution.auto: dec.append([a["rule"], a["space"], f"بند وحيد في اللائحة: {a['item']}", "(تلقائي)"])
    for sid, e in project.space_edits.items(): dec.append(["تعديل فراغ", sid, "، ".join(f"{k}={v}" for k, v in e.items()), ""])
    dec.append([f"إصدار اللائحة: {db.version}", "", "", ""])
    _table(wb, "سجل القرارات", ["القاعدة", "النطاق", "القرار", "المفتاح"], [36, 24, 80, 34], dec)

    # ---- المعلّقة ----
    pend = [[p["title"], p["reason"], "لا يدخل الإجمالي حتى تُدخل كميته يدوياً"] for p in result.pending]
    for u in result.resolution.unmatched:
        pend.append([u["space"], u["reason"], f"الكمية المحسوبة: {u['demand'].quantity:.2f} {u['demand'].unit}"])
    for s in result.resolution.skipped:
        pend.append([f"{s['space']} — {rules.get(s['demand'].rule_id, {}).get('title', s['demand'].rule_id)}",
                     "ستحدده يدوياً" if s["manual"] else "تُخطّي بقرارك", f"الكمية المحسوبة: {s['demand'].quantity:.2f} {s['demand'].unit}"])
    _table(wb, "بنود معلّقة", ["البند/الباب", "سبب التعليق", "ملاحظة"], [54, 80, 50], pend)

    wb.active = 1                     # تفتح على ورقة المقايسة
    path = Path(path)
    wb.save(path)
    return path
