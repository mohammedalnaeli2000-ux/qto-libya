"""تحويل DWG إلى DXF عبر أداة خارجية إن وُجدت (ODA File Converter أو LibreDWG)."""
from __future__ import annotations
import shutil, subprocess, tempfile
from pathlib import Path


class DwgConvertError(Exception):
    pass


HELP = ("لا توجد أداة تحويل DWG على هذا الجهاز. افتح الملف في AutoCAD ثم نفّذ SAVEAS واختر DXF "
        "(AutoCAD 2018 ASCII)، أو ثبّت ODA File Converter المجاني من موقع opendesign.com وسيعمل التحويل تلقائياً.")


def convert_dwg_to_dxf(src: Path, out_dir: Path | None = None) -> Path:
    src = Path(src)
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="qto_dwg_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    oda = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if not oda:
        for c in (r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
                  r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe"):
            if Path(c).exists():
                oda = c; break
    try:
        if oda:
            with tempfile.TemporaryDirectory() as tmp:
                shutil.copy(src, Path(tmp) / src.name)
                subprocess.run([oda, tmp, str(out_dir), "ACAD2018", "DXF", "0", "1", src.name],
                               check=True, capture_output=True, timeout=180)
        elif shutil.which("dwg2dxf"):
            subprocess.run(["dwg2dxf", "-y", "-o", str(out_dir / (src.stem + ".dxf")), str(src)],
                           check=True, capture_output=True, timeout=180)
        else:
            raise DwgConvertError(HELP)
    except subprocess.SubprocessError as e:
        raise DwgConvertError(f"فشل تحويل الملف: {e}") from e
    out = out_dir / (src.stem + ".dxf")
    if not out.exists():
        raise DwgConvertError("لم ينتج التحويل ملف DXF. " + HELP)
    return out
