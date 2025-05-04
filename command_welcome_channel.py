import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import traceback

DISPLAY_NAME = "Welcome and total users channel"
DESCRIPTION = "Creates a channel showing total members and posts welcome messages for new members"
ENABLED_BY_DEFAULT = False  # Off by default since it requires channel creation permissions

class WelcomeChannelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.welcome_channels = {}  # Store guild_id -> channel_id mappings
        self.config_key = "welcome_channel"
        
        # Start background task for initialization and channel checks
        self.bot.loop.create_task(self.startup_check())
    
    async def startup_check(self):
        """Initial check when bot starts up"""
        # Wait until bot is ready
        await self.bot.wait_until_ready()
        print("[Welcome] Bot is ready, checking for enabled feature in guilds...")
        
        try:
            # Wait a bit more to ensure all systems are loaded
            await asyncio.sleep(5)
            
            # Check all guilds
            for guild in self.bot.guilds:
                try:
                    # Check if feature is enabled
                    if self.bot.is_feature_enabled("welcome_channel", guild.id):
                        print(f"[Welcome] Feature enabled for {guild.name}, checking channel")
                        
                        # Find existing welcome channel
                        channel = await self.find_existing_welcome_channel(guild)
                        
                        if channel:
                            print(f"[Welcome] Found existing welcome channel {channel.name} ({channel.id}) in {guild.name}")
                            # Update our database with this channel
                            await self.save_welcome_channel(guild.id, channel.id)
                            # Update the channel
                            await self.update_welcome_channel(guild)
                        else:
                            # No existing channel found, create a new one
                            print(f"[Welcome] No existing welcome channel found in {guild.name}, creating new one")
                            await self.create_welcome_channel(guild)
                except Exception as e:
                    print(f"[Welcome] Error checking {guild.name}: {str(e)}")
                    traceback.print_exc()
        except Exception as e:
            print(f"[Welcome] Startup error: {str(e)}")
            traceback.print_exc()
    
    async def find_existing_welcome_channel(self, guild):
        """Find an existing channel with the welcome format"""
        try:
            # First check our database if we have a saved channel ID
            channel_id = await self.load_welcome_channel(guild.id)
            if channel_id:
                channel = guild.get_channel(int(channel_id))
                if channel:
                    print(f"[Welcome] Found channel from database: {channel.name} ({channel.id})")
                    return channel
            
            # If no channel found in database or it doesn't exist anymore,
            # search for channels matching our pattern
            prefix = "🧑┆users-"
            for channel in guild.text_channels:
                if channel.name.startswith(prefix):
                    print(f"[Welcome] Found channel by name pattern: {channel.name} ({channel.id})")
                    return channel
                    
            print(f"[Welcome] No existing welcome channel found in {guild.name}")
            return None
        except Exception as e:
            print(f"[Welcome] Error finding existing channel: {str(e)}")
            return None
    
    async def load_welcome_channel(self, guild_id):
        """Load the welcome channel ID from database"""
        try:
            # Use the bot's database connection/ORM to get the config
            # Adjust this method based on how your bot stores configuration
            query = "SELECT value FROM guild_config WHERE guild_id = ? AND key = ?"
            async with self.bot.db.execute(query, (guild_id, self.config_key)) as cursor:
                result = await cursor.fetchone()
                if result:
                    return result[0]  # Assume it's stored as the channel ID
            return None
        except Exception as e:
            print(f"[Welcome] Error loading channel ID: {str(e)}")
            return None
    
    async def save_welcome_channel(self, guild_id, channel_id):
        """Save the welcome channel ID to database"""
        try:
            # Use the bot's database connection/ORM to update the config
            # Adjust this based on your bot's configuration system
            query = """
                INSERT OR REPLACE INTO guild_config (guild_id, key, value)
                VALUES (?, ?, ?)
            """
            await self.bot.db.execute(query, (guild_id, self.config_key, channel_id))
            await self.bot.db.commit()
            
            # Update local cache
            self.welcome_channels[guild_id] = channel_id
            print(f"[Welcome] Saved channel ID {channel_id} for guild {guild_id}")
        except Exception as e:
            print(f"[Welcome] Error saving channel ID: {str(e)}")
    
    async def has_welcome_channel(self, guild_id):
        """Check if a welcome channel is configured"""
        # Check cache first
        if guild_id in self.welcome_channels and self.welcome_channels[guild_id]:
            return True
            
        # Check database
        channel_id = await self.load_welcome_channel(guild_id)
        if channel_id:
            self.welcome_channels[guild_id] = channel_id
            return True
            
        return False
    
    async def get_welcome_channel(self, guild_id):
        """Get the welcome channel ID"""
        # Check cache first
        if guild_id in self.welcome_channels:
            return self.welcome_channels[guild_id]
            
        # Load from database
        channel_id = await self.load_welcome_channel(guild_id)
        if channel_id:
            self.welcome_channels[guild_id] = channel_id
            
        return channel_id
    
    async def create_welcome_channel(self, guild):
        """Create a welcome channel for the guild"""
        try:
            # First try to find an existing welcome channel
            existing_channel = await self.find_existing_welcome_channel(guild)
            if existing_channel:
                print(f"[Welcome] Using existing welcome channel: {existing_channel.name} ({existing_channel.id})")
                await self.save_welcome_channel(guild.id, existing_channel.id)
                await self.update_welcome_channel(guild)
                return existing_channel
            
            # Create channel name with member count
            channel_name = f"🧑┆users-{guild.member_count}"
            
            # Set permissions: everyone can read but not send messages
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=False,
                    view_channel=True
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    view_channel=True,
                    manage_channels=True
                )
            }
            
            # Create the channel
            print(f"[Welcome] Creating new welcome channel for {guild.name}")
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"Welcome channel for new members | Last updated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            )
            
            # Save channel ID
            await self.save_welcome_channel(guild.id, channel.id)
            
            # Send initial message
            embed = discord.Embed(
                title="Welcome Channel Created",
                description=f"This channel will show welcome messages when new members join **{guild.name}**!",
                color=0x57F287  # Green color
            )
            embed.add_field(
                name="Current Member Count", 
                value=f"We currently have **{guild.member_count}** members!"
            )
            embed.timestamp = datetime.datetime.utcnow()
            
            await channel.send(embed=embed)
            print(f"[Welcome] Created new channel: {channel.name} ({channel.id}) in {guild.name}")
            
            return channel
        except discord.Forbidden:
            print(f"[Welcome] Missing permissions to create channel in {guild.name}")
            return None
        except Exception as e:
            print(f"[Welcome] Error creating channel: {str(e)}")
            traceback.print_exc()
            return None
    
    async def update_welcome_channel(self, guild):
        """Update an existing welcome channel"""
        try:
            # Get channel ID from our database
            channel_id = await self.get_welcome_channel(guild.id)
            channel = None
            
            if channel_id:
                # Try to get the channel from the guild
                channel = guild.get_channel(int(channel_id))
                
            # If we can't find the channel, try to find by name pattern
            if not channel:
                print(f"[Welcome] Channel {channel_id} not found in {guild.name}, searching by pattern")
                channel = await self.find_existing_welcome_channel(guild)
                
                # If we found a channel by pattern, update our database
                if channel:
                    await self.save_welcome_channel(guild.id, channel.id)
                    
            # If we still can't find the channel, create a new one
            if not channel:
                print(f"[Welcome] No welcome channel found in {guild.name}, creating new one")
                channel = await self.create_welcome_channel(guild)
                return channel is not None
                
            # Update channel name if needed
            new_name = f"🧑┆users-{guild.member_count}"
            if channel.name != new_name:
                try:
                    await channel.edit(
                        name=new_name,
                        topic=f"Welcome channel for new members | Last updated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                    )
                    print(f"[Welcome] Updated channel name to {new_name} in {guild.name}")
                except Exception as e:
                    print(f"[Welcome] Could not update channel name: {str(e)}")
                    
            return True
        except Exception as e:
            print(f"[Welcome] Error updating channel: {str(e)}")
            traceback.print_exc()
            return False
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Send welcome message when a new member joins"""
        try:
            # Check if feature is enabled
            if not self.bot.is_feature_enabled("welcome_channel", member.guild.id):
                return
                
            # Find or create welcome channel
            channel = await self.find_existing_welcome_channel(member.guild)
            if not channel:
                # Create channel if it doesn't exist
                channel = await self.create_welcome_channel(member.guild)
                if not channel:
                    return
            
            # Send welcome message
            welcome_embed = discord.Embed(
                title="New Member Joined!",
                description=f"Welcome {member.mention} to **{member.guild.name}**! 🎉",
                color=0x57F287  # Green color
            )
            
            # Add user avatar if available
            if hasattr(member, 'avatar') and member.avatar:
                welcome_embed.set_thumbnail(url=member.avatar.url)
            elif hasattr(member, 'display_avatar'):
                welcome_embed.set_thumbnail(url=member.display_avatar.url)
                
            welcome_embed.add_field(
                name="Member Count",
                value=f"We now have **{member.guild.member_count}** members!"
            )
            welcome_embed.timestamp = datetime.datetime.utcnow()
            
            await channel.send(embed=welcome_embed)
            print(f"[Welcome] Posted welcome message for {member.name} in {member.guild.name}")
            
            # Update channel name
            await self.update_welcome_channel(member.guild)
        except Exception as e:
            print(f"[Welcome] Error handling member join: {str(e)}")
            traceback.print_exc()
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Update channel name when a member leaves"""
        try:
            # Check if feature is enabled
            if not self.bot.is_feature_enabled("welcome_channel", member.guild.id):
                return
                
            # Update channel name
            await self.update_welcome_channel(member.guild)
        except Exception as e:
            print(f"[Welcome] Error handling member leave: {str(e)}")
    
    # Monitor feature enabling and create channel when enabled
    @commands.Cog.listener()
    async def on_guild_event(self, event_type, guild_id, data):
        """Handle events from the guild, including feature changes"""
        if event_type == "feature_changed" and data.get("feature") == "welcome_channel":
            enabled = data.get("enabled", False)
            if enabled:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    print(f"[Welcome] Feature was enabled for {guild.name}, checking for existing channel")
                    # Check for an existing welcome channel first
                    channel = await self.find_existing_welcome_channel(guild)
                    if channel:
                        print(f"[Welcome] Found existing channel {channel.name}, updating it")
                        await self.save_welcome_channel(guild.id, channel.id)
                        await self.update_welcome_channel(guild)
                    else:
                        print(f"[Welcome] No existing channel found, creating new one")
                        await self.create_welcome_channel(guild)

async def setup(bot: commands.Bot) -> None:
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Add hook to detect when feature is enabled
    # This is a monkey patch to detect feature enabling
    if not hasattr(bot, "_original_set_feature_enabled"):
        # Store original method
        bot._original_set_feature_enabled = bot.set_feature_enabled if hasattr(bot, "set_feature_enabled") else None
        
        # Create replacement method
        async def patched_set_feature_enabled(feature, guild_id, enabled):
            result = None
            
            # Call original method if it exists
            if bot._original_set_feature_enabled:
                result = await bot._original_set_feature_enabled(feature, guild_id, enabled)
            
            # Handle our feature specifically
            if feature == "welcome_channel" and enabled:
                print(f"[Welcome] Feature enabled for guild {guild_id}")
                
                # Run in background to avoid blocking
                async def create_welcome_channel_async():
                    # Get the welcome cog
                    welcome_cog = bot.get_cog("WelcomeChannelCog")
                    if welcome_cog:
                        # Get the guild
                        guild = bot.get_guild(guild_id)
                        if guild:
                            # Check for an existing channel first
                            channel = await welcome_cog.find_existing_welcome_channel(guild)
                            if channel:
                                print(f"[Welcome] Found existing channel {channel.name}, updating it")
                                await welcome_cog.save_welcome_channel(guild.id, channel.id)
                                await welcome_cog.update_welcome_channel(guild)
                            else:
                                print(f"[Welcome] No existing channel found, creating new one")
                                await welcome_cog.create_welcome_channel(guild)
                
                                # Run in background
                bot.loop.create_task(create_welcome_channel_async())
            
            return result
        
        # Apply patch if set_feature_enabled exists
        if hasattr(bot, "set_feature_enabled"):
            bot.set_feature_enabled = patched_set_feature_enabled
            print("[Welcome] Patched set_feature_enabled to detect feature enabling")
    
    # Register cog
    await bot.add_cog(WelcomeChannelCog(bot))
    
    @bot.tree.command(
        name="welcome_check",
        description="Check if the welcome channel exists and create it if needed"
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def welcome_check(interaction: discord.Interaction):
        # Check if feature is enabled
        if not bot.is_feature_enabled("welcome_channel", interaction.guild.id):
            await interaction.response.send_message(
                f"This feature is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Defer response
        await interaction.response.defer(thinking=True)
        
        # Get welcome cog
        welcome_cog = bot.get_cog("WelcomeChannelCog")
        if not welcome_cog:
            await interaction.followup.send("Welcome channel system is not available.")
            return
        
        # Find an existing welcome channel
        channel = await welcome_cog.find_existing_welcome_channel(interaction.guild)
        
        if channel:
            # Update existing channel
            await welcome_cog.save_welcome_channel(interaction.guild.id, channel.id)
            await welcome_cog.update_welcome_channel(interaction.guild)
            await interaction.followup.send(
                f"✅ Welcome channel is active: {channel.mention}\n"
                f"New members will receive welcome messages in this channel."
            )
        else:
            # Create new channel
            channel = await welcome_cog.create_welcome_channel(interaction.guild)
            if channel:
                await interaction.followup.send(
                    f"✅ Welcome channel has been created: {channel.mention}\n"
                    f"New members will receive welcome messages in this channel."
                )
            else:
                await interaction.followup.send(
                    "❌ Could not create welcome channel. Please make sure the bot has these permissions:\n"
                    "- Manage Channels\n"
                    "- Send Messages\n"
                    "- Embed Links"
                )
    
    @bot.tree.command(
        name="welcome_remove",
        description="Remove the welcome channel configuration"
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.describe(
        delete_channel="Delete the welcome channel (requires Manage Channels permission)"
    )
    async def welcome_remove(interaction: discord.Interaction, delete_channel: bool = False):
        # Check if feature is enabled
        if interaction.guild and not bot.is_feature_enabled("welcome_channel", interaction.guild.id):
            await interaction.response.send_message(
                f"This feature is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Get welcome cog
        welcome_cog = bot.get_cog("WelcomeChannelCog")
        if not welcome_cog:
            await interaction.response.send_message("Welcome channel system is not available.", ephemeral=True)
            return
        
        # Get channel ID
        channel_id = await welcome_cog.get_welcome_channel(interaction.guild.id)
        if not channel_id:
            # Try to find by pattern
            channel = await welcome_cog.find_existing_welcome_channel(interaction.guild)
            if not channel:
                await interaction.response.send_message("No welcome channel is configured.", ephemeral=True)
                return
            channel_id = channel.id
        else:
            channel = interaction.guild.get_channel(int(channel_id))
        
        # Clear config
        await welcome_cog.save_welcome_channel(interaction.guild.id, None)
        
        # Delete channel if requested
        if delete_channel and channel:
            try:
                await channel.delete(reason="Welcome channel removed")
                await interaction.response.send_message("Welcome channel deleted.", ephemeral=True)
                return
            except Exception as e:
                await interaction.response.send_message(
                    f"Could not delete channel: {str(e)}",
                    ephemeral=True
                )
                return
        
        channel_mention = f" ({channel.mention})" if channel else ""
        await interaction.response.send_message(
            f"Welcome channel configuration removed{channel_mention}. I will no longer post welcome messages.",
            ephemeral=True
        )
    
    # Add command to force create a welcome channel
    @bot.tree.command(
        name="welcome_force",
        description="Force create a welcome channel immediately"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_force(interaction: discord.Interaction):
        """Immediately create a welcome channel regardless of feature status"""
        # Defer response
        await interaction.response.defer(thinking=True)
        
        # Get welcome cog
        welcome_cog = bot.get_cog("WelcomeChannelCog")
        if not welcome_cog:
            await interaction.followup.send("Welcome channel system is not available.")
            return
        
        # Check for an existing channel first
        channel = await welcome_cog.find_existing_welcome_channel(interaction.guild)
        
        if channel:
            await welcome_cog.save_welcome_channel(interaction.guild.id, channel.id)
            await welcome_cog.update_welcome_channel(interaction.guild)
            await interaction.followup.send(
                f"✅ Found existing welcome channel: {channel.mention}\n"
                f"It has been updated with the latest member count."
            )
        else:
            # Create welcome channel
            channel = await welcome_cog.create_welcome_channel(interaction.guild)
            
            if channel:
                await interaction.followup.send(
                    f"✅ Welcome channel has been created: {channel.mention}\n"
                    f"New members will receive welcome messages in this channel."
                )
            else:
                await interaction.followup.send(
                    "❌ Could not create welcome channel. Please make sure the bot has these permissions:\n"
                    "- Manage Channels\n"
                    "- Send Messages\n"
                    "- Embed Links"
                )
    
    print(f"[Welcome] Module registered")