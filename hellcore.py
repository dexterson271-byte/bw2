import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, asyncio, os, platform, time, io, json, re, posixpath
import psutil
import matplotlib.pyplot as plt
from datetime import datetime
from discord.ext import tasks

from card import build_card
from tickets import post_all_ticket_panels, register_persistent_ticket_views, setup_ticket_system

def _load_local_env(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

_load_local_env()

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE  = os.getenv("API_BASE", "http://srv125.godlike.club:26045/api/v1/player/")
API_KEY   = os.getenv("API_KEY", "")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
ALL_MODES = ["Overall", "Solo", "Doubles", "4v4", "1v1", "4v4v4v4"]
START_TIME = time.time()

# Channel IDs
STATUS_CHANNEL_ID  = int(os.getenv("STATUS_CHANNEL_ID", 1504153522986418246))
RULES_CHANNEL_ID   = int(os.getenv("RULES_CHANNEL_ID", 1504191090905841717))
INFO_CHANNEL_ID    = int(os.getenv("INFO_CHANNEL_ID", 1504153029627219988))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", 1504153378593181726))

MC_SERVER_ADDR = "mc.hellcore.net"
HISTORY_FILE = "player_history.json"
MAX_HISTORY = 720  # 24 hours (720 * 2 min = 1440 min)
AUTHORIZED_ADMIN_ID = int(os.getenv("AUTHORIZED_ADMIN_ID", 1152817463189327902))
WEBSITE_API_BASE = os.getenv("WEBSITE_API_BASE", "https://hellcore.net/api")
WEBSITE_API_KEY  = os.getenv("WEBSITE_API_KEY", "hellcore_secret_key")
HC_BOT_SECRET    = os.getenv("HC_BOT_SECRET", "hellcore-secret-123")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AI_PROVIDER      = os.getenv("AI_PROVIDER", "openrouter").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "z-ai/glm-4.5-air:free")
OPENROUTER_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "OPENROUTER_FALLBACK_MODELS",
        "qwen/qwen3-coder:free,openai/gpt-oss-20b:free,google/gemma-4-26b-a4b-it:free",
    ).split(",")
    if model.strip()
]
OPENROUTER_MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "1200"))
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL       = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_MAX_TOKENS  = int(os.getenv("GROQ_MAX_TOKENS", "900"))
AI_SSH_HOST      = os.getenv("AI_SSH_HOST", "147.93.30.8")
AI_SSH_USER      = os.getenv("AI_SSH_USER", "root")
AI_SSH_PASSWORD  = os.getenv("AI_SSH_PASSWORD", "")
AI_REMOTE_ROOT   = os.getenv("AI_REMOTE_ROOT", "/var/lib/pterodactyl/volumes/22fe458f-52a9-45cf-b21a-4b10990f95a4")
AI_BACKUP_DIR    = os.getenv("AI_BACKUP_DIR", ".hc_ai_backups/latest")
AI_LIVE_CONTEXT  = os.getenv("AI_LIVE_CONTEXT", "true").lower() in ("1", "true", "yes", "on")
AI_LOG_TAIL_BYTES = int(os.getenv("AI_LOG_TAIL_BYTES", "20000"))
AI_LOG_CONTEXT_CHARS = int(os.getenv("AI_LOG_CONTEXT_CHARS", "5000"))
AI_SERVER_PATHS_CONTEXT = os.getenv("AI_SERVER_PATHS_CONTEXT", "").strip()
def _ai_normalize_allowed_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    if prefix in ("*", "*/"):
        return "*"
    return prefix.strip("/") + "/"

AI_ALLOWED_PREFIXES = tuple(
    _ai_normalize_allowed_prefix(p)
    for p in os.getenv("AI_ALLOWED_PREFIXES", "panel/").split(",")
    if p.strip()
)
AI_MAX_PROMPT_CHARS = int(os.getenv("AI_MAX_PROMPT_CHARS", "1600"))
AI_MAX_FILE_CHARS   = int(os.getenv("AI_MAX_FILE_CHARS", "80000"))
AI_MAX_TOTAL_CHARS  = int(os.getenv("AI_MAX_TOTAL_CHARS", "180000"))
AI_SYSTEM_INSTRUCTION = (
    "You are a professional server administrator AI integrated into the Hellcore Discord bot. "
    "You receive live server context when it is relevant, including a server path map, safe snapshots of logs, "
    "and plugin data. "
    "For file edits, you have SSH/SFTP access to read, create, and write/edit server files within the "
    "configured allowed directories. Never claim you cannot access files or logs "
    "when live context is provided. Use only the provided context and output the requested JSON structure."
)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
setup_ticket_system(bot, AUTHORIZED_ADMIN_ID)

# ── API ────────────────────────────────────────────────────────────────────────
async def fetch_player(username: str) -> dict:
    if not API_KEY:
        raise RuntimeError("Missing API_KEY environment variable")

    url = f"{API_BASE}{username}?apikey={API_KEY}"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            return await r.json()

# ── Helpers ────────────────────────────────────────────────────────────────────
def _fmt(n):
    try: return f"{int(n):,}"
    except: return str(n)

def _ratio(a, b): return round(a / b, 2) if b else float(a)

def _fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024

def _fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)

def _usage_bar(percent: float, width: int = 14) -> str:
    filled = round(width * max(0, min(percent, 100)) / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"

def _available_modes(p: dict) -> list:
    groups = (p.get("groupStats") or {}).get("groups") or {}
    return ["Overall"] + [m for m in ALL_MODES[1:] if m in groups]

def _resolve_ov(p: dict, mode: str = "overall") -> dict:
    gs = p.get("groupStats") or {}
    if mode == "overall":
        return gs.get("overall") or {
            "wins": p.get("wins", 0), "losses": p.get("losses", 0),
            "kills": p.get("kills", 0), "finalKills": p.get("finalKills", 0),
            "deaths": p.get("deaths", 0), "finalDeaths": p.get("finalDeaths", 0),
            "bedsBroken": p.get("bedsBroken", 0), "gamesPlayed": p.get("gamesPlayed", 0),
            "winstreak": 0, "highestWinstreak": 0,
            "wlr": p.get("wlr", 0), "fkdr": p.get("fkdr", 0), "kdr": p.get("kdr", 0),
        }
    return (gs.get("groups") or {}).get(mode)

def _build_table(p: dict) -> str:
    gs     = p.get("groupStats") or {}
    groups = gs.get("groups") or {}
    overall = _resolve_ov(p, "overall")

    col = "{:<14} {:>5} {:>5} {:>6} {:>6} {:>6} {:>6} {:>5} {:>5} {:>4}"
    header  = col.format("Mode", "W", "L", "WLR", "FK", "FD", "FKDR", "Beds", "WS", "GP")
    divider = "─" * len(header)

    def row(name, s):
        wlr  = s.get("wlr",  _ratio(s.get("wins", 0), s.get("losses", 1)))
        fkdr = s.get("fkdr", _ratio(s.get("finalKills", 0), s.get("finalDeaths", 1)))
        return col.format(
            name,
            _fmt(s.get("wins", 0)),
            _fmt(s.get("losses", 0)),
            f"{wlr:.2f}",
            _fmt(s.get("finalKills", 0)),
            _fmt(s.get("finalDeaths", 0)),
            f"{fkdr:.2f}",
            _fmt(s.get("bedsBroken", 0)),
            s.get("winstreak", 0),
            _fmt(s.get("gamesPlayed", 0)),
        )

    lines = [header, divider]
    lines.append(row("Overall", overall))
    lines.append(divider)

    for mode in ALL_MODES[1:]:
        if mode in groups:
            lines.append(row(mode, groups[mode]))

    return "```\n" + "\n".join(lines) + "\n```"

# ── UI ─────────────────────────────────────────────────────────────────────────
class ModeSelect(discord.ui.Select):
    def __init__(self, p: dict, current_mode: str, show_table: bool):
        self.p = p
        self.show_table = show_table

        options = [
            discord.SelectOption(
                label=m,
                value=m.lower() if m != "Overall" else "overall",
                default=(m.lower() == current_mode or (m == "Overall" and current_mode == "overall")),
            )
            for m in _available_modes(p)
        ]

        super().__init__(
            placeholder="🎮 Select mode...",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        mode = self.values[0]

        try:
            buf  = await build_card(self.p, mode)
            file = discord.File(buf, filename=f"{self.p['username']}_bw.png")

            view = StatsView(self.p, mode, self.show_table)
            content = _build_table(self.p) if self.show_table else None

            # 🔥 THIS FIXES DM + SERVER
            await interaction.edit_original_response(
                content=content,
                attachments=[file],
                view=view,
            )

        except Exception as e:
            await interaction.followup.send(f"❌ Error\n`{e}`", ephemeral=True)


class StatsView(discord.ui.View):
    def __init__(self, p: dict, current_mode: str, show_table: bool):
        super().__init__(timeout=180)
        self.add_item(ModeSelect(p, current_mode, show_table))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

def _ai_is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id == AUTHORIZED_ADMIN_ID

def _ai_clean_path(path: str) -> str:
    path = (path or "").strip().replace("\\", "/").lstrip("/")
    norm = posixpath.normpath(path)
    if norm in ("", ".") or norm.startswith("../") or norm == ".." or "\x00" in norm:
        raise ValueError(f"Unsafe path: {path}")
    if "*" not in AI_ALLOWED_PREFIXES and not any(norm.startswith(prefix) for prefix in AI_ALLOWED_PREFIXES):
        allowed = ", ".join(AI_ALLOWED_PREFIXES)
        raise ValueError(f"Path `{norm}` is outside allowed prefixes: {allowed}")
    return norm

def _ai_remote_path(path: str) -> str:
    return posixpath.join(AI_REMOTE_ROOT.rstrip("/"), _ai_clean_path(path))

def _ai_extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])

def _ai_response_text(data: dict, provider: str) -> str:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{provider} returned no choices: {str(data)[:500]}")

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        if parts:
            return "\n".join(parts)

    refusal = message.get("refusal")
    if refusal:
        raise RuntimeError(f"{provider} refused: {refusal}")
    reasoning = message.get("reasoning")
    if reasoning:
        raise RuntimeError(f"{provider} returned reasoning but no answer. Try another model.")
    raise RuntimeError(f"{provider} returned empty content: {str(data)[:500]}")

def _ai_paths_from_task(task: str) -> list[str]:
    paths = []
    pattern = r"(?:^|[\s`'\"])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_. -]+\.[A-Za-z0-9_.-]+)(?=$|[\s`'\".,;:!?])"
    for match in re.finditer(pattern, task or ""):
        raw = match.group(1).strip()
        try:
            path = _ai_clean_path(raw)
        except ValueError:
            continue
        if path not in paths:
            paths.append(path)
    return paths[:6]

async def _gemini_json(prompt: str, system_instruction: str = None) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                message = data.get("error", {}).get("message", str(data))
                raise RuntimeError(f"Gemini error {resp.status}: {message}")

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini returned no usable text")
    return _ai_extract_json(text)

async def _groq_json(prompt: str, system_instruction: str = None) -> dict:
    if not GROQ_API_KEY:
        raise RuntimeError("Missing GROQ_API_KEY")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": GROQ_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        for attempt in range(2):
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 429 and attempt == 0:
                    retry_after = resp.headers.get("retry-after")
                    try:
                        wait_seconds = min(float(retry_after or 20), 30)
                    except ValueError:
                        wait_seconds = 20
                    await asyncio.sleep(wait_seconds)
                    continue
                if resp.status >= 400:
                    message = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"Groq error {resp.status}: {message}")
                break

    text = _ai_response_text(data, "Groq")
    return _ai_extract_json(text)

async def _openrouter_json(prompt: str, system_instruction: str = None) -> dict:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://hellcore.net",
        "X-Title": "Hellcore Bot",
    }
    models = []
    for model in [OPENROUTER_MODEL] + OPENROUTER_FALLBACK_MODELS:
        if model not in models:
            models.append(model)

    last_error = None
    async with aiohttp.ClientSession() as session:
        for model in models:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": OPENROUTER_MAX_TOKENS,
                "response_format": {"type": "json_object"},
                "provider": {
                    "sort": "throughput",
                    "allow_fallbacks": True,
                },
            }
            try:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=75),
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status >= 400:
                        message = data.get("error", {}).get("message", str(data))
                        last_error = f"{model}: OpenRouter error {resp.status}: {message}"
                        continue

                text = _ai_response_text(data, f"OpenRouter {model}")
                return _ai_extract_json(text)
            except Exception as e:
                last_error = f"{model}: {e}"
                continue

    raise RuntimeError(last_error or "OpenRouter failed with all models")

async def _ai_json(prompt: str, system_instruction: str = None) -> dict:
    if AI_PROVIDER == "gemini":
        return await _gemini_json(prompt, system_instruction=system_instruction)
    if AI_PROVIDER == "groq":
        return await _groq_json(prompt, system_instruction=system_instruction)
    if AI_PROVIDER == "openrouter":
        return await _openrouter_json(prompt, system_instruction=system_instruction)
    raise RuntimeError(f"Unsupported AI_PROVIDER `{AI_PROVIDER}`")

def _ssh_client():
    if not AI_SSH_PASSWORD:
        raise RuntimeError("Missing AI_SSH_PASSWORD")
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(AI_SSH_HOST, username=AI_SSH_USER, password=AI_SSH_PASSWORD, timeout=15)
    return ssh

def _sftp_read_tail(sftp, path: str, max_bytes: int) -> str:
    with sftp.open(path, "rb") as f:
        try:
            size = f.stat().st_size
            f.seek(max(0, size - max_bytes))
        except IOError:
            pass
        return f.read(max_bytes).decode("utf-8", errors="replace")

def _extract_loaded_plugins(log_text: str) -> list[str]:
    plugins = []
    seen = set()
    patterns = (
        r"\[([^\]]+)\] Enabling ([A-Za-z0-9_.+\-() ]+)",
        r"\[([^\]]+)\] ([A-Za-z0-9_.+\-() ]+) (?:enabled|loaded)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, log_text, re.IGNORECASE):
            name = match.group(2).strip()
            low = name.lower()
            if low and low not in seen and low not in {"plugin", "successfully"}:
                seen.add(low)
                plugins.append(name)
    return plugins

def _ai_server_path_map() -> str:
    if AI_SERVER_PATHS_CONTEXT:
        return AI_SERVER_PATHS_CONTEXT

    root = AI_REMOTE_ROOT.rstrip("/")
    paths = {
        "server_root": root,
        "plugins_folder": posixpath.join(root, "plugins"),
        "latest_log": posixpath.join(root, "logs/latest.log"),
        "logs_folder": posixpath.join(root, "logs"),
        "worlds_folder": root,
        "bedwars_arenas_common": posixpath.join(root, "plugins/BedWars1058"),
        "rankedbedwars_config": posixpath.join(root, "plugins/rankedbedwars"),
        "hynick_config": posixpath.join(root, "plugins/HynickPlugin"),
        "tab_config": posixpath.join(root, "plugins/TAB"),
        "luckperms_config": posixpath.join(root, "plugins/LuckPerms"),
        "ai_backup_folder": posixpath.join(root, AI_BACKUP_DIR.strip("/")),
        "allowed_edit_prefixes": ", ".join(AI_ALLOWED_PREFIXES),
    }
    return "\n".join(f"- {name}: {path}" for name, path in paths.items())

def _ssh_collect_live_context(task: str) -> str:
    if not AI_LIVE_CONTEXT:
        return ""

    try:
        ssh = _ssh_client()
        sftp = ssh.open_sftp()
    except Exception as e:
        return f"LIVE SERVER CONTEXT unavailable: {e}"

    try:
        latest_log = ""
        log_path = posixpath.join(AI_REMOTE_ROOT.rstrip("/"), "logs/latest.log")
        try:
            latest_log = _sftp_read_tail(sftp, log_path, AI_LOG_TAIL_BYTES)
        except IOError:
            latest_log = ""

        loaded_plugins = _extract_loaded_plugins(latest_log)
        if not loaded_plugins:
            plugins_dir = posixpath.join(AI_REMOTE_ROOT.rstrip("/"), "plugins")
            try:
                for item in sftp.listdir_attr(plugins_dir):
                    name = item.filename
                    low = name.lower()
                    if low.endswith(".jar") and not low.endswith(".disabled"):
                        loaded_plugins.append(name[:-4])
            except IOError:
                pass

        task_low = task.lower()
        include_logs = any(
            word in task_low
            for word in ("log", "error", "crash", "rbw", "ranked", "bedwars", "hynick", "plugin", "start")
        )

        lines = ["LIVE SERVER CONTEXT", "Server path map:", _ai_server_path_map()]
        if loaded_plugins:
            lines.append("Loaded plugins / plugin jars:")
            lines.extend(f"- {name}" for name in loaded_plugins[:80])
        else:
            lines.append("Loaded plugins / plugin jars: unavailable")

        if include_logs and latest_log:
            tail = latest_log[-AI_LOG_CONTEXT_CHARS:]
            lines.append("Recent logs/latest.log tail:")
            lines.append(tail)

        return "\n".join(lines)
    finally:
        sftp.close()
        ssh.close()

def _sftp_mkdirs(sftp, path: str):
    parts = [p for p in path.split("/") if p]
    current = ""
    if path.startswith("/"):
        current = "/"
    for part in parts:
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)

def _sftp_rm_tree(sftp, path: str):
    try:
        entries = sftp.listdir_attr(path)
    except IOError:
        return
    for entry in entries:
        child = posixpath.join(path, entry.filename)
        if str(entry.longname).startswith("d"):
            _sftp_rm_tree(sftp, child)
        else:
            sftp.remove(child)

def _ssh_read_files(paths: list[str]) -> dict:
    ssh = _ssh_client()
    sftp = ssh.open_sftp()
    try:
        result = {}
        total = 0
        for raw_path in paths:
            path = _ai_clean_path(raw_path)
            remote = _ai_remote_path(path)
            try:
                with sftp.open(remote, "rb") as f:
                    data = f.read(AI_MAX_FILE_CHARS + 1)
                if len(data) > AI_MAX_FILE_CHARS:
                    raise RuntimeError(f"`{path}` is too large for AI editing")
                text = data.decode("utf-8", errors="replace")
            except FileNotFoundError:
                text = ""
            except IOError:
                text = ""
            total += len(text)
            if total > AI_MAX_TOTAL_CHARS:
                raise RuntimeError("Selected files are too large for one AI edit")
            result[path] = text
        return result
    finally:
        sftp.close()
        ssh.close()

def _ssh_apply_ai_edits(edits: list[dict]):
    ssh = _ssh_client()
    sftp = ssh.open_sftp()
    backup_root = posixpath.join(AI_REMOTE_ROOT.rstrip("/"), AI_BACKUP_DIR.strip("/"))
    try:
        _sftp_mkdirs(sftp, backup_root)
        _sftp_rm_tree(sftp, backup_root)

        for edit in edits:
            path = _ai_clean_path(edit.get("path", ""))
            content = edit.get("content", "")
            if not isinstance(content, str):
                raise RuntimeError(f"`{path}` content must be text")

            remote = _ai_remote_path(path)
            backup = posixpath.join(backup_root, path)
            _sftp_mkdirs(sftp, posixpath.dirname(backup))
            _sftp_mkdirs(sftp, posixpath.dirname(remote))

            try:
                with sftp.open(remote, "rb") as src:
                    old_data = src.read()
                with sftp.open(backup, "wb") as dst:
                    dst.write(old_data)
            except IOError:
                with sftp.open(backup + ".missing", "w") as dst:
                    dst.write("File did not exist before this AI edit.\n")

            with sftp.open(remote, "w") as dst:
                dst.write(content)
    finally:
        sftp.close()
        ssh.close()

def _ai_bullets(items, limit=10) -> str:
    clean = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not clean:
        return "- No summary provided"
    lines = [f"+ {item}" for item in clean[:limit]]
    if len(clean) > limit:
        lines.append(f"+ ...and {len(clean) - limit} more")
    return "\n".join(lines)

async def _ai_plan_files(task: str) -> list[str]:
    mentioned_paths = _ai_paths_from_task(task)
    if mentioned_paths:
        return mentioned_paths

    live_context = await asyncio.to_thread(_ssh_collect_live_context, task)
    prompt = f"""
You are planning a safe file edit for the Hellcore server panel.
Return JSON only with this schema:
{{"files":["relative/path"],"note":"short note"}}

Rules:
- Only suggest files that must be read before editing.
- Paths must be relative paths, not absolute.
- Prefer paths mentioned by the user.
- If the user mentions a relative file path, include it in files.
- If the task is not a file edit, return {{"files":[],"note":"answer only"}}.
- Do not invent more than 6 files.

Allowed path prefixes: {", ".join(AI_ALLOWED_PREFIXES)}
Live context:
{live_context or "No live context collected."}

Task: {task}
"""
    data = await _ai_json(prompt, system_instruction=AI_SYSTEM_INSTRUCTION)
    return [_ai_clean_path(path) for path in data.get("files", [])[:6]]

async def _ai_build_edit(task: str, files: dict) -> dict:
    live_context = await asyncio.to_thread(_ssh_collect_live_context, task)
    file_text = []
    for path, content in files.items():
        file_text.append(f"--- FILE: {path}\n{content}\n--- END FILE")

    prompt = f"""
You are editing Hellcore server panel files.
Return JSON only with this schema:
{{
  "answer": "short message if no edits are needed",
  "summary": ["Added X", "Changed Y"],
  "edits": [
    {{"path": "relative/path", "content": "full new file content"}}
  ]
}}

Rules:
- Only edit the provided files.
- Each edit content must be the full final file content, not a diff.
- Keep existing formatting and unrelated content.
- Do not include secrets.
- If no edit is needed, return edits as [] and put the reply in answer.

Task:
{task}

Live context:
{live_context or "No live context collected."}

Current files:
{chr(10).join(file_text)}
"""
    data = await _ai_json(prompt, system_instruction=AI_SYSTEM_INSTRUCTION)
    edits = []
    allowed = set(files.keys())
    for edit in data.get("edits", []):
        path = _ai_clean_path(edit.get("path", ""))
        if path not in allowed:
            raise RuntimeError(f"AI tried to edit `{path}`, but it was not in the approved read set")
        content = edit.get("content", "")
        if not isinstance(content, str):
            raise RuntimeError(f"AI returned invalid content for `{path}`")
        edits.append({"path": path, "content": content})
    return {
        "answer": str(data.get("answer", "")).strip(),
        "summary": data.get("summary", []),
        "edits": edits,
    }

class AIEditApprovalView(discord.ui.View):
    def __init__(self, owner_id: int, proposal: dict):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.proposal = proposal
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id and interaction.user.id != AUTHORIZED_ADMIN_ID:
            await interaction.response.send_message("Only the requester/admin can approve this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            await interaction.response.send_message("This request was already handled.", ephemeral=True)
            return
        self.done = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await asyncio.to_thread(_ssh_apply_ai_edits, self.proposal["edits"])
            paths = "\n".join(f"- `{edit['path']}`" for edit in self.proposal["edits"])
            await interaction.followup.send(
                f"Approved and applied.\n\nBackup refreshed in `{AI_BACKUP_DIR}`.\n\nEdited:\n{paths}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"Apply failed: `{e}`", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            await interaction.response.send_message("This request was already handled.", ephemeral=True)
            return
        self.done = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="AI edit cancelled.", embed=None, view=self)

# ── Command ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="bedwars", description="Look up a player's BedWars stats")
@app_commands.allowed_contexts(guilds=True, dms=True)
@app_commands.describe(username="Player username", table="Show stats table")
async def bedwars(interaction: discord.Interaction, username: str, table: bool = False):

    await interaction.response.defer(thinking=True)

    try:
        data = await fetch_player(username)
    except Exception as e:
        await interaction.followup.send(f"❌ API Error\n`{e}`")
        return

    if not data.get("success"):
        await interaction.followup.send(f"❌ Player **{username}** not found.")
        return

    p = data["player"]

    try:
        buf  = await build_card(p, "overall")
        file = discord.File(buf, filename=f"{p['username']}_bw.png")

        view = StatsView(p, "overall", table)
        content = _build_table(p) if table else None

        await interaction.followup.send(
            content=content,
            file=file,
            view=view
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Image Error\n`{e}`")

# ── System command ─────────────────────────────────────────────────────────────
@bot.tree.command(name="ai", description="Ask AI to prepare a server panel edit for approval")
@app_commands.allowed_contexts(guilds=True, dms=True)
@app_commands.describe(task="Describe what you want changed. Mention file paths when possible.")
@app_commands.checks.cooldown(1, 30.0, key=lambda i: i.user.id)
async def ai_command(interaction: discord.Interaction, task: str):
    if not _ai_is_admin(interaction):
        await interaction.response.send_message("This command is admin-only.", ephemeral=True)
        return
    if len(task) > AI_MAX_PROMPT_CHARS:
        await interaction.response.send_message(
            f"Task is too long. Keep it under {AI_MAX_PROMPT_CHARS} characters.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    try:
        files = await _ai_plan_files(task)
        if not files:
            live_context = await asyncio.to_thread(_ssh_collect_live_context, task)
            data = await _ai_json(
                "Return JSON only as {\"answer\":\"short answer\"}. "
                f"Answer this Hellcore admin question without editing files.\n\n"
                f"Live context:\n{live_context or 'No live context collected.'}\n\n"
                f"Task:\n{task}",
                system_instruction=AI_SYSTEM_INSTRUCTION
            )
            await interaction.followup.send(data.get("answer", "No answer returned."))
            return

        current_files = await asyncio.to_thread(_ssh_read_files, files)
        proposal = await _ai_build_edit(task, current_files)
        edits = proposal["edits"]
        if not edits:
            await interaction.followup.send(proposal.get("answer") or "AI did not propose any edits.")
            return

        embed = discord.Embed(
            title="AI wants to edit",
            color=discord.Color.from_rgb(85, 255, 255),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Files",
            value="\n".join(f"- `{edit['path']}`" for edit in edits)[:1024],
            inline=False,
        )
        embed.add_field(
            name="Changes",
            value=_ai_bullets(proposal.get("summary"))[:1024],
            inline=False,
        )
        embed.add_field(
            name="Approve?",
            value="Press Approve to refresh the backup folder, then write these files. Press Cancel to do nothing.",
            inline=False,
        )
        view = AIEditApprovalView(interaction.user.id, proposal)
        await interaction.followup.send(embed=embed, view=view)
    except app_commands.CommandOnCooldown:
        raise
    except Exception as e:
        await interaction.followup.send(f"AI failed: `{e}`")

@bot.tree.command(name="uses", description="Show bot server resource usage")
@app_commands.allowed_contexts(guilds=True, dms=True)
async def uses(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    cpu_percent = psutil.cpu_percent(interval=0.4)
    cpu_count = psutil.cpu_count() or 1
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    proc = psutil.Process(os.getpid())
    proc_mem = proc.memory_info().rss

    embed = discord.Embed(
        title="HellCore Resource Usage",
        description="Live container stats for the bot worker.",
        color=discord.Color.from_rgb(85, 255, 255),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="CPU",
        value=f"`{_usage_bar(cpu_percent)}`\n`{cpu_percent:.1f}%` across `{cpu_count}` cores",
        inline=False,
    )
    embed.add_field(
        name="Memory",
        value=(
            f"`{_usage_bar(mem.percent)}`\n"
            f"`{_fmt_bytes(mem.used)}` / `{_fmt_bytes(mem.total)}` (`{mem.percent:.1f}%`)\n"
            f"Bot process: `{_fmt_bytes(proc_mem)}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Storage",
        value=(
            f"`{_usage_bar(disk.percent)}`\n"
            f"`{_fmt_bytes(disk.used)}` / `{_fmt_bytes(disk.total)}` (`{disk.percent:.1f}%`)"
        ),
        inline=False,
    )
    embed.add_field(name="Uptime", value=f"`{_fmt_uptime(time.time() - START_TIME)}`", inline=True)
    embed.add_field(name="Python", value=f"`{platform.python_version()}`", inline=True)
    embed.add_field(name="Platform", value=f"`{platform.system()} {platform.machine()}`", inline=True)
    embed.set_footer(text=f"Requested by {interaction.user}")

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Show all available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 HELLCORE BOT | COMMAND HELP",
        description="Here is a list of all commands you can use with the HellCore Bot.",
        color=discord.Color.from_rgb(85, 255, 255)
    )
    
    embed.add_field(
        name="🎮 BedWars Stats",
        value=(
            "`/bedwars <username> [table]` - Look up a player's stats.\n"
            "`/verify <code>` - Link your Minecraft account."
        ),
        inline=False
    )
    
    embed.add_field(
        name="📊 Server Info",
        value=(
            "`/uses` - Show bot server resource usage.\n"
            "`/userinfo <member>` - Check linked account info (Admin)."
        ),
        inline=False
    )
    
    embed.add_field(
        name="🛠️ Management",
        value=(
            "`/post_game_rules` - Post BedWars rules (Admin).\n"
            "`/post_server_rules` - Post server rules (Admin).\n"
            "`/audit <time>` - Fetch website audit logs (Admin).\n"
            "`/force_sync` - Force a rank sync (Admin).\n"
            "`/unlink <member>` - Unlink a user (Admin)."
        ),
        inline=False
    )
    
    embed.set_footer(text="HellCore Network | Need more help? Ask a staff member.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Startup ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    register_persistent_ticket_views(bot)
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Game(name="mc.hellcore.net"))
    print(f"✅ Logged in as {bot.user}")
    print("✅ Commands synced")
    posted_panels = await post_all_ticket_panels(bot)
    print(f"Ticket panels refreshed: {posted_panels}")
    if not update_status_embed.is_running():
        update_status_embed.start()
    if not sync_ranks_task.is_running():
        sync_ranks_task.start()

# ── Status Task ────────────────────────────────────────────────────────────────
player_history = []
if os.path.exists(HISTORY_FILE):
    try:
        with open(HISTORY_FILE, "r") as f:
            player_history = json.load(f)
    except: pass

def generate_player_graph(history):
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 4))
    
    if not history:
        ax.text(0.5, 0.5, "No data yet", ha='center', va='center', color='gray')
    else:
        # Get last 720 points for the graph (24 hours)
        recent = history[-720:]
        times = [datetime.fromtimestamp(t) for t, c in recent]
        counts = [c for t, c in recent]
        
        ax.plot(times, counts, color='#55FFFF', linewidth=3, marker='o', markersize=5, markerfacecolor='#00AAAA')
        ax.fill_between(times, counts, color='#55FFFF', alpha=0.15)
        
        # Fixed peak at 20 or higher
        current_max = max(counts) if counts else 0
        ax.set_ylim(0, max(20, current_max + 2))

    ax.set_title(f"HELLCORE NETWORK - Player Activity", color='white', pad=15, fontsize=12, fontweight='bold')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle='--', alpha=0.2)
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf

@tasks.loop(minutes=2)
async def update_status_embed():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel: return

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.mcstatus.io/v2/status/java/{MC_SERVER_ADDR}", timeout=10) as r:
                data = await r.json()
        
        online = data.get("online", False)
        players = data.get("players", {}).get("online", 0)
        max_p = data.get("players", {}).get("max", 0)
        version = data.get("version", {}).get("name_clean", "Unknown")
        
        # Update history
        player_history.append((time.time(), players))
        if len(player_history) > MAX_HISTORY:
            player_history.pop(0)
        
        with open(HISTORY_FILE, "w") as f:
            json.dump(player_history, f)

        # Build Embed
        embed = discord.Embed(
            title="💎 HELLCORE NETWORK | SERVER STATUS",
            description=(
                "**Official Ranked Bedwars Server**\n"
                "**IP:** `mc.hellcore.net`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.from_rgb(85, 255, 255) if online else discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        
        status_icon = "🟢" if online else "🔴"
        status_text = "Online" if online else "Offline"
        
        embed.add_field(name="📶 STATUS", value=f"{status_icon} **{status_text}**", inline=True)
        embed.add_field(name="👥 PLAYERS", value=f"❯ `{players}` / `{max_p}`", inline=True)
        embed.add_field(name="🛠️ VERSION", value=f"❯ `1.8 - 1.21`", inline=True)
        
        if not online:
            embed.description = "⚠️ **Server is currently unreachable.**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        # Graph
        graph_buf = generate_player_graph(player_history)
        file = discord.File(graph_buf, filename="graph.png")
        embed.set_image(url="attachment://graph.png")
        embed.set_footer(text="Updates every 2 minutes • mc.hellcore.net")

        # Find existing message to edit or send new
        last_msg = None
        try:
            async for msg in channel.history(limit=20):
                if msg.author == bot.user and msg.embeds and "HELLCORE NETWORK" in msg.embeds[0].title:
                    last_msg = msg
                    break
            
            if last_msg:
                # IMPORTANT: Use edit to avoid resending
                await last_msg.edit(embed=embed, attachments=[file])
            else:
                await channel.send(embed=embed, file=file)
        except Exception as msg_err:
            print(f"❌ Error finding/editing message: {msg_err}")
            await channel.send(embed=embed, file=file)

    except Exception as e:
        print(f"❌ Status Update Error: {e}")

# ── Audit Command ──────────────────────────────────────────────────────────────
@bot.tree.command(name="audit", description="Get website audit logs")
@app_commands.allowed_contexts(guilds=True, dms=True)
@app_commands.describe(time="Timeframe (1day, 1month)")
async def audit(interaction: discord.Interaction, time: str = "1day"):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    if time not in ["1day", "1month"]:
        await interaction.followup.send("❌ Invalid time option. Use `1day` or `1month`.")
        return

    try:
        # We'll fetch from the website API
        # Note: In a real scenario, you'd need an API key for this
        url = f"{WEBSITE_API_BASE}/admin/audit-logs?time={time}"
        headers = {"X-API-Key": WEBSITE_API_KEY}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=10) as r:
                if r.status != 200:
                    await interaction.followup.send(f"❌ Website API returned error `{r.status}`")
                    return
                logs = await r.json()

        if not logs:
            await interaction.followup.send(f"ℹ️ No logs found for the selected timeframe.")
            return

        # Format logs into an embed or file
        # If too many logs, send as a file
        log_text = "ID | Admin | Action | Details | Date\n"
        log_text += "-" * 50 + "\n"
        for log in logs[:20]: # Show last 20 in embed
            admin = log.get("admin_name") or f"ID:{log.get('admin_id')}"
            action = log.get("action", "Unknown")
            details = log.get("details", "")[:30]
            date = log.get("created_at", "")
            log_text += f"{log.get('id')} | {admin} | {action} | {details} | {date}\n"

        if len(logs) > 20:
            full_log = "ID | Admin | Action | Details | Date\n"
            full_log += "=" * 60 + "\n"
            for log in logs:
                admin = log.get("admin_name") or f"ID:{log.get('admin_id')}"
                full_log += f"{log.get('id')} | {admin} | {log.get('action')} | {log.get('details')} | {log.get('created_at')}\n"
            
            buf = io.BytesIO(full_log.encode())
            file = discord.File(buf, filename=f"audit_logs_{time}.txt")
            await interaction.followup.send(content=f"📊 Found `{len(logs)}` logs for `{time}`. Here are the most recent ones:", file=file)
        else:
            await interaction.followup.send(f"📊 Audit Logs for `{time}`:\n```\n{log_text}\n```")

    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching logs: `{e}`")

# ── Admin Commands ─────────────────────────────────────────────────────────────
@bot.tree.command(name="force_sync", description="Force a rank sync for all users (Admin)")
@app_commands.allowed_contexts(guilds=True, dms=True)
async def force_sync(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await sync_ranks_task()
    await interaction.followup.send("✅ Rank sync triggered!", ephemeral=True)

@bot.tree.command(name="unlink", description="Unlink a Discord user from their website account (Admin)")
@app_commands.allowed_contexts(guilds=True, dms=True)
@app_commands.describe(member="The Discord member to unlink")
async def unlink(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        headers = {"X-Bot-Secret": HC_BOT_SECRET}
        # We need an endpoint to unlink or just use a specific bot API
        # For now I'll assume we can use bot_verify with empty/null logic or add a new endpoint
        # Let's add an unlink endpoint to app.py too
        async with aiohttp.ClientSession() as session:
            payload = {"discord_id": str(member.id)}
            async with session.post(f"{WEBSITE_API_BASE}/bot/unlink", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    await interaction.followup.send(f"✅ Successfully unlinked {member.mention}.", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Error unlinking: {resp.status}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="userinfo", description="Check linked account info for a Discord member (Admin)")
@app_commands.allowed_contexts(guilds=True, dms=True)
@app_commands.describe(member="The Discord member to check")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        headers = {"X-Bot-Secret": HC_BOT_SECRET}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WEBSITE_API_BASE}/bot/ranks", headers=headers) as resp:
                users_data = await resp.json()
                
        user_info = next((u for u in users_data if u["discord_id"] == str(member.id)), None)
        
        if not user_info:
            await interaction.followup.send(f"❌ {member.mention} is not linked to any Hellcore account.", ephemeral=True)
            return
            
        embed = discord.Embed(title=f"User Info: {user_info['username']}", color=discord.Color.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Discord", value=member.mention, inline=True)
        embed.add_field(name="Website Username", value=user_info["username"], inline=True)
        
        ranks = user_info.get("ranks", {})
        if ranks:
            rank_text = "\n".join([f"**{gm}**: {r}" for gm, r in ranks.items()])
            embed.add_field(name="Ranks", value=rank_text, inline=False)
        else:
            embed.add_field(name="Ranks", value="None", inline=False)
            
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ── Rank Sync Task ─────────────────────────────────────────────────────────────
@tasks.loop(minutes=5)
async def sync_ranks_task():
    """Sync Minecraft ranks to Discord roles."""
    print("🔄 Syncing ranks...")
    try:
        headers = {"X-Bot-Secret": HC_BOT_SECRET}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WEBSITE_API_BASE}/bot/ranks", headers=headers) as resp:
                if resp.status != 200:
                    print(f"❌ Failed to fetch ranks: {resp.status}")
                    return
                users_data = await resp.json()

        for guild in bot.guilds:
            roles_map = {role.name.lower(): role for role in guild.roles}
            target_ranks = ["vip", "vip+", "mvp", "mvp+", "mvp++", "bronze", "silver", "gold"]
            priority = ["mvp++", "mvp+", "mvp", "vip+", "vip", "gold", "silver", "bronze"]

            for user_info in users_data:
                discord_id = user_info.get("discord_id")
                if not discord_id: continue
                
                member = guild.get_member(int(discord_id))
                if not member: continue

                user_ranks = [r.lower() for r in user_info.get("ranks", {}).values()]
                primary_rank = "default"
                for p in priority:
                    if p in user_ranks:
                        primary_rank = p
                        break
                
                try:
                    roles_to_add = []
                    roles_to_remove = []
                    for r_name in target_ranks:
                        role = roles_map.get(r_name)
                        if not role: continue
                        if r_name == primary_rank:
                            if role not in member.roles: roles_to_add.append(role)
                        else:
                            if role in member.roles: roles_to_remove.append(role)
                    
                    if roles_to_remove: await member.remove_roles(*roles_to_remove)
                    if roles_to_add: await member.add_roles(*roles_to_add)
                except Exception as e:
                    print(f"❌ Failed to update roles for {member.name}: {e}")
    except Exception as e:
        print(f"❌ Rank sync error: {e}")

# ── Welcome Event ──────────────────────────────────────────────────────────────
@bot.event
async def on_member_join(member):
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not channel: return

    embed = discord.Embed(
        title="Welcome to Hellcore rbw",
        description=(
            f"🔥 **Welcome to the pits of hell, {member.mention}!** 🔥\n"
            "Check out <#1504191090905841717> and <#1504153029627219988> to get started."
        ),
        color=discord.Color.from_rgb(85, 255, 255),
        timestamp=discord.utils.utcnow()
    )
    
    embed.add_field(
        name="👤 User Profile",
        value=f"**Name:** {member.name}\n**ID:** `{member.id}`",
        inline=True
    )
    embed.add_field(
        name="📈 Community",
        value=f"**Member Count:** `{member.guild.member_count}`",
        inline=True
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_image(url="https://media.discordapp.net/attachments/1503736831043174411/1504434667556962395/rbw.gif")
    
    footer_text = f"Welcome to Hell, {member.name}! | Enjoy your stay"
    embed.set_footer(text=footer_text, icon_url=member.guild.icon.url if member.guild.icon else None)

    await channel.send(content=member.mention, embed=embed)

# ── Rules Posting Commands ─────────────────────────────────────────────────────
@bot.tree.command(name="post_game_rules", description="Post the BedWars game rules embed")
async def post_game_rules(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)

    banner_url = "https://media.discordapp.net/attachments/1503736831043174411/1504434667556962395/rbw.gif"

    embed = discord.Embed(
        title="⚔️ HELLCORE BEDWARS | OFFICIAL GAME RULES",
        description=(
            "To maintain a fun and fair environment, all players are expected to follow the guidelines below.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(85, 255, 255)
    )
    embed.set_image(url=banner_url)

    embed.add_field(
        name="🔹 ALLOWED (ALL GAME)",
        value=(
            "❯ 🪜 **Ladders**\n"
            "❯ 💠 **Blue Side Island**\n"
            "❯ 🛡️ **Diamond Armor**\n"
            "❯ 🗡️ **Diamond Sword**\n"
            "❯ 🌊 **Water** (At own base only)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 ALLOWED (AFTER EMERALD II 💚)",
        value=(
            "❯ 🧪 **Invis Potion**\n"
            "❯ 🧪 **Jump Potion**\n"
            "❯ 🧪 **Speed Potion**\n"
            "❯ 🥚 **Bridge Eggs**"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 ALLOWED (AFTER BED BREAK 🛏️)",
        value=(
            "❯ 🌊 **Water** (Everywhere)\n"
            "❯ 🟢 **Pearls**\n"
            "❯ 🟨 **Yellow Side Island**\n"
            "❯ 🏒 **Knockback Sticks**"
        ),
        inline=False
    )

    embed.add_field(
        name="🔹 STRICTLY PROHIBITED ❌",
        value=(
            "❯ 🔥 **Fireballing Diamonds**\n"
            "❯ ⬛ **Obsidian**\n"
            "❯ 🏹 **Bows**\n"
            "❯ 📦 **Pop-Up Towers**"
        ),
        inline=False
    )

    embed.set_footer(text="HellCore Network | Play Fair", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    
    await interaction.followup.send("✅ Rules embed posted!", ephemeral=True)
    await interaction.channel.send(embed=embed)

@bot.tree.command(name="post_server_rules", description="Post the general server rules embed")
async def post_server_rules(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_ADMIN_ID:
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)

    embed = discord.Embed(
        title="📜 HELLCORE NETWORK | SERVER RULES",
        description=(
            "Respect our community and follow the rules to avoid sanctions.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(255, 85, 85)
    )

    rules = [
        "**1.** No disrespect or harassing others.",
        "**2.** Avoid any form of disrespectful behavior.",
        "**3.** No spamming in any channel.",
        "**4.** No advertising (except in designated areas).",
        "**5.** Do not send harmful or malicious links.",
        "**6.** Do **NOT** attempt to bribe staff members.",
        "**7.** Do not impersonate other users or staff.",
        "**8.** Avoid leaking private or sensitive information.",
        "**9.** No hacking or using unauthorized clients. (Strong Anti-Cheat in place)"
    ]

    embed.add_field(name="📋 RULES LIST", value="\n".join([f"❯ {r}" for r in rules]), inline=False)
    
    embed.set_footer(text="HellCore Network © 2026 | Safety First", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    
    await interaction.followup.send("✅ Server rules embed posted!", ephemeral=True)
    await interaction.channel.send(embed=embed)

# ── Verify Command ─────────────────────────────────────────────────────────────
@bot.tree.command(name="verify", description="Link your account to the website")
@app_commands.describe(code="6-digit code from /verify on the website")
async def verify(interaction: discord.Interaction, code: str):
    await interaction.response.defer(ephemeral=True)
    try:
        headers = {"X-Bot-Secret": HC_BOT_SECRET}
        payload = {"code": code, "discord_id": str(interaction.user.id)}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{WEBSITE_API_BASE}/bot/verify", json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    await interaction.followup.send(f"✅ Linked to **{data['username']}**!", ephemeral=True)
                    sync_ranks_task.restart()
                else:
                    await interaction.followup.send(f"❌ Error: {data.get('error', 'Unknown')}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Verification error: {e}", ephemeral=True)


if not BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")

bot.run(BOT_TOKEN)
