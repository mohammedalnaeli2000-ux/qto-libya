"""استيراد لائحة الأسعار الاسترشادية من ملف Excel إلى قاعدة SQLite.

المبدأ: لا يُخترع أي رقم. كل ما لا يمكن الجزم به يُوسم في عمود flags للمراجعة.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import openpyxl

CONFIG = Path(__file__).resolve().parent.parent / "config" / "units.json"

# الأوراق التي تحوي بنود أسعار (تُستثنى الفهرس والعلاوات والأعمال الهندسية)
PRICE_SHEETS_SKIP = {"االمحتويات", "علاوة منطقة", "الأعمال الهندسية"}

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def load_unit_map() -> dict[str, str]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {_norm_unit_text(v): k for k, vs in data.items() for v in vs}


def _norm_unit_text(s: str) -> str:
    # نزيل التطويل والمسافات والنقاط والشرطات المائلة ليتطابق: م.ط = م ط = م/ط = م . ط
    t = str(s).translate(_AR_DIGITS).replace("\u0640", "")
    return re.sub(r"[\s.\-/\\_]+", "", t)


@dataclass
class Item:
    sheet: str
    row: int
    code: str
    parent_code: Optional[str]
    chapter_no: Optional[int]
    description: str
    unit: Optional[str]
    unit_raw: Optional[str]
    unit_price: Optional[float]
    is_header: bool
    context: str = ""
    flags: list[str] = field(default_factory=list)


def _code_parts(code: str) -> list[int]:
    return [int(p) for p in code.split(".")]


class CodeFixer:
    """يعالج فقدان الأصفار اللاحقة: 9.10 المخزّنة كعدد عشري تصبح 9.1.

    القاعدة: في مستوى البنود ذات الرقمين (فصل.رقم) نتوقع أن يكون الرقم
    التالي = الأخير + 1. إن كان الرقم المقروء m والمتوقع m*10 فنعتمد m*10.
    """

    def __init__(self) -> None:
        self.last_minor: dict[int, int] = {}

    def fix(self, raw) -> tuple[str, bool]:
        if isinstance(raw, int):
            return str(raw), False
        if isinstance(raw, float):
            text = repr(raw)
            major_s, _, minor_s = text.partition(".")
            major, minor = int(major_s), int(minor_s or 0)
            repaired = False
            last = self.last_minor.get(major, 0)
            if minor != last + 1 and minor * 10 == last + 1:
                minor *= 10
                repaired = True
            self.last_minor[major] = minor
            return f"{major}.{minor}", repaired
        code = str(raw).strip().translate(_AR_DIGITS)
        parts = code.split(".")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            self.last_minor[int(parts[0])] = int(parts[1])
        return code, False


def parse_sheet(ws, unit_map: dict[str, str]) -> list[Item]:
    items: list[Item] = []
    fixer = CodeFixer()
    chapter_no: Optional[int] = None
    pending_context: list[str] = []
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        raw_code, desc, unit_raw, price = (list(row) + [None] * 4)[:4]
        if r_idx == 1 and isinstance(raw_code, str) and "رقم" in raw_code:
            continue
        desc_s = re.sub(r"\s+", " ", str(desc)).strip() if desc else ""
        if raw_code is None:
            if desc_s and price is None:  # عنوان فرعي بلا رقم (سياق للبنود اللاحقة)
                pending_context = [desc_s]
            continue
        code, repaired = fixer.fix(raw_code)
        if not re.fullmatch(r"\d+(\.\d+)*", code):
            continue
        parts = code.split(".")
        if len(parts) == 1:
            chapter_no = int(parts[0])
            pending_context = []
        parent = ".".join(parts[:-1]) or None
        has_price = isinstance(price, (int, float)) and not isinstance(price, bool)
        unit = None
        if unit_raw:
            unit = unit_map.get(_norm_unit_text(unit_raw))
        flags: list[str] = []
        if repaired:
            flags.append("code_repaired")
        if has_price and unit_raw and unit is None:
            flags.append("unknown_unit")
        if has_price and not unit_raw:
            flags.append("price_without_unit")
        if has_price and price <= 0:
            flags.append("non_positive_price")
        if not has_price and price is not None:
            flags.append("price_not_numeric")
        items.append(Item(
            sheet=ws.title, row=r_idx, code=code, parent_code=parent,
            chapter_no=chapter_no, description=desc_s,
            unit=unit, unit_raw=str(unit_raw).strip() if unit_raw else None,
            unit_price=float(price) if has_price else None,
            is_header=not has_price, context=" | ".join(pending_context),
            flags=flags))
        if not has_price and len(parts) >= 3:
            pending_context = []
    return items


def validate(items: list[Item]) -> None:
    """فحوص آلية: تكرار الأرقام، الأب المفقود، بند فرعي بلا سعر."""
    seen: dict[tuple[str, str], int] = {}
    codes = {(i.sheet, i.code) for i in items}
    for it in items:
        key = (it.sheet, it.code)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            it.flags.append("duplicate_code")
        if it.parent_code and (it.sheet, it.parent_code) not in codes:
            it.flags.append("missing_parent")
    dup = {k for k, v in seen.items() if v > 1}
    for it in items:
        if (it.sheet, it.code) in dup and "duplicate_code" not in it.flags:
            it.flags.append("duplicate_code")
    parents = {(i.sheet, i.parent_code) for i in items if i.parent_code}
    for it in items:
        if it.is_header and (it.sheet, it.code) not in parents and it.parent_code:
            it.flags.append("leaf_without_price")


SCHEMA = """
CREATE TABLE IF NOT EXISTS boq_versions(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, source TEXT, frozen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS boq_items(
  id INTEGER PRIMARY KEY, version_id INTEGER REFERENCES boq_versions(id),
  sheet TEXT, src_row INTEGER, code TEXT, parent_code TEXT, chapter_no INTEGER,
  description TEXT, unit TEXT, unit_raw TEXT, unit_price REAL, is_header INTEGER,
  context TEXT, flags TEXT,
  -- حقول التصنيف (تُملأ في م3 بعد مراجعتي)
  applies_to TEXT, measure_rule TEXT, variant_group TEXT, review_status TEXT DEFAULT 'pending');
CREATE INDEX IF NOT EXISTS ix_items ON boq_items(version_id, sheet, code);
"""


def import_excel(xlsx: Path, db: Path, version: str) -> list[Item]:
    unit_map = load_unit_map()
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    items: list[Item] = []
    for ws in wb.worksheets:
        if ws.title in PRICE_SHEETS_SKIP:
            continue
        items.extend(parse_sheet(ws, unit_map))
    validate(items)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    cur = con.execute("INSERT INTO boq_versions(name, source) VALUES (?,?)", (version, xlsx.name))
    vid = cur.lastrowid
    con.executemany(
        "INSERT INTO boq_items(version_id,sheet,src_row,code,parent_code,chapter_no,description,"
        "unit,unit_raw,unit_price,is_header,context,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(vid, i.sheet, i.row, i.code, i.parent_code, i.chapter_no, i.description, i.unit,
          i.unit_raw, i.unit_price, int(i.is_header), i.context, ",".join(i.flags)) for i in items])
    con.commit()
    con.close()
    return items


if __name__ == "__main__":
    import sys
    its = import_excel(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    print(len(its), "items")
