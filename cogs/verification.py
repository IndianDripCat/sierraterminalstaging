import discord
from discord.ext import commands
from discord import app_commands, ui, Embed, Color, Interaction
import aiohttp
import random
import string
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
import os
from motor.motor_asyncio import AsyncIOMotorClient

# Use centralized GROUP_IDS from project root
from group_ids import GROUP_IDS

# MongoDB collections used by the cog
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"))
role_bindings_col = mongo_client['sierra_applications']['role_bindings']
verifications_col = mongo_client['sierra_applications']['verifications']


class VerificationModal(ui.Modal, title="Roblox Verification"):
    """Modal for entering Roblox username"""
    username = ui.TextInput(
        label="What is your ROBLOX Username?",
        placeholder="Enter your Roblox username (e.g AtlantisSider)",
        required=True,
        min_length=3,
        max_length=20
    )

    def __init__(self, verification_cog):
        super().__init__()
        self.verification_cog = verification_cog

    async def on_submit(self, interaction: Interaction):
        roblox_username = self.username.value.strip()

        try:
            # Send initial checking message as the interaction response (ephemeral)
            checking_embed = Embed(
                title="Checking...",
                description="Checking account status, hang tight.",
                color=discord.Color.dark_blue()
            )
            checking_embed.set_footer(text=f"Terminal • {self._get_footer()}")

            await interaction.response.send_message(embed=checking_embed, ephemeral=True)
            checking_message = await interaction.original_response()

            # Check if account exists on Roblox API
            roblox_user_id = await self.verification_cog.get_roblox_user_id(roblox_username)

            if roblox_user_id is None:
                # Account not found
                error_embed = Embed(
                    title="❌ Account not found",
                    description=f'The account, `{roblox_username}`, could not be found on the Roblox API. If you feel this was in error, please contact a member of Terminal Staff.',
                    color=discord.Color.dark_gold()
                )
                error_embed.set_footer(text=f"Terminal • {self._get_footer()}")
                await checking_message.edit(embed=error_embed)
            else:
                # Account found - show confirmation
                roblox_profile_url = f"https://www.roblox.com/users/{roblox_user_id}/profile"

                found_embed = Embed(
                    title="✅ Account found",
                    description=f"Is this your Roblox account?\n\nUsername: [{roblox_username}]({roblox_profile_url})\nRoblox Profile: {roblox_profile_url}",
                    color=discord.Color.dark_blue()
                )
                found_embed.set_footer(text=f"Terminal • {self._get_footer()}")

                # Create Yes/No buttons
                confirm_view = ConfirmAccountView(
                    self.verification_cog,
                    interaction.user,
                    roblox_username,
                    roblox_user_id
                )

                await checking_message.edit(embed=found_embed, view=confirm_view)
        except Exception as e:
            try:
                await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
            except Exception:
                pass
            print(f"Error in VerificationModal.on_submit: {e}")

    def _get_footer(self):
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return formatted


class ConfirmAccountView(ui.View):
    """View for confirming if the found Roblox account is correct"""
    
    def __init__(self, verification_cog, user, roblox_username, roblox_user_id):
        super().__init__(timeout=300)
        self.verification_cog = verification_cog
        self.user = user
        self.roblox_username = roblox_username
        self.roblox_user_id = roblox_user_id
        self.message = None

    async def on_error(self, interaction: Interaction, error: Exception, item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    @ui.button(label="✅ Yes", style=discord.ButtonStyle.green)
    async def confirm_yes(self, interaction: Interaction, button: ui.Button):
        # edit the ephemeral message the user interacted with
        
        # Generate 24-digit verification code
        verification_code = ''.join(random.choices(string.digits, k=24))
        
        # Send verification code message
        code_embed = Embed(
            title="🔗 Linking your Roblox account",
            description=f"Please put this code into your Roblox bio to confirm your identity.\n```SIERRATERMINAL-VERIFICATION-{verification_code}```\nPlease click \"Done\" when you have put the code into your bio.",
            color=discord.Color.dark_blue()  # Blue
        )
        code_embed.set_footer(text=f"Terminal • {self._get_footer()}")
        
        # Store the context for the Done button
        verify_view = VerifyBioView(
            self.verification_cog,
            self.user,
            self.roblox_username,
            self.roblox_user_id,
            verification_code
        )
        
        await interaction.response.edit_message(embed=code_embed, view=verify_view)

    @ui.button(label="❌ No", style=discord.ButtonStyle.red)
    async def confirm_no(self, interaction: Interaction, button: ui.Button):
        # Open the modal again for the user
        modal = VerificationModal(self.verification_cog)
        await interaction.response.send_modal(modal)

    def _get_footer(self):
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return formatted


class VerifyBioView(ui.View):
    """View for verifying the code in the Roblox bio"""
    
    def __init__(self, verification_cog, user, roblox_username, roblox_user_id, verification_code):
        super().__init__(timeout=600)  # 10 minute timeout
        self.verification_cog = verification_cog
        self.user = user
        self.roblox_username = roblox_username
        self.roblox_user_id = roblox_user_id
        self.verification_code = verification_code
        self.original_embed = None

    async def on_error(self, interaction: Interaction, error: Exception, item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    @ui.button(label="Done", style=discord.ButtonStyle.green)
    async def done_button(self, interaction: Interaction, button: ui.Button):
        # edit the ephemeral message the user interacted with
        
        # Check if code is in user's bio
        code_found = await self.verification_cog.check_roblox_bio(
            self.roblox_user_id,
            self.verification_code
        )
        
        if not code_found:
            # Code not found - show error and keep the button
            error_embed = Embed(
                title="❌ Ownership not confirmed",
                description=f"The code could not be found in your Roblox bio. Please re-enter it.\n```SIERRATERMINAL-VERIFICATION-{self.verification_code}```\nClick done once completed.",
                color=discord.Color.dark_gold()
            )
            error_embed.set_footer(text=f"Terminal • {self._get_footer()}")
            
            await interaction.response.edit_message(embed=error_embed)
        else:
            saved = await self.verification_cog.log_verification(
                self.user.id,
                str(self.user),
                self.roblox_user_id,
                self.roblox_username
            )

            if not saved:
                already_embed = Embed(
                    title="✅ Already Verified",
                    description="This Discord account is already verified in the MongoDB database.",
                    color=discord.Color.dark_blue()
                )
                already_embed.set_footer(text=f"Terminal • {self._get_footer()}")
                await interaction.response.edit_message(embed=already_embed, view=None)
                return

            success_embed = Embed(
                title="✅ Account ownership confirmed",
                description=f'You have successfully linked Discord account `{self.user}` to Roblox account `{self.roblox_username}`.\n\nIf this was done in error, please let a member of Terminal Staff know as soon as possible.',
                color=discord.Color.dark_blue()
            )
            success_embed.set_footer(text=f"Terminal • {self._get_footer()}")
            await interaction.response.edit_message(embed=success_embed, view=None)

    def _get_footer(self):
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return formatted


class VerificationLandingView(ui.View):
    """Landing view for /verify with optional delete-data button."""

    def __init__(self, verification_cog, owner_id: int, oauth_url: str, show_delete_button: bool = False):
        super().__init__(timeout=300)
        self.verification_cog = verification_cog
        self.owner_id = owner_id
        self.add_item(
            ui.Button(
                label="Link Roblox Account",
                style=discord.ButtonStyle.gray,
                url=oauth_url,
            )
        )

        if show_delete_button:
            delete_button = ui.Button(
                label="Delete Verification Data",
                emoji="⚠️",
                style=discord.ButtonStyle.red,
            )
            delete_button.callback = self.delete_verification
            self.add_item(delete_button)

    async def on_error(self, interaction: Interaction, error: Exception, item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    async def delete_verification(self, interaction: Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This button is not for you.", ephemeral=True)
            return

        record = await self.verification_cog.get_existing_verification(interaction.user.id)
        if not self.verification_cog.is_verified_record(record):
            embed = Embed(
                title="❌ Not Verified",
                description="You do not currently have any Roblox verification data stored.",
                color=discord.Color.dark_gold()
            )
            embed.set_footer(text=f"Terminal • {self._get_footer()}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        roblox_username = self.verification_cog.get_verified_username(record) or str(interaction.user)
        roblox_user_id = self.verification_cog.get_verified_user_id(record)
        profile_url = (
            f"https://www.roblox.com/users/{roblox_user_id}/profile"
            if roblox_user_id is not None
            else f"https://www.roblox.com/search/users?keyword={quote(roblox_username)}"
        )
        avatar_url = (
            f"https://rbxavatar.unnamed.games/avatar-headshot?userName={quote(roblox_username)}"
            if roblox_username
            else None
        )

        embed = Embed(
            title="⚠️ Deleting Verification Data",
            description="Clicking the button below will delete all mention of your Roblox verification data. You will need to reverify again after clicking the button below.",
            color=discord.Color.red()
        )
        if avatar_url:
            embed.set_author(name=roblox_username, url=profile_url, icon_url=avatar_url)
        else:
            embed.set_author(name=roblox_username, url=profile_url)
        embed.set_footer(text=f"Terminal • {self._get_footer()}")

        await interaction.response.send_message(
            embed=embed,
            view=DeleteVerificationConfirmView(self.verification_cog, self.owner_id),
            ephemeral=True,
        )

    def _get_footer(self):
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return formatted


class DeleteVerificationConfirmView(ui.View):
    """Confirmation view for deleting a user's verification data."""

    def __init__(self, verification_cog, owner_id: int):
        super().__init__(timeout=180)
        self.verification_cog = verification_cog
        self.owner_id = owner_id

    async def on_error(self, interaction: Interaction, error: Exception, item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    @ui.button(label="Delete Verification Data", emoji="⚠️", style=discord.ButtonStyle.red)
    async def confirm_delete(self, interaction: Interaction, button: ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This button is not for you.", ephemeral=True)
            return

        deleted_count = await self.verification_cog.delete_verification_data(interaction.user.id)

        if deleted_count == 0:
            embed = Embed(
                title="❌ Nothing to Delete",
                description="No Roblox verification records were found for your account.",
                color=discord.Color.dark_gold()
            )
        else:
            embed = Embed(
                title="✅ Verification Data Deleted",
                description="All mention of your Roblox verification data has been deleted. You will need to verify again if you want to use verification-based features.",
                color=discord.Color.dark_blue()
            )

        embed.set_footer(text=f"Terminal • {self._get_footer()}")
        await interaction.response.edit_message(embed=embed, view=None)

    def _get_footer(self):
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return formatted


class BeginVerificationView(ui.View):
    """View for the initial /verify command"""
    
    def __init__(self, verification_cog):
        super().__init__(timeout=None)
        self.verification_cog = verification_cog

    async def on_error(self, interaction: Interaction, error: Exception, item):
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

    @ui.button(label="Begin Verification", style=discord.ButtonStyle.gray)
    async def begin_verification(self, interaction: Interaction, button: ui.Button):
        modal = VerificationModal(self.verification_cog)
        await interaction.response.send_modal(modal)


class Verification(commands.Cog):
    @app_commands.command(name="verify")
    async def verify(self, interaction: Interaction):
        """Slash command: send Roblox OAuth link embed and optional delete-data button."""
        existing_record = await self.get_existing_verification(interaction.user.id)
        is_verified = self.is_verified_record(existing_record)

        if is_verified:
            roblox_username = self.get_verified_username(existing_record) or "your linked Roblox account"
            embed = Embed(
                title="✅ Already Verified",
                description=(
                    f"You are already verified as `{roblox_username}`.\n"
                    "Use the link button below if you need to relink, or the red button to delete your stored verification data."
                ),
                color=discord.Color.dark_blue()
            )
        else:
            embed = Embed(
                title="🔗 Linking your Roblox account",
                description="Please click the link below and follow all steps given to you.",
                color=discord.Color.dark_gray()
            )

        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        embed.set_footer(text=f"Terminal • {formatted}")

        oauth_url = "https://flaskwebappsierra7-production-6f7b.up.railway.app/discord/oauth/start"
        view = VerificationLandingView(self, interaction.user.id, oauth_url, show_delete_button=is_verified)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = None

    async def cog_load(self):
        """Initialize aiohttp session when cog loads"""
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        """Clean up aiohttp session when cog unloads"""
        if self.session:
            await self.session.close()

    async def get_existing_verification(self, discord_id: int):
        return await verifications_col.find_one(
            {
                "$or": [
                    {"discord_id": discord_id},
                    {"discord.id": str(discord_id)},
                ]
            }
        )

    def is_verified_record(self, record) -> bool:
        if not record:
            return False
        roblox = record.get("roblox", {})
        return bool(
            record.get("verified")
            or record.get("roblox_id")
            or roblox.get("id")
            or roblox.get("sub")
        )

    def get_verified_username(self, record) -> str | None:
        if not record:
            return None
        roblox = record.get("roblox", {})
        return (
            roblox.get("preferred_username")
            or roblox.get("username")
            or roblox.get("name")
            or record.get("roblox_username")
        )

    def get_verified_user_id(self, record) -> int | None:
        if not record:
            return None
        roblox = record.get("roblox", {})
        roblox_id = record.get("roblox_id") or roblox.get("id") or roblox.get("sub")
        try:
            return int(roblox_id) if roblox_id is not None else None
        except (TypeError, ValueError):
            return None

    async def delete_verification_data(self, discord_id: int) -> int:
        record = await self.get_existing_verification(discord_id)
        clauses = [
            {"discord_id": discord_id},
            {"discord.id": str(discord_id)},
        ]

        if record:
            roblox = record.get("roblox", {})
            roblox_id = record.get("roblox_id") or roblox.get("id") or roblox.get("sub")
            if roblox_id is not None:
                clauses.extend([
                    {"roblox_id": roblox_id},
                    {"roblox_id": str(roblox_id)},
                    {"roblox.id": roblox_id},
                    {"roblox.id": str(roblox_id)},
                    {"roblox.sub": roblox_id},
                    {"roblox.sub": str(roblox_id)},
                ])

            roblox_username = self.get_verified_username(record)
            if roblox_username:
                clauses.extend([
                    {"roblox_username": roblox_username},
                    {"roblox.username": roblox_username},
                    {"roblox.preferred_username": roblox_username},
                    {"roblox.name": roblox_username},
                ])

        result = await verifications_col.delete_many({"$or": clauses})
        return result.deleted_count

    async def get_roblox_user_id(self, username: str) -> int:
        """Fetch Roblox user ID from API"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        try:
            url = "https://users.roblox.com/v1/usernames/users"
            payload = {"usernames": [username]}
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return data["data"][0]["id"]
                return None
        except Exception as e:
            print(f"Error fetching Roblox user ID: {e}")
            return None

    async def get_roblox_groups(self, user_id: int) -> dict:
        """Retrieve the groups and roles for a user from Roblox API"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        try:
            url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {}
        except Exception as e:
            print(f"Error fetching Roblox groups: {e}")
            return {}

    async def check_roblox_bio(self, user_id: int, verification_code: str) -> bool:
        """Check if verification code is in the user's Roblox bio"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        
        try:
            url = f"https://users.roblox.com/v1/users/{user_id}"
            
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bio = data.get("description", "").lower()
                    code_to_check = f"sierraterminal-verification-{verification_code}".lower()
                    return code_to_check in bio
                return False
        except Exception as e:
            print(f"Error checking Roblox bio: {e}")
            return False

    async def log_verification(self, discord_id: int, discord_username: str, roblox_id: int, roblox_username: str) -> bool:
        """Log verification data to MongoDB unless the user is already verified."""
        try:
            existing_record = await self.get_existing_verification(discord_id)
            if self.is_verified_record(existing_record):
                print(f"Verification skipped for {discord_username}; user is already verified.")
                return False

            verification_data = {
                "discord_id": discord_id,
                "discord_username": discord_username,
                "roblox_id": roblox_id,
                "roblox_username": roblox_username,
                "verified_at": datetime.now(ZoneInfo("Australia/Melbourne")),
                "verified": True
            }

            await verifications_col.update_one(
                {"discord_id": discord_id},
                {"$set": verification_data},
                upsert=True
            )

            print(f"Verification logged for {discord_username} -> {roblox_username}")
            return True
        except Exception as e:
            print(f"Error logging verification to MongoDB: {e}")
            return False

    async def check_permission(self, interaction_or_ctx, permission_string: str) -> tuple:
        """Check if user has permission for the given permission string.

        The permission string may contain comma-separated clauses. Each clause
        should follow the schema ``permissions:Group:<id>[:<rank>-<rank>]``.
        A leading ``permissions:`` prefix is optional and will be added
        automatically if missing. Returns ``(has_access, permission_text, not_verified)``.
        """

        async def _eval_clause(clause: str) -> tuple[bool, str, bool]:
            clause = clause.strip()
            if clause.lower().startswith("permissions:all"):
                return True, "✅ All users allowed", False

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
                elif rank_part:
                    try:
                        min_rank = max_rank = int(rank_part)
                    except ValueError:
                        pass

            user_id = interaction_or_ctx.user.id if hasattr(interaction_or_ctx, "user") else interaction_or_ctx.author.id

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

            if group_ident.isdigit():
                group_id = int(group_ident)
            else:
                group_id = GROUP_IDS.get(group_ident) or GROUP_IDS.get(group_ident.upper())

            if group_id is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank)
                return False, perm_text, False

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

            if not record or roblox_id is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return False, perm_text, True

            groups_data = await self.get_roblox_groups(roblox_id)
            ranks = {}
            for entry in groups_data.get("data", []):
                gid = entry["group"]["id"]
                rank = entry["role"].get("rank")
                ranks[gid] = rank

            user_rank = ranks.get(group_id)
            if user_rank is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return False, perm_text, False

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
            results = []
            not_verified_any = False

            for clause in clauses:
                result = await _eval_clause(clause)
                results.append(result)
                if result[0]:
                    return True, result[1], result[2]
                not_verified_any = not_verified_any or result[2]

            perm_texts = [text for (_, text, _) in results if text]
            combined = ", *or*\n".join(perm_texts) if perm_texts else (permission_string or "No permission rule configured.")
            return False, combined, not_verified_any
        except Exception as e:
            print(f"Error in check_permission: {e}")
            return False, f"❗ Permission check failed: `{e}`", False

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

    @commands.command(name="manverify")
    async def manverify(self, ctx: commands.Context, member: discord.Member, roblox_username: str):
        """Manually verify a user by Roblox username."""
        # Check permissions: permissions:Group:SD:254-
        has_access, perm_text, not_verified = await self.check_permission(ctx, "permissions:Group:35798374")
        if not has_access:
            if not_verified:
                embed = Embed(
                    title="🚫 Not Verified",
                    description="You are not verified! Please type `/verify` to begin the verification process.",
                    color=discord.Color.red()
                )
                return await ctx.send(embed=embed)
            embed = Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            return await ctx.send(embed=embed)
        
        try:
            # Fetch Roblox user ID
            roblox_user_id = await self.get_roblox_user_id(roblox_username)
            
            if roblox_user_id is None:
                return await ctx.send(f"❌ Roblox account `{roblox_username}` not found.", delete_after=5)
            
            # Log verification to MongoDB
            await self.log_verification(
                member.id,
                str(member),
                roblox_user_id,
                roblox_username
            )
            
            # Send confirmation
            embed = Embed(
                title="✅ Manual Verification Complete",
                description=f"Successfully verified {member.mention} as `{roblox_username}`",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Error: {e}", delete_after=5)
            print(f"Error in manverify: {e}")




async def setup(bot: commands.Bot):
    """Setup function for loading the cog"""
    cog = Verification(bot)
    await bot.add_cog(cog)

    # Note: command sync will be performed after all commands (verify + roles)
    # are added below to ensure everything is registered together.

    # Role bindings have been moved to `cogs/roles.py` for clearer separation
    # and easier debugging. The roles cog will register `/roles` commands
    # and handle synchronization independently.

