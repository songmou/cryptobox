class CryptoboxError(Exception):
    """Base exception for expected Cryptobox failures."""


class InvalidPassword(CryptoboxError):
    pass


class InvalidVault(CryptoboxError):
    pass


class PlainFile(CryptoboxError):
    pass


class CorruptFile(CryptoboxError):
    pass


class UnsafePath(CryptoboxError):
    pass


class ConcurrentModification(CryptoboxError):
    pass

