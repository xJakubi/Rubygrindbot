import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import datetime
import re

# Module metadata
DISPLAY_NAME = "User Restriction"
DESCRIPTION = "Adds commands to restrict users from using certain Discord features"
ENABLED_BY_DEFAULT = False

class RestrictionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_restrictions = {}  # Store active restriction timers
        
    @app_commands.command(
        name="restrict", 
        description="Restrict a user from using links, reactions, emojis, etc for a specified time")
    @app_commands.describe(
        user="The user to restrict",
        time="Time duration in HH:MM format (e.g., 01:30 for 1 hour 30 minutes)"
    )
    @app_commands.default_permissions(manage_roles=True)
    async def restrict_user(self, interaction: discord.Interaction, user: discord.Member, time: str):
        # Check if the command user has proper permissions
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
            
        # Check if the bot has sufficient permissions
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("I don't have the required permissions to manage roles.", ephemeral=True)
            return
            
        # Verify the time format using regex (HH:MM)
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$', time):
            await interaction.response.send_message("Invalid time format. Please use HH:MM format (e.g., 01:30).", ephemeral=True)
            return
        
        # Parse the time
        hours, minutes = map(int, time.split(':'))
        duration_seconds = hours * 3600 + minutes * 60
        
        if duration_seconds <= 0:
            await interaction.response.send_message("Time must be greater than 00:00.", ephemeral=True)
            return
            
        # Get or create the "Restricted" role
        restricted_role = discord.utils.get(interaction.guild.roles, name="Restricted")
        
        if not restricted_role:
            # Create the role if it doesn't exist
            try:
                restricted_role = await interaction.guild.create_role(
                    name="Restricted",
                    color=discord.Color.dark_gray(),
                    reason="Created for user restrictions"
                )
                
                # Update permissions for all channels
                for channel in interaction.guild.channels:
                    overwrites = channel.overwrites_for(restricted_role)
                    # Disable permissions that should be restricted
                    overwrites.send_messages = True  # Can send messages
                    overwrites.embed_links = False   # Can't send links
                    overwrites.attach_files = False  # Can't attach files
                    overwrites.use_external_emojis = False  # Can't use external emojis
                    overwrites.add_reactions = False  # Can't add reactions
                    overwrites.use_application_commands = False  # Can't use slash commands
                    overwrites.use_external_stickers = False  # Can't use stickers
                    
                    await channel.set_permissions(restricted_role, overwrite=overwrites)
                
                await interaction.response.send_message("Creating 'Restricted' role and configuring permissions...", ephemeral=True)
                # Edit the response after permissions are set
                await interaction.edit_original_response(content="'Restricted' role created and configured.")
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to create roles.", ephemeral=True)
                return
            except Exception as e:
                await interaction.response.send_message(f"Error creating role: {str(e)}", ephemeral=True)
                return
        
        # Add the role to the user
        try:
            await interaction.response.defer()
            await user.add_roles(restricted_role)
            
            # Calculate when the restriction will be lifted
            end_time = datetime.datetime.now() + datetime.timedelta(seconds=duration_seconds)
            time_format = f"{hours:02d}:{minutes:02d}"
            
            # Store the active restriction
            self.active_restrictions[user.id] = {
                'end_time': end_time,
                'task': asyncio.create_task(self._remove_restriction_after(user.id, restricted_role, duration_seconds))
            }
            
            # Send confirmation
            embed = discord.Embed(
                title="User Restricted",
                description=f"{user.mention} has been restricted for {time_format}.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Restriction ends at", value=f"<t:{int(end_time.timestamp())}:F>")
            embed.set_footer(text=f"Restricted by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            
            # DM the user
            try:
                dm_embed = discord.Embed(
                    title="You have been restricted",
                    description=f"You have been restricted in {interaction.guild.name} for {time_format}.",
                    color=discord.Color.orange()
                )
                dm_embed.add_field(name="Restriction ends at", value=f"<t:{int(end_time.timestamp())}:F>")
                dm_embed.add_field(
                    name="Limitations", 
                    value="While restricted, you cannot post links, add reactions, use emojis, stickers, or the soundboard.",
                    inline=False
                )
                
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                # User may have DMs disabled, we can ignore this
                pass
                
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to add roles to this user.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error restricting user: {str(e)}", ephemeral=True)
    
    @app_commands.command(
        name="unrestrict", 
        description="Remove restrictions from a user")
    @app_commands.describe(
        user="The user to unrestrict"
    )
    @app_commands.default_permissions(manage_roles=True)
    async def unrestrict_user(self, interaction: discord.Interaction, user: discord.Member):
        # Check if the command user has proper permissions
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return
        
        # Get the "Restricted" role
        restricted_role = discord.utils.get(interaction.guild.roles, name="Restricted")
        
        if not restricted_role:
            await interaction.response.send_message("The 'Restricted' role doesn't exist.", ephemeral=True)
            return
            
        # Check if the user has the role
        if restricted_role not in user.roles:
            await interaction.response.send_message(f"{user.mention} is not currently restricted.", ephemeral=True)
            return
            
        # Remove the role
        try:
            await user.remove_roles(restricted_role)
            
            # Cancel any active timer for this user
            if user.id in self.active_restrictions:
                self.active_restrictions[user.id]['task'].cancel()
                del self.active_restrictions[user.id]
            
            embed = discord.Embed(
                title="User Unrestricted",
                description=f"{user.mention} has been unrestricted by {interaction.user.mention}.",
                color=discord.Color.green()
            )
            
            await interaction.response.send_message(embed=embed)
            
            # DM the user
            try:
                dm_embed = discord.Embed(
                    title="Restriction Removed",
                    description=f"Your restriction in {interaction.guild.name} has been removed.",
                    color=discord.Color.green()
                )
                
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                # User may have DMs disabled, we can ignore this
                pass
                
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to remove roles from this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error unrestricting user: {str(e)}", ephemeral=True)
    
    async def _remove_restriction_after(self, user_id, role, duration):
        """Task to remove restriction after the specified duration"""
        try:
            await asyncio.sleep(duration)
            
            # Find the user and remove the role
            for guild in self.bot.guilds:
                member = guild.get_member(user_id)
                if member and role in member.roles:
                    await member.remove_roles(role)
                    
                    # Notify the user
                    try:
                        embed = discord.Embed(
                            title="Restriction Expired",
                            description=f"Your restriction in {guild.name} has expired.",
                            color=discord.Color.green()
                        )
                        await member.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    
                    # Find a log channel
                    log_channel = discord.utils.get(guild.text_channels, name="mod-log")
                    if log_channel:
                        log_embed = discord.Embed(
                            title="User Restriction Expired",
                            description=f"{member.mention}'s restriction has expired and been automatically removed.",
                            color=discord.Color.green()
                        )
                        await log_channel.send(embed=log_embed)
                    
                    break
        except asyncio.CancelledError:
            # The task was cancelled (user was manually unrestricted)
            pass
        except Exception as e:
            print(f"Error in _remove_restriction_after: {e}")
        finally:
            # Remove from active restrictions if it still exists
            if user_id in self.active_restrictions:
                del self.active_restrictions[user_id]

async def setup(bot):
    await bot.add_cog(RestrictionCog(bot))