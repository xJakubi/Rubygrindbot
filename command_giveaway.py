import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import datetime
import random
import asyncio
import os
from typing import Optional, List

# Module information for the setup system
DISPLAY_NAME = "Giveaways"
DESCRIPTION = "Create and manage timed giveaways with automatic winner selection"
ENABLED_BY_DEFAULT = False

# File to store active giveaways
GIVEAWAYS_FILE = "active_giveaways.json"

class GiveawaySystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_giveaways = {}
        self.load_giveaways()
        self.giveaway_check.start()
    
    def cog_unload(self):
        self.giveaway_check.cancel()
        self.save_giveaways()
    
    def load_giveaways(self):
        """Load active giveaways from file"""
        try:
            if os.path.exists(GIVEAWAYS_FILE):
                with open(GIVEAWAYS_FILE, 'r') as f:
                    giveaways_data = json.load(f)
                
                # Convert string timestamps back to datetime objects
                for message_id, giveaway in giveaways_data.items():
                    giveaway['end_time'] = datetime.datetime.fromisoformat(giveaway['end_time'])
                
                self.active_giveaways = giveaways_data
                print(f"Loaded {len(self.active_giveaways)} active giveaways")
        except Exception as e:
            print(f"Error loading giveaways: {e}")
            self.active_giveaways = {}
    
    def save_giveaways(self):
        """Save active giveaways to file"""
        try:
            # Convert datetime objects to ISO format strings for JSON serialization
            giveaways_data = {}
            for message_id, giveaway in self.active_giveaways.items():
                giveaway_copy = giveaway.copy()
                giveaway_copy['end_time'] = giveaway_copy['end_time'].isoformat()
                giveaways_data[message_id] = giveaway_copy
            
            with open(GIVEAWAYS_FILE, 'w') as f:
                json.dump(giveaways_data, f, indent=4)
        except Exception as e:
            print(f"Error saving giveaways: {e}")
    
    @tasks.loop(seconds=30)
    async def giveaway_check(self):
        """Check for ended giveaways every 30 seconds"""
        now = datetime.datetime.now()
        ended_giveaways = []
        
        for message_id, giveaway in self.active_giveaways.items():
            # Update remaining time display for active giveaways
            if giveaway['end_time'] > now:
                try:
                    await self.update_giveaway_embed(message_id, giveaway)
                except Exception as e:
                    print(f"Error updating giveaway {message_id}: {e}")
            # End giveaways that have reached their end time
            elif giveaway['end_time'] <= now:
                try:
                    await self.end_giveaway(message_id, giveaway)
                    ended_giveaways.append(message_id)
                except Exception as e:
                    print(f"Error ending giveaway {message_id}: {e}")
        
        # Remove ended giveaways from the active list
        for message_id in ended_giveaways:
            del self.active_giveaways[message_id]
        
        # Save changes if any giveaways ended
        if ended_giveaways:
            self.save_giveaways()
    
    @giveaway_check.before_loop
    async def before_giveaway_check(self):
        await self.bot.wait_until_ready()
    
    async def update_giveaway_embed(self, message_id, giveaway):
        """Update the giveaway embed with current time remaining"""
        try:
            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                return
            
            message = await channel.fetch_message(int(message_id))
            if not message:
                return
            
            remaining = giveaway['end_time'] - datetime.datetime.now()
            days, remainder = divmod(remaining.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            time_str = ""
            if days > 0:
                time_str += f"{int(days)}d "
            if hours > 0 or days > 0:
                time_str += f"{int(hours)}h "
            if minutes > 0 or hours > 0 or days > 0:
                time_str += f"{int(minutes)}m "
            time_str += f"{int(seconds)}s"
            
            embed = discord.Embed(
                title="🎉 GIVEAWAY 🎉",
                description=f"**{giveaway['prize']}**\n\nReact with 🎉 to enter!\nTime remaining: **{time_str}**",
                color=0x00FF00
            )
            embed.set_footer(text=f"Ends at • {giveaway['end_time'].strftime('%Y-%m-%d %H:%M:%S')} • Hosted by {giveaway['host_name']}")
            
            await message.edit(embed=embed)
        except discord.NotFound:
            # Message was deleted, remove from active giveaways
            if message_id in self.active_giveaways:
                del self.active_giveaways[message_id]
                self.save_giveaways()
        except Exception as e:
            print(f"Error updating giveaway embed {message_id}: {e}")
    
    async def end_giveaway(self, message_id, giveaway):
        """End a giveaway and select a winner"""
        try:
            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                return
            
            message = await channel.fetch_message(int(message_id))
            if not message:
                return
            
            # Get all users who reacted with 🎉
            reaction = discord.utils.get(message.reactions, emoji='🎉')
            if not reaction:
                winners = []
            else:
                # Updated to correctly collect users from the AsyncIterator
                users = [user async for user in reaction.users() if not user.bot]
                
                # Select winner(s)
                winner_count = giveaway.get('winner_count', 1)
                winners = random.sample(users, min(winner_count, len(users))) if users else []
            
            # Update the giveaway embed
            if winners:
                winner_mentions = " ".join([winner.mention for winner in winners])
                embed = discord.Embed(
                    title="🎉 GIVEAWAY ENDED 🎉",
                    description=f"**{giveaway['prize']}**\n\nWinner(s): {winner_mentions}",
                    color=0xFF0000
                )
                embed.set_footer(text=f"Ended at • {giveaway['end_time'].strftime('%Y-%m-%d %H:%M:%S')} • Hosted by {giveaway['host_name']}")
                
                await message.edit(embed=embed)
                
                # Send a congratulation message
                await channel.send(
                    f"🎉 Congratulations {winner_mentions}! You won **{giveaway['prize']}**!"
                )
            else:
                embed = discord.Embed(
                    title="🎉 GIVEAWAY ENDED 🎉",
                    description=f"**{giveaway['prize']}**\n\nNo valid entries found for the giveaway.",
                    color=0xFF0000
                )
                embed.set_footer(text=f"Ended at • {giveaway['end_time'].strftime('%Y-%m-%d %H:%M:%S')} • Hosted by {giveaway['host_name']}")
                
                await message.edit(embed=embed)
                await channel.send(f"No winner was determined for the giveaway: **{giveaway['prize']}**")
            
        except discord.NotFound:
            # Message was deleted, just remove from active giveaways
            pass
        except Exception as e:
            print(f"Error ending giveaway {message_id}: {e}")
    
    @app_commands.command(name="giveaway", description="Start a giveaway with specified prize and duration")
    @app_commands.describe(
        prize="What are you giving away?",
        time="Duration in HH:MM format (e.g., 01:30 for 1 hour and 30 minutes)",
        winners="Number of winners (default: 1)"
    )
    @app_commands.default_permissions(manage_channels=True)
    async def giveaway_command(self, interaction: discord.Interaction, prize: str, time: str, winners: Optional[int] = 1):
        """Create a new giveaway"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("giveaway", interaction.guild_id):
            await interaction.response.send_message("The giveaway feature is not enabled in this server. Please ask an administrator to enable it using the `/setup` command.", ephemeral=True)
            return

        # Check if the time format is valid (HH:MM)
        try:
            hours, minutes = map(int, time.split(':'))
            if hours < 0 or minutes < 0 or minutes >= 60:
                await interaction.response.send_message("Invalid time format. Please use HH:MM format (e.g., 01:30 for 1 hour and 30 minutes).", ephemeral=True)
                return
            
            duration = datetime.timedelta(hours=hours, minutes=minutes)
            if duration.total_seconds() < 60:  # Minimum 1 minute
                await interaction.response.send_message("Giveaway duration must be at least 1 minute.", ephemeral=True)
                return
            
            end_time = datetime.datetime.now() + duration
        except ValueError:
            await interaction.response.send_message("Invalid time format. Please use HH:MM format (e.g., 01:30 for 1 hour and 30 minutes).", ephemeral=True)
            return
        
        # Check if winners count is valid
        if winners < 1:
            await interaction.response.send_message("Number of winners must be at least 1.", ephemeral=True)
            return
        
        # Create the giveaway embed
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        time_str = ""
        if hours > 0:
            time_str += f"{int(hours)}h "
        if minutes > 0 or hours > 0:
            time_str += f"{int(minutes)}m "
        time_str += f"{int(seconds)}s"
        
        embed = discord.Embed(
            title="🎉 GIVEAWAY 🎉",
            description=f"**{prize}**\n\nReact with 🎉 to enter!\nTime remaining: **{time_str}**",
            color=0x00FF00
        )
        embed.set_footer(text=f"Ends at • {end_time.strftime('%Y-%m-%d %H:%M:%S')} • Hosted by {interaction.user.name}")
        
        # Defer the response before potentially longer operations
        await interaction.response.defer()
        
        # Send the giveaway message
        channel = interaction.channel
        message = await channel.send(embed=embed)
        
        # Add the initial reaction
        await message.add_reaction('🎉')
        
        # Store the giveaway data
        self.active_giveaways[str(message.id)] = {
            'channel_id': channel.id,
            'guild_id': interaction.guild_id,
            'host_id': interaction.user.id,
            'host_name': interaction.user.name,
            'prize': prize,
            'end_time': end_time,
            'winner_count': winners
        }
        
        # Save the updated giveaways
        self.save_giveaways()
        
        # Send confirmation to the user
        await interaction.followup.send(f"Giveaway for **{prize}** created! It will end in {time_str}.", ephemeral=True)
    
    @app_commands.command(name="reroll", description="Reroll a winner for a completed giveaway")
    @app_commands.describe(message_id="ID of the giveaway message to reroll")
    @app_commands.default_permissions(manage_channels=True)
    async def reroll_command(self, interaction: discord.Interaction, message_id: str):
        """Reroll a winner for a completed giveaway"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("giveaway", interaction.guild_id):
            await interaction.response.send_message("The giveaway feature is not enabled in this server.", ephemeral=True)
            return
        
        try:
            # Get the original giveaway message
            channel = interaction.channel
            message = await channel.fetch_message(int(message_id))
            
            if not message:
                await interaction.response.send_message("Couldn't find a giveaway with that message ID in this channel.", ephemeral=True)
                return
            
            # Check if this is a giveaway message from the bot
            if message.author.id != self.bot.user.id or not message.embeds:
                await interaction.response.send_message("That message is not a giveaway message.", ephemeral=True)
                return
            
            # Get the prize from the embed
            embed = message.embeds[0]
            if not embed.description:
                await interaction.response.send_message("This doesn't appear to be a valid giveaway message.", ephemeral=True)
                return
            
            description_lines = embed.description.split('\n')
            prize = description_lines[0].strip('*')
            
            # Get all users who reacted with 🎉
            reaction = discord.utils.get(message.reactions, emoji='🎉')
            if not reaction:
                await interaction.response.send_message("No reactions found on this giveaway.", ephemeral=True)
                return
            
            # Updated to correctly collect users from the AsyncIterator
            users = [user async for user in reaction.users() if not user.bot]
            
            if not users:
                await interaction.response.send_message("No valid entries found for this giveaway.", ephemeral=True)
                return
            
            # Select a new winner
            winner = random.choice(users)
            
            await interaction.response.send_message(
                f"🎉 The new winner is {winner.mention}! Congratulations, you won **{prize}**!"
            )
            
        except ValueError:
            await interaction.response.send_message("Please provide a valid message ID.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("Couldn't find a message with that ID in this channel.", ephemeral=True)
        except Exception as e:
            print(f"Error rerolling giveaway: {e}")
            await interaction.response.send_message("An error occurred while trying to reroll the giveaway.", ephemeral=True)
    
    @app_commands.command(name="giveaway_list", description="List all active giveaways in this server")
    @app_commands.default_permissions(manage_channels=True)
    async def list_command(self, interaction: discord.Interaction):
        """List all active giveaways in the server"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("giveaway", interaction.guild_id):
            await interaction.response.send_message("The giveaway feature is not enabled in this server.", ephemeral=True)
            return
        
        # Find all giveaways for this guild
        guild_giveaways = {
            msg_id: giveaway for msg_id, giveaway in self.active_giveaways.items()
            if giveaway['guild_id'] == interaction.guild_id
        }
        
        if not guild_giveaways:
            await interaction.response.send_message("There are no active giveaways in this server.", ephemeral=True)
            return
        
        # Create an embed with the list of giveaways
        embed = discord.Embed(
            title="Active Giveaways",
            description=f"There are {len(guild_giveaways)} active giveaways in this server.",
            color=0x00FF00
        )
        
        for msg_id, giveaway in guild_giveaways.items():
            remaining = giveaway['end_time'] - datetime.datetime.now()
            days, remainder = divmod(remaining.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            time_str = ""
            if days > 0:
                time_str += f"{int(days)}d "
            if hours > 0 or days > 0:
                time_str += f"{int(hours)}h "
            if minutes > 0 or hours > 0 or days > 0:
                time_str += f"{int(minutes)}m "
            time_str += f"{int(seconds)}s"
            
            channel = self.bot.get_channel(giveaway['channel_id'])
            channel_name = channel.name if channel else "Unknown channel"
            
            embed.add_field(
                name=f"Prize: {giveaway['prize']}",
                value=f"Channel: #{channel_name}\nTime remaining: {time_str}\nMessage ID: {msg_id}\nWinners: {giveaway.get('winner_count', 1)}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="giveaway_cancel", description="Cancel an active giveaway")
    @app_commands.describe(message_id="ID of the giveaway message to cancel")
    @app_commands.default_permissions(manage_channels=True)
    async def cancel_command(self, interaction: discord.Interaction, message_id: str):
        """Cancel an active giveaway"""
        # Check if the feature is enabled for this guild
        if not self.bot.is_feature_enabled("giveaway", interaction.guild_id):
            await interaction.response.send_message("The giveaway feature is not enabled in this server.", ephemeral=True)
            return
        
        if message_id not in self.active_giveaways:
            await interaction.response.send_message("No active giveaway found with that message ID.", ephemeral=True)
            return
        
        giveaway = self.active_giveaways[message_id]
        
        # Verify the giveaway is in this guild
        if giveaway['guild_id'] != interaction.guild_id:
            await interaction.response.send_message("That giveaway is not in this server.", ephemeral=True)
            return
        
        try:
            # Get the giveaway message
            channel = self.bot.get_channel(giveaway['channel_id'])
            if channel:
                try:
                    message = await channel.fetch_message(int(message_id))
                    
                    # Update the embed to show it was cancelled
                    embed = discord.Embed(
                        title="🚫 GIVEAWAY CANCELLED 🚫",
                        description=f"**{giveaway['prize']}**\n\nThis giveaway has been cancelled.",
                        color=0xFF0000
                    )
                    embed.set_footer(text=f"Cancelled by {interaction.user.name}")
                    
                    await message.edit(embed=embed)
                    await channel.send(f"The giveaway for **{giveaway['prize']}** has been cancelled by {interaction.user.mention}.")
                except discord.NotFound:
                    # Message was already deleted
                    pass
            
            # Remove from active giveaways
            del self.active_giveaways[message_id]
            self.save_giveaways()
            
            await interaction.response.send_message(f"Giveaway for **{giveaway['prize']}** has been cancelled.", ephemeral=True)
            
        except Exception as e:
            print(f"Error cancelling giveaway: {e}")
            await interaction.response.send_message("An error occurred while trying to cancel the giveaway.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(GiveawaySystem(bot))