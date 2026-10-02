from dataclasses import dataclass, fields
from pathlib import PurePosixPath
import tomllib


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    label: str
    name: str | None = None
    exe_prefix: str | None = None
    exe_contains: str | None = None
    cwd_prefix: str | None = None
    cmdline_contains: str | None = None


@dataclass(frozen=True)
class Label:
    name: str
    kind: str


def parse_rules(text: str) -> list[Rule]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(str(exc)) from exc
    if set(data) - {"rule"} or not isinstance(data.get("rule", []), list):
        raise RulesError("Expected [[rule]] entries")
    result = []
    keys = {f.name for f in fields(Rule)}
    for entry in data.get("rule", []):
        if (not isinstance(entry, dict) or set(entry) - keys
                or not isinstance(entry.get("label"), str) or not entry["label"].strip()
                or len(entry) < 2 or any(not isinstance(v, str) or not v for v in entry.values())):
            raise RulesError(f"Invalid rule: {entry!r}; use a label and at least one known string condition")
        result.append(Rule(**entry))
    return result


def _matches(rule, proc):
    checks = ((rule.name, proc.name, "exact"),
              (rule.exe_prefix, proc.exe, "prefix"),
              (rule.exe_contains, proc.exe, "contains"),
              (rule.cwd_prefix, proc.cwd, "prefix"),
              (rule.cmdline_contains, " ".join(proc.cmdline) or None, "contains"))
    for needle, value, mode in checks:
        if needle is None:
            continue
        if value is None:
            return False
        if mode == "prefix":
            match = value.startswith(needle)
        elif mode == "exact":
            match = value.casefold() == needle.casefold()
        else:
            match = needle.casefold() in value.casefold()
        if not match:
            return False
    return True


def classify(procs, rules, projects_root: str) -> dict[int, Label]:
    table = {p.pid: p for p in procs}
    result = {}
    root = PurePosixPath(projects_root)
    for p in procs:
        matched = next((r for r in rules if _matches(r, p)), None)
        if matched:
            result[p.pid] = Label(matched.label, "rule")
            continue
        for candidate in (p.cwd, *p.cmdline):
            if not candidate:
                continue
            try:
                relative = PurePosixPath(candidate).relative_to(root)
            except ValueError:
                continue
            if relative.parts and ".." not in relative.parts:
                result[p.pid] = Label(relative.parts[0], "project")
                break
        if p.pid in result:
            continue
        if p.exe and p.exe.startswith(("/System/", "/usr/", "/bin/", "/sbin/", "/Library/Apple/", "/private/var/")):
            result[p.pid] = Label("macOS system", "system")
        elif p.exe and ".app/" in p.exe:
            result[p.pid] = Label(p.exe.split(".app/", 1)[0].rsplit("/", 1)[-1], "app")
    for p in procs:
        if p.pid in result:
            continue
        seen = {p.pid}
        parent = p.ppid
        while parent in table and parent not in seen:
            seen.add(parent)
            if parent in result:
                label = result[parent]
                if label.kind in {"rule", "project", "app", "inherited"}:
                    result[p.pid] = Label(label.name, "inherited")
                break
            parent = table[parent].ppid
        if p.pid not in result:
            result[p.pid] = Label("Other: " + (PurePosixPath(p.exe).name if p.exe else p.name), "other")
    return result
