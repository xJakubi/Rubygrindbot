import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import asyncio
import os
import json
import time
import io
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter
import numpy as np
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from datetime import datetime, timedelta
import random
from typing import Dict, List, Optional

DISPLAY_NAME = "Moderator Point Tracker"
DESCRIPTION = "Tracks activity points for moderators and generates reports"
ENABLED_BY_DEFAULT = False

# Configuration
MOD_ROLE_NAME = "Moderator"
TARGET_VOICE_CHANNEL_ID = 1348080364282974249
POINTS_CONFIG = {
    "message": 0.5,              # Points per message
    "voice_minute": 10,          # Points per minute in voice channel
    "poll_participate": 5,       # Points for participating in a poll
    "poll_create": 15,           # Points for creating a poll
    "improvement": 5,            # Points for improving from previous week
    "ticket_message": 5,         # Points for messages in support tickets
    "daily_inactivity": -25      # Points deducted for 24h inactivity
}

# Cosmos DB configuration
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE")

# Initialize the Cosmos DB client
try:
    cosmos_client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
    database = cosmos_client.get_database_client(COSMOS_DATABASE)
    # Create container if it doesn't exist
    container = database.create_container_if_not_exists(
        id="mod_points",
        partition_key=PartitionKey(path="/guild_id"),
        offer_throughput=400
    )
except Exception as e:
    print(f"Error initializing Cosmos DB for mod points: {e}")
    cosmos_client = None
    database = None
    container = None

class ModPointTracker:
    def __init__(self, bot):
        self.bot = bot
        self.voice_users = {}  # Track users in voice channels: {user_id: join_timestamp}
        self.last_activity = {}  # Track last activity time: {guild_id: {user_id: timestamp}}
        self.active_today = {}  # Track users who gained points today: {guild_id: {user_id: bool}}

    async def get_or_create_user_record(self, guild_id: int, user_id: int):
        """Get or create a user record in the database"""
        try:
            query = f"SELECT * FROM c WHERE c.guild_id = '{guild_id}' AND c.user_id = '{user_id}'"
            items = list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            if items:
                return items[0]
            else:
                # Create new record
                record = {
                    "id": f"{guild_id}_{user_id}",
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "points_log": [],
                    "last_updated": datetime.utcnow().isoformat()
                }
                container.create_item(body=record)
                return record
        except Exception as e:
            print(f"Error getting/creating user record: {e}")
            return None

    async def add_points(self, guild_id: int, user_id: int, points: float, reason: str):
        """Add points to a user's record"""
        if not self.bot.is_feature_enabled("modtracker", guild_id):
            return

        try:
            # Get current user record
            record = await self.get_or_create_user_record(guild_id, user_id)
            if not record:
                return
                
            # Add new points entry
            now = datetime.utcnow()
            
            # Initialize if points_log doesn't exist
            if "points_log" not in record:
                record["points_log"] = []
                
            # Add the new points entry
            record["points_log"].append({
                "timestamp": now.isoformat(),
                "points": points,
                "reason": reason
            })
            
            # Clean up entries older than 30 days
            thirty_days_ago = (now - timedelta(days=30)).isoformat()
            record["points_log"] = [entry for entry in record["points_log"] 
                                  if entry["timestamp"] >= thirty_days_ago]
            
            record["last_updated"] = now.isoformat()
            
            # Update the document in Cosmos DB
            container.upsert_item(body=record)
            
            # Track user activity for today
            if points > 0:
                if guild_id not in self.active_today:
                    self.active_today[guild_id] = {}
                self.active_today[guild_id][user_id] = True
                
            # Update last activity time
            if guild_id not in self.last_activity:
                self.last_activity[guild_id] = {}
            self.last_activity[guild_id][user_id] = now.timestamp()
            
        except Exception as e:
            print(f"Error adding points: {e}")

    async def get_user_points(self, guild_id: int, user_id: int, days: int = 30):
        """Get points for a user over the last specified days"""
        try:
            record = await self.get_or_create_user_record(guild_id, user_id)
            if not record or "points_log" not in record:
                return []
                
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            return [entry for entry in record["points_log"] if entry["timestamp"] >= cutoff]
        except Exception as e:
            print(f"Error getting user points: {e}")
            return []

    async def get_weekly_points(self, guild_id: int, user_id: int, weeks_ago: int = 0):
        """Get points for a user for a specific week"""
        now = datetime.utcnow()
        week_end = now - timedelta(days=7*weeks_ago)
        week_start = week_end - timedelta(days=7)
        
        points_log = await self.get_user_points(guild_id, user_id)
        weekly_points = []
        
        for entry in points_log:
            entry_time = datetime.fromisoformat(entry["timestamp"])
            if week_start <= entry_time <= week_end:
                weekly_points.append(entry)
                
        return weekly_points
    
    async def get_total_points(self, guild_id: int, user_id: int, days: int = 30):
        """Get total points for a user over the last specified days"""
        points_log = await self.get_user_points(guild_id, user_id, days)
        return sum(entry["points"] for entry in points_log)

    async def get_all_mod_points(self, guild: discord.Guild, days: int = 30):
        """Get points for all moderators in a guild"""
        result = []
        
        # Get the moderator role
        mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        if not mod_role:
            return result
            
        # Get all users with moderator role
        for member in mod_role.members:
            points = await self.get_total_points(guild.id, member.id, days)
            result.append((member, points))
            
        # Sort by points in descending order
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def create_report_image(self, points_data, user_name):
        """Create a graph image from points data"""
        if not points_data:
            return None
            
        # Extract dates and points
        dates = []
        point_values = []
        running_total = []
        total = 0
        
        for entry in sorted(points_data, key=lambda x: x["timestamp"]):
            dates.append(datetime.fromisoformat(entry["timestamp"]))
            point_values.append(entry["points"])
            total += entry["points"]
            running_total.append(total)
            
        # Create figure
        plt.figure(figsize=(10, 6))
        
        # Plot points per action
        plt.subplot(2, 1, 1)
        plt.bar(dates, point_values, width=0.03, color='skyblue')
        plt.title(f'Point Activity for {user_name}')
        plt.ylabel('Points')
        plt.grid(True, alpha=0.3)
        
        # Plot running total
        plt.subplot(2, 1, 2)
        plt.plot(dates, running_total, 'r-')
        plt.title('Running Total')
        plt.ylabel('Total Points')
        plt.xlabel('Date')
        plt.grid(True, alpha=0.3)
        
        # Format x-axis dates
        plt.gcf().autofmt_xdate()
        
        # Save to bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        return buf

    def calculate_weekly_summary(self, points_log):
        """Calculate weekly summaries from points log"""
        if not points_log:
            return []
            
        # Group entries by week
        weeks = {}
        
        for entry in points_log:
            entry_time = datetime.fromisoformat(entry["timestamp"])
            # Determine week number (0 = current week, 1 = last week, etc.)
            now = datetime.utcnow()
            days_ago = (now - entry_time).days
            week_num = days_ago // 7
            
            if week_num not in weeks:
                weeks[week_num] = []
                
            weeks[week_num].append(entry)
        
        # Calculate weekly totals
        summary = []
        for week_num, entries in sorted(weeks.items()):
            start_date = datetime.utcnow() - timedelta(days=(week_num+1)*7)
            end_date = datetime.utcnow() - timedelta(days=week_num*7)
            total = sum(entry["points"] for entry in entries)
            
            summary.append({
                "week": week_num,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "total_points": total,
                "entries": len(entries)
            })
            
        return summary

    async def check_weekly_improvement(self):
        """Check and reward moderators who improved from previous week"""
        for guild in self.bot.guilds:
            if not self.bot.is_feature_enabled("modtracker", guild.id):
                continue
                
            # Get the moderator role
            mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
            if not mod_role:
                continue
                
            for member in mod_role.members:
                try:
                    # Get points for current week and previous week
                    current_week = await self.get_weekly_points(guild.id, member.id, 0)
                    previous_week = await self.get_weekly_points(guild.id, member.id, 1)
                    
                    current_total = sum(entry["points"] for entry in current_week)
                    previous_total = sum(entry["points"] for entry in previous_week)
                    
                    # If current week is better than previous week, award points
                    if current_total > previous_total and previous_total > 0:
                        await self.add_points(
                            guild.id, 
                            member.id, 
                            POINTS_CONFIG["improvement"],
                            "Weekly improvement bonus"
                        )
                except Exception as e:
                    print(f"Error in weekly improvement check: {e}")

    async def check_daily_inactivity(self):
        """Check and penalize inactive moderators"""
        now = datetime.utcnow().timestamp()
        
        for guild in self.bot.guilds:
            if not self.bot.is_feature_enabled("modtracker", guild.id):
                continue
                
            # Get the moderator role
            mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
            if not mod_role:
                continue
                
            if guild.id not in self.active_today:
                self.active_today[guild.id] = {}
                
            for member in mod_role.members:
                # Skip if already marked active today
                if member.id in self.active_today.get(guild.id, {}) and self.active_today[guild.id][member.id]:
                    continue
                    
                # Check if user has been inactive for more than 24 hours
                last_active = self.last_activity.get(guild.id, {}).get(member.id, 0)
                if now - last_active > 86400:  # 24 hours in seconds
                    await self.add_points(
                        guild.id,
                        member.id,
                        POINTS_CONFIG["daily_inactivity"],
                        "24-hour inactivity penalty"
                    )
            
            # Reset daily activity tracking
            self.active_today[guild.id] = {}

    async def send_weekly_reports(self):
        """Send weekly reports to all moderators"""
        for guild in self.bot.guilds:
            if not self.bot.is_feature_enabled("modtracker", guild.id):
                continue
                
            # Get the moderator role
            mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
            if not mod_role:
                continue
                
            for member in mod_role.members:
                try:
                    # Get points for the past week
                    weekly_points = await self.get_weekly_points(guild.id, member.id)
                    total = sum(entry["points"] for entry in weekly_points)
                    
                    # Create and send DM
                    if total > 0:
                        message = (f"📊 **Weekly Moderation Report**\n\n"
                                  f"Thank you for your hard work this week, {member.display_name}!\n\n"
                                  f"You've earned **{total:.1f} points** in the past 7 days. "
                                  f"Keep up the great work! Your efforts are greatly appreciated.")
                    else:
                        message = (f"📊 **Weekly Moderation Report**\n\n"
                                  f"Hello {member.display_name},\n\n"
                                  f"This week your moderation score is **{total:.1f} points**. Don't worry about it! "
                                  f"We all have personal lives and busy periods. If you're currently unable to "
                                  f"dedicate time to moderation, please let the team know in the moderator chat.")
                    
                    try:
                        await member.send(message)
                    except discord.Forbidden:
                        # Cannot DM this user
                        pass
                        
                except Exception as e:
                    print(f"Error sending weekly report to {member.display_name}: {e}")

    # Background task methods
    async def voice_channel_tracker(self):
        """Track time spent in voice channels"""
        while True:
            try:
                now = time.time()
                
                for guild in self.bot.guilds:
                    if not self.bot.is_feature_enabled("modtracker", guild.id):
                        continue
                        
                    # Get target voice channel
                    voice_channel = guild.get_channel(TARGET_VOICE_CHANNEL_ID)
                    if not voice_channel:
                        continue
                        
                    # Get moderator role
                    mod_role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
                    if not mod_role:
                        continue
                        
                    # Check users in voice channel
                    for member in voice_channel.members:
                        # Skip if not a moderator
                        if mod_role not in member.roles:
                            continue
                            
                        # Calculate minutes spent
                        user_key = f"{guild.id}_{member.id}"
                        
                        if user_key in self.voice_users:
                            join_time = self.voice_users[user_key]
                            minutes_spent = (now - join_time) / 60
                            
                            # Award points for each minute
                            if minutes_spent >= 1:
                                points_earned = POINTS_CONFIG["voice_minute"] * int(minutes_spent)
                                await self.add_points(
                                    guild.id,
                                    member.id,
                                    points_earned,
                                    f"Voice channel presence: {int(minutes_spent)} minutes"
                                )
                                # Update join time for next calculation
                                self.voice_users[user_key] = now - (minutes_spent - int(minutes_spent)) * 60
                        else:
                            # First time seeing this user in voice
                            self.voice_users[user_key] = now
            except Exception as e:
                print(f"Error in voice channel tracker: {e}")
                
            # Run every minute
            await asyncio.sleep(60)

    async def scheduled_tasks(self):
        """Run scheduled tasks daily"""
        while True:
            try:
                # Get current time
                now = datetime.utcnow()
                
                # Check weekly improvement at midnight every Sunday
                if now.weekday() == 6 and now.hour == 0 and 0 <= now.minute < 5:
                    await self.check_weekly_improvement()
                    await self.send_weekly_reports()
                
                # Check daily inactivity every day at midnight
                if now.hour == 0 and 0 <= now.minute < 5:
                    await self.check_daily_inactivity()
                    
            except Exception as e:
                print(f"Error in scheduled tasks: {e}")
                
            # Check every 5 minutes
            await asyncio.sleep(300)

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Global tracker instance
mod_tracker = None

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Initialize mod tracker
    global mod_tracker
    mod_tracker = ModPointTracker(bot)
    
    # Start background tasks
    bot.loop.create_task(mod_tracker.voice_channel_tracker())
    bot.loop.create_task(mod_tracker.scheduled_tasks())
    
    # Register commands
    
    @bot.tree.command(name="checkmod", description="Check a moderator's point report")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(moderator="The moderator to check")
    async def checkmod_command(interaction: discord.Interaction, moderator: discord.Member):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return
            
        # Check if target user is a moderator
        mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
        if not mod_role or mod_role not in moderator.roles:
            await interaction.response.send_message(f"{moderator.display_name} is not a moderator.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get points data
            points_log = await mod_tracker.get_user_points(interaction.guild.id, moderator.id)
            
            if not points_log:
                await interaction.followup.send(f"No point data found for {moderator.display_name}.")
                return
                
            # Calculate total points
            total_points = sum(entry["points"] for entry in points_log)
            
            # Calculate weekly summaries
            weekly_summary = mod_tracker.calculate_weekly_summary(points_log)
            
            # Create report image
            report_image = mod_tracker.create_report_image(points_log, moderator.display_name)
            
            # Create embed
            embed = discord.Embed(
                title=f"Moderation Points Report: {moderator.display_name}",
                description=f"Total points in the last 30 days: **{total_points:.1f}**",
                color=0x5865F2
            )
            
            # Add weekly breakdown
            if weekly_summary:
                weekly_text = []
                for week in weekly_summary:
                    weekly_text.append(f"**Week {week['start_date']} to {week['end_date']}:** {week['total_points']:.1f} points ({week['entries']} activities)")
                embed.add_field(
                    name="Weekly Breakdown",
                    value="\n".join(weekly_text) or "No weekly data available",
                    inline=False
                )
            
            # Add reason breakdown
            reason_counts = {}
            for entry in points_log:
                reason = entry["reason"]
                if reason not in reason_counts:
                    reason_counts[reason] = {"count": 0, "points": 0}
                reason_counts[reason]["count"] += 1
                reason_counts[reason]["points"] += entry["points"]
                
            if reason_counts:
                reason_text = []
                for reason, data in sorted(reason_counts.items(), key=lambda x: x[1]["points"], reverse=True):
                    reason_text.append(f"**{reason}:** {data['points']:.1f} points ({data['count']} occurrences)")
                
                embed.add_field(
                    name="Activity Breakdown",
                    value="\n".join(reason_text[:10]) + ("\n..." if len(reason_text) > 10 else ""),
                    inline=False
                )
            
            # Send report with image if available
            if report_image:
                file = discord.File(fp=report_image, filename="mod_report.png")
                embed.set_image(url="attachment://mod_report.png")
                await interaction.followup.send(embed=embed, file=file)
            else:
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            print(f"Error in checkmod command: {e}")
            await interaction.followup.send(f"An error occurred: {str(e)}")
    
    @bot.tree.command(name="modleaderboard", description="View the moderator points leaderboard")
    @app_commands.default_permissions(administrator=True)
    async def modleaderboard_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get points for all moderators
            leaderboard = await mod_tracker.get_all_mod_points(interaction.guild)
            
            if not leaderboard:
                await interaction.followup.send("No moderator point data available.")
                return
                
            # Create embed
            embed = discord.Embed(
                title="Moderator Points Leaderboard",
                description=f"Points earned in the last 30 days",
                color=0x5865F2
            )
            
            # Add leaderboard entries
            for i, (member, points) in enumerate(leaderboard, 1):
                medal = ""
                if i == 1:
                    medal = "🥇 "
                elif i == 2:
                    medal = "🥈 "
                elif i == 3:
                    medal = "🥉 "
                    
                embed.add_field(
                    name=f"{medal}#{i}: {member.display_name}",
                    value=f"{points:.1f} points",
                    inline=False
                )
                
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            print(f"Error in modleaderboard command: {e}")
            await interaction.followup.send(f"An error occurred: {str(e)}")
    
    @bot.tree.command(name="givepoints", description="Award points to a moderator")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        moderator="The moderator to award points to",
        points="The number of points to award",
        reason="The reason for awarding points"
    )
    async def givepoints_command(
        interaction: discord.Interaction, 
        moderator: discord.Member, 
        points: float,
        reason: str = "Manual point adjustment"
    ):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You must be an administrator to use this command.", ephemeral=True)
            return
            
        # Check if target user is a moderator
        mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
        if not mod_role or mod_role not in moderator.roles:
            await interaction.response.send_message(f"{moderator.display_name} is not a moderator.", ephemeral=True)
            return
            
        # Add points
        await mod_tracker.add_points(
            interaction.guild.id,
            moderator.id,
            points,
            f"Manual: {reason}"
        )
        
        await interaction.response.send_message(
            f"Awarded {points} points to {moderator.display_name} for: {reason}",
            ephemeral=True
        )
    
    # Event handlers
    
    # Check for poll creation and participation
    @bot.listen('on_message')
    async def track_discord_polls(message):
        # Skip bot messages and if the feature is disabled
        if message.author.bot or (message.guild and not bot.is_feature_enabled(feature_name, message.guild.id)):
            return
            
        # Check if message author is a moderator
        if message.guild:
            mod_role = discord.utils.get(message.guild.roles, name=MOD_ROLE_NAME)
            if not mod_role or mod_role not in message.author.roles:
                return
        
        try:
            # Check if the message contains a Discord poll
            # Discord polls have embedded components with specific structure
            if message.embeds and len(message.embeds) > 0:
                embed = message.embeds[0]
                
                # Discord polls typically have a footer with "Poll" text
                if embed.footer and "poll" in embed.footer.text.lower():
                    await mod_tracker.add_points(
                        message.guild.id,
                        message.author.id,
                        POINTS_CONFIG["poll_create"],
                        "Discord poll created"
                    )
                    
                # Alternative detection: check for specific poll structure
                if (embed.fields and 
                    embed.description and 
                    "vote" in embed.description.lower() and
                    len(embed.fields) >= 2):  # Polls have at least 2 options
                    await mod_tracker.add_points(
                        message.guild.id,
                        message.author.id,
                        POINTS_CONFIG["poll_create"],
                        "Discord poll created"
                    )
        except Exception as e:
            print(f"Error tracking Discord polls: {e}")

    # For poll participation, we need to track the interaction with Discord's polls
    @bot.listen('on_interaction')
    async def track_polls(interaction: discord.Interaction):
        # Skip if no guild or if the feature is disabled for this guild
        if not interaction.guild or not bot.is_feature_enabled(feature_name, interaction.guild.id):
            return
        
        # Skip if no data
        if not interaction.data:
            return
            
        try:
            # Check if user is a moderator
            mod_role = discord.utils.get(interaction.guild.roles, name=MOD_ROLE_NAME)
            if not mod_role or mod_role not in interaction.user.roles:
                return
                
            # Check for poll creation via Discord slash command
            if interaction.command and interaction.command.name == "poll" and not interaction.command.guild_id:
                # This detects Discord's native /poll command (it has no guild_id because it's a global command)
                await mod_tracker.add_points(
                    interaction.guild.id,
                    interaction.user.id,
                    POINTS_CONFIG["poll_create"],
                    "Discord poll created"
                )
                
            # Check for poll participation (button or select menu interactions)
            elif (interaction.data.get("component_type", 0) in (2, 3) and  # Button or Select Menu
                interaction.message and interaction.message.embeds and 
                any("poll" in (e.footer.text.lower() if e.footer else "") for e in interaction.message.embeds)):
                await mod_tracker.add_points(
                    interaction.guild.id,
                    interaction.user.id,
                    POINTS_CONFIG["poll_participate"],
                    "Poll participation"
                )
        except Exception as e:
            print(f"Error tracking poll interaction: {e}")