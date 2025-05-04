import discord
from discord.ext import commands
from discord import app_commands
import datetime
import asyncio
from typing import Optional

DISPLAY_NAME = "Anti-Bot Verification"
DESCRIPTION = "Automatically verifies new users, requiring verification for recently created accounts"
ENABLED_BY_DEFAULT = True

# Configuration
MINIMUM_ACCOUNT_AGE_DAYS = 90  # 3 months in days
UNVERIFIED_ROLE_NAME = "Unverified"
SUPPORT_TICKETS_CATEGORY = "Support Tickets"

async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    @bot.listen('on_member_join')
    async def verify_new_member(member):
        # Skip if this feature is disabled for the guild
        if not bot.is_feature_enabled(feature_name, member.guild.id):
            return
        
        # Calculate account age
        account_age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
        
        # If account is older than 3 months, don't lock them
        if account_age.days >= MINIMUM_ACCOUNT_AGE_DAYS:
            return
        
        # Get or create the "Unverified" role
        unverified_role = discord.utils.get(member.guild.roles, name=UNVERIFIED_ROLE_NAME)
        if not unverified_role:
            try:
                # Create role with no permissions
                unverified_role = await member.guild.create_role(
                    name=UNVERIFIED_ROLE_NAME,
                    color=discord.Color.dark_gray(),
                    reason="Created for anti-bot verification system"
                )
                
                # Set permissions for all channels to deny view access for this role
                for channel in member.guild.channels:
                    try:
                        await channel.set_permissions(unverified_role, view_channel=False, send_messages=False)
                    except discord.Forbidden:
                        continue
            except discord.Forbidden:
                print(f"Failed to create 'Unverified' role in guild {member.guild.name}")
                return
        
        # Assign the unverified role to the member
        try:
            await member.add_roles(unverified_role, reason=f"Account created {account_age.days} days ago (less than {MINIMUM_ACCOUNT_AGE_DAYS} days)")
        except discord.Forbidden:
            print(f"Failed to assign 'Unverified' role to {member} in guild {member.guild.name}")
            return
        
        # Create a verification ticket
        await create_verification_ticket(bot, member)

    async def create_verification_ticket(bot, member):
        """Creates a verification ticket for a new member with a young account"""
        guild = member.guild
        
        # Find "Support" and "Moderator" roles
        support_role = discord.utils.get(guild.roles, name="Support")
        mod_role = discord.utils.get(guild.roles, name="Moderator")
        
        # Generate ticket name
        ticket_count = len([c for c in guild.channels if c.name.startswith("verify-")])
        ticket_name = f"verify-{member.name}-{ticket_count + 1:04d}"
        
        # Set up permissions for the channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        # Find or create a ticket category
        ticket_category = discord.utils.get(guild.categories, name=SUPPORT_TICKETS_CATEGORY)
        if not ticket_category:
            ticket_category = await guild.create_category(SUPPORT_TICKETS_CATEGORY)
        
        # Create the ticket channel
        try:
            channel = await guild.create_text_channel(
                name=ticket_name,
                category=ticket_category,
                overwrites=overwrites,
                topic=f"Verification for {member.name} | Account Age: {(datetime.datetime.now(datetime.timezone.utc) - member.created_at).days} days | User ID: {member.id}"
            )
        except discord.Forbidden:
            print(f"Failed to create verification channel for {member} in guild {guild.name}")
            return
        
        # Create embed for the verification ticket
        embed = discord.Embed(
            title="New User Verification Required",
            description=f"{member.mention} has joined the server, but their account is less than 3 months old. Verification is required.",
            color=0xFF9900
        )
        
        embed.add_field(
            name="Account Information",
            value=f"User: {member.mention} ({member.name})\nAccount Created: {member.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC\nAccount Age: {(datetime.datetime.now(datetime.timezone.utc) - member.created_at).days} days"
        )
        
        embed.add_field(
            name="Verification Instructions", 
            value="Please provide the following to get verified:\n• Your in-game name\n• A screenshot showing you in the game\n\nA moderator will review your information and unlock your access.",
            inline=False
        )
        
        # Create verification buttons
        view = VerificationButtonView(member)
        
        # Build mentions
        mentions = []
        if mod_role:
            mentions.append(mod_role.mention)
        if support_role:
            mentions.append(support_role.mention)
        mentions.append(member.mention)
        
        mention_text = " ".join(mentions)
        
        # Send the verification message
        await channel.send(mention_text, embed=embed, view=view)
        
        # Send welcome message to the user explaining what they need to do
        user_embed = discord.Embed(
            title=f"Welcome to {guild.name}!",
            description="Your account is less than 3 months old, so we need to verify you're not a bot.",
            color=0x3498db
        )
        
        user_embed.add_field(
            name="What to do next",
            value=f"Please check {channel.mention} and follow the instructions to get verified.\n\nYou'll need to provide your in-game name and a screenshot as proof."
        )
        
        try:
            await member.send(embed=user_embed)
        except discord.Forbidden:
            # User has DMs disabled, we'll just continue
            pass
    
    class VerificationButtonView(discord.ui.View):
        def __init__(self, member: discord.Member):
            super().__init__(timeout=None)  # Persistent view with no timeout
            self.member = member
        
        @discord.ui.button(label="Unlock User", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_unlock")
        async def unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Check if user has permission to manage channels
            if not interaction.user.guild_permissions.manage_channels:
                await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
                return
            
            # Get the unverified role
            unverified_role = discord.utils.get(self.member.guild.roles, name=UNVERIFIED_ROLE_NAME)
            
            if unverified_role and unverified_role in self.member.roles:
                # Remove unverified role
                try:
                    await self.member.remove_roles(unverified_role, reason=f"Manually verified by {interaction.user}")
                    await interaction.response.send_message(f"{self.member.mention} has been verified and given access to the server.")
                    
                    # Send welcome DM to the user
                    try:
                        embed = discord.Embed(
                            title=f"Welcome to {self.member.guild.name}!",
                            description="You have been verified and now have full access to the server.",
                            color=0x2ECC71
                        )
                        await self.member.send(embed=embed)
                    except discord.Forbidden:
                        # User has DMs disabled
                        pass
                    
                    # Close the ticket after a delay
                    await interaction.followup.send("This verification ticket will be closed in 10 seconds.")
                    await asyncio.sleep(10)
                    await interaction.channel.delete(reason=f"Verification completed by {interaction.user}")
                    
                except discord.Forbidden:
                    await interaction.response.send_message("I don't have permission to remove the Unverified role.")
            else:
                await interaction.response.send_message(f"{self.member.mention} doesn't have the Unverified role or is no longer in the server.")
        
        @discord.ui.button(label="Reject User", style=discord.ButtonStyle.danger, emoji="❌", custom_id="verify_reject")
        async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Check if user has permission to manage channels
            if not interaction.user.guild_permissions.manage_channels:
                await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
                return
            
            await interaction.response.send_message(f"{self.member.mention} has been rejected. This ticket will be closed in 5 seconds.")
            await asyncio.sleep(5)
            await interaction.channel.delete(reason=f"Verification rejected by {interaction.user}")

    # Command to manually trigger verification for a user
    @bot.tree.command(name="verify", description="Manually verify a user with a recently created account")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(member="The member to verify")
    async def verify_command(interaction: discord.Interaction, member: discord.Member):
        # Check if the feature is enabled
        if not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message("The Anti-Bot Verification system is disabled.", ephemeral=True)
            return
        
        await interaction.response.send_message(f"Creating verification ticket for {member.mention}...", ephemeral=True)
        
        # Check if member already has the unverified role
        unverified_role = discord.utils.get(interaction.guild.roles, name=UNVERIFIED_ROLE_NAME)
        if not unverified_role:
            unverified_role = await interaction.guild.create_role(
                name=UNVERIFIED_ROLE_NAME,
                color=discord.Color.dark_gray(),
                reason="Created for anti-bot verification system"
            )
        
        # Add the unverified role if they don't have it
        if unverified_role not in member.roles:
            await member.add_roles(unverified_role, reason=f"Manual verification requested by {interaction.user}")
        
        # Create the verification ticket
        await create_verification_ticket(bot, member)
        await interaction.followup.send(f"Verification ticket created for {member.mention}.", ephemeral=True)