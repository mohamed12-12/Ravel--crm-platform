from __future__ import annotations
from typing import Any


class FakeCell:
    """Mimics openpyxl's Cell object."""
    __slots__ = ("_value", "row", "col", "sheet_name", "_mutations", "_row_data")

    def __init__(self, value: Any, row: int, col: int, sheet_name: str, mutations: list, row_data: list) -> None:
        self._value = value
        self.row = row
        self.col = col
        self.sheet_name = sheet_name
        self._mutations = mutations
        self._row_data = row_data

    @property
    def value(self) -> Any:
        return self._value

    @value.setter
    def value(self, val: Any) -> None:
        self._value = val
        self._row_data[self.col - 1] = "" if val is None else val
        # Track this mutation
        self._mutations.append({
            "type": "update",
            "sheet": self.sheet_name,
            "row": self.row,
            "col": self.col,
            "value": val
        })

    @property
    def coordinate(self) -> str:
        # Minimal coordinate support for translate_formula if needed
        # (Very basic A1 notation)
        col_str = ""
        c = self.col
        while c > 0:
            c, rem = divmod(c - 1, 26)
            col_str = chr(65 + rem) + col_str
        return f"{col_str}{self.row}"


class SheetRowAdapter:
    """
    Wraps a list-of-lists (from gspread.get_all_values()) as an
    openpyxl-like worksheet object.
    """

    def __init__(self, rows: list[list[str]], title: str = "", mutations: list | None = None) -> None:
        self._rows = [list(r) for r in rows]  # copy to mutable lists
        self.title = title
        self.mutations = mutations if mutations is not None else []

    @property
    def max_row(self) -> int:
        return len(self._rows)

    @property
    def max_column(self) -> int:
        return max((len(r) for r in self._rows), default=0)

    def cell(self, row: int, col: int) -> FakeCell:
        """1-indexed, same as openpyxl."""
        r_idx, c_idx = row - 1, col - 1
        
        # Ensure row exists in internal storage if we are writing to it
        while len(self._rows) <= r_idx:
            self._rows.append([])
            
        row_data = self._rows[r_idx]
        while len(row_data) <= c_idx:
            row_data.append("")
            
        val = row_data[c_idx]
        # Return a cell that can track its own updates
        return FakeCell(
            value=val if val != "" else None,
            row=row,
            col=col,
            sheet_name=self.title,
            mutations=self.mutations,
            row_data=row_data,
        )

    def append(self, values: list | dict) -> None:
        """
        Mimics openpyxl's ws.append().
        Only supports list/tuple of values for now.
        """
        if isinstance(values, (list, tuple)):
            row_idx = len(self._rows) + 1
            self._rows.append(list(values))
            self.mutations.append({
                "type": "append",
                "sheet": self.title,
                "values": list(values),
                "row": row_idx
            })
        else:
            raise NotImplementedError("ws.append() only supports lists in SheetRowAdapter")

    def iter_rows(
        self,
        min_row: int = 1,
        max_row: int | None = None,
        min_col: int = 1,
        max_col: int | None = None,
        values_only: bool = False,
    ):
        end_row = min(max_row or self.max_row, self.max_row)
        end_col = max_col or self.max_column
        for r in range(min_row, end_row + 1):
            if values_only:
                yield tuple(self.cell(r, c).value for c in range(min_col, end_col + 1))
            else:
                yield tuple(self.cell(r, c) for c in range(min_col, end_col + 1))


class FakeWorkbook:
    """
    Minimal openpyxl Workbook stand-in.
    """
    def __init__(self, sheets: dict[str, SheetRowAdapter]) -> None:
        self._sheets = sheets
        self.mutations: list = []
        for ws in self._sheets.values():
            ws.mutations = self.mutations

    @property
    def sheetnames(self) -> list[str]:
        return list(self._sheets.keys())

    def __getitem__(self, name: str) -> SheetRowAdapter:
        return self._sheets[name]

    def create_sheet(self, name: str) -> SheetRowAdapter:
        if name not in self._sheets:
            # Create a new empty sheet adapter
            ws = SheetRowAdapter(rows=[], title=name, mutations=self.mutations)
            self._sheets[name] = ws
            # We don't track the creation of the sheet itself in mutations yet,
            # but any rows added to it WILL be tracked as 'append' or 'update'.
            # The gateway _append_row will handle creating the worksheet if it doesn't exist
            # if we wanted to be very robust, but for now we assume it exists or use gspread's
            # worksheet() which errors if not found.
        return self._sheets[name]

    def close(self) -> None:
        pass

    def save(self, filename: Any = None) -> None:
        pass  # mutations are tracked in self.mutations
