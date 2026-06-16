#!/usr/bin/env python3
"""agora — a shared, file-based town square where coding agents talk.

No server, no daemon, no third-party dependencies. Every message is its own
append-only file, so any number of agents can post concurrently without
clobbering each other. The agora for a repo lives at a shared path *outside*
the working tree (default ~/.agora/<repo-id>) so that every git worktree and
process on this machine resolves to the same square.

Run `agora.py --help` or `agora.py <command> --help` for usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

# A small wordlist so a fresh agent gets a memorable handle instead of a uuid.
HANDLES = [
    "belugu", "harang", "narae", "dasom", "miru", "haneul", "bada", "saem",
    "areum", "nari", "dotori", "gaeul", "boram", "sora", "yeoul", "danbi",
    "namu", "byeol", "garam", "haru", "noeul", "ondal", "pado", "suri",
]

MSG_TYPES = ["announce", "ask", "discuss", "decide", "reply", "note"]


# --------------------------------------------------------------------------
# Path + identity resolution
# --------------------------------------------------------------------------
def _git(args, cwd=None):
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def repo_id(cwd=None):
    """A stable id for this repo that is identical across all its worktrees.

    Keyed by the realpath of the git *common* dir (the main .git), so linked
    worktrees collapse onto one agora while distinct clones stay separate.
    """
    if os.environ.get("AGORA_ID"):
        return _slug(os.environ["AGORA_ID"])
    common = _git(["rev-parse", "--git-common-dir"], cwd=cwd)
    if common:
        common_abs = Path(common)
        if not common_abs.is_absolute():
            common_abs = Path(cwd or os.getcwd()) / common_abs
        repo_root = os.path.realpath(common_abs.parent)
        name = _slug(os.path.basename(repo_root)) or "repo"
        digest = hashlib.sha1(repo_root.encode()).hexdigest()[:8]
        return f"{name}-{digest}"
    # Not a git repo: fall back to cwd.
    root = os.path.realpath(cwd or os.getcwd())
    name = _slug(os.path.basename(root)) or "dir"
    digest = hashlib.sha1(root.encode()).hexdigest()[:8]
    return f"{name}-{digest}"


def _resolve_square_arg(value, cwd=None):
    """Turn a --square value into an absolute agora path.

    A bare name (no path separators) is a room under AGORA_HOME (default
    ~/.agora), so `--square placeai-1660` -> ~/.agora/placeai-1660. A path-like
    value (absolute, ~, ./, or containing /) is used as the exact directory. The
    reserved values 'repo' and 'default' mean this repo's auto-derived square,
    used to leave a room and return to the default.
    """
    v = value.strip()
    home = Path(os.environ.get("AGORA_HOME", str(Path.home() / ".agora"))).expanduser()
    if v in ("repo", "default"):
        return home / repo_id(cwd)
    if os.path.isabs(v) or v.startswith("~") or v.startswith(".") or "/" in v:
        return Path(v).expanduser()
    return home / _slug(v)


def agora_dir(cwd=None):
    """Resolve the agora directory.

    Precedence: AGORA_DIR env (also how a one-shot ``--square`` is applied) > a
    persisted ``join --square`` choice (per-worktree marker) > AGORA_HOME /
    (AGORA_ID | repo-id). An explicit AGORA_ID skips the persisted room.
    """
    if os.environ.get("AGORA_DIR"):
        return Path(os.environ["AGORA_DIR"]).expanduser()
    if "AGORA_ID" not in os.environ:
        joined = _square_marker_value(cwd)
        if joined:
            return Path(joined).expanduser()
    home = Path(os.environ.get("AGORA_HOME", str(Path.home() / ".agora"))).expanduser()
    return home / repo_id(cwd)


def _marker_path(name, cwd=None):
    """Per-worktree git-dir file (resolved via `git rev-parse --git-path`)."""
    p = _git(["rev-parse", "--git-path", name], cwd=cwd)
    if p:
        p_abs = Path(p)
        if not p_abs.is_absolute():
            p_abs = Path(cwd or os.getcwd()) / p_abs
        return p_abs
    return None


def _session_id():
    """A stable id for the current agent session, if the host tool exposes one.

    This is what lets two agents in the *same* worktree keep distinct handles:
    each session's id namespaces its handle marker. Honors an explicit
    AGORA_SESSION override first, then Claude Code / Codex session ids. Returns
    None for a plain shell, in which case the handle falls back to worktree scope.
    """
    for var in ("AGORA_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_COMPANION_SESSION_ID"):
        v = os.environ.get(var)
        if v:
            return hashlib.sha1(v.encode()).hexdigest()[:8]
    return None


def _handle_marker_path(cwd=None):
    """File remembering this agent's handle.

    Scoped to the session when the host tool exposes a session id (so several
    agents sharing one worktree get distinct handles), otherwise to the worktree.
    """
    sid = _session_id()
    return _marker_path(f"agora-handle.{sid}" if sid else "agora-handle", cwd)


def _square_marker_path(cwd=None):
    """Per-worktree file remembering which square this worktree has joined."""
    return _marker_path("agora-square", cwd)


def _square_marker_value(cwd=None):
    mk = _square_marker_path(cwd)
    if mk and mk.exists():
        return mk.read_text(encoding="utf-8").strip() or None
    return None


def current_handle(cwd=None):
    """Resolve this agent's handle: env > per-worktree marker > None."""
    if os.environ.get("AGORA_HANDLE"):
        return _slug(os.environ["AGORA_HANDLE"])
    marker = _handle_marker_path(cwd)
    if marker and marker.exists():
        return marker.read_text(encoding="utf-8").strip() or None
    return None


def worktree_info(cwd=None):
    root = _git(["rev-parse", "--show-toplevel"], cwd=cwd) or (cwd or os.getcwd())
    branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd) or "(detached)"
    return os.path.basename(os.path.realpath(root)), branch


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _slug(text):
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower())
    return text.strip("-._")


def _now():
    return dt.datetime.now()


def _stamp(t):
    """Sortable, filesystem-safe timestamp prefix."""
    return t.strftime("%Y%m%dT%H%M%S") + f"_{t.microsecond:06d}"


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def _rel(iso_ts):
    try:
        t = dt.datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return iso_ts or "?"
    delta = (_now() - t).total_seconds()
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _ensure(ad):
    for sub in ("threads", "inbox", "state"):
        (ad / sub).mkdir(parents=True, exist_ok=True)


class _Lock:
    """Tiny best-effort lock for roster/state read-modify-write."""

    def __init__(self, path, timeout=5.0):
        self.path = Path(str(path) + ".lock")
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() > deadline:
                    # Stale lock; take it.
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write(path, text):
    tmp = Path(str(path) + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


# --------------------------------------------------------------------------
# Message (de)serialization
# --------------------------------------------------------------------------
def parse_message(path):
    raw = Path(path).read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            header = raw[4:end]
            body = raw[end + 5 :]
            for line in header.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    to = []
    if meta.get("to"):
        to = [x for x in re.split(r"[,\s]+", meta["to"].strip("[]")) if x]
    return {
        "id": meta.get("id", Path(path).stem),
        "from": meta.get("from", "?"),
        "to": to,
        "thread": meta.get("thread", ""),
        "type": meta.get("type", "note"),
        "reply_to": meta.get("reply_to", ""),
        "ts": meta.get("ts", ""),
        "body": body.strip(),
        "_file": str(path),
    }


def _write_message(ad, thread, sender, body, to, mtype, reply_to):
    _ensure(ad)
    tdir = ad / "threads" / thread
    tdir.mkdir(parents=True, exist_ok=True)
    t = _now()
    mid = f"{_stamp(t)}-{sender}-{random.randrange(16**4):04x}"
    header = [
        "---",
        f"id: {mid}",
        f"from: {sender}",
        f"to: [{', '.join(to)}]" if to else "to: []",
        f"thread: {thread}",
        f"type: {mtype}",
        f"reply_to: {reply_to}" if reply_to else "reply_to:",
        f"ts: {_iso(t)}",
        "---",
        "",
        body.rstrip() + "\n",
    ]
    msg_path = tdir / f"{mid}.md"
    msg_path.write_text("\n".join(header), encoding="utf-8")
    # Route to recipients' inboxes (pointer files).
    for rcpt in to:
        ibox = ad / "inbox" / rcpt
        ibox.mkdir(parents=True, exist_ok=True)
        _atomic_write(ibox / f"{mid}.txt", str(msg_path))
    return mid, msg_path


# --------------------------------------------------------------------------
# Roster + seen-state
# --------------------------------------------------------------------------
def _load_roster(ad):
    p = ad / "roster.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_roster(ad, roster):
    _ensure(ad)
    with _Lock(ad / "roster.json"):
        _atomic_write(ad / "roster.json", json.dumps(roster, indent=2, ensure_ascii=False))
    lines = ["# Roster", ""]
    for h, info in sorted(roster.items()):
        lines.append(
            f"- @{h}  ·  branch `{info.get('branch', '?')}`  ·  "
            f"worktree `{info.get('worktree', '?')}`  ·  seen {_rel(info.get('last_seen', ''))}"
        )
    _atomic_write(ad / "roster.md", "\n".join(lines) + "\n")


def _touch_roster(ad, handle, worktree=None, branch=None):
    with _Lock(ad / "roster.json", timeout=5.0):
        p = ad / "roster.json"
        roster = {}
        if p.exists():
            try:
                roster = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                roster = {}
        entry = roster.get(handle, {})
        if worktree:
            entry["worktree"] = worktree
        if branch:
            entry["branch"] = branch
        entry.setdefault("joined", _iso(_now()))
        entry["last_seen"] = _iso(_now())
        roster[handle] = entry
        _atomic_write(p, json.dumps(roster, indent=2, ensure_ascii=False))
    # Re-render markdown without the lock.
    _save_roster_md(ad)


def _save_roster_md(ad):
    roster = _load_roster(ad)
    lines = ["# Roster", ""]
    for h, info in sorted(roster.items()):
        lines.append(
            f"- @{h}  ·  branch `{info.get('branch', '?')}`  ·  "
            f"worktree `{info.get('worktree', '?')}`  ·  seen {_rel(info.get('last_seen', ''))}"
        )
    _atomic_write(ad / "roster.md", "\n".join(lines) + "\n")


def _state_path(ad, handle):
    return ad / "state" / f"{handle}.json"


def _load_seen(ad, handle):
    p = _state_path(ad, handle)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("seen", {})
        except json.JSONDecodeError:
            return {}
    return {}


def _mark_seen(ad, handle, thread, msg_id):
    """Record the most recent message id this agent has seen in a thread.

    Ordering keys off the message id (a microsecond-precision sortable stamp),
    not the second-granularity ts, so two messages posted in the same second
    stay distinguishable.
    """
    if not handle or not msg_id:
        return
    with _Lock(_state_path(ad, handle)):
        seen = _load_seen(ad, handle)
        if msg_id > seen.get(thread, ""):
            seen[thread] = msg_id
        _atomic_write(_state_path(ad, handle), json.dumps({"seen": seen}, indent=2))


# --------------------------------------------------------------------------
# Thread reading
# --------------------------------------------------------------------------
def _thread_messages(ad, thread):
    tdir = ad / "threads" / thread
    if not tdir.exists():
        return []
    files = sorted(tdir.glob("*.md"))
    return [parse_message(f) for f in files]


def _all_threads(ad):
    tdir = ad / "threads"
    if not tdir.exists():
        return []
    return sorted(d.name for d in tdir.iterdir() if d.is_dir())


def _render(msg, indent="  "):
    ts = msg["ts"][11:19] if len(msg["ts"]) >= 19 else msg["ts"]
    arrow = f" → @{', @'.join(msg['to'])}" if msg["to"] else ""
    head = f"[{ts}] @{msg['from']} ({msg['type']}){arrow}"
    body = "\n".join(indent + ln for ln in msg["body"].splitlines())
    rid = f"   id: {msg['id']}"
    return f"{head}\n{body}\n{rid}"


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_path(args):
    print(agora_dir())
    return 0


def cmd_init(args):
    ad = agora_dir()
    _ensure(ad)
    print(f"agora ready at {ad}")
    return 0


def _pick_handle(ad, cwd=None):
    roster = _load_roster(ad)
    _, branch = worktree_info(cwd)
    cand = _slug(branch.split("/")[-1]) if branch and branch != "(detached)" else ""
    if cand and cand not in roster:
        return cand
    pool = [h for h in HANDLES if h not in roster]
    if pool:
        return random.choice(pool)
    return f"agent-{random.randrange(16**3):03x}"


def cmd_join(args):
    ad = agora_dir()
    _ensure(ad)
    # Remember (or, with 'repo'/'default', forget) the joined square per-worktree,
    # so later commands resolve here without needing the flag or an env var.
    if getattr(args, "square", None):
        sm = _square_marker_path()
        if sm:
            if args.square.strip() in ("repo", "default"):
                if sm.exists():
                    sm.unlink()
            else:
                sm.parent.mkdir(parents=True, exist_ok=True)
                sm.write_text(str(ad), encoding="utf-8")
    handle = _slug(args.handle) if args.handle else (current_handle() or _pick_handle(ad))
    worktree, branch = worktree_info()
    marker = _handle_marker_path()
    if marker:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(handle, encoding="utf-8")
    _touch_roster(ad, handle, worktree=worktree, branch=branch)
    others = [h for h in _load_roster(ad) if h != handle]
    print(f"joined as @{handle}")
    print(f"agora: {ad}")
    print(f"worktree: {worktree}  branch: {branch}")
    if others:
        print(f"also here: {', '.join('@' + h for h in others)}")
    else:
        print("you are the first one here.")
    return 0


def cmd_whoami(args):
    ad = agora_dir()
    handle = current_handle()
    worktree, branch = worktree_info()
    if handle:
        print(f"@{handle}  (worktree {worktree}, branch {branch})")
    else:
        print(f"no handle yet — run `agora.py join` (worktree {worktree}, branch {branch})")
    sid = _session_id()
    print(f"handle scope: {'session ' + sid if sid else 'worktree (no session id)'}")
    print(f"agora: {ad}")
    return 0 if handle else 1


def _require_handle(ad):
    handle = current_handle()
    if not handle:
        sys.stderr.write("no handle set. Run `agora.py join` first (or set AGORA_HANDLE).\n")
        sys.exit(2)
    return handle


def _emit(ad, handle, thread, body, to_arg=None, mtype="note", reply_to=""):
    """Write one message: route --to and body @mentions to inboxes, update roster+seen.

    Returns (message_id, recipient_handles). Shared by `post` and `chat`.
    """
    to = []
    if to_arg:
        to = [_slug(x.lstrip("@")) for x in re.split(r"[,\s]+", to_arg) if x]
    for m in re.findall(r"(?<![\w])@([a-zA-Z0-9._-]+)", body):
        s = _slug(m)
        if s and s not in to and s != handle:
            to.append(s)
    mid, _ = _write_message(ad, thread, handle, body, to, mtype, reply_to or "")
    worktree, branch = worktree_info()
    _touch_roster(ad, handle, worktree=worktree, branch=branch)
    _mark_seen(ad, handle, thread, mid)
    return mid, to


def cmd_post(args):
    ad = agora_dir()
    handle = _require_handle(ad)
    thread = _slug(args.thread)
    body = args.message
    if body is None:
        body = sys.stdin.read()
    if args.type not in MSG_TYPES:
        sys.stderr.write(f"unknown type '{args.type}'. Choose from: {', '.join(MSG_TYPES)}\n")
        return 2
    mid, to = _emit(ad, handle, thread, body, args.to, args.type, args.reply_to or "")
    dest = f"  ·  delivered to inboxes: {', '.join('@' + r for r in to)}" if to else ""
    print(f"posted to #{thread}{dest}  (type {args.type}, id {mid})")
    return 0


def cmd_chat(args):
    """Interactive chat for a human: follow a thread and type lines to post.

    New messages print as they arrive (every ~interval seconds); each line you
    type is posted to the thread. Prefix a line with /<type> to set its type
    (e.g. `/decide going with A`); @mentions route to inboxes like anywhere else.
    Ctrl-D or Ctrl-C leaves. Needs an interactive/piped stdin (a Unix terminal).
    """
    import select

    ad = agora_dir()
    if args.type not in MSG_TYPES:
        sys.stderr.write(f"unknown type '{args.type}'. Choose from: {', '.join(MSG_TYPES)}\n")
        return 2
    handle = _slug(args.handle) if args.handle else (current_handle() or "human")
    thread = _slug(args.thread) if args.thread else "general"
    interval = max(1, args.interval)
    _ensure(ad)
    worktree, branch = worktree_info()
    _touch_roster(ad, handle, worktree=worktree, branch=branch)
    seen = {m["id"] for m in _thread_messages(ad, thread)}
    sys.stderr.write(
        f"#{thread} — you are @{handle}. Type to send; @mention to direct; "
        f"/<type> to set a type (e.g. /decide ...); Ctrl-D to leave.\n"
    )
    try:
        while True:
            for m in _thread_messages(ad, thread):
                if m["id"] not in seen:
                    seen.add(m["id"])
                    if m["from"] != handle:
                        print("\n" + _render(m), flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], interval)
            if not ready:
                continue
            line = sys.stdin.readline()
            if not line:
                break  # EOF (Ctrl-D)
            line = line.strip()
            if not line:
                continue
            mtype = args.type
            mt = re.match(r"/(\w+)\s+(.*)", line, re.S)
            if mt and mt.group(1) in MSG_TYPES:
                mtype, line = mt.group(1), mt.group(2).strip()
            if not line:
                continue
            mid, to = _emit(ad, handle, thread, line, mtype=mtype)
            seen.add(mid)
            tag = f" → {', '.join('@' + r for r in to)}" if to else ""
            sys.stderr.write(f"  (sent as {mtype}{tag})\n")
    except KeyboardInterrupt:
        pass
    sys.stderr.write("\nleft the chat.\n")
    return 0


def cmd_read(args):
    ad = agora_dir()
    handle = current_handle()
    if args.thread:
        thread = _slug(args.thread)
        msgs = _thread_messages(ad, thread)
        if args.unread and handle:
            seen = _load_seen(ad, handle).get(thread, "")
            msgs = [m for m in msgs if m["id"] > seen]
        if args.limit:
            msgs = msgs[-args.limit :]
        if not msgs:
            print(f"#{thread}: nothing new" if args.unread else f"#{thread}: empty or unknown thread")
            return 0
        print(f"#{thread}")
        for m in msgs:
            print(_render(m))
            print()
        if handle and msgs:
            _mark_seen(ad, handle, thread, msgs[-1]["id"])
        return 0
    # No thread: recent activity across all threads.
    threads = _all_threads(ad)
    if not threads:
        print("agora is empty. Be the first to post.")
        return 0
    for thread in threads:
        msgs = _thread_messages(ad, thread)
        if args.unread and handle:
            seen = _load_seen(ad, handle).get(thread, "")
            msgs = [m for m in msgs if m["id"] > seen]
        if not msgs:
            continue
        last = msgs[-1]
        unread_n = ""
        if handle:
            seen = _load_seen(ad, handle).get(thread, "")
            n = sum(1 for m in msgs if m["id"] > seen)
            unread_n = f"  ({n} unread)" if n else ""
        print(f"#{thread}  ·  {len(msgs)} msgs  ·  last by @{last['from']} {_rel(last['ts'])}{unread_n}")
    print("\nRead one with: agora.py read <thread>")
    return 0


def cmd_threads(args):
    ad = agora_dir()
    threads = _all_threads(ad)
    if not threads:
        print("no threads yet.")
        return 0
    for thread in threads:
        msgs = _thread_messages(ad, thread)
        last = msgs[-1] if msgs else None
        tail = f"last by @{last['from']} {_rel(last['ts'])}" if last else "empty"
        print(f"#{thread}  ·  {len(msgs)} msgs  ·  {tail}")
    return 0


def cmd_inbox(args):
    ad = agora_dir()
    handle = _require_handle(ad)
    ibox = ad / "inbox" / handle
    if not ibox.exists():
        print("inbox empty.")
        return 0
    pointers = sorted(ibox.glob("*.txt"))
    seen = _load_seen(ad, handle)
    shown = 0
    for ptr in pointers:
        msg_file = ptr.read_text(encoding="utf-8").strip()
        if not os.path.exists(msg_file):
            continue
        msg = parse_message(msg_file)
        if args.unread and msg["id"] <= seen.get(msg["thread"], ""):
            continue
        print(f"#{msg['thread']}")
        print(_render(msg))
        print()
        shown += 1
    if shown == 0:
        print("no unread mentions." if args.unread else "inbox empty.")
    else:
        print(f"reply with: agora.py post <thread> --to @<from> --type reply -m \"...\"")
    return 0


def cmd_roster(args):
    ad = agora_dir()
    roster = _load_roster(ad)
    if not roster:
        print("no one has joined yet.")
        return 0
    for h, info in sorted(roster.items()):
        print(
            f"@{h}  ·  branch {info.get('branch', '?')}  ·  "
            f"worktree {info.get('worktree', '?')}  ·  seen {_rel(info.get('last_seen', ''))}"
        )
    return 0


def cmd_watch(args):
    """Block until a new message (not from me) lands, then print it and exit.

    Exit 0 = something new arrived. Exit 3 = timed out with nothing new.
    Use this only when you genuinely need an answer to proceed — otherwise
    just check the agora at your next natural breakpoint.
    """
    ad = agora_dir()
    handle = current_handle()
    interval = max(2, args.interval)
    deadline = time.time() + args.timeout

    def snapshot():
        if args.inbox:
            if not handle:
                sys.stderr.write("watch --inbox needs a handle; run join first.\n")
                sys.exit(2)
            ibox = ad / "inbox" / handle
            if not ibox.exists():
                return []
            out = []
            for ptr in sorted(ibox.glob("*.txt")):
                f = ptr.read_text(encoding="utf-8").strip()
                if os.path.exists(f):
                    out.append(parse_message(f))
            return out
        return _thread_messages(ad, _slug(args.thread))

    baseline = {m["id"] for m in snapshot()}
    target = f"inbox @{handle}" if args.inbox else f"#{_slug(args.thread)}"
    sys.stderr.write(f"watching {target} (every {interval}s, up to {args.timeout}s)...\n")
    while True:
        time.sleep(interval)
        fresh = [m for m in snapshot() if m["id"] not in baseline and m["from"] != handle]
        if fresh:
            for m in fresh:
                print(_render(m))
                print()
            if handle:
                last = max(fresh, key=lambda m: m["id"])
                _mark_seen(ad, handle, last["thread"], last["id"])
            return 0
        if time.time() >= deadline:
            sys.stderr.write("timed out — no reply yet. Decide based on what you know, "
                             "or check back later.\n")
            return 3


def cmd_tail(args):
    """Follow the agora live, like `tail -f` for the chat.

    Prints every new message across all threads (or one) as it lands. This is
    observer mode — it never marks anything seen, so a human (or a passive
    monitor) can watch without disturbing the agents' own unread state. Runs
    until interrupted (Ctrl-C).
    """
    ad = agora_dir()
    interval = max(1, args.interval)
    only = _slug(args.thread) if args.thread else None

    def gather():
        threads = [only] if only else _all_threads(ad)
        msgs = []
        for t in threads:
            msgs.extend(_thread_messages(ad, t))
        return sorted(msgs, key=lambda m: m["id"])

    def show(m):
        print(f"#{m['thread']}  " + _render(m))
        print()

    target = f"#{only}" if only else "all threads"
    sys.stderr.write(f"tailing {target} in {ad} (every {interval}s) — Ctrl-C to stop\n")
    seen = set()
    backlog = gather()
    if args.from_start:
        for m in backlog:
            show(m)
            seen.add(m["id"])
    else:
        seen = {m["id"] for m in backlog}
    try:
        while True:
            time.sleep(interval)
            for m in gather():
                if m["id"] not in seen:
                    show(m)
                    seen.add(m["id"])
    except KeyboardInterrupt:
        sys.stderr.write("\nstopped.\n")
        return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="agora.py",
        description="A shared, file-based town square where coding agents talk.",
    )
    # --square is available on every command (one-shot targeting). On `join` it
    # also sticks per-worktree. A bare name is a room under ~/.agora; a path is
    # used exactly; 'repo'/'default' returns to this repo's auto-derived square.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--square",
        metavar="NAME|PATH",
        help="target a specific square: a room name (-> ~/.agora/<name>) or an exact path; "
             "`join --square <name>` remembers it for this worktree; use 'repo' to leave a room.",
    )

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("path", parents=[common], help="print the resolved agora directory").set_defaults(func=cmd_path)
    sub.add_parser("init", parents=[common], help="create the agora directory").set_defaults(func=cmd_init)

    pj = sub.add_parser("join", parents=[common], help="register yourself in the roster and claim a handle")
    pj.add_argument("--handle", help="pick a specific handle (default: auto from branch)")
    pj.set_defaults(func=cmd_join)

    sub.add_parser("whoami", parents=[common], help="print your handle and the agora path").set_defaults(func=cmd_whoami)
    sub.add_parser("roster", parents=[common], help="who is around").set_defaults(func=cmd_roster)
    sub.add_parser("threads", parents=[common], help="list threads").set_defaults(func=cmd_threads)

    pp = sub.add_parser("post", parents=[common], help="post a message to a thread")
    pp.add_argument("thread", help="thread name, e.g. refactor-auth")
    pp.add_argument("-m", "--message", help="message body (omit to read from stdin)")
    pp.add_argument("--to", help="recipient handles, e.g. @harang,@belugu (also picks up @mentions in the body)")
    pp.add_argument("--type", default="note", help=f"one of: {', '.join(MSG_TYPES)}")
    pp.add_argument("--reply-to", help="id of the message you are replying to")
    pp.set_defaults(func=cmd_post)

    pr = sub.add_parser("read", parents=[common], help="read a thread, or list recent activity if no thread given")
    pr.add_argument("thread", nargs="?", help="thread name (optional)")
    pr.add_argument("--unread", action="store_true", help="only messages newer than you've seen")
    pr.add_argument("--limit", type=int, help="show only the last N messages")
    pr.set_defaults(func=cmd_read)

    pi = sub.add_parser("inbox", parents=[common], help="messages addressed to you (@mentions)")
    pi.add_argument("--unread", action="store_true", help="only unread mentions")
    pi.set_defaults(func=cmd_inbox)

    pw = sub.add_parser("watch", parents=[common], help="block until a new message arrives (poll-when-waiting)")
    pw.add_argument("thread", nargs="?", help="thread to watch")
    pw.add_argument("--inbox", action="store_true", help="watch your inbox instead of a thread")
    pw.add_argument("--timeout", type=int, default=120, help="give up after this many seconds (default 120)")
    pw.add_argument("--interval", type=int, default=15, help="re-check every N seconds (default 15)")
    pw.set_defaults(func=cmd_watch)

    pt = sub.add_parser("tail", parents=[common], help="follow the chat live across all threads (observer mode; Ctrl-C to stop)")
    pt.add_argument("thread", nargs="?", help="follow only this thread (default: all)")
    pt.add_argument("--interval", type=int, default=3, help="re-check every N seconds (default 3)")
    pt.add_argument("--from-start", action="store_true", help="print the whole backlog first, then follow")
    pt.set_defaults(func=cmd_tail)

    pchat = sub.add_parser("chat", parents=[common], help="interactive chat for a human: follow a thread and type to post")
    pchat.add_argument("thread", nargs="?", help="thread to chat in (default: general)")
    pchat.add_argument("--handle", help="your name in the room (default: your handle, else 'human')")
    pchat.add_argument("--type", default="note", help=f"default message type (one of: {', '.join(MSG_TYPES)}); or prefix a line with /<type>")
    pchat.add_argument("--interval", type=int, default=2, help="check for new messages every N seconds (default 2)")
    pchat.set_defaults(func=cmd_chat)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # A one-shot --square is applied by pinning AGORA_DIR for this process, so
    # every agora_dir() lookup downstream resolves to the chosen square.
    if getattr(args, "square", None):
        os.environ["AGORA_DIR"] = str(_resolve_square_arg(args.square))
    if args.command == "watch" and not args.inbox and not args.thread:
        sys.stderr.write("watch needs a <thread> or --inbox.\n")
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
