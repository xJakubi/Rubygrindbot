import discord
from discord.ext import commands
from discord import app_commands
import functools

# This is a template for how command modules should be structured


DISPLAY_NAME = "Template Feature"  # Display name shown in setup UI
DESCRIPTION = "This is a template for new features"  # Description shown in setup UI
ENABLED_BY_DEFAULT = False  # DO NOT CHANGE

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Helper function to create a background task that respects feature toggling
def create_background_task(bot, feature_name, coro, *args, **kwargs):
    """
    Create a background task that only executes when the feature is enabled
    in the specific guild being processed
    """
    async def wrapped_coro(*args, **kwargs):
        # Filter args to check for guild
        guild = None
        for arg in args:
            if isinstance(arg, discord.Guild):
                guild = arg
                break
            elif isinstance(arg, discord.abc.GuildChannel):
                guild = arg.guild
                break
            elif isinstance(arg, discord.Member):
                guild = arg.guild
                break
        
        # If we found a guild, check if feature is enabled
        if guild and not bot.is_feature_enabled(feature_name, guild.id):
            return  # Skip execution if feature is disabled for this guild
            
        # Otherwise execute the original coroutine
        return await coro(*args, **kwargs)
    
    # Create and return the task
    return bot.loop.create_task(wrapped_coro(*args, **kwargs))

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__

    # Define your command(s) -> This is where you add your logic/commands, if unsure, leave everything as is and replace only this part with your logic.
    @bot.tree.command(name="template", description="Template command")
    async def template_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Command implementation
        await interaction.response.send_message("This is a template command!")
# template ends here

    # Example of an event listener that respects feature toggle -> add more if needed, EG: on action XYZ
    @bot.listen('on_message')
    async def template_message_handler(message):
        # Skip processing if feature is disabled
        if message.guild and not bot.is_feature_enabled(feature_name, message.guild.id):
            return
            
        # Event handler implementation
        if 'template' in message.content.lower():
            # This logic will only run if the feature is enabled for this guild
            print(f"Template feature triggered in {message.guild.name} by {message.author}")