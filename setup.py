import os
import glob
import importlib
import asyncio
import discord
from discord.ui import View, Select, Button
from azure.cosmos import CosmosClient, PartitionKey, exceptions

########################################
# Cosmos DB Integration
########################################

# Cosmos DB configuration.
# IMPORTANT: Set COSMOS_KEY to your valid Base64-encoded Cosmos master key.
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT") or "https://thefinalsdb.documents.azure.com:443/"
COSMOS_KEY = os.environ.get("COSMOS_KEY") or "2BrNaP1un47Nxid7emzalHA78ui0I3WCSQSozp3VkmaAQEWEBPcROGibgIeJRYx8ZS3EheujBnuwACDbFM2K9Q=="
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE") or "thefinalsdb"

# Initialize the Cosmos DB client.
try:
    # Fix: Pass the key as a string, not None
    if COSMOS_KEY is None:
        raise ValueError("No Cosmos DB key found. Please set the COSMOS_KEY environment variable.")
    client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
except Exception as e:
    print(f"Error creating CosmosClient: {e}")
    # Don't raise here - we'll handle this differently to prevent startup failures
    client = None
    database = None
    container = None
else:
    # Only create these if the client was successfully initialized
    # Ensure the database and container exist.
    database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
    container = database.create_container_if_not_exists(
        id="guild_settings",
        partition_key=PartitionKey(path="/guild_id"),
        offer_throughput=400
    )

def default_guild_settings(available_commands: dict) -> dict:
    """
    Returns a dictionary that contains all command names set to False.
    """
    settings = {}
    for cmd in available_commands:
        settings[cmd] = False
    return settings

async def get_guild_settings(guild_id: int, available_commands: dict) -> dict:
    """
    Retrieve the settings for a guild from Cosmos DB.
    If no settings exist, return the default settings (all commands disabled).
    """
    if client is None or container is None:
        print("Cosmos DB client not initialized. Using default settings.")
        return default_guild_settings(available_commands)
        
    try:
        settings = await asyncio.to_thread(
            container.read_item,
            item=str(guild_id),
            partition_key=str(guild_id)
        )
        # Remove Cosmos reserved keys if necessary (like 'id' and 'guild_id') before using.
        # This ensures we only work with command toggles.
        settings.pop("id", None)
        settings.pop("guild_id", None)
        return settings
    except exceptions.CosmosResourceNotFoundError:
        return default_guild_settings(available_commands)
    except Exception as e:
        print(f"Error retrieving guild settings: {e}")
        return default_guild_settings(available_commands)

async def save_guild_settings(guild_id: int, settings: dict):
    """
    Save (upsert) the guild settings to Cosmos DB.
    """
    if client is None or container is None:
        print("Cosmos DB client not initialized. Settings not saved to Cosmos DB.")
        return False
        
    # Attach the required Cosmos keys.
    data = settings.copy()
    data["id"] = str(guild_id)
    data["guild_id"] = str(guild_id)
    try:
        return await asyncio.to_thread(container.upsert_item, data)
    except Exception as e:
        print(f"Error saving guild settings: {e}")
        return False

########################################
# Dynamic Command Info Loader
########################################

def get_available_features() -> dict:
    """
    Returns a dictionary containing all available command modules with their display name and description.
    This function scans for command_*.py files and extracts their metadata.
    """
    features = {}
    command_files = glob.glob("command_*.py")
    
    for file in command_files:
        feature_id = file[8:-3]  # Remove 'command_' prefix and '.py' extension
        try:
            module = importlib.import_module(file[:-3])
            display_name = getattr(module, 'DISPLAY_NAME', feature_id.replace('_', ' ').title())
            description = getattr(module, 'DESCRIPTION', f"Enables the {display_name} feature")
            features[feature_id] = {
                "name": display_name,
                "description": description,
                "enabled_by_default": False
            }
        except Exception as e:
            print(f"Error getting details for {file}: {e}")
            features[feature_id] = {
                "name": feature_id.replace('_', ' ').title(),
                "description": f"Enables the {feature_id} feature",
                "enabled_by_default": False
            }
    return features

def truncate_description(text: str) -> str:
    """
    Ensure the description is 100 characters or fewer.
    """
    return text if len(text) <= 100 else text[:97] + "..."

########################################
# Setup View Code
########################################
async def create_setup_view(bot, guild_id: int) -> View:
    """
    Create a setup view for configuring the individual command toggles for a guild.
    The view loads settings from Cosmos DB. When "Save Setup" is clicked,
    the enabled/disabled status for each command is upserted to the database.
    """
    # Get available features from the bot
    AVAILABLE_COMMANDS = bot.get_available_features()
    
    # Load current settings from the bot
    current_settings = bot.get_guild_settings(guild_id)
    # Make a copy for temporary adjustments
    settings = current_settings.copy()

    async def enable_all_callback(interaction: discord.Interaction):
        # Enable all commands.
        for cmd in AVAILABLE_COMMANDS:
            settings[cmd] = True
        for child in view.children:
            if isinstance(child, CommandSelect):
                child.update_options(settings)
        await interaction.response.edit_message(view=view)

    async def start_setup_callback(interaction: discord.Interaction):
        # Save the new settings to the bot (which will save to file)
        bot.save_guild_settings(guild_id, settings)
        
        # Also try to save to Cosmos DB if available
        if client is not None:
            await save_guild_settings(guild_id, settings)
        
        embed = discord.Embed(
            title="Setup Complete!",
            description="The bot's command configuration has been saved.",
            color=0x2ecc71
        )
        
        # Fixed version to handle missing commands in AVAILABLE_COMMANDS
        enabled_list = []
        for cmd, enabled in settings.items():
            if enabled:
                # Check if the command exists in AVAILABLE_COMMANDS before accessing
                if cmd in AVAILABLE_COMMANDS:
                    enabled_list.append(f"✅ {AVAILABLE_COMMANDS[cmd]['name']}")
                else:
                    # Skip commands that don't exist in AVAILABLE_COMMANDS
                    print(f"Warning: Command '{cmd}' in settings but not in AVAILABLE_COMMANDS")
        
        if enabled_list:
            embed.add_field(name="Enabled Commands", value="\n".join(enabled_list), inline=False)
        else:
            embed.add_field(
                name="No Commands Enabled",
                value="No command was enabled. You can update settings later using the setup command.",
                inline=False
            )
        await interaction.response.edit_message(embed=embed, view=None)

    class CommandSelect(Select):
        def __init__(self, available_commands, current_settings):
            self.available_commands = available_commands
            self.current_settings = current_settings.copy()
            options = []
            for cmd, info in available_commands.items():
                is_enabled = current_settings.get(cmd, False)
                status = "✅ " if is_enabled else "❌ "
                options.append(discord.SelectOption(
                    label=f"{status}{info['name']}",
                    description=truncate_description(info.get("description", "")),
                    value=cmd
                ))
            super().__init__(
                placeholder="Select a command to enable/disable...",
                min_values=1,
                max_values=1,
                options=options[:25]  # Discord has a limit of 25 options
            )
        
        async def callback(self, interaction: discord.Interaction):
            selected = self.values[0]
            current = self.current_settings.get(selected, False)
            # Toggle the value
            new_value = not current
            # Update both dictionaries
            self.current_settings[selected] = new_value
            settings[selected] = new_value
            # Update the UI
            self.update_options(self.current_settings)
            await interaction.response.edit_message(view=self.view)
        
        def update_options(self, settings):
            new_options = []
            for cmd, info in self.available_commands.items():
                is_enabled = settings.get(cmd, False)
                status = "✅ " if is_enabled else "❌ "
                new_options.append(discord.SelectOption(
                    label=f"{status}{info['name']}",
                    description=truncate_description(info.get("description", "")),
                    value=cmd
                ))
            self.options = new_options[:25]  # Respect Discord's limit of 25 options

    class SetupView(View):
        def __init__(self):
            super().__init__(timeout=900)  # 15-minute timeout
            # Add the command selection dropdown.
            self.add_item(CommandSelect(AVAILABLE_COMMANDS, current_settings))
            # Button to enable all commands.
            enable_all_btn = Button(
                label="Enable All Commands",
                style=discord.ButtonStyle.secondary,
                custom_id="enable_all"
            )
            enable_all_btn.callback = enable_all_callback
            self.add_item(enable_all_btn)
            # Button to save the setup.
            save_setup_btn = Button(
                label="Save Setup",
                style=discord.ButtonStyle.success,
                custom_id="start_setup"
            )
            save_setup_btn.callback = start_setup_callback
            self.add_item(save_setup_btn)

    view = SetupView()
    return view


    class CommandSelect(Select):
        def __init__(self, available_commands, current_settings):
            self.available_commands = available_commands
            self.current_settings = current_settings.copy()
            options = []
            for cmd, info in available_commands.items():
                is_enabled = current_settings.get(cmd, False)
                status = "✅ " if is_enabled else "❌ "
                options.append(discord.SelectOption(
                    label=f"{status}{info['name']}",
                    description=truncate_description(info.get("description", "")),
                    value=cmd
                ))
            super().__init__(
                placeholder="Select a command to enable/disable...",
                min_values=1,
                max_values=1,
                options=options[:25]  # Discord has a limit of 25 options
            )
        
        async def callback(self, interaction: discord.Interaction):
            selected = self.values[0]
            current = self.current_settings.get(selected, False)
            # Toggle the value
            new_value = not current
            # Update both dictionaries
            self.current_settings[selected] = new_value
            settings[selected] = new_value
            # Update the UI
            self.update_options(self.current_settings)
            await interaction.response.edit_message(view=self.view)
        
        def update_options(self, settings):
            new_options = []
            for cmd, info in self.available_commands.items():
                is_enabled = settings.get(cmd, False)
                status = "✅ " if is_enabled else "❌ "
                new_options.append(discord.SelectOption(
                    label=f"{status}{info['name']}",
                    description=truncate_description(info.get("description", "")),
                    value=cmd
                ))
            self.options = new_options[:25]  # Respect Discord's limit of 25 options

    class SetupView(View):
        def __init__(self):
            super().__init__(timeout=None)
            # Add the command selection dropdown.
            self.add_item(CommandSelect(AVAILABLE_COMMANDS, current_settings))
            # Button to enable all commands.
            enable_all_btn = Button(
                label="Enable All Commands",
                style=discord.ButtonStyle.secondary,
                custom_id="enable_all"
            )
            enable_all_btn.callback = enable_all_callback
            self.add_item(enable_all_btn)
            # Button to save the setup.
            save_setup_btn = Button(
                label="Save Setup",
                style=discord.ButtonStyle.success,
                custom_id="start_setup"
            )
            save_setup_btn.callback = start_setup_callback
            self.add_item(save_setup_btn)

    view = SetupView()
    return view