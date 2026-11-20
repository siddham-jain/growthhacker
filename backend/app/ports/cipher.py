from typing import NamedTuple, Protocol


class EncryptedValue(NamedTuple):
    kid: str
    ciphertext: bytes


class SecretCipher(Protocol):
    # binding ties a ciphertext to its row so it cannot be replayed elsewhere
    def encrypt(self, plaintext: bytes, *, binding: str) -> EncryptedValue: ...

    def decrypt(self, kid: str, ciphertext: bytes, *, binding: str) -> bytes: ...

    def rotate(self, kid: str, ciphertext: bytes, *, binding: str) -> EncryptedValue: ...
