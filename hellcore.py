import discord
from discord.ext import commands
from discord import app_commands
import aiohttp, os, platform, time, io, json
import psutil
import matplotlib.pyplot as plt
from datetime import datetime
from discord.ext import tasks

from card import build_card

# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE  = os.getenv("API_BASE", "http://srv125.godlike.club:26045/api/v1/player/")
API_KEY   = os.getenv("API_KEY", "")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
ALL_MODES = ["Overall", "Solo", "Doubles", "4v4", "1v1", "4v4v4v4"]
START_TIME = time.time()
STATUS_CHANNEL_ID = 1493686255844593674
MC_SERVER_ADDR = "mc.hellcore.net"
HISTORY_FILE = "player_history.json"
MAX_HISTORY = 720  # 24 hours (720 * 2 min = 1440 min)
AUTHORIZED_ADMIN_ID = 1152817463189327902
WEBSITE_API_BASE = os.getenv("WEBSITE_API_BASE", "https://hellcore.net/api")
WEBSITE_API_KEY  = os.getenv("WEBSITE_API_KEY", "hellcore_secret_key")
HC_BOT_SECRET    = os.getenv("HC_BOT_SECRET", "hellcore-secret-123")

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
    update_status_embed.start()
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
            title="HELLCORE NETWORK | [1.8-1.21]",
            description="Bedwars • Practice • Survival • Lifesteal",
            color=discord.Color.from_rgb(85, 255, 255) if online else discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        
        status_text = "🟢 **Online**" if online else "🔴 **Offline**"
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Players", value=f"`{players}`/`{max_p}`", inline=True)
        embed.add_field(name="Version", value=f"`{version}`", inline=True)
        
        if not online:
            embed.description = "⚠️ Server is currently unreachable."

        # Graph
        graph_buf = generate_player_graph(player_history)
        file = discord.File(graph_buf, filename="graph.png")
        embed.set_image(url="attachment://graph.png")
        embed.set_footer(text="Updates every 2 minutes")

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
