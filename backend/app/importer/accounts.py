"""Cuentas contables: correspondencia entre el plan de Quipu y el de Holded.

Restricciones del API de Holded:
  - `POST /accounting/v1/account` crea «la siguiente libre» bajo un prefijo de 4 dígitos (no se
    elige el número): solo es seguro crear una subcuenta si todas las anteriores existen y no hay
    ninguna posterior. Los huecos se rellenan con el catálogo de Quipu.
  - No crea cuentas base (xxxx0000): crea la xxxx0001. Por eso, si Quipu usa una cuenta base que no
    existe en Holded, se usa la primera subcuenta del grupo.
"""

from typing import Any

from app.clients import HoldedClient
from app.importer.base import RecordError

NON_DEDUCTIBLE_SUFFIX = " – no deducible IRPF"


def account_code(category: dict[str, Any], subcategory: dict[str, Any] | None) -> str:
    """Cuenta de Quipu: prefijo de la categoría + sufijo de la subcategoría (p. ej. 62900003)."""
    prefix = str(category["prefix"])
    digits = int(category.get("accounting_digits_number") or 8)
    suffix = int((subcategory or {}).get("suffix") or 0)
    return f"{prefix}{suffix:0{digits - len(prefix)}d}"


def holded_account_number(holded: HoldedClient, number: int) -> int | None:
    """Cuenta de Holded que corresponde a la de Quipu (la base o, si falta, la xxxx0001)."""
    names = holded.accounting_account_names()
    if number in names:
        return number
    if number % 10_000 == 0 and number + 1 in names:
        return number + 1
    return None


def ensure_exact_account(
    holded: HoldedClient, number: int, name: str | None, catalog: dict[str, str]
) -> int:
    """Garantiza que existe en Holded la cuenta de Quipu y devuelve su número en Holded."""
    names = holded.accounting_account_names()
    existing = holded_account_number(holded, number)
    if existing is not None:
        if names[existing].endswith(NON_DEDUCTIBLE_SUFFIX):
            raise RecordError(
                f"En Holded la cuenta {existing} es una subcuenta «no deducible»: no coincide "
                f"con la {number} de Quipu. Revisa el plan contable"
            )
        return existing
    prefix, base = number // 10_000, (number // 10_000) * 10_000
    siblings = [n for n in names if n // 10_000 == prefix]
    if number == base and not siblings:
        holded.create_accounting_account(prefix, name or catalog.get(str(number)))
        created = holded_account_number(holded, number)
        if created is None:
            raise RecordError(f"Holded no ha creado la cuenta del grupo {prefix} esperada")
        return created
    if base not in names or max(siblings) > number:
        raise RecordError(
            f"Falta la cuenta {number} en Holded y no se puede crear con ese número "
            "automáticamente: créala a mano en el plan contable y vuelve a cargar"
        )
    for missing in range(max(siblings) + 1, number + 1):
        missing_name = catalog.get(str(missing))
        if missing == number:
            missing_name = name or missing_name  # sin nombre, Holded usa el de la cuenta padre
        elif not missing_name:
            raise RecordError(
                f"Para crear la cuenta {number} hace falta antes la {missing}, que no está "
                "en Quipu: créala a mano en Holded y vuelve a cargar"
            )
        holded.create_accounting_account(prefix, missing_name)
        if missing not in holded.accounting_accounts():
            raise RecordError(f"Holded no ha creado la cuenta {missing} con el número esperado")
    return number


def non_deductible_account_id(holded: HoldedClient, number: int) -> str | None:
    """Subcuenta «<nombre> – no deducible IRPF» del mismo grupo de 4 dígitos que `number`."""
    names = holded.accounting_account_names()
    existing = holded_account_number(holded, number)
    if existing is None:
        return None
    wanted = names[existing] + NON_DEDUCTIBLE_SUFFIX
    for num, name in names.items():
        if num // 10_000 == number // 10_000 and name == wanted:
            return holded.accounting_accounts()[num]
    return None


def ensure_non_deductible_account(holded: HoldedClient, number: int) -> None:
    if non_deductible_account_id(holded, number):
        return
    names = holded.accounting_account_names()
    existing = holded_account_number(holded, number)
    holded.create_accounting_account(number // 10_000, names[existing] + NON_DEDUCTIBLE_SUFFIX)
    if not non_deductible_account_id(holded, number):
        raise RecordError(f"Holded no ha creado la subcuenta no deducible de {number}")
