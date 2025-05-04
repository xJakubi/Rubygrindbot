import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Module information for the setup system
DISPLAY_NAME = "Live on Twitch"
DESCRIPTION = "Notify when users go live on Twitch and assign a special role"
ENABLED_BY_DEFAULT = False

# Twitch configuration constants
TWITCH_CLIENT_ID = ''
TWITCH_CLIENT_SECRET = ''
TWITCH_AUTH_URL = ""
TWITCH_API_BASE = ""

# File to store twitch links
TWITCH_LINKS_FILE = "twitch_links.json"
TWITCH_SETTINGS_FILE = "twitch_settings.json"

class TwitchConfig:
    def __init__(self):
        self.access_token = None
        self.token_expires_at = datetime.now()
        
    async def get_access_token(self, session: aiohttp.ClientSession) -> str:
        """Get a valid Twitch API access token, refreshing if necessary"""
        if self.access_token is None or datetime.now() >= self.token_expires_at:
            await self.refresh_access_token(session)
        return self.access_token
    
    async def refresh_access_token(self, session: aiohttp.ClientSession):
        """Get a new access token from Twitch"""
        try:
            params = {
                'client_id': TWITCH_CLIENT_ID,
                'client_secret': TWITCH_CLIENT_SECRET,
                'grant_type': 'client_credentials'
            }
            async with session.post(TWITCH_AUTH_URL, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data['access_token']
                    # Set expiration time (typically 60 days, but we'll set it to 50 to be safe)
                    self.token_expires_at = datetime.now() + timedelta(days=50)
                    print("Refreshed Twitch access token successfully")
                else:
                    error_text = await response.text()
                    print(f"Failed to refresh Twitch token: {response.status} - {error_text}")
        except Exception as e:
            print(f"Error refreshing Twitch token: {e}")
            self.access_token = None

class TwitchIntegration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.twitch_config = TwitchConfig()
        self.twitch_links = {}  # guild_id -> { user_id -> twitch_username }
        self.twitch_settings = {}  # guild_id -> { notification_channel_id, etc. }
        self.currently_live = {}  # guild_id -> { user_id -> stream_data }
        
        # Load saved data
        self.load_twitch_links()
        self.load_twitch_settings()
        
        # Start background tasks
        self.check_twitch_streams.start()
    
    def cog_unload(self):
        """Clean up when the cog is unloaded"""
        self.check_twitch_streams.cancel()
    
    def load_twitch_links(self):
        """Load saved Twitch username links from file"""
        try:
            if os.path.exists(TWITCH_LINKS_FILE):
                with open(TWITCH_LINKS_FILE, 'r') as f:
                    self.twitch_links = json.load(f)
                print(f"Loaded Twitch links for {len(self.twitch_links)} guilds")
        except Exception as e:
            print(f"Error loading Twitch links: {e}")
            self.twitch_links = {}
    
    def save_twitch_links(self):
        """Save Twitch username links to file"""
        try:
            with open(TWITCH_LINKS_FILE, 'w') as f:
                json.dump(self.twitch_links, f, indent=4)
        except Exception as e:
            print(f"Error saving Twitch links: {e}")
    
    def load_twitch_settings(self):
        """Load Twitch-related settings from file"""
        try:
            if os.path.exists(TWITCH_SETTINGS_FILE):
                with open(TWITCH_SETTINGS_FILE, 'r') as f:
                    self.twitch_settings = json.load(f)
                print(f"Loaded Twitch settings for {len(self.twitch_settings)} guilds")
        except Exception as e:
            print(f"Error loading Twitch settings: {e}")
            self.twitch_settings = {}
    
    def save_twitch_settings(self):
        """Save Twitch-related settings to file"""
        try:
            with open(TWITCH_SETTINGS_FILE, 'w') as f:
                json.dump(self.twitch_settings, f, indent=4)
        except Exception as e:
            print(f"Error saving Twitch settings: {e}")
    
    async def get_or_create_live_role(self, guild: discord.Guild) -> discord.Role:
        """Get the 'Live on Twitch' role or create it if it doesn't exist"""
        role = discord.utils.get(guild.roles, name="Live on Twitch")
        if role is None:
            try:
                # Create a new role with a purple color (Twitch's brand color)
                role = await guild.create_role(
                    name="Live on Twitch",
                    color=discord.Color.purple(),
                    hoist=True,  # Separate role in the member list
                    mentionable=True,
                    reason="Created for Twitch live notifications"
                )
                print(f"Created 'Live on Twitch' role in guild {guild.name}")
            except Exception as e:
                print(f"Error creating 'Live on Twitch' role: {e}")
                return None
        return role
    
    async def check_user_has_required_level(self, member: discord.Member) -> bool:
        """Check if user has at least one of the required level roles"""
        required_roles = ["Level 5", "Level 6", "Level 7", "Level 8", "Level 9", "Level 10"]
        for role_name in required_roles:
            role = discord.utils.get(member.roles, name=role_name)
            if role is not None:
                return True
        return False
    
    @tasks.loop(minutes=5)
    async def check_twitch_streams(self):
        """Check if linked Twitch users are currently streaming"""
        async with aiohttp.ClientSession() as session:
            # Ensure we have a valid token
            access_token = await self.twitch_config.get_access_token(session)
            if not access_token:
                print("Failed to get Twitch access token, skipping stream check")
                return
            
            # Process each guild's linked users
            for guild_id, user_links in self.twitch_links.items():
                if not user_links:
                    continue
                
                # Skip if feature is disabled for this guild
                if not self.bot.is_feature_enabled("liveontiwtch", int(guild_id)):
                    continue
                
                # Get guild object
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                
                # Get settings for this guild
                guild_settings = self.twitch_settings.get(guild_id, {})
                notification_channel_id = guild_settings.get("notification_channel_id")
                if not notification_channel_id:
                    continue
                
                # Get notification channel
                notification_channel = guild.get_channel(int(notification_channel_id))
                if not notification_channel:
                    continue
                
                # Get 'Live on Twitch' role
                live_role = await self.get_or_create_live_role(guild)
                if not live_role:
                    continue
                
                # Get all twitch usernames for this guild
                twitch_usernames = list(user_links.values())
                if not twitch_usernames:
                    continue
                
                # Initialize live status tracking for this guild if not exists
                if guild_id not in self.currently_live:
                    self.currently_live[guild_id] = {}
                
                # Query Twitch API for stream status
                await self.check_and_update_streams(
                    session, access_token, guild, guild_id, user_links, 
                    notification_channel, live_role, twitch_usernames
                )
    
    async def check_and_update_streams(
        self, session, access_token, guild, guild_id, user_links, 
        notification_channel, live_role, twitch_usernames
    ):
        """Check streams and update statuses for a guild"""
        try:
            # Twitch API limits to 100 usernames per request
            for i in range(0, len(twitch_usernames), 100):
                batch = twitch_usernames[i:i+100]
                
                # Build request URL with user_login parameters
                url = f"{TWITCH_API_BASE}/streams"
                params = {"user_login": batch}
                
                # Make request to Twitch API
                headers = {
                    "Client-ID": TWITCH_CLIENT_ID,
                    "Authorization": f"Bearer {access_token}"
                }
                
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"Twitch API error: {response.status} - {error_text}")
                        continue
                    
                    data = await response.json()
                    live_streams = data.get("data", [])
                    
                    # Create reverse mapping of twitch_username -> user_id
                    username_to_user_id = {v: k for k, v in user_links.items()}
                    
                    # Process currently live streams
                    current_live_user_ids = set()
                    for stream in live_streams:
                        twitch_username = stream["user_login"].lower()
                        
                        # Find the Discord user ID for this Twitch user
                        discord_user_id = username_to_user_id.get(twitch_username)
                        if not discord_user_id:
                            continue
                        
                        current_live_user_ids.add(discord_user_id)
                        
                        # Check if this is a new live notification
                        was_already_live = discord_user_id in self.currently_live[guild_id]
                        
                        if not was_already_live:
                            # User just went live
                            self.currently_live[guild_id][discord_user_id] = stream
                            
                            # Add the live role to the user
                            member = guild.get_member(int(discord_user_id))
                            if member:
                                try:
                                    await member.add_roles(live_role, reason="User is live on Twitch")
                                except Exception as e:
                                    print(f"Error adding 'Live on Twitch' role: {e}")
                            
                            # Send notification
                            await self.send_live_notification(notification_channel, member, stream)
                    
                    # Process users who went offline
                    users_went_offline = [
                        user_id for user_id in self.currently_live[guild_id].keys()
                        if user_id not in current_live_user_ids
                    ]
                    
                    for user_id in users_went_offline:
                        # Remove from currently live dict
                        if user_id in self.currently_live[guild_id]:
                            del self.currently_live[guild_id][user_id]
                        
                        # Remove the live role
                        member = guild.get_member(int(user_id))
                        if member and live_role in member.roles:
                            try:
                                await member.remove_roles(live_role, reason="User is no longer live on Twitch")
                            except Exception as e:
                                print(f"Error removing 'Live on Twitch' role: {e}")
        
        except Exception as e:
            print(f"Error checking Twitch streams: {e}")
    
    async def send_live_notification(self, channel, member, stream):
        """Send a notification that a user has gone live on Twitch"""
        if not channel or not member:
            return
        
        try:
            # Create a rich embed for the stream
            embed = discord.Embed(
                title=stream["title"],
                url=f"https://twitch.tv/{stream['user_login']}",
                color=discord.Color.purple()
            )
            
            embed.set_author(
                name=f"{member.display_name} is now live on Twitch!",
                icon_url=member.display_avatar.url
            )
            
            # Add game name if available
            if stream.get("game_name"):
                embed.add_field(name="Playing", value=stream["game_name"], inline=True)
            
            
            # Add thumbnail if available
            thumbnail_url = stream.get("thumbnail_url", "")
            if thumbnail_url:
                # Replace size parameters in thumbnail URL
                thumbnail_url = thumbnail_url.replace("{width}", "440").replace("{height}", "248")
                embed.set_image(url=thumbnail_url)
            
            # Add footer with timestamp
            embed.set_footer(text="Started streaming")
            embed.timestamp = datetime.strptime(stream["started_at"], "%Y-%m-%dT%H:%M:%SZ")
            
            # Send the embed
            await channel.send(
                f"🔴 **{member.mention} is now live on Twitch!** Check out their stream:",
                embed=embed
            )
        
        except Exception as e:
            print(f"Error sending live notification: {e}")
            # Fallback to simple message if embed fails
            try:
                await channel.send(
                    f"🔴 **{member.mention}** is now live on Twitch: https://twitch.tv/{stream['user_login']}"
                )
            except:
                pass
    
    @check_twitch_streams.before_loop
    async def before_check_twitch_streams(self):
        """Wait until the bot is ready before starting the stream check loop"""
        await self.bot.wait_until_ready()
        # Wait an additional 30 seconds to ensure all data is loaded
        await asyncio.sleep(30)
    
    @app_commands.command(
        name="linktwitch",
        description="Link your Twitch account to receive live notifications"
    )
    @app_commands.describe(twitch_username="Your Twitch username")
    async def link_twitch(self, interaction: discord.Interaction, twitch_username: str):
        """Link a Discord user to their Twitch username"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("liveontiwtch", interaction.guild_id):
            await interaction.response.send_message(
                "The Twitch integration feature is not enabled in this server. "
                "Please ask an administrator to enable it using the `/setup` command.",
                ephemeral=True
            )
            return
        
        # Check if user has the required level role
        if not await self.check_user_has_required_level(interaction.user):
            await interaction.response.send_message(
                "You need to be at least Level 5 to link your Twitch account. "
                "Continue leveling up in the server to unlock this feature!",
                ephemeral=True
            )
            return
        
        # Clean the username (remove @ if present and convert to lowercase)
        twitch_username = twitch_username.lstrip('@').lower()
        
        # Validate the Twitch username exists
        async with aiohttp.ClientSession() as session:
            # Get access token
            access_token = await self.twitch_config.get_access_token(session)
            if not access_token:
                await interaction.response.send_message(
                    "Sorry, I couldn't verify your Twitch username due to an authentication issue. "
                    "Please try again later.",
                    ephemeral=True
                )
                return
            
            # Check if the Twitch username exists
            url = f"{TWITCH_API_BASE}/users"
            headers = {
                "Client-ID": TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {access_token}"
            }
            params = {"login": twitch_username}
            
            await interaction.response.defer(ephemeral=True)
            
            async with session.get(url, headers=headers, params=params) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        "Sorry, I couldn't verify your Twitch username due to an API error. "
                        "Please try again later.",
                        ephemeral=True
                    )
                    return
                
                data = await response.json()
                if not data.get("data") or len(data["data"]) == 0:
                    await interaction.followup.send(
                        f"The Twitch username '{twitch_username}' doesn't seem to exist. "
                        "Please check the spelling and try again.",
                        ephemeral=True
                    )
                    return
                
                # Username exists, store the link
                guild_id = str(interaction.guild_id)
                user_id = str(interaction.user.id)
                
                # Initialize guild data if needed
                if guild_id not in self.twitch_links:
                    self.twitch_links[guild_id] = {}
                
                # Store the link
                self.twitch_links[guild_id][user_id] = twitch_username
                self.save_twitch_links()
                
                await interaction.followup.send(
                    f"Successfully linked your Discord account to Twitch user '{twitch_username}'. "
                    "You'll now receive a special role and the server will be notified when you go live!",
                    ephemeral=True
                )
    
    @app_commands.command(
        name="unlinktwitch",
        description="Unlink your Twitch account from Discord"
    )
    async def unlink_twitch(self, interaction: discord.Interaction):
        """Unlink a Discord user from their Twitch username"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("liveontiwtch", interaction.guild_id):
            await interaction.response.send_message(
                "The Twitch integration feature is not enabled in this server.",
                ephemeral=True
            )
            return
        
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)
        
        # Check if the user has linked their account
        if (guild_id not in self.twitch_links or
            user_id not in self.twitch_links[guild_id]):
            await interaction.response.send_message(
                "You don't have a linked Twitch account.",
                ephemeral=True
            )
            return
        
        # Remove the link
        twitch_username = self.twitch_links[guild_id].pop(user_id)
        self.save_twitch_links()
        
        # Remove from currently live if needed
        if guild_id in self.currently_live and user_id in self.currently_live[guild_id]:
            del self.currently_live[guild_id][user_id]
        
        # Remove the live role if they have it
        live_role = discord.utils.get(interaction.guild.roles, name="Live on Twitch")
        if live_role and live_role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(live_role, reason="User unlinked Twitch account")
            except Exception as e:
                print(f"Error removing 'Live on Twitch' role: {e}")
        
        await interaction.response.send_message(
            f"Successfully unlinked your Discord account from Twitch user '{twitch_username}'.",
            ephemeral=True
        )
    
    @app_commands.command(
        name="twitchnotificationchannel",
        description="Set the channel for Twitch live notifications"
    )
    @app_commands.describe(channel="The channel where notifications will be sent")
    @app_commands.default_permissions(administrator=True)
    async def set_notification_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for Twitch live notifications"""
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("liveontiwtch", interaction.guild_id):
            await interaction.followup.send(
                "The Twitch integration feature is not enabled in this server. "
                "Please enable it first using the `/setup` command.",
                ephemeral=True
            )
            return
        
        guild_id = str(interaction.guild_id)
        
        # Initialize guild settings if needed
        if guild_id not in self.twitch_settings:
            self.twitch_settings[guild_id] = {}
        
        # Store the notification channel
        self.twitch_settings[guild_id]["notification_channel_id"] = channel.id
        self.save_twitch_settings()
        
        await interaction.followup.send(
            f"Successfully set {channel.mention} as the Twitch live notification channel.",
            ephemeral=True
        )
    
    @app_commands.command(
        name="twitchlinkedusers",
        description="Show all users who have linked their Twitch accounts"
    )
    @app_commands.default_permissions(administrator=True)
    async def show_linked_users(self, interaction: discord.Interaction):
        """Show all users who have linked their Twitch accounts"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("liveontiwtch", interaction.guild_id):
            await interaction.response.send_message(
                "The Twitch integration feature is not enabled in this server.",
                ephemeral=True
            )
            return
        
        guild_id = str(interaction.guild_id)
        
        if guild_id not in self.twitch_links or not self.twitch_links[guild_id]:
            await interaction.response.send_message(
                "No users have linked their Twitch accounts in this server.",
                ephemeral=True
            )
            return
        
        # Create an embed to display linked users
        embed = discord.Embed(
            title="Linked Twitch Accounts",
            description="Users who have linked their Discord accounts to Twitch:",
            color=discord.Color.purple()
        )
        
        # Add linked users to the embed
        for user_id, twitch_username in self.twitch_links[guild_id].items():
            member = interaction.guild.get_member(int(user_id))
            if member:
                embed.add_field(
                    name=member.display_name,
                    value=f"[{twitch_username}](https://twitch.tv/{twitch_username})",
                    inline=True
                )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(TwitchIntegration(bot))