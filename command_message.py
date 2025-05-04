import discord
from discord.ext import commands
from discord import app_commands
import functools
import asyncio
import json
import os
from datetime import datetime
import uuid
from typing import List, Optional

DISPLAY_NAME = "Direct Messaging System"
DESCRIPTION = "Allows staff to send direct messages to users with read receipts"
ENABLED_BY_DEFAULT = False

# File to store message data
MESSAGE_DATA_FILE = "message_data.json"

class MessageData:
    def __init__(self):
        self.messages = {}
        self.load_data()
    
    def load_data(self):
        """Load message data from file"""
        try:
            if os.path.exists(MESSAGE_DATA_FILE):
                with open(MESSAGE_DATA_FILE, 'r') as f:
                    self.messages = json.load(f)
        except Exception as e:
            print(f"Error loading message data: {e}")
            self.messages = {}
    
    def save_data(self):
        """Save message data to file"""
        try:
            with open(MESSAGE_DATA_FILE, 'w') as f:
                json.dump(self.messages, f)
        except Exception as e:
            print(f"Error saving message data: {e}")
    
    def add_message(self, message_id: str, data: dict):
        """Add a new message to the database"""
        self.messages[message_id] = data
        self.save_data()
    
    def get_message(self, message_id: str) -> Optional[dict]:
        """Get a message by ID"""
        return self.messages.get(message_id)
    
    def update_message(self, message_id: str, data: dict):
        """Update a message's data"""
        if message_id in self.messages:
            self.messages[message_id] = data
            self.save_data()
    
    def get_all_messages_for_guild(self, guild_id: int) -> List[dict]:
        """Get all messages for a specific guild"""
        return [msg for msg in self.messages.values() if msg.get("guild_id") == str(guild_id)]

# Global message data instance
message_data = MessageData()

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__

    @bot.tree.command(name="message", description="Send a direct message to one or more users")
    @app_commands.describe(
        users="The users to message (mention multiple users)",
        message="The message content to send"
    )
    @app_commands.default_permissions(ban_members=True)
    async def message_command(interaction: discord.Interaction, message: str, users: str):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The messaging system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Extract user IDs from mentions
        user_ids = []
        for mention in users.split():
            # Extract user ID from mention format <@123456789>
            if mention.startswith('<@') and mention.endswith('>'):
                user_id = mention.replace('<@', '').replace('>', '')
                # Remove the ! character if it's a nickname mention
                user_id = user_id.replace('!', '')
                try:
                    user_ids.append(int(user_id))
                except ValueError:
                    pass
        
        if not user_ids:
            await interaction.response.send_message(
                "No valid user mentions found. Please mention at least one user with @username.",
                ephemeral=True
            )
            return
        
        # Generate a unique message ID
        message_id = str(uuid.uuid4())[:8]
        
        # Create message data with all recipients
        msg_data = {
            "id": message_id,
            "sender_id": str(interaction.user.id),
            "sender_name": interaction.user.name,
            "guild_id": str(interaction.guild.id),
            "guild_name": interaction.guild.name,
            "content": message,
            "timestamp": datetime.now().isoformat(),
            "recipients": []
        }
        
        # Defer the response since we might need to send multiple DMs
        await interaction.response.defer()
        
        # Track successful and failed messages
        successful = []
        failed = []
        
        # Send the message to each user
        for user_id in user_ids:
            try:
                user = interaction.guild.get_member(user_id)
                if not user:
                    # Try fetching from the bot's cache
                    user = bot.get_user(user_id)
                    if not user:
                        # Last resort: try fetching from Discord API
                        try:
                            user = await bot.fetch_user(user_id)
                        except:
                            failed.append(f"<@{user_id}> (User not found)")
                            continue
                
                # Add recipient to message data
                recipient_data = {
                    "id": str(user.id),
                    "name": user.name,
                    "acknowledged": False,
                    "acknowledged_at": None
                }
                msg_data["recipients"].append(recipient_data)
                
                # Create the embed for the user
                user_embed = discord.Embed(
                    title=f"Message from {interaction.guild.name}",
                    description=message,
                    color=discord.Color.blue()
                )
                user_embed.set_footer(text=f"Sent by {interaction.user.name} • Please react with 👍 to acknowledge")
                
                # Send the message to the user
                dm_message = await user.send(embed=user_embed)
                
                # Add the thumbs up reaction for easy acknowledgment
                await dm_message.add_reaction("👍")
                
                # Set up a background task to monitor for acknowledgment
                bot.loop.create_task(monitor_acknowledgment(bot, message_id, dm_message.id, user.id))
                
                successful.append(user.mention)
                
            except discord.Forbidden:
                # User has DMs disabled
                failed.append(f"{user.mention} (DMs disabled)")
                
                # Mark delivery as failed in recipient data
                recipient_data["delivery_failed"] = True
                
            except Exception as e:
                failed.append(f"{user.mention if 'user' in locals() else f'<@{user_id}>'} (Error: {str(e)})")
        
        # Save the message data
        message_data.add_message(message_id, msg_data)
        
        # Create a response embed with results
        response_embed = discord.Embed(
            title="Message Delivery Results",
            description=f"Message ID: `{message_id}`",
            color=discord.Color.green() if successful else discord.Color.red()
        )
        
        response_embed.add_field(name="Message Content", value=message, inline=False)
        
        if successful:
            response_embed.add_field(
                name=f"✅ Successfully Sent ({len(successful)})",
                value=", ".join(successful[:10]) + ("..." if len(successful) > 10 else ""),
                inline=False
            )
        
        if failed:
            response_embed.add_field(
                name=f"❌ Failed to Send ({len(failed)})",
                value="\n".join(failed[:10]) + ("\n..." if len(failed) > 10 else ""),
                inline=False
            )
        
        # Follow up with the results
        await interaction.followup.send(embed=response_embed)
        
        # Also log the message in the log channel if set
        guild_id = str(interaction.guild.id)
        guild_data = message_data.messages.get(guild_id, {})
        
        if "log_channel" in guild_data:
            log_channel_id = guild_data.get("log_channel")
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            
            if log_channel:
                sent_embed = discord.Embed(
                    title="Message Sent",
                    description=f"A message has been sent to {len(successful)} users",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                
                sent_embed.add_field(
                    name="Message Content", 
                    value=message, 
                    inline=False
                )
                
                sent_embed.add_field(
                    name="Sent By", 
                    value=interaction.user.mention,
                    inline=True
                )
                
                sent_embed.add_field(
                    name="Message ID", 
                    value=message_id,
                    inline=True
                )
                
                if successful:
                    sent_embed.add_field(
                        name="Recipients", 
                        value=", ".join(successful[:15]) + ("..." if len(successful) > 15 else ""),
                        inline=False
                    )
                
                await log_channel.send(embed=sent_embed)

    @bot.tree.command(name="setmessage", description="Set a channel to receive message acknowledgments")
    @app_commands.default_permissions(administrator=True)
    async def setmessage_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The messaging system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Set the current channel as the message log channel
        guild_id = str(interaction.guild.id)
        
        # Check if we have any message data for this guild
        if guild_id not in message_data.messages:
            message_data.messages[guild_id] = {"log_channel": str(interaction.channel.id)}
        else:
            message_data.messages[guild_id] = {
                **message_data.messages[guild_id], 
                "log_channel": str(interaction.channel.id)
            }
        
        message_data.save_data()
        
        await interaction.response.send_message(
            f"This channel has been set as the message log channel. All message acknowledgments will be posted here.",
           
        )

# Replace the monitor_acknowledgment function with this improved version
    async def monitor_acknowledgment(bot, message_id, dm_message_id, user_id):
        """Monitor for message acknowledgment via reaction"""
        try:
            # Wait for a limited time (7 days max)
            max_wait_time = 60 * 60 * 24 * 7  # 7 days in seconds
            
            def check(reaction, user):
                return (
                    user.id == user_id and 
                    str(reaction.emoji) == "👍" and
                    reaction.message.id == dm_message_id
                )
            
            try:
                # Wait for the reaction
                reaction, user = await bot.wait_for('reaction_add', check=check, timeout=max_wait_time)
                
                # Get the message data (do this after reaction to get fresh data)
                msg = message_data.get_message(message_id)
                if not msg:
                    return
                
                # Find the user in recipients
                recipient = None
                for r in msg["recipients"]:
                    if r["id"] == str(user_id):
                        recipient = r
                        break
                
                if not recipient:
                    return
                
                # User acknowledged the message
                recipient["acknowledged"] = True
                recipient["acknowledged_at"] = datetime.now().isoformat()
                message_data.update_message(message_id, msg)
                
                # Send acknowledgment to log channel if set
                guild_id = msg["guild_id"]
                
                # Need to get the guild_data differently since we're using guild_id as the key for log_channel
                # First check if guild_id is directly a key in messages
                if guild_id in message_data.messages:
                    guild_data = message_data.messages[guild_id]
                else:
                    # Otherwise check all entries for a matching guild_id field
                    guild_entries = [msg for msg_id, msg in message_data.messages.items() 
                                    if msg.get("guild_id") == guild_id]
                    guild_data = guild_entries[0] if guild_entries else {}
                
                # Check if log_channel is directly in messages[guild_id]
                log_channel_id = None
                if "log_channel" in guild_data:
                    log_channel_id = guild_data["log_channel"]
                
                if log_channel_id:
                    guild = bot.get_guild(int(guild_id))
                    
                    if guild:
                        log_channel = guild.get_channel(int(log_channel_id))
                        
                        if log_channel:
                            # Get the acknowledging user and sender
                            recipient_user = guild.get_member(user_id)
                            sender_id = int(msg["sender_id"])
                            sender_user = guild.get_member(sender_id)
                            
                            # Create the acknowledgment embed
                            ack_embed = discord.Embed(
                                title="Message Acknowledged",
                                description=f"{recipient_user.mention if recipient_user else f'User {user_id}'} has acknowledged a message.",
                                color=discord.Color.green(),
                                timestamp=datetime.now()
                            )
                            
                            ack_embed.add_field(
                                name="Message Content", 
                                value=msg["content"], 
                                inline=False
                            )
                            
                            ack_embed.add_field(
                                name="Sent By", 
                                value=sender_user.mention if sender_user else msg["sender_name"],
                                inline=True
                            )
                            
                            ack_embed.add_field(
                                name="Sent At", 
                                value=datetime.fromisoformat(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                                inline=True
                            )
                            
                            ack_embed.add_field(
                                name="Acknowledged At", 
                                value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                inline=True
                            )
                            
                            await log_channel.send(embed=ack_embed)
                
            except asyncio.TimeoutError:
                # User didn't acknowledge, but that's okay
                pass
                
        except Exception as e:
            print(f"Error monitoring acknowledgment: {e}")

    return bot