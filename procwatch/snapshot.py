from collections import defaultdict


def _scale(procs, machine, metric):
    if metric == 'mem':
        total = max(0, machine.mem_total)
        used = total - min(total, max(0, machine.mem_available))
        raw = {p.pid: max(0, p.rss) for p in procs}
    elif metric == 'cpu':
        total = 100
        used = min(total, max(0, machine.cpu_busy_pct))
        raw = {p.pid: max(0, p.cpu) for p in procs}
    else:
        raise ValueError('Unknown metric')
    summed = sum(raw.values())
    return total, used, raw, used / summed if summed else 0


def _shares(slices, total):
    for item in slices:
        item['share'] = item['value'] * 100 / total if total else 0.0
    return slices


def _merge(slices, top_n, process=False):
    slices.sort(key=lambda item: (-item['value'], item['label'], item.get('pid') or 0))
    top_n = max(1, top_n)
    if len(slices) <= top_n:
        return slices
    tail = slices[top_n:]
    other = dict(label='Everything else', kind='other', value=sum(x['value'] for x in tail),
                 count=sum(x['count'] for x in tail))
    if process:
        other.update(pid=None, has_children=False)
    return slices[:top_n] + [other]


def build_snapshot(procs, labels, machine, *, metric, top_n=10):
    if metric == 'gpu':
        note = 'Per-app GPU is unavailable on macOS without administrator rights.'
        busy = machine.gpu_busy_pct
        if busy is None:
            return dict(metric=metric, total=0, used=0, approx=False, note='GPU reading unavailable. ' + note, slices=[], groups={})
        busy = min(100, max(0, busy))
        slices = [dict(label='GPU busy', kind='gpu', value=busy, count=0),
                  dict(label='Idle', kind='free', value=100-busy, count=0)]
        return dict(metric=metric, total=100, used=busy, approx=False, note=note, slices=_shares(slices, 100), groups={})
    total, used, raw, scale = _scale(procs, machine, metric)
    groups = {}
    rank = {'rule': 0, 'project': 1, 'system': 2, 'app': 3, 'inherited': 4, 'other': 5}
    for p in procs:
        label = labels[p.pid]
        group = groups.setdefault(label.name, dict(kind=label.kind, value=0, raw=0, pids=[]))
        if rank.get(label.kind, 9) < rank.get(group['kind'], 9):
            group['kind'] = label.kind
        group['raw'] += raw[p.pid]
        group['pids'].append(p.pid)
    slices = []
    for name, group in groups.items():
        group['value'] = group['raw'] * scale
        if group['value'] > 0:
            slices.append(dict(label=name, kind=group['kind'], value=group['value'], count=len(group['pids'])))
    slices = _merge(slices, top_n)
    if not sum(raw.values()) and used:
        slices.append(dict(label='Unattributed', kind='unattributed', value=used, count=0))
    slices.append(dict(label='Free' if metric == 'mem' else 'Idle', kind='free', value=total-used, count=0))
    note = ('Memory is approximate (shared memory is counted once per process); scaled to machine used memory.'
            if metric == 'mem' else 'Process CPU is approximate, scaled to whole-machine activity.')
    return dict(metric=metric, total=total, used=used, approx=True, note=note, slices=_shares(slices, total), groups=groups)


def breakdown(procs, labels, machine, *, metric, group, parent_pid=None, top_n=10):
    _, _, raw, scale = _scale(procs, machine, metric)
    members = {p.pid: p for p in procs if labels[p.pid].name == group}
    if not members or (parent_pid is not None and parent_pid not in members):
        raise ValueError('Unknown group or parent outside the group')
    parents = {pid: p.ppid if p.ppid in members else None for pid, p in members.items()}
    completed = set()
    for pid in sorted(members):
        trail = set()
        current = pid
        while current is not None and current not in completed:
            if current in trail:
                parents[current] = None
                break
            trail.add(current)
            current = parents[current]
        completed.update(trail)
    children = defaultdict(list)
    for pid, parent in parents.items():
        children[parent].append(pid)
    totals, counts = {}, {}
    stack = [(pid, False) for pid in children[None]]
    while stack:
        pid, finish = stack.pop()
        if finish:
            totals[pid] = raw[pid] * scale + sum(totals[c] for c in children[pid])
            counts[pid] = 1 + sum(counts[c] for c in children[pid])
        else:
            stack.append((pid, True))
            stack.extend((c, False) for c in children[pid])
    slices = [dict(label=members[pid].name, pid=pid, kind='process', value=totals[pid],
                   count=counts[pid], has_children=bool(children[pid])) for pid in children[parent_pid]]
    path = []
    if parent_pid is not None:
        slices.append(dict(label=members[parent_pid].name, pid=parent_pid, kind='self',
                           value=raw[parent_pid]*scale, count=1, has_children=False))
        current = parent_pid
        while current is not None:
            path.append(dict(pid=current, name=members[current].name))
            current = parents[current]
    total = sum(x['value'] for x in slices)
    return dict(metric=metric, group=group, parent_pid=parent_pid, total=total, approx=True,
                path=list(reversed(path)), slices=_shares(_merge(slices, top_n, True), total))


def freed(pids, procs, machine):
    pids = set(pids)
    result = {}
    for metric in ('mem', 'cpu'):
        _, _, raw, scale = _scale(procs, machine, metric)
        value = sum(raw.get(pid, 0) for pid in pids) * scale
        result[metric] = int(value) if metric == 'mem' else float(value)
    return result


def origin(proc, procs):
    table = {p.pid: p for p in procs}
    seen, parents = {proc.pid}, []
    pid = proc.ppid
    while pid in table and pid not in seen:
        seen.add(pid)
        parent = table[pid]
        parents.append(dict(pid=pid, name=parent.name))
        if pid <= 1:
            break
        pid = parent.ppid
    return dict(exe=proc.exe, cwd=proc.cwd, cmdline=' '.join(proc.cmdline) or None, parents=parents)
