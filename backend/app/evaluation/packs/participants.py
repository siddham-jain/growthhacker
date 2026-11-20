import uuid

# labeller and rater names become stable pseudonymous ids; names are never stored
PARTICIPANT_NAMESPACE = uuid.UUID("6c1f3a0e-5b8e-4b8f-9d0b-2c4e6a8f0d12")


def participant_id(name: str) -> uuid.UUID:
    cleaned = name.strip().lower()
    if not cleaned:
        raise ValueError("a participant name is required")
    return uuid.uuid5(PARTICIPANT_NAMESPACE, cleaned)
