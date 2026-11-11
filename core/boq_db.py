"""قراءة قاعدة المقايسة (SQLite) وبناء قائمة البنود المسعّرة مع نص الآباء."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .models import BoqItem

DEFAULT_DB = DATA_DIR / "boq.db"


class BoqDb:
    def __init__(self, path: Path | str = DEFAULT_DB, version: Optional[str] = None):
        self.path = Path(path)
        self.con = sqlite3.connect(self.path, check_same_thread=False)   # الويب: كل تحديث للصفحة في خيط مختلف
        self.version = version or self.versions()[-1]
        self._items: Optional[list[BoqItem]] = None

    def versions(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT name FROM boq_versions ORDER BY id")]

    def building_sheet(self) -> str:
        """ورقة/جزء أعمال المباني (الأول في الإصدار)."""
        r = self.con.execute("SELECT sheet FROM boq_items WHERE version_id=(SELECT id FROM boq_versions WHERE name=?) "
                             "ORDER BY id LIMIT 1", (self.version,)).fetchone()
        return r[0]

    def items(self) -> list[BoqItem]:
        """البنود المسعّرة (أوراق المباني) مع ancestors = نص العناوين الأب."""
        if self._items is not None:
            return self._items
        sheet = self.building_sheet()
        rows = self.con.execute(
            "SELECT id,code,parent_code,chapter_no,description,context,unit,unit_price,is_header,unit_raw "
            "FROM boq_items WHERE version_id=(SELECT id FROM boq_versions WHERE name=?) AND sheet=? ORDER BY id",
            (self.version, sheet)).fetchall()
        heads: dict[str, str] = {}
        out: list[BoqItem] = []
        for (id_, code, parent, ch, desc, ctx, unit, price, is_h, uraw) in rows:
            chain = []
            p = parent
            while p:                                   # ننسب الأب الأقرب السابق لهذا الصف
                if p in heads and "." in p:            # نتجاوز عنوان الباب (مستوى واحد)
                    chain.append(heads[p])
                p = ".".join(p.split(".")[:-1]) or None
            anc = " ← ".join(reversed([c[:90] for c in chain]))
            if is_h:
                heads[code] = desc
                continue
            if unit is None or price is None or price <= 0:
                continue
            out.append(BoqItem(id_, code, parent, ch or 0, desc, ctx or "", unit, float(price), sheet, anc, (uraw or "").strip()))
        self._items = out
        return out

    def by_id(self, item_id: int) -> Optional[BoqItem]:
        return next((i for i in self.items() if i.id == item_id), None)

    def chapters(self) -> dict[int, str]:
        sheet = self.building_sheet()
        return {r[0]: r[1] for r in self.con.execute(
            "SELECT chapter_no, description FROM boq_items WHERE version_id=(SELECT id FROM boq_versions WHERE name=?) "
            "AND sheet=? AND code NOT LIKE '%.%' ORDER BY id", (self.version, sheet))}

    def outline(self, used_ids: set[int]) -> list[dict]:
        """صفوف اللائحة بترتيبها الأصلي لما استُخدم فقط: عناوين الآباء (باب/قسم/عنوان) ثم البنود.

        كل صف: {id, code, description, kind: chapter|section|heading|item, chapter_no, unit_raw, unit_price}
        """
        sheet = self.building_sheet()
        rows = self.con.execute(
            "SELECT id,code,parent_code,chapter_no,description,is_header,unit_raw,unit_price FROM boq_items "
            "WHERE version_id=(SELECT id FROM boq_versions WHERE name=?) AND sheet=? ORDER BY id", (self.version, sheet)).fetchall()
        heads: dict[str, int] = {}
        need: set[int] = set()
        for (id_, code, parent, ch, desc, is_h, uraw, price) in rows:
            if is_h:
                heads[code] = id_
                continue
            if id_ in used_ids:
                need.add(id_)
                p = parent
                while p:
                    if p in heads:
                        need.add(heads[p])
                    p = ".".join(p.split(".")[:-1]) or None
        out = []
        for (id_, code, parent, ch, desc, is_h, uraw, price) in rows:
            if id_ not in need:
                continue
            dots = code.count(".")
            kind = "item" if not is_h else ("chapter" if dots == 0 else "section" if dots == 1 else "heading")
            out.append({"id": id_, "code": code, "description": " ".join((desc or "").split()), "kind": kind,
                        "chapter_no": ch or 0, "unit_raw": (uraw or "").strip(), "unit_price": price})
        return out
