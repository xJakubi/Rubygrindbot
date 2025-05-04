import discord
from discord.ext import commands
from discord import app_commands
import functools
from discord.ui import View, Button
import json
import asyncio
from azure.cosmos import CosmosClient, exceptions

DISPLAY_NAME = "Announcements"
DESCRIPTION = "Transforms messages in a designated channel into clean, professional announcements with additional options"
ENABLED_BY_DEFAULT = False

# Constants
ANNOUNCEMENT_CHANNEL_ID_KEY = "announcement_channel_id"
DM_OPT_OUT_CONTAINER = "dm_opt_outs"

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Initialize Cosmos DB container for DM opt-outs
async def init_cosmos_db(bot):
    """Initialize connection to Cosmos DB and create container if needed"""
    import os
    from azure.cosmos import CosmosClient, PartitionKey

    # Get Cosmos DB configuration from environment
    cosmos_endpoint = os.environ.get("COSMOS_ENDPOINT")
    cosmos_key = os.environ.get("COSMOS_KEY")
    cosmos_database = os.environ.get("COSMOS_DATABASE")
    
    if not all([cosmos_endpoint, cosmos_key, cosmos_database]):
        print("Cosmos DB configuration incomplete. DM opt-out settings will not be saved.")
        return None, None
    
    try:
        client = CosmosClient(cosmos_endpoint, credential=cosmos_key)
        database = client.get_database_client(cosmos_database)
        
        # Create container if it doesn't exist
        try:
            container = database.create_container_if_not_exists(
                id=DM_OPT_OUT_CONTAINER,
                partition_key=PartitionKey(path="/user_id"),
                offer_throughput=400
            )
        except Exception as e:
            print(f"Error creating DM opt-out container: {e}")
            return client, None
            
        return client, container
    except Exception as e:
        print(f"Error connecting to Cosmos DB: {e}")
        return None, None

# Functions to manage DM opt-out settings
async def is_user_opted_out(cosmos_container, user_id):
    """Check if user has opted out of announcement DMs"""
    if not cosmos_container:
        return False
        
    try:
        item = await asyncio.to_thread(
            cosmos_container.read_item,
            item=str(user_id),
            partition_key=str(user_id)
        )
        return item.get("opted_out", False)
    except exceptions.CosmosResourceNotFoundError:
        return False
    except Exception as e:
        print(f"Error checking opt-out status: {e}")
        return False

async def set_user_opt_out(cosmos_container, user_id, opted_out=True):
    """Set user's opt-out preference for announcement DMs"""
    if not cosmos_container:
        return False
        
    try:
        data = {
            "id": str(user_id),
            "user_id": str(user_id),
            "opted_out": opted_out
        }
        await asyncio.to_thread(cosmos_container.upsert_item, data)
        return True
    except Exception as e:
        print(f"Error saving opt-out preference: {e}")
        return False

# Custom View for announcement messages
class AnnouncementView(View):
    def __init__(self, author_id, cosmos_container):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.cosmos_container = cosmos_container

    @discord.ui.button(label="Re-announce", style=discord.ButtonStyle.primary, custom_id="reannounce")
    async def reannounce(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user is administrator
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can re-announce messages.", ephemeral=True)
            return
            
        # Get the announcement content
        original_message = interaction.message
        content = original_message.content
        embeds = original_message.embeds
        
        # Delete original message
        await original_message.delete()
        
        # Re-send the announcement
        new_view = AnnouncementView(interaction.user.id, self.cosmos_container)
        await interaction.channel.send(content=content, embeds=embeds, view=new_view)
        
        # Confirm to the admin that message was re-announced
        await interaction.response.send_message("Announcement has been re-posted.", ephemeral=True)

    @discord.ui.button(label="Send via DM", style=discord.ButtonStyle.success, custom_id="send_dm")
    async def send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user is administrator
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can send announcements via DM.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Get the announcement content
        original_message = interaction.message
        content = original_message.content
        embeds = original_message.embeds
        
        # Prepare DM view with opt-out button
        class DMView(View):
            def __init__(self, cosmos_container):
                super().__init__(timeout=None)
                self.cosmos_container = cosmos_container
                
            @discord.ui.button(label="Don't receive future announcements via DM", 
                            style=discord.ButtonStyle.secondary, 
                            custom_id="opt_out_dms")
            async def opt_out(self, dm_interaction: discord.Interaction, button: discord.ui.Button):
                success = await set_user_opt_out(self.cosmos_container, dm_interaction.user.id, True)
                if success:
                    await dm_interaction.response.send_message("You've been opted out of future announcement DMs.", ephemeral=True)
                else:
                    await dm_interaction.response.send_message("There was an error opting out. Please try again later.", ephemeral=True)
        
        # Get all members in guild
        members = interaction.guild.members
        sent_count = 0
        failed_count = 0
        opted_out_count = 0
        
        # Send DM to each member who hasn't opted out
        for member in members:
            if member.bot:
                continue
                
            # Check if user has opted out
            if await is_user_opted_out(self.cosmos_container, member.id):
                opted_out_count += 1
                continue
                
            try:
                dm_view = DMView(self.cosmos_container)
                await member.send(
                    content=f"**Announcement from {interaction.guild.name}**\n\n{content}",
                    embeds=embeds,
                    view=dm_view
                )
                sent_count += 1
            except discord.Forbidden:
                # User has DMs disabled
                failed_count += 1
            except Exception as e:
                print(f"Error sending DM to {member}: {e}")
                failed_count += 1
                
        await interaction.followup.send(
            f"Announcement sent via DM:\n"
            f"- Successfully sent: {sent_count}\n"
            f"- Failed to send: {failed_count}\n"
            f"- Users opted out: {opted_out_count}",
            ephemeral=True
        )

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Initialize Cosmos DB connection for DM opt-outs
    cosmos_client, cosmos_container = await init_cosmos_db(bot)
    
    @bot.tree.command(name="setannouncementchannel", description="Set the channel for announcements")
    @app_commands.default_permissions(administrator=True)
    async def set_announcement_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The announcements feature is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Check if user is administrator
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can set the announcement channel.", ephemeral=True)
            return
            
        # Save channel ID to guild settings
        guild_settings = bot.get_guild_settings(interaction.guild.id)
        guild_settings[ANNOUNCEMENT_CHANNEL_ID_KEY] = channel.id
        bot.save_guild_settings(interaction.guild.id, guild_settings)
        
        await interaction.response.send_message(
            f"Announcement channel has been set to {channel.mention}. "
            f"All messages sent in this channel will be converted into announcements.",
            ephemeral=True
        )

    @bot.listen('on_message')
    async def handle_announcement_message(message):
        # Skip if not in a guild or if message is from the bot
        if not message.guild or message.author.bot:
            return
            
        # Skip if feature is disabled
        if not bot.is_feature_enabled(feature_name, message.guild.id):
            return
            
        # Get announcement channel ID from guild settings
        guild_settings = bot.get_guild_settings(message.guild.id)
        announcement_channel_id = guild_settings.get(ANNOUNCEMENT_CHANNEL_ID_KEY)
        
        # Skip if no announcement channel is set or if message is not in the announcement channel
        if not announcement_channel_id or message.channel.id != announcement_channel_id:
            return
            
        # Message is in the announcement channel - repost it as an announcement from the bot
        try:
            # Create embed for attachments if any
            embeds = []
            if message.embeds:
                embeds.extend(message.embeds)
                
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.url.lower().endswith(('png', 'jpg', 'jpeg', 'gif')):
                        embed = discord.Embed()
                        embed.set_image(url=attachment.url)
                        embeds.append(embed)
            
            # Delete original message
            await message.delete()
            
            # Send the announcement message with buttons
            view = AnnouncementView(message.author.id, cosmos_container)
            await message.channel.send(content=message.content, embeds=embeds, view=view)
        except discord.Forbidden:
            # Bot doesn't have permission to delete messages
            await message.channel.send(
                "I don't have permission to manage messages in this channel. "
                "Please give me the 'Manage Messages' permission to use announcement features.",
                delete_after=10
            )
        except Exception as e:
            print(f"Error processing announcement: {e}")