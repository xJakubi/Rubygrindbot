import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput
import asyncio
import datetime
import re
from azure.cosmos import CosmosClient, exceptions
import os
import time
import functools

# Command metadata
DISPLAY_NAME = "Ban Command"
DESCRIPTION = "Allows moderators to ban users for a specific duration"
ENABLED_BY_DEFAULT = False

# Database setup
cosmos_endpoint = os.getenv("COSMOS_ENDPOINT") 
cosmos_key = os.getenv("COSMOS_KEY") 
cosmos_database = os.getenv("COSMOS_DATABASE") 

# Global variables
ban_appeal_channel = {}  # guild_id -> channel_id mapping

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Function to parse time string
def parse_time(time_str):
    """Parse time string in format dd:hh:mm"""
    pattern = r"^(\d+):(\d+):(\d+)$"
    match = re.match(pattern, time_str)
    if not match:
        return None
        
    days, hours, minutes = map(int, match.groups())
    return datetime.timedelta(days=days, hours=hours, minutes=minutes)

async def update_user_permissions(guild, user_id, is_banned):
        try:
            # Get the member object from the guild
            member = guild.get_member(user_id)
            if not member:
                # Try to fetch member if not in cache
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    print(f"User {user_id} is no longer in the guild {guild.id}")
                    return False
            
            # Loop through all text and voice channels
            for channel in guild.channels:
                # Skip categories
                if isinstance(channel, discord.CategoryChannel):
                    continue
                    
                if is_banned:
                    # Ban: Deny read and send permissions
                    try:
                        await channel.set_permissions(member, read_messages=False, send_messages=False, 
                                                    connect=False, speak=False)
                    except Exception as e:
                        print(f"Error setting permissions in channel {channel.id}: {e}")
                else:
                    # Unban: Reset permissions to None (use default)
                    try:
                        await channel.set_permissions(member, overwrite=None)
                    except Exception as e:
                        print(f"Error resetting permissions in channel {channel.id}: {e}")
                        
            return True
        except Exception as e:
            print(f"Error updating permissions: {e}")
            return False
# Function to save ban to database
async def save_ban(guild_id, user_id, moderator_id, reason, duration, client, guild=None):
    try:
        # Get the database - don't try to create it
        database = client.get_database_client(cosmos_database)
        
        # Check if container exists first
        containers = list(database.list_containers())
        container_exists = any(c['id'] == "user_bans" for c in containers)
        
        # Create container only if it doesn't exist
        if not container_exists:
            try:
                database.create_container(
                    id="user_bans",
                    partition_key={"paths": ["/guild_id"]},
                    offer_throughput=400
                )
                print("Created user_bans container successfully")
            except Exception as e:
                print(f"Error creating container: {e}")
                # Try to proceed anyway with getting the container
        
        # Get the container client
        container = database.get_container_client("user_bans")
        
        # Calculate end time
        end_time = datetime.datetime.now() + duration
        
        # Create ban record
        ban_record = {
            "id": f"{guild_id}_{user_id}",
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "moderator_id": str(moderator_id),
            "reason": reason,
            "end_time": end_time.isoformat(),
            "ban_time": datetime.datetime.now().isoformat(),
            "appeal_submitted": False,
            "appeal_text": "",
            "appeal_status": "none"  # none, accepted, rejected
        }
        
        # Save to database
        await asyncio.to_thread(container.upsert_item, ban_record)
        
        # Update user permissions if guild provided
        if guild:
            try:
                await update_user_permissions(guild, user_id, True)
            except Exception as e:
                print(f"Error updating permissions during ban: {e}")
        
        return True
    except Exception as e:
        print(f"Error saving ban: {e}")
        return False

# Function to check if a user is banned
async def is_user_banned(guild_id, user_id, client):
    try:
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="user_bans",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        # Now get the container client
        container = database.get_container_client("user_bans")
        
        # Query for active bans
        query = "SELECT * FROM c WHERE c.guild_id = @guild_id AND c.user_id = @user_id"
        parameters = [
            {"name": "@guild_id", "value": str(guild_id)},
            {"name": "@user_id", "value": str(user_id)}
        ]
        
        results = container.query_items(query=query, parameters=parameters, enable_cross_partition_query=True)
        ban_records = list(results)
        
        if not ban_records:
            return False, None
        
        ban = ban_records[0]
        end_time = datetime.datetime.fromisoformat(ban["end_time"])
        
        # Check if ban has expired
        if datetime.datetime.now() > end_time:
            # Ban expired, delete it
            await asyncio.to_thread(container.delete_item, item=ban["id"], partition_key=str(guild_id))
            return False, None
            
        return True, ban
    except Exception as e:
        print(f"Error checking ban status: {e}")
        return False, None

# Function to remove ban
async def remove_ban(guild_id, user_id, client, guild=None):
    try:
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="user_bans",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        container = database.get_container_client("user_bans")
        
        ban_id = f"{guild_id}_{user_id}"
        await asyncio.to_thread(container.delete_item, item=ban_id, partition_key=str(guild_id))
        
        # Restore user permissions if guild provided
        if guild:
            try:
                await update_user_permissions(guild, user_id, False)
            except Exception as e:
                print(f"Error updating permissions during unban: {e}")
        
        return True
    except exceptions.CosmosResourceNotFoundError:
        return False
    except Exception as e:
        print(f"Error removing ban: {e}")
        return False

# Function to save appeal channel setting
async def save_appeal_channel(guild_id, channel_id, client):
    try:
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="guild_settings",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        container = database.get_container_client("guild_settings")
        
        # Try to get existing settings
        try:
            settings = await asyncio.to_thread(
                container.read_item,
                item=str(guild_id),
                partition_key=str(guild_id)
            )
        except exceptions.CosmosResourceNotFoundError:
            settings = {
                "id": str(guild_id),
                "guild_id": str(guild_id)
            }
        
        # Update with ban appeal channel
        settings["ban_appeal_channel"] = str(channel_id)
        
        # Save settings
        await asyncio.to_thread(container.upsert_item, settings)
        
        # Also update the global dict
        ban_appeal_channel[str(guild_id)] = channel_id
        return True
    except Exception as e:
        print(f"Error saving appeal channel setting: {e}")
        return False

# Function to get appeal channel setting
async def get_appeal_channel(guild_id, client):
    try:
        # Check cache first
        if str(guild_id) in ban_appeal_channel:
            return ban_appeal_channel[str(guild_id)]
        
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="guild_settings",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        container = database.get_container_client("guild_settings")
        
        try:
            settings = await asyncio.to_thread(
                container.read_item,
                item=str(guild_id),
                partition_key=str(guild_id)
            )
            
            channel_id = settings.get("ban_appeal_channel")
            if channel_id:
                ban_appeal_channel[str(guild_id)] = int(channel_id)
                return int(channel_id)
            return None
        except exceptions.CosmosResourceNotFoundError:
            return None
    except Exception as e:
        print(f"Error getting appeal channel: {e}")
        return None

# Function to update ban appeal status
async def update_appeal(guild_id, user_id, appeal_text, client):
    try:
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="user_bans",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        container = database.get_container_client("user_bans")
        
        ban_id = f"{guild_id}_{user_id}"
        
        try:
            ban = await asyncio.to_thread(
                container.read_item,
                item=ban_id,
                partition_key=str(guild_id)
            )
            
            ban["appeal_submitted"] = True
            ban["appeal_text"] = appeal_text
            
            await asyncio.to_thread(container.upsert_item, ban)
            return True
        except exceptions.CosmosResourceNotFoundError:
            return False
    except Exception as e:
        print(f"Error updating appeal: {e}")
        return False

# Function to update appeal status
async def update_appeal_status(guild_id, user_id, status, reason, client, guild=None):
    try:
        # Create database if not exists
        database = client.create_database_if_not_exists(id=cosmos_database)
        
        # Create container if not exists
        database.create_container_if_not_exists(
            id="user_bans",
            partition_key="/guild_id",
            offer_throughput=400
        )
        
        container = database.get_container_client("user_bans")
        
        ban_id = f"{guild_id}_{user_id}"
        
        try:
            ban = await asyncio.to_thread(
                container.read_item,
                item=ban_id,
                partition_key=str(guild_id)
            )
            
            ban["appeal_status"] = status
            ban["appeal_response"] = reason
            
            await asyncio.to_thread(container.upsert_item, ban)
            
            # If accepted, remove the ban
            if status == "accepted":
                # First remove from database
                await remove_ban(guild_id, user_id, client)
                
                # Then restore permissions if guild provided
                if guild:
                    try:
                        await update_user_permissions(guild, int(user_id), False)
                    except Exception as e:
                        print(f"Error updating permissions after appeal acceptance: {e}")
                
            return True
        except exceptions.CosmosResourceNotFoundError:
            return False
    except Exception as e:
        print(f"Error updating appeal status: {e}")
        return False
    
# Ban Appeal Modal
class AppealModal(Modal, title="Ban Appeal"):
    appeal_text = TextInput(
        label="Your appeal",
        style=discord.TextStyle.paragraph,
        placeholder="Explain why your ban should be removed",
        required=True,
        max_length=1000
    )
    
    def __init__(self, bot, user_id, guild_id):
        super().__init__()
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
    
    async def on_submit(self, interaction: discord.Interaction):
        # Immediately acknowledge the interaction to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Initialize Cosmos client
        client = CosmosClient(cosmos_endpoint, credential=cosmos_key)
        
        # Update ban with appeal
        success = await update_appeal(self.guild_id, self.user_id, self.appeal_text.value, client)
        
        if not success:
            await interaction.followup.send("Failed to submit appeal. The ban may have expired or been removed.", ephemeral=True)
            return
            
        # Get the appeal channel
        channel_id = await get_appeal_channel(self.guild_id, client)
        
        if not channel_id:
            await interaction.followup.send("Your appeal has been recorded, but the server administrators haven't set up an appeal channel yet.", ephemeral=True)
            return
            
        # Send appeal to the channel
        channel = self.bot.get_channel(channel_id)
        
        if not channel:
            await interaction.followup.send("Your appeal has been recorded, but I couldn't find the appeal channel.", ephemeral=True)
            return
            
        # Get user and moderator info
        banned_user = await self.bot.fetch_user(self.user_id)
        
        # Check ban info
        _, ban_info = await is_user_banned(self.guild_id, self.user_id, client)
        moderator_id = int(ban_info["moderator_id"])
        moderator = await self.bot.fetch_user(moderator_id)
        
        # Create embed
        embed = discord.Embed(
            title="Ban Appeal",
            description=f"**{banned_user.name}** has submitted an appeal for their ban.",
            color=discord.Color.gold()
        )
        
        embed.add_field(name="User", value=f"{banned_user.mention} ({banned_user.id})", inline=True)
        embed.add_field(name="Banned By", value=f"{moderator.mention} ({moderator.id})", inline=True)
        embed.add_field(name="Ban Reason", value=ban_info["reason"], inline=False)
        embed.add_field(name="Appeal", value=self.appeal_text.value, inline=False)
        embed.set_thumbnail(url=banned_user.display_avatar.url)
        embed.timestamp = datetime.datetime.now()
        
        # Create buttons for accept/deny
        class AppealView(View):
            def __init__(self, bot, user_id, guild_id):
                super().__init__(timeout=None)
                self.bot = bot
                self.user_id = user_id
                self.guild_id = guild_id
            
            @discord.ui.button(label="Accept Appeal", style=discord.ButtonStyle.success, custom_id=f"accept_appeal_{self.user_id}")
            async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Create response modal
                modal = ResponseModal(self.bot, self.user_id, self.guild_id, "accepted")
                await interaction.response.send_modal(modal)
            
            @discord.ui.button(label="Deny Appeal", style=discord.ButtonStyle.danger, custom_id=f"deny_appeal_{self.user_id}")
            async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Create response modal
                modal = ResponseModal(self.bot, self.user_id, self.guild_id, "rejected")
                await interaction.response.send_modal(modal)
        
        view = AppealView(self.bot, self.user_id, self.guild_id)
        await channel.send(embed=embed, view=view)
        
        await interaction.followup.send("Your appeal has been submitted. You'll be notified when moderators review it.", ephemeral=True)

# Response Modal for appeal decisions
class ResponseModal(Modal):
    def __init__(self, bot, user_id, guild_id, action):
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id
        self.action = action
        
        if action == "accepted":
            title = "Accept Appeal Reason"
        else:
            title = "Deny Appeal Reason"
            
        super().__init__(title=title)
        
        self.response = TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder=f"Reason for {action} the appeal",
            required=True,
            max_length=1000
        )
        self.add_item(self.response)
    
    async def on_submit(self, interaction: discord.Interaction):
        # Immediately acknowledge the interaction with a deferral
        await interaction.response.defer(ephemeral=True)
        
        # Initialize Cosmos client
        client = CosmosClient(cosmos_endpoint, credential=cosmos_key)
        
        # Update appeal status
        success = await update_appeal_status(self.guild_id, self.user_id, self.action, self.response.value, client, interaction.guild)
        
        if not success:
            await interaction.followup.send("Failed to process appeal. The ban may have expired or been removed.", ephemeral=True)
            return
            
        # Notify the user via DM
        user = await self.bot.fetch_user(self.user_id)
        
        try:
            if self.action == "accepted":
                embed = discord.Embed(
                    title="Ban Appeal Accepted",
                    description="Your ban appeal has been accepted. You can now return to the server.",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="Ban Appeal Denied",
                    description="Your ban appeal has been denied.",
                    color=discord.Color.red()
                )
                
            embed.add_field(name="Reason", value=self.response.value)
            embed.timestamp = datetime.datetime.now()
            
            await user.send(embed=embed)
        except Exception as e:
            print(f"Failed to DM user about appeal decision: {e}")
        
        # Create a new embed for the channel message
        if self.action == "accepted":
            embed = discord.Embed(
                title="Ban Appeal Accepted",
                description=f"Appeal for <@{self.user_id}> was accepted by {interaction.user.mention}",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="Ban Appeal Denied",
                description=f"Appeal for <@{self.user_id}> was denied by {interaction.user.mention}",
                color=discord.Color.red()
            )
        
        embed.add_field(name="Reason", value=self.response.value)
        embed.timestamp = datetime.datetime.now()
        
        # Send followup response
        await interaction.followup.send(embed=embed)
        
        # Try to find and update the original message
        try:
            channel = interaction.channel
            if channel:
                async for message in channel.history(limit=50):
                    if message.author == self.bot.user and len(message.embeds) > 0:
                        orig_embed = message.embeds[0]
                        if orig_embed.title == "Ban Appeal" and f"<@{self.user_id}>" in orig_embed.description:
                            await message.edit(embed=embed, view=None)
                            break
        except Exception as e:
            print(f"Failed to update original appeal message: {e}")


# Setup function to register commands
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__

    # Initialize Cosmos client
    client = CosmosClient(cosmos_endpoint, credential=cosmos_key)
    
    # Create necessary containers
    try:
        # Get the database - don't try to create it
        database = client.get_database_client(cosmos_database)
        
        # Check if container exists first
        containers = list(database.list_containers())
        user_bans_exists = any(c['id'] == "user_bans" for c in containers)
        guild_settings_exists = any(c['id'] == "guild_settings" for c in containers)
        
        # Create user_bans container if it doesn't exist
        if not user_bans_exists:
            try:
                database.create_container(
                    id="user_bans",
                    partition_key={"paths": ["/guild_id"]},
                    offer_throughput=400
                )
                print("Created user_bans container successfully")
            except Exception as e:
                print(f"Error creating user_bans container: {e}")
        
        # Create guild_settings container if it doesn't exist
        if not guild_settings_exists:
            try:
                database.create_container(
                    id="guild_settings",
                    partition_key={"paths": ["/guild_id"]},
                    offer_throughput=400
                )
                print("Created guild_settings container successfully")
            except Exception as e:
                print(f"Error creating guild_settings container: {e}")
    except Exception as e:
        print(f"Error setting up ban database: {e}")
    
    # Ban command
    @bot.tree.command(name="ban", description="Ban a user for a specified duration")
    @app_commands.describe(
        user="The user to ban",
        reason="Reason for the ban",
        time="Ban duration in format days:hours:minutes (e.g., 01:00:00 for 1 day)"
    )
    @app_commands.default_permissions(ban_members=True)
    async def ban_command(interaction: discord.Interaction, user: discord.Member, reason: str, time: str):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Parse the time string
        duration = parse_time(time)
        if not duration:
            await interaction.response.send_message("Invalid time format. Please use dd:hh:mm (days:hours:minutes)", ephemeral=True)
            return
            
        # Check permissions
        if interaction.user.top_role <= user.top_role:
            await interaction.response.send_message("You can't ban someone with a higher or equal role to yours.", ephemeral=True)
            return
            
        if user.id == bot.user.id:
            await interaction.response.send_message("I can't ban myself!", ephemeral=True)
            return
            
        if user.id == interaction.guild.owner_id:
            await interaction.response.send_message("I can't ban the server owner!", ephemeral=True)
            return
        
        # Acknowledge the command immediately to prevent timeout
        await interaction.response.defer()
        
        # Save ban to database
        success = await save_ban(
            interaction.guild.id,
            user.id,
            interaction.user.id,
            reason,
            duration,
            client,
            interaction.guild  # Pass the guild object
        )
        
        if not success:
            await interaction.followup.send("Failed to ban user. Please try again later.")
            return
        
        # DM user
        try:
            # Format ban duration
            days = duration.days
            hours, remainder = divmod(duration.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            duration_text = f"{days} day(s), {hours} hour(s), {minutes} minute(s)"
            
            embed = discord.Embed(
                title=f"You've been banned from {interaction.guild.name}",
                description=f"You have been banned for {duration_text}",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason)
            
            # Create appeal button
            class AppealView(View):
                def __init__(self, bot, user_id, guild_id):
                    super().__init__(timeout=None)
                    self.bot = bot
                    self.user_id = user_id
                    self.guild_id = guild_id
                
                @discord.ui.button(label="Appeal Ban", style=discord.ButtonStyle.primary, custom_id=f"appeal_ban_{user.id}")
                async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Check if already appealed
                    _, ban_info = await is_user_banned(self.guild_id, self.user_id, client)
                    
                    if ban_info and ban_info.get("appeal_submitted"):
                        await interaction.response.send_message("You have already submitted an appeal. You can only appeal once.", ephemeral=True)
                        return
                        
                    # Show appeal modal
                    modal = AppealModal(self.bot, self.user_id, self.guild_id)
                    await interaction.response.send_modal(modal)
            
            view = AppealView(bot, user.id, interaction.guild.id)
            
            await user.send(embed=embed, view=view)
        except Exception as e:
            print(f"Error sending DM to banned user: {e}")
            # Continue execution - don't return, as we still want to send the message in the channel
        
        # Create banned message with gif - using followup since we used defer
        embed = discord.Embed(
            title=f"User Banned",
            description=f"{user.mention} has been banned by {interaction.user.mention}",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Duration", value=f"{time} (dd:hh:mm)", inline=True)
        # Fix GIF URL - using direct media URL for the Bane GIF
        embed.set_image(url="https://media.tenor.com/GDmDsgsTdesAAAAd/bane-no.gif")

        await interaction.followup.send(embed=embed)

        
        
        await interaction.followup.send(embed=embed)
    
    # Unban command
    @bot.tree.command(name="unban", description="Unban a user")
    @app_commands.describe(
        user="The user to unban (user ID or mention)"
    )
    @app_commands.default_permissions(ban_members=True)
    async def unban_command(interaction: discord.Interaction, user: discord.User):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Defer the response to prevent timeout
        await interaction.response.defer()
        
        # Remove ban from database
        success = await remove_ban(
            interaction.guild.id, 
            user.id, 
            client,
            interaction.guild  # Pass the guild object
        )
        
        if not success:
            await interaction.followup.send(f"{user.mention} is not banned or an error occurred.", ephemeral=True)
            return
            
        # Notify unban
        embed = discord.Embed(
            title="User Unbanned",
            description=f"{user.mention} has been unbanned by {interaction.user.mention}",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    
    # Set ban appeal channel command
    @bot.tree.command(name="setbanappeal", description="Set the channel for ban appeals")
    @app_commands.describe(
        channel="The channel where ban appeals will be sent"
    )
    @app_commands.default_permissions(administrator=True)
    async def set_appeal_channel_command(interaction: discord.Interaction, channel: discord.TextChannel):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check bot permissions in the channel
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(f"I don't have permission to send messages in {channel.mention}.", ephemeral=True)
            return
            
        # Save the channel to database
        success = await save_appeal_channel(interaction.guild.id, channel.id, client)
        
        if not success:
            await interaction.response.send_message("Failed to set appeal channel. Please try again later.", ephemeral=True)
            return
            
        await interaction.response.send_message(f"Ban appeals will now be sent to {channel.mention}.")

    # Message event handler to block banned users
    @bot.listen('on_message')
    async def ban_message_filter(message):
        # Skip bot messages and DMs
        if message.author.bot or not message.guild:
            return
            
        # Skip if feature is disabled
        if not bot.is_feature_enabled(feature_name, message.guild.id):
            return
        
        # Check if user is banned
        is_banned, _ = await is_user_banned(message.guild.id, message.author.id, client)
        
        if is_banned:
            try:
                await message.delete()
            except:
                pass

    @bot.listen('on_member_join')
    async def check_banned_on_join(member):
        # Skip if feature is disabled
        if not bot.is_feature_enabled(feature_name, member.guild.id):
            return
        
        # Check if user is banned
        is_banned, _ = await is_user_banned(member.guild.id, member.id, client)
        
        if is_banned:
            try:
                # Apply ban permissions
                await update_user_permissions(member.guild, member.id, True)
            except Exception as e:
                print(f"Error applying ban restrictions on user join: {e}")    
# Create background task to check for expired bans
    async def check_expired_bans():
        await bot.wait_until_ready()
        
        while not bot.is_closed():
            try:
                # Create database if not exists
                database = client.create_database_if_not_exists(id=cosmos_database)
                
                # Create container if not exists
                database.create_container_if_not_exists(
                    id="user_bans",
                    partition_key="/guild_id",
                    offer_throughput=400
                )
                
                container = database.get_container_client("user_bans")
                
                # Query for all bans that have expired
                now = datetime.datetime.now().isoformat()
                query = "SELECT * FROM c WHERE c.end_time <= @now"
                parameters = [{"name": "@now", "value": now}]
                
                results = container.query_items(query=query, parameters=parameters, enable_cross_partition_query=True)
                expired_bans = list(results)
                
                for ban in expired_bans:
                    try:
                        # Delete ban from database
                        await asyncio.to_thread(container.delete_item, item=ban["id"], partition_key=ban["guild_id"])
                        
                        # Get the guild
                        guild_id = int(ban["guild_id"])
                        guild = bot.get_guild(guild_id)
                        
                        if guild:
                            # Restore user permissions
                            user_id = int(ban["user_id"])
                            await update_user_permissions(guild, user_id, False)
                            
                        print(f"Removed expired ban for user {ban['user_id']} in guild {ban['guild_id']}")
                    except Exception as e:
                        print(f"Error removing expired ban: {e}")
                
            except Exception as e:
                print(f"Error checking expired bans: {e}")
                
            # Check every 10 minutes
            await asyncio.sleep(600)

    





    # Start the background task
    bot.loop.create_task(check_expired_bans())

