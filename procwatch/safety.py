from dataclasses import dataclass

_PROTECTED_EXE_PREFIXES = ("/usr/libexec/", "/System/Library/")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


def check(proc, *, me, self_pid, ancestors_of_self):
    if proc.pid <= 1:
        return Verdict(False, "Protected system pid")
    if proc.pid == self_pid:
        return Verdict(False, "This is procwatch itself")
    if proc.pid in ancestors_of_self:
        return Verdict(False, "This process launched procwatch")
    if proc.username is None or proc.username != me:
        return Verdict(False, "You do not own this process (or its owner is unreadable)")
    if proc.name.casefold() in {"launchd", "kernel_task", "windowserver", "loginwindow", "finder", "dock", "systemuiserver", "coreaudiod"}:
        return Verdict(False, "Protected macOS process")
    if proc.exe and proc.exe.startswith(_PROTECTED_EXE_PREFIXES):
        return Verdict(False, "Protected macOS service")
    return Verdict(True, "")
