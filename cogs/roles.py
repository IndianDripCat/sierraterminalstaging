import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed
import os
import aiohttp
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient

# MongoDB collections for roles cog
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"))
role_bindings_col = mongo_client['sierra_applications']['role_bindings']
verifications_col = mongo_client['sierra_applications']['verifications']

# Use centralized GROUP_IDS mapping
from group_ids import GROUP_IDS

def _format_component_lines(items: list[str]) -> str:
    if not items:
        return "> None"
    return "\n".join(f"> {item}" for item in items)


def _extract_roblox_id(record: dict | None) -> int | None:
    if not record:
        return None

    roblox = record.get("roblox", {})
    roblox_id = record.get("roblox_id") or roblox.get("sub") or roblox.get("id")
    try:
        return int(roblox_id) if roblox_id is not None else None
    except (TypeError, ValueError):
        return None


async def format_permission_text(group_ident: str, min_rank: int = None, max_rank: int = None, rank_names: dict = None) -> str:
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

async def check_permission(interaction: Interaction, permission_string: str) -> tuple[bool, str, bool]:
    """Check if user has permission for the given permission string.

    The permission string contains one or more clauses separated by commas.
    Each clause should look like:

        permissions:Group:<identifier>[:<rank>-<rank>]

    The leading ``permissions:`` prefix may be omitted (e.g. ``Group:SD:254-``) – the
    helper will prepend it automatically.  However, malformed clauses are now
    treated as **denials** rather than silently granting access.

    Example with two clauses:
        "permissions:Group:SCPF:75-,permissions:Group:MaD:255"

    Returns a triple `(has_access, formatted_permission_text, not_verified)` where
    `not_verified` is True if the user has no verification record.
    """    # helper to evaluate a single clause
    async def _eval_clause(clause: str) -> tuple[bool, str, bool]:
        # normalize whitespace
        clause = clause.strip()

        # global override: anyone allowed
        if clause.lower().startswith("permissions:all"):
            return True, "✅ All users allowed", False

        # if the caller omitted the leading `permissions:` treat it as if it were present
        if not clause.lower().startswith("permissions:"):
            clause = "permissions:" + clause

        parts = clause.split(":")
        # format must be permissions:Group:<identifier>[:<rank spec>]
        if len(parts) < 3 or parts[1] != "Group":
            # malformed clauses should **not** grant access
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

        # Get the user's verification record across both legacy and OAuth schemas.
        record = await verifications_col.find_one(
            {
                "$or": [
                    {"discord_id": interaction.user.id},
                    {"discord.id": str(interaction.user.id)},
                ]
            }
        )
        roblox_id = _extract_roblox_id(record)

        # Convert group_ident to numeric group ID up front so we can fetch
        # rank names even if the user isn't verified or isn't in the group.
        if group_ident.isdigit():
            group_id = int(group_ident)
        else:
            # try exact key first (preserve case), fall back to uppercase keys
            group_id = GROUP_IDS.get(group_ident) or GROUP_IDS.get(group_ident.upper())

        if group_id is None:
            perm_text = await format_permission_text(group_ident, min_rank, max_rank)
            return False, perm_text, False

        # Fetch rank names for display (done early so they are available for
        # any failure message).  We only do this when a specific rank is
        # requested; for whole-group permissions there's nothing to look up.
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

        # If there's no usable verification record, we can't check the user's groups
        # so stop here; the permission text includes any fetched rank names.
        if not record or roblox_id is None:
            perm_text = await format_permission_text(group_ident, min_rank, max_rank, rank_names)
            return False, perm_text, True

        # Get verification cog to fetch groups
        verification_cog = interaction.client.get_cog('Verification')
        if verification_cog is None:
            perm_text = await format_permission_text(group_ident, min_rank, max_rank, rank_names)
            return False, perm_text, False

        groups_data = await verification_cog.get_roblox_groups(roblox_id)
        ranks = {}
        for entry in groups_data.get('data', []):
            gid = entry['group']['id']
            rank = entry['role'].get('rank')
            ranks[gid] = rank

        user_rank = ranks.get(group_id)

        # Check if user has the required rank
        if user_rank is None:
            perm_text = await format_permission_text(group_ident, min_rank, max_rank, rank_names)
            return False, perm_text, False

        # Check rank requirement
        has_access = False
        if min_rank is None:
            # Entire group membership
            has_access = True
        elif max_rank is None:
            has_access = user_rank >= min_rank
        else:
            has_access = min_rank <= user_rank <= max_rank

        perm_text = await format_permission_text(group_ident, min_rank, max_rank, rank_names)
        return has_access, perm_text, False

    try:
        # split into comma-separated clauses and evaluate each
        clauses = [c.strip() for c in permission_string.split(",") if c.strip()]
        results = []
        for clause in clauses:
            res = await _eval_clause(clause)
            results.append(res)
            # short-circuit: if any clause grants access return immediately
            if res[0]:
                return True, res[1], res[2]

        # no clause allowed access; combine permissions in an "or" list
        perm_texts = [t for (_, t, _) in results if t]
        combined = ", *or*\n".join(perm_texts) if perm_texts else ""
        # if any clause reported not_verified, propagate it
        not_verified = any(n for (_, _, n) in results)
        return False, combined, not_verified

    except Exception as e:
        print(f"Error checking permission: {e}")
        return True, "✅ Permission check error (defaulting to access)", False
roles_group = app_commands.Group(name="roles", description="Manage Roblox role bindings")

@roles_group.command(name="bind")
@app_commands.describe(
    discord_role="The Discord role to assign for this binding",
    mapping="Mapping string, e.g. Group:SD or Group:SD:254-255 or Group:123456:2-",
    nickname="Optional nickname template, e.g. [DEV] {roblox-username}"
)
async def bind(interaction: Interaction, discord_role: discord.Role, mapping: str, nickname: str | None = None):
    """Create a role binding for the current guild."""
    print(f"/roles bind called by {interaction.user} with role {getattr(discord_role, 'id', 'unknown')} mapping {mapping} nickname {nickname}")
    # Defer response immediately to prevent timeout
    await interaction.response.defer(ephemeral=True)

    # Check permissions: permissions:Group:SD:254-
    # both clauses include the `permissions:` prefix; the helper will also
    # accept the shorthand `Group:XYZ` if you forget it, but it's clearer to
    # specify it explicitly here.
    has_access, perm_text, not_verified = await check_permission(
        interaction,
        "permissions:Group:SCPF:255,permissions:Group:EAA:251-,permissions:Group:MaD:249-"
    )
    if not has_access:
        if not_verified:
            embed = Embed(
                title="🚫 Not Verified",
                description="You are not verified! Please type `/verify` to begin the verification process.",
                color=discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        embed = Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red()
        )
        embed.add_field(name="Permissions", value=perm_text, inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    try:
        if not mapping.startswith("Group:"):
            return await interaction.followup.send("Mapping must start with `Group:`", ephemeral=True)
        parts = mapping.split(":")
        if len(parts) < 2 or len(parts) > 3:
            return await interaction.followup.send("Invalid mapping format. Use Group:SD or Group:SD:254-255", ephemeral=True)
        
        group_ident = parts[1]
        min_rank = None
        max_rank = None
        
        # If there's a third part, parse ranks; otherwise entire group
        if len(parts) == 3:
            rank_part = parts[2]
            if "-" in rank_part:
                low, high = rank_part.split("-", 1)
                try:
                    min_rank = int(low)
                except ValueError:
                    return await interaction.followup.send("Invalid minimum rank.", ephemeral=True)
                if high:
                    try:
                        max_rank = int(high)
                    except ValueError:
                        return await interaction.followup.send("Invalid maximum rank.", ephemeral=True)
                else:
                    max_rank = None
            else:
                try:
                    min_rank = max_rank = int(rank_part)
                except ValueError:
                    return await interaction.followup.send("Invalid rank.", ephemeral=True)

        # Convert group_ident to numeric group ID
        if group_ident.isdigit():
            group_id = int(group_ident)
        else:
            # try exact key first (preserve case), fall back to uppercase keys
            group_id = GROUP_IDS.get(group_ident) or GROUP_IDS.get(group_ident.upper())
            if group_id is None:
                return await interaction.followup.send(f"Unknown group abbreviation: {group_ident}", ephemeral=True)

        # Fetch group roles from Roblox API to get rank names (only if specific ranks are set)
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

        await role_bindings_col.delete_many({"guild_id": interaction.guild.id, "discord_role_id": discord_role.id})
        nickname_template = nickname.strip() if nickname else None

        await role_bindings_col.insert_one({
            "guild_id": interaction.guild.id,
            "discord_role_id": discord_role.id,
            "group": group_ident,
            "min_rank": min_rank,
            "max_rank": max_rank,
            "nickname_template": nickname_template,
        })

        success_embed = Embed(
            title="Successfully binded roles",
            description="Successfully binded the following permissions:",
            color=discord.Color.dark_blue()
        )
        
        # Build permission text using rank names
        if min_rank is None:
            # Entire group membership
            perm_text = f"> - In {group_ident}"
        else:
            min_rank_name = rank_names.get(min_rank, f"Rank {min_rank}")
            if max_rank is None:
                # "Rank X or above"
                perm_text = f"> - **{min_rank_name}** or above in {group_ident}"
            elif min_rank == max_rank:
                # Single rank
                perm_text = f"> - **{min_rank_name}** in {group_ident}"
            else:
                # Range: show min-max
                max_rank_name = rank_names.get(max_rank, f"Rank {max_rank}")
                perm_text = f"> - **{min_rank_name}** to **{max_rank_name}** in {group_ident}"

        success_embed.add_field(name="Permissions", value=perm_text, inline=False)
        if nickname_template:
            success_embed.add_field(name="Nickname Template", value=f"`{nickname_template}`", inline=False)
        await interaction.followup.send(embed=success_embed, ephemeral=True)
    except Exception as exc:
        print(f"Error in /roles bind: {exc}")
        try:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
        except Exception:
            pass


@roles_group.command(name="mapping")
async def mapping(interaction: Interaction):
    """Show current role bindings for this guild."""
    # Defer response immediately to prevent timeout
    await interaction.response.defer(ephemeral=True)
    # Check permissions: require MaD 255
    has_access, perm_text, not_verified = await check_permission(interaction, "permissions:Group:SCPF:255,permissions:Group:EAA:251-,permissions:Group:MaD:250-")
    if not has_access:
        if not_verified:
            embed = Embed(
                title="🚫 Not Verified",
                description="You are not verified! Please type `/verify` to begin the verification process.",
                color=discord.Color.red()
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)
        embed = Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red()
        )
        embed.add_field(name="Permissions", value=perm_text, inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)
    if not has_access:
        embed = Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red()
        )
        embed.add_field(name="Permissions", value=perm_text, inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    try:
        binds = await role_bindings_col.find({"guild_id": interaction.guild.id}).to_list(None)
        embed = Embed(title=f"Role Bindings - {interaction.guild.name}")
        embed.description = f"This guild has **{len(binds)}** role binds"
        for i, bind in enumerate(binds, start=1):
            role = interaction.guild.get_role(bind['discord_role_id'])
            if not role:
                continue
            grp = bind['group']
            mn = bind.get('min_rank')
            mx = bind.get('max_rank')
            if mn is None:
                cond = f"In {grp}"
            elif mx is None:
                cond = f"[role id {mn}] or higher in {grp}"
            elif mn == mx:
                cond = f"{mn} in {grp}"
            else:
                cond = f"{mn}-{mx} in {grp}"
            value = f"> - {cond}"
            if bind.get('nickname_template'):
                value += f"\n> - Nickname: `{bind['nickname_template']}`"
            embed.add_field(name=f"{i}. Role: {role.mention}", value=value, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as exc:
        print(f"Error in /roles mapping: {exc}")
        try:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
        except Exception:
            pass


@roles_group.command(name="update")
@app_commands.describe(user="Optional: User to update roles for (defaults to you)")
async def update(interaction: Interaction, user: discord.User = None):
    """Update Discord roles based on Roblox groups."""
    await interaction.response.defer(thinking=True)

    # Check permissions: require SD rank 254 or higher
    has_access, perm_text, not_verified = await check_permission(interaction, "permissions:All")
    if not has_access:
        if not_verified:
            embed = Embed(
                title="🚫 Not Verified",
                description="You are not verified! Please type `/verify` to begin the verification process.",
                color=discord.Color.red()
            )
            return await interaction.edit_original_response(content=None, embed=embed, view=None)
        embed = Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red()
        )
        embed.add_field(name="Permissions", value=perm_text, inline=False)
        return await interaction.edit_original_response(content=None, embed=embed, view=None)

    response_sent = False
    try:

        # Use provided user or default to interaction.user
        target_user = user if user else interaction.user

        # Support both legacy verification records and the newer OAuth-shaped records.
        record = await verifications_col.find_one(
            {
                "$or": [
                    {"discord_id": target_user.id},
                    {"discord.id": str(target_user.id)},
                ]
            }
        )
        if not record:
            await interaction.edit_original_response(content=f"{target_user.mention} has not linked a Roblox account.")
            return
        roblox = record.get('roblox', {})
        roblox_id = _extract_roblox_id(record)
        roblox_username = (
            roblox.get('preferred_username')
            or roblox.get('username')
            or roblox.get('name')
            or record.get('roblox_username')
            or 'Unknown'
        )
        if roblox_id is None:
            await interaction.edit_original_response(content=f"{target_user.mention} has not linked a Roblox account.")
            return
        # attempt to use the Verification cog to fetch groups
        verification_cog = interaction.client.get_cog('Verification')
        if verification_cog is None:
            await interaction.edit_original_response(content="Verification cog not loaded.")
            return
        groups_data = await verification_cog.get_roblox_groups(roblox_id)
        ranks = {}
        print(f"[DEBUG] Roblox groups for user {roblox_username} (ID: {roblox_id}):")
        for entry in groups_data.get('data', []):
            gid = entry['group']['id']
            rank = entry['role'].get('rank')
            print(f"  Group ID: {gid}, Rank: {rank}")
            ranks[gid] = rank
        member = interaction.guild.get_member(target_user.id)
        if not member:
            await interaction.edit_original_response(content=f"Could not find {target_user.mention} in this guild.")
            return

        added = []
        removed = []
        nickname_template_to_apply = None
        binds = await role_bindings_col.find({"guild_id": interaction.guild.id}).to_list(None)
        for bind in binds:
            grp = bind['group']
            if grp.isdigit():
                gid = int(grp)
            else:
                gid = GROUP_IDS.get(grp) or GROUP_IDS.get(grp.upper())
            print(f"[DEBUG] Checking bind: group='{grp}' (resolved ID: {gid}), min_rank={bind.get('min_rank')}, max_rank={bind.get('max_rank')}")
            if gid is None:
                print(f"[DEBUG] Skipping bind: could not resolve group ID for '{grp}'")
                continue
            user_rank = ranks.get(gid)
            print(f"[DEBUG] User rank in group {gid}: {user_rank}")
            allowed = False
            if user_rank is not None:
                mn = bind.get('min_rank')
                mx = bind.get('max_rank')
                if mn is None:
                    # Entire group membership - allow if user is in group
                    allowed = True
                elif mx is None:
                    allowed = user_rank >= mn
                else:
                    allowed = mn <= user_rank <= mx
            role_obj = interaction.guild.get_role(bind['discord_role_id'])
            if not role_obj:
                print(f"[DEBUG] Skipping bind: could not find Discord role with ID {bind['discord_role_id']}")
                continue
            if allowed:
                print(f"[DEBUG] Adding role {role_obj.name} to user.")
                if bind.get('nickname_template') and nickname_template_to_apply is None:
                    nickname_template_to_apply = bind['nickname_template']
                if role_obj not in member.roles:
                    await member.add_roles(role_obj)
                    added.append(role_obj.mention)
            else:
                print(f"[DEBUG] Removing role {role_obj.name} from user (if present).")
                if role_obj in member.roles:
                    await member.remove_roles(role_obj)
                    removed.append(role_obj.mention)
        
        desired_nickname = roblox_username
        if nickname_template_to_apply:
            desired_nickname = (
                nickname_template_to_apply
                .replace("{roblox-username}", roblox_username)
                .replace("{roblox_username}", roblox_username)
                .replace("{username}", roblox_username)
            ).strip()
            if not desired_nickname:
                desired_nickname = roblox_username

        desired_nickname = desired_nickname[:32]
        nickname_display = member.nick or member.name
        try:
            await member.edit(nick=desired_nickname)
            nickname_display = desired_nickname
        except discord.Forbidden:
            pass  # Bot doesn't have permission to change nicknames

        # Build success response using discord.py v2 layout components
        roblox_profile_url = f"https://www.roblox.com/users/{roblox_id}/profile"
        avatar_url = f"https://rbxavatar.unnamed.games/avatar-headshot?userName={roblox_username}"
        timestamp = int(datetime.now().timestamp())
        added_display = _format_component_lines(added)
        removed_display = _format_component_lines(removed)

        success_view = discord.ui.LayoutView()
        container = discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay("# Successfully updated user roles"),
                discord.ui.TextDisplay(f"## 👤 [`{roblox_username}`]({roblox_profile_url}) `{roblox_id}`"),
                accessory=discord.ui.Thumbnail(
                    avatar_url,
                    description=f"{roblox_username}'s avatar"
                )
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"### 📛 Nickname\n> {nickname_display}"),
            discord.ui.TextDisplay(f"### 🆕 Added\n{added_display}"),
            discord.ui.TextDisplay(f"### 🗑️ Removed\n{removed_display}"),
            discord.ui.Separator(visible=False),
            discord.ui.TextDisplay(f"-# Sierra Terminal • <t:{timestamp}:R>"),
            accent_colour=discord.Colour.teal()
        )
        success_view.add_item(container)

        response_sent = True
        await interaction.edit_original_response(content=None, attachments=[], view=success_view)
    except Exception as exc:
        print(f"Error in /roles update: {exc}")
        if response_sent:
            return
        try:
            await interaction.edit_original_response(content=f"Error: {exc}", attachments=[], view=None)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    # register the roles group commands and sync
    if bot.tree.get_command('roles') is None:
        bot.tree.add_command(roles_group)

    async def _sync_roles():
        try:
            await bot.tree.sync()
            print("Synchronized /roles commands")
        except Exception as e:
            print(f"Error syncing /roles commands: {e}")

    if bot.is_ready():
        await _sync_roles()
    else:
        bot.add_listener(_sync_roles, 'on_ready')
