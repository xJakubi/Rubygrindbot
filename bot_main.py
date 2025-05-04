import discord
import asyncio
import os
from discord.ext import commands
from discord import app_commands
import importlib
import glob
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('TOKEN')
if TOKEN is None:
    raise Exception("No bot token found. Please add your token to the .env file with the key TOKEN.")

# Set up intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Initialize bot with application command support
class TheFinalsBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.guild_settings = {}
        self.initial_sync_done = False

    async def setup_hook(self):
        # Load settings
        try:
            with open('guild_settings.json', 'r') as f:
                self.guild_settings = json.load(f)
        except FileNotFoundError:
            # Create file if it doesn't exist
            with open('guild_settings.json', 'w') as f:
                json.dump({}, f)
        
        # Load command modules
        await self.load_command_modules()

    async def load_command_modules(self):
        # Get all command_*.py files
        command_files = glob.glob("command_*.py")
        loaded_commands = []
        
        for file in command_files:
            try:
                # Convert filename to module name (remove .py extension)
                module_name = file[:-3]
                module = importlib.import_module(module_name)
                
                # If the module has a setup function, call it with the bot instance
                if hasattr(module, 'setup'):
                    await module.setup(self)
                    loaded_commands.append(module_name)
                    print(f"Loaded command module: {module_name}")
            except Exception as e:
                print(f"Failed to load command module {file}: {e}")
                
        return loaded_commands

    async def on_ready(self):
        if not self.initial_sync_done:
            # Sync app commands with Discord
            await self.tree.sync()
            self.initial_sync_done = True
            print("Slash commands synced!")
        
        print(f'{self.user} has connected to Discord!')
        print(f'Connected to {len(self.guilds)} guilds.')
        
        # Set custom status
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name="THE FINALS Leaderboard"
        ))

# Create bot instance
bot = TheFinalsBot()

@bot.event
async def on_guild_join(guild):
    """Called when the bot joins a new server"""
    print(f"Bot joined new guild: {guild.name}")
    
    # Find administrators role for tagging
    admin_role = discord.utils.get(guild.roles, name="Administrator") or discord.utils.get(guild.roles, name="Admin")
    
    # Find the system channel or default to the first text channel
    channel = guild.system_channel
    if not channel:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                channel = ch
                break
    
    if channel:
        # Send setup message with view
        from setup import create_setup_view
        
        # Mention the admin role if it exists
        admin_mention = f"{admin_role.mention} " if admin_role else ""
        
        embed = discord.Embed(
            title="THE FINALS Leaderboard Bot Setup",
            description=f"{admin_mention}Thanks for adding THE FINALS Leaderboard Bot to your server! Please select which features you'd like to enable.",
            color=0x3498db
        )
        embed.add_field(
            name="Instructions",
            value="Use the dropdown menu below to enable or disable features. When you're done, click 'Start Bot' to begin using the enabled features."
        )
        
        view = await create_setup_view(bot, guild.id)
        await channel.send(embed=embed, view=view)

# Check if a feature is enabled for a guild
def is_feature_enabled(feature_name, guild_id):
    """Check if a feature is enabled for a specific guild"""
    guild_settings = bot.guild_settings.get(str(guild_id), {})
    return guild_settings.get(feature_name, False)

# Make feature check available to other modules
bot.is_feature_enabled = is_feature_enabled

# Functions to manage guild settings
def get_guild_settings(guild_id):
    return bot.guild_settings.get(str(guild_id), {})

def save_guild_settings(guild_id, settings):
    bot.guild_settings[str(guild_id)] = settings
    # Save to file
    with open('guild_settings.json', 'w') as f:
        json.dump(bot.guild_settings, f)

bot.get_guild_settings = get_guild_settings
bot.save_guild_settings = save_guild_settings

# Get all available features
def get_available_features():
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

bot.get_available_features = get_available_features

# Enable a feature in a guild
def enable_feature(guild_id, feature_name):
    """Enable a specific feature for a guild"""
    guild_settings = get_guild_settings(guild_id)
    guild_settings[feature_name] = True
    save_guild_settings(guild_id, guild_settings)

# Disable a feature in a guild
def disable_feature(guild_id, feature_name):
    """Disable a specific feature for a guild"""
    guild_settings = get_guild_settings(guild_id)
    guild_settings[feature_name] = False
    save_guild_settings(guild_id, guild_settings)

# Modified version of this function
def is_feature_enabled(feature_name, guild_id):
    """Check if a feature is enabled for a specific guild"""
    guild_settings = bot.guild_settings.get(str(guild_id), {})
    # Check if the feature exists in settings, if not return the default value for that feature
    # Get the default value from the module if possible
    try:
        module_name = f"command_{feature_name}"
        module = importlib.import_module(module_name)
        default = getattr(module, 'ENABLED_BY_DEFAULT', False)
        return guild_settings.get(feature_name, default)
    except (ImportError, AttributeError):
        # If module doesn't exist or doesn't have ENABLED_BY_DEFAULT, default to False
        return guild_settings.get(feature_name, False)


# Setup command
@bot.tree.command(name="setup", description="Configure the bot settings for this server")
@app_commands.default_permissions(administrator=True)
async def setup_command(interaction: discord.Interaction):
    """Run the bot setup process again"""
    from setup import create_setup_view
    
    embed = discord.Embed(
        title="THE FINALS Leaderboard Bot Setup",
        description="Configure which features you'd like to enable in this server.",
        color=0x3498db
    )
    
    view = await create_setup_view(bot, interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)