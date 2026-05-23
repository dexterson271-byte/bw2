import html
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import discord
import aiohttp
from discord import app_commands


# Ticket config
MAIN_GUILD_ID = 1450500068091232380
RBW_GUILD_ID = 1503724604617785436
MAIN_PANEL_CHANNEL_ID = 1452985439118954666
RBW_PANEL_CHANNEL_ID = 1504478248367816755

STAFF_ROLE_NAME = os.getenv("TICKET_STAFF_ROLE_NAME", "Ticket Support")
LOG_CATEGORY_NAME = os.getenv("TICKET_LOG_CATEGORY_NAME", "Ticket Logs")
LOG_CHANNEL_NAME = os.getenv("TICKET_LOG_CHANNEL_NAME", "ticket-logs")
TICKET_DATA_FILE = Path(os.getenv("TICKET_DATA_FILE", "ticket_data.json"))
BANNER_IMAGE_PATH = Path(os.getenv("TICKET_BANNER_PATH", "assets/ticket_banner.png"))
WEBSITE_API_BASE = os.getenv("WEBSITE_API_BASE", "https://hellcore.net/api").rstrip("/")
WEBSITE_API_KEY = os.getenv("WEBSITE_API_KEY", "")
HC_BOT_SECRET = os.getenv("HC_BOT_SECRET", "")
TICKET_TRANSCRIPT_API_URL = os.getenv("TICKET_TRANSCRIPT_API_URL", f"{WEBSITE_API_BASE}/bot/tickets/transcripts")
TICKET_TRANSCRIPT_URL_TEMPLATE = os.getenv("TICKET_TRANSCRIPT_URL_TEMPLATE", "https://hellcore.net/tickets/{ticket_id}")

PANEL_COLOR = discord.Color.from_rgb(245, 170, 42)
TICKET_COLOR = discord.Color.from_rgb(46, 106, 182)
PRIORITIES = ("Low", "Normal", "High", "Urgent")
DEFAULT_REASON = "No reason provided."

PANEL_RULES = (
    "**SUPPORT**\n\n"
    "Open a ticket only if you need help.\n"
    "Choose the correct ticket type.\n"
    "Do not open duplicate tickets.\n"
    "Do not make false reports.\n"
    "Be respectful and wait for staff.\n\n"
    "**Working time:** 8:00 AM to 11:30 PM"
)


@dataclass(frozen=True)
class TicketType:
    key: str
    label: str
    description: str


@dataclass(frozen=True)
class TicketPanelConfig:
    key: str
    guild_id: int
    panel_channel_id: int
    title: str
    category_prefix: str
    ticket_types: tuple[TicketType, ...]


@dataclass
class TicketRecord:
    ticket_channel_id: int
    user_id: int
    guild_id: int
    ticket_type: str
    ticket_type_label: str
    claimed_staff_id: int | None
    created_time: str
    status: str
    priority: str
    welcome_message_id: int | None = None


MAIN_TICKET_TYPES = (
    TicketType("support", "Support", "General support request"),
    TicketType("bug-report", "Bug Report", "Report a bug or glitch"),
    TicketType("store-help", "Store Help", "Store or purchase support"),
    TicketType("player-report", "Player Report", "Report a player"),
    TicketType("staff-application", "Staff Application", "Apply for staff"),
    TicketType("other", "Other", "Something else"),
)

RBW_TICKET_TYPES = (
    TicketType("support", "Support", "General support request"),
    TicketType("player-report", "Player Report", "Report a player"),
    TicketType("punishment-appeal", "Punishment Appeal", "Appeal a punishment"),
    TicketType("staff-application", "Staff Application", "Apply for staff"),
    TicketType("match-issue", "Match Issue", "Report a match issue"),
    TicketType("queue-issue", "Queue Issue", "Report a queue issue"),
    TicketType("other", "Other", "Something else"),
)

PANEL_CONFIGS = {
    "main": TicketPanelConfig(
        key="main",
        guild_id=MAIN_GUILD_ID,
        panel_channel_id=MAIN_PANEL_CHANNEL_ID,
        title="Hellcore Support",
        category_prefix="Tickets - ",
        ticket_types=MAIN_TICKET_TYPES,
    ),
    "rbw": TicketPanelConfig(
        key="rbw",
        guild_id=RBW_GUILD_ID,
        panel_channel_id=RBW_PANEL_CHANNEL_ID,
        title="Hellcore RBW Support",
        category_prefix="RBW Tickets - ",
        ticket_types=RBW_TICKET_TYPES,
    ),
}


class TicketStore:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[int, TicketRecord] = {}
        self.load()

    def load(self):
        if not self.path.exists():
            self.records = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = {int(k): TicketRecord(**v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError, TypeError):
            self.records = {}

    def save(self):
        data = {str(k): asdict(v) for k, v in self.records.items()}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, record: TicketRecord):
        self.records[record.ticket_channel_id] = record
        self.save()

    def get(self, channel_id: int) -> TicketRecord | None:
        return self.records.get(channel_id)

    def remove(self, channel_id: int):
        if channel_id in self.records:
            del self.records[channel_id]
            self.save()

    def find_open(self, guild_id: int, user_id: int, ticket_type: str) -> TicketRecord | None:
        for record in self.records.values():
            if (
                record.guild_id == guild_id
                and record.user_id == user_id
                and record.ticket_type == ticket_type
                and record.status == "Open"
            ):
                return record
        return None


STORE = TicketStore(TICKET_DATA_FILE)


def setup_ticket_system(bot, authorized_admin_id: int):
    register_ticket_commands(bot, authorized_admin_id)
    bot.add_listener(cleanup_deleted_ticket, "on_guild_channel_delete")


def register_persistent_ticket_views(bot):
    if getattr(bot, "_hellcore_ticket_views_registered", False):
        return
    for config in PANEL_CONFIGS.values():
        bot.add_view(TicketPanelView(config))
    bot.add_view(TicketControlsView())
    bot.add_view(CloseConfirmView())
    bot._hellcore_ticket_views_registered = True


def register_ticket_commands(bot, authorized_admin_id: int):
    @bot.tree.command(name="ticket-setup", description="Post a ticket panel")
    @app_commands.describe(panel="Ticket panel to post")
    @app_commands.choices(panel=[
        app_commands.Choice(name="main", value="main"),
        app_commands.Choice(name="rbw", value="rbw"),
    ])
    async def ticket_setup(interaction: discord.Interaction, panel: app_commands.Choice[str]):
        if interaction.user.id != authorized_admin_id:
            await interaction.response.send_message("You cannot use this command.", ephemeral=True)
            return

        config = PANEL_CONFIGS[panel.value]
        guild = bot.get_guild(config.guild_id)
        channel = bot.get_channel(config.panel_channel_id)
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("The configured server or panel channel was not found.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await ensure_staff_role(guild)
        await ensure_log_channel(guild)
        panel_has_banner = await post_ticket_panel(channel, config)
        if not panel_has_banner:
            await interaction.followup.send(
                f"Posted {config.title} panel in {channel.mention}. Banner file is missing, so the panel was sent without an image.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"Posted {config.title} panel in {channel.mention}.", ephemeral=True)

    @bot.tree.command(name="post_ticket_panels", description="Post or refresh all ticket panels")
    async def post_ticket_panels(interaction: discord.Interaction):
        if interaction.user.id != authorized_admin_id:
            await interaction.response.send_message("You cannot use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        posted = await post_all_ticket_panels(bot)
        await interaction.followup.send(f"Refreshed {posted} ticket panel(s).", ephemeral=True)


async def cleanup_deleted_ticket(channel: discord.abc.GuildChannel):
    STORE.remove(channel.id)


async def post_all_ticket_panels(bot) -> int:
    count = 0
    for config in PANEL_CONFIGS.values():
        guild = bot.get_guild(config.guild_id)
        channel = bot.get_channel(config.panel_channel_id)
        if guild and isinstance(channel, discord.TextChannel):
            await ensure_staff_role(guild)
            await ensure_log_channel(guild)
            panel_has_banner = await post_ticket_panel(channel, config)
            if not panel_has_banner:
                print(f"Ticket banner image is missing: {resolve_banner_path()}")
            count += 1
    return count


async def post_ticket_panel(channel: discord.TextChannel, config: TicketPanelConfig) -> bool:
    banner = load_banner_file()
    file = discord.File(banner, filename=f"{config.key}_ticket_panel.png") if banner else None
    embed = build_panel_embed(config, has_banner=file is not None)
    view = TicketPanelView(config)
    existing = await find_existing_panel_message(channel, config)

    if existing:
        await existing.edit(embed=embed, attachments=[file] if file else [], view=view)
    else:
        if file:
            await channel.send(embed=embed, file=file, view=view, allowed_mentions=no_everyone_mentions())
        else:
            await channel.send(embed=embed, view=view, allowed_mentions=no_everyone_mentions())
    return file is not None


async def find_existing_panel_message(channel: discord.TextChannel, config: TicketPanelConfig):
    marker = f"Ticket Panel: {config.key}"
    async for message in channel.history(limit=25):
        if message.author.bot and message.embeds:
            footer = message.embeds[0].footer.text or ""
            if marker in footer:
                return message
    return None


def build_panel_embed(config: TicketPanelConfig, has_banner: bool) -> discord.Embed:
    embed = discord.Embed(
        title=config.title,
        description=PANEL_RULES,
        color=PANEL_COLOR,
    )
    if has_banner:
        embed.set_image(url=f"attachment://{config.key}_ticket_panel.png")
    for ticket_type in config.ticket_types:
        embed.add_field(name=ticket_type.label, value=ticket_type.description, inline=True)
    embed.set_footer(text=f"Ticket Panel: {config.key}")
    return embed


def resolve_banner_path() -> Path:
    path = BANNER_IMAGE_PATH
    if not path.is_absolute():
        return Path(__file__).parent / path
    return path


def load_banner_file() -> io.BytesIO | None:
    path = resolve_banner_path()
    if not path.exists():
        return None
    return io.BytesIO(path.read_bytes())


class TicketPanelView(discord.ui.View):
    def __init__(self, config: TicketPanelConfig):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(config))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, config: TicketPanelConfig):
        self.config = config
        super().__init__(
            placeholder="Select a ticket type",
            min_values=1,
            max_values=1,
            custom_id=f"ticket_select:{config.key}",
            options=[
                discord.SelectOption(label=t.label, value=t.key, description=t.description[:100])
                for t in config.ticket_types
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != self.config.guild_id:
            await interaction.response.send_message("This ticket panel is not for this server.", ephemeral=True)
            return

        ticket_type = get_ticket_type(self.config, self.values[0])
        if ticket_type is None:
            await interaction.response.send_message("That ticket type is not available.", ephemeral=True)
            return

        existing = STORE.find_open(interaction.guild.id, interaction.user.id, ticket_type.key)
        if existing:
            channel = interaction.guild.get_channel(existing.ticket_channel_id)
            if channel:
                await interaction.response.send_message(f"You already have this ticket open: {channel.mention}", ephemeral=True)
                return
            STORE.remove(existing.ticket_channel_id)

        if not can_create_tickets(interaction.guild):
            await interaction.response.send_message("The bot is missing ticket permissions.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = await create_ticket_channel(interaction, self.config, ticket_type)
        except discord.Forbidden:
            await interaction.followup.send("The bot cannot create or manage ticket channels.", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"Could not create ticket: {exc}", ephemeral=True)
            return

        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="ticket_claim", row=0)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        if record.claimed_staff_id:
            await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
            return
        record.claimed_staff_id = interaction.user.id
        STORE.save()
        await refresh_ticket_message(interaction.channel, record)
        await interaction.response.send_message("Ticket claimed.", ephemeral=True)

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="ticket_unclaim", row=0)
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        if not record.claimed_staff_id:
            await interaction.response.send_message("This ticket is not claimed.", ephemeral=True)
            return
        if record.claimed_staff_id != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only the claimed staff member or an admin can unclaim.", ephemeral=True)
            return
        record.claimed_staff_id = None
        STORE.save()
        await refresh_ticket_message(interaction.channel, record)
        await interaction.response.send_message("Ticket unclaimed.", ephemeral=True)

    @discord.ui.button(label="Close Request", style=discord.ButtonStyle.secondary, custom_id="ticket_close_request", row=0)
    async def close_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        await interaction.response.send_message("Close request sent.", ephemeral=True)
        await interaction.channel.send(
            f"<@{record.user_id}> staff requested to close this ticket.",
            view=CloseConfirmView(),
            allowed_mentions=user_only_mentions(),
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_close", row=0)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_modal(CloseReasonModal(record.ticket_channel_id))

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, custom_id="ticket_add_user", row=1)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_modal(UserPermissionModal("add", record.ticket_channel_id))

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.secondary, custom_id="ticket_remove_user", row=1)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_modal(UserPermissionModal("remove", record.ticket_channel_id))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, custom_id="ticket_rename", row=1)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_modal(RenameTicketModal(record.ticket_channel_id))

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.secondary, custom_id="ticket_transcript", row=1)
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        filename, html_text = await build_transcript_html(interaction.channel)
        transcript_url = await publish_ticket_transcript(
            interaction.channel,
            record,
            filename,
            html_text,
            requested_by=interaction.user,
            reason="Manual transcript export.",
        )
        if transcript_url:
            await interaction.followup.send(f"Transcript: {transcript_url}", ephemeral=True)
        else:
            await interaction.followup.send(file=transcript_file(filename, html_text), ephemeral=True)

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.secondary, custom_id="ticket_lock", row=2)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        member = interaction.guild.get_member(record.user_id)
        if member:
            await interaction.channel.set_permissions(member, send_messages=False, view_channel=True, read_message_history=True)
        await interaction.response.send_message("Ticket locked.", ephemeral=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.secondary, custom_id="ticket_unlock", row=2)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        member = interaction.guild.get_member(record.user_id)
        if member:
            await interaction.channel.set_permissions(member, send_messages=True, view_channel=True, read_message_history=True)
        await interaction.response.send_message("Ticket unlocked.", ephemeral=True)

    @discord.ui.button(label="Priority", style=discord.ButtonStyle.secondary, custom_id="ticket_priority", row=2)
    async def priority(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_message("Select a priority.", view=PriorityView(record.ticket_channel_id), ephemeral=True)

    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, custom_id="ticket_transfer", row=2)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = await require_staff_ticket(interaction)
        if record is not None:
            await interaction.response.send_modal(TransferClaimModal(record.ticket_channel_id))


class PriorityView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=60)
        self.add_item(PrioritySelect(channel_id))


class PrioritySelect(discord.ui.Select):
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        super().__init__(
            placeholder="Priority",
            options=[discord.SelectOption(label=p, value=p) for p in PRIORITIES],
        )

    async def callback(self, interaction: discord.Interaction):
        record = await require_staff_ticket(interaction)
        if record is None:
            return
        record.priority = self.values[0]
        STORE.save()
        await refresh_ticket_message(interaction.channel, record)
        await interaction.response.send_message(f"Priority set to {record.priority}.", ephemeral=True)


class CloseConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept Close", style=discord.ButtonStyle.danger, custom_id="ticket_user_accept_close")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = get_record_for_interaction(interaction)
        if record is None:
            await interaction.response.send_message("This is not an open ticket.", ephemeral=True)
            return
        if interaction.user.id != record.user_id:
            await interaction.response.send_message("Only the ticket owner can use this.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_ticket(interaction.channel, record, interaction.user, "Closed by ticket owner.")

    @discord.ui.button(label="Cancel Close", style=discord.ButtonStyle.secondary, custom_id="ticket_user_cancel_close")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = get_record_for_interaction(interaction)
        if record is None or interaction.user.id != record.user_id:
            await interaction.response.send_message("Only the ticket owner can use this.", ephemeral=True)
            return
        await interaction.response.send_message("Close request cancelled.", ephemeral=False)


class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(label="Close reason", required=False, max_length=500)

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        record = STORE.get(self.channel_id)
        if record is None:
            await interaction.response.send_message("Ticket data was not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_ticket(interaction.channel, record, interaction.user, str(self.reason.value).strip() or DEFAULT_REASON)


class UserPermissionModal(discord.ui.Modal):
    user_id = discord.ui.TextInput(label="User ID", required=True, max_length=24)

    def __init__(self, mode: str, channel_id: int):
        super().__init__(title="Add User" if mode == "add" else "Remove User")
        self.mode = mode
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        record = STORE.get(self.channel_id)
        member = await resolve_member(interaction.guild, str(self.user_id.value))
        if record is None or member is None:
            await interaction.response.send_message("User or ticket was not found.", ephemeral=True)
            return
        if self.mode == "add":
            await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
            await interaction.response.send_message(f"Added {member.mention}.", ephemeral=True, allowed_mentions=no_everyone_mentions())
        else:
            await interaction.channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"Removed {member.mention}.", ephemeral=True, allowed_mentions=no_everyone_mentions())


class RenameTicketModal(discord.ui.Modal, title="Rename Ticket"):
    channel_name = discord.ui.TextInput(label="New channel name", required=True, max_length=90)

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        record = STORE.get(self.channel_id)
        if record is None:
            await interaction.response.send_message("Ticket data was not found.", ephemeral=True)
            return
        new_name = sanitize_channel_name(str(self.channel_name.value))
        await interaction.channel.edit(name=new_name, reason=f"Ticket renamed by {interaction.user}")
        await interaction.response.send_message("Ticket renamed.", ephemeral=True)


class TransferClaimModal(discord.ui.Modal, title="Transfer Claim"):
    staff_user_id = discord.ui.TextInput(label="Staff user ID", required=True, max_length=24)

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        record = STORE.get(self.channel_id)
        member = await resolve_member(interaction.guild, str(self.staff_user_id.value))
        if record is None or member is None or not is_ticket_staff(member):
            await interaction.response.send_message("Staff member or ticket was not found.", ephemeral=True)
            return
        record.claimed_staff_id = member.id
        STORE.save()
        await refresh_ticket_message(interaction.channel, record)
        await interaction.response.send_message(f"Claim transferred to {member.mention}.", ephemeral=True, allowed_mentions=no_everyone_mentions())


async def create_ticket_channel(
    interaction: discord.Interaction,
    config: TicketPanelConfig,
    ticket_type: TicketType,
) -> discord.TextChannel:
    guild = interaction.guild
    member = interaction.user
    staff_role = await ensure_staff_role(guild)
    category = await get_or_create_ticket_category(guild, config, ticket_type, staff_role)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            manage_channels=True,
            attach_files=True,
            embed_links=True,
        ),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    channel = await guild.create_text_channel(
        name=build_ticket_channel_name(member, ticket_type),
        category=category,
        overwrites=overwrites,
        topic=f"hellcore_ticket=true owner_id={member.id} type={ticket_type.key}",
        reason=f"Ticket opened by {member}",
    )

    record = TicketRecord(
        ticket_channel_id=channel.id,
        user_id=member.id,
        guild_id=guild.id,
        ticket_type=ticket_type.key,
        ticket_type_label=ticket_type.label,
        claimed_staff_id=None,
        created_time=utc_now_iso(),
        status="Open",
        priority="Normal",
    )
    STORE.add(record)

    message = await channel.send(
        content=f"{staff_role.mention} {member.mention}",
        embed=build_ticket_embed(record, guild),
        view=TicketControlsView(),
        allowed_mentions=discord.AllowedMentions(everyone=False, roles=[staff_role], users=[member]),
    )
    record.welcome_message_id = message.id
    STORE.save()
    return channel


async def get_or_create_ticket_category(
    guild: discord.Guild,
    config: TicketPanelConfig,
    ticket_type: TicketType,
    staff_role: discord.Role,
) -> discord.CategoryChannel:
    name = f"{config.category_prefix}{ticket_type.label}"
    category = discord.utils.get(guild.categories, name=name)
    if category:
        return category
    return await guild.create_category(
        name=name,
        overwrites={
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, manage_channels=True),
        },
        reason="Creating ticket category",
    )


async def ensure_staff_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
    if role:
        return role
    return await guild.create_role(
        name=STAFF_ROLE_NAME,
        mentionable=True,
        permissions=discord.Permissions.none(),
        reason="Creating ticket support role",
    )


async def ensure_log_channel(guild: discord.Guild) -> discord.TextChannel:
    staff_role = await ensure_staff_role(guild)
    category = discord.utils.get(guild.categories, name=LOG_CATEGORY_NAME)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        staff_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
    }
    if category is None:
        category = await guild.create_category(name=LOG_CATEGORY_NAME, overwrites=overwrites, reason="Creating ticket logs")
    channel = discord.utils.get(category.text_channels, name=LOG_CHANNEL_NAME)
    if channel:
        return channel
    return await guild.create_text_channel(name=LOG_CHANNEL_NAME, category=category, overwrites=overwrites, reason="Creating ticket logs")


def build_ticket_embed(record: TicketRecord, guild: discord.Guild) -> discord.Embed:
    claimed = f"<@{record.claimed_staff_id}>" if record.claimed_staff_id else "None"
    owner = f"<@{record.user_id}>"
    embed = discord.Embed(
        title=f"{record.ticket_type_label} Ticket",
        description=(
            f"Welcome {owner}.\n"
            "A staff member will help you soon.\n\n"
            f"**Type:** {record.ticket_type_label}\n"
            f"**Status:** {record.status}\n"
            f"**Claimed by:** {claimed}\n"
            f"**Priority:** {record.priority}\n\n"
            "Please explain your issue clearly.\n"
            "Use the buttons below to manage this ticket."
        ),
        color=TICKET_COLOR,
        timestamp=parse_iso(record.created_time),
    )
    embed.set_footer(text=f"{guild.name} ticket")
    return embed


async def refresh_ticket_message(channel: discord.TextChannel, record: TicketRecord):
    if not record.welcome_message_id:
        return
    try:
        message = await channel.fetch_message(record.welcome_message_id)
        await message.edit(embed=build_ticket_embed(record, channel.guild), view=TicketControlsView(), allowed_mentions=no_everyone_mentions())
    except discord.DiscordException:
        pass


async def close_ticket(channel: discord.TextChannel, record: TicketRecord, closed_by: discord.abc.User, reason: str):
    record.status = "Closed"
    STORE.save()
    filename, html_text = await build_transcript_html(channel)
    transcript_url = await publish_ticket_transcript(
        channel,
        record,
        filename,
        html_text,
        requested_by=closed_by,
        reason=reason,
    )
    transcript = transcript_file(filename, html_text)
    log_file = clone_file(transcript)
    dm_file = clone_file(transcript)
    closed_time = utc_now_iso()
    log_channel = await ensure_log_channel(channel.guild)
    owner = channel.guild.get_member(record.user_id) or await fetch_user(channel.guild, record.user_id)

    embed = discord.Embed(title="Ticket Closed", color=discord.Color.dark_gold(), timestamp=parse_iso(closed_time))
    embed.add_field(name="Ticket owner", value=f"{owner} ({record.user_id})", inline=False)
    embed.add_field(name="Ticket type", value=record.ticket_type_label, inline=True)
    embed.add_field(name="Server name", value=channel.guild.name, inline=True)
    embed.add_field(name="Claimed staff member", value=f"<@{record.claimed_staff_id}>" if record.claimed_staff_id else "None", inline=False)
    embed.add_field(name="Closed by", value=f"{closed_by} ({closed_by.id})", inline=False)
    embed.add_field(name="Created time", value=record.created_time, inline=False)
    embed.add_field(name="Closed time", value=closed_time, inline=False)
    embed.add_field(name="Close reason", value=reason, inline=False)
    embed.add_field(name="Ticket channel name", value=channel.name, inline=False)
    if transcript_url:
        embed.add_field(name="Website transcript", value=transcript_url, inline=False)

    if transcript_url:
        await log_channel.send(embed=embed, allowed_mentions=no_everyone_mentions())
    else:
        await log_channel.send(embed=embed, file=log_file, allowed_mentions=no_everyone_mentions())

    dm_failed = False
    if owner:
        try:
            if transcript_url:
                await owner.send(embed=embed, allowed_mentions=no_everyone_mentions())
            else:
                await owner.send(embed=embed, file=dm_file, allowed_mentions=no_everyone_mentions())
        except discord.DiscordException:
            dm_failed = True

    if dm_failed:
        await channel.send("Could not DM the ticket owner.", allowed_mentions=no_everyone_mentions())
    await channel.send("Ticket closed. This channel will be deleted shortly.", allowed_mentions=no_everyone_mentions())
    STORE.remove(channel.id)
    await channel.delete(reason=f"Ticket closed by {closed_by}: {reason}")


async def build_transcript(channel: discord.TextChannel) -> discord.File:
    filename, html_text = await build_transcript_html(channel)
    return transcript_file(filename, html_text)


async def build_transcript_html(channel: discord.TextChannel) -> tuple[str, str]:
    rows = []
    async for message in channel.history(limit=None, oldest_first=True):
        attachments = ", ".join(a.url for a in message.attachments) or "None"
        content = message.content or ""
        rows.append(
            "<article>"
            f"<h3>{html.escape(str(message.author))} ({message.author.id})</h3>"
            f"<p><strong>Time:</strong> {message.created_at.isoformat()}</p>"
            f"<p><strong>Content:</strong> {html.escape(content)}</p>"
            f"<p><strong>Attachments:</strong> {html.escape(attachments)}</p>"
            "</article>"
        )
    body = "\n".join(rows) or "<p>No messages.</p>"
    html_text = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Ticket Transcript</title>"
        "<style>body{font-family:Arial,sans-serif;background:#111827;color:#e5e7eb}"
        "article{border-bottom:1px solid #374151;padding:10px}p{margin:4px 0}</style>"
        "</head><body>"
        f"<h1>{html.escape(channel.name)}</h1>{body}</body></html>"
    )
    return f"{channel.name}-transcript.html", html_text


def transcript_file(filename: str, html_text: str) -> discord.File:
    data = io.BytesIO(html_text.encode("utf-8"))
    return discord.File(data, filename=filename)


async def publish_ticket_transcript(
    channel: discord.TextChannel,
    record: TicketRecord,
    filename: str,
    html_text: str,
    requested_by: discord.abc.User,
    reason: str,
) -> str | None:
    if not TICKET_TRANSCRIPT_API_URL:
        return None

    ticket_id = str(record.ticket_channel_id)
    payload = {
        "ticket_id": ticket_id,
        "guild_id": str(record.guild_id),
        "guild_name": channel.guild.name,
        "channel_id": str(record.ticket_channel_id),
        "channel_name": channel.name,
        "owner_id": str(record.user_id),
        "ticket_type": record.ticket_type,
        "ticket_type_label": record.ticket_type_label,
        "claimed_staff_id": str(record.claimed_staff_id) if record.claimed_staff_id else None,
        "status": record.status,
        "priority": record.priority,
        "created_time": record.created_time,
        "requested_by_id": str(requested_by.id),
        "requested_by_name": str(requested_by),
        "reason": reason,
        "filename": filename,
        "html": html_text,
    }
    headers = {"Content-Type": "application/json"}
    if WEBSITE_API_KEY:
        headers["X-API-Key"] = WEBSITE_API_KEY
    if HC_BOT_SECRET:
        headers["X-Bot-Secret"] = HC_BOT_SECRET

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(TICKET_TRANSCRIPT_API_URL, json=payload, headers=headers) as response:
                text = await response.text()
                if response.status >= 400:
                    print(f"[tickets] Transcript upload failed: HTTP {response.status} {text[:300]}")
                    return None
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {}
    except Exception as exc:
        print(f"[tickets] Transcript upload failed: {exc}")
        return None

    url = data.get("url") or data.get("transcript_url") or data.get("public_url")
    returned_id = str(data.get("ticket_id") or data.get("id") or ticket_id)
    if not url and TICKET_TRANSCRIPT_URL_TEMPLATE:
        url = TICKET_TRANSCRIPT_URL_TEMPLATE.format(ticket_id=returned_id)
    return url


def clone_file(file: discord.File) -> discord.File:
    file.fp.seek(0)
    return discord.File(io.BytesIO(file.fp.read()), filename=file.filename)


async def require_staff_ticket(interaction: discord.Interaction) -> TicketRecord | None:
    record = get_record_for_interaction(interaction)
    if record is None:
        await interaction.response.send_message("This is not an open ticket.", ephemeral=True)
        return None
    if not is_ticket_staff(interaction.user):
        await interaction.response.send_message("Only ticket staff can use this.", ephemeral=True)
        return None
    return record


def get_record_for_interaction(interaction: discord.Interaction) -> TicketRecord | None:
    if not isinstance(interaction.channel, discord.TextChannel):
        return None
    return STORE.get(interaction.channel.id)


def is_ticket_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    return any(role.name == STAFF_ROLE_NAME for role in member.roles)


def can_create_tickets(guild: discord.Guild) -> bool:
    me = guild.me
    permissions = me.guild_permissions
    return permissions.manage_channels and permissions.send_messages and permissions.embed_links and permissions.attach_files


def get_ticket_type(config: TicketPanelConfig, key: str) -> TicketType | None:
    return next((ticket_type for ticket_type in config.ticket_types if ticket_type.key == key), None)


def build_ticket_channel_name(member: discord.abc.User, ticket_type: TicketType) -> str:
    username = sanitize_channel_name(getattr(member, "name", str(member)))
    return f"{username}-{ticket_type.key}"[:90]


def sanitize_channel_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value).strip("-").lower()
    return cleaned or "ticket"


async def resolve_member(guild: discord.Guild, raw: str) -> discord.Member | None:
    match = re.search(r"\d{15,24}", raw)
    if not match:
        return None
    user_id = int(match.group(0))
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.DiscordException:
        return None


async def fetch_user(guild: discord.Guild, user_id: int):
    try:
        return guild.get_member(user_id) or await guild.fetch_member(user_id)
    except discord.DiscordException:
        return None


def no_everyone_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)


def user_only_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
