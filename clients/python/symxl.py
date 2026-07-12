"""
symxl — Excel for Python, powered by Java's Apache POI through Sym.

    import symxl

    sheet = symxl.open("sales.xlsx")
    print(sheet["A1"])                # read any cell
    sheet["B2"] = 42                  # write cells
    sheet.save("sales.xlsx")

    wb = symxl.new()                  # or start fresh
    s = wb.sheet("Q3")
    s["A1"] = "Revenue"

Python never runs a line of POI. POI is a Java library, full stop.
Every cell you touch is a live Java object in a JVM that Sym launched;
this file is ~90 lines of sugar over object handles.

Requires the jars once:  sym add --java poi
"""

import re
import sym

_DF = None  # DataFormatter, created lazily


def _fmt():
    global _DF
    if _DF is None:
        _DF = sym.java("org.apache.poi.ss.usermodel.DataFormatter")()
    return _DF


def _a1(ref: str):
    """'B12' → (row 11, col 1)"""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    if not m:
        raise ValueError(f"'{ref}' is not an A1-style cell reference")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, col - 1


class Sheet:
    def __init__(self, handle, workbook):
        self._h = handle
        self._wb = workbook

    def __getitem__(self, ref):
        r, c = _a1(ref)
        row = self._h.getRow(r)
        if repr(row).endswith("None") or row is None:
            return ""
        cell = row.getCell(c)
        if cell is None:
            return ""
        return _fmt().formatCellValue(cell)

    def __setitem__(self, ref, value):
        r, c = _a1(ref)
        row = self._h.getRow(r)
        if row is None:
            row = self._h.createRow(r)
        cell = row.getCell(c)
        if cell is None:
            cell = row.createCell(c)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cell.setCellValue(float(value))
        else:
            cell.setCellValue(str(value))

    @property
    def name(self):
        return self._h.getSheetName()


class Workbook:
    def __init__(self, handle):
        self._h = handle

    def sheet(self, name_or_index=0):
        if isinstance(name_or_index, int):
            if self._h.getNumberOfSheets() == 0:
                return Sheet(self._h.createSheet("Sheet1"), self)
            return Sheet(self._h.getSheetAt(name_or_index), self)
        existing = self._h.getSheet(name_or_index)
        if existing is None:
            return Sheet(self._h.createSheet(name_or_index), self)
        return Sheet(existing, self)

    def save(self, path):
        out = sym.java("java.io.FileOutputStream")(path)
        self._h.write(out)
        out.close()

    def close(self):
        self._h.close()


def open(path):  # noqa: A001 — mirrors builtins.open on purpose
    f = sym.java("java.io.File")(path)
    wf = sym.java("org.apache.poi.ss.usermodel.WorkbookFactory")
    return Workbook(wf.create(f))


def new():
    return Workbook(sym.java("org.apache.poi.xssf.usermodel.XSSFWorkbook")())


# convenience: sheet-level open, matching the dream syntax exactly
def open_sheet(path, which=0):
    return open(path).sheet(which)
