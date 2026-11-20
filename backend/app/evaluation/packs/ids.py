import os
import uuid
from datetime import datetime


def batch_uuid(at: datetime) -> uuid.UUID:
    """uuidv7 layout (ms timestamp first), so batch ids sort by creation time."""
    millis = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (
        (millis << 80) | (0x7 << 76) | ((rand >> 68) & 0xFFF) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    )
    return uuid.UUID(int=value)
