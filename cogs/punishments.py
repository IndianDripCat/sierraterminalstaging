import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed
from datetime import datetime
from zoneinfo import ZoneInfo
from mongo_db import MongoDB

class Punishments(commands.Cog):
    """Manage player punishments including application blocks."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = MongoDB()

    punishments = app_commands.Group(name="punishments", description="Player punishments commands")

    async def check_permission(self, interaction, permission_string: str):
        appblock_cog = self.bot.get_cog('AppBlock')
        if appblock_cog:
            return await appblock_cog.check_permission(interaction, permission_string)
        return True, "✅ Permission check fallback", False

    async def is_allowed(self, interaction):
        # Remove role ID check, always return True
        return True

    @punishments.command(name="issue")
    @app_commands.describe(
        punishment="Type of punishment",
        player="Roblox username to punish",
        reason="Reason for punishment",
        evidence="Evidence (optional)",
        expires="Expiry (e.g., 1d, 2w, 1mo, never)"
    )
    @app_commands.choices(
        punishment=[
            app_commands.Choice(name="Application Block", value="application block"),
            app_commands.Choice(name="Game Ban", value="game ban"),
            app_commands.Choice(name="Foundation Blacklist", value="foundation blacklist")
        ]
    )
    async def issue(self, interaction: Interaction, punishment: app_commands.Choice[str], player: str, reason: str, evidence: str = None, expires: str = None):
        mel_tz = ZoneInfo("Australia/Melbourne")
        now = datetime.now(mel_tz)
        punishment_value = punishment.value if punishment else None
        # Resolve roblox username to roblox_id if possible
        roblox_id = player  # Replace with actual lookup if you have a verification system
        roblox_username = player
        if punishment_value == "application block":
            perm_string = "permissions:Group:SCPF:255"
        elif punishment_value == "game ban":
            perm_string = "permissions:Group:SCPF:255"
        elif punishment_value == "foundation blacklist":
            perm_string = "permissions:Group:SCPF:255"
        else:
            await interaction.response.send_message("Invalid punishment type.", ephemeral=True)
            return
        has_access, perm_text, not_verified = await self.check_permission(interaction, perm_string)
        if not has_access:
            embed = discord.Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not await self.is_allowed(interaction):
            embed = discord.Embed(
                title="⛔ Missing Permissions",
                description="You don't have the required role to use this command.",
                color=discord.Color.red()
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if punishment_value == "application block":
            block_data = self.db.add_application_block(
                user_id=roblox_id,
                user_name=roblox_username,
                reason=reason,
                evidence=evidence if evidence else "`No evidence supplied.`",
                issued_by_id=interaction.user.id,
                issued_by_name=str(interaction.user),
                expires_in=expires if expires else "never",
                now_override=now
            )
            embed = discord.Embed(
                title=f"✅ Application Blocked - {roblox_username}",
                description=f"{roblox_username} has been application blocked\n**Reason:**\n> {reason}",
                color=discord.Color.from_rgb(244, 124, 124)
            )
            embed.set_author(name=f"{roblox_username}")
            embed.add_field(name="📝 Blocked User", value=f"`{roblox_username}` (`{roblox_id}`)", inline=False)
            embed.add_field(name="🕵️ Issued By", value=f"`{interaction.user.name}` (`{interaction.user.id}`)", inline=True)
            embed.add_field(name="📁 Attached evidence", value=evidence if evidence else "`No evidence supplied.`", inline=True)
            embed.add_field(name="🔗 Infraction ID", value=f"```{block_data['block_id']}```", inline=False)
            embed.add_field(name="📅 Issued", value=f"<t:{int(now.timestamp())}:F>", inline=False)
            if block_data['expires_at']:
                expiry_timestamp = int(block_data['expires_at'].timestamp())
                expiry_str = f"<t:{expiry_timestamp}:R> (<t:{expiry_timestamp}:f>)"
            else:
                expiry_str = "Never"
            embed.add_field(name="⌛ Expires", value=expiry_str, inline=False)
            embed.set_footer(text=f"{self.bot.user.name} • {now.strftime('%d/%m/%Y %H:%M')}", icon_url=self.bot.user.display_avatar.url)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"{punishment_value} issued for {roblox_username} (no logic implemented yet).", ephemeral=True)

    @punishments.command(name="revoke")
    @app_commands.describe(
        block_id="The ID of the punishment to revoke"
    )
    async def revoke(self, interaction: Interaction, block_id: str):
        # Only Application Block logic for now
        perm_string = "permissions:Group:SCPF:255"  # Replace with actual permission logic if needed
        has_access, perm_text, not_verified = await self.check_permission(interaction, perm_string)
        if not has_access:
            embed = discord.Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not await self.is_allowed(interaction):
            embed = discord.Embed(
                title="⛔ Missing Permissions",
                description="You don't have the required role to use this command.",
                color=discord.Color.red()
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        block = self.db.revoke_application_block(
            block_id=block_id,
            revoked_by_id=interaction.user.id,
            revoked_by_name=str(interaction.user)
        )
        if not block:
            embed = discord.Embed(
                title="❗ ID does not exist",
                description=f"The specified ID, `{block_id}`, does not exist. Please check again and try again later.",
                color=discord.Color.orange()
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(
            title=f"✅ Application Block revoked - {block['user_name']}",
            description=f"**Reason:**\n> {block['reason']}",
            color=discord.Color.from_rgb(79, 195, 247)
        )
        embed.set_author(name=f"{block['user_name']}", icon_url=interaction.user.display_avatar.url)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="❌ Blocked User", value=f"`{block['user_name']}` (`{block['user_id']}`)", inline=False)
        embed.add_field(name="✍️ Issued By", value=f"`{block['issued_by_name']}` (`{block['issued_by_id']}`)", inline=True)
        embed.add_field(name="📁 Attached evidence", value=block['evidence'], inline=True)
        issued_at = block['issued_at']
        if isinstance(issued_at, str):
            issued_at = datetime.fromisoformat(issued_at)
        mel_tz = ZoneInfo("Australia/Melbourne")
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(mel_tz)
        else:
            issued_at = issued_at.astimezone(mel_tz)
        issued_timestamp = int(issued_at.timestamp())
        embed.add_field(name="🔗 Infraction ID", value=f"```{block['block_id']}```", inline=False)
        embed.add_field(name="📅 Issued", value=f"<t:{issued_timestamp}:F>", inline=False)
        expires_at = block.get('expires_at')
        if expires_at:
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(mel_tz)
            else:
                expires_at = expires_at.astimezone(mel_tz)
            expiry_timestamp = int(expires_at.timestamp())
            expiry_str = f"<t:{expiry_timestamp}:R> (<t:{expiry_timestamp}:f>)"
        else:
            expiry_str = "Never"
        embed.add_field(name="⏳ Expires", value=expiry_str, inline=False)
        revoked_at = block.get('revoked_at')
        if isinstance(revoked_at, str):
            revoked_at = datetime.fromisoformat(revoked_at)
        if revoked_at:
            if revoked_at.tzinfo is None:
                revoked_at = revoked_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(mel_tz)
            else:
                revoked_at = revoked_at.astimezone(mel_tz)
        else:
            revoked_at = datetime.now(mel_tz)
        revoked_timestamp = int(revoked_at.timestamp())
        embed.add_field(
            name="🗑️ Revoked", 
            value=f"Revoked by `{interaction.user.name}` on <t:{revoked_timestamp}:F>",
            inline=False
        )
        embed.set_footer(text=f"{self.bot.user.name} • {datetime.now(mel_tz).strftime('%d/%m/%Y %H:%M')}", icon_url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @punishments.command(name="view")
    @app_commands.describe(player="Roblox username to view punishments for")
    async def view(self, interaction: Interaction, player: str):
        blocks = list(self.db.blocks.find({"user_name": player}))
        if not blocks:
            await interaction.response.send_message(f"No punishments found for {player}.", ephemeral=True)
            return
        embed = Embed(title=f"Punishments for {player}", color=discord.Color.red())
        for block in blocks:
            status = "Revoked" if block.get("revoked_at") else "Active"
            embed.add_field(
                name=f"Block ID: {block['block_id']}",
                value=f"Reason: {block['reason']}\nStatus: {status}\nIssued: {block['issued_at']}\nExpires: {block.get('expires_at', 'Never')}",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Punishments(bot))
