import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import aiohttp
import discord
from discord import Interaction, app_commands
from discord.ext import commands

from cogs.eaa_tracker import (
    TRACKED_GROUP_INFO,
    TRACKED_GROUPS,
    eaa_group_history_col,
    eaa_watchlist_col,
    get_group_link,
    get_group_name,
    get_rank_name,
)
from cogs.verification import verifications_col
from group_ids import GROUP_IDS

PRIMARY_RANK_GROUP_ID = GROUP_IDS.get("SCPF", 34230328)
BGC_REQUEST_PERMISSION_STRING = "Group:SCPF:40-,Group:MaD:249-"
BGC_REVIEW_PERMISSION_STRING = "Group:EAA"
BGC_FORUM_CHANNEL_ID = 1492034349489455185

eaa_group = app_commands.Group(name="eaa", description="Background checking tools")


def _parse_roblox_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_date(value: datetime | None) -> str:
    if not isinstance(value, datetime):
        return "Unknown"
    return value.astimezone(timezone.utc).strftime("%d/%m/%Y")


def _truncate_lines(lines: list[str], limit: int = 10) -> list[str]:
    if len(lines) <= limit:
        return lines
    extra = len(lines) - limit
    return [*lines[:limit], f"> - ... and {extra} more"]


def _terminal_footer() -> str:
    return f"Sierra Terminal • {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}"


async def _get_actor_identity(discord_id: int, fallback_name: str) -> dict[str, Any]:
    record = await verifications_col.find_one(
        {
            "$or": [
                {"discord_id": discord_id},
                {"discord.id": str(discord_id)},
            ]
        }
    )
    roblox = record.get("roblox", {}) if record else {}
    roblox_id = (record.get("roblox_id") or roblox.get("sub") or roblox.get("id")) if record else None
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
        "record": record,
        "roblox_id": roblox_id,
        "roblox_username": roblox_username,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
    }


async def _build_actor_embed(
    interaction: Interaction,
    discord_id: int,
    fallback_name: str,
    title: str,
    description: str,
    *,
    color: discord.Color | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color or discord.Color.dark_blue(),
    )
    actor = await _get_actor_identity(discord_id, fallback_name)

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

    bot_user = interaction.client.user if interaction.client else None
    icon_url = bot_user.display_avatar.url if bot_user else None
    embed.set_footer(text=_terminal_footer(), icon_url=icon_url)
    return embed


async def _send_interaction_embed(interaction: Interaction, embed: discord.Embed, *, ephemeral: bool = True):
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def _ensure_permission(interaction: Interaction, permission_string: str) -> bool:
    verification_cog = interaction.client.get_cog("Verification") if interaction.client else None
    if verification_cog is None:
        embed = discord.Embed(
            title="❗ Error while running command",
            description="The verification system is currently unavailable. Please try again later.",
            color=discord.Color.red(),
        )
        bot_user = interaction.client.user if interaction.client else None
        embed.set_footer(text=_terminal_footer(), icon_url=bot_user.display_avatar.url if bot_user else None)
        await _send_interaction_embed(interaction, embed, ephemeral=True)
        return False

    has_access, perm_text, not_verified = await verification_cog.check_permission(interaction, permission_string)
    if has_access:
        return True

    if not_verified:
        embed = discord.Embed(
            title="🚫 Not Verified",
            description="You are not verified! Please type `/verify` to begin the verification process.",
            color=discord.Color.red(),
        )
    else:
        embed = discord.Embed(
            title="🚫 Invalid Access",
            description="You do not have proper authorization to use this command.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Permissions", value=perm_text or permission_string, inline=False)

    bot_user = interaction.client.user if interaction.client else None
    embed.set_footer(text=_terminal_footer(), icon_url=bot_user.display_avatar.url if bot_user else None)
    await _send_interaction_embed(interaction, embed, ephemeral=True)
    return False


def _extract_verified_target(record: dict[str, Any]) -> dict[str, Any]:
    roblox = record.get("roblox", {}) if record else {}
    discord_info = record.get("discord", {}) if record else {}

    roblox_id = record.get("roblox_id") or roblox.get("id") or roblox.get("sub") if record else None
    discord_id = record.get("discord_id") or discord_info.get("id") if record else None

    try:
        roblox_id = int(roblox_id) if roblox_id is not None else None
    except (TypeError, ValueError):
        roblox_id = None

    try:
        discord_id = int(discord_id) if discord_id is not None else None
    except (TypeError, ValueError):
        discord_id = None

    roblox_username = (
        roblox.get("preferred_username")
        or roblox.get("username")
        or roblox.get("name")
        or record.get("roblox_username")
        or "Unknown"
    )
    discord_username = (
        record.get("discord_username")
        or discord_info.get("global_name")
        or discord_info.get("username")
        or "Unknown"
    )
    profile_url = f"https://www.roblox.com/users/{roblox_id}/profile" if roblox_id is not None else "Unknown"

    return {
        "roblox_username": roblox_username,
        "roblox_id": roblox_id,
        "discord_username": discord_username,
        "discord_id": discord_id,
        "profile_url": profile_url,
    }


async def _fetch_verified_target_record(roblox_username: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        resolved = await _resolve_roblox_user(session, roblox_username.strip())

    if not resolved:
        return None, None

    roblox_id = int(resolved["id"])
    record = await verifications_col.find_one(
        {
            "$or": [
                {"roblox_id": roblox_id},
                {"roblox_id": str(roblox_id)},
                {"roblox.id": roblox_id},
                {"roblox.id": str(roblox_id)},
                {"roblox.sub": roblox_id},
                {"roblox.sub": str(roblox_id)},
            ]
        }
    )
    return record, resolved


async def _fetch_requester_user(client: discord.Client, requester_id: int):
    user = client.get_user(requester_id)
    if user is not None:
        return user
    try:
        return await client.fetch_user(requester_id)
    except Exception as exc:
        print(f"[EAA] failed to fetch requester {requester_id}: {exc}")
        return None


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    method: str = "GET",
    **kwargs,
) -> dict[str, Any]:
    try:
        async with session.request(method, url, **kwargs) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}
    except Exception as exc:
        print(f"[EAA] request failed for {url}: {exc}")
        return {}


async def _resolve_roblox_user(session: aiohttp.ClientSession, username: str) -> dict[str, Any] | None:
    payload = {"usernames": [username], "excludeBannedUsers": False}
    data = await _fetch_json(
        session,
        "https://users.roblox.com/v1/usernames/users",
        method="POST",
        json=payload,
    )
    entries = data.get("data", [])
    return entries[0] if entries else None


async def _fetch_badge_count(session: aiohttp.ClientSession, user_id: int) -> str:
    total = 0
    cursor = None

    for _ in range(5):
        params = {"limit": 100, "sortOrder": "Desc"}
        if cursor:
            params["cursor"] = cursor
        data = await _fetch_json(
            session,
            f"https://badges.roblox.com/v1/users/{user_id}/badges",
            params=params,
        )
        entries = data.get("data", [])
        total += len(entries)
        cursor = data.get("nextPageCursor")
        if not cursor:
            return str(total)

    return f"{total}+"


async def _fetch_avatar_url(session: aiohttp.ClientSession, user_id: int) -> str:
    data = await _fetch_json(
        session,
        "https://thumbnails.roblox.com/v1/users/avatar-headshot",
        params={
            "userIds": str(user_id),
            "size": "150x150",
            "format": "Png",
            "isCircular": "false",
        },
    )
    image_url = (((data.get("data") or [{}])[0]).get("imageUrl"))
    if image_url:
        return image_url
    return f"https://www.roblox.com/headshot-thumbnail/image?userId={user_id}&width=150&height=150&format=png"


class EAAActionSelect(discord.ui.Select):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        username = payload["username"]
        options = [
            discord.SelectOption(
                label=f"{username}'s Groups",
                description=f"Background checking of {username}'s groups",
                value="groups",
            ),
            discord.SelectOption(
                label=f"{username}'s Friends",
                description=f"Background checking of {username}'s friends",
                value="friends",
            ),
        ]
        super().__init__(
            placeholder="Make a selection",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: Interaction):
        if interaction.user.id != self.payload["requester_id"]:
            await interaction.response.send_message(
                "Only the user who ran this background check can use this menu.",
                ephemeral=True,
            )
            return

        if self.values[0] == "groups":
            embed = discord.Embed(
                title=f"{self.payload['username']}'s Groups",
                description="\n".join(self.payload["department_membership_lines"]),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Tracked departments found: {self.payload['department_entry_count']}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{self.payload['username']}'s Friends",
            description="\n".join(self.payload["friend_lines"]),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Showing up to {self.payload['friend_preview_count']} friend(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EAAMoreDepartmentsButton(discord.ui.Button):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(label="More Departments", emoji="📂", style=discord.ButtonStyle.secondary)
        self.payload = payload

    async def callback(self, interaction: Interaction):
        if interaction.user.id != self.payload["requester_id"]:
            await interaction.response.send_message(
                "Only the user who ran this background check can use this button.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{self.payload['username']}'s Department Membership",
            description="\n".join(self.payload["department_membership_lines"]),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Tracked departments found: {self.payload['department_entry_count']}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EAACheckView(discord.ui.LayoutView):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(timeout=900)

        container = discord.ui.Container(
            discord.ui.TextDisplay(f"Subject `{payload['username']}`"),
            discord.ui.Section(
                discord.ui.TextDisplay(f"## 👤 [{payload['username']}]({payload['profile_url']})"),
                discord.ui.TextDisplay(f"## 📅 <t:{payload['created_ts']}:D>"),
                accessory=discord.ui.Thumbnail(
                    payload["avatar_url"],
                    description=f"{payload['username']}'s avatar",
                ),
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(payload["general_block"]),
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="Roblox Profile",
                    emoji="🪪",
                    style=discord.ButtonStyle.link,
                    url=payload["profile_url"],
                )
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(payload["previous_block"]),
            discord.ui.Separator(),
            discord.ui.TextDisplay(payload["department_block"]),
            discord.ui.Separator(),
            discord.ui.TextDisplay(payload["misc_block"]),
            discord.ui.ActionRow(EAAActionSelect(payload)),
            discord.ui.ActionRow(EAAMoreDepartmentsButton(payload)),
            discord.ui.TextDisplay(f"-# EAA Background Check • <t:{payload['requested_ts']}:R>"),
            accent_colour=discord.Colour.blurple(),
        )
        self.add_item(container)


class BGCAcceptConfirmView(discord.ui.View):
    def __init__(self, panel_view: "BGCReviewPanelView", request_data: dict[str, Any], reviewer_id: int):
        super().__init__(timeout=300)
        self.panel_view = panel_view
        self.request_data = request_data
        self.reviewer_id = reviewer_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message("This confirmation prompt is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm_yes(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        requester = await _fetch_requester_user(interaction.client, self.request_data["requester_discord_id"])
        if requester is not None:
            try:
                embed = await _build_actor_embed(
                    interaction,
                    interaction.user.id,
                    str(interaction.user),
                    "🟢 Background Check Accepted",
                    f"Your background check for {self.request_data['subject_username']} has been reviewed and accepted by the Executive Assurance Administration.",
                    color=discord.Color.green(),
                )
                await requester.send(embed=embed)
            except Exception as exc:
                print(f"[EAA] failed to DM acceptance notice: {exc}")

        await self.panel_view.close_panel(interaction, f"🟢 Accepted by {interaction.user.mention}")
        if self.message is not None:
            await self.message.delete()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def confirm_no(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.message is not None:
            await self.message.delete()


class BGCDenyReasonModal(discord.ui.Modal, title="Reason for Denial"):
    reason = discord.ui.TextInput(
        label="Reason for denial",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(
        self,
        panel_view: "BGCReviewPanelView",
        request_data: dict[str, Any],
        reviewer_id: int,
        confirm_message: discord.Message | None,
    ):
        super().__init__()
        self.panel_view = panel_view
        self.request_data = request_data
        self.reviewer_id = reviewer_id
        self.confirm_message = confirm_message

    async def on_submit(self, interaction: Interaction):
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message("This denial form is not for you.", ephemeral=True)
            return

        requester = await _fetch_requester_user(interaction.client, self.request_data["requester_discord_id"])
        if requester is not None:
            try:
                embed = await _build_actor_embed(
                    interaction,
                    interaction.user.id,
                    str(interaction.user),
                    "🔴 Background Check Denied",
                    f"Your background check for {self.request_data['subject_username']} has been reviewed and denied by the Executive Assurance Administration.",
                    color=discord.Color.red(),
                )
                embed.add_field(name="✍️ Reason", value=self.reason.value, inline=False)
                await requester.send(embed=embed)
            except Exception as exc:
                print(f"[EAA] failed to DM denial notice: {exc}")

        await self.panel_view.close_panel(interaction, f"🔴 Denied by {interaction.user.mention}")
        await interaction.response.send_message("Background check denied and requester notified.", ephemeral=True)
        if self.confirm_message is not None:
            try:
                await self.confirm_message.delete()
            except Exception:
                pass


class BGCDenyConfirmView(discord.ui.View):
    def __init__(self, panel_view: "BGCReviewPanelView", request_data: dict[str, Any], reviewer_id: int):
        super().__init__(timeout=300)
        self.panel_view = panel_view
        self.request_data = request_data
        self.reviewer_id = reviewer_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.reviewer_id:
            await interaction.response.send_message("This confirmation prompt is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm_yes(self, interaction: Interaction, button: discord.ui.Button):
        modal = BGCDenyReasonModal(self.panel_view, self.request_data, self.reviewer_id, self.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def confirm_no(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.message is not None:
            await self.message.delete()


class BGCReviewPanelView(discord.ui.View):
    def __init__(self, request_data: dict[str, Any]):
        super().__init__(timeout=None)
        self.request_data = request_data
        self.message: discord.Message | None = None

    async def close_panel(self, interaction: Interaction, status_text: str):
        for child in self.children:
            child.disabled = True

        if self.message is not None and self.message.embeds:
            embed = self.message.embeds[0].copy()
            embed.description = f"{embed.description}\n\n> **Status:** {status_text}"
            await self.message.edit(embed=embed, view=self)

        thread = self.message.channel if self.message is not None else interaction.channel
        if isinstance(thread, discord.Thread):
            try:
                await thread.send("## Background check completed. Post has been locked and stored.")
            except Exception as exc:
                print(f"[EAA] failed to send completion message: {exc}")

            try:
                await thread.edit(locked=True, archived=True)
            except Exception as exc:
                print(f"[EAA] failed to lock/archive thread: {exc}")

    async def _send_warning_reply(self, interaction: Interaction, description: str, view: discord.ui.View):
        warning_embed = await _build_actor_embed(
            interaction,
            interaction.user.id,
            str(interaction.user),
            "⚠️ Warning",
            description,
            color=discord.Color.gold(),
        )
        await interaction.response.defer()
        reply_message = await interaction.channel.send(
            embed=warning_embed,
            view=view,
            reference=interaction.message.to_reference(),
            mention_author=False,
        )
        view.message = reply_message

    @discord.ui.button(label="ACCEPT", emoji="🟢", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: Interaction, button: discord.ui.Button):
        if not await _ensure_permission(interaction, BGC_REVIEW_PERMISSION_STRING):
            return

        description = (
            f"Are you sure you want to accept `{self.request_data['subject_username']}`'s `({self.request_data['subject_roblox_id']})` background check?"
        )
        await self._send_warning_reply(
            interaction,
            description,
            BGCAcceptConfirmView(self, self.request_data, interaction.user.id),
        )

    @discord.ui.button(label="DENY", emoji="🔴", style=discord.ButtonStyle.red)
    async def deny_button(self, interaction: Interaction, button: discord.ui.Button):
        if not await _ensure_permission(interaction, BGC_REVIEW_PERMISSION_STRING):
            return

        description = (
            f"Are you sure you want to deny `{self.request_data['subject_username']}`'s `({self.request_data['subject_roblox_id']})` background check?"
        )
        await self._send_warning_reply(
            interaction,
            description,
            BGCDenyConfirmView(self, self.request_data, interaction.user.id),
        )


async def _build_payload(roblox_username: str, requester_id: int) -> dict[str, Any] | None:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        resolved_user = await _resolve_roblox_user(session, roblox_username)
        if not resolved_user:
            return None

        user_id = int(resolved_user["id"])
        canonical_username = resolved_user.get("name") or roblox_username
        profile_url = f"https://www.roblox.com/users/{user_id}/profile"

        profile_task = _fetch_json(session, f"https://users.roblox.com/v1/users/{user_id}")
        groups_task = _fetch_json(session, f"https://groups.roblox.com/v1/users/{user_id}/groups/roles")
        friends_count_task = _fetch_json(session, f"https://friends.roblox.com/v1/users/{user_id}/friends/count")
        friends_preview_task = _fetch_json(
            session,
            f"https://friends.roblox.com/v1/users/{user_id}/friends",
            params={"limit": 10},
        )
        username_history_task = _fetch_json(
            session,
            f"https://users.roblox.com/v1/users/{user_id}/username-history",
            params={"limit": 10, "sortOrder": "Desc"},
        )
        avatar_task = _fetch_avatar_url(session, user_id)
        badge_count_task = _fetch_badge_count(session, user_id)

        (
            profile_data,
            groups_data,
            friends_count_data,
            friends_preview_data,
            username_history_data,
            avatar_url,
            badge_count,
        ) = await asyncio.gather(
            profile_task,
            groups_task,
            friends_count_task,
            friends_preview_task,
            username_history_task,
            avatar_task,
            badge_count_task,
        )

    created_at = _parse_roblox_datetime(profile_data.get("created"))
    created_ts = int(created_at.timestamp()) if created_at else int(datetime.now(timezone.utc).timestamp())
    requested_ts = int(datetime.now(timezone.utc).timestamp())

    all_groups = groups_data.get("data", [])
    group_count = len(all_groups)
    current_rank = "Guest"
    current_memberships: dict[int, dict[str, Any]] = {}

    for entry in all_groups:
        group = entry.get("group", {})
        role = entry.get("role", {})
        group_id = group.get("id")
        if not group_id:
            continue

        group_id = int(group_id)
        if group_id == PRIMARY_RANK_GROUP_ID:
            current_rank = get_rank_name(role.get("name"), role.get("rank"))

        if group_id in TRACKED_GROUPS:
            current_memberships[group_id] = {
                "group_name": get_group_name(group_id, group.get("name")),
                "rank_name": get_rank_name(role.get("name"), role.get("rank")),
            }

    previous_usernames = [
        item.get("name") or item.get("username")
        for item in username_history_data.get("data", [])
        if item.get("name") or item.get("username")
    ]
    previous_usernames = [name for name in previous_usernames if name != canonical_username]

    try:
        await eaa_watchlist_col.update_one(
            {"roblox_id": user_id},
            {
                "$set": {
                    "roblox_id": user_id,
                    "roblox_username": canonical_username,
                    "last_checked_at": datetime.now(timezone.utc),
                    "checked_by_discord_id": requester_id,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        print(f"[EAA] failed to update watchlist: {exc}")

    former_history: list[dict[str, Any]] = []
    open_history_docs: dict[int, dict[str, Any]] = {}
    try:
        former_history = [
            doc
            async for doc in eaa_group_history_col.find(
                {
                    "roblox_id": user_id,
                    "group_id": {"$in": list(TRACKED_GROUPS)},
                    "left_at": {"$ne": None},
                }
            ).sort("left_at", -1).limit(10)
        ]
        open_history_docs = {
            doc["group_id"]: doc
            async for doc in eaa_group_history_col.find(
                {
                    "roblox_id": user_id,
                    "group_id": {"$in": list(TRACKED_GROUPS)},
                    "left_at": None,
                }
            )
        }
    except Exception as exc:
        print(f"[EAA] failed to load group history: {exc}")

    current_group_lines = []
    for group_id, membership in sorted(current_memberships.items(), key=lambda item: item[1]["group_name"].lower()):
        open_record = open_history_docs.get(group_id, {})
        joined_text = _format_date(open_record.get("joined_at"))
        current_group_lines.append(
            f"> **__Current__** `{membership['rank_name']}` - [{membership['group_name']}]({get_group_link(group_id)}) - {joined_text} (Joined)"
        )

    former_group_lines = []
    for record in former_history:
        group_id = int(record.get("group_id"))
        group_name = get_group_name(group_id, record.get("group_name"))
        rank_name = get_rank_name(record.get("rank_name"), record.get("rank_id"))
        joined_text = _format_date(record.get("joined_at"))
        left_text = _format_date(record.get("left_at"))
        former_group_lines.append(
            f"> **__Former__** `{rank_name}` - [{group_name}]({get_group_link(group_id)}) - {joined_text} (Joined) - {left_text} (Left)"
        )

    all_department_membership_lines = [*current_group_lines, *former_group_lines]
    department_membership_lines = _truncate_lines(all_department_membership_lines, limit=6) or [
        "> No tracked department history recorded."
    ]

    friends_count = friends_count_data.get("count", 0)
    friend_preview = friends_preview_data.get("data", [])[:10]
    friend_lines = [
        f"> - [{friend.get('displayName') or friend.get('name', 'Unknown')}]"
        f"(https://www.roblox.com/users/{friend.get('id')}/profile) (`{friend.get('name', 'Unknown')}`)"
        for friend in friend_preview
        if friend.get("id")
    ]
    if not friend_lines:
        friend_lines = ["> - No friends preview could be loaded."]

    previous_username_lines = [f"> - {name}" for name in previous_usernames] or ["> - No previous usernames recorded."]

    general_block = (
        "### General Information\n"
        f"> - **Roblox Connections:** {friends_count}\n"
        f"> - **Badge Count:** {badge_count}\n"
        f"> - **Group Count:** {group_count}\n"
        f"> - **Account Created:** <t:{created_ts}:F>"
    )
    previous_block = (
        f"### 📝 Previous Usernames ({len(previous_usernames)})\n"
        + "\n".join(_truncate_lines(previous_username_lines))
    )
    department_block = (
        "### 🏛️ Department Membership\n"
        + "\n".join(department_membership_lines)
    )
    misc_block = (
        "### ❓ Miscellaneous\n"
        f"> - **Current Username:** {canonical_username}\n"
        f"> - **Current Rank:** {current_rank}"
    )

    return {
        "requester_id": requester_id,
        "user_id": user_id,
        "username": canonical_username,
        "profile_url": profile_url,
        "avatar_url": avatar_url,
        "created_ts": created_ts,
        "requested_ts": requested_ts,
        "general_block": general_block,
        "previous_block": previous_block,
        "department_block": department_block,
        "misc_block": misc_block,
        "current_group_lines": current_group_lines,
        "former_group_lines": former_group_lines,
        "department_membership_lines": all_department_membership_lines or ["> No tracked department history recorded."],
        "friend_lines": _truncate_lines(friend_lines),
        "friend_preview_count": len(friend_preview),
        "tracked_group_count": len(current_memberships),
        "department_entry_count": len(all_department_membership_lines),
    }


@eaa_group.command(name="bgcrequest")
@app_commands.describe(
    roblox_username="The Roblox username to request a background check for",
    reason="The reason for requesting the background check",
)
async def eaa_bgcrequest(interaction: Interaction, roblox_username: str, reason: str):
    """Request an EAA background check and create a forum post for review."""
    if not await _ensure_permission(interaction, BGC_REQUEST_PERMISSION_STRING):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    verified_record, resolved_user = await _fetch_verified_target_record(roblox_username)
    if not verified_record or not resolved_user:
        embed = await _build_actor_embed(
            interaction,
            interaction.user.id,
            str(interaction.user),
            "❗ User not verified",
            "The user you are requesting a background check for is not verified! Please ask them to verify as soon as possible or contact Terminal Staff to get them manually verified.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    forum_channel = interaction.client.get_channel(BGC_FORUM_CHANNEL_ID)
    if not isinstance(forum_channel, discord.ForumChannel):
        embed = await _build_actor_embed(
            interaction,
            interaction.user.id,
            str(interaction.user),
            "❗ Error while running command",
            "The background check forum channel could not be found.",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    target = _extract_verified_target(verified_record)
    subject_username = resolved_user.get("name") or target["roblox_username"] or roblox_username.strip()
    subject_roblox_id = int(resolved_user["id"])
    subject_profile_url = f"https://www.roblox.com/users/{subject_roblox_id}/profile"

    thread_content = (
        f"-# Requester: {interaction.user.mention}\n\n"
        f"# Basic Information\n"
        f"> **Roblox Username:** {subject_username}\n"
        f"> **Roblox ID:** {subject_roblox_id}\n"
        f"> **Roblox Profile Link:** {subject_profile_url}\n"
        f"> **Discord Username:** {target['discord_username']}\n"
        f"> **Discord ID:** {target['discord_id'] or 'Unknown'}\n\n"
        f"> **Reason for Check:** {reason}"
    )

    request_data = {
        "requester_discord_id": interaction.user.id,
        "requester_discord_name": str(interaction.user),
        "subject_username": subject_username,
        "subject_roblox_id": subject_roblox_id,
        "subject_profile_url": subject_profile_url,
        "target_discord_username": target["discord_username"],
        "target_discord_id": target["discord_id"],
        "reason": reason,
    }

    try:
        thread_result = await forum_channel.create_thread(
            name=subject_username,
            content=thread_content,
        )
        thread = thread_result.thread
    except Exception as exc:
        print(f"[EAA] failed to create BGC forum post: {exc}")
        embed = await _build_actor_embed(
            interaction,
            interaction.user.id,
            str(interaction.user),
            "❗ Error while running command",
            f"The forum post could not be created.\n```{exc}```",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    panel_embed = await _build_actor_embed(
        interaction,
        interaction.user.id,
        str(interaction.user),
        "❗ Background Check Panel",
        "Use this to accept or deny the background check. A proper reason is required if you select deny to send to the requester of the check.\n-# **Do not click anything on this panel unless the background check has been fully completed.",
        color=discord.Color.dark_blue(),
    )
    panel_view = BGCReviewPanelView(request_data)
    panel_message = await thread.send(embed=panel_embed, view=panel_view)
    panel_view.message = panel_message
    try:
        await panel_message.pin()
    except Exception as exc:
        print(f"[EAA] failed to pin BGC panel message: {exc}")

    success_embed = await _build_actor_embed(
        interaction,
        interaction.user.id,
        str(interaction.user),
        "❗ EAA Background Check Requested",
        (
            f"You have successfully requested a background check for `{subject_username}` with reason: `{reason}`\n\n"
            "EAA Background Checks can take up to 3 weeks to fully complete. Do not contact EAA Investigators to begin your background check. You will be ignored."
        ),
        color=discord.Color.dark_blue(),
    )
    await interaction.followup.send(embed=success_embed, ephemeral=True)


@eaa_group.command(name="check")
@app_commands.describe(roblox_username="Roblox username to background check")
async def eaa_check(interaction: Interaction, roblox_username: str):
    """Background check a Roblox profile for EAA review."""
    await interaction.response.defer(thinking=True)

    try:
        payload = await _build_payload(roblox_username.strip(), interaction.user.id)
        if not payload:
            await interaction.edit_original_response(
                content=f"Could not find a Roblox account named `{roblox_username}`.",
                embed=None,
                view=None,
            )
            return

        view = EAACheckView(payload)
        await interaction.edit_original_response(content=None, embed=None, attachments=[], view=view)
    except Exception as exc:
        print(f"Error in /eaa check: {exc}")
        await interaction.edit_original_response(content=f"Error: {exc}", embed=None, view=None)


async def setup(bot: commands.Bot):
    if bot.tree.get_command("eaa") is None:
        bot.tree.add_command(eaa_group)

    async def _sync_eaa():
        try:
            await bot.tree.sync()
            print("Synchronized /eaa commands")
        except Exception as exc:
            print(f"Error syncing /eaa commands: {exc}")

    if bot.is_ready():
        await _sync_eaa()
    else:
        bot.add_listener(_sync_eaa, "on_ready")
