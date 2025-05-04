import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import datetime

# Module metadata
DISPLAY_NAME = "Message Management"
DESCRIPTION = "Allows administrators to manage messages, lock channels, and apply slowmode"
ENABLED_BY_DEFAULT = False

# Error messages
PERMISSION_ERROR = "This command requires manage messages permission"

async def setup(bot):
    # Create command group
    prune_group = app_commands.Group(name="prune", description="Commands for deleting messages in bulk")
    
    @prune_group.command(name="messages", description="Delete a specified number of recent messages in the channel")
    @app_commands.describe(count="Number of messages to delete (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def prune_messages(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled("prune", interaction.guild_id):
            return await interaction.response.send_message("The prune feature is not enabled on this server.", ephemeral=True)
        
        # Defer the response since deletion might take time
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Add 1 to include potential bot feedback messages
            deleted = await interaction.channel.purge(limit=count)
            await interaction.followup.send(f"Successfully deleted {len(deleted)} messages.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to delete messages in this channel.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Error deleting messages: {str(e)}", ephemeral=True)
    
    @prune_group.command(name="user", description="Delete a specified number of messages from a specific user")
    @app_commands.describe(
        user="The user whose messages to delete",
        count="Number of messages to check (1-100)"
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def prune_user(
        interaction: discord.Interaction, 
        user: discord.Member, 
        count: app_commands.Range[int, 1, 100]
    ):
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled("prune", interaction.guild_id):
            return await interaction.response.send_message("The prune feature is not enabled on this server.", ephemeral=True)
        
        # Defer the response since deletion might take time
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Custom check function to only delete messages from the specified user
            def check_user(message):
                return message.author == user
            
            # Add 1 to include potential bot feedback messages
            deleted = await interaction.channel.purge(limit=count, check=check_user)
            await interaction.followup.send(f"Successfully deleted {len(deleted)} messages from {user.display_name}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to delete messages in this channel.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Error deleting messages: {str(e)}", ephemeral=True)

    # Add lock/unlock channel commands
    @bot.tree.command(name="lockchannel", description="Lock the current channel so only staff can send messages")
    @app_commands.describe(duration="Duration to lock the channel (format: HH:MM, leave empty for indefinite)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock_channel(interaction: discord.Interaction, duration: str = None):
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled("prune", interaction.guild_id):
            return await interaction.response.send_message("The channel management feature is not enabled on this server.", ephemeral=True)
        
        try:
            channel = interaction.channel
            # Get the default role (@everyone) for the guild
            default_role = interaction.guild.default_role
            
            # Save current permissions to restore them later
            current_perms = channel.overwrites_for(default_role)
            
            # Create new permission overwrite that denies sending messages
            new_perms = discord.PermissionOverwrite(**{k: v for k, v in current_perms})
            new_perms.send_messages = False
            
            # Apply the overwrite
            await channel.set_permissions(default_role, overwrite=new_perms)
            
            # Format confirmation message
            if duration:
                try:
                    # Parse the duration
                    hours, minutes = map(int, duration.split(':'))
                    total_seconds = hours * 3600 + minutes * 60
                    unlock_time = datetime.datetime.now() + datetime.timedelta(seconds=total_seconds)
                    
                    embed = discord.Embed(
                        title="🔒 Channel Locked",
                        description=f"This channel has been locked until {unlock_time.strftime('%H:%M')}.",
                        color=discord.Color.red()
                    )
                    await interaction.response.send_message(embed=embed)
                    
                    # Schedule unlock
                    await asyncio.sleep(total_seconds)
                    
                    # Restore original permissions
                    await channel.set_permissions(default_role, overwrite=current_perms)
                    
                    unlock_embed = discord.Embed(
                        title="🔓 Channel Unlocked",
                        description="The channel lock has expired and the channel is now unlocked.",
                        color=discord.Color.green()
                    )
                    await channel.send(embed=unlock_embed)
                    
                except ValueError:
                    await interaction.response.send_message(
                        "Invalid duration format. Please use HH:MM format (e.g., 01:30 for 1 hour 30 minutes).", 
                        ephemeral=True
                    )
            else:
                embed = discord.Embed(
                    title="🔒 Channel Locked",
                    description="This channel has been locked indefinitely. Only staff can send messages.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
        
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to manage this channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error locking channel: {str(e)}", ephemeral=True)

    @bot.tree.command(name="unlockchannel", description="Unlock the current channel to allow everyone to send messages")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock_channel(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled("prune", interaction.guild_id):
            return await interaction.response.send_message("The channel management feature is not enabled on this server.", ephemeral=True)
        
        try:
            channel = interaction.channel
            # Get the default role (@everyone) for the guild
            default_role = interaction.guild.default_role
            
            # Get current permissions
            current_perms = channel.overwrites_for(default_role)
            
            # Remove send_messages restriction (set to None to use guild defaults)
            if current_perms.send_messages is False:
                current_perms.send_messages = None
                await channel.set_permissions(default_role, overwrite=current_perms)
                
                embed = discord.Embed(
                    title="🔓 Channel Unlocked",
                    description="This channel has been unlocked. Everyone can now send messages.",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("This channel is not locked!", ephemeral=True)
                
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to manage this channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error unlocking channel: {str(e)}", ephemeral=True)

    # Add user slowmode command
    @bot.tree.command(name="slowmode", description="Apply slowmode to a specific user")
    @app_commands.describe(
        user="The user to apply slowmode to",
        seconds="Slowmode interval in seconds (0 to remove)"
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def user_slowmode(interaction: discord.Interaction, user: discord.Member, seconds: int):
        # Check if feature is enabled for this guild
        if not bot.is_feature_enabled("prune", interaction.guild_id):
            return await interaction.response.send_message("The slowmode feature is not enabled on this server.", ephemeral=True)
            
        try:
            # Get or create user-specific permission overwrites
            overwrites = interaction.channel.overwrites_for(user)
            
            if seconds > 0:
                # Set user-specific slowmode
                overwrites.update(use_application_commands=True)  # Ensure they can still use app commands
                await interaction.channel.set_permissions(user, overwrite=overwrites)
                
                # Store user's timeout information in the bot
                # We'll need to implement message event handling for this user
                if not hasattr(bot, 'user_slowmodes'):
                    bot.user_slowmodes = {}
                    
                bot.user_slowmodes[(interaction.guild_id, interaction.channel_id, user.id)] = {
                    'interval': seconds,
                    'last_message': 0  # Will be set when they send messages
                }
                
                await interaction.response.send_message(
                    f"Slowmode of {seconds} seconds has been applied to {user.display_name} in this channel.",
                    ephemeral=True
                )
            else:
                # Remove slowmode
                if hasattr(bot, 'user_slowmodes'):
                    key = (interaction.guild_id, interaction.channel_id, user.id)
                    if key in bot.user_slowmodes:
                        del bot.user_slowmodes[key]
                
                await interaction.response.send_message(
                    f"Slowmode has been removed from {user.display_name} in this channel.",
                    ephemeral=True
                )
                
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to manage permissions in this channel.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error setting slowmode: {str(e)}", ephemeral=True)

    # Add message handler for user slowmode
    @bot.event
    async def on_message(message):
        # Ignore messages from bots
        if message.author.bot:
            return
            
        # Check if we have any slowmodes set
        if hasattr(bot, 'user_slowmodes'):
            key = (message.guild.id, message.channel.id, message.author.id)
            if key in bot.user_slowmodes:
                slowmode = bot.user_slowmodes[key]
                now = datetime.datetime.now().timestamp()
                
                # Check if the user is under slowmode restriction
                if now - slowmode['last_message'] < slowmode['interval']:
                    # Delete the message
                    await message.delete()
                    
                    # Send a warning message that will auto-delete
                    remaining = int(slowmode['interval'] - (now - slowmode['last_message']))
                    warning = await message.channel.send(
                        f"{message.author.mention}, you're in slowmode. Wait {remaining} more seconds before sending another message.",
                        delete_after=5
                    )
                    return
                else:
                    # Update their last message time
                    slowmode['last_message'] = now
        
        # Process commands as normal
        await bot.process_commands(message)

    # Handle command errors, particularly permission errors
    @prune_messages.error
    @prune_user.error
    async def prune_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(PERMISSION_ERROR, ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {str(error)}", ephemeral=True)

    # Add the command group to the bot
    bot.tree.add_command(prune_group)
    print("Message management module loaded!")