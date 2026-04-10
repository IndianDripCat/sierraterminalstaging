import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from mongo_db import MongoDB
import os
from motor.motor_asyncio import AsyncIOMotorClient

# MongoDB collections for appblock cog
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"))
verifications_col = mongo_client['sierra_applications']['verifications']

# Use centralized GROUP_IDS mapping
from group_ids import GROUP_IDS

class AppBlock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = MongoDB()
        self.ALLOWED_ROLE_ID = 1461243381945729192  # Role ID that's allowed to use these commands

    async def check_permission(self, ctx, permission_string: str) -> tuple[bool, str, bool]:
        """Check if user has permission for the given permission string.

        Multiple clauses may be separated with commas, e.g.:
            permissions:Group:SCPF:75-,permissions:Group:MaD:255
        A leading ``permissions:`` prefix can be omitted and will be added
        automatically.  Malformed clauses now result in denial instead of
        silently granting access.
        Returns (has_access, permission_text, not_verified).
        """
        import aiohttp

        async def _eval_clause(clause: str) -> tuple[bool, str, bool]:
            clause = clause.strip()
            if clause.lower().startswith("permissions:all"):
                return True, "✅ All users allowed", False

            # support shorthand by prepending prefix if missing
            if not clause.lower().startswith("permissions:"):
                clause = "permissions:" + clause

            parts = clause.split(":")
            if len(parts) < 3 or parts[1] != "Group":
                return False, f"❗ Invalid permission clause `{clause}`", False

            group_ident = parts[2]
            min_rank = None
            max_rank = None

            if len(parts) >= 4:
                rank_part = parts[3]
                if "-" in rank_part:
                    low, high = rank_part.split("-", 1)
                    try:
                        min_rank = int(low)
                    except ValueError:
                        pass
                    if high:
                        try:
                            max_rank = int(high)
                        except ValueError:
                            pass
                    else:
                        max_rank = None
                elif rank_part:
                    try:
                        min_rank = max_rank = int(rank_part)
                    except ValueError:
                        pass

            # Get user ID
            if isinstance(ctx, commands.Context):
                user_id = ctx.author.id
            else:
                user_id = ctx.user.id

            # Get the user's verification record across both legacy and OAuth schemas.
            record = await verifications_col.find_one(
                {
                    "$or": [
                        {"discord_id": user_id},
                        {"discord.id": str(user_id)},
                    ]
                }
            )
            roblox = record.get("roblox", {}) if record else {}
            roblox_id = (
                record.get("roblox_id")
                or roblox.get("sub")
                or roblox.get("id")
            ) if record else None
            try:
                roblox_id = int(roblox_id) if roblox_id is not None else None
            except (TypeError, ValueError):
                roblox_id = None

            # Convert group_ident to numeric group ID early
            if group_ident.isdigit():
                group_id = int(group_ident)
            else:
                # try exact key first (preserve case), fall back to uppercase keys
                group_id = GROUP_IDS.get(group_ident) or GROUP_IDS.get(group_ident.upper())

            if group_id is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank)
                return False, perm_text, False

            # fetch rank names ahead of membership checks
            rank_names = {}
            if min_rank is not None:
                try:
                    async with aiohttp.ClientSession() as session:
                        url = f"https://groups.roblox.com/v1/groups/{group_id}/roles"
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                for role in data.get("roles", []):
                                    rank_names[role["rank"]] = role["name"]
                except Exception as e:
                    print(f"Error fetching group roles: {e}")

            # if no usable verification record, bail out now
            if not record or roblox_id is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return False, perm_text, True

            # Get verification cog
            verification_cog = ctx.bot.get_cog('Verification') if isinstance(ctx, commands.Context) else ctx.client.get_cog('Verification')
            if verification_cog is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return False, perm_text, False

            groups_data = await verification_cog.get_roblox_groups(roblox_id)
            ranks = {}
            for entry in groups_data.get('data', []):
                gid = entry['group']['id']
                rank = entry['role'].get('rank')
                ranks[gid] = rank

            # Convert group_ident to numeric group ID
            # (already done above)

            user_rank = ranks.get(group_id)

            if user_rank is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return False, perm_text, False

            # rank names were already fetched earlier; nothing to do here.
            # (This leftover block from a previous refactor caused a syntax
            # error and has been removed.)

            # Check rank requirement
            has_access = False
            if min_rank is None:
                has_access = True
            elif max_rank is None:
                has_access = user_rank >= min_rank
            else:
                has_access = min_rank <= user_rank <= max_rank

            perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
            return has_access, perm_text, False

        try:
            clauses = [c.strip() for c in permission_string.split(",") if c.strip()]
            not_verified_any = False
            results = []
            for clause in clauses:
                res = await _eval_clause(clause)
                results.append(res)
                if res[0]:
                    return True, res[1], res[2]
                not_verified_any = not_verified_any or res[2]
            # none granted access; build an "or" list
            perm_texts = [t for (_, t, _) in results if t]
            combined = ", *or*\n".join(perm_texts) if perm_texts else ""
            return False, combined, not_verified_any
        except Exception as e:
            print(f"Error checking permission: {e}")
            return True, "✅ Permission check error (defaulting to access)", False

    async def format_permission_text(self, group_ident: str, min_rank: int = None, max_rank: int = None, rank_names: dict = None) -> str:
        """Format permission text for invalid-access embeds."""
        if rank_names is None:
            rank_names = {}

        group_label = f"`{group_ident}`"

        if min_rank is None:
            return f"In {group_label}"

        min_rank_name = rank_names.get(min_rank, f"Rank {min_rank}")
        if max_rank is None or min_rank == max_rank:
            return f"**{min_rank_name}** in {group_label}"

        max_rank_name = rank_names.get(max_rank, f"Rank {max_rank}")
        return f"**{min_rank_name} - {max_rank_name}** in {group_label}"

    async def is_allowed(self, ctx) -> bool:
        """Check if the user has the required role to use these commands"""
        if isinstance(ctx, commands.Context):
            member = ctx.author
        elif isinstance(ctx, discord.Interaction):
            member = ctx.user
        else:
            return False
        if isinstance(member, discord.Member):
            return any(role.id == self.ALLOWED_ROLE_ID for role in member.roles)
        return False

    @commands.hybrid_group(name="appblock", description="Manage application blocks")
    async def appblock(self, ctx):
        """Base command for managing application blocks"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @appblock.command(name="issue", description="Block a user from submitting applications")
    @app_commands.describe(
        user="The user to block from submitting applications",
        reason="The reason for the block",
        evidence="Evidence for the block. Leave blank if none.",
        expires="When the block expires (e.g., 1d, 2w, 1mo, never). Leave blank for permanent block."
    )
    async def appblock_issue(self, ctx: commands.Context, user: discord.Member, reason: str, evidence: str = None, expires: str = None):
        mel_tz = ZoneInfo("Australia/Melbourne")
        if ctx.interaction is not None:
            await ctx.interaction.response.defer()
        
        # Check permissions: permissions:Group:SD:254-
        has_access, perm_text, not_verified = await self.check_permission(ctx, "permissions:Group:767872560:255")
        if not has_access:
            if not_verified:
                embed = discord.Embed(
                    title="🚫 Not Verified",
                    description="You are not verified! Please type `/verify` to begin the verification process.",
                    color=discord.Color.red()
                )
                if ctx.interaction is not None:
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed)
                return
            embed = discord.Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            if ctx.interaction is not None:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        
        if not await self.is_allowed(ctx):
            embed = discord.Embed(
                title="⛔ Missing Permissions",
                description="You don't have the required role to use this command.",
                color=discord.Color.red()
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
            if ctx.interaction is not None:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        now = datetime.now(mel_tz)
        block_data = self.db.add_application_block(
            user_id=user.id,
            user_name=str(user),
            reason=reason,
            evidence=evidence if evidence else "`No evidence supplied.`",
            issued_by_id=ctx.author.id,
            issued_by_name=str(ctx.author),
            expires_in=expires if expires else "never",
            now_override=now
        )
        embed = discord.Embed(
            title=f"✅ Application Blocked - {user}",
            description=f"{user.mention} has been application blocked\n**Reason:**\n> {reason}",
            color=discord.Color.from_rgb(244, 124, 124)  # #f47c7c
        )
        embed.set_author(name=f"{user}", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="📝 Blocked User", value=f"`{user.name}` (`{user.id}`)", inline=False)
        embed.add_field(name="🕵️ Issued By", value=f"`{ctx.author.name}` (`{ctx.author.id}`)", inline=True)
        embed.add_field(name="📁 Attached evidence", value=evidence if evidence else "`No evidence supplied.`", inline=True)
        embed.add_field(name="🔗 Infraction ID", value=f"```{block_data['block_id']}```", inline=False)
        embed.add_field(name="📅 Issued", value=f"<t:{int(now.timestamp())}:F>", inline=False)
        if block_data['expires_at']:
            expiry_timestamp = int(block_data['expires_at'].timestamp())
            expiry_str = f"<t:{expiry_timestamp}:R> (<t:{expiry_timestamp}:f>)"
        else:
            expiry_str = "Never"
        embed.add_field(name="⌛ Expires", value=expiry_str, inline=False)
        embed.set_footer(text=f"{ctx.bot.user.name} • {now.strftime('%d/%m/%Y %H:%M')}", 
                        icon_url=ctx.bot.user.display_avatar.url)
        if ctx.interaction is not None:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)

    @appblock.command(name="revoke", description="Revoke an application block")
    @app_commands.describe(
        block_id="The ID of the block to revoke"
    )
    async def appblock_revoke(self, ctx: commands.Context, block_id: str):
        mel_tz = ZoneInfo("Australia/Melbourne")
        if ctx.interaction is not None:
            await ctx.interaction.response.defer()
        
        # Check permissions: permissions:Group:SD:254-
        has_access, perm_text, not_verified = await self.check_permission(ctx, "permissions:Group:SD:254-")
        if not has_access:
            if not_verified:
                embed = discord.Embed(
                    title="🚫 Not Verified",
                    description="You are not verified! Please type `/verify` to begin the verification process.",
                    color=discord.Color.red()
                )
                if ctx.interaction is not None:
                    await ctx.interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await ctx.send(embed=embed)
                return
            embed = discord.Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            if ctx.interaction is not None:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        
        if not await self.is_allowed(ctx):
            embed = discord.Embed(
                title="⛔ Missing Permissions",
                description="You don't have the required role to use this command.",
                color=discord.Color.red()
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
            if ctx.interaction is not None:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        block = self.db.revoke_application_block(
            block_id=block_id,
            revoked_by_id=ctx.author.id,
            revoked_by_name=str(ctx.author)
        )
        if not block:
            embed = discord.Embed(
                title="❗ ID does not exist",
                description=f"The specified ID, `{block_id}`, does not exist. Please check again and try again later.",
                color=discord.Color.orange()
            )
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
            if ctx.interaction is not None:
                await ctx.interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await ctx.send(embed=embed)
            return
        embed = discord.Embed(
            title=f"✅ Application Block revoked - {block['user_name']}",
            description=f"**Reason:**\n> {block['reason']}",
            color=discord.Color.from_rgb(79, 195, 247)  # #4fc3f7
        )
        # Set author and thumbnail to blocked user
        avatar_url = None
        if hasattr(self, 'bot') and hasattr(self.bot, 'get_user'):
            user_obj = self.bot.get_user(block['user_id'])
            if user_obj:
                avatar_url = user_obj.display_avatar.url
        if not avatar_url:
            avatar_url = f"https://cdn.discordapp.com/avatars/{block['user_id']}/{block.get('user_avatar', '')}.png"
        embed.set_author(name=f"{block['user_name']}", icon_url=avatar_url)
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="❌ Blocked User", value=f"`{block['user_name']}` (`{block['user_id']}`)", inline=False)
        embed.add_field(name="✍️ Issued By", value=f"`{block['issued_by_name']}` (`{block['issued_by_id']}`)", inline=True)
        embed.add_field(name="📁 Attached evidence", value=block['evidence'], inline=True)
        issued_at = block['issued_at']
        if isinstance(issued_at, str):
            issued_at = datetime.fromisoformat(issued_at)
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
            value=f"Revoked by `{ctx.author.name}` on <t:{revoked_timestamp}:F>",
            inline=False
        )
        embed.set_footer(text=f"{ctx.bot.user.name} • {datetime.now(mel_tz).strftime('%d/%m/%Y %H:%M')}", 
                        icon_url=ctx.bot.user.display_avatar.url)
        if ctx.interaction is not None:
            await ctx.interaction.followup.send(embed=embed)
        else:
            await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(AppBlock(bot))
