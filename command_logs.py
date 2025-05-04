import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import datetime
import traceback
import io
from typing import Optional, List, Dict, Any
import time
import re
import aiohttp

DISPLAY_NAME = "Server Logs"
DESCRIPTION = "Logs server events like message edits, deletions, and user activity"
ENABLED_BY_DEFAULT = False  # Off by default since it requires special permissions

class ServerLogsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_key = "server_logs"
        self.log_channels = {}  # guild_id -> channel_id
        
        # Initialize cosmos db client if available
        try:
            from azure.cosmos import CosmosClient, PartitionKey
            self.cosmos_endpoint = os.getenv("COSMOS_ENDPOINT")
            self.cosmos_key = os.getenv("COSMOS_KEY")
            self.cosmos_database = os.getenv("COSMOS_DATABASE")
            
            # Initialize Cosmos DB client
            try:
                self.cosmos_client = CosmosClient(self.cosmos_endpoint, credential=self.cosmos_key)
                self.database = self.cosmos_client.get_database_client(self.cosmos_database)
                
                # Create or get container for bot configuration
                try:
                    self.config_container = self.database.create_container_if_not_exists(
                        id="bot_config",
                        partition_key=PartitionKey(path="/guild_id")
                    )
                except Exception as e:
                    print(f"[ServerLogs] Error creating container: {str(e)}")
                    self.config_container = self.database.get_container_client("bot_config")
                
                print(f"[ServerLogs] Connected to Azure Cosmos DB: {self.cosmos_database}/bot_config")
            except Exception as e:
                print(f"[ServerLogs] Error connecting to Azure Cosmos DB: {str(e)}")
                traceback.print_exc()
                self.cosmos_client = None
                self.database = None
                self.config_container = None
        except ImportError:
            print("[WARNING] Azure Cosmos DB SDK not installed. Database features will use fallback mode.")
            self.cosmos_client = None
            self.database = None
            self.config_container = None
        
        # Start background task for initialization
        self.bot.loop.create_task(self.load_log_channels())
    
    async def load_log_channels(self):
        """Load log channels from Cosmos DB on startup"""
        await self.bot.wait_until_ready()
        print("[ServerLogs] Loading log channel configurations...")
        
        for guild in self.bot.guilds:
            try:
                # Check if feature is enabled
                if self.bot.is_feature_enabled(self.config_key, guild.id):
                    channel_id = await self.get_cosmos_config_item(guild.id, f"{self.config_key}_channel")
                    if channel_id:
                        self.log_channels[guild.id] = int(channel_id)
                        print(f"[ServerLogs] Loaded log channel for {guild.name}: {channel_id}")
            except Exception as e:
                print(f"[ServerLogs] Error loading log channel for {guild.name}: {str(e)}")
    
    async def get_cosmos_config_item(self, guild_id, key):
        """Get configuration item from Cosmos DB"""
        if not self.config_container:
            return None
            
        try:
            # Build the item ID
            item_id = f"{guild_id}_{key}"
            
            # Query for the item
            query = f"SELECT * FROM c WHERE c.id = '{item_id}'"
            items = list(self.config_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            if items:
                return items[0].get("value")
            return None
        except Exception as e:
            print(f"[ServerLogs] Error getting config from Cosmos DB: {str(e)}")
            return None
    
    async def set_cosmos_config_item(self, guild_id, key, value):
        """Set configuration item in Cosmos DB"""
        if not self.config_container:
            return False
            
        try:
            # Build the item ID and data
            item_id = f"{guild_id}_{key}"
            item = {
                "id": item_id,
                "guild_id": str(guild_id),
                "key": key,
                "value": value,
                "updated_at": datetime.datetime.utcnow().isoformat()
            }
            
            # Upsert the item
            self.config_container.upsert_item(item)
            return True
        except Exception as e:
            print(f"[ServerLogs] Error setting config in Cosmos DB: {str(e)}")
            return False
    
    async def log_to_channel(self, guild: discord.Guild, embed: discord.Embed, file: discord.File = None):
        """Send a log message to the configured log channel for the guild"""
        if not guild:
            return False
        
        # Check if the guild has a log channel configured
        channel_id = self.log_channels.get(guild.id)
        if not channel_id:
            return False
        
        # Get the channel
        channel = guild.get_channel(channel_id)
        if not channel:
            return False
        
        # Check if the bot has permission to send messages in the channel
        if not channel.permissions_for(guild.me).send_messages:
            print(f"[ServerLogs] No permission to send messages in log channel {channel.name}")
            return False
            
        # Send the log message
        try:
            if file:
                await channel.send(embed=embed, file=file)
            else:
                await channel.send(embed=embed)
            return True
        except Exception as e:
            print(f"[ServerLogs] Error sending log message: {str(e)}")
            return False
    
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Log when a message is deleted"""
        # Don't log DMs or bot messages
        if message.guild is None or message.author.bot:
            return
        
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, message.guild.id):
            return
        
        # Create embed for deleted message
        embed = discord.Embed(
            title="Message Deleted",
            description=f"**In {message.channel.mention}**",
            color=0xff0000,  # Red
            timestamp=datetime.datetime.utcnow()
        )
        
        # Add message content if available
        if message.content:
            if len(message.content) > 1024:
                embed.add_field(name="Content", value=message.content[:1021] + "...", inline=False)
            else:
                embed.add_field(name="Content", value=message.content, inline=False)
        else:
            embed.add_field(name="Content", value="[No text content]", inline=False)
        
        # Add author info
        embed.add_field(name="Author", value=f"{message.author.name} (ID: {message.author.id})", inline=True)
        embed.add_field(name="Channel", value=f"{message.channel.name} (ID: {message.channel.id})", inline=True)
        embed.add_field(name="Message ID", value=message.id, inline=True)
        embed.set_footer(text=f"Message sent: {message.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Add author avatar if available
        if message.author.avatar:
            embed.set_author(name=message.author.name, icon_url=message.author.avatar.url)
        
        # Handle attachments
        file_to_send = None
        if message.attachments:
            attachment_list = []
            for i, attachment in enumerate(message.attachments):
                attachment_list.append(f"[{i+1}] {attachment.filename} - {attachment.url}")
            
            if attachment_list:
                embed.add_field(name="Attachments", value="\n".join(attachment_list), inline=False)
        
        # Send the log message
        await self.log_to_channel(message.guild, embed, file_to_send)
    
    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Log when a message is edited"""
        # Don't log DMs or bot messages
        if before.guild is None or before.author.bot:
            return
        
        # Skip if the content didn't change (e.g., only an embed was added)
        if before.content == after.content:
            return
        
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, before.guild.id):
            return
        
        # Create embed for edited message
        embed = discord.Embed(
            title="Message Edited",
            description=f"**In {before.channel.mention}** [Jump to Message]({after.jump_url})",
            color=0xffcc00,  # Gold
            timestamp=datetime.datetime.utcnow()
        )
        
        # Add message content (before and after)
        if len(before.content) > 512:
            embed.add_field(name="Before", value=before.content[:509] + "...", inline=False)
        else:
            embed.add_field(name="Before", value=before.content if before.content else "[No text content]", inline=False)
        
        if len(after.content) > 512:
            embed.add_field(name="After", value=after.content[:509] + "...", inline=False)
        else:
            embed.add_field(name="After", value=after.content if after.content else "[No text content]", inline=False)
        
        # Add author info
        embed.add_field(name="Author", value=f"{before.author.name} (ID: {before.author.id})", inline=True)
        embed.add_field(name="Channel", value=f"{before.channel.name} (ID: {before.channel.id})", inline=True)
        embed.add_field(name="Message ID", value=before.id, inline=True)
        
        # Add author avatar if available
        if before.author.avatar:
            embed.set_author(name=before.author.name, icon_url=before.author.avatar.url)
        
        # Send the log message
        await self.log_to_channel(before.guild, embed)
    
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Log when a channel is deleted"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, channel.guild.id):
            return
        
        # Get audit log to see who deleted the channel
        try:
            await asyncio.sleep(1)  # Wait a bit for audit log to update
            async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
                if entry.target.id == channel.id:
                    # Create embed for channel deletion
                    embed = discord.Embed(
                        title="Channel Deleted",
                        description=f"**{channel.name}** was deleted",
                        color=0xff0000,  # Red
                        timestamp=datetime.datetime.utcnow()
                    )
                    
                    embed.add_field(name="Channel ID", value=channel.id, inline=True)
                    embed.add_field(name="Channel Type", value=str(channel.type).replace('_', ' ').title(), inline=True)
                    embed.add_field(name="Deleted By", value=f"{entry.user.name} (ID: {entry.user.id})", inline=True)
                    
                    # Add user avatar if available
                    if entry.user.avatar:
                        embed.set_author(name=entry.user.name, icon_url=entry.user.avatar.url)
                    
                    # Send the log message
                    await self.log_to_channel(channel.guild, embed)
                    return
        except Exception as e:
            print(f"[ServerLogs] Error getting audit log for channel deletion: {str(e)}")
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Log when a user changes their nickname"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, before.guild.id):
            return
        
        # Check if nickname changed
        if before.nick != after.nick:
            embed = discord.Embed(
                title="Nickname Changed",
                description=f"**{before.name}**'s nickname was changed",
                color=0x3498db,  # Blue
                timestamp=datetime.datetime.utcnow()
            )
            
            embed.add_field(name="Before", value=before.nick if before.nick else "[No nickname]", inline=True)
            embed.add_field(name="After", value=after.nick if after.nick else "[No nickname]", inline=True)
            embed.add_field(name="User ID", value=before.id, inline=False)
            
            # Add user avatar if available
            if after.avatar:
                embed.set_author(name=after.name, icon_url=after.avatar.url)
            
            # Send the log message
            await self.log_to_channel(before.guild, embed)
    
    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        """Log when a user changes their username or avatar"""
        # Check each guild the user is in
        for guild in self.bot.guilds:
            # Check if feature is enabled and the user is in this guild
            if not self.bot.is_feature_enabled(self.config_key, guild.id):
                continue
            
            member = guild.get_member(before.id)
            if not member:
                continue
            
            # Check if username changed
            if before.name != after.name:
                embed = discord.Embed(
                    title="Username Changed",
                    color=0x3498db,  # Blue
                    timestamp=datetime.datetime.utcnow()
                )
                
                embed.add_field(name="Before", value=before.name, inline=True)
                embed.add_field(name="After", value=after.name, inline=True)
                embed.add_field(name="User ID", value=before.id, inline=False)
                
                # Add user avatar if available
                if after.avatar:
                    embed.set_author(name=after.name, icon_url=after.avatar.url)
                
                # Send the log message
                await self.log_to_channel(guild, embed)
    

    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Log when a member leaves the server"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, member.guild.id):
            return
        
        # Create embed for member leave
        embed = discord.Embed(
            title="Member Left",
            description=f"**{member.name}** left the server",
            color=0xe74c3c,  # Red
            timestamp=datetime.datetime.utcnow()
        )
        
        # Add member info
        joined_time = "Unknown"
        member_since = "Unknown"
        if member.joined_at:
            joined_time = member.joined_at.strftime("%Y-%m-%d %H:%M:%S")
            member_since = datetime.datetime.utcnow() - member.joined_at
            member_since = f"{member_since.days} days"
        
        embed.add_field(name="Joined Server", value=f"{joined_time} ({member_since} ago)", inline=True)
        embed.add_field(name="Roles", value=", ".join([role.name for role in member.roles if role.name != "@everyone"]) or "None", inline=False)
        embed.add_field(name="User ID", value=member.id, inline=True)
        
        # Add user avatar if available
        if member.avatar:
            embed.set_author(name=member.name, icon_url=member.avatar.url)
            embed.set_thumbnail(url=member.avatar.url)
        
        # Send the log message
        await self.log_to_channel(member.guild, embed)
    
    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        """Log when an invite is created"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, invite.guild.id):
            return
        
        # Create embed for invite creation
        embed = discord.Embed(
            title="Invite Created",
            description=f"Invite **{invite.code}** created",
            color=0x9b59b6,  # Purple
            timestamp=datetime.datetime.utcnow()
        )
        
        # Add invite details
        embed.add_field(name="Creator", value=f"{invite.inviter.name} (ID: {invite.inviter.id})" if invite.inviter else "Unknown", inline=True)
        embed.add_field(name="Channel", value=f"{invite.channel.name} (ID: {invite.channel.id})", inline=True)
        embed.add_field(name="Uses", value=f"{invite.max_uses if invite.max_uses else 'Unlimited'}", inline=True)
        embed.add_field(name="Expires", value=f"{invite.max_age} seconds" if invite.max_age else "Never", inline=True)
        embed.add_field(name="Temporary", value="Yes" if invite.temporary else "No", inline=True)
        
        # Add creator avatar if available
        if invite.inviter and invite.inviter.avatar:
            embed.set_author(name=invite.inviter.name, icon_url=invite.inviter.avatar.url)
        
        # Send the log message
        await self.log_to_channel(invite.guild, embed)
    
    async def handle_verification_log(self, member, role_name, image_url=None):
        """Log when a user gets verified or gets a pro role"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled(self.config_key, member.guild.id):
            return
        
        # Create embed for verification
        embed = discord.Embed(
            title="Role Verification",
            description=f"**{member.name}** verified for role **{role_name}**",
            color=0x1abc9c,  # Teal
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.add_field(name="User", value=f"{member.name} (ID: {member.id})", inline=True)
        embed.add_field(name="Role", value=role_name, inline=True)
        
        # Add member avatar if available
        if member.avatar:
            embed.set_author(name=member.name, icon_url=member.avatar.url)
        
        file_to_send = None
        # If an image was provided for verification
        if image_url:
            embed.add_field(name="Verification Image", value="Image attached below", inline=False)
            
            # Try to download and attach the image
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            file_to_send = discord.File(fp=io.BytesIO(image_data), filename="verification.png")
                            embed.set_image(url="attachment://verification.png")
                        else:
                            print(f"[ServerLogs] Failed to download image: Status code {resp.status}")
                            embed.add_field(name="Image Error", value=f"Failed to download image (Status {resp.status})", inline=False)
            except Exception as e:
                print(f"[ServerLogs] Error downloading verification image: {str(e)}")
                embed.add_field(name="Image Error", value=f"Failed to download image: {str(e)}", inline=False)
        
        # Send the log message
        success = await self.log_to_channel(member.guild, embed, file_to_send)
        print(f"[ServerLogs] Verification log sent: {success}")
        return success
    
    @app_commands.command(
        name="setlogschannel",
        description="Set the channel where server logs should be sent"
    )
    @app_commands.describe(
        channel="The channel where logs will be sent"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setlogschannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for server logs"""
        # Check if the bot has permission to send messages in the channel
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message("I don't have permission to send messages in that channel. Please choose a different channel or adjust my permissions.", ephemeral=True)
            return
        
        # Set the log channel in the cache
        self.log_channels[interaction.guild.id] = channel.id
        
        # Store the log channel in the database
        success = await self.set_cosmos_config_item(interaction.guild.id, f"{self.config_key}_channel", str(channel.id))
        
        # Enable the feature if needed
        try:
            # Use the new method from the bot to enable the feature
            self.bot.set_feature_enabled(self.config_key, interaction.guild.id, True)
            
            # If cosmos DB is available, try to update there too
            if hasattr(self.bot, 'save_guild_settings_to_cosmos') and callable(getattr(self.bot, 'save_guild_settings_to_cosmos', None)):
                await self.bot.save_guild_settings_to_cosmos(interaction.guild.id, self.bot.get_guild_settings(interaction.guild.id))
        except AttributeError:
            # Fallback if the method doesn't exist
            print(f"[ServerLogs] Warning: Could not enable feature in guild settings, using manual approach")
            settings = self.bot.get_guild_settings(interaction.guild.id)
            settings[self.config_key] = True
            self.bot.save_guild_settings(interaction.guild.id, settings)
            
            # Send confirmation
            embed = discord.Embed(
                title="Server Logs Configured",
                description=f"All server logs will now be sent to {channel.mention}",
                color=0x2ecc71,  # Green
                timestamp=datetime.datetime.utcnow()
            )
            
            # List what will be logged
            log_features = [
                "Message deletions (with content)",
                "Message edits",
                "Channel deletions",
                "Nickname changes",
                "Username changes",
                "Members joining",
                "Members leaving",
                "Invite creations",
                "Role verifications with images"
            ]
            
            embed.add_field(name="Logs will include", value="\n".join([f"• {feature}" for feature in log_features]), inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Send a test log
            test_embed = discord.Embed(
                title="Logging System Activated",
                description="The server logging system has been configured. All specified server events will now be logged in this channel.",
                color=0x3498db,  # Blue
                timestamp=datetime.datetime.utcnow()
            )
            
            test_embed.add_field(name="Setup By", value=f"{interaction.user.name} (ID: {interaction.user.id})", inline=True)
            test_embed.add_field(name="Logging Started", value=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), inline=True)
            
            await channel.send(embed=test_embed)

async def setup(bot):
    await bot.add_cog(ServerLogsCog(bot))