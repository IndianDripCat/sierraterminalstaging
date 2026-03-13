import discord
from discord.ext import commands
from discord import app_commands, ui, Embed, Color, Interaction
import aiohttp
import random
import string
from datetime import datetime
from zoneinfo import ZoneInfo
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
            # Code found - success!
            success_embed = Embed(
                title="✅ Account ownership confirmed",
                description=f'You have successfully linked Discord account `{self.user}` to Roblox account `{self.roblox_username}`.\n\nIf this was done in error, please let a member of Terminal Staff know as soon as possible.',
                color=discord.Color.dark_blue()
            )
            success_embed.set_footer(text=f"Terminal • {self._get_footer()}")
            
            await interaction.response.edit_message(embed=success_embed, view=None)
            
            # Log to MongoDB
            await self.verification_cog.log_verification(
                self.user.id,
                str(self.user),
                self.roblox_user_id,
                self.roblox_username
            )

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
    @app_commands.command(name="verify-test")
    async def verify_test(self, interaction: Interaction):
        """Slash command: send Roblox OAuth link embed."""
        # Embed for linking Roblox account
        embed = Embed(
            title="🔗 Linking your Roblox account",
            description="Please click the link below and follow all steps given to you.",
            color=discord.Color.dark_gray()
        )
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        embed.set_footer(text=f"Terminal • {formatted}")

        # Railway OAuth URL (replace with your deployed Railway URL)
        oauth_url = "https://sierraterminalstaging-production.up.railway.app/roblox/oauth/start"

        class RobloxOAuthView(ui.View):
            def __init__(self):
                super().__init__(timeout=None)
                self.add_item(
                    ui.Button(
                        label="Link Roblox Account",
                        style=discord.ButtonStyle.gray,
                        url=oauth_url
                    )
                )

        view = RobloxOAuthView()
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

    async def log_verification(self, discord_id: int, discord_username: str, roblox_id: int, roblox_username: str):
        """Log verification data to MongoDB"""
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            
            mongo_uri = "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"
            client = AsyncIOMotorClient(mongo_uri)
            db = client['sierra_applications']
            verifications_col = db['verifications']
            
            verification_data = {
                "discord_id": discord_id,
                "discord_username": discord_username,
                "roblox_id": roblox_id,
                "roblox_username": roblox_username,
                "verified_at": datetime.now(ZoneInfo("Australia/Melbourne")),
                "verified": True
            }
            
            # Upsert: update if exists, insert if not
            await verifications_col.update_one(
                {"discord_id": discord_id},
                {"$set": verification_data},
                upsert=True
            )
            
            print(f"Verification logged for {discord_username} -> {roblox_username}")
        except Exception as e:
            print(f"Error logging verification to MongoDB: {e}")

    async def check_permission(self, interaction_or_ctx, permission_string: str) -> tuple:
        """Check if user has permission for the given permission string.

        The permission string may contain comma-separated clauses.  Each clause
        should follow the schema ``permissions:Group:<id>[:<rank>-<rank>]``.  A
        leading ``permissions:`` prefix is optional and will be added
        automatically if missing; malformed clauses are now treated as denials.
        ``permissions:All`` works as a global override.

        Returns (has_access, formatted_permission_text, not_verified).
        """
        try:
            async def _eval_clause(clause: str) -> tuple[bool, str, bool]:
                clause = clause.strip()
                if clause.lower().startswith("permissions:all"):
                    return True, "✅ All users allowed", False

                # add missing prefix so that "Group:XYZ" works too
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
                if hasattr(interaction_or_ctx, 'user'):
                    user_id = interaction_or_ctx.user.id
                else:
                    user_id = interaction_or_ctx.author.id

                # Get user's verification record (if any)
                record = await verifications_col.find_one({"discord_id": user_id})
                roblox_id = record.get('roblox_id') if record else None

                # Convert group_ident to numeric group ID early
                if group_ident.isdigit():
                    group_id = int(group_ident)
                else:
                    # try exact key first (preserve case), fall back to uppercase keys
                    group_id = GROUP_IDS.get(group_ident) or GROUP_IDS.get(group_ident.upper())

                if group_id is None:
                    perm_text = await self.format_permission_text(group_ident, min_rank, max_rank)
                    return False, perm_text, False

                # Fetch rank names for display; do this before membership checks
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

                # if not verified, return now – still include rank names
                if not record:
                    perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                    return False, perm_text, True

                groups_data = await self.get_roblox_groups(roblox_id)
                ranks = {}
                for entry in groups_data.get('data', []):
                    gid = entry['group']['id']
                    rank = entry['role'].get('rank')
                    ranks[gid] = rank

                user_rank = ranks.get(group_id)
                if user_rank is None:
                    perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                    return False, perm_text, False

                # rank requirement
                has_access = False
                if min_rank is None:
                    has_access = True
                elif max_rank is None:
                    has_access = user_rank >= min_rank
                else:
                    has_access = min_rank <= user_rank <= max_rank

                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
                return has_access, perm_text, False

            # evaluate clauses one by one
            clauses = [c.strip() for c in permission_string.split(",") if c.strip()]
            not_verified_any = False
            results = []
            for clause in clauses:
                res = await _eval_clause(clause)
                results.append(res)
                if res[0]:
                    # granted by this clause; return immediately
                    return True, res[1], res[2]
                not_verified_any = not_verified_any or res[2]

            # combine all failure messages into a clean bullet list
            perm_texts = []
            for (_, t, _) in results:
                line = t
                if line.startswith("> -"):
                    line = line[3:].strip()
                perm_texts.append(f"- {line}")
            combined = "\n".join(perm_texts) if perm_texts else ""
            return False, combined, not_verified_any
        except Exception as e:  
            print(f"Error in check_permission: {e}")
            return True, "✅ Permission check error (defaulting to access)", False
            
            user_rank = ranks.get(group_id)
            
            if user_rank is None:
                perm_text = await self.format_permission_text(group_ident, min_rank, max_rank)
                return False, perm_text
            
            # Fetch rank names
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
            
            # Check rank requirement
            has_access = False
            if min_rank is None:
                has_access = True
            elif max_rank is None:
                has_access = user_rank >= min_rank
            else:
                has_access = min_rank <= user_rank <= max_rank
            
            perm_text = await self.format_permission_text(group_ident, min_rank, max_rank, rank_names)
            return has_access, perm_text
        
        except Exception as e:
            print(f"Error checking permission: {e}")
            return True, "✅ Permission check error (defaulting to access)"

    async def format_permission_text(self, group_ident: str, min_rank: int = None, max_rank: int = None, rank_names: dict = None) -> str:
        """Format permission text similar to role binding display"""
        if rank_names is None:
            rank_names = {}
        
        if min_rank is None:
            return f"> - In {group_ident}"
        else:
            min_rank_name = rank_names.get(min_rank, f"Rank {min_rank}")
            if max_rank is None:
                return f"> - **{min_rank_name}** or above in {group_ident}"
            elif min_rank == max_rank:
                return f"> - **{min_rank_name}** in {group_ident}"
            else:
                max_rank_name = rank_names.get(max_rank, f"Rank {max_rank}")
                return f"> - **{min_rank_name}** to **{max_rank_name}** in {group_ident}"

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

    @app_commands.command(name="verify")
    async def verify_action(self, interaction: Interaction):
        """Slash command callback: send the initial verification message (public)."""
        # Check permissions: permissions:Group:SD:254-
        has_access, perm_text, not_verified = await self.check_permission(interaction, "permissions:All")
        if not has_access:
            if not_verified:
                embed = Embed(
                    title="🚫 Not Verified",
                    description="You are not verified! Please type `/verify` to begin the verification process.",
                    color=discord.Color.red()
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            embed = Embed(
                title="🚫 Invalid Access",
                description="You do not have proper authorization to use this command.",
                color=discord.Color.red()
            )
            embed.add_field(name="Permissions", value=perm_text, inline=False)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        verify_embed = Embed(
            title="🔗 Linking your Roblox account",
            description="In order to verify your Roblox account with Sierra Terminal, please click the \"Begin Verification\" button below.\n\nWhen you have verified, update your roles with the `/update` command. After verification, you can remove the code in your bio.",
            color=discord.Color.dark_blue()  # Blue
        )

        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        verify_embed.set_footer(text=f"Terminal • {formatted}")

        view = BeginVerificationView(self)

        await interaction.response.send_message(embed=verify_embed, view=view)


async def setup(bot: commands.Bot):
    """Setup function for loading the cog"""
    cog = Verification(bot)
    await bot.add_cog(cog)

    # Note: command sync will be performed after all commands (verify + roles)
    # are added below to ensure everything is registered together.

    # Role bindings have been moved to `cogs/roles.py` for clearer separation
    # and easier debugging. The roles cog will register `/roles` commands
    # and handle synchronization independently.


