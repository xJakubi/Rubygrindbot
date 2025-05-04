import matplotlib
matplotlib.use('Agg')
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import datetime
import time
from typing import Dict, List, Optional, Tuple, Union
from azure.cosmos import CosmosClient, PartitionKey, exceptions
import os
import math
from collections import defaultdict
import functools

# Module metadata for bot integration
DISPLAY_NAME = "Experience System"
DESCRIPTION = "Track user activity with XP, levels, roles and leaderboards"
ENABLED_BY_DEFAULT = False

# Constants for the XP system
XP_PER_MESSAGE = 1
XP_PER_MINUTE_VOICE = 0.5
XP_DECAY_AMOUNT = 25
XP_DECAY_HOURS = 5

# Level thresholds - XP needed for each level (10 levels total)
LEVEL_THRESHOLDS = [
    0,      # Level 1: 0 XP
    100,    # Level 2: 100 XP
    250,    # Level 3: 250 XP
    500,    # Level 4: 500 XP
    1000,   # Level 5: 1000 XP
    2000,   # Level 6: 2000 XP
    3500,   # Level 7: 3500 XP
    5500,   # Level 8: 5500 XP
    8000,   # Level 9: 8000 XP
    12000   # Level 10: 12000 XP
]

# Role colors for each level - from light to dark colors
LEVEL_COLORS = [
    0xADD8E6,  # Light Blue
    0x90EE90,  # Light Green
    0xFFA500,  # Orange
    0xFFD700,  # Gold
    0xDA70D6,  # Orchid
    0x20B2AA,  # Light Sea Green
    0x8A2BE2,  # Blue Violet
    0xDC143C,  # Crimson
    0x4169E1,  # Royal Blue
    0x800080   # Purple
]

# Database connection constants
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE") or "thefinalsdb"
COSMOS_CONTAINER = "exp_system"

# Cosmos DB Client initialization
try:
    cosmos_client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
    database = cosmos_client.create_database_if_not_exists(id=COSMOS_DATABASE)
    container = database.create_container_if_not_exists(
        id=COSMOS_CONTAINER,
        partition_key=PartitionKey(path="/guild_id"),
        offer_throughput=400
    )
    print("Connected to Cosmos DB successfully")
except Exception as e:
    print(f"Error initializing Cosmos DB: {e}")
    cosmos_client = None
    database = None
    container = None

# In-memory cache to reduce database calls
xp_cache = {}
voice_tracker = {}
last_message_time = {}
task_initialized = False

# Helper function for feature check
def feature_check(bot, interaction, feature_name="expsystem"):
    """Check if the XP system is enabled for this guild"""
    if interaction.guild is None:
        return False  # Don't work in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Database operations with caching
async def get_user_xp(guild_id: int, user_id: int) -> dict:
    """Get user XP data from cache or database"""
    cache_key = f"{guild_id}:{user_id}"
    
    # Check cache first
    if cache_key in xp_cache:
        return xp_cache[cache_key]
        
    # Not in cache, try database
    try:
        if container is None:
            raise Exception("Database not available")
            
        item_id = f"user_{user_id}"
        
        try:
            item = await asyncio.to_thread(
                container.read_item,
                item=item_id,
                partition_key=str(guild_id)
            )
        except exceptions.CosmosResourceNotFoundError:
            # Create default record if not found
            current_time = int(time.time())
            item = {
                "id": item_id,
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "xp": 0,
                "last_updated": current_time,
                "history": {
                    "daily": [],  # Daily XP history (last 30 days)
                    "hourly": []  # Hourly XP history (last 24 hours)
                }
            }
            item = await asyncio.to_thread(container.create_item, body=item)
        
        # Cache the result
        xp_cache[cache_key] = item
        return item
        
    except Exception as e:
        print(f"Error fetching user XP data: {e}")
        # Return a default object if database fails
        current_time = int(time.time())
        return {
            "id": f"user_{user_id}",
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "xp": 0,
            "last_updated": current_time,
            "history": {
                "daily": [],
                "hourly": []
            }
        }

async def update_user_xp(guild_id: int, user_id: int, xp_change: int) -> dict:
    """Update a user's XP and history in the database"""
    try:
        # Get current data
        user_data = await get_user_xp(guild_id, user_id)
        current_time = int(time.time())
        
        # Update XP value
        current_xp = user_data.get("xp", 0)
        new_xp = max(0, current_xp + xp_change)  # Prevent negative XP
        user_data["xp"] = new_xp
        
        # Update history
        history = user_data.get("history", {"daily": [], "hourly": []})
        
        # Update hourly history (keep 24 entries max)
        current_hour = current_time // 3600
        hourly = history.get("hourly", [])
        
        # Find or create current hour record
        hour_updated = False
        for entry in hourly:
            if entry.get("hour") == current_hour:
                entry["xp"] += xp_change
                hour_updated = True
                break
                
        if not hour_updated:
            hourly.append({"hour": current_hour, "xp": xp_change})
            
        # Keep only last 24 hours
        history["hourly"] = sorted(hourly, key=lambda x: x["hour"], reverse=True)[:24]
        
        # Update daily history (keep 30 entries max)
        current_day = current_time // 86400
        daily = history.get("daily", [])
        
        # Find or create current day record
        day_updated = False
        for entry in daily:
            if entry.get("day") == current_day:
                entry["xp"] += xp_change
                day_updated = True
                break
                
        if not day_updated:
            daily.append({"day": current_day, "xp": xp_change})
            
        # Keep only last 30 days
        history["daily"] = sorted(daily, key=lambda x: x["day"], reverse=True)[:30]
        
        # Update timestamp
        user_data["last_updated"] = current_time
        user_data["history"] = history
        
        # Update database
        if container is not None:
            await asyncio.to_thread(container.upsert_item, body=user_data)
            
        # Update cache
        cache_key = f"{guild_id}:{user_id}"
        xp_cache[cache_key] = user_data
        
        return user_data
        
    except Exception as e:
        print(f"Error updating user XP: {e}")
        return None

async def get_leaderboard(guild_id: int, limit: int = 10) -> List[dict]:
    """Get the guild's XP leaderboard"""
    try:
        if container is None:
            return []
            
        # Query users from this guild, sorted by XP
        query = f"SELECT * FROM c WHERE c.guild_id = '{guild_id}' AND STARTSWITH(c.id, 'user_') ORDER BY c.xp DESC OFFSET 0 LIMIT {limit}"
        
        items = []
        async for item in container.query_items(
            query=query,
            enable_cross_partition_query=True
        ):
            items.append(item)
            
        return items
        
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
        return []

async def ensure_level_roles(guild: discord.Guild) -> Dict[int, discord.Role]:
    """Ensure all level roles exist in the guild and return them"""
    level_roles = {}
    
    for i, color in enumerate(LEVEL_COLORS, 1):
        role_name = f"Level {i}"
        role = discord.utils.get(guild.roles, name=role_name)
        
        if role is None:
            try:
                # Create the role if it doesn't exist
                role = await guild.create_role(
                    name=role_name,
                    color=discord.Color(color),
                    reason="Experience system level role"
                )
                print(f"Created role {role_name} in {guild.name}")
            except Exception as e:
                print(f"Failed to create role {role_name} in {guild.name}: {e}")
                continue
                
        level_roles[i] = role
        
    return level_roles

def calculate_level(xp: int) -> int:
    """Calculate level based on XP"""
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp < threshold:
            return i  # Return the previous level
        if i == len(LEVEL_THRESHOLDS) - 1:
            return i + 1  # Max level
    return 1  # Minimum level is 1

def calculate_next_level_xp(xp: int) -> Tuple[int, int]:
    """Return the XP needed for the next level and total XP for next level"""
    level = calculate_level(xp)
    
    if level >= len(LEVEL_THRESHOLDS):
        # Already max level
        return 0, 0
        
    next_level_total_xp = LEVEL_THRESHOLDS[level]
    xp_needed = next_level_total_xp - xp
    
    return xp_needed, next_level_total_xp

async def update_user_roles(member: discord.Member, xp: int) -> None:
    """Update the user's level roles"""
    if not member.guild.me.guild_permissions.manage_roles:
        return  # Bot doesn't have permission
        
    # Get the level roles
    level_roles = await ensure_level_roles(member.guild)
    current_level = calculate_level(xp)
    
    # Get all level roles the user should NOT have
    roles_to_remove = [role for level, role in level_roles.items() if level != current_level]
    
    # Get the role the user SHOULD have
    role_to_add = level_roles.get(current_level)
    
    # Update roles
    try:
        # Add the current level role if it exists and user doesn't have it
        if role_to_add and role_to_add not in member.roles:
            await member.add_roles(role_to_add, reason=f"Experience system level {current_level}")
            
        # Remove other level roles
        roles_to_remove = [r for r in roles_to_remove if r in member.roles]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Experience system level update")
    except Exception as e:
        print(f"Failed to update roles for {member.name} in {member.guild.name}: {e}")



# Task to decay XP
@tasks.loop(hours=XP_DECAY_HOURS)
async def decay_inactive_users(bot):
    """Remove XP from inactive users every 5 hours"""
    print("Running XP decay task...")
    current_time = int(time.time())
    decay_threshold = current_time - (XP_DECAY_HOURS * 3600)
    
    # Get all keys from cache
    for cache_key in list(xp_cache.keys()):
        try:
            guild_id, user_id = map(int, cache_key.split(':'))
            user_data = xp_cache[cache_key]
            
            # Check if user has been inactive
            last_updated = user_data.get("last_updated", current_time)
            if last_updated < decay_threshold:
                # User is inactive, apply decay
                await update_user_xp(guild_id, user_id, -XP_DECAY_AMOUNT)
                print(f"Applied decay to {user_id} in guild {guild_id}")
                
                # Try to update roles if possible
                guild = bot.get_guild(guild_id)
                if guild:
                    member = guild.get_member(user_id)
                    if member:
                        new_xp = user_data.get("xp", 0) - XP_DECAY_AMOUNT
                        await update_user_roles(member, max(0, new_xp))
                        
        except Exception as e:
            print(f"Error decaying XP for cache key {cache_key}: {e}")
            
    # If database is available, check for users not in cache
    if container is not None:
        try:
            query = f"SELECT * FROM c WHERE c.last_updated < {decay_threshold} AND STARTSWITH(c.id, 'user_')"
            
            # Fixed the async iteration issue by using asyncio.to_thread
            query_items = await asyncio.to_thread(
                lambda: list(container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))
            )
            
            for item in query_items:
                try:
                    guild_id = int(item.get("guild_id"))
                    user_id = int(item.get("user_id"))
                    cache_key = f"{guild_id}:{user_id}"
                    
                    # Skip if already processed from cache
                    if cache_key in xp_cache:
                        continue
                        
                    # Apply decay
                    await update_user_xp(guild_id, user_id, -XP_DECAY_AMOUNT)
                    print(f"Applied decay to {user_id} in guild {guild_id} from database")
                    
                    # Try to update roles
                    guild = bot.get_guild(guild_id)
                    if guild:
                        member = guild.get_member(user_id)
                        if member:
                            new_xp = item.get("xp", 0) - XP_DECAY_AMOUNT
                            await update_user_roles(member, max(0, new_xp))
                            
                except Exception as e:
                    print(f"Error processing database decay for user: {e}")
                    
        except Exception as e:
            print(f"Error querying database for XP decay: {e}")

@decay_inactive_users.before_loop
async def before_decay():
    """Wait for bot to be ready before starting decay task"""
    await asyncio.sleep(3600)  # Wait 1 hour before first decay

# Voice state tracking
async def check_voice_activity(bot):
    """Award XP to users in voice channels periodically"""
    try:
        while True:
            current_time = int(time.time())
            
            # Copy to avoid modification during iteration
            voice_states = voice_tracker.copy()
            
            for (guild_id, user_id), started_at in voice_states.items():
                # Calculate minutes since last check
                minutes_active = (current_time - started_at) // 60
                
                if minutes_active > 0:
                    # Award XP based on minutes (5 XP per minute)
                    xp_gain = minutes_active * XP_PER_MINUTE_VOICE
                    
                    # Update tracker with new timestamp for next check
                    voice_tracker[(guild_id, user_id)] = current_time - (current_time - started_at) % 60
                    
                    # Award XP
                    user_data = await update_user_xp(guild_id, user_id, xp_gain)
                    
                    # Update role if user is still in the guild
                    guild = bot.get_guild(guild_id)
                    if guild:
                        member = guild.get_member(user_id)
                        if member:
                            await update_user_roles(member, user_data.get("xp", 0))
                            
            # Sleep for a minute
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        print("Voice activity tracking task cancelled")
    except Exception as e:
        print(f"Error in voice activity tracking: {e}")

# Commands
async def setup(bot: commands.Bot):
    """Set up the XP system"""
    global task_initialized
    
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    if not task_initialized:
        # Start decay task
        decay_inactive_users.start(bot)
        
        # Start voice activity checking task
        bot.loop.create_task(check_voice_activity(bot))
        task_initialized = True
        
    # Message XP
    @bot.listen('on_message')
    async def award_message_xp(message):
        # Skip if feature is disabled
        if message.guild is None or not bot.is_feature_enabled(feature_name, message.guild.id):
            return
            
        # Skip bot messages and system messages
        if message.author.bot or message.type != discord.MessageType.default:
            return
            
        # Rate limiting - only award XP once per minute per user
        current_time = int(time.time())
        rate_limit_key = f"{message.guild.id}:{message.author.id}"
        last_time = last_message_time.get(rate_limit_key, 0)
        
        if current_time - last_time < 60:
            return  # Rate limited
            
        last_message_time[rate_limit_key] = current_time
        
        # Award XP
        user_data = await update_user_xp(message.guild.id, message.author.id, XP_PER_MESSAGE)
        
        # Update roles
        await update_user_roles(message.author, user_data.get("xp", 0))
        
    # Voice XP tracking
    @bot.listen('on_voice_state_update')
    async def track_voice_activity(member, before, after):
        # Skip if feature is disabled
        if member.guild is None or not bot.is_feature_enabled(feature_name, member.guild.id):
            return
            
        # Skip bots
        if member.bot:
            return
            
        guild_id = member.guild.id
        user_id = member.id
        key = (guild_id, user_id)
        current_time = int(time.time())
        
        # User joined a voice channel
        if before.channel is None and after.channel is not None:
            # Don't track AFK channel
            if after.channel != member.guild.afk_channel:
                voice_tracker[key] = current_time
                
        # User left a voice channel
        elif before.channel is not None and after.channel is None:
            if key in voice_tracker:
                started_at = voice_tracker[key]
                del voice_tracker[key]
                
                # Calculate minutes
                minutes_active = (current_time - started_at) // 60
                if minutes_active > 0:
                    # Award XP based on minutes
                    xp_gain = minutes_active * XP_PER_MINUTE_VOICE
                    user_data = await update_user_xp(guild_id, user_id, xp_gain)
                    
                    # Update roles - only if user_data is not None
                    if user_data is not None:
                        await update_user_roles(member, user_data.get("xp", 0))
                    
        # User switched between voice channels
        elif before.channel != after.channel:
            # If moving to/from AFK, handle appropriately
            if before.channel == member.guild.afk_channel:
                # Coming out of AFK - start tracking
                voice_tracker[key] = current_time
            elif after.channel == member.guild.afk_channel:
                # Going into AFK - stop tracking and award XP
                if key in voice_tracker:
                    started_at = voice_tracker[key]
                    del voice_tracker[key]
                    
                    minutes_active = (current_time - started_at) // 60
                    if minutes_active > 0:
                        xp_gain = minutes_active * XP_PER_MINUTE_VOICE
                        user_data = await update_user_xp(guild_id, user_id, xp_gain)
                        # Update roles - only if user_data is not None
                        if user_data is not None:
                            await update_user_roles(member, user_data.get("xp", 0))
    
    # XP and Level command
    @bot.tree.command(name="level", description="Check your XP level and progress")
    async def level_command(interaction: discord.Interaction, user: Optional[discord.Member] = None):
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "The XP system is disabled on this server. An admin can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Default to the command user if no user is specified
        target_user = user or interaction.user
        
        # Get user's XP data
        await interaction.response.defer()
        user_data = await get_user_xp(interaction.guild.id, target_user.id)
        
        # Calculate level information
        xp = user_data.get("xp", 0)
        level = calculate_level(xp)
        xp_needed, next_level_xp = calculate_next_level_xp(xp)
        
        # Create a beautiful embed
        embed = discord.Embed(
            title=f"{target_user.display_name}'s Experience",
            description=f"Level progress and statistics",
            color=LEVEL_COLORS[level - 1] if level <= len(LEVEL_COLORS) else 0x800080
        )
        
        # Add level information
        embed.add_field(
            name="Current Level",
            value=f"**{level}** {get_level_emoji(level)}",
            inline=True
        )
        
        embed.add_field(
            name="Total XP",
            value=f"**{xp}** XP",
            inline=True
        )
        
        # Calculate XP progress for current level
        if level < len(LEVEL_THRESHOLDS):
            current_level_xp = LEVEL_THRESHOLDS[level - 1] if level > 0 else 0
            next_level_xp = LEVEL_THRESHOLDS[level]
            xp_progress = xp - current_level_xp
            xp_needed = next_level_xp - current_level_xp
            
            progress_percent = min(100, int((xp_progress / xp_needed) * 100))
            
            # Create a progress bar
            progress_bar = create_progress_bar(progress_percent)
            
            embed.add_field(
                name="Progress to Next Level",
                value=f"{progress_bar} **{progress_percent}%**\n{xp_progress}/{xp_needed} XP",
                inline=False
            )
            
            embed.add_field(
                name="XP Needed",
                value=f"**{next_level_xp - xp}** XP to reach Level {level+1}",
                inline=True
            )
        else:
            embed.add_field(
                name="Maximum Level",
                value="You've reached the maximum level! 🎉",
                inline=False
            )
        
        # Add XP gain/loss statistics
        history = user_data.get("history", {})
        daily_data = history.get("daily", [])
        hourly_data = history.get("hourly", [])
        
        # Calculate 24-hour XP change
        xp_24h = sum(entry.get("xp", 0) for entry in hourly_data)
        
        # Calculate 7-day XP change (last 7 entries in daily)
        sorted_daily = sorted(daily_data, key=lambda x: x.get("day", 0), reverse=True)
        xp_7d = sum(entry.get("xp", 0) for entry in sorted_daily[:7])
        
        # Calculate 30-day XP change
        xp_30d = sum(entry.get("xp", 0) for entry in sorted_daily)
        
        embed.add_field(
            name="XP Gain/Loss",
            value=f"**24 Hours:** {format_xp_change(xp_24h)}\n"
                f"**7 Days:** {format_xp_change(xp_7d)}\n"
                f"**30 Days:** {format_xp_change(xp_30d)}",
            inline=True
        )
        
        # Add activity information
        voice_key = (interaction.guild.id, target_user.id)
        if voice_key in voice_tracker:
            minutes_active = (int(time.time()) - voice_tracker[voice_key]) // 60
            embed.add_field(
                name="Current Activity",
                value=f"🎤 In voice for {minutes_active} minutes\n"
                    f"Earning {XP_PER_MINUTE_VOICE} XP per minute",
                inline=True
            )
            
        # Add XP decay information
        embed.set_footer(text=f"Users lose {XP_DECAY_AMOUNT} XP every {XP_DECAY_HOURS} hours of inactivity")
        
        # Set user avatar
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        # Send the embed without graph
        await interaction.followup.send(embed=embed)

    # Leaderboard command
    @bot.tree.command(name="leaderboard", description="Show the server's XP leaderboard")
    async def leaderboard_command(interaction: discord.Interaction):
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "The XP system is disabled on this server. An admin can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        await interaction.response.defer()
        
        # Get leaderboard data
        leaderboard_data = await get_leaderboard(interaction.guild.id, 10)
        
        if not leaderboard_data:
            await interaction.followup.send("No XP data found for this server yet!")
            return
            
        # Create embed
        embed = discord.Embed(
            title=f"🏆 XP Leaderboard for {interaction.guild.name}",
            description="Top members by experience points",
            color=0xFFD700  # Gold color
        )
        
        # For position emoji
        position_emojis = {
            0: "🥇",
            1: "🥈",
            2: "🥉"
        }
        
        # Add field for each user
        for i, entry in enumerate(leaderboard_data):
            try:
                user_id = int(entry.get("user_id", 0))
                xp = entry.get("xp", 0)
                level = calculate_level(xp)
                
                # Get member if possible
                member = interaction.guild.get_member(user_id)
                display_name = member.display_name if member else f"User {user_id}"
                
                # Position emoji
                                # Position emoji
                pos_str = position_emojis.get(i, f"#{i+1}")
                
                # Format entry
                embed.add_field(
                    name=f"{pos_str} {display_name}",
                    value=f"Level {level} | {xp} XP",
                    inline=False
                )
            except Exception as e:
                print(f"Error formatting leaderboard entry: {e}")
        
        await interaction.followup.send(embed=embed)

    # Admin command to give XP
    @bot.tree.command(name="give_exp", description="Give XP to a user (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def give_exp_command(interaction: discord.Interaction, user: discord.Member, amount: int):
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "The XP system is disabled on this server. An admin can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Validate input
        if amount == 0:
            await interaction.response.send_message("Amount cannot be zero.", ephemeral=True)
            return
        
        # Update user's XP
        await interaction.response.defer(ephemeral=True)
        user_data = await update_user_xp(interaction.guild.id, user.id, amount)
        
        if user_data:
            # Update user's roles
            new_xp = user_data.get("xp", 0)
            new_level = calculate_level(new_xp)
            await update_user_roles(user, new_xp)
            
            # Send confirmation
            action = "given" if amount > 0 else "removed"
            await interaction.followup.send(
                f"Successfully {action} **{abs(amount)}** XP {'to' if amount > 0 else 'from'} {user.mention}.\n"
                f"They now have **{new_xp}** XP (Level {new_level}).",
                ephemeral=True
            )
        else:
            await interaction.followup.send("Failed to update XP. Please try again.", ephemeral=True)

# Helper functions for UI formatting

def get_level_emoji(level: int) -> str:
    """Get an appropriate emoji for the level"""
    if level == 10:
        return "👑"  # Crown for max level
    elif level >= 8:
        return "⭐"  # Star for high levels
    elif level >= 5:
        return "🔶"  # Diamond for mid levels
    else:
        return "🔷"  # Blue diamond for low levels

def create_progress_bar(percent: int, length: int = 10) -> str:
    """Create a text-based progress bar"""
    filled = int((percent / 100) * length)
    empty = length - filled
    
    return f"{'🟩' * filled}{'⬜' * empty}"

def format_xp_change(xp_change: int) -> str:
    """Format XP change with color indicators"""
    if xp_change > 0:
        return f"+{xp_change} XP 📈"
    elif xp_change < 0:
        return f"{xp_change} XP 📉"
    else:
        return "0 XP ➖"