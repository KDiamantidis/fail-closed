"""Classify a signing request by its shape. Never guess: anything unclear is other_call."""

from dataclasses import dataclass
from enum import StrEnum

from app.models import Authorization, TxRequest

TRANSFER = "0xa9059cbb"  # transfer(address,uint256)
APPROVE = "0x095ea7b3"  # approve(address,uint256)
NATIVE = "native"


class Kind(StrEnum):
    NATIVE_TRANSFER = "native_transfer"
    ERC20_TRANSFER = "erc20_transfer"
    ERC20_APPROVE = "erc20_approve"
    DELEGATION = "delegation"
    OTHER_CALL = "other_call"


@dataclass(frozen=True)
class Decoded:
    kind: Kind
    destination: str  # who receives value (recipient for transfers, else tx.to)
    asset: str  # "native" or the token address
    amount: int  # wei or token base units; 0 if unknown
    note: str = ""


def authority_of(auth: Authorization, tx: TxRequest) -> str:
    """The account that signs an authorization. Unsigned and unnamed -> the request's signer."""
    return auth.authority or tx.from_


def _two_words(data: str) -> tuple[str, int] | None:
    """Decode (address,uint256) args strictly: exactly two words, clean address padding."""
    args = data[10:]
    if len(args) != 128:
        return None
    addr_word, amount_word = args[:64], args[64:]
    if addr_word[:24] != "0" * 24:
        return None
    return "0x" + addr_word[24:], int(amount_word, 16)


def decode(tx: TxRequest, known_tokens: list[str]) -> Decoded:
    if tx.authorization_list is not None:
        return Decoded(Kind.DELEGATION, tx.to, NATIVE, tx.value,
                       f"{len(tx.authorization_list)} EIP-7702 authorization(s)")

    if tx.data == "0x":
        return Decoded(Kind.NATIVE_TRANSFER, tx.to, NATIVE, tx.value)

    selector = tx.data[:10]
    if selector == TRANSFER and tx.value == 0 and tx.to in known_tokens:
        parsed = _two_words(tx.data)
        if parsed is not None:
            recipient, amount = parsed
            return Decoded(Kind.ERC20_TRANSFER, recipient, tx.to, amount)
        return Decoded(Kind.OTHER_CALL, tx.to, NATIVE, tx.value, "malformed transfer calldata")

    if selector == APPROVE and tx.value == 0:
        parsed = _two_words(tx.data)
        if parsed is not None:
            spender, amount = parsed
            return Decoded(Kind.ERC20_APPROVE, spender, tx.to, amount)
        return Decoded(Kind.OTHER_CALL, tx.to, NATIVE, tx.value, "malformed approve calldata")

    note = "transfer() on a token that is not on KNOWN_TOKENS" if selector == TRANSFER else ""
    return Decoded(Kind.OTHER_CALL, tx.to, NATIVE, tx.value, note)
