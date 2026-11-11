"""استيراد لائحة 2026 من JSON (مستخرج من الـ PDF) إلى نفس مخطط SQLite.

الأرقام تُنقل كما هي دون تعديل؛ التحقق الآلي يوسم المشكوك فيه فقط.
"""
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.boq_importer import SCHEMA, load_unit_map, _norm_unit_text


def import_json(src: Path, db: Path, version: str) -> int:
    data = json.loads(src.read_text(encoding="utf-8"))
    unit_map = load_unit_map()
    rows, seen = [], {}
    for part in data["price_list"]["parts"]:
        sheet = part["name"]
        codes = {i["code"] for i in part["items"] if i["code"]}
        ctx = {}
        for i in part["items"]:
            if i["type"] == "note":
                ctx.setdefault(i["parent_code"], []).append(i["description"])
        for n, i in enumerate(part["items"]):
            if i["type"] == "note":
                continue
            code, price = i["code"], i["price_lyd"]
            unit_raw = i["unit"]
            unit = unit_map.get(_norm_unit_text(unit_raw)) if unit_raw else None
            is_item = i["type"] == "item"
            flags = []
            if is_item and price is None: flags.append("item_without_price")
            if is_item and price is not None and price <= 0: flags.append("non_positive_price")
            if is_item and price is not None and not unit_raw: flags.append("price_without_unit")
            if unit_raw and unit is None: flags.append("unknown_unit")
            if i["parent_code"] and i["parent_code"] not in codes: flags.append("missing_parent")
            seen[(sheet, code)] = seen.get((sheet, code), 0) + 1
            parts = code.split(".")
            rows.append([sheet, i["page"], code, i["parent_code"], int(parts[0]), i["description"],
                         unit, unit_raw, price, int(price is None), " | ".join(ctx.get(code, [])),
                         flags])
    for r in rows:
        if seen[(r[0], r[2])] > 1: r[11].append("duplicate_code")
    con = sqlite3.connect(db); con.executescript(SCHEMA)
    vid = con.execute("INSERT INTO boq_versions(name, source) VALUES (?,?)", (version, src.name)).lastrowid
    con.executemany(
        "INSERT INTO boq_items(version_id,sheet,src_row,code,parent_code,chapter_no,description,"
        "unit,unit_raw,unit_price,is_header,context,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(vid, *r[:11], ",".join(r[11])) for r in rows])
    con.commit(); con.close()
    return len(rows)

if __name__ == "__main__":
    print(import_json(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]), "items")
