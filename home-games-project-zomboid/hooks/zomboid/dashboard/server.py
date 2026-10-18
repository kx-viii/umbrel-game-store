"""The app's dashboard: server status, players on a live map, mods, settings, console and backups.

Standard library only, so it runs on the stock Python image. It reads the server's files from
the shared Zomboid folder and talks to the server over RCON on the app's private network.
The Umbrel login sits in front of it (app_proxy), so it has no login of its own.
"""
import json
import os
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ZOMBOID = os.environ.get("ZOMBOID_DIR", "/zomboid")
WORKSHOP = os.environ.get("WORKSHOP_DIR", "/workshop") + "/content/108600"
HERE = os.path.dirname(os.path.abspath(__file__))
LISTS = os.environ.get("LISTS_DIR", "/umbrel")
SERVERNAME = os.environ.get("SERVERNAME", "home")
GAME_PORT = os.environ.get("GAME_PORT", "16261")
GAME_VERSION = os.environ.get("GAME_VERSION", "")
RCON_HOST = os.environ.get("RCON_HOST", "server")
RCON_PORT = int(os.environ.get("RCON_PORT", "27015"))
COLLECTION = os.environ.get("COLLECTION", "")

SECRETS = f"{ZOMBOID}/umbrel-secrets.env"
LIVE = f"{ZOMBOID}/Lua/umbrel/live.txt"
CONSOLE = f"{ZOMBOID}/server-console.txt"
INI = f"{ZOMBOID}/Server/{SERVERNAME}.ini"
SANDBOX = f"{ZOMBOID}/Server/{SERVERNAME}_SandboxVars.lua"
SAVE_DIR = f"{ZOMBOID}/Saves/Multiplayer/{SERVERNAME}"
CACHE = f"{ZOMBOID}/.umbrel-dashboard"

# The map picture comes from pzmap.org. The vanilla Build 42 map hasn't changed since 42.20.
TILE_URL = "https://tiles.pzmap.org/42.20.0/base/layer0_files/{level}/{tile}.jpg"
TILE_CACHE_BYTES = 1024 ** 3
LIVE_FRESH_S = 20

# Settings the app writes itself on every start, so edits here would be lost
LOCKED_SETTINGS = {
    "Mods": "Set from mods.txt in the app on every start.",
    "WorkshopItems": "Set from workshop.txt in the app on every start.",
    "Map": "Set from the downloaded map mods on every start.",
    "Password": "The app sets the join password on every start.",
    "RCONPassword": "The dashboard uses this to talk to the server.",
    "RCONPort": "The dashboard uses this to talk to the server.",
    "DefaultPort": "Fixed by the app's published ports.",
    "UDPPort": "Fixed by the app's published ports.",
    "Public": "Set by the app on every start.",
    "PublicName": "Set by the app on every start.",
    "DoLuaChecksum": "Off so the dashboard's live map script can run.",
}
HIDDEN_SETTINGS = {"ResetID", "ServerPlayerID"}
POPULAR_SETTINGS = [
    "PublicDescription", "ServerWelcomeMessage", "MaxPlayers", "PauseEmpty", "Open", "PVP",
    "SafetySystem", "GlobalChat", "PlayerSafehouse", "AdminSafehouse", "SafehouseAllowLoot",
    "SleepAllowed", "SleepNeeded", "SaveWorldEveryMinutes", "BackupsCount", "BackupsOnStart",
    "BackupsPeriod", "MapRemotePlayerVisibility", "AllowCoop", "SpawnItems", "HoursForLootRespawn",
    "MaxItemsForLootRespawn", "NoFire", "AnnounceDeath", "Faction",
]

# Rough town centres, only for "near Muldraugh" on the player list
TOWNS = [
    ("Muldraugh", 10750, 9700), ("West Point", 11900, 6900), ("Riverside", 6400, 5400),
    ("Rosewood", 8150, 11650), ("March Ridge", 10000, 12750), ("Louisville", 12700, 2200),
]

state_lock = threading.Lock()
restart_state = {"at": None, "reason": None}
last_good_live = {}


# ---------------------------------------------------------------- helpers

def read_text(path, limit=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except OSError:
        return None


def secrets():
    out = {}
    for line in (read_text(SECRETS) or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def redact(text):
    for value in secrets().values():
        if len(value) >= 6:
            text = text.replace(value, "••••••")
    return text


def tail(path, lines):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-lines:]


def live():
    """The latest data written by umbrel-live.lua, and whether it is fresh."""
    global last_good_live
    try:
        mtime = os.path.getmtime(LIVE)
        data = json.loads(read_text(LIVE) or "")
        last_good_live = {"data": data, "mtime": mtime}
    except (OSError, ValueError):
        pass  # missing, or caught mid-write: keep the previous read
    if not last_good_live:
        return None, False
    fresh = time.time() - last_good_live["mtime"] < LIVE_FRESH_S
    return last_good_live["data"], fresh


def near_town(x, y):
    best = min(TOWNS, key=lambda t: (t[1] - x) ** 2 + (t[2] - y) ** 2)
    if (best[1] - x) ** 2 + (best[2] - y) ** 2 < 900 ** 2:
        return best[0]
    return None


# ---------------------------------------------------------------- RCON

class RconError(Exception):
    pass


def rcon(command, timeout=6):
    password = secrets().get("RCON_PASSWORD")
    if not password:
        raise RconError("The RCON password hasn't been made yet")

    def send(sock, req_id, kind, body):
        payload = struct.pack("<ii", req_id, kind) + body.encode("utf-8") + b"\0\0"
        sock.sendall(struct.pack("<i", len(payload)) + payload)

    def recv_exact(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RconError("The server closed the connection")
            buf += chunk
        return buf

    def receive(sock):
        (length,) = struct.unpack("<i", recv_exact(sock, 4))
        data = recv_exact(sock, length)
        req_id, kind = struct.unpack("<ii", data[:8])
        return req_id, kind, data[8:-2].decode("utf-8", errors="replace")

    try:
        with socket.create_connection((RCON_HOST, RCON_PORT), timeout=timeout) as sock:
            send(sock, 1, 3, password)
            while True:
                req_id, kind, _ = receive(sock)
                if req_id == -1:
                    raise RconError("RCON password rejected")
                if kind == 2:
                    break
            send(sock, 2, 2, command)
            _, _, body = receive(sock)
            return body
    except (OSError, struct.error) as e:
        raise RconError(f"Can't reach the server: {e}") from e


rcon_cache = {"t": 0, "ok": False}


def rcon_reachable():
    if time.time() - rcon_cache["t"] > 15:
        try:
            rcon("players", timeout=3)
            rcon_cache.update(ok=True)
        except RconError:
            rcon_cache.update(ok=False)
        rcon_cache["t"] = time.time()
    return rcon_cache["ok"]


def say(text):
    text = text.replace('"', "'").replace("\n", " ").strip()[:240]
    return rcon(f'servermsg "{text}"')


def restart_worker(minutes, reason):
    end = time.time() + minutes * 60
    with state_lock:
        restart_state.update(at=end, reason=reason)
    warned = set()
    try:
        while True:
            with state_lock:
                if restart_state["at"] != end:
                    return  # cancelled or replaced
            left = end - time.time()
            if left <= 0:
                break
            for mark in (600, 300, 120, 60, 30, 10):
                if left <= mark + 1 and mark not in warned and mark < minutes * 60 + 2:
                    warned.add(mark)
                    label = f"{mark // 60} minute{'s' if mark >= 120 else ''}" if mark >= 60 else f"{mark} seconds"
                    try:
                        say(f"Server restarting in {label}. The world is saved first.")
                    except RconError:
                        pass
            time.sleep(1)
        try:
            say("Restarting now, back in a couple of minutes.")
        except RconError:
            pass
        rcon("save", timeout=60)
        time.sleep(5)
        # The server saves and exits, then Docker starts it again
        rcon("quit", timeout=10)
    except RconError:
        pass
    finally:
        with state_lock:
            if restart_state["at"] == end:
                restart_state.update(at=None, reason=None)


# ---------------------------------------------------------------- mods

mods_cache = {"t": 0, "data": None}


def mod_folders(item_dir):
    """For each mod in a workshop item: the folder holding the mod.info Build 42 uses, and the
    folders its files can be in (the newest 42.x folder, then common)."""
    found = {}
    mods_dir = os.path.join(item_dir, "mods")
    try:
        folders = os.listdir(mods_dir)
    except OSError:
        return found
    for folder in folders:
        base = os.path.join(mods_dir, folder)
        versions = []
        try:
            for sub in os.listdir(base):
                m = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", sub)
                if m and int(m.group(1)) == 42 and os.path.isdir(os.path.join(base, sub)):
                    versions.append((tuple(int(g or 0) for g in m.groups()), os.path.join(base, sub)))
        except OSError:
            continue
        roots = [p for _, p in sorted(versions, reverse=True)] + [os.path.join(base, "common"), base]
        info = next((r for r in roots if os.path.isfile(os.path.join(r, "mod.info"))), None)
        if info:
            found[folder] = (info, [r for r in roots if os.path.isdir(r)])
    return found


def parse_mod_info(path):
    info = {}
    for line in (read_text(os.path.join(path, "mod.info")) or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip().lower()
            if k in info and k in ("require", "incompatible"):
                info[k] += "," + v.strip()
            else:
                info.setdefault(k, v.strip())
    return info


def list_file(path):
    out = []
    for line in (read_text(path) or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def mods():
    if mods_cache["data"] and time.time() - mods_cache["t"] < 60:
        return mods_cache["data"]
    order = list_file(f"{LISTS}/mods.txt")
    items = list_file(f"{LISTS}/workshop.txt")
    by_id = {}
    downloaded = 0
    for item in items:
        item_dir = os.path.join(WORKSHOP, item)
        if os.path.isdir(item_dir):
            downloaded += 1
        for folder, (path, roots) in mod_folders(item_dir).items():
            info = parse_mod_info(path)
            mod_id = info.get("id", folder).strip().lstrip("\\")
            poster = info.get("poster", "")
            poster_path = None
            for root in roots:
                if poster and os.path.isfile(os.path.join(root, poster)):
                    poster_path = os.path.relpath(os.path.join(root, poster), WORKSHOP).replace(os.sep, "/")
                    break
            java = bool(info.get("javajarfile")) or any(os.path.isdir(os.path.join(r, "media", "java")) for r in roots)
            desc = re.sub(r"<[^>]+>", " ", info.get("description", ""))
            by_id[mod_id] = {
                "id": mod_id,
                "name": info.get("name", mod_id),
                "item": item,
                "description": re.sub(r"\s+", " ", desc).strip()[:400],
                "requires": [r.strip().lstrip("\\") for r in info.get("require", "").split(",") if r.strip()],
                "version": info.get("modversion", ""),
                "poster": poster_path,
                "java": java,
            }
    rows = []
    for i, mod_id in enumerate(order, 1):
        row = dict(by_id.get(mod_id) or {"id": mod_id, "name": mod_id, "item": None, "poster": None})
        row["order"] = i
        row["found"] = mod_id in by_id
        rows.append(row)
    data = {"mods": rows, "items": len(items), "downloaded": downloaded, "collection": COLLECTION}
    mods_cache.update(t=time.time(), data=data)
    return data


# ---------------------------------------------------------------- settings

INI_LINE = re.compile(r"^([A-Za-z0-9_]+)=(.*)$")
LUA_VALUE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*,?\s*)$")
LUA_OPEN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{\s*$")
OPTION = re.compile(r"^(-?\d+(?:\.\d+)?)\s*=\s*(.+)$")


def value_type(raw):
    if raw in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+", raw):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return "float"
    return "string"


def parse_ini():
    rows = []
    comments = []
    for line in (read_text(INI) or "").splitlines():
        if line.startswith("#"):
            comments.append(line.lstrip("# ").strip())
            continue
        m = INI_LINE.match(line)
        if m and m.group(1) not in HIDDEN_SETTINGS:
            key, raw = m.groups()
            locked = LOCKED_SETTINGS.get(key)
            rows.append({
                "key": key,
                "value": "" if key in ("Password", "RCONPassword") else raw,
                "type": value_type(raw),
                "help": " ".join(c for c in comments if c),
                "locked": locked,
                "popular": key in POPULAR_SETTINGS,
            })
        comments = []
    rows.sort(key=lambda r: (not r["popular"], POPULAR_SETTINGS.index(r["key"]) if r["popular"] else 0))
    return rows


def parse_sandbox():
    rows = []
    stack = []
    comments = []
    for line in (read_text(SANDBOX) or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            comments.append(stripped[2:].strip())
            continue
        m = LUA_OPEN.match(line)
        if m:
            stack.append(m.group(1))
            comments = []
            continue
        if stripped.startswith("}"):
            if stack:
                stack.pop()
            comments = []
            continue
        m = LUA_VALUE.match(line)
        if m and stack:
            key, raw = m.group(2), m.group(4)
            if key.upper() == "VERSION" and len(stack) == 1:
                comments = []
                continue
            options = []
            help_lines = []
            for c in comments:
                om = OPTION.match(c)
                if om:
                    options.append({"value": om.group(1), "label": om.group(2).strip()})
                else:
                    help_lines.append(c)
            path = ".".join(stack[1:] + [key])
            rows.append({
                "path": path,
                "key": key,
                "group": stack[1] if len(stack) > 1 else "General",
                "value": raw[1:-1] if raw.startswith('"') else raw,
                "type": value_type(raw) if not raw.startswith('"') else "string",
                "options": options,
                "help": " ".join(h for h in help_lines if h),
            })
        comments = []
    return rows


def write_atomic(path, text):
    tmp = path + ".dashboard-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def check_value(raw_type, value):
    value = str(value)
    if raw_type == "bool" and value not in ("true", "false"):
        raise ValueError("must be true or false")
    if raw_type == "int" and not re.fullmatch(r"-?\d+", value):
        raise ValueError("must be a whole number")
    if raw_type == "float" and not re.fullmatch(r"-?\d+(\.\d+)?", value):
        raise ValueError("must be a number")
    if "\n" in value or "\r" in value:
        raise ValueError("can't contain line breaks")
    return value


def save_settings(server_changes, sandbox_changes):
    errors = {}
    if server_changes:
        lines = (read_text(INI) or "").splitlines()
        for i, line in enumerate(lines):
            m = INI_LINE.match(line)
            if m and m.group(1) in server_changes:
                key, raw = m.groups()
                if key in LOCKED_SETTINGS or key in HIDDEN_SETTINGS:
                    errors[key] = "This one is managed by the app"
                    continue
                try:
                    lines[i] = f"{key}={check_value(value_type(raw), server_changes[key])}"
                except ValueError as e:
                    errors[key] = str(e)
        if not errors:
            write_atomic(INI, "\n".join(lines) + "\n")
    if sandbox_changes and not errors:
        lines = (read_text(SANDBOX) or "").splitlines()
        stack = []
        for i, line in enumerate(lines):
            if line.strip().startswith("--"):
                continue
            m = LUA_OPEN.match(line)
            if m:
                stack.append(m.group(1))
                continue
            if line.strip().startswith("}"):
                if stack:
                    stack.pop()
                continue
            m = LUA_VALUE.match(line)
            if m and stack:
                path = ".".join(stack[1:] + [m.group(2)])
                if path not in sandbox_changes:
                    continue
                raw = m.group(4)
                try:
                    if raw.startswith('"'):
                        value = check_value("string", sandbox_changes[path])
                        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
                    else:
                        value = check_value(value_type(raw), sandbox_changes[path])
                    lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{m.group(5)}"
                except ValueError as e:
                    errors[path] = str(e)
        if not errors:
            write_atomic(SANDBOX, "\n".join(lines) + "\n")
    return errors


def settings_changed_since_start(started_ms):
    """Whether settings were saved here after the server last started."""
    if not started_ms:
        return False
    try:
        return float(read_text(f"{CACHE}/settings-saved") or "0") > started_ms / 1000
    except ValueError:
        return False


# ---------------------------------------------------------------- map tiles

tile_lock = threading.Lock()
tile_writes = {"n": 0}


def tile(level, name):
    path = f"{CACHE}/tiles/{level}/{name}.jpg"
    if os.path.isfile(path):
        os.utime(path)
        with open(path, "rb") as f:
            return f.read()
    req = urllib.request.Request(
        TILE_URL.format(level=level, tile=name),
        headers={"User-Agent": "UmbrelZomboidDashboard/1.0 (self-hosted server map)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return b""  # empty area of the map
        raise
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    with tile_lock:
        tile_writes["n"] += 1
        if tile_writes["n"] % 50 == 0:
            threading.Thread(target=trim_tile_cache, daemon=True).start()
    return data


def trim_tile_cache():
    files = []
    for root, _, names in os.walk(f"{CACHE}/tiles"):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
                files.append((st.st_atime, st.st_size, p))
            except OSError:
                pass
    total = sum(f[1] for f in files)
    for _, size, p in sorted(files):
        if total <= TILE_CACHE_BYTES:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


# ---------------------------------------------------------------- state

def phase_and_status():
    data, fresh = live()
    log_tail = tail(CONSOLE, 400)
    started = any("SERVER STARTED" in line for line in log_tail) or bool(data)
    if fresh:
        phase = "online"
    elif rcon_reachable():
        phase = "online"
    elif os.path.exists(CONSOLE) and not started:
        phase = "starting"
    elif os.path.exists(CONSOLE):
        phase = "offline"
    else:
        phase = "starting"
    return phase, data if fresh else None, log_tail


def rcon_players():
    """Names only, for when the live map script isn't reporting."""
    try:
        out = rcon("players", timeout=3)
    except RconError:
        return []
    return [{"name": l[1:].strip()} for l in out.splitlines() if l.startswith("-") and l[1:].strip()]


def state():
    phase, data, log_tail = phase_and_status()
    s = secrets()
    players = []
    if data:
        for p in data.get("players", []):
            p = dict(p)
            p["near"] = near_town(p.get("x") or 0, p.get("y") or 0)
            players.append(p)
    elif phase == "online":
        players = rcon_players()
    m = mods()
    with state_lock:
        restart_at = restart_state["at"]
    last_line = next((redact(l) for l in reversed(log_tail) if l.strip()), "")
    started_ms = data.get("startedAt") if data else None
    return {
        "phase": phase,
        "name": SERVERNAME,
        "port": GAME_PORT,
        "publicAddress": join_address(),
        "version": GAME_VERSION,
        "startedAt": started_ms,
        "now": int(time.time() * 1000),
        "players": players,
        "gameTime": data.get("gameTime") if data else None,
        "weather": data.get("weather") if data else None,
        "liveMap": bool(data),
        "joinPassword": s.get("JOIN_PASSWORD", ""),
        "adminPassword": s.get("ADMIN_PASSWORD", ""),
        "mods": {"count": len(m["mods"]), "items": m["items"], "downloaded": m["downloaded"],
                 "missing": sum(1 for r in m["mods"] if not r["found"])},
        "restartAt": int(restart_at * 1000) if restart_at else None,
        "needsRestart": phase == "online" and settings_changed_since_start(started_ms),
        "lastLog": last_line[:300],
    }


def join_address():
    """The address friends outside the home use, e.g. a playit.gg tunnel, set on the dashboard."""
    try:
        with open(f"{CACHE}/join.json", encoding="utf-8") as f:
            data = json.load(f)
        return {"host": str(data.get("host", "")), "port": str(data.get("port", ""))}
    except (OSError, ValueError):
        return {"host": "", "port": ""}


def backups():
    out = []
    root = f"{ZOMBOID}/backups"
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"path": os.path.relpath(p, root), "size": st.st_size, "time": int(st.st_mtime * 1000)})
    out.sort(key=lambda b: b["time"], reverse=True)
    return out


def folder_size(path):
    total = 0
    for dirpath, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "ZomboidDashboard"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def error(self, code, message):
        self.send(code, {"error": message})

    def static(self, name, ctype, cache=False):
        path = os.path.join(HERE, name)
        with open(path, "rb") as f:
            body = f.read()
        self.send(200, body, ctype, {"Cache-Control": "max-age=86400" if cache else "no-cache"})

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        p = url.path
        try:
            if p in ("/", "/index.html"):
                return self.static("index.html", "text/html; charset=utf-8")
            if p == "/openseadragon.min.js":
                return self.static("openseadragon.min.js", "text/javascript", cache=True)
            if p == "/api/state":
                return self.send(200, state())
            if p == "/api/mods":
                return self.send(200, mods())
            if p == "/api/poster":
                rel = q.get("path", [""])[0]
                full = os.path.realpath(os.path.join(WORKSHOP, rel))
                if not full.startswith(os.path.realpath(WORKSHOP) + os.sep) or not os.path.isfile(full):
                    return self.error(404, "No picture")
                ctype = "image/png" if full.lower().endswith(".png") else "image/jpeg"
                with open(full, "rb") as f:
                    return self.send(200, f.read(), ctype, {"Cache-Control": "max-age=86400"})
            if p == "/api/settings":
                return self.send(200, {"server": parse_ini(), "sandbox": parse_sandbox(),
                                       "ready": os.path.exists(INI) and os.path.exists(SANDBOX)})
            if p == "/api/log":
                n = min(int(q.get("lines", ["300"])[0]), 2000)
                return self.send(200, {"lines": [redact(l) for l in tail(CONSOLE, n)]})
            if p == "/api/backups":
                return self.send(200, {"backups": backups(), "worldSize": folder_size(SAVE_DIR)})
            if p == "/api/backup":
                rel = q.get("path", [""])[0]
                root = os.path.realpath(f"{ZOMBOID}/backups")
                full = os.path.realpath(os.path.join(root, rel))
                if not full.startswith(root + os.sep) or not os.path.isfile(full):
                    return self.error(404, "No such backup")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(full)))
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(full)}"')
                self.end_headers()
                with open(full, "rb") as f:
                    while chunk := f.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            if p == "/api/world.zip":
                return self.world_zip()
            m = re.fullmatch(r"/tiles/(\d{1,2})/(\d{1,6}_\d{1,6})\.jpg", p)
            if m:
                data = tile(m.group(1), m.group(2))
                if not data:
                    return self.send(404, b"", "image/jpeg", {"Cache-Control": "max-age=86400"})
                return self.send(200, data, "image/jpeg", {"Cache-Control": "max-age=604800"})
            return self.error(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the dashboard up whatever one request does
            try:
                self.error(500, str(e))
            except OSError:
                pass

    def do_POST(self):
        # A header a cross-site form can't send, so other sites can't post here through
        # the browser's Umbrel session
        if self.headers.get("X-Dashboard") != "1":
            return self.error(403, "Missing header")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
        except ValueError:
            return self.error(400, "Bad JSON")
        p = urlparse(self.path).path
        try:
            if p == "/api/settings":
                errors = save_settings(body.get("server") or {}, body.get("sandbox") or {})
                if errors:
                    return self.send(400, {"errors": errors})
                os.makedirs(CACHE, exist_ok=True)
                write_atomic(f"{CACHE}/settings-saved", str(time.time()))
                return self.send(200, {"ok": True})
            if p == "/api/join":
                text = str(body.get("address", "")).strip()
                m = re.fullmatch(r"([A-Za-z0-9.-]{1,253})(?::(\d{1,5}))?", text)
                if text and not m:
                    return self.error(400, "Use host or host:port, like name.ply.gg:12345")
                os.makedirs(CACHE, exist_ok=True)
                host, port = (m.group(1), m.group(2) or GAME_PORT) if m else ("", "")
                write_atomic(f"{CACHE}/join.json", json.dumps({"host": host, "port": port}))
                return self.send(200, {"ok": True})
            if p == "/api/broadcast":
                say(str(body.get("text", "")))
                return self.send(200, {"ok": True})
            if p == "/api/save":
                rcon("save", timeout=60)
                return self.send(200, {"ok": True})
            if p == "/api/restart":
                if body.get("cancel"):
                    with state_lock:
                        restart_state.update(at=None, reason=None)
                    try:
                        say("Restart cancelled.")
                    except RconError:
                        pass
                    return self.send(200, {"ok": True})
                rcon("players", timeout=5)  # fail now, not after the countdown
                minutes = max(0, min(int(body.get("minutes", 0)), 30))
                threading.Thread(target=restart_worker, args=(minutes, body.get("reason")), daemon=True).start()
                return self.send(200, {"ok": True})
            if p == "/api/command":
                command = str(body.get("command", "")).strip()
                if not command or "\n" in command:
                    return self.error(400, "Type a command")
                return self.send(200, {"output": redact(rcon(command.lstrip("/"), timeout=30))})
            return self.error(404, "Not found")
        except RconError as e:
            return self.error(503, str(e))
        except Exception as e:
            return self.error(500, str(e))

    def world_zip(self):
        try:
            rcon("save", timeout=60)
            time.sleep(3)
        except RconError:
            pass  # offline: the files on disk are already complete
        stamp = time.strftime("%Y-%m-%d_%H-%M")
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="zomboid-{SERVERNAME}-{stamp}.zip"')
        self.send_header("Connection", "close")
        self.end_headers()

        class Out:
            def __init__(self, w):
                self.w, self.n = w, 0

            def write(self, b):
                self.w.write(b)
                self.n += len(b)
                return len(b)

            def tell(self):
                return self.n

            def flush(self):
                self.w.flush()

        sources = [SAVE_DIR, f"{ZOMBOID}/db/{SERVERNAME}.db"]
        sources += [f"{ZOMBOID}/Server/{n}" for n in os.listdir(f"{ZOMBOID}/Server") if n.startswith(SERVERNAME)] \
            if os.path.isdir(f"{ZOMBOID}/Server") else []
        with zipfile.ZipFile(Out(self.wfile), "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for src in sources:
                if os.path.isfile(src):
                    z.write(src, os.path.relpath(src, ZOMBOID))
                for dirpath, _, names in os.walk(src) if os.path.isdir(src) else []:
                    for n in names:
                        full = os.path.join(dirpath, n)
                        try:
                            z.write(full, os.path.relpath(full, ZOMBOID))
                        except OSError:
                            pass  # a file the server just replaced


if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
