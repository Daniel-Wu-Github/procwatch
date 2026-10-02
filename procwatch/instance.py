"""A private, process-lifetime dashboard lease. Never unlink a lock file."""
import fcntl
import json
import os
from pathlib import Path


class Instance:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('PROCWATCH_INSTANCE_DIR')
                              or Path.home() / 'Library/Caches/procwatch')
        self.file = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.stat().st_uid != os.getuid():
            raise PermissionError(f'{self.directory} is not owned by you')
        os.chmod(self.directory, 0o700)
        path = self.directory / 'instance.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        self.file = os.fdopen(fd, 'r+')
        return self

    def acquire(self):
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.file.seek(0)
        self.file.truncate()
        self.file.flush()
        return True

    def publish(self, info):
        self.file.seek(0)
        json.dump(info, self.file)
        self.file.truncate()
        self.file.flush()

    def read(self):
        self.file.seek(0)
        try:
            info = json.load(self.file)
            return info if isinstance(info, dict) else None
        except (ValueError, OSError):
            return None

    def __exit__(self, *_):
        self.file.close()
