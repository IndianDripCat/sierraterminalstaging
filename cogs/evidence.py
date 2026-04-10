import os
import random
import string
from datetime import datetime, timezone
from urllib.parse import quote

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

mongo_client = AsyncIOMotorClient(
    os.getenv(
        "MONGO_URI",
        "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018",
    )
)
evidence_col = mongo_client["sierra_applications"]["evidence_records"]
verifications_col = mongo_client["sierra_applications"]["verifications"]
scif_col = mongo_client["sierra_applications"]["scif_clearances"]

CLASSIFICATIONS = {
    "OFFICIAL": {"code": 0, "label": "⚪ OFFICIAL"},
    "CONFIDENTIAL": {"code": 1, "label": "🔵 CONFIDENTIAL"},
    "RESTRICTED": {"code": 2, "label": "🟡 RESTRICTED"},
    "SECRET": {"code": 3, "label": "🟠 SECRET"},
    "TOP SECRET": {"code": 4, "label": "🔴 TOP SECRET"},
    "THAUMIEL": {"code": 5, "label": "🟣 THAUMIEL"},
}

TYPE_LABELS = {
    "IMAGE": "🖼️ Image",
    "VIDEO": "🎥 Video",
    "TEXT": "📝 Text",
}

CLASSIFICATION_ORDER = [
    "OFFICIAL",
    "CONFIDENTIAL",
    "RESTRICTED",
    "SECRET",
    "TOP SECRET",
    "THAUMIEL",
]

PERMISSION_STRING = "Group:EAA,Group:EC:246-,Group:CMT,Group:OOTA,Group:SCPF:254-"
SCIF_PERMISSION_STRING = "Group:EAA,Group:SCPF:254-"


class Evidence(commands.Cog):
    evidence = app_commands.Group(name="evidence", description="Manage RAISA evidence records")
    scif = app_commands.Group(name="scif", description="Manage evidence channel SCIFs", parent=evidence)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await evidence_col.create_index("fullcode", unique=True)
            await evidence_col.create_index("shortcode", unique=True)
            await scif_col.create_index("channel_id", unique=True)
            await scif_col.create_index("guild_id")
        except Exception as exc:
            print(f"[Evidence] Failed to create MongoDB indexes: {exc}")

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _embed_color(self) -> discord.Color:
        return discord.Color.from_rgb(118, 92, 172)

    def _error_color(self) -> discord.Color:
        return discord.Color.from_rgb(226, 120, 120)

    def _footer_text(self) -> str:
        return f"Sierra Terminal • {self._now().strftime('%d/%m/%Y %H:%M')}"

    def _footer_icon(self) -> str | None:
        if self.bot.user:
            return self.bot.user.display_avatar.url
        return None

    def _make_error_embed(self, title: str, description: str) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=self._error_color())
        embed.set_footer(text=self._footer_text(), icon_url=self._footer_icon())
        return embed

    def _build_fullcode(self, classification: str, evidence_type: str, shortcode: str, version: int) -> str:
        class_code = CLASSIFICATIONS[classification]["code"]
        return f"RAISA/SC{class_code}/{evidence_type}/{shortcode}/V{version}"

    def _get_classification_code(self, classification: str | None) -> int:
        return CLASSIFICATIONS.get((classification or "OFFICIAL").upper(), {}).get("code", 0)

    def _get_classification_label(self, classification: str | None) -> str:
        if not classification:
            return "N/A"
        key = classification.upper()
        return CLASSIFICATIONS.get(key, {}).get("label", classification)

    def _image_like(self, content: str | None) -> bool:
        if not content:
            return False
        lowered = content.lower().split("?", 1)[0]
        return lowered.startswith(("http://", "https://")) and lowered.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        )

    def _format_content_value(self, content: str | None) -> str:
        if not content:
            return "`No content supplied.`"
        if content.startswith(("http://", "https://")):
            return f"[Open evidence content]({content})"
        if len(content) > 1000:
            return content[:1000] + "…"
        return content

    async def _generate_shortcode(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        while True:
            shortcode = "".join(random.choices(alphabet, k=16))
            existing = await evidence_col.find_one({"shortcode": shortcode}, {"_id": 1})
            if not existing:
                return shortcode

    async def _get_creator_identity(self, discord_id: int, fallback_name: str) -> dict:
        record = await verifications_col.find_one(
            {
                "$or": [
                    {"discord_id": discord_id},
                    {"discord.id": str(discord_id)},
                ]
            }
        )

        roblox = record.get("roblox", {}) if record else {}
        roblox_id = record.get("roblox_id") or roblox.get("sub") or roblox.get("id") if record else None

        try:
            roblox_id = int(roblox_id) if roblox_id is not None else None
        except (TypeError, ValueError):
            roblox_id = None

        roblox_username = (
            roblox.get("preferred_username")
            or roblox.get("username")
            or roblox.get("name")
            or (record.get("roblox_username") if record else None)
            or fallback_name
        )

        profile_url = (
            f"https://www.roblox.com/users/{roblox_id}/profile"
            if roblox_id is not None
            else f"https://www.roblox.com/search/users?keyword={quote(roblox_username)}"
        )
        avatar_url = (
            f"https://rbxavatar.unnamed.games/avatar-headshot?userName={quote(roblox_username)}"
            if roblox_username
            else None
        )

        return {
            "roblox_id": roblox_id,
            "roblox_username": roblox_username,
            "profile_url": profile_url,
            "avatar_url": avatar_url,
        }

    async def _build_actor_embed(
        self,
        user_id: int,
        fallback_name: str,
        title: str,
        description: str,
        *,
        color: discord.Color | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color or self._embed_color(),
        )
        actor = await self._get_creator_identity(user_id, fallback_name)

        if actor["avatar_url"] and actor["profile_url"]:
            embed.set_author(
                name=actor["roblox_username"],
                url=actor["profile_url"],
                icon_url=actor["avatar_url"],
            )
        elif actor["avatar_url"]:
            embed.set_author(name=actor["roblox_username"], icon_url=actor["avatar_url"])
        elif actor["profile_url"]:
            embed.set_author(name=actor["roblox_username"], url=actor["profile_url"])
        else:
            embed.set_author(name=actor["roblox_username"])

        embed.set_footer(text=self._footer_text(), icon_url=self._footer_icon())
        return embed

    async def _ensure_access(self, interaction: Interaction, permission_string: str | None = None) -> bool:
        verification_cog = self.bot.get_cog("Verification")
        if verification_cog is None:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "The verification system is currently unavailable. Please try again later.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        perm_to_check = permission_string or PERMISSION_STRING
        has_access, perm_text, not_verified = await verification_cog.check_permission(
            interaction,
            perm_to_check,
        )
        if has_access:
            return True

        if not_verified:
            embed = self._make_error_embed(
                "🚫 Not Verified",
                "You are not verified! Please type `/verify` to begin the verification process.",
            )
        else:
            embed = self._make_error_embed(
                "🚫 Invalid Access",
                "You do not have proper authorization to use this command.",
            )
            embed.add_field(name="Permissions", value=perm_text or perm_to_check, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    async def _get_scif_record(self, channel_id: int) -> dict | None:
        return await scif_col.find_one({"channel_id": channel_id})

    async def _check_scif_access(self, channel_id: int, evidence_classification: str | None) -> tuple[bool, str | None]:
        scif_record = await self._get_scif_record(channel_id)
        if not scif_record:
            return False, None

        channel_classification = scif_record.get("classification")
        evidence_code = self._get_classification_code(evidence_classification)
        channel_code = self._get_classification_code(channel_classification)
        return evidence_code <= channel_code, channel_classification

    async def _build_scif_denial_embed(
        self,
        interaction: Interaction,
        evidence_classification: str | None,
        channel_classification: str | None,
    ) -> discord.Embed:
        channel_display = self._get_classification_label(channel_classification) if channel_classification else "N/A"
        description = (
            "You cannot view this RAISA evidence file because the record classification is higher than the classification ceiling for the channel, or the channel has no SCIF specified.\n"
            f"> - **Evidence Classification:** `{self._get_classification_label(evidence_classification)}`\n"
            f"> - **Channel Classification:** `{channel_display}`"
        )
        return await self._build_actor_embed(
            interaction.user.id,
            str(interaction.user),
            "📂 Cannot view RAISA file",
            description,
            color=self._error_color(),
        )

    def _build_record_embed(self, action: str, record: dict) -> discord.Embed:
        fullcode = record.get("fullcode", "Unknown")
        titles = {
            "create": f"👁️ Created `{fullcode}`",
            "view": f"👁️ Viewing `{fullcode}`",
            "edit": f"✍️ Edited `{fullcode}`",
        }
        descriptions = {
            "create": f"> 🎥 Successfully created RAISA record entry for `{fullcode}`",
            "view": f"> 🎥 Viewing RAISA record entry for `{fullcode}`",
            "edit": f"> 🎥 Edited RAISA record entry for `{fullcode}`",
        }

        embed = discord.Embed(
            title=titles[action],
            description=descriptions[action],
            color=self._embed_color(),
        )

        author_name = record.get("author_roblox_username", "Unknown Roblox User")
        author_profile = record.get("author_profile_url")
        author_avatar = record.get("author_avatar_url")

        if author_avatar and author_profile:
            embed.set_author(name=author_name, url=author_profile, icon_url=author_avatar)
        elif author_avatar:
            embed.set_author(name=author_name, icon_url=author_avatar)
        elif author_profile:
            embed.set_author(name=author_name, url=author_profile)
        else:
            embed.set_author(name=author_name)

        embed.add_field(
            name="📷 Type",
            value=TYPE_LABELS.get(record.get("type", "TEXT"), record.get("type", "TEXT")),
            inline=True,
        )
        classification_label = CLASSIFICATIONS.get(record.get("classification", "OFFICIAL"), {}).get(
            "label", record.get("classification", "OFFICIAL")
        )
        embed.add_field(
            name="📝 Classification",
            value=f"`{classification_label}`",
            inline=True,
        )

        if author_profile:
            author_value = f"[{author_name}]({author_profile})"
        else:
            author_value = author_name

        embed.add_field(name="👤 Author", value=author_value, inline=True)

        created_at = record.get("created_at")
        if isinstance(created_at, datetime):
            created_value = f"<t:{int(created_at.timestamp())}:F>"
        else:
            created_value = "Unknown"
        embed.add_field(name="📅 Created", value=created_value, inline=True)

        embed.add_field(
            name="▶️ Content",
            value=self._format_content_value(record.get("content")),
            inline=False,
        )

        if record.get("type") == "IMAGE" and self._image_like(record.get("content")):
            embed.set_image(url=record.get("content"))

        embed.set_footer(text=self._footer_text(), icon_url=self._footer_icon())
        return embed

    @evidence.command(name="create", description="Create a new RAISA evidence entry")
    @app_commands.rename(record_type="type", evidence_text="evidence")
    @app_commands.describe(
        record_type="The evidence type to store",
        classification="The classification level for the record",
        evidence_text="The evidence content or URL",
    )
    @app_commands.choices(
        record_type=[
            app_commands.Choice(name="🖼️ Image", value="IMAGE"),
            app_commands.Choice(name="🎥 Video", value="VIDEO"),
            app_commands.Choice(name="📝 Text", value="TEXT"),
        ],
        classification=[
            app_commands.Choice(name="⚪ OFFICIAL", value="OFFICIAL"),
            app_commands.Choice(name="🔵 CONFIDENTIAL", value="CONFIDENTIAL"),
            app_commands.Choice(name="🟡 RESTRICTED", value="RESTRICTED"),
            app_commands.Choice(name="🟠 SECRET", value="SECRET"),
            app_commands.Choice(name="🔴 TOP SECRET", value="TOP SECRET"),
            app_commands.Choice(name="🟣 THAUMIEL", value="THAUMIEL"),
        ],
    )
    async def evidence_create(
        self,
        interaction: Interaction,
        record_type: str,
        classification: str,
        evidence_text: str,
    ):
        if not await self._ensure_access(interaction):
            return

        creator = await self._get_creator_identity(interaction.user.id, str(interaction.user))
        shortcode = await self._generate_shortcode()
        version = 1
        fullcode = self._build_fullcode(classification, record_type, shortcode, version)
        now = self._now()

        record = {
            "shortcode": shortcode,
            "fullcode": fullcode,
            "version": version,
            "type": record_type,
            "classification": classification,
            "content": evidence_text,
            "author_discord_id": interaction.user.id,
            "author_discord_name": str(interaction.user),
            "author_roblox_id": creator["roblox_id"],
            "author_roblox_username": creator["roblox_username"],
            "author_profile_url": creator["profile_url"],
            "author_avatar_url": creator["avatar_url"],
            "created_at": now,
            "updated_at": now,
        }

        try:
            await evidence_col.insert_one(record)
        except Exception as exc:
            embed = self._make_error_embed(
                "❗ Error while running command",
                f"The evidence entry could not be saved to MongoDB.\n```{exc}```",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.send_message(
            embed=self._build_record_embed("create", record),
            ephemeral=True,
        )

    @scif.command(name="add", description="Create or update a channel SCIF")
    @app_commands.describe(
        channel="The channel to add to the SCIF list",
        classification="The highest classification this channel can view",
    )
    @app_commands.choices(
        classification=[
            app_commands.Choice(name="⚪ OFFICIAL", value="OFFICIAL"),
            app_commands.Choice(name="🔵 CONFIDENTIAL", value="CONFIDENTIAL"),
            app_commands.Choice(name="🟡 RESTRICTED", value="RESTRICTED"),
            app_commands.Choice(name="🟠 SECRET", value="SECRET"),
            app_commands.Choice(name="🔴 TOP SECRET", value="TOP SECRET"),
            app_commands.Choice(name="🟣 THAUMIEL", value="THAUMIEL"),
        ]
    )
    async def scif_add(self, interaction: Interaction, channel: discord.TextChannel, classification: str):
        if interaction.guild is None:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "This command can only be used inside a server.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not await self._ensure_access(interaction, SCIF_PERMISSION_STRING):
            return

        now = self._now()
        classification_code = self._get_classification_code(classification)
        await scif_col.update_one(
            {"channel_id": channel.id},
            {
                "$set": {
                    "guild_id": interaction.guild.id,
                    "channel_id": channel.id,
                    "channel_name": channel.name,
                    "classification": classification,
                    "classification_code": classification_code,
                    "updated_at": now,
                    "updated_by_discord_id": interaction.user.id,
                    "updated_by_discord_name": str(interaction.user),
                },
                "$setOnInsert": {
                    "created_at": now,
                    "created_by_discord_id": interaction.user.id,
                    "created_by_discord_name": str(interaction.user),
                },
            },
            upsert=True,
        )

        embed = await self._build_actor_embed(
            interaction.user.id,
            str(interaction.user),
            "🤖 Channel SCIF Created",
            f"Successfully created a channel SCIF for channel {channel.mention} with classification `{self._get_classification_label(classification)}`",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scif.command(name="remove", description="Remove a channel SCIF")
    @app_commands.describe(channel="The channel to remove from the SCIF list")
    async def scif_remove(self, interaction: Interaction, channel: discord.TextChannel):
        if interaction.guild is None:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "This command can only be used inside a server.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not await self._ensure_access(interaction, SCIF_PERMISSION_STRING):
            return

        existing = await scif_col.find_one_and_delete({
            "guild_id": interaction.guild.id,
            "channel_id": channel.id,
        })
        if not existing:
            embed = self._make_error_embed(
                "❗ Error while running command",
                f"No channel SCIF could be found for {channel.mention}.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = await self._build_actor_embed(
            interaction.user.id,
            str(interaction.user),
            "❌ Channel SCIF Removed",
            f"Successfully removed channel SCIF {channel.mention} with classification `{self._get_classification_label(existing.get('classification'))}`",
            color=self._error_color(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scif.command(name="list", description="List all channel SCIFs in this server")
    async def scif_list(self, interaction: Interaction):
        if interaction.guild is None:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "This command can only be used inside a server.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not await self._ensure_access(interaction, SCIF_PERMISSION_STRING):
            return

        records = [
            record async for record in scif_col.find({"guild_id": interaction.guild.id}).sort("classification_code", 1)
        ]
        embed = await self._build_actor_embed(
            interaction.user.id,
            str(interaction.user),
            "🤖 Listing all channel SCIFs",
            f"Found `{len(records)}` guilds with a channel SCIF for guild **{interaction.guild.name}**",
        )

        grouped: dict[str, list[str]] = {}
        for record in records:
            classification = record.get("classification", "OFFICIAL")
            guild_channel = interaction.guild.get_channel(record.get("channel_id"))
            channel_value = guild_channel.mention if guild_channel else f"`Unknown Channel ({record.get('channel_id')})`"
            grouped.setdefault(classification, []).append(channel_value)

        for classification in CLASSIFICATION_ORDER:
            channels = grouped.get(classification, [])
            if not channels:
                continue
            embed.add_field(
                name=self._get_classification_label(classification),
                value="\n".join(f"> - {channel_link}" for channel_link in channels),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @evidence.command(name="view", description="View a RAISA evidence entry")
    @app_commands.describe(
        fullcode="The full RAISA code, e.g. RAISA/SC1/IMAGE/XXXXXXXXXXXXXXX/V1",
        shortcode="The 16-character shortcode only",
    )
    async def evidence_view(
        self,
        interaction: Interaction,
        fullcode: str | None = None,
        shortcode: str | None = None,
    ):
        if not await self._ensure_access(interaction):
            return

        if not fullcode and not shortcode:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "You must input either a fullcode or shortcode when running `/evidence view`!",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        query_parts = []
        if fullcode:
            query_parts.append({"fullcode": fullcode.strip().upper()})
        if shortcode:
            query_parts.append({"shortcode": shortcode.strip().upper()})

        record = await evidence_col.find_one({"$or": query_parts}) if query_parts else None
        if not record:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "No RAISA evidence entry could be found with the code you provided.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        channel_id = interaction.channel_id
        scif_allowed, channel_classification = await self._check_scif_access(
            channel_id,
            record.get("classification"),
        )
        if not scif_allowed:
            denial_embed = await self._build_scif_denial_embed(
                interaction,
                record.get("classification"),
                channel_classification,
            )
            await interaction.response.send_message(embed=denial_embed, ephemeral=True)
            return

        await interaction.response.send_message(
            embed=self._build_record_embed("view", record),
            ephemeral=True,
        )

    @evidence.command(name="edit", description="Edit an existing RAISA evidence entry")
    @app_commands.rename(record_type="type", evidence_text="evidence")
    @app_commands.describe(
        fullcode="The full RAISA code to edit",
        record_type="Optional new evidence type",
        classification="Optional new classification",
        evidence_text="Optional new evidence content or URL",
    )
    @app_commands.choices(
        record_type=[
            app_commands.Choice(name="🖼️ Image", value="IMAGE"),
            app_commands.Choice(name="🎥 Video", value="VIDEO"),
            app_commands.Choice(name="📝 Text", value="TEXT"),
        ],
        classification=[
            app_commands.Choice(name="⚪ OFFICIAL", value="OFFICIAL"),
            app_commands.Choice(name="🔵 CONFIDENTIAL", value="CONFIDENTIAL"),
            app_commands.Choice(name="🟡 RESTRICTED", value="RESTRICTED"),
            app_commands.Choice(name="🟠 SECRET", value="SECRET"),
            app_commands.Choice(name="🔴 TOP SECRET", value="TOP SECRET"),
            app_commands.Choice(name="🟣 THAUMIEL", value="THAUMIEL"),
        ],
    )
    async def evidence_edit(
        self,
        interaction: Interaction,
        fullcode: str,
        record_type: str | None = None,
        classification: str | None = None,
        evidence_text: str | None = None,
    ):
        if not await self._ensure_access(interaction):
            return

        if record_type is None and classification is None and evidence_text is None:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "You must provide at least one of `type`, `classification`, or `evidence` when running `/evidence edit`.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        existing = await evidence_col.find_one({"fullcode": fullcode.strip().upper()})
        if not existing:
            embed = self._make_error_embed(
                "❗ Error while running command",
                "No RAISA evidence entry could be found with that fullcode.",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        new_type = record_type or existing.get("type", "TEXT")
        new_classification = classification or existing.get("classification", "OFFICIAL")
        new_content = evidence_text if evidence_text is not None else existing.get("content", "")
        new_version = int(existing.get("version", 1)) + 1
        new_fullcode = self._build_fullcode(new_classification, new_type, existing["shortcode"], new_version)
        updated_at = self._now()

        update_doc = {
            "type": new_type,
            "classification": new_classification,
            "content": new_content,
            "version": new_version,
            "fullcode": new_fullcode,
            "updated_at": updated_at,
            "last_edited_by_discord_id": interaction.user.id,
            "last_edited_by_discord_name": str(interaction.user),
        }

        try:
            await evidence_col.update_one({"_id": existing["_id"]}, {"$set": update_doc})
        except Exception as exc:
            embed = self._make_error_embed(
                "❗ Error while running command",
                f"The evidence entry could not be updated.\n```{exc}```",
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        updated_record = {**existing, **update_doc}
        await interaction.response.send_message(
            embed=self._build_record_embed("edit", updated_record),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Evidence(bot))
