import sys
import traceback
import asyncio
import discord
from discord.ext import commands
import os
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

print("Starting bot in debug mode...")

# Get the bot token from environment variables
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("ERROR: No Discord token found. Please set DISCORD_TOKEN environment variable.")
    sys.exit(1)

print("Token loaded successfully")

try:
    # Create bot instance with intents
    intents = discord.Intents.default()
    intents.message_content = True  # Enable message content intent
    intents.members = True  # Enable server members intent
    
    bot = commands.Bot(command_prefix='!', intents=intents)
    print("Bot instance created")
    
    # Available features store
    available_features = {}
    
    @bot.event
    async def on_ready():
        print(f"\n=== BOT IS READY ===")
        print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
        print(f"Connected to {len(bot.guilds)} guilds")
        print("========================\n")
        
        # Load commands
        print("Loading command modules...")
        await load_commands()
    
    @bot.event
    async def on_command_error(ctx, error):
        print(f"Command error: {error}")
        if isinstance(error, commands.CommandNotFound):
            return
        await ctx.send(f"Error: {str(error)}")
    
    async def load_commands():
        print("Looking for command modules...")
        # Scan directory for command modules
        command_files = [f for f in os.listdir('.') if f.startswith('command_') and f.endswith('.py')]
        print(f"Found {len(command_files)} command modules: {', '.join(command_files)}")
        
        for file in command_files:
            try:
                extension = file[:-3]  # Remove .py extension
                print(f"Loading extension: {extension}...")
                await bot.load_extension(extension)
                print(f"Successfully loaded: {extension}")
                
                # Extract the command name from the file name
                cmd_name = file[8:-3]  # command_NAME.py -> NAME
                
                # Add to available features
                available_features[cmd_name] = {
                    "name": cmd_name.replace('_', ' ').title(),
                    "description": f"Enables the {cmd_name} feature",
                    "enabled_by_default": False
                }
                
            except Exception as e:
                print(f"Failed to load extension {file}: {str(e)}")
                traceback.print_exc()
    
    # Add a method to get available features
    def get_available_features():
        return available_features
    
    # Add the method to the bot
    bot.get_available_features = get_available_features
    
    # Placeholder methods for guild settings
    def get_guild_settings(guild_id):
        # In a real implementation, this would load from a database
        return {name: False for name in available_features}
    
    def save_guild_settings(guild_id, settings):
        # In a real implementation, this would save to a database
        print(f"Saving settings for guild {guild_id}: {settings}")
        return True
    
    # Add these methods to the bot
    bot.get_guild_settings = get_guild_settings
    bot.save_guild_settings = save_guild_settings
    
    # Add a method to check if a feature is enabled
    def is_feature_enabled(feature_name, guild_id):
        settings = get_guild_settings(guild_id)
        return settings.get(feature_name, False)
    
    # Add the method to the bot
    bot.is_feature_enabled = is_feature_enabled
    
    print("Bot configured, connecting to Discord...")
    
    try:
        print("Starting bot run...")
        bot.run(TOKEN, log_handler=None)
        print("Bot has shut down")
    except discord.LoginFailure:
        print("ERROR: Invalid Discord token!")
    except discord.HTTPException as e:
        print(f"ERROR: HTTP Exception: {e}")
    except Exception as e:
        print(f"ERROR: Unexpected error during bot.run(): {e}")
        traceback.print_exc()

except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    traceback.print_exc()
    print("\nKeeping console open for 30 seconds...")
    time.sleep(30)