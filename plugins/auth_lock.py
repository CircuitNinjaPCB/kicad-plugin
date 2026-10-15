import errno
import hashlib
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from service_errors import AuthenticationBusy, AuthLockError, Operation


def lock_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Caches"
    else:
        root = Path.home() / ".cache"
    return root / "CircuitNinja" / "auth-locks"


@contextmanager
def authentication_lock(
    origin: str,
    operation: Operation,
    *,
    timeout: float = 5,
    directory: Path | None = None,
) -> Generator[None, None, None]:
    if timeout < 0:
        raise ValueError("Authentication lock timeout must not be negative.")
    path = (directory if directory is not None else lock_directory()) / (
        hashlib.sha256(origin.encode("utf-8")).hexdigest() + ".lock"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=448)
        handle = path.open("a+b")
    except OSError as exc:
        raise AuthLockError(
            "Cannot open the authentication lock. Check cache directory permissions.",
            operation=operation,
        ) from exc
    with handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise AuthLockError(
                        "Cannot acquire the authentication lock.", operation=operation
                    ) from exc
                if time.monotonic() >= deadline:
                    raise AuthenticationBusy(
                        "Authentication is in progress in another CircuitNinja window. Wait for it to finish, then try again. No credentials were changed by this request.",
                        operation=operation,
                    ) from exc
                time.sleep(0.05)
            else:
                break
        yield
