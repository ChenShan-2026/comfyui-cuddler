"""US dollar amounts as the Platform API writes them: decimal strings, never floats."""
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
import re

from .errors import CuddlerError

_AMOUNT = re.compile(r"-?(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?")
MIN_CAP_MICROS = 10_000            # $0.01
MAX_CAP_MICROS = 1_000_000_000     # $1,000


def to_micros(text):
    if not isinstance(text, str) or not _AMOUNT.fullmatch(text):
        raise CuddlerError("invalid_amount")
    return int(Decimal(text) * 1_000_000)


def from_micros(micros):
    sign = "-" if micros < 0 else ""
    micros = abs(micros)
    return f"{sign}{micros // 1_000_000}.{micros % 1_000_000:06d}"


def cap_from_widget(value):
    """The node's spending-limit widget -> the API's max_usd string, in whole micro-dollars."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise CuddlerError("invalid_cap")
        amount = amount.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError):
        raise CuddlerError("invalid_cap") from None
    micros = int(amount * 1_000_000)
    if not MIN_CAP_MICROS <= micros <= MAX_CAP_MICROS:
        raise CuddlerError("invalid_cap")
    return from_micros(micros)


def display(text, *, down=False):
    """'$0.66' or '-$1.50' for people. A charge is rounded away from zero, so it is never shown smaller than it is.

    `down=True` is for a limit or a balance: rounded toward zero, so it is never shown larger than it is (a hold of
    $2.006 over a $2.005 limit reads "up to $2.01, above the $2.00 limit", as the web price label writes it).
    """
    micros = to_micros(text)
    rounding = ROUND_DOWN if down else ROUND_UP
    amount = Decimal(abs(micros)) / 1_000_000
    shown = amount.quantize(Decimal("0.0001"), rounding=rounding)
    if shown == 0 or shown >= Decimal("0.01"):
        shown = amount.quantize(Decimal("0.01"), rounding=rounding)
    return f"{'-' if micros < 0 and shown else ''}${shown}"
