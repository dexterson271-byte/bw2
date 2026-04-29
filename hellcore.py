import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, os, platform, time
import psutil

from card import build_card

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE  = os.getenv("API_BASE", "http://srv125.godlike.club:26045/api/v1/player/")
API_KEY   = os.getenv("API_KEY", "")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
ALL_MODES = ["Overall", "Solo", "Doubles", "4v4", "1v1", "4v4v4v4"]
START_TIME = time.time()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

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


# ── Startup ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")
    print("✅ Commands synced")

if not BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")

bot.run(BOT_TOKEN)
