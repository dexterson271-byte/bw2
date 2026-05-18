import io
import os
import re
import asyncio
from dataclasses import dataclass

import discord
from PIL import Image, ImageDraw, ImageFont


MAIN_GUILD_ID = 1450500068091232380
RBW_GUILD_ID = 1503724604617785436

MAIN_PANEL_CHANNEL_ID = 1452985439118954666
RBW_PANEL_CHANNEL_ID = 1504478248367816755

PANEL_COLOR = discord.Color.from_rgb(35, 110, 180)
TICKET_COLOR = discord.Color.from_rgb(47, 128, 237)


TICKET_RULES = (
    "**Ticket System Rules**\n"
    "Do not open multiple tickets for the same issue.\n"
    "Use the correct ticket category.\n"
    "Do not make false reports or troll.\n"
    "Do not share private personal information.\n\n"
    "**Staff Interaction**\n"
    "Do not ping or DM staff about your ticket.\n"
    "Wait patiently for a staff response.\n"
    "Be respectful and do not use bad language.\n\n"
    "**Support Availability**\n"
    "Working time: 8:00 AM to 11:30 PM.\n"
    "Replies are usually faster on Saturday and Sunday.\n"
    "Staff may not reply instantly, so please be patient."
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
    subtitle: str
    category_prefix: str
    ticket_types: tuple[TicketType, ...]


MAIN_TICKET_TYPES = (
    TicketType("support", "Support", "Get help with a general issue"),
    TicketType("bug-report", "Bug Report", "Report a bug or glitch"),
    TicketType("store-purchase-help", "Store / Purchase Help", "Get help with store or payment issues"),
    TicketType("player-report", "Player Report", "Report a player for rule breaking"),
    TicketType("staff-application", "Staff Application", "Apply for HellCore Network staff"),
    TicketType("other", "Other", "Open a ticket for another issue"),
)

RBW_TICKET_TYPES = (
    TicketType("support", "Support", "Get help with a general issue"),
    TicketType("player-report", "Player Report", "Report a player for rule breaking"),
    TicketType("punishment-appeal", "Punishment Appeal", "Appeal a punishment"),
    TicketType("staff-application", "Staff Application", "Apply for HellCore RBW staff"),
    TicketType("match-queue-issue", "Match / Queue Issue", "Report a match or queue issue"),
    TicketType("other", "Other", "Open a ticket for another issue"),
)

PANEL_CONFIGS = {
    "main": TicketPanelConfig(
        key="main",
        guild_id=MAIN_GUILD_ID,
        panel_channel_id=MAIN_PANEL_CHANNEL_ID,
        title="HellCore Support",
        subtitle="Choose the ticket category that best matches your request.",
        category_prefix="Tickets - ",
        ticket_types=MAIN_TICKET_TYPES,
    ),
    "rbw": TicketPanelConfig(
        key="rbw",
        guild_id=RBW_GUILD_ID,
        panel_channel_id=RBW_PANEL_CHANNEL_ID,
        title="HellCore RBW Support",
        subtitle="Choose the ticket category that best matches your request.",
        category_prefix="RBW Tickets - ",
        ticket_types=RBW_TICKET_TYPES,
    ),
}


def setup_ticket_system(bot, authorized_admin_id: int):
    register_ticket_commands(bot, authorized_admin_id)


def register_persistent_ticket_views(bot):
    if getattr(bot, "_hellcore_ticket_views_registered", False):
        return

    for config in PANEL_CONFIGS.values():
        bot.add_view(TicketPanelView(config))
    bot.add_view(CloseTicketView())
    bot._hellcore_ticket_views_registered = True


def register_ticket_commands(bot, authorized_admin_id: int):
    @bot.tree.command(name="post_ticket_panels", description="Post or refresh the HellCore ticket panels")
    async def post_ticket_panels(interaction: discord.Interaction):
        if interaction.user.id != authorized_admin_id:
            await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        posted = await post_all_ticket_panels(bot)
        await interaction.followup.send(f"Refreshed {posted} ticket panel(s).", ephemeral=True)


async def post_all_ticket_panels(bot) -> int:
    count = 0
    for config in PANEL_CONFIGS.values():
        guild = bot.get_guild(config.guild_id)
        channel = bot.get_channel(config.panel_channel_id)
        if guild and isinstance(channel, discord.TextChannel):
            await post_ticket_panel(channel, config)
            count += 1
    return count


async def post_ticket_panel(channel: discord.TextChannel, config: TicketPanelConfig):
    banner = build_ticket_banner(config.title, config.subtitle)
    file = discord.File(banner, filename=f"{config.key}_ticket_panel.png")
    embed = build_panel_embed(config)
    view = TicketPanelView(config)

    existing = await find_existing_panel_message(channel, config)
    if existing:
        await existing.edit(embed=embed, attachments=[file], view=view)
    else:
        await channel.send(embed=embed, file=file, view=view)


async def find_existing_panel_message(channel: discord.TextChannel, config: TicketPanelConfig):
    marker = f"Ticket Panel: {config.key}"
    async for message in channel.history(limit=25):
        if message.author.bot and message.embeds:
            footer = message.embeds[0].footer.text or ""
            if marker in footer:
                return message
    return None


def build_panel_embed(config: TicketPanelConfig) -> discord.Embed:
    embed = discord.Embed(
        title=config.title,
        description=config.subtitle,
        color=PANEL_COLOR,
    )
    embed.set_image(url=f"attachment://{config.key}_ticket_panel.png")

    for ticket_type in config.ticket_types:
        embed.add_field(
            name=ticket_type.label,
            value=ticket_type.description,
            inline=False,
        )

    embed.set_footer(text=f"Ticket Panel: {config.key}")
    return embed


def build_ticket_banner(title: str, subtitle: str) -> io.BytesIO:
    width, height = 1200, 360
    img = Image.new("RGB", (width, height), (20, 24, 31))
    draw = ImageDraw.Draw(img)

    for y in range(height):
        blend = y / height
        r = int(20 + (34 - 20) * blend)
        g = int(24 + (57 - 24) * blend)
        b = int(31 + (84 - 31) * blend)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    draw.rectangle([0, height - 18, width, height], fill=(47, 128, 237))
    draw.rounded_rectangle([70, 64, 1130, 296], radius=24, outline=(76, 94, 120), width=3)

    title_font = _load_font(64, bold=True)
    subtitle_font = _load_font(30)
    small_font = _load_font(24)

    draw.text((105, 105), title, font=title_font, fill=(245, 248, 255))
    draw.text((108, 190), subtitle, font=subtitle_font, fill=(190, 200, 218))
    draw.text((108, 246), "Select a category below to open a private ticket.", font=small_font, fill=(130, 150, 178))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def _load_font(size: int, bold: bool = False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeuib.ttf" if bold else "segoeui.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


class TicketPanelView(discord.ui.View):
    def __init__(self, config: TicketPanelConfig):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(config))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, config: TicketPanelConfig):
        self.config = config
        options = [
            discord.SelectOption(
                label=ticket_type.label,
                value=ticket_type.key,
                description=ticket_type.description[:100],
            )
            for ticket_type in config.ticket_types
        ]
        super().__init__(
            placeholder="Select a ticket category",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"hellcore_ticket_select:{config.key}",
        )

    async def callback(self, interaction: discord.Interaction):
        ticket_type = get_ticket_type(self.config, self.values[0])
        if ticket_type is None:
            await interaction.response.send_message("That ticket category is no longer available.", ephemeral=True)
            return

        if interaction.guild is None or interaction.guild.id != self.config.guild_id:
            await interaction.response.send_message("This ticket panel is not configured for this server.", ephemeral=True)
            return

        existing = find_open_ticket(interaction.guild, interaction.user.id, ticket_type.key)
        if existing:
            await interaction.response.send_message(
                f"You already have an open {ticket_type.label} ticket: {existing.mention}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channel = await create_ticket_channel(interaction, self.config, ticket_type)
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to create ticket channels or manage channel permissions.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            await interaction.followup.send(f"Could not create ticket: {exc}", ephemeral=True)
            return

        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="hellcore_ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This can only be used inside a ticket channel.", ephemeral=True)
            return

        topic = interaction.channel.topic or ""
        if "hellcore_ticket=true" not in topic:
            await interaction.response.send_message("This channel is not a HellCore ticket.", ephemeral=True)
            return

        if not can_close_ticket(interaction):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return

        await interaction.response.send_message("Closing this ticket in 5 seconds.", ephemeral=True)
        await interaction.channel.send("Ticket closed. This channel will be deleted shortly.")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


async def create_ticket_channel(
    interaction: discord.Interaction,
    config: TicketPanelConfig,
    ticket_type: TicketType,
) -> discord.TextChannel:
    guild = interaction.guild
    member = interaction.user
    category = await get_or_create_ticket_category(guild, config, ticket_type)
    staff_roles = get_staff_roles(guild)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        ),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    for role in staff_roles:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )

    channel_name = build_ticket_channel_name(member, ticket_type)
    topic = f"hellcore_ticket=true owner_id={member.id} type={ticket_type.key} panel={config.key}"
    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=topic,
        reason=f"Ticket opened by {member}",
    )

    await send_ticket_welcome(channel, member, ticket_type, staff_roles)
    return channel


async def get_or_create_ticket_category(
    guild: discord.Guild,
    config: TicketPanelConfig,
    ticket_type: TicketType,
) -> discord.CategoryChannel:
    category_name = f"{config.category_prefix}{ticket_type.label}"
    category = discord.utils.get(guild.categories, name=category_name)
    if category:
        return category

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    for role in get_staff_roles(guild):
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)

    return await guild.create_category(
        name=category_name,
        overwrites=overwrites,
        reason="Creating HellCore ticket category",
    )


async def send_ticket_welcome(
    channel: discord.TextChannel,
    member: discord.abc.User,
    ticket_type: TicketType,
    staff_roles: list[discord.Role],
):
    staff_mentions = " ".join(role.mention for role in staff_roles[:5])
    intro = f"{member.mention}"
    if staff_mentions:
        intro = f"{intro} {staff_mentions}"

    embed = discord.Embed(
        title=f"{ticket_type.label} Ticket",
        description=(
            f"Welcome {member.mention}. A staff member will help you as soon as possible.\n\n"
            f"**Ticket Type**\n{ticket_type.label}\n\n"
            f"{TICKET_RULES}"
        ),
        color=TICKET_COLOR,
    )
    embed.set_footer(text="Use the button below when this ticket is resolved.")
    await channel.send(content=intro, embed=embed, view=CloseTicketView())


def get_ticket_type(config: TicketPanelConfig, key: str) -> TicketType | None:
    return next((ticket_type for ticket_type in config.ticket_types if ticket_type.key == key), None)


def find_open_ticket(guild: discord.Guild, user_id: int, ticket_type_key: str) -> discord.TextChannel | None:
    owner_token = f"owner_id={user_id}"
    type_token = f"type={ticket_type_key}"
    for channel in guild.text_channels:
        topic = channel.topic or ""
        if "hellcore_ticket=true" in topic and owner_token in topic and type_token in topic:
            return channel
    return None


def build_ticket_channel_name(member: discord.abc.User, ticket_type: TicketType) -> str:
    username = getattr(member, "name", str(member))
    username = re.sub(r"[^a-zA-Z0-9-]+", "-", username).strip("-").lower()
    username = username or "user"
    return f"{username}-{ticket_type.key}"[:90]


def get_staff_roles(guild: discord.Guild) -> list[discord.Role]:
    configured_ids = parse_id_list(os.getenv("TICKET_STAFF_ROLE_IDS", ""))
    if configured_ids:
        return [role for role in guild.roles if role.id in configured_ids]

    return [
        role
        for role in guild.roles
        if not role.is_default()
        and (role.permissions.administrator or role.permissions.manage_channels)
    ]


def parse_id_list(raw: str) -> set[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def can_close_ticket(interaction: discord.Interaction) -> bool:
    topic = interaction.channel.topic or ""
    if f"owner_id={interaction.user.id}" in topic:
        return True

    permissions = interaction.user.guild_permissions
    if permissions.administrator or permissions.manage_channels:
        return True

    return any(role in get_staff_roles(interaction.guild) for role in getattr(interaction.user, "roles", []))
