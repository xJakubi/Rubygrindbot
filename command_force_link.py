import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import Optional

# Module metadata
DISPLAY_NAME = "Force Link"
DESCRIPTION = "Allows staff to force link Discord users to in-game names"
ENABLED_BY_DEFAULT = False  # Disabled by default

# Import the necessary functions from the link_setup module
# We'll use these to handle the actual linking process
from command_link_setup import link_user, get_user_link, delete_user_link

async def setup(bot: commands.Bot) -> None:
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    @bot.tree.command(name="force_link", description="Link a user's Discord account to their in-game name (Staff only)")
    @app_commands.describe(
        user="The Discord user to link",
        in_game_name="The in-game name to link to the user (format: name#0000)"
    )
    async def force_link(interaction: discord.Interaction, user: discord.User, in_game_name: str) -> None:
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if the user has one of the required roles
        required_roles = ["Admin", "Moderator", "Support"]
        if interaction.guild:
            member = await interaction.guild.fetch_member(interaction.user.id)
            has_required_role = any(role.name in required_roles for role in member.roles)
            
            if not has_required_role and not member.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You don't have permission to use this command. Required roles: Admin, Moderator, or Support.",
                    ephemeral=True
                )
                return
        
        # Check if the target user already has a linked account
        existing_link = await get_user_link(user.id)
        if existing_link:
            # Create confirmation view for replacing the existing link
            view = discord.ui.View(timeout=60)  # 60 second timeout
            
            confirm_button = discord.ui.Button(
                label="Confirm Replace", 
                style=discord.ButtonStyle.danger, 
                custom_id="confirm_force_link"
            )
            
            cancel_button = discord.ui.Button(
                label="Cancel", 
                style=discord.ButtonStyle.secondary, 
                custom_id="cancel_force_link"
            )
            
            async def confirm_callback(button_interaction):
                if button_interaction.user.id != interaction.user.id:
                    await button_interaction.response.send_message("Only the command initiator can use these buttons.", ephemeral=True)
                    return
                
                # Delete the old link first
                await delete_user_link(user.id)
                
                # Create new link
                success = await link_user(user.id, str(user), in_game_name.strip())
                
                if success:
                    embed = discord.Embed(
                        title="Force Link Successful",
                        description=f"User **{user.mention}** has been linked to in-game name **{in_game_name}**.",
                        color=discord.Color.green()
                    )
                    await button_interaction.response.edit_message(embed=embed, view=None)
                else:
                    embed = discord.Embed(
                        title="Force Link Failed",
                        description="There was an error creating the link. Please try again later.",
                        color=discord.Color.red()
                    )
                    await button_interaction.response.edit_message(embed=embed, view=None)
            
            async def cancel_callback(button_interaction):
                if button_interaction.user.id != interaction.user.id:
                    await button_interaction.response.send_message("Only the command initiator can use these buttons.", ephemeral=True)
                    return
                    
                await button_interaction.response.edit_message(content="Force link operation cancelled.", embed=None, view=None)
            
            confirm_button.callback = confirm_callback
            cancel_button.callback = cancel_callback
            
            view.add_item(confirm_button)
            view.add_item(cancel_button)
            
            embed = discord.Embed(
                title="User Already Linked",
                description=(
                    f"User **{user.display_name}** is already linked to in-game name **{existing_link['in_game_name']}**.\n\n"
                    f"Do you want to replace this link with the new in-game name: **{in_game_name}**?"
                ),
                color=discord.Color.yellow()
            )
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        else:
            # No existing link, create a new one directly
            success = await link_user(user.id, str(user), in_game_name.strip())
            
            if success:
                embed = discord.Embed(
                    title="Force Link Successful",
                    description=f"User **{user.mention}** has been linked to in-game name **{in_game_name}**.",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed)
            else:
                embed = discord.Embed(
                    title="Force Link Failed",
                    description="There was an error creating the link. Please try again later.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

    # Add a command to force unlink users
    @bot.tree.command(name="force_unlink", description="Remove a user's linked in-game name (Staff only)")
    @app_commands.describe(
        user="The Discord user to unlink"
    )
    async def force_unlink(interaction: discord.Interaction, user: discord.User) -> None:
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if the user has one of the required roles
        required_roles = ["Admin", "Moderator", "Support"]
        if interaction.guild:
            member = await interaction.guild.fetch_member(interaction.user.id)
            has_required_role = any(role.name in required_roles for role in member.roles)
            
            if not has_required_role and not member.guild_permissions.administrator:
                await interaction.response.send_message(
                    "You don't have permission to use this command. Required roles: Admin, Moderator, or Support.",
                    ephemeral=True
                )
                return
        
        # Check if the target user has a linked account
        existing_link = await get_user_link(user.id)
        if not existing_link:
            await interaction.response.send_message(
                f"User {user.mention} doesn't have a linked account.",
                ephemeral=True
            )
            return
        
        # Create confirmation view for unlinking
        view = discord.ui.View(timeout=60)  # 60 second timeout
        
        confirm_button = discord.ui.Button(
            label="Confirm Unlink", 
            style=discord.ButtonStyle.danger, 
            custom_id="confirm_force_unlink"
        )
        
        cancel_button = discord.ui.Button(
            label="Cancel", 
            style=discord.ButtonStyle.secondary, 
            custom_id="cancel_force_unlink"
        )
        
        async def confirm_callback(button_interaction):
            if button_interaction.user.id != interaction.user.id:
                await button_interaction.response.send_message("Only the command initiator can use these buttons.", ephemeral=True)
                return
                
            # Delete the link
            success = await delete_user_link(user.id)
            
            if success:
                embed = discord.Embed(
                    title="Force Unlink Successful",
                    description=f"User **{user.mention}** has been unlinked from in-game name **{existing_link['in_game_name']}**.",
                    color=discord.Color.green()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
            else:
                embed = discord.Embed(
                    title="Force Unlink Failed",
                    description="There was an error removing the link. Please try again later.",
                    color=discord.Color.red()
                )
                await button_interaction.response.edit_message(embed=embed, view=None)
        
        async def cancel_callback(button_interaction):
            if button_interaction.user.id != interaction.user.id:
                await button_interaction.response.send_message("Only the command initiator can use these buttons.", ephemeral=True)
                return
                
            await button_interaction.response.edit_message(content="Force unlink operation cancelled.", embed=None, view=None)
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        embed = discord.Embed(
            title="Confirm Force Unlink",
            description=f"Are you sure you want to unlink user **{user.display_name}** from in-game name **{existing_link['in_game_name']}**?",
            color=discord.Color.yellow()
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    