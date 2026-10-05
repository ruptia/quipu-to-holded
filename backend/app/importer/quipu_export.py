"""Documentos del exportador de Quipu → gastos y tickets.

El API de Quipu no da el documento original de los gastos, pero su exportador sí los baja
todos. Cada fichero se llama «<cuenta>-<N>-1-<…>-<…>-<…>-<…>-<aleatorio>-<nombre original>»,
donde N sigue el orden de creación de los asientos en Quipu (su id). No es una posición exacta,
porque Quipu numera asientos que el API no devuelve, pero el orden se respeta siempre.

Emparejamiento:
  1. Anclas: ficheros cuyo nombre original contiene el número de un único gasto.
  2. Entre dos anclas consecutivas, ficheros y gastos van en el mismo orden: si hay tantos de
     unos como de otros, se emparejan uno a uno. Si no, quedan sin emparejar para revisarlos.
Las cuotas de amortización (cuenta 68x) no tienen documento y no cuentan.

Verificación: en los PDF se busca el total del gasto, su número o el emisor.
"""

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pypdf import PdfReader


@dataclass(frozen=True)
class ExportFile:
    name: str  # nombre con el que lo exporta Quipu
    index: int  # N: orden de creación en Quipu
    original: str  # nombre original del documento


@dataclass(frozen=True)
class ExpenseRef:
    key: Any  # identificador del llamante (p. ej. el id del registro)
    quipu_id: int
    number: str | None
    account: str | None


@dataclass(frozen=True)
class Match:
    file: ExportFile
    expense: ExpenseRef
    method: str  # "número": ancla por número; "orden": por posición entre anclas


@dataclass
class MatchResult:
    matches: list[Match]
    unmatched_files: list[ExportFile]
    unmatched_expenses: list[ExpenseRef]  # se esperaba documento y no hay fichero
    without_document: list[ExpenseRef]  # amortizaciones


def _normalize(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def parse_export_name(name: str) -> ExportFile | None:
    parts = name.split("-", 8)
    if len(parts) < 9 or not parts[1].isdigit() or not parts[8]:
        return None
    return ExportFile(name=name, index=int(parts[1]), original=parts[8])


def expects_document(expense: ExpenseRef) -> bool:
    return not (expense.account or "").startswith("68")


def _number_in_name(expense: ExpenseRef, file: ExportFile) -> bool:
    number = _normalize(expense.number)
    return len(number) >= 4 and number in _normalize(file.original)


def _increasing(anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """La mayor secuencia de anclas creciente en ficheros y en gastos (descarta las que
    contradicen el orden, p. ej. un número que aparece por casualidad en otro nombre)."""
    best: list[list[tuple[int, int]]] = []
    for anchor in anchors:
        candidates = [chain for chain in best if chain[-1][1] < anchor[1]]
        chain = max(candidates, key=len, default=[]) + [anchor]
        best.append(chain)
    return max(best, key=len, default=[])


def match_documents(files: Sequence[ExportFile], expenses: Sequence[ExpenseRef]) -> MatchResult:
    ordered_files = sorted(files, key=lambda f: (f.index, f.original))
    candidates = sorted((e for e in expenses if expects_document(e)), key=lambda e: e.quipu_id)

    anchors = []
    for file_index, file in enumerate(ordered_files):
        found = [i for i, expense in enumerate(candidates) if _number_in_name(expense, file)]
        if len(found) == 1:
            anchors.append((file_index, found[0]))
    anchors = _increasing(anchors)

    matches, unmatched_files, unmatched_expenses = [], [], []
    bounds = [(-1, -1), *anchors, (len(ordered_files), len(candidates))]
    for (file_a, expense_a), (file_b, expense_b) in zip(bounds, bounds[1:], strict=False):
        gap_files = ordered_files[file_a + 1 : file_b]
        gap_expenses = candidates[expense_a + 1 : expense_b]
        if len(gap_files) == len(gap_expenses):
            matches += [Match(f, e, "orden") for f, e in zip(gap_files, gap_expenses, strict=True)]
        else:
            unmatched_files += gap_files
            unmatched_expenses += gap_expenses
        if file_b < len(ordered_files):
            matches.append(Match(ordered_files[file_b], candidates[expense_b], "número"))

    return MatchResult(
        matches=matches,
        unmatched_files=unmatched_files,
        unmatched_expenses=unmatched_expenses,
        without_document=[e for e in expenses if not expects_document(e)],
    )


# --- Verificación del contenido ---


def pdf_text(content: bytes) -> str:
    try:
        return " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    except Exception:  # PDF dañado o cifrado: no se puede verificar
        return ""


def _amount_variants(total: str | None) -> set[str]:
    try:
        value = Decimal(str(total))
    except (InvalidOperation, TypeError):
        return set()
    dot = f"{value:.2f}"
    thousands = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return {dot, dot.replace(".", ","), thousands}


def verify_text(text: str, *, total: str | None, number: str | None, issuer: str | None) -> str:
    """'importe', 'número' o 'emisor' según lo que se encuentre en el texto; si no, 'no cuadra'."""
    if any(variant in text for variant in _amount_variants(total)):
        return "importe"
    if len(_normalize(number)) >= 4 and _normalize(number) in _normalize(text):
        return "número"
    words = [w for w in re.split(r"\W+", (issuer or "").upper()) if len(w) > 3][:2]
    if words and all(word in text.upper() for word in words):
        return "emisor"
    return "no cuadra"


def verify_document(
    content: bytes,
    content_type: str,
    *,
    total: str | None,
    number: str | None,
    issuer: str | None,
) -> str:
    """'importe' / 'número' / 'emisor' / 'no cuadra', o 'sin verificar' (imagen o PDF sin texto)."""
    if content_type != "application/pdf":
        return "sin verificar"
    text = pdf_text(content)
    if not text.strip():
        return "sin verificar"
    return verify_text(text, total=total, number=number, issuer=issuer)
