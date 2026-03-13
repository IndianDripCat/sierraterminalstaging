import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed
import os
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient

# MongoDB collections for roles cog
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"))
role_bindings_col = mongo_client['sierra_applications']['role_bindings']
verifications_col = mongo_client['sierra_applications']['verifications']

# Use centralized GROUP_IDS mapping
from group_ids import GROUP_IDS

async def format_permission_text(group_ident: str, min_rank: int = None, max_rank: int = None, rank_names: dict = None) -> str:
    """Format permission text similar to role binding display"""
    if rank_names is None:
        rank_names = {}
    
    if min_rank is None:
        # Entire group membership
        return f"> - In {group_ident}"
    else:
        min_rank_name = rank_names.get(min_rank, f"Rank {min_rank}")
        if max_rank is None:
            # "Rank X or above"
            return f"> - **{min_rank_name}** or above in {group_ident}"
        elif min_rank == max_rank:
            # Single rank
            return f"> - **{min_rank_name}** in {group_ident}"
        else:
            # Range: show min-max
            max_rank_name = rank_names.get(max_rank, f"Rank {max_rank}")
            return f"> - **{min_rank_name}** to **{max_rank_name}** in {group_ident}"

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

        # Get user's verification record
        record = await verifications_col.find_one({"discord_id": interaction.user.id})
        roblox_id = None
        if record:
            roblox_id = record.get('roblox_id')

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

        # If there's no verification record, we can't check the user's groups
        # so stop here; the permission text includes any fetched rank names.
        if not record:
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

        # no clause allowed access; combine permission texts for messaging
        # Build a clean listing of required permissions.  Each individual
        # clause returns a string formatted by :func:`format_permission_text`.
        # That helper prefixes lines with "> -" for quoting, which looks good
        # in some contexts but caused confusion when multiple clauses were
        # shown – users were seeing stray commas and "or" fragments.  Strip
        # the quoting and convert to a simple bullet list instead.
        perm_texts = []
        for (_, t, _) in results:
            line = t
            # remove the leading quote/bullet if present
            if line.startswith("> -"):
                line = line[3:].strip()
            perm_texts.append(f"- {line}")

        combined = "\n".join(perm_texts) if perm_texts else ""
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
    mapping="Mapping string, e.g. Group:SD or Group:SD:254-255 or Group:123456:2-"
)
async def bind(interaction: Interaction, discord_role: discord.Role, mapping: str):
    """Create a role binding for the current guild."""
    print(f"/roles bind called by {interaction.user} with role {getattr(discord_role, 'id', 'unknown')} mapping {mapping}")
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
        await role_bindings_col.insert_one({
            "guild_id": interaction.guild.id,
            "discord_role_id": discord_role.id,
            "group": group_ident,
            "min_rank": min_rank,
            "max_rank": max_rank,
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
    has_access, perm_text, not_verified = await check_permission(interaction, "permissions:Group:SCPF:255,permissions:Group:EAA:251-,permissions:Group:MaD:249-")
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
            embed.add_field(name=f"{i}. Role: {role.mention}", value=f"> - {cond}", inline=False)
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
    # Check permissions: require SD rank 254 or higher
    has_access, perm_text, not_verified = await check_permission(interaction, "permissions:All")
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
    if not has_access:
        embed = Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red()
        )
        embed.add_field(name="Permissions", value=perm_text, inline=False)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    try:
        await interaction.response.defer(ephemeral=True)
        
        # Use provided user or default to interaction.user
        target_user = user if user else interaction.user
        
        record = await verifications_col.find_one({"discord_id": target_user.id})
        if not record:
            return await interaction.followup.send(f"{target_user.mention} has not linked a Roblox account.", ephemeral=True)
        roblox_id = record.get('roblox_id')
        roblox_username = record.get('roblox_username', 'Unknown')
        # attempt to use the Verification cog to fetch groups
        verification_cog = interaction.client.get_cog('Verification')
        if verification_cog is None:
            return await interaction.followup.send("Verification cog not loaded.", ephemeral=True)
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
            return await interaction.followup.send(f"Could not find {target_user.mention} in this guild.", ephemeral=True)
        
        # Update server nickname to Roblox username
        try:
            await member.edit(nick=roblox_username)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to change nicknames
        
        added = []
        removed = []
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
                if role_obj not in member.roles:
                    await member.add_roles(role_obj)
                    added.append(role_obj.mention)
            else:
                print(f"[DEBUG] Removing role {role_obj.name} from user (if present).")
                if role_obj in member.roles:
                    await member.remove_roles(role_obj)
                    removed.append(role_obj.mention)
        
        # Build success embed
        roblox_profile_url = f"https://www.roblox.com/users/{roblox_id}/profile"
        avatar_url = f"https://rbxavatar.unnamed.games/avatar-headshot?userName={roblox_username}"
        success_embed = Embed(
            title="Successfully updated user roles",
            description=f"Successfully updated user roles for {roblox_username} ({member.mention})",
            color=discord.Color.dark_blue()
        )
        success_embed.set_thumbnail(url=avatar_url)
        success_embed.add_field(
            name="🙍 Roblox",
            value=f"[{roblox_username}]({roblox_profile_url})",
            inline=False
        )
        
        added_text = "\n".join(added) if added else "None"
        success_embed.add_field(
            name="🆕 Added",
            value=added_text,
            inline=True
        )
        
        removed_text = "\n".join(removed) if removed else "None"
        success_embed.add_field(
            name="🗑️ Removed",
            value=removed_text,
            inline=True
        )
        
        await interaction.followup.send(embed=success_embed, ephemeral=False)
    except Exception as exc:
        print(f"Error in /roles update: {exc}")
        try:
            await interaction.followup.send(f"Error: {exc}", ephemeral=True)
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
