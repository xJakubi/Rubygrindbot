import discord
from discord.ext import commands
from discord import app_commands
import functools
import asyncio
import json
import os
from typing import Dict, List, Optional
from datetime import datetime

DISPLAY_NAME = "Ticket System"
DESCRIPTION = "A support ticket system with feedback collection and transcripts"
ENABLED_BY_DEFAULT = False

# Constants for the ticket system
TICKET_CATEGORIES = {
    "general": {
        "name": "General Questions",
        "description": "Ask any general questions you have",
        "needs_input": True,
        "prompt": "Please describe your question in detail:"
    },
    "bot": {
        "name": "Bot Related Questions",
        "description": "Questions about the bot functionality",
        "needs_input": True,
        "prompt": "Please describe your bot-related question:",
        "mention_user_id": "199635181236125697"
    },
    "cheater": {
        "name": "Cheater Report",
        "description": "Report players who are cheating",
        "needs_input": False,
        "post_creation_message": "Please provide proof of the cheating you're reporting. Include videos, screenshots, or other evidence."
    },
    "teaming": {
        "name": "Teaming Report",
        "description": "Report players who are teaming.",
        "needs_input": False,
        "post_creation_message": "Please provide proof of the teaming you're reporting. Include videos, screenshots, or other evidence."
    }
}

# File to store ticket system configuration
TICKET_CONFIG_FILE = "ticket_config.json"

class TicketData:
    def __init__(self):
        self.config = {}
        self.load_config()
    
    def load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(TICKET_CONFIG_FILE):
                with open(TICKET_CONFIG_FILE, 'r') as f:
                    self.config = json.load(f)
        except Exception as e:
            print(f"Error loading ticket configuration: {e}")
            self.config = {}
    
    def save_config(self):
        """Save configuration to file"""
        try:
            with open(TICKET_CONFIG_FILE, 'w') as f:
                json.dump(self.config, f)
        except Exception as e:
            print(f"Error saving ticket configuration: {e}")
    
    def set_ticket_channel(self, guild_id: int, channel_id: int):
        """Set the ticket panel channel for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["ticket_channel"] = channel_id
        self.save_config()
    
    def get_ticket_channel(self, guild_id: int) -> Optional[int]:
        """Get the ticket panel channel for a guild"""
        guild_id = str(guild_id)
        if guild_id in self.config and "ticket_channel" in self.config[guild_id]:
            return self.config[guild_id]["ticket_channel"]
        return None
    
    def set_transcript_channel(self, guild_id: int, channel_id: int):
        """Set the transcript channel for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["transcript_channel"] = channel_id
        self.save_config()
    
    def get_transcript_channel(self, guild_id: int) -> Optional[int]:
        """Get the transcript channel for a guild"""
        guild_id = str(guild_id)
        if guild_id in self.config and "transcript_channel" in self.config[guild_id]:
            return self.config[guild_id]["transcript_channel"]
        return None
    
    def set_ticket_message(self, guild_id: int, message_id: int):
        """Set the ticket panel message ID for a guild"""
        guild_id = str(guild_id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["ticket_message"] = message_id
        self.save_config()
    
    def get_ticket_message(self, guild_id: int) -> Optional[int]:
        """Get the ticket panel message ID for a guild"""
        guild_id = str(guild_id)
        if guild_id in self.config and "ticket_message" in self.config[guild_id]:
            return self.config[guild_id]["ticket_message"]
        return None

# Global ticket data instance
ticket_data = TicketData()

# Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Helper function to save channel messages as a transcript
async def create_transcript(channel: discord.TextChannel) -> str:
    """Creates a transcript of the channel messages"""
    transcript = f"# Transcript for {channel.name}\n\n"
    
    # Add metadata
    transcript += f"- Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    transcript += f"- Ticket ID: {channel.name}\n\n"
    
    # Get all messages in the channel
    messages = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append(message)
    
    # Format each message
    for message in messages:
        timestamp = message.created_at.strftime('%Y-%m-%d %H:%M:%S')
        author = f"{message.author.name}#{message.author.discriminator}"
        content = message.content or "*[No content]*"
        
        # Handle attachments
        attachments = ""
        if message.attachments:
            attachments = "\nAttachments: " + ", ".join([a.url for a in message.attachments])
        
        transcript += f"**{timestamp} - {author}:**\n{content}{attachments}\n\n"
    
    return transcript

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Create a persistent view for ticket close buttons
    persistent_views_added = False
    
    # Setup ticket configurations when bot starts
    # Add to your ticket_setup_on_ready function
    @bot.listen('on_ready')
    async def ticket_setup_on_ready():
        # Register the persistent view with the exact same custom_id
        bot.add_view(PersistentTicketView(bot))
        print("Registered PersistentTicketView for tickets")
        
        # Skip processing if feature is disabled
        for guild in bot.guilds:
            if not bot.is_feature_enabled(feature_name, guild.id):
                continue
                
            # Get stored ticket message info
            ticket_channel_id = ticket_data.get_ticket_channel(guild.id)
            ticket_message_id = ticket_data.get_ticket_message(guild.id)
            
            if ticket_channel_id and ticket_message_id:
                try:
                    # Get the channel and message
                    channel = bot.get_channel(ticket_channel_id)
                    if channel:
                        try:
                            # Try to fetch the message
                            message = await channel.fetch_message(ticket_message_id)
                            
                            # Re-apply the view to the existing message
                            view = TicketView(bot, feature_name)
                            await message.edit(view=view)
                            print(f"Re-applied ticket view in guild {guild.name}")
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                            print(f"Could not find or update ticket message in {guild.name}: {e}")
                except Exception as e:
                    print(f"Error re-applying ticket view in guild {guild.id}: {e}")

    @bot.tree.command(name="ticket", description="Set up the ticket system panel")
    @app_commands.default_permissions(administrator=True)
    async def ticket_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The ticket system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Set the ticket channel to the current channel
        ticket_data.set_ticket_channel(interaction.guild.id, interaction.channel.id)
        
        # Post the ticket panel
        await interaction.response.send_message("Setting up ticket system panel...", ephemeral=True)
        await post_ticket_panel(bot, interaction.channel)

    @bot.tree.command(name="transcripts", description="Set the channel for ticket transcripts")
    @app_commands.default_permissions(administrator=True)
    async def transcripts_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The ticket system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Set the transcript channel to the current channel
        ticket_data.set_transcript_channel(interaction.guild.id, interaction.channel.id)
        
        await interaction.response.send_message(
            f"Ticket transcripts will now be sent to this channel.",
            ephemeral=True
        )

    async def post_ticket_panel(bot, channel):
        """Posts the ticket system panel with dropdown in the specified channel"""
        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description="Select a category below to open a new support ticket.",
            color=0x5865F2
        )
        
        for category_id, category in TICKET_CATEGORIES.items():
            embed.add_field(
                name=category["name"],
                value=category["description"],
                inline=False
            )
        
        # Create the dropdown for ticket categories
        view = TicketView(bot, feature_name)
        
        # Send the panel and store the message ID
        message = await channel.send(embed=embed, view=view)
        ticket_data.set_ticket_message(channel.guild.id, message.id)

    # Ticket category dropdown view
    class TicketView(discord.ui.View):
        def __init__(self, bot, feature_name):
            super().__init__(timeout=None)  # Persistent view
            self.bot = bot
            self.feature_name = feature_name
            
            # Add the dropdown
            self.add_item(TicketDropdown(bot, feature_name))

    # Ticket category dropdown
    class TicketDropdown(discord.ui.Select):
        def __init__(self, bot, feature_name):
            self.bot = bot
            self.feature_name = feature_name
            
            options = []
            for category_id, category in TICKET_CATEGORIES.items():
                options.append(discord.SelectOption(
                    label=category["name"],
                    description=category["description"],
                    value=category_id,
                    emoji="🎫"
                ))
            
            super().__init__(
                placeholder="Select a ticket category...",
                min_values=1,
                max_values=1,
                options=options,
                custom_id="ticket_category"
            )
        
        async def callback(self, interaction: discord.Interaction):
            # Skip if feature is disabled
            if not bot.is_feature_enabled(self.feature_name, interaction.guild.id):
                await interaction.response.send_message(
                    "The ticket system is currently disabled.",
                    ephemeral=True
                )
                return
            
            # Get the selected category
            category_id = self.values[0]
            category = TICKET_CATEGORIES[category_id]
            
            if category.get("needs_input", False):
                # If we need additional input, show a modal
                modal = TicketModal(self.bot, category_id, category)
                await interaction.response.send_modal(modal)
            else:
                # Otherwise create ticket directly
                await create_ticket(self.bot, interaction, category_id, None)

    # Modal for collecting additional information
    class TicketModal(discord.ui.Modal):
        def __init__(self, bot, category_id, category):
            super().__init__(title=f"Create {category['name']} Ticket")
            self.bot = bot
            self.category_id = category_id
            self.category = category
            
            self.add_item(discord.ui.TextInput(
                label=category["prompt"],
                style=discord.TextStyle.paragraph,
                placeholder="Please provide details...",
                required=True,
                max_length=1000
            ))
        
        async def on_submit(self, interaction: discord.Interaction):
            # Get the user's input
            user_input = self.children[0].value
            
            # Create the ticket with the input
            await create_ticket(self.bot, interaction, self.category_id, user_input)

    # Function to close a ticket
    class CloseButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                style=discord.ButtonStyle.danger,
                label="Close Ticket",
                emoji="🔒",
                custom_id="ticket:close"  # Make this simpler and consistent
            )
        
        async def callback(self, interaction: discord.Interaction):
            channel = interaction.channel
            
            try:
                # Check if user has permission (is support, mod, or ticket creator)
                # Check if user has manage_channels permission
                if not interaction.user.guild_permissions.manage_channels:
                    # Check if user is the ticket creator (check ticket topic)
                    ticket_user_id = int(channel.topic.split("User ID: ")[-1]) if channel.topic and "User ID:" in channel.topic else None
                    if interaction.user.id != ticket_user_id:
                        await interaction.response.send_message(
                            "You don't have permission to close this ticket.",
                            ephemeral=True
                        )
                        return
                
                # Close the ticket - send feedback request first
                await interaction.response.send_message("Closing this ticket and requesting feedback...")
                
                # Get the ticket creator
                ticket_user_id = int(channel.topic.split("User ID: ")[-1]) if channel.topic and "User ID:" in channel.topic else None
                if ticket_user_id:
                    user = interaction.guild.get_member(ticket_user_id)
                    if user:
                        try:
                            # Create feedback modal
                            feedback_view = FeedbackView(bot, interaction.channel)
                            
                            # Send feedback request to the user
                            await user.send(
                                f"Your ticket in {interaction.guild.name} has been closed. "
                                f"We'd appreciate your feedback on our support:",
                                view=feedback_view
                            )
                        except discord.Forbidden:
                            # User might have DMs closed
                            await channel.send("Could not send feedback request to the user (DMs closed).")
                
                # Create a transcript before closing
                transcript = await create_transcript(channel)
                
                # Check if transcript channel is configured
                transcript_channel_id = ticket_data.get_transcript_channel(interaction.guild.id)
                if transcript_channel_id:
                    transcript_channel = interaction.guild.get_channel(transcript_channel_id)
                    if transcript_channel:
                        # Format current time for the filename
                        time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        
                        # Save transcript to a file
                        filename = f"ticket_{channel.name}_{time_str}.txt"
                        with open(filename, "w", encoding="utf-8") as f:
                            f.write(transcript)
                        
                        # Send the transcript to the transcript channel
                        file = discord.File(filename, filename=filename)
                        await transcript_channel.send(
                            f"Transcript for ticket {channel.name} (closed by {interaction.user.mention}):",
                            file=file
                        )
                        
                        # Delete the temporary file
                        os.remove(filename)
                
                # Close the channel by deleting it after a short delay
                await asyncio.sleep(5)
                await channel.delete(reason=f"Ticket closed by {interaction.user}")
                
            except Exception as e:
                # Log the error
                print(f"Error in close button callback: {e}")
                # Try to respond if possible
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing this request.",
                        ephemeral=True
                    )
                else:
                    # If we've already responded, send a follow-up instead
                    await interaction.followup.send(
                        "An error occurred while processing this request.",
                        ephemeral=True
                    )
            
    # Define persistent view for ticket close buttons
    class PersistentTicketView(discord.ui.View):
        def __init__(self, bot):
            super().__init__(timeout=None)
            self.bot = bot
            self.add_item(CloseButton())

    # Feedback view sent to user when ticket is closed
    class FeedbackView(discord.ui.View):
        def __init__(self, bot, ticket_channel):
            super().__init__(timeout=86400)  # 24 hour timeout
            self.bot = bot
            self.ticket_channel = ticket_channel
        
        @discord.ui.button(label="Provide Feedback", style=discord.ButtonStyle.primary)
        async def feedback_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Show the feedback modal
            await interaction.response.send_modal(FeedbackModal(self.bot, self.ticket_channel))

    # Feedback modal for user ratings
    class FeedbackModal(discord.ui.Modal):
        def __init__(self, bot, ticket_channel):
            super().__init__(title="Support Feedback")
            self.bot = bot
            self.ticket_channel = ticket_channel
            
            self.add_item(discord.ui.TextInput(
                label="Rating (0-10)",
                placeholder="Enter a number from 0 to 10",
                required=True,
                max_length=2
            ))
            
            self.add_item(discord.ui.TextInput(
                label="Comments",
                style=discord.TextStyle.paragraph,
                placeholder="Please share your experience with our support...",
                required=False,
                max_length=1000
            ))
        
        async def on_submit(self, interaction: discord.Interaction):
            # Get the user's feedback
            rating = self.children[0].value
            comments = self.children[1].value
            
            # Validate rating
            try:
                rating_num = int(rating)
                if rating_num < 0 or rating_num > 10:
                    await interaction.response.send_message(
                        "Please provide a rating from 0 to 10.",
                        ephemeral=True
                    )
                    return
            except ValueError:
                await interaction.response.send_message(
                    "Please provide a valid number for the rating.",
                    ephemeral=True
                )
                return
            
            # Thank the user
            await interaction.response.send_message(
                "Thank you for your feedback! It helps us improve our support services.",
                ephemeral=True
            )
            
            # Send the feedback to the transcript channel if configured
            guild_id = self.ticket_channel.guild.id
            transcript_channel_id = ticket_data.get_transcript_channel(guild_id)
            if transcript_channel_id:
                transcript_channel = self.bot.get_channel(transcript_channel_id)
                if transcript_channel:
                    # Create embed for the feedback
                    embed = discord.Embed(
                        title=f"Feedback for {self.ticket_channel.name}",
                        description=f"Rating: {'⭐' * rating_num} ({rating}/10)",
                        color=0x00FF00 if rating_num >= 7 else (0xFFFF00 if rating_num >= 4 else 0xFF0000)
                    )
                    
                    if comments:
                        embed.add_field(name="Comments", value=comments, inline=False)
                    
                    embed.set_footer(text=f"From {interaction.user.name} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    await transcript_channel.send(embed=embed)

    async def create_ticket(bot, interaction, category_id, user_input=None):
        """Creates a new ticket channel"""
        guild = interaction.guild
        user = interaction.user
        category = TICKET_CATEGORIES[category_id]
        
        # Generate a unique ticket channel name
        ticket_count = len([c for c in guild.channels if c.name.startswith("ticket-")])
        ticket_name = f"ticket-{ticket_count + 1:04d}"
        
        # Find "Support" and "Moderator" roles
        support_role = discord.utils.get(guild.roles, name="Support") or discord.utils.get(guild.roles, name="Supporter")
        mod_role = discord.utils.get(guild.roles, name="Moderator")
        
        # Set up permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        # Create the ticket channel
        try:
            # Find or create a ticket category
            ticket_category = discord.utils.get(guild.categories, name="Support Tickets")
            if not ticket_category:
                ticket_category = await guild.create_category("Support Tickets")
                
            channel = await guild.create_text_channel(
                name=ticket_name,
                category=ticket_category,
                overwrites=overwrites,
                topic=f"Support ticket for {user.name} | Category: {category['name']} | User ID: {user.id}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to create channels. Please contact an administrator.",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                f"An error occurred while creating the ticket: {str(e)}",
                ephemeral=True
            )
            return
        
        # Confirm to the user
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"Your ticket has been created: {channel.mention}",
                ephemeral=True
            )
        
        # Create embed for the new ticket
        embed = discord.Embed(
            title=f"{category['name']} Support Ticket",
            description=f"Thank you for creating a ticket. Support staff will be with you shortly.",
            color=0x5865F2
        )
        
        # Add the user input if provided
        if user_input:
            embed.add_field(name="Initial Message", value=user_input, inline=False)
        
        # Tag relevant people/roles
        mentions = []
        mentions.append(user.mention)
        
        if support_role:
            mentions.append(support_role.mention)
        
        if mod_role:
            mentions.append(mod_role.mention)
        
        # Check if we need to mention a specific user
        if "mention_user_id" in category:
            mentions.append(f"<@{category['mention_user_id']}>")
        
        # Use the persistent view for close button
        view = PersistentTicketView(bot)
        
        # Send the initial message
        await channel.send(" ".join(mentions), embed=embed, view=view)
        
        # If there's a specific post-creation message, send it
        if "post_creation_message" in category:
            await channel.send(f"**Note:** {category['post_creation_message']}")