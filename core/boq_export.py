"""تصدير قاعدة المقايسة إلى JSON، وتقرير الصفوف المعلَّمة للمراجعة (CSV)."""
from __future__ import annotations
import csv, json, sqlite3, sys
from pathlib import Path

def export_json(db: Path, out: Path, version: str) -> None:
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    vid = con.execute("SELECT id FROM boq_versions WHERE name=?", (version,)).fetchone()[0]
    rows = [dict(r) for r in con.execute("SELECT * FROM boq_items WHERE version_id=? ORDER BY id", (vid,))]
    out.write_text(json.dumps({"version": version, "items": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

def review_report(db: Path, out: Path) -> int:
    con = sqlite3.connect(db)
    rows = con.execute("SELECT sheet,src_row,code,flags,unit_raw,unit_price,description FROM boq_items "
                       "WHERE flags!='' ORDER BY sheet,src_row").fetchall()
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["الورقة", "رقم الصف في Excel", "رقم البند", "العلامات", "الوحدة", "السعر", "الوصف"])
        w.writerows(rows)
    return len(rows)

if __name__ == "__main__":
    db = Path(sys.argv[1]); v = sys.argv[2]
    export_json(db, db.with_suffix(".json"), v)
    print(review_report(db, db.parent / "review_flags.csv"), "flagged rows")
