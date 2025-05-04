import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import re
import traceback
from azure.cosmos import CosmosClient, PartitionKey, exceptions

DISPLAY_NAME = "Link Setup"
DESCRIPTION = "Sends a permanent verification embed with a Verify button for linking your Discord account with your in-game name."
ENABLED_BY_DEFAULT = False  # Disabled by default
bot = None
# Use environment variables if available; otherwise, fall back to default values.
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT") or "https://thefinalsdb.documents.azure.com:443/"
COSMOS_KEY = os.getenv("COSMOS_KEY") or "=="
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE") or ""

def sanitize_id(text):
    """Sanitize text for use as a Cosmos DB document ID."""
    if not text:
        return f"unknown_{int(datetime.datetime.now().timestamp())}"
    return re.sub(r'[\\/?#]', '', text)

async def link_user(discord_id: int, discord_name: str, in_game_name: str) -> bool:
    """
    Link a Discord user to their in-game name in Cosmos DB.
    This creates the database and the "user_links" container if they don't exist, then inserts a document.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="user_links",
            partition_key=PartitionKey(path="/discord_id"),
            offer_throughput=400  # Adjust throughput as needed.
        )
        timestamp = int(datetime.datetime.now().timestamp())
        doc_id = f"{sanitize_id(str(discord_id))}_{timestamp}"
        # Store normalized (lowercase) version for case-insensitive matching
        normalized_name = in_game_name.lower()
        document = {
            "id": doc_id,
            "discord_id": discord_id,
            "discord_name": discord_name,
            "in_game_name": in_game_name,
            "normalized_name": normalized_name,  # Add normalized version for matching
            "timestamp": datetime.datetime.now().isoformat()
        }
        await asyncio.to_thread(container.create_item, document)
        print(f"User [{discord_name} | {discord_id}] linked with in-game name [{in_game_name}]")
        
        # Try to log this event if the server_logs cog is loaded
        try:
                logs_cog = bot.get_cog('ServerLogsCog')
                if logs_cog is not None:
                    # Look for the user in all guilds
                    for guild in bot.guilds:
                        member = guild.get_member(discord_id)
                        if member:
                            print(f"Found member {member} in guild {guild.name}, attempting to log verification")
                            # Check the method signature first to determine parameter names
                            import inspect
                            params = inspect.signature(logs_cog.handle_verification_log).parameters
                            print(f"Available parameters: {list(params.keys())}")
                            
                            # Try with various parameter combinations
                            try:
                                # Try default approach with updated parameter name
                                success = await logs_cog.handle_verification_log(
                                    member=member,
                                    reason=f"Linked with: {in_game_name}",  # Try 'reason' instead of 'verification_info'
                                    image_url=None
                                )
                                print(f"Log verification success: {success}")
                                if success:
                                    break  # Successfully logged, no need to try other guilds
                            except TypeError as param_error:
                                print(f"Parameter error: {param_error}")
                                # If that fails, try with positional arguments only
                                try:
                                    success = await logs_cog.handle_verification_log(member, f"Linked with: {in_game_name}", None)
                                    print(f"Log verification success with positional args: {success}")
                                    if success:
                                        break
                                except Exception as pos_error:
                                    print(f"Positional args error: {pos_error}")
        except Exception as log_error:
            print(f"Error logging link verification: {log_error}")
            traceback.print_exc()  # Print the full stack trace for debugging
        
        return True
    except exceptions.CosmosHttpResponseError as e:
        print(f"Failed to link user in Cosmos DB: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error linking user: {e}")
        return False

async def get_user_link(discord_id: int, in_game_name: str = None) -> dict:
    """
    Get the most recent link for a specific Discord user.
    If in_game_name is provided, it will match case-insensitively.
    Returns the link document or None if not found.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="user_links",
            partition_key=PartitionKey(path="/discord_id"),
            offer_throughput=400
        )

        # Query parameters
        parameters = [{"name": "@discord_id", "value": discord_id}]
        
        if in_game_name:
            # Case-insensitive search if in_game_name is provided
            normalized_name = in_game_name.lower()
            query = f"SELECT TOP 1 * FROM c WHERE c.discord_id = @discord_id AND c.normalized_name = @normalized_name ORDER BY c.timestamp DESC"
            parameters.append({"name": "@normalized_name", "value": normalized_name})
        else:
            # Just get the most recent link for this user
            query = f"SELECT TOP 1 * FROM c WHERE c.discord_id = @discord_id ORDER BY c.timestamp DESC"
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            return items[0]  # Return the most recent link
        return None
    except Exception as e:
        print(f"Error retrieving user link: {e}")
        return None

async def delete_user_link(discord_id: int) -> bool:
    """
    Delete all links associated with a Discord user.
    Returns True if successful, False otherwise.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="user_links",
            partition_key=PartitionKey(path="/discord_id"),
            offer_throughput=400
        )
        
        # Query to find all links for this Discord ID
        query = "SELECT * FROM c WHERE c.discord_id = @discord_id"
        parameters = [{"name": "@discord_id", "value": discord_id}]
        
        items_to_delete = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        print(f"Found {len(items_to_delete)} links to delete for Discord ID {discord_id}")
        
        if not items_to_delete:
            print(f"No links found for Discord ID {discord_id}")
            return False
        
        # Delete each found item - with error handling for each item
        success_count = 0
        for item in items_to_delete:
            try:
                # Print the item details for debugging
                print(f"Attempting to delete item: ID={item['id']}, PartitionKey={item['discord_id']}")
                
                # Try to delete using the actual values from the item
                container.delete_item(
                    item=item['id'],
                    partition_key=item['discord_id']
                )
                success_count += 1
            except exceptions.CosmosHttpResponseError as e:
                print(f"Error deleting item {item['id']}: {e}")
                # Try an alternative approach with string partition key
                try:
                    container.delete_item(
                        item=item['id'],
                        partition_key=str(item['discord_id'])
                    )
                    success_count += 1
                    print(f"Successfully deleted using string partition key")
                except Exception as e2:
                    print(f"Alternative delete also failed: {e2}")
                    
        print(f"Successfully deleted {success_count} out of {len(items_to_delete)} links")
        return success_count > 0
    except Exception as e:
        print(f"Error deleting user links: {e}")
        return False

# New functions to save and retrieve the verification configuration (target guild and channel IDs) from Cosmos DB.
async def save_verification_config(guild_id: int, channel_id: int) -> bool:
    """
    Save the verification configuration (guild ID and channel ID) into Cosmos DB.
    This uses the container "verification_config" with a single document.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        config_container = database.create_container_if_not_exists(
            id="verification_config",
            partition_key=PartitionKey(path="/id"),
            offer_throughput=400
        )
        config = {
            "id": "verification_config",  # singleton document
            "guild_id": guild_id,
            "channel_id": channel_id,
            "updated": datetime.datetime.now().isoformat()
        }
        # Upsert the config document.
        await asyncio.to_thread(config_container.upsert_item, config)
        print(f"Saved verification config: guild_id={guild_id}, channel_id={channel_id}")
        return True
    except Exception as e:
        print(f"Error saving verification config: {e}")
        return False

async def get_verification_config() -> dict:
    """
    Retrieve the verification configuration from Cosmos DB.
    Returns a dict with guild_id and channel_id if available, else None.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        config_container = database.create_container_if_not_exists(
            id="verification_config",
            partition_key=PartitionKey(path="/id"),
            offer_throughput=400
        )
        result = await asyncio.to_thread(config_container.read_item, item="verification_config", partition_key="verification_config")
        return result
    except exceptions.CosmosHttpResponseError as e:
        print(f"Verification config not found in Cosmos DB: {e}")
        return None
    except Exception as e:
        print(f"Error retrieving verification config: {e}")
        return None

class VerificationModal(discord.ui.Modal, title="Verify Your Account"):
    # Ensure the label is within Discord's 45-character limit.
    in_game_name = discord.ui.TextInput(
        label="In-Game Name (ex: name#0000)",
        placeholder="ingamename#0000",
        required=True,
        min_length=3,
        max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        discord_id = interaction.user.id
        discord_name = str(interaction.user)
        in_game_name_value = self.in_game_name.value.strip()
        success = await link_user(discord_id, discord_name, in_game_name_value)
        if success:
            await interaction.response.send_message("Your account has been linked successfully!", ephemeral=True)
        else:
            await interaction.response.send_message("There was an error linking your account. Please try again later.", ephemeral=True)

class DeleteLinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Delete Link", 
            style=discord.ButtonStyle.danger, 
            custom_id="delete_link_button"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Delete the user's link
        success = await delete_user_link(interaction.user.id)
        if success:
            embed = discord.Embed(
                title="Link Deleted",
                description="Your account link has been deleted. You can now link a new account using the Verify button.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="Error",
                description="There was an error deleting your account link. Please try again later.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class DeleteLinkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
        self.add_item(DeleteLinkButton())

class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="cancel_button"
        )
    
    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Action cancelled.", view=None)

class DeleteCurrentLinkButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Delete Current Link", 
            style=discord.ButtonStyle.danger, 
            custom_id="delete_current_link_button"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Check if user has a linked account
        existing_link = await get_user_link(interaction.user.id)
        if not existing_link:
            await interaction.response.send_message(
                "You don't have a linked account to delete.",
                ephemeral=True
            )
            return
            
        # Show confirmation with the linked account details
        embed = discord.Embed(
            title="Confirm Account Unlink",
            description=f"You are about to unlink your THE FINALS account:\n**In-Game Name:** {existing_link['in_game_name']}\n\nAre you sure?",
            color=discord.Color.yellow()
        )
        
        # Create confirmation buttons
        view = discord.ui.View(timeout=300)  # 5 minute timeout
        
        confirm_button = discord.ui.Button(
            label="Confirm Delete", 
            style=discord.ButtonStyle.danger, 
            custom_id="confirm_delete_link"
        )
        
        cancel_button = discord.ui.Button(
            label="Cancel", 
            style=discord.ButtonStyle.secondary, 
            custom_id="cancel_delete_link"
        )
        
        async def confirm_callback(button_interaction):
            success = await delete_user_link(interaction.user.id)
            if success:
                embed = discord.Embed(
                    title="Link Deleted",
                    description="Your account link has been deleted. You can now link a new account using the Verify button.",
                    color=discord.Color.green()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
            else:
                embed = discord.Embed(
                    title="Error",
                    description="There was an error deleting your account link. Please try again later.",
                    color=discord.Color.red()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
        
        async def cancel_callback(button_interaction):
            await button_interaction.response.edit_message(content="Action cancelled.", embed=None, view=None)
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class VerifyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Verify", style=discord.ButtonStyle.primary, custom_id="verify_button")

    async def callback(self, interaction: discord.Interaction) -> None:
        # Check if user is already linked
        existing_link = await get_user_link(interaction.user.id)
        if existing_link:
            # Show a message with information about the current link and an option to delete it
            embed = discord.Embed(
                title="Account Already Linked",
                description=f"You already have a linked account:\n**In-Game Name:** {existing_link['in_game_name']}\n**Linked on:** {existing_link['timestamp']}",
                color=discord.Color.yellow()
            )
            view = DeleteLinkView()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            # Show verification modal
            modal = VerificationModal()
            await interaction.response.send_modal(modal)

class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view.
        self.add_item(VerifyButton())
        self.add_item(DeleteCurrentLinkButton())

async def send_verification_embed(channel: discord.TextChannel) -> None:
    """
    Deletes any existing verification embed sent by the bot in the channel,
    then sends a new verification embed with the persistent view.
    This effectively replaces the old embed with a new one.
    """
    # Delete old messages from the bot that contain the verification embed.
    async for message in channel.history(limit=100):
        if message.author == channel.guild.me and message.embeds:
            embed = message.embeds[0]
            if embed.title == "Verification Required":
                try:
                    await message.delete()
                except Exception as e:
                    print(f"Failed to delete old message: {e}")
    view = VerificationView()
    embed = discord.Embed(
        title="Verification Required",
        description=(
            "Please click on the button below to verify yourself. After this step, you will be able to see all channels "
            "of the Discord server.\nOnce you click **Verify**, please input your THE FINALS name. Example: `ingamename#0000`\n\n"
            "If you need to delete your linked account, use the 'Delete Current Link' button."
        ),
        color=discord.Color.blue()
    )
    await channel.send(embed=embed, view=view)

async def setup(bot_instance: commands.Bot) -> None:
    
        # Make bot available for the link_user function
    global bot
    bot = bot_instance
    
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Slash command to set up the verification embed; if executed in a channel,
    # save that channel as the verification channel in the database.
    @bot.tree.command(name="link_setup", description="Set up the verification channel (Admin only).")
    @app_commands.default_permissions(administrator=True)
    async def link_setup(interaction: discord.Interaction) -> None:
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
            return

        # Save the current guild and channel in the DB.
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        saved = await save_verification_config(guild_id, channel_id)
        if not saved:
            await interaction.response.send_message("Failed to save verification configuration.", ephemeral=True)
            return

        await send_verification_embed(interaction.channel)
        await interaction.response.send_message("Verification embed has been refreshed and configuration saved.", ephemeral=True)
    
    # Add a command to allow users to delete their link directly
    @bot.tree.command(name="delete_link", description="Delete your linked THE FINALS account")
    async def delete_link_command(interaction: discord.Interaction) -> None:
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Check if user has a linked account
        existing_link = await get_user_link(interaction.user.id)
        if not existing_link:
            await interaction.response.send_message(
                "You don't have a linked account to delete.",
                ephemeral=True
            )
            return
            
        # Show confirmation with the linked account details
        embed = discord.Embed(
            title="Confirm Account Unlink",
            description=f"You are about to unlink your THE FINALS account:\n**In-Game Name:** {existing_link['in_game_name']}\n\nAre you sure?",
            color=discord.Color.yellow()
        )
        
        # Create confirmation buttons
        view = discord.ui.View(timeout=300)  # 5 minute timeout
        
        confirm_button = discord.ui.Button(
            label="Confirm Delete", 
            style=discord.ButtonStyle.danger, 
            custom_id="confirm_delete_link"
        )
        
        cancel_button = discord.ui.Button(
            label="Cancel", 
            style=discord.ButtonStyle.secondary, 
            custom_id="cancel_delete_link"
        )
        
        async def confirm_callback(button_interaction):
            success = await delete_user_link(interaction.user.id)
            if success:
                embed = discord.Embed(
                    title="Link Deleted",
                    description="Your account link has been deleted. You can now link a new account using the Verify button.",
                    color=discord.Color.green()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
            else:
                embed = discord.Embed(
                    title="Error",
                    description="There was an error deleting your account link. Please try again later.",
                    color=discord.Color.red()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
        
        async def cancel_callback(button_interaction):
            await button_interaction.response.edit_message(content="Action cancelled.", embed=None, view=None)
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # Function to refresh the verification embed on bot startup
    async def on_ready_refresh_embed():
        # Check if feature is enabled before proceeding
        # On bot restart, retrieve the configuration from DB
        config = await get_verification_config()
        if not config:
            print("No verification configuration found in DB.")
            return
            
        guild_id = config.get("guild_id")
        channel_id = config.get("channel_id")
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"Configured guild with ID {guild_id} not found. Ensure the bot is in that guild.")
            return
            
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled(feature_name, guild_id):
            print(f"Link setup feature is disabled for guild {guild.name}. Skipping embed refresh.")
            return
            
        channel = guild.get_channel(channel_id)
        if not channel:
            print(f"Configured channel with ID {channel_id} not found in guild '{guild.name}'.")
            return
            
        await send_verification_embed(channel)
        print(f"Verification embed has been replaced in channel '{channel.name}' in guild '{guild.name}'.")

    # Schedule the embed replacement on bot startup
    if bot.is_ready():
        bot.loop.create_task(on_ready_refresh_embed())
    else:
        bot.add_listener(on_ready_refresh_embed, "on_ready")

    # Register the persistent view to handle button interactions after bot restarts
    bot.add_view(VerificationView())