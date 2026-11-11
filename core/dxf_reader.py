"""قارئ DXF نصّي (ASCII) بلغة بايثون الخالصة: بلا اعتماد على مكتبات خارجية.

يدعم: LWPOLYLINE وPOLYLINE/VERTEX وLINE وTEXT وMTEXT وINSERT (+ATTRIB) والبلوكات المتداخلة،
ووحدة الرسم $INSUNITS وترميز النص (ANSI_1256 العربي أو UTF-8).
لا يدعم DXF الثنائي ولا SPLINE/HATCH (يُتجاهلان بلا خطأ).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from .geometry import Affine, Vert

MAX_DEPTH = 8
INSUNITS_TO_M = {1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0,
                 7: 1000.0, 8: 2.54e-8, 9: 2.54e-5, 10: 0.9144, 14: 0.1, 15: 10.0, 16: 100.0}
INSUNITS_NAME = {0: "غير محدد", 1: "إنش", 2: "قدم", 4: "مم", 5: "سم", 6: "م", 14: "ديسيمتر"}


class DxfError(Exception):
    pass


@dataclass
class Ent:
    kind: str                       # POLY | LINE | TEXT | INSERT
    layer: str
    verts: list[Vert] = field(default_factory=list)
    closed: bool = False
    text: str = ""
    pos: tuple[float, float] = (0.0, 0.0)
    height: float = 0.0
    name: str = ""                  # اسم البلوك لـ INSERT
    xf: Optional[Affine] = None     # تحويل INSERT الكلي (لتقدير الأبعاد)
    attribs: dict[str, str] = field(default_factory=dict)
    depth: int = 0                  # عمق التداخل داخل البلوكات (0 = الرسم مباشرة)


@dataclass
class Block:
    name: str
    base: tuple[float, float]
    ents: list[Ent] = field(default_factory=list)


@dataclass
class Drawing:
    insunits: int = 0
    codepage: str = ""
    blocks: dict[str, Block] = field(default_factory=dict)
    model: list[Ent] = field(default_factory=list)

    def iter_entities(self) -> Iterator[Ent]:
        """كل كيانات المسقط بعد فك البلوكات المتداخلة (مع إبقاء INSERT نفسها)."""
        yield from self._expand(self.model, Affine(), 0)

    def _expand(self, ents: list[Ent], xf: Affine, depth: int) -> Iterator[Ent]:
        for e in ents:
            if e.kind == "INSERT":
                ins = Affine.insert(e.pos[0], e.pos[1], *e.xf_params).then(xf)  # type: ignore[attr-defined]
                gx, gy = xf.apply(*e.pos)
                yield Ent("INSERT", e.layer, pos=(gx, gy), name=e.name, xf=ins, attribs=e.attribs, depth=depth)
                blk = self.blocks.get(e.name)
                if blk and depth < MAX_DEPTH:
                    sx, sy, rot = e.xf_params                                      # type: ignore[attr-defined]
                    inner = Affine.insert(e.pos[0], e.pos[1], sx, sy, rot, *blk.base).then(xf)
                    yield from self._expand(blk.ents, inner, depth + 1)
            else:
                o = _apply(e, xf)
                if depth and o is not e:
                    o.depth = depth
                yield o

    def block_extents(self, name: str, depth: int = 0) -> Optional[tuple[float, float, float, float]]:
        """مستطيل إحاطة البلوك بإحداثياته المحلية (بالنسبة لنقطة الإدراج)."""
        blk = self.blocks.get(name)
        if not blk:
            return None
        xs: list[float] = []; ys: list[float] = []
        for e in blk.ents:
            if e.kind in ("POLY", "LINE"):
                for x, y, _ in e.verts:
                    xs.append(x - blk.base[0]); ys.append(y - blk.base[1])
            elif e.kind == "INSERT" and depth < MAX_DEPTH:
                sub = self.block_extents(e.name, depth + 1)
                if sub:
                    sx, sy, rot = e.xf_params                                      # type: ignore[attr-defined]
                    m = Affine.insert(e.pos[0] - blk.base[0], e.pos[1] - blk.base[1], sx, sy, rot)
                    for cx in (sub[0], sub[2]):
                        for cy in (sub[1], sub[3]):
                            px, py = m.apply(cx, cy); xs.append(px); ys.append(py)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)


def _apply(e: Ent, xf: Affine) -> Ent:
    if e.kind in ("POLY", "LINE"):
        flip = xf.det < 0
        verts = []
        for x, y, b in e.verts:
            nx, ny = xf.apply(x, y)
            verts.append((nx, ny, -b if flip else b))
        return Ent(e.kind, e.layer, verts=verts, closed=e.closed)
    if e.kind == "TEXT":
        return Ent("TEXT", e.layer, text=e.text, pos=xf.apply(*e.pos), height=e.height * xf.scale_y)
    return e


_MTEXT_RE = [(re.compile(r"\\[Pp]"), " "), (re.compile(r"\\[fFHhWwQqTtAaCc][^;]*;"), ""),
             (re.compile(r"\\[LlOoKk]"), ""), (re.compile(r"\\S[^;]*;"), ""), (re.compile(r"[{}]"), "")]
_U_RE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def clean_text(s: str) -> str:
    s = _U_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    for rx, rep in _MTEXT_RE:
        s = rx.sub(rep, s)
    s = s.replace("%%c", "Ø").replace("%%d", "°").replace("%%p", "±").replace("\\\\", "\\")
    return re.sub(r"\s+", " ", s).strip()


_CODEPAGES = {"ANSI_1256": "cp1256", "ANSI_1252": "cp1252", "ANSI_1250": "cp1250",
              "ANSI_1251": "cp1251", "ANSI_1253": "cp1253", "ANSI_1254": "cp1254"}


def _decode(raw: bytes) -> tuple[str, str]:
    head = raw[:200000].decode("latin-1", "replace")
    ver = re.search(r"\$ACADVER\s*\r?\n\s*1\s*\r?\n\s*(AC\d+)", head)
    cp = re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n\s*([^\r\n]+)", head)
    cpname = cp.group(1).strip() if cp else ""
    if raw[:1] == b"\xef":
        raw = raw[3:]
    if ver and ver.group(1) >= "AC1021":
        return raw.decode("utf-8", "replace"), cpname or "UTF-8"
    return raw.decode(_CODEPAGES.get(cpname.upper(), "cp1252"), "replace"), cpname


def _pairs(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    out = []
    for i in range(0, len(lines) - 1, 2):
        try:
            out.append((int(lines[i].strip()), lines[i + 1].rstrip("\r")))
        except ValueError:
            continue
    return out


def _f(v: str) -> float:
    try:
        return float(v)
    except ValueError:
        return 0.0


def read_dxf(path: str | Path) -> Drawing:
    raw = Path(path).read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise DxfError("الملف DXF ثنائي. احفظه من الأوتوكاد بصيغة DXF نصّي (ASCII).")
    if raw[:6] in (b"AC1015", b"AC1018", b"AC1021", b"AC1024", b"AC1027", b"AC1032") or raw[:2] == b"AC":
        raise DxfError("هذا ملف DWG وليس DXF. حوّله إلى DXF (الأمر SAVEAS ثم DXF) أو بـ ODA File Converter.")
    text, cp = _decode(raw)
    pairs = _pairs(text)
    dr = Drawing(codepage=cp)
    section = ""
    i, n = 0, len(pairs)
    cur_block: Optional[Block] = None
    records: list[tuple[str, list[tuple[int, str]]]] = []

    def flush_records(target: list[Ent]) -> None:
        j = 0
        while j < len(records):
            t, pr = records[j]
            if t == "LWPOLYLINE":
                e = _lwpoly(pr)
                if e: target.append(e)
            elif t == "LINE":
                d = _group(pr)
                target.append(Ent("LINE", d.get(8, "0"),
                                  verts=[(_f(d.get(10, "0")), _f(d.get(20, "0")), 0.0),
                                         (_f(d.get(11, "0")), _f(d.get(21, "0")), 0.0)]))
            elif t in ("TEXT", "MTEXT"):
                d = _group(pr)
                body = "".join(v for c, v in pr if c == 3) + d.get(1, "")
                body = clean_text(body)
                if body:
                    px, py = _f(d.get(10, "0")), _f(d.get(20, "0"))
                    aligned = t == "TEXT" and (d.get(72, "0") != "0" or d.get(73, "0") != "0") and 11 in d
                    if aligned:                      # نص محاذى: نقطة المحاذاة الفعلية هي 11/21
                        px, py = _f(d.get(11, "0")), _f(d.get(21, "0"))
                    target.append(Ent("TEXT", d.get(8, "0"), text=body, pos=(px, py), height=_f(d.get(40, "0"))))
            elif t == "POLYLINE":
                d = _group(pr)
                vs: list[Vert] = []
                j += 1
                while j < len(records) and records[j][0] != "SEQEND":
                    if records[j][0] == "VERTEX":
                        dv = _group(records[j][1])
                        vs.append((_f(dv.get(10, "0")), _f(dv.get(20, "0")), _f(dv.get(42, "0"))))
                    j += 1
                if len(vs) >= 2:
                    target.append(Ent("POLY", d.get(8, "0"), verts=vs, closed=bool(int(_f(d.get(70, "0"))) & 1)))
            elif t == "INSERT":
                d = _group(pr)
                e = Ent("INSERT", d.get(8, "0"), pos=(_f(d.get(10, "0")), _f(d.get(20, "0"))), name=d.get(2, ""))
                e.xf_params = (_f(d.get(41, "1")) or 1.0, _f(d.get(42, "1")) or 1.0, _f(d.get(50, "0")))  # type: ignore[attr-defined]
                if int(_f(d.get(66, "0"))) == 1:
                    j += 1
                    while j < len(records) and records[j][0] != "SEQEND":
                        if records[j][0] == "ATTRIB":
                            da = _group(records[j][1])
                            e.attribs[da.get(2, "").strip().upper()] = clean_text(da.get(1, ""))
                        j += 1
                target.append(e)
            j += 1
        records.clear()

    while i < n:
        code, val = pairs[i]
        if code == 0 and val == "SECTION" and i + 1 < n:
            section = pairs[i + 1][1].strip(); i += 2; continue
        if code == 0 and val == "ENDSEC":
            section = ""; i += 1; continue
        if section == "HEADER" and code == 9:
            if val == "$INSUNITS" and i + 1 < n:
                dr.insunits = int(_f(pairs[i + 1][1]))
            i += 1; continue
        if section in ("ENTITIES", "BLOCKS") and code == 0:
            j = i + 1
            while j < n and pairs[j][0] != 0:
                j += 1
            body = pairs[i + 1:j]
            if section == "BLOCKS" and val == "BLOCK":
                d = _group(body)
                cur_block = Block(d.get(2, ""), (_f(d.get(10, "0")), _f(d.get(20, "0"))))
                records.clear()
            elif section == "BLOCKS" and val == "ENDBLK":
                if cur_block is not None:
                    flush_records(cur_block.ents)
                    dr.blocks[cur_block.name] = cur_block
                cur_block = None
            else:
                records.append((val, body))
            i = j; continue
        i += 1
    flush_records(dr.model)
    # الكيانات التي سقطت داخل BLOCKS قبل ENDBLK تمت معالجتها؛ هنا ENTITIES فقط
    return dr


def _group(pr: list[tuple[int, str]]) -> dict[int, str]:
    d: dict[int, str] = {}
    for c, v in pr:
        if c not in d:
            d[c] = v
    return d


def _lwpoly(pr: list[tuple[int, str]]) -> Optional[Ent]:
    layer, flags = "0", 0
    verts: list[list[float]] = []
    for c, v in pr:
        if c == 8: layer = v
        elif c == 70: flags = int(_f(v))
        elif c == 10: verts.append([_f(v), 0.0, 0.0])
        elif c == 20 and verts: verts[-1][1] = _f(v)
        elif c == 42 and verts: verts[-1][2] = _f(v)
    if len(verts) < 2:
        return None
    return Ent("POLY", layer, verts=[(x, y, b) for x, y, b in verts], closed=bool(flags & 1))
